"""A passing check reports what it MEASURED; a lost turn reports what SURVIVED (ICCINT-44, ICCINT-45).

Two sentences the Director met on one long build day, both reassuring, both content-free.

**ICCINT-44.** Every round the screen read the same line — *"Kontrola po oprave — appka sa spustila"* with
``app booted + responds`` behind the disclosure. Eleven characters of English on an otherwise Slovak screen,
identical whatever had happened, because the probe returned a constant and everything it had measured was
thrown away. On 31.08.2026 that hid the fact that mattered: the ``test`` container was still RUNNING when the
check looked, and later turned out never to finish at all. "Kontrola prešla" reads as "testy prešli".
Director: *"Nemal by Agent vracať nejaké zmyslupné hlásenie?"* — the line was not even the agent's; it was
the engine's.

**ICCINT-45.** A 40-minute turn ended with nothing committed. The transcript said *"žiadna zmena nezistená"*
and the instruction directly under it said *"hotové zmeny sú zapísané, môžeš pokračovať"*. Two sentences on
one screen, contradicting each other, and the reassuring one was the lie. The engine had counted the commits
all along — the sentence simply never asked.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services import orchestrator


def _stack(roles: dict) -> orchestrator._SmokeStack:
    return orchestrator._SmokeStack(
        base=["docker", "compose", "-p", "x-smoke"],
        compose=Path("/x/docker-compose.yml"),
        override=Path("/x/override.yml"),
        project="x-smoke",
        roles=roles,
        up_rc=0,
        up_detail="",
    )


WEB_APP = {"backend": "backend", "frontend": "frontend", "db": "db"}


# ── ICCINT-44 ─────────────────────────────────────────────────────────────────


def test_a_container_still_running_is_named_and_disclaimed() -> None:
    """THE defect. The check does not judge a one-shot mid-flight — that is a deliberate scope decision — but
    the Manažér has to be told, or "Kontrola prešla" silently means "testy prešli"."""
    detail = orchestrator._boot_pass_detail(
        _stack(WEB_APP),
        {
            "db": ("running", 0),
            "backend": ("running", 0),
            "frontend": ("running", 0),
            "migrate": ("exited", 0),
            "test": ("running", 0),
        },
    )
    assert "test" in detail, "the container still running went unmentioned"
    assert "neposudzuje" in detail, "nothing said its result was not judged"


def test_nothing_extra_is_said_when_there_is_nothing_extra() -> None:
    """The control. A settled stack must not grow the sentence — no news, no noise."""
    detail = orchestrator._boot_pass_detail(
        _stack(WEB_APP),
        {"db": ("running", 0), "backend": ("running", 0), "frontend": ("running", 0), "migrate": ("exited", 0)},
    )
    assert "neposudzuje" not in detail
    assert "migrate" in detail, "the one-shot that finished cleanly is worth saying"


def test_two_different_stacks_do_not_read_the_same() -> None:
    """The strictest one: a constant satisfies every other assertion here. This is what it cannot pass."""
    settled = orchestrator._boot_pass_detail(
        _stack(WEB_APP), {"backend": ("running", 0), "frontend": ("running", 0), "migrate": ("exited", 0)}
    )
    mid_flight = orchestrator._boot_pass_detail(
        _stack(WEB_APP),
        {"backend": ("running", 0), "frontend": ("running", 0), "migrate": ("exited", 0), "test": ("running", 0)},
    )
    assert settled != mid_flight


def test_the_managers_screen_carries_no_english() -> None:
    detail = orchestrator._boot_pass_detail(
        _stack(WEB_APP), {"backend": ("running", 0), "frontend": ("running", 0), "test": ("running", 0)}
    )
    for english in ("app booted", "responds", "still running", "not judged"):
        assert english not in detail.lower()


@pytest.mark.asyncio
async def test_the_reported_detail_is_the_measured_one_end_to_end(monkeypatch) -> None:
    """Through the real :func:`_boot_leg`, not the helper alone — the probe's own words must not leak back."""

    async def _step(cmd, timeout):
        if "ps" in cmd:
            return 0, "\n".join(
                json.dumps(r)
                for r in (
                    {"Service": "backend", "State": "running", "ExitCode": 0},
                    {"Service": "frontend", "State": "running", "ExitCode": 0},
                    {"Service": "test", "State": "running", "ExitCode": 0},
                )
            )
        return 0, ""

    async def _probe(stack):
        return True, "backend responded 200 /health"  # the old English detail

    monkeypatch.setattr(orchestrator, "_compose_smoke_step", _step)
    monkeypatch.setattr(orchestrator, "_run_app_starts_smoke", _probe)

    ok, detail = await orchestrator._boot_leg(_stack(WEB_APP))
    assert ok is True
    assert "200" not in detail and "responded" not in detail
    assert "test" in detail and "neposudzuje" in detail


# ── ICCINT-45 ─────────────────────────────────────────────────────────────────


def test_a_lost_turn_with_nothing_committed_says_so() -> None:
    """The sentence the Director read as "your work is safe" over 40 minutes of work that was gone."""
    text = orchestrator._envelope_loss_next_action("timeout", 2400, None, 0)
    assert "40 min" in text
    assert "žiadna zmena" in text, "still claimed something had been written"
    assert "zapísané zmeny" not in text
    # …and it names the consequence, not just the fact.
    assert "preč" in text


def test_a_lost_turn_that_did_commit_says_how_many() -> None:
    text = orchestrator._envelope_loss_next_action("timeout", 2400, None, 3)
    assert "3 commit" in text and "git log" in text


def test_an_unmeasurable_turn_claims_neither_way() -> None:
    """No audit → no claim. Unknown is not "safe" and not "lost"."""
    text = orchestrator._envelope_loss_next_action("timeout", 2400, None, None)
    assert "nevieme" in text
    assert "žiadna zmena" not in text and "zapísané zmeny" not in text


def test_a_crash_is_held_to_the_same_standard() -> None:
    """The crash branch carried the identical false reassurance; fixing only the timeout would leave half."""
    text = orchestrator._envelope_loss_next_action("crash", 2400, "/var/log/x", 0)
    assert "spojenie" in text and "žiadna zmena" in text
    assert "/var/log/x" in text, "the diagnostic log path must survive"


def test_the_two_sentences_on_the_screen_can_no_longer_disagree() -> None:
    """The actual defect was a CONTRADICTION: the transcript and the instruction disagreed. Both are built
    from the same count now, so for every count they tell the same story."""
    for count, expected in ((0, "žiadna zmena"), (2, "2 commit")):
        assert expected in orchestrator._saved_work_phrase(count)
        assert expected in orchestrator._envelope_loss_next_action("timeout", 2400, None, count)
