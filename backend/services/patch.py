"""Apply a partial update (PATCH) to a model row (DEV-323).

A field that was sent is written. A null that was sent clears an optional field — its column may be empty — and
leaves a required one as it is; a field that was not sent stays. The screen sends an emptied field as null, so
reading null as "leave it" made optional fields impossible to clear (the surname „Test“ the Director could not
remove in Nastavenia → Používatelia).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import inspect


def apply_changes(row: Any, changes: dict[str, Any], allowed: set[str]) -> None:
    """Write ``changes`` (``model_dump(exclude_unset=True)``) onto ``row``, only the ``allowed`` fields."""
    columns = inspect(type(row)).columns
    for field, value in changes.items():
        if field not in allowed:
            continue
        if value is None and not columns[field].nullable:
            continue
        setattr(row, field, value)
