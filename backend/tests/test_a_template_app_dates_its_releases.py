"""DEV-258 — an app on the template shows in Aktualizácie the day each version came out.

nex-app-kit reads the days from ``docs/specs/versions/release-dates.json``; nothing wrote it, so every version of an app
on the template stood there without a date. The cockpit writes the day a version was first declared finished — the
first Verifikácia PASS or Hotovo signature on record, today when there is none yet — beside its release note, in the
same commit. A project without the template is untouched.

30.10.2026 the specification of NEX Knowledge 0.2.0 had the agent write the file itself, with the day of the version it
builds, and the agent writes its own release note. The first cockpit kept a day already in the file and wrote nothing
when the note was not its own — NEX Knowledge would have shown the day of programming, or no day at all. The second
wrote only the version it completed: the agent's guess for 0.1.0 (29.10.) stayed, though the cockpit's record says
30.10. So the cockpit writes the day of EVERY version of the project it has a record for; the agent does not write the
file.
"""

from __future__ import annotations

import json
import subprocess
from datetime import date, datetime, timezone

from backend.db.models.pipeline import PipelineMessage
from backend.db.models.versions import Version
from backend.services import orchestrator, release_note_writer
from backend.tests.test_release_note_writer import _add_epic, _git_init, _seed

DATES = "docs/specs/versions/release-dates.json"
NOTE = "docs/specs/versions/v0.2.0/RELEASE_NOTES.md"


def _dates(root):
    return json.loads((root / DATES).read_text(encoding="utf-8"))


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout


def _commit_all(root, message):
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", message)


def _today():
    return orchestrator.local_time(datetime.now(timezone.utc)).date().isoformat()


def _template_version(db, root):
    _git_init(root)
    _creator, project, version = _seed(db, version_number="0.2.0")
    project.template = "nsv-standard"
    db.flush()
    _add_epic(db, project, version, 1, "Šablóna", "Aplikácia má jednotný rám.")
    return version


def _record(db, version, *, stage, kind, payload, at):
    db.add(
        PipelineMessage(
            version_id=version.id,
            stage=stage,
            author="manazer",
            recipient="ai_agent",
            kind=kind,
            content="záznam",
            payload=payload,
            created_at=at,
        )
    )
    db.flush()


def _committed_and_clean(root, path):
    return path in _git(root, "ls-files", path) and _git(root, "status", "--porcelain", path).strip() == ""


def test_the_day_of_a_version_is_written_beside_the_others(tmp_path):
    (tmp_path / "docs" / "specs" / "versions").mkdir(parents=True)
    (tmp_path / DATES).write_text('{"v0.1.0": "2026-10-28"}', encoding="utf-8")

    written = release_note_writer.record_release_dates(tmp_path, {"0.2.0": date(2026, 10, 30)})

    assert written == tmp_path / DATES
    assert _dates(tmp_path) == {"v0.1.0": "2026-10-28", "v0.2.0": "2026-10-30"}


def test_the_first_day_makes_the_file_and_a_v_in_the_number_does_not_double(tmp_path):
    release_note_writer.record_release_dates(tmp_path, {"v1.0.0": date(2026, 11, 2), "0.9.0": date(2026, 10, 1)})

    assert _dates(tmp_path) == {"v0.9.0": "2026-10-01", "v1.0.0": "2026-11-02"}


def test_the_days_given_replace_others_and_an_unreadable_file_is_left_alone(tmp_path):
    release_note_writer.record_release_dates(tmp_path, {"0.1.0": date(2026, 10, 29), "0.2.0": date(2026, 10, 20)})

    days = {"0.1.0": date(2026, 10, 30), "0.2.0": date(2026, 10, 30)}
    assert release_note_writer.record_release_dates(tmp_path, days) == tmp_path / DATES
    assert _dates(tmp_path) == {"v0.1.0": "2026-10-30", "v0.2.0": "2026-10-30"}
    assert release_note_writer.record_release_dates(tmp_path, days) is None  # nothing to change

    (tmp_path / DATES).write_text("{ nie json", encoding="utf-8")
    assert release_note_writer.record_release_dates(tmp_path, {"0.3.0": date(2026, 11, 5)}) is None
    assert (tmp_path / DATES).read_text(encoding="utf-8") == "{ nie json"


