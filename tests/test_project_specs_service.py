"""Unit tests for :mod:`backend.services.project_specs`.

Service-layer concerns:
- Slug validation
- Filesystem-realistic discovery under ``/opt/projects/<slug>/docs/``
  (all file types + empty directories — Director directive 2026-05-14)
- Hidden directory skip (``.git``, ``__pycache__``, ...)
- Path-traversal prevention in ``read_content`` / ``write_content``
- Text vs binary read semantics
- Edit restricted to ``.md`` (write/edit is a Markdown-only operation;
  non-Markdown viewing is supported but not editing)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.services import project_specs as svc
from backend.services.project_specs import ProjectSpecsError


@pytest.fixture()
def fake_projects_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect PROJECTS_ROOT to a tmp_path tree for the duration of a test."""
    monkeypatch.setattr(svc, "PROJECTS_ROOT", tmp_path)
    return tmp_path


def _seed_project(root: Path, slug: str, files: dict[str, str]) -> None:
    """Materialise a project directory tree under ``root``.

    ``files`` keys are paths relative to the project root (e.g.
    ``"docs/specs/customer-requirements.md"``); values are the file
    contents.
    """
    for rel, content in files.items():
        target = root / slug / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _seed_empty_dir(root: Path, slug: str, rel: str) -> None:
    """Create an empty directory under ``root/<slug>/<rel>``."""
    (root / slug / rel).mkdir(parents=True, exist_ok=True)


# ── list_all_specs ────────────────────────────────────────────────────


def test_list_empty_when_no_projects(fake_projects_root: Path) -> None:
    assert svc.list_all_specs() == []


def test_list_skips_projects_without_docs_dir(fake_projects_root: Path) -> None:
    # Project exists but has no docs/ subdirectory.
    (fake_projects_root / "nex-other").mkdir()
    (fake_projects_root / "nex-other" / "README.md").write_text("x")

    assert svc.list_all_specs() == []


def test_list_returns_all_files_under_docs(fake_projects_root: Path) -> None:
    """All file types appear — not just ``.md``. Director directive
    2026-05-14: ``import/`` may hold CSV/XLSX inputs; the user must see
    them on the filesystem-realistic view.
    """
    _seed_project(
        fake_projects_root,
        "nex-inbox",
        {
            "docs/specs/customer-requirements.md": "vision",
            "docs/specs/versions/v0.1.0/CHANGES.md": "initial",
            "docs/audits/release.md": "audit",
            "docs/import/data.csv": "a,b\n1,2",
            "docs/export/result.json": '{"x":1}',
        },
    )
    docs = svc.list_all_specs()
    paths = [d.relative_path for d in docs]
    assert paths == [
        "nex-inbox/docs/audits/release.md",
        "nex-inbox/docs/export/result.json",
        "nex-inbox/docs/import/data.csv",
        "nex-inbox/docs/specs/customer-requirements.md",
        "nex-inbox/docs/specs/versions/v0.1.0/CHANGES.md",
    ]
    # File entries carry the right metadata + are not flagged as dirs.
    for d in docs:
        assert d.is_directory is False
        assert d.size_bytes > 0


def test_list_includes_empty_directories(fake_projects_root: Path) -> None:
    """Empty folders (e.g. freshly created ``import/`` and ``export/``)
    appear as synthetic ``is_directory=True`` entries so the user sees
    them in the tree even before adding any files.
    """
    _seed_project(
        fake_projects_root,
        "nex-inbox",
        {"docs/specs/customer-requirements.md": "vision"},
    )
    _seed_empty_dir(fake_projects_root, "nex-inbox", "docs/import")
    _seed_empty_dir(fake_projects_root, "nex-inbox", "docs/export")

    docs = svc.list_all_specs()
    by_path = {d.relative_path: d for d in docs}

    # File still appears.
    assert "nex-inbox/docs/specs/customer-requirements.md" in by_path
    assert by_path["nex-inbox/docs/specs/customer-requirements.md"].is_directory is False

    # Empty directories appear as is_directory=True with size 0.
    for empty in ("nex-inbox/docs/import", "nex-inbox/docs/export"):
        assert empty in by_path, f"missing empty dir entry: {empty}"
        assert by_path[empty].is_directory is True
        assert by_path[empty].size_bytes == 0


def test_list_does_not_emit_implicit_parent_dirs(fake_projects_root: Path) -> None:
    """A non-empty directory (because a file lives under it) must NOT
    appear as a separate ``is_directory`` entry — the frontend tree
    builder creates it implicitly from the file path. Avoids duplicate
    entries that would clutter the tree.
    """
    _seed_project(
        fake_projects_root,
        "nex-inbox",
        {
            "docs/specs/x.md": "x",
            "docs/specs/versions/v0.1.0/y.md": "y",
        },
    )
    docs = svc.list_all_specs()
    dir_entries = [d.relative_path for d in docs if d.is_directory]
    assert dir_entries == [], f"non-empty parents should be implicit; got synthetic entries: {dir_entries}"


def test_list_skips_hidden_dirs(fake_projects_root: Path) -> None:
    """``.git`` / ``__pycache__`` / ``node_modules`` etc. are skipped for
    BOTH files and empty-dir scans (not just ``.md`` files).
    """
    _seed_project(
        fake_projects_root,
        "nex-inbox",
        {
            "docs/specs/good.md": "x",
            "docs/.git/HEAD": "ref",
            "docs/.git/notes.md": "leaked",
            "docs/__pycache__/cached.md": "cached",
            "docs/node_modules/x.md": "vendor",
        },
    )
    # Plus an empty hidden directory — must also be invisible.
    _seed_empty_dir(fake_projects_root, "nex-inbox", "docs/.vscode")

    docs = svc.list_all_specs()
    paths = [d.relative_path for d in docs]
    assert paths == ["nex-inbox/docs/specs/good.md"]


