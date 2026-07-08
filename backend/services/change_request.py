"""Change-request capture — the read-only Konzultácia → NEW version bridge (konzultacia-mode.md Part 2).

When a read-only consult on a FINISHED version surfaces a change the Manažér wants (a ``change_request``
marker on the consult answer message), the cockpit offers "Založiť novú verziu z tejto požiadavky". Its click
lands here: capture the request as a project backlog ``REQ-N`` AND mint the NEXT version in DRAFT
(``planned``, NO ``PipelineState``, NO build running), linking the REQ to it so the new version's Špecifikácia
starts from the request. It NEVER auto-starts a build — the Manažér opens the new version and engages
deliberately (Part 2.3).

Idempotent per SOURCE consult message (konzultacia-followup.md Fix 3): capture is keyed on the message that
carried the marker. The FIRST capture mints the version AND stamps the source marker with
``captured_version_id`` (+ number + backlog number); a SECOND capture of that already-captured marker returns
the EXISTING minted version — no duplicate draft versions (1.1.0, 1.2.0, …) from a double-click / revisit.
The source message must be a read-only consult turn (``stage == 'done'``) — a mid-build marker is rejected so
it can never mint a version (konzultacia-followup.md "gate the marker to terminal state").

Synchronous; ``flush()`` only — commit is the router's job (mirrors the version/backlog services).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from backend.db.models.pipeline import PipelineMessage
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.schemas.backlog import BacklogItemCreate
from backend.schemas.version import VersionCreate
from backend.services import backlog as backlog_service
from backend.services import version as version_service

#: DB column caps (backlog_items.title / versions.name) — clamp the request-derived strings to fit.
_REQ_TITLE_MAX = 500
_VERSION_NAME_MAX = 255


@dataclass(frozen=True)
class CaptureResult:
    """Outcome of a change-request capture (konzultacia-followup.md Fix 3/4).

    Carries the plain values the router echoes back (the FE navigates using ``project_slug`` +
    ``version_id`` — Fix 4). ``created`` is ``False`` on an idempotent replay: the source marker was already
    captured, so this returns the EXISTING minted version (no new mint)."""

    version_id: UUID
    version_number: str
    project_slug: str
    backlog_number: int
    created: bool


def _clamp(text: str, limit: int) -> str:
    """Trim + hard-cap ``text`` to ``limit`` chars (…-suffixed) so it fits a column without an IntegrityError."""
    text = " ".join(text.split()).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def capture(db: Session, *, source_message_id: UUID, user_id: UUID) -> CaptureResult:
    """Record a backlog ``REQ-N`` + mint the next DRAFT version for the change request on ``source_message_id``.

    ``source_message_id`` is the read-only consult answer that carried the ``change_request`` marker; its
    version's project owns both the new backlog item and the new version. The summary/title are read from that
    marker (authoritative), not from the client. Returns a :class:`CaptureResult`; the new version is
    ``planned`` with NO pipeline (the build begins only when the Manažér opens it and engages — Part 2.3).

    Raises ``ValueError``:
      * (→ 404) when the source message does not exist or its version is gone;
      * (→ 422) when the message is not a terminal consult turn (``stage != 'done'``), carries no
        ``change_request`` marker, or the marker's ``summary`` is blank.

    Idempotent (Fix 3): if the source marker already carries a ``captured_version_id`` for a still-existing
    version, that EXISTING version is returned (``created=False``) — a repeat click never mints a duplicate."""
    message = db.get(PipelineMessage, source_message_id)
    if message is None:
        raise ValueError(f"Consult message {source_message_id} not found")
    # Gate to terminal state (konzultacia-followup.md hardening): only a read-only consult turn's marker
    # (recorded at stage='done') is honored, so a mid-build marker can NEVER mint a version.
    if message.stage != "done":
        raise ValueError("change request source is not a finished consult (stage must be 'done')")
    payload = message.payload if isinstance(message.payload, dict) else {}
    marker = payload.get("change_request")
    if not isinstance(marker, dict):
        raise ValueError("consult message carries no change_request marker")
    summary = str(marker.get("summary") or "").strip()
    if not summary:
        raise ValueError("change request requires a non-empty summary")

    consulted = db.get(Version, message.version_id)
    if consulted is None:
        raise ValueError(f"Version {message.version_id} not found")
    project = db.get(Project, consulted.project_id)
    if project is None:
        raise ValueError(f"Project {consulted.project_id} not found")

    # Idempotency (Fix 3): a second capture of an already-captured marker returns the EXISTING minted version.
    captured_id = marker.get("captured_version_id")
    if captured_id:
        existing = db.get(Version, UUID(str(captured_id)))
        if existing is not None:
            return CaptureResult(
                version_id=existing.id,
                version_number=existing.version_number,
                project_slug=project.slug,
                backlog_number=int(marker.get("captured_backlog_number") or 0),
                created=False,
            )
        # The captured version was deleted → fall through and re-mint (self-healing; a stamp with no version).

    req_title = _clamp(str(marker.get("title")).strip() if marker.get("title") else summary, _REQ_TITLE_MAX)

    # (a) Record the request as a project backlog REQ-N (status='open').
    backlog_item = backlog_service.create(
        db,
        BacklogItemCreate(project_id=consulted.project_id, title=req_title, description=summary),
    )

    # (b) Mint the NEXT version in DRAFT — planned, NO PipelineState, NO build. version_service.create leaves
    # status at the DB server_default 'planned'; we never call apply_action('start') here (Part 2.3).
    next_number = version_service.suggest_next_version_number(db, consulted.project_id)
    new_version = version_service.create(
        db,
        consulted.project_id,
        VersionCreate(
            version_number=next_number,
            name=_clamp(req_title, _VERSION_NAME_MAX),
            description=summary,
        ),
        user_id,
    )

    # (c) Link the REQ to the new version (status='included') so the new version's Špecifikácia starts from it,
    # and seed its Zadanie (customer-requirements.md) so the Príprava phase reads the request when it begins.
    backlog_service.assign_to_version(db, backlog_item.id, new_version.id)
    version_service.write_zadanie(db, new_version.id, summary)

    # (d) Stamp the SOURCE marker (Fix 3): a repeat capture short-circuits to the existing version above, and
    # the FE bar hides once the latest message's marker carries a captured_version_id. Reassign the payload to
    # a NEW dict so SQLAlchemy detects the JSONB mutation (in-place dict edits aren't tracked).
    message.payload = {
        **payload,
        "change_request": {
            **marker,
            "captured_version_id": str(new_version.id),
            "captured_version_number": new_version.version_number,
            "captured_backlog_number": backlog_item.number,
        },
    }
    db.flush()

    return CaptureResult(
        version_id=new_version.id,
        version_number=new_version.version_number,
        project_slug=project.slug,
        backlog_number=backlog_item.number,
        created=True,
    )
