"""Behavioral guarantees on the tool layer (SR9): read_skill never scans."""

import contextlib

import pytest

from skillsmp_mcp import scanner, server
from skillsmp_mcp.github import ResolvedSkill
from skillsmp_mcp.scanner import ScanResult


class _FakeGH:
    def __init__(self, result):
        self._result = result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def resolve_skill(self, owner, repo, skill_name):
        return self._result


@pytest.fixture
def no_subprocess(monkeypatch):
    """Fail loudly if anything spawns a subprocess during the test."""
    import subprocess

    def boom(*a, **k):
        raise AssertionError("subprocess.run was called")

    monkeypatch.setattr(subprocess, "run", boom)


async def test_read_skill_makes_no_subprocess(monkeypatch, no_subprocess):
    resolved = ResolvedSkill(repo="o/r", skill_path="s/SKILL.md", skill_md="# hi",
                             files={"SKILL.md": b"# hi"})
    monkeypatch.setattr(server, "GitHubClient", lambda *a, **k: _FakeGH(resolved))
    out = await server.read_skill("o/r", "s")
    assert "# hi" in out
    assert "untrusted" in out.lower()


async def test_read_skill_ambiguous_returns_disambiguation(monkeypatch, no_subprocess):
    monkeypatch.setattr(server, "GitHubClient", lambda *a, **k: _FakeGH(["a/SKILL.md", "b/SKILL.md"]))
    out = await server.read_skill("o/r", "x")
    assert "a/SKILL.md" in out and "b/SKILL.md" in out


async def test_read_skill_bad_repo_rejected_before_network():
    # parse_repo raises GitHubError before any GitHubClient is constructed.
    out = await server.read_skill("not-a-repo", "x")
    assert "error" in out.lower()


async def test_scan_skill_invokes_scanner(monkeypatch):
    resolved = ResolvedSkill(repo="o/r", skill_path="s/SKILL.md", skill_md="# hi",
                             files={"SKILL.md": b"# hi"})
    monkeypatch.setattr(server, "GitHubClient", lambda *a, **k: _FakeGH(resolved))
    called = {}

    def fake_scan(scan_dir):
        called["dir"] = scan_dir
        return ScanResult(available=True, status="SAFE", max_severity="LOW",
                          analyzers_used=["static", "llm"])

    monkeypatch.setattr(scanner, "scan_directory", fake_scan)
    out = await server.scan_skill("o/r", "s")
    assert "SAFE" in out
    assert "dir" in called  # scanner really ran
    # temp scan dir is cleaned up
    assert not called["dir"].exists()


async def test_uninstall_skills_batch_mixed(monkeypatch, no_subprocess, tmp_path):
    from skillsmp_mcp import config

    # install_dir drives both install and uninstall; point it at a temp root.
    monkeypatch.setattr(config, "install_dir", lambda: tmp_path)
    installed = tmp_path / "anthropics-skills__pdf"
    installed.mkdir(parents=True)
    (installed / "SKILL.md").write_text("# PDF")

    out = await server.uninstall_skills("anthropics/skills", ["pdf", "ghost"])

    # The installed one is gone; the missing one is reported, not fatal.
    assert not installed.exists()
    assert "pdf" in out
    assert "ghost" in out
    assert "removed" in out.lower()
    assert "not installed" in out.lower()


async def test_uninstall_skills_bad_repo_rejected(no_subprocess):
    out = await server.uninstall_skills("not-a-repo", ["pdf"])
    assert "error" in out.lower()
