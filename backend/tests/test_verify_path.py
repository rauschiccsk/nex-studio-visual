"""v0.7.2 — Verify-path robustness (gate-loop root-fix) unit tests.

Covers the three behavioural contracts of the v0.7.2 CR against the real
``verify_done`` / ``_verify_with_retries`` in :mod:`backend.services.orchestrator`,
with only the leaf ``invoke_agent`` (the actual claude turn) faked — so the real
``invoke_agent_with_parse_retry`` wrapper, the auto-return loop and the
mechanical/judge classification all run for real:

* **R-A** — an unparseable Coordinator verify status block now triggers a parse-retry
  (``verify_done`` invokes through ``invoke_agent_with_parse_retry``), so a transient
  bad-JSON verify self-corrects on retry → the gate PASSes instead of failing.
* **R-B (escalate)** — a verify that stays unparseable after the retries escalates as a
  Coordinator SYSTEM error (``is_coordinator_error=True``) → ``_verify_with_retries``
  returns immediately WITHOUT auto-returning the Designer (no loop — the nex-asistent
  gate_b incident).
* **R-B (regression guard)** — a genuine Designer-report defect (a real Coordinator
  ``flagged`` block, mechanical) STILL auto-returns the Designer (the loop is unchanged).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from backend.db.models.foundation import User
from backend.db.models.pipeline import PipelineMessage, PipelineState
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import orchestrator
from backend.services.pipeline_status import ParseFailure, PipelineStatusBlock

# v2.0.0-dev: this whole module drives the v1 ENGINE verify path (Coordinator relay of a worker's
# parse-failure, the gate_b designer-report verify/auto-return) off v1 gate-flow pipeline_state rows the
# v2 CHECKs reject. The v2 verify path follows the Auditor / per-phase rebuild in Milestone C/D. Kept as
# the SPEC of the verify behaviour C/D must re-build; deferred meanwhile.
pytestmark = pytest.mark.skip(reason="v1 engine behaviour — replaced by v2 in Milestone C/D")

STAGE = "gate_b"


def _mk_block(kind: str, *, summary: str = "ok", question: str | None = None) -> PipelineStatusBlock:
    """A status block with empty commits/deliverables so ``verify_mechanical`` is a no-op (PASS)."""
    return PipelineStatusBlock(stage=STAGE, kind=kind, summary=summary, awaiting="director", question=question)


def _seed_version(db) -> uuid.UUID:
    """Persist a minimal Project + Version + PipelineState and return the version id."""
    creator = User(
        username=f"vp_{uuid.uuid4().hex[:8]}",
        email=f"vp_{uuid.uuid4().hex[:8]}@test.local",
        password_hash="x",
        role="ri",
    )
    db.add(creator)
    db.flush()
    project = Project(
        name="Verify Path Fixture",
        slug=f"verify-path-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="v0.7.2 verify-path test fixture.",
        created_by=creator.id,
    )
    db.add(project)
    db.flush()
    version = Version(project_id=project.id, version_number="v0.7.2", status="active")
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


class _FakeAgent:
    """Scripted stand-in for ``orchestrator.invoke_agent``.

    ``scripts`` maps an agent ``role`` to the sequence of results that role's successive
    invocations return; once a role's sequence is exhausted the LAST entry repeats (so an
    "always ParseFailure" coordinator is just ``[ParseFailure(...)]``). Records every call's
    role in ``calls`` so a test can assert WHO was (or was not) dispatched.
    """

    def __init__(self, scripts: dict[str, list[object]]) -> None:
        self._scripts = scripts
        self._idx: dict[str, int] = {}
        self.calls: list[str] = []

    async def __call__(self, db, **kw):  # noqa: ANN001 - mirrors invoke_agent's (db, **kwargs) shape
        role = kw["role"]
        self.calls.append(role)
        seq = self._scripts[role]
        i = self._idx.get(role, 0)
        self._idx[role] = i + 1
        return seq[min(i, len(seq) - 1)]


@pytest.mark.asyncio
async def test_ra_unparseable_verify_self_corrects_on_retry(db_session, monkeypatch) -> None:
    """R-A: a transient bad-JSON Coordinator verify is retried and self-corrects → PASS.

    Before R-A ``verify_done`` called bare ``invoke_agent`` once → the first ParseFailure was a hard
    verify FAIL. Now it runs through ``invoke_agent_with_parse_retry``, so the retry yields a clean
    PASS block and ``verify_done`` returns no failure reason.
    """
    version_id = _seed_version(db_session)
    fake = _FakeAgent({"coordinator": [ParseFailure("status block is not valid JSON"), _mk_block("gate_report")]})
    monkeypatch.setattr(orchestrator, "invoke_agent", fake)

    reason, directive, is_coord_error = await orchestrator.verify_done(db_session, version_id, _mk_block("gate_report"))

    assert reason is None, "the retried verify produced a valid PASS block → no failure"
    assert directive is None
    assert is_coord_error is False
    assert fake.calls.count("coordinator") == 2, "the wrapper retried the unparseable verify exactly once"


@pytest.mark.asyncio
async def test_rb_persistent_coordinator_error_escalates_without_returning_designer(db_session, monkeypatch) -> None:
    """R-B (escalate): a verify unparseable through all retries blocks as a system error, NO Designer loop."""
    version_id = _seed_version(db_session)
    state = orchestrator._get_state(db_session, version_id)
    fake = _FakeAgent({"coordinator": [ParseFailure("status block is not valid JSON")]})
    monkeypatch.setattr(orchestrator, "invoke_agent", fake)

    reason, is_scope = await orchestrator._verify_with_retries(db_session, state, _mk_block("gate_report"))

    assert reason is not None and reason.startswith("coordinator verify unparseable:")
    assert is_scope is False, "a Coordinator system error is NOT a scope escalation"
    # The Designer was never re-dispatched, and no auto-return turn was recorded — the loop was skipped.
    assert "designer" not in fake.calls, "R-B must NOT auto-return the Designer on a Coordinator system error"
    returns = (
        db_session.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version_id, PipelineMessage.kind == "return")
        )
        .scalars()
        .all()
    )
    assert returns == [], "no auto-return message should be recorded (no loop)"


@pytest.mark.asyncio
async def test_rb_genuine_designer_report_error_still_auto_returns(db_session, monkeypatch) -> None:
    """R-B (regression guard): a real Coordinator ``flagged`` (mechanical) block STILL auto-returns the Designer."""
    version_id = _seed_version(db_session)
    state = orchestrator._get_state(db_session, version_id)
    # verify #1 → blocked (mechanical, no directive → not scope); the Designer re-emits a clean report;
    # verify #2 → PASS. The loop must run (Designer dispatched + an auto-return turn recorded).
    fake = _FakeAgent(
        {
            "coordinator": [_mk_block("blocked", question="chýba citácia spec"), _mk_block("gate_report")],
            "designer": [_mk_block("gate_report", summary="opravené")],
        }
    )
    monkeypatch.setattr(orchestrator, "invoke_agent", fake)

    reason, is_scope = await orchestrator._verify_with_retries(db_session, state, _mk_block("gate_report"))

    assert reason is None, "the Designer's corrected report passed re-verify → loop converges to a PASS"
    assert is_scope is False
    assert "designer" in fake.calls, "a genuine Designer-report error MUST auto-return (re-dispatch) the Designer"
    returns = (
        db_session.execute(
            select(PipelineMessage).where(PipelineMessage.version_id == version_id, PipelineMessage.kind == "return")
        )
        .scalars()
        .all()
    )
    assert len(returns) >= 1, "the auto-return loop must record at least one return turn"
