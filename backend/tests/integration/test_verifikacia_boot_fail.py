"""B (release-smoke-boot-and-batch-fixes.md): a Verifikácia boot-FAIL settles a HONEST FAIL verdict.

Root of the nex-payables 1.1.0 confusion: the app never booted (a compose interpolation error), so the
Auditor's verdict turn timed out + its output didn't parse → ``_run_verifikacia_round`` fell into the
fail-closed *"Verdikt Auditora sa nepodarilo spracovať"* / ``blocked`` branch. The manager saw "the verdict
couldn't be parsed", not the TRUTH — the app didn't boot.

The fix: a boot-FAIL (``smoke_ok is False``) is a DECISIVE product FAIL and short-circuits AHEAD of the
Auditor turn + its verdict-parse block — it records a clean ``kind=verdict`` FAIL carrying the boot reason
("Appka sa nespustila: …") and settles the standard FAIL fix-loop, DETERMINISTICALLY, independent of whether
the Auditor could emit a parseable verdict. These tests pin that against the real v2 DB.
"""

from __future__ import annotations

import uuid as _uuid

import pytest
from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator

# The boot-FAIL reason the release smoke surfaces when compose interpolation fails (the nex-payables shape).
_BOOT_FAIL_DETAIL = "up exit 1: error while interpolating services.db.environment.POSTGRES_PASSWORD"


def _seed_state_at_verifikacia(db, *, flow_type: str = "new_version", iteration: int = 0) -> PipelineState:
    """A project + active version + a PipelineState parked at the Verifikácia stage (the round's entry)."""
    suffix = _uuid.uuid4().hex[:8]
    user = User(username=f"vbf_{suffix}", email=f"vbf_{suffix}@test.local", password_hash="x", role="ri")
    db.add(user)
    db.flush()
    project = Project(
        name=f"Boot-Fail Proj {suffix}",
        slug=f"boot-fail-{suffix}",
        type="standard",
        auth_mode="password",
        description="release-smoke boot-FAIL honest-verdict test project.",
        created_by=user.id,
    )
    db.add(project)
    db.flush()
    version = Version(project_id=project.id, version_number="v1.1.0", status="active")
    db.add(version)
    db.flush()
    state = PipelineState(
        version_id=version.id,
        flow_type=flow_type,
        current_stage="verifikacia",
        current_actor="auditor",
        status="agent_working",
        iteration=iteration,
    )
    db.add(state)
    db.flush()
    return state


