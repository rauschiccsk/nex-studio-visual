"""CR-V2-018: v2 two-agent charter provisioning + project normalisation.

The engine fail-closes on a missing ``ai-agent``/``auditor`` charter
(``claude_agent._load_charter``), so a freshly-scaffolded project MUST be provisioned with both v2
charters or it blocks at first dispatch ("Agent dispatch failed — pipeline blocked"). These tests
cover the provisioning function, the v2-shape normalisation, and the engine's descriptive guard.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from backend.services.claude_agent import ClaudeAgentError, _load_charter
from backend.services.create_project_postscaffold import (
    ProvisioningError,
    _commit_and_push_scaffold_finalisation,
    _mark_project_trusted,
    _provision_ci_runner,
    deprovision_ci_runner,
    provision_v2_agent_charters,
)


@pytest.fixture(autouse=True)
def _isolate_claude_config(monkeypatch, tmp_path_factory):
    """Point CLAUDE_CONFIG_DIR at an isolated dir so provisioning's trust-mark (CR-V2-030) never touches
    the real ``~/.claude/.claude.json``. With no config file there, the trust step is a no-op for the
    charter tests; the trust-specific tests below write their own config into this dir."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path_factory.mktemp("claude-cfg")))


def _claude_config_path() -> Path:
    return Path(os.environ["CLAUDE_CONFIG_DIR"]) / ".claude.json"


