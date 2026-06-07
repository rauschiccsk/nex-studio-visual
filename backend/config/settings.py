from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    database_url: str = "postgresql+pg8000://nexstudio:nexstudio@localhost:9178/nexstudio"
    test_database_url: str = "postgresql+pg8000://nexstudio:nexstudio@localhost:9178/nexstudio_test"
    secret_key: str = "change-me-in-production"
    backend_port: int = 9176
    frontend_port: int = 9177
    vite_api_base_url: str = "http://localhost:9176"
    cors_origins: list[str] = [
        "http://localhost:9177",
        "http://127.0.0.1:9177",
    ]

    # GitHub integration
    github_token: str = Field(
        default="",
        description="GitHub personal access token for repository validation API calls",
    )

    # Claude CLI configuration
    claude_config_dir: str = "/root/.claude"
    claude_cli_path: str = "claude"

    # Backstop timeout (seconds) for a single headless ``claude -p`` invocation
    # driven by the F-007 orchestrator (CR-NS-018 fix-round). Since agent
    # dispatch is asynchronous, this only guards a *hung* agent, so it is
    # generous. The orchestrator overrides it per stage (build is longer);
    # this is the default + the env-tunable knob.
    claude_invoke_timeout: int = 900

    # Public base URL of the NEX Studio frontend, used only to build the
    # ``/cockpit`` deep link in presence-aware Telegram notifications
    # (CR-NS-018 Phase 5a). Empty → the notification omits the link.
    app_public_url: str = ""

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
    credentials_storage_path: str = "/opt/data/nex-studio/credentials"

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


settings = Settings()
