"""DEV-367 — a version in Návrh or Vizuál goes back to Príprava („Vrátiť do Prípravy“).

NEX Finance 0.1.0 reached Vizuál and the whole design was wrong. A build that passed Príprava (the Špecifikácia
approved) and sits in Návrh or Vizuál had no way back: the only verbs reworked the phase it was in. Director
12.11.2026: „Súhlasím, vrátime sa do prípravy, pretože celý projekt je zlý.“

Pinned here:

* the action is offered on a settled Návrh / Vizuál of a regular version (also on a question, an error and the
  Decision Cards), never in Príprava or later, never on a fast fix, while the agent works, on a pause, on a NEX Studio
  bug, nor once the build has started — and the engine refuses it in each of those cases;
* after it the version is in Príprava waiting for the Manažér, its Špecifikácia is open again (the old approval
  stays on record), the task plan, the approved Vizuál commit and the waiting flags are gone, one marker says what
  happened, no agent turn starts and the preview is torn down (a failure there fails nothing);
* the project's git keeps the unsaved work in a commit of its own and reverts it in the next one, which also removes
  the Návrh, the database structure, the task plan and the Vizuál mock-up — they stay in git's history;
* everything that reads the whole version's record (the approval, the drafts, the consultations, the review notes)
  counts only what came after the return;
* the next round of Príprava tells the Staviteľ the version came back, with the Manažér's words and a requirement
  the Zásobník now takes again — once.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.api.routes import pipeline as pipeline_routes
from backend.db.models.backlog import BacklogItem
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.tasks import Epic, Feat, Task
from backend.db.models.versions import Version
from backend.services import backlog as backlog_service
from backend.services import orchestrator, pipeline_runner, vizual_sandbox
from backend.services.pipeline_status import PipelineStatusBlock
from tests.test_orchestrator_v2_priprava_dial import _make_version, _seed_state

ACTION = "vratit_do_pripravy"
NEXT_ACTION = (
    "Verzia je späť v Príprave — Návrh a Vizuál sú zahodené. Zaraď požiadavky zo Zásobníka a v Riadiacom centre "
    "napíš Staviteľovi, čo má prepracovať, a stlač „Uprav“."
)
MARKER = "Manažér vrátil verziu z fázy {phase} do Prípravy — Návrh, plán úloh a Vizuál sú zahodené."
NOTICE = (
    "VRÁTENÉ DO PRÍPRAVY: Manažér vrátil verziu z fázy {phase} do Prípravy. Návrh, plán úloh a Vizuál sú zahodené "
    "(ostali v histórii gitu); Špecifikácia nie je schválená. Prepracuj ju podľa toho, čo Manažér píše nižšie, a "
    "podľa požiadaviek zo Zásobníka; potom ju Manažér znova schváli."
)
NOTICE_HEAD = "VRÁTENÉ DO PRÍPRAVY"
TRACE_COMMIT = "chore(vizual): rozpracovaný Vizuál verzie {n} pred vrátením do Prípravy"
REMOVAL_COMMIT = "docs: Návrh a Vizuál verzie {n} vrátené do Prípravy — ostávajú v histórii gitu"
COMMENT = "Celý projekt je zlý — prehľad má ukázať pokladňu, banku aj faktúry odberateľov."
TITLE = "Pokladňa a banka v prehľade"
DESCRIPTION = "Prehľad ukáže stav pokladne aj bankových účtov."
LINE = f"REQ-7: {TITLE} — {DESCRIPTION}"


@pytest.fixture(autouse=True)
def teardowns(monkeypatch, tmp_path):
    """No live paths and no Docker: the projects root is the test's own, the preview teardown is recorded."""
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path / "projects")
    calls: list[tuple[str, dict]] = []

    def _teardown(slug, frontend_path=None, **kwargs):
        calls.append((slug, kwargs))

    monkeypatch.setattr(vizual_sandbox, "teardown", _teardown)
    return calls


