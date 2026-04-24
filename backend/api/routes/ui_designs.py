"""REST + SSE router for UIDesign — Step 2B of the pipeline.

Endpoints:
  GET    /ui-designs             → list (filter: project_id)
  GET    /ui-designs/{id}        → single
  POST   /ui-designs             → create
  PATCH  /ui-designs/{id}        → update (content, html_preview, approve)
  DELETE /ui-designs/{id}        → delete
  POST   /ui-designs/{id}/chat   → AI chat SSE — returns chat_chunk + html_chunk
  POST   /ui-designs/{id}/generate → AI initial generation SSE
"""

from __future__ import annotations

import json
import logging
from typing import Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config.settings import settings
from backend.core.security import require_ri_role
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.specifications import ProfessionalSpecification
from backend.db.session import SessionLocal, get_db
from backend.schemas.pagination import PaginatedResponse
from backend.schemas.ui_design import UIDesignCreate, UIDesignRead, UIDesignUpdate
from backend.schemas.ui_design_chat_message import (
    UIDesignChatMessageCreate,
    UIDesignChatMessageRead,
)
from backend.services import claude_subprocess
from backend.services import system_setting as system_setting_service
from backend.services import ui_design as ui_design_service
from backend.services import ui_design_chat_message as chat_message_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["UI Designs"])

_CHAT_MARKER = "[SPRÁVA]"
_HTML_MARKER = "[HTML]"

# Fall back to pure chat mode after this many characters of preamble
# without either marker. Mirrors the Vývojová dokumentácia chat (see
# ``professional_specifications.py``) — Claude occasionally answers a
# non-mockup question (clarification, test echo) without emitting any
# marker, and the previous logic silently discarded everything past the
# last 8 bytes. Large enough to tolerate a legitimate preamble (``{"type"
# :"system",...}``) but small enough to surface forgotten markers
# quickly.
_PREAMBLE_FALLBACK_THRESHOLD = 256


def _notify_mockup_reload(project_id: UUID) -> None:
    """Fire-and-forget POST to the mockup server's admin channel.

    Called after every ``html_preview`` write so the dedicated per-
    project listener on ``{Project.ui_design_port}`` picks up the new
    content without needing its own DB polling loop. Failures
    (mockup container down, network blip) are logged and swallowed —
    the mockup server will rehydrate from DB the next time it
    restarts.
    """
    url = f"{settings.mockup_admin_url.rstrip('/')}/admin/reload/{project_id}"
    try:
        httpx.post(url, timeout=2.0)
    except httpx.HTTPError as exc:
        logger.warning(
            "Mockup reload notify failed for project %s: %s", project_id, exc
        )


def _map_value_error(exc: ValueError) -> HTTPException:
    message = str(exc)
    lowered = message.lower()
    if "not found" in lowered:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get("", response_model=PaginatedResponse[UIDesignRead])
def list_ui_designs(
    project_id: Optional[UUID] = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> PaginatedResponse[UIDesignRead]:
    rows = ui_design_service.list_ui_designs(db, project_id=project_id, limit=limit, offset=skip)
    total = ui_design_service.count_ui_designs(db, project_id=project_id)
    return PaginatedResponse(items=[UIDesignRead.model_validate(r) for r in rows], total=total, skip=skip, limit=limit)


@router.get("/{ui_design_id}", response_model=UIDesignRead)
def get_ui_design(ui_design_id: UUID, db: Session = Depends(get_db)) -> UIDesignRead:
    try:
        return UIDesignRead.model_validate(ui_design_service.get_by_id(db, ui_design_id))
    except ValueError as exc:
        raise _map_value_error(exc) from exc


@router.post("", response_model=UIDesignRead, status_code=status.HTTP_201_CREATED)
def create_ui_design(
    data: UIDesignCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_ri_role),
) -> UIDesignRead:
    obj = ui_design_service.create(db, data)
    db.commit()
    db.refresh(obj)
    return UIDesignRead.model_validate(obj)


@router.patch("/{ui_design_id}", response_model=UIDesignRead)
def update_ui_design(
    ui_design_id: UUID,
    data: UIDesignUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(require_ri_role),
) -> UIDesignRead:
    try:
        obj = ui_design_service.update(db, ui_design_id, data)
        db.commit()
        db.refresh(obj)
    except ValueError as exc:
        raise _map_value_error(exc) from exc

    # Notify the per-project mockup listener only when the HTML
    # actually changed on this PATCH; approval-only updates have no
    # effect on the rendered mockup and do not deserve a reload ping.
    if data.html_preview is not None:
        _notify_mockup_reload(obj.project_id)

    return UIDesignRead.model_validate(obj)


