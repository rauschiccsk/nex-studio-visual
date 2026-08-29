"""A boot failure must carry the REASON, not only the fact (ICCINT-37).

29.–30.08.2026, nex-productcatalogs. The release smoke reported exactly this and nothing else:

    up exit 1: … container nex-productcatalogs-smoke-migrate-1 exited (1)

WHICH container died. Never WHY. What followed is the whole argument for this file:

  * Dedo read the reason off his OWN reproduction — run by hand, with the project's ``.env`` instead of the
    rendered smoke env — and reported a missing ``DATABASE_URL`` as fact. It was a real problem, but not
    this one; the smoke had a perfectly good URL.
  * The AI Agent, handed the same reasonless brief through the fix card, guessed a healthcheck race and
    fixed that instead. Also a real improvement, also not this.
  * The actual cause sat in the migrate container's log the entire time — ``value too long for type
    character varying(32)``, a 33-character alembic revision id — and it survived both "fixes" untouched.

Two people, two confident wrong diagnoses, from one withheld log. The second finding here is the same shape
one layer down: an empty ``DATABASE_URL=`` placeholder was being rewritten into ``//ci@db`` and injected into
every service, which would one day surface as a boot failure pointing nowhere near its cause.
"""

from __future__ import annotations

import json

import pytest

from backend.services import orchestrator


@pytest.mark.asyncio
async def test_a_boot_failure_carries_the_failing_container_log(monkeypatch) -> None:
    calls: list[list[str]] = []

    async def _fake_step(cmd, timeout):
        calls.append(cmd)
        if "ps" in cmd:
            return 0, json.dumps({"Service": "migrate", "ExitCode": 1}) + "\n" + json.dumps(
                {"Service": "db", "ExitCode": 0}
            )
        if "logs" in cmd:
            assert "migrate" in cmd, "asked for the logs of a container that did not fail"
            return 0, "sqlalchemy.exc.DBAPIError: value too long for type character varying(32)"
        return 0, ""

    monkeypatch.setattr(orchestrator, "_compose_smoke_step", _fake_step)

    detail = await orchestrator._smoke_failure_logs(["docker", "compose", "-p", "x-smoke"])

    assert "character varying(32)" in detail, "the reason was withheld — again"
    assert "--- migrate ---" in detail, "the reason must say which service it came from"
    # The service that exited 0 is not dragged in; a wall of healthy output buries the one line that matters.
    assert "--- db ---" not in detail


@pytest.mark.asyncio
async def test_collecting_the_reason_can_never_break_the_check(monkeypatch) -> None:
    """A diagnostic that can sink the thing it diagnoses is worse than no diagnostic."""

    async def _broken(cmd, timeout):
        if "ps" in cmd:
            return 1, "docker daemon gone"
        raise AssertionError("must not try to read logs when it cannot even list the containers")

    monkeypatch.setattr(orchestrator, "_compose_smoke_step", _broken)
    assert await orchestrator._smoke_failure_logs(["docker", "compose"]) == ""

    async def _garbage(cmd, timeout):
        return 0, "not json at all"

    monkeypatch.setattr(orchestrator, "_compose_smoke_step", _garbage)
    assert await orchestrator._smoke_failure_logs(["docker", "compose"]) == ""


@pytest.mark.asyncio
async def test_nothing_failed_means_nothing_appended(monkeypatch) -> None:
    async def _all_fine(cmd, timeout):
        if "ps" in cmd:
            return 0, json.dumps({"Service": "db", "ExitCode": 0})
        raise AssertionError("no container failed — there is nothing to fetch")

    monkeypatch.setattr(orchestrator, "_compose_smoke_step", _all_fine)
    assert await orchestrator._smoke_failure_logs(["docker", "compose"]) == ""


def test_an_empty_database_url_stays_empty() -> None:
    """``.env.example`` may legitimately carry ``DATABASE_URL=`` — a project whose compose composes the URL
    itself has nothing to put there. Rewriting that produced ``//ci@db``, injected into EVERY service."""
    assert orchestrator._rewrite_smoke_database_url("") == ""
    assert orchestrator._rewrite_smoke_database_url("   ") == "   "


def test_a_real_database_url_is_still_pointed_at_the_compose_db() -> None:
    """The control — the rewrite that MUST keep happening, scheme and driver intact."""
    out = orchestrator._rewrite_smoke_database_url("postgresql+asyncpg://catalogs:CHANGE_ME@localhost/catalogs")
    assert out == "postgresql+asyncpg://catalogs:ci@db/catalogs"


def test_an_empty_placeholder_never_reaches_the_container(tmp_path) -> None:
    """``KEY=`` in .env.example means "fill this in", not "set it to the empty string" (ICCINT-40).

    To a container those are worlds apart: unset falls back to the app's own default, empty string gets
    PARSED. ``CORS_ALLOW_ORIGINS=`` copied verbatim killed the migrate container with
    ``SettingsError: error parsing value for field "cors_allow_origins"`` — the third wrong-looking cause in
    one build, and the first one anybody actually SAW, because the log capture landed the same day.
    """
    example = tmp_path / ".env.example"
    example.write_text(
        "\n".join(
            [
                "# komentár zostáva",
                "CORS_ALLOW_ORIGINS=",
                "DATABASE_URL=",
                "APP_NAME=catalogs",
                "",
                "LOG_LEVEL=   ",
            ]
        ),
        encoding="utf-8",
    )
    dst = tmp_path / "smoke.env"
    assert orchestrator._render_smoke_env(example, dst)
    rendered = dst.read_text(encoding="utf-8")

    assert "CORS_ALLOW_ORIGINS" not in rendered
    assert "DATABASE_URL" not in rendered
    assert "LOG_LEVEL" not in rendered, "whitespace-only is empty too"
    # …while everything that HAS a value is untouched, and comments survive.
    assert "APP_NAME=catalogs" in rendered
    assert "# komentár zostáva" in rendered
    # POSTGRES_PASSWORD is APPENDED even when the example omitted it — the compose fail-fast guard needs it.
    assert "POSTGRES_PASSWORD=" in rendered and rendered.strip().endswith("ci")
