"""DEV-233 — the Verifikácia verdict is one message, also when the cockpit adds findings of its own.

26.10.2026, Author Asistent 0.1.0: the thread carried the round's verdict twice — the Auditor's own message (5829) and
the cockpit's verdict (5830), the same paragraph with „Kontrola nepoužívaného kódu: výnimka pwaOptions … nič
nepokrýva“ appended. ``_verdict_message`` joins the cockpit's verdict to the Auditor's message of the round
(ICCINT-49), but it looked that message up by IDENTICAL TEXT, and the round appends its own findings to the Auditor's
summary — the dead-code notes (DEV-156) and, on a FAIL only the cockpit measured, „Verzia funguje, ale …“ (DEV-65,
DEV-90, DEV-156). The texts differed, nothing was found, and a second message was written. Director: „Pokračuj, daj
vedieť, keď to bude hotové“.

Pinned: the verdict joins the Auditor's message of THIS round whatever the cockpit added, and the message shows the
cockpit's whole text; a message of an earlier round is never rewritten.
"""

from __future__ import annotations

import pytest

from backend.db.models.pipeline import PipelineMessage
from backend.services import orchestrator
from tests.test_orchestrator_v2_verifikacia import (
    _make_version,
    _msgs,
    _seed_verifikacia,
    _stub_smoke,
    _verdict_fail,
    _verdict_pass,
)

pytestmark = pytest.mark.asyncio

SUMMARY_FAIL = "Verifikácia neprešla — editor korektúr nedrží odseky vedľa ich ruských párov."
DEAD_CODE_NOTE = "Kontrola nepoužívaného kódu: výnimka pwaOptions (frontend/src/pwa/options.ts) nič nepokrýva"
LANGUAGE_FINDING = {"text": "Slovenský text v kóde, ktorý dostane zákazník.", "blocking": True}


def _auditor_that_writes(monkeypatch, block):
    """The Auditor's turn as ``invoke_agent`` records it: a ``kind=verdict`` message with its report, no verdict."""

    async def _turn(db, *, version_id, role, stage, prompt, **_kw):
        orchestrator._record_message(
            db,
            version_id=version_id,
            stage="verifikacia",
            author="auditor",
            recipient="manazer",
            kind="verdict",
            content=block.summary,
            payload={"report": {"summary": block.summary}, "awaiting": "manazer"},
        )
        return block

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _turn)


def _cockpit_adds(monkeypatch, *, notes=(), language=None):
    async def _dead_code(db, version_id, slug):
        return None, list(notes)

    async def _language(db, version_id, slug, version_label):
        return language

    monkeypatch.setattr(orchestrator, "_dead_code_check", _dead_code)
    monkeypatch.setattr(orchestrator, "_source_language_finding", _language)


def _verdicts(db_session, version_id) -> list[PipelineMessage]:
    return [m for m in _msgs(db_session, version_id) if m.author == "auditor" and m.kind == "verdict"]


async def test_a_dead_code_note_does_not_split_the_verdict(db_session, monkeypatch):
    version, _ = _make_version(db_session, project_dial="plna")
    _seed_verifikacia(db_session, version.id)
    _stub_smoke(monkeypatch)
    _auditor_that_writes(monkeypatch, _verdict_fail(["editor"]).model_copy(update={"summary": SUMMARY_FAIL}))
    _cockpit_adds(monkeypatch, notes=[DEAD_CODE_NOTE])

    await orchestrator.run_dispatch(db_session, version.id)

    verdicts = _verdicts(db_session, version.id)
    assert len(verdicts) == 1, [m.content for m in verdicts]
    assert verdicts[0].content == f"{SUMMARY_FAIL} {DEAD_CODE_NOTE}"
    assert verdicts[0].payload["verdict"] == "FAIL" and verdicts[0].payload["report"] == {"summary": SUMMARY_FAIL}


async def test_a_fail_only_the_cockpit_measured_is_one_message_too(db_session, monkeypatch):
    version, _ = _make_version(db_session, project_dial="plna")
    _seed_verifikacia(db_session, version.id)
    _stub_smoke(monkeypatch)
    _auditor_that_writes(monkeypatch, _verdict_pass())
    _cockpit_adds(monkeypatch, language=LANGUAGE_FINDING)

    await orchestrator.run_dispatch(db_session, version.id)

    verdicts = _verdicts(db_session, version.id)
    assert len(verdicts) == 1, [m.content for m in verdicts]
    assert verdicts[0].content.startswith("Verzia funguje, ale kód, ktorý dostane zákazník, má slovenský text")
    assert verdicts[0].content.endswith(_verdict_pass().summary)
    assert verdicts[0].payload["verdict"] == "FAIL"


async def test_the_same_text_still_joins_as_before(db_session, monkeypatch):
    """The control — what ICCINT-49 fixed keeps working."""
    version, _ = _make_version(db_session, project_dial="plna")
    _seed_verifikacia(db_session, version.id)
    _stub_smoke(monkeypatch)
    _auditor_that_writes(monkeypatch, _verdict_fail(["editor"]).model_copy(update={"summary": SUMMARY_FAIL}))
    _cockpit_adds(monkeypatch)

    await orchestrator.run_dispatch(db_session, version.id)

    verdicts = _verdicts(db_session, version.id)
    assert [m.content for m in verdicts] == [SUMMARY_FAIL]


async def test_a_message_of_an_earlier_round_is_never_rewritten(db_session, monkeypatch):
    """An earlier round left its Auditor message without a verdict; this round's turn wrote none. The cockpit's verdict
    is a new message — the earlier round keeps its own words."""
    version, _ = _make_version(db_session, project_dial="plna")
    _seed_verifikacia(db_session, version.id)
    earlier = orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="verifikacia",
        author="auditor",
        recipient="manazer",
        kind="verdict",
        content=SUMMARY_FAIL,
        payload={"report": {"summary": SUMMARY_FAIL}},
    )
    db_session.flush()
    _stub_smoke(monkeypatch)
    block = _verdict_fail(["editor"]).model_copy(update={"summary": SUMMARY_FAIL})

    async def _silent_turn(db, **_kw):
        return block

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _silent_turn)
    _cockpit_adds(monkeypatch)

    await orchestrator.run_dispatch(db_session, version.id)

    db_session.refresh(earlier)
    assert "verdict" not in earlier.payload and earlier.content == SUMMARY_FAIL
    verdicts = _verdicts(db_session, version.id)
    assert len(verdicts) == 2 and verdicts[-1].payload["verdict"] == "FAIL"
