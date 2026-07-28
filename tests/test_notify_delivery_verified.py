"""A Telegram nudge must be VERIFIABLE, and a nudge with no way back into the cockpit must say so.

Two audit findings meet here:

* ``scripts/notify_telegram.sh`` exited 0 on EVERY path — including its silent no-ops — and the backend
  inspected nothing, so "notification sent" was structurally unknowable: a deployment with no bot token
  looked identical to one delivering every nudge.
* ``app_public_url`` is empty in production, so the ``/riadiace-centrum`` deep link was silently dropped
  and an away Manažér got a ping with no way back in.
"""

from __future__ import annotations

import asyncio
import subprocess
import types
import uuid
from pathlib import Path

import pytest

from backend.api.routes import health
from backend.services import notify, pipeline_runner

REPO_ROOT = Path(__file__).resolve().parents[1]
NOTIFY = REPO_ROOT / "scripts" / "notify_telegram.sh"
TOKEN = "SECRET-bot-token-123456:should-never-appear"


def _run_script(tmp_path: Path, *args: str, check_mode: bool, with_token: bool) -> subprocess.CompletedProcess:
    central = tmp_path / "central.env"
    central.write_text(f"TELEGRAM_ICC_BOT_TOKEN={TOKEN}\n" if with_token else "# empty\n")
    env = {
        "PATH": "/usr/bin:/bin",
        "TELEGRAM_CENTRAL_ENV": str(central),
        "HOME": str(tmp_path),
        # DRY so a configured token never reaches Telegram from a test.
        "NOTIFY_DRY_RUN": "1",
    }
    if check_mode:
        env["NOTIFY_CHECK"] = "1"
    return subprocess.run(["bash", str(NOTIFY), *args], capture_output=True, text=True, cwd=str(tmp_path), env=env)


# ── the script: checked mode now reports every no-op ────────────────────────


@pytest.mark.parametrize(
    "args,with_token,expected",
    [
        (("hello",), False, "bot token"),  # server has no token configured
        (("",), True, "prázdna správa"),  # nothing to send
        (("hello",), True, "chat ID"),  # no chat_id arg and cwd is not a git repo with .env
    ],
)
def test_checked_mode_names_the_reason_and_fails(tmp_path, args, with_token, expected) -> None:
    res = _run_script(tmp_path, *args, check_mode=True, with_token=with_token)
    assert res.returncode != 0, "checked mode must FAIL when nothing was sent — exit 0 hid every no-op"
    assert res.stdout.startswith("TELEGRAM_FAIL:")
    assert expected in res.stdout
    assert TOKEN not in res.stdout and TOKEN not in res.stderr


@pytest.mark.parametrize("with_token", [True, False])
def test_default_mode_stays_a_silent_exit_zero(tmp_path, with_token) -> None:
    """Agent hooks call this fire-and-forget — a missing notify config must never fail their session."""
    res = _run_script(tmp_path, "hello", check_mode=False, with_token=with_token)
    assert res.returncode == 0
    assert res.stdout.strip() == ""
    assert TOKEN not in res.stdout and TOKEN not in res.stderr


# ── the backend: it now learns the outcome ──────────────────────────────────


class _FakeProc:
    def __init__(self, out: bytes) -> None:
        self._out = out

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._out, b""


@pytest.fixture(autouse=True)
def _fresh_ledger(monkeypatch):
    monkeypatch.setattr(notify, "ledger", notify.DeliveryLedger())


def _stub_script(monkeypatch, out: bytes) -> None:
    async def _fake(*_a, **_k):
        return _FakeProc(out)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake)


async def test_a_delivered_nudge_is_reported_true_and_counted(monkeypatch) -> None:
    _stub_script(monkeypatch, b"TELEGRAM_OK\n")
    assert await notify.send_telegram("hi", "123") is True
    assert notify.ledger.as_dict()["delivered"] == 1
    assert notify.ledger.as_dict()["failed"] == 0


async def test_a_rejected_nudge_is_reported_false_with_the_reason(monkeypatch) -> None:
    _stub_script(monkeypatch, b"TELEGRAM_FAIL:Bad Request: chat not found\n")
    assert await notify.send_telegram("hi", "999") is False
    tally = notify.ledger.as_dict()
    assert tally["failed"] == 1 and tally["delivered"] == 0
    assert "chat not found" in tally["last_failure"]


