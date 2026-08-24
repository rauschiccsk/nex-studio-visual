"""The throwaway PostgreSQL a sandboxed **Programovanie** turn tests against (ICCINT-16, STEP 2).

WHY THIS MODULE EXISTS — AND WHY IT IS NOT A DOCKER BROKER. STEP 1 (:mod:`build_sandbox`) moved the
document-writing phases into a container with no docker socket. Programovanie could not follow, and the
stated reason was that its charter has it run ``docker compose up`` and test against a real PostgreSQL. The
obvious next move was therefore a *brokered* docker socket — a proxy that allows some API calls and refuses
others. That would have been a new, security-critical component of our own making, and the whole point of
this work is to REMOVE the ways a mistaken build turn can reach a customer's production files, not to add a
component whose correctness we would then have to defend.

So the premise was checked instead of assumed, and it turned out to be narrower than the charter's wording.
A generated project needs Docker in Programovanie for exactly ONE thing: **a reachable Postgres for its
tests**. Its own ``conftest.py`` says so in as many words (verified on ``/opt/projects/nex-websites``):

    ``DATABASE_URL`` must point at a reachable Postgres: the compose ``db`` service in CI, or a local
    ``docker compose up -d db``.

Nothing else in that phase needs the daemon. Building and running the whole app IS a docker job — and it
belongs to Vizuál and Verifikácia, which are NOT sandboxed and still have the socket. So the engine hands
the Programovanie turn the one thing it genuinely needs, and the turn needs no docker at all:

  * a **per-build docker network** — created before the turn, removed after it;
  * a **per-build PostgreSQL container** on that network, reachable under the service name the project's
    own ``docker-compose.yml`` uses for its database;
  * ``DATABASE_URL`` in the sandbox's environment, pointing at it.

The container and the network are named after the project AND the turn, so two builds running at the same
time cannot collide, and an operator hunting a leak can find them by project (``docker ps | grep <slug>``).

"NOT A BROKER" IS A CLAIM ABOUT THE DECISION SURFACE, AND IT HAS TO BE EARNED FIELD BY FIELD. The engine
still starts a container on a build turn's behalf, so this module IS the thing standing between a compose
file and ``docker run``; calling it "not a broker" is only true if what the project can INFLUENCE is
bounded, and the first version bounded almost nothing. Audit drove ``audit.local/postgres:pwn`` (an image
whose own ``CMD`` printed ``uid=0(root)``) all the way through ``plan_database`` → ``start`` → running
container, and hijacked the build network's DNS with a service named ``api.anthropic.com``. Both came from
a file the SANDBOXED turn writes: ``docker-compose.yml`` is not frozen, because writing it is the work.
What the project may decide is therefore now enumerated, and everything else is ours:

  * the IMAGE is an allow-list of one repository (:data:`_ALLOWED_POSTGRES_IMAGE_RE`) — the project chooses
    a TAG, never a registry and never a namespace;
  * the ALIAS is one lowercase DNS label with no dots (:data:`_DOCKER_ALIAS_RE`) — no host name with a
    domain in it can be authored into the network the sandbox lives on;
  * the compose file itself must RESOLVE INSIDE THE PROJECT (:func:`load_services`) — a symlink out of the
    tree is ignored, not followed into another customer's directory;
  * both the image and the alias are re-asserted at the point of execution (:func:`_assert_runnable`), not
    only where the plan is composed.

THE PROJECT'S OWN COMPOSE FILE IS THE SPEC, not a guess of ours. Generated projects disagree about all the
details that matter — the service is called ``db`` in nex-websites but ``postgres`` in nex-shopify, the user
is ``nexweb`` here and ``nex`` there, and the SQLAlchemy driver is ``asyncpg`` in the generated apps but
``pg8000`` in the Studio itself. A ``DATABASE_URL`` with the wrong DRIVER does not degrade; it dies with
``ModuleNotFoundError``. So the shape of the environment is READ from ``docker-compose.yml``
(:func:`plan_database`); the only field that is OURS is the password, and it is ours because the compose
file names it by reference (``${POSTGRES_PASSWORD:?set it in .env}``) — the value lives in the project's
``.env``, a file the ICC rules keep out of logs and out of anything an agent can print. We own this
container and it dies with the turn, so there is nothing to gain by reading the customer's password and a
real rule to break by doing it.

WHEN THE PROJECT NEEDS MORE THAN POSTGRES, THE TURN FAILS — LOUDLY, WITH THE NAME. If the compose file
declares another off-the-shelf service (a Redis, a MinIO, a message broker — anything with an ``image:``
and no ``build:``), the engine does not supply it and does not pretend to: :class:`UnsupportedProjectService`
is raised BEFORE the container starts and names the service. Running the turn anyway would give the agent
half an environment, and half an environment produces a build that fails for reasons nobody can attribute —
the expensive kind of failure. An admitted boundary beats a quiet half-measure. Supplying that service is a
deliberate extension of this module, not something to be improvised per project.

CLEANUP IS NOT OPTIONAL. A forgotten Postgres holds RAM and disk and nobody notices until the disk is full;
a forgotten network eats a subnet from docker's pool. :func:`release` therefore runs from a ``finally`` in
:func:`claude_agent._invoke_once`, so it covers the clean return, the crash, the timeout and the cancel
alike — the same discipline :func:`build_sandbox.reap_container` already applies to the sandbox container,
extended to a resource that ``--rm`` cannot reap because it must OUTLIVE the process that created it. The
one exit a ``finally`` cannot cover is the backend being killed mid-turn, which is what an ordinary
``docker compose up -d`` deploy does — so :func:`reap_orphans` sweeps by :data:`OWNER_LABEL` at every
backend startup. A label with no sweeper behind it is an alibi, not a cleanup strategy.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import secrets
from dataclasses import dataclass, field
from typing import Optional

import yaml

from backend.services.claude_agent import ClaudeAgentError

logger = logging.getLogger(__name__)

#: This module's label in operator-facing errors.
_LABEL = "build database"

#: The phases that get a database. Programovanie writes code and runs the project's test suite; Príprava
#: and Návrh write documents and would have no use for one (a database nobody queries is a container
#: nobody reaps). Vizuál and Verifikácia are not sandboxed at all — they still hold the docker socket and
#: bring up the project's real compose stack themselves.
DATABASE_PHASES: tuple[str, ...] = ("programovanie",)

#: Compose file names docker itself accepts, in its own precedence order.
_COMPOSE_FILENAMES: tuple[str, ...] = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
)

#: Fallbacks used only when the project has not declared a database yet — which is the NORMAL state of the
#: first Programovanie turns, before the agent has written its ``docker-compose.yml``. The turn still gets
#: a database rather than a surprise, so the charter can promise one unconditionally.
DEFAULT_IMAGE = "postgres:16-alpine"
DEFAULT_ALIAS = "db"
#: The generated apps' driver (nex-websites, nex-shopify). Used only when the project declares no
#: ``DATABASE_URL`` of its own to copy the scheme from.
DEFAULT_DRIVER = "postgresql+asyncpg"
POSTGRES_PORT = 5432

#: An image that merely CLAIMS to be a Postgres by repository name. Used ONLY to give a rejected image a
#: message that names the real reason (see :func:`rejected_postgres_images`) — never to decide what runs.
_POSTGRES_IMAGE_PREFIX = "postgres"

#: THE ONLY IMAGES THIS MODULE STARTS — an ALLOW-LIST of one repository, and the narrowness is the point.
#:
#: The ``image:`` comes out of the project's ``docker-compose.yml``, which is a file the SANDBOXED turn N
#: writes — writing compose is what Programovanie DOES, and ``docker-compose.yml`` is deliberately not in
#: :data:`build_sandbox._FROZEN_PROJECT_CONFIG`. The container is then started by the BACKEND, which holds
#: the docker socket, and :func:`container_run_argv` deliberately passes no ``--user``, no ``--cap-drop`` and
#: no ``no-new-privileges`` (the postgres entrypoint needs to start as root). So whatever this pattern admits
#: runs AS ROOT WITH THE DEFAULT CAPABILITY SET on the same network the sandbox is pinned to — turn N would
#: be choosing what the privileged engine executes in turn N+1.
#:
#: The previous rule was "the last path segment of the repository starts with ``postgres``". Audit drove a
#: tagged ``audit.local/postgres:pwn`` through it end to end: ``plan_database`` accepted it, ``start`` ran it,
#: and its own ``CMD`` printed ``uid=0(root)`` from inside our network. ``evil.example.com/postgres:latest``
#: and ``attacker/postgres-backdoor:v1`` were accepted by the same rule. A check an attacker satisfies by
#: NAMING his image is not a check.
#:
#: Hence: the official Docker Hub ``postgres`` repository, with NO registry host and NO namespace, an
#: optional tag and an optional digest pin — nothing else. A legitimate ``mycorp/postgres-with-extensions``
#: is refused too, by name and out loud, which was always the honest answer for it: we would not know how to
#: configure it. Widening this is a deliberate act with a threat model attached, not a convenience edit.
_ALLOWED_POSTGRES_IMAGE_RE = re.compile(r"^postgres(:[A-Za-z0-9_][A-Za-z0-9._-]{0,126})?(@sha256:[a-f0-9]{64})?$")

#: PostgreSQL identifiers we are willing to put into an environment variable and a URL. Anything else
#: (an unresolved ``${VAR}``, a quote, a slash) falls back to the slug-derived name instead of being
#: passed through — the value ends up in another process's argv, so it is validated, never trusted.
_PG_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

#: THE NETWORK ALIAS IS A DNS RECORD THE SANDBOXED TURN WOULD OTHERWISE AUTHOR. The alias comes from the
#: SERVICE NAME in the project's ``docker-compose.yml`` — again a file turn N writes — and
#: :func:`container_run_argv` hands it to ``--network-alias``. Docker's embedded DNS on a user-defined
#: network answers an alias BEFORE any external resolution, and since STEP 2 that user-defined network is the
#: ONLY network the Programovanie sandbox is on (``--network`` in :func:`build_sandbox.build_run_argv`
#: replaces the default bridge). An earlier version of this pattern allowed DOTS, so a service named
#: ``api.anthropic.com`` overwrote that host name for the whole build network — demonstrated live by audit,
#: with the sandbox resolving ``api.anthropic.com`` to the database container while ``github.com`` (not
#: aliased) still resolved externally. The sandbox holds ``CLAUDE_CODE_OAUTH_TOKEN``, ``GITHUB_TOKEN`` and
#: ``GH_TOKEN``; TLS still stands between a hijacked name and a stolen token, but controlling name resolution,
#: cutting connections at will and reading anything unencrypted is already a breach of the isolation promise.
#:
#: So: ONE lowercase DNS label — no dots, therefore no host name with a domain in it can be authored here.
#: A service name that does not fit falls back to :data:`DEFAULT_ALIAS`, which costs nothing: the
#: ``DATABASE_URL`` this module composes uses whatever alias it settled on, so the project's own service name
#: never has to match.
_DOCKER_ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")

#: ``${VAR:-default}`` / ``${VAR-default}`` / ``${VAR}`` / ``${VAR:?msg}``. A bare ``$VAR`` is handled by
#: the same pass, as an unresolvable reference.
_INTERPOLATION_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:?[-?])?([^}]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")

#: How long the engine waits for the database to accept connections before it gives up on the turn. initdb
#: on a cold image takes a few seconds; a minute is generous without letting a broken image stall a build.
_READY_TIMEOUT = 60.0
_READY_POLL_INTERVAL = 0.5

#: Bound the short-lived control calls so a wedged daemon cannot hang a dispatch.
_DOCKER_CALL_TIMEOUT = 120.0

#: ``docker network rm`` fails while any endpoint is still attached, and docker detaches asynchronously
#: after the sandbox container exits — so the first attempt legitimately loses the race sometimes.
_NETWORK_RM_ATTEMPTS = 6
_NETWORK_RM_BACKOFF = 0.5

#: Stamped on both objects so anything left behind by a hard kill (SIGKILL of the backend mid-turn, which no
#: ``finally`` can survive) is identifiable and sweepable — and :func:`reap_orphans` is the sweeper, run at
#: backend startup. The label without the sweeper was a promise nobody kept: the ordinary
#: ``docker compose up -d`` of a Studio deploy re-creates the backend container, so a Programovanie turn in
#: flight loses its ``finally`` and leaves a PostgreSQL holding RAM and disk plus a network holding a subnet
#: from docker's finite pool — and once that pool is exhausted, creating ANY network fails, including the
#: next deploy's.
OWNER_LABEL = "nex-studio-visual.build-turn"


class BuildDatabaseUnavailable(ClaudeAgentError):
    """The per-build database could not be created — so the turn fails instead of running without one.

    Never a silent downgrade. A Programovanie turn with no database runs the project's test suite against
    nothing, and a test suite that cannot connect fails in a way the agent will try to "fix" in the
    project's code — which is the worst outcome available: real work, spent on a fault that is ours.
    """


class UnsupportedProjectService(ClaudeAgentError):
    """The project's compose file declares a supporting service the engine does not supply.

    A separate type from :class:`BuildDatabaseUnavailable` because it is a different statement: nothing is
    broken, the project simply needs an environment richer than the sandbox can build. The message names
    the service, so the answer ("teach the engine to supply it" or "move that work into Verifikácia") is a
    decision somebody can actually take.
    """


@dataclass(frozen=True)
class BuildDatabase:
    """Everything one turn's database is: the docker objects, and the URL the sandbox is handed.

    BOTH ``password`` AND ``url`` are ``repr=False``, and the second one is the point. Hiding only the
    password field was theatre — the password is INSIDE the URL, so a stray ``logger.debug("%r", plan)``
    printed it in full anyway (the test that now guards this found exactly that). What is left in the repr —
    the network, the container, the alias, the user, the dbname — is everything an operator needs to find
    this database, and nothing that unlocks it. Use :attr:`safe_url` for anything human-readable.
    """

    slug: str
    network: str
    container: str
    alias: str
    image: str
    user: str
    database: str
    url: str = field(repr=False)
    password: str = field(repr=False, default="")

    @property
    def safe_url(self) -> str:
        """The URL with the password blanked — the only form that goes into a log line."""
        return self.url.replace(f":{self.password}@", ":***@", 1) if self.password else self.url


def phase_needs_database(stage: Optional[str]) -> bool:
    """Whether ``stage`` is a phase the engine provisions a database for (:data:`DATABASE_PHASES`)."""
    return isinstance(stage, str) and stage.strip().lower() in DATABASE_PHASES


def network_name(slug: str, token: str) -> str:
    """Per-project, per-turn network name — two concurrent builds cannot collide, and neither can two
    turns of the same build."""
    return f"nex-build-net-{slug}-{token}"


def container_name(slug: str, token: str) -> str:
    """Per-project, per-turn database container name (same reasoning as :func:`network_name`)."""
    return f"nex-build-db-{slug}-{token}"


# --------------------------------------------------------------------------------------------------------
# Reading the project's own declaration
# --------------------------------------------------------------------------------------------------------


def _interpolate(raw: object) -> Optional[str]:
    """Resolve a compose value using ITS OWN DEFAULTS ONLY; ``None`` when a reference has no default.

    Deliberately NOT falling back to ``os.environ``: this process's environment is the BACKEND's, and its
    ``POSTGRES_PASSWORD`` / ``POSTGRES_USER`` belong to the Studio's own database. Substituting those into a
    customer project's configuration would be a quiet cross-wiring of two unrelated systems. Unresolvable
    means unresolvable, and the caller substitutes something of its own.
    """
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return str(raw)
    if not isinstance(raw, str):
        return None
    unresolved = False

    def _one(match: re.Match[str]) -> str:
        nonlocal unresolved
        if match.group(4) is not None:  # a bare $VAR — no default is expressible
            unresolved = True
            return ""
        operator, default = match.group(2), match.group(3)
        if operator in ("-", ":-"):
            return default or ""
        unresolved = True  # ${VAR}, ${VAR?msg}, ${VAR:?msg}
        return ""

    resolved = _INTERPOLATION_RE.sub(_one, raw)
    return None if unresolved else resolved


def _service_environment(service: object) -> dict[str, Optional[str]]:
    """A compose ``environment:`` block as a dict, accepting BOTH forms docker allows.

    Mapping form (``KEY: value``) and list form (``- KEY=value``) are equally common in the wild, and a
    reader that understands only one silently sees an empty environment — which here would mean silently
    falling back to a default user for a project that named its own.
    """
    if not isinstance(service, dict):
        return {}
    env = service.get("environment")
    if isinstance(env, dict):
        return {str(k): _interpolate(v) for k, v in env.items()}
    if isinstance(env, list):
        out: dict[str, Optional[str]] = {}
        for item in env:
            if not isinstance(item, str):
                continue
            key, sep, value = item.partition("=")
            out[key.strip()] = _interpolate(value) if sep else None
        return out
    return {}


def load_services(host_project_dir: str) -> dict[str, dict]:
    """The ``services:`` block of the project's compose file, or ``{}`` when there is none to read.

    An absent file is the NORMAL state early in Programovanie — the agent has not written it yet — and a
    malformed one is the project's problem, not a reason to refuse it a database. Both answer ``{}``, and
    the caller then provisions the defaults.

    THE FILE MUST RESOLVE INSIDE THE PROJECT. ``os.path.isfile`` and ``open`` both FOLLOW symlinks, and
    :func:`build_sandbox.assert_host_source_contained` only ever verified the project DIRECTORY — so a
    ``docker-compose.yml -> /opt/customers/<customer>/docker-compose.yml`` the sandboxed turn dropped in its
    own tree was read without complaint, out of a tree the backend mounts and the sandbox does not. Audit
    demonstrated both exfiltration channels: the foreign file's SERVICE NAMES came back in the
    :class:`UnsupportedProjectService` message, and its ``POSTGRES_USER`` / ``POSTGRES_DB`` would have landed
    in the ``DATABASE_URL`` handed straight to the sandbox's environment. The bandwidth is a few strings per
    turn and no password ever crosses (the password is always ours) — but it is a read primitive across the
    exact boundary this module calls impassable, so the resolved path is required to stay under the project
    and a link that leaves it is ignored, loudly.
    """
    project_root = os.path.realpath(host_project_dir)
    for name in _COMPOSE_FILENAMES:
        path = os.path.join(host_project_dir, name)
        real = os.path.realpath(path)
        if not os.path.isfile(real):
            continue
        if real != project_root and not real.startswith(project_root + os.sep):
            logger.warning(
                "%s: %s resolves to %s, OUTSIDE the project — ignored. A compose file reached through a "
                "symlink can carry another customer's service names and database identifiers into this "
                "turn's environment; the isolated turn reads its OWN project or nothing.",
                _LABEL,
                path,
                real,
            )
            continue
        try:
            with open(real, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("%s: %s is not readable/parseable (%s) — using the default environment", _LABEL, path, exc)
            return {}
        services = data.get("services") if isinstance(data, dict) else None
        return {str(k): v for k, v in services.items() if isinstance(v, dict)} if isinstance(services, dict) else {}
    return {}


def _is_off_the_shelf(service: dict) -> Optional[str]:
    """The image of a service the engine would have to SUPPLY, or ``None`` when the project builds it.

    A ``build:`` key means the service is the project's own code; the agent compiles and runs it itself in
    Vizuál/Verifikácia and it is none of this module's business. What is left — an ``image:`` with no
    ``build:`` — is infrastructure somebody has to start, and that somebody is the engine.
    """
    if service.get("build") is not None:
        return None
    image = _interpolate(service.get("image"))
    return image or None


def is_supported_postgres_image(image: str) -> bool:
    """Whether the engine is willing to RUN this image (:data:`_ALLOWED_POSTGRES_IMAGE_RE`).

    Public because it is a boundary, not an implementation detail: it is asserted by the tests and re-checked
    at the point of execution (:func:`_assert_runnable`), not only where the plan is composed.
    """
    return bool(_ALLOWED_POSTGRES_IMAGE_RE.match(image))


def _repository(image: str) -> str:
    """The last path segment of an image reference, with any digest and tag removed.

    The tag is what follows the LAST colon, and only when that colon comes after the last ``/`` — otherwise
    the "tag" is a registry PORT. Splitting on the first colon instead reads
    ``registry.example.com:5000/postgres:16`` as the repository ``registry.example.com``, which is how a
    private-registry image slips past a name check unnoticed.
    """
    ref = image.split("@", 1)[0]
    colon = ref.rfind(":")
    if colon > ref.rfind("/"):
        ref = ref[:colon]
    return ref.split("/")[-1]


def _names_itself_postgres(image: str) -> bool:
    """Whether an image CLAIMS to be a Postgres by repository name — the OLD, insufficient test.

    Kept for exactly one purpose: telling a rejected project WHY. "Unsupported service ``db``" would send
    somebody reading YAML; "``attacker/postgres-backdoor:v1`` is not the official postgres image" does not.
    """
    return _repository(image).startswith(_POSTGRES_IMAGE_PREFIX)


def _postgres_service(services: dict[str, dict]) -> Optional[tuple[str, dict, str]]:
    """``(name, service, image)`` of the project's database, or ``None`` if it declares none (yet).

    Only an image on the ALLOW-LIST counts. Anything else is not a database as far as this module is
    concerned — it is an off-the-shelf service the engine does not supply, and it fails loudly by name.
    """
    for name, service in services.items():
        image = _is_off_the_shelf(service)
        if image and is_supported_postgres_image(image):
            return name, service, image
    return None


def rejected_postgres_images(services: dict[str, dict]) -> list[tuple[str, str]]:
    """``(service, image)`` pairs that call themselves Postgres but are not the image the engine will run."""
    rejected: list[tuple[str, str]] = []
    for name, service in services.items():
        image = _is_off_the_shelf(service)
        if image and _names_itself_postgres(image) and not is_supported_postgres_image(image):
            rejected.append((name, image))
    return sorted(rejected)


def unsupported_services(services: dict[str, dict]) -> list[str]:
    """Names of off-the-shelf services the engine does not supply — everything but the one Postgres.

    Returned as a list rather than raised here so the decision (and its message) lives at ONE call site
    and the rule itself stays a pure function the tests can drive project by project.
    """
    postgres = _postgres_service(services)
    postgres_name = postgres[0] if postgres else None
    return [name for name, service in services.items() if name != postgres_name and _is_off_the_shelf(service)]


def _declared_driver(services: dict[str, dict]) -> str:
    """The SQLAlchemy driver scheme the project's OWN ``DATABASE_URL`` uses (``postgresql+asyncpg``, …).

    This is the one field that cannot be chosen freely: the scheme names a python package that either is or
    is not installed in the project, and a wrong one fails with ``ModuleNotFoundError`` — nothing a test
    author would ever suspect the engine of. The ``test`` service is preferred because that is the one
    describing how the suite is meant to reach the database.
    """
    ordered = sorted(services.items(), key=lambda kv: 0 if kv[0] == "test" else 1)
    for _name, service in ordered:
        url = _service_environment(service).get("DATABASE_URL")
        scheme, sep, _rest = (url or "").partition("://")
        if sep and scheme.startswith("postgres"):
            return scheme
    return DEFAULT_DRIVER


def _slug_identifier(slug: str) -> str:
    """A PostgreSQL-legal identifier derived from the project slug (``nex-websites`` → ``nex_websites``)."""
    ident = re.sub(r"[^A-Za-z0-9]+", "_", slug).strip("_").lower() or "nex_build"
    return ident if _PG_IDENT_RE.match(ident) else f"p_{ident}"[:63]


def plan_database(*, slug: str, host_project_dir: str, token: str) -> BuildDatabase:
    """Decide WHAT to start for this turn — pure, no side effects, so the rules can be tested directly.

    Raises:
        UnsupportedProjectService: the compose file declares an off-the-shelf service besides Postgres, or
            names a "postgres" image the engine will not run (:data:`_ALLOWED_POSTGRES_IMAGE_RE`).
    """
    services = load_services(host_project_dir)
    rejected = rejected_postgres_images(services)
    if rejected:
        # Checked BEFORE ``extras`` so the message names the real reason. These services would fall into the
        # generic "supporting service" bucket otherwise, and the operator would go looking for a Redis.
        raise UnsupportedProjectService(
            f"{_LABEL}: the project's database service(s) "
            f"{', '.join(f'{name} ({image})' for name, image in rejected)} do NOT use the official Docker Hub "
            f"``postgres`` image, and the engine starts nothing else. The image named in a compose file is "
            f"chosen by the build turn and started by the ENGINE — as root, with the default capability set, "
            f"on the build's own network — so the set of images that may run is fixed here and not derived "
            f"from a name. Use ``postgres:<tag>`` (no registry, no namespace); if this project genuinely "
            f"needs a different database image, teaching the engine to supply it is a deliberate change with "
            f"a threat model attached (ICCINT-16 STEP 2)."
        )
    extras = unsupported_services(services)
    if extras:
        raise UnsupportedProjectService(
            f"{_LABEL}: the project declares supporting service(s) the isolated Programovanie turn cannot be "
            f"given: {', '.join(sorted(extras))}. The engine supplies a PostgreSQL and nothing else — the "
            f"sandboxed turn has NO docker socket, so it cannot start them itself either. Bringing the whole "
            f"stack up belongs to Vizuál/Verifikácia, which are not sandboxed; if this service is genuinely "
            f"needed during Programovanie, the engine has to learn to supply it (ICCINT-16 STEP 2). "
            f"BUILD_SANDBOX=0 runs the phase unisolated again — a deliberate choice, with /opt/customers, "
            f"/opt/uat, /opt/infra and the docker socket back in reach."
        )

    postgres = _postgres_service(services)
    if postgres is None:
        alias, image, env = DEFAULT_ALIAS, DEFAULT_IMAGE, {}
        logger.info(
            "%s: %s declares no database service yet — provisioning the default %s as %r",
            _LABEL,
            slug,
            DEFAULT_IMAGE,
            DEFAULT_ALIAS,
        )
    else:
        name, service, image = postgres
        alias = name if _DOCKER_ALIAS_RE.match(name) else DEFAULT_ALIAS
        env = _service_environment(service)

    fallback = _slug_identifier(slug)
    user = env.get("POSTGRES_USER") or ""
    database = env.get("POSTGRES_DB") or ""
    user = user if _PG_IDENT_RE.match(user) else fallback
    database = database if _PG_IDENT_RE.match(database) else fallback
    # OURS, always — never the project's (the module docstring argues why reading it would be the wrong
    # trade). Hex rather than ``token_urlsafe``: the URL is parsed by every driver in the ecosystem and a
    # hex string needs no percent-encoding anywhere.
    password = secrets.token_hex(16)
    driver = _declared_driver(services)

    return BuildDatabase(
        slug=slug,
        network=network_name(slug, token),
        container=container_name(slug, token),
        alias=alias,
        image=image,
        user=user,
        database=database,
        password=password,
        url=f"{driver}://{user}:{password}@{alias}:{POSTGRES_PORT}/{database}",
    )


# --------------------------------------------------------------------------------------------------------
# Starting and reaping it
# --------------------------------------------------------------------------------------------------------


#: The address range every sandbox network is allocated from (ICCINT-21). It exists so ONE static firewall
#: rule on the host can fence EVERY build turn, forever, without the engine ever touching iptables: the host
#: side matches on this source range, so a network created here is fenced the moment it exists and nothing
#: has to be added or removed per build.
#:
#: Measured 24.08.2026 from a container on such a network, with the fence in place: the Studio API, SSH and
#: this host's PostgreSQL instances all time out; the search index, the local model, the build's OWN network
#: and the open internet all answer. Without it — the state before ICCINT-21 — every one of them answered.
SANDBOX_SUBNET_POOL = "10.77.0.0/16"
_POOL_SIZE = 256


def subnet_candidates(token: str) -> list[str]:
    """Every /24 in :data:`SANDBOX_SUBNET_POOL`, ordered so two concurrent builds start at different places.

    Docker allocates a subnet itself when none is given, and it would allocate OUTSIDE the fenced range —
    which is why the subnet is chosen here rather than left to it. The order is derived from the turn token
    so the first attempt almost never collides; a collision is not an error, it just costs one more attempt
    (``docker network create`` refuses an overlap, and :func:`create_fenced_network` walks on).
    """
    start = int(hashlib.sha256(token.encode()).hexdigest()[:8], 16) % _POOL_SIZE
    return [f"10.77.{(start + i) % _POOL_SIZE}.0/24" for i in range(_POOL_SIZE)]


async def create_fenced_network(name: str, owner: str, token: str) -> str:
    """Create ``name`` inside the fenced range and return the subnet it got.

    Raises:
        BuildDatabaseUnavailable: the whole pool is taken (256 concurrent builds, or leaked networks — the
            warning in :func:`release` names the sweep) or docker refused for another reason.
    """
    last = ""
    for subnet in subnet_candidates(token):
        code, detail = await _docker(
            "docker", "network", "create", "--subnet", subnet, "--label", f"{OWNER_LABEL}={owner}", name
        )
        if code == 0:
            return subnet
        last = detail
        low = detail.lower()
        if "overlap" not in low and "pool" not in low:
            break
    raise BuildDatabaseUnavailable(f"{_LABEL}: could not create fenced network {name} ({last[:300]})")


async def remove_network(name: str) -> None:
    """Remove a fenced network, retrying while docker detaches endpoints. Idempotent, never raises."""
    for attempt in range(_NETWORK_RM_ATTEMPTS):
        code, detail = await _docker("docker", "network", "rm", name, timeout=30)
        if code == 0 or "not found" in detail.lower() or "no such network" in detail.lower():
            return
        await asyncio.sleep(_NETWORK_RM_BACKOFF * (attempt + 1))
    logger.warning(
        "%s: network %s could not be removed after %d attempts — a leaked network holds a subnet from the "
        "fenced pool; sweep with: docker network ls --filter label=%s",
        _LABEL,
        name,
        _NETWORK_RM_ATTEMPTS,
        OWNER_LABEL,
    )


def network_create_argv(plan: BuildDatabase) -> list[str]:
    """``docker network create`` for the per-build network — no options that widen it.

    A plain user-defined bridge, on purpose: it gives docker's embedded DNS (so ``alias`` resolves without
    anybody having to look up an IP) and it keeps outbound internet, which the turn needs for the Claude MAX
    endpoint, for ``npm``/``pip`` and for ``gh``. Both were measured on this host from a container attached
    to exactly such a network: the Qdrant/Ollama gateway answered ``200`` and ``api.anthropic.com`` answered.
    ``--internal`` would cut all of that and is therefore not an option; the honest statement of what a
    build turn can still reach on the network stays :data:`build_sandbox.NETWORK_RESIDUAL`.
    """
    return ["docker", "network", "create", "--label", f"{OWNER_LABEL}={plan.slug}", plan.network]


def container_run_argv(plan: BuildDatabase) -> list[str]:
    """``docker run`` for the throwaway PostgreSQL.

    Every choice here is a boundary:

      * ``--network`` the per-build network and ``--network-alias`` the project's own service name, so the
        ``DATABASE_URL`` reads exactly like the project's own compose URL;
      * **no published port** — nothing outside that one network can reach this database, not even the host;
      * no volume — the data lives in the container's writable layer and dies with it, so a build's test
        data can never survive into the next build;
      * ``POSTGRES_PASSWORD`` passed BY VALUE, which is the deliberate exception to
        :mod:`build_sandbox`'s by-name rule and is argued at :func:`build_sandbox.build_run_argv`.

    Deliberately NOT here: ``--user``, ``--cap-drop``, ``no-new-privileges``. The postgres entrypoint starts
    as root to prepare its data directory and drops to the ``postgres`` user itself; taking that away does
    not harden anything a build can reach (the container is alone on a private network and holds one
    throwaway database) and does break the image outright. **That concession is only defensible because the
    image and the alias are not the project's to choose** — see :data:`_ALLOWED_POSTGRES_IMAGE_RE` and
    :data:`_DOCKER_ALIAS_RE`, re-asserted at :func:`_assert_runnable`. Without them this function would be
    "run whatever the sandboxed turn wrote into a YAML file, as root, on the sandbox's own network", which is
    exactly what audit demonstrated end to end.
    """
    return [
        "docker",
        "run",
        "-d",
        "--rm",
        "--name",
        plan.container,
        "--label",
        f"{OWNER_LABEL}={plan.slug}",
        "--network",
        plan.network,
        "--network-alias",
        plan.alias,
        "-e",
        f"POSTGRES_USER={plan.user}",
        "-e",
        f"POSTGRES_PASSWORD={plan.password}",
        "-e",
        f"POSTGRES_DB={plan.database}",
        plan.image,
    ]


async def _docker(*args: str, timeout: float = _DOCKER_CALL_TIMEOUT) -> tuple[int, str]:
    """Run one docker control command; returns ``(returncode, stderr_or_stdout_tail)``. Never raises."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (asyncio.TimeoutError, OSError) as exc:
        return 1, f"{args[:3]} failed: {exc}"
    detail = (stderr or b"").decode("utf-8", errors="replace").strip()
    return proc.returncode or 0, detail or (stdout or b"").decode("utf-8", errors="replace").strip()


