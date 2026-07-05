"""v1→v2 project migration service (STEP 8).

A safe, unit-testable DB copy tool that lifts the real projects from a v1 NEX
Studio database into a fresh v2 database. Driven by the standalone CLI
``scripts/migrate_v1_to_v2.py``; this package is the reusable core.

Public API:

* :class:`~backend.services.migration.config.MigrationConfig` — run configuration.
* :func:`~backend.services.migration.runner.run_migration` — orchestrates the run.
* :class:`~backend.services.migration.runner.MigrationReport` — the run result.
"""

from __future__ import annotations

from backend.services.migration.config import (
    DEFAULT_PROJECTS_ROOT_V2,
    DEFAULT_REPORT_DIR,
    MigrationConfig,
)
from backend.services.migration.runner import (
    MigrationGuardError,
    MigrationPreflightError,
    MigrationReport,
    ProjectResult,
    run_migration,
)

__all__ = [
    "DEFAULT_PROJECTS_ROOT_V2",
    "DEFAULT_REPORT_DIR",
    "MigrationConfig",
    "MigrationGuardError",
    "MigrationPreflightError",
    "MigrationReport",
    "ProjectResult",
    "run_migration",
]
