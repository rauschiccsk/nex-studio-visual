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

    # NOTE: operational timeouts (Claude stream / design doc / task plan,
    # GitHub API), conversation history limit, design-doc max chars,
    # token expiry, port registry bounds and path templates are all
    # resolved at request time from the ``system_settings`` table —
    # look in :mod:`backend.services.system_setting.DEFAULT_SETTINGS`
    # for the initial values. Edit them via Settings UI without a
    # rebuild.

    # Knowledge Base path (mounted read-only from host)
    knowledge_base_path: str = "/home/icc/knowledge"

    # Admin URL of the mockup server (``mockup_server/app.py``) that
    # hosts each project's UI design at its own ``ui_design_port``.
    # After the backend persists a new ``UIDesign.html_preview`` it
    # POSTs to ``{mockup_admin_url}/admin/reload/{project_id}`` so
    # the next GET on that project's port reflects the change.
    # Uses ``host.docker.internal`` because the mockup service runs
    # with ``network_mode: host`` while the backend is bridged —
    # see docker-compose.yml.
    mockup_admin_url: str = "http://host.docker.internal:9190"

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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