async def _wait_until_ready(plan: BuildDatabase) -> None:
    """Block until the database accepts TCP connections, or raise.

    ``-h 127.0.0.1`` is load-bearing, not decoration. The official postgres entrypoint runs ``initdb`` and
    then a TEMPORARY server with ``listen_addresses=''`` to apply the init scripts; a plain ``pg_isready``
    talks to that one over the unix socket and reports READY several seconds before anything on the network
    can connect. The turn would then start, run its first test, and fail with "connection refused" — a race
    that reproduces on a cold image and vanishes on a warm one, i.e. the worst kind to debug. Asking over
    TCP asks the only question that matters here.
    """
    deadline = asyncio.get_running_loop().time() + _READY_TIMEOUT
    last = "no probe ran"
    while asyncio.get_running_loop().time() < deadline:
        code, detail = await _docker(
            "docker",
            "exec",
            plan.container,
            "pg_isready",
            "-h",
            "127.0.0.1",
            "-U",
            plan.user,
            "-d",
            plan.database,
            "-q",
            timeout=15,
        )
        if code == 0:
            return
        last = detail or f"pg_isready exit {code}"
        await asyncio.sleep(_READY_POLL_INTERVAL)
    raise BuildDatabaseUnavailable(
        f"{_LABEL}: {plan.image} did not accept connections within {_READY_TIMEOUT:.0f}s "
        f"({plan.container}: {last[:300]})"
    )


