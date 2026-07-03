# PRD — SkillsMP MCP Server

**Status:** Ready to implement
**Owner:** Aslan
**Target implementer:** Claude Code
**Language:** Python 3.10+
**One-line:** An MCP server you own end-to-end that searches the SkillsMP catalogue, reads a skill's `SKILL.md` from GitHub, runs the *full* Cisco AI Skill Scanner (incl. the LLM semantic layer), and installs skills only after they pass the scan.

---

## 1. Problem & rationale

SkillsMP publishes a **REST API**, not an official MCP server. Every `skillsmp-mcp-*` package on npm is unaffiliated third-party code, and an MCP whose job is to *install* skills is the last place to run an unvetted dependency. Owning a small, auditable server removes that supply-chain risk: the API key touches exactly one host, GitHub is read-only, and installs are gated on a real security scan.

Existing community wrappers also run only Cisco's **static + behavioral** analyzers (the offline subset). This server must additionally drive Cisco's **LLM-as-judge** semantic analyzer, which is what catches novel, prose-based prompt injection that signature rules miss.

## 2. Goals

- G1. Keyword and semantic search over SkillsMP via its REST API.
- G2. Read a skill's `SKILL.md` (and sibling files) directly from GitHub, read-only, no local clone.
- G3. Full-engine Cisco security scan (static + behavioral + trigger + LLM + meta; VirusTotal/AI-Defense when keys present).
- G4. Scan-gated install: refuse HIGH/CRITICAL findings unless explicitly forced; refuse unscanned installs unless forced.
- G5. Single-host key handling and clean, auditable module boundaries.

## 3. Non-goals

- Not a catalogue mirror or scraper (SkillsMP already indexes GitHub).
- No auto-execution of skill code; Cisco analysis is static/AST only.
- No write access to GitHub; no OAuth flows.
- No background/continuous monitoring — scanning is point-in-time, on demand.
- v1 does not implement uninstall or update (listed as future work).

## 4. Users & primary flows

Single developer using Claude Desktop / Claude Code.

1. **Discover:** `search_skills` / `ai_search_skills` → candidate skills with repo URLs.
2. **Inspect:** `read_skill` → `SKILL.md` (flagged untrusted). No scan at this step.
3. **Verify:** `scan_skill` → full scan, findings only (separate, approvable action).
4. **Adopt:** `install_skill` → scan-gated write into the install dir.

---

## 5. Architecture

Python package `skillsmp_mcp`, one module per concern. Official MCP SDK (`mcp`, FastMCP). Async HTTP via `httpx`. Cisco scanner invoked as a subprocess CLI (not a library import) for version stability.

```
src/skillsmp_mcp/
  __init__.py
  config.py         # all env-driven tunables; require_api_key()
  skillsmp_api.py   # SkillsMP REST client (keyword + AI search)
  github.py         # tree + contents fetch; resolve skill -> file map (size-capped)
  scanner.py        # Cisco scanner driver (subprocess, full engine set), result parsing
  installer.py      # scan-gated write; namespaced folder, no silent overwrite; path sanitization
  server.py         # FastMCP app; registers 5 tools; output formatting
pyproject.toml      # deps + `skillsmp-mcp` entry point
README.md
```

Keys and settings are supplied via the MCP server's `env` block in the Claude
config (the client injects them into the process environment at launch); the
code reads them with `os.environ`. **No `.env` file** — nothing in the build
loads one. For standalone/terminal runs, export the variables in the shell.

**Dependencies:** `mcp>=1.2.0`, `httpx>=0.27`. Runtime-only external tool: `cisco-ai-skill-scanner` (installed separately via `uv pip install` or run through `uvx`).

---

## 6. External interface contracts (verified)

### 6.1 SkillsMP REST API

