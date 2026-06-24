"""REST router for :class:`~backend.db.models.projects.Project`.

Exposes the standard CRUD surface for projects:

* ``GET    /``              → paginated list (filter by ``status``,
  ``category`` and ``created_by``).
* ``GET    /{project_id}``  → single project by primary key.
* ``POST   /``              → create a new project (with port and GitHub
  repo validation).
* ``PATCH  /{project_id}``  → partial update of the mutable fields.
* ``DELETE /{project_id}``  → hard-delete a project (HTTP 204).

All endpoints are synchronous ``def`` — pg8000 is a synchronous driver and
FastAPI dispatches sync endpoints to a thread pool automatically. The
router delegates every persistence operation to
:mod:`backend.services.project` and handles commit/rollback itself so the
service layer remains transaction-agnostic.

The router is prefix-less; the mount prefix (``/api/v1/projects``) is
applied in ``backend/main.py`` via ``app.include_router``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from backend.api.dependencies import get_knowledge_base_writer, get_rag_indexer
from backend.core.security import require_ha_or_above
from backend.db.models.foundation import User
from backend.db.session import get_db
from backend.rag.indexer import RAGIndexer
from backend.schemas.pagination import PaginatedResponse
from backend.schemas.project import (
    GitHubRepoNotFoundError,
    PortBlockSuggestResponse,
    PortCheckResponse,
    PortConflictError,
    PortSuggestResponse,
    ProjectCategory,
    ProjectCreate,
    ProjectRead,
    ProjectStatus,
    ProjectUpdate,
)
from backend.schemas.version import VersionCreate
from backend.services import github_validation as github_validation_service
from backend.services import port_registry as port_registry_service
from backend.services import project as project_service
from backend.services import system_setting as system_setting_service
from backend.services import uat_provisioner
from backend.services import version as version_service
from backend.services.knowledge_base_writer import KnowledgeBaseWriter
from backend.services.live_documents import LiveDocumentService
from backend.services.template_bootstrap import (
    GitPushVerificationError,
    TemplateBootstrapError,
    invoke_init_script,
    push_and_verify,
    rollback_partial_state,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Projects"],
    dependencies=[Depends(require_ha_or_above)],
)


def _resolve_created_by(db: Session, created_by: Optional[UUID]) -> UUID:
    """Return the supplied UUID or fall back to the first active 'ri' user.

    This is a placeholder until JWT auth is wired up — at that point the
    router will extract the user ID from the token instead.
    """
    if created_by is not None:
        return created_by
    from sqlalchemy import select

    user = db.execute(
        select(User).where(User.is_active.is_(True)).where(User.role == "ri").limit(1)
    ).scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="No active 'ri' user found — cannot resolve created_by.",
        )
    return user.id


def _validate_ports(db: Session, payload: ProjectCreate) -> None:
    """Validate that all supplied ports are in range and not already allocated.

    Raises :class:`~fastapi.HTTPException` directly — 422 for out-of-range,
    409 for conflicts.
    """
    ports = [
        ("backend_port", payload.backend_port),
        ("frontend_port", payload.frontend_port),
        ("db_port", payload.db_port),
    ]

    # Same-row uniqueness — no two non-NULL port columns may share a value.
    # Enforced at the DB level by ck_projects_ports_distinct (migration 030);
    # we pre-check here to return a cleaner 422 before flush.
    seen: dict[int, str] = {}
    for field_name, port_value in ports:
        if port_value is None:
            continue
        if port_value in seen:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Port {port_value} is assigned to both '{seen[port_value]}' "
                    f"and '{field_name}'. Each port column must be distinct."
                ),
            )
        seen[port_value] = field_name

    for field_name, port_value in ports:
        if port_value is None:
            continue

        # Range check — bounds are runtime-configurable via Settings UI
        # (keys ``port_range_min`` / ``port_range_max``).
        range_min = system_setting_service.get_int(db, "port_range_min")
        range_max = system_setting_service.get_int(db, "port_range_max")
        if port_value < range_min or port_value > range_max:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(f"Port {port_value} ({field_name}) is outside the allowed range ({range_min}–{range_max})."),
            )

        # Uniqueness check
        if not port_registry_service.check_port_available(db, port_value):
            conflict_name = port_registry_service.get_conflict_project_name(db, port_value)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=PortConflictError(
                    detail=(
                        f"Port {port_value} ({field_name}) is already allocated"
                        f"{' to project ' + repr(conflict_name) if conflict_name else ''}."
                    ),
                    port=port_value,
                    conflict_project=conflict_name,
                ).model_dump(),
            )

        # Reserved-block check — read CSV from system_settings
        # (key: reserved_port_ranges, e.g. "10110-10159,10200-10209").
        # Externally-managed reservations (NEX Automat per D-022, etc.)
        # not represented in the projects table go here.
        reserved_csv = system_setting_service.get_str(db, "reserved_port_ranges")
        if reserved_csv:
            for spec in (s.strip() for s in reserved_csv.split(",")):
                if not spec or "-" not in spec:
                    continue
                try:
                    start_str, end_str = spec.split("-", 1)
                    r_start = int(start_str.strip())
                    r_end = int(end_str.strip())
                except ValueError:
                    # Malformed entry — skip (operator will see in logs);
                    # don't block project creation on a config typo.
                    logger.warning("Malformed reserved_port_ranges entry %r — skipped", spec)
                    continue
                if r_start <= port_value <= r_end:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=(
                            f"Port {port_value} ({field_name}) falls inside "
                            f"reserved range {r_start}-{r_end}. This range is "
                            f"reserved per ICC_STANDARDS / DECISIONS — pick a "
                            f"different port block."
                        ),
                    )

    # Block-alignment check — backend_port must be at the start of a
    # 10-port block (10100, 10110, 10120, ...) when in commercial range
    # (>= 10100). Legacy 9100-9199 entries are exempt (D-020 "no
    # migration" clause). Frontend / db must follow the D-020 layout
    # (+0 BE, +1 FE, +2 DB) so contiguous blocks remain coherent and
    # `suggest_next_port_block` can find the next free 10-port block
    # deterministically.
    bp = payload.backend_port
    if bp is not None and bp >= 10100:
        if bp % 10 != 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"backend_port {bp} must be 10-aligned (10100, 10110, "
                    f"10120, ...) per D-020 commercial-range layout. The "
                    f"first port of a 10-port block starts the project's "
                    f"reserved slot."
                ),
            )
        layout = [
            ("frontend_port", payload.frontend_port, 1),
            ("db_port", payload.db_port, 2),
        ]
        for field_name, value, offset in layout:
            if value is not None and value != bp + offset:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"{field_name} {value} does not match D-020 layout — "
                        f"expected backend_port + {offset} = {bp + offset} "
                        f"(got {value}). Either use the expected slot or "
                        f"leave the column NULL."
                    ),
                )


def _validate_github_repo(repo_url: str | None, *, timeout: float) -> None:
    """Validate that the GitHub repository exists via the GitHub API.

    Delegates to :func:`backend.services.github_validation.validate_github_repo`
    and translates its exceptions into appropriate HTTP responses.

    Expects *repo_url* in ``owner/repo`` format (e.g.
    ``rauschiccsk/nex-horizont``). Raises :class:`~fastapi.HTTPException`
    422 when the repo is not found or the API call fails.
    """
    if not repo_url:
        return

    try:
        exists = github_validation_service.validate_github_repo(repo_url, timeout=timeout)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=GitHubRepoNotFoundError(
                detail=str(exc),
                repo_url=repo_url,
            ).model_dump(),
        ) from exc
    except (RuntimeError, httpx.HTTPError) as exc:
        logger.warning("GitHub API request failed for repo %r", repo_url, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not verify GitHub repository '{repo_url}': GitHub API unavailable.",
        ) from exc

    if not exists:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=GitHubRepoNotFoundError(
                detail=f"GitHub repository '{repo_url}' not found.",
                repo_url=repo_url,
            ).model_dump(),
        )


def _map_value_error(exc: ValueError) -> HTTPException:
    """Translate a service-layer ``ValueError`` into an HTTP exception.

    Mirrors the ICC error-handling pattern: ``not found`` → 404,
    duplicates/conflicts → 409, everything else (constraint / FK /
    validation failures) → 422.
    """
    message = str(exc)
    lowered = message.lower()
    if "not found" in lowered:
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)
    if "already exists" in lowered or "duplicate" in lowered or "conflict" in lowered:
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=message)


@router.get("", response_model=PaginatedResponse[ProjectRead])
def list_projects(
    status_filter: Optional[ProjectStatus] = Query(
        default=None,
        alias="status",
        description="Filter by lifecycle status (active | archived | paused).",
    ),
    category: Optional[ProjectCategory] = Query(
        default=None,
        description="Filter by category (singlemodule | multimodule).",
    ),
    created_by: Optional[UUID] = Query(
        default=None,
        description="Filter by the creating user's id.",
    ),
    skip: int = Query(default=0, ge=0, description="Number of rows to skip."),
    limit: int = Query(default=50, ge=1, le=100, description="Maximum rows to return."),
    db: Session = Depends(get_db),
) -> PaginatedResponse[ProjectRead]:
    """Return a paginated list of projects."""
    try:
        rows = project_service.list_projects(
            db,
            status=status_filter,
            category=category,
            created_by=created_by,
            limit=limit,
            offset=skip,
        )
        total = project_service.count_projects(
            db,
            status=status_filter,
            category=category,
            created_by=created_by,
        )
    except ValueError as exc:
        raise _map_value_error(exc) from exc

    return PaginatedResponse[ProjectRead](
        items=[ProjectRead.model_validate(row) for row in rows],
        total=total,
        skip=skip,
        limit=limit,
    )


@router.get("/ports/check", response_model=PortCheckResponse)
def check_port(
    port: int = Query(..., description="Port number to check (10100–14999)."),
    project_id: Optional[str] = Query(
        default=None,
        description="Exclude this project from the conflict check (for editing).",
    ),
    db: Session = Depends(get_db),
) -> PortCheckResponse:
    """Check whether a port is available in the ICC Port Registry range."""
    try:
        available = port_registry_service.check_port_available(db, port, project_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc

    conflict_project: str | None = None
    if not available:
        conflict_project = port_registry_service.get_conflict_project_name(db, port, project_id)

    return PortCheckResponse(available=available, conflict_project=conflict_project)


@router.get("/ports/suggest", response_model=PortSuggestResponse)
def suggest_port(
    type: str = Query(
        ...,
        alias="type",
        description="Port type: backend | frontend | db.",
    ),
    db: Session = Depends(get_db),
) -> PortSuggestResponse:
    """Suggest the next available port for the given type."""
    try:
        suggested = port_registry_service.suggest_next_port(db, type)
    except ValueError as exc:
        raise _map_value_error(exc) from exc

    return PortSuggestResponse(suggested_port=suggested)


@router.get("/ports/suggest-block", response_model=PortBlockSuggestResponse)
def suggest_port_block(
    db: Session = Depends(get_db),
) -> PortBlockSuggestResponse:
    """Return the base port of the first free 10-port block in the registry.

    Used by the new-project form to auto-fill the three port inputs
    (backend / frontend / db) from a contiguous block per
    DECISIONS.md D-020 (Port Registry v2, 10-port blocks).
    """
    try:
        base = port_registry_service.suggest_next_port_block(db)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return PortBlockSuggestResponse(
        base=base,
        block_size=system_setting_service.get_int(db, "port_block_size"),
    )


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(
    project_id: UUID,
    db: Session = Depends(get_db),
) -> ProjectRead:
    """Return a single project by primary key."""
    try:
        project = project_service.get_by_id(db, project_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    return ProjectRead.model_validate(project)


@router.post(
    "",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {"model": PortConflictError, "description": "Port or slug conflict"},
        422: {"model": GitHubRepoNotFoundError, "description": "Validation error or GitHub repo not found"},
    },
)
def create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
    kb_writer: KnowledgeBaseWriter = Depends(get_knowledge_base_writer),
    indexer: RAGIndexer = Depends(get_rag_indexer),
) -> ProjectRead:
    """Create a new project.

    Validates before persisting:

    * **Slug uniqueness** — 409 if another project uses the same slug.
    * **Port range** — 422 if any supplied port is outside 10100–14999.
    * **Port uniqueness across projects** — 409 if any supplied port
      is already allocated to another project.
    * **Same-row port uniqueness** — 422 if the payload contains the
      same port number in two different port columns.

    On success:

    * **Creates the GitHub repository** via
      ``github_validation.create_github_repo`` before any DB state
      changes. Failure → 500 and no further work is attempted. A repo
      that already exists is treated as success — reruns and
      deliberate reuse both work. Skipped when ``repo_url`` is NULL.
    * **Seeds two live documents** (``STATUS.md`` / ``HISTORY.md``)
      under ``{knowledge_base_path}/projects/{slug}/``. ARCHITECT.md
      is no longer seeded — replaced by per-agent session logs in
      ``docs/session-logs/<role>/`` (three-agent architecture).
    * **Auto-creates initial version v0.1.0** in ``planned`` status —
      per main CLAUDE.md §2 (no spec change without a version binding).
      Designer's Step 0 VERSION binding finds this version to start work.
    * The DB insert, KB write, version creation and commit happen in a
      single transaction — a failure at any step rolls the row back. If
      KB write fails after GitHub repo was already created, the repo
      stays dangling (documented known-item; manual cleanup on the
      GitHub side).
    """
    # Pre-creation validation (ports).
    _validate_ports(db, payload)

    # Resolve created_by — use supplied UUID or fall back to active ri user.
    payload.created_by = _resolve_created_by(db, payload.created_by)

    # Notification owner (CR-NS-012) defaults to the creator when omitted.
    if payload.owner_id is None:
        payload.owner_id = payload.created_by

    gh_timeout = float(system_setting_service.get_int(db, "github_api_timeout_seconds"))

    # Stage 1 — GitHub repo. Runs before any DB state so a failure is
    # fully reversible (nothing has happened yet on our side).
    if payload.repo_url:
        try:
            github_validation_service.create_github_repo(
                payload.repo_url,
                description=payload.description or "",
                private=True,
                timeout=gh_timeout,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=GitHubRepoNotFoundError(detail=str(exc), repo_url=payload.repo_url).model_dump(),
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create GitHub repository: {exc}",
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning("GitHub API network error for %r", payload.repo_url, exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"GitHub API unreachable while creating '{payload.repo_url}': {exc}",
            ) from exc

    try:
        project = project_service.create(db, payload)
        LiveDocumentService(project.slug, writer=kb_writer, indexer=indexer).init_live_documents(db, project.id)
        # Auto-create initial version v0.1.0 (planned) per three-agent architecture.
        # Main CLAUDE.md §2: žiadna zmena dokumentu v docs/specs/ bez priradenia
        # ku konkrétnej verzii. Designer's Step 0 VERSION binding finds this
        # version to begin work; status defaults to 'planned' via DB server_default.
        version_service.create(
            db,
            project.id,
            VersionCreate(version_number="0.1.0", name="Initial prototype"),
            user_id=payload.created_by,
        )
        # CR-R2-1 (#1a): set uat_slug at creation so a deployable app carries its UAT target from the
        # start (early visibility — the completion guard + UAT board no longer wait for the Phase-3 lazy
        # derive at first release). Idempotent (set_uat_slug flushes; the route's db.commit() persists it).
        # An underivable slug must NOT 500 the create — log + continue; the Phase-3 lazy derive stays the
        # safety net.
        try:
            project_service.set_uat_slug(db, project)
        except ValueError as exc:
            logger.warning("uat_slug not set at create for slug=%s: %s", project.slug, exc)
        # Stage 3 — filesystem bootstrap via icc-claude-template/init.sh.
        # Runs BEFORE db.commit() so a bootstrap failure rolls back the
        # DB row cleanly. KB live docs are already on disk at this point;
        # if init.sh fails, they remain dangling — documented known-item
        # alongside the GitHub-repo dangling case (see docstring above).
        # Disabled when template_init_script_path is empty.
        try:
            invoke_init_script(db, project, enable_coordinator=payload.enable_coordinator)
        except TemplateBootstrapError as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Filesystem bootstrap failed: {exc}",
            ) from exc

        # Stage 4 — F-004 K-001 push + verify, K-002 rollback on failure.
        # Runs only if payload.repo_url + source_path + .git directory exist.
        # The .git check is post-init.sh: dry-run mode, disabled bootstrap
        # (template_init_script_path=""), or partial scaffold failure all
        # legitimately produce no .git → Stage 4 logged + skipped (best-effort,
        # matches Stage 5+6 pattern). Rollback v partial-push-failure:
        # local .git deleted; GitHub repo zostáva (manual cleanup by Director).
        stage4_should_run = (
            payload.repo_url and project.source_path and Path(project.source_path).joinpath(".git").is_dir()
        )
        if stage4_should_run:
            from backend.services.template_bootstrap import _repo_from_url

            repo_full_name = _repo_from_url(payload.repo_url, project.slug)
            try:
                push_and_verify(target=project.source_path, repo_full_name=repo_full_name)
            except GitPushVerificationError as exc:
                # K-002: clean up local .git so re-run is idempotent
                rollback_partial_state(
                    target=project.source_path,
                    repo_full_name=repo_full_name,
                    delete_github_repo=False,  # Director confirms manually
                )
                db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=(
                        f"Stage 4 push+verify failed: {exc}. Local .git rolled back. "
                        f"GitHub repo {repo_full_name} preserved — Director cleanup if needed."
                    ),
                ) from exc
        elif payload.repo_url and project.source_path:
            # repo_url + source_path set but no .git — log info, skip Stage 4.
            # Common in tests (init.sh dry-run mode) or disabled bootstrap.
            logger.info(
                "Stage 4 (push+verify) SKIPPED for slug=%s — no .git directory in %s "
                "(init.sh dry-run, disabled bootstrap, or partial scaffold)",
                project.slug,
                project.source_path,
            )

        # Stage 5+6 — F-004 K-004 smoke test + K-005 CI/CD opt-in.
        # Both are best-effort (logged warnings, NIE 500) — partial success
        # acceptable. Director can re-run smoke / wire CI manually if needed.
        from backend.services.create_project_postscaffold import (
            run_post_scaffold_steps,
        )

        run_post_scaffold_steps(
            target=project.source_path or "",
            slug=project.slug,
            repo_url=payload.repo_url,
            enable_cicd=payload.enable_cicd,
            full_smoke=payload.full_smoke,
            enable_branch_protection=payload.enable_branch_protection,
        )

        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    except OSError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initialise live documents: {exc}",
        ) from exc
    db.refresh(project)
    return ProjectRead.model_validate(project)


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: UUID,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
) -> ProjectRead:
    """Partially update a project's mutable fields.

    ``id``, ``slug``, ``category``, ``created_by`` and ``created_at`` are
    immutable; ``updated_at`` is refreshed by the ORM. Fields omitted from
    the payload are left unchanged.
    """
    try:
        project = project_service.update(db, project_id, payload)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(project)
    return ProjectRead.model_validate(project)


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_project(
    project_id: UUID,
    db: Session = Depends(get_db),
    kb_writer: KnowledgeBaseWriter = Depends(get_knowledge_base_writer),
    delete_github: bool = Query(
        default=False,
        description=(
            "When true, also delete the project's GitHub repository via "
            "the API (needs delete_repo token scope). Default false — "
            "only the DB row and KB folder are removed, repo stays."
        ),
    ),
) -> Response:
    """Hard-delete a project by primary key.

    Every inbound FK to ``projects.id`` uses ``ON DELETE CASCADE``, so
    dependent rows (modules, specifications, design documents,
    KB docs, architect sessions, epics, bugs, delegations, migration
    tables, report configs) are removed automatically. Archiving is the
    preferred soft-disable path — callers should prefer ``PATCH`` with
    ``status='archived'`` and reserve delete for test fixtures / admin
    tooling.

    Side effects on success:

    * The KB folder ``{knowledge_base_path}/projects/{slug}/`` with
      its live documents (STATUS.md / HISTORY.md) is removed — matches
      the rest of the live-docs hooks.
    * If ``delete_github=true`` is passed, the backing GitHub
      repository is deleted too. Off by default — the DB row and KB
      folder go, but the repo stays in case the caller wants to
      re-register the project later or keep history on GitHub.
    """
    # Capture slug + repo_url before deletion so we can drive the
    # KB / GitHub cleanup after the DB row is gone. Uses the service
    # lookup so "missing" still surfaces as a clean ValueError.
    try:
        project = project_service.get_by_id(db, project_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc

    slug = project.slug
    repo_url = project.repo_url
    uat_slug = project.uat_slug  # capture before delete for UAT teardown (v0.9.0 Phase 3 CR-2)

    try:
        project_service.delete(db, project_id)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc

    # UAT teardown — orphan prevention (v0.9.0 Phase 3 CR-2). A deleted project's UAT is orphaned:
    # tear down its containers (`docker compose down -v`) + reclaim the allocated port. Best-effort,
    # never raises, never undoes the committed delete — mirrors the KB / GitHub cleanups below.
    # (Version supersede is NOT a teardown — a new version of a LIVE project redeploys the same UAT.)
    if uat_slug:
        ok, detail = uat_provisioner.teardown_uat(uat_slug)
        if not ok:
            logger.warning("UAT teardown failed for deleted project %r (uat_slug=%r): %s", slug, uat_slug, detail)

    # KB cleanup — best-effort. A failure here does not undo the DB
    # delete (that has already committed); we log and return 204 so
    # the caller sees the project as gone, and a follow-up orphan
    # scan picks up any stragglers.
    try:
        kb_writer.delete_project(slug)
    except OSError as exc:
        logger.warning("KB cleanup failed for deleted project %r: %s", slug, exc)

    # GitHub repo cleanup — opt-in.
    if delete_github and repo_url:
        gh_timeout = float(system_setting_service.get_int(db, "github_api_timeout_seconds"))
        try:
            github_validation_service.delete_github_repo(repo_url, timeout=gh_timeout)
        except (ValueError, RuntimeError) as exc:
            logger.warning("GitHub repo delete failed for %r: %s", repo_url, exc)
        except httpx.HTTPError as exc:
            logger.warning("GitHub API unreachable during delete of %r: %s", repo_url, exc)

    return Response(status_code=status.HTTP_204_NO_CONTENT)
