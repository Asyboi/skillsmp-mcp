import base64

import httpx
import pytest

from skillsmp_mcp import github
from skillsmp_mcp.github import (
    GitHubClient,
    GitHubError,
    ResolvedSkill,
    find_skill_md_paths,
    parse_repo,
    plan_skill_files,
    select_matching,
)


# --- parse_repo ------------------------------------------------------------

def test_parse_repo_valid():
    assert parse_repo("anthropics/skills") == ("anthropics", "skills")


def test_parse_repo_strips_github_url():
    assert parse_repo("https://github.com/anthropics/skills") == ("anthropics", "skills")


def test_parse_repo_strips_trailing_git_and_slash():
    assert parse_repo("anthropics/skills.git/") == ("anthropics", "skills")


@pytest.mark.parametrize("bad", ["", "noslash", "a/b/c", "/x", "x/", "a b/c"])
def test_parse_repo_rejects_bad(bad):
    with pytest.raises(GitHubError):
        parse_repo(bad)


# --- find_skill_md_paths ---------------------------------------------------

def test_find_skill_md_paths_filters_basename():
    paths = ["pdf/SKILL.md", "pdf/helper.py", "README.md", "docx/SKILL.md", "notes/skill.md.txt"]
    assert find_skill_md_paths(paths) == ["pdf/SKILL.md", "docx/SKILL.md"]


def test_find_skill_md_paths_root_level():
    assert find_skill_md_paths(["SKILL.md", "main.py"]) == ["SKILL.md"]


# --- select_matching -------------------------------------------------------

def test_select_matching_exact_dir_name():
    cands = ["pdf/SKILL.md", "pdf-tools/SKILL.md", "docx/SKILL.md"]
    assert select_matching(cands, "pdf") == ["pdf/SKILL.md"]


def test_select_matching_case_insensitive():
    cands = ["PDF/SKILL.md", "docx/SKILL.md"]
    assert select_matching(cands, "pdf") == ["PDF/SKILL.md"]


def test_select_matching_substring_when_no_exact():
    cands = ["pdf-tools/SKILL.md", "docx/SKILL.md"]
    assert select_matching(cands, "pdf") == ["pdf-tools/SKILL.md"]


def test_select_matching_ambiguous_returns_multiple():
    cands = ["pdf-a/SKILL.md", "pdf-b/SKILL.md"]
    assert set(select_matching(cands, "pdf")) == {"pdf-a/SKILL.md", "pdf-b/SKILL.md"}


def test_select_matching_no_match_returns_empty():
    cands = ["docx/SKILL.md"]
    assert select_matching(cands, "pdf") == []


# --- plan_skill_files (size caps) -----------------------------------------

def test_plan_skill_files_includes_siblings_under_dir():
    entries = [
        ("pdf/SKILL.md", 100),
        ("pdf/helper.py", 200),
        ("pdf/sub/data.txt", 50),
        ("docx/SKILL.md", 100),  # different skill, excluded
        ("README.md", 30),
    ]
    included, excluded = plan_skill_files(
        entries, "pdf/SKILL.md", max_files=100, max_single=10_000, max_total=10_000
    )
    assert set(included) == {"pdf/SKILL.md", "pdf/helper.py", "pdf/sub/data.txt"}
    assert excluded == []


def test_plan_skill_files_skips_oversize_single_file_but_keeps_skill_md():
    entries = [("pdf/SKILL.md", 100), ("pdf/huge.bin", 999_999)]
    included, excluded = plan_skill_files(
        entries, "pdf/SKILL.md", max_files=100, max_single=1000, max_total=10_000
    )
    assert "pdf/SKILL.md" in included
    assert "pdf/huge.bin" not in included
    assert any("huge.bin" in e for e in excluded)


def test_plan_skill_files_honors_total_cap():
    entries = [("pdf/SKILL.md", 100), ("pdf/a", 600), ("pdf/b", 600)]
    included, excluded = plan_skill_files(
        entries, "pdf/SKILL.md", max_files=100, max_single=10_000, max_total=1000
    )
    assert "pdf/SKILL.md" in included
    # a fits (100+600<=1000), b would push to 1300 > 1000 -> excluded
    assert "pdf/a" in included
    assert "pdf/b" not in included
    assert any("b" in e for e in excluded)


def test_plan_skill_files_honors_file_count_cap():
    entries = [("pdf/SKILL.md", 10)] + [(f"pdf/f{i}", 10) for i in range(10)]
    included, excluded = plan_skill_files(
        entries, "pdf/SKILL.md", max_files=3, max_single=1000, max_total=100_000
    )
    assert "pdf/SKILL.md" in included
    assert len(included) == 3
    assert len(excluded) == 8


# --- client fetch + resolve (MockTransport) --------------------------------

def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


async def test_resolve_skill_fetches_skill_md_and_siblings():
    def handler(request: httpx.Request):
        url = str(request.url)
        assert request.headers.get("Authorization") in (None, "")  # no skillsmp key
        if "git/trees" in url:
            return httpx.Response(
                200,
                json={
                    "tree": [
                        {"path": "pdf/SKILL.md", "type": "blob", "size": 20},
                        {"path": "pdf/helper.py", "type": "blob", "size": 15},
                        {"path": "docx/SKILL.md", "type": "blob", "size": 10},
                        {"path": "pdf/subdir", "type": "tree"},
                    ]
                },
            )
        if "contents/pdf/SKILL.md" in url:
            return httpx.Response(200, json={"content": _b64("# PDF skill"), "encoding": "base64"})
        if "contents/pdf/helper.py" in url:
            return httpx.Response(200, json={"content": _b64("print(1)"), "encoding": "base64"})
        return httpx.Response(404, json={"message": "not found"})

    transport = httpx.MockTransport(handler)
    client = GitHubClient(http=httpx.AsyncClient(transport=transport))
    result = await client.resolve_skill("anthropics", "skills", "pdf")
    assert isinstance(result, ResolvedSkill)
    assert result.skill_path == "pdf/SKILL.md"
    assert result.skill_md == "# PDF skill"
    assert set(result.files.keys()) == {"SKILL.md", "helper.py"}


async def test_resolve_skill_ambiguous_returns_candidate_list():
    def handler(request):
        if "git/trees" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "tree": [
                        {"path": "pdf-a/SKILL.md", "type": "blob", "size": 5},
                        {"path": "pdf-b/SKILL.md", "type": "blob", "size": 5},
                    ]
                },
            )
        return httpx.Response(404)

    client = GitHubClient(http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    result = await client.resolve_skill("o", "r", "pdf")
    assert isinstance(result, list)
    assert set(result) == {"pdf-a/SKILL.md", "pdf-b/SKILL.md"}


async def test_resolve_skill_no_skills_returns_empty_list():
    def handler(request):
        if "git/trees" in str(request.url):
            return httpx.Response(200, json={"tree": [{"path": "README.md", "type": "blob", "size": 3}]})
        return httpx.Response(404)

    client = GitHubClient(http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    result = await client.resolve_skill("o", "r", "pdf")
    assert result == []


async def test_tree_403_surfaces_rate_limit_hint():
    def handler(request):
        return httpx.Response(403, json={"message": "API rate limit exceeded"})

    client = GitHubClient(http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(GitHubError) as exc:
        await client.resolve_skill("o", "r", "pdf")
    assert "token" in str(exc.value).lower()
