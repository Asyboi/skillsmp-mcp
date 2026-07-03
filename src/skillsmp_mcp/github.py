"""Read-only GitHub source access and skill resolution.

This module fetches a repo's file tree and file contents over the public GitHub
API. It NEVER carries the SkillsMP key (SR1) and NEVER needs write scopes (SR2).
An optional read-only ``GITHUB_TOKEN`` only raises the rate limit.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field

import httpx

from . import config

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class GitHubError(RuntimeError):
    """Any failure fetching from GitHub or resolving a skill."""


@dataclass
class ResolvedSkill:
    repo: str
    skill_path: str
    skill_md: str
    files: dict[str, bytes] = field(default_factory=dict)
    scan_note: str | None = None


def parse_repo(repo: str) -> tuple[str, str]:
    """Parse ``owner/repo`` (or a github URL) into ``(owner, repo)``.

    Raises ``GitHubError`` on anything that isn't a clean single owner/repo pair.
    """
    if not repo or not isinstance(repo, str):
        raise GitHubError("repo must be a non-empty 'owner/repo' string.")
    s = repo.strip()
    for prefix in ("https://github.com/", "http://github.com/", "github.com/"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    s = s.rstrip("/")
    if s.endswith(".git"):
        s = s[: -len(".git")]
    if not _REPO_RE.match(s):
        raise GitHubError(
            f"Invalid repo {repo!r}. Expected 'owner/repo' (e.g. 'anthropics/skills')."
        )
    owner, name = s.split("/")
    return owner, name


def find_skill_md_paths(paths: list[str]) -> list[str]:
    """Return paths whose basename is exactly ``SKILL.md``, order preserved."""
    return [p for p in paths if p.rsplit("/", 1)[-1] == "SKILL.md"]


def _skill_dir(skill_md_path: str) -> str:
    return skill_md_path.rsplit("/", 1)[0] if "/" in skill_md_path else ""


def _dir_name(skill_md_path: str) -> str:
    d = _skill_dir(skill_md_path)
    return d.rsplit("/", 1)[-1] if d else ""


def select_matching(skill_md_paths: list[str], skill_name: str) -> list[str]:
    """Return the SKILL.md paths that match ``skill_name``.

    Preference: exact (case-insensitive) directory-name match; if none, fall back
    to substring match on the containing directory name. Empty list means no
    match — the caller treats that as "offer all candidates" for disambiguation.
    """
    name = (skill_name or "").strip().lower()
    if not name:
        return list(skill_md_paths)

    exact = [p for p in skill_md_paths if _dir_name(p).lower() == name]
    if exact:
        return exact
    return [p for p in skill_md_paths if name in _dir_name(p).lower()]


def plan_skill_files(
    entries: list[tuple[str, int]],
    skill_md_path: str,
    max_files: int,
    max_single: int,
    max_total: int,
) -> tuple[list[str], list[str]]:
    """Decide which files under the skill dir to fetch, honoring size caps.

    ``entries`` is ``[(path, size), ...]`` from the git tree. Returns
    ``(included_paths, excluded_notes)``. SKILL.md is always considered first so
    the core content survives tight caps.
    """
    skill_dir = _skill_dir(skill_md_path)
    prefix = skill_dir + "/" if skill_dir else ""
    in_dir = [(p, sz) for (p, sz) in entries if p == skill_md_path or p.startswith(prefix)]

    # SKILL.md first, then stable path order.
    in_dir.sort(key=lambda t: (t[0] != skill_md_path, t[0]))

    included: list[str] = []
    excluded: list[str] = []
    total = 0
    for path, size in in_dir:
        if len(included) >= max_files:
            excluded.append(f"{path} (file count cap {max_files})")
            continue
        if size > max_single:
            excluded.append(f"{path} ({size} B > per-file cap {max_single})")
            continue
        if total + size > max_total:
            excluded.append(f"{path} (would exceed total cap {max_total} B)")
            continue
        included.append(path)
        total += size
    return included, excluded


class GitHubClient:
    def __init__(self, token: str | None = None, http: httpx.AsyncClient | None = None):
        self._token = token if token is not None else config.github_token()
        self._base = config.GITHUB_API_BASE.rstrip("/")
        self._http = http
        self._owns_http = http is None

    async def __aenter__(self) -> "GitHubClient":
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30.0)
            self._owns_http = True
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    @property
    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=30.0)
            self._owns_http = True
        return self._http

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "skillsmp-mcp",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def _get(self, url: str) -> httpx.Response:
        try:
            resp = await self._client.get(url, headers=self._headers())
        except httpx.HTTPError as exc:
            raise GitHubError(f"Network error contacting GitHub: {exc}") from exc

        if resp.status_code == 403:
            body = resp.text.lower()
            if "rate limit" in body:
                raise GitHubError(
                    "GitHub rate limit exceeded. Set a read-only GITHUB_TOKEN to "
                    "raise the limit to 5000/hr."
                )
            raise GitHubError(f"GitHub returned 403: {resp.text[:200]}")
        if resp.status_code >= 400:
            raise GitHubError(f"GitHub HTTP {resp.status_code}: {resp.text[:200]}")
        return resp

    async def fetch_tree(self, owner: str, repo: str) -> list[tuple[str, int]]:
        """Return ``[(path, size), ...]`` for every blob in the repo at HEAD."""
        url = f"{self._base}/repos/{owner}/{repo}/git/trees/HEAD?recursive=1"
        resp = await self._get(url)
        data = resp.json()
        entries: list[tuple[str, int]] = []
        for node in data.get("tree", []):
            if node.get("type") == "blob":
                entries.append((node["path"], int(node.get("size") or 0)))
        return entries

    async def fetch_file(self, owner: str, repo: str, path: str) -> bytes:
        url = f"{self._base}/repos/{owner}/{repo}/contents/{path}"
        resp = await self._get(url)
        data = resp.json()
        content = data.get("content", "")
        encoding = data.get("encoding", "base64")
        if encoding == "base64":
            return base64.b64decode(content)
        return str(content).encode()

    async def resolve_skill(
        self, owner: str, repo: str, skill_name: str
    ) -> ResolvedSkill | list[str]:
        """Resolve a skill to its files, or return candidate paths.

        Returns a ``ResolvedSkill`` on a unique match, a ``list[str]`` of
        candidate SKILL.md paths when ambiguous or unmatched, or ``[]`` when the
        repo has no SKILL.md at all.
        """
        entries = await self.fetch_tree(owner, repo)
        skill_md_paths = find_skill_md_paths([p for p, _ in entries])
        if not skill_md_paths:
            return []

        matches = select_matching(skill_md_paths, skill_name)
        if len(matches) != 1:
            # Ambiguous or unmatched: hand back candidates for disambiguation.
            return matches if matches else skill_md_paths

        skill_md_path = matches[0]
        included, excluded = plan_skill_files(
            entries,
            skill_md_path,
            max_files=config.max_files(),
            max_single=config.max_single_file_bytes(),
            max_total=config.max_total_bytes(),
        )

        skill_dir = _skill_dir(skill_md_path)
        prefix = skill_dir + "/" if skill_dir else ""
        files: dict[str, bytes] = {}
        for path in included:
            data = await self.fetch_file(owner, repo, path)
            relpath = path[len(prefix):] if prefix and path.startswith(prefix) else path
            files[relpath] = data

        skill_md_bytes = files.get("SKILL.md")
        if skill_md_bytes is None:
            # Cap excluded SKILL.md itself; fetch it directly so content survives.
            skill_md_bytes = await self.fetch_file(owner, repo, skill_md_path)
            files["SKILL.md"] = skill_md_bytes

        scan_note = None
        if excluded:
            scan_note = "Excluded from fetch (size caps): " + "; ".join(excluded)

        return ResolvedSkill(
            repo=f"{owner}/{repo}",
            skill_path=skill_md_path,
            skill_md=skill_md_bytes.decode("utf-8", errors="replace"),
            files=files,
            scan_note=scan_note,
        )
