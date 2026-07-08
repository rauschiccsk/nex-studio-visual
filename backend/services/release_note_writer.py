"""Deterministic per-version ``RELEASE_NOTES.md`` writer — the AUTHORING half of
the *Aktualizácie* changelog (Part 1 of ``docs/specs/per-app-changelog-standard.md``).

NEX Studio — **not** the AI agent — owns the plain-language note. At build
completion it (re)generates the note for the completing version from the
control-plane DB and commits it into the generated app's repo, so the app's own
:mod:`backend.services.release_notes` endpoint (``GET /api/v1/release-notes``,
the SERVING half) reads the image-baked file. This removes the agent-dependence
that dropped the note in the flagship v3 app (nex-payables).

Altitude + honesty (design §1):

* **Epics are the user-facing feature level.** The note bullets come from each
  Epic's ``plain_description`` (already jargon-free manager-facing prose), NEVER
  from Tasks (too granular) — falling back to the Epic ``title`` when the plain
  description is absent.
* **No internal codes.** ``CR-…``/``EPIC-…``/``BUG-…``/``FEAT-…``/``TASK-…`` and
  file names never surface — :func:`_strip_codes` defensively scrubs them from
  the (rare) title-fallback path; ``plain_description`` is clean by construction.
* **Resolved bugs** may append a short *Opravené* line — titles only, never the
  severity/status jargon.

Immutability (design §3): a version already ``released`` is a historical record —
:func:`write_release_note` refuses to regenerate it. NEX Studio is the source of
truth for every NOT-yet-released version and overwrites any stale placeholder.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models.bugs import Bug
from backend.db.models.tasks import Epic
from backend.db.models.versions import Version

#: Internal work-item codes that must NEVER surface in a user-facing note. Matches
#: e.g. ``CR-NS-001``, ``EPIC-3``, ``BUG-12``, ``FEAT-4``, ``TASK-7``. Applied only
#: as defence on the title-fallback path — ``plain_description`` is jargon-free.
_CODE_RE = re.compile(r"\b(?:CR|EPIC|FEAT|TASK|BUG)-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\b")


def _strip_codes(text: str) -> str:
    """Remove internal work-item codes + tidy the residual whitespace/punctuation."""
    cleaned = _CODE_RE.sub("", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip(" -–—:\t")


def _bare(version_number: str) -> str:
    """Version number without a leading ``v`` (``"v1.0.0"`` and ``"1.0.0"`` → ``"1.0.0"``)."""
    return version_number[1:] if version_number.startswith("v") else version_number


def version_notes_dir(proj_root: Path, version_number: str) -> Path:
    """The per-version notes directory ``<root>/docs/specs/versions/v<N>``.

    Mirrors the layout :mod:`backend.services.release_notes` globs and
    ``orchestrator._version_spec_rel`` writes the rest of the version spec into.
    Normalises the ``v`` prefix so a graduated number that already carries it
    (``FIRST_PROD_VERSION == "v1.0.0"``) does not yield a ``vv1.0.0`` dir.
    """
    return Path(proj_root) / "docs" / "specs" / "versions" / f"v{_bare(version_number)}"


def _epic_bullets(db: Session, version: Version) -> list[str]:
    """One plain-language bullet per Epic (ordered by number).

    ``plain_description`` is the user-facing prose; fall back to the ``title``
    (code-stripped) when it is null/blank. Epics that carry neither are skipped.
    """
    epics = db.execute(select(Epic).where(Epic.version_id == version.id).order_by(Epic.number)).scalars().all()
    bullets: list[str] = []
    for epic in epics:
        text = (epic.plain_description or "").strip()
        if not text:
            text = _strip_codes((epic.title or "").strip())
        if text:
            bullets.append(text)
    return bullets


def _fixed_bullets(db: Session, version: Version) -> list[str]:
    """Plain-language *Opravené* bullets — titles of RESOLVED bugs (no codes/severity)."""
    bugs = (
        db.execute(select(Bug).where(Bug.version_id == version.id, Bug.status == "resolved").order_by(Bug.bug_number))
        .scalars()
        .all()
    )
    out: list[str] = []
    for bug in bugs:
        title = _strip_codes((bug.title or "").strip())
        if title:
            out.append(title)
    return out


def render_release_note(db: Session, version: Version) -> str:
    """Render the plain-language Slovak note markdown for *version*.

    Mirrors the scaffold template: an ``## v<N> — <name>`` H2 heading, plain
    bullets from the Epics, and an optional ``### Opravené`` block from resolved
    bugs. No internal codes, no Task dump, no date in the heading (the endpoint
    sources the date from the DB).
    """
    heading = f"## v{_bare(version.version_number)}"
    name = (version.name or "").strip()
    if name:
        heading += f" — {name}"

    lines = [heading, ""]

    bullets = _epic_bullets(db, version)
    if bullets:
        lines.extend(f"- {b}" for b in bullets)
    else:
        # Never emit an empty note — a version with no Epics still ships one honest line.
        lines.append("- Nová verzia.")

    fixed = _fixed_bullets(db, version)
    if fixed:
        lines.append("")
        lines.append("### Opravené")
        lines.extend(f"- {b}" for b in fixed)

    return "\n".join(lines) + "\n"


def write_release_note(db: Session, version_id, proj_root: Path) -> Path | None:
    """(Re)generate + write the note for *version_id* under *proj_root*.

    Returns the written path, or ``None`` when nothing was written — either the
    version does not exist, or it is already ``released`` (immutable historical
    record: NEVER regenerated). Creates the version dir (``mkdir -p``) and
    overwrites any stale placeholder for a not-yet-released version.
    """
    version = db.get(Version, version_id)
    if version is None:
        return None
    if version.status == "released":
        return None

    notes_dir = version_notes_dir(proj_root, version.version_number)
    notes_dir.mkdir(parents=True, exist_ok=True)
    path = notes_dir / "RELEASE_NOTES.md"
    path.write_text(render_release_note(db, version), encoding="utf-8")
    return path
