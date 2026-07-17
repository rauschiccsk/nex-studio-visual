"""Tests for the two Návrh-gate hardening fixes (Director 2026-07-17):

* **Fix A** — :func:`orchestrator._commit_navrh_deliverables` freezes the Príprava/Návrh deliverables into a
  commit BEFORE the Auditor upfront review, so the audit reviews a STABLE committed snapshot (not a still-
  being-written worktree — the stale-audit that FAILed on already-resolved gaps) AND the output is durable.
* **Fix B** — when the Auditor finds a hole and the AI Agent DISPUTES it (returns a gate_report judging it
  already-resolved) instead of decision cards, :func:`orchestrator._settle_for_consultation` no longer stops
  with a context-less "posúď klasicky"; it surfaces BOTH the Auditor findings AND the agent's response.
"""

from __future__ import annotations

import subprocess
import uuid as _uuid

import pytest
from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator
from backend.services.pipeline_status import PipelineStatusBlock

# ── Fix A — deliverable freeze commit (plain, no DB) ──────────────────────────


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)


def test_commit_navrh_deliverables_commits_docs(tmp_path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@test.local")
    _git(tmp_path, "config", "user.name", "T")
    (tmp_path / "README.md").write_text("x\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    head_before = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()

    docs = tmp_path / "docs" / "specs" / "versions" / "v0.1.0"
    docs.mkdir(parents=True)
    (docs / "specification.md").write_text("# spec\n", encoding="utf-8")
    (docs / "design.md").write_text("# design\n", encoding="utf-8")

    orchestrator._commit_navrh_deliverables(tmp_path)

    head_after = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()
    assert head_after != head_before, "deliverables were not frozen into a commit"
    # Both deliverables are now tracked (the audit sees a committed snapshot).
    tracked = _git(tmp_path, "ls-files", "docs").stdout
    assert "docs/specs/versions/v0.1.0/specification.md" in tracked
    assert "docs/specs/versions/v0.1.0/design.md" in tracked


def test_commit_navrh_deliverables_noop_when_clean(tmp_path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@test.local")
    _git(tmp_path, "config", "user.name", "T")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "x.md").write_text("x\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    head_before = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()

    orchestrator._commit_navrh_deliverables(tmp_path)  # nothing changed → no empty commit

    assert _git(tmp_path, "rev-parse", "HEAD").stdout.strip() == head_before


def test_commit_navrh_deliverables_noop_without_git(tmp_path) -> None:
    # No .git checkout (dry-run / disabled bootstrap) → best-effort no-op, never raises.
    orchestrator._commit_navrh_deliverables(tmp_path)


# ── Fix B — audit-vs-agent dispute is surfaced with both sides ────────────────


def _seed_navrh_state(
    db, *, mode: str | None = "conversation", status: str = "agent_working"
) -> tuple[Version, PipelineState]:
    suffix = _uuid.uuid4().hex[:8]
    user = User(username=f"cc_{suffix}", email=f"cc_{suffix}@test.local", password_hash="x", role="ri")
    db.add(user)
    db.flush()
    project = Project(
        name=f"Dispute {suffix}",
        slug=f"disp-{suffix}",
        type="standard",
        auth_mode="password",
        description="Dispute test project.",
        created_by=user.id,
        source_path=None,
    )
    db.add(project)
    db.flush()
    version = Version(project_id=project.id, version_number="0.1.0", status="active")
    db.add(version)
    db.flush()
    state = PipelineState(
        version_id=version.id,
        flow_type="new_version",
        current_stage="navrh",
        current_actor="ai_agent",
        status=status,
        mode=mode,
    )
    db.add(state)
    db.flush()
    return version, state


@pytest.mark.asyncio
async def test_dispute_surfaces_both_findings_and_agent_response(db_session, monkeypatch) -> None:
    version, state = _seed_navrh_state(db_session)

    findings = [
        "[BLOKUJÚCE] Rozpoznanie platby dobierka/prevod nie je definované.",
        "[BLOKUJÚCE] Token z NEX Managera nemá serverovú zmluvu.",
    ]
    verdict = PipelineStatusBlock(
        stage="navrh", kind="verdict", summary="Nezávislá previerka Návrhu.", findings=findings, awaiting="manazer"
    )

    # The AI Agent DISPUTES the findings — returns a gate_report (not a consultation block).
    async def _fake_dispute(*args, **kwargs):
        return PipelineStatusBlock(
            stage="navrh",
            kind="gate_report",
            summary="Táto previerka je zastaraná — všetko je už vyriešené v aktuálnych dokumentoch.",
            awaiting="manazer",
        )

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake_dispute)

    settled = await orchestrator._settle_for_consultation(db_session, state, source="auditor_upfront", verdict=verdict)

    # Fail-open to a Manažér stop (never wedged), but NOT context-less.
    assert settled.status == "awaiting_manazer"

    note = db_session.execute(
        select(PipelineMessage)
        .where(
            PipelineMessage.version_id == version.id,
            PipelineMessage.author == "system",
            PipelineMessage.kind == "notification",
        )
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one()

    # BOTH sides are surfaced in the content: the dispute framing, every finding, and the agent's response.
    assert "rozporuje" in note.content
    assert "zastaraná" in note.content
    # Rendered as MARKDOWN: the findings are a proper "- " bulleted list under a bold heading, so
    # ConversationThread's SpecMarkdown shows a readable list — NOT one collapsed wall of text.
    assert "**Nálezy previerky:**" in note.content
    assert "\n- [BLOKUJÚCE] Rozpoznanie platby" in note.content
    assert "\n- [BLOKUJÚCE] Token z NEX Managera" in note.content
    # …and structured in the payload for the UI.
    assert note.payload["auditor_findings"] == findings
    assert "zastaraná" in note.payload["agent_response"]
    # The state prompt stays short (the detail lives in the message content).
    assert "Spor previerka" in state.next_action


# ── Auditor over-strictness + loop fix — calibrated, dispute-aware re-review ───


def test_auditor_directive_first_review_calibrated_no_rereview(db_session) -> None:
    version, _state = _seed_navrh_state(db_session)

    directive = orchestrator._auditor_upfront_directive(db_session, version.id)

    # Calibration is ALWAYS present: don't flag config+fail-safe / documented-deferral / build-hedge as a hole.
    assert "fail-safe" in directive
    assert "re-overiť pri builde" in directive
    # FIRST review — no prior verdict → the review runs fresh, no re-review block.
    assert "RE-PREVIERKA" not in directive


def test_auditor_directive_rereview_threads_prior_findings_and_reaction(db_session) -> None:
    version, _state = _seed_navrh_state(db_session)
    # A prior Auditor verdict with findings…
    db_session.add(
        PipelineMessage(
            version_id=version.id,
            stage="navrh",
            author="auditor",
            recipient="manazer",
            kind="verdict",
            content="Predošlá previerka.",
            status="delivered",
            payload={"findings": ["[BLOKUJÚCE] Platba nedefinovaná", "Token bez zmluvy"]},
        )
    )
    db_session.flush()
    # …and the AI Agent's reaction after it.
    db_session.add(
        PipelineMessage(
            version_id=version.id,
            stage="navrh",
            author="ai_agent",
            recipient="manazer",
            kind="gate_report",
            content="Už vyriešené — pozri specification.md §5.3.",
            status="delivered",
            payload={},
        )
    )
    db_session.flush()

    directive = orchestrator._auditor_upfront_directive(db_session, version.id)

    # RE-review mode: prior findings + the agent's reaction are threaded in with a converge instruction.
    assert "RE-PREVIERKA" in directive
    assert "Platba nedefinovaná" in directive
    assert "Už vyriešené" in directive
    assert "ZNOVA over" in directive


# ── Návrh Schváliť button restored — board offers schvalit at navrh w/o a plan ─


def test_board_offers_schvalit_at_navrh_without_materialized_plan(db_session) -> None:
    """The stale board post-filter dropped ``schvalit`` at navrh unless the task plan was materialized, but
    the plan is built at Programovanie START since 2026-07-13 — so it was ALWAYS dropped, permanently hiding
    the Návrh Schváliť button (nex-shopify 2026-07-17: no clickable action at the Návrh gate). The board must
    offer it (apply_action already accepts it: schvalit advances navrh→vizual, plan built later)."""
    from backend.api.routes.pipeline import _board

    # A legacy (mode=None) phase-automaton build settled at the Návrh gate, NO task plan yet.
    version, _state = _seed_navrh_state(db_session, mode=None, status="awaiting_manazer")

    board = _board(db_session, version.id)

    assert "schvalit" in board.available_actions
