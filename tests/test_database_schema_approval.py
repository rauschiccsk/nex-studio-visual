"""DEV-7 — the database schema is approved by Ri with the Návrh, or by a card during Programovanie, and the cockpit
writes it into the Knowledge Base itself.

02.10.2026, dedo-home: the build stopped on its database schema in Programovanie, the Director approved it in a
free-text answer and Dedo copied it into the Knowledge Base from a terminal. 09.10.2026, NEX Inbox: the schema in
the Knowledge Base was still v0.1.0 although 1.7.0 added migration 025 — approved inside the Návrh, never written
anywhere. Director: „Áno, rozšír DEV-7 a postav to takto".

Pinned here:
* the Návrh brief asks for the WHOLE target schema of a database app in a fixed place; the Auditor checks it;
* a database app whose Návrh brings no schema document is asked once more, then the Návrh stops with the reason;
* a Návrh that changes the schema says so and only Ri may approve it; approving writes the Knowledge Base first,
  and a Knowledge Base that refuses the write leaves the Návrh unapproved;
* in Programovanie an agent that needs a schema change stops for Ri (``schema_approval``) instead of asking a
  free-text question; Ri's approval publishes the schema and the agent continues;
* Programovanie does not start on a schema that changed after the Návrh was approved (the Vizuál write-back);
* the board says what the Manažér is approving.
"""

from __future__ import annotations

import asyncio
import subprocess
import uuid
from pathlib import Path

import pytest

from backend.api.routes.pipeline import _board
from backend.db.models.foundation import User
from backend.services import database_schema, orchestrator
from backend.services.orchestrator import OrchestratorError
from backend.services.pipeline_status import PipelineStatusBlock
from tests.services.test_database_schema_publish import SCHEMA, _git, _kb
from tests.test_orchestrator_v2_navrh import _design_done, _make_version, _msgs, _seed_navrh
from tests.test_orchestrator_v2_programovanie import (
    _done_block,
    _no_baseline_git,
    _seed_programovanie,
    _seed_tasks,
    _stub_mech,
    _stub_turns,
)

CHANGED = SCHEMA + "| email | text |\n"


def _user(db_session, role):
    user = User(
        username=f"{role}_{uuid.uuid4().hex[:6]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role=role,
    )
    db_session.add(user)
    db_session.flush()
    return user


def _checkout(tmp_path: Path, version, *, schema: str | None, design: bool = True) -> Path:
    root = tmp_path / "checkout"
    if design:
        doc = root / orchestrator._navrh_design_doc_rel(version.version_number)
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text("# Návrh\n\n## Dátový model\n\nTabuľka users.\n", encoding="utf-8")
    if schema is not None:
        doc = root / orchestrator._database_schema_rel(version.version_number)
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(schema, encoding="utf-8")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _point_kb(monkeypatch, kb: Path) -> None:
    monkeypatch.setattr(database_schema, "kb_root", lambda: kb)


def _kb_with(tmp_path: Path, slug: str, doc: str | None) -> Path:
    kb = _kb(tmp_path)
    if doc is not None:
        (kb / "projects" / slug).mkdir(parents=True)
        (kb / "projects" / slug / "DATABASE_SCHEMAS.md").write_text(doc, encoding="utf-8")
        _git(kb, "add", "-A")
        _git(kb, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "schema")
    return kb


def _turns(monkeypatch, design_turns, *, auditor_pass=True):
    """Script the AI Agent's Návrh turns (a callable may write files before returning); the Auditor passes."""
    calls: list[dict] = []
    queue = list(design_turns)

    async def _fake(db, *, version_id, role, stage, prompt, **_kw):
        if role == orchestrator.AUDITOR_ROLE:
            calls.append({"role": role, "prompt": prompt})
            return PipelineStatusBlock(stage="navrh", kind="verdict", summary="ok", awaiting="manazer", verdict=True)
        calls.append({"role": role, "prompt": prompt})
        turn = queue.pop(0) if len(queue) > 1 else queue[0]
        return turn() if callable(turn) else turn

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake)
    return calls


# ── Návrh: the brief, the document, the stop ────────────────────────────────────────────────────────────────────


async def test_the_navrh_brief_asks_for_the_whole_schema_of_a_database_app(db_session, monkeypatch):
    version, project = _make_version(db_session)
    _seed_navrh(db_session, version.id)

    brief = orchestrator._navrh_directive(db_session, version.id)

    assert f"`{orchestrator._database_schema_rel(version.version_number)}`" in brief
    assert "CELÚ cieľovú schému" in brief
    assert f"projects/{project.slug}/DATABASE_SCHEMAS.md" in brief
    assert "schvaľuje Ri" in brief


