"""The kind of a refused request, told by its type — never by the words of its message (DEV-385).

14.11.2026: the routes decided between 404, 409 and 400 by English words in an error's text („not found“, „already
started“), so the text people read could not be Slovak without breaking the status the screen acts on. Director, on
turning the cockpit's English error texts Slovak: „Áno založ samostatný tiket a implementuj.“ An error says what kind
it is by its class; its message is free to say it in the language of the person who reads it.
"""

from __future__ import annotations

from fastapi import HTTPException, status


class NotFoundError(ValueError):
    """What was asked for does not exist — HTTP 404."""


class ConflictError(ValueError):
    """The request collides with the state it found (it already happened) — HTTP 409."""


class ForbiddenError(ValueError):
    """The person may not do this — HTTP 403."""


def http_error(exc: ValueError, *, default: int = 422) -> HTTPException:
    """A service's refusal as the screen reads it (DEV-386): 404 / 409 / 403 by the error's type, anything else the
    route's own default — never by the words of the message, which is Slovak and free to change."""
    if isinstance(exc, NotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ConflictError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    if isinstance(exc, ForbiddenError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    return HTTPException(status_code=default, detail=str(exc))
