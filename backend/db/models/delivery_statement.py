"""An issued delivered-token statement of a version — the basis of an invoice for development (DEV-50).

Written once, when the Manažér issues it, and never rewritten: the rates, the counting rule and the tokenizer it was
issued with stay with it, so a later change cannot alter an invoice already sent. A new issue is a new row that
REPLACES the version's valid one (DEV-54) — the old row only gets ``replaced_at``; one version has one valid statement.
"""

from sqlalchemy import TIMESTAMP, CheckConstraint, Column, ForeignKey, Index, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from backend.db.models.base import Base, TimestampMixin, UUIDMixin


class DeliveryStatement(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "delivery_statements"

    version_id = Column(UUID(as_uuid=True), ForeignKey("versions.id", ondelete="CASCADE"), nullable=False, index=True)
    #: The delivered state of the previous version (or of the project after founding) and of this one.
    base_sha = Column(String(40), nullable=False)
    delivered_sha = Column(String(40), nullable=False)
    #: Where the delivered state came from — the sign-off, the Verifikácia PASS, or the version tag.
    delivered_source = Column(String(40), nullable=False)
    tokenizer = Column(String(100), nullable=False)
    #: ``fix`` (an error in delivered code — 0 €) or ``change`` (billed).
    work_kind = Column(String(10), nullable=False)
    tokens_code = Column(Integer, nullable=False)
    tokens_tests = Column(Integer, nullable=False)
    tokens_docs = Column(Integer, nullable=False)
    #: € per 1 000 tokens at issue — code and tests share one rate, documentation has its own.
    rate_code = Column(Numeric(12, 4), nullable=False)
    rate_docs = Column(Numeric(12, 4), nullable=False)
    #: € per line, each rounded to cents; ``amount_eur`` is their sum. NULL only on a statement issued before the
    #: lines were kept whose lines would not add up to its total (migration 111) — its total stays as issued.
    amount_code_eur = Column(Numeric(12, 2), nullable=True)
    amount_docs_eur = Column(Numeric(12, 2), nullable=True)
    amount_eur = Column(Numeric(12, 2), nullable=False)
    #: Every file of the delivery: path, kind or the reason it was left out, lines, tokens.
    files = Column(JSONB, nullable=False)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    #: DEV-54 — the statement this one replaced (issued again for the same version), and when this one was replaced
    #: in turn. A replaced statement stays as issued, marked: it must not be invoiced.
    replaces_id = Column(UUID(as_uuid=True), ForeignKey("delivery_statements.id"), nullable=True)
    replaced_at = Column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("work_kind IN ('fix', 'change')", name="ck_delivery_statements_work_kind"),
        CheckConstraint(
            "tokens_code >= 0 AND tokens_tests >= 0 AND tokens_docs >= 0 AND amount_eur >= 0",
            name="ck_delivery_statements_nonneg",
        ),
        CheckConstraint(
            "(amount_code_eur IS NULL AND amount_docs_eur IS NULL) OR "
            "(amount_code_eur >= 0 AND amount_docs_eur >= 0 AND amount_code_eur + amount_docs_eur = amount_eur)",
            name="ck_delivery_statements_lines_add_up",
        ),
        CheckConstraint("replaces_id IS NULL OR replaces_id <> id", name="ck_delivery_statements_replaces_other"),
        # One valid statement per version; a statement is replaced by one statement at most.
        Index(
            "ux_delivery_statements_one_valid",
            "version_id",
            unique=True,
            postgresql_where=text("replaced_at IS NULL"),
        ),
        Index(
            "ux_delivery_statements_replaces",
            "replaces_id",
            unique=True,
            postgresql_where=text("replaces_id IS NOT NULL"),
        ),
    )
