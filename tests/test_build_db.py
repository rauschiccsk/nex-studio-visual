"""The throwaway PostgreSQL a sandboxed Programovanie turn tests against (ICCINT-16 STEP 2).

WHAT THESE TESTS ARE ABOUT. :mod:`tests.test_build_sandbox` pins the WIRING — that the turn joins the
database's network, gets a URL that points at it, and destroys both afterwards. This file pins the DECISION
that comes first: what to start, read out of the project's own ``docker-compose.yml``.

That reading is not a nicety. Generated projects disagree about every field that matters — the service is
``db`` in nex-websites and ``postgres`` in nex-shopify, the user is ``nexweb`` in one and ``nex`` in the
next — and the SQLAlchemy DRIVER is a package that either is or is not installed, so a wrong scheme does not
degrade, it dies with ``ModuleNotFoundError`` in a place no test author would ever suspect the engine of. So
the shapes of the four real generated projects on this host are represented here verbatim.

No docker is executed: the module's single shell-out seam (:func:`build_db._docker`) is recorded instead, so
what these tests read is the exact argv, never a mock's opinion of it.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from backend.services import build_db

TOKEN = "0123456789ab"


@pytest.fixture
def docker_calls(monkeypatch) -> list[tuple[str, ...]]:
    """Record every docker control call; return ``(0, "")`` for all of them."""
    calls: list[tuple[str, ...]] = []

    async def _fake(*args: str, **_kwargs) -> tuple[int, str]:
        calls.append(tuple(args))
        return 0, ""

    monkeypatch.setattr(build_db, "_docker", _fake)
    return calls


def _project(tmp_path, compose: str | None, name: str = "docker-compose.yml"):
    root = tmp_path / "acme"
    root.mkdir(exist_ok=True)
    if compose is not None:
        (root / name).write_text(compose, encoding="utf-8")
    return str(root)


def _plan(tmp_path, compose: str | None, *, slug: str = "acme", name: str = "docker-compose.yml"):
    return build_db.plan_database(slug=slug, host_project_dir=_project(tmp_path, compose, name), token=TOKEN)


# ---------------------------------------------------------------------------
# The project's compose file is the spec
# ---------------------------------------------------------------------------

#: The four generated projects that exist on this host, reduced to what the planner reads. Written out as
#: data because the point is that they DIFFER: a planner that only ever saw one of them would look correct.
_REAL_SHAPES = {
    "nex-websites": (
        """
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: nexweb
      POSTGRES_DB: nex_websites
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-ci}
  backend:
    build: ./backend
  test:
    build:
      context: .
      dockerfile: Dockerfile.test
    environment:
      DATABASE_URL: postgresql+asyncpg://nexweb:${POSTGRES_PASSWORD:-ci}@db:5432/nex_websites
""",
        ("db", "nexweb", "nex_websites", "postgresql+asyncpg"),
    ),
    "nex-shopify": (
        """
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-nex}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-nex}
      POSTGRES_DB: ${POSTGRES_DB:-nex_shopify}
  backend:
    build: ./backend
    environment:
      DATABASE_URL: postgresql+asyncpg://nex:nex@postgres:5432/nex_shopify
""",
        ("postgres", "nex", "nex_shopify", "postgresql+asyncpg"),
    ),
    "nex-payables": (
        """
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: nex_payables
      POSTGRES_DB: nex_payables
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}
""",
        ("db", "nex_payables", "nex_payables", "postgresql+asyncpg"),
    ),
    "studio-flavoured": (
        """
services:
  db:
    image: postgres:16-alpine
    environment:
      - POSTGRES_USER=nexstudiovisual
      - POSTGRES_DB=nexstudiovisual
  backend:
    build: .
    environment:
      - DATABASE_URL=postgresql+pg8000://nexstudiovisual:x@db:5432/nexstudiovisual
