"""DEV-211 — an exception of the dead-code check names a route by its function too; an exception that covers nothing
is said, never listed as added.

24.10.2026 NEX Manager 1.4.0: the Auditor verified the version, the dead-code check (DEV-156) stopped it. Among the
blocking findings were GET /directory/groups and GET /directory/groups/{id}/members — the routes other applications
(NEX Knowledge) call, as the approved specification says. The agent had excepted them in docs/dead-code-exceptions.yml
by their functions' names (list_directory_groups, …); the check names a route by its method and path and compared the
two exactly, so the exceptions covered nothing — and the verdict listed them among „výnimky pridané v tejto verzii“
while telling the agent to remove the very routes. Poradca found it; Director: „POradca našiel chybu kokpitu,
prekontroluj“, „Áno, súhlasím, založ tiket a oprav to“.
"""

from __future__ import annotations

from backend.services import dead_code
from tests.test_a_version_adds_no_code_nobody_uses import (  # noqa: F401 — fixture
    BASE,
    FRONTEND_BASE,
    ROUTE_CALL,
    _commit,
    _half,
    _sentences,
    repo,
)

DIRECTORY = (
    "from fastapi import APIRouter\n"
    'router = APIRouter(prefix="/directory")\n'
    '@router.get("/groups")\n'
    "def list_directory_groups(): ...\n"
    '@router.get("/groups/{group_id}/members")\n'
    "def list_directory_members(group_id: str): ...\n"
)
ROUTER = "backend/app/routers/directory.py"


def _exceptions(*names: str) -> str:
    return "".join(
        f"- path: {ROUTER}\n  name: {name}\n  reason: Called by other applications with their key.\n" for name in names
    )


def _scan(repo, monkeypatch, exceptions: str, *, errors=None):  # noqa: F811
    base = _commit(repo, {**BASE, **FRONTEND_BASE}, "base")
    head = _commit(repo, {ROUTER: DIRECTORY, dead_code.EXCEPTIONS_FILE: exceptions}, "version")
    monkeypatch.setattr(
        dead_code, "_run_script_half", _half({}, {"base": ROUTE_CALL, "head": ROUTE_CALL}, errors=errors)
    )
    return dead_code.scan(repo, base, head)


def test_a_route_excepted_by_its_functions_name_is_no_finding(repo, monkeypatch):  # noqa: F811
    report = _scan(repo, monkeypatch, _exceptions("list_directory_groups", "list_directory_members"))

    assert _sentences(report.new) == []
    assert [w.name for w in report.waivers_added] == ["list_directory_groups", "list_directory_members"]
    assert report.invalid_waivers == []


def test_a_route_excepted_by_its_method_and_path_still_is(repo, monkeypatch):  # noqa: F811
    report = _scan(repo, monkeypatch, _exceptions("GET /directory/groups", "GET /directory/groups/{group_id}/members"))

    assert _sentences(report.new) == []


def test_an_exception_that_covers_nothing_is_said_and_not_listed_as_added(repo, monkeypatch):  # noqa: F811
    report = _scan(repo, monkeypatch, _exceptions("list_directory_groups", "list_groups"))

    assert _sentences(report.new) == [
        f"trasu GET /directory/groups/{{group_id}}/members ({ROUTER}:5) nevolá žiadna obrazovka"
    ]
    assert [w.name for w in report.waivers_added] == ["list_directory_groups"]
    assert report.invalid_waivers == [
        f"výnimka list_groups ({ROUTER}) v {dead_code.EXCEPTIONS_FILE} nič nepokrýva — kontrola v tom súbore nenašla "
        "nepoužívaný kód s týmto menom (trasu menuje meno jej funkcie alebo metóda s cestou, napr. „GET /items“); "
        "výnimku oprav, alebo ju zmaž"
    ]


def test_when_the_routes_could_not_be_judged_no_exception_is_called_empty(repo, monkeypatch):  # noqa: F811
    report = _scan(repo, monkeypatch, _exceptions("list_groups"), errors={"head": "npm install failed"})

    assert not any("nič nepokrýva" in sentence for sentence in report.invalid_waivers)


def test_the_agent_is_told_how_a_route_is_named():
    assert "trasu menuje meno jej funkcie alebo metóda s cestou" in dead_code.HOW_TO_RESOLVE


def test_the_agents_charter_says_a_route_may_be_named_by_its_function():
    from pathlib import Path

    charter = (Path(__file__).resolve().parents[1] / "templates" / "ai-agent-charter.md").read_text(encoding="utf-8")

    assert "trasu API aj menom jej funkcie" in " ".join(charter.split())
