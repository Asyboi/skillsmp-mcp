import pytest

from skillsmp_mcp import scanner
from skillsmp_mcp.scanner import Finding, ScanResult, build_scan_command, parse_scan_report


# --- build_scan_command ----------------------------------------------------

def test_build_command_full_engine_set_with_llm():
    argv, warnings = build_scan_command(
        ["skill-scanner"], "/tmp/scan", "/tmp/out.json",
        use_llm=True, use_aidefense=False, policy=None,
    )
    assert argv[:2] == ["skill-scanner", "scan"]
    assert "/tmp/scan" in argv
    for flag in ("--use-behavioral", "--use-trigger", "--use-llm", "--enable-meta"):
        assert flag in argv
    assert argv[-4:] == ["--format", "json", "--output", "/tmp/out.json"]
    assert warnings == []


def test_build_command_drops_llm_and_warns_when_no_key():
    argv, warnings = build_scan_command(
        ["skill-scanner"], "/tmp/scan", "/tmp/out.json",
        use_llm=False, use_aidefense=False, policy=None,
    )
    assert "--use-llm" not in argv
    assert "--enable-meta" not in argv
    assert any("llm" in w.lower() for w in warnings)


def test_build_command_appends_aidefense_and_policy():
    argv, _ = build_scan_command(
        ["skill-scanner"], "/tmp/scan", "/tmp/out.json",
        use_llm=True, use_aidefense=True, policy="strict",
    )
    assert "--use-aidefense" in argv
    assert argv[argv.index("--policy") + 1] == "strict"


# --- resolve_scanner_command ----------------------------------------------

def test_resolve_prefers_env_override(monkeypatch):
    monkeypatch.setattr(scanner.config, "scanner_cmd_override", lambda: ["my-scanner", "--x"])
    assert scanner.resolve_scanner_command() == ["my-scanner", "--x"]


def test_resolve_uses_path_binary(monkeypatch):
    monkeypatch.setattr(scanner.config, "scanner_cmd_override", lambda: None)
    monkeypatch.setattr(scanner.shutil, "which", lambda name: "/usr/bin/skill-scanner" if name == "skill-scanner" else None)
    assert scanner.resolve_scanner_command() == ["skill-scanner"]


def test_resolve_falls_back_to_uvx(monkeypatch):
    monkeypatch.setattr(scanner.config, "scanner_cmd_override", lambda: None)
    monkeypatch.setattr(scanner.shutil, "which", lambda name: "/usr/bin/uvx" if name == "uvx" else None)
    assert scanner.resolve_scanner_command() == ["uvx", "--from", "cisco-ai-skill-scanner", "skill-scanner"]


def test_resolve_none_when_nothing_available(monkeypatch):
    monkeypatch.setattr(scanner.config, "scanner_cmd_override", lambda: None)
    monkeypatch.setattr(scanner.shutil, "which", lambda name: None)
    assert scanner.resolve_scanner_command() is None


# --- parse_scan_report -----------------------------------------------------

def test_parse_report_safe():
    report = {
        "is_safe": True,
        "max_severity": "LOW",
        "findings": [],
        "analyzers_used": ["static", "behavioral", "llm"],
        "findings_count": 0,
    }
    result = parse_scan_report(report)
    assert result.status == "SAFE"
    assert result.max_severity == "LOW"
    assert "llm" in result.analyzers_used


def test_parse_report_unsafe_with_field_aliases():
    report = {
        "is_safe": False,
        "max_severity": "HIGH",
        "findings": [
            {
                "ruleId": "R1",
                "severity": "HIGH",
                "message": "prompt injection",
                "filePath": "SKILL.md",
                "analyzer": "llm",
            }
        ],
        "analyzers_used": ["llm"],
    }
    result = parse_scan_report(report)
    assert result.status == "UNSAFE"
    f = result.findings[0]
    assert f.rule_id == "R1"
    assert f.description == "prompt injection"
    assert f.file_path == "SKILL.md"


def test_parse_report_handles_snake_case_aliases():
    report = {
        "is_safe": False,
        "max_severity": "MEDIUM",
        "findings": [
            {"rule_id": "R2", "severity": "MEDIUM", "description": "d", "file_path": "a.py", "analyzer": "static"}
        ],
    }
    result = parse_scan_report(report)
    assert result.findings[0].rule_id == "R2"
    assert result.findings[0].file_path == "a.py"


# --- ScanResult.is_blocked -------------------------------------------------

def test_is_blocked_true_when_max_severity_in_block_set():
    r = ScanResult(available=True, status="UNSAFE", max_severity="HIGH")
    assert r.is_blocked({"HIGH", "CRITICAL"}) is True


def test_is_blocked_false_when_below_threshold():
    r = ScanResult(available=True, status="UNSAFE", max_severity="MEDIUM")
    assert r.is_blocked({"HIGH", "CRITICAL"}) is False


def test_is_blocked_false_when_safe():
    r = ScanResult(available=True, status="SAFE", max_severity="LOW")
    assert r.is_blocked({"HIGH", "CRITICAL"}) is False