""",
        ("db", "nexstudiovisual", "nexstudiovisual", "postgresql+pg8000"),
    ),
}


@pytest.mark.parametrize("shape", sorted(_REAL_SHAPES))
def test_the_plan_is_read_from_the_project_not_guessed(tmp_path, shape) -> None:
    compose, (alias, user, database, driver) = _REAL_SHAPES[shape]
    plan = _plan(tmp_path, compose)
    assert (plan.alias, plan.user, plan.database) == (alias, user, database)
    assert plan.url.startswith(f"{driver}://")
    # The URL is one the project's own conftest could have written: its service name, its port, its dbname.
    assert plan.url.endswith(f"@{alias}:5432/{database}")


def test_the_driver_scheme_is_the_one_field_that_cannot_be_chosen_freely(tmp_path) -> None:
    """``pg8000`` vs ``asyncpg`` is an INSTALLED PACKAGE, not a preference. The ``test`` service wins,
    because that is the one describing how the suite is meant to reach the database."""
    plan = _plan(
        tmp_path,
        """
services:
  db:
    image: postgres:16-alpine
  backend:
    build: .
    environment:
      DATABASE_URL: postgresql+asyncpg://a:b@db:5432/c
  test:
    build: .
    environment:
      DATABASE_URL: postgresql+pg8000://a:b@db:5432/c
""",
    )
    assert plan.url.startswith("postgresql+pg8000://")


def test_a_project_with_no_compose_yet_still_gets_a_database(tmp_path) -> None:
    """The NORMAL state of the first Programovanie turns — the agent has not written the file yet. It gets
    the default rather than a surprise, so the charter can promise a database unconditionally."""
    plan = _plan(tmp_path, None)
    assert plan.image == build_db.DEFAULT_IMAGE
    assert plan.alias == build_db.DEFAULT_ALIAS
    assert plan.user == plan.database == "acme"
    assert plan.url.startswith(build_db.DEFAULT_DRIVER + "://")


def test_a_broken_compose_file_does_not_cost_the_turn_its_database(tmp_path) -> None:
    plan = _plan(tmp_path, "services: [ this is not\n  valid: yaml: at all")
    assert plan.image == build_db.DEFAULT_IMAGE


@pytest.mark.parametrize("name", ["docker-compose.yaml", "compose.yml", "compose.yaml"])
def test_every_filename_docker_itself_accepts_is_read(tmp_path, name) -> None:
    plan = _plan(tmp_path, "services:\n  store:\n    image: postgres:15\n", name=name)
    assert (plan.alias, plan.image) == ("store", "postgres:15")


def test_the_projects_own_postgres_image_is_the_one_started(tmp_path) -> None:
    """Testing on the engine that the app runs on is the whole reason the suite is not on SQLite."""
    plan = _plan(tmp_path, "services:\n  db:\n    image: postgres:15-bookworm\n")
    assert plan.image == "postgres:15-bookworm"


def test_the_database_is_found_by_its_image_not_by_being_called_db(tmp_path) -> None:
    plan = _plan(tmp_path, "services:\n  hlavna-databaza:\n    image: postgres:16-alpine\n")
    assert plan.alias == "hlavna-databaza"


# ---------------------------------------------------------------------------
# Values that cannot be resolved are never invented — and never borrowed
# ---------------------------------------------------------------------------


def test_an_unresolvable_reference_falls_back_to_the_slug_and_not_to_our_own_environment(tmp_path, monkeypatch) -> None:
    """``${POSTGRES_USER}`` with no default must not pick up the BACKEND's ``POSTGRES_USER``.

    This process's environment describes the NEX Studio database; substituting it into a customer project's
    configuration would quietly cross-wire two unrelated systems, and the failure would look like the
    project's own misconfiguration.
    """
    monkeypatch.setenv("POSTGRES_USER", "studio_owner")
    monkeypatch.setenv("POSTGRES_DB", "nexstudiovisual")
    plan = _plan(
        tmp_path,
        "services:\n  db:\n    image: postgres:16-alpine\n"
        "    environment:\n      POSTGRES_USER: ${POSTGRES_USER}\n      POSTGRES_DB: ${POSTGRES_DB}\n",
        slug="nex-websites",
    )
    assert plan.user == plan.database == "nex_websites"
    assert "studio_owner" not in plan.url and "nexstudiovisual" not in plan.url


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("${A:-ci}", "ci"),
        ("${A-ci}", "ci"),
        ("plain", "plain"),
        ("pre-${A:-x}-post", "pre-x-post"),
        ("${A}", None),
        ("${A:?boom}", None),
        ("${A?boom}", None),
        ("$A", None),
    ],
)
def test_compose_interpolation_resolves_defaults_and_admits_when_it_cannot(raw, expected) -> None:
    assert build_db._interpolate(raw) == expected


def test_an_illegal_identifier_never_reaches_an_environment_variable(tmp_path) -> None:
    """The user/dbname end up in another process's argv, so they are validated, never passed through."""
    plan = _plan(
        tmp_path,
        "services:\n  db:\n    image: postgres:16-alpine\n"
        '    environment:\n      POSTGRES_USER: "x; rm -rf /"\n      POSTGRES_DB: "-"\n',
    )
    assert plan.user == plan.database == "acme"
    assert "rm -rf" not in plan.url