async def test_a_template_project_s_sign_off_commits_the_day_with_the_note(db_session, tmp_path):
    version = _template_version(db_session, tmp_path)

    await orchestrator._commit_release_note(db_session, version.id, tmp_path, "0.2.0")

    assert _dates(tmp_path) == {"v0.2.0": _today()}
    assert _committed_and_clean(tmp_path, DATES) and _committed_and_clean(tmp_path, NOTE)


async def test_a_day_the_agent_wrote_ahead_gives_way_to_the_day_of_the_sign_off(db_session, tmp_path):
    version = _template_version(db_session, tmp_path)
    (tmp_path / "docs" / "specs" / "versions").mkdir(parents=True)
    (tmp_path / DATES).write_text('{"v0.1.0": "2026-10-30", "v0.2.0": "2026-10-20"}', encoding="utf-8")
    _commit_all(tmp_path, "docs: the days of the versions")

    await orchestrator._commit_release_note(db_session, version.id, tmp_path, "0.2.0")

    assert _dates(tmp_path) == {"v0.1.0": "2026-10-30", "v0.2.0": _today()}
    assert _committed_and_clean(tmp_path, DATES)


async def test_a_note_the_agent_wrote_itself_still_gets_its_day(db_session, tmp_path):
    version = _template_version(db_session, tmp_path)
    (tmp_path / NOTE).parent.mkdir(parents=True)
    own_note = "# NEX Knowledge 0.2.0\n\nNový rám aplikácie, Nastavenia a Relácie.\n"
    (tmp_path / NOTE).write_text(own_note, encoding="utf-8")
    _commit_all(tmp_path, "docs(release): the notes of 0.2.0")

    await orchestrator._commit_release_note(db_session, version.id, tmp_path, "0.2.0")

    assert (tmp_path / NOTE).read_text(encoding="utf-8") == own_note
    assert _dates(tmp_path) == {"v0.2.0": _today()}
    assert _committed_and_clean(tmp_path, DATES)
    assert _git(tmp_path, "log", "-1", "--format=%s") == "docs(release-notes): v0.2.0 — the day of the release\n"


async def test_a_later_re_verification_keeps_the_day_the_version_was_first_finished(db_session, tmp_path):
    version = _template_version(db_session, tmp_path)
    _record(
        db_session,
        version,
        stage="verifikacia",
        kind="verdict",
        payload={"verdict": "FAIL"},
        at=datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc),
    )
    _record(
        db_session,
        version,
        stage="priprava",
        kind="notification",
        payload={"phase": "priprava", "hotovo": True},
        at=datetime(2026, 10, 25, 23, 30, tzinfo=timezone.utc),  # 26.10. on the local clock (CET)
    )
    _record(
        db_session,
        version,
        stage="verifikacia",
        kind="verdict",
        payload={"verdict": "PASS"},
        at=datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc),
    )

    await orchestrator._commit_release_note(db_session, version.id, tmp_path, "0.2.0")

    assert _dates(tmp_path) == {"v0.2.0": "2026-10-26"}


async def test_an_auditor_pass_before_any_signature_dates_the_version(db_session, tmp_path):
    version = _template_version(db_session, tmp_path)
    _record(
        db_session,
        version,
        stage="verifikacia",
        kind="verdict",
        payload={"verdict": "PASS"},
        at=datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc),
    )

    await orchestrator._commit_release_note(db_session, version.id, tmp_path, "0.2.0")

    assert _dates(tmp_path) == {"v0.2.0": "2026-10-24"}


