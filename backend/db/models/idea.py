"""Nápady — a new idea talked over with Poradca before any project exists (DEV-318, ``docs/specs/ideas.md``).

Director 06.11.2026: „Návrhy nových projektov sa mi zhromažďujú. Navrhni mi riešenie, aby som mohol tie návrhy robiť
pokiaľ ty pracuješ nad kokpitom.“ An idea is one Poradca conversation (``poradca_conversations.idea_id``) that belongs
to no project. It ends when a person clicks one of its two outcomes: a new project founded from the brief Poradca wrote,
or the brief added to an existing project's Zásobník. The conversation then moves to that project and goes on there;
the idea keeps where it ended.

What the screen calls the idea's state is derived, never stored twice: an outcome is set → „Založený ako …“ /
„Pridaný do …“; an answer carries a brief → „Zadanie hotové“; otherwise „Rozpracovaný“.
"""

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID

from backend.db.models.base import Base, TimestampMixin, UUIDMixin

#: The two ways an idea ends (DEV-318): a new project founded from its brief, or the brief added to an existing one.
OUTCOME_FOUNDED = "founded"
OUTCOME_ADDED = "added"
OUTCOMES = (OUTCOME_FOUNDED, OUTCOME_ADDED)


class Idea(Base, UUIDMixin, TimestampMixin):
    """One person's new idea. Its title and talk live in its Poradca conversation (one per idea)."""

    __tablename__ = "ideas"

    #: Who started it — they and the admin account see it, as with Poradca's conversations. RESTRICT as there
    #: (ICCINT-169): deleting the author would take away the cost of the answers.
    author_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    outcome = Column(String(16), nullable=True)
    #: The project the idea ended in — founded from it, or the one its brief was added to.
    outcome_project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    #: When the brief was added to an existing project: the Zásobník requirement it became (REQ-N).
    outcome_backlog_item_id = Column(
        UUID(as_uuid=True), ForeignKey("backlog_items.id", ondelete="SET NULL"), nullable=True
    )
    #: The Poradca answer whose brief the outcome took — a second click finds the outcome instead of repeating it.
    outcome_message_id = Column(
        UUID(as_uuid=True), ForeignKey("poradca_messages.id", ondelete="SET NULL"), nullable=True
    )
    concluded_at = Column(DateTime(timezone=True), nullable=True)
    #: The ticket a founded project got in the DEV register with its brief (``plane_tickets.file_ticket``).
    plane_issue_id = Column(String(64), nullable=True)
    plane_identifier = Column(String(32), nullable=True)
    plane_url = Column(String(500), nullable=True)
    #: When the author (or admin) threw it away. The row stays for the cost of its answers, as with a conversation.
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            f"outcome IS NULL OR outcome IN ({', '.join(repr(o) for o in OUTCOMES)})", name="ck_ideas_outcome"
        ),
    )
