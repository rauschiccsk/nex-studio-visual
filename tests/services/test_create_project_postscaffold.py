"""Tests for backend/services/create_project_postscaffold.py F-004 Stage 5+6 (K-004/K-005).

Per Implementer charter §10.d test approach matrix:
- Subprocess (docker, git, gh) — mocked s explicit return codes (real docker
  build je slow + flaky for unit scope per §10.d.2)
- Filesystem — real I/O cez tmp_path (template copy + commit verification)
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.services import create_project_postscaffold as mod

# Note: `_enable_log_propagation` autouse fixture moved do tests/services/conftest.py
# (CR-030 cleanup batch 2026-05-26 Návrh #1 — shared across service tests).


# ─── _run_smoke_test (K-004) ─────────────────────────────────────────────────


def test_smoke_skipped_when_no_compose_yml(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """No docker-compose.yml v target → smoke skipped (logged info)."""
    target = tmp_path / "project"
    target.mkdir()
    with caplog.at_level("INFO", logger="backend.services.create_project_postscaffold"):
        mod._run_smoke_test(target, "test-proj", full=False)
    assert any("no docker-compose.yml" in r.message for r in caplog.records)


def test_smoke_minimal_pass(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Compose exists + docker build returns 0 → PASS logged, no full smoke."""
    target = tmp_path / "project"
    target.mkdir()
    (target / "docker-compose.yml").write_text("services: {}\n")

    with patch.object(mod, "subprocess") as ps:
        ps.run.return_value = subprocess.CompletedProcess(
            args=["docker"],
            returncode=0,
            stdout="",
            stderr="",
        )
        with caplog.at_level("INFO", logger="backend.services.create_project_postscaffold"):
            mod._run_smoke_test(target, "test-proj", full=False)

    assert any("minimal smoke test PASS" in r.message for r in caplog.records)
    # Only build called — not up
    calls = [c.args[0] for c in ps.run.call_args_list]
    assert ["docker", "compose", "build"] in calls
    assert ["docker", "compose", "up", "-d"] not in calls


