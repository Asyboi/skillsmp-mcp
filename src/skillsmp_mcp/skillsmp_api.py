"""SkillsMP REST client (keyword + semantic search).

The API key is attached here and nowhere else — GitHub requests live in a
different module and never see it (SR1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from . import config


class SkillsMPError(RuntimeError):
    """Any failure talking to the SkillsMP API."""


@dataclass
class Skill:
    id: str | None = None
    name: str = ""
    description: str = ""
    author: str | None = None
    stars: int | None = None
    updated_at: str | None = None
    github_url: str | None = None
    skill_url: str | None = None
    tags: list[str] = field(default_factory=list)
    score: float | None = None


def _skill_from_dict(raw: dict[str, Any], score: float | None = None) -> Skill:
    return Skill(
        id=_str_or_none(raw.get("id")),
        name=str(raw.get("name") or ""),
        description=str(raw.get("description") or ""),
        author=_str_or_none(raw.get("author")),
        stars=_int_or_none(raw.get("stars")),
        updated_at=_str_or_none(raw.get("updatedAt") or raw.get("updated_at")),
        github_url=_str_or_none(raw.get("githubUrl") or raw.get("github_url")),
        skill_url=_str_or_none(raw.get("skillUrl") or raw.get("skill_url")),
        tags=list(raw.get("tags") or []),
        score=score if score is not None else _float_or_none(raw.get("score")),
    )


def _str_or_none(v: Any) -> str | None:
    return str(v) if v is not None else None


def _int_or_none(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _float_or_none(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _require_success(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("success") is False:
        msg = ""
        if isinstance(payload, dict):
            msg = str(payload.get("error") or payload.get("message") or "")
        raise SkillsMPError(f"SkillsMP returned an error{': ' + msg if msg else ''}")
    return payload


def parse_search_response(payload: dict[str, Any]) -> list[Skill]:
    _require_success(payload)
    data = payload.get("data") or {}
    return [_skill_from_dict(s) for s in (data.get("skills") or [])]


def parse_ai_search_response(payload: dict[str, Any]) -> list[Skill]:
    _require_success(payload)
    data = payload.get("data") or {}
    results: list[Skill] = []
    for item in data.get("data") or []:
        score = _float_or_none(item.get("score"))
        nested = item.get("skill")
        if isinstance(nested, dict):
            results.append(_skill_from_dict(nested, score=score))
        else:
            # No embedded skill object — surface what we have (filename/file_id).
            results.append(
                Skill(
                    id=_str_or_none(item.get("file_id")),
                    name=str(item.get("filename") or item.get("file_id") or ""),
                    score=score,
                )
            )
    return results


class SkillsMPClient:
    def __init__(self, api_key: str | None = None, http: httpx.AsyncClient | None = None):
        self._api_key = api_key or config.require_api_key()
        self._base = config.SKILLSMP_API_BASE.rstrip("/")
        self._http = http
        self._owns_http = http is None

    async def __aenter__(self) -> "SkillsMPClient":
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
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
            "User-Agent": "skillsmp-mcp",
        }

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base}{path}"
        try:
            resp = await self._client.get(url, params=params, headers=self._headers())
        except httpx.HTTPError as exc:
            raise SkillsMPError(f"Network error contacting SkillsMP: {exc}") from exc

        if resp.status_code == 401:
            raise SkillsMPError("SkillsMP rejected the request: invalid or missing API key (401).")
        if resp.status_code == 429:
            raise SkillsMPError("SkillsMP rate limited the request (429). Try again later.")
        if resp.status_code >= 400:
            body = resp.text[:300]
            raise SkillsMPError(f"SkillsMP HTTP {resp.status_code}: {body}")

        try:
            return resp.json()
        except ValueError as exc:
            raise SkillsMPError("SkillsMP returned a non-JSON response.") from exc

    async def search(
        self, query: str, limit: int = 20, sort_by: str = "stars", page: int = 1
    ) -> list[Skill]:
        params = {"q": query, "page": page, "limit": limit, "sortBy": sort_by}
        payload = await self._get("/skills/search", params)
        return parse_search_response(payload)

    async def ai_search(self, query: str) -> list[Skill]:
        payload = await self._get("/skills/ai-search", {"q": query})
        return parse_ai_search_response(payload)