@pytest.fixture
def prompts(monkeypatch):
    """The prompts the Staviteľ got; each answer is recorded as the real invoke records it (author ``ai_agent``)."""
    got: list[str] = []

    async def _fake(db, *, version_id, role, stage, prompt, **_kw):
        got.append(prompt)
        orchestrator._record_message(
            db,
            version_id=version_id,
            stage=stage,
            author=role,
            recipient="manazer",
            kind="question",
            content="Mám otázku k bodu 4.",
        )
        return PipelineStatusBlock(stage=stage, kind="question", summary="?", question="Bod 4?", awaiting="manazer")

    monkeypatch.setattr(orchestrator, "invoke_agent_with_parse_retry", _fake)
    return got


def _settled(db, *, stage, status="awaiting_manazer", block_reason=None, flow_type="new_version", source_path=None):
    version, project = _make_version(db, source_path=source_path)
    state = _seed_state(db, version.id, stage=stage, actor="ai_agent", flow_type=flow_type)
    state.status = status
    state.block_reason = block_reason
    db.flush()
    return version, project, state


def _offline_state(*, stage, status, block_reason=None, flow_type="new_version", resume=None) -> PipelineState:
    state = PipelineState(flow_type=flow_type, current_stage=stage, current_actor="ai_agent")
    state.status = status
    state.block_reason = block_reason
    state.pause_reason = None
    state.resume_after_framework_fix = resume == "framework_fix"
    state.resume_after_restart = resume == "restart"
    state.ci_pending_since = None
    return state


def _say(db, version_id, *, stage="navrh", author="ai_agent", recipient="manazer", kind="gate_report", payload=None):
    return orchestrator._record_message(
        db,
        version_id=version_id,
        stage=stage,
        author=author,
        recipient=recipient,
        kind=kind,
        content="záznam",
        payload=payload,
    )


def _mark_returned(db, version_id, from_stage="navrh"):
    """The marker exactly as the action writes it (asserted in ``test_one_marker_says_what_happened``)."""
    return orchestrator._record_message(
        db,
        version_id=version_id,
        stage="priprava",
        author="manazer",
        recipient="ai_agent",
        kind="return",
        content=MARKER.format(phase="Návrh" if from_stage == "navrh" else "Vizuál"),
        payload={"phase": "priprava", "back_to_priprava": True, "from_stage": from_stage},
    )


def _approve(db, version_id):
    return _say(
        db,
        version_id,
        stage="priprava",
        author="manazer",
        recipient="ai_agent",
        kind="approval",
        payload={"phase": "priprava", "approve_spec": True},
    )


def _plan(db, version, project, *, started=False):
    epic = Epic(project_id=project.id, version_id=version.id, number=1, title="Základ", status="planned")
    db.add(epic)
    db.flush()
    feat = Feat(epic_id=epic.id, number=1, title="Faktúry", status="todo")
    db.add(feat)
    db.flush()
    for i in (1, 2):
        db.add(Task(feat_id=feat.id, number=i, title=f"Úloha {i}", task_type="backend", status="todo"))
    db.flush()
    if started:
        db.execute(select(Task).where(Task.feat_id == feat.id, Task.number == 1)).scalar_one().status = "in_progress"
        db.flush()
    return epic


def _messages(db, version_id) -> list[PipelineMessage]:
    return list(
        db.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version_id).order_by(PipelineMessage.seq.asc())
        )
        .scalars()
        .all()
    )


async def _go_back(db, version_id):
    return await orchestrator.apply_action(db, version_id=version_id, action=ACTION, payload={})


# ── where it is offered ─────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("stage", ["navrh", "vizual"])
@pytest.mark.parametrize(
    ("status", "block_reason", "resume"),
    [
        ("awaiting_manazer", None, None),
        ("blocked", "decision_needed", None),
        ("blocked", "agent_question", None),
        ("blocked", "agent_error", None),
        ("blocked", "parse_exhaustion", None),
        ("blocked", "schema_approval", None),
    ],
)
def test_it_is_offered_on_a_settled_navrh_and_vizual(stage, status, block_reason, resume):
    state = _offline_state(stage=stage, status=status, block_reason=block_reason, resume=resume)

    assert ACTION in orchestrator.determine_available_actions(state)


