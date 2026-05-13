"""Unit tests for :mod:`backend.services.project_specs`.

Service-layer concerns:
- Slug validation
- Recursive ``.md`` discovery under ``/opt/projects/<slug>/docs/``
- Hidden directory skip (``.git``, ``__pycache__``, ...)
- Path-traversal prevention in ``read_content`` / ``write_content``
- Read / write round-trips
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


# ── list_all_specs ────────────────────────────────────────────────────


def test_list_empty_when_no_projects(fake_projects_root: Path) -> None:
    assert svc.list_all_specs() == []


def test_list_skips_projects_without_docs_dir(fake_projects_root: Path) -> None:
    # Project exists but has no docs/ subdirectory.
    (fake_projects_root / "nex-other").mkdir()
    (fake_projects_root / "nex-other" / "README.md").write_text("x")

    assert svc.list_all_specs() == []


def test_list_returns_md_under_docs(fake_projects_root: Path) -> None:
    _seed_project(
        fake_projects_root,
        "nex-inbox",
        {
            "docs/specs/customer-requirements.md": "vision",
            "docs/specs/versions/v0.1.0/CHANGES.md": "initial",
            "docs/audits/release.md": "audit",
        },
    )
    docs = svc.list_all_specs()
    paths = [d.relative_path for d in docs]
    assert paths == [
        "nex-inbox/docs/audits/release.md",
        "nex-inbox/docs/specs/customer-requirements.md",
        "nex-inbox/docs/specs/versions/v0.1.0/CHANGES.md",
    ]
    # Each entry carries the correct category (parent path) + size.
    for d in docs:
        assert d.relative_path.startswith("nex-inbox/docs/")
        assert d.filename.endswith(".md")
        assert d.size_bytes > 0


def test_list_skips_hidden_dirs(fake_projects_root: Path) -> None:
    _seed_project(
        fake_projects_root,
        "nex-inbox",
        {
            "docs/specs/good.md": "x",
            "docs/.git/HEAD": "ref",  # not .md but in hidden dir
            "docs/.git/notes.md": "leaked",  # .md but in hidden dir
            "docs/__pycache__/cached.md": "cached",
            "docs/node_modules/x.md": "vendor",
        },
    )
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


def test_read_content_happy_path(fake_projects_root: Path) -> None:
    _seed_project(fake_projects_root, "nex-inbox", {"docs/specs/x.md": "hello world"})
    assert svc.read_content("nex-inbox", "docs/specs/x.md") == "hello world"


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


def test_read_content_only_md(fake_projects_root: Path) -> None:
    _seed_project(fake_projects_root, "nex-inbox", {"docs/x.json": "{}"})
    with pytest.raises(ProjectSpecsError, match=".md"):
        svc.read_content("nex-inbox", "docs/x.json")


def test_read_content_missing_file(fake_projects_root: Path) -> None:
    (fake_projects_root / "nex-inbox" / "docs").mkdir(parents=True)
    with pytest.raises(ProjectSpecsError, match="not found"):
        svc.read_content("nex-inbox", "docs/missing.md")


# ── write_content ──────────────────────────────────────────────────────


def test_write_content_overwrites_existing(fake_projects_root: Path) -> None:
    _seed_project(fake_projects_root, "nex-inbox", {"docs/specs/x.md": "original"})
    svc.write_content("nex-inbox", "docs/specs/x.md", "edited")
    assert svc.read_content("nex-inbox", "docs/specs/x.md") == "edited"


def test_write_content_refuses_to_create_new(fake_projects_root: Path) -> None:
    (fake_projects_root / "nex-inbox" / "docs").mkdir(parents=True)
    with pytest.raises(ProjectSpecsError, match="cannot create"):
        svc.write_content("nex-inbox", "docs/new.md", "x")


def test_write_content_rejects_traversal(fake_projects_root: Path) -> None:
    with pytest.raises(ProjectSpecsError, match="traversal"):
        svc.write_content("nex-inbox", "docs/../escape.md", "x")