def _make_v1_scaffold(root: Path) -> None:
    """Build the v1-shaped charter layout the icc-claude-template ``init.sh`` emits today."""
    agents = root / ".claude" / "agents"
    for role in ("designer", "implementer", "auditor", "customer"):
        (agents / role).mkdir(parents=True, exist_ok=True)
        (agents / role / "CLAUDE.md").write_text(f"v1 {role} charter\n", encoding="utf-8")
        (agents / role / "settings.json").write_text("{}\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text("# v1 universal — 5 roles, Gate E\n", encoding="utf-8")


# ─── provision_v2_agent_charters — happy path ──────────────────────────────────


def test_provision_writes_both_v2_charters_concatenated(tmp_path: Path) -> None:
    _make_v1_scaffold(tmp_path)

    provision_v2_agent_charters(tmp_path, "demo", "Demo Project")

    agents = tmp_path / ".claude" / "agents"
    ai_charter = (agents / "ai-agent" / "CLAUDE.md").read_text(encoding="utf-8")
    auditor_charter = (agents / "auditor" / "CLAUDE.md").read_text(encoding="utf-8")

    # Each charter = shared base concatenated BEFORE the role charter (the engine reads the single file).
    assert "Bezpečnosť §4 — INVIOLABLE" in ai_charter  # from agent-shared-base.md
    assert "Pravidlá agenta — AI Agent" in ai_charter  # from ai-agent-charter.md
    assert "Bezpečnosť §4 — INVIOLABLE" in auditor_charter
    assert "Pravidlá agenta — Auditor" in auditor_charter
    # The v1 auditor charter was overwritten with the v2 one.
    assert "v1 auditor charter" not in auditor_charter


def test_provision_substitutes_project_root_in_settings(tmp_path: Path) -> None:
    _make_v1_scaffold(tmp_path)

    provision_v2_agent_charters(tmp_path, "demo", "Demo Project")

    for role in ("ai-agent", "auditor"):
        settings = (tmp_path / ".claude" / "agents" / role / "settings.json").read_text(encoding="utf-8")
        assert "<PROJECT_ROOT>" not in settings  # placeholder fully substituted
        assert str(tmp_path) in settings  # to the concrete project root (== agent cwd at dispatch)


def test_provision_normalises_to_v2_shape(tmp_path: Path) -> None:
    _make_v1_scaffold(tmp_path)

    provision_v2_agent_charters(tmp_path, "demo", "Demo Project")

    agents = tmp_path / ".claude" / "agents"
    # v1-only agent dirs removed (the engine never reads them); v2 dirs kept.
    for v1_dir in ("designer", "implementer", "customer"):
        assert not (agents / v1_dir).exists()
    assert (agents / "ai-agent").is_dir()
    assert (agents / "auditor").is_dir()

    # v1 universal CLAUDE.md replaced with the v2-native one (auto-loaded by the claude CLI from cwd).
    universal = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Demo Project" in universal  # {{PROJECT_NAME}} substituted
    assert "NEX Studio v2.0.0" in universal
    assert "Gate E" not in universal  # no v1 5-role guidance leaks in


# ─── provision_v2_agent_charters — edge cases ──────────────────────────────────


def test_provision_noop_without_checkout(tmp_path: Path) -> None:
    """No .claude on disk (dry-run / disabled bootstrap) → graceful no-op, never raises."""
    target = tmp_path / "empty"
    target.mkdir()

    provision_v2_agent_charters(target, "demo", "Demo Project")  # must not raise

    assert not (target / "CLAUDE.md").exists()
    assert not (target / ".claude").exists()


def test_provision_raises_when_templates_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _make_v1_scaffold(tmp_path)
    empty_templates = tmp_path / "no-templates"
    empty_templates.mkdir()
    monkeypatch.setattr(
        "backend.services.create_project_postscaffold.NEX_STUDIO_TEMPLATES",
        empty_templates,
    )

    with pytest.raises(ProvisioningError) as exc_info:
        provision_v2_agent_charters(tmp_path, "demo", "Demo Project")
    assert "template" in str(exc_info.value).lower()


# ─── claude_agent._load_charter — engine guard ─────────────────────────────────


def test_load_charter_missing_raises_descriptive_error(tmp_path: Path) -> None:
    with pytest.raises(ClaudeAgentError) as exc_info:
        _load_charter(tmp_path / "ai-agent" / "CLAUDE.md")
    message = str(exc_info.value)
    assert "missing" in message.lower()
    assert "NEX Studio v2" in message  # actionable hint, not a raw FileNotFoundError


def test_load_charter_returns_content(tmp_path: Path) -> None:
    charter = tmp_path / "CLAUDE.md"
    charter.write_text("Pravidlá agenta\n", encoding="utf-8")
    assert _load_charter(charter) == "Pravidlá agenta\n"


# ─── CR-V2-030: pre-trust the project in the claude config ──────────────────────


def test_mark_project_trusted_sets_flag_and_preserves_rest() -> None:
    cfg = _claude_config_path()
    cfg.write_text(
        json.dumps(
            {
                "projects": {"/opt/projects/other": {"hasTrustDialogAccepted": True, "x": 1}},
                "oauthAccount": "KEEP-ME",
            }
        ),
        encoding="utf-8",
    )

    _mark_project_trusted(Path("/opt/projects/demo"))

    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["projects"]["/opt/projects/demo"]["hasTrustDialogAccepted"] is True
    # untouched: other projects + unrelated top-level keys (e.g. the account/token) survive verbatim.
    assert data["projects"]["/opt/projects/other"] == {"hasTrustDialogAccepted": True, "x": 1}
    assert data["oauthAccount"] == "KEEP-ME"


def test_mark_project_trusted_is_idempotent() -> None:
    cfg = _claude_config_path()
    cfg.write_text(
        json.dumps({"projects": {"/opt/projects/demo": {"hasTrustDialogAccepted": True}}}),
        encoding="utf-8",
    )
    before = cfg.read_text(encoding="utf-8")

    _mark_project_trusted(Path("/opt/projects/demo"))

    assert cfg.read_text(encoding="utf-8") == before  # already trusted → no needless rewrite


def test_mark_project_trusted_missing_config_does_not_raise() -> None:
    # No .claude.json in CLAUDE_CONFIG_DIR → best-effort no-op (headless build works regardless).
    assert not _claude_config_path().exists()
    _mark_project_trusted(Path("/opt/projects/demo"))  # must not raise


# ─── v2 normalisation — stale v1 state files + git finalisation ─────────────────


def test_provision_removes_stale_v1_state_files(tmp_path: Path) -> None:
    _make_v1_scaffold(tmp_path)
    # the icc-claude-template also drops 5-role session-state files at the project root
    for role in ("designer", "implementer", "customer", "auditor"):
        (tmp_path / f".nex-{role}-state.md").write_text("stale\n", encoding="utf-8")

    provision_v2_agent_charters(tmp_path, "demo", "Demo Project")

    # v1-only role state files removed; auditor (a v2 role) kept.
    for v1_role in ("designer", "implementer", "customer"):
        assert not (tmp_path / f".nex-{v1_role}-state.md").exists()
    assert (tmp_path / ".nex-auditor-state.md").exists()


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)


