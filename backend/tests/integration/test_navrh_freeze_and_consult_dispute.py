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


# ── Vizuál preview serves the AI's self-contained mockup (not the auth-gated FE) ─


def test_vizual_mockup_rel_prefers_index_else_newest(tmp_path) -> None:
    vis = tmp_path / "docs" / "specs" / "versions" / "v0.1.0" / "visual"
    vis.mkdir(parents=True)
    assert orchestrator._vizual_mockup_rel(tmp_path, "0.1.0") is None  # empty dir → None
    (vis / "app-mockup.html").write_text("<html>m</html>", encoding="utf-8")
    assert orchestrator._vizual_mockup_rel(tmp_path, "0.1.0") == "docs/specs/versions/v0.1.0/visual/app-mockup.html"
    (vis / "index.html").write_text("<html>i</html>", encoding="utf-8")
    assert orchestrator._vizual_mockup_rel(tmp_path, "0.1.0") == "docs/specs/versions/v0.1.0/visual/index.html"


def test_read_vizual_mockup_returns_html_and_none(db_session, tmp_path, monkeypatch) -> None:
    from backend.db.models.projects import Project as _Project
    from backend.services import claude_agent

    version, _state = _seed_navrh_state(db_session, mode=None, status="awaiting_manazer")
    slug = db_session.execute(
        select(_Project.slug).join(Version, Version.project_id == _Project.id).where(Version.id == version.id)
    ).scalar_one()
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)

    assert orchestrator.read_vizual_mockup(db_session, version.id) is None  # no mockup yet
    vis = tmp_path / slug / "docs" / "specs" / "versions" / "v0.1.0" / "visual"
    vis.mkdir(parents=True)
    (vis / "index.html").write_text("<html><body>Mockup panela</body></html>", encoding="utf-8")

    html = orchestrator.read_vizual_mockup(db_session, version.id)
    assert html is not None and "Mockup panela" in html


def test_vizual_directive_mockup_mode_edits_the_mockup(db_session) -> None:
    version, _state = _seed_navrh_state(db_session)
    rel = "docs/specs/versions/v0.1.0/visual/index.html"

    d = orchestrator._vizual_directive(db_session, version.id, "Zväčši písmo v hlavičke", mockup_rel=rel)
    assert "mockup" in d.lower()
    assert rel in d
    assert "Zväčši písmo v hlavičke" in d

    # FE mode (no mockup) keeps the live-FE instruction.
    d_fe = orchestrator._vizual_directive(db_session, version.id, "Zväčši písmo v hlavičke")
    assert "frontend/" in d_fe


def test_vizual_directive_livefe_requires_msw_preview_harness(db_session) -> None:
    version, _state = _seed_navrh_state(db_session)
    # Live-FE mode (no mockup): the directive MUST instruct the MSW preview harness so the
    # sandbox renders the REAL app without a backend/login (v4.0.22 — the faithful Vizuál).
    d = orchestrator._vizual_directive(db_session, version.id, "Uprav obrazovku katalógu")
    assert "MSW" in d
    assert "VITE_PREVIEW" in d
    assert "ProtectedRoute" in d
    # And it reinforces the binding: what the Manažér approves is what Programovanie builds.
    assert "schváli" in d and "Programovanie" in d


def test_board_vizual_url_prefers_mockup_route_when_present(db_session, tmp_path, monkeypatch) -> None:
    from backend.api.routes.pipeline import _board
    from backend.db.models.projects import Project as _Project
    from backend.services import claude_agent

    version, state = _seed_navrh_state(db_session, mode=None, status="awaiting_manazer")
    state.current_stage = "vizual"  # the mockup override is guarded to the vizual stage
    db_session.flush()
    slug = db_session.execute(
        select(_Project.slug).join(Version, Version.project_id == _Project.id).where(Version.id == version.id)
    ).scalar_one()
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    vis = tmp_path / slug / "docs" / "specs" / "versions" / "v0.1.0" / "visual"
    vis.mkdir(parents=True)
    (vis / "index.html").write_text("<html>m</html>", encoding="utf-8")

    board = _board(db_session, version.id)
    assert board.vizual_url == f"/api/v1/pipeline/{version.id}/vizual-mockup"


