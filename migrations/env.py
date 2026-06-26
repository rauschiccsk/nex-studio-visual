"""Alembic environment configuration."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from backend.config.settings import settings

# ``backend.db.base`` imports every ORM model in the domain, so this single
# import is sufficient to populate ``Base.metadata`` for ``autogenerate``.
# When adding a new model, register it in ``backend/db/base.py`` and Alembic
# will pick it up automatically.
from backend.db.base import Base
from backend.db.session import _ensure_pg8000_driver

target_metadata = Base.metadata


def _setup_config() -> None:
    """Configure Alembic — only callable within Alembic runtime context."""
    config = context.config

    if config.config_file_name is not None:
        # disable_existing_loggers=False keeps uvicorn's access + error
        # loggers alive after Alembic runs during the FastAPI lifespan
        # hook. Without it, ``docker logs`` went dark the moment
        # migrations finished, which hid the SSE spec-chat diagnostics
        # during Zoltán's "žiadna reakcia" incident.
        fileConfig(config.config_file_name, disable_existing_loggers=False)

    # An explicit URL passed via ``-x url=...`` wins over the configured
    # production ``settings.database_url``. The test harness uses this to
    # migrate the SEPARATE test database to head (see ``tests/conftest.py``);
    # production / CLI runs pass no override and keep targeting the cockpit DB.
    x_args = context.get_x_argument(as_dictionary=True)
    override_url = x_args.get("url")
    url = override_url if override_url else settings.database_url
    config.set_main_option("sqlalchemy.url", _ensure_pg8000_driver(url))


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    config = context.config
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    config = context.config
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if hasattr(context, "config"):
    _setup_config()

    if context.is_offline_mode():
        run_migrations_offline()
    else:
        run_migrations_online()
