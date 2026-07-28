"""The release-notes commit + the verified ``v{N}`` tag must LEAVE this machine (audit finding).

``_commit_release_note`` committed and ``_git_tag_version`` tagged, and nothing ever pushed either — so the
changelog the "Aktualizácie" tab promises and the tag a release is meant to be reproducible from existed
only inside one container's checkout, and would vanish with the volume. These exercise
:func:`_push_release_artifacts` against REAL local git repos (a bare repo standing in for ``origin``), so the
assertions are about refs that actually moved, not about calls that were made.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

from backend.services.orchestrator import _push_release_artifacts


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo_with_origin(tmp_path) -> tuple[Path, Path]:
    """A work repo with one commit + a ``v1.2.3`` tag, wired to a bare repo playing ``origin``."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(origin)], check=True)

    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    _git(work, "config", "user.email", "studio@isnex.eu")
    _git(work, "config", "user.name", "NEX Studio")
    (work / "RELEASE_NOTES.md").write_text("# v1.2.3\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "docs(release-notes): v1.2.3 — user-facing changelog")
    _git(work, "tag", "-f", "-a", "v1.2.3", "-m", "NEX Studio: v1.2.3 verified")
    _git(work, "remote", "add", "origin", str(origin))
    return work, origin


def test_commit_and_tag_reach_the_remote(repo_with_origin) -> None:
    work, origin = repo_with_origin
    assert _push_release_artifacts(work, "1.2.3") is True
    # The branch commit landed…
    assert _git(origin, "rev-parse", "refs/heads/main") == _git(work, "rev-parse", "HEAD")
    # …and so did the tag that names it.
    assert "v1.2.3" in _git(origin, "tag", "--list")


def test_a_moved_tag_is_force_updated_on_the_remote(repo_with_origin) -> None:
    """A FAIL→fix→re-PASS re-anchors the tag locally (``tag -f``); the remote must follow or the two
    disagree about which commit was verified."""
    work, origin = repo_with_origin
    _push_release_artifacts(work, "1.2.3")

    (work / "fix.txt").write_text("fixed\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "fix")
    _git(work, "tag", "-f", "-a", "v1.2.3", "-m", "NEX Studio: v1.2.3 verified")

    assert _push_release_artifacts(work, "1.2.3") is True
    assert _git(origin, "rev-parse", "refs/tags/v1.2.3^{commit}") == _git(work, "rev-parse", "HEAD")


def test_a_non_main_branch_is_pushed_to_its_own_name(tmp_path) -> None:
    """Never assume ``main`` — pushing to the wrong branch is worse than not pushing."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(origin)], check=True)
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "v2.0.0-dev")
    _git(work, "config", "user.email", "studio@isnex.eu")
    _git(work, "config", "user.name", "NEX Studio")
    (work / "a.txt").write_text("a\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "c")
    _git(work, "tag", "-f", "-a", "v9.9.9", "-m", "t")
    _git(work, "remote", "add", "origin", str(origin))

    assert _push_release_artifacts(work, "9.9.9") is True
    assert _git(origin, "rev-parse", "refs/heads/v2.0.0-dev") == _git(work, "rev-parse", "HEAD")


# ── the paths that must NOT raise, and must not lie ─────────────────────────


@pytest.fixture
def _capturable_backend_logs(monkeypatch):
    """``backend.main`` detaches the ``backend`` logger from the root (propagate=False) so uvicorn's access
    log stays intact; caplog attaches to the root, so re-attach it for the duration of a log assertion."""
    monkeypatch.setattr(logging.getLogger("backend"), "propagate", True)


def test_no_origin_is_not_a_failure_but_is_stated(tmp_path, caplog, _capturable_backend_logs) -> None:
    work = tmp_path / "local-only"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    _git(work, "config", "user.email", "studio@isnex.eu")
    _git(work, "config", "user.name", "NEX Studio")
    (work / "a.txt").write_text("a\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "c")

    with caplog.at_level(logging.INFO, logger="backend.services.orchestrator"):
        assert _push_release_artifacts(work, "1.0.0") is False
    assert "no 'origin' remote" in caplog.text


def test_missing_checkout_never_raises(tmp_path) -> None:
    assert _push_release_artifacts(tmp_path / "does-not-exist", "1.0.0") is False


def test_unreachable_remote_is_reported_not_swallowed(tmp_path, caplog, _capturable_backend_logs) -> None:
    """A push that cannot happen must leave a WARNING naming git's own reason — the audited behaviour was
    to leave nothing at all, so nobody ever learned the release stayed local."""
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    _git(work, "config", "user.email", "studio@isnex.eu")
    _git(work, "config", "user.name", "NEX Studio")
    (work / "a.txt").write_text("a\n", encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "c")
    _git(work, "remote", "add", "origin", str(tmp_path / "nowhere.git"))

    with caplog.at_level(logging.WARNING, logger="backend.services.orchestrator"):
        assert _push_release_artifacts(work, "1.0.0") is False
    assert "NOT pushed" in caplog.text
