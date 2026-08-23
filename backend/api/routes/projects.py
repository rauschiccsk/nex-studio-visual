"""REST router for :class:`~backend.db.models.projects.Project`.

Exposes the standard CRUD surface for projects:

* ``GET    /``              → paginated list (filter by ``status``,
  ``type`` and ``created_by``).
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
import shutil
import subprocess
from pathlib import Path
from typing import Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.api.dependencies import get_knowledge_base_writer, get_rag_indexer
from backend.core import authz
from backend.core.security import get_current_user, require_shu_or_above
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
    ProjectCreate,
    ProjectRead,
    ProjectStatus,
    ProjectType,
    ProjectUpdate,
)
from backend.schemas.version import VersionCreate
from backend.services import deploy as deploy_service
from backend.services import git_state as git_state_service
from backend.services import github_validation as github_validation_service
from backend.services import nexshared as nexshared_service
from backend.services import port_registry as port_registry_service
from backend.services import project as project_service
from backend.services import project_memory, uat_provisioner
from backend.services import system_setting as system_setting_service
from backend.services import version as version_service
from backend.services.knowledge_base_writer import KnowledgeBaseWriter
from backend.services.template_bootstrap import (
    GitPushVerificationError,
    TemplateBootstrapError,
    invoke_init_script,
    push_and_verify,
    resolve_github_org,
    rollback_partial_state,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Projects"],
    # v4.0.35: any authenticated user may reach the projects surface; per-route ownership checks
    # (backend.core.authz) then scope a Junior (shu) to their OWN projects (created_by == self).
    dependencies=[Depends(require_shu_or_above)],
)


#: Manager-facing names for the three port fields. The wire names (``backend_port``) are ours, not his:
#: an error he is expected to act on must name the field the way the screen labels it.
_FIELD_SK = {"backend_port": "Backend", "frontend_port": "Frontend", "db_port": "Databáza"}


def _validate_ports(db: Session, payload: ProjectCreate | ProjectUpdate, *, project_id: UUID | None = None) -> None:
    """Validate that all supplied ports are in range and not already allocated.

    Raises :class:`~fastapi.HTTPException` directly — 422 for out-of-range,
    409 for conflicts.

    ``project_id`` is passed on an UPDATE so the project's OWN current ports are not
    reported as a conflict with itself. Until v4.0.90 this ran on create only: ``PATCH``
    set the columns with no checking at all, so the one path that could repair a bad
    allocation was also the one path that could write a port the create route refuses —
    a reserved block, or one another project already holds.
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

        # Availability — resolved against the projects table, the declared
        # reservations AND the host's own published-port map. The table alone
        # used to decide this, which is why a port a neighbouring container had
        # been publishing for twelve days could be handed out again.
        verdict = port_registry_service.describe_port_availability(db, port_value, project_id)

        # Fail CLOSED. "Could not verify" is not "free": creating a project on
        # an unverified port is how a live customer deployment loses its port.
        # 503 (not 4xx) because nothing is wrong with the request — the host
        # check is unavailable and the operator should retry once it is back.
        if verdict.state == port_registry_service.UNKNOWN:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    f"Port {port_value} ({_FIELD_SK.get(field_name, field_name)}) sa na tomto stroji "
                    f"nedal overiť, takže sa nepridelí. {verdict.reason}"
                ),
            )

        if verdict.state == port_registry_service.TAKEN:
            if verdict.source == "reserved":
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=(
                        f"Port {port_value} ({_FIELD_SK.get(field_name, field_name)}) patrí do "
                        f"rezervovaného bloku, ktorý má pridelený {verdict.holder}. "
                        f"Vyber port z bloku tohto projektu."
                    ),
                )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=PortConflictError(
                    detail=(
                        f"Port {port_value} ({_FIELD_SK.get(field_name, field_name)}) je už pridelený. {verdict.reason}"
                    ),
                    port=port_value,
                    # Only a cockpit project has a project name; a port held by a
                    # neighbouring container reports its container in ``reason``.
                    conflict_project=(verdict.holder if verdict.source == "projects" else None),
                ).model_dump(),
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
        # Read the CONFIGURED block size rather than a hardcoded 10. ``port_block_size`` is a live,
        # editable setting that ``port_registry.suggest_next_port_block`` honours — so with any value
        # other than 10, the cockpit SUGGESTED a correctly-aligned base and then rejected it here for
        # not being a multiple of ten. One source, not two.
        block_size = system_setting_service.get_int(db, "port_block_size")
        if block_size < 1:  # a nonsensical setting must not disable the check
            block_size = 10
        if bp % block_size != 0:
            first = 10100 - (10100 % block_size)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Port backendu {bp} musí byť začiatkom bloku po {block_size} portoch "
                    f"({first}, {first + block_size}, {first + 2 * block_size}, …) — tak je rozdelený "
                    f"komerčný rozsah (D-020). Prvý port bloku otvára pridelený úsek projektu."
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


