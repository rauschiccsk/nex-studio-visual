"""Existence-only credential-file checker (§4 secret governance).

The migration copies only the credentials REGISTRY rows (id, title, file_path) —
the on-disk secret files under ``/opt/data/nex-studio/credentials/`` are SHARED
between v1 and v2 and are NEVER touched by the tool. Verification confirms a
copied pointer still resolves to a file, but MUST do so WITHOUT reading a single
byte of secret content.

This module therefore uses ``Path.exists()`` / ``Path.is_file()`` ONLY. It
deliberately does NOT import the credentials service (whose content API decodes
file bytes) and it never opens, returns, logs, or raises with any file content.
A secret value can never leak through here.
"""

from __future__ import annotations

from pathlib import Path


def credential_file_present(file_path: str | None) -> bool:
    """Return True iff ``file_path`` points at an existing regular file.

    Existence + is-file check ONLY — the file is never opened or read. A NULL/empty
    path, a missing file, or a non-file (directory/socket) all return False so the
    caller can emit a non-critical WARN. The content is never inspected.
    """
    if not file_path:
        return False
    p = Path(file_path)
    return p.exists() and p.is_file()
