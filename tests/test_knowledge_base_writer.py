"""Tests for :mod:`backend.services.knowledge_base_writer`.

Exercises the narrow filesystem contract that backs the live-document
service — ``save`` (overwrite), ``read``, ``append`` (with dedup),
``exists`` — under the allow-list guard (valid project slug + one of
:data:`ALLOWED_FILENAMES`).

Every test runs against a ``tmp_path``-scoped Knowledge Base root;
no test touches the real ``/home/icc/knowledge`` tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.services.knowledge_base_writer import (
    ALLOWED_FILENAMES,
    KnowledgeBaseWriter,
)

# ── fixtures ──────────────────────────────────────────────────────────


@pytest.fixture()
def writer(tmp_path: Path) -> KnowledgeBaseWriter:
    """A writer rooted at an isolated tmp KB."""
    return KnowledgeBaseWriter(tmp_path)


# ── save ──────────────────────────────────────────────────────────────


def test_save_creates_file(writer: KnowledgeBaseWriter, tmp_path: Path) -> None:
    target = writer.save("nex-test", "STATUS.md", "# NEX Test — Status\n")

    assert target == tmp_path / "projects" / "nex-test" / "STATUS.md"
    assert target.read_text(encoding="utf-8") == "# NEX Test — Status\n"


def test_save_overwrites_existing(writer: KnowledgeBaseWriter) -> None:
    writer.save("nex-test", "STATUS.md", "first")
    writer.save("nex-test", "STATUS.md", "second")

    assert writer.read("nex-test", "STATUS.md") == "second"


def test_save_auto_creates_parent_dir(writer: KnowledgeBaseWriter, tmp_path: Path) -> None:
    project_dir = tmp_path / "projects" / "brand-new"
    assert not project_dir.exists()

    writer.save("brand-new", "ARCHITECT.md", "content")

    assert project_dir.is_dir()


def test_save_content_is_utf8(writer: KnowledgeBaseWriter) -> None:
    # Slovak diacritics + emoji — ensure explicit utf-8 is preserved.
    body = "žltý kôň — ✅\n"
    writer.save("nex-test", "HISTORY.md", body)

    assert writer.read("nex-test", "HISTORY.md") == body


def test_no_tmp_file_after_successful_save(writer: KnowledgeBaseWriter, tmp_path: Path) -> None:
    writer.save("nex-test", "STATUS.md", "content")

    project_dir = tmp_path / "projects" / "nex-test"
    assert list(project_dir.glob("*.tmp")) == []


# ── read ──────────────────────────────────────────────────────────────


def test_read_returns_content(writer: KnowledgeBaseWriter) -> None:
    writer.save("nex-test", "STATUS.md", "hello")

    assert writer.read("nex-test", "STATUS.md") == "hello"


def test_read_raises_file_not_found(writer: KnowledgeBaseWriter) -> None:
    with pytest.raises(FileNotFoundError):
        writer.read("nex-test", "STATUS.md")


# ── append ────────────────────────────────────────────────────────────


def test_append_creates_new_file_with_header(writer: KnowledgeBaseWriter) -> None:
    writer.append(
        "nex-test",
        "HISTORY.md",
        "12:00 Task 1.1 done\n",
        header_if_new="# nex-test — History\n\n",
    )

    content = writer.read("nex-test", "HISTORY.md")
    assert content.startswith("# nex-test — History")
    assert "12:00 Task 1.1 done" in content


def test_append_to_existing_file(writer: KnowledgeBaseWriter) -> None:
    writer.save("nex-test", "HISTORY.md", "# nex-test — History\n\n12:00 Task 1.1 done\n")
    writer.append("nex-test", "HISTORY.md", "12:05 Task 1.2 done\n")

    content = writer.read("nex-test", "HISTORY.md")
    assert "12:00 Task 1.1 done" in content
    assert "12:05 Task 1.2 done" in content
    assert content.index("12:00") < content.index("12:05")


def test_append_skips_duplicate_first_line(writer: KnowledgeBaseWriter) -> None:
    writer.save("nex-test", "HISTORY.md", "# header\n\n12:00 Task 1.1 done\n  Audit: PASS\n")
    before = writer.read("nex-test", "HISTORY.md")

    # Same first line as the existing entry — should be skipped.
    writer.append("nex-test", "HISTORY.md", "12:00 Task 1.1 done\n  Audit: PASS\n")

    assert writer.read("nex-test", "HISTORY.md") == before


def test_append_no_header_if_existing(writer: KnowledgeBaseWriter) -> None:
    writer.save("nex-test", "HISTORY.md", "existing\n")
    writer.append(
        "nex-test",
        "HISTORY.md",
        "new entry\n",
        header_if_new="THIS MUST NOT APPEAR\n",
    )

    assert "THIS MUST NOT APPEAR" not in writer.read("nex-test", "HISTORY.md")


def test_append_no_header_if_new_defaults_to_empty(writer: KnowledgeBaseWriter) -> None:
    writer.append("nex-test", "HISTORY.md", "entry\n")

    # No header → content starts directly with the entry.
    assert writer.read("nex-test", "HISTORY.md").lstrip().startswith("entry")


def test_append_adds_trailing_newline(writer: KnowledgeBaseWriter) -> None:
    writer.append("nex-test", "HISTORY.md", "entry without trailing nl")

    assert writer.read("nex-test", "HISTORY.md").endswith("\n")


def test_append_dedup_ignores_leading_blank_lines(writer: KnowledgeBaseWriter) -> None:
    writer.save("nex-test", "HISTORY.md", "marker line\nbody\n")

    # Entry starts with blank lines — marker is still "marker line".
    writer.append("nex-test", "HISTORY.md", "\n\nmarker line\nother body\n")

    # Dedup should have kicked in — no duplicate marker line.
    content = writer.read("nex-test", "HISTORY.md")
    assert content.count("marker line") == 1


# ── exists ────────────────────────────────────────────────────────────


def test_exists_true_after_save(writer: KnowledgeBaseWriter) -> None:
    writer.save("nex-test", "STATUS.md", "x")

    assert writer.exists("nex-test", "STATUS.md") is True


def test_exists_false_for_missing_file(writer: KnowledgeBaseWriter) -> None:
    assert writer.exists("nex-test", "STATUS.md") is False


def test_exists_false_for_nonexistent_project(writer: KnowledgeBaseWriter) -> None:
    assert writer.exists("nothing-here", "STATUS.md") is False


# ── slug validation ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad_slug",
    [
        "UPPERCASE",
        "has spaces",
        "has_underscore",
        "has.dot",
        "has/slash",
        "../traversal",
        "-leading-hyphen",
        "",
        "has!bang",
    ],
)
def test_reject_invalid_slug(writer: KnowledgeBaseWriter, bad_slug: str) -> None:
    with pytest.raises(ValueError, match="Invalid project slug"):
        writer.save(bad_slug, "STATUS.md", "x")


def test_accept_valid_slug_with_hyphens_and_digits(writer: KnowledgeBaseWriter) -> None:
    # All four services exercise the same validation surface.
    writer.save("nex-test-2026", "STATUS.md", "ok")
    writer.read("nex-test-2026", "STATUS.md")
    writer.append("nex-test-2026", "HISTORY.md", "x\n")
    assert writer.exists("nex-test-2026", "STATUS.md") is True


# ── filename validation ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad_filename",
    [
        "RANDOM.md",
        "status.md",  # case-sensitive — only STATUS.md is allowed
        "STATUS",  # no extension
        "STATUS.MD",  # wrong case
        "../STATUS.md",  # traversal attempt
        "subdir/STATUS.md",  # nested path
        "",
    ],
)
def test_reject_invalid_filename(writer: KnowledgeBaseWriter, bad_filename: str) -> None:
    with pytest.raises(ValueError, match="Invalid filename"):
        writer.save("nex-test", bad_filename, "x")


@pytest.mark.parametrize("allowed", sorted(ALLOWED_FILENAMES))
def test_accept_all_allowed_filenames(writer: KnowledgeBaseWriter, allowed: str) -> None:
    writer.save("nex-test", allowed, "ok")
    assert writer.exists("nex-test", allowed) is True


# ── path traversal — physical check ───────────────────────────────────


def test_resolved_path_is_under_projects_root(writer: KnowledgeBaseWriter, tmp_path: Path) -> None:
    target = writer.save("nex-test", "STATUS.md", "x")
    projects_root = (tmp_path / "projects").resolve()
    assert str(target).startswith(str(projects_root))


def test_symlink_escape_is_rejected(writer: KnowledgeBaseWriter, tmp_path: Path) -> None:
    # If someone symlinks projects/escape-slug → /etc, resolve() should detect
    # and the guard should refuse.
    projects_root = tmp_path / "projects"
    projects_root.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (projects_root / "escape-slug").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="escapes Knowledge Base projects root"):
        writer.save("escape-slug", "STATUS.md", "x")


# ── delete_project ────────────────────────────────────────────────────


def test_delete_project_removes_folder(writer: KnowledgeBaseWriter, tmp_path: Path) -> None:
    writer.save("to-delete", "STATUS.md", "content")
    writer.save("to-delete", "HISTORY.md", "content")
    project_dir = tmp_path / "projects" / "to-delete"
    assert project_dir.is_dir()

    result = writer.delete_project("to-delete")

    assert result is True
    assert not project_dir.exists()


def test_delete_project_returns_false_when_missing(writer: KnowledgeBaseWriter) -> None:
    """Idempotent — deleting a non-existent project returns False."""
    assert writer.delete_project("never-existed") is False


def test_delete_project_rejects_invalid_slug(writer: KnowledgeBaseWriter) -> None:
    with pytest.raises(ValueError, match="Invalid project slug"):
        writer.delete_project("../traversal")


def test_delete_project_leaves_other_projects_intact(writer: KnowledgeBaseWriter, tmp_path: Path) -> None:
    writer.save("keep-me", "STATUS.md", "content")
    writer.save("remove-me", "STATUS.md", "content")

    writer.delete_project("remove-me")

    assert not (tmp_path / "projects" / "remove-me").exists()
    assert (tmp_path / "projects" / "keep-me" / "STATUS.md").is_file()