@router.delete("/{ui_design_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ui_design(
    ui_design_id: UUID,
    db: Session = Depends(get_db),
    _: User = Depends(require_ri_role),
) -> Response:
    try:
        ui_design_service.delete(db, ui_design_id)
        db.commit()
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── AI Chat SSE ───────────────────────────────────────────────────────────────

class _ChatHistoryItem(BaseModel):
    role: str
    content: str


class _ChatPayload(BaseModel):
    message: str
    current_content: str = ""
    current_html: str = ""
    history: list[_ChatHistoryItem] = []


_SYSTEM_PROMPT = (
    "Si ICC UI Dizajnér AI. Tvojou úlohou je vytvárať a iterovať HTML "
    "mockupy podnikových informačných systémov na základe schválenej "
    "vývojovej dokumentácie.\n\n"
    "FORMÁT ODPOVEDE — POVINNÝ, DODRŽUJ PRESNE:\n"
    "[SPRÁVA]\n"
    "1–3 vety v slovenčine o tom čo si zmenil alebo doplnil v mockupe "
    "(alebo prečo nemôžeš splniť požiadavku).\n\n"
    "[HTML]\n"
    "KOMPLETNÉ self-contained HTML — celý dokument od <!DOCTYPE html> po "
    "</html>. VYNECHAJ tento blok iba ak požiadavka nie je zmena mockupu "
    "(napr. otázka, potvrdenie, test) — v takom prípade odpovedaj iba v "
    "[SPRÁVA].\n\n"
    "PRAVIDLÁ PRE HTML:\n"
    "- Generuj kompletný HTML dokument (DOCTYPE, <html>, <head>, <body>).\n"
    "- Použi len inline <style> v <head> alebo style= atribúty — žiadne "
    "externé CSS knižnice, žiadne CDN linky.\n"
    "- Bez JavaScriptu — statický HTML/CSS prototype.\n"
    "- Dark theme: pozadie #0f172a (slate-950), text #e2e8f0 (slate-200), "
    "border #1e293b (slate-800), primárna farba #6366f1 (indigo-500).\n"
    "- Font: system-ui, sans-serif. Mono pre identifikátory: ui-monospace, "
    "SFMono-Regular, Menlo.\n"
    "- Výška body 100vh, flex layout (sidebar + main content).\n"
    "- Realistické slovenské dummy dáta zodpovedajúce doméne aplikácie.\n\n"
    "LAYOUT PATTERNS (použi tieto ak vývojová dokumentácia nevyžaduje iné):\n"
    "- Top bar (výška ~48 px): logo vľavo, príkazové / vyhľadávacie pole "
    "uprostred (napr. placeholder 'Napíš príkaz alebo skratku (OF)…'), "
    "user avatar vpravo.\n"
    "- Ľavý sidebar (šírka ~220 px): moduly zoskupené podľa kategórií zo "
    "špecifikácie; kategórie majú malý caps label, moduly ikonou + názvom; "
    "aktívny modul podsvietený.\n"
    "- Hlavná plocha: tabový pracovný priestor s 1–3 otvorenými tabmi; aspoň "
    "jeden tab zobrazuje obsah konkrétneho modulu (tabuľka záznamov, "
    "formulár, dashboard metriky) s realistickými dummy dátami.\n"
    "- Ak špecifikácia obsahuje maticu prístupových práv — ukáž jej náhľad "
    "v jednom tab-e alebo na samostatnej obrazovke.\n\n"
    "PRAVIDLÁ ITERÁCIE:\n"
    "- VŽDY začni odpoveď s [SPRÁVA] — nikdy nič pred tým.\n"
    "- Keď emituješ [HTML], musí obsahovať KOMPLETNÝ dokument (nie diff).\n"
    "- Meň len to čo používateľ explicitne požaduje; ostatné ponechaj.\n"
    "- Jazyk UI: slovenčina. Komentáre v HTML sú voliteľné.\n"
)