def _host_probe_unavailable(exc: Exception) -> HTTPException:
    """Translate a failed host port probe into 503.

    Deliberately NOT a fallback to a table-only answer. Port availability is
    only meaningful when the host has been consulted; degrading to "free"
    because the probe broke is precisely the silent double-book this guard
    exists to prevent, so the endpoint refuses and says why.
    """
    logger.warning("Host port probe unavailable — refusing to suggest a port: %s", exc)
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            f"Port availability cannot be verified against this host right now, so no port is being suggested. {exc}"
        ),
    )


def _workspace_safe_to_remove(source_path: str, root: Path) -> bool:
    """True iff ``source_path`` is an existing directory strictly UNDER ``root`` — never ``root`` itself,
    never a path outside it (CR-V2-027). The guard before ``shutil.rmtree`` of a deleted project's
    workspace: ``source_path`` is system-set, but an rmtree on the wrong path is catastrophic, so this is
    defence-in-depth."""
    if not source_path:
        return False
    ws = Path(source_path).resolve()
    r = root.resolve()
    return ws != r and ws.is_relative_to(r) and ws.is_dir()


def _uat_teardown_destroys_data(db: Session, project_id: UUID, uat_slug: str) -> bool:
    """True iff tearing this project's UAT down would really destroy data — the guard before a delete.

    :func:`uat_provisioner.teardown_uat` runs ``docker compose down -v``; the ``-v`` takes the UAT
    VOLUMES, i.e. the customer's UAT DATABASE and everything they have entered into it. Two independent
    signals say a UAT holds data — EITHER is enough, because neither sees the whole picture:

    * A rendered UAT stack on disk (``/opt/uat/<uat_slug>/docker-compose.yml``) — teardown shells out
      against exactly this file. A UAT brought up by ``scripts/uat-deploy.py`` leaves no ``deploy_events``
      row at all, so this is the ONLY signal that sees it (``/opt/uat/inbox`` is a live customer UAT).
    * A recorded successful UAT deploy — the deploy went through the cockpit, so there is a database
      behind it holding whatever the customer has entered since.

    No compose AND no successful UAT deploy = teardown is its own documented no-op ("nothing to tear
    down") and the delete destroys no data: the early/throwaway project the hard delete exists for.
    ``UAT_ROOT`` is read off the module at call time so a test can point it at a tmp dir.
    """
    if (uat_provisioner.UAT_ROOT / uat_slug / "docker-compose.yml").is_file():
        return True
    return any(
        event.environment == "uat" and event.event_type == "deploy" and event.status == "ok"
        for event in deploy_service.list_project_events(db, project_id)
    )


def _workspace_holds_foreign_files(source_path: str | None) -> bool:
    """True when ``source_path`` already holds files — i.e. it is NOT ours to delete.

    Sampled BEFORE Stage 3 scaffolds anything, which is the only moment the answer is knowable:
    afterwards init.sh's own output is indistinguishable from the Manager's. A missing or empty
    directory is not foreign — that is the greenfield shape init.sh creates and may therefore undo.
    Unreadable is treated as foreign: when we cannot tell, we do not delete.
    """
    if not source_path:
        return False
    path = Path(source_path)
    if not path.exists():
        # GREENFIELD — the ordinary case, and the one this used to get wrong. The directory does not
        # exist yet; init.sh creates it. Treating "missing" as foreign (which ``iterdir`` did, by
        # raising FileNotFoundError into the except below) meant the cleanup NEVER ran on a normal
        # create, so one failed founding left the scaffold behind and burned that project name for
        # good — the exact harm the cleanup exists to prevent.
        return False
    try:
        return any(path.iterdir())
    except OSError:
        # It exists but cannot be read. That is genuinely unknown, and unknown is not ours to delete.
        return True


