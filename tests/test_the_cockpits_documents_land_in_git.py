"""DEV-180 — what the cockpit commits of a version's documents lands in git, or the Manažér hears why not.

21.10.2026, Career Asistent 1.1.0: the approval of the Vizuál committed the screens and the documents in one commit and
gave it 15 s. The project's pre-commit check (type-check and eslint of both frontends) took 18 s, the commit was killed
and nobody was told. The Špecifikácia, the Návrh and the database structure stayed on disk only; from the first task on
CI failed on main at the agent's test that reads the database structure, 30 tasks long. The Návrh's own commit had not
landed either — a plain ``git commit`` takes the whole index, and with someone else's frontend change waiting in it, the
check of that frontend ran over a commit of documents. Director 22.10.2026: „Súhlasím“.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import time
from pathlib import Path

import pytest

from backend.config.settings import settings
from backend.core.offload import BlockingWorkTimedOut
from backend.services import claude_agent, cockpit_commit, orchestrator
from backend.tests.test_the_cockpit_cannot_be_suffocated_by_one_turn import _calls_on_the_loop
from tests.test_orchestrator_v2_verifikacia import _make_version, _msgs, _seed_verifikacia
from tests.test_release_note_commit_rebuilds_what_the_app_derives import (  # noqa: F401 — fixtures used by name
    _generator,
    project,
    writes_the_note,
)

BACKEND = pathlib.Path(orchestrator.__file__).resolve().parents[1]
SPEC = "docs/specs/versions/v1.1.0/specification.md"
SCHEMA = "docs/specs/versions/v1.1.0/DATABASE_SCHEMAS.md"
SCREEN = "frontend/src/Settings.tsx"
FAILED_LINE = "❌ frontend type-check FAILED"

#: The project's pre-commit check as Career Asistent has it (K-005): a staged frontend file costs that frontend's check.
HOOK = """#!/usr/bin/env bash
STAGED=$(git diff --cached --name-only --diff-filter=ACMRD)
if grep -q '^frontend/' <<< "$STAGED"; then
    echo "pre-commit: frontend — type-check"
    sleep {seconds}
    touch "$(git rev-parse --git-dir)/check-ended"
    {verdict}
