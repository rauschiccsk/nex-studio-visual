"""Spec tests for the deterministic per-version ``RELEASE_NOTES.md`` auto-writer.

Part 1 of ``docs/specs/per-app-changelog-standard.md``: NEX Studio (NOT the AI
agent) generates the plain-language Slovak release note for the completing
version from its **Epics** (the user-facing feature altitude — never a Task
dump), optionally appends a short *Opravené* line from resolved Bugs, and commits
it into the generated app's repo so the app's own ``/api/v1/release-notes``
endpoint serves it.

Covered contracts:
  * generation from Epic ``plain_description`` (fallback to Epic ``title``);
  * correct on-disk path ``docs/specs/versions/v<N>/RELEASE_NOTES.md``, mkdir -p;
  * H2 version heading, plain bullets, NO internal codes (CR-/EPIC-/BUG-/files);
  * a resolved-Bug *Opravené* line (title only — no severity/codes);
  * a ``released`` version's note is IMMUTABLE (never regenerated);
  * ``version_notes_dir`` normalises the ``v`` prefix (no ``vv1.0.0`` footgun);
  * ``orchestrator._commit_release_note`` commits the note into the app repo;
  * ``deploy._graduate_version_in_place`` MOVES the note dir + commits on rename.
"""

from __future__ import annotations

import subprocess
import uuid

from backend.db.models.bugs import Bug
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic
from backend.db.models.versions import Version
from backend.services import release_note_writer


# ---------------------------------------------------------------------------
# Fixtures / seed helpers
# ---------------------------------------------------------------------------
def _seed(db, *, version_number="0.1.0", status="active", name=None):
    creator = User(
        username=f"rnw_{uuid.uuid4().hex[:8]}",
        email=f"rnw_{uuid.uuid4().hex[:8]}@test.local",
        password_hash="x",
        role="ri",
    )
    db.add(creator)
    db.flush()
    project = Project(
        name=f"RNW {uuid.uuid4().hex[:6]}",
        slug=f"rnw-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="release-note-writer fixture",
        created_by=creator.id,
    )
    db.add(project)
    db.flush()
    version = Version(
        project_id=project.id,
        version_number=version_number,
        name=name,
        status=status,
    )
    db.add(version)
    db.flush()
    return creator, project, version


def _add_epic(db, project, version, number, title, plain_description):
    epic = Epic(
        project_id=project.id,
        version_id=version.id,
        number=number,
        title=title,
        plain_description=plain_description,
        status="done",
    )
    db.add(epic)
    db.flush()
    return epic


def _add_bug(db, project, version, creator, bug_number, title, *, status="resolved", severity="minor"):
    bug = Bug(
        project_id=project.id,
        version_id=version.id,
        bug_number=bug_number,
        title=title,
        description="detail",
        severity=severity,
        status=status,
        created_by=creator.id,
    )
    db.add(bug)
    db.flush()
    return bug


def _git_init(root):
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@test.local"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    (root / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "init"], check=True)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------
def test_write_release_note_from_epics(db_session, tmp_path):
    _creator, project, version = _seed(db_session, version_number="0.2.0", name="Export do Excelu")
    _add_epic(db_session, project, version, 1, "Order export endpoint", "Faktúry teraz vieš stiahnuť do Excelu.")
    _add_epic(db_session, project, version, 2, "Bulk import", "Doklady sa dajú nahrať hromadne.")

    path = release_note_writer.write_release_note(db_session, version.id, tmp_path)

    assert path == tmp_path / "docs" / "specs" / "versions" / "v0.2.0" / "RELEASE_NOTES.md"
    body = path.read_text(encoding="utf-8")
    # H2 version heading carrying the version name.
    assert body.startswith("## v0.2.0")
    assert "Export do Excelu" in body.splitlines()[0]
    # Epic-level bullets from plain_description (user-facing altitude, not Tasks).
    assert "- Faktúry teraz vieš stiahnuť do Excelu." in body
    assert "- Doklady sa dajú nahrať hromadne." in body
    # NO internal codes / filenames.
    for token in ("CR-", "EPIC-", "BUG-", "FEAT-", "TASK-", ".py", ".tsx"):
        assert token not in body


