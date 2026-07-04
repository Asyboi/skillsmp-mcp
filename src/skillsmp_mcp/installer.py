"""Scan-gated install into the skills directory.

Enforces SR4–SR7: refuse HIGH/CRITICAL (or configured) findings, refuse
unscanned installs, namespace the folder to prevent cross-source collisions and
path escape, and never silently overwrite an existing folder. ``force=True``
overrides all three gates.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .github import ResolvedSkill
from .scanner import ScanResult

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


class InstallerError(RuntimeError):
    pass


@dataclass
class GateDecision:
    allowed: bool
    reason: str


@dataclass
class InstallResult:
    installed: bool
    folder_name: str
    path: Path | None
    reason: str


@dataclass
class UninstallResult:
    skill_name: str
    folder_name: str
    status: str  # "removed" | "not_installed" | "error"
    reason: str


def _sanitize(component: str) -> str:
    cleaned = _UNSAFE.sub("-", component).strip("-._")
    if cleaned in ("", ".", ".."):
        return "skill"
    return cleaned


def _skill_dir_name(repo_name: str, skill_path: str) -> str:
    directory = skill_path.rsplit("/", 1)[0] if "/" in skill_path else ""
    base = directory.rsplit("/", 1)[-1] if directory else ""
    return base or repo_name


def _folder_from_dir(owner: str, repo: str, skill_dir: str) -> str:
    raw = f"{_sanitize(owner)}-{_sanitize(repo)}__{_sanitize(skill_dir)}"
    # Final guard: collapse any residual separators and traversal tokens.
    safe = _UNSAFE.sub("-", raw)
    if safe in (".", "..", ""):
        return "skill"
    return safe


def namespaced_folder_name(owner: str, repo: str, skill_path: str) -> str:
    """Build ``<owner>-<repo>__<skill-dir>``, sanitized to a single safe segment."""
    return _folder_from_dir(owner, repo, _skill_dir_name(repo, skill_path))


def local_folder_name(owner: str, repo: str, skill_name: str) -> str:
    """Namespaced folder for a bare skill directory name — no GitHub lookup.

    ``install`` derives the folder from the resolved ``SKILL.md`` path; for
    uninstall the caller only knows the skill directory, so resolve it locally.
    Takes the last path segment and falls back to the repo name for a
    root-level skill, matching ``namespaced_folder_name`` exactly.
    """
    skill_dir = skill_name.rsplit("/", 1)[-1] if skill_name else ""
    return _folder_from_dir(owner, repo, skill_dir or repo)


def evaluate_gate(
    scan: ScanResult,
    dest_exists: bool,
    force: bool,
    block_severities: set[str],
) -> GateDecision:
    """Pure gating decision. ``force`` overrides every blocking reason."""
    reasons: list[str] = []
    if scan.status == "SKIPPED":
        reasons.append("scan was skipped (scanner unavailable) — refusing to install unscanned")
    elif scan.status == "ERROR":
        reasons.append(f"scan errored ({scan.error or 'unknown error'}) — no clean verdict")
    elif scan.is_blocked(block_severities):
        reasons.append(f"max severity {scan.max_severity} is in the blocking set {sorted(block_severities)}")
    if dest_exists:
        reasons.append("destination folder already exists (would overwrite)")

    if not reasons:
        return GateDecision(True, "scan clean and destination free")
    if force:
        return GateDecision(True, "forced install, overriding: " + "; ".join(reasons))
    return GateDecision(False, "; ".join(reasons))


def _safe_join(root: Path, relpath: str) -> Path:
    """Join ``relpath`` under ``root``, rejecting traversal/absolute paths."""
    if relpath.startswith("/") or "\\" in relpath:
        raise InstallerError(f"Unsafe file path in skill: {relpath!r}")
    target = (root / relpath).resolve()
    root_resolved = root.resolve()
    if root_resolved != target and root_resolved not in target.parents:
        raise InstallerError(f"File path escapes install folder: {relpath!r}")
    return target


def install(
    resolved: ResolvedSkill,
    scan: ScanResult,
    install_root: Path,
    force: bool,
    block_severities: set[str],
) -> InstallResult:
    """Gate on the scan, then write the skill's files under a namespaced folder."""
    owner, repo = resolved.repo.split("/", 1)
    folder_name = namespaced_folder_name(owner, repo, resolved.skill_path)

    install_root = Path(install_root)
    dest = install_root / folder_name
    # Defense in depth: the folder must be a direct child of the install root.
    if dest.resolve().parent != install_root.resolve():
        raise InstallerError(f"Refusing install: {folder_name!r} escapes the install root.")

    decision = evaluate_gate(scan, dest.exists(), force, block_severities)
    if not decision.allowed:
        return InstallResult(False, folder_name, None, decision.reason)

    # Validate every relpath BEFORE writing anything, so a bad path can't leave
    # a half-written install behind.
    planned = [(_safe_join(dest, rel), data) for rel, data in resolved.files.items()]

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for target, data in planned:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    return InstallResult(True, folder_name, dest, decision.reason)


def uninstall(owner: str, repo: str, skill_name: str, install_root: Path) -> UninstallResult:
    """Delete a previously installed skill's namespaced folder from the root.

    Resolves the same ``<owner>-<repo>__<skill-dir>`` folder that ``install``
    wrote (no network), then removes it. Reports ``not_installed`` for a missing
    folder rather than raising, so a batch can keep going. Only ever removes a
    direct child of ``install_root``.
    """
    folder_name = local_folder_name(owner, repo, skill_name)
    install_root = Path(install_root)
    dest = install_root / folder_name

    # Defense in depth: the folder must be a direct child of the install root.
    if dest.resolve().parent != install_root.resolve():
        return UninstallResult(
            skill_name, folder_name, "error", "refusing to remove: escapes the install root"
        )
    if not dest.exists():
        return UninstallResult(skill_name, folder_name, "not_installed", "no such folder")
    if not dest.is_dir():
        return UninstallResult(skill_name, folder_name, "error", "not a directory")

    shutil.rmtree(dest)
    return UninstallResult(skill_name, folder_name, "removed", f"deleted {dest}")