def test_the_password_is_ours_fresh_every_turn_and_never_in_a_repr(tmp_path) -> None:
    """The project names its password by reference (``${POSTGRES_PASSWORD:?…}``), i.e. it lives in ``.env``
    — a file the ICC rules keep out of logs and out of anything an agent can print. We own this container,
    so there is nothing to gain by reading it and a real rule to break."""
    first = _plan(tmp_path, _REAL_SHAPES["nex-websites"][0])
    second = _plan(tmp_path, _REAL_SHAPES["nex-websites"][0])
    assert first.password and first.password != second.password
    assert "ci" != first.password  # not the compose default either
    # Hiding the password FIELD alone was theatre: it is inside the URL, so the whole plan's repr printed
    # it. Both fields are therefore out of the repr, and this is what noticed.
    assert first.password not in repr(first)
    assert first.password not in first.safe_url
    assert first.network in repr(first)  # …while everything needed to FIND the database is still there


# ---------------------------------------------------------------------------
# Anything but Postgres is an admitted boundary
# ---------------------------------------------------------------------------


def test_an_extra_off_the_shelf_service_stops_the_turn_and_is_named(tmp_path) -> None:
    with pytest.raises(build_db.UnsupportedProjectService) as exc:
        _plan(
            tmp_path,
            "services:\n"
            "  db:\n    image: postgres:16-alpine\n"
            "  cache:\n    image: redis:7-alpine\n"
            "  objects:\n    image: minio/minio\n"
            "  backend:\n    build: ./backend\n",
        )
    message = str(exc.value)
    assert "cache" in message and "objects" in message
    assert "backend" not in message  # the project builds that one itself


def test_a_service_the_project_builds_is_never_mistaken_for_infrastructure(tmp_path) -> None:
    """``image:`` AND ``build:`` together is the normal "build it, tag it" idiom — still the project's own."""
    services = build_db.load_services(
        _project(
            tmp_path,
            "services:\n"
            "  db:\n    image: postgres:16-alpine\n"
            "  backend:\n    build: ./backend\n    image: acme-backend:latest\n",
        )
    )
    assert build_db.unsupported_services(services) == []


def test_a_project_with_no_database_and_no_extras_is_fine(tmp_path) -> None:
    services = build_db.load_services(_project(tmp_path, "services:\n  backend:\n    build: .\n"))
    assert build_db.unsupported_services(services) == []


# ---------------------------------------------------------------------------
# What the compose file may DECIDE — the fields that reach a privileged ``docker run``
# ---------------------------------------------------------------------------

#: Images audit drove through the old rule ("the last path segment starts with postgres"). The first one was
#: run for real: a tagged alpine whose ``CMD`` printed ``PWNED…`` and ``uid=0(root)`` from inside our own
#: network, started BY THE BACKEND, which holds the docker socket. All three came out of a compose file the
#: SANDBOXED turn writes — ``docker-compose.yml`` is deliberately not frozen, because writing it is the work.
_ATTACKER_IMAGES = (
    "audit.local/postgres:pwn",
    "evil.example.com/postgres:latest",
    "attacker/postgres-backdoor:v1",
    "registry.example.com:5000/postgres:16",
    "mycorp/postgres-with-extensions:1",
)


