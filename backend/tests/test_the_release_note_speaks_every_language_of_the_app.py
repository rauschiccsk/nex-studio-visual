"""DEV-418 — the release note the cockpit writes itself is said in every language of the app.

17.11.2026: nex-app-kit 0.16.0 says Aktualizácie in the person's language from ``RELEASE_NOTES.<language>.md``, but the
note the cockpit assembles from the plan's epics was Slovak only. Director, on the method — the plan writer writes each
epic's plain description in every language of the app, and the cockpit assembles the note in each of them: „Súhlasím“.
"""

from __future__ import annotations

import subprocess
import uuid

import pytest

from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.tasks import Epic
from backend.db.models.versions import Version
from backend.services import orchestrator, release_note_writer
from backend.services.pipeline_status import ParseFailure, parse_task_plan_skeleton
from tests.test_conversation_task_plan import (
    _approve_spec,
    _epics,
    _feat_tasks_dict,
    _make_version,
    _plan,
    _seed_conversation,
    _skeleton_dict,
)

MARK = release_note_writer.GENERATED_MARKER


def _project(db, languages: list[str], *, number: str = "1.7.0", status: str = "active") -> tuple[Project, Version]:
    owner = User(
        username=f"u{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@t.local", password_hash="x", role="ri"
    )
    db.add(owner)
    db.flush()
    project = Project(
        name=f"NEX Manager {uuid.uuid4().hex[:6]}",
        slug=f"nm-{uuid.uuid4().hex[:8]}",
        type="standard",
        auth_mode="password",
        description="DEV-418",
        created_by=owner.id,
        languages=languages,
    )
    db.add(project)
    db.flush()
    version = Version(project_id=project.id, version_number=number, name="Jazyky používateľov", status=status)
    db.add(version)
    db.flush()
    return project, version


def _epic(db, project: Project, version: Version, number: int, text: str, translations: dict | None) -> None:
    db.add(
        Epic(
            project_id=project.id,
            version_id=version.id,
            number=number,
            title=f"Epic {number}",
            plain_description=text,
            plain_translations=translations,
            status="done",
        )
    )
    db.flush()


def _body(path) -> str:
    """The note without its signature line."""
    return "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if MARK not in line).strip()


def _two_epics(db, project, version, *, en_of_second: str | None = "People keep their own language.") -> None:
    _epic(
        db,
        project,
        version,
        1,
        "Správca mení jazyk človeka.",
        {"hu": "Az adminisztrátor módosítja a nyelvet.", "en": "The admin sets a person's language."},
    )
    second = {"hu": "Mindenki a saját nyelvén olvas."}
    if en_of_second:
        second["en"] = en_of_second
    _epic(db, project, version, 2, "Každý číta vo svojom jazyku.", second)


def test_the_note_is_said_in_every_language_of_the_app(db_session, tmp_path) -> None:
    project, version = _project(db_session, ["sk", "hu", "en"])
    _two_epics(db_session, project, version)

    main = release_note_writer.write_release_note(db_session, version.id, tmp_path)
    written = release_note_writer.write_translated_notes(db_session, version.id, tmp_path)

    notes = tmp_path / "docs" / "specs" / "versions" / "v1.7.0"
    assert sorted(p.name for p in written) == ["RELEASE_NOTES.en.md", "RELEASE_NOTES.hu.md"]
    assert (
        _body(main)
        == "## v1.7.0 — Jazyky používateľov\n\n- Správca mení jazyk človeka.\n- Každý číta vo svojom jazyku."
    )
    assert _body(notes / "RELEASE_NOTES.hu.md") == (
        "## v1.7.0\n\n- Az adminisztrátor módosítja a nyelvet.\n- Mindenki a saját nyelvén olvas."
    )
    assert _body(notes / "RELEASE_NOTES.en.md") == (
        "## v1.7.0\n\n- The admin sets a person's language.\n- People keep their own language."
    )
    assert MARK in (notes / "RELEASE_NOTES.en.md").read_text(encoding="utf-8"), "ours, so it is kept up to date"


