"""FastMCP server: registers the five SkillsMP tools and formats their output.

Tool separation (SR9, §7): ``read_skill`` is pure read — no subprocess, no
scan. ``scan_skill`` is the separately approvable scan. ``install_skill`` always
scans internally as its install gate.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from . import config, scanner
from .github import GitHubClient, GitHubError, ResolvedSkill, parse_repo
from .installer import install as do_install
from .installer import uninstall as do_uninstall
from .scanner import ScanResult
from .skillsmp_api import Skill, SkillsMPClient, SkillsMPError

mcp = FastMCP("skillsmp")

_STATUS_ICON = {"SAFE": "✅", "UNSAFE": "🚫", "ERROR": "❗", "SKIPPED": "⏭️"}
_MAX_FINDINGS = 25


# --------------------------------------------------------------------------
# Formatting (pure)
# --------------------------------------------------------------------------

def _truncate(text: str, n: int = 140) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def format_search_results(skills: list[Skill]) -> str:
    if not skills:
        return "No skills found."
    lines = [f"Found {len(skills)} skill(s):", ""]
    for i, s in enumerate(skills, 1):
        star = f" ★{s.stars}" if s.stars is not None else ""
        author = f" by {s.author}" if s.author else ""
        lines.append(f"{i}. {s.name}{star}{author}")
        if s.description:
            lines.append(f"   {_truncate(s.description)}")
        if s.github_url:
            lines.append(f"   {s.github_url}")
    return "\n".join(lines)


def format_skill_content(resolved: ResolvedSkill) -> str:
    header = (
        f"⚠️ UNTRUSTED CONTENT — {resolved.repo} :: {resolved.skill_path}\n"
        "This SKILL.md was fetched read-only and has NOT been scanned. Review it "
        "yourself before acting on it. Run scan_skill before install_skill.\n"
        + "=" * 60
    )
    parts = [header, resolved.skill_md]
    if resolved.scan_note:
        parts.append("\n" + "-" * 60 + f"\nNote: {resolved.scan_note}")
    other = [f for f in resolved.files if f != "SKILL.md"]
    if other:
        parts.append(f"\nSibling files fetched: {', '.join(sorted(other))}")
    return "\n".join(parts)


def format_disambiguation(repo: str, candidates: list[str]) -> str:
    if not candidates:
        return f"No SKILL.md found in {repo}."
    lines = [
        f"Multiple/ambiguous skills in {repo}. Re-run with a skill_name matching "
        "one of these directories:",
        "",
    ]
    lines += [f"  - {c}" for c in candidates]
    return "\n".join(lines)


def format_scan_summary(scan: ScanResult) -> str:
    icon = _STATUS_ICON.get(scan.status, "•")
    lines = [f"{icon} {scan.status}"]
    if scan.max_severity:
        lines[0] += f" — max severity {scan.max_severity}"
    if scan.analyzers_used:
        lines.append(f"Analyzers: {', '.join(scan.analyzers_used)}")
    if scan.error:
        lines.append(f"Error: {scan.error}")
    for w in scan.warnings:
        lines.append(f"⚠️ {w}")
    if scan.findings:
        lines.append("")
        lines.append(f"Findings ({len(scan.findings)}):")
        for f in scan.findings[:_MAX_FINDINGS]:
            loc = f" [{f.file_path}]" if f.file_path else ""
            lines.append(f"  {f.severity} {f.rule_id}: {_truncate(f.description)}{loc}")
        if len(scan.findings) > _MAX_FINDINGS:
            lines.append(f"  … and {len(scan.findings) - _MAX_FINDINGS} more.")
    return "\n".join(lines)


def format_install_result(result, scan: ScanResult) -> str:
    summary = format_scan_summary(scan)
    if result.installed:
        return f"{summary}\n\n✅ Installed to: {result.path}\n({result.reason})"
    return (
        f"{summary}\n\n🚫 Install refused: {result.reason}\n"
        "Re-run with force=True to override (you accept the risk)."
    )


_UNINSTALL_ICON = {"removed": "🗑️", "not_installed": "•", "error": "❗"}
_UNINSTALL_LABEL = {"removed": "removed", "not_installed": "not installed", "error": "error"}


def format_uninstall_results(results, install_root) -> str:
    removed = sum(1 for r in results if r.status == "removed")
    lines = [f"Uninstall from {install_root} — {removed}/{len(results)} removed:", ""]
    for r in results:
        icon = _UNINSTALL_ICON.get(r.status, "•")
        label = _UNINSTALL_LABEL.get(r.status, r.status)
        lines.append(f"{icon} {r.skill_name} ({r.folder_name}): {label} — {r.reason}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Shared resolution
# --------------------------------------------------------------------------

async def _resolve(repo: str, skill_name: str):
    """Resolve to a ResolvedSkill, or a formatted disambiguation string."""
    owner, name = parse_repo(repo)
    async with GitHubClient() as gh:
        result = await gh.resolve_skill(owner, name, skill_name)
    if isinstance(result, list):
        return format_disambiguation(f"{owner}/{name}", result)
    return result


def _scan_resolved(resolved: ResolvedSkill) -> ScanResult:
    """Write the skill's files to a private temp dir, scan, and clean up (SR3)."""
    tmp = Path(tempfile.mkdtemp(prefix="skillsmp-scan-"))
    try:
        for relpath, data in resolved.files.items():
            target = tmp / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return scanner.scan_directory(tmp)
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------

