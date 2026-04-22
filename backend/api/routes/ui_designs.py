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

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.security import require_ri_role
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.schemas.pagination import PaginatedResponse
from backend.schemas.ui_design import UIDesignCreate, UIDesignRead, UIDesignUpdate
from backend.services import claude_subprocess
from backend.services import ui_design as ui_design_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["UI Designs"])

_CHAT_MARKER = "[SPRÁVA]"
_HTML_MARKER = "[HTML]"


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
        return UIDesignRead.model_validate(obj)
    except ValueError as exc:
        raise _map_value_error(exc) from exc


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
    "Si ICC UI Dizajnér AI. Tvojou úlohou je vytvárať a upravovať HTML mockupy webových aplikácií "
    "podľa požiadaviek vývojového tímu a zákazníka.\n\n"
    "FORMÁT ODPOVEDE — POVINNÝ, DODRŽUJ PRESNE:\n"
    "[SPRÁVA]\n"
    "Tu napíš 1-3 vety v slovenčine o tom čo si zmenil alebo doplnil v mockupe.\n\n"
    "[HTML]\n"
    "Tu napíš KOMPLETNÉ self-contained HTML — celý dokument od <!DOCTYPE html> po </html>.\n\n"
    "PRAVIDLÁ PRE HTML:\n"
    "- VŽDY generuj kompletný HTML dokument (vrátane DOCTYPE, head, body)\n"
    "- Použi len inline <style> alebo style= atribúty — bez externých CSS knižníc\n"
    "- Dark theme: pozadie #0f172a (slate-950), text #e2e8f0 (slate-200), border #1e293b (slate-800)\n"
    "- Primárna farba: #6366f1 (indigo-500)\n"
    "- Základná štruktúra: sidebar (šírka 180px, pevný vľavo) + main content (flex-1)\n"
    "- Realistické slovenské dummy dáta\n"
    "- Font: system-ui, sans-serif\n"
    "- Bez JavaScriptu — len statický HTML/CSS prototype\n"
    "- Výška body 100vh, flex layout\n\n"
    "PRAVIDLÁ:\n"
    "- VŽDY začni odpoveď s [SPRÁVA] — nikdy nič pred tým\n"
    "- [HTML] musí obsahovať KOMPLETNÝ dokument, nie len zmenené časti\n"
    "- Meni len to čo používateľ požaduje, ostatné ponechaj\n"
    "- Jazyk UI: slovenčina\n"
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

    async def _sse_generator():
        buffer = ""
        state = "preamble"

        try:
            async for chunk in claude_subprocess.run_claude_stream(
                prompt=user_prompt,
                context=_SYSTEM_PROMPT,
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
                        elif len(buffer) > len(_CHAT_MARKER) * 3:
                            buffer = buffer[-len(_CHAT_MARKER):]

                    elif state == "chat":
                        if _HTML_MARKER in buffer:
                            idx = buffer.index(_HTML_MARKER)
                            chat_part = buffer[:idx].strip()
                            if chat_part:
                                yield f"data: {json.dumps({'type': 'chat_chunk', 'content': chat_part})}\n\n"
                            buffer = buffer[idx + len(_HTML_MARKER):]
                            state = "html"
                            changed = True
                        else:
                            safe_len = max(0, len(buffer) - len(_HTML_MARKER))
                            if safe_len > 0:
                                yield f"data: {json.dumps({'type': 'chat_chunk', 'content': buffer[:safe_len]})}\n\n"
                                buffer = buffer[safe_len:]

                    elif state == "html":
                        if buffer:
                            yield f"data: {json.dumps({'type': 'html_chunk', 'content': buffer})}\n\n"
                            buffer = ""

        except (RuntimeError, TimeoutError) as exc:
            logger.error("Claude stream error for UI design chat %s: %s", ui_design_id, exc)
            yield f"data: {json.dumps({'type': 'error', 'content': str(exc)})}\n\n"

        if buffer:
            event_type = "html_chunk" if state == "html" else "chat_chunk"
            yield f"data: {json.dumps({'type': event_type, 'content': buffer.strip()})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Initial generate ──────────────────────────────────────────────────────────

class _GeneratePayload(BaseModel):
    project_name: str = ""
    profspec_content: str = ""


@router.post("/{ui_design_id}/generate", status_code=status.HTTP_200_OK)
async def generate_ui_design(
    ui_design_id: UUID,
    payload: _GeneratePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ri_role),
):
    """Stream-generate initial HTML mockup from profspec context.

    Same SSE format as /chat — chat_chunk + html_chunk + done.
    """
    try:
        ui_design_service.get_by_id(db, ui_design_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc

    project_ctx = f"Projekt: {payload.project_name}\n\n" if payload.project_name else ""
    profspec_ctx = (
        f"PROFESIONÁLNA ŠPECIFIKÁCIA:\n{payload.profspec_content[:3000]}\n\n"
        if payload.profspec_content
        else ""
    )

    user_prompt = (
        f"{project_ctx}"
        f"{profspec_ctx}"
        "ÚLOHA: Vytvor základný UI prototype (HTML mockup) pre túto aplikáciu. "
        "Zahrň: sidebar s navigáciou, hlavný obsah dashboardu s kľúčovými metrikami alebo zoznamom. "
        "Použi realistické slovenské dummy dáta."
    )

    async def _sse_generator():
        buffer = ""
        state = "preamble"

        try:
            async for chunk in claude_subprocess.run_claude_stream(
                prompt=user_prompt,
                context=_SYSTEM_PROMPT,
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
                        elif len(buffer) > len(_CHAT_MARKER) * 3:
                            buffer = buffer[-len(_CHAT_MARKER):]
                    elif state == "chat":
                        if _HTML_MARKER in buffer:
                            idx = buffer.index(_HTML_MARKER)
                            chat_part = buffer[:idx].strip()
                            if chat_part:
                                yield f"data: {json.dumps({'type': 'chat_chunk', 'content': chat_part})}\n\n"
                            buffer = buffer[idx + len(_HTML_MARKER):]
                            state = "html"
                            changed = True
                        else:
                            safe_len = max(0, len(buffer) - len(_HTML_MARKER))
                            if safe_len > 0:
                                yield f"data: {json.dumps({'type': 'chat_chunk', 'content': buffer[:safe_len]})}\n\n"
                                buffer = buffer[safe_len:]
                    elif state == "html":
                        if buffer:
                            yield f"data: {json.dumps({'type': 'html_chunk', 'content': buffer})}\n\n"
                            buffer = ""
        except (RuntimeError, TimeoutError) as exc:
            logger.error("Claude generate error for UI design %s: %s", ui_design_id, exc)
            yield f"data: {json.dumps({'type': 'error', 'content': str(exc)})}\n\n"

        if buffer:
            event_type = "html_chunk" if state == "html" else "chat_chunk"
            yield f"data: {json.dumps({'type': event_type, 'content': buffer.strip()})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