def test_a_language_an_epic_lacks_is_left_to_the_main_note(db_session, tmp_path) -> None:
    """A note half in one language is not said; the app then says the main one. One of ours that the plan no longer
    covers goes away."""
    project, version = _project(db_session, ["sk", "hu", "en"])
    _two_epics(db_session, project, version)
    release_note_writer.write_translated_notes(db_session, version.id, tmp_path)
    english = tmp_path / "docs" / "specs" / "versions" / "v1.7.0" / "RELEASE_NOTES.en.md"
    assert english.is_file()

    db_session.query(Epic).filter(Epic.version_id == version.id, Epic.number == 2).update(
        {"plain_translations": {"hu": "Mindenki a saját nyelvén olvas."}}
    )
    db_session.flush()
    touched = release_note_writer.write_translated_notes(db_session, version.id, tmp_path)

    assert not english.exists()
    assert sorted(p.name for p in touched) == ["RELEASE_NOTES.en.md", "RELEASE_NOTES.hu.md"]


def test_a_translation_somebody_wrote_is_left_as_it_is(db_session, tmp_path) -> None:
    project, version = _project(db_session, ["sk", "hu"])
    _two_epics(db_session, project, version)
    notes = tmp_path / "docs" / "specs" / "versions" / "v1.7.0"
    notes.mkdir(parents=True)
    (notes / "RELEASE_NOTES.hu.md").write_text("## v1.7.0\n\n- Saját szöveg.\n", encoding="utf-8")

    assert release_note_writer.write_translated_notes(db_session, version.id, tmp_path) == []
    assert (notes / "RELEASE_NOTES.hu.md").read_text(encoding="utf-8") == "## v1.7.0\n\n- Saját szöveg.\n"


def test_an_app_whose_main_language_is_hungarian_has_its_main_note_in_hungarian(db_session, tmp_path) -> None:
    project, version = _project(db_session, ["hu", "sk"])
    _two_epics(db_session, project, version)

    main = release_note_writer.write_release_note(db_session, version.id, tmp_path)
    written = release_note_writer.write_translated_notes(db_session, version.id, tmp_path)

    assert _body(main) == "## v1.7.0\n\n- Az adminisztrátor módosítja a nyelvet.\n- Mindenki a saját nyelvén olvas."
    assert [p.name for p in written] == ["RELEASE_NOTES.sk.md"]
    assert _body(written[0]).endswith("- Správca mení jazyk človeka.\n- Každý číta vo svojom jazyku.")


def test_without_the_main_languages_translation_the_main_note_stays_slovak_and_alone(db_session, tmp_path) -> None:
    project, version = _project(db_session, ["hu", "sk"])
    _epic(db_session, project, version, 1, "Správca mení jazyk človeka.", None)

    main = release_note_writer.write_release_note(db_session, version.id, tmp_path)

    assert _body(main) == "## v1.7.0 — Jazyky používateľov\n\n- Správca mení jazyk človeka."
    assert release_note_writer.write_translated_notes(db_session, version.id, tmp_path) == []
    assert not (tmp_path / "docs" / "specs" / "versions" / "v1.7.0" / "RELEASE_NOTES.sk.md").exists()


def test_a_slovak_app_and_a_released_version_get_no_translation(db_session, tmp_path) -> None:
    project, version = _project(db_session, ["sk"])
    _two_epics(db_session, project, version)
    assert release_note_writer.write_translated_notes(db_session, version.id, tmp_path) == []

    project, released = _project(db_session, ["sk", "hu"], number="1.6.0", status="released")
    _two_epics(db_session, project, released)
    assert release_note_writer.write_translated_notes(db_session, released.id, tmp_path) == []


@pytest.mark.asyncio
async def test_the_translations_ride_the_notes_commit(db_session, tmp_path) -> None:
    for args in (("init", "-q"), ("config", "user.email", "t@t.local"), ("config", "user.name", "t")):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True)
    (tmp_path / "README.md").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "seed"], check=True)
    project, version = _project(db_session, ["sk", "hu", "en"])
    _two_epics(db_session, project, version)

    await orchestrator._commit_release_note(db_session, version.id, tmp_path, "1.7.0")

    shown = subprocess.run(
        ["git", "-C", str(tmp_path), "show", "--name-only", "--format=", "HEAD"], capture_output=True, text=True
    ).stdout.split()
    assert sorted(shown) == [
        "docs/specs/versions/v1.7.0/RELEASE_NOTES.en.md",
        "docs/specs/versions/v1.7.0/RELEASE_NOTES.hu.md",
        "docs/specs/versions/v1.7.0/RELEASE_NOTES.md",
    ]


