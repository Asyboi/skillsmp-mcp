from skillsmp_mcp import server
from skillsmp_mcp.github import ResolvedSkill
from skillsmp_mcp.installer import InstallResult
from skillsmp_mcp.scanner import Finding, ScanResult
from skillsmp_mcp.skillsmp_api import Skill


def test_format_search_results_lists_stars_and_url():
    skills = [
        Skill(name="pdf-tools", description="Work with PDFs", author="anthropics", stars=42,
              github_url="https://github.com/anthropics/skills"),
    ]
    out = server.format_search_results(skills)
    assert "pdf-tools" in out
    assert "42" in out
    assert "anthropics/skills" in out


def test_format_search_results_empty():
    assert "no" in server.format_search_results([]).lower()


def test_format_ai_results_shows_score():
    out = server.format_ai_results([Skill(name="x", score=0.87)])
    assert "0.87" in out


def test_format_skill_content_has_untrusted_banner():
    resolved = ResolvedSkill(repo="o/r", skill_path="s/SKILL.md", skill_md="# Hello")
    out = server.format_skill_content(resolved)
    assert "untrusted" in out.lower()
    assert "# Hello" in out


def test_format_disambiguation_lists_candidates():
    out = server.format_disambiguation("o/r", ["a/SKILL.md", "b/SKILL.md"])
    assert "a/SKILL.md" in out
    assert "b/SKILL.md" in out


def test_format_scan_summary_safe():
    scan = ScanResult(available=True, status="SAFE", max_severity="LOW",
                      analyzers_used=["static", "llm"])
    out = server.format_scan_summary(scan)
    assert "SAFE" in out
    assert "llm" in out


def test_format_scan_summary_lists_findings_and_warnings():
    scan = ScanResult(
        available=True, status="UNSAFE", max_severity="HIGH",
        analyzers_used=["llm"],
        warnings=["coverage degraded"],
        findings=[Finding("R1", "HIGH", "prompt injection", "SKILL.md", "llm")],
    )
    out = server.format_scan_summary(scan)
    assert "UNSAFE" in out
    assert "R1" in out
    assert "prompt injection" in out
    assert "coverage degraded" in out


def test_format_scan_summary_skipped():
    scan = ScanResult(available=False, status="SKIPPED", warnings=["scanner not found"])
    out = server.format_scan_summary(scan)
    assert "SKIPPED" in out


def test_format_scan_summary_truncates_many_findings():
    findings = [Finding(f"R{i}", "MEDIUM", "d", "f", "static") for i in range(40)]
    scan = ScanResult(available=True, status="UNSAFE", max_severity="MEDIUM", findings=findings)
    out = server.format_scan_summary(scan)
    assert "R0" in out
    # more than 25 findings -> a truncation note appears
    assert "more" in out.lower()


def test_format_install_result_success():
    from pathlib import Path
    scan = ScanResult(available=True, status="SAFE", max_severity="LOW")
    res = InstallResult(True, "o-r__s", Path("/tmp/o-r__s"), "clean")
    out = server.format_install_result(res, scan)
    assert "o-r__s" in out


def test_format_install_result_refusal():
    scan = ScanResult(available=True, status="UNSAFE", max_severity="HIGH")
    res = InstallResult(False, "o-r__s", None, "max severity HIGH is blocked")
    out = server.format_install_result(res, scan)
    assert "HIGH" in out
    assert "force" in out.lower()
