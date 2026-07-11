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


def test_compose_interpolation_error_is_a_config_cause_not_the_app() -> None:
    # The nex-payables 1.1.0 shape: docker compose could not interpolate a missing env value, so the app
    # never started + no check ever ran. Must NOT be framed as "some checks failed" (reads as an app bug) —
    # it is a DEPLOYMENT-SETTINGS cause, explicitly "not the app's code".
    phrase = humanize_release_failure("up exit 1: error while interpolating services.db.environment.POSTGRES_PASSWORD")
    assert "nastavení nasadenia" in phrase
    assert "nie je to chyba v kóde" in phrase
    assert "automatická skúška" not in phrase  # not misattributed to a failed post-launch check
    _assert_plain(phrase)


def test_port_conflict_is_a_config_cause_not_the_app() -> None:
    phrase = humanize_release_failure("Error response from daemon: driver failed: port is already allocated")
    assert "port" in phrase
    assert "nie je to chyba v kóde" in phrase
    _assert_plain(phrase)


def test_unknown_detail_falls_back_to_plain_never_raw() -> None:
    phrase = humanize_release_failure("Traceback (most recent call last): KeyError 'foo'")
    assert phrase == "skúška po spustení zlyhala"
    _assert_plain(phrase)


@pytest.mark.parametrize("raw", ["", "   ", None])
def test_empty_detail_is_safe(raw) -> None:
    phrase = humanize_release_failure(raw)  # type: ignore[arg-type]
    _assert_plain(phrase)