async def test_the_older_versions_get_the_day_of_their_record_and_a_version_without_one_keeps_its_day(
    db_session, tmp_path
):
    """NEX Knowledge 30.10.2026: the agent wrote 0.1.0 as 29.10.; its first PASS is 30.10. 04:06 on the local clock."""
    version = _template_version(db_session, tmp_path)
    older = Version(project_id=version.project_id, version_number="0.1.0", status="done")
    db_session.add(older)
    db_session.flush()
    _record(
        db_session,
        older,
        stage="verifikacia",
        kind="verdict",
        payload={"verdict": "PASS"},
        at=datetime(2026, 10, 30, 3, 6, tzinfo=timezone.utc),
    )
    (tmp_path / "docs" / "specs" / "versions").mkdir(parents=True)
    (tmp_path / DATES).write_text(
        '{"v0.0.9": "2026-09-01", "v0.1.0": "2026-10-29", "v0.2.0": "2026-10-20"}', encoding="utf-8"
    )
    _commit_all(tmp_path, "docs: the days of the versions")

    await orchestrator._commit_release_note(db_session, version.id, tmp_path, "0.2.0")

    assert _dates(tmp_path) == {"v0.0.9": "2026-09-01", "v0.1.0": "2026-10-30", "v0.2.0": _today()}
    assert _committed_and_clean(tmp_path, DATES)


async def test_another_project_s_record_dates_nothing_here(db_session, tmp_path):
    version = _template_version(db_session, tmp_path)
    _other_creator, _other_project, other = _seed(db_session, version_number="0.1.0")
    _record(
        db_session,
        other,
        stage="verifikacia",
        kind="verdict",
        payload={"verdict": "PASS"},
        at=datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc),
    )

    await orchestrator._commit_release_note(db_session, version.id, tmp_path, "0.2.0")

    assert _dates(tmp_path) == {"v0.2.0": _today()}


async def test_a_released_version_is_not_dated_again(db_session, tmp_path):
    version = _template_version(db_session, tmp_path)
    version.status = "released"
    db_session.flush()

    await orchestrator._commit_release_note(db_session, version.id, tmp_path, "0.2.0")

    assert not (tmp_path / DATES).exists()


async def test_a_project_without_the_template_gets_no_dates_file(db_session, tmp_path):
    _git_init(tmp_path)
    _creator, project, version = _seed(db_session, version_number="0.2.0")
    _add_epic(db_session, project, version, 1, "Bez šablóny", "Vlastný rám.")

    await orchestrator._commit_release_note(db_session, version.id, tmp_path, "0.2.0")

    assert (tmp_path / "docs" / "specs" / "versions" / "v0.2.0" / "RELEASE_NOTES.md").is_file()
    assert not (tmp_path / DATES).exists()


async def test_a_day_that_cannot_be_written_does_not_sink_the_sign_off(db_session, tmp_path, monkeypatch):
    version = _template_version(db_session, tmp_path)

    def unwritable(*_args):
        raise OSError("read-only file system")

    monkeypatch.setattr(release_note_writer, "record_release_dates", unwritable)

    await orchestrator._commit_release_note(db_session, version.id, tmp_path, "0.2.0")

    assert _committed_and_clean(tmp_path, NOTE) and not (tmp_path / DATES).exists()


def test_the_agent_of_a_template_project_reads_that_the_cockpit_dates_its_version(tmp_path):
    from backend.services import create_project_postscaffold as cps

    root = tmp_path / "projekt"
    (root / ".claude").mkdir(parents=True)
    cps.provision_v2_agent_charters(root, "projekt", "Projekt", adopted=False, template="nsv-standard")
    skill = (root / ".claude" / "skills" / "sablona-aplikacie" / cps.SKILL_FILE).read_text(encoding="utf-8")

    assert "**Dátum vydania v `docs/specs/versions/release-dates.json` zapisuje kokpit**" in skill
    flat = " ".join(skill.split())
    assert "Súbor nepíš ani neopravuj" in flat
    assert "deň každej verzie projektu, ktorú má zaznamenanú ako overenú alebo hotovú" in flat
