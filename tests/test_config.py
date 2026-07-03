import os
from pathlib import Path

import pytest

from skillsmp_mcp import config


def test_require_api_key_returns_value(monkeypatch):
    monkeypatch.setenv("SKILLSMP_API_KEY", "sk_live_abc")
    assert config.require_api_key() == "sk_live_abc"


def test_require_api_key_raises_when_missing(monkeypatch):
    monkeypatch.delenv("SKILLSMP_API_KEY", raising=False)
    with pytest.raises(config.ConfigError):
        config.require_api_key()


def test_block_severities_default(monkeypatch):
    monkeypatch.delenv("SKILLSMP_BLOCK_SEVERITIES", raising=False)
    assert config.block_severities() == {"HIGH", "CRITICAL"}


def test_block_severities_custom_uppercased_and_trimmed(monkeypatch):
    monkeypatch.setenv("SKILLSMP_BLOCK_SEVERITIES", "high, medium ,critical")
    assert config.block_severities() == {"HIGH", "MEDIUM", "CRITICAL"}


def test_install_dir_default(monkeypatch):
    monkeypatch.delenv("SKILLSMP_INSTALL_DIR", raising=False)
    assert config.install_dir() == Path.home() / ".claude" / "skills"


def test_install_dir_expands_user(monkeypatch):
    monkeypatch.setenv("SKILLSMP_INSTALL_DIR", "~/custom/skills")
    assert config.install_dir() == Path.home() / "custom" / "skills"


def test_size_caps_defaults(monkeypatch):
    for var in ("SKILLSMP_MAX_FILES", "SKILLSMP_MAX_SINGLE_FILE_BYTES", "SKILLSMP_MAX_TOTAL_BYTES"):
        monkeypatch.delenv(var, raising=False)
    assert config.max_files() == 100
    assert config.max_single_file_bytes() == 512_000
    assert config.max_total_bytes() == 5_242_880


def test_scanner_timeout_default_and_override(monkeypatch):
    monkeypatch.delenv("SKILLSMP_SCANNER_TIMEOUT", raising=False)
    assert config.scanner_timeout() == 300
    monkeypatch.setenv("SKILLSMP_SCANNER_TIMEOUT", "45")
    assert config.scanner_timeout() == 45


def test_scanner_subprocess_env_only_includes_set_vars(monkeypatch):
    for var in (
        "SKILL_SCANNER_LLM_API_KEY",
        "SKILL_SCANNER_LLM_MODEL",
        "VIRUSTOTAL_API_KEY",
        "AI_DEFENSE_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SKILL_SCANNER_LLM_API_KEY", "sk-ant-xyz")
    env = config.scanner_subprocess_env()
    assert env["SKILL_SCANNER_LLM_API_KEY"] == "sk-ant-xyz"
    # default model is filled in when a key is present
    assert env["SKILL_SCANNER_LLM_MODEL"] == "anthropic/claude-sonnet-5"
    assert "VIRUSTOTAL_API_KEY" not in env
    assert "AI_DEFENSE_API_KEY" not in env


def test_scanner_subprocess_env_empty_when_no_keys(monkeypatch):
    for var in (
        "SKILL_SCANNER_LLM_API_KEY",
        "SKILL_SCANNER_LLM_MODEL",
        "VIRUSTOTAL_API_KEY",
        "AI_DEFENSE_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    assert config.scanner_subprocess_env() == {}