def test_the_auditor_checks_the_schema_document_against_the_design(db_session):
    version, _ = _make_version(db_session)

    brief = orchestrator._auditor_upfront_directive(db_session, version.id)

    assert f"`{orchestrator._database_schema_rel(version.version_number)}`" in brief
    assert "dátovým modelom" in brief


async def test_a_navrh_that_changes_the_schema_says_so_and_waits_for_ri(db_session, monkeypatch, tmp_path):
    version, project = _make_version(db_session, project_dial="plna")
    project.source_path = str(_checkout(tmp_path, version, schema=SCHEMA))
    _point_kb(monkeypatch, _kb_with(tmp_path, project.slug, None))
    _seed_navrh(db_session, version.id)
    _turns(monkeypatch, [_design_done()])

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert (state.current_stage, state.status) == ("navrh", "awaiting_manazer")
    assert "štruktúru databázy" in state.next_action and "Ri" in state.next_action
    [note] = [m for m in _msgs(db_session, version.id) if m.payload and m.payload.get("database_schema")]
    assert note.payload["database_schema"] == {
        "path": orchestrator._database_schema_rel(version.version_number),
        "kb_path": f"projects/{project.slug}/DATABASE_SCHEMAS.md",
        "kb_exists": False,
        "added_lines": len(SCHEMA.splitlines()),
        "removed_lines": 0,
    }
    assert "prvá schéma" in note.content


async def test_a_database_app_without_a_schema_document_is_asked_once(db_session, monkeypatch, tmp_path):
    version, project = _make_version(db_session)
    root = _checkout(tmp_path, version, schema=None)
    project.source_path = str(root)
    _point_kb(monkeypatch, _kb_with(tmp_path, project.slug, SCHEMA))
    _seed_navrh(db_session, version.id)

    def _writes_it():
        doc = root / orchestrator._database_schema_rel(version.version_number)
        doc.write_text(CHANGED, encoding="utf-8")
        return _design_done()

    calls = _turns(monkeypatch, [_design_done(), _writes_it])

    state = await orchestrator.run_dispatch(db_session, version.id)

    agent_prompts = [c["prompt"] for c in calls if c["role"] != orchestrator.AUDITOR_ROLE]
    assert agent_prompts[1] == orchestrator.database_schema_missing(version.version_number, project.slug)
    assert (state.current_stage, state.status) == ("navrh", "awaiting_manazer")


async def test_a_database_app_that_never_writes_the_schema_document_stops(db_session, monkeypatch, tmp_path):
    version, project = _make_version(db_session)
    project.source_path = str(_checkout(tmp_path, version, schema=None))
    _point_kb(monkeypatch, _kb_with(tmp_path, project.slug, SCHEMA))
    _seed_navrh(db_session, version.id)
    calls = _turns(monkeypatch, [_design_done()])

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert len([c for c in calls if c["role"] != orchestrator.AUDITOR_ROLE]) == 2  # asked once more, not looped
    assert (state.current_stage, state.status, state.block_reason) == ("navrh", "blocked", "agent_error")
    assert "štruktúry databázy" in state.next_action


async def test_an_app_without_a_database_needs_no_schema_document(db_session, monkeypatch, tmp_path):
    version, project = _make_version(db_session)
    project.source_path = str(_checkout(tmp_path, version, schema=None))
    _point_kb(monkeypatch, _kb_with(tmp_path, project.slug, None))
    _seed_navrh(db_session, version.id)
    calls = _turns(monkeypatch, [_design_done()])

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert len([c for c in calls if c["role"] != orchestrator.AUDITOR_ROLE]) == 1
    assert (state.current_stage, state.status) == ("navrh", "awaiting_manazer")


# ── Návrh: who approves, and what approving does ────────────────────────────────────────────────────────────────


def _awaiting_navrh(db_session, monkeypatch, tmp_path, *, schema):
    version, project = _make_version(db_session)
    project.source_path = str(_checkout(tmp_path, version, schema=schema))
    kb = _kb_with(tmp_path, project.slug, None)
    _point_kb(monkeypatch, kb)
    state = _seed_navrh(db_session, version.id)
    state.status = "awaiting_manazer"
    db_session.flush()
    return version, project, kb