def _discard_orphaned_workspace(source_path: str | None, slug: str, *, scaffolded_by_this_create: bool) -> None:
    """Remove a workspace left behind by a create that failed AFTER init.sh scaffolded it.

    Without this, one half-way failure kills that project name FOREVER. The DB row is rolled back,
    so DELETE /projects/{id} — the only route that removes a workspace — has nothing to delete; and
    a retry cannot succeed either, because init.sh refuses a target that already holds a CLAUDE.md
    unless --force, which we never pass. The Manager was left with a slug he could not create and a
    directory he could not reach from the cockpit, with no message saying so.

    ``scaffolded_by_this_create`` decides whether there is anything to clean up at all, and it is
    KEYWORD-ONLY WITH NO DEFAULT so that no future caller can reach the ``rmtree`` without having
    answered the question. It exists because location is not origin, and this function once trusted
    location alone: ``_workspace_safe_to_remove`` proves the path lies under ``PROJECTS_ROOT``, which
    is equally true of every project the Manager already has. Greenfield auto-founding is now refused
    (:mod:`backend.services.template_bootstrap`), so the ONLY remaining way to reach a create with a
    populated ``source_path`` is BROWNFIELD ADOPTION — pointing the cockpit at an existing checkout.
    A Stage-4 push failure there (bad token, no network, a rejected verify) would have deleted the
    Manager's entire source tree, and the very fix that stops a failed create from blocking a project
    name is what put that tree within reach. So: we discard only what we ourselves scaffolded, and an
    adopted workspace is left exactly as we found it.

    Also guarded by ``_workspace_safe_to_remove`` (strictly under PROJECTS_ROOT, never the root
    itself) and never raises: a cleanup failure must not replace the real error with its own.
    """
    from backend.services.claude_agent import PROJECTS_ROOT

    if not scaffolded_by_this_create:
        logger.info(
            "Kept the workspace after a failed create: slug=%s path=%s — it already held files before "
            "this request, so it is an adopted checkout, not ours to remove",
            slug,
            source_path,
        )
        return
    if not source_path or not _workspace_safe_to_remove(source_path, PROJECTS_ROOT):
        return
    try:
        shutil.rmtree(source_path)
        logger.info("Removed orphaned workspace after failed create: slug=%s path=%s", slug, source_path)
    except OSError as exc:  # pragma: no cover - defensive
        logger.warning(
            "Could not remove orphaned workspace after failed create (slug=%s, path=%s): %s — "
            "the next create for this slug will fail on the leftover directory",
            slug,
            source_path,
            exc,
        )


# The standard owner for a project workspace: uid:gid 1000:1000 — the host user, matching every existing
# tree under ``/opt/projects`` AND the Vizuál sandbox's ``SANDBOX_USER``. It is NOT the backend's own uid:
# the backend container runs as root (0:0), so a freshly scaffolded workspace lands root-owned. That breaks
# any NON-root tooling that later operates on it — most concretely the Vizuál live-preview sandbox, which
# runs ``npm install`` as uid 1000 and cannot write ``node_modules`` into a root-owned ``frontend/`` (v4.0.44).
_WORKSPACE_OWNER = "1000:1000"


