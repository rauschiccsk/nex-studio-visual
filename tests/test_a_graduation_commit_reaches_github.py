"""DEV-147 — the commit a first PROD deploy makes (release notes moved to v1.0.0) reaches GitHub, or the deploy says so.

15.10.2026, Career Asistent v1.0.0 to PROD: graduation moved ``docs/specs/versions/v0.1.1`` to ``v1.0.0`` and committed
it as 11a225d — and nothing ever pushed it. The checkout stayed one commit ahead of GitHub, CI never ran for it, and
since 19.10.2026 the cockpit says "kód ešte nie je na GitHube vo vetve main". NEX Websites' identical commit (431c0a4)
reached GitHub only because a later, unrelated commit was pushed on top of it.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

from backend.services import claude_agent, orchestrator
from tests.test_deploy_service import _deploy, _FakeRunner, _make_customer, _make_project, _make_user, _make_version


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


def _checkout_with_notes(projects_root: Path, slug: str, origin: Path) -> Path:
    """The project's checkout as founding leaves it: on ``main``, everything on ``origin``, notes of v0.1.0."""
    work = projects_root / slug
    work.mkdir(parents=True)
    _git(work, "init", "-q", "-b", "main")
    _git(work, "config", "user.email", "studio@isnex.eu")
    _git(work, "config", "user.name", "NEX Studio")
    notes = work / "docs" / "specs" / "versions" / "v0.1.0"
    notes.mkdir(parents=True)
    (notes / "RELEASE_NOTES.md").write_text("## v0.1.0\n\n- Prvá verzia.\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "docs(release-notes): v0.1.0")
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "-q", "origin", "main")
    return work


async def _first_prod_deploy(db_session, monkeypatch, tmp_path, *, push_to: Path | None = None):
    projects_root = tmp_path / "projects"
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", projects_root)
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    origin = tmp_path / "origin.git"
    work = _checkout_with_notes(projects_root, project.slug, origin)
    if push_to is not None:
        _git(work, "remote", "set-url", "--push", "origin", str(push_to))
    _make_version(db_session, project, "0.1.0")
    customer = _make_customer(db_session, project)
    runner = _FakeRunner()
    await _deploy(db_session, customer, version_number="0.1.0", environment="uat", actor=user, runner=runner)
    from backend.services import deploy as deploy_service

    deploy_service.accept(db_session, customer.id, "0.1.0", user.id)
    event, _url, bumped = await _deploy(
        db_session, customer, version_number="0.1.0", environment="prod", actor=user, runner=runner
    )
    return event, bumped, work, origin


@pytest.mark.asyncio
async def test_the_moved_release_notes_reach_github(db_session, monkeypatch, tmp_path) -> None:
    event, bumped, work, origin = await _first_prod_deploy(db_session, monkeypatch, tmp_path)

    assert bumped == "1.0.0"
    assert _git(work, "log", "-1", "--format=%s") == "docs(release-notes): graduate notes to v1.0.0"
    assert _git(origin, "log", "-1", "--format=%s", "main") == "docs(release-notes): graduate notes to v1.0.0"
    assert _git(work, "rev-list", "--count", "origin/main..HEAD") == "0"
    assert not [w for w in event.warnings if "GitHub" in w], event.warnings


@pytest.mark.asyncio
async def test_a_refused_push_is_said_and_the_deploy_still_stands(db_session, monkeypatch, tmp_path) -> None:
    event, bumped, work, _origin = await _first_prod_deploy(
        db_session, monkeypatch, tmp_path, push_to=tmp_path / "missing.git"
    )

    assert bumped == "1.0.0" and event.status == "ok"
    said = [w for w in event.warnings if w.startswith("Poznámky k vydaniu sa presunuli do v1.0.0")]
    assert len(said) == 1, event.warnings
    assert said[0].startswith(
        "Poznámky k vydaniu sa presunuli do v1.0.0, ale zmena ostala len na serveri — na GitHub sa neodoslala ("
    )
    assert said[0].endswith("). Kontroly (CI) pre ňu nebežia, kým sa neodošle."), said[0]
    assert _git(work, "rev-list", "--count", "origin/main..HEAD") == "1"


