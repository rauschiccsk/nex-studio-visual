"""DEV-175 — a version is founded only over saved work, whichever way the Manažér founds it.

The check lived on the New-Version page alone; the backend founded a version over any working tree. On 21.10.2026
Career Asistent 1.1.0 was founded from Dedo's brief with 13 unsaved changes — that button never asked. Every route
that founds a version now refuses (409) with what is unsaved, and the screens offer Uložiť / Zahodiť. Director
21.10.2026: „Súhlasím“, „Poradie nechám na teba“.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import git_state, unsaved_work
from tests.test_dedo_proposal import _build, _propose, _send
from tests.test_dedo_proposal_before_the_work_starts import (  # noqa: F401 — fixtures
    _bearer,
    _make_project,
    _make_user,
    _navrhni,
    _posli,
    dedo_token,
    no_dispatch,
)

REPO = Path(__file__).resolve().parents[1]

UNSAVED_MESSAGE = (
    "Projekt má neuložené zmeny (1). Ulož ich alebo zahoď — inak by kokpit nevedel, čo do novej verzie patrí. "
    "Verzia sa nezaložila."
)
ONE_UNSAVED_FILE = {
    "clean": False,
    "dirty_count": 1,
    "files": [{"code": "M", "path": "frontend/a.ts"}],
    "truncated": False,
}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def workspace(tmp_path) -> Path:
    """A project's checkout with one unsaved change of the Manažér's — and one of the cockpit's own files changed,
    which does not count (DEV-174)."""
    ws = tmp_path / "projekt"
    (ws / "frontend").mkdir(parents=True)
    (ws / "frontend" / "a.ts").write_text("export const a = 1;\n")
    (ws / ".claude" / "agents" / "ai-agent").mkdir(parents=True)
    (ws / ".claude" / "agents" / "ai-agent" / "CLAUDE.md").write_text("# rules\n")
    _git(ws.parent, "init", "-q", "-b", "main", str(ws))
    _git(ws, "config", "user.email", "t@example.com")
    _git(ws, "config", "user.name", "t")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "founding")
    (ws / "frontend" / "a.ts").write_text("export const a = 2;\n")
    (ws / ".claude" / "agents" / "ai-agent" / "CLAUDE.md").write_text("# refreshed rules\n")
    return ws


def _numbers(db, project_id) -> list[str]:
    return sorted(db.execute(select(Version.version_number).where(Version.project_id == project_id)).scalars().all())


def _project_in(db, user, workspace: Path) -> Project:
    project = _make_project(db, user)
    project.source_path = str(workspace)
    db.commit()
    return project


def _assert_refused_for_unsaved_work(answer, project_id) -> None:
    assert answer.status_code == 409, answer.text
    assert answer.json()["detail"] == {
        "message": UNSAVED_MESSAGE,
        "project_id": str(project_id),
        "unsaved_work": ONE_UNSAVED_FILE,
    }


# --- every way a version is founded ---------------------------------------------------------------------------


def test_the_new_version_page_cannot_found_over_unsaved_work(client, db_session, workspace):
    user = _make_user(db_session)
    project = _project_in(db_session, user, workspace)
    before = _numbers(db_session, project.id)

    answer = client.post(
        f"/api/v1/projects/{project.id}/versions", headers=_bearer(user), json={"version_number": "1.6.0"}
    )

    _assert_refused_for_unsaved_work(answer, project.id)
    assert _numbers(db_session, project.id) == before


@pytest.mark.parametrize("action", ["new_version", "fast_fix"])
def test_dedos_brief_cannot_found_over_unsaved_work(client, db_session, workspace, dedo_token, no_dispatch, action):  # noqa: F811
    user = _make_user(db_session)
    project = _project_in(db_session, user, workspace)
    before = _numbers(db_session, project.id)
    brief = _navrhni(client, project.id, action=action).json()

    answer = _posli(client, user, project.id, brief["id"])

    _assert_refused_for_unsaved_work(answer, project.id)
    assert _numbers(db_session, project.id) == before and no_dispatch == []
    waiting = client.get(f"/api/v1/projects/{project.id}/dedo-proposal", headers=_bearer(user)).json()
    assert waiting["id"] == brief["id"], "the refusal closed the brief — the Manažér could not found it after saving"


def test_the_fast_fix_dialog_cannot_found_over_unsaved_work(client, db_session, workspace, no_dispatch):  # noqa: F811
    user = _make_user(db_session)
    project = _project_in(db_session, user, workspace)
    before = _numbers(db_session, project.id)

    answer = client.post(
        "/api/v1/pipeline/fast-fix",
        headers=_bearer(user),
        json={"project_id": str(project.id), "directive": "Oprav preklep.", "work_kind": "fix"},
    )

    _assert_refused_for_unsaved_work(answer, project.id)
    assert _numbers(db_session, project.id) == before and no_dispatch == []


def test_dedos_proposal_on_a_version_cannot_found_a_fast_fix_over_unsaved_work(
    client,
    db_session,
    workspace,
    no_dispatch,  # noqa: F811
):
    user, version, _state = _build(db_session, version_number="0.1.0")
    version.status = "released"  # DEV-110: the fast fix numbers itself from a finished version
    db_session.get(Project, version.project_id).source_path = str(workspace)
    db_session.flush()
    proposal = _propose(db_session, version.id, content="Oprav vstupnú bránu.", action="fast_fix")

    answer = _send(client, user, version.id, proposal, text="Oprav vstupnú bránu.", work_kind="fix")

    _assert_refused_for_unsaved_work(answer, version.project_id)
    assert _numbers(db_session, version.project_id) == ["0.1.0"] and no_dispatch == []


# --- what does not stop founding, and what does ---------------------------------------------------------------


def test_the_cockpits_own_files_alone_do_not_stop_founding(client, db_session, workspace):
    (workspace / "frontend" / "a.ts").write_text("export const a = 1;\n")
    user = _make_user(db_session)
    project = _project_in(db_session, user, workspace)

    answer = client.post(
        f"/api/v1/projects/{project.id}/versions", headers=_bearer(user), json={"version_number": "1.6.0"}
    )

    assert answer.status_code == 201, answer.text


def test_once_the_work_is_saved_the_version_is_founded(client, db_session, workspace):
    user = _make_user(db_session)
    project = _project_in(db_session, user, workspace)
    assert client.post(f"/api/v1/projects/{project.id}/git-commit", headers=_bearer(user), json={}).status_code == 200

    answer = client.post(
        f"/api/v1/projects/{project.id}/versions", headers=_bearer(user), json={"version_number": "1.6.0"}
    )

    assert answer.status_code == 201, answer.text


def test_a_check_that_cannot_be_made_refuses_founding_and_says_so(client, db_session, workspace, monkeypatch):
    """„Couldn't ask“ is not „clean“ — git's own failure, as ``working_tree_status`` raises it."""

    def _git_failed(*_a, **_k):
        raise ValueError("git status zlyhal")

    monkeypatch.setattr(git_state, "working_tree_status", _git_failed)
    user = _make_user(db_session)
    project = _project_in(db_session, user, workspace)
    before = _numbers(db_session, project.id)

    answer = client.post(
        f"/api/v1/projects/{project.id}/versions", headers=_bearer(user), json={"version_number": "1.6.0"}
    )

    assert (answer.status_code, answer.json()["detail"]) == (
        409,
        {"message": unsaved_work.COULD_NOT_CHECK, "project_id": str(project.id)},
    )
    assert _numbers(db_session, project.id) == before


def test_the_new_version_page_reads_the_same_answer_founding_is_refused_over(client, db_session, workspace):
    user = _make_user(db_session)
    project = _project_in(db_session, user, workspace)

    answer = client.get(f"/api/v1/projects/{project.id}/git-status", headers=_bearer(user))

    assert (answer.status_code, answer.json()) == (200, ONE_UNSAVED_FILE)


# --- the rule: no route founds a version without the check ------------------------------------------------------

#: Where a version is founded without the check, and why that is right. A new entry is a decision, not a fix.
EXEMPT = {
    ("backend.api.routes.projects", "create_project"): "a new project's first version is founded before its "
    "workspace exists — there is no work to save yet",
    ("backend.services.orchestrator", "_found_the_regular_version"): "a fast fix moved to a regular version goes on "
    "with the same brief; its unsaved changes are that work, not someone else's",
}
CHECK = ("backend.services.unsaved_work", "refusal")
FOUNDS = ("backend.services.version", "create")


def _module_of(path: Path) -> str:
    return ".".join(path.relative_to(REPO).with_suffix("").parts)


def _aliases(tree: ast.Module) -> dict[str, tuple[str, str | None]]:
    """A name of this module → the module it stands for (``None``) or the function imported under it."""
    names: dict[str, tuple[str, str | None]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("backend."):
            for alias in node.names:
                names[alias.asname or alias.name] = (
                    (f"{node.module}.{alias.name}", None)
                    if node.module in ("backend.services", "backend.api.routes")
                    else (node.module, alias.name)
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("backend.") and alias.asname:
                    names[alias.asname] = (alias.name, None)
    return names


def _functions() -> tuple[dict[tuple[str, str], set[tuple[str, str]]], set[tuple[str, str]]]:
    """Every function of the application → every function it names (a call or a reference handed on, as to
    ``run_blocking``), and the set of routes."""
    functions: dict[tuple[str, str], set[tuple[str, str]]] = {}
    routes: set[tuple[str, str]] = set()
    for path in sorted((REPO / "backend").rglob("*.py")):
        if "tests" in path.parts:
            continue
        module = _module_of(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        aliases = _aliases(tree)
        local = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            named: set[tuple[str, str]] = set()
            for node in ast.walk(fn):
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                    owner, function = aliases.get(node.value.id, (None, None))
                    if owner and function is None:
                        named.add((owner, node.attr))
                elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                    owner, function = aliases.get(node.id, (None, None))
                    if owner and function:
                        named.add((owner, function))
                    elif node.id in local:
                        named.add((module, node.id))
            functions[(module, fn.name)] = named
            if any(
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Attribute)
                and isinstance(d.func.value, ast.Name)
                and d.func.value.id == "router"
                for d in fn.decorator_list
            ):
                routes.add((module, fn.name))
    return functions, routes


def _founders(functions, *, stop: set[tuple[str, str]]) -> set[tuple[str, str]]:
    """Every function that founds a version, itself or through what it names — not through the ones in ``stop``."""
    founders = {FOUNDS}
    changed = True
    while changed:
        changed = False
        for key, named in functions.items():
            if key not in founders and key not in stop and named & founders:
                founders.add(key)
                changed = True
    return founders


def test_no_route_founds_a_version_without_asking_about_unsaved_work_first():
    functions, routes = _functions()
    checked = {key for key, named in functions.items() if CHECK in named}

    every_founder = _founders(functions, stop=set())
    unchecked = _founders(functions, stop=checked | set(EXEMPT))

    assert {
        ("backend.api.routes.versions", "create_version"),
        ("backend.api.routes.pipeline", "start_fast_fix"),
    } <= every_founder & routes, "the scan does not see the routes that found a version — it would prove nothing"
    assert set(EXEMPT) <= every_founder, "an exemption names a function that no longer founds a version"
    assert sorted(unchecked & routes) == []