@pytest.mark.parametrize(
    ("stage", "status", "block_reason", "flow_type", "resume"),
    [
        ("priprava", "awaiting_manazer", None, "new_version", None),
        ("priprava", "blocked", "agent_question", "new_version", None),
        ("programovanie", "awaiting_manazer", None, "new_version", None),
        ("programovanie", "blocked", "agent_error", "new_version", None),
        ("verifikacia", "awaiting_manazer", None, "new_version", None),
        ("navrh", "awaiting_manazer", None, "fast_fix", None),
        ("vizual", "blocked", "agent_question", "fast_fix", None),
        ("navrh", "agent_working", None, "new_version", None),
        ("vizual", "agent_working", None, "new_version", None),
        ("navrh", "paused", None, "new_version", None),
        ("vizual", "blocked", "framework_issue", "new_version", None),
        ("navrh", "awaiting_manazer", None, "new_version", "framework_fix"),
        # a phase a restart cut off offers exactly „Pokračovať“ and „Uprav“ (DEV-204)
        ("navrh", "awaiting_manazer", None, "new_version", "restart"),
        ("vizual", "awaiting_manazer", None, "new_version", "restart"),
    ],
)
def test_it_is_not_offered_elsewhere(stage, status, block_reason, flow_type, resume):
    state = _offline_state(stage=stage, status=status, block_reason=block_reason, flow_type=flow_type, resume=resume)

    assert ACTION not in orchestrator.determine_available_actions(state)


def test_a_conversation_build_is_not_offered_it_and_the_engine_refuses_it():
    """A conversation build's turns run outside the rounds that carry the notice — the Staviteľ would not learn that
    Návrh and Vizuál went away. No such build was ever started (60 builds on 12.11.2026, all by phases), and the
    screen starts none; the button stays where it works whole."""
    state = _offline_state(stage="vizual", status="awaiting_manazer")
    state.mode = "conversation"

    assert ACTION not in orchestrator.determine_available_actions(state)
    assert orchestrator._back_to_priprava_refusal(state) == orchestrator.BACK_TO_PRIPRAVA_CONVERSATION


def test_the_next_step_names_the_button_the_manazer_has():
    """The Manažér's own button to send the Staviteľ what to rework is „Uprav“ (ConversationComposer)."""
    assert orchestrator.BACK_TO_PRIPRAVA_NEXT_ACTION == (
        "Verzia je späť v Príprave — Návrh a Vizuál sú zahodené. Zaraď požiadavky zo Zásobníka a v Riadiacom centre "
        "napíš Staviteľovi, čo má prepracovať, a stlač „Uprav“."
    )


def test_the_board_drops_it_once_the_build_started(db_session):
    version, project, _ = _settled(db_session, stage="vizual")
    _plan(db_session, version, project)
    assert ACTION in pipeline_routes._board(db_session, version.id).available_actions

    started, started_project, _ = _settled(db_session, stage="vizual")
    _plan(db_session, started, started_project, started=True)
    assert ACTION not in pipeline_routes._board(db_session, started.id).available_actions


# ── where the engine refuses it ─────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("stage", "status", "block_reason", "flow_type"),
    [
        ("priprava", "awaiting_manazer", None, "new_version"),
        ("programovanie", "awaiting_manazer", None, "new_version"),
        ("verifikacia", "blocked", "agent_question", "new_version"),
        ("navrh", "awaiting_manazer", None, "fast_fix"),
        ("navrh", "agent_working", None, "new_version"),
        ("vizual", "paused", None, "new_version"),
        ("vizual", "blocked", "framework_issue", "new_version"),
    ],
)
async def test_the_engine_refuses_it_outside_a_settled_navrh_or_vizual(
    db_session, stage, status, block_reason, flow_type
):
    version, _, state = _settled(db_session, stage=stage, status=status, block_reason=block_reason, flow_type=flow_type)

    with pytest.raises(ValueError) as refused:
        await _go_back(db_session, version.id)

    assert "Unknown action" not in str(refused.value)
    assert state.current_stage == stage and state.status == status
    assert not [m for m in _messages(db_session, version.id) if (m.payload or {}).get("back_to_priprava")]


