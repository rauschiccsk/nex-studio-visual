#!/usr/bin/env python3
"""Render a CI .env from .env.example for the CI `migrate` job (CR-3 / H3).

Purpose
-------
The CI `migrate` job boots the compose `db` service and runs the `migrate`
service (`alembic upgrade head`) against a REAL Postgres. That exercises the
actual deployed migrate path — in particular it proves the *rendered*
`DATABASE_URL` carries a working driver (`postgresql+pg8000://`). A bare scheme
(`postgresql://`) would silently fall back to psycopg2 and crash with
`ModuleNotFoundError` — the dogfood bug this gate catches.

Contract
--------
- The `DATABASE_URL` **scheme is preserved verbatim** (e.g. `postgresql+pg8000`).
  We never hardcode it; whatever `.env.example` declares is what gets exercised.
- Connection target is rewritten to match the compose `db` service:
    * host      -> `db`           (compose service name on the project network)
    * password  -> `ci`           (matches DB_PASSWORD / POSTGRES_PASSWORD below)
    * user      -> kept from .env.example
    * dbname    -> kept from .env.example
  The compose `db` service hardcodes `POSTGRES_USER`/`POSTGRES_DB` (no env_file),
  so the user/dbname MUST stay as the example's values or auth fails. Only the
  password is a placeholder (`CHANGE_ME`) that has to become a real value.
- `DB_PASSWORD=ci` (drives the compose `db` service `POSTGRES_PASSWORD`) and
  `POSTGRES_PASSWORD=ci` are emitted so the db service auth matches the URL.
- Every other line from `.env.example` is copied verbatim (the remaining
  placeholder secrets are non-empty strings, which satisfies pydantic and is
  irrelevant to the migrate path).

Usage
-----
    python scripts/ci_render_dotenv.py            # .env.example -> .env
    python scripts/ci_render_dotenv.py SRC DST    # explicit paths
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

CI_PASSWORD = "ci"
CI_HOST = "db"


def _rewrite_database_url(value: str) -> str:
    """Rewrite host + password while preserving scheme, user, port, and dbname."""
    parts = urlsplit(value)

    # Keep the user (compose `db` provisions exactly this role) and the port.
    username = parts.username or ""
    port = f":{parts.port}" if parts.port is not None else ""

    userinfo = f"{username}:{CI_PASSWORD}" if username else CI_PASSWORD
    netloc = f"{userinfo}@{CI_HOST}{port}"

    # urlunsplit keeps `scheme` (incl. the `+pg8000` driver) byte-for-byte.
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def render(src: Path, dst: Path) -> None:
    out: list[str] = []
    seen_postgres_password = False

    for raw in src.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if stripped.startswith("DATABASE_URL="):
            key, _, val = raw.partition("=")
            out.append(f"{key}={_rewrite_database_url(val)}")
        elif stripped.startswith("DB_PASSWORD="):
            out.append(f"DB_PASSWORD={CI_PASSWORD}")
        elif stripped.startswith("POSTGRES_PASSWORD="):
            out.append(f"POSTGRES_PASSWORD={CI_PASSWORD}")
            seen_postgres_password = True
        else:
            out.append(raw)

    if not seen_postgres_password:
        out.append(f"POSTGRES_PASSWORD={CI_PASSWORD}")

    dst.write_text("\n".join(out) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    src = Path(argv[1]) if len(argv) > 1 else Path(".env.example")
    dst = Path(argv[2]) if len(argv) > 2 else Path(".env")
    if not src.exists():
        print(f"error: source not found: {src}", file=sys.stderr)
        return 1
    render(src, dst)
    print(f"rendered CI dotenv: {src} -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
