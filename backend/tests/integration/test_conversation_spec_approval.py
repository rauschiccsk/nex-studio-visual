"""Integration tests for the STEP 2 (Špecifikácia) conversation spec-approval path.

These pin the ADDITIVE ``mode='conversation'`` branch of ``approve_spec`` against the real v2 DB
(test DB :9178, SAVEPOINT-isolated via the ``db_session`` fixture — NEVER PROD :9198):

  * **Conversation approve** — a ``mode='conversation'`` build with ``specification.md`` on disk approves
    by writing EXACTLY ONE ``kind='approval'`` message (author=manazer, payload = pointer+metadata only,
    never the spec body / secrets), settling ``awaiting_manazer`` and STAYING in ``priprava`` — it does
    NOT walk the phase automaton (no ``_next_stage``, no ``_begin_dispatch``). The rozhovor continues.
  * **Missing spec** — a conversation build whose checkout EXISTS but has no ``specification.md`` cannot be
    approved (nothing to freeze) → ``OrchestratorError``.
  * **Legacy regression** — a ``mode`` NULL build stays BYTE-IDENTICAL: approve_spec advances
    priprava → navrh and calls ``_begin_dispatch`` with the legacy payload (no ``mode`` / ``spec_path``).
  * **Sole-writer / append-only** — the approval is recorded ONLY through ``_record_message``; no other
    message leaks; ``PipelineMessage`` carries no ``updated_at`` (append-only by construction).
  * **Additive constants** — ``approval`` / ``awaiting_manazer`` already exist (no migration).
  * **Zadanie optional** — an empty Zadanie ``write_zadanie`` succeeds, and a conversation build with no
    ``customer-requirements.md`` on disk does NOT block approval.
  * **Directive** — ``_conversation_directive`` names the concrete spec + Zadanie paths, marks the Zadanie
    optional, and instructs the partner not to paste the whole ``specification.md`` into chat replies —
    while staying phase-free.
"""

from __future__ import annotations

import uuid as _uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import (
    MESSAGE_KIND_VALUES,
    STATUS_VALUES,
    PipelineMessage,
    PipelineState,
)
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator
from backend.services import version as version_service
from backend.services.orchestrator import OrchestratorError

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _seed_user(db) -> User:
    u = User(
        username=f"cs_{_uuid.uuid4().hex[:8]}",
        email=f"cs_{_uuid.uuid4().hex[:8]}@test.local",
        password_hash="x",
        role="ri",
    )
    db.add(u)
    db.flush()
    return u


def _seed_project(db, *, creator: User, source_path: str | None) -> Project:
    suffix = _uuid.uuid4().hex[:8]
    project = Project(
        name=f"Conv Spec Proj {suffix}",
        slug=f"conv-spec-{suffix}",
        type="standard",
        auth_mode="password",
        description="STEP 2 conversation spec-approval test project.",
        created_by=creator.id,
        source_path=source_path,
    )
    db.add(project)
    db.flush()
    return project


def _seed_version(db, project: Project, version_number: str = "2.0.0") -> Version:
    version = Version(project_id=project.id, version_number=version_number, status="active")
    db.add(version)
    db.flush()
    return version


def _seed_state(db, version: Version, *, mode: str | None, flow_type: str = "new_version") -> PipelineState:
    """A settled Príprava build ready for ``approve_spec`` (advancing action needs a settled agent)."""
    state = PipelineState(
        version_id=version.id,
        flow_type=flow_type,
        current_stage="priprava",
        current_actor="ai_agent",
        status="awaiting_manazer",
        mode=mode,
    )
    db.add(state)
    db.flush()
    return state


def _write_spec_file(source_root: Path, version: Version) -> str:
    """Write a ``specification.md`` at the EXACT rel path the disk-verify computes; returns the rel path."""
    rel = orchestrator._priprava_spec_rel(version.version_number)
    abs_path = source_root / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text("# Špecifikácia\n\nObsah dohodnutý v rozhovore.\n", encoding="utf-8")
    return rel


