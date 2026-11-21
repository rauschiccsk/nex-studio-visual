"""DEV-444 — a change of the schema document's prose asks no approval of Ri; a change of its structure does.

20.11.2026 Orthodox Hermenia 0.2.0: only the introduction of ``DATABASE_SCHEMAS.md`` changed (the template's version
and migrations named in it) and the Návrh screen said „Návrh mení štruktúru databázy. Oproti schválenej schéme v
Znalostnej báze pribúda 13 a ubúda 2 riadkov. Schváliť ju smie len Ri“. The comparison read the whole document line by
line. Now it compares what follows the introduction, and of it only headings, table rows, list items and code.
"""

from __future__ import annotations

from pathlib import Path

from backend.services import database_schema

REL = "docs/specs/versions/v0.2.0/DATABASE_SCHEMAS.md"
APPROVED = """# Orthodox Hermenia — Database Schemas

Databáza: `orthodox_hermenia` · Tabuľky šablóny: balík nex-app-kit **v0.15.2**.

**Verzia 0.1.0:** celá schéma prvej verzie.

## Tabuľka: stored_files

Každý súbor v trvalom zväzku.

| Stĺpec | Typ | Nullable |
|---|---|---|
| id | UUID | NO |

- PK: `id`
- INDEX `ix_stored_files_sha256` (`sha256`)

```sql
CREATE TRIGGER trg_stored_files_frozen BEFORE UPDATE ON stored_files;
```
"""


def _compare(tmp_path: Path, version_doc: str):
    kb = tmp_path / "knowledge"
    (kb / "projects" / "orthodox-hermenia").mkdir(parents=True)
    (kb / "projects" / "orthodox-hermenia" / database_schema.SCHEMA_FILENAME).write_text(APPROVED, encoding="utf-8")
    project = tmp_path / "project"
    (project / REL).parent.mkdir(parents=True)
    (project / REL).write_text(version_doc, encoding="utf-8")
    return database_schema.compare(project, REL, kb, "orthodox-hermenia")


def test_a_rewritten_introduction_and_description_change_no_structure(tmp_path):
    reworded = (
        APPROVED.replace("**v0.15.2**.", "**v0.17.0** (migrácie kit_0001 až kit_0003).")
        .replace(
            "**Verzia 0.1.0:** celá schéma prvej verzie.",
            "**Verzia 0.2.0:** schéma sa nemení.\n\nZmenila sa len hlavička.",
        )
        .replace("Každý súbor v trvalom zväzku.", "Každý súbor v trvalom zväzku `/data/files`.")
    )

    cmp = _compare(tmp_path, reworded)

    assert cmp.changes is False
    assert (cmp.added_lines, cmp.removed_lines) == (0, 0)


def test_a_new_column_is_a_change_of_structure(tmp_path):
    cmp = _compare(tmp_path, APPROVED.replace("| id | UUID | NO |", "| id | UUID | NO |\n| sha256 | CHAR(64) | NO |"))

    assert cmp.changes is True
    assert (cmp.added_lines, cmp.removed_lines) == (1, 0)


def test_a_changed_check_index_or_code_line_is_a_change_of_structure(tmp_path):
    for n, (old, new) in enumerate(
        (
            ("- PK: `id`", "- PK: `id`\n- CHECK `ck_size`: `size_bytes >= 0`"),
            ("(`sha256`)", "(`sha256`, `id`)"),
            ("BEFORE UPDATE", "BEFORE UPDATE OR DELETE"),
            ("## Tabuľka: stored_files", "## Tabuľka: files"),
        )
    ):
        assert _compare(tmp_path / str(n), APPROVED.replace(old, new)).changes is True, new
