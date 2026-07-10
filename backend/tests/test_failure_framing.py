"""Plain-language failure framing (self-sufficiency kernel, 2026-07-10).

``humanize_release_failure`` turns the RAW release-smoke / boot / acceptance failure details the engine
produces into a plain-Slovak WHY a non-expert Manažér can read. Pins each known raw shape → its plain phrase,
and the guarantee that NO raw English / tool-output fragment leaks through (incl. the unrecognised fallback).
"""

from __future__ import annotations

import pytest

from backend.services.failure_framing import humanize_release_failure

# Raw English / tool fragments that must NEVER appear in a manager-facing phrase.
_RAW_LEAKS = ["did not boot", "not responding", "not serving", "release_smoke_test", "exit ", "within 900s"]


def _assert_plain(phrase: str) -> None:
    assert phrase and phrase.strip(), "framing must never be empty"
    low = phrase.lower()
    for leak in _RAW_LEAKS:
        assert leak not in low, f"raw fragment {leak!r} leaked into the manager phrase: {phrase!r}"


def test_boot_timeout_with_seconds_is_humanised_to_minutes() -> None:
    phrase = humanize_release_failure("app did not boot / not responding within 900s: connection refused")
    assert "nespustila do 15 min" in phrase
    _assert_plain(phrase)


def test_boot_timeout_without_seconds_falls_back_to_plain() -> None:
    phrase = humanize_release_failure("app did not boot: container exited")
    assert "nespustila" in phrase
    _assert_plain(phrase)


def test_frontend_not_serving_is_humanised() -> None:
    phrase = humanize_release_failure("frontend 'frontend' not serving on :80 after 120s: 502 Bad Gateway")
    assert "webové rozhranie" in phrase
    _assert_plain(phrase)


def test_acceptance_script_exit_is_humanised() -> None:
    phrase = humanize_release_failure("release_smoke_test.sh exit 1: assertion FEATURE#3 failed: expected 200")
    assert "automatická skúška" in phrase
    _assert_plain(phrase)


def test_unknown_detail_falls_back_to_plain_never_raw() -> None:
    phrase = humanize_release_failure("Traceback (most recent call last): KeyError 'foo'")
    assert phrase == "skúška po spustení zlyhala"
    _assert_plain(phrase)


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_empty_detail_is_safe(raw) -> None:
    phrase = humanize_release_failure(raw)  # type: ignore[arg-type]
    _assert_plain(phrase)