@pytest.mark.parametrize("image", _ATTACKER_IMAGES)
def test_the_turn_cannot_choose_what_the_engine_runs_as_root(tmp_path, image) -> None:
    """THE IMAGE IS NOT THE PROJECT'S TO CHOOSE — and the reason is in :func:`build_db.container_run_argv`.

    That function deliberately passes no ``--user``, no ``--cap-drop`` and no ``no-new-privileges``, because
    the postgres entrypoint has to start as root to prepare its data directory. That concession is only
    defensible while the set of images is fixed: otherwise turn N writes an ``image:`` into its compose file
    and turn N+1 has the privileged backend run it as root, with the default capability set, on the very
    network the sandbox is pinned to. Audit demonstrated exactly that, end to end.

    The rule is an allow-list of ONE repository, so this fails for a registry host, for a namespace and for a
    perfectly well-meant ``mycorp/postgres-with-extensions`` alike — and it fails LOUDLY, naming the image,
    because "unsupported service db" would send somebody reading YAML.
    """
    assert build_db.is_supported_postgres_image(image) is False
    with pytest.raises(build_db.UnsupportedProjectService) as exc:
        _plan(tmp_path, f"services:\n  db:\n    image: {image}\n")
    assert image in str(exc.value)


@pytest.mark.parametrize(
    "image",
    ["postgres", "postgres:16", "postgres:16-alpine", "postgres:15-bookworm", "postgres:16@sha256:" + "a" * 64],
)
def test_the_official_image_in_every_form_a_project_writes_it_is_accepted(tmp_path, image) -> None:
    """The allow-list has to admit what real projects actually write, or it just breaks them."""
    assert build_db.is_supported_postgres_image(image) is True
    assert _plan(tmp_path, f"services:\n  db:\n    image: {image}\n").image == image


def test_the_default_image_is_itself_on_the_allow_list() -> None:
    """The fallback for a project that has not written its compose file yet must satisfy the same rule —
    otherwise the first Programovanie turn of every new project dies in ``_assert_runnable``."""
    assert build_db.is_supported_postgres_image(build_db.DEFAULT_IMAGE) is True


@pytest.mark.parametrize(
    "service_name",
    ["api.anthropic.com", "github.com", "registry.npmjs.org", "DB", "db_main", "x" * 40],
)
def test_a_service_name_can_never_become_a_host_name_on_the_build_network(tmp_path, service_name) -> None:
    """THE ALIAS IS A DNS RECORD, and since STEP 2 the build network is the sandbox's ONLY network.

    Docker's embedded DNS answers a ``--network-alias`` BEFORE any external resolution, so an alias with
    dots in it overwrites a real host name for every container on that network. Audit ran the whole chain
    live: a service called ``api.anthropic.com`` in the project's compose file, and the sandbox — which
    carries ``CLAUDE_CODE_OAUTH_TOKEN``, ``GITHUB_TOKEN`` and ``GH_TOKEN`` — resolved that name to the
    database container while ``github.com`` (not aliased) still resolved externally. TLS still stands
    between a hijacked name and a stolen token; controlling resolution, cutting connections and reading
    anything unencrypted does not need to beat TLS to be a breach of the isolation promise.

    A name that does not fit one lowercase DNS label falls back to ``db``, which costs nothing: the
    ``DATABASE_URL`` this module composes uses whatever alias it settled on.
    """
    plan = _plan(tmp_path, f"services:\n  {service_name}:\n    image: postgres:16-alpine\n")
    assert plan.alias == build_db.DEFAULT_ALIAS
    assert "." not in plan.alias
    assert f"@{plan.alias}:{build_db.POSTGRES_PORT}/" in plan.url
    argv = build_db.container_run_argv(plan)
    assert argv[argv.index("--network-alias") + 1] == build_db.DEFAULT_ALIAS
    assert argv.count("--network-alias") == 1


