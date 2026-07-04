"""STEP 3 — Plán úloh in the conversation register (step3-plan-design.md).

After the Špecifikácia is approved, the "Zostaviť plán" action composes the EPIC→FEAT→TASK task plan FROM
the frozen Špecifikácia — reusing the PROVEN incremental machinery (skeleton pass + per-feat passes +
MAX_PLAN_FEATS + fail-closed HALT), NOT a whole-tree parse off one turn. Exercised against the real v2
branch DB (4-phase CHECKs). Proves the four design fixes end-to-end:

* **FIX1 (honest stage)** — every record of the conversation plan round carries ``stage='priprava'`` AND
  ``payload['phase']=='priprava'`` (nothing hardcodes ``navrh`` on the conversation path); the Návrh path
  stays ``navrh`` byte-identical.
* **FIX2 (button split like schvalit)** — ``determine_available_actions`` offers ``zostav_plan``
  unconditionally at ``priprava`` (state-only); the board route POST-FILTERS it to conversation +
  spec-approved + plan-not-materialized; ``apply_action`` enforces the same rule authoritatively (raises on
  each missing condition).
* **FIX3 (restart-safe trigger)** — ``dispatch_directive`` returns ``None`` for ``zostav_plan``;
  ``run_conversation_turn`` delegates to the plan round SOLELY on the durable ``compose_plan`` DB marker,
  never entering ``run_dispatch`` / the phase automaton.
* **FIX4 (plain_description)** — the composed Epic/Feat/Task rows carry ``plain_description`` (populated when
  emitted, empty when omitted — the Epic's ONLY prose).

MD-2 — the round re-reads the CURRENT specification.md and rebuilds the plan IN PLACE (SAVEPOINT
drop-and-recreate); a second compose never duplicates.

The narrowed task-plan passes are stubbed via a controllable fake ``invoke_claude`` (the real
structured-output path), exactly like ``test_orchestrator_v2_navrh.py``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from backend.api.routes import pipeline as pipeline_routes
from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import orchestrator
from backend.services.orchestrator import OrchestratorError

# (pytest ``asyncio_mode = auto`` — async tests run without an explicit mark.)


@pytest.fixture(autouse=True)
def _clean_engine_sessions():
    orchestrator._RELAY_QUEUES.clear()
    orchestrator._ENGINE_ACTIVE_SESSIONS.clear()
    yield
    orchestrator._RELAY_QUEUES.clear()
    orchestrator._ENGINE_ACTIVE_SESSIONS.clear()


# ── fixtures ────────────────────────────────────────────────────────────────


def _make_version(db_session, *, source_path=None):
    user = User(
        username=f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()
    project = Project(
        name=f"P {uuid.uuid4().hex[:8]}",
        slug=f"p-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="d",
        created_by=user.id,
        source_path=source_path,  # None → _write_task_plan_doc / _repo_head are graceful no-ops
    )
    db_session.add(project)
    db_session.flush()
    version = Version(project_id=project.id, version_number=f"1.{uuid.uuid4().hex[:4]}.0")
    db_session.add(version)
    db_session.flush()
    return version, project


def _seed_conversation(db_session, version_id, *, status="awaiting_manazer", mode="conversation"):
    """A settled spine build: mode='conversation', stage=priprava/actor=ai_agent (the SAME shape as a phase
    build). ``dispatch_in_flight`` is only set while agent_working."""
    state = PipelineState(
        version_id=version_id,
        flow_type="new_version",
        current_stage="priprava",
        current_actor="ai_agent",
        status=status,
        next_action="rozhovor",
        mode=mode,
        dispatch_in_flight=(status == "agent_working"),
    )
    db_session.add(state)
    db_session.flush()
    return state


def _seed_navrh_phase(db_session, version_id):
    """A legacy phase build parked at Návrh (mode NULL) — for the byte-identity control."""
    state = PipelineState(
        version_id=version_id,
        flow_type="new_version",
        current_stage="navrh",
        current_actor="ai_agent",
        status="agent_working",
        next_action="working",
        mode=None,
    )
    db_session.add(state)
    db_session.flush()
    return state


def _approve_spec(db_session, version_id):
    """Record the durable kind='approval' Špecifikácia freeze signal (what orchestrator.spec_approved reads)."""
    db_session.add(
        PipelineMessage(
            version_id=version_id,
            stage="priprava",
            author="manazer",
            recipient="ai_agent",
            kind="approval",
            content="Špecifikácia schválená.",
            payload={"phase": "priprava", "approve_spec": True},
        )
    )
    db_session.flush()


def _materialize_one_task(db_session, project_id, version_id):
    """A minimal EPIC→FEAT→TASK so navrh_plan_materialized() is True."""
    epic = Epic(project_id=project_id, version_id=version_id, number=1, title="E")
    db_session.add(epic)
    db_session.flush()
    feat = Feat(epic_id=epic.id, number=1, title="F", description="")
    db_session.add(feat)
    db_session.flush()
    db_session.add(Task(feat_id=feat.id, number=1, title="T", description="", task_type="backend"))
    db_session.flush()


def _msgs(db_session, version_id):
    return (
        db_session.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version_id).order_by(PipelineMessage.seq.asc())
        )
        .scalars()
        .all()
    )


def _epics(db_session, version_id):
    return db_session.execute(select(Epic).where(Epic.version_id == version_id).order_by(Epic.number)).scalars().all()


# ── Stub the narrowed task-plan passes (invoke_claude) ────────────────────────


def _skeleton_dict(plan_spec, cross="## Invarianty\n- x", *, flagship=None, safety=None) -> dict:
    epics = []
    for e in plan_spec:
        feats = [
            {
                "title": f["title"],
                "description": f.get("description", ""),
                "plain_description": f.get("plain_description", ""),
                **({"estimated_minutes": f["estimated_minutes"]} if f.get("estimated_minutes") else {}),
            }
            for f in e["feats"]
        ]
        epics.append({"title": e["title"], "plain_description": e.get("plain_description", ""), "feats": feats})
    obj: dict = {"epics": epics, "cross_cutting_rules": cross}
    obj["flagship_features"] = flagship if flagship is not None else ["Kľúčová funkcia"]
    obj["safety_properties"] = safety if safety is not None else []
    return obj


def _feat_tasks_dict(tasks) -> dict:
    return {
        "tasks": [
            {
                "title": t["title"],
                "task_type": t["task_type"],
                "description": t.get("description", ""),
                "plain_description": t.get("plain_description", ""),
            }
            for t in tasks
        ]
    }


def _stub_plan_passes(monkeypatch, plan_spec, *, cross="## Invarianty\n- x"):
    """Drive the folded task-plan passes via a fake ``invoke_claude``: the skeleton pass (prompt contains
    "KOSTRU") → EPIC+FEAT(no tasks)+cross(+coverage); a per-feat pass (the feat title appears) → that feat's
    tasks. Mirrors ``test_orchestrator_v2_navrh._stub_plan_passes`` (structured_output shape)."""
    feat_by_title = {f["title"]: f["tasks"] for e in plan_spec for f in e["feats"]}

    async def _fake_invoke_claude(*, prompt, **_kw):
        if "KOSTRU" in prompt:
            return ("", None, _skeleton_dict(plan_spec, cross))
        for title, tasks in feat_by_title.items():
            if title in prompt:
                return ("", None, _feat_tasks_dict(tasks))
        raise AssertionError(f"unexpected plan-pass prompt: {prompt[:80]}")

    monkeypatch.setattr(orchestrator, "invoke_claude", _fake_invoke_claude)
    monkeypatch.setattr(orchestrator, "_split_claude_result", lambda r: r)
    monkeypatch.setattr(orchestrator, "_resolve_orch_session", lambda db, slug, role: (uuid.uuid4(), False))
    monkeypatch.setattr(orchestrator, "_resolve_dispatch_overrides", lambda db, vid, role: (None, None))


def _plan(*, epic_plain="Základ appky.", feat_plain="Založíme databázu.", task_plain="Tabuľky používateľov."):
    return [
        {
            "title": "Foundation",
            "plain_description": epic_plain,
            "feats": [
                {
                    "title": "Schema",
                    "description": "DB schema",
                    "plain_description": feat_plain,
                    "estimated_minutes": 60,
                    "tasks": [
                        {"title": "users table", "task_type": "migration", "plain_description": task_plain},
                    ],
                }
            ],
        }
    ]


# ── FIX2: button gating (state-only offer + board post-filter + apply_action guard) ──


class TestZostavPlanGating:
    def test_determine_available_actions_offers_zostav_plan_at_priprava(self, db_session):
        version, _ = _make_version(db_session)
        state = _seed_conversation(db_session, version.id)
        # State-only (like schvalit) — offered UNCONDITIONALLY at a settled priprava, no DB read.
        assert "zostav_plan" in orchestrator.determine_available_actions(state)

    def test_board_offers_only_when_conversation_spec_approved_not_materialized(self, db_session):
        version, project = _make_version(db_session)
        _seed_conversation(db_session, version.id)
        # (c) conversation but NOT spec-approved → post-filtered out.
        assert "zostav_plan" not in pipeline_routes._board(db_session, version.id).available_actions
        # (a) conversation + spec-approved + not materialized → OFFERED.
        _approve_spec(db_session, version.id)
        assert "zostav_plan" in pipeline_routes._board(db_session, version.id).available_actions
        # (d) conversation + spec-approved + plan MATERIALIZED → post-filtered out.
        _materialize_one_task(db_session, project.id, version.id)
        assert "zostav_plan" not in pipeline_routes._board(db_session, version.id).available_actions

    def test_board_hides_zostav_plan_on_legacy_phase_build(self, db_session):
        # (b) mode NULL (phase automaton) — determine_available_actions still offers it (state-only), but the
        # board post-filter drops it because mode != 'conversation'.
        version, _ = _make_version(db_session)
        _seed_conversation(db_session, version.id, mode=None)
        _approve_spec(db_session, version.id)
        assert "zostav_plan" not in pipeline_routes._board(db_session, version.id).available_actions

    async def test_apply_action_raises_when_not_conversation(self, db_session):
        version, _ = _make_version(db_session)
        _seed_conversation(db_session, version.id, mode=None)
        _approve_spec(db_session, version.id)
        with pytest.raises(OrchestratorError, match="rozhovorovom"):
            await orchestrator.apply_action(db_session, version_id=version.id, action="zostav_plan")

    async def test_apply_action_raises_when_spec_not_approved(self, db_session):
        version, _ = _make_version(db_session)
        _seed_conversation(db_session, version.id)  # no approval message
        with pytest.raises(OrchestratorError, match="schválení Špecifikácie"):
            await orchestrator.apply_action(db_session, version_id=version.id, action="zostav_plan")

    async def test_apply_action_raises_when_plan_already_materialized(self, db_session):
        version, project = _make_version(db_session)
        _seed_conversation(db_session, version.id)
        _approve_spec(db_session, version.id)
        _materialize_one_task(db_session, project.id, version.id)
        with pytest.raises(OrchestratorError, match="už existuje"):
            await orchestrator.apply_action(db_session, version_id=version.id, action="zostav_plan")


# ── FIX3: apply_action arms the durable marker + restart-safe trigger ─────────


class TestZostavPlanArmsMarker:
    async def test_apply_action_records_compose_plan_marker_and_arms_working(self, db_session):
        version, _ = _make_version(db_session)
        _seed_conversation(db_session, version.id)
        _approve_spec(db_session, version.id)

        state = await orchestrator.apply_action(db_session, version_id=version.id, action="zostav_plan")

        assert state.status == "agent_working"  # _begin_dispatch armed the turn
        assert state.current_stage == "priprava"  # NO phase walk
        assert state.dispatch_in_flight is True
        marker = _msgs(db_session, version.id)[-1]
        assert marker.kind == "directive" and marker.author == "manazer" and marker.recipient == "ai_agent"
        assert marker.payload.get("compose_plan") is True and marker.stage == "priprava"
        # FIX3: the in-memory dispatch directive is None for zostav_plan (only the DB marker drives the round).
        assert orchestrator.dispatch_directive(db_session, version.id, "zostav_plan", {}, "priprava") is None

    def test_pending_marker_true_only_when_latest_is_compose_plan(self, db_session):
        version, _ = _make_version(db_session)
        _seed_conversation(db_session, version.id)
        assert orchestrator._pending_compose_plan_marker(db_session, version.id) is False
        _approve_spec(db_session, version.id)  # latest is an approval, not a compose_plan directive
        assert orchestrator._pending_compose_plan_marker(db_session, version.id) is False
        db_session.add(
            PipelineMessage(
                version_id=version.id,
                stage="priprava",
                author="manazer",
                recipient="ai_agent",
                kind="directive",
                content="Zostav plán úloh zo schválenej Špecifikácie.",
                payload={"phase": "priprava", "compose_plan": True},
            )
        )
        db_session.flush()
        assert orchestrator._pending_compose_plan_marker(db_session, version.id) is True


# ── FIX1 + FIX4: the conversation plan round (honest stage + plain_description) ──


class TestConversationPlanRound:
    async def test_round_materializes_plan_honest_priprava_stage(self, db_session, monkeypatch):
        version, project = _make_version(db_session)
        _seed_conversation(db_session, version.id)
        _approve_spec(db_session, version.id)
        _stub_plan_passes(monkeypatch, _plan())

        # run_dispatch (the phase automaton) must NEVER be entered on the conversation path.
        async def _boom_dispatch(*a, **k):  # pragma: no cover
            raise AssertionError("conversation plan round must not enter run_dispatch")

        def _boom_advance(*a, **k):  # pragma: no cover
            raise AssertionError("conversation plan round must not advance a phase")

        monkeypatch.setattr(orchestrator, "run_dispatch", _boom_dispatch)
        # No phase advance — the spine never touches the automaton's settle/advance.
        monkeypatch.setattr(orchestrator, "_settle_phase_boundary", _boom_advance)
        monkeypatch.setattr(orchestrator, "_next_stage", _boom_advance)

        state = await orchestrator.apply_action(db_session, version_id=version.id, action="zostav_plan")
        assert state.status == "agent_working"

        out = await orchestrator.run_conversation_turn(db_session, version.id)

        # Settles for the Manažér, current_stage UNCHANGED (no phase advance — the spine invariant).
        assert out.status == "awaiting_manazer"
        assert out.current_stage == "priprava"
        assert out.mode == "conversation"

        # The plan actually materialized.
        epics = _epics(db_session, version.id)
        assert len(epics) == 1 and epics[0].title == "Foundation"
        feats = db_session.execute(select(Feat).where(Feat.epic_id == epics[0].id)).scalars().all()
        assert len(feats) == 1
        tasks = db_session.execute(select(Task).where(Task.feat_id == feats[0].id)).scalars().all()
        assert len(tasks) == 1

        # FIX1: EVERY plan-round record carries the honest priprava stage AND payload phase — never navrh.
        plan_records = [
            m
            for m in _msgs(db_session, version.id)
            if m.kind in ("gate_report", "notification") and (m.author in ("ai_agent", "system"))
        ]
        assert plan_records, "expected plan-round records"
        for m in plan_records:
            assert m.stage == "priprava", f"{m.kind} recorded on stage {m.stage!r} — must be priprava"
            if isinstance(m.payload, dict) and "phase" in m.payload:
                assert m.payload["phase"] == "priprava", f"{m.kind} payload phase {m.payload['phase']!r}"
        # The gate_report explicitly carries phase=priprava (not navrh).
        gate = [m for m in plan_records if m.kind == "gate_report"][-1]
        assert gate.payload["phase"] == "priprava"

    async def test_round_carries_plain_description_populated(self, db_session, monkeypatch):
        version, project = _make_version(db_session)
        _seed_conversation(db_session, version.id)
        _approve_spec(db_session, version.id)
        _stub_plan_passes(
            monkeypatch,
            _plan(epic_plain="Základ appky.", feat_plain="Databáza a audit.", task_plain="Tabuľka faktúr."),
        )

        await orchestrator.apply_action(db_session, version_id=version.id, action="zostav_plan")
        await orchestrator.run_conversation_turn(db_session, version.id)

        epic = _epics(db_session, version.id)[0]
        assert epic.plain_description == "Základ appky."  # the Epic's ONLY prose
        feat = db_session.execute(select(Feat).where(Feat.epic_id == epic.id)).scalars().one()
        assert feat.plain_description == "Databáza a audit."
        assert feat.description == "DB schema"  # distinct technical description preserved
        task = db_session.execute(select(Task).where(Task.feat_id == feat.id)).scalars().one()
        assert task.plain_description == "Tabuľka faktúr."

    async def test_round_plain_description_empty_when_omitted(self, db_session, monkeypatch):
        version, project = _make_version(db_session)
        _seed_conversation(db_session, version.id)
        _approve_spec(db_session, version.id)
        # Empty one-liners everywhere (a valid omission — default empty parses).
        _stub_plan_passes(monkeypatch, _plan(epic_plain="", feat_plain="", task_plain=""))

        await orchestrator.apply_action(db_session, version_id=version.id, action="zostav_plan")
        await orchestrator.run_conversation_turn(db_session, version.id)

        epic = _epics(db_session, version.id)[0]
        assert epic.plain_description == ""  # empty, never fabricated
        feat = db_session.execute(select(Feat).where(Feat.epic_id == epic.id)).scalars().one()
        task = db_session.execute(select(Task).where(Task.feat_id == feat.id)).scalars().one()
        assert feat.plain_description == "" and task.plain_description == ""

    async def test_round_directive_re_reads_current_specification(self, db_session, monkeypatch):
        """MD-2: the skeleton pass's brief names the CURRENT specification.md (the single source of truth)."""
        version, _ = _make_version(db_session)
        _seed_conversation(db_session, version.id)
        _approve_spec(db_session, version.id)
        captured = {}

        async def _fake_invoke_claude(*, prompt, **_kw):
            if "KOSTRU" in prompt:
                captured["skeleton_prompt"] = prompt
                return ("", None, _skeleton_dict(_plan()))
            return ("", None, _feat_tasks_dict(_plan()[0]["feats"][0]["tasks"]))

        monkeypatch.setattr(orchestrator, "invoke_claude", _fake_invoke_claude)
        monkeypatch.setattr(orchestrator, "_split_claude_result", lambda r: r)
        monkeypatch.setattr(orchestrator, "_resolve_orch_session", lambda db, slug, role: (uuid.uuid4(), False))
        monkeypatch.setattr(orchestrator, "_resolve_dispatch_overrides", lambda db, vid, role: (None, None))

        await orchestrator.apply_action(db_session, version_id=version.id, action="zostav_plan")
        await orchestrator.run_conversation_turn(db_session, version.id)

        assert "specification.md" in captured["skeleton_prompt"]
        assert "Špecifikácie" in captured["skeleton_prompt"]