- Base: `https://skillsmp.com/api/v1`
- Auth: header `Authorization: Bearer <sk_live_...>`. **This key is sent to this host only.**
- Rate limits: anonymous 50/day, 10/min; authenticated 500/day, 30/min. Wildcards unsupported.
- **Keyword search:** `GET /skills/search?q=&page=&limit=&sortBy=stars|recent`
  Response: `{ success: bool, data: { skills: Skill[], pagination: {...} } }`
- **Semantic search:** `GET /skills/ai-search?q=`
  Response: `{ success: bool, data: { data: [ { file_id, filename, score, skill? } ], has_more, next_page } }`
- **Skill object fields:** `id, name, description, author?, stars?, updatedAt?, tags?, githubUrl?, skillUrl?`
- Errors: non-2xx or `success:false`. Map 401 → "invalid/missing key", 429 → "rate limited".

### 6.2 GitHub (skill source, read-only)

- Tree: `GET https://api.github.com/repos/{owner}/{repo}/git/trees/HEAD?recursive=1` → filter `type == "blob"`.
- File: `GET https://api.github.com/repos/{owner}/{repo}/contents/{path}` → base64 `content`, decode.
- Optional `GITHUB_TOKEN` (read-only) raises 60/hr anonymous limit to 5000/hr. Never needs write scopes.
- Headers: `Accept: application/vnd.github+json`, `User-Agent: skillsmp-mcp`.

### 6.3 Cisco AI Skill Scanner (subprocess)

- Package: `cisco-ai-skill-scanner`. CLI binary: `skill-scanner`.
- Resolution order: `SKILLSMP_SCANNER_CMD` if set → `skill-scanner` on PATH → `uvx --from cisco-ai-skill-scanner skill-scanner` → `uv x --from cisco-ai-skill-scanner skill-scanner`. If none, scanning is unavailable.
- Invocation (full engine set):
  `skill-scanner scan <dir> --use-behavioral --use-trigger --use-llm --enable-meta --format json --output <out.json>`
  - Append `--use-aidefense` when `AI_DEFENSE_API_KEY` set.
  - Append `--policy <preset>` when `SKILLSMP_SCANNER_POLICY` set.
  - Drop `--use-llm`/`--enable-meta` (with a warning) when `SKILL_SCANNER_LLM_API_KEY` is unset.
- Env passed to subprocess: `SKILL_SCANNER_LLM_API_KEY`, `SKILL_SCANNER_LLM_MODEL` (LiteLLM format, e.g. `anthropic/claude-sonnet-5`), `VIRUSTOTAL_API_KEY`, `AI_DEFENSE_API_KEY` (only those that are set).
- JSON output fields consumed: `is_safe` (bool), `max_severity` (str), `findings[]` (`rule_id`/`ruleId`, `severity`, `description`/`message`, `file_path`/`filePath`, `analyzer`), `analyzers_used[]`, `findings_count`.
- Semantics: `is_safe == true` ⇒ no HIGH/CRITICAL. **A clean scan is not a safety guarantee** — it means no known patterns matched.
- Analyzers: static (YAML+YARA signatures), behavioral (AST dataflow, Python only, no execution), trigger-specificity, LLM-as-judge (semantic, needs key), meta (false-positive filter).

---

## 7. Tool specifications

All tools return human-readable text. Skill resolution is shared: when a name is ambiguous or unmatched, return the candidate `SKILL.md` path list for disambiguation rather than erroring.

| Tool | Params | Behavior | Key output |
|------|--------|----------|-----------|
| `search_skills` | `query: str`, `limit: int = 20`, `sort_by: str = "stars"` | Keyword search. | Ranked list: name, ★stars, author, truncated description, repo URL. |
| `ai_search_skills` | `query: str` | Semantic search by intent. | Ranked list with relevance score. |
| `read_skill` | `repo: str` (`owner/repo`), `skill_name: str` | Resolve → fetch files → return `SKILL.md`. **Read-only: no scan, no subprocess, no install.** | `SKILL.md` under an "untrusted — review before acting" banner. |
| `scan_skill` | `repo: str`, `skill_name: str` | Resolve → fetch → full scan. This is the **separately approvable** scan step (spawns a subprocess; with the LLM analyzer on, sends skill content to an LLM). | Findings summary only. |
| `install_skill` | `repo: str`, `skill_name: str`, `force: bool = False` | Resolve → fetch → scan → gated write. | Scan summary + install path, or refusal with reason. |

