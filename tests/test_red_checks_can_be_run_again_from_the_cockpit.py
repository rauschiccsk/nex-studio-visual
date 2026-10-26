"""DEV-234 — red checks of a commit the Auditor passed can be run again from the cockpit; green lets the PASS stand.

26.10.2026, Author Asistent 0.1.0: the Auditor passed the version („Verifikácia prešla“, 20:20 UTC), and the cockpit
stopped it all the same (ICCINT-64) because the project's CI on the same commit was red — the build could not download
a library („Could not find a version that satisfies the requirement sqlalchemy>=2.0.36 (from versions: none)“); the
dependencies had not changed since the green run two hours before. The card recommended „Znova over bez opravy“ — a
whole new Auditor round (20–40 min) that would have ended on the same red run, because nothing ran the checks again.
The Director re-ran them on GitHub by hand, they passed, and he still had to start a new Auditor round. Director:
„Áno, súhlasím, založ tiket a oprav to“.

Pinned: when only red CI stopped a commit the Auditor passed and that commit is still HEAD, the card — the round cap's
too — offers „Spustiť kontroly znova“; it re-runs the failed jobs of the commit's red runs and the version waits for
them with no Auditor round; green records the Auditor's PASS again and the version goes to Hotovo; red brings the
card back; a moved HEAD or an Auditor FAIL gets no such option, and the engine refuses it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from backend.config.settings import settings
from backend.services import orchestrator
from backend.services.orchestrator import OrchestratorError
from tests.test_orchestrator_v2_verifikacia import (
    _ban_deploy_calls,
    _make_version,
    _msgs,
    _seed_verifikacia,
    _stub_auditor,
    _stub_smoke,
    _verdict_fail,
    _verdict_pass,
)

pytestmark = pytest.mark.asyncio

HEAD = "deadbeefcafe"
RED = [
    {"status": "completed", "conclusion": "failure", "databaseId": 42, "workflowName": "CI", "headBranch": "main"},
    {"status": "completed", "conclusion": "success", "databaseId": 41, "workflowName": "Lint", "headBranch": "main"},
]
RUNNING = [{"status": "in_progress", "conclusion": None, "databaseId": 42, "workflowName": "CI", "headBranch": "main"}]
GREEN = [
    {"status": "completed", "conclusion": "success", "databaseId": 42, "workflowName": "CI", "headBranch": "main"},
    {"status": "completed", "conclusion": "success", "databaseId": 41, "workflowName": "Lint", "headBranch": "main"},
]


class _GitHub:
    """GitHub as the one subprocess seam of the CI floor: ``rows`` is what it says now; re-runs are recorded."""

    def __init__(self, monkeypatch, rows) -> None:
        self.rows = rows
        self.head = HEAD
        self.reruns: list[list[str]] = []
        self.refuse_rerun = False
        monkeypatch.setattr(orchestrator, "_project_has_ci", lambda root: True)
        monkeypatch.setattr(orchestrator, "_repo_head", lambda root: self.head)
        monkeypatch.setattr(orchestrator, "CI_RUN_APPEAR_INTERVAL", 0)
        monkeypatch.setattr(orchestrator, "CI_RUN_FINISH_INTERVAL", 0)
        monkeypatch.setattr(orchestrator, "_run_publish_step", self.step)

    async def step(self, cmd, timeout):
        if "config" in cmd:
            return 0, "https://github.com/rauschiccsk/nex-demo.git\n"
        if cmd[:3] == ["gh", "run", "rerun"]:
            if self.refuse_rerun:
                return 1, "HTTP 403: Resource not accessible by personal access token"
            self.reruns.append(cmd)
            return 0, ""
        return 0, json.dumps(self.rows)


class _Auditor:
    """Counts the Auditor's turns after the first one — a re-run of the checks must not start another."""

    def __init__(self, monkeypatch) -> None:
        self.turns = 0

        async def _turn(db, **_kw):
            self.turns += 1
            return _verdict_pass()

        monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _turn)