def test_a_compose_file_symlinked_out_of_the_project_is_not_read(tmp_path) -> None:
    """``os.path.isfile`` and ``open`` FOLLOW symlinks; the containment guard only ever checked the DIRECTORY.

    So a link the sandboxed turn drops in its own tree read a file out of ``/opt/customers`` — which the
    backend mounts and the sandbox does not. Audit demonstrated both channels out: the foreign file's
    SERVICE NAMES came back inside the ``UnsupportedProjectService`` message, and its ``POSTGRES_USER`` /
    ``POSTGRES_DB`` would have travelled in the ``DATABASE_URL`` handed to the sandbox's environment. Small
    bandwidth, no passwords — and still a read primitive across a boundary this module calls impassable.
    """
    outside = tmp_path / "another-customer"
    outside.mkdir()
    (outside / "docker-compose.yml").write_text(
        "services:\n"
        "  db:\n    image: postgres:16-alpine\n"
        "    environment:\n      POSTGRES_USER: their_user\n      POSTGRES_DB: their_db\n"
        "  billing_worker:\n    image: acme/billing:9\n",
        encoding="utf-8",
    )
    project = tmp_path / "acme"
    project.mkdir()
    (project / "docker-compose.yml").symlink_to(outside / "docker-compose.yml")

    assert build_db.load_services(str(project)) == {}
    # …and the turn still runs, on OUR defaults — the foreign identifiers never appear anywhere.
    plan = build_db.plan_database(slug="acme", host_project_dir=str(project), token=TOKEN)
    assert (plan.user, plan.database, plan.alias) == ("acme", "acme", build_db.DEFAULT_ALIAS)
    assert "their_user" not in plan.url and "their_db" not in plan.url


def test_a_symlink_that_stays_inside_the_project_is_still_read(tmp_path) -> None:
    """The guard is containment, not "no symlinks" — a project may organise its own files however it likes."""
    project = tmp_path / "acme"
    (project / "deploy").mkdir(parents=True)
    (project / "deploy" / "compose.yml").write_text("services:\n  db:\n    image: postgres:15\n", encoding="utf-8")
    (project / "docker-compose.yml").symlink_to(project / "deploy" / "compose.yml")
    assert build_db.plan_database(slug="acme", host_project_dir=str(project), token=TOKEN).image == "postgres:15"


@pytest.mark.parametrize(
    ("image", "alias"),
    [("attacker/postgres-backdoor:v1", "db"), ("postgres:16-alpine", "api.anthropic.com")],
)
async def test_the_two_dangerous_fields_are_re_checked_where_the_container_is_started(
    image, alias, docker_calls
) -> None:
    """Belt AND suspenders, on purpose: a guarantee enforced only where the plan is COMPOSED is one
    refactor away from being enforced nowhere, and :func:`build_db.start` is where the daemon is actually
    told to run something. Nothing may be created before the refusal."""
    plan = build_db.BuildDatabase(
        slug="acme",
        network=build_db.network_name("acme", TOKEN),
        container=build_db.container_name("acme", TOKEN),
        alias=alias,
        image=image,
        user="acme",
        database="acme",
        url="postgresql+asyncpg://acme:x@db:5432/acme",
    )
    with pytest.raises(build_db.BuildDatabaseUnavailable):
        await build_db.start(plan)
    assert docker_calls == [], "the refusal must come BEFORE any network or container exists"


# ---------------------------------------------------------------------------
# Starting it, and getting rid of it
# ---------------------------------------------------------------------------


async def test_readiness_is_asked_over_tcp_because_the_unix_socket_lies(tmp_path, docker_calls) -> None:
    """``-h 127.0.0.1`` is load-bearing.

    The postgres entrypoint runs ``initdb`` and then a TEMPORARY server with ``listen_addresses=''``; a
    plain ``pg_isready`` reaches that one over the unix socket and reports READY seconds before anything on
    the network can connect. The turn would start, run its first test and fail with "connection refused" —
    on a cold image only, which is the worst kind of race to be handed.
    """
    plan = _plan(tmp_path, _REAL_SHAPES["nex-websites"][0])
    await build_db.start(plan)
    probe = next(c for c in docker_calls if c[:2] == ("docker", "exec"))
    assert "pg_isready" in probe
    assert probe[probe.index("-h") + 1] == "127.0.0.1"


