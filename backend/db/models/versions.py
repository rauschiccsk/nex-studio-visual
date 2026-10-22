"""Version domain model — release version for a project.

A ``Version`` groups Epics and Bugs targeted at a specific release of a
project. Each project has many versions (e.g. ``1.0.0``, ``1.1.0``).
"""

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from backend.db.models.base import Base, TimestampMixin, UUIDMixin

#: DEV-170 — a version number is ``major.minor.patch``, nothing before or after it. The cockpit adds the ``v`` itself
#: where one belongs (``docs/specs/versions/v1.2.0``, the tag ``v1.2.0``); a stored ``v1.0.0`` became ``vv1.0.0``
#: there. The database, the API (:mod:`backend.schemas.version`) and the deploy log hold every number to this.
VERSION_NUMBER_PATTERN = r"^[0-9]+\.[0-9]+\.[0-9]+$"
#: What the Manažér reads when a number does not have that shape.
VERSION_NUMBER_RULE = "Číslo verzie musí byť v tvare 1.2.3."


class Version(Base, UUIDMixin, TimestampMixin):
    """Release version of a project — container for Epics and Bugs."""

    __tablename__ = "versions"

    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version_number = Column(String(50), nullable=False)
    name = Column(String(255), nullable=True)
    status = Column(String(20), nullable=False, server_default="planned")
    description = Column(Text, nullable=True)
    target_date = Column(Date, nullable=True)
    release_date = Column(Date, nullable=True)
    # v4.0.23: the git commit of the Manažér-approved Vizuál (frontend/) — the binding
    # contract. Programovanie preserves it (wire data, don't redesign) and the Auditor
    # verifies the delivered FE still matches it. NULL until Vizuál is approved.
    vizual_approved_sha = Column(String(40), nullable=True)
    # DEV-50: ``fix`` — a fix of an error in code we delivered (it does not do what the approved specification
    # says), never billed; ``change`` — new work or a change, billed. NULL = not decided: a new version counts as
    # ``change``, a fast fix must be decided by the Manažér before its delivered-token statement is issued.
    work_kind = Column(String(10), nullable=True)
    # DEV-122: the ticket in Plane's DEV register a fast fix was started from — it follows the version on its own
    # (started → In Progress, deployed to UAT → a comment, deployed to PROD → Done).
    plane_issue_id = Column(String(64), nullable=True)
    plane_identifier = Column(String(32), nullable=True)
    plane_url = Column(String(500), nullable=True)
    # DEV-74: the version's own token rates for its delivery statement (per 1 000 tokens) — code and tests,
    # documentation; NULL = the rate in Nastavenia. Every change is kept in ``version_rate_changes``.
    rate_code = Column(Numeric(12, 4), nullable=True)
    rate_docs = Column(Numeric(12, 4), nullable=True)
    #: DEV-75: the discount of each billing category for this version, in % — empty = the default in Nastavenia.
    discount_bug = Column(Numeric(5, 2), nullable=True)
    discount_request = Column(Numeric(5, 2), nullable=True)
    discount_gift = Column(Numeric(5, 2), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "version_number",
            name="uq_versions_project_id_version_number",
        ),
        CheckConstraint(
            "status IN ('planned', 'active', 'done', 'released')",
            name="ck_versions_status",
        ),
        CheckConstraint(f"version_number ~ '{VERSION_NUMBER_PATTERN}'", name="ck_versions_version_number"),
        CheckConstraint("work_kind IS NULL OR work_kind IN ('fix', 'change', 'gift')", name="ck_versions_work_kind"),
        # DEV-74: an own rate is a price — empty means Nastavenia, zero is no rate.
        CheckConstraint("rate_code > 0 AND rate_docs > 0", name="ck_versions_rates_positive"),
        CheckConstraint(
            "(discount_bug >= 0 AND discount_bug <= 100) AND (discount_request >= 0 AND discount_request <= 100)"
            " AND (discount_gift >= 0 AND discount_gift <= 100)",
            name="ck_versions_discounts_range",
        ),
    )

    # Relationship to the owning Project. The inverse side ``Project.versions``
    # is defined on the Project model.
    project = relationship("Project", back_populates="versions")

    # Inverse sides of Epic.version and Bug.version. The FK columns
    # (Epic.version_id, Bug.version_id) are nullable with ondelete='RESTRICT' —
    # a Version cannot be deleted while Epics or Bugs still reference it.
    # ``passive_deletes=True`` prevents SQLAlchemy from auto-nulling the FK
    # before the DELETE, letting the DB enforce the RESTRICT constraint.
    epics = relationship(
        "Epic",
        back_populates="version",
        passive_deletes=True,
    )
    bugs = relationship(
        "Bug",
        back_populates="version",
        passive_deletes=True,
    )
