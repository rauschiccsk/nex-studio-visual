"""Foundation domain models — users and user sessions."""

from sqlalchemy import (
    TIMESTAMP,
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID

from backend.db.models.base import Base, TimestampMixin, UUIDMixin


class User(Base, UUIDMixin, TimestampMixin):
    """ICC team member with role-based access (ri/ha/shu)."""

    __tablename__ = "users"

    username = Column(String(50), nullable=False)
    email = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(10), nullable=False)
    is_active = Column(Boolean, nullable=False, server_default="true")
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    # Telegram chat_id for agent notifications (CR-NS-011/012). When a user
    # owns a project, this id is written into that project's .env as
    # TELEGRAM_NOTIFY_CHAT_ID so agent reports reach them.
    telegram_chat_id = Column(String(64), nullable=True)
    # Force a change of an admin-set initial password before the user can use the app (v4.0.32).
    # Set true on admin create + admin reset; cleared when the user changes their OWN password.
    must_change_password = Column(Boolean, nullable=False, server_default="false")

    __table_args__ = (
        UniqueConstraint("username", name="uq_users_username"),
        UniqueConstraint("email", name="uq_users_email"),
        CheckConstraint(
            "role IN ('ri', 'ha', 'shu')",
            name="ck_users_role",
        ),
    )


class UserSession(Base, UUIDMixin, TimestampMixin):
    """JWT session with token rotation (token_version invalidates old JWTs)."""

    __tablename__ = "user_sessions"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_version = Column(Integer, nullable=False, server_default="0")
    last_seen_at = Column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class UserAgentSettings(Base, UUIDMixin, TimestampMixin):
    """Per-USER per-pipeline-role model/effort the cockpit applies at dispatch (CR-NS-040, E3(b/c)).

    ``agent_role`` is the PIPELINE agent role (ai_agent/auditor — same set as ``OrchestratorSession``;
    v2.0.0 CR-V2-001), NOT the user's ri/ha/shu access role. ``model``/``effort``
    are nullable ``str`` validated by pydantic enums at the API layer — deliberately NO DB CHECK on
    them, so the CLI's accepted model IDs / effort levels can evolve without a migration. Only
    ``agent_role`` (a stable set) keeps a DB CHECK. The project owner's row drives a build's dispatch;
    an absent row (or an unset field) falls back to today's no-flag behavior.
    """

    __tablename__ = "user_agent_settings"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_role = Column(String(16), nullable=False)
    model = Column(String(64), nullable=True)
    effort = Column(String(16), nullable=True)
    # CR-V2-038: the model the AI Agent spawns its ephemeral HELPERS on (Agent/Task tool). NULL → the
    # dispatch default (Haiku — cheap/fast bulk; the AI Agent does the hard core on its own model). Only
    # the ai_agent row is consulted (only the AI Agent spawns helpers). Validated by the pydantic enum.
    helper_model = Column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "agent_role", name="uq_user_agent_settings_user_role"),
        CheckConstraint(
            # v2.0.0 (CR-V2-001): 2 agent roles. A SECOND surviving 5-role CHECK (was migration 061) —
            # moves in lock-step with ck_orchestrator_session_role or 2-role dispatch is DB-rejected.
            "agent_role IN ('ai_agent', 'auditor')",
            name="ck_user_agent_settings_role",
        ),
    )
