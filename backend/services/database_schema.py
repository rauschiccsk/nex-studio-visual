"""The approved database schema of a project — written by the agent in Návrh, approved by Ri, published to the
Knowledge Base by the cockpit (DEV-7).

``icc/SCHEMA_GOVERNANCE.md`` makes ``projects/<slug>/DATABASE_SCHEMAS.md`` in the Knowledge Base the single source
of truth for a project's database and gives its approval to Ri alone. The cockpit honoured neither: 02.10.2026 the
dedo-home schema was approved in a free-text answer and copied into the Knowledge Base by Dedo from a terminal, and
on 09.10.2026 the NEX Inbox document still described v0.1.0 (14.05.2026) while the app ran 25 migrations.

The flow this module serves:

* the agent writes the WHOLE target schema of the version next to the design document (never a delta — so a
  project whose Knowledge Base lags behind catches up on its next approval);
* :func:`compare` says whether that document differs from the Knowledge Base — the gate and the screen read it;
* only a user with :data:`APPROVER_ROLE` may approve a difference;
* :class:`KnowledgeBaseSchemaPublisher` writes the document and the project's rows and commits exactly those
  files. The agent keeps the Knowledge Base read-only (Director, 23.08.2026) — the cockpit server writes it.
"""

from __future__ import annotations

import difflib
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from backend.config.settings import settings

#: The schema document's name — in the version's spec folder and in the Knowledge Base alike.
SCHEMA_FILENAME = "DATABASE_SCHEMAS.md"

#: ``SCHEMA_GOVERNANCE.md``: „Výlučne Ri … Ha a Shu nemajú oprávnenie schvaľovať zmeny databázovej štruktúry."
APPROVER_ROLE = "ri"

#: Where a generated app keeps its migrations — a project with any of them has a database.
MIGRATION_DIRS = ("backend/alembic/versions",)

GOVERNANCE_REL = "icc/SCHEMA_GOVERNANCE.md"
INDEX_REL = "projects/INDEX.md"

#: The table in ``SCHEMA_GOVERNANCE.md`` that lists every project's schema document.
_GOVERNANCE_HEADING = "## Kde sú schémy"
#: The table in ``projects/INDEX.md`` that lists the projects living in ``/opt/projects``.
_INDEX_HEADING = "## Active"

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

#: How long one git command may take on the Knowledge Base repository.
_GIT_TIMEOUT_SECONDS = 30

#: The identity the cockpit commits under (the same one the backend image configures for project repositories).
_GIT_AUTHOR = ("NEX Studio", "studio@isnex.eu")

#: The longest project description that goes into the project index — one line of a table.
_INDEX_DESCRIPTION_MAX = 200


def kb_root() -> Path:
    """The Knowledge Base the cockpit publishes into. A function, so the test suite can point it elsewhere."""
    return Path(settings.knowledge_base_path)


def kb_schema_rel(slug: str) -> str:
    return f"projects/{slug}/{SCHEMA_FILENAME}"


def _normalized(text: str) -> list[str]:
    return [line.rstrip() for line in text.replace("\r\n", "\n").strip("\n").split("\n")] if text.strip() else []


@dataclass(frozen=True)
class SchemaComparison:
    """The version's schema document against the approved one in the Knowledge Base."""

    #: The version's document, or ``None`` when the agent has not written one.
    version_doc: Optional[str]
    #: The approved document in the Knowledge Base, or ``None`` before the project's first approval.
    kb_doc: Optional[str]
    added_lines: int = 0
    removed_lines: int = 0

    @property
    def changes(self) -> bool:
        """The version asks for a schema Ri has not approved yet."""
        return self.version_doc is not None and _normalized(self.version_doc) != _normalized(self.kb_doc or "")


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def compare(project_dir: Path, version_rel: str, kb: Path, slug: str) -> SchemaComparison:
    """Compare the version's schema document (``project_dir/version_rel``) with the Knowledge Base one."""
    version_doc = _read(Path(project_dir) / version_rel)
    kb_doc = _read(Path(kb) / kb_schema_rel(slug)) if _SLUG_RE.match(slug) else None
    if version_doc is None:
        return SchemaComparison(version_doc=None, kb_doc=kb_doc)
    added = removed = 0
    for line in difflib.ndiff(_normalized(kb_doc or ""), _normalized(version_doc)):
        if line.startswith("+ "):
            added += 1
        elif line.startswith("- "):
            removed += 1
    return SchemaComparison(version_doc=version_doc, kb_doc=kb_doc, added_lines=added, removed_lines=removed)


