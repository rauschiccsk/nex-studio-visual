"""REST router for per-user per-role agent model/effort config (CR-NS-040, E3(b/c)).

* ``GET  /``              → the CALLER's config rows (only roles they have set).
* ``GET  /models``        → the model families on offer + the version that last really ran (ICCINT-167).
* ``PUT  /{agent_role}``  → upsert the caller's ``model`` + ``effort`` for one pipeline role.

Every call is scoped to ``current_user`` — a user can only read/edit their OWN config (there is no
user_id in the path, so editing another user's config is structurally impossible). Any authenticated
user may manage their own settings. The router is prefix-less; the mount prefix
(``/api/v1/user-agent-settings``) is applied in :mod:`backend.main`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.schemas.user_agent_setting import (
    AgentModelOption,
    PipelineAgentRole,
    UserAgentSettingRead,
    UserAgentSettingUpsert,
)
from backend.services import user_agent_settings as service

router = APIRouter(tags=["User Agent Settings"])


@router.get("", response_model=list[UserAgentSettingRead])
def list_my_agent_settings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[UserAgentSettingRead]:
    """Return the authenticated user's per-role model/effort config rows."""
    return service.list_for_user(db, current_user.id)


@router.get("/models", response_model=list[AgentModelOption])
def list_agent_models(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AgentModelOption]:
    """The model families a role can run on, strongest first, each with the version that last ran on it.

    No version is written in the cockpit: the family is dispatched and the CLI runs its newest version;
    the label the screen shows is what the run record says really ran."""
    return service.model_options(db)


@router.put("/{agent_role}", response_model=UserAgentSettingRead)
def upsert_my_agent_setting(
    agent_role: PipelineAgentRole,
    payload: UserAgentSettingUpsert,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserAgentSettingRead:
    """Upsert the authenticated user's ``model`` + ``effort`` for one pipeline role.

    ``agent_role`` is a path Literal → FastAPI 422s an unknown role; ``model``/``effort`` are
    pydantic-enum validated in the body. Scoped to ``current_user`` (cannot touch another's config).
    """
    row = service.upsert(
        db,
        user_id=current_user.id,
        agent_role=agent_role,
        model=payload.model,
        effort=payload.effort,
        helper_model=payload.helper_model,
    )
    db.commit()
    return row
