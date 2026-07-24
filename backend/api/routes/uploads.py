"""Screenshot upload endpoint.

Accepts a PNG/image file via multipart form-data and saves it to the local
``uploads/`` directory.  The directory is mounted as a Docker volume so files
persist across container restarts.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, UploadFile

from backend.core.security import require_shu_or_above

router = APIRouter(
    tags=["uploads"],
    # v4.0.35: any authenticated user may upload a screenshot (a Junior needs this too). These uploads
    # carry NO project association, so there is nothing to ownership-check — just the role floor.
    dependencies=[Depends(require_shu_or_above)],
)

UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "/app/uploads"))


@router.post("/uploads/screenshot")
async def upload_screenshot(file: UploadFile) -> dict:
    """Save a screenshot PNG and return its stored filename."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    suffix = Path(file.filename or "screenshot.png").suffix or ".png"
    filename = f"{uuid.uuid4().hex}{suffix}"
    dest = UPLOAD_DIR / filename

    content = await file.read()
    dest.write_bytes(content)

    return {"filename": filename, "size": len(content)}