def project_has_database(project_dir: Path, kb: Path, slug: str) -> bool:
    """Does the project already have a database — an approved schema, or migrations in its code?

    A NEW project has neither while it is being designed; whether it needs one is then the Auditor's question
    (the upfront review checks the design against the schema document)."""
    if _SLUG_RE.match(slug) and (Path(kb) / kb_schema_rel(slug)).is_file():
        return True
    for rel in MIGRATION_DIRS:
        folder = Path(project_dir) / rel
        if folder.is_dir() and any(p.suffix == ".py" and p.name != "__init__.py" for p in folder.iterdir()):
            return True
    return False


def may_approve(role: Optional[str]) -> bool:
    return role == APPROVER_ROLE


# ── publishing into the Knowledge Base ─────────────────────────────────────────────────────────────────────────


class SchemaPublishRefused(Exception):
    """The Knowledge Base was not written — the message says why, in words the Manažér can act on."""


@dataclass(frozen=True)
class PublishResult:
    #: The new Knowledge Base commit, or ``None`` when it already held exactly this schema and both rows.
    commit: Optional[str]
    kb_path: str
    changed: list[str] = field(default_factory=list)


def _table_end(lines: list[str], heading: str) -> Optional[int]:
    """Index just past the last row of the first table under the heading starting with ``heading``."""
    start = next((i for i, line in enumerate(lines) if line.startswith(heading)), None)
    if start is None:
        return None
    end = None
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## "):
            break
        if lines[i].startswith("|"):
            end = i + 1
        elif end is not None:
            break
    return end


def _with_row(text: str, heading: str, row: str, present: str, label: str) -> str:
    """``text`` with ``row`` appended to the table under ``heading`` — unchanged when ``present`` is already in it."""
    if present in text:
        return text
    lines = text.split("\n")
    end = _table_end(lines, heading)
    if end is None:
        raise SchemaPublishRefused(
            f"V Znalostnej báze sa v {label} nenašla tabuľka „{heading.lstrip('# ')}“ — riadok projektu by sa "
            "zapísal naslepo, preto sa nezapísalo nič."
        )
    return "\n".join([*lines[:end], row, *lines[end:]])


def _one_line(text: str, fallback: str) -> str:
    first = next((line.strip() for line in (text or "").splitlines() if line.strip()), "") or fallback
    first = first.replace("|", "/")
    return first if len(first) <= _INDEX_DESCRIPTION_MAX else first[: _INDEX_DESCRIPTION_MAX - 1].rstrip() + "…"


