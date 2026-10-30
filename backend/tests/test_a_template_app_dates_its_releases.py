"""DEV-258 — an app on the template shows in Aktualizácie the day each version came out.

nex-app-kit reads the days from ``docs/specs/versions/release-dates.json``; nothing wrote it, so every version of an app
on the template stood there without a date. The cockpit writes the day a version is signed off (Hotovo), beside its
release note and in the same commit; a version that has its day keeps it, a project without the template is untouched.
"""

from __future__ import annotations

import json
import subprocess
from datetime import date

from backend.services import orchestrator, release_note_writer
from backend.tests.test_release_note_writer import _add_epic, _git_init, _seed

DATES = "docs/specs/versions/release-dates.json"


def _dates(root):
    return json.loads((root / DATES).read_text(encoding="utf-8"))


def test_the_day_of_a_version_is_written_beside_the_others(tmp_path):
    (tmp_path / "docs" / "specs" / "versions").mkdir(parents=True)
    (tmp_path / DATES).write_text('{"v0.1.0": "2026-10-28"}', encoding="utf-8")

    written = release_note_writer.record_release_date(tmp_path, "0.2.0", date(2026, 10, 30))

    assert written == tmp_path / DATES
    assert _dates(tmp_path) == {"v0.1.0": "2026-10-28", "v0.2.0": "2026-10-30"}


def test_the_first_day_makes_the_file_and_a_v_in_the_number_does_not_double(tmp_path):
    release_note_writer.record_release_date(tmp_path, "v1.0.0", date(2026, 11, 2))

    assert _dates(tmp_path) == {"v1.0.0": "2026-11-02"}


def test_a_version_keeps_its_day_and_an_unreadable_file_is_left_alone(tmp_path):
    release_note_writer.record_release_date(tmp_path, "0.2.0", date(2026, 10, 30))

    assert release_note_writer.record_release_date(tmp_path, "0.2.0", date(2026, 11, 5)) is None
    assert _dates(tmp_path) == {"v0.2.0": "2026-10-30"}

    (tmp_path / DATES).write_text("{ nie json", encoding="utf-8")
    assert release_note_writer.record_release_date(tmp_path, "0.3.0", date(2026, 11, 5)) is None
    assert (tmp_path / DATES).read_text(encoding="utf-8") == "{ nie json"


async def test_a_template_project_s_sign_off_commits_the_day_with_the_note(db_session, tmp_path):
    _git_init(tmp_path)
    _creator, project, version = _seed(db_session, version_number="0.2.0")
    project.template = "nsv-standard"
    db_session.flush()
    _add_epic(db_session, project, version, 1, "Šablóna", "Aplikácia má jednotný rám.")

    await orchestrator._commit_release_note(db_session, version.id, tmp_path, "0.2.0")

    assert list(_dates(tmp_path)) == ["v0.2.0"]
    tracked = subprocess.run(["git", "-C", str(tmp_path), "ls-files", DATES], capture_output=True, text=True).stdout
    pending = subprocess.run(
        ["git", "-C", str(tmp_path), "status", "--porcelain", DATES], capture_output=True, text=True
    ).stdout
    assert DATES in tracked and pending.strip() == ""


async def test_a_project_without_the_template_gets_no_dates_file(db_session, tmp_path):
    _git_init(tmp_path)
    _creator, project, version = _seed(db_session, version_number="0.2.0")
    _add_epic(db_session, project, version, 1, "Bez šablóny", "Vlastný rám.")

    await orchestrator._commit_release_note(db_session, version.id, tmp_path, "0.2.0")

    assert (tmp_path / "docs" / "specs" / "versions" / "v0.2.0" / "RELEASE_NOTES.md").is_file()
    assert not (tmp_path / DATES).exists()
