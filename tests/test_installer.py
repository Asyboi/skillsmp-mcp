import pytest

from skillsmp_mcp import installer
from skillsmp_mcp.github import ResolvedSkill
from skillsmp_mcp.installer import GateDecision, evaluate_gate, namespaced_folder_name
from skillsmp_mcp.scanner import ScanResult

BLOCK = {"HIGH", "CRITICAL"}


def _scan(status="SAFE", max_sev="LOW"):
    return ScanResult(available=(status != "SKIPPED"), status=status, max_severity=max_sev)


# --- namespaced_folder_name -----------------------------------------------

def test_folder_name_namespaced():
    assert namespaced_folder_name("anthropics", "skills", "pdf/SKILL.md") == "anthropics-skills__pdf"


def test_folder_name_root_skill_uses_repo():
    assert namespaced_folder_name("o", "r", "SKILL.md") == "o-r__r"


def test_folder_name_sanitizes_unsafe_chars():
    name = namespaced_folder_name("o w/n", "re po", "sk ill/SKILL.md")
    assert "/" not in name
    assert " " not in name


def test_folder_name_cannot_be_dot_dot():
    # A crafted dir name must never collapse to a traversal token.
    name = namespaced_folder_name("..", "..", "../SKILL.md")
    assert name not in ("..", ".", "")
    assert "/" not in name


# --- evaluate_gate ---------------------------------------------------------

def test_gate_allows_safe_new_install():
    d = evaluate_gate(_scan("SAFE", "LOW"), dest_exists=False, force=False, block_severities=BLOCK)
    assert d.allowed is True


def test_gate_blocks_high_severity():
    d = evaluate_gate(_scan("UNSAFE", "HIGH"), dest_exists=False, force=False, block_severities=BLOCK)
    assert d.allowed is False
    assert "high" in d.reason.lower()


def test_gate_blocks_skipped_scan():
    d = evaluate_gate(_scan("SKIPPED", None), dest_exists=False, force=False, block_severities=BLOCK)
    assert d.allowed is False
    assert "skip" in d.reason.lower()


def test_gate_blocks_scan_error():
    d = evaluate_gate(_scan("ERROR", None), dest_exists=False, force=False, block_severities=BLOCK)
    assert d.allowed is False


def test_gate_blocks_existing_folder():
    d = evaluate_gate(_scan("SAFE", "LOW"), dest_exists=True, force=False, block_severities=BLOCK)
    assert d.allowed is False
    assert "exist" in d.reason.lower()


def test_gate_force_overrides_high_severity():
    d = evaluate_gate(_scan("UNSAFE", "CRITICAL"), dest_exists=False, force=True, block_severities=BLOCK)
    assert d.allowed is True


def test_gate_force_overrides_skipped_and_existing():
    d = evaluate_gate(_scan("SKIPPED", None), dest_exists=True, force=True, block_severities=BLOCK)
    assert d.allowed is True


def test_gate_medium_not_blocked_by_default():
    d = evaluate_gate(_scan("UNSAFE", "MEDIUM"), dest_exists=False, force=False, block_severities=BLOCK)
    assert d.allowed is True


# --- install (filesystem) --------------------------------------------------

def _resolved():
    return ResolvedSkill(
        repo="anthropics/skills",
        skill_path="pdf/SKILL.md",
        skill_md="# PDF",
        files={"SKILL.md": b"# PDF", "helper.py": b"print(1)", "sub/data.txt": b"x"},
    )


def test_install_writes_files_when_allowed(tmp_path):
    result = installer.install(
        _resolved(), _scan("SAFE", "LOW"), install_root=tmp_path, force=False, block_severities=BLOCK
    )
    assert result.installed is True
    dest = tmp_path / "anthropics-skills__pdf"
    assert (dest / "SKILL.md").read_text() == "# PDF"
    assert (dest / "helper.py").read_bytes() == b"print(1)"
    assert (dest / "sub" / "data.txt").read_bytes() == b"x"


def test_install_refuses_high_without_force(tmp_path):
    result = installer.install(
        _resolved(), _scan("UNSAFE", "HIGH"), install_root=tmp_path, force=False, block_severities=BLOCK
    )
    assert result.installed is False
    assert not (tmp_path / "anthropics-skills__pdf").exists()


def test_install_refuses_existing_folder_without_force(tmp_path):
    (tmp_path / "anthropics-skills__pdf").mkdir(parents=True)
    result = installer.install(
        _resolved(), _scan("SAFE", "LOW"), install_root=tmp_path, force=False, block_severities=BLOCK
    )
    assert result.installed is False


def test_install_force_overwrites_existing(tmp_path):
    dest = tmp_path / "anthropics-skills__pdf"
    dest.mkdir(parents=True)
    (dest / "stale.txt").write_text("old")
    result = installer.install(
        _resolved(), _scan("SAFE", "LOW"), install_root=tmp_path, force=True, block_severities=BLOCK
    )
    assert result.installed is True
    assert (dest / "SKILL.md").exists()


def test_install_rejects_traversal_in_relpath(tmp_path):
    bad = ResolvedSkill(
        repo="o/r", skill_path="s/SKILL.md", skill_md="x",
        files={"SKILL.md": b"x", "../escape.txt": b"bad"},
    )
    with pytest.raises(installer.InstallerError):
        installer.install(bad, _scan("SAFE", "LOW"), install_root=tmp_path, force=False, block_severities=BLOCK)
    # nothing escaped
    assert not (tmp_path.parent / "escape.txt").exists()