def _chown_workspace(source_path: str | None) -> None:
    """Best-effort ``chown -R 1000:1000`` of a freshly created project workspace.

    The backend runs as root, so it creates the workspace root-owned; align it with ``/opt/projects``
    convention + the Vizuál sandbox uid so non-root tooling can operate on it. Logged, NEVER raises — a
    chown failure must not abort (or roll back) an otherwise successful create. root + the agent keep
    working afterwards (root bypasses ownership)."""
    if not source_path:
        return
    try:
        result = subprocess.run(
            ["chown", "-R", _WORKSPACE_OWNER, source_path],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            logger.warning(
                "workspace chown to %s failed for %s: %s",
                _WORKSPACE_OWNER,
                source_path,
                (result.stderr or "").strip(),
            )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("workspace chown to %s errored for %s: %s", _WORKSPACE_OWNER, source_path, exc)


@router.get("", response_model=PaginatedResponse[ProjectRead])
def list_projects(
    status_filter: Optional[ProjectStatus] = Query(
        default=None,
        alias="status",
        description="Filter by lifecycle status (active | archived | paused).",
    ),
    type: Optional[ProjectType] = Query(
        default=None,
        description="Filter by archetype (standard | web).",
    ),
    created_by: Optional[UUID] = Query(
        default=None,
        description="Filter by the creating user's id.",
    ),
    skip: int = Query(default=0, ge=0, description="Number of rows to skip."),
    limit: int = Query(default=50, ge=1, le=100, description="Maximum rows to return."),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PaginatedResponse[ProjectRead]:
    """Return a paginated list of projects.

    A Junior (``shu``) sees ONLY their own projects (``created_by`` forced to self); ri/ha see all
    (optionally narrowed by the ``created_by`` filter). (v4.0.35.)
    """
    if not authz.is_admin(current_user):
        created_by = current_user.id
    try:
        rows = project_service.list_projects(
            db,
            status=status_filter,
            type=type,
            created_by=created_by,
            limit=limit,
            offset=skip,
        )
        total = project_service.count_projects(
            db,
            status=status_filter,
            type=type,
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
    """Check whether a port is available in the ICC Port Registry range.

    Answers from all three sources (projects table + declared reservations +
    the host's published-port map). Returns 200 with ``state="unknown"`` when
    the host could not be consulted — the caller is told plainly that the port
    is unverified instead of being shown a green "available".
    """
    try:
        verdict = port_registry_service.describe_port_availability(db, port, project_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc

    return PortCheckResponse(
        available=verdict.available,
        conflict_project=(verdict.holder if verdict.source == "projects" else None),
        state=verdict.state,
        holder=verdict.holder,
        source=verdict.source,
        reason=verdict.reason,
        warnings=list(verdict.warnings),
    )


@router.get("/ports/suggest", response_model=PortSuggestResponse)
def suggest_port(
    type: str = Query(
        ...,
        alias="type",
        description="Port type: backend | frontend | db.",
    ),
    db: Session = Depends(get_db),
) -> PortSuggestResponse:
    """Suggest the next available port for the given type.

    Refuses (503) rather than suggesting a port it cannot verify against the
    host — an unverifiable suggestion is exactly how a port already published
    by a neighbouring container gets handed out a second time.
    """
    try:
        suggested = port_registry_service.suggest_next_port(db, type)
    except port_registry_service.HostProbeError as exc:
        raise _host_probe_unavailable(exc) from exc
    except ValueError as exc:
        raise _map_value_error(exc) from exc

    return PortSuggestResponse(
        suggested_port=suggested,
        warnings=port_registry_service.reserved_ranges_status(db).warnings,
    )


@router.get("/ports/suggest-block", response_model=PortBlockSuggestResponse)
def suggest_port_block(
    db: Session = Depends(get_db),
) -> PortBlockSuggestResponse:
    """Return the base port of the first free 10-port block in the registry.

    Used by the new-project form to auto-fill the three port inputs
    (backend / frontend / db) from a contiguous block per
    DECISIONS.md D-020 (Port Registry v2, 10-port blocks).

    The block is free on all three sources — the cockpit's own table, the
    declared reservations, and the host's published-port map. If the host
    cannot be consulted no block is suggested (503).
    """
    try:
        base = port_registry_service.suggest_next_port_block(db)
    except port_registry_service.HostProbeError as exc:
        raise _host_probe_unavailable(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return PortBlockSuggestResponse(
        base=base,
        block_size=system_setting_service.get_int(db, "port_block_size"),
        warnings=port_registry_service.reserved_ranges_status(db).warnings,
    )


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectRead:
    """Return a single project by primary key."""
    try:
        project = project_service.get_by_id(db, project_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    authz.authorize_project(current_user, project)
    result = ProjectRead.model_validate(project)
    # CR-V2-027: compute the PROD-deploy flag here (one query) so the FE can disable + explain the
    # delete control without a second round-trip. Only authoritative on this detail endpoint.
    result.has_prod_deploy = deploy_service.project_had_prod_deploy(db, project.id)
    return result


@router.get("/{project_id}/nexshared-status")
def get_nexshared_status(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """nex-shared upgrade status for the auto-notify prompt (#3): the app's pinned version vs
    the latest published tag + the "Čo prinesie" changelog delta. Drives the opt-in prompt shown
    when the Manažér founds a new version. Never a false prompt (no pin / no source → offer nothing)."""
    try:
        project = project_service.get_by_id(db, project_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    authz.authorize_project(current_user, project)
    if not project.source_path:
        return {"current": None, "latest": None, "behind": 0, "up_to_date": False, "changelog": []}
    return nexshared_service.status_for_source(project.source_path)


class _NexsharedUpgradeRequest(BaseModel):
    target_version: str


@router.post("/{project_id}/nexshared-upgrade")
def upgrade_nexshared(
    project_id: UUID,
    payload: _NexsharedUpgradeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Opt-in bump (#3): rewrite the app's ``frontend/package.json`` nex-shared pin to
    ``target_version`` + commit it, so the new version's build (and its Vizuál preview) run on
    the chosen nex-shared. The Manažér decides per version; nothing happens without this call."""
    try:
        project = project_service.get_by_id(db, project_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    authz.authorize_project(current_user, project)
    if not project.source_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Projekt nemá zdrojovú cestu.")
    if not nexshared_service.upgrade_source_pin(project.source_path, payload.target_version):
        # Since the lockfile step exists, this covers far more than a bad pin: npm unreachable, the
        # resolver failing, or the lockfile not actually reaching the target. Saying "neplatná verzia"
        # for a network blip sent the Manager looking for a typo that was not there.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Povýšenie sa nepodarilo dokončiť — verzia knižnice sa nezapísala do zámku "
                "závislostí, takže by sa zostavovala pôvodná. V projekte sa nič nezmenilo. "
                "Skús to znova; ak to potrvá, over dostupnosť GitHubu."
            ),
        )
    committed = nexshared_service.commit_pin_upgrade(project.source_path, payload.target_version)
    return {"upgraded": True, "target_version": payload.target_version, "committed": committed}


@router.get("/{project_id}/git-status")
def get_git_status(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Working-tree preflight for the New-Version flow (v4.0.25): is the project's tree
    clean enough to found a version? A dirty tree would surface uncommitted work to the
    pipeline agent as an expert scope question the operator can't answer (Tibor/Nazar lens).
    A project with no source path has nothing to guard → reported clean."""
    try:
        project = project_service.get_by_id(db, project_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    authz.authorize_project(current_user, project)
    if not project.source_path:
        return {"clean": True, "dirty_count": 0, "files": [], "truncated": False}
    if not git_state_service.workspace_is_git_repo(project.source_path):
        # A workspace with no git repository has nothing to guard — the same answer as a project with no
        # source path, and for the same reason. This used to be a 400, which the New-Version screen turned
        # into a permanently disabled "Uložiť Zadanie" with no stated reason and no way to clear it: the
        # only controls that reset the error live in a panel that renders solely for a DIRTY tree, and a
        # 400 leaves the status null, so that panel could never appear. Founding was blocked outright for
        # every project whose workspace is not a checkout.
        return {"clean": True, "dirty_count": 0, "files": [], "truncated": False, "not_a_repo": True}
    try:
        return git_state_service.working_tree_status(project.source_path)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


class _GitCommitRequest(BaseModel):
    message: Optional[str] = None


@router.post("/{project_id}/git-commit")
def commit_git(
    project_id: UUID,
    payload: _GitCommitRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Commit ALL pending changes so the tree is clean before founding (the guard's
    "Uložiť ich" action). Preserves work — the safe, default resolution."""
    try:
        project = project_service.get_by_id(db, project_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    authz.authorize_project(current_user, project)
    if not project.source_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Projekt nemá zdrojovú cestu.")
    try:
        result = git_state_service.commit_all(project.source_path, payload.message or "")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result.get("error", "Commit zlyhal"))
    return result


@router.post("/{project_id}/git-discard")
def discard_git(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Discard ALL pending changes → clean tree (the guard's "Zahodiť" action).
    Destructive; the FE gates it behind an explicit operator confirmation."""
    try:
        project = project_service.get_by_id(db, project_id)
    except ValueError as exc:
        raise _map_value_error(exc) from exc
    authz.authorize_project(current_user, project)
    if not project.source_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Projekt nemá zdrojovú cestu.")
    try:
        result = git_state_service.discard_all(project.source_path)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not result.get("ok"):
        # Pass the service's own reason through. It distinguishes "nothing was deleted, these items
        # cannot be discarded" from "the tree is still dirty afterwards" — a difference the Manager
        # must see, because one of them means his work is intact and the other does not. The generic
        # sentence that used to be here hid both.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error") or "Zahodenie zmien zlyhalo.",
        )
    return result


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
    current_user: User = Depends(require_shu_or_above),
    db: Session = Depends(get_db),
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
    * **Seeds the AI Agent's per-project ``MEMORY.md``** in the project
      workspace (``/opt/projects/<slug>/``) once the Stage-3 init script
      has created the checkout. This is the single source of truth for
      project status / history (CR-V2-016, R-DOUBLEWRITE); the old
      DB-driven STATUS.md / HISTORY.md seeding is retired. The agent owns
      every later write (exactly one writer).
    * **Auto-creates initial version v0.1.0** in ``planned`` status —
      per main CLAUDE.md §2 (no spec change without a version binding).
    * The DB insert, version creation and commit happen in a single
      transaction — a failure at any step rolls the row back. If a later
      stage fails after the GitHub repo was already created, the repo
      stays dangling (documented known-item; manual cleanup on the
      GitHub side).
    """
    # Pre-creation validation (ports).
    _validate_ports(db, payload)

    # v4.0.35: the creator is the AUTHENTICATED user — never a client-supplied UUID (that let any caller
    # forge created_by / fall back to a random ri). This binds project ownership to the real creator, so
    # the authz layer can scope a Junior to their own projects.
    payload.created_by = current_user.id

    # Notification owner (CR-NS-012) defaults to the creator when omitted.
    if payload.owner_id is None:
        payload.owner_id = payload.created_by

    gh_timeout = float(system_setting_service.get_int(db, "github_api_timeout_seconds"))
    # The configured organisation, resolved ONCE and threaded through every repo-derived stage below
    # (scaffold argv, push+verify, CI runner, branch protection). Stage 1 honours ``repo_url``
    # verbatim; the later stages used to re-derive an owner and fell back to a hardcoded one, so a
    # non-default organisation was honoured at repo creation and nowhere else.
    github_org = resolve_github_org(db)

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

    # Did WE scaffold the workspace this request is about? Only then may a failure discard it.
    # Starts False so the exits that fire before any scaffolding — a duplicate slug, a rejected
    # payload — can never reach the rmtree, and is answered for real just before Stage 3 runs.
    scaffolded_here = False

    try:
        project = project_service.create(db, payload)
        # CR-V2-016: STATUS.md/HISTORY.md DB-driven seeding is RETIRED — the AI Agent's
        # own per-project ``MEMORY.md`` is the single source of truth (R-DOUBLEWRITE).
        # The memory is seeded AFTER the workspace exists (Stage 3, ``invoke_init_script``),
        # since it lives in the project workspace (``/opt/projects/<slug>/MEMORY.md``).
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
        # DB row cleanly. Disabled when template_init_script_path is empty.
        # Sampled HERE, the last instant the answer is knowable: once init.sh has run, its own output
        # is indistinguishable from files the Manager brought. Everything below keys its cleanup off
        # this one reading.
        scaffolded_here = not _workspace_holds_foreign_files(project.source_path)

        try:
            invoke_init_script(db, project)
        except TemplateBootstrapError as exc:
            db.rollback()
            # init.sh runs under `set -euo pipefail` and writes CLAUDE.md well before it finishes, so
            # anything failing after that point — the nex-shared lock guard, a git commit, a full
            # disk, the subprocess timeout — leaves a half-scaffolded directory behind. That directory
            # then blocks the slug FOREVER: the DB row is gone, so no delete route can reach it, and a
            # retry dies in Stage 1 on the leftover. This exit was the one most likely to produce it
            # and the one that did not clean up.
            _discard_orphaned_workspace(project.source_path, project.slug, scaffolded_by_this_create=scaffolded_here)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Filesystem bootstrap failed: {exc}",
            ) from exc

        # CR-V2-018: provision the v2 two-agent ``Pravidlá agenta`` charters into the just-bootstrapped
        # workspace + normalise it to v2 shape. HARD step (unlike the best-effort post-scaffold seeds):
        # the engine fail-closes on a missing ai-agent/auditor charter, so a silent skip would block the
        # pipeline at first dispatch with "Agent dispatch failed". Rolls back + 500s on failure (mirrors
        # Stage 3); no-ops when there is no checkout on disk (dry-run / disabled bootstrap). Bound to the
        # engine's project root (``PROJECTS_ROOT / slug`` = the agent's cwd + charter-read root), NOT
        # ``source_path`` — equal under the invariant, but binding here keeps charters + deny globs aligned
        # with where the engine actually reads/runs.
        from backend.services.claude_agent import PROJECTS_ROOT
        from backend.services.create_project_postscaffold import (
            ProvisioningError,
            provision_v2_agent_charters,
        )

        try:
            provision_v2_agent_charters(
                PROJECTS_ROOT / project.slug, project.slug, project.name, adopted=not scaffolded_here
            )
        except ProvisioningError as exc:
            db.rollback()
            _discard_orphaned_workspace(project.source_path, project.slug, scaffolded_by_this_create=scaffolded_here)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"v2 agent charter provisioning failed: {exc}",
            ) from exc

        # CR-V2-016: seed the AI Agent's per-project ``MEMORY.md`` (single source of
        # truth — replaces the retired STATUS.md/HISTORY.md DB-driven seeding). One-shot
        # scaffold in the just-bootstrapped workspace; the agent owns every later write
        # (R-DOUBLEWRITE — exactly one writer). No-ops when there is no checkout on disk
        # (dry-run / disabled bootstrap) or when a memory already exists.
        project_memory.seed_memory(project.slug, project.name)

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

            repo_full_name = _repo_from_url(payload.repo_url, project.slug, default_owner=github_org)
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
                _discard_orphaned_workspace(
                    project.source_path, project.slug, scaffolded_by_this_create=scaffolded_here
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=(
                        f"Stage 4 push+verify failed: {exc}. Local workspace removed so the same "
                        f"project name can be created again. GitHub repo {repo_full_name} "
                        f"preserved — Director cleanup if needed."
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

        setup_warnings = run_post_scaffold_steps(
            target=project.source_path or "",
            slug=project.slug,
            repo_url=payload.repo_url,
            project_type=project.type,
            auth_mode=project.auth_mode,
            enable_cicd=payload.enable_cicd,
            full_smoke=payload.full_smoke,
            enable_branch_protection=payload.enable_branch_protection,
            github_org=github_org,
        )

        # Align the freshly scaffolded (root-owned) workspace with the 1000:1000 convention so the
        # non-root Vizuál sandbox can operate on it. Last step of scaffolding, so it covers everything
        # init.sh + charter provisioning + post-scaffold wrote. Best-effort (never aborts the create).
        _chown_workspace(project.source_path)

        db.commit()

        # Record the block in the ONE registry (ICCINT-2). Runs after the commit so we never
        # claim a block for a create that rolled back. Reading the registry stops us handing out
        # a neighbour's ports; writing back is what stops the registry going stale again — every
        # hole found on 21.08.2026 came from an allocation nobody copied into the KB by hand.
        # Best-effort, but the failure travels to the Manažér in setup_warnings rather than dying
        # in the log: an unrecorded block is exactly how the next project gets handed this one.
        block_warning = port_registry_service.record_allocation(
            slug=project.slug,
            base=project.backend_port,
            block_size=system_setting_service.get_int(db, "port_block_size") or 10,
            backend_port=project.backend_port,
            frontend_port=project.frontend_port,
            db_port=project.db_port,
        )
        if block_warning:
            setup_warnings = [*setup_warnings, block_warning]
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    except OSError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create project filesystem state: {exc}",
        ) from exc
    db.refresh(project)
    result = ProjectRead.model_validate(project)
    # What did NOT finish. The steps are best-effort by design and the project is genuinely created,
    # but "the Manager can finish manually" only works if he is told there is something to finish.
    result.setup_warnings = setup_warnings
    return result


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: UUID,
    payload: ProjectUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProjectRead:
    """Partially update a project's mutable fields.

    ``id``, ``slug``, ``type``, ``auth_mode``, ``created_by`` and
    ``created_at`` are immutable; ``updated_at`` is refreshed by the ORM. Fields omitted from
    the payload are left unchanged.
    """
    authz.assert_project_id_access(db, current_user, project_id)

    # Ports get the SAME scrutiny as on create. Without this the update path could write a
    # port the create path refuses — a reserved block, or one another project already holds.
    # ``project_id`` keeps the project's own current ports from reading as a conflict.
    touches_ports = any(field in payload.model_fields_set for field in ("backend_port", "frontend_port", "db_port"))
    if touches_ports:
        _validate_ports(db, payload, project_id=project_id)

    try:
        project = project_service.update(db, project_id, payload)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise _map_value_error(exc) from exc
    db.refresh(project)

    # Move the block in the registry too. A port changed here and left unrecorded there is
    # exactly the drift ICCINT-2 removed: the abandoned block would stay reserved against
    # everyone else and the new one would look free to the next project.
    result = ProjectRead.model_validate(project)
    if touches_ports:
        block_warning = port_registry_service.record_allocation(
            slug=project.slug,
            base=project.backend_port,
            block_size=system_setting_service.get_int(db, "port_block_size") or 10,
            backend_port=project.backend_port,
            frontend_port=project.frontend_port,
            db_port=project.db_port,
        )
        if block_warning:
            result.setup_warnings = [block_warning]
    return result


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def delete_project(
    project_id: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_shu_or_above),
    kb_writer: KnowledgeBaseWriter = Depends(get_knowledge_base_writer),
    rag_indexer: RAGIndexer = Depends(get_rag_indexer),
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

    Guards (CR-V2-027, v4.0.35): **owner or privileged** (the project's creator, or an ``ri``/``ha`` lead;
    a Junior can delete only their OWN project, others get 403),
    **only a project that has never had a successful PROD deploy** — once a project graduates to PROD it
    can only be archived (409 otherwise) — and **only a project whose UAT teardown destroys no data**
    (409 otherwise): the teardown below is ``docker compose down -v``, so a UAT that was really deployed
    would lose its database with it (see :func:`_uat_teardown_destroys_data`). Archiving is the preferred
    soft-disable path generally — callers should prefer ``PATCH`` with ``status='archived'`` and reserve
    delete for early/throwaway projects.

    Every inbound FK to ``projects.id`` uses ``ON DELETE CASCADE``, so
    dependent rows (modules, specifications, design documents,
    KB docs, architect sessions, epics, bugs, delegations, migration
    tables, report configs — and the project's **customers** with their whole
    **deploy/acceptance history**) are removed automatically.

    Side effects on success:

    * The project's UAT environment ``{uat_slug}`` is torn down with ``docker compose down -v``
      (best-effort). The ``-v`` takes its volumes, so this is destructive — hence the guard above,
      which lets the delete through only when there is no UAT data to lose.

    * The on-disk project workspace ``{source_path}`` (= ``/opt/projects/{slug}``, incl. the per-project
      ``MEMORY.md``) is removed so the slug can be cleanly re-created (best-effort, guarded to a path
      strictly under PROJECTS_ROOT).
    * The KB folder ``{knowledge_base_path}/projects/{slug}/`` (any
      project-scoped KB docs) is removed so a deleted project leaves no
      orphaned KB tree.
    * The project's RAG entries (Qdrant points in tenant ``icc`` whose
      ``source_file`` is under ``projects/{slug}/``) are removed too, so a
      deleted project also leaves no ghost in RAG search — best-effort,
      keyed on the slug prefix (the disk docs are already gone).
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
    # v4.0.35: delete was owner-or-admin — now owner-or-admin (a Junior may delete their OWN throwaway project; a
    # Medior does NOT gain delete on projects they don't own). ha is NOT privileged for this write.
    authz.authorize_project(current_user, project)

    slug = project.slug
    repo_url = project.repo_url
    source_path = project.source_path  # capture before delete for workspace cleanup (CR-V2-027)
    uat_slug = project.uat_slug  # capture before delete for UAT teardown (v0.9.0 Phase 3 CR-2)

    # CR-V2-027: a project graduates to PROD on its first successful prod deploy; from then on it can
    # only be archived, never hard-deleted (the deploy audit trail + any live customers must survive).
    if deploy_service.project_had_prod_deploy(db, project_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Projekt {slug!r} už bol nasadený do PROD — namiesto mazania ho archivuj (PATCH status='archived')."
            ),
        )

    # Same guard for a UAT that holds data (audit 2026-07-28). The teardown below is
    # ``docker compose down -v`` — it takes the UAT database and everything the customer ever entered
    # into it, which is exactly as irreversible as the PROD history the guard above protects. Covering
    # PROD but not UAT was an oversight, not a policy: /opt/uat carries live customer data, and a
    # project slug maps straight onto it (``nex-inbox`` → ``/opt/uat/inbox``). A project whose UAT
    # would really be destroyed can therefore only be archived, same as a PROD one.
    if uat_slug and _uat_teardown_destroys_data(db, project_id, uat_slug):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Projekt {slug!r} má UAT prostredie {uat_slug!r} s databázou — jeho zmazanie by ju aj so "
                f"všetkými údajmi, ktoré do nej zákazník zadal, nenávratne odstránilo. Namiesto mazania ho "
                f"archivuj (PATCH status='archived')."
            ),
        )

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

    # CI runner teardown — orphan prevention (Director 2026-07-16). A deleted project's containerized
    # self-hosted runner (nex-ci-runner-<slug>) would otherwise linger; remove it. Best-effort, never
    # raises, never undoes the committed delete — mirrors the UAT teardown above.
    from backend.services.create_project_postscaffold import deprovision_ci_runner

    deprovision_ci_runner(slug)

    # KB cleanup — best-effort. A failure here does not undo the DB
    # delete (that has already committed); we log and return 204 so
    # the caller sees the project as gone, and a follow-up orphan
    # scan picks up any stragglers.
    try:
        kb_writer.delete_project(slug)
    except OSError as exc:
        logger.warning("KB cleanup failed for deleted project %r: %s", slug, exc)

    # RAG cleanup — best-effort. The KB rmtree above clears the project's docs
    # on disk but NOT their Qdrant vectors; without this a deleted project would
    # still surface in RAG search. Delete the project's points (tenant 'icc',
    # source_file under projects/<slug>/) keyed on the slug PREFIX — the disk
    # files are already gone, so the path prefix is the only reliable key. Never
    # raises: a Qdrant failure must NOT undo the already-committed DB delete
    # (mirrors the KB / GitHub / UAT cleanups). rag_indexer is None when indexing
    # is disabled (e.g. tests) — skip cleanly in that case.
    if rag_indexer is not None:
        try:
            removed = rag_indexer.delete_project_documents(slug)
            logger.info("RAG cleanup removed %d points for deleted project %r", removed, slug)
        except Exception as exc:  # noqa: BLE001 — best-effort, must never undo the committed delete
            logger.warning("RAG cleanup failed for deleted project %r: %s", slug, exc)

    # Workspace cleanup — best-effort (CR-V2-027). Remove the on-disk project workspace so a deleted
    # project leaves no orphaned tree and the slug can be cleanly re-created (the per-project MEMORY.md
    # lives here and goes with it). Guarded: only an existing dir strictly UNDER PROJECTS_ROOT is removed,
    # never the root itself or a path outside it. A failure does not undo the committed DB delete.
    if source_path:
        from backend.services.claude_agent import PROJECTS_ROOT

        if _workspace_safe_to_remove(source_path, PROJECTS_ROOT):
            try:
                shutil.rmtree(source_path)
            except OSError as exc:
                logger.warning("Workspace cleanup failed for deleted project %r (%s): %s", slug, source_path, exc)
        else:
            logger.warning(
                "Workspace cleanup SKIPPED for deleted project %r — %r not a dir strictly under %s",
                slug,
                source_path,
                PROJECTS_ROOT,
            )

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