async def test_the_engine_refuses_it_after_dedo_released_a_build_and_once_the_build_started(db_session):
    released, _, state = _settled(db_session, stage="navrh")
    state.resume_after_framework_fix = True
    db_session.flush()
    with pytest.raises(ValueError) as refused:
        await _go_back(db_session, released.id)
    assert "Unknown action" not in str(refused.value)

    cut_off, _, cut_state = _settled(db_session, stage="vizual")
    cut_state.resume_after_restart = True
    db_session.flush()
    with pytest.raises(ValueError) as refused:
        await _go_back(db_session, cut_off.id)
    assert "Pokračovať" in str(refused.value) and cut_state.current_stage == "vizual"

    version, project, started_state = _settled(db_session, stage="vizual")
    _plan(db_session, version, project, started=True)
    with pytest.raises(ValueError) as refused:
        await _go_back(db_session, version.id)
    assert "Programovanie" in str(refused.value)
    assert started_state.current_stage == "vizual"


# ── what the action does ────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("stage", "status", "block_reason"),
    [("navrh", "awaiting_manazer", None), ("vizual", "blocked", "decision_needed")],
)
async def test_after_it_the_version_is_in_priprava_waiting_for_the_manager(db_session, stage, status, block_reason):
    version, _, state = _settled(db_session, stage=stage, status=status, block_reason=block_reason)
    state.pending_vizual_signoff = True
    state.retry_consultation = True
    version.vizual_approved_sha = "a" * 40
    db_session.flush()

    after = await _go_back(db_session, version.id)

    assert (after.current_stage, after.current_actor, after.status, after.block_reason) == (
        "priprava",
        "ai_agent",
        "awaiting_manazer",
        None,
    )
    assert after.next_action == NEXT_ACTION
    assert after.pending_vizual_signoff is False and after.retry_consultation is False
    assert db_session.get(Version, version.id).vizual_approved_sha is None


@pytest.mark.parametrize(("stage", "phase"), [("navrh", "Návrh"), ("vizual", "Vizuál")])
async def test_one_marker_says_what_happened(db_session, stage, phase):
    version, _, _ = _settled(db_session, stage=stage)
    before = len(_messages(db_session, version.id))

    await _go_back(db_session, version.id)

    new = _messages(db_session, version.id)[before:]
    assert len(new) == 1
    marker = new[0]
    assert (marker.kind, marker.stage, marker.author, marker.recipient) == ("return", "priprava", "manazer", "ai_agent")
    assert marker.payload == {"phase": "priprava", "back_to_priprava": True, "from_stage": stage}
    assert marker.content == MARKER.format(phase=phase)


async def test_the_specification_is_open_again_and_the_old_approval_stays_on_record(db_session):
    version, _, _ = _settled(db_session, stage="vizual")
    approval = _approve(db_session, version.id)
    assert orchestrator.spec_approved(db_session, version.id)

    await _go_back(db_session, version.id)

    assert orchestrator.spec_approved(db_session, version.id) is False
    assert db_session.get(PipelineMessage, approval.id) is not None
    board = pipeline_routes._board(db_session, version.id)
    assert board.spec_approved is False
    assert "approve_spec" in board.available_actions and ACTION not in board.available_actions


async def test_the_task_plan_is_gone_rows_and_file(db_session, tmp_path):
    checkout = tmp_path / "checkout"  # a checkout without git — the plan file goes all the same
    version, project, _ = _settled(db_session, stage="vizual", source_path=str(checkout))
    plan_file = checkout / f"docs/specs/versions/v{version.version_number}/spec/task-plan.md"
    plan_file.parent.mkdir(parents=True)
    plan_file.write_text("# Plán úloh\n", encoding="utf-8")
    epic = _plan(db_session, version, project)
    epic_id = epic.id

    await _go_back(db_session, version.id)
    db_session.expire_all()

    assert db_session.execute(select(Epic).where(Epic.version_id == version.id)).first() is None
    assert db_session.execute(select(Feat).where(Feat.epic_id == epic_id)).first() is None
    assert not orchestrator.navrh_plan_materialized(db_session, version.id)
    assert not plan_file.exists()


