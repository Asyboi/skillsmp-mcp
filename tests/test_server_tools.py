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


class _MapGH:
    """Resolve each 'owner/repo:skill' spec to a distinct result in one batch."""

    def __init__(self, mapping):
        self._mapping = mapping

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def resolve_skill(self, owner, repo, skill_name):
        return self._mapping[f"{owner}/{repo}:{skill_name}"]


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


def _resolved(repo, skill_dir, body=b"# ok"):
    return ResolvedSkill(
        repo=repo, skill_path=f"{skill_dir}/SKILL.md", skill_md=body.decode(),
        files={"SKILL.md": body},
    )


async def test_install_skills_installs_across_repos(monkeypatch, tmp_path):
    from skillsmp_mcp import config

    # Two skills from two DIFFERENT repos in a single call.
    mapping = {
        "anthropics/skills:pdf": _resolved("anthropics/skills", "pdf"),
        "octocat/hello:greet": _resolved("octocat/hello", "greet"),
    }
    monkeypatch.setattr(server, "GitHubClient", lambda *a, **k: _MapGH(mapping))
    monkeypatch.setattr(
        scanner, "scan_directory",
        lambda d: ScanResult(available=True, status="SAFE", max_severity="LOW"),
    )
    monkeypatch.setattr(config, "install_dir", lambda: tmp_path)

    out = await server.install_skills(["anthropics/skills:pdf", "octocat/hello:greet"])

    assert (tmp_path / "anthropics-skills__pdf" / "SKILL.md").exists()
    assert (tmp_path / "octocat-hello__greet" / "SKILL.md").exists()
    assert "2/2" in out


async def test_install_skills_gates_per_skill(monkeypatch, tmp_path):
    from skillsmp_mcp import config

    mapping = {
        "o/r:good": _resolved("o/r", "good"),
        "o/r:bad": _resolved("o/r", "bad", b"# BAD"),
    }
    monkeypatch.setattr(server, "GitHubClient", lambda *a, **k: _MapGH(mapping))

    def fake_scan(scan_dir):
        content = (scan_dir / "SKILL.md").read_text()
        if "BAD" in content:
            return ScanResult(available=True, status="UNSAFE", max_severity="HIGH")
        return ScanResult(available=True, status="SAFE", max_severity="LOW")

    monkeypatch.setattr(scanner, "scan_directory", fake_scan)
    monkeypatch.setattr(config, "install_dir", lambda: tmp_path)

    out = await server.install_skills(["o/r:good", "o/r:bad"])

    assert (tmp_path / "o-r__good").exists()
    assert not (tmp_path / "o-r__bad").exists()  # blocked by HIGH severity
    assert "1/2" in out
    assert "force" in out.lower()


async def test_install_skills_reports_errors_without_aborting(monkeypatch, tmp_path):
    from skillsmp_mcp import config

    # One good, one ambiguous (disambiguation list), one malformed spec — the
    # good one must still install.
    mapping = {
        "o/r:good": _resolved("o/r", "good"),
        "o/r:amb": ["x/SKILL.md", "y/SKILL.md"],
    }
    monkeypatch.setattr(server, "GitHubClient", lambda *a, **k: _MapGH(mapping))
    monkeypatch.setattr(
        scanner, "scan_directory",
        lambda d: ScanResult(available=True, status="SAFE", max_severity="LOW"),
    )
    monkeypatch.setattr(config, "install_dir", lambda: tmp_path)

    out = await server.install_skills(["o/r:good", "o/r:amb", "garbage-no-colon"])

    assert (tmp_path / "o-r__good").exists()
    assert "x/SKILL.md" in out  # disambiguation surfaced for the ambiguous one
    assert "garbage-no-colon" in out  # malformed spec reported, not fatal
    assert "1/3" in out


async def test_install_skills_malformed_spec_reported(no_subprocess):
    out = await server.install_skills(["not-a-valid-spec"])
    assert "error" in out.lower() or "invalid" in out.lower()


async def test_uninstall_skills_across_repos(monkeypatch, no_subprocess, tmp_path):
    from skillsmp_mcp import config

    # install_dir drives both install and uninstall; point it at a temp root.
    monkeypatch.setattr(config, "install_dir", lambda: tmp_path)
    a = tmp_path / "anthropics-skills__pdf"
    a.mkdir(parents=True)
    b = tmp_path / "octocat-hello__greet"
    b.mkdir(parents=True)

    out = await server.uninstall_skills(
        ["anthropics/skills:pdf", "octocat/hello:greet", "anthropics/skills:ghost"]
    )

    # Both installed folders (from different repos) are gone; the missing one is
    # reported, not fatal.
    assert not a.exists()
    assert not b.exists()
    assert "removed" in out.lower()
    assert "not installed" in out.lower()


async def test_uninstall_skills_malformed_spec_reported(no_subprocess, monkeypatch, tmp_path):
    from skillsmp_mcp import config

    monkeypatch.setattr(config, "install_dir", lambda: tmp_path)
    out = await server.uninstall_skills(["no-colon-here"])
    assert "error" in out.lower()
