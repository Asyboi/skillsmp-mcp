"""Env-driven configuration.

Every value is read from ``os.environ`` at call time so that keys injected via
the MCP client's ``env`` block (or exported in a shell) are always current.
There is deliberately no ``.env`` loader.
"""

from __future__ import annotations

import os
from pathlib import Path

SKILLSMP_API_BASE = os.environ.get("SKILLSMP_API_BASE", "https://skillsmp.com/api/v1")
GITHUB_API_BASE = "https://api.github.com"

DEFAULT_LLM_MODEL = "anthropic/claude-sonnet-5"
DEFAULT_BLOCK_SEVERITIES = {"HIGH", "CRITICAL"}
DEFAULT_INSTALL_DIR = "~/.claude/skills"
DEFAULT_MAX_FILES = 100
DEFAULT_MAX_SINGLE_FILE_BYTES = 512_000
DEFAULT_MAX_TOTAL_BYTES = 5_242_880
DEFAULT_SCANNER_TIMEOUT = 300


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def require_api_key() -> str:
    """Return the SkillsMP API key or raise if it is unset/empty."""
    key = os.environ.get("SKILLSMP_API_KEY", "").strip()
    if not key:
        raise ConfigError(
            "SKILLSMP_API_KEY is not set. Provide it via the MCP server's env "
            "block or export it in your shell."
        )
    return key


def github_token() -> str | None:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    return token or None


def block_severities() -> set[str]:
    raw = os.environ.get("SKILLSMP_BLOCK_SEVERITIES")
    if not raw or not raw.strip():
        return set(DEFAULT_BLOCK_SEVERITIES)
    return {part.strip().upper() for part in raw.split(",") if part.strip()}


def install_dir() -> Path:
    raw = os.environ.get("SKILLSMP_INSTALL_DIR") or DEFAULT_INSTALL_DIR
    return Path(raw).expanduser()


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def max_files() -> int:
    return _int_env("SKILLSMP_MAX_FILES", DEFAULT_MAX_FILES)


def max_single_file_bytes() -> int:
    return _int_env("SKILLSMP_MAX_SINGLE_FILE_BYTES", DEFAULT_MAX_SINGLE_FILE_BYTES)


def max_total_bytes() -> int:
    return _int_env("SKILLSMP_MAX_TOTAL_BYTES", DEFAULT_MAX_TOTAL_BYTES)


def scanner_timeout() -> int:
    return _int_env("SKILLSMP_SCANNER_TIMEOUT", DEFAULT_SCANNER_TIMEOUT)


def scanner_cmd_override() -> list[str] | None:
    raw = os.environ.get("SKILLSMP_SCANNER_CMD", "").strip()
    return raw.split() if raw else None


def scanner_policy() -> str | None:
    raw = os.environ.get("SKILLSMP_SCANNER_POLICY", "").strip()
    return raw or None


def has_llm_key() -> bool:
    return bool(os.environ.get("SKILL_SCANNER_LLM_API_KEY", "").strip())


def has_virustotal_key() -> bool:
    return bool(os.environ.get("VIRUSTOTAL_API_KEY", "").strip())


def has_aidefense_key() -> bool:
    return bool(os.environ.get("AI_DEFENSE_API_KEY", "").strip())


def scanner_subprocess_env() -> dict[str, str]:
    """Return only the scanner-related env vars that are actually set.

    When the LLM key is present but the model is not, the default model string
    is filled in so the semantic analyzer has something to route with.
    """
    env: dict[str, str] = {}
    llm_key = os.environ.get("SKILL_SCANNER_LLM_API_KEY", "").strip()
    if llm_key:
        env["SKILL_SCANNER_LLM_API_KEY"] = llm_key
        model = os.environ.get("SKILL_SCANNER_LLM_MODEL", "").strip() or DEFAULT_LLM_MODEL
        env["SKILL_SCANNER_LLM_MODEL"] = model

    for name in ("VIRUSTOTAL_API_KEY", "AI_DEFENSE_API_KEY"):
        val = os.environ.get(name, "").strip()
        if val:
            env[name] = val
    return env
