"""DEV-92 — the cockpit's backend carries no dead code: no function, class, method or property nobody calls.

12.10.2026 the Director asked for a complete check that removes the old, unneeded „dead“ code of the cockpit. The
cleanup removed what nobody called; this keeps new dead code from coming back.

vulture reads the backend together with its tests, the migrations and the scripts — a function only a test calls counts
as used (removing it is a decision of its own). What a library calls by itself is not dead: FastAPI routes, pydantic
validators and SQLAlchemy listeners are called through their decorators, a standard-library request handler through its
method names. Variables and attributes are not judged: pydantic fields and model columns are read by the libraries.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import vulture

ROOT = Path(__file__).resolve().parents[1]

#: Where code lives and where it is called from.
SCANNED = ["backend", "tests", "conftest.py", "migrations", "scripts"]

#: Called through a decorator, never by name.
CALLED_BY_DECORATOR = [
    "@router.*",
    "@app.*",
    "@*.validator",
    "@*field_validator",
    "@*model_validator",
    "@*field_serializer",
    "@*model_serializer",
    "@pytest.fixture",
    "@event.listens_for",
    "@*.on_event",
    "@*.exception_handler",
    "@*.middleware",
]

#: Called by name by a library or a framework.
CALLED_BY_NAME = [
    "test_*",  # pytest
    "Test*",  # pytest
    "upgrade",  # alembic
    "downgrade",  # alembic
    "do_GET",  # http.server — the test servers that play GitHub or a deployed app
    "do_POST",
    "do_HEAD",
    "log_message",
]

#: What is judged. Variables and attributes are left out on purpose (see the module's docstring).
JUDGED = {"function", "class", "method", "property", "import", "unreachable_code"}

#: vulture's own scale: 60 % is the lowest it gives a function nobody calls.
MIN_CONFIDENCE = 60


def dead_code(paths: list[Path]) -> list[str]:
    """``file:line kind name`` of every judged piece of code nobody calls."""
    scan = vulture.Vulture(ignore_names=CALLED_BY_NAME, ignore_decorators=CALLED_BY_DECORATOR)
    scan.scavenge([str(p) for p in paths])
    found = []
    for item in scan.get_unused_code(min_confidence=MIN_CONFIDENCE):
        if item.typ not in JUDGED:
            continue
        where = Path(item.filename)
        where = where.relative_to(ROOT) if where.is_relative_to(ROOT) else where
        found.append(f"{where}:{item.first_lineno} {item.typ} {item.name}")
    return sorted(found)


def test_the_backend_has_no_dead_code():
    found = dead_code([ROOT / p for p in SCANNED])

    assert found == [], "nobody calls these — remove them, or name what calls them here:\n  " + "\n  ".join(found)


def test_the_check_finds_dead_code_and_leaves_called_code(tmp_path):
    """⚠️ The guard over the guard: a check that never finds anything is green in a burning house."""
    (tmp_path / "module.py").write_text(
        textwrap.dedent(
            """
            from fastapi import APIRouter

            router = APIRouter()


            def used():
                return 1


            def forgotten():
                return used()


            @router.get("/x")
            def route():
                return used()
            """
        ),
        encoding="utf-8",
    )

    found = dead_code([tmp_path])

    assert [line.split(" ", 1)[1] for line in found] == ["function forgotten"]