def _auditor_that_writes(monkeypatch, block) -> None:
    """The Auditor's turn as ``invoke_agent`` records it — its report and the turn's usage on its verdict message."""

    async def _turn(db, *, version_id, **_kw):
        orchestrator._record_message(
            db,
            version_id=version_id,
            stage="verifikacia",
            author="auditor",
            recipient="manazer",
            kind="verdict",
            content=block.summary,
            payload={"report": "## Auditor\nVšetko overené.", "usage": {"output_tokens": 1200}},
        )
        return block

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _turn)


async def _passed_but_ci_red(db_session, monkeypatch, *, iteration=0):
    github = _GitHub(monkeypatch, RED)
    version, _project = _make_version(db_session)
    _seed_verifikacia(db_session, version.id, iteration=iteration)
    _auditor_that_writes(monkeypatch, _verdict_pass())
    _stub_smoke(monkeypatch)
    _ban_deploy_calls(monkeypatch)
    state = await orchestrator.run_dispatch(db_session, version.id)
    return version, state, github


def _card(db_session, version_id) -> dict:
    consultation, _seq = orchestrator._latest_consultation(db_session, version_id)
    return consultation


def _options(db_session, version_id) -> dict[str, bool]:
    decision = _card(db_session, version_id)["decisions"][0]
    return {o["id"]: o.get("recommended", False) for o in decision["options"]}


async def _choose(db_session, version_id, option_id):
    card = _card(db_session, version_id)
    return await orchestrator.apply_action(
        db_session,
        version_id=version_id,
        action="decide",
        payload={"decision_key": card["decisions"][0]["key"], "option_id": option_id},
    )


def _latest_verdict(db_session, version_id):
    return [m for m in _msgs(db_session, version_id) if m.kind == "verdict"][-1]


async def test_the_card_offers_running_the_checks_again(db_session, monkeypatch):
    version, state, _github = await _passed_but_ci_red(db_session, monkeypatch)

    assert (state.current_stage, state.status, state.block_reason) == ("programovanie", "blocked", "decision_needed")
    options = _options(db_session, version.id)
    assert options.get("rerun_ci") is True, options
    assert [o for o, recommended in options.items() if recommended] == ["rerun_ci"]
    assert "spustiť kontroly znova" in _card(db_session, version.id)["decisions"][0]["rationale"].lower()


async def test_choosing_it_reruns_the_red_run_and_waits_without_the_auditor(db_session, monkeypatch):
    version, state, github = await _passed_but_ci_red(db_session, monkeypatch)
    iteration = state.iteration
    auditor = _Auditor(monkeypatch)

    new = await _choose(db_session, version.id, "rerun_ci")

    assert github.reruns == [["gh", "run", "rerun", "42", "--failed", "-R", "rauschiccsk/nex-demo"]]
    assert (new.current_stage, new.status, new.block_reason) == ("verifikacia", "awaiting_manazer", None)
    assert new.ci_pending_since is not None and new.iteration == iteration
    assert "kontrolu na GitHube" in new.next_action
    assert "schvalit" not in orchestrator.determine_available_actions(new)
    assert auditor.turns == 0
    note = _msgs(db_session, version.id)[-1]
    assert note.kind == "notification" and "znova" in note.content and "beh 42" in note.content


async def test_green_lets_the_auditors_pass_stand(db_session, monkeypatch):
    version, _state, github = await _passed_but_ci_red(db_session, monkeypatch)
    auditor = _Auditor(monkeypatch)
    await _choose(db_session, version.id, "rerun_ci")
    github.rows = GREEN

    settled = await orchestrator.settle_ci_waits(db_session)

    state = settled[0]
    assert (state.current_stage, state.status, state.ci_pending_since) == ("verifikacia", "awaiting_manazer", None)
    assert orchestrator._verifikacia_passed(db_session, version.id) is True
    assert orchestrator.version_verified(db_session, version.id) == (True, "sha_match")
    assert "schvalit" in orchestrator.determine_available_actions(state)
    verdict = _latest_verdict(db_session, version.id)
    assert verdict.payload["verdict"] == "PASS" and verdict.payload["verified_sha"] == HEAD
    assert "report" not in verdict.payload, "the Auditor's report would be on screen twice"
    assert "usage" not in verdict.payload, "the costs would count the Auditor's turn twice"
    assert verdict.content.startswith("Kontroly projektu po opätovnom spustení prešli")
    assert auditor.turns == 0


