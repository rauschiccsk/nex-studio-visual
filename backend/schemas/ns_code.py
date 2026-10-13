"""DEV-86 — NS Code: the files of the selected project, read-only and without secrets."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from backend.schemas.ci_runs import CiRunTask


class CodeFilesRead(BaseModel):
    """Every path NS Code shows — the tree and the quick open are drawn from it."""

    #: The commit the checkout is on; ``None`` before the first commit.
    head: Optional[str] = None
    files: list[str] = []
    #: DEV-87 — ``{path: "M"|"U"}``: changed against the last commit, or new.
    changes: dict[str, str] = {}
    #: DEV-87 — ``{path: stamp}``; an open file whose stamp moved is fetched again.
    stamps: dict[str, str] = {}
    #: How often the screen asks again.
    refresh_seconds: int


class CodeLastCommit(BaseModel):
    """DEV-87 — the newest commit that changed the file, with its task when the subject names one."""

    sha: str
    at: str
    subject: str
    task: Optional[CiRunTask] = None


class CodeFileRead(BaseModel):
    path: str
    size: int
    #: ``None`` for a binary file or one bigger than the cockpit shows.
    text: Optional[str] = None
    binary: bool = False
    too_big: bool = False
    #: The largest file NS Code shows, in bytes — said when ``too_big``.
    max_bytes: int
    stamp: str = ""
    last_commit: Optional[CodeLastCommit] = None


class CodeOriginalRead(BaseModel):
    """DEV-87 — the file as it was, to compare with: at the last commit, or where the version began."""

    path: str
    #: ``head`` · ``base``
    against: str
    #: The commit compared with.
    rev: str
    #: ``False`` = the file did not exist then (it is new since).
    existed: bool
    text: Optional[str] = None
    binary: bool = False
    too_big: bool = False


class CodeHit(BaseModel):
    path: str
    line: int
    text: str


class CodeSearchRead(BaseModel):
    query: str
    hits: list[CodeHit] = []
    #: More lines matched than are listed.
    truncated: bool = False