**Tool separation rationale:** scanning is its own tool (not folded into `read_skill`) so the MCP client can allow reads freely while gating the scan behind per-call approval. `read_skill` never spawns a subprocess. `install_skill` always scans internally as its gate — that scan is not optional, since installing unscanned defeats the purpose.

**Scan summary format:** status icon + `SAFE|UNSAFE|ERROR|SKIPPED`, max severity, analyzers used, any degraded-coverage warnings, then up to ~25 findings as `SEVERITY rule_id: description [file]`.

---

## 8. Configuration (env)

All variables are read from the process environment via `os.environ`. In normal
use they're set in the MCP server's `env` block in the Claude config; for
standalone runs, export them in the shell. No `.env` loader is included.

| Var | Required | Default | Purpose |
|-----|----------|---------|---------|
| `SKILLSMP_API_KEY` | ✅ | — | SkillsMP REST auth. |
| `SKILL_SCANNER_LLM_API_KEY` | recommended | — | Enables the LLM semantic analyzer. |
| `SKILL_SCANNER_LLM_MODEL` | — | `anthropic/claude-sonnet-5` | LiteLLM model string. |
| `GITHUB_TOKEN` | — | — | Read-only PAT to raise GitHub rate limit. |
| `SKILLSMP_INSTALL_DIR` | — | `~/.claude/skills` | Install target (the dir Claude loads skills from). |
| `SKILLSMP_BLOCK_SEVERITIES` | — | `HIGH,CRITICAL` | Severities that block install. |
| `SKILLSMP_SCANNER_CMD` | — | auto-detect | Override scanner command. |
| `SKILLSMP_SCANNER_POLICY` | — | — | Cisco policy preset (e.g. `strict`). |
| `SKILLSMP_SCANNER_TIMEOUT` | — | `300` | Scan timeout (s). |
| `VIRUSTOTAL_API_KEY` / `AI_DEFENSE_API_KEY` | — | — | Optional cloud engines. |
| `SKILLSMP_MAX_FILES` / `SKILLSMP_MAX_SINGLE_FILE_BYTES` / `SKILLSMP_MAX_TOTAL_BYTES` | — | `100` / `512000` / `5242880` | Fetch/scan size caps. |

---

## 9. Security requirements (must-haves)

- SR1. The SkillsMP key is sent only to `SKILLSMP_API_BASE`. GitHub requests never carry it.
- SR2. GitHub access is read-only; no token write scopes; no clone.
- SR3. Skill files for scanning are written to a private temp dir and deleted after the scan (even on error/timeout). No skill code is executed.
- SR4. `install_skill` refuses when `max_severity ∈ BLOCK_SEVERITIES` unless `force=True`.
- SR5. `install_skill` refuses when the scan was **skipped** (scanner unavailable) unless `force=True` — never silently install unscanned.
- SR6. Install folder is **namespaced** as `<owner>-<repo>__<skill-dir>` and sanitized (`[^A-Za-z0-9._-]` collapsed) so (a) skills from different sources cannot collide and (b) a crafted name cannot escape the install root.
- SR7. Install **never silently overwrites**: if the destination folder already exists, install refuses unless `force=True`. (This is also the update path — re-installing the same skill is a deliberate forced action.)
- SR8. `read_skill` output labels `SKILL.md` as untrusted content to be reviewed before acting on it.
- SR9. `read_skill` is pure read: it makes no subprocess calls and performs no scan. Scanning happens only via `scan_skill` or inside `install_skill`.
- SR10. Fetch honors size caps (per-file, total, count) with a note listing what was excluded.

---

## 10. Data models

