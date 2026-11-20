"""DEV-449 — the version is renamed to 1.0.0 when the Manažér accepts the UAT that opens its first PROD, and PROD is
built from that commit once its CI passed — not renamed after the deploy with a commit nobody checked.

20.11.2026, Orthodox Hermenia: the first PROD built the checked commit 24628d8 (v0.2.0); after the successful deploy the
cockpit renamed the version to 1.0.0 and pushed ee718b7 („docs(release-notes): graduate notes to v1.0.0“) straight to
main. The app's own test named v0.2.0 and main turned red (CI #111) — the third time this step did so (DEV-307,
DEV-418).
PROD reported 1.0.0 while its Aktualizácie said v0.2.0. Director: „Súhlasím, založ tikety a sprav z nich balík“.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from backend.core.errors import ConflictError
from backend.services import ci_status, claude_agent, orchestrator
from backend.services import deploy as deploy_service
from tests.test_a_graduation_commit_reaches_github import _checkout_with_notes, _git
from tests.test_deploy_service import _deploy, _FakeRunner, _make_customer, _make_project, _make_user, _make_version

CHARTER = Path(__file__).resolve().parents[1] / "templates" / "ai-agent-charter.md"


async def _accepted(db_session, monkeypatch, tmp_path, *, prod_before: bool = False):
    """A project whose 0.1.0 runs on the customer's UAT and is accepted there (optionally after an earlier PROD)."""
    projects_root = tmp_path / "projects"
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", projects_root)
    user = _make_user(db_session)
    project = _make_project(db_session, user=user)
    origin = tmp_path / "origin.git"
    work = _checkout_with_notes(projects_root, project.slug, origin)
    version = _make_version(db_session, project, "0.1.0")
    _git(work, "push", "-q", "origin", "main")
    customer = _make_customer(db_session, project)
    runner = _FakeRunner()
    if prod_before:
        monkeypatch.setattr(deploy_service, "project_had_prod_deploy", lambda db, project_id: True)
    await _deploy(db_session, customer, version_number="0.1.0", environment="uat", actor=user, runner=runner)
    event = await asyncio.to_thread(deploy_service.accept, db_session, customer.id, "0.1.0", user.id)
    return event, version, customer, user, runner, work, origin


@pytest.mark.asyncio
async def test_the_acceptance_that_opens_the_first_prod_renames_and_publishes(db_session, monkeypatch, tmp_path):
    event, version, _customer, _user, _runner, work, origin = await _accepted(db_session, monkeypatch, tmp_path)

    assert (version.version_number, version.graduated_from, version.status) == ("1.0.0", "0.1.0", "released")
    assert (work / "docs" / "specs" / "versions" / "v1.0.0" / "RELEASE_NOTES.md").is_file()
    assert _git(work, "log", "-1", "--format=%s") == "docs(release-notes): graduate notes to v1.0.0"
    assert version.graduation_commit == _git(work, "rev-parse", "HEAD")
    assert (
        _git(work, "rev-parse", "HEAD")
        == subprocess.run(
            ["git", "--git-dir", str(origin), "rev-parse", "main"], capture_output=True, text=True, check=True
        ).stdout.strip()
    ), "the commit reached GitHub, so its CI runs before PROD"
    assert event.version_number == "1.0.0"
    assert event.detail.endswith(deploy_service.GRADUATED_AT_ACCEPTANCE.format(version="1.0.0"))


@pytest.mark.asyncio
async def test_prod_is_built_from_the_graduation_commit(db_session, monkeypatch, tmp_path):
    _event, version, _customer, _user, _runner, work, _origin = await _accepted(db_session, monkeypatch, tmp_path)

    assert orchestrator.deployable_commit(db_session, version.id) == (_git(work, "rev-parse", "HEAD"), "commit")


@pytest.mark.asyncio
async def test_prod_waits_while_the_graduation_commits_ci_runs(db_session, monkeypatch, tmp_path):
    _event, version, customer, user, runner, work, _origin = await _accepted(db_session, monkeypatch, tmp_path)
    graduation = _git(work, "rev-parse", "HEAD")
    asked: list[str] = []

    async def running(root, commit, cerstvy=False):
        asked.append(commit)
        return ci_status.StavZostavenia(stav="pending", detail="kontroly bežia", sha=commit, bezi=True)

    monkeypatch.setattr(ci_status, "stav_commitu", running)

    with pytest.raises(ConflictError, match="ešte bežia"):
        await _deploy(db_session, customer, version_number="0.1.0", environment="prod", actor=user, runner=runner)

    assert asked == [graduation], "the CI that counts is the graduation commit's"


@pytest.mark.asyncio
async def test_a_project_that_had_prod_is_not_renamed_at_acceptance(db_session, monkeypatch, tmp_path):
    event, version, *_rest, work, _origin = await _accepted(db_session, monkeypatch, tmp_path, prod_before=True)

    assert (version.version_number, version.graduated_from, version.graduation_commit) == ("0.1.0", None, None)
    assert not (work / "docs" / "specs" / "versions" / "v1.0.0").exists()
    assert event.version_number == "0.1.0"


def test_the_charter_keeps_version_numbers_out_of_code_and_tests():
    text = " ".join(CHARTER.read_text(encoding="utf-8").split())

    assert "Číslo verzie nepíš do kódu ani do skúšok" in text
    assert "prvé nasadenie do PROD premenuje poznámky k vydaniu na 1.0.0" in text
