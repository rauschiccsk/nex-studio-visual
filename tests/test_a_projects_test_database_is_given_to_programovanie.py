"""DEV-209 — a project's own test database is given to Programovanie; a turn the engine cannot equip is not a crash.

23.10.2026 NEX Manager 1.4.0, task #21.1.1, gave the backend tests a database of their own as its approved design
said: a second PostgreSQL ``test-db`` behind the ``test`` profile and a test runner whose ``TEST_DATABASE_URL`` points
at it. From task #21.1.2 on the engine refused every Programovanie turn — it gives a turn ONE PostgreSQL, under the name
of the first database service, and took the second for a supporting service it does not supply. The orchestrator
then filed the refusal as a crash of the agent: it retried it, and the Manažér read „Agent stratil spojenie / spadol
… Môžeš pokračovať“ — and „Pokračovať“ failed the same way. The project also declares its driver only in
``TEST_DATABASE_URL`` (``pg8000``), so the ``DATABASE_URL`` the engine composed named ``asyncpg``, which it does not
have. Director 23.10.2026: „Agent stratil spojenie a spadol, zisti prosím príčinu“, „Áno, súhlasím, založ tiket a
oprav to“.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from backend.services import build_db, build_sandbox, claude_agent
from tests.test_build_db import TOKEN, _plan, docker_calls  # noqa: F401 — fixture

#: NEX Manager 1.4.0 as task #21.1.1 left it, reduced to what the planner reads.
NEX_MANAGER = (
    "services:\n"
    "  db:\n"
    "    image: postgres:16-alpine\n"
    "    environment:\n"
    "      POSTGRES_USER: ${POSTGRES_USER:?set}\n"
    "      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set}\n"
    "  backend:\n"
    "    build: ./backend\n"
    "    env_file: .env\n"
    "  test-db:\n"
    "    image: postgres:16-alpine\n"
    "    profiles: [test]\n"
    "    environment:\n"
    "      POSTGRES_USER: test\n"
    "      POSTGRES_DB: test\n"
    "      POSTGRES_HOST_AUTH_METHOD: trust\n"
    "  test:\n"
    "    build:\n"
    "      context: .\n"
    "      dockerfile: Dockerfile.test\n"
    "    profiles: [test]\n"
    "    environment:\n"
    "      TEST_DATABASE_URL: postgresql+pg8000://test@test-db:5432/test\n"
    "    depends_on:\n"
    "      test-db:\n"
    "        condition: service_healthy\n"
)


@pytest.fixture
def sandbox(monkeypatch, tmp_path):
    """``build_run_argv`` maps the project's in-container path to the host's, as in ``test_build_sandbox``."""
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", Path("/opt/projects"))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-test")
    monkeypatch.setattr(build_sandbox.settings, "build_sandbox_image", "nex-studio-visual-backend:vTEST")
    monkeypatch.setattr(build_sandbox, "_CLAUDE_HOME_DIR", str(tmp_path / "claude-home"))


def _url(plan: build_db.BuildDatabase, host: str, driver: str = "postgresql+pg8000") -> str:
    return f"{driver}://{plan.user}:{plan.password}@{host}:5432/{plan.database}"


def test_the_test_database_does_not_stop_the_turn(tmp_path):
    plan = _plan(tmp_path, NEX_MANAGER)

    assert (plan.alias, plan.other_aliases) == ("db", ("test-db",))


def test_both_database_names_reach_the_one_database(tmp_path):
    argv = build_db.container_run_argv(_plan(tmp_path, NEX_MANAGER))

    aliases = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--network-alias"]
    assert aliases == ["db", "test-db"]


def test_the_turn_gets_the_test_runners_url_and_its_driver(tmp_path):
    plan = _plan(tmp_path, NEX_MANAGER)

    assert plan.test_url == _url(plan, "test-db")
    assert plan.url == _url(plan, "db"), "the app's own URL must name the driver the project has, not asyncpg"


def test_the_sandbox_is_handed_both_urls(tmp_path, sandbox):
    plan = _plan(tmp_path, NEX_MANAGER)

    argv = build_sandbox.build_run_argv(
        project_slug="acme", container_name="c", claude_argv=["claude", "-p", "x"], database=plan
    )

    assert f"DATABASE_URL={plan.url}" in argv
    assert f"TEST_DATABASE_URL={plan.test_url}" in argv
    assert "TEST_DATABASE_URL" not in repr(plan) and plan.password not in repr(plan)


def test_a_project_without_a_test_runner_gets_no_test_url(tmp_path, sandbox):
    plan = _plan(tmp_path, "services:\n  db:\n    image: postgres:16\n  backend:\n    build: .\n")

    argv = build_sandbox.build_run_argv(
        project_slug="acme", container_name="c", claude_argv=["claude", "-p", "x"], database=plan
    )

    assert (plan.other_aliases, plan.test_url) == ((), None)
    assert not any(arg.startswith("TEST_DATABASE_URL=") for arg in argv)


def test_a_test_runner_pointing_elsewhere_gets_the_one_database(tmp_path):
    """``localhost`` (a developer's own machine) is not a service of the project — the turn's database is the one."""
    plan = _plan(
        tmp_path,
        "services:\n  db:\n    image: postgres:16\n"
        "  test:\n    build: .\n    environment:\n      TEST_DATABASE_URL: postgresql+psycopg://t@localhost:5432/t\n",
    )

    assert plan.test_url == _url(plan, "db", "postgresql+psycopg")


def test_any_other_service_still_stops_the_turn_and_is_named(tmp_path):
    with pytest.raises(build_db.UnsupportedProjectService, match="cache"):
        _plan(tmp_path, NEX_MANAGER + "  cache:\n    image: redis:7-alpine\n")


def test_a_second_database_name_that_is_not_one_dns_label_is_not_an_alias(tmp_path):
    plan = _plan(tmp_path, NEX_MANAGER.replace("test-db", "Test_DB"))

    assert plan.other_aliases == ()
    assert plan.test_url == _url(plan, "db")


async def test_every_alias_is_re_checked_where_the_container_is_started(tmp_path, docker_calls):  # noqa: F811
    plan = _plan(tmp_path, NEX_MANAGER)
    hijack = build_db.BuildDatabase(**{**plan.__dict__, "other_aliases": ("api.anthropic.com",)})

    with pytest.raises(build_db.BuildDatabaseUnavailable):
        await build_db.start(hijack)
    assert docker_calls == [], "the refusal must come BEFORE any network or container exists"


# --- a turn the engine cannot equip is the cockpit's problem, not the agent's crash ------------------------------


REFUSAL = (
    "build database: the project declares supporting service(s) the isolated Programovanie turn cannot be given: cache."
)


@pytest.fixture
def refused_turn(db_session, monkeypatch):
    """A Programovanie task whose turn the engine refuses to equip; records every CLI call and every Dedo message."""
    from backend.services import orchestrator
    from tests.test_build_robustness_crash import _make_version, _no_baseline_git, _seed_one_task, _seed_programovanie

    version, project = _make_version(db_session)
    _seed_programovanie(db_session, version.id)
    _seed_one_task(db_session, version, project)
    _no_baseline_git(monkeypatch)
    calls: list[str] = []
    to_dedo: list[str] = []

    async def _refuse(**_kw):
        calls.append("invoke")
        raise build_db.UnsupportedProjectService(REFUSAL)

    async def _escalate(**kw):
        to_dedo.append(kw["dedo_message"])

    monkeypatch.setattr(orchestrator, "invoke_claude", _refuse)
    monkeypatch.setattr(orchestrator, "_resolve_orch_session", lambda db, slug, role: (uuid.uuid4(), False))
    monkeypatch.setattr(orchestrator, "_resolve_dispatch_overrides", lambda db, vid, role: (None, None))
    monkeypatch.setattr(orchestrator.dedo_escalation, "escalate_to_dedo", _escalate)
    return version, calls, to_dedo


async def test_a_turn_the_engine_cannot_equip_is_not_retried(db_session, refused_turn):
    from backend.services import orchestrator

    version, calls, _to_dedo = refused_turn

    await orchestrator.run_dispatch(db_session, version.id)

    assert calls == ["invoke"], "an environment that cannot be given will not be given on a second try either"


async def test_it_stops_the_build_as_the_cockpits_problem_with_the_reason(db_session, refused_turn):
    from backend.db.models.pipeline import PipelineMessage
    from backend.services import orchestrator
    from tests.test_build_robustness_crash import _tasks

    version, _calls, to_dedo = refused_turn

    state = await orchestrator.run_dispatch(db_session, version.id)

    assert (state.status, state.block_reason) == ("blocked", "framework_issue")
    assert orchestrator.determine_available_actions(state) == {"nahlasit_znova"}
    assert len(to_dedo) == 1 and "cache" in to_dedo[0] and "#1.1.1" in to_dedo[0]
    texts = [m.content for m in db_session.query(PipelineMessage).filter_by(version_id=version.id)]
    assert not any("stratil spojenie" in t for t in texts), "the Manažér was told the agent crashed"
    assert [t.status for t in _tasks(db_session, version.id)] == ["in_progress"]


def test_the_agents_charter_says_what_the_turn_is_given():
    charter = " ".join((Path(__file__).resolve().parents[1] / "templates" / "ai-agent-charter.md").read_text().split())

    assert "jednu databázu pod menom každej databázovej služby projektu (aj skúšobnej, napr. `test-db`)" in charter
    assert "zastaví ako svoju chybu pre Deda a vypíše jej meno" in charter
