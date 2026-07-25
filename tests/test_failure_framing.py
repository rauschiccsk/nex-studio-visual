"""Plain-language failure framing (self-sufficiency kernel).

Focus: ``release_failure_headline`` (v4.0.47) must NOT say "the app didn't start" when the app actually
booted and only a behavioural acceptance check (the Aktualizácie changelog gate) failed — the engine folds
that acceptance failure into the same smoke-FAIL signal, and a false "Appka sa nespustila" sent the manager
+ the AI-Agent fixer boot-debugging instead of at the failing check.
"""

from __future__ import annotations

from backend.services.failure_framing import humanize_release_failure, release_failure_headline


def test_headline_aktualizacie_says_app_booted_not_boot_fail():
    detail = (
        "Aktualizácie chýba: /api/v1/release-notes neobsahuje verziu v0.1.0 (vrátené: ['0.1.0 — Initial prototype'])"
    )
    headline = release_failure_headline(detail)
    assert "nespustila" not in headline.lower()  # the app DID boot — never claim otherwise
    assert "Aktualizácie" in headline
    assert headline[0].isupper() and not headline.endswith("—")  # a complete, capitalised sentence


def test_headline_real_boot_failure_still_says_did_not_start():
    detail = "app did not boot / not responding within 900s: connection refused"
    headline = release_failure_headline(detail)
    assert "nespustila" in headline.lower()  # a genuine boot failure IS framed as "did not start"
    assert headline[0].isupper()


def test_headline_frontend_not_serving_is_boot_family():
    headline = release_failure_headline("frontend 'web' not serving within 900s: ...")
    assert "nespustil" in headline.lower()  # webové rozhranie sa nespustilo


def test_humanize_still_returns_after_dash_clause():
    # The headline builder reuses humanize for the non-Aktualizácie cases — keep humanize lowercase-initial.
    clause = humanize_release_failure("something totally unrecognised")
    assert clause and clause[0].islower()
