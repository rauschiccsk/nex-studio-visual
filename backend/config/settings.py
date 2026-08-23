import os

from pydantic import Field
from pydantic_settings import BaseSettings

#: Environment variable carrying Dedo's PLAINTEXT machine secret (ICCINT-14). Read ONCE, at import, and
#: REMOVED from ``os.environ`` as it is read — see :func:`_take_dedo_plaintext_token`.
DEDO_TOKEN_ENV = "DEDO_API_TOKEN"


def _take_dedo_plaintext_token() -> str:
    """Read Dedo's plaintext secret out of the environment and TAKE IT WITH US — leaving nothing behind.

    This process spawns the AI Agent (``claude`` with an unrestricted Bash tool) and every child inherits
    this process's environment. A secret left in ``os.environ`` is therefore a secret handed to the very
    party Dedo's door exists to keep out: an agent whose build is blocked could read ``DEDO_API_TOKEN`` with
    ``printenv`` and clear its own escalation. So the variable is popped here, at import, long before any
    spawn can happen — the one place that covers EVERY child, including the ones that inherit implicitly.

    It does NOT make the plaintext configuration safe, and nothing can: ``/proc/1/environ`` still shows what
    the process was started with (``unsetenv`` does not rewrite that region), and the agent has a root shell
    in this container. That is why the deployed shape is the DIGEST
    (:data:`Settings.dedo_api_token_sha256`), which leaves nothing here to steal; this path exists for local
    development, where the agent and the attacker are the same person and it does not matter.
    """
    return os.environ.pop(DEDO_TOKEN_ENV, "").strip()


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    database_url: str = "postgresql+pg8000://nexstudiovisual:nexstudiovisual@localhost:9215/nexstudiovisual"
    test_database_url: str = "postgresql+pg8000://nexstudiovisual:nexstudiovisual@localhost:9215/nexstudiovisual_test"
    secret_key: str = "change-me-in-production"
    # DECLARED, NOT HONOURED — read this before wiring anything to them. The container images
    # hardcode the ports: the backend CMD is `uvicorn --port 9176` and nginx serves 9177, and the
    # healthcheck hardcodes 9176 too. Setting BACKEND_PORT / FRONTEND_PORT in an .env or a compose
    # file changes NOTHING, and there is not even a failure to notice — the container keeps listening
    # where it always did and the healthcheck keeps passing. They survive as the value other code
    # READS when it needs to know where the app listens; they do not decide it. Changing where it
    # listens means editing the Dockerfile CMD and the nginx config, not this.
    backend_port: int = 9176
    frontend_port: int = 9177
    vite_api_base_url: str = "http://localhost:9213"
    cors_origins: list[str] = [
        "http://localhost:9214",
        "http://127.0.0.1:9214",
    ]

    # GitHub integration
    github_token: str = Field(
        default="",
        description="GitHub personal access token for repository validation API calls",
    )

    # Claude CLI configuration
    claude_config_dir: str = "/root/.claude"
    claude_cli_path: str = "claude"

    # Konzultácia OS-level read-only sidecar (konzultacia-sidecar-sandbox.md). The consult turn runs
    # inside an ephemeral ``docker run --rm`` sibling of THIS backend image so the project is
    # KERNEL-enforced ``:ro``. ``consult_sandbox_image`` is that image tag — a dedicated knob
    # (env ``CONSULT_SANDBOX_IMAGE``) rather than overloading ``app_version`` (a semver, not an image
    # tag). The on/off kill-switch is the ``CONSULT_SANDBOX`` env (default on), read at turn time in
    # :func:`backend.services.consult_sandbox.sandbox_enabled`.
    #
    # EMPTY on purpose, like ``build_sandbox_image`` below. The literal default this used to carry
    # (``nex-studio-visual-backend:v3.0.0``) names a tag that does not exist on this host — the oldest
    # present is v4.0.85 — so every Konzultácia raised ``SidecarUnavailable`` and degraded to the in-process
    # turn, i.e. the kernel read-only guarantee was in effect on none of them.
    # :func:`backend.services.consult_sandbox.sidecar_image` now derives the running version's tag instead,
    # which is the tag the deploy script actually built. ``CONSULT_SANDBOX_IMAGE`` still overrides.
    consult_sandbox_image: str = ""

    # Build sandbox (ICCINT-16 STEP 1) — the image the Príprava/Návrh build turns run inside, a sibling of
    # THIS backend image (it needs the same git/gh/node tooling and the same mounted ``claude`` binary).
    # EMPTY on purpose: :func:`backend.services.build_sandbox.sandbox_image` then derives
    # ``nex-studio-visual-backend:v<app_version>``, i.e. the tag the deploy script actually built for the
    # running version. A literal default is what broke ``consult_sandbox_image`` — it names a tag that does
    # not exist on this host, so every Konzultácia silently lost its guarantee. Set BUILD_SANDBOX_IMAGE to
    # pin something else. The on/off switch is the ``BUILD_SANDBOX`` env (default on), read at turn time.
    build_sandbox_image: str = ""

    # Backstop timeout (seconds) for a single headless ``claude -p`` invocation
    # driven by the F-007 orchestrator (CR-NS-018 fix-round). Since agent
    # dispatch is asynchronous, this only guards a *hung* agent, so it is
    # generous. The orchestrator overrides it per stage (build is longer);
    # this is the default + the env-tunable knob.
    claude_invoke_timeout: int = 900

    # WS-D metrics (CR-NS-036) — stored now for the FUTURE metrics page (Phase 3 / E5); no cost
    # calc / UI uses them yet. The developer hourly rate baselines an agent run against a human
    # developer; the per-million-token API prices (IN / OUT separately, the natural Claude billing
    # unit) turn captured token usage into a money figure. Default 0.0 = "not configured" (never a
    # fabricated price); set via env (API_PRICE_INPUT_PER_MTOK / ...). These are the FLAT fallback
    # pair — the per-model-family prices live in system_settings only. (CR-V2-063 retired
    # `developer_hourly_rate`: the human side is per-phase wages × one token→minutes coefficient.)
    api_price_input_per_mtok: float = 0.0
    api_price_output_per_mtok: float = 0.0

    # Public base URL of the NEX Studio frontend, used only to build the
    # ``/cockpit`` deep link in presence-aware Telegram notifications
    # (CR-NS-018 Phase 5a). Empty → the notification omits the link.
    app_public_url: str = ""

    # Filesystem location of the .dedo-channel Dedo↔agent message bus (Director
    # observation #6). The agent → Dedo ``framework_issue`` escalation writes an
    # audit-trail file into ``<dir>/inbox/``. Env-configurable (DEDO_CHANNEL_DIR)
    # because in v3 ``/opt/projects`` is an isolated per-instance workspace that
    # does NOT contain nex-studio, so the legacy hardcoded path resolves to
    # nothing there (SAME reachability class as the #5-fixed notify script) —
    # Dedo mounts the real ``.dedo-channel`` in and points this at the mount.
    dedo_channel_dir: str = "/opt/projects/nex-studio-visual/.dedo-channel"

    # NOTE: operational timeouts (Claude stream / design doc / task plan,
    # GitHub API), conversation history limit, design-doc max chars,
    # token expiry, port registry bounds and path templates are all
    # resolved at request time from the ``system_settings`` table —
    # look in :mod:`backend.services.system_setting.DEFAULT_SETTINGS`
    # for the initial values. Edit them via Settings UI without a
    # rebuild.

    # Knowledge Base path (mounted read-only from host)
    knowledge_base_path: str = "/home/icc/knowledge"

    # Maximum size in bytes the ``GET /kb-documents/{id}/content``
    # endpoint will return. Larger files are rejected with HTTP 422 to
    # protect the backend (and client) from accidentally loading huge
    # markdown / log dumps. 5 MB is generous for hand-written docs and
    # well below the threshold where rendering becomes painful.
    kb_content_max_bytes: int = 5 * 1024 * 1024

    # Filesystem location for the credentials store. Deliberately OUTSIDE
    # the KB root (``/home/icc/knowledge/``) so credentials cannot be
    # picked up by any RAG indexer or kb_sync seed. Mounted as a Docker
    # volume in production. Owner = process user, mode 0700. Backup is
    # an infrastructure-layer concern (restic include path); not handled
    # here.
    credentials_storage_path: str = "/opt/data/nex-studio-visual/credentials"

    # Maximum size in bytes for any single credentials file (read or write).
    # Same rationale as ``kb_content_max_bytes``.
    credentials_content_max_bytes: int = 5 * 1024 * 1024

    # KB access matrix per Shuhari role.
    # Mirrors NEX Command's ``KB_ACCESS`` config (M2 feature parity, 2026-05-07).
    # * "*"          — full access (every category)
    # * concrete prefixes — read access to those category trees
    # * "shu" gets icc/ + shuhari/ baseline; assigned project paths are
    #   added dynamically by ``backend.utils.kb_access._add_assigned_projects``.
    kb_access_ri: list[str] = ["*"]
    kb_access_ha: list[str] = [
        "icc/",
        "shuhari/",
        "infrastructure/",
        "projects/",
        "customers/",
        "templates/",
    ]
    kb_access_shu: list[str] = ["icc/", "shuhari/"]

    # RAG / Qdrant configuration (M3 milestone of feature parity audit).
    # Mirrors NEX Command's RAG constants. Qdrant runs on the shared ICC
    # infra port 9130 (CLAUDE.md ICC Port Registry); Ollama on 9132 with
    # the ``nomic-embed-text`` embedding model. Chunking parameters
    # match NEX Command exactly so re-indexing produces identical
    # collections and search results carry over 1:1.
    #
    # DEPLOYMENT — the two URLs below are the BARE-METAL defaults (backend
    # started on the host, as in local dev and the CI test job). They are
    # deliberately NOT container-aware: inside the backend container
    # ``localhost`` is the container itself, where neither Qdrant nor Ollama
    # runs, so a containerized deployment MUST override them via the env vars
    # ``QDRANT_URL`` / ``OLLAMA_URL`` in its compose file (the addresses depend
    # on how that instance reaches the shared infra — a docker network alias,
    # the host gateway, …, which is an operator decision, not a code default).
    # Until they are set, KB *search* cannot work — but it now SAYS so: the
    # reader raises :class:`backend.rag.reader.RagUnavailableError` and the
    # ``/rag`` routes answer HTTP 503 naming the service and the address,
    # instead of the old behaviour where an unreachable index was swallowed
    # into an empty result list and read as "nothing matched". Browsing the KB
    # from disk (``/knowledge/*``) is unaffected either way.
    #
    # An EMPTY value is honoured as "not configured" and reported as such —
    # use it to state plainly that this instance has no vector index.
    qdrant_url: str = "http://localhost:9130"
    ollama_url: str = "http://localhost:9132"
    embed_model: str = "nomic-embed-text"
    rag_api_timeout: int = 30
    rag_chunk_max_chars: int = 1000
    rag_chunk_overlap: int = 200

    # Claude Code OAuth token — surfaced as an env var by the Claude CLI
    # environment. ICC uses Claude MAX OAuth rather than the Anthropic
    # API (CLAUDE.md §7.1, DECISIONS.md D-001). The backend does not
    # consume the token itself; the CLI subprocess does. Declared here
    # so pydantic's strict ``extra='forbid'`` default tolerates the
    # ``.env`` entry without downgrading to ``extra='ignore'``.
    claude_code_oauth_token: str = Field(
        default="",
        description=(
            "Claude Code OAuth token from the CLI environment. "
            "Declared so Settings tolerates it in .env; not consumed "
            "by the backend directly."
        ),
    )

    # Dedo's machine identity (ICCINT-14, charter §4.5) — TWO fields, ONE of which should be used in a
    # deployment. What opens Dedo's OWN door (``/api/v1/dedo/*``) is never a user account and never a JWT:
    # a user login would be the impersonation the charter forbids, and a JWT-shaped value could be replayed
    # against the USER doors, which is the one crossing this design exists to prevent.
    #
    # EMPTY IS THE DEFAULT AND MEANS "CLOSED", not "open": with neither field configured the whole router
    # answers 503 and nothing behind it can be reached.
    #
    # PREFER THE DIGEST (``DEDO_API_TOKEN_SHA256``). This process also runs the AI Agent — with a Bash tool,
    # as root, in this very container — so ANYTHING this container holds, the agent can read
    # (``/proc/1/environ`` keeps the value the process started with even after ``os.environ.pop``, and a
    # mounted secret file is one ``cat`` away). A verifier is all the door needs: it must recognise Dedo's
    # secret, not possess it. With only the SHA-256 digest here, reading everything this container has
    # yields nothing that opens the door — the boundary is enforced by the token, which is what charter §4.5
    # demands, instead of by the agent not thinking to look.
    dedo_api_token_sha256: str = Field(
        default="",
        description=(
            "SHA-256 hex digest of Dedo's machine token (ICCINT-14) — the RECOMMENDED configuration: the "
            "server verifies, it does not hold the secret. Empty = fall back to dedo_api_token."
        ),
    )

    # The PLAINTEXT secret — LOCAL DEVELOPMENT ONLY. Still honoured (it is the shape ICCINT-14 was
    # specified in, and it is the convenient one for a local run), but weaker than the digest for the
    # reason above: the value is inside the process the agent runs in. It is taken out of ``os.environ``
    # at import (see :func:`_take_dedo_plaintext_token`) so no child process inherits it, and
    # :mod:`backend.core.agent_env` strips it again at every agent spawn — but neither of those can undo
    # ``/proc/1/environ``. Never generated here, never written to the repository, never logged or echoed.
    dedo_api_token: str = Field(
        default="",
        description=(
            "Opaque random secret for Dedo's machine identity (ICCINT-14), plaintext. Prefer "
            "dedo_api_token_sha256. Empty = Dedo's door is closed. Never a JWT, never committed, never logged."
        ),
    )

    app_version: str = "0.1.0"

    # Frontend Vite build version — written to ``.env`` by the
    # ``.githooks/post-commit`` hook so the sidebar footer reflects each
    # commit. Consumed by Vite at frontend build time, not by the
    # backend. Declared here so pydantic's strict ``extra='forbid'``
    # default tolerates the ``.env`` entry without downgrading to
    # ``extra='ignore'`` — same pattern as ``claude_code_oauth_token``.
    vite_app_version: str = Field(
        default="0.1.0",
        description=(
            "Frontend Vite build version stamped into .env by the "
            "post-commit hook. Not consumed by the backend directly."
        ),
    )

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


# Dedo's plaintext secret is taken from the environment BEFORE the settings object is built and is passed
# in explicitly, so pydantic never has to re-read a variable that is (deliberately) no longer there. Passed
# only when non-empty: an empty kwarg would override a value legitimately set in a dev ``.env``.
_dedo_plaintext_token = _take_dedo_plaintext_token()

settings = Settings(**({"dedo_api_token": _dedo_plaintext_token} if _dedo_plaintext_token else {}))