@pytest.mark.parametrize("role", ["ha", "shu", None])
async def test_only_ri_may_approve_a_navrh_that_changes_the_schema(db_session, monkeypatch, tmp_path, role):
    version, project, kb = _awaiting_navrh(db_session, monkeypatch, tmp_path, schema=SCHEMA)
    head = _git(kb, "rev-parse", "HEAD")
    acting = _user(db_session, role) if role else None

    with pytest.raises(OrchestratorError, match="Ri"):
        await orchestrator.apply_action(
            db_session, version_id=version.id, action="schvalit", payload={}, acting_user=acting
        )

    assert _git(kb, "rev-parse", "HEAD") == head
    assert not (kb / "projects" / project.slug).exists()
    assert orchestrator._get_state(db_session, version.id).current_stage == "navrh"


async def test_ri_approving_the_navrh_writes_the_schema_into_the_kb_and_moves_on(db_session, monkeypatch, tmp_path):
    version, project, kb = _awaiting_navrh(db_session, monkeypatch, tmp_path, schema=SCHEMA)
    ri = _user(db_session, "ri")

    state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="schvalit", payload={}, acting_user=ri
    )

    assert (kb / "projects" / project.slug / "DATABASE_SCHEMAS.md").read_text(encoding="utf-8") == SCHEMA
    assert ri.username in _git(kb, "log", "-1", "--format=%s")
    assert state.current_stage == "vizual"
    commit = _git(kb, "rev-parse", "--short", "HEAD").strip()
    [note] = [m for m in _msgs(db_session, version.id) if m.payload and m.payload.get("database_schema_published")]
    assert f"projects/{project.slug}/DATABASE_SCHEMAS.md" in note.content and commit in note.content


async def test_a_kb_that_refuses_the_write_leaves_the_navrh_unapproved(db_session, monkeypatch, tmp_path):
    version, _, kb = _awaiting_navrh(db_session, monkeypatch, tmp_path, schema=SCHEMA)
    (kb / "projects/INDEX.md").write_text("cudzia rozpísaná zmena\n", encoding="utf-8")
    subprocess.run(["git", "add", "projects/INDEX.md"], cwd=kb, check=True)

    with pytest.raises(OrchestratorError, match="projects/INDEX.md"):
        await orchestrator.apply_action(
            db_session, version_id=version.id, action="schvalit", payload={}, acting_user=_user(db_session, "ri")
        )

    assert orchestrator._get_state(db_session, version.id).current_stage == "navrh"
    assert not [m for m in _msgs(db_session, version.id) if m.kind == "approval"]


async def test_a_navrh_without_a_schema_change_is_approved_as_before(db_session, monkeypatch, tmp_path):
    version, _, kb = _awaiting_navrh(db_session, monkeypatch, tmp_path, schema=None)
    head = _git(kb, "rev-parse", "HEAD")

    state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="schvalit", payload={}, acting_user=_user(db_session, "ha")
    )

    assert state.current_stage == "vizual"
    assert _git(kb, "rev-parse", "HEAD") == head


# ── Programovanie: a schema change the agent needs on the way ───────────────────────────────────────────────────


def _building(db_session, monkeypatch, tmp_path, *, schema, titles=("T1",)):
    version, project = _make_version(db_session)
    project.source_path = str(_checkout(tmp_path, version, schema=schema))
    kb = _kb_with(tmp_path, project.slug, SCHEMA)
    _point_kb(monkeypatch, kb)
    state = _seed_programovanie(db_session, version.id)
    _seed_tasks(db_session, version, project, list(titles))
    _no_baseline_git(monkeypatch)
    _stub_mech(monkeypatch, [None])
    return version, project, kb, state


def _asks_for_schema(summary="Pridať stĺpec email do tabuľky users — prihlásenie e-mailom."):
    return PipelineStatusBlock(
        stage="programovanie",
        kind="question",
        summary="potrebujem zmenu schémy",
        awaiting="manazer",
        question="Môžem pridať stĺpec email?",
        database_schema_change=summary,
    )


def _agent_writes_then_asks(monkeypatch, project, version):
    """The agent changes the version's schema document mid-task, then asks for the change to be approved."""

    async def _fake(db, *, version_id, role, stage, prompt, **_kw):
        doc = Path(project.source_path) / orchestrator._database_schema_rel(version.version_number)
        doc.write_text(CHANGED, encoding="utf-8")
        return _asks_for_schema()

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake)