class KnowledgeBaseSchemaPublisher:
    """The narrow door through which the cockpit writes an approved schema into the Knowledge Base.

    Three fixed targets and nothing else: the project's schema document and the project's row in the governance
    table and in the project index. The commit is built on a private index from the committed versions of those
    files, so somebody's uncommitted work — in other files or inside a file that gets a row — is never committed;
    it stays in the working tree as it was. The schema document itself is refused rather than written over an
    edit. Git runs as the owner of the repository, so the root-run backend leaves no file only root can touch.
    """

    def __init__(self, base_path: Path | str) -> None:
        self._root = Path(base_path).resolve()

    def publish(
        self,
        *,
        slug: str,
        project_name: str,
        description: str,
        source_path: str,
        content: str,
        version_number: str,
        approved_by: str,
    ) -> PublishResult:
        if not _SLUG_RE.match(slug or ""):
            raise SchemaPublishRefused(
                f"Meno projektu „{slug}“ nezodpovedá pravidlu — do Znalostnej bázy sa nezapísalo nič."
            )
        if not (self._root / ".git").exists():
            raise SchemaPublishRefused("Znalostná báza nie je git úložisko — schéma sa nedá uložiť s históriou.")
        schema_rel = kb_schema_rel(slug)
        for rel in (schema_rel, GOVERNANCE_REL, INDEX_REL):
            target = (self._root / rel).resolve()
            if not target.is_relative_to(self._root):
                raise SchemaPublishRefused(f"Cesta {rel} vedie mimo Znalostnej bázy — nezapísalo sa nič.")

        staged = set(self._git("diff", "--cached", "--name-only").split())
        busy = [rel for rel in (schema_rel, GOVERNANCE_REL, INDEX_REL) if rel in staged]
        if busy:
            raise SchemaPublishRefused(
                f"V Znalostnej báze sú pripravené na uloženie cudzie zmeny v {', '.join(busy)} — kokpit ich "
                "nepribalí ani neprepíše. Treba ich najprv uložiť alebo vrátiť."
            )

        document = content.replace("\r\n", "\n").strip("\n") + "\n"
        head_doc = self._committed(schema_rel)
        if (_read(self._root / schema_rel) or None) != head_doc:
            raise SchemaPublishRefused(
                f"Dokument {schema_rel} má v Znalostnej báze neuložené zmeny — kokpit ho neprepíše. Treba ich "
                "najprv uložiť alebo vrátiť."
            )

        governance_row = f"| {project_name} | `{schema_rel}` |"
        index_row = f"| **{slug}** | `{source_path}` | {_one_line(description, project_name)} |"
        rows = {
            GOVERNANCE_REL: (_GOVERNANCE_HEADING, governance_row, f"`{schema_rel}`", GOVERNANCE_REL),
            INDEX_REL: (_INDEX_HEADING, index_row, f"| **{slug}** |", INDEX_REL),
        }
        committed: dict[str, str] = {}
        on_disk: dict[str, str] = {}
        if _normalized(head_doc or "") != _normalized(document):
            committed[schema_rel] = document
            on_disk[schema_rel] = document
        for rel, (heading, row, present, label) in rows.items():
            head_text = self._committed(rel)
            if head_text is None:
                raise SchemaPublishRefused(f"V Znalostnej báze chýba {rel} — riadok projektu nie je kam zapísať.")
            new_head = _with_row(head_text, heading, row, present, label)
            if new_head != head_text:
                committed[rel] = new_head
                work_text = _read(self._root / rel)
                on_disk[rel] = _with_row(work_text, heading, row, present, label) if work_text is not None else new_head

        if not committed:
            return PublishResult(commit=None, kb_path=schema_rel)

        commit = self._commit(
            committed,
            f"docs({slug}): schválená štruktúra databázy v{version_number} — schválil {approved_by} v kokpite",
        )
        for rel, text in on_disk.items():
            self._write(rel, text)

        for rel, text in committed.items():
            if self._committed(rel) != text:
                raise SchemaPublishRefused(f"Po zápise sa {rel} v Znalostnej báze nezhoduje so schválenou podobou.")
        if _read(self._root / schema_rel) != (on_disk.get(schema_rel) or head_doc):
            raise SchemaPublishRefused(f"Po zápise sa {schema_rel} na disku nezhoduje so schválenou podobou.")
        return PublishResult(commit=commit, kb_path=schema_rel, changed=sorted(committed))

    # ── git plumbing ──────────────────────────────────────────────────────────────────────────────────────────

    def _owner(self) -> tuple[int, int]:
        st = self._root.stat()
        return st.st_uid, st.st_gid

    def _git(self, *args: str, stdin: Optional[str] = None, env: Optional[dict[str, str]] = None) -> str:
        uid, gid = self._owner()
        as_owner = os.geteuid() == 0 and uid != 0
        run_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": tempfile.gettempdir(),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": _GIT_AUTHOR[0],
            "GIT_AUTHOR_EMAIL": _GIT_AUTHOR[1],
            "GIT_COMMITTER_NAME": _GIT_AUTHOR[0],
            "GIT_COMMITTER_EMAIL": _GIT_AUTHOR[1],
            **(env or {}),
        }
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self._root,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT_SECONDS,
                env=run_env,
                user=uid if as_owner else None,
                group=gid if as_owner else None,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SchemaPublishRefused(f"Git v Znalostnej báze neodpovedal ({exc.__class__.__name__}).") from exc
        if result.returncode != 0:
            raise SchemaPublishRefused(f"Git v Znalostnej báze odmietol príkaz: {result.stderr.strip()[:300]}")
        return result.stdout

    def _committed(self, rel: str) -> Optional[str]:
        listed = self._git("ls-tree", "--name-only", "HEAD", "--", rel).strip()
        return self._git("show", f"HEAD:{rel}") if listed == rel else None

    def _commit(self, files: dict[str, str], message: str) -> str:
        """Commit ``files`` on top of HEAD through a private index; the shared index gets only these paths."""
        branch = self._git("symbolic-ref", "-q", "HEAD").strip()
        old = self._git("rev-parse", "HEAD").strip()
        blobs = {rel: self._git("hash-object", "-w", "--stdin", stdin=text).strip() for rel, text in files.items()}
        with tempfile.TemporaryDirectory() as tmp:
            private = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
            if os.geteuid() == 0:
                uid, gid = self._owner()
                os.chown(tmp, uid, gid)
            self._git("read-tree", "HEAD", env=private)
            for rel, blob in blobs.items():
                self._git("update-index", "--add", "--cacheinfo", f"100644,{blob},{rel}", env=private)
            tree = self._git("write-tree", env=private).strip()
        new = self._git("commit-tree", tree, "-p", old, "-m", message).strip()
        # Compare-and-swap: a commit somebody made meanwhile is never thrown away.
        try:
            self._git("update-ref", "-m", message, branch, new, old)
        except SchemaPublishRefused as exc:
            raise SchemaPublishRefused("Znalostná báza sa práve menila — schválenie skús ešte raz.") from exc
        for rel, blob in blobs.items():
            self._git("update-index", "--add", "--cacheinfo", f"100644,{blob},{rel}")
        return new

    def _write(self, rel: str, text: str) -> None:
        target = self._root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
        if os.geteuid() == 0:
            uid, gid = self._owner()
            os.chown(target, uid, gid)
            os.chown(target.parent, uid, gid)
