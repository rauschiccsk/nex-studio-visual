"""REST router for :class:`~backend.db.models.specifications.ProfessionalSpecification`.

Exposes the standard CRUD surface for AI-generated professional
specifications — the structured markdown document produced from a
customer-submitted raw specification (DESIGN.md §1.8
ProfessionalSpecification, §6.5 Specification Pipeline) — that backs the
Specification Pipeline UI (DESIGN.md §3.1 ``SpecificationPage`` /
``SpecificationViewer`` with version history) and gates downstream
DESIGN.md generation (DESIGN.md §9 / §10: approval unlocks
``design-documents/generate``):

* ``GET    /``            → paginated list (filter by ``project_id``,
  ``raw_spec_id``, ``approved_by`` and ``version``).
* ``GET    /{spec_id}``   → single professional specification by
  primary key.
* ``POST   /``            → create a new professional specification.
* ``PATCH  /{spec_id}``   → partial update of the mutable fields.
* ``DELETE /{spec_id}``   → hard-delete a professional specification
  (HTTP 204).

All endpoints are synchronous ``def`` — pg8000 is a synchronous driver
and FastAPI dispatches sync endpoints to a thread pool automatically.
The router delegates every persistence operation to
:mod:`backend.services.professional_specification` and handles commit /
rollback itself so the service layer remains transaction-agnostic.

The router is prefix-less; the mount prefix
(``/api/v1/professional-specifications``) is applied in
``backend/main.py`` via ``app.include_router`` (Task 4.27).

Design notes (per DESIGN.md §1.8 ProfessionalSpecification, §2
``professional_specifications`` table, §6.5 Specification Pipeline, §9
approval gating, §10 pipeline gating):

* ``id``, ``created_at`` and ``updated_at`` are server-managed and
  therefore immutable. ``project_id`` and ``raw_spec_id`` are immutable
  foreign keys — a professional specification belongs to exactly one
  project and is derived from exactly one raw specification for its
  lifetime (regenerations are new rows with an incremented ``version``,
  not a reassignment). This mirrors the treatment of ``project_id`` on
  :class:`~backend.schemas.design_document.DesignDocumentUpdate` and
  ``project_id`` / ``created_by`` on
  :class:`~backend.schemas.raw_specification.RawSpecificationUpdate`.
  :class:`~backend.schemas.professional_specification.ProfessionalSpecificationUpdate`
  deliberately omits both columns and the service enforces the contract
  defensively via an ``allowed_fields`` allow-list.
* :class:`ProfessionalSpecification` has **no** UNIQUE constraints
  beyond the PK — multiple rows sharing the same ``(project_id,
  raw_spec_id)`` pair are expected and represent regeneration history
  (one row per ``version``). ``POST`` therefore performs no pre-flush
  natural-key check.
* Approval convenience: when ``approved_by`` transitions from ``None``
  to a user UUID via ``PATCH`` and ``approved_at`` is not supplied
  explicitly, the service stamps ``approved_at = now()`` automatically
  (mirroring the ``approved_at`` auto-stamp on
  :mod:`backend.services.design_document`, the ``resolved_at``
  auto-stamp on :mod:`backend.services.bug` and the ``closed_at``
  auto-stamp on :mod:`backend.services.architect_session`). Approval
  unlocks downstream DESIGN.md generation (DESIGN.md §9 / §10 pipeline
  gating: ``professional_specifications.approved_by`` must be non-null
  before ``design-documents/generate`` can be triggered).
* ``professional_specifications`` has **no inbound foreign keys** — no
  other table references it. ``DELETE`` is a straightforward
  hard-delete with no RESTRICT dependency check. In normal operation
  professional specifications are retained as version history
  (DESIGN.md §3.1 ``SpecificationPage`` / ``SpecificationViewer``);
  ``DELETE`` is reserved for test fixtures / admin redaction tooling
  where the generated document itself must go. The outbound FKs
  ``project_id`` (``ON DELETE CASCADE``), ``raw_spec_id`` (``ON DELETE
  CASCADE``) and ``approved_by`` (``ON DELETE RESTRICT``) keep the row
  self-consistent when the parent rows change.
* List filters (``project_id``, ``raw_spec_id``, ``approved_by``,
  ``version``) map to the indexed columns
  (``ix_professional_specifications_project_id``,
  ``ix_professional_specifications_raw_spec_id``) and back the
  Specification Pipeline UI queries: "load this project's professional
  specifications", "load the professional specifications derived from
  this raw specification", "show unapproved specifications pending
  ``ri`` review", "fetch a specific version for display".
* List ordering (``created_at DESC``) is owned by the service so the
  newest version appears first, matching the ``SpecificationViewer``
  version-history UI convention (latest regeneration on top). In
  practice ``version`` is monotonically incremented on regeneration,
  so newest-by-``created_at`` is equivalent to highest-by-``version``
  for any given ``(project, raw_spec)`` pair.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.config.settings import settings
from backend.core.security import require_ri_role
from backend.db.models.foundation import User
from backend.db.session import SessionLocal, get_db
from backend.schemas.design_document import DesignDocumentCreate
from backend.schemas.pagination import PaginatedResponse
from backend.schemas.professional_specification import (
    ProfessionalSpecificationCreate,
    ProfessionalSpecificationRead,
    ProfessionalSpecificationUpdate,
)
from backend.services import claude_subprocess
from backend.services import design_document as design_document_service
from backend.services import professional_specification as professional_specification_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Professional Specifications"])


def _map_value_error(exc: ValueError) -> HTTPException:
    """Translate a service-layer ``ValueError`` into an HTTP exception.

    Mirrors the ICC error-handling pattern: ``not found`` → 404,
    duplicates/conflicts → 409, everything else (constraint / FK /
    validation failures) → 422.
    """
    message = str(exc)
    lowered = message.lower()
    if "not found" in lowered:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    if "already exists" in lowered or "duplicate" in lowered or "conflict" in lowered:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)


@router.get("", response_model=PaginatedResponse[ProfessionalSpecificationRead])
def list_professional_specifications(
    project_id: Optional[UUID] = Query(
        default=None,
        description=(
            "Filter by the project the professional specification belongs "
            "to. Hits the ``ix_professional_specifications_project_id`` "
            "index — the core ``SpecificationPage`` query (DESIGN.md §3.1)."
        ),
    ),
    raw_spec_id: Optional[UUID] = Query(
        default=None,
        description=(
            "Filter by the raw specification this professional "
            "specification was derived from. Hits the "
            "``ix_professional_specifications_raw_spec_id`` index — one "
            "raw spec can have multiple regenerated professional specs, "
            "one per ``version``."
        ),
    ),
    approved_by: Optional[UUID] = Query(
        default=None,
        description=(
            "Filter by approver — restrict to specifications approved by "
            "a specific ``ri``-role user. Combine with an explicit "
            "``None`` filter in the service layer to surface pending "
            "approvals."
        ),
    ),
    version: Optional[int] = Query(
        default=None,
        ge=1,
        description=("Filter by version number — fetch a specific version from the regeneration history."),
    ),
    skip: int = Query(default=0, ge=0, description="Number of rows to skip."),
    limit: int = Query(default=50, ge=1, le=100, description="Maximum rows to return."),
    db: Session = Depends(get_db),
) -> PaginatedResponse[ProfessionalSpecificationRead]:
    """Return a paginated list of professional specifications.

    Results are ordered by ``created_at DESC`` (newest version first) —
    owned by the service layer, matching the ``SpecificationViewer``
    version-history UI convention (DESIGN.md §3.1 — latest regeneration
    on top).
    """
    try:
        rows = professional_specification_service.list_professional_specifications(
            db,
            project_id=project_id,
            raw_spec_id=raw_spec_id,
            approved_by=approved_by,
            version=version,
            limit=limit,
            offset=skip,
        )
        total = professional_specification_service.count_professional_specifications(
            db,
            project_id=project_id,
            raw_spec_id=raw_spec_id,
            approved_by=approved_by,
            version=version,
        )
    except ValueError as exc:
        raise _map_value_error(exc) from exc

    return PaginatedResponse[ProfessionalSpecificationRead](
        items=[ProfessionalSpecificationRead.model_validate(row) for row in rows],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/{spec_id}", response_model=ProfessionalSpecificationRead)
def get_professional_specification(
    spec_id: UUID,
    db: Session = Depends(get_db),
) -> ProfessionalSpecificationRead:
    """Return a single professional specification by primary key."""
    try:
        spec = professional_specification_service.get_by_id(db, spec_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    return ProfessionalSpecificationRead.model_validate(spec)


@router.post(
    "",
    response_model=ProfessionalSpecificationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_professional_specification(
    payload: ProfessionalSpecificationCreate,
    db: Session = Depends(get_db),
) -> ProfessionalSpecificationRead:
    """Create a new AI-generated professional specification.

    ``version`` defaults to ``1`` via the Pydantic schema / DB
    ``server_default`` when omitted. ``approved_by`` / ``approved_at``
    are typically ``None`` at creation — a specification is approved via
    a subsequent ``PATCH`` by a user with the ``ri`` role (DESIGN.md §9
    business rule). :class:`ProfessionalSpecification` has no UNIQUE
    constraints beyond the PK, so no pre-flush natural-key validation is
    performed; multiple rows sharing the same ``(project_id,
    raw_spec_id)`` pair are expected and represent regeneration history.
    Missing or invalid foreign keys (``project_id``, ``raw_spec_id``,
    ``approved_by``) are rejected by the DB-level FK and surface as
    HTTP 422.
    """
    try:
        spec = professional_specification_service.create(db, payload)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(spec)
    return ProfessionalSpecificationRead.model_validate(spec)


@router.patch("/{spec_id}", response_model=ProfessionalSpecificationRead)
def update_professional_specification(
    spec_id: UUID,
    payload: ProfessionalSpecificationUpdate,
    db: Session = Depends(get_db),
) -> ProfessionalSpecificationRead:
    """Partially update a professional specification's mutable fields.

    Only ``content``, ``version``, ``approved_by`` and ``approved_at``
    are mutable. ``id``, ``project_id``, ``raw_spec_id`` and
    ``created_at`` are immutable — a specification belongs to exactly
    one project and is derived from exactly one raw specification for
    its lifetime (regenerations are new rows with an incremented
    ``version``). ``updated_at`` is refreshed by the ORM on flush via
    ``onupdate=func.now()``. Fields omitted from the payload are left
    unchanged (PATCH semantics).

    When ``approved_by`` transitions from ``None`` to a user UUID and
    ``approved_at`` is not supplied explicitly, the service stamps
    ``approved_at = now()`` automatically (mirroring the ``approved_at``
    auto-stamp on :mod:`backend.services.design_document`, the
    ``resolved_at`` auto-stamp on :mod:`backend.services.bug` and the
    ``closed_at`` auto-stamp on :mod:`backend.services.architect_session`).
    Approval unlocks downstream DESIGN.md generation (DESIGN.md §9 /
    §10 pipeline gating).
    """
    try:
        spec = professional_specification_service.update(db, spec_id, payload)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(spec)
    return ProfessionalSpecificationRead.model_validate(spec)


class _SpecChatHistoryItem(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class _SpecChatPayload(BaseModel):
    message: str
    current_content: str
    history: list[_SpecChatHistoryItem] = []


_CHAT_MARKER = "[SPRÁVA]"
_SPEC_MARKER = "[SPEC]"


@router.post("/{spec_id}/chat", status_code=status.HTTP_200_OK)
async def chat_professional_spec(
    spec_id: UUID,
    payload: _SpecChatPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ri_role),
):
    """Iteratively refine a professional specification via chat (SSE).

    The AI returns two logical sections separated by ``[SPRÁVA]`` /
    ``[SPEC]`` markers.  The backend parses these on the fly and emits
    typed SSE events so the frontend can update the chat panel and the
    spec editor independently::

        data: {"type": "chat_chunk", "content": "..."}   ← conversational text
        data: {"type": "spec_chunk", "content": "..."}   ← updated spec content
        data: {"type": "done"}
        data: {"type": "error", "content": "..."}

    The endpoint is stateless — chat history is supplied by the caller
    in ``payload.history`` so no DB chat-message table is required.
    ``ri`` role only.
    """
    try:
        professional_specification_service.get_by_id(db, spec_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc

    template_path = Path(settings.knowledge_base_path) / "templates" / "PROFESSIONAL_SPEC_TEMPLATE.md"
    try:
        template_content = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Cannot read PROFESSIONAL_SPEC_TEMPLATE.md: {exc}",
        ) from exc

    # Build conversation history block
    history_parts: list[str] = []
    for item in payload.history:
        role_label = "Používateľ" if item.role == "user" else "AI"
        history_parts.append(f"[{role_label}]: {item.content}")
    history_block = "\n\n".join(history_parts)

    system_prompt = (
        "Si ICC Professional Specification Editor AI.\n"
        "Tvojou úlohou je upravovať profesionálnu špecifikáciu na základe požiadaviek používateľa.\n\n"
        "FORMÁT ODPOVEDE — POVINNÝ, DODRŽUJ PRESNE:\n"
        "[SPRÁVA]\n"
        "Tu napíš 1-3 vety v slovenčine o tom čo si zmenil alebo doplnil.\n\n"
        "[SPEC]\n"
        "Tu napíš CELÚ aktualizovanú profesionálnu špecifikáciu — kompletný dokument.\n\n"
        "PRAVIDLÁ:\n"
        "- VŽDY začni odpoveď s [SPRÁVA] — nikdy nič pred tým\n"
        "- [SPEC] musí obsahovať KOMPLETNÝ dokument, nie len zmenené časti\n"
        "- Meni len to čo používateľ požaduje, ostatné ponechaj\n"
        "- Jazyk: slovenčina, business štýl zrozumiteľný zákazníkovi\n"
        "- Dodržuj štruktúru ICC šablóny\n\n"
        f"ICC ŠABLÓNA:\n{template_content}"
    )

    user_prompt = (
        f"AKTUÁLNA ŠPECIFIKÁCIA:\n{payload.current_content}\n\n"
        + (f"HISTÓRIA KONVERZÁCIE:\n{history_block}\n\n" if history_block else "")
        + f"POŽIADAVKA POUŽÍVATEĽA:\n{payload.message}"
    )

    # Fall back to pure chat mode after this many characters of preamble
    # without ``[SPRÁVA]`` — Claude sometimes skips the marker on short
    # prompts, and the previous logic silently trimmed the whole reply
    # down to an 8-char tail. Large enough to allow a legitimate preamble
    # (``{"type":"system",...``) but small enough to catch forgotten
    # markers before the real response gets buffered into the void.
    PREAMBLE_FALLBACK_THRESHOLD = 256

    async def _sse_generator():
        buffer = ""
        # ``preamble`` → ``chat`` on [SPRÁVA] → ``spec`` on [SPEC].
        # ``chat_fallback`` is entered when markers are missing entirely;
        # everything flows out as chat_chunk with no spec-content updates.
        state = "preamble"
        total_chars = 0
        chat_emitted = 0
        spec_emitted = 0

        try:
            async for chunk in claude_subprocess.run_claude_stream(
                prompt=user_prompt,
                context=system_prompt,
            ):
                buffer += chunk
                total_chars += len(chunk)

                # State machine — process buffer until no more transitions
                changed = True
                while changed:
                    changed = False

                    if state == "preamble":
                        if _CHAT_MARKER in buffer:
                            idx = buffer.index(_CHAT_MARKER)
                            buffer = buffer[idx + len(_CHAT_MARKER) :]
                            state = "chat"
                            changed = True
                        elif _SPEC_MARKER in buffer:
                            # AI skipped [SPRÁVA] — treat the preamble as
                            # chat and jump straight to spec.
                            idx = buffer.index(_SPEC_MARKER)
                            chat_part = buffer[:idx].strip()
                            if chat_part:
                                payload_json = json.dumps(
                                    {"type": "chat_chunk", "content": chat_part}
                                )
                                yield f"data: {payload_json}\n\n"
                                chat_emitted += len(chat_part)
                            buffer = buffer[idx + len(_SPEC_MARKER) :]
                            state = "spec"
                            changed = True
                        elif len(buffer) > PREAMBLE_FALLBACK_THRESHOLD:
                            # No markers at all — treat the whole stream
                            # as a chat reply; spec content stays untouched.
                            state = "chat_fallback"
                            changed = True

                    elif state == "chat":
                        if _SPEC_MARKER in buffer:
                            idx = buffer.index(_SPEC_MARKER)
                            chat_part = buffer[:idx].strip()
                            if chat_part:
                                payload_json = json.dumps(
                                    {"type": "chat_chunk", "content": chat_part}
                                )
                                yield f"data: {payload_json}\n\n"
                                chat_emitted += len(chat_part)
                            buffer = buffer[idx + len(_SPEC_MARKER) :]
                            state = "spec"
                            changed = True
                        else:
                            # Emit safe portion, hold potential partial marker tail
                            safe_len = max(0, len(buffer) - len(_SPEC_MARKER))
                            if safe_len > 0:
                                payload_json = json.dumps(
                                    {"type": "chat_chunk", "content": buffer[:safe_len]}
                                )
                                yield f"data: {payload_json}\n\n"
                                chat_emitted += safe_len
                                buffer = buffer[safe_len:]

                    elif state == "chat_fallback":
                        if buffer:
                            payload_json = json.dumps(
                                {"type": "chat_chunk", "content": buffer}
                            )
                            yield f"data: {payload_json}\n\n"
                            chat_emitted += len(buffer)
                            buffer = ""

                    elif state == "spec":
                        if buffer:
                            payload_json = json.dumps(
                                {"type": "spec_chunk", "content": buffer}
                            )
                            yield f"data: {payload_json}\n\n"
                            spec_emitted += len(buffer)
                            buffer = ""

        except (RuntimeError, TimeoutError) as exc:
            logger.error("Claude stream error for spec chat %s: %s", spec_id, exc)
            yield f"data: {json.dumps({'type': 'error', 'content': str(exc)})}\n\n"

        # Flush remaining buffer. ``preamble`` at EOF means we never saw
        # [SPRÁVA] AND never crossed the fallback threshold — rare but
        # possible for very short Claude replies; send the whole thing as
        # chat_chunk so the user at least sees something.
        if buffer:
            event_type = "spec_chunk" if state == "spec" else "chat_chunk"
            payload_json = json.dumps({"type": event_type, "content": buffer.strip()})
            yield f"data: {payload_json}\n\n"
            if event_type == "chat_chunk":
                chat_emitted += len(buffer.strip())
            else:
                spec_emitted += len(buffer.strip())

        logger.info(
            "spec chat %s done: state=%s received=%d chat=%d spec=%d",
            spec_id,
            state,
            total_chars,
            chat_emitted,
            spec_emitted,
        )
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{spec_id}/generate-design-doc", status_code=status.HTTP_200_OK)
async def generate_design_doc(
    spec_id: UUID,
    doc_type: Literal["design", "behavior"] = Query(
        ...,
        description="Type of document to generate: ``design`` (DESIGN.md) or ``behavior`` (BEHAVIOR.md).",
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ri_role),
):
    """Stream-generate a DESIGN.md or BEHAVIOR.md from an approved professional spec.

    Reads the professional specification content and the appropriate template
    (DESIGN_TEMPLATE.md or BEHAVIOR_TEMPLATE.md), then streams the AI response
    as SSE events::

        data: {"type": "chunk", "content": "..."}
        data: {"type": "done", "content": "...full text...", "design_doc_id": "..."}
        data: {"type": "error", "content": "..."}

    On completion the result is persisted as a new ``DesignDocument`` row.
    ``ri`` role only.
    """
    try:
        prof_spec = professional_specification_service.get_by_id(db, spec_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc

    # Load the appropriate template from KB
    template_name = "DESIGN_TEMPLATE.md" if doc_type == "design" else "BEHAVIOR_TEMPLATE.md"
    template_path = Path(settings.knowledge_base_path) / "templates" / template_name
    try:
        template_content = template_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Cannot read {template_name}: {exc}",
        ) from exc

    doc_label = "DESIGN.md" if doc_type == "design" else "BEHAVIOR.md"

    if doc_type == "design":
        system_prompt = (
            "Si ICC Architect AI. Tvoja odpoveď = kompletný obsah DESIGN.md dokumentu v Markdown."
            " Žiadny úvod, žiadne vysvetlenie, žiadny popis — iba čistý Markdown od prvého riadku.\n\n"
            "POVINNÉ PRAVIDLÁ — dodržuj presne:\n"
            "- Výstup musí byť validný Markdown podľa šablóny\n"
            "- Vypln VŠETKY sekcie šablóny — žiadne placeholder texty, žiadne [ARCHITECT] značky\n"
            "- Jazyk: angličtina (technický dokument pre vývojárov)\n"
            "- Tech stack: FastAPI (sync def), SQLAlchemy 2.0 SYNC, pg8000"
            " (NIKDY asyncpg/psycopg2), PostgreSQL 16, React+TS+Tailwind\n\n"
            "ICC PORT REGISTRY — KRITICKÉ:\n"
            "- Všetky porty MUSIA byť v rozsahu 9100–9299\n"
            "- Ak špecifikácia neuvádza konkrétne porty, priraď z rozsahu 9170–9199\n"
            "- Port 5432 je ZAKÁZANÝ ako host-mapped port (je to container-internal port)\n"
            "- DATABASE_URL používa db:5432 (interný), Section 1.1 uvádza host port (napr. 9171)\n"
            "- Pridaj komentár '# container-internal: db:5432' vedľa DATABASE_URL\n\n"
            "KONZISTENCIA — KRITICKÉ:\n"
            "- FK definície MUSIA byť identické v Section 3.3 aj Section 5\n"
            "- Ak AuditLog nemá FK 'by design', uveď to TAK v Section 3.3 aj Section 5\n"
            "- Unique constraints v Section 5 musia zodpovedať business rules v Section 4\n"
            "- Open questions (Q-xx) len ak existuje dedikovaná sekcia s ich registrom\n"
            "- FIELD KONZISTENCIA: každé pole deklarované v Section 3.3 pre entitu MUSÍ"
            " byť prítomné v Section 5 tabuľke tej istej entity (vrátane created_at,"
            " updated_at) — žiadne pole nesmie byť v jednej sekcii a chýbať v druhej\n\n"
            "PORT DOKUMENTÁCIA:\n"
            "- Port 5173 (Vite dev server) je povolená výnimka — v Section 1.1 pridaj"
            " poznámku '(dev-only, not in ICC registry)' za tento port\n\n"
            "OPEN QUESTIONS REGISTER:\n"
            "- Q-xx register môže obsahovať aj vyriešené otázky (Status: Resolved)"
            " ak sa týkajú business alebo technických rozhodnutí\n"
            "- ZAKÁZANÉ v Q-xx: administratívne poznámky o prečíslovaní a redakčné záznamy"
            " o štruktúre dokumentu (napr. 'táto otázka bola prečíslovaná')\n"
            "- Q-xx číslovanie musí byť sekvenčné bez medzier\n"
            "- Ak bola otázka vyriešená, pridaj riadok 'Resolved: [dôvod]' — nikdy"
            " nevynechávaj čísla\n\n"
            "FK RIADKY V SECTION 5:\n"
            "- Správny formát: `- **FK:** field_name REFERENCES table(field) ON DELETE ACTION`\n"
            "- BEZ backtick okolo názvov polí — nie `field_name` ale field_name\n"
            "- `**FK:**` má dvojbodku VNÚTRI bold markera — párový uzatvárací `**` je povinný\n"
            "- Skontroluj každý FK riadok pred dokončením — nezatvorený `**` je Markdown chyba\n\n"
            "FK CONSTRAINT KONZISTENCIA — KRITICKÉ:\n"
            "- ON DELETE SET NULL vyžaduje NULLABLE stĺpec — nikdy NOT NULL\n"
            "- ON DELETE RESTRICT alebo CASCADE: stĺpec môže byť NOT NULL\n"
            "- Pravidlo: ak FK má ON DELETE SET NULL → stĺpec MUSÍ byť UUID NULL\n"
            "- Pravidlo: ak stĺpec je NOT NULL → FK MUSÍ mať ON DELETE RESTRICT alebo CASCADE\n"
            "- Skontroluj každý FK v Section 3.3 aj Section 5 pred dokončením\n\n"
            "CREDENTIALS V SECTION 9 A SECTION 10:\n"
            "- Každý placeholder credential (password, SECRET_KEY, API key, connection string"
            " s heslom) musí mať komentár '# REPLACE IN PRODUCTION'\n"
            "- DATABASE_URL a iné URLs s embedded heslom: '# REPLACE IN PRODUCTION'"
            " musí byť na TOM ISTOM RIADKU ako URL — nie na pokračovacom riadku\n"
            "- '# REPLACE IN PRODUCTION' patrí LEN k premenným s citlivou hodnotou —"
            " voliteľné vývojové vars bez hesla (napr. TEST_DATABASE_URL ak nemá citlivú"
            " hodnotu) ho nepotrebujú\n\n"
            "POSTGRESQL CONTAINER VARS — POVINNÉ V SECTION 10:\n"
            "- Section 10.1 (env vars tabuľka) aj 10.2 (.env template) MUSIA obsahovať:\n"
            "  POSTGRES_USER=...  # REPLACE IN PRODUCTION\n"
            "  POSTGRES_PASSWORD=...  # REPLACE IN PRODUCTION\n"
            "  POSTGRES_DB=...  # REPLACE IN PRODUCTION\n"
            "- Bez týchto vars PostgreSQL Docker container zlyhá pri štarte\n\n"
            "ICC-STANDARD MARKERY:\n"
            "- [ICC-STANDARD] markery NESMÚ byť v texte headingov\n"
            "- Ak šablóna má '## Section N: Name [ICC-STANDARD]',"
            " výstup musí byť '## Section N: Name' — marker odstráň\n"
            "- [ARCHITECT] a podobné markery musia byť kompletne odstránené z výstupu\n\n"
            "DEFERRED ITEMS KONZISTENCIA:\n"
            "- Každý cross-reference W-xx v texte dokumentu MUSÍ existovať"
            " ako riadok v Deferred Items tabuľke\n"
            "- Pred dokončením dokumentu skontroluj všetky W-xx referencie\n\n"
            "YAML KOMENTÁRE:\n"
            "- YAML inline komentáre: vždy na TOM ISTOM riadku ako hodnota:"
            " 'KEY: value  # komentár'\n"
            "- Komentár na pokračovacom riadku je syntaktická chyba v YAML\n\n"
            "TABUĽKY V SECTION 5:\n"
            "- Každý riadok tabuľky musí mať PRESNE rovnaký počet stĺpcov ako header riadok\n"
            "- Pred dokončením každej tabuľky over INTERNE (nevypisuj overenie) že každý"
            " riadok má rovnaký počet stĺpcov ako header — ak nájdeš nesúlad, oprav riadok\n"
            "- Hodnota stĺpca nesmie obsahovať obsah iného stĺpca\n"
            "- Obzvlášť náchylné: riadky s číselnými hodnotami, decimal typmi,"
            " dlhými názvami stĺpcov\n\n"
            f"ŠABLÓNA:\n\n{template_content}"
        )
    else:
        system_prompt = (
            "Si ICC Architect AI. Tvoja odpoveď = kompletný obsah BEHAVIOR.md dokumentu v Markdown."
            " Žiadny úvod, žiadne vysvetlenie, žiadny popis — iba čistý Markdown od prvého riadku.\n\n"
            "POVINNÉ PRAVIDLÁ — dodržuj presne:\n"
            "- Výstup musí byť čistý Markdown — BEZ akýchkoľvek <!-- komentárov --> zo šablóny\n"
            "- BEZ textových markerov v headingoch: '⚠️ MANDATORY — do not remove or defer',"
            " '[ICC-STANDARD]', '[ARCHITECT]' a podobné — tieto sú pokyny pre autora, nie obsah\n"
            "- Všetky {placeholder} hodnoty MUSIA byť nahradené reálnym obsahom\n"
            "- Všetky example_* anchory MUSIA byť premenované na reálne názvy z projektu\n"
            "- Vypln VŠETKY sekcie: Actors, Entry Points, Workflows, Edge Cases,"
            " State Machines, Business Rules, Error Taxonomy, Glossary\n"
            "- Workflow 3.1 MUSÍ byť user_login — blocker EPIC-1, nesmie byť vynechaný\n"
            "- Jazyk: slovenčina pre user-facing texty, angličtina pre technické identifikátory\n"
            "- Každý workflow musí mať: Precondition + Steps (tabuľka) + Postcondition\n"
            "- Minimálne 10 workflows (happy paths) + 8 edge cases\n"
            "- Všetky {premenné} v runtime message templates musia mať opisný názov"
            " (napr. {obrat_firmy_eur}) — jednopísmenové premenné ako {X} sú zakázané\n"
            "- Ak workflow popisuje odoslanie e-mailu alebo externej notifikácie, Section 8"
            " musí obsahovať 'Infrastructure dependency:' riadok s popisom potrebnej služby\n"
            "- Slovenská gramatika: skontroluj pádovú zhodu — inštrumentál ('nenulovým"
            " zostatkom', nie 'nenulový zostatkom'), genitív ('priradenej firme', nie"
            " 'priradenou firmy')\n\n"
            "CROSS-REFERENCIE — KRITICKÉ:\n"
            "- [[workflow:X]] použi LEN ak X je definovaný ako workflow v Section 3\n"
            "- [[edge:X]] použi LEN ak X je definovaný ako edge case v Section 4\n"
            "- Pred dokončením skontroluj každý cross-reference — nesprávny typ je chyba\n\n"
            "ERROR TAXONOMY — KRITICKÉ:\n"
            "- Každý error kód musí byť UNIKÁTNY — dva rôzne edge cases nesmú zdieľať"
            " jeden kód; ak máš viac scenárov pre rovnaký typ chyby, vytvor E101, E102 atď.\n"
            "- Každý user-facing error kód musí mať zodpovedajúci edge case v Section 4\n"
            "- Bezpečnostné správanie (rate limiting, account lockout, session expiry)"
            " popísané v Entry Points MUSÍ mať zodpovedajúci edge case v Section 4"
            " a error kód v Section 7\n\n"
            "DATA TOUCHED — POVINNÉ:\n"
            "- Každý workflow v Section 3 MUSÍ mať sekciu '**Data touched:**' — bez výnimky\n"
            "- Zahŕňa VŠETKY entity ktoré workflow čítá alebo modifikuje\n"
            "- Ak pre daný workflow existuje audit logging, [[entity:AuditLog]]"
            " MUSÍ byť zahrnutý v Data touched\n\n"
            "ANCHOR FORMÁT:\n"
            "- Používaj VÝLUČNE [[typ:meno]] formát (dvojité hranaté závorky,"
            " bez medzery za dvojbodkou)\n"
            "- Zakázaný formát: {{typ: meno}} — kučeravé závorky sú zakázané\n\n"
            "BUSINESS RULES ŠTRUKTÚRA:\n"
            "- Všetky pravidlá v Section 6 vrátane kalkulačných BC-xx MUSIA mať:"
            " Constraint, Dôvod, Enforced at, Porušenie\n"
            "- Pre kalkulačné pravidlá kde zlyhanie nie je user-visible:"
            " Porušenie = 'N/A — pure computation'\n\n"
            "INTERNÉ KÓDY:\n"
            "- Ak workflow kroky referencujú interné kódy (BC-xx, formula kódy a pod.),"
            " tieto musia byť definované v dokumente — inak ich nahraď popisným textom\n\n"
            "OPEN QUESTIONS REGISTER:\n"
            "- Q-xx číslovanie musí byť sekvenčné bez medzier\n"
            "- Ak Q-xx číslo bolo zrušené alebo zlúčené, musí ostať v registri"
            " s poznámkou 'Zrušené: [dôvod]' alebo 'Zlúčené s Q-yy'\n"
            "- Externé kódy (F-xxx z Professional Specification) v Q-xx musia mať"
            " anotáciu '[Professional Spec F-xxx]' pre jasnosť\n\n"
            "METADÁTA:\n"
            "- Všetky metadatové polia (Verzia, Komplementárny k, Dátum) musia byť vyplnené\n"
            "- Ak verzia komplementárneho dokumentu nie je známa, uveď 'TBD'"
            " — NIKDY nie 'v(bude priradené)' alebo podobné\n\n"
            f"ŠABLÓNA:\n\n{template_content}"
        )

    user_prompt = (
        f"Vypíš kompletný obsah {doc_label} dokumentu podľa šablóny a pravidiel."
        f" Začni priamo prvým riadkom dokumentu — '# {doc_label} —'."
        f" Žiadny text pred týmto riadkom.\n\n"
        f"PROFESIONÁLNA ŠPECIFIKÁCIA:\n{prof_spec.content}"
    )

    project_id = prof_spec.project_id

    async def _sse_generator():
        full_content: list[str] = []
        error_occurred = False
        try:
            async for chunk in claude_subprocess.run_claude_stream(
                prompt=user_prompt,
                context=system_prompt,
                timeout=settings.claude_design_doc_timeout,
            ):
                full_content.append(chunk)
                yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"
        except (RuntimeError, TimeoutError) as exc:
            error_occurred = True
            logger.error("Claude stream error for prof_spec %s: %s", spec_id, exc)
            yield f"data: {json.dumps({'type': 'error', 'content': str(exc)})}\n\n"

        # Strip leading whitespace (newlines/spaces before first #) — fixes minor
        # formatting artifact where Claude emits a leading space/newline before the title.
        assistant_content = "".join(full_content).lstrip("\n ")
        design_doc_id: str | None = None

        # Post-generation validation: multi-check pipeline.
        # Basic checks (prefix + length) run first; pattern checks only if basic pass.
        expected_prefix = f"# {doc_label}"
        validation_failures: list[str] = []

        if not error_occurred:
            if not assistant_content.lstrip().startswith(expected_prefix):
                validation_failures.append(f"Obsah nezačína s '{expected_prefix}'")
            elif len(assistant_content) < 5000:
                validation_failures.append(f"Obsah je príliš krátky ({len(assistant_content)} znakov, minimum 5000)")
            else:
                # Pattern checks (only after basic checks pass)
                if re.search(r"^#{1,6} .*\[ICC-STANDARD\]", assistant_content, re.MULTILINE):
                    validation_failures.append("Dokument obsahuje [ICC-STANDARD] markery v headingoch")
                if doc_type == "behavior" and "<!--" in assistant_content:
                    validation_failures.append("Dokument obsahuje HTML komentáre <!-- ... --> zo šablóny")
                if "(bude priradené)" in assistant_content:
                    validation_failures.append("Dokument obsahuje nevyplnené metadátové polia '(bude priradené)'")
                # BEHAVIOR.md: wrong anchor format {{...}} instead of [[...]]
                if doc_type == "behavior" and "{{" in assistant_content:
                    validation_failures.append("Dokument obsahuje nesprávny anchor formát '{{...}}' — použiť '[[...]]'")
                # DESIGN.md: backtick-wrapped FK field names
                if doc_type == "design" and re.search(r"\*\*FK:\*\* `", assistant_content):
                    validation_failures.append(
                        "Dokument obsahuje backtick-wrapped FK field names — použiť plain text bez backtick"
                    )

        validation_ok = not error_occurred and not validation_failures

        if validation_failures and not error_occurred:
            reason = "; ".join(validation_failures)
            logger.warning(
                "Design doc validation failed (type=%s, prof_spec=%s): %s",
                doc_type,
                spec_id,
                reason,
            )
            yield f"data: {json.dumps({'type': 'validation_error', 'content': reason})}\n\n"

        if assistant_content and validation_ok:
            persist_db = SessionLocal()
            try:
                design_doc = design_document_service.create(
                    persist_db,
                    DesignDocumentCreate(
                        project_id=project_id,
                        module_id=None,
                        doc_type=doc_type,
                        content=assistant_content,
                    ),
                )
                persist_db.commit()
                persist_db.refresh(design_doc)
                design_doc_id = str(design_doc.id)
            except Exception:
                persist_db.rollback()
                logger.exception(
                    "Failed to persist design doc (type=%s) for prof_spec %s",
                    doc_type,
                    spec_id,
                )
            finally:
                persist_db.close()

        yield f"data: {json.dumps({'type': 'done', 'content': assistant_content, 'design_doc_id': design_doc_id})}\n\n"

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete(
    "/{spec_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_professional_specification(
    spec_id: UUID,
    db: Session = Depends(get_db),
) -> Response:
    """Hard-delete a professional specification by primary key.

    ``professional_specifications`` has no inbound foreign keys — no
    other table references it — so no RESTRICT dependency check is
    required. In normal operation professional specifications are
    retained as version history (DESIGN.md §3.1 ``SpecificationPage`` /
    ``SpecificationViewer``); delete is reserved for test fixtures /
    admin redaction tooling where the generated document itself must
    go. The outbound FKs ``project_id`` (``ON DELETE CASCADE``),
    ``raw_spec_id`` (``ON DELETE CASCADE``) and ``approved_by``
    (``ON DELETE RESTRICT``) keep the row self-consistent when the
    parent rows change; deleting the specification itself is the
    explicit inverse.
    """
    try:
        professional_specification_service.delete(db, spec_id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