# ---------------------------------------------------------------------------
# (i) Conversation approve — one approval, settles awaiting_manazer, stays priprava, no dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversation_approve_records_one_approval_and_holds_priprava(db_session, tmp_path, monkeypatch) -> None:
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator, source_path=str(tmp_path))
    version = _seed_version(db_session, project)
    _seed_state(db_session, version, mode="conversation")
    rel = _write_spec_file(tmp_path, version)
    # The Zadanie is OPTIONAL — a conversation build without customer-requirements.md must still approve.
    assert not (tmp_path / orchestrator._version_spec_rel(version.version_number) / "customer-requirements.md").exists()

    dispatched: list = []
    monkeypatch.setattr(orchestrator, "_begin_dispatch", lambda db, state: dispatched.append(state.version_id))

    before = db_session.execute(select(PipelineMessage).where(PipelineMessage.version_id == version.id)).scalars().all()

    state = await orchestrator.apply_action(
        db_session,
        version_id=version.id,
        action="approve_spec",
        payload={"comment": "Vyzerá dobre, schvaľujem."},
    )

    # SETTLE to the Manažér — NO phase advance, NO dispatch.
    assert state.status == "awaiting_manazer"
    assert state.current_stage == "priprava"
    assert state.next_action and "zmraz" in state.next_action.lower()
    assert dispatched == []  # _begin_dispatch NOT called for a conversation approval

    # EXACTLY ONE new message, and it is the approval with pointer+metadata only (no spec body / secrets).
    after = db_session.execute(select(PipelineMessage).where(PipelineMessage.version_id == version.id)).scalars().all()
    new_msgs = [m for m in after if m not in before]
    assert len(new_msgs) == 1
    approval = new_msgs[0]
    assert approval.kind == "approval"
    assert approval.author == "manazer"
    assert approval.recipient == "ai_agent"
    assert approval.stage == "priprava"
    assert approval.content == "Vyzerá dobre, schvaľujem."
    assert approval.payload == {
        "phase": "priprava",
        "approve_spec": True,
        "mode": "conversation",
        "spec_path": rel,
    }
    # The payload is a POINTER, not the spec body — the on-disk .md stays the single source of truth.
    assert "# Špecifikácia" not in str(approval.payload)


@pytest.mark.asyncio
async def test_conversation_approve_uses_default_comment_when_omitted(db_session, tmp_path, monkeypatch) -> None:
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator, source_path=str(tmp_path))
    version = _seed_version(db_session, project)
    _seed_state(db_session, version, mode="conversation")
    _write_spec_file(tmp_path, version)
    monkeypatch.setattr(orchestrator, "_begin_dispatch", lambda db, state: None)

    await orchestrator.apply_action(db_session, version_id=version.id, action="approve_spec")

    approval = db_session.execute(
        select(PipelineMessage).where(PipelineMessage.version_id == version.id, PipelineMessage.kind == "approval")
    ).scalar_one()
    assert approval.content == "Špecifikácia schválená."


# ---------------------------------------------------------------------------
# (ii) Missing spec on an existing checkout → OrchestratorError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversation_approve_missing_spec_raises(db_session, tmp_path, monkeypatch) -> None:
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator, source_path=str(tmp_path))
    version = _seed_version(db_session, project)
    _seed_state(db_session, version, mode="conversation")
    # Checkout EXISTS (source_path=tmp_path) but specification.md is absent → nothing to freeze.
    monkeypatch.setattr(orchestrator, "_begin_dispatch", lambda db, state: None)

    with pytest.raises(OrchestratorError, match="Špecifikácia ešte nie je napísaná"):
        await orchestrator.apply_action(db_session, version_id=version.id, action="approve_spec")

    # No approval leaked despite the raise.
    approvals = (
        db_session.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version.id, PipelineMessage.kind == "approval")
        )
        .scalars()
        .all()
    )
    assert approvals == []


# ---------------------------------------------------------------------------
# (iii) REGRESSION — legacy mode NULL stays byte-identical (priprava → navrh + _begin_dispatch)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_null_mode_approve_advances_to_navrh_and_dispatches(db_session, tmp_path, monkeypatch) -> None:
    creator = _seed_user(db_session)
    # Legacy path never reads specification.md on approve (the gate ran earlier) — source_path is irrelevant.
    project = _seed_project(db_session, creator=creator, source_path=str(tmp_path))
    version = _seed_version(db_session, project)
    _seed_state(db_session, version, mode=None, flow_type="new_version")

    dispatched: list = []
    monkeypatch.setattr(orchestrator, "_begin_dispatch", lambda db, state: dispatched.append(state.version_id))

    state = await orchestrator.apply_action(
        db_session,
        version_id=version.id,
        action="approve_spec",
        payload={"comment": "ok"},
    )

    # Legacy: advances Príprava → Návrh and dispatches; the approval payload has NO mode / spec_path.
    assert state.current_stage == "navrh"
    assert dispatched == [version.id]
    approval = db_session.execute(
        select(PipelineMessage).where(PipelineMessage.version_id == version.id, PipelineMessage.kind == "approval")
    ).scalar_one()
    assert approval.payload == {"phase": "priprava", "approve_spec": True}
    assert "mode" not in approval.payload
    assert "spec_path" not in approval.payload


# ---------------------------------------------------------------------------
# (iv) Sole-writer / append-only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_conversation_approve_writes_only_through_record_message(db_session, tmp_path, monkeypatch) -> None:
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator, source_path=str(tmp_path))
    version = _seed_version(db_session, project)
    _seed_state(db_session, version, mode="conversation")
    _write_spec_file(tmp_path, version)
    monkeypatch.setattr(orchestrator, "_begin_dispatch", lambda db, state: None)

    real_record = orchestrator._record_message
    calls: list[dict] = []

    def _spy(db, **kw):
        calls.append(kw)
        return real_record(db, **kw)

    monkeypatch.setattr(orchestrator, "_record_message", _spy)

    await orchestrator.apply_action(db_session, version_id=version.id, action="approve_spec")

    # The approval was the ONLY message written on this path, and it went through _record_message.
    assert len(calls) == 1
    assert calls[0]["kind"] == "approval"


