"""``POST /client-errors`` — what a page of the cockpit crashed on, written to the backend's log (DEV-192).

23.10.2026 the cockpit had no error boundary: a page that crashed while rendering left the window white and nothing
reached the server — the Director's „prázdna stránka“ at Poradca had to be told apart from such a crash by a
screenshot. The page now shows what happened and reports it here; the log line carries who, where and which version,
cut to a size a log can carry. Only someone signed in may write.

Called by: every page of the cockpit through its error boundary (``frontend/src/components/layout/PageErrorBoundary``).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Response, status

from backend.core.security import get_current_user
from backend.db.models.foundation import User
from backend.schemas.client_error import ClientErrorReport

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Chyby stránok"])

#: How much of the message and of the stack the log keeps.
MESSAGE_KEPT = 1_000
STACK_KEPT = 8_000
PATH_KEPT = 500


def _cut(text: str, kept: int) -> str:
    return text if len(text) <= kept else f"{text[:kept]}… (skrátené)"


@router.post("/client-errors", status_code=status.HTTP_204_NO_CONTENT)
def report_client_error(report: ClientErrorReport, current_user: User = Depends(get_current_user)) -> Response:
    logger.warning(
        "page crashed: user=%s path=%s version=%s message=%s\n%s",
        current_user.username,
        _cut(report.path, PATH_KEPT),
        _cut(report.version, 50),
        _cut(report.message, MESSAGE_KEPT),
        _cut(report.stack, STACK_KEPT),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