# --- the plan writes the translations ----------------------------------------------------------------------------


def test_the_plan_of_a_multilingual_app_asks_for_the_translations(db_session) -> None:
    rule = orchestrator._task_plan_skeleton_directive(None, ("sk", "hu", "en"))

    assert orchestrator.plain_translations_rule(("sk", "hu", "en")) in rule
    assert '{"hu": "…", "en": "…"}' in rule
    assert "maďarčina, angličtina" in rule
    assert "plain_translations" not in orchestrator._task_plan_skeleton_directive(None, ("sk",))


def _skeleton(translations: dict) -> dict:
    return {
        "epics": [
            {
                "title": "Languages",
                "plain_description": "Správca mení jazyk človeka.",
                "plain_translations": translations,
                "feats": [{"title": "Users", "description": "x", "plain_description": "y"}],
            },
            {"title": "Internal", "plain_description": "", "feats": [{"title": "Z", "plain_description": "z"}]},
        ],
        "flagship_features": ["Jazyk človeka"],
        "safety_properties": [],
    }


def test_a_skeleton_without_a_translation_is_asked_again_naming_what_is_missing() -> None:
    parsed = parse_task_plan_skeleton(_skeleton({"hu": "Az adminisztrátor módosítja a nyelvet.", "en": " ", "de": "x"}))
    assert not isinstance(parsed, ParseFailure)
    assert parsed.epics[0].plain_translations == {"hu": "Az adminisztrátor módosítja a nyelvet.", "de": "x"}

    assert orchestrator.plain_translations_gap(parsed, ("sk", "hu", "en")) == (
        "chýba preklad `plain_description` v `plain_translations`: „Languages“ (angličtina)"
    )
    complete = parse_task_plan_skeleton(_skeleton({"hu": "Az admin módosítja.", "en": "The admin sets it."}))
    assert orchestrator.plain_translations_gap(complete, ("sk", "hu", "en")) is None


@pytest.mark.asyncio
async def test_the_plan_round_asks_again_and_keeps_each_epics_translations(db_session, monkeypatch) -> None:
    version, project = _make_version(db_session)
    project.languages = ["sk", "hu", "en"]
    db_session.flush()
    _seed_conversation(db_session, version.id)
    _approve_spec(db_session, version.id)
    plan = _plan(epic_plain="Správca mení jazyk človeka.")
    skeleton_prompts: list[str] = []

    async def _fake_invoke_claude(*, prompt, **_kw):
        if "KOSTRU" in prompt or "kostra plánu nemá preklady" in prompt:  # the pass, or its retry
            skeleton_prompts.append(prompt)
            skeleton = _skeleton_dict(plan)
            if len(skeleton_prompts) > 1:
                skeleton["epics"][0]["plain_translations"] = {"hu": "Az admin módosítja.", "en": "The admin sets it."}
            return ("", None, skeleton)
        return ("", None, _feat_tasks_dict(plan[0]["feats"][0]["tasks"]))

    monkeypatch.setattr(orchestrator, "invoke_claude", _fake_invoke_claude)
    monkeypatch.setattr(orchestrator, "_split_claude_result", lambda r: r)
    monkeypatch.setattr(orchestrator, "_resolve_orch_session", lambda db, slug, role: (uuid.uuid4(), False))
    monkeypatch.setattr(orchestrator, "_resolve_dispatch_overrides", lambda db, vid, role: (None, None))

    await orchestrator.apply_action(db_session, version_id=version.id, action="zostav_plan")
    await orchestrator.run_conversation_turn(db_session, version.id)

    assert len(skeleton_prompts) == 2
    assert orchestrator.plain_translations_rule(("sk", "hu", "en")) in skeleton_prompts[0]
    assert "„Foundation“ (maďarčina, angličtina)" in skeleton_prompts[1]
    assert _epics(db_session, version.id)[0].plain_translations == {
        "hu": "Az admin módosítja.",
        "en": "The admin sets it.",
    }
