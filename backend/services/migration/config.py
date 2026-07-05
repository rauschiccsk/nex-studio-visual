"""Configuration for the v1→v2 migration tool (STEP 8).

A frozen :class:`MigrationConfig` carries the two explicit DB URLs and every
run-shaping flag. The tool deliberately does NOT read the DB URL from
``backend.config.settings`` — ``settings.database_url`` (settings.py:8) hardcodes
a SINGLE cockpit URL and ``backend.db.session.engine`` (session.py:16) is a
single engine bound to it, so it cannot express the two distinct source/target
databases this copy tool needs. Both URLs are passed in as CLI args instead.
"""

from __future__ import annotations

from dataclasses import dataclass

# The v2 PROD projects root. New-column default for ``projects.source_path`` — a
# migrated project's on-disk path is rebased ``/opt/projects/<slug>`` →
# ``<projects_root>/<slug>`` (default below). Overridable via ``--projects-root``.
DEFAULT_PROJECTS_ROOT_V2 = "/opt/projects-v2"

# Where the JSON run-report is written when ``--report-path`` is not given. A
# timestamped file lands under this directory. NEVER contains secret values (§4).
DEFAULT_REPORT_DIR = "/opt/data/nex-studio/migration-log"


@dataclass(frozen=True)
class MigrationConfig:
    """Immutable configuration for one migration run.

    ``dry_run`` defaults to True — the safe default. The apply path (``--apply``)
    commits per-project; the dry-run path runs the IDENTICAL code and rolls back.
    """

    source_url: str
    target_url: str
    projects_root: str = DEFAULT_PROJECTS_ROOT_V2
    dry_run: bool = True
    # Restrict the run to these project slugs (empty = all source projects).
    only_slugs: tuple[str, ...] = ()
    # Skip source projects whose ``status`` is in this set (e.g. 'archived').
    exclude_statuses: tuple[str, ...] = ()
    # OPT-IN on-disk directory copy (default OFF). The DB copy is the load-bearing core.
    copy_dirs: bool = False
    # Explicit report path; None → DEFAULT_REPORT_DIR/<timestamp>.json.
    report_path: str | None = None
    # Escape hatch for the prod-target-name guard (--i-understand-target-is-prod).
    # Default False → the tool REFUSES a target whose DB name equals the cockpit
    # PROD DB name (settings.database_url). In build/CI the target is always
    # nexstudio_test, so an accidental PROD write is impossible.
    allow_prod_target: bool = False

    def __post_init__(self) -> None:
        # Normalise list-ish inputs to tuples so the dataclass stays hashable/frozen
        # even when the CLI hands us lists.
        object.__setattr__(self, "only_slugs", tuple(self.only_slugs))
        object.__setattr__(self, "exclude_statuses", tuple(self.exclude_statuses))