```
Skill { id, name, description, author?, stars?, github_url?, skill_url?, tags?, score? }
ResolvedSkill { repo, skill_path, skill_md, files: {relpath: bytes}, scan_note? }
Finding { rule_id, severity, description, file_path, analyzer }
ScanResult { available, status, max_severity, findings[], analyzers_used[], warnings[], error? ; blocked: derived }
```

`resolve_skill()` returns `ResolvedSkill` **or** `list[str]` (candidate paths) for disambiguation.

## 11. Error handling

- SkillsMP 401/429 → explicit user-facing messages; other non-2xx → status + truncated body.
- GitHub non-2xx → `GitHubError` with status; rate-limit (403) surfaces cleanly and suggests a token.
- Bad `repo` format (not `owner/repo`) → validation error before any network call.
- Scanner missing → `ScanResult(status="SKIPPED")` with install-blocking behavior per SR5.
- Scanner timeout / no report / bad JSON → `ScanResult(status="ERROR")` with detail; never crash the tool.

---

## 12. Acceptance criteria

1. All five tools register and are listable via the MCP server.
2. `search_skills("pdf")` returns formatted results against the live SkillsMP API with a valid key; 401 path proven with a bad key.
3. `read_skill("anthropics/skills", "pdf")` fetches the correct `SKILL.md` and spawns **no** subprocess; an ambiguous name returns a candidate list; a bad repo string is rejected pre-network.
4. `scan_skill(...)` with the Cisco scanner installed + LLM key returns a parsed verdict whose `analyzers_used` includes the LLM analyzer; without the LLM key it still returns a verdict plus a degraded-coverage warning.
5. `install_skill(...)` writes files under `SKILLSMP_INSTALL_DIR/<owner>-<repo>__<skill>/` when SAFE; refuses on HIGH/CRITICAL, when unscanned, and when the destination folder already exists; `force=True` overrides all three. Temp scan dir is removed in all cases.
6. Grep confirms the SkillsMP key is only ever attached to `SKILLSMP_API_BASE` requests.
7. `python -m py_compile` clean; server starts over stdio.

## 13. Implementation order (for Claude Code)

1. `config.py` → `skillsmp_api.py` (test search live).
2. `github.py` (test resolve against a real repo; use a `GITHUB_TOKEN` to avoid rate limits).
3. `scanner.py` (test with a known-benign skill; confirm LLM analyzer appears in `analyzers_used`).
4. `installer.py` (unit-test gating with fake `ScanResult`s: HIGH blocks, SAFE passes, SKIPPED blocks, existing-folder blocks, `force` overrides all; assert namespaced folder name).
5. `server.py` wiring + formatting; `pyproject.toml`, `README.md`. Add `.gitignore` (ignore `.venv/`, `__pycache__/`, and any `.env`).
6. Register in `claude_desktop_config.json` (merge into `mcpServers`, don't replace); use the venv binary path if not on PATH; restart the client.

## 14. Future work / extension points

- Post-install hook writing a scanned-skill index entry into an Obsidian vault (browsable library).
- `uninstall_skill` / `update_skill` / `list_installed`.
- Cache scan results by repo+commit SHA to avoid rescanning unchanged skills.
- Pin skills to a commit SHA so a later malicious update can't be silently pulled.
- Optional stricter default (`BLOCK_SEVERITIES` incl. `MEDIUM`) as a config profile.

## 15. Resolved decisions

- **Install layout:** namespaced — `<install_dir>/<owner>-<repo>__<skill-dir>/`. Prevents cross-source collisions and preserves provenance. Install refuses to overwrite an existing folder unless `force=True` (see SR6–SR7).
- **read_skill scanning:** none. `read_skill` is read-only; scanning is a separate, approvable `scan_skill` tool, and `install_skill` scans internally as its gate (see SR9 and §7 rationale).
- **Install target:** `~/.claude/skills` (the dir Claude loads skills from). Configurable via `SKILLSMP_INSTALL_DIR`; no vault integration in v1.