@pytest.mark.asyncio
async def test_boot_fail_settles_honest_fail_verdict_ahead_of_auditor(db_session, monkeypatch) -> None:
    """A boot-FAIL records a ``kind=verdict`` FAIL carrying the boot reason and settles the FAIL fix-loop —
    WITHOUT invoking the Auditor and WITHOUT the confusing "verdikt sa nepodarilo spracovať / blocked" path."""
    state = _seed_state_at_verifikacia(db_session, flow_type="new_version", iteration=0)
    version_id = state.version_id

    # The release smoke reports a boot-FAIL (compose interpolation error) → acceptance never ran (None).
    async def _boot_fail_smoke(slug, version_label, coverage_req=(0, 0)):
        return (False, _BOOT_FAIL_DETAIL), None

    monkeypatch.setattr(orchestrator, "_run_release_smoke", _boot_fail_smoke)
    # The PASS-time release-note write needs a real project tree — irrelevant to a boot-FAIL, no-op it.
    monkeypatch.setattr(orchestrator, "_write_release_note_to_disk", lambda *a, **k: None)

    # Regression guard: the boot-FAIL MUST short-circuit BEFORE the Auditor turn — if the Auditor is invoked,
    # the whole point (independence from verdict-parseability) is lost.
    auditor_called = {"hit": False}

    async def _no_auditor(*a, **k):
        auditor_called["hit"] = True
        raise AssertionError("the Auditor turn must NOT run on a boot-FAIL (it short-circuits ahead of it)")

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _no_auditor)

    settled = await orchestrator._run_verifikacia_round(db_session, state)

    assert auditor_called["hit"] is False, "boot-FAIL must settle without an Auditor turn"

    # 1. A clean kind=verdict FAIL carrying the boot reason is on record.
    verdict = db_session.execute(
        select(PipelineMessage).where(
            PipelineMessage.version_id == version_id,
            PipelineMessage.stage == "verifikacia",
            PipelineMessage.kind == "verdict",
        )
    ).scalar_one()
    assert verdict.payload["verdict"] == "FAIL"
    assert verdict.payload["engine_override"] == "boot_fail"
    # Honest-by-construction: the manager-facing content + findings are the HUMANISED why (never the raw probe
    # jargon / leaked env-var names); the raw probe string is preserved as a breadcrumb in technical_detail
    # (the FE's collapsible "Technický detail"), and the AI Agent fixer reproduces the boot failure itself.
    assert verdict.content.startswith("Appka sa nespustila")
    assert _BOOT_FAIL_DETAIL not in verdict.content
    assert _BOOT_FAIL_DETAIL in verdict.payload["technical_detail"]
    assert verdict.payload["findings"] == [verdict.content]
    # The humanised WHY threads to the AI Agent fix brief / Decision Card explanation.
    assert orchestrator._latest_verifikacia_fix_scope(db_session, version_id), "the fix brief carries the reason"

    # 2. The state SETTLED FAIL (the standard bounded fix-loop), NOT blocked-on-parse.
    assert settled.block_reason != "agent_error", "must NOT be the unparseable-verdict block"
    assert settled.block_reason != "parse_exhaustion"
    assert settled.current_stage == "programovanie", "the FAIL fix-loop re-entered Programovanie"
    assert settled.is_regate is True and settled.iteration == 1

    # 3. The confusing "verdikt sa nepodarilo spracovať" notification is NEVER recorded.
    contents = (
        db_session.execute(select(PipelineMessage.content).where(PipelineMessage.version_id == version_id))
        .scalars()
        .all()
    )
    assert not any("nepodarilo spracovať" in c for c in contents)


@pytest.mark.asyncio
async def test_boot_fail_at_loop_max_escalates_not_blocked_on_parse(db_session, monkeypatch) -> None:
    """At the bounded-loop ceiling a boot-FAIL still settles an HONEST FAIL — it escalates to a Manažér
    Decision Card (``blocked``/``decision_needed``), NEVER the ``agent_error`` unparseable-verdict block."""
    state = _seed_state_at_verifikacia(db_session, flow_type="new_version", iteration=orchestrator.AUDITOR_LOOP_MAX)
    version_id = state.version_id

    async def _boot_fail_smoke(slug, version_label, coverage_req=(0, 0)):
        return (False, _BOOT_FAIL_DETAIL), None

    monkeypatch.setattr(orchestrator, "_run_release_smoke", _boot_fail_smoke)
    monkeypatch.setattr(orchestrator, "_write_release_note_to_disk", lambda *a, **k: None)

    async def _no_auditor(*a, **k):
        raise AssertionError("the Auditor turn must NOT run on a boot-FAIL")

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _no_auditor)

    settled = await orchestrator._run_verifikacia_round(db_session, state)

    # A verdict FAIL is still recorded, and the escalation is a DECISION (not the parse-fail agent_error block).
    verdict = db_session.execute(
        select(PipelineMessage).where(PipelineMessage.version_id == version_id, PipelineMessage.kind == "verdict")
    ).scalar_one()
    assert verdict.payload["verdict"] == "FAIL" and verdict.content.startswith("Appka sa nespustila")
    assert settled.status == "blocked" and settled.block_reason == "decision_needed"
    assert settled.block_reason != "agent_error"


