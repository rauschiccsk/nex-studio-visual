"""Unit tests for the agent → Dedo escalation delivery helper (Director observation #6).

:mod:`backend.services.dedo_escalation` delivers a ``framework_issue`` escalation two ways (A+B): it writes
an audit file into ``<DEDO_CHANNEL_DIR>/inbox/`` and pings the project owner over Telegram. These pin the
file FORMAT (README frontmatter) + that it lands in the env-configurable channel dir + that Telegram is
called — with the notify script mocked (never actually shells out) and a tmp channel dir (never touches the
real ``.dedo-channel``).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.services import dedo_escalation


def test_build_channel_file_format() -> None:
    now = datetime(2026, 7, 7, 14, 5, tzinfo=timezone.utc)
    filename, body = dedo_escalation.build_channel_file(
        project_slug="nex-payables",
        version_number="1.2.0",
        dedo_message="Smoke test nevie nabehnúť — chýba docker socket. Treba to opraviť v NEX Studiu.",
        context="Projekt: nex-payables · Fáza: programovanie",
        now=now,
    )
    # Filename convention: system-to-dedo-YYYY-MM-DD-HHMM-framework-issue-<slug>.md
    assert filename == "system-to-dedo-2026-07-07-1405-framework-issue-nex-payables.md"
    # YAML frontmatter per .dedo-channel/README.md
    assert body.startswith("---\n")
    assert "from: system\n" in body
    assert "to: dedo\n" in body
    assert "type: flag\n" in body
    # The agent's message + context are carried through.
    assert "Smoke test nevie nabehnúť" in body
    assert "Projekt: nex-payables · Fáza: programovanie" in body


@pytest.mark.asyncio
async def test_escalate_writes_file_and_pings_telegram(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dedo_escalation.settings, "dedo_channel_dir", str(tmp_path))
    sent: list[tuple[str, str]] = []

    async def _fake_send(message: str, chat_id: str) -> None:
        sent.append((message, chat_id))

    monkeypatch.setattr(dedo_escalation.notify, "send_telegram", _fake_send)

    now = datetime(2026, 7, 7, 9, 30, tzinfo=timezone.utc)
    path = await dedo_escalation.escalate_to_dedo(
        project_slug="acme-books",
        version_number="0.3.0",
        dedo_message="Alembic drift-test zlyháva kvôli chýbajúcej migrácii v NEX Studiu.",
        context="ctx",
        owner_chat_id="7204918893",
        now=now,
    )

    # (A) the file landed in the env-configured channel dir's inbox/, well-formed.
    assert path is not None
    assert path.parent == tmp_path / "inbox"
    assert path.exists()
    written = path.read_text(encoding="utf-8")
    assert "Alembic drift-test zlyháva" in written
    assert "type: flag" in written

    # (B) the owner got a Telegram ping carrying the headline + a message preview.
    assert len(sent) == 1
    msg, chat_id = sent[0]
    assert chat_id == "7204918893"
    assert "NEX Studio potrebuje opravu (Dedo)" in msg
    assert "acme-books" in msg


@pytest.mark.asyncio
async def test_escalate_skips_telegram_when_no_chat_id(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(dedo_escalation.settings, "dedo_channel_dir", str(tmp_path))
    calls: list[str] = []

    async def _fake_send(message: str, chat_id: str) -> None:
        calls.append(chat_id)

    monkeypatch.setattr(dedo_escalation.notify, "send_telegram", _fake_send)

    path = await dedo_escalation.escalate_to_dedo(
        project_slug="p",
        version_number="1.0.0",
        dedo_message="msg",
        context="ctx",
        owner_chat_id=None,
        now=datetime(2026, 7, 7, 0, 0, tzinfo=timezone.utc),
    )
    # (A) still writes the audit file; (B) skipped (no recipient) — never a spurious empty-chat_id send.
    assert path is not None and path.exists()
    assert calls == []


@pytest.mark.asyncio
async def test_escalate_never_raises_when_channel_dir_unwritable(monkeypatch) -> None:
    # A v3 instance without the .dedo-channel mount: the write fails, but the escalation must NOT crash the
    # settle path (the block is already durable in the DB). Point the dir at an unwritable location.
    monkeypatch.setattr(dedo_escalation.settings, "dedo_channel_dir", "/proc/nonexistent/cannot-write")

    async def _fake_send(message: str, chat_id: str) -> None:
        pass

    monkeypatch.setattr(dedo_escalation.notify, "send_telegram", _fake_send)

    path = await dedo_escalation.escalate_to_dedo(
        project_slug="p",
        version_number="1.0.0",
        dedo_message="msg",
        context="ctx",
        owner_chat_id="123",
        now=datetime(2026, 7, 7, 0, 0, tzinfo=timezone.utc),
    )
    assert path is None  # write failed gracefully, returned None, no exception
