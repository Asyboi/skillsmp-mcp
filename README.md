# skillsmp-mcp

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

## Install

```bash
# from the project dir
uv pip install -e .            # or: pip install -e .

# the scanner is a separate tool (only needed for scan/install):
uv pip install cisco-ai-skill-scanner
# ...or just have `uv` installed — the server will run it via uvx on demand.
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
  "command": "skillsmp-mcp",
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

If `skillsmp-mcp` isn't on PATH, use the venv's Python:
`"command": "/path/to/.venv/bin/skillsmp-mcp"`. Fully quit and relaunch Claude
Desktop after editing.

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
| `SKILL_SCANNER_LLM_MODEL` | no | `anthropic/claude-sonnet-5` | LiteLLM model string for the semantic analyzer. |
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