# ── Manager-facing build reports: plain Slovak + hierarchical task number ──────


def _seed_epic_feat_task(db, version, *, epic_n=1, feat_n=2, task_n=1, title="Štruktúrované logovanie"):
    from backend.db.models.projects import Project as _Project
    from backend.db.models.tasks import Epic, Feat, Task

    project_id = db.execute(
        select(_Project.id).join(Version, Version.project_id == _Project.id).where(Version.id == version.id)
    ).scalar_one()
    epic = Epic(project_id=project_id, version_id=version.id, number=epic_n, title="E", status="planned")
    db.add(epic)
    db.flush()
    feat = Feat(epic_id=epic.id, number=feat_n, title="F", status="todo")
    db.add(feat)
    db.flush()
    task = Task(feat_id=feat.id, number=task_n, title=title, status="todo", task_type="backend")
    db.add(task)
    db.flush()
    return task


def test_task_full_number_is_hierarchical(db_session) -> None:
    version, _s = _seed_navrh_state(db_session, mode=None)
    task = _seed_epic_feat_task(db_session, version, epic_n=1, feat_n=2, task_n=1)
    assert orchestrator._task_full_number(db_session, task) == "1.2.1"


def test_build_task_directive_is_plain_language_and_labeled() -> None:
    from backend.db.models.tasks import Task

    task = Task(number=1, title="Štruktúrované logovanie", description="Priprav logy.")
    directive = orchestrator._directive_for_build_task(task, None, [], task_label="1.2.1")

    assert "TASK #1.2.1" in directive  # hierarchical label, not the bare "#1"
    # plain-language manager summary rule + jargon ban.
    assert "ĽUDSKOU rečou" in directive
    assert "NEŠPECIALISTA" in directive
    assert "commits[]" in directive  # technical detail belongs there, not in summary


@pytest.mark.asyncio
async def test_record_task_summary_shows_hierarchical_number(db_session) -> None:
    version, _s = _seed_navrh_state(db_session, mode=None, status="agent_working")
    task = _seed_epic_feat_task(db_session, version, epic_n=1, feat_n=2, task_n=1)

    await orchestrator._record_task_summary(db_session, version.id, task, status="done", attempts=1)

    msg = db_session.execute(
        select(PipelineMessage)
        .where(
            PipelineMessage.version_id == version.id,
            PipelineMessage.author == "system",
            PipelineMessage.kind == "notification",
        )
        .order_by(PipelineMessage.seq.desc())
        .limit(1)
    ).scalar_one()
    assert "#1.2.1" in msg.content
    assert "Štruktúrované logovanie" in msg.content


# ── Auditor findings in plain Slovak + release-acceptance port robustness ──────


def test_auditor_directives_findings_are_plain_language(db_session) -> None:
    version, _s = _seed_navrh_state(db_session, mode=None)
    upfront = orchestrator._auditor_upfront_directive(db_session, version.id)
    verif = orchestrator._verifikacia_directive(db_session, version.id, smoke_block="", flow_type="new_version")
    for directive in (upfront, verif):
        # v4.0.11: the manager-facing text must be plain for a non-expert, with a HIDDEN technical outlet.
        assert "NEŠPECIALISTA" in directive
        assert "technical_detail" in directive  # all jargon goes to the collapsible outlet, not summary/findings
        assert "proposed_fix" in directive  # the AI-Agent fix brief (may be technical) — distinct from findings


def test_release_smoke_template_defaults_backend_port() -> None:
    from pathlib import Path

    from backend.services import orchestrator as _o

    tpl = (Path(_o.__file__).resolve().parents[2] / "templates" / "release_smoke_test.sh").read_text(encoding="utf-8")
    # Boot-floor must default the port to 8000 — never probe an empty ":80" (0 asserts, verified nothing).
    assert "${SMOKE_BACKEND_PORT:-8000}/health" in tpl
    assert '"http://localhost:${SMOKE_BACKEND_PORT}/health"' not in tpl  # no bare (defaultless) var left
