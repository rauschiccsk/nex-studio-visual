"""Service layer for :class:`~backend.db.models.architect.ArchitectMessage`.

Provides the synchronous CRUD surface used by API routers. All methods
accept ``db: Session`` as the first argument and only ever call
``session.flush()`` — transaction commit is the router's responsibility.
Errors are signalled via :class:`ValueError` so the router can translate
them to the appropriate HTTP status code.

Design notes (per DESIGN.md §1.12 ArchitectMessage, §1.5 Architect
Sessions / ``architect_messages`` table, D-08 SSE streaming, and
:mod:`backend.db.models.architect.ArchitectMessage`):

    * ``id``, ``created_at`` and ``updated_at`` are server-managed and
      therefore immutable from the service layer (``updated_at`` is
      auto-stamped by the ORM via ``onupdate=func.now()`` on flush).
    * ``session_id``, ``role`` and ``content`` are immutable once the
      row is persisted — Architect chat history is **append-only** per
      DESIGN.md §1.5 ("Streaming: The message row is written after the
      stream completes with final token counts"). A message always
      belongs to exactly one session, with a fixed role and fixed
      content, for its lifetime. :class:`ArchitectMessageUpdate`
      deliberately omits those columns and the service's
      ``allowed_fields`` allow-list enforces that contract defensively.
    * ``role`` is constrained by the ``ck_architect_messages_role`` DB
      CHECK (``user | assistant``). The Pydantic
      :data:`~backend.schemas.architect_message.ArchitectMessageRole`
      literal mirrors the DB constraint, so the service does not
      revalidate — if an invalid value ever reaches the service (e.g.
      a bypassed schema) the DB CHECK rejects it on flush.
    * ``input_tokens``, ``output_tokens`` and ``cost_usd`` are the only
      mutable columns. They are typically ``NULL`` at creation (the
      row is written before the SSE stream completes) and backfilled
      via :func:`update` with the final accounting once the assistant
      turn terminates. They can also be corrected retroactively if
      Anthropic reports a billing adjustment.
    * ``ArchitectMessage`` has **no** UNIQUE constraints beyond the PK
      — a session may contain many messages with the same role and
      even identical content (e.g. the user repeating a prompt).
      :func:`create` therefore performs no pre-flush natural-key
      check. If ``session_id`` does not match an existing row the
      DB-level FK rejects the flush and the error propagates as-is.
    * ``architect_messages`` has **no inbound foreign keys** — no
      other table references it. :func:`delete` performs no dependency
      check and is a straightforward hard-delete. In normal operation
      chat history is retained; :func:`delete` is reserved for test
      fixtures / admin redaction tooling.
    * List filters (``session_id``, ``role``) match the indexed column
      (``ix_architect_messages_session_id``) and support the Architect
      UI (DESIGN.md §3.1 ``ArchitectPage``) — "load the full
      conversation for this session", "show only assistant turns for
      token-usage accounting".
    * List ordering is ``created_at ASC`` so messages appear in
      conversation order (oldest first) — the natural rendering for a
      chat transcript. This deliberately differs from
      :mod:`backend.services.architect_session`, which orders sessions
      newest-first for the session list view.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.architect import ArchitectMessage
from backend.schemas.architect_message import (
    ArchitectMessageCreate,
    ArchitectMessageRole,
    ArchitectMessageUpdate,
)


def list_architect_messages(
    db: Session,
    *,
    session_id: Optional[UUID] = None,
    role: Optional[ArchitectMessageRole] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[ArchitectMessage]:
    """Return Architect messages filtered by the supplied criteria.

    Results are ordered by ``created_at ASC`` so the transcript appears
    in conversation order (oldest first), matching the Architect chat
    UI convention (DESIGN.md §3.1 ``ArchitectChat``).

    Args:
        db: Active SQLAlchemy session.
        session_id: Optional session filter — restrict to messages
            belonging to a specific Architect session (the core
            "load conversation" query). Hits the
            ``ix_architect_messages_session_id`` index.
        role: Optional role filter (``user`` | ``assistant``) — useful
            for token-usage accounting where only assistant turns
            carry ``cost_usd``.
        limit: Maximum number of rows to return.
        offset: Number of rows to skip.

    Returns:
        List of :class:`ArchitectMessage` instances.
    """
    stmt = select(ArchitectMessage)
    if session_id is not None:
        stmt = stmt.where(ArchitectMessage.session_id == session_id)
    if role is not None:
        stmt = stmt.where(ArchitectMessage.role == role)
    stmt = stmt.order_by(ArchitectMessage.created_at.asc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def get_by_id(db: Session, message_id: UUID) -> ArchitectMessage:
    """Return a single Architect message by primary key.

    Raises:
        ValueError: If no message with the supplied ``message_id``
            exists. The router converts this to an HTTP 404 response.
    """
    message = db.get(ArchitectMessage, message_id)
    if message is None:
        raise ValueError(f"ArchitectMessage {message_id} not found")
    return message


def create(db: Session, data: ArchitectMessageCreate) -> ArchitectMessage:
    """Create a new Architect chat message.

    ``input_tokens``, ``output_tokens`` and ``cost_usd`` are typically
    ``None`` at creation because the row is persisted either before the
    SSE stream starts (for user turns) or after it completes (for
    assistant turns, DESIGN.md §1.5 "Streaming"). They may still be
    supplied up-front for backfill / import flows.

    :class:`ArchitectMessage` has no UNIQUE constraints beyond the PK,
    so no pre-flush natural-key validation is required; if the supplied
    ``session_id`` foreign key does not match an existing row the
    DB-level FK rejects the flush and the error propagates as-is
    (routed at the API layer as a 409/422).

    Args:
        db: Active SQLAlchemy session.
        data: Validated creation payload.

    Returns:
        The newly created and flushed :class:`ArchitectMessage` with
        its server-generated ``id``, ``created_at`` and ``updated_at``
        populated.
    """
    message = ArchitectMessage(
        session_id=data.session_id,
        role=data.role,
        content=data.content,
        input_tokens=data.input_tokens,
        output_tokens=data.output_tokens,
        cost_usd=data.cost_usd,
    )
    db.add(message)
    db.flush()
    return message


def update(
    db: Session,
    message_id: UUID,
    data: ArchitectMessageUpdate,
) -> ArchitectMessage:
    """Partially update an Architect chat message.

    Only ``input_tokens``, ``output_tokens`` and ``cost_usd`` may be
    changed — chat history is append-only, so ``session_id``, ``role``
    and ``content`` are immutable after creation. ``id`` and
    ``created_at`` are likewise immutable; ``updated_at`` is
    auto-stamped by the ORM on flush via ``onupdate=func.now()``.

    Fields that are ``None`` in the payload are treated as "leave
    unchanged" to support PATCH semantics — the usage/cost columns are
    nullable at the DB level, so the explicit-null "clear a backfilled
    value" transition is not expressible through this service. In
    practice the UI never needs to null out usage; backfill is a
    one-way operation.

    Raises:
        ValueError: If the message does not exist.
    """
    message = get_by_id(db, message_id)

    update_data = data.model_dump(exclude_unset=True)
    # Defensive guard — the schema already excludes immutable fields,
    # but silently dropping any that slip through keeps the service
    # honest.
    allowed_fields = {
        "input_tokens",
        "output_tokens",
        "cost_usd",
    }

    for field, value in update_data.items():
        if field in allowed_fields and value is not None:
            setattr(message, field, value)

    db.flush()
    return message


def delete(db: Session, message_id: UUID) -> None:
    """Hard-delete an Architect message.

    ``architect_messages`` has no inbound foreign keys, so no
    dependency check is required. Chat history is normally retained
    for the lifetime of the session; :func:`delete` is reserved for
    test fixtures / admin redaction tooling. Deleting the parent
    :class:`~backend.db.models.architect.ArchitectSession` cascades
    automatically via ``ON DELETE CASCADE`` on ``session_id`` — the
    session-level delete is the usual path for removing a whole
    conversation.

    Raises:
        ValueError: If the message does not exist.
    """
    message = get_by_id(db, message_id)
    db.delete(message)
    db.flush()
