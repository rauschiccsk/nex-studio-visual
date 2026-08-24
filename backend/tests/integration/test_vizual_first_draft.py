"""The Vizuál stage draws the app's first screens before asking anyone to look at them (ICCINT-27).

Found by the Director 2026-08-24 on nex-productcatalogs. He approved the Návrh; **77 milliseconds** later the
cockpit said *"Vizuál je pripravený — otvor si ho"* and set ``next_action`` to *"Prezri si vizuál a keď sedí,
schváľ"*. No agent turn had run — zero tokens. What the link opened was the founding scaffold's own
``LoginPage`` behind ``ProtectedRoute``, with no accounts to log in with: the "dead login screen"
:func:`orchestrator._vizual_mockup_rel` already warned about in its own docstring.

The screens COULD be built — a change-request (``uprav``) dispatches the agent — but nothing said so, and the
one thing the cockpit did say was to approve what he was looking at. So the phase the product is named after
("vizuálne najprv") produced nothing and reported success.

These pin the two halves of the fix, behaviour not wording:
  * a first entry with nothing drawn DISPATCHES the first draft from the approved Špecifikácia + Návrh;
  * the preview URL is never announced before there is something behind it.
"""

from __future__ import annotations

import uuid as _uuid

import pytest

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import claude_agent, orchestrator, vizual_sandbox
from backend.services.pipeline_status import PipelineStatusBlock

PREVIEW_URL = "http://sandbox.local:5173"


def _seed_vizual_state(db) -> tuple[Version, PipelineState, str]:
    """A build settled at the Vizuál stage, exactly as approving the Návrh leaves it."""
    suffix = _uuid.uuid4().hex[:8]
    user = User(username=f"cc_{suffix}", email=f"cc_{suffix}@test.local", password_hash="x", role="ri")
    db.add(user)
    db.flush()
    project = Project(
        name=f"Vizual {suffix}",
        slug=f"viz-{suffix}",
        type="standard",
        auth_mode="password",
        description="Vizual first-draft test project.",
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
        current_stage="vizual",
        current_actor="ai_agent",
        status="agent_working",
        mode=None,  # a phase-automaton build — the kind the Director was running
    )
    db.add(state)
    db.flush()
    return version, state, project.slug


def _url_announced(db, version_id) -> bool:
    return orchestrator._vizual_url_recorded(db, version_id)


# ── the probe that tells a first entry from a later one ───────────────────────


def test_draft_attempted_is_false_until_the_agent_has_taken_a_vizual_turn(db_session) -> None:
    version, _state, _slug = _seed_vizual_state(db_session)
    assert orchestrator._vizual_draft_attempted(db_session, version.id) is False

    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="vizual",
        author=orchestrator.AI_AGENT_ROLE,
        recipient="manazer",
        kind="gate_report",
        content="Prvé obrazovky sú hotové.",
        payload={},
    )
    db_session.flush()
    assert orchestrator._vizual_draft_attempted(db_session, version.id) is True


def test_draft_attempted_ignores_system_and_manager_chatter(db_session) -> None:
    """Only an AI Agent turn counts as "something was drawn" — the readiness notification must not."""
    version, _state, _slug = _seed_vizual_state(db_session)
    for author, kind in (("system", "notification"), ("manazer", "return")):
        orchestrator._record_message(
            db_session,
            version_id=version.id,
            stage="vizual",
            author=author,
            recipient="manazer" if author == "system" else "ai_agent",
            kind=kind,
            content="…",
            payload={},
        )
    db_session.flush()
    assert orchestrator._vizual_draft_attempted(db_session, version.id) is False


# ── the first-draft brief ─────────────────────────────────────────────────────


def test_first_draft_directive_builds_from_the_approved_documents(db_session) -> None:
    version, _state, _slug = _seed_vizual_state(db_session)
    d = orchestrator._vizual_directive(db_session, version.id, None)

    # It says what to do WITHOUT a Manažér request, and points at the two approved documents.
    assert "PRVÝ NÁVRH" in d
    assert "specification.md" in d and "design.md" in d
    # No request was made, so none may be quoted — a brief containing «None» is the bug wearing a disguise.
    assert "None" not in d
    assert "«" not in d
    # The preview harness stays mandatory: without it the draft renders as the dead login screen.
    assert "MSW" in d and "VITE_PREVIEW" in d and "ProtectedRoute" in d
    # Still frontend-only — the data model belongs to Programovanie.
    assert "frontend/" in d


def test_change_request_directive_is_unchanged_by_the_first_draft_branch(db_session) -> None:
    version, _state, _slug = _seed_vizual_state(db_session)
    d = orchestrator._vizual_directive(db_session, version.id, "Zväčši písmo v hlavičke")
    assert "Zväčši písmo v hlavičke" in d
    assert "PRVÝ NÁVRH" not in d


