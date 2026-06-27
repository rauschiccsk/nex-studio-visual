"""Orchestration pipeline models — F-007 Orchestration Cockpit (CR-NS-018).

Backend-owned single source of truth for the multi-agent pipeline. Two
tables:

* :class:`PipelineState` — one row per version (``version_id`` UNIQUE). A
  single ``SELECT`` answers "who is on turn and what's next" — the root
  problem F-007 solves. Created lazily by the orchestrator when a pipeline
  actually starts via the cockpit (never eager-seeded).
* :class:`PipelineMessage` — append-only typed message log. The v1 5-role
  file-bus is retired (v2.0.0 CR-V2-017): with only the AI Agent + Auditor
  there are no roles to bus between, so the engine records every turn here as
  a typed in-DB message — Manažér decisions, agent reports, and the Auditor's
  verdict — giving a queryable audit trail (alongside the PTY log + phase tabs).

Enums follow the codebase convention (``String`` + DB ``CHECK`` constraint,
not native PG ENUM). Phase 1 of F-007 §12.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

from backend.db.models.base import Base, TimestampMixin, UUIDMixin

# Canonical enum value tuples — the SINGLE SOURCE OF TRUTH for both the DB CHECK constraints
# (below) and the Pydantic ``Literal`` schemas (``backend/schemas/pipeline.py`` imports these),
# so the two can never drift (v0.7.0 R2, D2). String + CHECK rather than native PG enum follows
# the codebase convention (F-007 §3.1, §12). Declaration order is meaningful — it is preserved
# into the OpenAPI ``enum`` arrays and the generated FE union member order (R2-b).
# v2.0.0 (CR-V2-001): the v1 5-role serial waterfall (Designer/Customer/Implementer/Auditor/
# Coordinator across 11 stages) collapses to TWO agents — AI Agent (doer) + Auditor (independent
# verifier) — across 4 phases (priprava → navrh → programovanie → verifikacia) + done. flow_type
# drops 'cr'/'bug' (OQ-1): every change is a 'new_version' (full 4-phase) or a 'fast_fix' (short path).
FLOW_TYPE_VALUES = ("new_version", "fast_fix")
STAGE_VALUES = (
    "priprava",
    "navrh",
    "programovanie",
    "verifikacia",
    "done",
)
# Actors (PipelineState.current_actor) = the AGENTS on turn: AI Agent + Auditor. Participants
# (message author/recipient) additionally include the human operator (``manazer`` — renamed from
# ``director`` in CR-V2-004) and ``system`` (message-only). (F-007 §3.1, §4.2.)
ACTOR_VALUES = ("ai_agent", "auditor")
PARTICIPANT_VALUES = ACTOR_VALUES + ("manazer", "system")
STATUS_VALUES = ("agent_working", "awaiting_manazer", "blocked", "paused", "done")
# Why a ``blocked`` state happened (v0.7.0 R4, D1) — the authoritative, persisted reason the FE
# reads INSTEAD of the fragile ``lastMessage.author == "system"`` heuristic. ``agent_question`` = the
# worker asked something (Manažér answers); ``agent_error`` = the worker's turn failed
# (a build-task / phase deliverable fail); ``parse_exhaustion`` = the worker produced no parseable output
# after retries; ``system_error`` = an engine-side step failed (UAT deploy / release verify / task-plan
# write / gate mechanical). SET deterministically at each block site (orchestrator), CLEARED when the
# status leaves ``blocked`` (the set-listener below). NULL whenever the pipeline isn't blocked.
BLOCK_REASON_VALUES = ("agent_question", "agent_error", "system_error", "parse_exhaustion")
MESSAGE_KIND_VALUES = (
    "kickoff",
    "question",
    "answer",
    "gate_report",
    "directive",
    "approval",
    "return",
    "verdict",
    "notification",
)
MESSAGE_STATUS_VALUES = ("pending", "delivered", "answered", "archived")


def _sql_in_list(values: tuple[str, ...]) -> str:
    """Render a tuple of enum values as a SQL ``IN`` list fragment (``'a', 'b', 'c'``).

    Builds the CHECK-constraint body from the canonical tuples above so the DB constraint and the
    Pydantic ``Literal`` schema share one source. The output is byte-identical to the previously
    hand-written fragments — no schema change, no migration (v0.7.0 R2).
    """
    return ", ".join(f"'{v}'" for v in values)


class PipelineState(Base, UUIDMixin, TimestampMixin):
    """Current orchestration state for one version's pipeline (F-007 §4.1)."""

    __tablename__ = "pipeline_state"

    version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    flow_type = Column(String(16), nullable=False)
    current_stage = Column(String(16), nullable=False)
    current_actor = Column(String(16), nullable=False)
    # The longest status value ('agent_working' / 'awaiting_manazer') needs > 16. Kept String(20)
    # (CR-V2-004 renamed the value, not the width — 'awaiting_director' was 17, 'awaiting_manazer' is 16).
    status = Column(String(20), nullable=False)
    #: Human-readable "what happens next" sentence rendered on the board.
    next_action = Column(Text, nullable=False, server_default="")
    is_regate = Column(Boolean, nullable=False, server_default="false")
    iteration = Column(Integer, nullable=False, server_default="0")
    #: Transient return marker. v1-ORPHANED COLUMN (CR-V2-017 FLAG): its only writer was the retired
    #: Coordinator ``route_to_designer`` executor, excised with the v1 5-role engine. No v2 code reads or
    #: writes it; kept as a nullable column (dropping it needs a migration, deferred — Milestone E ships no
    #: migration). A later cleanup CR drops the column.
    returns_to = Column(String(20), nullable=True)
    #: WS-D (CR-NS-036): when the pipeline ENTERED its current Manažér-wait status
    #: (``awaiting_manazer`` / ``blocked``). Maintained by the ``status`` ``set`` event listener
    #: below — set on entry, preserved across wait→wait, cleared on leaving. Powers the future
    #: metrics page's Manažér-wait time (now − ``awaiting_director_since``). Nullable; NULL whenever
    #: the pipeline isn't waiting on the Manažér. (Column name kept as ``awaiting_director_since`` —
    #: CR-V2-004 renamed the operator label + status VALUE, not the live column, to avoid needless DDL churn.)
    awaiting_director_since = Column(TIMESTAMP(timezone=True), nullable=True)
    #: E5 (CR-NS-043): accumulated total Manažér-wait time for this version (seconds). The status
    #: listener folds each finished wait interval (now − awaiting_director_since) in here on EXIT, so
    #: the metrics page has the lifetime total; a live open wait is added on top at read time. Starts
    #: fresh — versions finished before this column show 0 (no backfill, documented). (Column name kept
    #: as ``total_director_wait_seconds`` — operator/status relabel only, no column rename DDL; CR-V2-004.)
    total_director_wait_seconds = Column(Float, nullable=False, server_default="0")
    #: R1 dispatch resilience (v0.7.0, D1/D2). Repo HEAD captured at the START of a dispatch
    #: (``_begin_dispatch``), FROZEN across that dispatch's parse-retries (Seam #4) and reset to NULL on
    #: settle (the ``status`` set listener below + the ``pipeline_runner._run`` backstop). On an agent
    #: envelope-loss (timeout/crash) the engine audits ``dispatch_baseline_sha..HEAD`` so committed-but-lost
    #: work is surfaced to the Director, never silently lost. Distinct from the per-task ``Task.baseline_sha``
    #: (verify anchor) — a turn-start snapshot, not a verify anchor (Seam #7). Nullable; NULL when idle.
    dispatch_baseline_sha = Column(String(40), nullable=True)
    #: R1 durable single-flight (D2, CR-NS-027 hardening): True while a dispatch is in flight for this
    #: version. Enforced at the DB level so it survives a backend restart, complementing — not replacing —
    #: the in-memory ``pipeline_runner._ACTIVE_DISPATCH`` guard. Set by ``_begin_dispatch``; cleared on every
    #: settle (the ``status`` listener below) + the ``_run`` backstop + startup orphan recovery.
    dispatch_in_flight = Column(Boolean, nullable=False, server_default="false")
    #: R4 operator legibility (v0.7.0, D1): WHY the pipeline is ``blocked`` — one of ``BLOCK_REASON_VALUES``
    #: (agent_question / agent_error / system_error / parse_exhaustion), SET deterministically at each block
    #: site in the orchestrator and CLEARED to NULL by the ``status`` set-listener the moment the state leaves
    #: ``blocked`` (never stale). Authoritative replacement for the FE ``isErrorBlock`` heuristic — the banner
    #: + action-bar derive question-vs-error from this, falling back to the heuristic only for NULL (legacy)
    #: rows. Nullable; NULL whenever ``status != 'blocked'``.
    block_reason = Column(String(20), nullable=True)
    #: Per-build Miera autonómie override (v2.0.0, CR-V2-008 / AUTON-6). The TOP layer of the
    #: dial resolution order (per-build → per-project → global): a non-NULL value here overrides
    #: both the per-project (``projects.miera_autonomie``) and the global default for THIS build;
    #: NULL (the default) inherits the per-project value, which itself inherits the global. One of
    #: the four presets (plna | len_na_konci | pri_klucovych_bodoch | po_kazdej_faze) — validated
    #: by the orchestrator resolver, not a DB CHECK (the dial value set lives in one place in code;
    #: an unrecognised stored value degrades to the next layer). Written on ``start`` by CR-V2-009.
    miera_autonomie = Column(String(32), nullable=True)

    __table_args__ = (
        UniqueConstraint("version_id", name="uq_pipeline_state_version_id"),
        CheckConstraint(
            # v2.0.0 (CR-V2-001, OQ-1): two flow_types — 'new_version' (full 4-phase) and 'fast_fix'
            # (the lighter short path). 'cr'/'bug' dropped — every change is a version or a fast-fix.
            f"flow_type IN ({_sql_in_list(FLOW_TYPE_VALUES)})",
            name="ck_pipeline_state_flow_type",
        ),
        CheckConstraint(
            f"current_stage IN ({_sql_in_list(STAGE_VALUES)})",
            name="ck_pipeline_state_current_stage",
        ),
        CheckConstraint(
            f"current_actor IN ({_sql_in_list(ACTOR_VALUES)})",
            name="ck_pipeline_state_current_actor",
        ),
        CheckConstraint(
            f"status IN ({_sql_in_list(STATUS_VALUES)})",
            name="ck_pipeline_state_status",
        ),
        CheckConstraint(
            # Nullable enum (R4, D1): NULL is valid (not blocked); a non-NULL value must be in the canonical
            # tuple. Single source via ``_sql_in_list`` (R2 pattern) so the DB CHECK + the Pydantic Literal
            # never drift. (``X IN (...)`` already passes for NULL — the explicit ``IS NULL OR`` documents intent.)
            f"block_reason IS NULL OR block_reason IN ({_sql_in_list(BLOCK_REASON_VALUES)})",
            name="ck_pipeline_state_block_reason",
        ),
    )