def _init_repo(root: Path) -> None:
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@t.t")
    _git(root, "config", "user.name", "t")


def test_scaffold_finalisation_commits_residual_changes(tmp_path: Path) -> None:
    # Repo with a bootstrap commit, then an uncommitted change (mimics the v2 normalisation).
    _init_repo(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("bootstrap\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "bootstrap")
    head_before = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()

    (tmp_path / "CLAUDE.md").write_text("v2 shape\n", encoding="utf-8")  # normalisation edit
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "x").write_text("new\n", encoding="utf-8")

    # push is best-effort (no remote configured → swallowed); the commit is the asserted behaviour.
    _commit_and_push_scaffold_finalisation(tmp_path, "demo")

    assert _git(tmp_path, "rev-parse", "HEAD").stdout.strip() != head_before  # residual commit made
    assert _git(tmp_path, "status", "--porcelain").stdout.strip() == ""  # working tree now clean


def test_scaffold_finalisation_noop_on_clean_tree(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "f").write_text("x\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    head_before = _git(tmp_path, "rev-parse", "HEAD").stdout.strip()

    _commit_and_push_scaffold_finalisation(tmp_path, "demo")  # clean tree → no commit

    assert _git(tmp_path, "rev-parse", "HEAD").stdout.strip() == head_before


# --- Containerized CI runner provisioning (Director 2026-07-16) --------------


class _FakeCompleted:
    """Minimal ``subprocess.CompletedProcess`` stand-in for the docker-call mocks below."""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_provision_ci_runner_runs_container_with_correct_label(monkeypatch) -> None:
    """Happy path: no existing container → a ``docker run -d`` for the right repo/label, with the PAT passed
    via the child ENV (name-only ``-e ACCESS_TOKEN``) and NEVER on argv (ps/log leak guard)."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secretTOKEN")
    monkeypatch.delenv("GH_TOKEN", raising=False)

    calls: list[tuple[list[str], dict]] = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        if len(cmd) > 1 and cmd[1] == "ps":
            return _FakeCompleted(returncode=0, stdout="")  # no existing runner container
        return _FakeCompleted(returncode=0, stdout="newcontainerid\n")

    monkeypatch.setattr(subprocess, "run", fake_run)

    _provision_ci_runner("nex-demo", "https://github.com/rauschiccsk/nex-demo")

    run_cmd, run_kwargs = calls[-1]
    assert run_cmd[:3] == ["docker", "run", "-d"]
    assert "nex-ci-runner-nex-demo" in run_cmd
    assert "LABELS=andros-ubuntu-nex-demo" in run_cmd
    assert "RUNNER_NAME=andros-ubuntu-nex-demo" in run_cmd
    assert "REPO_URL=https://github.com/rauschiccsk/nex-demo" in run_cmd
    assert "/var/run/docker.sock:/var/run/docker.sock" in run_cmd
    # PAT: passed via env, NOT baked onto argv; the ``-e ACCESS_TOKEN`` flag is name-only (no ``=value``).
    assert "ghp_secretTOKEN" not in run_cmd
    assert run_kwargs["env"]["ACCESS_TOKEN"] == "ghp_secretTOKEN"
    assert "ACCESS_TOKEN" in run_cmd
    assert not any(str(arg).startswith("ACCESS_TOKEN=") for arg in run_cmd)


def test_provision_ci_runner_skips_without_token(monkeypatch) -> None:
    """No GITHUB_TOKEN / GH_TOKEN → never shells out (best-effort skip, logged)."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    calls: list = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a) or _FakeCompleted())

    _provision_ci_runner("nex-demo", None)

    assert calls == []


def test_provision_ci_runner_idempotent_when_container_exists(monkeypatch) -> None:
    """An existing ``nex-ci-runner-<slug>`` container → only the ``docker ps`` check runs, no ``docker run``."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_x")

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _FakeCompleted(returncode=0, stdout="existingcontainerid\n")  # ps finds one

    monkeypatch.setattr(subprocess, "run", fake_run)

    _provision_ci_runner("nex-demo", None)

    assert len(calls) == 1
    assert calls[0][1] == "ps"


def test_deprovision_ci_runner_removes_container(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: calls.append(cmd) or _FakeCompleted(returncode=0))

    deprovision_ci_runner("nex-demo")

    assert calls[0] == ["docker", "rm", "-f", "nex-ci-runner-nex-demo"]
