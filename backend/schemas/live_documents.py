"""Pydantic schemas for live document service data-transfer objects.

Two immutable DTOs drive the generators in
:mod:`backend.services.live_documents`:

* :class:`TaskCompletionData` — everything produced by a completed task
  run that the ``HISTORY.md`` / ``ARCHITECT.md`` entries need (status,
  duration, agent, commits, findings, attempt count, timestamp).
* :class:`FeatCompletionData` — the rolled-up feat-level outcome that
  closes out a phase in ``HISTORY.md`` (task count, duration, audit
  verdict, CI verdict).

Both models are ``frozen=True`` — a completion datum is a record of a
moment in time and must not mutate after being captured.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

#: Terminal state of a single task completion run.
TaskStatus = Literal["done", "failed"]

#: Phase-level audit and CI verdicts. ``"na"`` marks cases where the
#: verdict did not apply (e.g. no CI configured for the project yet —
#: legitimate for NEX Studio until a remote repo exists, see
#: ``CLAUDE.md §2.4``).
PhaseResult = Literal["pass", "fail", "na"]

#: Module lifecycle event types emitted to ``HISTORY.md``.
ModuleEventType = Literal["created", "status_changed", "deleted"]

#: Module lifecycle statuses — mirrors the DB CHECK constraint on
#: ``project_modules.status``.
ModuleStatus = Literal["planned", "in_design", "in_development", "done"]


class TaskCompletionData(BaseModel):
    """Immutable record of a completed task run.

    Consumed by :meth:`~backend.services.live_documents.LiveDocumentService.generate_history_entry`
    and :meth:`~backend.services.live_documents.LiveDocumentService.generate_architect_entry`.

    Field notes:

    * ``commit_hashes`` is a list to preserve multi-commit delegations
      from the NEX Command source. NEX Studio's ``execution_logs`` row
      currently stores at most one hash, so the caller passes a
      singleton list (or empty list on pure-review / audit runs).
    * ``changed_files`` captures arch-relevant files for
      ``ARCHITECT.md``. NEX Studio does not yet track changed files at
      the DB level, so this list will typically be empty in production
      — generators skip the ``Files:`` line when it is empty rather
      than failing.
    * ``timestamp`` defaults to "now" but is explicit in tests for
      deterministic output.
    """

    model_config = ConfigDict(frozen=True)

    feat_number: int = Field(..., ge=0)
    task_number: int = Field(..., ge=0)
    task_title: str
    status: TaskStatus
    duration_seconds: float = Field(..., ge=0.0)
    agent: str
    commit_hashes: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    code_review_passed: bool = True
    code_review_findings: list[str] = Field(default_factory=list)
    audit_passed: bool = True
    audit_findings: list[str] = Field(default_factory=list)
    auto_fix_attempts: int = Field(default=0, ge=0)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ModuleEventData(BaseModel):
    """Immutable record of a lifecycle event on a project module.

    Emitted from the project-modules router after create / status
    change / delete; consumed by
    :meth:`LiveDocumentService.generate_module_event_entry` to emit
    a line into ``HISTORY.md``. Only events that are meaningful for
    audit reviewers land here — pure metadata edits (renames,
    category tweaks) are skipped so HISTORY does not fill with
    noise.
    """

    model_config = ConfigDict(frozen=True)

    event_type: ModuleEventType
    module_code: str
    module_name: str
    category: str
    old_status: Optional[ModuleStatus] = None
    new_status: Optional[ModuleStatus] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class FeatCompletionData(BaseModel):
    """Immutable record of a completed feat / phase.

    Consumed by
    :meth:`~backend.services.live_documents.LiveDocumentService.generate_phase_summary_entry`
    to emit the divider-separated phase closing entry in
    ``HISTORY.md``.
    """

    model_config = ConfigDict(frozen=True)

    feat_number: int = Field(..., ge=0)
    feat_title: str
    total_tasks: int = Field(..., ge=0)
    duration_seconds: float = Field(..., ge=0.0)
    audit_result: PhaseResult = "na"
    ci_result: PhaseResult = "na"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
