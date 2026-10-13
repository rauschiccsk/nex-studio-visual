"""DEV-86 — NS Code: the files of the selected project, read-only and without secrets."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class CodeFilesRead(BaseModel):
    """Every path NS Code shows — the tree and the quick open are drawn from it."""

    #: The commit the checkout is on; ``None`` before the first commit.
    head: Optional[str] = None
    files: list[str] = []


class CodeFileRead(BaseModel):
    path: str
    size: int
    #: ``None`` for a binary file or one bigger than the cockpit shows.
    text: Optional[str] = None
    binary: bool = False
    too_big: bool = False
    #: The largest file NS Code shows, in bytes — said when ``too_big``.
    max_bytes: int


class CodeHit(BaseModel):
    path: str
    line: int
    text: str


class CodeSearchRead(BaseModel):
    query: str
    hits: list[CodeHit] = []
    #: More lines matched than are listed.
    truncated: bool = False
