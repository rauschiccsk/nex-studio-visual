"""DEV-7 — an approved database schema reaches the Knowledge Base through the cockpit, never by hand.

02.10.2026, dedo-home: the agent wrote the schema, the Director approved it in a free-text answer, and Dedo had to
copy it into the Knowledge Base from a terminal. 09.10.2026, measured on NEX Inbox: the Knowledge Base still holds
the v0.1.0 schema from 14.05.2026 while the app runs 25 migrations — no column added since 0.2.0 is in it, although
``icc/SCHEMA_GOVERNANCE.md`` makes that document the single source of truth. Director: „Áno, rozšír DEV-7 a postav
to takto".

Pinned here (the Knowledge Base half; the pipeline half is in ``tests/test_database_schema_approval.py``):
* publishing writes ``projects/<slug>/DATABASE_SCHEMAS.md`` plus the project's row in the governance table and in
  the project index, and commits exactly those files;
* somebody else's uncommitted work in the Knowledge Base stays uncommitted and unharmed — also inside a file the
  cockpit adds a row to; the schema document itself is never written over someone's edit;
* the narrow door: a slug outside the pattern is refused, nothing is written;
* publishing the same schema again commits nothing;
* the comparison the screen and the gate read: no document, the same document, a changed one, a first one.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.services import database_schema
from backend.services.database_schema import KnowledgeBaseSchemaPublisher, SchemaPublishRefused

GOVERNANCE = """# ICC Schema Governance

## Kto schvaľuje

Výlučne **Ri (Zoltán Rausch)**.

## Kde sú schémy

| Projekt | KB cesta |
|---|---|
| NEX Inbox | `projects/nex-inbox/DATABASE_SCHEMAS.md` |
| Dedo Home | `projects/dedo-home/DATABASE_SCHEMAS.md` |

**Povinnosť:** Pri vytvorení nového softvérového projektu sa musí do tejto tabuľky pridať riadok.
"""

INDEX = """# ICC Projects — INDEX

## Active — source in `/opt/projects/<slug>/` (current STRUCTURE.md layout)

| Project | Source path | What it is |
|---|---|---|
| **nex-inbox** | `/opt/projects/nex-inbox` | PDF → Peppol BIS UBL invoice extractor. |

## Other real projects — source at `/opt/<name>[-src]/`