async def test_a_failed_start_leaves_nothing_behind(tmp_path, monkeypatch) -> None:
    """The failure path must not create the very leak :func:`release` exists to prevent."""
    calls: list[tuple[str, ...]] = []

    async def _fake(*args: str, **_kwargs) -> tuple[int, str]:
        calls.append(tuple(args))
        if args[:2] == ("docker", "run"):
            return 125, "no such image: postgres:16-alpine"
        return 0, ""

    monkeypatch.setattr(build_db, "_docker", _fake)
    plan = _plan(tmp_path, None)
    with pytest.raises(build_db.BuildDatabaseUnavailable) as exc:
        await build_db.start(plan)
    assert plan.image in str(exc.value)
    assert ("docker", "rm", "-f", plan.container) in calls
    assert ("docker", "network", "rm", plan.network) in calls


async def test_a_database_that_never_answers_fails_the_turn_rather_than_running_without_one(
    tmp_path, monkeypatch
) -> None:
    """A Programovanie turn with no database runs the suite against nothing, and the agent then "fixes"
    the project's code for a fault that is ours — the most expensive outcome available."""
    monkeypatch.setattr(build_db, "_READY_TIMEOUT", 0.05)
    monkeypatch.setattr(build_db, "_READY_POLL_INTERVAL", 0.01)

    async def _fake(*args: str, **_kwargs) -> tuple[int, str]:
        return (1, "no response") if args[:2] == ("docker", "exec") else (0, "")

    monkeypatch.setattr(build_db, "_docker", _fake)
    with pytest.raises(build_db.BuildDatabaseUnavailable):
        await build_db.start(_plan(tmp_path, None))


async def test_release_retries_the_network_because_docker_detaches_late(tmp_path, monkeypatch) -> None:
    """``network rm`` legitimately loses the race with docker's asynchronous endpoint teardown, and a
    network that survives its build leaks a subnet from a finite pool."""
    attempts = {"n": 0}

    async def _fake(*args: str, **_kwargs) -> tuple[int, str]:
        if args[:3] == ("docker", "network", "rm"):
            attempts["n"] += 1
            if attempts["n"] < 3:
                return 1, "error while removing network: has active endpoints"
        return 0, ""

    monkeypatch.setattr(build_db, "_docker", _fake)
    monkeypatch.setattr(build_db, "_NETWORK_RM_BACKOFF", 0.0)
    await build_db.release(_plan(tmp_path, None))
    assert attempts["n"] == 3


