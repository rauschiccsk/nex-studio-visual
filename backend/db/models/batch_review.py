"""One round of decision cards Poradca assessed at once for the Manažér (DEV-483).

Director 21.11.2026: „Táto nová cesta naraz by vybavila všetky otázky s poradcom a mne by dali len súhrn na
schválenie.“ When the account that leads the project has the fast path switched on, the cockpit hands every card of a
new consultation to Poradca in one question; this row ties that consultation (the ``seq`` of the message that carries
its cards) to Poradca's answer, or says why Poradca did not get it.
"""

from sqlalchemy import BigInteger, Column, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from backend.db.models.base import Base, TimestampMixin, UUIDMixin


class BatchReview(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "batch_reviews"

    version_id = Column(UUID(as_uuid=True), ForeignKey("versions.id", ondelete="CASCADE"), nullable=False, index=True)
    #: The message that carries the cards — a new consultation is a new message, so a new review.
    consultation_seq = Column(BigInteger, nullable=False)
    conversation_id = Column(
        UUID(as_uuid=True), ForeignKey("poradca_conversations.id", ondelete="SET NULL"), nullable=True
    )
    answer_message_id = Column(
        UUID(as_uuid=True), ForeignKey("poradca_messages.id", ondelete="SET NULL"), nullable=True
    )
    #: Why Poradca did not get the cards (not ready, busy) — the cards are then decided one by one as before.
    failure = Column(Text, nullable=True)

    __table_args__ = (UniqueConstraint("version_id", "consultation_seq", name="uq_batch_reviews_version_seq"),)