async def test_an_agent_that_needs_a_schema_change_stops_for_ri(db_session, monkeypatch, tmp_path):
    version, project, _, _ = _building(db_session, monkeypatch, tmp_path, schema=SCHEMA)
    _agent_writes_then_asks(monkeypatch, project, version)

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert (state.status, state.block_reason) == ("blocked", "schema_approval")
    assert "Pridať stĺpec email do tabuľky users" in state.next_action and "Ri" in state.next_action
    assert "schvalit_schemu" in orchestrator.determine_available_actions(state)


async def test_a_question_without_an_actual_schema_change_stays_a_question(db_session, monkeypatch, tmp_path):
    version, _, _, _ = _building(db_session, monkeypatch, tmp_path, schema=SCHEMA)
    _stub_turns(monkeypatch, [_asks_for_schema()])

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert (state.status, state.block_reason) == ("blocked", "agent_question")
    assert "schvalit_schemu" not in orchestrator.determine_available_actions(state)


def _blocked_on_schema(db_session, monkeypatch, tmp_path):
    version, project, kb, state = _building(db_session, monkeypatch, tmp_path, schema=CHANGED)
    state.status = "blocked"
    state.block_reason = "schema_approval"
    db_session.flush()
    return version, project, kb


async def test_ri_approving_the_schema_change_publishes_it_and_the_agent_continues(db_session, monkeypatch, tmp_path):
    version, project, kb = _blocked_on_schema(db_session, monkeypatch, tmp_path)

    state = await orchestrator.apply_action(
        db_session, version_id=version.id, action="schvalit_schemu", payload={}, acting_user=_user(db_session, "ri")
    )

    assert (kb / "projects" / project.slug / "DATABASE_SCHEMAS.md").read_text(encoding="utf-8") == CHANGED
    assert state.status == "agent_working" and state.block_reason is None
    answer = [m for m in _msgs(db_session, version.id) if m.kind == "answer"][-1]
    assert answer.author == "manazer" and answer.recipient == "ai_agent"
    assert f"projects/{project.slug}/DATABASE_SCHEMAS.md" in answer.content and "Pokračuj" in answer.content


async def test_schvalit_schemu_by_someone_else_is_refused(db_session, monkeypatch, tmp_path):
    version, project, kb = _blocked_on_schema(db_session, monkeypatch, tmp_path)
    head = _git(kb, "rev-parse", "HEAD")

    with pytest.raises(OrchestratorError, match="Ri"):
        await orchestrator.apply_action(
            db_session, version_id=version.id, action="schvalit_schemu", payload={}, acting_user=_user(db_session, "ha")
        )

    assert _git(kb, "rev-parse", "HEAD") == head
    assert orchestrator._get_state(db_session, version.id).block_reason == "schema_approval"


async def test_only_ri_moves_a_schema_stop_even_when_the_kb_already_holds_the_schema(db_session, monkeypatch, tmp_path):
    version, project, kb = _blocked_on_schema(db_session, monkeypatch, tmp_path)
    (kb / "projects" / project.slug / "DATABASE_SCHEMAS.md").write_text(CHANGED, encoding="utf-8")
    _git(kb, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "someone published it meanwhile")

    with pytest.raises(OrchestratorError, match="Ri"):
        await orchestrator.apply_action(
            db_session, version_id=version.id, action="schvalit_schemu", payload={}, acting_user=_user(db_session, "ha")
        )

    assert orchestrator._get_state(db_session, version.id).block_reason == "schema_approval"
    assert not [m for m in _msgs(db_session, version.id) if (m.payload or {}).get("schema_approved")]


def _record_reindex(monkeypatch):
    seen: list[str] = []

    class _Indexer:
        async def index_document(self, path, *, tenant, source_file):
            seen.append(source_file)

    monkeypatch.setattr("backend.rag.indexer.RAGIndexer", _Indexer)
    return seen


async def test_a_publish_into_another_folder_never_reaches_the_live_search_index(db_session, monkeypatch, tmp_path):
    version, _, _ = _awaiting_navrh(db_session, monkeypatch, tmp_path, schema=SCHEMA)
    seen = _record_reindex(monkeypatch)

    await orchestrator.apply_action(
        db_session, version_id=version.id, action="schvalit", payload={}, acting_user=_user(db_session, "ri")
    )
    await asyncio.sleep(0.05)

    assert seen == []


async def test_a_publish_into_the_configured_kb_is_reindexed(db_session, monkeypatch, tmp_path):
    version, project, kb = _awaiting_navrh(db_session, monkeypatch, tmp_path, schema=SCHEMA)
    monkeypatch.setattr(orchestrator.settings, "knowledge_base_path", str(kb))
    seen = _record_reindex(monkeypatch)

    await orchestrator.apply_action(
        db_session, version_id=version.id, action="schvalit", payload={}, acting_user=_user(db_session, "ri")
    )
    await asyncio.sleep(0.05)

    assert sorted(seen) == sorted(
        ["icc/SCHEMA_GOVERNANCE.md", "projects/INDEX.md", f"projects/{project.slug}/DATABASE_SCHEMAS.md"]
    )