def _assert_runnable(plan: BuildDatabase) -> None:
    """Re-check, WHERE THE CONTAINER IS ACTUALLY STARTED, the two fields a project file can influence.

    :func:`plan_database` is the only producer of a :class:`BuildDatabase` today, so this is redundant today —
    and that is exactly why it is here. The image decides what the privileged backend executes and the alias
    decides what the build network's DNS answers; both were once derived from attacker-writable compose
    fields with a check that a name satisfied. A guarantee enforced only at the composing end is one
    refactor away from being enforced nowhere, and this one is cheap.

    Raises:
        BuildDatabaseUnavailable: the plan carries an image or an alias that must never reach ``docker run``.
    """
    if not is_supported_postgres_image(plan.image):
        raise BuildDatabaseUnavailable(
            f"{_LABEL}: refusing to run {plan.image!r} — only the official Docker Hub ``postgres`` image is "
            f"started by the engine (it runs as root, with the docker socket's authority behind it)"
        )
    if not _DOCKER_ALIAS_RE.match(plan.alias):
        raise BuildDatabaseUnavailable(
            f"{_LABEL}: refusing the network alias {plan.alias!r} — an alias is a DNS record on the build's "
            f"only network, so it must be a single lowercase label with no dots"
        )


async def start(plan: BuildDatabase) -> BuildDatabase:
    """Create the network + the database container and wait until it answers. Cleans up after itself.

    Any failure removes whatever was already created before it raises — a half-built environment is exactly
    the leak :func:`release` exists to prevent, and it must not be created by the failure path either.

    Raises:
        BuildDatabaseUnavailable: the plan is not runnable (:func:`_assert_runnable`), or the network, the
            container or the readiness probe failed.
    """
    _assert_runnable(plan)
    # ICCINT-21: inside the fenced range, so the host rule covers this build without knowing it exists.
    await create_fenced_network(plan.network, plan.slug, plan.network)
    try:
        code, detail = await _docker(*container_run_argv(plan))
        if code != 0:
            raise BuildDatabaseUnavailable(f"{_LABEL}: could not start {plan.image} for {plan.slug} ({detail[:300]})")
        await _wait_until_ready(plan)
    except BaseException:
        await release(plan)
        raise
    logger.info("%s: %s ready for %s on %s (url=%s)", _LABEL, plan.image, plan.slug, plan.network, plan.safe_url)
    return plan