@pytest.mark.asyncio
async def test_the_push_runs_off_the_main_loop(db_session, monkeypatch, tmp_path) -> None:
    """A push waits on the network — on the main loop it would freeze the whole cockpit (ICCINT-74)."""
    loop_thread = threading.current_thread()
    seen: list[threading.Thread] = []
    real_push = orchestrator._push_branch

    def push(root):
        seen.append(threading.current_thread())
        return real_push(root)

    monkeypatch.setattr(orchestrator, "_push_branch", push)

    await _first_prod_deploy(db_session, monkeypatch, tmp_path)

    assert seen and all(t is not loop_thread for t in seen), seen


@pytest.mark.asyncio
async def test_nothing_waiting_is_not_pushed(db_session, monkeypatch, tmp_path) -> None:
    """A later PROD deploy graduates nothing — and a checkout with nothing to publish is not pushed at all."""
    pushes: list[Path] = []
    monkeypatch.setattr(orchestrator, "_push_branch", lambda root: pushes.append(root) or (True, "main", None))
    event, _bumped, work, _origin = await _first_prod_deploy(db_session, monkeypatch, tmp_path)
    pushes.clear()
    _git(work, "push", "-q", "origin", "main")  # the stand-in push above did not really push

    from backend.services import deploy as deploy_service

    customer_id = event.customer_id
    user_id = event.actor_id
    second = await deploy_service.deploy(
        db_session,
        customer_id,
        version_number="1.0.0",
        environment="prod",
        actor_id=user_id,
        force_fresh=False,
        deploy_runner=_FakeRunner(),
    )

    assert second[2] is None, "only the first PROD deploy graduates"
    assert pushes == []


def test_a_checkout_with_nothing_waiting_is_not_pushed(monkeypatch, tmp_path) -> None:
    """Nothing to publish → GitHub is not asked at all, so a deploy does not depend on it answering."""
    from backend.services import deploy as deploy_service

    work = _checkout_with_notes(tmp_path / "projects", "p", tmp_path / "origin.git")
    monkeypatch.setattr(orchestrator, "_push_branch", lambda root: pytest.fail("pushed with nothing waiting"))

    assert deploy_service._publish_graduation(work, "1.0.0") is None


def test_a_checkout_without_github_is_not_a_failure(tmp_path) -> None:
    """No ``origin`` to publish to — nothing the Manažér can act on, so nothing is said."""
    from backend.services import deploy as deploy_service

    work = _checkout_with_notes(tmp_path / "projects", "p", tmp_path / "origin.git")
    _git(work, "remote", "remove", "origin")

    assert deploy_service._publish_graduation(work, "1.0.0") is None


@pytest.mark.asyncio
async def test_a_push_that_does_not_finish_in_time_is_said(db_session, monkeypatch, tmp_path) -> None:
    from backend.core import offload

    real = offload.run_blocking

    async def capped(fn, /, *args, cap, **kwargs):
        if fn.__name__ == "_publish_graduation":
            raise offload.BlockingWorkTimedOut("_publish_graduation", cap)
        return await real(fn, *args, cap=cap, **kwargs)

    monkeypatch.setattr(offload, "run_blocking", capped)

    event, _bumped, _work, _origin = await _first_prod_deploy(db_session, monkeypatch, tmp_path)

    assert (
        f"Poznámky k vydaniu sa presunuli do v1.0.0, ale zmena ostala len na serveri — na GitHub sa neodoslala "
        f"(odoslanie neskončilo do {orchestrator.GIT_PUSH_CAP} s). Kontroly (CI) pre ňu nebežia, kým sa neodošle."
    ) in event.warnings