fi
echo "pre-commit: ✓ all checks passed"
"""


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


def _write(root: Path, rel: str, text: str = "x\n") -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding="utf-8")


def _project(root: Path, *, seconds: float = 0, fails: bool = False, origin: Path | None = None) -> Path:
    root.mkdir(parents=True)
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "studio@isnex.eu")
    _git(root, "config", "user.name", "NEX Studio")
    _write(root, "README.md")
    _write(
        root,
        ".githooks/pre-commit",
        HOOK.format(
            seconds=seconds, verdict=f'echo "{FAILED_LINE}"; echo "npm error code 2" >&2; exit 1' if fails else ":"
        ),
    )
    (root / ".githooks" / "pre-commit").chmod(0o755)
    _git(root, "config", "core.hooksPath", ".githooks")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "start")
    if origin is not None:
        subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(origin)], check=True)
        _git(root, "remote", "add", "origin", str(origin))
        _git(root, "push", "-q", "origin", "main")
    return root


def _head_files(root: Path) -> list[str]:
    return sorted(_git(root, "show", "--name-only", "--format=", "HEAD").split())


def _staged(root: Path) -> list[str]:
    return sorted(_git(root, "diff", "--cached", "--name-only").split())


# ── one commit of the cockpit's ────────────────────────────────────────────────────────────────────────────────────


def test_the_documents_land_although_someone_elses_frontend_change_waits_in_the_index(tmp_path):
    root = _project(tmp_path / "career-asistent", fails=True)
    _write(root, SCREEN)
    _git(root, "add", SCREEN)  # someone else's, waiting — its check fails
    _write(root, SPEC)

    assert orchestrator._commit_navrh_deliverables(root) is None

    assert _head_files(root) == [SPEC]
    assert _git(root, "log", "-1", "--format=%s").strip() == (
        "docs(navrh): Špecifikácia + návrhový dokument (zmrazené pred previerkou)"
    )
    assert _staged(root) == [SCREEN], "someone else's change was swept into the cockpit's commit"


def test_a_check_slower_than_its_time_is_said_and_one_with_time_enough_lands(tmp_path, monkeypatch):
    root = _project(tmp_path / "career-asistent", seconds=2)
    _write(root, SCREEN)
    _write(root, SPEC)
    head = _git(root, "rev-parse", "HEAD")

    monkeypatch.setattr(settings, "project_commit_timeout_seconds", 1)
    assert orchestrator._commit_vizual_changes(root, ("frontend",)) == cockpit_commit.TOO_LONG.format(seconds=1)
    assert _git(root, "rev-parse", "HEAD") == head
    assert not (root / ".git" / "index.lock").exists(), "the killed commit left the project locked for every git call"
    time.sleep(3)
    assert not (root / ".git" / "check-ended").exists(), "the check of a killed commit went on running"

    monkeypatch.setattr(settings, "project_commit_timeout_seconds", 30)
    assert orchestrator._commit_vizual_changes(root, ("frontend",)) is None
    assert _head_files(root) == [SPEC, SCREEN]
    assert _git(root, "status", "--porcelain") == ""


def test_a_failed_check_is_named_by_the_line_that_says_what_failed(tmp_path):
    root = _project(tmp_path / "career-asistent", fails=True)
    _write(root, SCREEN)
    _write(root, SPEC)
    head = _git(root, "rev-parse", "HEAD")

    assert orchestrator._commit_vizual_changes(root, ("frontend",)) == cockpit_commit.REFUSED.format(reason=FAILED_LINE)
    assert _git(root, "rev-parse", "HEAD") == head


def test_nothing_to_commit_is_no_commit_and_no_reason(tmp_path):
    root = _project(tmp_path / "career-asistent")
    _write(root, SPEC)
    assert orchestrator._commit_navrh_deliverables(root) is None
    head = _git(root, "rev-parse", "HEAD")

    assert orchestrator._commit_navrh_deliverables(root) is None
    assert _git(root, "rev-parse", "HEAD") == head
    assert cockpit_commit.commit(tmp_path / "no-git", ["docs"], "x") is None


def test_a_file_gone_from_the_disk_is_committed_gone(tmp_path):
    root = _project(tmp_path / "career-asistent")
    _write(root, SPEC)
    _git(root, "add", SPEC)
    _git(root, "commit", "-q", "-m", "spec")
    (root / SPEC).unlink()
    _write(root, SCHEMA, "a table\n")

    assert cockpit_commit.commit(root, [SPEC, SCHEMA, "docs/never-was.md"], "docs: move") is None

    assert _git(root, "show", "--name-status", "--format=", "HEAD").split() == ["A", SCHEMA, "D", SPEC]


def test_files_left_after_the_commit_are_said(tmp_path, monkeypatch):
    root = _project(tmp_path / "career-asistent")
    _write(root, SPEC)
    real = cockpit_commit._commit

    def _commit_while_the_agent_writes(*args):
        done = real(*args)
        _write(root, SCHEMA)  # written while the check ran
        return done

    monkeypatch.setattr(cockpit_commit, "_commit", _commit_while_the_agent_writes)

    assert cockpit_commit.commit(root, ["docs"], "docs") == cockpit_commit.LEFT_OVER.format(count=1)


def test_git_that_cannot_run_is_a_reason_not_a_crash(tmp_path, monkeypatch):
    root = _project(tmp_path / "career-asistent")
    _write(root, SPEC)

    def _no_git(*a, **k):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", _no_git)
    assert cockpit_commit.commit(root, ["docs"], "docs") == cockpit_commit.NOT_RUN


# ── the Manažér hears it ───────────────────────────────────────────────────────────────────────────────────────────


async def test_what_did_not_land_is_said_to_the_manazer(db_session):
    version, _ = _make_version(db_session)
    _seed_verifikacia(db_session, version.id)
    heard = []

    async def _hear(msg):
        heard.append(msg)

    why = cockpit_commit.REFUSED.format(reason=FAILED_LINE)
    assert (
        await orchestrator._commit_off_the_loop(
            db_session, version.id, "Dokumenty Návrhu", lambda: why, on_message=_hear
        )
        == why
    )

    told = [m for m in _msgs(db_session, version.id) if m.recipient == "manazer"]
    assert [m.content for m in told] == [
        "Dokumenty Návrhu sa nezapísali do gitu — kontrola projektu pred zápisom neprešla "
        f"({FAILED_LINE}). Ostali len na disku kokpitu, takže kontroly na GitHube (CI) ich nevidia. Stavba pokračuje."
    ]
    assert heard == told

    assert await orchestrator._commit_off_the_loop(db_session, version.id, "Dokumenty Návrhu", lambda: None) is None
    assert len([m for m in _msgs(db_session, version.id) if m.recipient == "manazer"]) == 1


async def test_a_commit_that_never_ends_is_said_too(db_session, monkeypatch):
    version, _ = _make_version(db_session)
    _seed_verifikacia(db_session, version.id)

    async def _hangs(fn, *args, cap, **kw):
        raise BlockingWorkTimedOut("x")

    monkeypatch.setattr(orchestrator, "run_blocking", _hangs)

    why = await orchestrator._commit_off_the_loop(db_session, version.id, "Dokumenty Návrhu", lambda: None)

    assert why == f"zápis neskončil do {cockpit_commit.CAP} s"
    assert [m.payload["documents_not_in_git"] for m in _msgs(db_session, version.id)] == [why]


# ── before the verification ────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def career_asistent(tmp_path, monkeypatch):
    """Career Asistent on 22.10.2026: the documents of 1.1.0 staged by the killed commit, a frontend change of someone
    else's waiting with them, a check that fails on it, and GitHub."""
    projects = tmp_path / "projects"
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", projects)
    origin = tmp_path / "origin.git"
    root = _project(projects / "career-asistent", fails=True, origin=origin)
    for rel in (SPEC, SCHEMA, SCREEN):
        _write(root, rel)
        _git(root, "add", rel)
    return root, origin


