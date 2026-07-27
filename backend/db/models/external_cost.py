"""External cost domain model — hand-entered token spend the cockpit cannot meter (CR-V2-063).

The cockpit meters only its OWN builds. Work done outside a build (Dedo in the terminal, a developer
working directly) contributes zero to a project's cost today — hence a manual entry. Standalone +
project-scoped, deliberately OUTSIDE the pipeline capture: nothing in the engine writes here, only the
Manažér through ``/projects/{slug}/external-costs``.
"""

from sqlalchemy import CheckConstraint, Column, Date, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from backend.db.models.base import Base, TimestampMixin, UUIDMixin


class ExternalCost(Base, UUIDMixin, TimestampMixin):
    """Manually entered token spend the cockpit cannot meter (work done outside a build:
    Dedo in the terminal, a developer working directly). Priced with the SAME per-model prices
    and the SAME human coefficient as measured work, but always reported as a separate row and a
    separate ``…_external`` total — entered figures are never merged into measured ones."""

    __tablename__ = "external_cost"

    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # NULL = a project-level entry (counts in the project total, in no version). The version belonging
    # to the project is NOT a DB constraint — it is validated in the router (422).
    # ``SET NULL`` on delete, deliberately NOT ``CASCADE``: the money was really spent, so deleting the
    # version must never erase it from the project total — the entry survives and degrades to the
    # project scope, which is precisely what NULL already means here.
    version_id = Column(
        UUID(as_uuid=True),
        ForeignKey("versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    occurred_on = Column(Date, nullable=False)
    description = Column(String(500), nullable=False)
    # Full model id (e.g. "claude-opus-5") — priced through the SAME ``_model_family`` chain as metered
    # spend, so an entered Opus turn costs exactly what a metered Opus turn costs.
    model = Column(String(100), nullable=False)
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    created_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0",
            name="ck_external_cost_tokens_nonneg",
        ),
    )