async def test_no_agent_turn_starts(db_session, monkeypatch):
    version, _, _ = _settled(db_session, stage="navrh")
    begun: list[object] = []
    scheduled: list[object] = []
    monkeypatch.setattr(orchestrator, "_begin_dispatch", lambda db, st: begun.append(st))
    monkeypatch.setattr(pipeline_runner, "schedule_dispatch", lambda vid, directive=None: scheduled.append(vid))

    state = await pipeline_routes._apply_and_publish(db_session, version.id, ACTION, None)

    assert begun == [] and scheduled == []
    assert state.status == "awaiting_manazer" and state.dispatch_in_flight is False


async def test_the_preview_is_torn_down_and_its_failure_fails_nothing(db_session, monkeypatch, teardowns):
    version, project, _ = _settled(db_session, stage="vizual")

    await _go_back(db_session, version.id)
    assert teardowns == [(project.slug, {})]

    other, _, _ = _settled(db_session, stage="vizual")

    def _broken(slug, frontend_path=None, **kwargs):
        raise RuntimeError("docker neodpovedá")

    monkeypatch.setattr(vizual_sandbox, "teardown", _broken)
    after = await _go_back(db_session, other.id)
    assert after.current_stage == "priprava"


# ── the project's git ───────────────────────────────────────────────────────────────────────────────────────────────


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)
    return done.stdout


def _docs(root: Path, number: str) -> Path:
    return root / "docs" / "specs" / "versions" / f"v{number}"


