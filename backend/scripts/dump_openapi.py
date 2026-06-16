"""Dump the FastAPI OpenAPI schema to a static JSON file for FE codegen (v0.7.0 R2, D1).

Deterministic by construction: imports the app and serialises ``app.openapi()`` — no running
server, no DB, no network — so CI can regenerate the FE contract types (``openapi-typescript``)
hermetically. ``sort_keys=True`` pins a byte-stable ordering so the downstream drift-gate compares
apples to apples; ``enum`` arrays are JSON lists (not dicts) so the meaningful member order from
the Pydantic ``Literal`` declarations is preserved.

Usage::

    poetry run python -m backend.scripts.dump_openapi [OUTPUT_PATH]

With no argument (or ``-``) the schema is written to stdout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from backend.main import app


def render() -> str:
    """Return the app's OpenAPI schema as a deterministic JSON string (trailing newline added)."""
    schema = app.openapi()
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> None:
    out = argv[1] if len(argv) > 1 else "-"
    text = render()
    if out == "-":
        sys.stdout.write(text)
    else:
        Path(out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv)