class PipelineMessage(Base, UUIDMixin):
    """Append-only typed message in a version's pipeline (F-007 §4.2).

    Append-only — no ``updated_at`` (carries ``created_at`` only).
    """

    __tablename__ = "pipeline_message"

    version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    stage = Column(String(16), nullable=False)
    author = Column(String(16), nullable=False)
    recipient = Column(String(16), nullable=False)
    kind = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    status = Column(String(16), nullable=False, server_default="pending")
    payload = Column(JSONB, nullable=True)
    created_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    # Monotonic insertion order (CR-NS-018). ``created_at`` uses ``func.now()``,
    # which is constant within a transaction, so same-transaction messages (a
    # worker's gate_report + the Coordinator's verify gate_report) tie on
    # ``created_at`` and order non-deterministically. ``seq`` disambiguates →
    # the board always shows the worker's report before its verification.
    seq = Column(BigInteger, Identity(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            f"stage IN ({_sql_in_list(STAGE_VALUES)})",
            name="ck_pipeline_message_stage",
        ),
        CheckConstraint(
            f"author IN ({_sql_in_list(PARTICIPANT_VALUES)})",
            name="ck_pipeline_message_author",
        ),
        CheckConstraint(
            f"recipient IN ({_sql_in_list(PARTICIPANT_VALUES)})",
            name="ck_pipeline_message_recipient",
        ),
        CheckConstraint(
            f"kind IN ({_sql_in_list(MESSAGE_KIND_VALUES)})",
            name="ck_pipeline_message_kind",
        ),
        CheckConstraint(
            f"status IN ({_sql_in_list(MESSAGE_STATUS_VALUES)})",
            name="ck_pipeline_message_status",
        ),
        Index("ix_pipeline_message_version_created", "version_id", "created_at"),
        Index("ix_pipeline_message_version_seq", "version_id", "seq"),
    )


