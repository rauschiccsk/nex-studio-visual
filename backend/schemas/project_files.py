"""Files the Manažér hands the AI Agent — ``private/`` of the project (DEV-44)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class PrivateFileRead(BaseModel):
    """One file in ``private/``. ``uploaded_by``/``uploaded_at`` come from the build history — empty for a file
    the agent put there itself."""

    path: str
    size_bytes: int
    size_label: str
    modified_at: datetime
    uploaded_by: Optional[str] = None
    uploaded_at: Optional[datetime] = None


class PrivateFilesRead(BaseModel):
    """What ``private/`` holds now, and the largest file that may be attached."""

    files: list[PrivateFileRead]
    max_bytes: int
    max_label: str


class PrivateFileUploaded(PrivateFilesRead):
    """The stored file, the line the screen puts into the Manažér's message, and the folder after the upload."""

    file: PrivateFileRead
    answer_line: str