async def test_the_verification_commits_the_documents_left_on_disk_and_sends_them_to_github(
    db_session, career_asistent
):
    root, origin = career_asistent
    version, _ = _make_version(db_session)
    _seed_verifikacia(db_session, version.id)

    await orchestrator._documents_into_git_before_verification(db_session, version.id, "career-asistent", "1.1.0")

    assert (
        _git(root, "log", "-1", "--format=%s").strip() == "docs: the documents of version 1.1.0 before its verification"
    )
    assert _head_files(root) == sorted([SPEC, SCHEMA])
    assert _git(origin, "rev-parse", "refs/heads/main") == _git(root, "rev-parse", "HEAD")
    assert _staged(root) == [SCREEN]
    assert [m for m in _msgs(db_session, version.id) if m.recipient == "manazer"] == []

    head = _git(root, "rev-parse", "HEAD")
    await orchestrator._documents_into_git_before_verification(db_session, version.id, "career-asistent", "1.1.0")
    assert _git(root, "rev-parse", "HEAD") == head


async def test_documents_github_did_not_take_are_said(db_session, career_asistent):
    root, origin = career_asistent
    _git(root, "remote", "set-url", "origin", str(origin.parent / "gone.git"))
    version, _ = _make_version(db_session)
    _seed_verifikacia(db_session, version.id)

    await orchestrator._documents_into_git_before_verification(db_session, version.id, "career-asistent", "1.1.0")

    assert _head_files(root) == sorted([SPEC, SCHEMA])
    told = [m.content for m in _msgs(db_session, version.id) if m.recipient == "manazer"]
    assert len(told) == 1
    assert told[0].startswith("Dokumenty verzie 1.1.0 sú v gite, ale na GitHub sa nepodarilo odoslať (")
    assert told[0].endswith("Kontroly na GitHube ich zatiaľ nevidia; kokpit ich odošle pri ďalšom odosielaní.")