def test_list_aggregates_multiple_projects_sorted(fake_projects_root: Path) -> None:
    _seed_project(fake_projects_root, "nex-zinc", {"docs/a.md": "1"})
    _seed_project(fake_projects_root, "nex-alpha", {"docs/a.md": "2"})
    _seed_project(fake_projects_root, "nex-mid", {"docs/a.md": "3"})

    docs = svc.list_all_specs()
    paths = [d.relative_path for d in docs]
    assert paths == [
        "nex-alpha/docs/a.md",
        "nex-mid/docs/a.md",
        "nex-zinc/docs/a.md",
    ]


def test_list_ignores_invalid_slug_dirs(fake_projects_root: Path) -> None:
    # Directory whose name doesn't match the slug regex must be ignored.
    bad = fake_projects_root / "Has_Underscore"
    bad.mkdir()
    (bad / "docs").mkdir()
    (bad / "docs" / "x.md").write_text("nope")

    _seed_project(fake_projects_root, "nex-ok", {"docs/y.md": "yes"})

    docs = svc.list_all_specs()
    assert [d.relative_path for d in docs] == ["nex-ok/docs/y.md"]


# ── read_content ───────────────────────────────────────────────────────


def test_read_content_md_returns_text(fake_projects_root: Path) -> None:
    _seed_project(fake_projects_root, "nex-inbox", {"docs/specs/x.md": "hello world"})
    content, is_text = svc.read_content("nex-inbox", "docs/specs/x.md")
    assert content == "hello world"
    assert is_text is True


def test_read_content_non_md_text_returns_text(fake_projects_root: Path) -> None:
    """CSV / JSON / YAML etc. are readable as text — Director can view
    contents of import/export folders without SSH.
    """
    _seed_project(
        fake_projects_root,
        "nex-inbox",
        {
            "docs/import/data.csv": "a,b\n1,2",
            "docs/export/result.json": '{"x": 1}',
            "docs/scripts/run.sh": "#!/bin/bash\necho hello",
        },
    )
    csv_content, csv_text = svc.read_content("nex-inbox", "docs/import/data.csv")
    assert csv_content == "a,b\n1,2"
    assert csv_text is True

    json_content, json_text = svc.read_content("nex-inbox", "docs/export/result.json")
    assert json_content == '{"x": 1}'
    assert json_text is True

    sh_content, sh_text = svc.read_content("nex-inbox", "docs/scripts/run.sh")
    assert "echo hello" in sh_content
    assert sh_text is True


def test_read_content_binary_returns_empty_with_flag(fake_projects_root: Path) -> None:
    """Binary files (``.pdf``, ``.xlsx``, ``.png``...) yield
    ``is_text=False`` and empty content — frontend renders a "cannot
    display" placeholder instead of garbled bytes.
    """
    bin_path = fake_projects_root / "nex-inbox" / "docs" / "export" / "report.pdf"
    bin_path.parent.mkdir(parents=True, exist_ok=True)
    bin_path.write_bytes(b"%PDF-1.7\n\x00\x01\x02 binary garbage \xff\xfe")

    content, is_text = svc.read_content("nex-inbox", "docs/export/report.pdf")
    assert content == ""
    assert is_text is False


def test_read_content_undecodable_text_falls_back_to_binary(
    fake_projects_root: Path,
) -> None:
    """A whitelisted-extension file with bytes that aren't valid UTF-8
    (legacy cp1250 ``.txt`` etc.) is reported as binary rather than
    raising — the user sees the placeholder, no 500.
    """
    txt_path = fake_projects_root / "nex-inbox" / "docs" / "legacy.txt"
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.write_bytes(b"Pri\xe1zniv\xe1 zima")  # cp1250 bytes for "Priaznivá zima"

    content, is_text = svc.read_content("nex-inbox", "docs/legacy.txt")
    assert content == ""
    assert is_text is False


def test_read_content_rejects_invalid_slug(fake_projects_root: Path) -> None:
    with pytest.raises(ProjectSpecsError, match="Invalid slug"):
        svc.read_content("BAD_SLUG", "docs/x.md")


def test_read_content_rejects_path_traversal(fake_projects_root: Path) -> None:
    _seed_project(fake_projects_root, "nex-inbox", {"docs/specs/x.md": "x"})
    with pytest.raises(ProjectSpecsError, match="traversal"):
        svc.read_content("nex-inbox", "docs/../../etc/passwd")


def test_read_content_requires_docs_prefix(fake_projects_root: Path) -> None:
    _seed_project(fake_projects_root, "nex-inbox", {"README.md": "top"})
    with pytest.raises(ProjectSpecsError, match="inside docs/"):
        svc.read_content("nex-inbox", "README.md")


def test_read_content_missing_file(fake_projects_root: Path) -> None:
    (fake_projects_root / "nex-inbox" / "docs").mkdir(parents=True)
    with pytest.raises(ProjectSpecsError, match="not found"):
        svc.read_content("nex-inbox", "docs/missing.md")


# ── write_content ──────────────────────────────────────────────────────