async def test_schvalit_schemu_outside_a_schema_stop_is_refused(db_session, monkeypatch, tmp_path):
    version, _, _, state = _building(db_session, monkeypatch, tmp_path, schema=CHANGED)
    state.status = "blocked"
    state.block_reason = "agent_question"
    db_session.flush()

    with pytest.raises(OrchestratorError):
        await orchestrator.apply_action(
            db_session, version_id=version.id, action="schvalit_schemu", payload={}, acting_user=_user(db_session, "ri")
        )


async def test_programovanie_does_not_start_on_a_schema_changed_after_the_navrh(db_session, monkeypatch, tmp_path):
    version, project = _make_version(db_session)
    project.source_path = str(_checkout(tmp_path, version, schema=CHANGED))  # the Vizuál write-back changed it
    _point_kb(monkeypatch, _kb_with(tmp_path, project.slug, SCHEMA))
    _seed_programovanie(db_session, version.id)  # no plan yet: Programovanie is just starting
    calls = _stub_turns(monkeypatch, [_done_block()])

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert (state.status, state.block_reason) == ("blocked", "schema_approval")
    assert "od schválenia Návrhu zmenila" in state.next_action
    assert calls == []  # no plan pass and no task ran on a schema nobody approved


async def test_a_task_that_changed_the_schema_without_asking_stops_the_build_after_it(
    db_session, monkeypatch, tmp_path
):
    version, project, _, _ = _building(db_session, monkeypatch, tmp_path, schema=SCHEMA, titles=("T1", "T2"))
    calls = []

    async def _fake(db, *, version_id, role, stage, prompt, **_kw):
        calls.append(prompt)
        doc = Path(project.source_path) / orchestrator._database_schema_rel(version.version_number)
        doc.write_text(CHANGED, encoding="utf-8")
        return _done_block()

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake)

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert (state.status, state.block_reason) == ("blocked", "schema_approval")
    assert "zmenila štruktúru databázy bez schválenia" in state.next_action
    assert len(calls) == 1  # the next task waits for Ri


async def test_every_task_of_a_database_app_carries_the_schema_rule(db_session, monkeypatch, tmp_path):
    version, project, _, _ = _building(db_session, monkeypatch, tmp_path, schema=SCHEMA)
    calls = _stub_turns(monkeypatch, [_done_block()])

    await orchestrator.run_dispatch(db_session, version.id)

    assert "`database_schema_change`" in calls[0]["prompt"]
    assert f"`{orchestrator._database_schema_rel(version.version_number)}`" in calls[0]["prompt"]


async def test_the_approval_reaches_the_resumed_task(db_session, monkeypatch, tmp_path):
    version, _, _ = _blocked_on_schema(db_session, monkeypatch, tmp_path)
    await orchestrator.apply_action(
        db_session, version_id=version.id, action="schvalit_schemu", payload={}, acting_user=_user(db_session, "ri")
    )

    directive = orchestrator.dispatch_directive(db_session, version.id, "schvalit_schemu", {}, "programovanie")

    assert directive is not None and "Schvaľujem zmenu štruktúry databázy" in directive and "Pokračuj" in directive


# ── what the Manažér sees ───────────────────────────────────────────────────────────────────────────────────────


def test_the_board_says_what_the_manager_is_approving(db_session, monkeypatch, tmp_path):
    version, project, _ = _awaiting_navrh(db_session, monkeypatch, tmp_path, schema=SCHEMA)

    board = _board(db_session, version.id)

    assert board.database_schema is not None
    assert board.database_schema.model_dump() == {
        "path": orchestrator._database_schema_rel(version.version_number),
        "kb_path": f"projects/{project.slug}/DATABASE_SCHEMAS.md",
        "kb_exists": False,
        "changes": True,
        "added_lines": len(SCHEMA.splitlines()),
        "removed_lines": 0,
        "approver_role": "ri",
    }


def test_the_board_is_silent_without_a_schema_document(db_session, monkeypatch, tmp_path):
    version, _, _ = _awaiting_navrh(db_session, monkeypatch, tmp_path, schema=None)

    assert _board(db_session, version.id).database_schema is None