async def test_red_again_brings_the_card_back_with_the_same_way_out(db_session, monkeypatch):
    version, _state, github = await _passed_but_ci_red(db_session, monkeypatch)
    await _choose(db_session, version.id, "rerun_ci")

    settled = await orchestrator.settle_ci_waits(db_session)

    state = settled[0]
    assert (state.status, state.block_reason) == ("blocked", "decision_needed")
    assert orchestrator._verifikacia_passed(db_session, version.id) is False
    assert _latest_verdict(db_session, version.id).payload["engine_override"] == "ci_red"
    assert _options(db_session, version.id).get("rerun_ci") is True
    await _choose(db_session, version.id, "rerun_ci")
    assert len(github.reruns) == 2


async def test_a_wait_past_the_cap_is_not_taken_for_green(db_session, monkeypatch):
    version, _state, github = await _passed_but_ci_red(db_session, monkeypatch)
    state = await _choose(db_session, version.id, "rerun_ci")
    github.rows = RUNNING
    state.ci_pending_since = datetime.now(timezone.utc) - timedelta(hours=settings.ci_pending_cap_hours + 1)

    await orchestrator.settle_ci_waits(db_session)

    assert orchestrator._verifikacia_passed(db_session, version.id) is False
    assert (state.status, state.block_reason) == ("blocked", "decision_needed")


async def test_checks_already_green_are_just_waited_for(db_session, monkeypatch):
    """The Director re-ran them on GitHub himself before choosing — nothing to re-run, the PASS stands on green."""
    version, _state, github = await _passed_but_ci_red(db_session, monkeypatch)
    github.rows = GREEN

    new = await _choose(db_session, version.id, "rerun_ci")
    await orchestrator.settle_ci_waits(db_session)

    assert github.reruns == [] and new.ci_pending_since is None
    assert orchestrator._verifikacia_passed(db_session, version.id) is True


async def test_the_round_cap_card_offers_it_too(db_session, monkeypatch):
    version, state, github = await _passed_but_ci_red(db_session, monkeypatch, iteration=orchestrator.AUDITOR_LOOP_MAX)

    assert _card(db_session, version.id)["source"] == "verifikacia_fail"
    assert _options(db_session, version.id).get("rerun_ci") is True
    new = await _choose(db_session, version.id, "rerun_ci")
    assert len(github.reruns) == 1 and (new.current_stage, new.status) == ("verifikacia", "awaiting_manazer")


async def test_a_moved_head_gets_no_rerun(db_session, monkeypatch):
    version, state, github = await _passed_but_ci_red(db_session, monkeypatch)
    github.head = "0123456789ab"

    rebuilt = orchestrator._build_fix_consultation(db_session, version.id, state)
    with pytest.raises(OrchestratorError, match="commit"):
        await _choose(db_session, version.id, "rerun_ci")

    assert "rerun_ci" not in {o.id for o in rebuilt.decisions[0].options}
    assert github.reruns == []


async def test_an_auditor_fail_gets_no_rerun(db_session, monkeypatch):
    github = _GitHub(monkeypatch, GREEN)
    version, _project = _make_version(db_session)
    _seed_verifikacia(db_session, version.id)
    _stub_auditor(monkeypatch, _verdict_fail(["editor drží odseky zle"]))
    _stub_smoke(monkeypatch)

    await orchestrator.run_dispatch(db_session, version.id)

    assert "rerun_ci" not in _options(db_session, version.id)
    assert github.reruns == []


async def test_a_refused_rerun_says_so_and_changes_nothing(db_session, monkeypatch):
    version, state, github = await _passed_but_ci_red(db_session, monkeypatch)
    github.refuse_rerun = True

    with pytest.raises(OrchestratorError, match="GitHub"):
        await _choose(db_session, version.id, "rerun_ci")

    assert (state.current_stage, state.status, state.ci_pending_since) == ("programovanie", "blocked", None)