def test_fix_loop_epics_excluded_from_notes(db_session, tmp_path):
    """Drift fix: the internal re-verify fix-loop Epics ("Oprava po Verifikácii", one per Verifikácia-FAIL
    round) must NEVER reach the changelog — they are churn, and including them drifted RELEASE_NOTES by one
    duplicate bullet per round, blocking every Verifikácia. The version's REAL features stay; the fix Epics
    are dropped no matter how many rounds ran."""
    _creator, project, version = _seed(db_session, version_number="1.1.0", name="Platby SLSP")
    _add_epic(db_session, project, version, 8, "Bankový adaptér SLSP", "Napojenie na banku SLSP.")
    _fix = release_note_writer.VERIFIKACIA_FIX_EPIC_TITLE
    _add_epic(db_session, project, version, 14, _fix, _fix)
    _add_epic(db_session, project, version, 15, _fix, _fix)

    body = release_note_writer.write_release_note(db_session, version.id, tmp_path).read_text(encoding="utf-8")

    assert "- Napojenie na banku SLSP." in body  # the real feature stays
    assert "Oprava po Verifikácii" not in body  # ZERO fix-loop bullets → no drift, no matter how many rounds


def test_fix_epic_marker_in_sync_with_orchestrator():
    """The exclusion is keyed on the exact fix-Epic title, defined in BOTH orchestrator (where the Epics are
    created) and release_note_writer (where they are filtered). Pin the two equal so a rename can never
    silently re-open the drift."""
    from backend.services import orchestrator

    assert release_note_writer.VERIFIKACIA_FIX_EPIC_TITLE == orchestrator._VERIFIKACIA_FIX_TITLE


def test_epic_title_fallback_when_plain_description_null(db_session, tmp_path):
    _creator, project, version = _seed(db_session, version_number="0.1.0")
    _add_epic(db_session, project, version, 1, "Prihlásenie používateľa", None)

    body = release_note_writer.write_release_note(db_session, version.id, tmp_path).read_text(encoding="utf-8")

    assert "- Prihlásenie používateľa" in body


def test_internal_codes_stripped_from_bullets(db_session, tmp_path):
    _creator, project, version = _seed(db_session, version_number="0.1.0")
    # A title leaking a code prefix (the fallback path) must never surface the code.
    _add_epic(db_session, project, version, 1, "EPIC-3 Reporting modul", None)

    body = release_note_writer.write_release_note(db_session, version.id, tmp_path).read_text(encoding="utf-8")

    assert "EPIC-3" not in body
    assert "Reporting modul" in body


def test_resolved_bugs_appended_as_opravene(db_session, tmp_path):
    creator, project, version = _seed(db_session, version_number="0.3.0")
    _add_epic(db_session, project, version, 1, "Nová funkcia", "Pridali sme prehľad objednávok.")
    _add_bug(db_session, project, version, creator, 1, "Chybný súčet DPH", status="resolved")
    # A non-resolved bug must NOT appear.
    _add_bug(db_session, project, version, creator, 2, "Nezobrazený dátum", status="new")

    body = release_note_writer.write_release_note(db_session, version.id, tmp_path).read_text(encoding="utf-8")

    assert "### Opravené" in body
    assert "- Chybný súčet DPH" in body
    assert "Nezobrazený dátum" not in body
    # Severity jargon never leaks.
    assert "minor" not in body


def test_no_opravene_section_when_no_resolved_bugs(db_session, tmp_path):
    _creator, project, version = _seed(db_session, version_number="0.1.0")
    _add_epic(db_session, project, version, 1, "Základ", "Prvá verzia.")

    body = release_note_writer.write_release_note(db_session, version.id, tmp_path).read_text(encoding="utf-8")

    assert "Opravené" not in body


# ---------------------------------------------------------------------------
# Path helper / immutability
# ---------------------------------------------------------------------------
def test_version_notes_dir_normalises_v_prefix(tmp_path):
    assert release_note_writer.version_notes_dir(tmp_path, "0.1.0").name == "v0.1.0"
    # A graduated number that already carries the leading "v" must not double it.
    assert release_note_writer.version_notes_dir(tmp_path, "v1.0.0").name == "v1.0.0"


