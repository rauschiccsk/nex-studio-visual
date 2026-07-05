"""Project domain models — projects."""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from backend.db.models.base import Base, TimestampMixin, UUIDMixin


class Project(Base, UUIDMixin, TimestampMixin):
    """Project managed in NEX Studio."""

    __tablename__ = "projects"

    name = Column(String(255), nullable=False)
    slug = Column(String(100), nullable=False)
    # Project archetype (CR-V2-005, replaces v1 ``category``). A ``type`` is a
    # preset SURFACE COMPOSITION / scaffold template (design §4.2):
    #   * ``standard`` → backend + a single app-frontend surface.
    #   * ``web``      → backend + an admin-frontend surface + a public-site
    #     surface (a managed/monitored site). The Web eshop/commerce add-on is
    #     DEFERRED (§7 Open #11) — no commerce code is scaffolded in v2.0.0.
    # Mobil is a deferred future archetype (§8 Open #1) — the enum ships only
    # ``standard``/``web``.
    type = Column(String(20), nullable=False)
    # Mandatory authentication mode (CR-V2-005). Picks the login flavour the
    # scaffolder wires onto the backend + each frontend surface:
    #   * ``password`` → username+password login (like NEX Studio).
    #   * ``token``    → token-launch (like NEX Inbox).
    auth_mode = Column(String(20), nullable=False)
    description = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, server_default="active")
    backend_port = Column(Integer, nullable=True)
    frontend_port = Column(Integer, nullable=True)
    db_port = Column(Integer, nullable=True)
    repo_url = Column(String(255), nullable=True)
    source_path = Column(Text, nullable=True)
    kb_path = Column(Text, nullable=True)
    # UAT deploy mapping (F-009, CR-NS-098). Maps this project to its
    # ``/opt/uat/<uat_slug>`` deploy (e.g. ``nex-ledger`` → ``"ledger"``,
    # ``nex-inbox`` → ``"mager"``) so the Fast-Fix Lane can auto-redeploy UAT
    # via ``scripts/uat-deploy.py <uat_slug> --project <slug>``. NULL = no UAT
    # configured → the fast-fix auto-deploy is skipped gracefully.
    uat_slug = Column(String(100), nullable=True)
    # Per-project Miera autonómie override (v2.0.0, CR-V2-008 / AUTON-6). The MIDDLE layer of
    # the dial resolution order (per-build → per-project → global): a non-NULL value here
    # overrides the global ``DEFAULT_SETTINGS['miera_autonomie']`` for every build of this
    # project; NULL (the default) inherits the global. One of the four presets
    # (plna | len_na_konci | pri_klucovych_bodoch | po_kazdej_faze) — validated by the
    # orchestrator resolver, not a DB CHECK (the value set evolves with the dial, kept in
    # one place in code; an unrecognised stored value degrades to the global default).
    miera_autonomie = Column(String(32), nullable=True)
    guardian_enabled = Column(Boolean, nullable=False, server_default="false")
    # STEP 6 (step6-hotovo-design.md, R9): "Vývoj na zákazku" — the ONLY switch that later permits a
    # project to deviate from the unified default design (firemné zásady §4). Set ONCE at creation (like
    # ``type`` / ``auth_mode`` — excluded from ProjectUpdate); an INERT stored datum in STEP 6 (no behaviour
    # binds to it yet — the deviation gate is a future scope). Clones the ``guardian_enabled`` Column shape.
    custom_development_enabled = Column(Boolean, nullable=False, server_default="false")
    created_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Notification owner (CR-NS-012). Receives agent Telegram notifications
    # for this project via their User.telegram_chat_id. Optional — defaults
    # to the creator at create time; ON DELETE SET NULL so removing the user
    # leaves the project intact (just unowned for notifications).
    owner_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    __table_args__ = (
        UniqueConstraint("name", name="uq_projects_name"),
        UniqueConstraint("slug", name="uq_projects_slug"),
        CheckConstraint(
            "type IN ('standard', 'web')",
            name="ck_projects_type",
        ),
        CheckConstraint(
            "auth_mode IN ('password', 'token')",
            name="ck_projects_auth_mode",
        ),
        CheckConstraint(
            "status IN ('active', 'archived', 'paused')",
            name="ck_projects_status",
        ),
        # Per-project port uniqueness — no two port columns on the same
        # row may share a non-NULL value. Matches migration 030.
        CheckConstraint(
            """
                    (backend_port IS NULL OR frontend_port IS NULL OR backend_port <> frontend_port)
                AND (backend_port IS NULL OR db_port IS NULL OR backend_port <> db_port)
                AND (frontend_port IS NULL OR db_port IS NULL OR frontend_port <> db_port)
            """,
            name="ck_projects_ports_distinct",
        ),
    )

    # Inverse side of Version.project (defined in backend/db/models/versions.py).
    # Deleting a Project cascades to its Versions via the FK ondelete='CASCADE'.
    versions = relationship(
        "Version",
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
