# skillsmp-mcp

[![PyPI](https://img.shields.io/pypi/v/skillsmp-mcp)](https://pypi.org/project/skillsmp-mcp/)

A Python MCP server for [SkillsMP](https://skillsmp.com) that you own end to end:
search the catalogue, read a skill's `SKILL.md` straight from GitHub, run the
**full** Cisco AI Skill Scanner (including the LLM semantic layer that "lite"
wrappers skip), and install skills **only after they pass the scan**.

## Why this exists

SkillsMP publishes a REST API, not an official MCP server. Every `skillsmp-mcp-*`
package on npm is third-party code. This server talks to SkillsMP with your key
over exactly one host, fetches skills read-only from GitHub, and gates install on
a real security scan — so there's nothing to trust but code you can read.

## Tools

| Tool | What it does |
|------|--------------|
| `search_skills(query, limit, sort_by)` | Keyword search (`sort_by`: `stars`/`recent`). |
| `read_skill(repo, skill_name)` | Fetch `SKILL.md` from a GitHub `owner/repo`, **read-only**. Never scans, never spawns a subprocess, never installs. |
| `scan_skill(repo, skill_name)` | Full Cisco scan; reports findings only. |
| `install_skill(repo, skill_name, force=False)` | Scan-gated install. Refuses on HIGH/CRITICAL unless `force=True`. |

## Requirements

- **Python 3.10+**
- A **SkillsMP API key** (`sk_live_...`) from [skillsmp.com](https://skillsmp.com) — the only hard requirement.
- For `scan_skill` / `install_skill`: either [`uv`](https://docs.astral.sh/uv/)
  installed (the server runs the scanner via `uvx` on demand) **or** the
  `cisco-ai-skill-scanner` package installed directly. Without either, scans
  return `SKIPPED` and installs are blocked.
- Recommended: an **Anthropic API key** (`sk-ant-...`) to enable the LLM
  semantic analyzer.

## Install

The package is on [PyPI](https://pypi.org/project/skillsmp-mcp/). The simplest
path is to let [`uvx`](https://docs.astral.sh/uv/) fetch and run it on demand —
nothing to install or keep updated:

```bash
uvx skillsmp-mcp          # runs the server (reads keys from the environment)
```

Or install a persistent command:

```bash
pipx install skillsmp-mcp     # isolated global command (recommended)
# or
pip install skillsmp-mcp      # into the current environment
```

Either install gives you two ways to launch it — the **`skillsmp-mcp`** console
script and **`python -m skillsmp_mcp`** (handy when the script isn't on PATH).

For `scan_skill` / `install_skill` you also need the Cisco scanner reachable:
have `uv` installed (the server runs it via `uvx` on demand) or
`pipx install cisco-ai-skill-scanner`. Without it, scans return `SKIPPED` and
installs are blocked.

Quick smoke test (no keys needed — completes the MCP handshake, lists the 4
tools, then exits):

```bash
printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"0"}}}\n' \
  | SKILLSMP_API_KEY=dummy uvx skillsmp-mcp
```

## Configure & register with Claude Desktop

Keys are supplied through the MCP server's `env` block in your Claude config —
the code reads them from the process environment, which the client injects at
launch. There is no `.env` file to manage.

Add to `claude_desktop_config.json` (macOS:
`~/Library/Application Support/Claude/claude_desktop_config.json`). Merge into
`mcpServers` — don't replace the block.

```json
"skillsmp": {
  "command": "/Users/you/.local/bin/uvx",
  "args": ["skillsmp-mcp"],
  "env": {
    "SKILLSMP_API_KEY": "sk_live_...",
    "SKILL_SCANNER_LLM_API_KEY": "sk-ant-...",
    "SKILL_SCANNER_LLM_MODEL": "anthropic/claude-sonnet-5"
  }
}
```

`SKILLSMP_API_KEY` is the only hard requirement. Set `SKILL_SCANNER_LLM_API_KEY`
too so the semantic analyzer runs — without it, scans fall back to static +
behavioral only and can miss prose-based prompt injection. All other variables
(see the table below) are optional and go in the same `env` block.

**Use absolute paths for `command`.** GUI apps like Claude Desktop don't inherit
your shell `PATH`, so a bare `uvx` (or `skillsmp-mcp`) often won't resolve. Run
`which uvx` to get the full path and use that (as shown above). If you installed
with `pipx` instead, point `command` straight at the installed script — find it
with `which skillsmp-mcp` — and drop the `args` line.

For the same PATH reason, set `SKILLSMP_SCANNER_CMD` to the absolute `uvx` path
so the scanner is found when the server runs under the client:

```json
"SKILLSMP_SCANNER_CMD": "/Users/you/.local/bin/uvx --from cisco-ai-skill-scanner skill-scanner"
```

Fully quit and relaunch Claude Desktop after editing.

## Environment variables

| Var | Required | Default | Purpose |
|-----|----------|---------|---------|
| `SKILLSMP_API_KEY` | yes | — | SkillsMP REST auth (sent only to skillsmp.com). |
| `SKILL_SCANNER_LLM_API_KEY` | recommended | — | Enables the LLM semantic analyzer. |
| `SKILL_SCANNER_LLM_MODEL` | no | `anthropic/claude-sonnet-5` | LiteLLM model string. |
| `GITHUB_TOKEN` | no | — | Read-only PAT; raises GitHub rate limit. |
| `SKILLSMP_INSTALL_DIR` | no | `~/.claude/skills` | Install target. |
| `SKILLSMP_BLOCK_SEVERITIES` | no | `HIGH,CRITICAL` | Severities that block install. |
| `SKILLSMP_SCANNER_CMD` | no | auto-detect | Override the scanner command. |
| `SKILLSMP_SCANNER_POLICY` | no | — | Cisco policy preset (e.g. `strict`). |
| `SKILLSMP_SCANNER_TIMEOUT` | no | `300` | Scan timeout (seconds). |
| `SKILLSMP_MAX_FILES` / `SKILLSMP_MAX_SINGLE_FILE_BYTES` / `SKILLSMP_MAX_TOTAL_BYTES` | no | `100` / `512000` / `5242880` | Fetch/scan size caps. |
| `VIRUSTOTAL_API_KEY` / `AI_DEFENSE_API_KEY` | no | — | Optional cloud engines. |

For standalone runs outside a Claude client (e.g. terminal testing), just export
the variables in your shell first — the server reads the same environment either
way.

## Security model

- Your SkillsMP key is sent only to `skillsmp.com`. GitHub fetches are
  unauthenticated (or use a read-only `GITHUB_TOKEN`); the two never mix.
- Skill files are fetched read-only, written to a private temp dir for scanning,
  and that dir is deleted afterward. Cisco's analysis is static/AST — nothing is
  executed.
- `install_skill` refuses HIGH/CRITICAL findings unless forced, refuses to
  install unscanned (scanner missing) unless forced, and never silently
  overwrites — re-installing an existing skill folder also requires `force=True`.
- Installs are namespaced as `<owner>-<repo>__<skill-dir>` so skills from
  different sources can't collide and a crafted name can't escape the install root.
- A clean scan is **not** a safety guarantee — it means no known patterns matched.
  Read the `SKILL.md` yourself; `read_skill` labels it untrusted for that reason.

## Where to add your own touches

- `config.py` — new env tunables, stricter `BLOCK_SEVERITIES`, install target.
- `scanner.py` — analyzer selection, extra engines, custom result parsing.
- `installer.py` — post-install hooks (e.g. write an index entry to a vault).
- `server.py` — register more `@mcp.tool()` functions.

## Development

Work from a clone (contributors / local changes):

```bash
git clone https://github.com/Asyboi/skillsmp-mcp.git
cd skillsmp-mcp
uv venv
uv pip install -e ".[dev]"
pytest                       # full suite (unit + parsing + gating)
python -m py_compile src/skillsmp_mcp/*.py
```

Tests are offline by default — network clients are driven with
`httpx.MockTransport` and the scanner logic is unit-tested with fake reports, so
`pytest` needs no keys or the Cisco scanner.

## License

MIT — see [LICENSE](LICENSE).