def test_released_version_note_not_regenerated(db_session, tmp_path):
    _creator, project, version = _seed(db_session, version_number="0.1.0", status="released")
    _add_epic(db_session, project, version, 1, "X", "Toto sa nesmie prepísať.")
    notes_dir = tmp_path / "docs" / "specs" / "versions" / "v0.1.0"
    notes_dir.mkdir(parents=True)
    original = "## v0.1.0\n\n- Pôvodný historický záznam.\n"
    (notes_dir / "RELEASE_NOTES.md").write_text(original, encoding="utf-8")

    result = release_note_writer.write_release_note(db_session, version.id, tmp_path)

    assert result is None
    assert (notes_dir / "RELEASE_NOTES.md").read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# Commit into the app repo (orchestrator seam)
# ---------------------------------------------------------------------------
def test_commit_release_note_commits_into_repo(db_session, tmp_path):
    from backend.services import orchestrator

    _git_init(tmp_path)
    _creator, project, version = _seed(db_session, version_number="0.1.0")
    _add_epic(db_session, project, version, 1, "Základ", "Prvá verzia aplikácie.")

    orchestrator._commit_release_note(db_session, version.id, tmp_path, "0.1.0")

    rel = "docs/specs/versions/v0.1.0/RELEASE_NOTES.md"
    assert (tmp_path / rel).is_file()
    tracked = subprocess.run(["git", "-C", str(tmp_path), "ls-files", rel], capture_output=True, text=True).stdout
    assert rel in tracked
    # Committed → nothing pending for that path.
    pending = subprocess.run(
        ["git", "-C", str(tmp_path), "status", "--porcelain", rel], capture_output=True, text=True
    ).stdout
    assert pending.strip() == ""


def test_commit_release_note_released_is_noop(db_session, tmp_path):
    from backend.services import orchestrator

    _git_init(tmp_path)
    _creator, project, version = _seed(db_session, version_number="0.1.0", status="released")
    _add_epic(db_session, project, version, 1, "X", "Nesmie sa zapísať.")

    orchestrator._commit_release_note(db_session, version.id, tmp_path, "0.1.0")

    # A released version → no note written, no commit created.
    assert not (tmp_path / "docs" / "specs" / "versions" / "v0.1.0" / "RELEASE_NOTES.md").exists()


# ---------------------------------------------------------------------------
# Graduation dir-move (deploy seam)
# ---------------------------------------------------------------------------
def test_graduation_moves_release_note_dir(db_session, tmp_path):
    from backend.services import deploy

    _git_init(tmp_path)
    _creator, _project, version = _seed(db_session, version_number="0.1.0", status="active")
    old_dir = tmp_path / "docs" / "specs" / "versions" / "v0.1.0"
    old_dir.mkdir(parents=True)
    (old_dir / "RELEASE_NOTES.md").write_text("## v0.1.0\n\n- Prvá verzia.\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "note"], check=True)

    deploy._graduate_version_in_place(db_session, version, "v1.0.0", tmp_path)

    # Version renamed + released.
    assert version.version_number == "v1.0.0"
    assert version.status == "released"
    # Note moved: old dir file gone, new dir file present with the same content.
    assert not (old_dir / "RELEASE_NOTES.md").exists()
    new_file = tmp_path / "docs" / "specs" / "versions" / "v1.0.0" / "RELEASE_NOTES.md"
    assert new_file.is_file()
    assert "Prvá verzia" in new_file.read_text(encoding="utf-8")
    # Committed: new tracked, old untracked, working tree clean.
    tracked = subprocess.run(["git", "-C", str(tmp_path), "ls-files"], capture_output=True, text=True).stdout
    assert "docs/specs/versions/v1.0.0/RELEASE_NOTES.md" in tracked
    assert "docs/specs/versions/v0.1.0/RELEASE_NOTES.md" not in tracked
    porcelain = subprocess.run(
        ["git", "-C", str(tmp_path), "status", "--porcelain"], capture_output=True, text=True
    ).stdout
    assert porcelain.strip() == ""


def test_graduation_idempotent_when_number_unchanged(db_session, tmp_path):
    from backend.services import deploy

    _git_init(tmp_path)
    _creator, _project, version = _seed(db_session, version_number="v1.0.0", status="active")

    # Already the target number → no rename, no move, no crash.
    deploy._graduate_version_in_place(db_session, version, "v1.0.0", tmp_path)

    assert version.version_number == "v1.0.0"
    assert version.status == "released"
