import httpx
import pytest

from skillsmp_mcp import skillsmp_api
from skillsmp_mcp.skillsmp_api import Skill, SkillsMPClient, SkillsMPError


def test_parse_search_response_maps_fields():
    payload = {
        "success": True,
        "data": {
            "skills": [
                {
                    "id": "1",
                    "name": "pdf-tools",
                    "description": "Work with PDFs",
                    "author": "anthropics",
                    "stars": 42,
                    "githubUrl": "https://github.com/anthropics/skills",
                    "skillUrl": "https://skillsmp.com/s/pdf",
                    "tags": ["pdf", "docs"],
                }
            ],
            "pagination": {"page": 1},
        },
    }
    skills = skillsmp_api.parse_search_response(payload)
    assert len(skills) == 1
    s = skills[0]
    assert s.name == "pdf-tools"
    assert s.stars == 42
    assert s.author == "anthropics"
    assert s.github_url == "https://github.com/anthropics/skills"
    assert s.tags == ["pdf", "docs"]


def test_parse_search_response_rejects_success_false():
    with pytest.raises(SkillsMPError):
        skillsmp_api.parse_search_response({"success": False, "error": "nope"})




async def _client_with_transport(handler):
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return SkillsMPClient(api_key="sk_live_test", http=http)


async def test_search_sends_bearer_only_to_skillsmp_host():
    seen = {}

    def handler(request: httpx.Request):
        seen["host"] = request.url.host
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(
            200,
            json={"success": True, "data": {"skills": [], "pagination": {}}},
        )

    client = await _client_with_transport(handler)
    await client.search("pdf")
    assert seen["host"] == "skillsmp.com"
    assert seen["auth"] == "Bearer sk_live_test"


async def test_search_maps_401_to_invalid_key():
    def handler(request):
        return httpx.Response(401, json={"success": False})

    client = await _client_with_transport(handler)
    with pytest.raises(SkillsMPError) as exc:
        await client.search("pdf")
    assert "key" in str(exc.value).lower()


async def test_search_maps_429_to_rate_limited():
    def handler(request):
        return httpx.Response(429, text="slow down")

    client = await _client_with_transport(handler)
    with pytest.raises(SkillsMPError) as exc:
        await client.search("pdf")
    assert "rate" in str(exc.value).lower()
