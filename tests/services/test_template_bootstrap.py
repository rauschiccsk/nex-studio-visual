"""Tests for backend/services/template_bootstrap.py F-004 Stage 4 (K-001/K-002).

Per Implementer charter §10.d (CR-029 test approach matrix):
- Filesystem (git init/push) — real I/O cez tmp_path bare-repo simulation
- Subprocess (git, gh) — real git subprocess; gh selectively mocked
- Mandatory negative test pre K-002 rollback (per §10.d.3)
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.services import system_setting as system_setting_service
from backend.services.template_bootstrap import (
    GitPushVerificationError,
    TemplateBootstrapError,
    _repo_from_url,
    invoke_init_script,
    push_and_verify,
    resolve_github_org,
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


# ─── Regression: the cockpit's argv must stay valid for the REAL init.sh ─────


ICC_INIT_SCRIPT = Path("/home/icc/knowledge/templates/claude-project/init.sh")


def test_init_script_accepts_the_argv_the_cockpit_builds(db_session, tmp_path):
    """Run the REAL init.sh with the REAL argv `invoke_init_script` builds, dry-run.

    The cockpit and init.sh live in two different repositories and drift apart silently. On
    2026-07-27 the Koordinátor role was retired from init.sh — including the `--no-coordinator`
    flag that opted out of it — while the cockpit went on sending that flag. init.sh's argument
    parser ends in a catch-all that prints usage and EXITS 1 on anything it does not recognise, so
    from that moment EVERY project creation failed with HTTP 500 and no project could be founded.

    Nothing caught it: no test had ever executed the real script. `conftest` forces dry_run and
    leaves `template_init_script_path` unset in the test DB, so the subprocess never ran at all —
    the mocked tests all passed while founding was completely broken in production.

    This test closes that gap. It is the only place the two repositories are checked against each
    other, so it deliberately uses the real script rather than a fixture: a copy would drift the
    same way the assumption did. It runs in CI because the Test job is self-hosted on ANDROS, where
    the KB is present; elsewhere it skips with a stated reason rather than passing vacuously.
    """
    if not ICC_INIT_SCRIPT.is_file():
        pytest.skip(f"real init.sh not on this host ({ICC_INIT_SCRIPT}) — cockpit/template drift unchecked here")

    system_setting_service.upsert(db_session, "template_init_script_path", str(ICC_INIT_SCRIPT))
    db_session.flush()

    user = User(
        username=f"boot-{uuid.uuid4().hex[:8]}",
        email=f"boot-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()

    target = tmp_path / "argv-probe"
    project = Project(
        name="Argv Probe",
        slug=f"argv-probe-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="Regression probe for cockpit↔init.sh argv compatibility",
        created_by=user.id,
        source_path=str(target),
        backend_port=14990,
    )
    db_session.add(project)
    db_session.flush()

    # Raises TemplateBootstrapError on a non-zero exit — which is exactly the production failure.
    result = invoke_init_script(db_session, project, dry_run=True)

    assert result.init_script == str(ICC_INIT_SCRIPT)
    assert result.target == str(target)


def test_disabled_bootstrap_refuses_a_greenfield_project_instead_of_founding_a_hollow_one(db_session, tmp_path):
    """Empty `template_init_script_path` + no workspace on disk + a REAL run = refuse, loudly.

    `dry_run` is exempt: that is the tests' deliberate scaffold-nothing mode.

    It used to return a clean 201 for a project with NO directory, NO agent charters, NO git and NO
    CI — every downstream step has its own silent skip branch, so nothing complained. The Manager
    found out only when his first build died on a missing charter, with advice ("re-create the
    project") that could not work because re-creating produced the same hollow project.
    """
    system_setting_service.upsert(db_session, "template_init_script_path", "")
    db_session.flush()

    user = User(
        username=f"hollow-{uuid.uuid4().hex[:8]}",
        email=f"hollow-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()

    project = Project(
        name="Hollow Probe",
        description="Regression probe",
        slug=f"hollow-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        created_by=user.id,
        source_path=str(tmp_path / "does-not-exist"),
        backend_port=14990,
    )
    db_session.add(project)
    db_session.flush()

    with pytest.raises(TemplateBootstrapError, match="Automatické zakladanie je vypnuté"):
        invoke_init_script(db_session, project)


# ─── github_org is authoritative for every repo-derived step, not just repo creation ─────


def test_repo_from_url_keeps_the_owner_of_the_short_form_projects_actually_store():
    """``repo_url`` is stored as ``owner/name`` (the new-project form fills it from ``github_org``).

    Only the full-URL shape used to be recognised, so the short one fell through to the fallback and
    the owner was replaced by a hardcoded organisation: the repo was created under the configured org
    (that path passes ``repo_url`` through verbatim) while the scaffold argv, the push+verify target,
    the CI runner's REPO_URL and branch protection all pointed at the wrong owner.
    """
    assert _repo_from_url("acme-org/nex-thing", "nex-thing", default_owner="ignored") == "acme-org/nex-thing"
    # The legacy full-URL shape keeps working, with and without the .git suffix.
    assert _repo_from_url("https://github.com/acme-org/nex-thing.git", "nex-thing", default_owner="x") == (
        "acme-org/nex-thing"
    )


def test_repo_from_url_falls_back_to_the_configured_org_not_a_hardcoded_one():
    """No usable ``repo_url`` → the owner comes from the caller's ``github_org``, not a literal."""
    assert _repo_from_url(None, "nex-thing", default_owner="acme-org") == "acme-org/nex-thing"
    assert _repo_from_url("   ", "nex-thing", default_owner="acme-org") == "acme-org/nex-thing"
    # An unparseable value must not silently become part of the repo name either.
    assert _repo_from_url("ssh://git@example.com/a/b/c", "nex-thing", default_owner="acme-org") == "acme-org/nex-thing"


def test_resolve_github_org_reads_the_setting(db_session):
    system_setting_service.upsert(db_session, "github_org", "acme-org")
    db_session.flush()
    assert resolve_github_org(db_session) == "acme-org"


def test_init_script_receives_the_configured_org_when_repo_url_is_absent(db_session, tmp_path, monkeypatch):
    """End-to-end for the scaffold stage: a project with no ``repo_url`` under a non-default org must
    still be scaffolded as ``<configured-org>/<slug>``."""
    from backend.services import template_bootstrap as mod

    script = tmp_path / "init.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    system_setting_service.upsert(db_session, "template_init_script_path", str(script))
    system_setting_service.upsert(db_session, "github_org", "acme-org")
    db_session.flush()

    user = User(
        username=f"org-{uuid.uuid4().hex[:8]}",
        email=f"org-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()

    project = Project(
        name="Org Probe",
        slug=f"org-probe-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="github_org threading probe",
        created_by=user.id,
        source_path=str(tmp_path / "org-probe"),
        backend_port=14990,
    )
    db_session.add(project)
    db_session.flush()

    captured: dict[str, list[str]] = {}

    def fake_run(args, **kwargs):
        captured["argv"] = list(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    invoke_init_script(db_session, project)

    argv = captured["argv"]
    assert argv[argv.index("--repo") + 1] == f"acme-org/{project.slug}"


def test_disabled_bootstrap_still_registers_an_existing_brownfield_workspace(db_session, tmp_path):
    """The opt-out stays usable for its real purpose: adopting a project that already exists."""
    system_setting_service.upsert(db_session, "template_init_script_path", "")
    db_session.flush()

    existing = tmp_path / "already-here"
    existing.mkdir()
    (existing / "CLAUDE.md").write_text("# existing project\n")

    user = User(
        username=f"brown-{uuid.uuid4().hex[:8]}",
        email=f"brown-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role="ri",
    )
    db_session.add(user)
    db_session.flush()

    project = Project(
        name="Brownfield Probe",
        description="Regression probe",
        slug=f"brown-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        created_by=user.id,
        source_path=str(existing),
        backend_port=14990,
    )
    db_session.add(project)
    db_session.flush()

    result = invoke_init_script(db_session, project)
    assert result.target == str(existing)
    assert result.init_script == ""