async def release(plan: Optional[BuildDatabase]) -> None:
    """Remove the container and the network. Idempotent, never raises, safe on a partial start.

    Called from a ``finally``, so it also runs after a timeout, a cancellation and a crash — the three
    paths on which ``--rm`` cannot help, because the docker CLI that would have honoured it was killed.
    The network is retried: docker detaches endpoints asynchronously, so the first ``network rm`` after the
    sandbox container exits legitimately loses the race sometimes, and a network that survives its build is
    a subnet leaked from a finite pool.
    """
    if plan is None:
        return
    await _docker("docker", "rm", "-f", plan.container, timeout=30)
    for attempt in range(_NETWORK_RM_ATTEMPTS):
        code, detail = await _docker("docker", "network", "rm", plan.network, timeout=30)
        if code == 0 or "not found" in detail.lower() or "no such network" in detail.lower():
            return
        await asyncio.sleep(_NETWORK_RM_BACKOFF * (attempt + 1))
    logger.warning(
        "%s: network %s could not be removed after %d attempts — a leaked network holds a subnet from "
        "docker's pool; sweep with: docker network ls --filter label=%s",
        _LABEL,
        plan.network,
        _NETWORK_RM_ATTEMPTS,
        OWNER_LABEL,
    )


async def reap_orphans() -> int:
    """Remove every build-turn container and network this host still carries. Returns how many it removed.

    THE LABEL IS ONLY WORTH WHAT SWEEPS IT. :data:`OWNER_LABEL` was stamped on both objects with the stated
    reason that a hard kill leaves something "identifiable and sweepable", and then nothing swept: audit
    found the constant used nowhere outside this module and its tests, while ``backend/main.py`` ran only the
    readiness announcements at boot. The leak is not hypothetical — every ``docker compose up -d`` of a Studio
    deploy re-creates the backend container, which during development happens often, and a Programovanie turn
    in flight at that moment has no ``finally`` left to run.

    Run at STARTUP and nowhere else. At that moment this process owns no turn, and the previous process's
    turns are dead by definition — so everything wearing the label is garbage. Calling it later would race a
    live build's own database, which is why it is not a periodic task.

    Never raises: a docker daemon that cannot be reached at boot is already reported by
    :func:`build_sandbox.log_startup_readiness`, and a failed sweep must not stop the backend from starting.
    """
    removed = 0
    label = f"label={OWNER_LABEL}"

    code, out = await _docker("docker", "ps", "-aq", "--filter", label, timeout=30)
    containers = out.split() if code == 0 else []
    for container in containers:
        rm_code, _ = await _docker("docker", "rm", "-f", container, timeout=30)
        removed += 1 if rm_code == 0 else 0

    code, out = await _docker("docker", "network", "ls", "-q", "--filter", label, timeout=30)
    networks = out.split() if code == 0 else []
    for network in networks:
        rm_code, _ = await _docker("docker", "network", "rm", network, timeout=30)
        removed += 1 if rm_code == 0 else 0

    if containers or networks:
        logger.warning(
            "%s: swept %d of %d docker object(s) left over from a build turn the backend did not outlive "
            "(%d container(s), %d network(s), label=%s) — each was holding memory, disk or a subnet from "
            "docker's pool until now",
            _LABEL,
            removed,
            len(containers) + len(networks),
            len(containers),
            len(networks),
            OWNER_LABEL,
        )
    return removed
