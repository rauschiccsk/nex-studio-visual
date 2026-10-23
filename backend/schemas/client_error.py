"""What a page of the cockpit reports when it crashed while rendering (DEV-192)."""

from __future__ import annotations

from pydantic import BaseModel


class ClientErrorReport(BaseModel):
    message: str
    stack: str = ""
    #: The page with its query, as the browser had it.
    path: str = ""
    #: The frontend's version.
    version: str = ""