@pytest.mark.asyncio
async def test_decide_guide_note_routes_manager_directive_to_fixer(db_session, monkeypatch) -> None:
    """v4.0.9: the manager's typed directive on a ``verifikacia_fix`` Decision Card reaches the AI Agent
    fixer even when it lands in the always-visible ``note`` box (not the hidden free-text escape). Before,
    the fix brief read ONLY ``free_text`` → a directive typed as ``note`` was dropped and the agent got just
    the option LABEL ('Usmerniť opravu') → it refused to 'fix blindly' (the nex-shopify 2026-07-20 wedge)."""
    state = _seed_state_at_verifikacia(db_session)
    version_id = state.version_id
    # Park it exactly like a real per-FAIL escalation: blocked at Programovanie with a verifikacia_fix card.
    state.current_stage = "programovanie"
    state.current_actor = "ai_agent"
    state.status = "blocked"
    state.block_reason = "decision_needed"
    db_session.flush()
    orchestrator._record_message(
        db_session,
        version_id=version_id,
        stage="verifikacia",
        author="system",
        recipient="manazer",
        kind="consultation",
        content="Verifikácia našla chybu — potrebné je tvoje rozhodnutie.",
        payload={
            "phase": "verifikacia",
            "consultation": {
                "id": "verifikacia-fix-test-1",
                "source": "verifikacia_fix",
                "intro": "Verifikácia našla chybu.",
                "decisions": [
                    {
                        "key": "verifikacia_fix_next",
                        "question": "Ako chceš pokračovať?",
                        "allow_free_text": True,
                        "options": [
                            {"id": "guide", "label": "Usmerniť opravu"},
                            {"id": "hold", "label": "Zatiaľ podržať"},
                        ],
                    }
                ],
            },
        },
    )

    captured: dict[str, str] = {}

    async def _capture_route(db, st, *, comment, on_message=None):
        captured["comment"] = comment
        return st

    monkeypatch.setattr(orchestrator, "_route_manazer_fix_to_ai_agent", _capture_route)

    directive = "Neupravuj projekt — engine bol opravený; iba znovu spusti Verifikáciu."
    await orchestrator.apply_action(
        db_session,
        version_id=version_id,
        action="decide",
        payload={"decision_key": "verifikacia_fix_next", "option_id": "guide", "note": directive},
    )

    # The manager's DIRECTIVE (typed into the note box) IS the fix brief — NOT the bare option label.
    assert captured.get("comment") == directive
    assert captured["comment"] != "Usmerniť opravu"


@pytest.mark.asyncio
async def test_overit_bez_opravy_reruns_verifikacia_gate(db_session, monkeypatch) -> None:
    """v4.0.10: from a Verifikácia fix-loop (Programovanie, blocked) with a fix-scope on record, the Manažér's
    'Znova overiť bez opravy' SKIPS the commit-demanding fix task and re-enters the Verifikácia gate directly —
    for a root cause fixed OUTSIDE the project (engine/framework), where the fix loop would only churn."""
    state = _seed_state_at_verifikacia(db_session)
    version_id = state.version_id
    state.current_stage = "programovanie"
    state.current_actor = "ai_agent"
    state.status = "blocked"
    state.block_reason = "agent_question"
    state.dispatch_baseline_sha = "deadbeef"
    db_session.flush()
    # A Verifikácia fix-scope on record (a manazer fix directive) → the action is meaningful.
    orchestrator._record_message(
        db_session,
        version_id=version_id,
        stage="verifikacia",
        author="manazer",
        recipient="ai_agent",
        kind="return",
        content="Usmerniť opravu",
        payload={"phase": "verifikacia", "manazer_fix_directive": True},
    )
    # Don't touch a real repo when _begin_dispatch re-captures the baseline.
    monkeypatch.setattr(orchestrator, "_repo_head", lambda *a, **k: "cafef00d")

    settled = await orchestrator.apply_action(db_session, version_id=version_id, action="overit_bez_opravy")

    assert settled.current_stage == "verifikacia"
    assert settled.current_actor == orchestrator.STAGE_ACTOR["verifikacia"]  # the independent Auditor
    assert settled.status == "agent_working"  # _begin_dispatch → the background turn runs _run_verifikacia_round
    assert settled.block_reason is None  # the fix-loop block is cleared


@pytest.mark.asyncio
async def test_overit_bez_opravy_rejected_outside_fix_loop(db_session) -> None:
    """Invalid without a Verifikácia fix-scope on record (a fresh Programovanie build that never reached
    Verifikácia) — it must never re-verify a build that was never verified."""
    state = _seed_state_at_verifikacia(db_session)
    version_id = state.version_id
    state.current_stage = "programovanie"
    state.current_actor = "ai_agent"
    state.status = "blocked"
    state.block_reason = "agent_question"
    db_session.flush()
    with pytest.raises(orchestrator.OrchestratorError):
        await orchestrator.apply_action(db_session, version_id=version_id, action="overit_bez_opravy")
