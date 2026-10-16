"""Poradca — conversations with an agent that only reads and advises (ICCINT-167, ``docs/specs/poradca.md``).

**Why separate tables and not ``pipeline_message``.** Poradca runs ALONGSIDE a build: it does not switch its state,
does not wait for it, and the build agent knows nothing about the conversation. A message in ``pipeline_message`` is a
write to the build log, which both the agent and the Manažér read as the version history — a Poradca conversation would
be a foreign voice there. Besides, Poradca may also be asked about the whole project when no version is running.

**What is stored from the progress.** Only STEPS — tool and target („Read · backend/x.py", „logy · uat backend") —
never the content the tool returned. The content may carry anything the agent saw; only the answer, which has passed
the secrets filter, goes to the database and the browser (design 4.1, finding B7 of the independent review).
"""

from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, Float, ForeignKey, Identity, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from backend.db.models.base import Base, TimestampMixin, UUIDMixin
from backend.db.models.pipeline import STAGE_VALUES

#: Who speaks in the conversation: the human (the author of the conversation) or Poradca.
AUTHOR_HUMAN = "human"
AUTHOR_PORADCA = "poradca"
MESSAGE_AUTHORS = (AUTHOR_HUMAN, AUTHOR_PORADCA)

#: State of a Poradca answer. The human's question is always ``done`` — it is complete the moment they send it.
RUNNING = "running"
DONE = "done"
FAILED = "failed"
STOPPED = "stopped"
MESSAGE_STATUSES = (RUNNING, DONE, FAILED, STOPPED)


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


class PoradcaConversation(Base, UUIDMixin, TimestampMixin):
    """One conversation of one person about one project.

    ``version_id`` is what we are talking about — empty means „celý projekt". It can be changed during the conversation,
    because a person often asks about the version that is running right now, and then about the previous one.

    ``claude_session_id`` is the Claude Code session the conversation carries via ``--resume``; its record lies
    in the directory of THIS conversation, not in the project directory with the build agent's records (finding B3).
    """

    __tablename__ = "poradca_conversations"

    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    version_id = Column(UUID(as_uuid=True), ForeignKey("versions.id", ondelete="SET NULL"), nullable=True)
    #: Author of the conversation — the logged-in account. They and the admin account see it. RESTRICT (ICCINT-169):
    #: deleting the author would take away the conversations and the cost of answers from Náklady — deactivate instead.
    author_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    #: Title from the first question (shortened), so that conversations can be told apart in the list.
    title = Column(String(200), nullable=False)
    claude_session_id = Column(UUID(as_uuid=True), nullable=False, unique=True)
    #: DEV-30: fingerprint of the charter this conversation last received. Claude Code takes the charter only on
    #: the first question (``--append-system-prompt`` is ignored on ``--resume``); when it changes, the runner adds
    #: the current one to the next question's text. Empty for conversations started before DEV-30.
    charter_sha = Column(String(64), nullable=True)
    #: When the author (or admin) deleted it. The row stays only for Náklady: text, steps and title are gone,
    #: of the messages only usage and time remain. For the interface such a conversation does not exist (404).
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    #: DEV-138: the phase whose decision cards this conversation reviews — set when the „Overiť s Poradcom“ button
    #: founds it, so the next card of the same version and phase goes into it instead of a new conversation
    #: (``backend.services.poradca.review``). Empty for a conversation the person started by hand.
    review_stage = Column(String(16), nullable=True)

    __table_args__ = (
        Index("ix_poradca_conversations_project_author", "project_id", "author_id"),
        CheckConstraint(
            f"review_stage IS NULL OR review_stage IN ({_in(STAGE_VALUES)})",
            name="ck_poradca_conversations_review_stage",
        ),
    )


class PoradcaMessage(Base, UUIDMixin, TimestampMixin):
    """One message of the conversation — a human's question or a Poradca answer.

    The answer is created as ``running`` and filled in: ``steps`` (tool and target, never content), then text,
    usage (``usage`` — tokens and the full model name as Claude Code reports it) and duration.
    """

    __tablename__ = "poradca_messages"

    conversation_id = Column(
        UUID(as_uuid=True), ForeignKey("poradca_conversations.id", ondelete="CASCADE"), nullable=False
    )
    #: Order in the conversation — monotonic even with equal ``created_at`` in one transaction.
    seq = Column(BigInteger, Identity(), nullable=False)
    author = Column(String(16), nullable=False)
    content = Column(Text, nullable=False, server_default="")
    #: ``[{"tool": "Read", "target": "backend/x.py"}, …]`` — what Poradca did, without content.
    steps = Column(JSONB, nullable=False, server_default="[]")
    #: DEV-52 — screenshots attached to a question: ``[{"id", "name", "mime", "size_bytes"}, …]``; the files lie in
    #: Poradca's data next to the conversation's record (``sandbox.attachments_dir``), never in the project.
    attachments = Column(JSONB, nullable=False, server_default="[]")
    status = Column(String(16), nullable=False, server_default=DONE)
    #: ``{"input_tokens", "output_tokens", "model"}`` — the same shape as a build's ``payload.usage``, so that
    #: Poradca can be priced by the same path as a build.
    usage = Column(JSONB, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    #: Why the answer failed — a sentence for the human, without secrets.
    error = Column(Text, nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    #: The version the question referred to when it was asked — a conversation may change its version, yet a version's
    #: Náklady must sum only what was actually asked about it. Empty = the whole project.
    version_id = Column(UUID(as_uuid=True), ForeignKey("versions.id", ondelete="SET NULL"), nullable=True)
    #: DEV-29: the backlog item the Manažér saved from this answer's request — a second click returns it.
    captured_backlog_item_id = Column(
        UUID(as_uuid=True), ForeignKey("backlog_items.id", ondelete="SET NULL"), nullable=True
    )
    #: DEV-119: the ticket in Plane a person founded from this answer's proposal — a second click returns it.
    plane_issue_id = Column(String(64), nullable=True)
    plane_identifier = Column(String(32), nullable=True)
    plane_url = Column(String(500), nullable=True)

    __table_args__ = (
        CheckConstraint(f"author IN ({_in(MESSAGE_AUTHORS)})", name="ck_poradca_messages_author"),
        CheckConstraint(f"status IN ({_in(MESSAGE_STATUSES)})", name="ck_poradca_messages_status"),
        Index("ix_poradca_messages_conversation_seq", "conversation_id", "seq"),
    )
