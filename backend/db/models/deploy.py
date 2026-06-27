"""Deploy domain model — the per-customer deploy & UAT-acceptance audit-log (v2.0.0, CR-V2-026).

Design source: ``docs/architecture/nex-studio-v2-design.md`` §3 (Deploy &
Customers) — §3.4 (deploy flow), §3.5 (UAT acceptance gate), §3.6 (versioning),
§3.7 (fresh-first-then-data-preserving) — and the build plan CR-V2-026
(DEPLOY-6/8/9/10, AUTON-4).

A ``deploy_events`` row is the **append-only audit trail** of every deploy and
every acceptance, per customer (design §3.5/§5.3 "who / when / version /
customer"). It is the load-bearing record behind two never-bypassed invariants:

* **The UAT acceptance gate (§3.5).** PROD is opened for a (version, customer)
  ONLY when an ``accept`` event exists for that exact pair. The gate is never
  bypassed (incident 2026-06-10); this table is the single source of truth the
  deploy service consults before allowing a PROD deploy.
* **Per-customer independence (§3.3).** Different customers may run different
  versions simultaneously; each customer's deploy/accept history is its own.

**Secret governance (OQ-5 / CLAUDE.md §4/§5).** This table NEVER stores secret
material. Per-customer secrets live ONLY in the credentials store
(``backend/services/credentials.py``); the deploy backend *points into* that
store keyed per customer (via ``Customer.credential_id``) and never duplicates a
secret here, in source, or in a log line.

**Environment + event-type enums** are kept in code (Python tuples) sourced
once and mirrored by a DB CHECK so the value set has a single source of truth
(the established enum-tuple pattern).
"""

from sqlalchemy import BigInteger, CheckConstraint, Column, ForeignKey, Identity, String, Text
from sqlalchemy.dialects.postgresql import UUID

from backend.db.models.base import Base, TimestampMixin, UUIDMixin

# ---------------------------------------------------------------------------
# Enum value sets (single source of truth — mirrored by the DB CHECKs below
# and by migration 076). Underscore DB convention.
# ---------------------------------------------------------------------------

# The two per-customer deploy environments (design §3.3 — UAT / PROD tabs).
ENVIRONMENT_VALUES: tuple[str, ...] = ("uat", "prod")

# The two audit-log event kinds:
#   * ``deploy`` — a version was provisioned/updated onto a customer's instance.
#   * ``accept`` — the Manažér accepted a customer's UAT (opens PROD; §3.5).
EVENT_TYPE_VALUES: tuple[str, ...] = ("deploy", "accept")

# Per-deploy outcome (an ``accept`` event is always ``ok``).
STATUS_VALUES: tuple[str, ...] = ("ok", "failed")


def _ck_in(column: str, values: tuple[str, ...]) -> str:
    """Render a ``column IN ('a', 'b')`` SQL fragment from a value tuple."""
    rendered = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({rendered})"


class DeployEvent(Base, UUIDMixin, TimestampMixin):
    """One deploy or acceptance event in the per-customer audit-log (§3.5)."""

    __tablename__ = "deploy_events"

    # Monotonic append-order sequence — the deterministic "latest event" key.
    # ``created_at`` (a transaction-start timestamp) is identical for rows written
    # in one transaction, so it cannot order them; this DB-assigned IDENTITY does.
    # ``current_version`` / acceptance-recency order by this, newest-first.
    seq = Column(BigInteger, Identity(always=False), nullable=False, unique=True)

    # Owning customer — every deploy/accept is per-customer (§3.1). ON DELETE
    # CASCADE: removing a customer removes its deploy history (the audit-log is
    # meaningless without the customer). ``project_id`` is reachable via the
    # customer; kept denormalised below for project-scoped queries.
    customer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Denormalised owning project (the customer's project at event time) so the
    # project-scoped UAT/PROD pages can query the log without a join. ON DELETE
    # CASCADE mirrors the customer cascade.
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The version being deployed/accepted, by version_number (e.g. ``v0.1.0``).
    # Stored as the string number (not a versions FK) so the audit row survives
    # a version delete — a finished history entry, like the credentials store
    # pointer pattern. Matches ``versions.version_number`` (String(50)).
    version_number = Column(String(50), nullable=False)
    # ``uat`` | ``prod`` (design §3.3).
    environment = Column(String(10), nullable=False)
    # ``deploy`` | ``accept``.
    event_type = Column(String(10), nullable=False)
    # ``ok`` | ``failed`` (an accept is always ``ok``).
    status = Column(String(10), nullable=False, server_default="ok")
    # WHO performed it (the Manažér / operator) — the acceptance gate logs the
    # actor (§3.5 "who / when / version / customer"). ON DELETE SET NULL so the
    # audit row survives a user delete (history must not vanish). The WHEN is
    # ``created_at`` from :class:`TimestampMixin`.
    actor_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Short non-secret detail (e.g. ``"OK"``, a deploy error tail, the deployed
    # URL). NEVER holds secret material (§4) — only a human-readable summary.
    detail = Column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(_ck_in("environment", ENVIRONMENT_VALUES), name="ck_deploy_events_environment"),
        CheckConstraint(_ck_in("event_type", EVENT_TYPE_VALUES), name="ck_deploy_events_event_type"),
        CheckConstraint(_ck_in("status", STATUS_VALUES), name="ck_deploy_events_status"),
    )