def test_smoke_build_failure_logged_not_raised(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Mandatory negative test (§10.d.3): build failure → warning, no exception."""
    target = tmp_path / "project"
    target.mkdir()
    (target / "docker-compose.yml").write_text("services: {}\n")

    with patch.object(mod, "subprocess") as ps:
        ps.run.return_value = subprocess.CompletedProcess(
            args=["docker"],
            returncode=1,
            stdout="",
            stderr="image build error\nspecific failure",
        )
        with caplog.at_level("WARNING", logger="backend.services.create_project_postscaffold"):
            # Žiadny raise — best-effort
            mod._run_smoke_test(target, "test-proj", full=False)

    assert any("K-004 smoke test FAIL" in r.message for r in caplog.records)


def test_smoke_full_runs_up_and_down(tmp_path: Path) -> None:
    """full=True → docker compose up -d + curl /health + down -v (cleanup)."""
    target = tmp_path / "project"
    target.mkdir()
    (target / "docker-compose.yml").write_text("services: {}\n")

    with patch.object(mod, "subprocess") as ps:
        ps.run.return_value = subprocess.CompletedProcess(
            args=["docker"],
            returncode=0,
            stdout="",
            stderr="",
        )
        mod._run_smoke_test(target, "test-proj", full=True)

    cmds = [c.args[0] for c in ps.run.call_args_list]
    # Verify build, up, down sequence
    assert ["docker", "compose", "build"] in cmds
    assert ["docker", "compose", "up", "-d"] in cmds
    assert ["docker", "compose", "down", "-v"] in cmds


def test_smoke_full_cleanup_runs_even_on_up_failure(tmp_path: Path) -> None:
    """If up fails, down -v still runs (finally block) — no resource leak."""
    target = tmp_path / "project"
    target.mkdir()
    (target / "docker-compose.yml").write_text("services: {}\n")

    def side_effect(args, **kwargs):
        if args[:3] == ["docker", "compose"] and args[3] == "up":
            return subprocess.CompletedProcess(
                args=args,
                returncode=1,
                stdout="",
                stderr="up failed",
            )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    with patch.object(mod, "subprocess") as ps:
        ps.run.side_effect = side_effect
        mod._run_smoke_test(target, "test-proj", full=True)

    cmds = [c.args[0] for c in ps.run.call_args_list]
    assert ["docker", "compose", "down", "-v"] in cmds


# ─── _wire_cicd_workflow (K-005) ─────────────────────────────────────────────


def test_cicd_template_missing(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """CICD template not present → skipped (logged warning)."""
    target = tmp_path / "project"
    target.mkdir()

    with patch.object(mod, "CICD_TEMPLATE", tmp_path / "nonexistent.yml"):
        with caplog.at_level("WARNING", logger="backend.services.create_project_postscaffold"):
            mod._wire_cicd_workflow(target, "test-proj")

    assert any("template missing" in r.message for r in caplog.records)


def test_cicd_copies_template_and_pushes(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Happy path: template exists → copied + commit + push (mocked git)."""
    target = tmp_path / "project"
    target.mkdir()
    template = tmp_path / "ci-template.yml"
    template.write_text("name: CI\non: push\n")

    with patch.object(mod, "CICD_TEMPLATE", template), patch.object(mod, "subprocess") as ps:
        ps.run.return_value = subprocess.CompletedProcess(
            args=["git"],
            returncode=0,
            stdout="",
            stderr="",
        )
        with caplog.at_level("INFO", logger="backend.services.create_project_postscaffold"):
            mod._wire_cicd_workflow(target, "test-proj")

    # Verify ci.yml was copied
    assert (target / ".github" / "workflows" / "ci.yml").is_file()
    # Verify git add/commit/push were called
    git_calls = [c.args[0] for c in ps.run.call_args_list if c.args[0][0] == "git"]
    assert any("add" in call for call in git_calls)
    assert any("commit" in call for call in git_calls)
    assert any("push" in call for call in git_calls)


def test_cicd_idempotent_when_ci_yml_exists(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ci.yml už existuje → skipped (žiadny re-copy, žiadny re-push)."""
    target = tmp_path / "project"
    workflows = target / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("name: Existing\n")

    template = tmp_path / "ci-template.yml"
    template.write_text("name: NewTemplate\n")

    with patch.object(mod, "CICD_TEMPLATE", template), patch.object(mod, "subprocess") as ps:
        with caplog.at_level("INFO", logger="backend.services.create_project_postscaffold"):
            mod._wire_cicd_workflow(target, "test-proj")

    # Existing ci.yml NOT overwritten
    assert (workflows / "ci.yml").read_text() == "name: Existing\n"
    # No git calls (skipped path)
    ps.run.assert_not_called()


def test_cicd_push_failure_logged_not_raised(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Push fails → warning, commit stays local, no exception."""
    target = tmp_path / "project"
    target.mkdir()
    template = tmp_path / "ci-template.yml"
    template.write_text("name: CI\n")

    def side_effect(args, **kwargs):
        if "push" in args:
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="push failed")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    with patch.object(mod, "CICD_TEMPLATE", template), patch.object(mod, "subprocess") as ps:
        ps.run.side_effect = side_effect
        with caplog.at_level("WARNING", logger="backend.services.create_project_postscaffold"):
            mod._wire_cicd_workflow(target, "test-proj")

    assert any("git push failed" in r.message for r in caplog.records)


# ─── _enable_branch_protection (O-3) ─────────────────────────────────────────


def test_branch_protection_invokes_gh_api(caplog: pytest.LogCaptureFixture) -> None:
    """Happy path: gh api PUT call s correct path."""
    with patch.object(mod, "subprocess") as ps:
        ps.run.return_value = subprocess.CompletedProcess(
            args=["gh"],
            returncode=0,
            stdout="{}",
            stderr="",
        )
        with caplog.at_level("INFO", logger="backend.services.create_project_postscaffold"):
            mod._enable_branch_protection("https://github.com/rauschiccsk/test-proj", "test-proj")

    call_args = ps.run.call_args.args[0]
    assert call_args[:4] == ["gh", "api", "--method", "PUT"]
    assert "repos/rauschiccsk/test-proj/branches/main/protection" in call_args
    assert any("Branch protection enabled" in r.message for r in caplog.records)


def test_branch_protection_failure_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    """Mandatory negative test: gh api fails → warning, no exception."""
    with patch.object(mod, "subprocess") as ps:
        ps.run.return_value = subprocess.CompletedProcess(
            args=["gh"],
            returncode=1,
            stdout="",
            stderr="HTTP 403 forbidden",
        )
        with caplog.at_level("WARNING", logger="backend.services.create_project_postscaffold"):
            mod._enable_branch_protection("https://github.com/rauschiccsk/test-proj", "test-proj")

    assert any("Branch protection setup failed" in r.message for r in caplog.records)


# ─── Orchestrator run_post_scaffold_steps ────────────────────────────────────


def test_orchestrator_skips_all_when_target_invalid(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Invalid target → smoke + cicd skipped; branch protection ak repo_url."""
    with caplog.at_level("INFO", logger="backend.services.create_project_postscaffold"):
        mod.run_post_scaffold_steps(
            target="/nonexistent/path",
            slug="test-proj",
            repo_url=None,
            enable_cicd=True,
            full_smoke=False,
            enable_branch_protection=False,
        )
    assert any("not a directory" in r.message for r in caplog.records)


def test_orchestrator_dispatches_all_three(tmp_path: Path) -> None:
    """Happy path: smoke + CI/CD + branch protection all dispatched per flags."""
    target = tmp_path / "project"
    target.mkdir()
    (target / "docker-compose.yml").write_text("services: {}\n")
    template = tmp_path / "ci-template.yml"
    template.write_text("name: CI\n")

    with (
        patch.object(mod, "CICD_TEMPLATE", template),
        patch.object(mod, "subprocess") as ps,
    ):
        ps.run.return_value = subprocess.CompletedProcess(
            args=["x"],
            returncode=0,
            stdout="",
            stderr="",
        )
        mod.run_post_scaffold_steps(
            target=str(target),
            slug="test-proj",
            repo_url="https://github.com/rauschiccsk/test-proj",
            enable_cicd=True,
            full_smoke=False,
            enable_branch_protection=True,
        )

    # Verify all 3 stages called subprocess
    cmds = [c.args[0] for c in ps.run.call_args_list]
    # Smoke
    assert ["docker", "compose", "build"] in cmds
    # CI/CD
    assert any("git" in cmd[0] and "push" in cmd for cmd in cmds)
    # Branch protection (gh api)
    assert any(cmd[0] == "gh" and cmd[1] == "api" for cmd in cmds)