| Project | Source path | What it is |
|---|---|---|
| **nex-automat** | `/opt/nex-automat` | Future ERP. |
"""

SCHEMA = "# Career Asistent — Database Schemas\n\n## users\n\n| stĺpec | typ |\n|---|---|\n| id | uuid |\n"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def _kb(tmp_path: Path) -> Path:
    kb = tmp_path / "knowledge"
    (kb / "icc").mkdir(parents=True)
    (kb / "projects").mkdir()
    (kb / "infrastructure").mkdir()
    (kb / "icc" / "SCHEMA_GOVERNANCE.md").write_text(GOVERNANCE, encoding="utf-8")
    (kb / "projects" / "INDEX.md").write_text(INDEX, encoding="utf-8")
    (kb / "infrastructure" / "port-registry.yaml").write_text("next_free: 10240\n", encoding="utf-8")
    _git(kb, "init", "-q", "-b", "main")
    _git(kb, "add", "-A")
    _git(kb, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "base")
    return kb


def _publish(kb: Path, content: str = SCHEMA, slug: str = "career-asistent"):
    return KnowledgeBaseSchemaPublisher(kb).publish(
        slug=slug,
        project_name="Career Asistent",
        description="Pomocník pri hľadaní práce.\nDruhý riadok popisu.",
        source_path=f"/opt/projects/{slug}",
        content=content,
        version_number="0.1.0",
        approved_by="alex",
    )


def _committed(kb: Path) -> list[str]:
    return sorted(_git(kb, "show", "--name-only", "--format=", "HEAD").split())


def test_an_approved_schema_lands_in_the_kb_with_both_rows_and_only_its_files_are_committed(tmp_path):
    kb = _kb(tmp_path)

    result = _publish(kb)

    assert (kb / "projects/career-asistent/DATABASE_SCHEMAS.md").read_text(encoding="utf-8") == SCHEMA
    assert "| Career Asistent | `projects/career-asistent/DATABASE_SCHEMAS.md` |" in (
        kb / "icc/SCHEMA_GOVERNANCE.md"
    ).read_text(encoding="utf-8")
    assert "| **career-asistent** | `/opt/projects/career-asistent` | Pomocník pri hľadaní práce. |" in (
        kb / "projects/INDEX.md"
    ).read_text(encoding="utf-8")
    assert _committed(kb) == [
        "icc/SCHEMA_GOVERNANCE.md",
        "projects/INDEX.md",
        "projects/career-asistent/DATABASE_SCHEMAS.md",
    ]
    assert result.commit == _git(kb, "rev-parse", "HEAD").strip()
    assert result.kb_path == "projects/career-asistent/DATABASE_SCHEMAS.md"
    assert "alex" in _git(kb, "log", "-1", "--format=%s")
    # The repository is left clean: nothing staged, nothing modified — the index agrees with the new commit.
    assert _git(kb, "status", "--porcelain") == ""


def test_the_rows_go_into_their_own_tables(tmp_path):
    kb = _kb(tmp_path)

    _publish(kb)

    governance = (kb / "icc/SCHEMA_GOVERNANCE.md").read_text(encoding="utf-8").splitlines()
    row = governance.index("| Career Asistent | `projects/career-asistent/DATABASE_SCHEMAS.md` |")
    assert governance[row - 1] == "| Dedo Home | `projects/dedo-home/DATABASE_SCHEMAS.md` |"
    index = (kb / "projects/INDEX.md").read_text(encoding="utf-8").splitlines()
    row = next(i for i, line in enumerate(index) if line.startswith("| **career-asistent**"))
    assert index[row - 1].startswith("| **nex-inbox**")  # the Active table, not the older layout below it


def test_foreign_uncommitted_work_stays_uncommitted(tmp_path):
    kb = _kb(tmp_path)
    (kb / "infrastructure/port-registry.yaml").write_text("next_free: 10260\n", encoding="utf-8")
    (kb / "infrastructure/OBNOVA_SERVERA.md").write_text("rozpísané\n", encoding="utf-8")

    _publish(kb)

    assert "infrastructure/port-registry.yaml" not in _committed(kb)
    assert _git(kb, "status", "--porcelain").splitlines() == [
        " M infrastructure/port-registry.yaml",
        "?? infrastructure/OBNOVA_SERVERA.md",
    ]
    assert (kb / "infrastructure/port-registry.yaml").read_text(encoding="utf-8") == "next_free: 10260\n"


def test_a_foreign_edit_in_a_file_that_gets_a_row_is_kept_but_not_committed(tmp_path):
    kb = _kb(tmp_path)
    governance = kb / "icc/SCHEMA_GOVERNANCE.md"
    governance.write_text(GOVERNANCE + "\nRozpísaná veta niekoho iného.\n", encoding="utf-8")

    _publish(kb)

    committed = _git(kb, "show", "HEAD:icc/SCHEMA_GOVERNANCE.md")
    assert "| Career Asistent |" in committed and "Rozpísaná veta" not in committed
    on_disk = governance.read_text(encoding="utf-8")
    assert "| Career Asistent |" in on_disk and "Rozpísaná veta niekoho iného." in on_disk
    # Nothing staged: the next `git commit` of whoever owns the edit does not undo the cockpit's row.
    assert _git(kb, "diff", "--cached", "--name-only") == ""
    assert _git(kb, "status", "--porcelain").splitlines() == [" M icc/SCHEMA_GOVERNANCE.md"]


def test_a_schema_document_somebody_is_editing_is_not_written_over(tmp_path):
    kb = _kb(tmp_path)
    _publish(kb)
    head = _git(kb, "rev-parse", "HEAD")
    doc = kb / "projects/career-asistent/DATABASE_SCHEMAS.md"
    doc.write_text(SCHEMA + "| email | text |\n", encoding="utf-8")

    with pytest.raises(SchemaPublishRefused, match="neuložené zmeny"):
        _publish(kb, SCHEMA + "| name | text |\n")

    assert doc.read_text(encoding="utf-8") == SCHEMA + "| email | text |\n"
    assert _git(kb, "rev-parse", "HEAD") == head


def test_a_staged_change_in_a_target_file_is_refused(tmp_path):
    kb = _kb(tmp_path)
    (kb / "projects/INDEX.md").write_text(INDEX + "\npripravené\n", encoding="utf-8")
    _git(kb, "add", "projects/INDEX.md")
    head = _git(kb, "rev-parse", "HEAD")

    with pytest.raises(SchemaPublishRefused, match="projects/INDEX.md"):
        _publish(kb)

    assert _git(kb, "rev-parse", "HEAD") == head
    assert not (kb / "projects/career-asistent/DATABASE_SCHEMAS.md").exists()


@pytest.mark.parametrize("slug", ["../icc", "Career", "career/../../etc", ""])
def test_a_slug_outside_the_pattern_is_refused(tmp_path, slug):
    kb = _kb(tmp_path)
    head = _git(kb, "rev-parse", "HEAD")

    with pytest.raises(SchemaPublishRefused):
        _publish(kb, slug=slug)

    assert _git(kb, "rev-parse", "HEAD") == head
    assert _git(kb, "status", "--porcelain") == ""


def test_publishing_the_same_schema_again_commits_nothing(tmp_path):
    kb = _kb(tmp_path)
    first = _publish(kb)

    again = _publish(kb, SCHEMA.rstrip("\n") + "\n\n")

    assert again.commit is None
    assert _git(kb, "rev-parse", "HEAD").strip() == first.commit
    assert (kb / "icc/SCHEMA_GOVERNANCE.md").read_text(encoding="utf-8").count("career-asistent") == 1


def test_a_changed_schema_of_a_known_project_replaces_the_document_and_adds_no_rows(tmp_path):
    kb = _kb(tmp_path)
    _publish(kb)

    _publish(kb, SCHEMA + "| email | text |\n")

    assert _committed(kb) == ["projects/career-asistent/DATABASE_SCHEMAS.md"]
    assert (kb / "projects/INDEX.md").read_text(encoding="utf-8").count("**career-asistent**") == 1


def test_a_kb_that_is_not_a_repository_is_refused(tmp_path):
    kb = tmp_path / "plain"
    (kb / "icc").mkdir(parents=True)
    (kb / "projects").mkdir()

    with pytest.raises(SchemaPublishRefused, match="git"):
        _publish(kb)

    assert not (kb / "projects/career-asistent").exists()


# ── what the gate and the screen compare ───────────────────────────────────────────────────────────────────────


def _project(tmp_path: Path, doc: str | None) -> Path:
    project = tmp_path / "career-asistent"
    if doc is not None:
        (project / "docs/specs/versions/v0.1.0").mkdir(parents=True)
        (project / "docs/specs/versions/v0.1.0/DATABASE_SCHEMAS.md").write_text(doc, encoding="utf-8")
    else:
        project.mkdir()
    return project


REL = "docs/specs/versions/v0.1.0/DATABASE_SCHEMAS.md"


def test_no_version_document_is_no_change(tmp_path):
    kb = _kb(tmp_path)
    cmp = database_schema.compare(_project(tmp_path, None), REL, kb, "career-asistent")

    assert cmp.version_doc is None and cmp.changes is False


def test_a_first_schema_is_a_change(tmp_path):
    kb = _kb(tmp_path)
    cmp = database_schema.compare(_project(tmp_path, SCHEMA), REL, kb, "career-asistent")

    assert cmp.changes is True and cmp.kb_doc is None
    assert cmp.added_lines == len(SCHEMA.splitlines()) and cmp.removed_lines == 0


def test_the_same_schema_differing_only_in_trailing_space_is_no_change(tmp_path):
    kb = _kb(tmp_path)
    _publish(kb)
    doc = SCHEMA.replace("| id | uuid |", "| id | uuid |   ") + "\n\n"

    cmp = database_schema.compare(_project(tmp_path, doc), REL, kb, "career-asistent")

    assert cmp.changes is False


def test_a_changed_schema_counts_what_was_added_and_removed(tmp_path):
    kb = _kb(tmp_path)
    _publish(kb)
    doc = SCHEMA.replace("| id | uuid |", "| id | bigint |") + "| email | text |\n"

    cmp = database_schema.compare(_project(tmp_path, doc), REL, kb, "career-asistent")

    assert cmp.changes is True
    assert (cmp.added_lines, cmp.removed_lines) == (2, 1)


def test_a_project_has_a_database_when_the_kb_or_its_migrations_say_so(tmp_path):
    kb = _kb(tmp_path)
    project = _project(tmp_path, None)
    assert database_schema.project_has_database(project, kb, "career-asistent") is False

    (project / "backend/alembic/versions").mkdir(parents=True)
    (project / "backend/alembic/versions/__init__.py").write_text("", encoding="utf-8")
    assert database_schema.project_has_database(project, kb, "career-asistent") is False
    (project / "backend/alembic/versions/001_initial.py").write_text("x", encoding="utf-8")
    assert database_schema.project_has_database(project, kb, "career-asistent") is True

    other = tmp_path / "dedo-home"
    other.mkdir()
    (kb / "projects/dedo-home").mkdir()
    (kb / "projects/dedo-home/DATABASE_SCHEMAS.md").write_text(SCHEMA, encoding="utf-8")
    assert database_schema.project_has_database(other, kb, "dedo-home") is True