#: Statuses in which the pipeline is waiting on a Manažér decision (WS-D, CR-NS-036). CR-V2-004
#: renamed the operator status VALUE ``awaiting_director`` → ``awaiting_manazer``; the frozenset
#: membership is updated in lock-step so the 3 status listeners below keep firing on the renamed
#: value (a value rename WITHOUT this update would silently stop them). R-LISTENERS re-wire COMPLETE
#: (CR-V2-009): the 4-phase engine (``orchestrator.run_dispatch`` / ``apply_action``) now WRITES the
#: renamed ``awaiting_manazer`` value, so all three sync, lock-free listeners — the Manažér-wait timer
#: (:func:`_stamp_awaiting_director_since`), the single-flight clear (:func:`_clear_dispatch_on_settle`)
#: and the block_reason clear (:func:`_clear_block_reason_on_unblock`) — fire on the new value. The
#: listener BODIES key only on this frozenset + the UNCHANGED literals (``agent_working`` / ``blocked``),
#: so no body edit was needed; the sync iteration / lock-free invariant is preserved (no async refactor).
_DIRECTOR_WAIT_STATUSES = frozenset({"awaiting_manazer", "blocked"})


@event.listens_for(PipelineState.status, "set")
def _stamp_awaiting_director_since(target, value, oldvalue, initiator):
    """Maintain :attr:`PipelineState.awaiting_director_since` on every ``status`` change (WS-D).

    * ENTER a Manažér-wait status from a non-wait status → stamp ``now``.
    * wait → wait (e.g. ``blocked`` → ``awaiting_manazer``) → keep the original clock (don't reset).
    * LEAVE to any non-wait status → clear (``None``).

    All status writes go through this ORM attribute (no bulk ``UPDATE`` bypasses it), so this is the
    single, caller-agnostic source of truth — no need to touch the ~18 transition sites individually.
    ``oldvalue`` may be SQLAlchemy's ``NO_VALUE`` sentinel for a never-loaded attribute; ``not in``
    then treats it as a non-wait prior, which yields the correct stamp-on-entry behaviour.
    """
    if value == oldvalue:
        return
    if value in _DIRECTOR_WAIT_STATUSES:
        if oldvalue not in _DIRECTOR_WAIT_STATUSES:
            target.awaiting_director_since = datetime.now(timezone.utc)
    else:
        # LEAVE a wait status → fold the finished interval into the accumulated total (E5, CR-NS-043),
        # then clear. Only when we were actually waiting (awaiting_director_since set) — a non-wait→
        # non-wait set or an initial stamp has it None and contributes nothing.
        if target.awaiting_director_since is not None:
            elapsed = (datetime.now(timezone.utc) - target.awaiting_director_since).total_seconds()
            target.total_director_wait_seconds = (target.total_director_wait_seconds or 0.0) + elapsed
        target.awaiting_director_since = None