async def test_an_unconfigured_server_is_a_failure_not_a_success(monkeypatch) -> None:
    """The audited behaviour: no marker at all (the script no-oped) used to be indistinguishable from a
    delivered message, because nothing was inspected and every exit was 0."""
    _stub_script(monkeypatch, b"")
    assert await notify.send_telegram("hi", "123") is False
    assert notify.ledger.as_dict()["failed"] == 1


async def test_empty_chat_id_never_spawns_and_is_not_counted(monkeypatch) -> None:
    called = False

    async def _boom(*_a, **_k):
        nonlocal called
        called = True
        return _FakeProc(b"")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _boom)
    assert await notify.send_telegram("hi", "") is False
    assert called is False
    assert notify.ledger.as_dict()["attempted"] == 0


def test_health_publishes_the_delivery_tally(monkeypatch) -> None:
    monkeypatch.setattr(notify.ledger, "attempted", 4)
    monkeypatch.setattr(notify.ledger, "delivered", 0)
    monkeypatch.setattr(notify.ledger, "failed", 4)
    monkeypatch.setattr(notify.ledger, "last_failure", "chat not found")
    tally = health.health_check()["telegram"]
    assert tally["attempted"] == 4 and tally["delivered"] == 0, "a deployment whose nudges never land"
    assert tally["last_failure"] == "chat not found"


# ── the cockpit deep link ───────────────────────────────────────────────────


class _FakeDb:
    """Stands in for the Session — ``_maybe_notify`` only reads the project/version label from it."""

    def execute(self, *_a, **_k):
        return types.SimpleNamespace(one_or_none=lambda: ("Projekt", "v1.0.0"))


@pytest.fixture(autouse=True)
def _reset_link_warning(monkeypatch):
    monkeypatch.setattr(pipeline_runner, "_no_link_warning_logged", False)


def test_configured_url_becomes_the_deep_link(monkeypatch) -> None:
    monkeypatch.setattr(pipeline_runner.settings, "app_public_url", "https://studio.isnex.eu/")
    assert pipeline_runner._cockpit_link() == "\nhttps://studio.isnex.eu/riadiace-centrum"


def test_missing_url_says_so_instead_of_dropping_the_link(monkeypatch) -> None:
    monkeypatch.setattr(pipeline_runner.settings, "app_public_url", "")
    link = pipeline_runner._cockpit_link()
    assert "APP_PUBLIC_URL" in link, "the away Manažér must learn WHY there is no way back into the board"
    assert link != ""


def test_whitespace_only_url_counts_as_missing(monkeypatch) -> None:
    monkeypatch.setattr(pipeline_runner.settings, "app_public_url", "   ")
    assert "APP_PUBLIC_URL" in pipeline_runner._cockpit_link()


@pytest.mark.parametrize("status", ["awaiting_manazer", "done"])
async def test_the_nudge_body_carries_the_missing_link_note(monkeypatch, status) -> None:
    monkeypatch.setattr(pipeline_runner.settings, "app_public_url", "")
    monkeypatch.setattr(pipeline_runner, "_notify_chat_ids", lambda _db, _vid: ["123"])
    monkeypatch.setattr(pipeline_runner.registry, "away_director_ids", lambda _vid: ["u"])
    monkeypatch.setattr(pipeline_runner.registry, "active_director_ids", lambda _vid: [])

    sent: list[tuple[str, str]] = []

    async def _fake_send(message: str, chat_id: str) -> bool:
        sent.append((message, chat_id))
        return True

    monkeypatch.setattr(pipeline_runner.notify, "send_telegram", _fake_send)

    await pipeline_runner._maybe_notify(_FakeDb(), uuid.uuid4(), types.SimpleNamespace(status=status))

    assert len(sent) == 1
    assert "APP_PUBLIC_URL" in sent[0][0]


def test_health_reports_the_missing_public_url(monkeypatch) -> None:
    monkeypatch.setattr(health.settings, "app_public_url", "")
    assert health.health_check()["app_public_url_configured"] is False
    monkeypatch.setattr(health.settings, "app_public_url", "https://studio.isnex.eu")
    assert health.health_check()["app_public_url_configured"] is True