@router.post("/{ui_design_id}/chat", status_code=status.HTTP_200_OK)
async def chat_ui_design(
    ui_design_id: UUID,
    payload: _ChatPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ri_role),
):
    """Stream AI chat response — updates both chat panel and HTML preview.

    SSE events:
        data: {"type": "chat_chunk", "content": "..."}
        data: {"type": "html_chunk", "content": "..."}
        data: {"type": "done"}
        data: {"type": "error", "content": "..."}
    """
    try:
        ui_design_service.get_by_id(db, ui_design_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc

    history_parts: list[str] = []
    for item in payload.history:
        role_label = "Používateľ" if item.role == "user" else "AI"
        history_parts.append(f"[{role_label}]: {item.content}")
    history_block = "\n\n".join(history_parts)

    user_prompt = (
        (f"AKTUÁLNY POPIS UI:\n{payload.current_content}\n\n" if payload.current_content else "")
        + (f"HISTÓRIA KONVERZÁCIE:\n{history_block}\n\n" if history_block else "")
        + f"POŽIADAVKA:\n{payload.message}"
    )

    stream_timeout = system_setting_service.get_int(db, "claude_stream_timeout_seconds")

    async def _sse_generator():
        buffer = ""
        # ``preamble`` → ``chat`` on [SPRÁVA] → ``html`` on [HTML].
        # ``chat_fallback`` is entered when neither marker appears within
        # ``_PREAMBLE_FALLBACK_THRESHOLD`` bytes — the whole stream is
        # then treated as chat (no HTML update) so a markerless reply
        # (e.g. AI answering a plain question) isn't silently swallowed.
        state = "preamble"
        chat_accumulator: list[str] = []
        stream_error = False

        try:
            async for chunk in claude_subprocess.run_claude_stream(
                prompt=user_prompt,
                context=_SYSTEM_PROMPT,
                timeout=stream_timeout,
            ):
                buffer += chunk

                changed = True
                while changed:
                    changed = False

                    if state == "preamble":
                        if _CHAT_MARKER in buffer:
                            idx = buffer.index(_CHAT_MARKER)
                            buffer = buffer[idx + len(_CHAT_MARKER):]
                            state = "chat"
                            changed = True
                        elif _HTML_MARKER in buffer:
                            # AI skipped [SPRÁVA] entirely — treat the
                            # preamble as chat and jump straight to HTML.
                            idx = buffer.index(_HTML_MARKER)
                            chat_part = buffer[:idx].strip()
                            if chat_part:
                                yield f"data: {json.dumps({'type': 'chat_chunk', 'content': chat_part})}\n\n"
                                chat_accumulator.append(chat_part)
                            buffer = buffer[idx + len(_HTML_MARKER):]
                            state = "html"
                            changed = True
                        elif len(buffer) > _PREAMBLE_FALLBACK_THRESHOLD:
                            # No markers at all — treat the whole stream
                            # as a chat reply; HTML preview untouched.
                            state = "chat_fallback"
                            changed = True

                    elif state == "chat":
                        if _HTML_MARKER in buffer:
                            idx = buffer.index(_HTML_MARKER)
                            chat_part = buffer[:idx].strip()
                            if chat_part:
                                yield f"data: {json.dumps({'type': 'chat_chunk', 'content': chat_part})}\n\n"
                                chat_accumulator.append(chat_part)
                            buffer = buffer[idx + len(_HTML_MARKER):]
                            state = "html"
                            changed = True
                        else:
                            safe_len = max(0, len(buffer) - len(_HTML_MARKER))
                            if safe_len > 0:
                                yield f"data: {json.dumps({'type': 'chat_chunk', 'content': buffer[:safe_len]})}\n\n"
                                chat_accumulator.append(buffer[:safe_len])
                                buffer = buffer[safe_len:]

                    elif state == "chat_fallback":
                        if buffer:
                            yield f"data: {json.dumps({'type': 'chat_chunk', 'content': buffer})}\n\n"
                            chat_accumulator.append(buffer)
                            buffer = ""

                    elif state == "html":
                        if buffer:
                            yield f"data: {json.dumps({'type': 'html_chunk', 'content': buffer})}\n\n"
                            buffer = ""

        except (RuntimeError, TimeoutError) as exc:
            stream_error = True
            logger.error("Claude stream error for UI design chat %s: %s", ui_design_id, exc)
            yield f"data: {json.dumps({'type': 'error', 'content': str(exc)})}\n\n"

        if buffer:
            event_type = "html_chunk" if state == "html" else "chat_chunk"
            yield f"data: {json.dumps({'type': event_type, 'content': buffer.strip()})}\n\n"
            if event_type == "chat_chunk":
                chat_accumulator.append(buffer.strip())

        # Persist the turn (user + assistant) so the chat panel survives
        # navigation. Skipped on errored streams — partial logs would be
        # misleading. Uses a fresh SessionLocal because the request-scoped
        # session may already be closed by the time the SSE tail runs.
        if not stream_error:
            assistant_text = "".join(chat_accumulator).strip()
            persist_db = SessionLocal()
            try:
                chat_message_service.create(
                    persist_db,
                    UIDesignChatMessageCreate(
                        ui_design_id=ui_design_id,
                        role="user",
                        content=payload.message,
                    ),
                )
                if assistant_text:
                    chat_message_service.create(
                        persist_db,
                        UIDesignChatMessageCreate(
                            ui_design_id=ui_design_id,
                            role="assistant",
                            content=assistant_text,
                        ),
                    )
                persist_db.commit()
            except Exception:
                persist_db.rollback()
                logger.exception(
                    "Failed to persist chat messages for UIDesign %s", ui_design_id
                )
            finally:
                persist_db.close()

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Chat message history ──────────────────────────────────────────────────────


@router.get(
    "/{ui_design_id}/chat-messages",
    response_model=list[UIDesignChatMessageRead],
)
def list_chat_messages(
    ui_design_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ri_role),
) -> list[UIDesignChatMessageRead]:
    """Return every persisted chat turn for a UIDesign — drives the
    left-panel chat hydration on UIDesignPage mount. Sorted ASC by
    ``created_at`` so the FE can render top-to-bottom.
    """
    try:
        ui_design_service.get_by_id(db, ui_design_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    rows = chat_message_service.list_by_ui_design(db, ui_design_id)
    return [UIDesignChatMessageRead.model_validate(r) for r in rows]


# ── Initial generate ──────────────────────────────────────────────────────────

# The generate endpoint no longer takes profspec text from the caller
# — it pulls the latest approved Vývojová dokumentácia straight from
# the DB so the FE cannot accidentally submit a stale or truncated
# context. An empty body is accepted for forward-compat with clients
# that might want to override later.
class _GeneratePayload(BaseModel):
    pass


@router.post("/{ui_design_id}/generate", status_code=status.HTTP_200_OK)
async def generate_ui_design(
    ui_design_id: UUID,
    payload: _GeneratePayload,  # noqa: ARG001 — reserved for future overrides
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ri_role),
):
    """Stream-generate initial HTML mockup from approved Vývojová dokumentácia.

    The ``UIDesign.project_id`` points at the owning project; we fetch
    the newest ``ProfessionalSpecification`` for that project that has
    ``approved_at`` stamped and feed its full content into the AI
    prompt. Generation is refused with 422 if no approved spec exists
    — the UI Design step is gated on Krok 2A approval per the pipeline
    contract (see VersionDetailPage ``STEP_ROUTES``).

    Same SSE format as /chat — chat_chunk + html_chunk + done.
    """
    try:
        ui_design = ui_design_service.get_by_id(db, ui_design_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc

    # Load the project (for the name) and the approved Vývojová
    # dokumentácia content in a single trip so the prompt can reference
    # both without string shuffling at the call site.
    project = db.get(Project, ui_design.project_id)
    if project is None:  # defensive — FK CASCADE should prevent this
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Owning project for UIDesign {ui_design_id} not found",
        )
    profspec = (
        db.execute(
            select(ProfessionalSpecification)
            .where(ProfessionalSpecification.project_id == ui_design.project_id)
            .where(ProfessionalSpecification.approved_at.isnot(None))
            .order_by(ProfessionalSpecification.approved_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if profspec is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Najprv schváľ Vývojovú dokumentáciu (Krok 2A) — UI Design "
                "generátor potrebuje schválený obsah ako vstup."
            ),
        )

    user_prompt = (
        f"PROJEKT: {project.name}\n\n"
        "VÝVOJOVÁ DOKUMENTÁCIA (schválená, v plnom znení):\n"
        "─────────────────────────────────────────────────\n"
        f"{profspec.content}\n"
        "─────────────────────────────────────────────────\n\n"
        "ÚLOHA: Na základe celej vývojovej dokumentácie vytvor prvý HTML "
        "prototype UI tejto aplikácie.\n\n"
        "Mockup musí vizuálne pokryť KAŽDÝ funkčný modul z §3 špecifikácie "
        "— bočný panel musí obsahovať všetky aktívne moduly zoskupené "
        "podľa kategórií, hlavná plocha musí mať tabový pracovný priestor "
        "s aspoň jedným otvoreným modulom, a v hornej lište musí byť "
        "príkazové / vyhľadávacie pole pre spúšťanie modulu cez krátky "
        "identifikátor (napr. 'OF'). Použi realistické slovenské dummy "
        "dáta zodpovedajúce doméne IS (faktúry, partneri, skladové karty…).\n\n"
        "Rešpektuj aktorov a ich obmedzenia (§2): mockup zobrazuj z "
        "pohľadu Bežného používateľa (rola ha/editor), ktorý má prístup "
        "iba k niekoľkým modulom — ostatné kategórie skry."
    )

    stream_timeout = system_setting_service.get_int(db, "claude_stream_timeout_seconds")

    async def _sse_generator():
        buffer = ""
        state = "preamble"
        # Mirror of ``/chat`` — accumulate the chat portion so the
        # assistant's reply can be persisted at end of stream. The
        # initial-generate turn has no user-typed prompt, so only the
        # assistant row is stored (plus a fallback placeholder if Claude
        # produces an empty [SPRÁVA]).
        chat_accumulator: list[str] = []
        stream_error = False

        try:
            async for chunk in claude_subprocess.run_claude_stream(
                prompt=user_prompt,
                context=_SYSTEM_PROMPT,
                timeout=stream_timeout,
            ):
                buffer += chunk
                changed = True
                while changed:
                    changed = False
                    if state == "preamble":
                        if _CHAT_MARKER in buffer:
                            idx = buffer.index(_CHAT_MARKER)
                            buffer = buffer[idx + len(_CHAT_MARKER):]
                            state = "chat"
                            changed = True
                        elif _HTML_MARKER in buffer:
                            idx = buffer.index(_HTML_MARKER)
                            chat_part = buffer[:idx].strip()
                            if chat_part:
                                yield f"data: {json.dumps({'type': 'chat_chunk', 'content': chat_part})}\n\n"
                                chat_accumulator.append(chat_part)
                            buffer = buffer[idx + len(_HTML_MARKER):]
                            state = "html"
                            changed = True
                        elif len(buffer) > _PREAMBLE_FALLBACK_THRESHOLD:
                            state = "chat_fallback"
                            changed = True
                    elif state == "chat":
                        if _HTML_MARKER in buffer:
                            idx = buffer.index(_HTML_MARKER)
                            chat_part = buffer[:idx].strip()
                            if chat_part:
                                yield f"data: {json.dumps({'type': 'chat_chunk', 'content': chat_part})}\n\n"
                                chat_accumulator.append(chat_part)
                            buffer = buffer[idx + len(_HTML_MARKER):]
                            state = "html"
                            changed = True
                        else:
                            safe_len = max(0, len(buffer) - len(_HTML_MARKER))
                            if safe_len > 0:
                                yield f"data: {json.dumps({'type': 'chat_chunk', 'content': buffer[:safe_len]})}\n\n"
                                chat_accumulator.append(buffer[:safe_len])
                                buffer = buffer[safe_len:]
                    elif state == "chat_fallback":
                        if buffer:
                            yield f"data: {json.dumps({'type': 'chat_chunk', 'content': buffer})}\n\n"
                            chat_accumulator.append(buffer)
                            buffer = ""
                    elif state == "html":
                        if buffer:
                            yield f"data: {json.dumps({'type': 'html_chunk', 'content': buffer})}\n\n"
                            buffer = ""
        except (RuntimeError, TimeoutError) as exc:
            stream_error = True
            logger.error("Claude generate error for UI design %s: %s", ui_design_id, exc)
            yield f"data: {json.dumps({'type': 'error', 'content': str(exc)})}\n\n"

        if buffer:
            event_type = "html_chunk" if state == "html" else "chat_chunk"
            yield f"data: {json.dumps({'type': event_type, 'content': buffer.strip()})}\n\n"
            if event_type == "chat_chunk":
                chat_accumulator.append(buffer.strip())

        # Persist the assistant turn so the chat panel survives navigation
        # after the first mockup generation. Uses a fresh SessionLocal —
        # the request-scoped ``db`` may be closed by the time the SSE tail
        # runs. Empty / errored streams are skipped so the chat log isn't
        # seeded with misleading rows. If the AI emitted a clean [HTML]
        # block but no [SPRÁVA] body, we still store a short placeholder
        # so the rehydrated chat doesn't appear empty after reload.
        if not stream_error:
            assistant_text = (
                "".join(chat_accumulator).strip()
                or "Základný mockup vygenerovaný."
            )
            persist_db = SessionLocal()
            try:
                chat_message_service.create(
                    persist_db,
                    UIDesignChatMessageCreate(
                        ui_design_id=ui_design_id,
                        role="assistant",
                        content=assistant_text,
                    ),
                )
                persist_db.commit()
            except Exception:
                persist_db.rollback()
                logger.exception(
                    "Failed to persist generate chat message for UIDesign %s",
                    ui_design_id,
                )
            finally:
                persist_db.close()

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