@mcp.tool()
async def search_skills(query: str, limit: int = 20, sort_by: str = "stars") -> str:
    """Keyword search the SkillsMP catalogue. sort_by: 'stars' or 'recent'."""
    try:
        async with SkillsMPClient() as client:
            skills = await client.search(query, limit=limit, sort_by=sort_by)
        return format_search_results(skills)
    except (SkillsMPError, config.ConfigError) as exc:
        return f"Error: {exc}"


@mcp.tool()
async def read_skill(repo: str, skill_name: str) -> str:
    """Fetch a skill's SKILL.md from GitHub (owner/repo), read-only.

    Does NOT scan, spawn a subprocess, or install. Content is untrusted.
    """
    try:
        resolved = await _resolve(repo, skill_name)
    except GitHubError as exc:
        return f"Error: {exc}"
    if isinstance(resolved, str):
        return resolved
    return format_skill_content(resolved)


@mcp.tool()
async def scan_skill(repo: str, skill_name: str) -> str:
    """Run the full Cisco scan over a skill and report findings only.

    Spawns a subprocess; with the LLM analyzer on, skill content is sent to an
    LLM. This is the separately approvable scan step — it does not install.
    """
    try:
        resolved = await _resolve(repo, skill_name)
    except GitHubError as exc:
        return f"Error: {exc}"
    if isinstance(resolved, str):
        return resolved
    scan = _scan_resolved(resolved)
    return format_scan_summary(scan)


@mcp.tool()
async def install_skill(repo: str, skill_name: str, force: bool = False) -> str:
    """Scan-gated install. Refuses HIGH/CRITICAL, unscanned, or existing folder
    unless force=True."""
    try:
        resolved = await _resolve(repo, skill_name)
    except GitHubError as exc:
        return f"Error: {exc}"
    if isinstance(resolved, str):
        return resolved
    scan = _scan_resolved(resolved)
    result = do_install(
        resolved,
        scan,
        install_root=config.install_dir(),
        force=force,
        block_severities=config.block_severities(),
    )
    return format_install_result(result, scan)


@mcp.tool()
async def uninstall_skills(repo: str, skill_names: list[str]) -> str:
    """Remove one or more installed skills from the install root (SKILLSMP_INSTALL_DIR).

    Deletes the same ``<owner>-<repo>__<skill-dir>`` folders that install_skill
    wrote — no scan, no network. Processes every name and reports per-skill
    results (removed / not installed / error); a missing skill does not abort
    the batch. Only removes folders matching the install namespacing scheme,
    and never outside the install root.
    """
    try:
        owner, name = parse_repo(repo)
    except GitHubError as exc:
        return f"Error: {exc}"

    root = config.install_dir()
    results = [do_uninstall(owner, name, sn, install_root=root) for sn in skill_names]
    return format_uninstall_results(results, root)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