def _repo(tmp_path: Path, number: str, *, with_design: bool = True) -> Path:
    root = tmp_path / "checkout"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "commit.gpgsign", "false")
    docs = _docs(root, number)
    (docs / "spec").mkdir(parents=True)
    (docs / "specification.md").write_text("# Špecifikácia\n", encoding="utf-8")
    if with_design:
        (docs / "design.md").write_text("# Návrh\n", encoding="utf-8")
        (docs / "DATABASE_SCHEMAS.md").write_text("# Štruktúra databázy\n", encoding="utf-8")
        (docs / "spec" / "task-plan.md").write_text("# Plán úloh\n", encoding="utf-8")
        (docs / "visual").mkdir()
        (docs / "visual" / "index.html").write_text("<html></html>\n", encoding="utf-8")
    (root / "frontend" / "src").mkdir(parents=True)
    (root / "frontend" / "src" / "App.tsx").write_text("export const App = 1;\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "založenie")
    return root


def _subjects(root: Path) -> list[str]:
    return _git(root, "log", "--format=%s").splitlines()


async def test_unsaved_work_is_kept_in_one_commit_and_reverted_in_the_next(db_session, tmp_path):
    version, project, _ = _settled(db_session, stage="vizual")
    n = version.version_number
    root = _repo(tmp_path, n)
    project.source_path = str(root)
    db_session.flush()
    app = root / "frontend" / "src" / "App.tsx"
    app.write_text("export const App = 2; // drawn in the Vizuál\n", encoding="utf-8")
    drawn = root / "frontend" / "src" / "Prehlad.tsx"
    drawn.write_text("export const Prehlad = 1;\n", encoding="utf-8")

    await _go_back(db_session, version.id)

    assert _subjects(root)[:3] == [REMOVAL_COMMIT.format(n=n), TRACE_COMMIT.format(n=n), "založenie"]
    trace = _git(root, "show", "--name-status", "--format=", "HEAD~1").split("\n")
    assert "M\tfrontend/src/App.tsx" in trace and "A\tfrontend/src/Prehlad.tsx" in trace
    assert "App = 2" in _git(root, "show", "HEAD~1:frontend/src/App.tsx")
    removal = set(_git(root, "show", "--name-status", "--format=", "HEAD").split("\n")) - {""}
    docs = f"docs/specs/versions/v{n}"
    assert removal == {
        "M\tfrontend/src/App.tsx",
        "D\tfrontend/src/Prehlad.tsx",
        f"D\t{docs}/design.md",
        f"D\t{docs}/DATABASE_SCHEMAS.md",
        f"D\t{docs}/spec/task-plan.md",
        f"D\t{docs}/visual/index.html",
    }
    assert app.read_text(encoding="utf-8") == "export const App = 1;\n" and not drawn.exists()
    assert not (_docs(root, n) / "design.md").exists() and not (_docs(root, n) / "visual").exists()
    assert (_docs(root, n) / "specification.md").exists()
    assert _git(root, "status", "--porcelain") == ""


async def test_a_clean_tree_gets_only_the_removals(db_session, tmp_path):
    version, project, _ = _settled(db_session, stage="navrh")
    n = version.version_number
    root = _repo(tmp_path, n)
    project.source_path = str(root)
    db_session.flush()

    await _go_back(db_session, version.id)

    assert _subjects(root)[:2] == [REMOVAL_COMMIT.format(n=n), "založenie"]
    assert not (_docs(root, n) / "DATABASE_SCHEMAS.md").exists()
    assert (root / "frontend" / "src" / "App.tsx").read_text(encoding="utf-8") == "export const App = 1;\n"
    assert _git(root, "status", "--porcelain") == ""


async def test_nothing_to_remove_and_nothing_unsaved_is_no_commit(db_session, tmp_path):
    version, project, _ = _settled(db_session, stage="navrh")
    root = _repo(tmp_path, version.version_number, with_design=False)
    project.source_path = str(root)
    db_session.flush()

    after = await _go_back(db_session, version.id)

    assert _subjects(root) == ["založenie"]
    assert after.current_stage == "priprava"


# ── the record counts from the return on ───────────────────────────────────────────────────────────────────────────


def test_spec_approved_counts_only_an_approval_after_the_return(db_session):
    version, _, _ = _settled(db_session, stage="priprava")
    _approve(db_session, version.id)
    _mark_returned(db_session, version.id)
    assert orchestrator.spec_approved(db_session, version.id) is False

    _approve(db_session, version.id)
    assert orchestrator.spec_approved(db_session, version.id) is True


def test_a_vizual_draft_before_the_return_is_not_one(db_session):
    version, _, _ = _settled(db_session, stage="priprava")
    _say(db_session, version.id, stage="vizual", payload={"phase": "vizual"})
    _say(
        db_session,
        version.id,
        stage="vizual",
        author="system",
        kind="notification",
        payload={"vizual_url": "https://stary-nahlad", "vizual_checklist": {"round": "1", "items": []}},
    )
    assert orchestrator._vizual_draft_attempted(db_session, version.id)
    assert orchestrator._vizual_url_recorded(db_session, version.id)

    _mark_returned(db_session, version.id, from_stage="vizual")

    assert orchestrator._vizual_draft_attempted(db_session, version.id) is False
    assert orchestrator._vizual_url_recorded(db_session, version.id) is False
    assert orchestrator.latest_vizual_url(db_session, version.id) is None
    assert orchestrator.vizual_checklists(db_session, version.id) == []


def _consultation(db, version_id, *, source="auditor_upfront", key="d1"):
    decision = {"key": key, "question": "Košík, alebo jeden klik?", "options": [{"id": "a", "label": "Košík"}]}
    return _say(
        db,
        version_id,
        kind="consultation",
        payload={"phase": "navrh", "consultation": {"id": key, "source": source, "decisions": [decision]}},
    )


def test_consultations_before_the_return_do_not_count(db_session):
    version, _, _ = _settled(db_session, stage="priprava")
    _consultation(db_session, version.id)
    _consultation(db_session, version.id, key="d2")
    _say(
        db_session,
        version.id,
        author="system",
        kind="notification",
        payload={"consult_retry": True, "consult_source": "auditor_upfront", "auditor_findings": ["Chýba banka."]},
    )
    assert orchestrator._consult_rounds_used(db_session, version.id, "auditor_upfront") == 2
    assert orchestrator.consultation_retry_pending(db_session, version.id) is not None
    assert orchestrator._latest_consultation(db_session, version.id) is not None
    assert orchestrator.decision_card_context(db_session, version.id) != {}

    _mark_returned(db_session, version.id)

    assert orchestrator._consult_rounds_used(db_session, version.id, "auditor_upfront") == 0
    assert orchestrator.consultation_retry_pending(db_session, version.id) is None
    assert orchestrator._latest_consultation(db_session, version.id) is None
    assert orchestrator.decision_card_context(db_session, version.id) == {}

    _consultation(db_session, version.id, key="d3")
    assert orchestrator._consult_rounds_used(db_session, version.id, "auditor_upfront") == 1


def test_the_auditors_notes_before_the_return_are_not_handed_over(db_session):
    version, _, _ = _settled(db_session, stage="priprava")
    _say(
        db_session,
        version.id,
        author="auditor",
        kind="verdict",
        payload={"upfront_review": True, "verdict": "PASS", "findings": ["Prehľad nemá banku."]},
    )
    assert orchestrator._unhandled_review_notes(db_session, version.id) == ["Prehľad nemá banku."]
    assert orchestrator._prior_auditor_review_context(db_session, version.id) is not None

    _mark_returned(db_session, version.id)

    assert orchestrator._unhandled_review_notes(db_session, version.id) == []
    assert orchestrator._prior_auditor_review_context(db_session, version.id) is None


# ── the Staviteľ hears it ───────────────────────────────────────────────────────────────────────────────────────────


def _requirement(db, project) -> BacklogItem:
    item = BacklogItem(project_id=project.id, number=7, title=TITLE, description=DESCRIPTION)
    db.add(item)
    db.flush()
    return item


async def _round(db, version_id, action, payload):
    """The Manažér's action and the round of Príprava the runner starts with its directive — the prompt it built."""
    state = await orchestrator.apply_action(db, version_id=version_id, action=action, payload=payload)
    assert state.status == "agent_working"
    directive = orchestrator.dispatch_directive(db, version_id, action, payload, state.current_stage)
    await orchestrator.run_dispatch(db, version_id, directive=directive)
    return directive


async def test_after_the_return_the_zasobnik_takes_the_version_and_the_next_round_carries_all_of_it(
    db_session, prompts
):
    version, project, _ = _settled(db_session, stage="navrh")
    _approve(db_session, version.id)
    _say(db_session, version.id, stage="navrh")  # the Staviteľ's Návrh
    item = _requirement(db_session, project)
    with pytest.raises(ValueError):  # past Príprava the Zásobník does not take it (DEV-361)
        backlog_service.assign_to_version(db_session, item.id, version.id)

    await _go_back(db_session, version.id)
    backlog_service.assign_to_version(db_session, item.id, version.id)
    db_session.flush()
    directive = await _round(db_session, version.id, "uprav", {"comment": COMMENT})

    prompt = prompts[-1]
    notice = NOTICE.format(phase="Návrh")
    assert prompt.startswith(notice)
    assert COMMENT in directive and LINE in prompt
    assert prompt.index(notice) < prompt.index(directive) < prompt.index(LINE)
    assert item.delivered_at is not None

    await _round(db_session, version.id, "answer", {"text": "Bod 4 — áno."})
    assert NOTICE_HEAD not in prompts[-1]


async def test_the_notice_names_the_phase_it_came_back_from_and_rides_once(db_session, prompts):
    version, _, state = _settled(db_session, stage="priprava")
    _say(db_session, version.id, stage="vizual")
    _mark_returned(db_session, version.id, from_stage="vizual")

    await _round(db_session, version.id, "ask", {"text": "Prečo je prehľad prázdny?"})
    assert prompts[-1].startswith(NOTICE.format(phase="Vizuál") + "\n\n")

    await _round(db_session, version.id, "answer", {"text": "Ukáž stav pokladne."})
    assert NOTICE_HEAD not in prompts[-1]


async def test_a_round_without_a_directive_gets_the_notice_too(db_session, prompts):
    version, _, state = _settled(db_session, stage="priprava")
    _say(db_session, version.id, stage="navrh")
    _mark_returned(db_session, version.id)
    state.status = "agent_working"
    db_session.flush()

    await orchestrator.run_dispatch(db_session, version.id)

    assert prompts[-1].startswith(NOTICE.format(phase="Návrh") + "\n\n")
    assert "Načítaj zadanie a začni prípravu špecifikácie" in prompts[-1]


async def test_a_version_that_never_came_back_hears_nothing_of_it(db_session, prompts):
    version, _, state = _settled(db_session, stage="priprava")
    state.status = "agent_working"
    db_session.flush()

    await orchestrator.run_dispatch(db_session, version.id)
    await _round(db_session, version.id, "answer", {"text": "Áno."})

    assert all(NOTICE_HEAD not in p for p in prompts)
