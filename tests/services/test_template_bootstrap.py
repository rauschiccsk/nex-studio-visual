"""Tests for backend/services/template_bootstrap.py F-004 Stage 4 (K-001/K-002).

Per Implementer charter §10.d (CR-029 test approach matrix):
- Filesystem (git init/push) — real I/O cez tmp_path bare-repo simulation
- Subprocess (git, gh) — real git subprocess; gh selectively mocked
- Mandatory negative test pre K-002 rollback (per §10.d.3)
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.services.template_bootstrap import (
    GitPushVerificationError,
    TemplateBootstrapError,
    push_and_verify,
    rollback_partial_state,
)

# ─── Fixtures ────────────────────────────────────────────────────────────────


def _init_local_repo(target: Path) -> str:
    """Create a real git repo with one commit. Returns the commit SHA."""
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=target, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@nex-studio.local"],
        cwd=target,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test Runner"],
        cwd=target,
        check=True,
        capture_output=True,
    )
    (target / "README.md").write_text("# test\n")
    subprocess.run(["git", "add", "README.md"], cwd=target, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=target,
        check=True,
        capture_output=True,
    )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=target,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _init_bare_origin(origin_dir: Path) -> None:
    """Create a real bare repo to act as origin."""
    origin_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "--bare", "-b", "main"],
        cwd=origin_dir,
        check=True,
        capture_output=True,
    )


# ─── push_and_verify happy path ──────────────────────────────────────────────


def test_push_and_verify_happy_path(tmp_path: Path) -> None:
    """K-001: real local repo + real bare origin → push succeeds, HEAD matches."""
    local = tmp_path / "project"
    origin = tmp_path / "origin.git"
    local_head = _init_local_repo(local)
    _init_bare_origin(origin)

    push_and_verify(
        target=str(local),
        repo_full_name="rauschiccsk/test-proj",
        remote_url=str(origin),  # bypass GitHub — use local bare repo
    )

    # Verify remote was set
    remote_url_result = subprocess.run(
        ["git", "-C", str(local), "remote", "get-url", "origin"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert remote_url_result.stdout.strip() == str(origin)

    # Verify origin HEAD matches local HEAD
    origin_head = subprocess.run(
        ["git", "-C", str(origin), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert origin_head == local_head


def test_push_and_verify_remote_already_exists(tmp_path: Path) -> None:
    """Idempotent re-run: existing origin → set-url path used, push still works."""
    local = tmp_path / "project"
    origin = tmp_path / "origin.git"
    _init_local_repo(local)
    _init_bare_origin(origin)

    # Pre-set origin to a bogus URL — push_and_verify must overwrite it
    subprocess.run(
        ["git", "-C", str(local), "remote", "add", "origin", "ssh://bogus/url"],
        check=True,
        capture_output=True,
    )

    push_and_verify(
        target=str(local),
        repo_full_name="rauschiccsk/test-proj",
        remote_url=str(origin),
    )

    # Verify URL was updated
    remote_url_result = subprocess.run(
        ["git", "-C", str(local), "remote", "get-url", "origin"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert remote_url_result.stdout.strip() == str(origin)


# ─── push_and_verify failure paths ───────────────────────────────────────────


def test_push_and_verify_not_a_git_repo(tmp_path: Path) -> None:
    """Target without .git → TemplateBootstrapError (pre-condition fail)."""
    target = tmp_path / "no-git"
    target.mkdir()
    (target / "README.md").write_text("not a repo")

    with pytest.raises(TemplateBootstrapError, match="not a git repository"):
        push_and_verify(
            target=str(target),
            repo_full_name="rauschiccsk/test",
            remote_url="ssh://anywhere",
        )


def test_push_and_verify_push_fails_after_retries(tmp_path: Path) -> None:
    """Push to non-existent origin → GitPushVerificationError after retries."""
    local = tmp_path / "project"
    _init_local_repo(local)

    bad_origin = tmp_path / "does-not-exist"  # neither bare repo nor any path

    with pytest.raises(GitPushVerificationError, match="git push failed after"):
        push_and_verify(
            target=str(local),
            repo_full_name="rauschiccsk/test",
            remote_url=str(bad_origin),
            push_retry_attempts=1,  # 1 retry = 2 total attempts
        )


def test_push_and_verify_head_mismatch_detected(tmp_path: Path) -> None:
    """Simulate ls-remote returning different SHA → K-001 verify fails."""
    local = tmp_path / "project"
    origin = tmp_path / "origin.git"
    _init_local_repo(local)
    _init_bare_origin(origin)

    # Real push will succeed; mock _run_git only for ls-remote to fake mismatch
    from backend.services import template_bootstrap as mod

    real_run_git = mod._run_git

    def fake_run_git(args, *, cwd, timeout=60):
        if args[:2] == ["ls-remote", "origin"]:
            # Fake completed process with wrong SHA
            return subprocess.CompletedProcess(
                args=["git", "ls-remote"],
                returncode=0,
                stdout="0000000000000000000000000000000000000000\tHEAD\n",
                stderr="",
            )
        return real_run_git(args, cwd=cwd, timeout=timeout)

    with patch.object(mod, "_run_git", side_effect=fake_run_git):
        with pytest.raises(GitPushVerificationError, match="local HEAD .* != remote HEAD"):
            push_and_verify(
                target=str(local),
                repo_full_name="rauschiccsk/test",
                remote_url=str(origin),
            )


# ─── K-002 rollback ──────────────────────────────────────────────────────────


def test_rollback_removes_git_dir(tmp_path: Path) -> None:
    """Mandatory negative test (§10.d.3): .git exists → rollback removes it."""
    local = tmp_path / "project"
    _init_local_repo(local)
    assert (local / ".git").is_dir()

    rollback_partial_state(
        target=str(local),
        repo_full_name="rauschiccsk/test",
        delete_github_repo=False,
    )

    assert not (local / ".git").exists()
    # Files outside .git should remain (so re-run is idempotent at project level)
    assert (local / "README.md").is_file()


def test_rollback_idempotent_when_no_git_dir(tmp_path: Path) -> None:
    """No .git → rollback is no-op (no exception)."""
    target = tmp_path / "project"
    target.mkdir()
    (target / "README.md").write_text("x")

    rollback_partial_state(
        target=str(target),
        repo_full_name="rauschiccsk/test",
        delete_github_repo=False,
    )

    # README still intact, no .git was there to delete
    assert (target / "README.md").is_file()


def test_rollback_gh_delete_called_when_optin(tmp_path: Path) -> None:
    """delete_github_repo=True → gh repo delete invoked."""
    local = tmp_path / "project"
    _init_local_repo(local)

    with patch("backend.services.template_bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=["gh"], returncode=0, stdout="", stderr="")
        rollback_partial_state(
            target=str(local),
            repo_full_name="rauschiccsk/test-proj",
            delete_github_repo=True,
        )

    # First call = rm -rf .git, second call = gh repo delete
    calls = mock_run.call_args_list
    assert len(calls) >= 2
    # Verify gh repo delete was invoked with correct args
    gh_call_found = any(
        call.args[0][:3] == ["gh", "repo", "delete"] and call.args[0][3] == "rauschiccsk/test-proj" for call in calls
    )
    assert gh_call_found, f"gh repo delete not found in calls: {[c.args[0] for c in calls]}"


def test_rollback_gh_delete_failure_warned_not_raised(tmp_path: Path) -> None:
    """gh repo delete failing → log warning, no exception (graceful)."""
    local = tmp_path / "project"
    _init_local_repo(local)

    with patch("backend.services.template_bootstrap.subprocess.run") as mock_run:
        # rm -rf succeeds; gh delete fails
        def side_effect(args, **kwargs):
            if args[:3] == ["gh", "repo", "delete"]:
                return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="gh: not authenticated")
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect

        # No raise expected
        rollback_partial_state(
            target=str(local),
            repo_full_name="rauschiccsk/test-proj",
            delete_github_repo=True,
        )


def test_rollback_rm_failure_raises(tmp_path: Path) -> None:
    """rm -rf .git failure → TemplateBootstrapError (cleanup itself broken)."""
    local = tmp_path / "project"
    _init_local_repo(local)

    with patch("backend.services.template_bootstrap.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=["rm"], returncode=1, stdout="", stderr="Permission denied"
        )
        with pytest.raises(TemplateBootstrapError, match="rm -rf .* failed"):
            rollback_partial_state(
                target=str(local),
                repo_full_name="rauschiccsk/test",
                delete_github_repo=False,
            )


# ─── PermissionError end-to-end (per §10.d.3 mandatory negative test) ────────


def test_push_and_verify_handles_readonly_target_gracefully(tmp_path: Path) -> None:
    """Target directory read-only → git remote add fails → GitPushVerificationError."""
    local = tmp_path / "project"
    _init_local_repo(local)
    # Make .git read-only (simulate permission constraint)
    git_dir = local / ".git"
    original_mode = git_dir.stat().st_mode
    try:
        os.chmod(git_dir, 0o555)
        # Push will fail at some step — either remote add or push itself
        with pytest.raises(GitPushVerificationError):
            push_and_verify(
                target=str(local),
                repo_full_name="rauschiccsk/test",
                remote_url=str(tmp_path / "origin.git"),  # non-existent origin
            )
    finally:
        os.chmod(git_dir, original_mode)


# ─── CR-NS-013 — HTTPS push via gh credential helper ─────────────────────────

from backend.services import template_bootstrap  # noqa: E402


@pytest.fixture(autouse=True)
def _neutralize_gh(monkeypatch):
    """Keep the real-git tests hermetic — never invoke the real ``gh`` binary.

    ``push_and_verify`` now runs ``gh auth setup-git`` before pushing; the
    real-git fixtures push to a local bare repo and don't need it, so stub it
    to a success no-op. The dedicated ordering test below re-patches it with
    its own recorder.
    """
    monkeypatch.setattr(
        template_bootstrap,
        "_run_gh",
        lambda args, *, timeout=60: subprocess.CompletedProcess(args=["gh", *args], returncode=0, stdout="", stderr=""),
    )


def test_push_uses_https_origin_by_default(monkeypatch):
    """Default remote URL is HTTPS (gh credential helper), never SSH (no ssh in container)."""
    captured = {}

    def fake_git(args, *, cwd, timeout=60):
        if args[:2] == ["remote", "get-url"]:
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="no remote")
        if args[0] == "remote" and args[1] in ("add", "set-url"):
            captured["url"] = args[-1]
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        if args[0] == "push":
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        if args == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="abc123def456\n", stderr="")
        if args[:2] == ["ls-remote", "origin"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="abc123def456\tHEAD\n", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(template_bootstrap, "_run_git", fake_git)
    monkeypatch.setattr(template_bootstrap.Path, "is_dir", lambda self: True)

    push_and_verify(target="/tmp/proj", repo_full_name="rauschiccsk/test-proj", remote_url=None)

    assert captured["url"] == "https://github.com/rauschiccsk/test-proj.git"
    assert not captured["url"].startswith("git@")


def test_gh_auth_setup_git_runs_before_push(monkeypatch):
    """``gh auth setup-git`` is invoked before the first ``git push``."""
    calls: list[tuple[str, tuple[str, ...]]] = []

    def fake_git(args, *, cwd, timeout=60):
        calls.append(("git", tuple(args)))
        if args[:2] == ["remote", "get-url"]:
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="")
        if args == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="abc\n", stderr="")
        if args[:2] == ["ls-remote", "origin"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="abc\tHEAD\n", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    def fake_gh(args, *, timeout=60):
        calls.append(("gh", tuple(args)))
        return subprocess.CompletedProcess(args=["gh", *args], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(template_bootstrap, "_run_git", fake_git)
    monkeypatch.setattr(template_bootstrap, "_run_gh", fake_gh)
    monkeypatch.setattr(template_bootstrap.Path, "is_dir", lambda self: True)

    push_and_verify(target="/tmp/proj", repo_full_name="rauschiccsk/test", remote_url=None)

    gh_idx = next(i for i, c in enumerate(calls) if c == ("gh", ("auth", "setup-git")))
    push_idx = next(i for i, c in enumerate(calls) if c[0] == "git" and c[1] and c[1][0] == "push")
    assert gh_idx < push_idx