# ── the round itself ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_first_entry_draws_the_screens_instead_of_asking_for_approval(db_session, tmp_path, monkeypatch) -> None:
    version, state, _slug = _seed_vizual_state(db_session)
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(vizual_sandbox, "spin_up", lambda slug: PREVIEW_URL)

    seen: dict[str, object] = {}

    async def _fake_agent(*args, **kwargs):
        seen["prompt"] = kwargs["prompt"]
        # THE point of the ticket: the Manažér has not yet been promised anything to open.
        seen["url_announced_before_draft"] = _url_announced(db_session, version.id)
        return PipelineStatusBlock(
            stage="vizual", kind="done", summary="Prvé obrazovky sú postavené.", awaiting="manazer"
        )

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake_agent)

    settled = await orchestrator._run_vizual_round(db_session, state)

    # The agent RAN — this is the whole defect: it used to settle here having done nothing.
    assert "prompt" in seen, "the first entry into Vizuál did not draw anything"
    assert "PRVÝ NÁVRH" in str(seen["prompt"])
    assert seen["url_announced_before_draft"] is False, "the preview was promised before it had any content"

    # And only afterwards is the Manažér invited — to look and refine, not to approve an empty app.
    assert settled.status == "awaiting_manazer"
    assert "Prvý návrh" in settled.next_action
    assert _url_announced(db_session, version.id) is True


@pytest.mark.asyncio
async def test_a_failed_first_draft_does_not_promise_a_preview(db_session, tmp_path, monkeypatch) -> None:
    """If the draft could not be produced there is still nothing to open — say nothing about a link."""
    version, state, _slug = _seed_vizual_state(db_session)
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(vizual_sandbox, "spin_up", lambda slug: PREVIEW_URL)

    async def _fake_agent(*args, **kwargs):
        return PipelineStatusBlock(
            stage="vizual",
            kind="question",
            summary="Neviem sa rozhodnúť.",
            question="Ktorý zoznam prvý?",
            awaiting="manazer",
        )

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake_agent)

    settled = await orchestrator._run_vizual_round(db_session, state)

    assert settled.status == "blocked"
    assert settled.block_reason == "agent_question"
    assert _url_announced(db_session, version.id) is False


@pytest.mark.asyncio
async def test_second_entry_hands_over_the_walk_without_redrawing(db_session, tmp_path, monkeypatch) -> None:
    """Once the agent has drawn something, a fresh entry is the walk+approve settle — unchanged behaviour."""
    version, state, _slug = _seed_vizual_state(db_session)
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(vizual_sandbox, "spin_up", lambda slug: PREVIEW_URL)
    orchestrator._record_message(
        db_session,
        version_id=version.id,
        stage="vizual",
        author=orchestrator.AI_AGENT_ROLE,
        recipient="manazer",
        kind="gate_report",
        content="Obrazovky sú hotové.",
        payload={},
    )
    db_session.flush()

    async def _must_not_run(*args, **kwargs):
        raise AssertionError("a second entry re-ran the draft over the Manažér's existing screens")

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _must_not_run)

    settled = await orchestrator._run_vizual_round(db_session, state)

    assert settled.status == "awaiting_manazer"
    assert "Prezri si vizuál" in settled.next_action
    assert _url_announced(db_session, version.id) is True


@pytest.mark.asyncio
async def test_an_existing_mockup_counts_as_drawn(db_session, tmp_path, monkeypatch) -> None:
    """A self-contained mockup on disk IS the draft (Director 2026-07-17) — do not redraw over it."""
    version, state, slug = _seed_vizual_state(db_session)
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    vis = tmp_path / slug / "docs" / "specs" / "versions" / "v0.1.0" / "visual"
    vis.mkdir(parents=True)
    (vis / "index.html").write_text("<html><body>Mockup</body></html>", encoding="utf-8")

    async def _must_not_run(*args, **kwargs):
        raise AssertionError("the round redrew over an existing mockup")

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _must_not_run)

    settled = await orchestrator._run_vizual_round(db_session, state)

    assert settled.status == "awaiting_manazer"
    assert "Prezri si vizuál" in settled.next_action


@pytest.mark.asyncio
async def test_a_change_request_still_reaches_the_agent_on_a_first_entry(db_session, tmp_path, monkeypatch) -> None:
    """A Manažér who DID ask for something gets exactly that — the draft branch must not swallow his words."""
    version, state, _slug = _seed_vizual_state(db_session)
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(vizual_sandbox, "spin_up", lambda slug: PREVIEW_URL)

    seen: dict[str, object] = {}

    async def _fake_agent(*args, **kwargs):
        seen["prompt"] = kwargs["prompt"]
        return PipelineStatusBlock(stage="vizual", kind="done", summary="Hotovo.", awaiting="manazer")

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake_agent)

    settled = await orchestrator._run_vizual_round(db_session, state, directive="Daj katalóg na prvú obrazovku")

    assert "Daj katalóg na prvú obrazovku" in str(seen["prompt"])
    assert "PRVÝ NÁVRH" not in str(seen["prompt"])
    assert "Zmena je vo vizuáli" in settled.next_action


@pytest.mark.asyncio
async def test_no_build_is_ever_told_to_approve_an_undrawn_vizual(db_session, tmp_path, monkeypatch) -> None:
    """The regression in one line: 'look at it and approve' may never be the outcome of drawing nothing."""
    version, state, _slug = _seed_vizual_state(db_session)
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(vizual_sandbox, "spin_up", lambda slug: PREVIEW_URL)

    dispatched = {"agent_ran": False}

    async def _fake_agent(*args, **kwargs):
        dispatched["agent_ran"] = True
        return PipelineStatusBlock(stage="vizual", kind="done", summary="Hotovo.", awaiting="manazer")

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake_agent)

    settled = await orchestrator._run_vizual_round(db_session, state)

    # Approving is a perfectly good thing to offer — ONCE something has been drawn. The defect was offering it
    # as the outcome of a round that drew nothing, so that is what this ties together.
    if "schváľ" in (settled.next_action or ""):
        assert dispatched["agent_ran"], "asked for approval of an app nobody drew"