async def test_release_gives_up_loudly_and_says_how_to_sweep(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.setattr(build_db, "_NETWORK_RM_BACKOFF", 0.0)

    async def _fake(*args: str, **_kwargs) -> tuple[int, str]:
        return (1, "has active endpoints") if args[:3] == ("docker", "network", "rm") else (0, "")

    monkeypatch.setattr(build_db, "_docker", _fake)
    # ``backend.main`` sets ``propagate = False`` on the ``backend`` logger (it has its own stderr
    # handler), so caplog — which listens on the ROOT logger — sees nothing until propagation is restored.
    monkeypatch.setattr(logging.getLogger("backend"), "propagate", True)
    with caplog.at_level("WARNING", logger=build_db.logger.name):
        await build_db.release(_plan(tmp_path, None))
    assert build_db.OWNER_LABEL in caplog.text


async def test_release_is_a_no_op_when_there_was_never_a_database(monkeypatch) -> None:
    """Every non-Programovanie phase plans ``None``; the ``finally`` still runs."""
    called = False

    async def _fake(*_args: str, **_kwargs) -> tuple[int, str]:
        nonlocal called
        called = True
        return 0, ""

    monkeypatch.setattr(build_db, "_docker", _fake)
    await build_db.release(None)
    assert called is False


async def test_a_wedged_daemon_cannot_hang_a_dispatch(monkeypatch) -> None:
    """Every control call is bounded. A wedged daemon does not stop the ``docker`` binary from STARTING —
    it stops it from answering — so the bound that matters is on the wait, and this drives exactly that."""

    class _Hanging:
        returncode = None

        async def communicate(self):
            await asyncio.sleep(3600)

    async def _spawn(*_a, **_k):
        return _Hanging()

    monkeypatch.setattr(build_db.asyncio, "create_subprocess_exec", _spawn)
    code, detail = await build_db._docker("docker", "network", "ls", timeout=0.05)
    assert code == 1 and "failed" in detail


def test_both_objects_are_labelled_so_a_hard_kill_is_sweepable(tmp_path) -> None:
    """A SIGKILL of the backend mid-turn survives no ``finally``. What it leaves behind has to be findable
    by something other than memory."""
    plan = _plan(tmp_path, None)
    assert f"{build_db.OWNER_LABEL}=acme" in build_db.network_create_argv(plan)
    assert f"{build_db.OWNER_LABEL}=acme" in build_db.container_run_argv(plan)


async def test_the_label_actually_gets_swept_and_not_merely_stamped(monkeypatch) -> None:
    """A LABEL WITH NO SWEEPER BEHIND IT IS AN ALIBI. :data:`build_db.OWNER_LABEL` was stamped on both
    objects with the stated reason that a hard kill leaves something "identifiable and sweepable", and then
    nothing ever looked: audit found the constant used nowhere outside this module and its own tests, while
    ``backend/main.py`` ran only the readiness announcements at boot.

    It is not a theoretical leak. Every ``docker compose up -d`` of a Studio deploy re-creates the backend
    container, and a Programovanie turn in flight at that moment has no ``finally`` left to run — leaving a
    PostgreSQL holding RAM and disk, and a network holding a subnet from docker's FINITE pool. Exhaust that
    pool and creating any new network fails, including the next deploy's.
    """
    calls: list[tuple[str, ...]] = []

    async def _fake(*args: str, **_kwargs) -> tuple[int, str]:
        calls.append(tuple(args))
        if args[:3] == ("docker", "ps", "-aq"):
            return 0, "cafe1\ncafe2\n"
        if args[:3] == ("docker", "network", "ls"):
            return 0, "net1\n"
        return 0, ""

    monkeypatch.setattr(build_db, "_docker", _fake)
    assert await build_db.reap_orphans() == 3
    # Found by LABEL, never by a name pattern — a name is a convention, a label is on the object.
    assert ("docker", "ps", "-aq", "--filter", f"label={build_db.OWNER_LABEL}") in calls
    assert ("docker", "network", "ls", "-q", "--filter", f"label={build_db.OWNER_LABEL}") in calls
    # …and both kinds of leftover are actually removed, the containers before the networks (a network with a
    # live endpoint on it cannot be removed).
    assert calls.index(("docker", "rm", "-f", "cafe2")) < calls.index(("docker", "network", "rm", "net1"))


async def test_a_sweep_with_nothing_to_sweep_is_silent_and_cheap(monkeypatch) -> None:
    """It runs at every backend boot, so the ordinary case must cost two list calls and say nothing."""
    calls: list[tuple[str, ...]] = []

    async def _fake(*args: str, **_kwargs) -> tuple[int, str]:
        calls.append(tuple(args))
        return 0, ""

    monkeypatch.setattr(build_db, "_docker", _fake)
    assert await build_db.reap_orphans() == 0
    assert len(calls) == 2


async def test_a_daemon_that_cannot_be_reached_never_stops_the_backend_from_booting(monkeypatch) -> None:
    """Readiness already reports an unreachable daemon (``build_sandbox.log_startup_readiness``); a failed
    sweep must not be a second, fatal opinion about the same fact."""

    async def _fake(*_args: str, **_kwargs) -> tuple[int, str]:
        return 1, "Cannot connect to the Docker daemon"

    monkeypatch.setattr(build_db, "_docker", _fake)
    assert await build_db.reap_orphans() == 0
