"""Požiadavka → koncept novej verzie (ICCINT-167).

Z požiadavky na zmenu (dnes z odpovede Poradcu — :mod:`backend.services.poradca.handoff`) vznikne
``REQ-N`` v zásobníku projektu a ďalšia verzia ako KONCEPT (``planned``, bez ``PipelineState``, bez
stavby), s požiadavkou priradenou k nej a zadaním verzie z jej textu. Stavbu nikdy nespustí — Manažér
verziu otvorí a začne sám. Pôvodne to bol most z Konzultácie na hotovej verzii; tú nahradil Poradca.

Synchronous; ``flush()`` only — commit is the router's job (mirrors the version/backlog services).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.schemas.backlog import BacklogItemCreate
from backend.schemas.version import VersionCreate
from backend.services import backlog as backlog_service
from backend.services import version as version_service

#: DB column caps (backlog_items.title / versions.name) — clamp the request-derived strings to fit.
_REQ_TITLE_MAX = 500
_VERSION_NAME_MAX = 255


def _clamp(text: str, limit: int) -> str:
    """Trim + hard-cap ``text`` to ``limit`` chars (…-suffixed) so it fits a column without an IntegrityError."""
    text = " ".join(text.split()).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def mint_from_request(db: Session, *, project_id: UUID, summary: str, title: str, user_id: UUID):
    """Požiadavka → ``REQ-N`` v zásobníku + ďalšia verzia ako KONCEPT so zadaním z požiadavky.

    Koncept bez stavby, požiadavka v zásobníku priradená k nej a zadanie verzie z textu požiadavky (ICCINT-167).
    Stavbu spustí Manažér sám, keď verziu otvorí. Vráti ``(položka zásobníka, nová verzia)``; ``flush`` len,
    commit robí volajúci.
    """
    req_title = _clamp(title or summary, _REQ_TITLE_MAX)

    # (a) Record the request as a project backlog REQ-N (status='open').
    backlog_item = backlog_service.create(
        db,
        BacklogItemCreate(project_id=project_id, title=req_title, description=summary),
    )

    # (b) Mint the NEXT version in DRAFT — planned, NO PipelineState, NO build. version_service.create leaves
    # status at the DB server_default 'planned'; we never call apply_action('start') here (Part 2.3).
    next_number = version_service.suggest_next_version_number(db, project_id)
    new_version = version_service.create(
        db,
        project_id,
        VersionCreate(
            version_number=next_number,
            name=_clamp(req_title, _VERSION_NAME_MAX),
            description=summary,
        ),
        user_id,
    )

    # (c) Link the REQ to the new version (status='included') so the new version's Špecifikácia starts from it,
    # and seed its Zadanie (customer-requirements.md) so the Príprava phase reads the request when it begins.
    backlog_service.assign_to_version(db, backlog_item.id, new_version.id)
    version_service.write_zadanie(db, new_version.id, summary)
    db.flush()
    return backlog_item, new_version