@event.listens_for(PipelineState.status, "set")
def _clear_dispatch_on_settle(target, value, oldvalue, initiator):
    """Clear the durable single-flight flag + dispatch baseline the moment the pipeline SETTLES (R1, D2).

    A dispatch is in flight only while ``status == 'agent_working'``. Any settle (``awaiting_manazer`` /
    ``blocked`` / ``paused`` / ``done``) means the dispatch has ended → drop ``dispatch_in_flight`` and reset
    ``dispatch_baseline_sha`` to NULL. This is the DRY "settle paths" clear the design calls for: every status
    write goes through this ORM attribute, so the ~18 transition sites need no individual touch, and a fresh
    dispatch re-captures the baseline from a clean NULL. ``pipeline_runner._run`` keeps a backstop clear for a
    settle that never goes through an ORM status set. A re-entry that keeps ``agent_working`` (the fast_fix
    one-touch chain) is NOT a settle, so the flag + baseline survive the chain (Seam #4). The lost-work audit
    reads the baseline BEFORE the settling status write, so the value is captured before this clears it."""
    if value == oldvalue or value == "agent_working":
        return
    target.dispatch_in_flight = False
    target.dispatch_baseline_sha = None


@event.listens_for(PipelineState.status, "set")
def _clear_block_reason_on_unblock(target, value, oldvalue, initiator):
    """Clear :attr:`PipelineState.block_reason` the moment the status LEAVES ``blocked`` (R4, D1).

    ``block_reason`` is meaningful only while ``status == 'blocked'``. Every block site sets it explicitly
    alongside ``status = 'blocked'``; entering ``blocked`` (``value == 'blocked'``) is a no-op here so the
    site's set survives regardless of write order, and any transition AWAY from ``blocked`` (settle /
    re-dispatch / done) drops it to NULL so it can never be stale. All status writes go through this ORM
    attribute (no bulk UPDATE bypass), so the ~18 transition sites need no individual touch — mirrors
    :func:`_clear_dispatch_on_settle`. A ``blocked`` → ``blocked`` re-block (``value == oldvalue``) is a
    no-op too; that site overwrites ``block_reason`` with the fresh reason itself."""
    if value == oldvalue or value == "blocked":
        return
    target.block_reason = None
