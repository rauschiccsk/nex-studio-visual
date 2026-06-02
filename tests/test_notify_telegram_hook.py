"""Tests for the agent Telegram notify scripts (CR-NS-011).

Exercises ``scripts/notify_telegram.sh`` (sender) and
``scripts/hook_agent_notify.sh`` (PostToolUse hook) via subprocess, using
``NOTIFY_DRY_RUN=1`` so no real Telegram call is made. Verifies the hook
fires only for agent → Dedo/Director ``done-report`` / ``question`` reports,
no-ops otherwise, and that the bot token never leaks to any output.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
NOTIFY = REPO_ROOT / "scripts" / "notify_telegram.sh"
HOOK = REPO_ROOT / "scripts" / "hook_agent_notify.sh"

TOKEN = "SECRET-bot-token-123456:should-never-appear"


def _make_project(tmp_path: Path, *, with_env: bool = True, chat_id: str = "TEST") -> Path:
    """Create a temp git project with an optional .env carrying the chat_id."""
    project = tmp_path / "sample-project"
    (project / ".dedo-channel" / "inbox").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    if with_env:
        (project / ".env").write_text(f"TELEGRAM_NOTIFY_CHAT_ID={chat_id}\nOTHER=ignored\n")
    return project


def _central_env(tmp_path: Path, *, with_token: bool = True) -> Path:
    central = tmp_path / "central.env"
    central.write_text(f"TELEGRAM_ICC_BOT_TOKEN={TOKEN}\n" if with_token else "# empty\n")
    return central


def _write_report(project: Path, name: str, *, frm: str, topic: str, rtype: str) -> Path:
    path = project / ".dedo-channel" / "inbox" / name
    path.write_text(
        f"---\nfrom: {frm}\nto: dedo\ntopic: {topic}\ndate: 2026-06-02T00:00:00Z\ntype: {rtype}\n---\n\nbody\n"
    )
    return path


def _run_hook(report: Path, project: Path, central: Path) -> subprocess.CompletedProcess:
    payload = '{"tool_input": {"file_path": "%s"}}' % report
    return subprocess.run(
        ["bash", str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        cwd=project,
        env={
            "PATH": "/usr/bin:/bin",
            "NOTIFY_DRY_RUN": "1",
            "TELEGRAM_CENTRAL_ENV": str(central),
            "HOME": str(project),
        },
    )


# ── hook fires ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "name,rtype,label",
    [
        ("implementer-to-dedo-2026-06-02-1200-cr-ns-011-done.md", "done-report", "done"),
        ("auditor-to-dedo-2026-06-02-1200-some-question.md", "question", "question"),
    ],
)
def test_hook_fires_for_agent_report(tmp_path, name, rtype, label):
    project = _make_project(tmp_path)
    central = _central_env(tmp_path)
    report = _write_report(project, name, frm=name.split("-to-")[0], topic="cr-ns-011 scope", rtype=rtype)

    res = _run_hook(report, project, central)

    assert res.returncode == 0
    assert "DRY: TEST " in res.stdout, res.stdout
    assert f"[{label}]" in res.stdout
    assert "cr-ns-011 scope" in res.stdout
    assert "sample-project" in res.stdout
    # The bot token must never leak.
    assert TOKEN not in res.stdout
    assert TOKEN not in res.stderr


# ── hook no-ops ─────────────────────────────────────────────────────────────


def test_hook_noop_non_channel_path(tmp_path):
    project = _make_project(tmp_path)
    central = _central_env(tmp_path)
    other = project / "backend" / "main.py"
    other.parent.mkdir(parents=True)
    other.write_text("x = 1\n")

    res = _run_hook(other, project, central)

    assert res.returncode == 0
    assert res.stdout.strip() == ""


def test_hook_noop_outbound_dedo_to_implementer(tmp_path):
    project = _make_project(tmp_path)
    central = _central_env(tmp_path)
    report = _write_report(
        project,
        "dedo-to-implementer-2026-06-02-1200-directive.md",
        frm="dedo",
        topic="some directive",
        rtype="directive",
    )

    res = _run_hook(report, project, central)

    assert res.returncode == 0
    assert res.stdout.strip() == ""


def test_hook_noop_uninteresting_type(tmp_path):
    project = _make_project(tmp_path)
    central = _central_env(tmp_path)
    report = _write_report(
        project,
        "implementer-to-dedo-2026-06-02-1200-fyi.md",
        frm="implementer",
        topic="fyi",
        rtype="directive",
    )

    res = _run_hook(report, project, central)

    assert res.returncode == 0
    assert res.stdout.strip() == ""


# ── sender no-ops when config absent ────────────────────────────────────────


def test_sender_noop_without_central_env(tmp_path):
    project = _make_project(tmp_path)
    res = subprocess.run(
        ["bash", str(NOTIFY), "hello"],
        capture_output=True,
        text=True,
        cwd=project,
        env={
            "PATH": "/usr/bin:/bin",
            "NOTIFY_DRY_RUN": "1",
            "TELEGRAM_CENTRAL_ENV": str(tmp_path / "does-not-exist.env"),
            "HOME": str(project),
        },
    )
    assert res.returncode == 0
    assert res.stdout.strip() == ""


def test_sender_noop_without_repo_env(tmp_path):
    project = _make_project(tmp_path, with_env=False)
    central = _central_env(tmp_path)
    res = subprocess.run(
        ["bash", str(NOTIFY), "hello"],
        capture_output=True,
        text=True,
        cwd=project,
        env={
            "PATH": "/usr/bin:/bin",
            "NOTIFY_DRY_RUN": "1",
            "TELEGRAM_CENTRAL_ENV": str(central),
            "HOME": str(project),
        },
    )
    assert res.returncode == 0
    assert res.stdout.strip() == ""
    assert TOKEN not in res.stdout
