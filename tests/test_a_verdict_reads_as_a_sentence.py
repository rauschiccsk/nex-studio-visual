"""DEV-236 — a verdict the cockpit writes reads as a sentence, never a bare „FAIL“ or „PASS“.

26.10.2026, Author Asistent 0.1.0: the Auditor passed the version, the cockpit floored it for red project checks
(ICCINT-64), and the Manažér's thread showed a bubble with one word in it — „FAIL“ (message 5843). Why stood only in
the technical detail and on the card below. The manual verdict wrote a bare „PASS“/„FAIL“ the same way, also when
the cockpit floored a manual PASS for a red launch check. Director: „Áno, súhlasím, založ tiket a oprav to“.

Pinned: the CI floor — in the round and when the CI watcher settles a wait — and the manual verdict write a sentence
that says what happened; the payload the gates read is unchanged.
"""

from __future__ import annotations

import pytest

from backend.services import orchestrator
from tests.test_a_version_waits_for_its_ci import RED, _passed_while_ci_runs
from tests.test_orchestrator_v2_verifikacia import (
    _fake_ci,
    _make_version,
    _msgs,
    _pass_verdict,
    _seed_done_tasks,
    _seed_verifikacia,
)

pytestmark = pytest.mark.asyncio

CI_RED = (
    "Verifikácia neprešla pre kontroly projektu na GitHube — CI zlyhalo (beh 42, failure). Kým nebudú zelené, "
    "verzia sa nedá vyhlásiť za overenú."
)


def _verdicts(db_session, version_id):
    return [m for m in _msgs(db_session, version_id) if m.kind == "verdict"]


async def test_red_checks_floor_a_pass_with_a_sentence(db_session, monkeypatch):
    _fake_ci(monkeypatch, conclusion="failure")

    last = await _pass_verdict(db_session, monkeypatch)

    assert last.content == CI_RED
    assert (last.payload["verdict"], last.payload["engine_override"]) == ("FAIL", "ci_red")


async def test_the_ci_watcher_writes_the_same_sentence(db_session, monkeypatch):
    version, _state, github = await _passed_while_ci_runs(db_session, monkeypatch)
    github["rows"] = RED

    await orchestrator.settle_ci_waits(db_session)

    last = _verdicts(db_session, version.id)[-1]
    assert last.content.startswith("Verifikácia neprešla pre kontroly projektu na GitHube — CI zlyhalo (postup CI")
    assert last.content.endswith("Kým nebudú zelené, verzia sa nedá vyhlásiť za overenú.")


@pytest.mark.parametrize(
    ("verdict", "sentence"),
    [("PASS", "Verifikácia prešla (ručné rozhodnutie)."), ("FAIL", "Verifikácia neprešla (ručné rozhodnutie).")],
)
async def test_a_manual_verdict_is_a_sentence(db_session, monkeypatch, verdict, sentence):
    _fake_ci(monkeypatch, conclusion="success")
    version, project = _make_version(db_session, project_dial="po_kazdej_faze")
    state = _seed_verifikacia(db_session, version.id, iteration=0)
    state.status = "awaiting_manazer"
    _seed_done_tasks(db_session, version, project, ["T1"])
    monkeypatch.setattr(orchestrator, "_repo_head", lambda root: "deadbeefcafe")
    db_session.flush()

    await orchestrator.apply_action(db_session, version_id=version.id, action="verdict", payload={"verdict": verdict})

    manual = _verdicts(db_session, version.id)[0]
    assert (manual.content, manual.payload["verdict"]) == (sentence, verdict)


async def test_a_floored_manual_pass_says_why(db_session, monkeypatch):
    version, _project = _make_version(db_session, project_dial="po_kazdej_faze")
    state = _seed_verifikacia(db_session, version.id, iteration=0)
    state.status = "awaiting_manazer"
    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="verifikacia",
        author="system",
        recipient="manazer",
        kind="notification",
        content="Release smoke — boot PASS",
        payload={"phase": "verifikacia", "smoke": {"pass": True, "detail": "booted"}},
    )
    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="verifikacia",
        author="system",
        recipient="manazer",
        kind="notification",
        content="Release acceptance — FAIL",
        payload={
            "phase": "verifikacia",
            "release_acceptance": {"pass": False, "detail": "2 of 12 failed", "skipped": False},
        },
    )
    db_session.flush()

    await orchestrator.apply_action(db_session, version_id=version.id, action="verdict", payload={"verdict": "PASS"})

    floored = _verdicts(db_session, version.id)[-1]
    assert floored.content == "Verifikácia neprešla: ručné „prešla“ neplatí, lebo skúška po spustení bola červená."
    assert (floored.payload["verdict"], floored.payload["engine_override"]) == ("FAIL", "runtime_floor_red")