# ── MD-2: in-place replace (SAVEPOINT drop-and-recreate) ──────────────────────


class TestInPlaceReplace:
    async def test_second_plan_round_replaces_in_place(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        state = _seed_conversation(db_session, version.id)
        _approve_spec(db_session, version.id)

        # First compose: 1 epic / 1 feat / 1 task.
        _stub_plan_passes(monkeypatch, _plan())
        await orchestrator._run_conversation_plan_round(db_session, state)
        assert len(_epics(db_session, version.id)) == 1

        # Re-run the plan round (MD-2 rebuild) with a DIFFERENT plan shape → replaced in place, not appended.
        two_epic_plan = [
            {
                "title": "Alpha",
                "plain_description": "A",
                "feats": [
                    {
                        "title": "AlphaFeat",
                        "plain_description": "af",
                        "tasks": [{"title": "a-task", "task_type": "backend", "plain_description": "at"}],
                    }
                ],
            },
            {
                "title": "Beta",
                "plain_description": "B",
                "feats": [
                    {
                        "title": "BetaFeat",
                        "plain_description": "bf",
                        "tasks": [{"title": "b-task", "task_type": "backend", "plain_description": "bt"}],
                    }
                ],
            },
        ]
        _stub_plan_passes(monkeypatch, two_epic_plan)
        await orchestrator._run_conversation_plan_round(db_session, state)

        epics = _epics(db_session, version.id)
        assert [e.title for e in epics] == ["Alpha", "Beta"]  # replaced in place — the old "Foundation" is gone


# ── Návrh path stays byte-identical (stage=navrh) ─────────────────────────────


class TestNavrhUnchanged:
    async def test_navrh_fold_still_records_navrh_stage(self, db_session, monkeypatch):
        version, _ = _make_version(db_session)
        state = _seed_navrh_phase(db_session, version.id)
        _stub_plan_passes(monkeypatch, _plan())

        settled = await orchestrator._fold_task_plan_into_navrh(
            db_session, state, on_event=None, directive=None, on_message=None
        )
        assert settled is None  # success — the caller runs the shared dial-settle

        plan_records = [
            m
            for m in _msgs(db_session, version.id)
            if m.kind in ("gate_report", "notification") and m.author in ("ai_agent", "system")
        ]
        assert plan_records
        for m in plan_records:
            assert m.stage == "navrh", f"navrh path recorded stage {m.stage!r} — must stay navrh"
            if isinstance(m.payload, dict) and "phase" in m.payload:
                assert m.payload["phase"] == "navrh"
        gate = [m for m in plan_records if m.kind == "gate_report"][-1]
        assert gate.content.startswith("Návrh hotový")  # navrh register summary (message content) unchanged


# ── task-plan doc register (Auditor clause dropped for the conversation register) ──


class TestTaskPlanDocRegister:
    def test_render_drops_auditor_clause_for_priprava_register(self, db_session):
        version, project = _make_version(db_session)
        _materialize_one_task(db_session, project.id, version.id)
        v = db_session.get(Version, version.id)
        p = db_session.get(Project, project.id)

        navrh_md = orchestrator._render_task_plan_md(db_session, v, p, "navrh")
        assert "fázy Návrh" in navrh_md and "nezávislému Auditorovi" in navrh_md and "pred stavbou" in navrh_md

        conv_md = orchestrator._render_task_plan_md(db_session, v, p, "priprava")
        assert "nezávislému Auditorovi" not in conv_md and "pred stavbou" not in conv_md
        assert "Plán úloh" in conv_md  # still a real, titled plan doc
