"""CR-2 (v0.7.3) — Director-facing comms: the shared formatting brief + the prominent-rail marker.

Asserts the GENERATION-side contract (the root cause of monolithic prose, per CR-2) for ALL FIVE
Director-facing Coordinator prompt builders in :mod:`backend.services.orchestrator` —
``_coordinator_synthesis``, ``_coordinator_relay``, the ``verify_done`` judge,
``_coordinator_relay_engine_failure`` (engine-failure/HALT escalation) and ``_coordinator_review_gap``
(Gate-E gap recommendation) — each:

* appends the shared ``_DIRECTOR_FORMAT_BRIEF`` (headline-first markdown), and
* cites the status-block section by the unambiguous full-filename ref
  ``(F-007-orchestration-cockpit.md §5.3)`` — never the bare ``(§7.2)`` (a non-existent charter §7.2).

Prominent-rail markers (audit 2026-06-18 gating):
* ``_coordinator_synthesis`` keeps its own ``is_synthesis``.
* ``_coordinator_relay`` / ``_coordinator_relay_engine_failure`` / ``_coordinator_review_gap`` are
  Director-facing BY CONSTRUCTION → tagged ``is_director_brief`` on the invoke.
* ``verify_done`` is NOT tagged on the invoke (a gate_report PASS hands Director-facing to the synthesis;
  an auto-return retry re-dispatches the worker → ``agent_working``). It is tagged only by the caller's
  settle via ``_mark_latest_coordinator_brief`` — covered by its own unit test.

Only the leaf ``invoke_agent`` is faked, so the real ``invoke_agent_with_parse_retry`` wrapper builds +
forwards the prompt and we capture exactly what each builder emits.
"""

from __future__ import annotations

import uuid

import pytest

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator
from backend.services.pipeline_status import PipelineStatusBlock

# v2.0.0-dev: this whole module formats the Director(Manažér) brief from v1 gate-flow pipeline_state rows
# (gate_b/designer) the v2 CHECKs reject — it tests the v1 ENGINE's gate-report→brief rendering. The v2
# brief is re-derived from the 4-phase model in Milestone C/D. Kept as the SPEC of the brief C/D must
# re-build; deferred meanwhile.
pytestmark = pytest.mark.skip(reason="v1 engine behaviour — replaced by v2 in Milestone C/D")

STAGE = "gate_b"


def _mk_block(kind: str, *, summary: str = "ok", question: str | None = None) -> PipelineStatusBlock:
    """A status block with empty commits/deliverables so ``verify_mechanical`` is a no-op (PASS)."""
    return PipelineStatusBlock(stage=STAGE, kind=kind, summary=summary, awaiting="director", question=question)


def _seed_version(db) -> uuid.UUID:
    """Persist a minimal Project + Version + PipelineState (designer mid-stage) and return the version id."""
    creator = User(
        username=f"db_{uuid.uuid4().hex[:8]}",
        email=f"db_{uuid.uuid4().hex[:8]}@test.local",
        password_hash="x",
        role="ri",
    )
    db.add(creator)
    db.flush()
    project = Project(
        name="Director Brief Fixture",
        slug=f"director-brief-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="CR-2 director-brief test fixture.",
        created_by=creator.id,
    )
    db.add(project)
    db.flush()
    version = Version(project_id=project.id, version_number="v0.7.3", status="active")
    db.add(version)
    db.flush()
    state = PipelineState(
        version_id=version.id,
        flow_type="new_version",
        current_stage=STAGE,
        current_actor="designer",
        status="agent_working",
    )
    db.add(state)
    db.flush()
    return version.id


class _CaptureAgent:
    """Stand-in for ``orchestrator.invoke_agent`` — records each call's prompt + extra_payload and returns
    a fixed valid block so the wrapper never retries."""

    def __init__(self, result: PipelineStatusBlock) -> None:
        self._result = result
        self.calls: list[dict] = []

    async def __call__(self, db, **kw):  # noqa: ANN001 - mirrors invoke_agent's (db, **kwargs) shape
        self.calls.append({"prompt": kw.get("prompt"), "extra_payload": kw.get("extra_payload")})
        return self._result


def _assert_brief_and_ref(prompt: str) -> None:
    """Every Director-facing prompt carries the shared brief + the unambiguous full-filename status-block ref."""
    assert orchestrator._DIRECTOR_FORMAT_BRIEF in prompt, "the shared Director formatting brief must be appended"
    assert "(F-007-orchestration-cockpit.md §5.3)" in prompt, "status-block ref must use the full filename"
    assert "(§7.2)" not in prompt, "the stale bare (§7.2) charter-ambiguous ref must be gone"


@pytest.mark.asyncio
async def test_synthesis_prompt_has_brief_and_keeps_is_synthesis(db_session, monkeypatch) -> None:
    version_id = _seed_version(db_session)
    state = orchestrator._get_state(db_session, version_id)
    fake = _CaptureAgent(_mk_block("gate_report", summary="zhrnutie"))
    monkeypatch.setattr(orchestrator, "invoke_agent", fake)

    await orchestrator._coordinator_synthesis(db_session, state, trigger="fáza 'gate_b'")

    assert len(fake.calls) == 1
    _assert_brief_and_ref(fake.calls[0]["prompt"])
    # synthesis keeps its OWN marker (is_synthesis); it is NOT relabelled as a director_brief.
    assert fake.calls[0]["extra_payload"] == {"is_synthesis": True}


