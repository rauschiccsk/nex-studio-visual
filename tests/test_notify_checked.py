"""``notify.send_telegram_checked`` (v4.0.52) — the "Poslať test" self-service path.

Parses the notify script's ``NOTIFY_CHECK=1`` stdout marker (``TELEGRAM_OK`` / ``TELEGRAM_FAIL:<desc>``)
into ``(ok, plain-Slovak detail)``. The bot token never appears (the script prints only the parsed API
``ok`` / ``description``). These pin the parsing without invoking the real script / Telegram.
"""

from __future__ import annotations

import asyncio

from backend.services import notify


class _FakeProc:
    def __init__(self, out: bytes) -> None:
        self._out = out

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._out, b""


def _stub_proc(monkeypatch, out: bytes) -> None:
    async def _fake(*_a, **_k):
        return _FakeProc(out)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake)


async def test_checked_ok(monkeypatch):
    _stub_proc(monkeypatch, b"TELEGRAM_OK\n")
    ok, detail = await notify.send_telegram_checked("hi", "123")
    assert ok is True
    assert "odoslan" in detail.lower()


async def test_checked_chat_not_found_gives_actionable_hint(monkeypatch):
    _stub_proc(monkeypatch, b"TELEGRAM_FAIL:Bad Request: chat not found\n")
    ok, detail = await notify.send_telegram_checked("hi", "999")
    assert ok is False
    assert "chat not found" in detail.lower()
    assert "/start" in detail  # the actionable "did you start the bot?" hint


async def test_checked_other_failure_passes_the_description(monkeypatch):
    _stub_proc(monkeypatch, b"TELEGRAM_FAIL:Too Many Requests\n")
    ok, detail = await notify.send_telegram_checked("hi", "999")
    assert ok is False
    assert "Too Many Requests" in detail


async def test_checked_no_marker_is_config_error(monkeypatch):
    _stub_proc(monkeypatch, b"")  # script no-oped early (missing token/env) → no marker
    ok, detail = await notify.send_telegram_checked("hi", "123")
    assert ok is False
    assert "nakonfigurovan" in detail.lower()


async def test_checked_empty_chat_id_short_circuits(monkeypatch):
    # Never even spawns the script when there is no chat_id.
    called = False

    async def _boom(*_a, **_k):
        nonlocal called
        called = True
        return _FakeProc(b"")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _boom)
    ok, detail = await notify.send_telegram_checked("hi", "")
    assert ok is False and "nie je nastaven" in detail.lower()
    assert called is False
