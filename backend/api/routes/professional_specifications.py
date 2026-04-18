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

    async def _sse_generator():
        buffer = ""
        state = "preamble"  # → "chat" after [SPRÁVA] → "spec" after [SPEC]

        try:
            async for chunk in claude_subprocess.run_claude_stream(
                prompt=user_prompt,
                context=system_prompt,
            ):
                buffer += chunk

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
                        elif len(buffer) > len(_CHAT_MARKER) * 3:
                            # Discard preamble noise, keep tail for partial marker
                            buffer = buffer[-len(_CHAT_MARKER) :]

                    elif state == "chat":
                        if _SPEC_MARKER in buffer:
                            idx = buffer.index(_SPEC_MARKER)
                            chat_part = buffer[:idx].strip()
                            if chat_part:
                                yield f"data: {json.dumps({'type': 'chat_chunk', 'content': chat_part})}\n\n"
                            buffer = buffer[idx + len(_SPEC_MARKER) :]
                            state = "spec"
                            changed = True
                        else:
                            # Emit safe portion, hold potential partial marker tail
                            safe_len = max(0, len(buffer) - len(_SPEC_MARKER))
                            if safe_len > 0:
                                yield f"data: {json.dumps({'type': 'chat_chunk', 'content': buffer[:safe_len]})}\n\n"
                                buffer = buffer[safe_len:]

                    elif state == "spec":
                        if buffer:
                            yield f"data: {json.dumps({'type': 'spec_chunk', 'content': buffer})}\n\n"
                            buffer = ""

        except (RuntimeError, TimeoutError) as exc:
            logger.error("Claude stream error for spec chat %s: %s", spec_id, exc)
            yield f"data: {json.dumps({'type': 'error', 'content': str(exc)})}\n\n"

        # Flush remaining buffer
        if buffer:
            event_type = "spec_chunk" if state == "spec" else "chat_chunk"
            yield f"data: {json.dumps({'type': event_type, 'content': buffer.strip()})}\n\n"

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
            "- Open questions (Q-xx) len ak existuje dedikovaná sekcia s ich registrom\n\n"
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
            "- Minimálne 10 workflows (happy paths) + 8 edge cases\n\n"
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

        assistant_content = "".join(full_content)
        design_doc_id: str | None = None

        # Post-generation validation: content must start with the expected heading
        # and be at least 5000 chars (summary/description outputs are typically <3000).
        expected_prefix = f"# {doc_label}"
        validation_ok = (
            not error_occurred
            and len(assistant_content) >= 5000
            and assistant_content.lstrip().startswith(expected_prefix)
        )

        if not validation_ok and not error_occurred:
            reason = (
                f"Obsah nezačína s '{expected_prefix}'"
                if not assistant_content.lstrip().startswith(expected_prefix)
                else f"Obsah je príliš krátky ({len(assistant_content)} znakov, minimum 5000)"
            )
            logger.warning(
                "Design doc validation failed (type=%s, prof_spec=%s): %s",
                doc_type, spec_id, reason,
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