@pytest.mark.asyncio
async def test_relay_prompt_has_brief_and_director_brief_marker(db_session, monkeypatch) -> None:
    version_id = _seed_version(db_session)
    state = orchestrator._get_state(db_session, version_id)
    fake = _CaptureAgent(_mk_block("gate_report", summary="relay"))
    monkeypatch.setattr(orchestrator, "invoke_agent", fake)

    await orchestrator._coordinator_relay(db_session, state, _mk_block("question", question="ako ďalej?"))

    assert len(fake.calls) == 1
    _assert_brief_and_ref(fake.calls[0]["prompt"])
    assert fake.calls[0]["extra_payload"] == {"is_director_brief": True}


@pytest.mark.asyncio
async def test_relay_engine_failure_prompt_has_brief_and_director_brief_marker(db_session, monkeypatch) -> None:
    """New Director-facing prompt #1 (audit 2026-06-18): the engine-failure/HALT escalation."""
    version_id = _seed_version(db_session)
    fake = _CaptureAgent(_mk_block("blocked", summary="zlyhanie"))
    monkeypatch.setattr(orchestrator, "invoke_agent", fake)

    await orchestrator._coordinator_relay_engine_failure(db_session, version_id, STAGE, "plán sa nepodarilo zapísať")

    assert len(fake.calls) == 1
    _assert_brief_and_ref(fake.calls[0]["prompt"])
    assert fake.calls[0]["extra_payload"] == {"is_director_brief": True}


@pytest.mark.asyncio
async def test_review_gap_prompt_has_brief_and_director_brief_marker(db_session, monkeypatch) -> None:
    """New Director-facing prompt #2 (audit 2026-06-18): the Gate-E gap recommendation."""
    version_id = _seed_version(db_session)
    state = orchestrator._get_state(db_session, version_id)
    fake = _CaptureAgent(_mk_block("gate_report", summary="odporúčanie"))
    monkeypatch.setattr(orchestrator, "invoke_agent", fake)

    designer_block = PipelineStatusBlock(
        stage="gate_e", kind="answer", summary="medzera", awaiting="director", proposed_fix="pridať pole X"
    )
    await orchestrator._coordinator_review_gap(db_session, state, designer_block)

    assert len(fake.calls) == 1
    _assert_brief_and_ref(fake.calls[0]["prompt"])
    assert fake.calls[0]["extra_payload"] == {"is_director_brief": True}


@pytest.mark.asyncio
async def test_verify_prompt_has_brief_but_is_NOT_tagged_on_the_invoke(db_session, monkeypatch) -> None:
    """GATING (audit 2026-06-18): the verify judge carries the brief, but is NEVER tagged is_director_brief on
    the invoke — so a gate_report PASS and the auto-return-loop retries never get the prominent rail. The
    Director-facing tag is applied only by the caller's settle (see _mark_latest_coordinator_brief test)."""
    version_id = _seed_version(db_session)
    fake = _CaptureAgent(_mk_block("gate_report", summary="overené"))
    monkeypatch.setattr(orchestrator, "invoke_agent", fake)

    reason, directive, is_coord_error = await orchestrator.verify_done(db_session, version_id, _mk_block("gate_report"))

    assert reason is None and directive is None and is_coord_error is False, "a PASS verify (kind != blocked)"
    assert len(fake.calls) == 1
    _assert_brief_and_ref(fake.calls[0]["prompt"])
    assert not (fake.calls[0]["extra_payload"] or {}).get("is_director_brief"), "verify must NOT self-tag on the invoke"


def test_mark_latest_coordinator_brief_tags_only_the_terminal_turn(db_session) -> None:
    """The settle's tagger marks ONLY the latest Coordinator turn at the stage — an older Coordinator turn
    (e.g. an auto-return-loop intermediate verify) stays untagged, so the prominent rail lands on exactly the
    terminal verify the Director reads."""
    version_id = _seed_version(db_session)
    older = orchestrator._record_message(
        db_session,
        version_id=version_id,
        stage=STAGE,
        author="coordinator",
        recipient="director",
        kind="gate_report",
        content="staršie overenie",
    )
    # a blocked verify is recorded as kind="question" (invoke_agent maps block kind blocked→question)
    newer = orchestrator._record_message(
        db_session,
        version_id=version_id,
        stage=STAGE,
        author="coordinator",
        recipient="director",
        kind="question",
        content="terminálny blok",
    )
    # a worker turn at the same stage must never be tagged either
    worker = orchestrator._record_message(
        db_session,
        version_id=version_id,
        stage=STAGE,
        author="designer",
        recipient="director",
        kind="gate_report",
        content="práca návrhára",
    )
    db_session.flush()

    orchestrator._mark_latest_coordinator_brief(db_session, version_id, STAGE)
    for m in (older, newer, worker):
        db_session.refresh(m)

    assert (newer.payload or {}).get("is_director_brief") is True, "the terminal coordinator turn is tagged"
    assert not (older.payload or {}).get("is_director_brief"), "an older coordinator turn stays untagged"
    assert not (worker.payload or {}).get("is_director_brief"), "a worker turn is never tagged"