def test_pipeline_message_is_append_only() -> None:
    """Append-only by construction — no ``updated_at`` mutation surface on the log."""
    assert not hasattr(PipelineMessage, "updated_at")


# ---------------------------------------------------------------------------
# (v) Additive constants — no migration needed
# ---------------------------------------------------------------------------


def test_approval_kind_and_awaiting_status_exist() -> None:
    assert "approval" in MESSAGE_KIND_VALUES
    assert "awaiting_manazer" in STATUS_VALUES


# ---------------------------------------------------------------------------
# (vi) Zadanie optional
# ---------------------------------------------------------------------------


def test_write_zadanie_accepts_empty_content(db_session, tmp_path, monkeypatch) -> None:
    """The backend has NO not-empty check — an empty Zadanie write succeeds and returns the rel path."""
    monkeypatch.setattr(version_service, "_PROJECTS_ROOT", tmp_path)
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator, source_path=str(tmp_path / "unused"))
    version = _seed_version(db_session, project, version_number="1.4.0")

    rel = version_service.write_zadanie(db_session, version.id, "")

    assert rel == "docs/specs/versions/v1.4.0/customer-requirements.md"
    assert (tmp_path / project.slug / rel).read_text(encoding="utf-8") == ""


# ---------------------------------------------------------------------------
# (vii) Directive names the paths, marks Zadanie optional, forbids pasting the spec — phase-free
# ---------------------------------------------------------------------------


def test_conversation_directive_names_spec_and_zadanie_paths(db_session) -> None:
    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator, source_path=None)
    version = _seed_version(db_session, project, version_number="3.1.0")

    directive = orchestrator._conversation_directive(db_session, version.id)

    spec_rel = orchestrator._priprava_spec_rel(version.version_number)
    zadanie_rel = f"{orchestrator._version_spec_rel(version.version_number)}/customer-requirements.md"
    assert spec_rel in directive
    assert zadanie_rel in directive
    # Zadanie is optional.
    assert "NEPOVINNÉ" in directive
    # Critique fix: do NOT paste the whole specification.md into the chat reply (file is the source of truth).
    assert "NEVKLADAJ" in directive
    assert "specification.md" in directive
    # Phase-free: no gate / stage-advance automaton language leaks into the conversation brief.
    assert "gate_report" not in directive
    assert "Schváliť špecifikáciu" not in directive


# ---------------------------------------------------------------------------
# (viii) Durable board signal — board.spec_approved (STEP 2 follow-up)
# ---------------------------------------------------------------------------
#
# The Špecifikácia badge ("Schválená" / "Rozpracované") needs a DURABLE flag, not the truncated
# recent_messages tail (and approve_spec STAYS in available_actions after approval, so that can't
# drive it either). ``PipelineBoardRead.spec_approved`` is TRUE iff ≥1 ``kind='approval'`` message
# exists for the version — correct for BOTH conversation and legacy builds, additive, no migration.


def _add_approval(db, version: Version) -> None:
    """Append a bare ``kind='approval'`` message — the durable "spec frozen" signal the board counts."""
    db.add(
        PipelineMessage(
            version_id=version.id,
            stage="priprava",
            author="manazer",
            recipient="ai_agent",
            kind="approval",
            content="Špecifikácia schválená.",
        )
    )
    db.flush()


def test_board_spec_approved_false_when_no_approval(db_session, tmp_path) -> None:
    from backend.api.routes.pipeline import _board

    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator, source_path=str(tmp_path))
    version = _seed_version(db_session, project)
    _seed_state(db_session, version, mode="conversation")

    board = _board(db_session, version.id)
    assert board.spec_approved is False


def test_board_spec_approved_true_after_approval(db_session, tmp_path) -> None:
    from backend.api.routes.pipeline import _board

    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator, source_path=str(tmp_path))
    version = _seed_version(db_session, project)
    _seed_state(db_session, version, mode="conversation")
    _add_approval(db_session, version)

    board = _board(db_session, version.id)
    assert board.spec_approved is True


def test_board_spec_approved_true_for_legacy_build(db_session, tmp_path) -> None:
    """Correct for a legacy (``mode`` NULL) build too — the signal keys on the approval message, not mode."""
    from backend.api.routes.pipeline import _board

    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator, source_path=str(tmp_path))
    version = _seed_version(db_session, project)
    _seed_state(db_session, version, mode=None)
    _add_approval(db_session, version)

    board = _board(db_session, version.id)
    assert board.spec_approved is True


def test_board_spec_approved_defaults_false_without_state(db_session, tmp_path) -> None:
    """A version whose pipeline never started (no state, no messages) → honest False, no crash."""
    from backend.api.routes.pipeline import _board

    creator = _seed_user(db_session)
    project = _seed_project(db_session, creator=creator, source_path=str(tmp_path))
    version = _seed_version(db_session, project)

    board = _board(db_session, version.id)
    assert board.spec_approved is False
