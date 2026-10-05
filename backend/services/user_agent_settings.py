"""Per-user per-role agent model/effort config service (CR-NS-040, E3(b/c)).

Thin CRUD over ``user_agent_settings``. Each user reads + upserts only their OWN rows (the routes
scope every call to ``current_user``); the cockpit reads the project owner's rows at dispatch
(:func:`backend.services.orchestrator._resolve_dispatch_overrides`).
"""

from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.foundation import UserAgentSettings
from backend.db.models.pipeline import PipelineMessage
from backend.schemas.user_agent_setting import MODEL_FAMILIES, AgentModelOption


def list_for_user(db: Session, user_id: uuid.UUID) -> list[UserAgentSettings]:
    """Return the user's per-role config rows (only roles they have set)."""
    return list(
        db.execute(
            select(UserAgentSettings).where(UserAgentSettings.user_id == user_id).order_by(UserAgentSettings.agent_role)
        )
        .scalars()
        .all()
    )


def upsert(
    db: Session,
    *,
    user_id: uuid.UUID,
    agent_role: str,
    model: Optional[str],
    effort: Optional[str],
    helper_model: Optional[str] = None,
) -> UserAgentSettings:
    """Insert or update the ``(user_id, agent_role)`` row with ``model`` + ``effort`` (+ ``helper_model``).

    Caller commits. Validation of ``agent_role`` / ``model`` / ``effort`` / ``helper_model`` happens at the
    API layer (path Literal + pydantic enums); the DB CHECK on ``agent_role`` is the last line of defence.
    ``helper_model`` (CR-V2-038) is the model the AI Agent spawns its helpers on — only the ai_agent row
    consults it at dispatch; an auditor row's value is simply unused.
    """
    row = db.execute(
        select(UserAgentSettings).where(
            UserAgentSettings.user_id == user_id,
            UserAgentSettings.agent_role == agent_role,
        )
    ).scalar_one_or_none()
    if row is None:
        row = UserAgentSettings(
            user_id=user_id, agent_role=agent_role, model=model, effort=effort, helper_model=helper_model
        )
        db.add(row)
    else:
        row.model = model
        row.effort = effort
        row.helper_model = helper_model
    db.flush()
    db.refresh(row)
    return row


def model_options(db: Session) -> list[AgentModelOption]:
    """Every model family the Nastavenia offers, with the version that last REALLY ran on it (ICCINT-167).

    The cockpit dispatches the family and the CLI picks its newest version, so the only truthful "Opus
    means Opus 5.5 today" is the run record: ``payload.usage.model`` is filled from the CLI's own
    ``modelUsage`` (:func:`backend.services.claude_agent._usage_from`). Newest run per family,
    across all projects — the alias resolves the same for everyone on this host."""
    run_model = PipelineMessage.payload["usage"]["model"].astext
    options: list[AgentModelOption] = []
    for family in MODEL_FAMILIES:
        row = db.execute(
            select(run_model, PipelineMessage.created_at)
            .where(run_model.ilike(f"%{family}%"))
            .order_by(PipelineMessage.created_at.desc(), PipelineMessage.seq.desc())
            .limit(1)
        ).first()
        options.append(
            AgentModelOption(
                id=family,
                last_run_model=row[0] if row is not None else None,
                last_run_at=row[1] if row is not None else None,
            )
        )
    return options