def test_the_verification_puts_the_documents_into_git_before_it_builds_anything():
    tree = ast.parse((BACKEND / "services" / "orchestrator.py").read_text(encoding="utf-8"))
    round_ = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_run_verifikacia_round")
    calls = [
        (c.lineno, c.func.id)
        for c in ast.walk(round_)
        if isinstance(c, ast.Call)
        and isinstance(c.func, ast.Name)
        and c.func.id
        in {"_documents_into_git_before_verification", "_write_release_note_to_disk", "_run_release_smoke"}
    ]
    assert [name for _, name in sorted(calls)] == [
        "_documents_into_git_before_verification",
        "_write_release_note_to_disk",
        "_run_release_smoke",
    ]


# ── off the main loop ──────────────────────────────────────────────────────────────────────────────────────────────


def test_no_async_function_waits_for_a_documents_commit_on_the_main_loop():
    """The commit waits on the project's check for minutes — only a thread may wait for it (ICCINT-74). The stall
    guard looks across modules; the orchestrator's own wrappers are called by bare name, so they are looked for here:
    every sync function of the orchestrator that commits through :mod:`cockpit_commit`."""
    tree = ast.parse((BACKEND / "services" / "orchestrator.py").read_text(encoding="utf-8"))
    committing = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(c, ast.Call)
            and isinstance(c.func, ast.Attribute)
            and isinstance(c.func.value, ast.Name)
            and (c.func.value.id, c.func.attr) == ("cockpit_commit", "commit")
            for c in ast.walk(node)
        )
    }
    assert committing == {"_commit_vizual_changes", "_commit_navrh_deliverables"}

    on_the_loop = sorted(
        f"{node.name}:{call.lineno} → {call.func.id}"
        for path in sorted(BACKEND.rglob("*.py"))
        if "tests" not in path.parts
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.AsyncFunctionDef)
        for call in _calls_on_the_loop(node)
        if isinstance(call.func, ast.Name) and call.func.id in committing
    )
    assert on_the_loop == []


# ── the release notes ──────────────────────────────────────────────────────────────────────────────────────────────


async def test_release_notes_whose_check_fails_are_said_to_the_manazer(
    db_session,
    project,  # noqa: F811 — the fixture of the release-note tests
    writes_the_note,  # noqa: F811 — the fixture of the release-note tests
    monkeypatch,
):
    """The note goes with ``frontend/src/lib/release-notes.ts`` (DEV-104), so the frontend's check runs over it."""
    _write(project, ".githooks/pre-commit", HOOK.format(seconds=0, verdict=f'echo "{FAILED_LINE}"; exit 1'))
    (project / ".githooks" / "pre-commit").chmod(0o755)
    _git(project, "config", "core.hooksPath", ".githooks")
    version, _ = _make_version(db_session)
    _seed_verifikacia(db_session, version.id)
    _generator(monkeypatch)
    head = _git(project, "rev-parse", "HEAD")

    await orchestrator._commit_release_note(db_session, version.id, project, "1.5.2")

    assert _git(project, "rev-parse", "HEAD") == head
    assert [m.content for m in _msgs(db_session, version.id) if m.recipient == "manazer"] == [
        orchestrator.DOCUMENTS_NOT_IN_GIT.format(
            what="Poznámky k vydaniu v1.5.2", why=cockpit_commit.REFUSED.format(reason=FAILED_LINE)
        )
    ]
