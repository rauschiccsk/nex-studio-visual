"""DEV-536 — Sprievodca in the cockpit: the switch on the project page (on by default, off for a special app), the
standard that puts a missing catalog of steps into a version's scope, the check of the catalog before Overenie, and the
rules the Staviteľ reads.

Stage 1 (nex-app-kit 0.24.0) gave apps on the template „Ako urobiť“ and the guiding step by step — through the app's
catalog of steps only. The Director 28.11.2026: „Najprv treba upraviť kokpit tak aby dokázal doplniť do aplikácie túto
vrstvu. … Má byť default, že tam je ale v prípade, že ide o nejakú špeciálnu aplikáciu aby sme to mohli vypnúť.“ The
plan of stage 2: „Súhlasím“."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from backend.services import app_guide, app_languages, app_template, claude_agent, orchestrator
from backend.services import create_project_postscaffold as postscaffold
from tests.test_web_project_surfaces import _as, _project, _user

KIT = "0.24.1"
MANIFEST = {
    "template": "nsv-standard",
    "name": "NEX Ukážka",
    "initials": "NU",
    "auth": "password",
    "nav": [
        {"label": "Oblasti", "path": "/areas", "icon": "🗂️"},
        {"label": "Prehľady", "path": "/reports", "icon": "📊"},
    ],
}
APP_TS = """\
import { defineApp } from "nex-app-kit";

import data from "../app.manifest.json";
import { guide } from "./guide";
import { messages } from "./locales";

export const manifest = defineApp(data, { messages, guide });
"""
GUIDE_TS = """\
import type { GuideCatalog } from "nex-app-kit";
import { api } from "./api";

// guideTarget("area.ghost") — a comment is no button, and { id: "ghost.step", isDone } in a comment no step
export const guide: GuideCatalog = {
  steps: [
    {
      id: "area.create",
      title: "guide.area.create.title",
      hint: "guide.area.create.hint",
      screen: "guide.area.create.screen",
      path: () => "/areas",
      isDone: async () => (await api.areas()).length > 0,
    },
    {
      title: "guide.area.upload.title",
      id: "area.upload",
      hint: "guide.area.upload.hint",
      screen: "guide.area.upload.screen",
      subject: "area",
      requires: ["area.create"],
      target: "area.files",
      path: (id) => `/areas/${id}`,
      isDone: async (id) => {
        const files = await api.files(id!);
        return files.length > 0;
      },
    },
    {
      id: "report.open",
      title: "guide.report.open.title",
      hint: "guide.report.open.hint",
      screen: "guide.report.open.screen",
      path: () => "/reports/",
      isDone: () => true,
    },
  ],
  subjects: { area: { list: async () => [{ id: "a1", label: "Prvá oblasť" }] } },
};
"""
AREAS_TSX = """\
import { Button } from "nex-shared";
import { guideTarget } from "nex-app-kit";

export function AreasPage() {
  return (
    <>
      <Button {...guideTarget("area.create")}>Nová oblasť</Button>
      <div data-guide="area.files">Súbory</div>
    </>
  );
}
"""
REPORTS_TSX = """\
import { guideTarget } from "nex-app-kit";

export const ReportsPage = () => <button {...guideTarget('report.open')}>Otvoriť</button>;
"""


def app(root: Path, manifest: dict | None = None) -> Path:
    """An app on the template with a whole catalog of steps."""
    write(root, "frontend/app.manifest.json", json.dumps(manifest or MANIFEST, ensure_ascii=False))
    write(root, "frontend/src/app.ts", APP_TS)
    write(root, "frontend/src/guide.ts", GUIDE_TS)
    write(root, "frontend/src/pages/AreasPage.tsx", AREAS_TSX)
    write(root, "frontend/src/pages/ReportsPage.tsx", REPORTS_TSX)
    return root


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def edit(root: Path, rel: str, old: str, new: str) -> None:
    path = root / rel
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, (rel, old)
    path.write_text(text.replace(old, new), encoding="utf-8")


# --- the check of the catalog -----------------------------------------------------------------------------------------


def test_a_whole_catalog_passes(tmp_path):
    assert orchestrator.check_guide_catalog(app(tmp_path), KIT) == []


def _no_file(root):
    (root / "frontend/src/guide.ts").unlink()


def _not_handed(root):
    edit(root, "frontend/src/app.ts", "{ messages, guide }", "{ messages }")


def _not_exported(root):
    edit(root, "frontend/src/guide.ts", "export const guide: GuideCatalog", "const catalog: GuideCatalog")


def _no_steps(root):
    write(
        root,
        "frontend/src/guide.ts",
        'import type { GuideCatalog } from "nex-app-kit";\n\nexport const guide: GuideCatalog = { steps: [] };\n',
    )


def _no_button(root):
    edit(root, "frontend/src/pages/AreasPage.tsx", '<Button {...guideTarget("area.create")}>', "<Button>")


def _button_only_in_a_comment(root):
    edit(
        root,
        "frontend/src/pages/AreasPage.tsx",
        '<Button {...guideTarget("area.create")}>',
        '<Button /* guideTarget("area.create") */>',
    )


def _path_not_as_text(root):
    edit(root, "frontend/src/guide.ts", 'path: () => "/reports/",', "path: reportsPath,")


def _stray_button(root):
    edit(
        root, "frontend/src/pages/ReportsPage.tsx", "Otvoriť</button>", 'Otvoriť</button>; <a data-guide="area.old" />'
    )


def _screen_without_step(root):
    manifest = {**MANIFEST, "nav": [*MANIFEST["nav"], {"label": "Sklad", "path": "/stock", "icon": "📦"}]}
    write(root, "frontend/app.manifest.json", json.dumps(manifest, ensure_ascii=False))


def _twice(root):
    edit(root, "frontend/src/guide.ts", 'id: "report.open"', 'id: "area.create"')
    edit(root, "frontend/src/pages/ReportsPage.tsx", "guideTarget('report.open')", "guideTarget('area.create')")


NO_BUTTON = (
    'krok Sprievodcu „area.create“ nemá tlačidlo — žiadny prvok s {...guideTarget("area.create")} vo frontend/src'
)
NO_STEP_FOR_REPORTS = "obrazovka „Prehľady“ (/reports) z ľavého panela nemá v katalógu Sprievodcu ani jeden krok"


@pytest.mark.parametrize(
    ("spoil", "sentences"),
    [
        (
            _no_file,
            [
                "chýba katalóg krokov Sprievodcu — frontend/src/guide.ts s `export const guide: GuideCatalog` "
                "(zručnosť sablona-aplikacie)"
            ],
        ),
        (_not_handed, ["frontend/src/app.ts nedáva katalóg rámu — `defineApp(data, { …, guide })`"]),
        (
            _not_exported,
            ["frontend/src/guide.ts nevydáva katalóg — `export const guide: GuideCatalog = { steps: [...] }`"],
        ),
        (
            _no_steps,
            [
                "katalóg krokov Sprievodcu (frontend/src/guide.ts) nemá ani jeden krok — každá dôležitá akcia "
                "aplikácie má svoj krok"
            ],
        ),
        (
            _no_button,
            [NO_BUTTON],
        ),
        (
            _button_only_in_a_comment,
            [NO_BUTTON],
        ),
        (
            _path_not_as_text,
            [
                'krok Sprievodcu „report.open“ nepíše adresu obrazovky ako text — `path: () => "/…"` alebo '
                "`path: (s) => `/…/${s}``",
                NO_STEP_FOR_REPORTS,
            ],
        ),
        (_stray_button, ['tlačidlo s guideTarget("area.old") ukazuje na krok, ktorý katalóg nemá']),
        (
            _screen_without_step,
            ["obrazovka „Sklad“ (/stock) z ľavého panela nemá v katalógu Sprievodcu ani jeden krok"],
        ),
        (_twice, ["krok Sprievodcu „area.create“ je v katalógu dvakrát"]),
    ],
    ids=lambda v: v.__name__ if callable(v) else "",
)
def test_a_catalog_that_is_not_whole_is_said_one_sentence_each(tmp_path, spoil, sentences):
    root = app(tmp_path)
    spoil(root)

    assert orchestrator.check_guide_catalog(root, KIT) == sentences


def test_a_step_on_an_object_covers_the_screen_its_address_starts_with(tmp_path):
    """``(id) => `/areas/${id}` `` is below ``/areas`` — without the step that opens /areas the screen has one still."""
    root = app(tmp_path)
    edit(root, "frontend/src/guide.ts", 'path: () => "/areas",', 'path: () => "/start",')

    assert orchestrator.check_guide_catalog(root, KIT) == []


def test_an_address_that_only_shares_the_beginning_is_no_step_of_the_screen(tmp_path):
    root = app(tmp_path)
    edit(root, "frontend/src/guide.ts", 'path: () => "/reports/",', 'path: () => "/reportsarchive",')

    assert orchestrator.check_guide_catalog(root, KIT) == [NO_STEP_FOR_REPORTS]


@pytest.mark.parametrize(
    ("manifest", "kit"),
    [({**MANIFEST, "guide": False}, KIT), (MANIFEST, "0.23.0"), (MANIFEST, None)],
    ids=["switched off", "a template without Sprievodca", "a template that cannot be read"],
)
def test_nothing_is_asked_where_the_app_has_no_sprievodca(tmp_path, manifest, kit):
    root = app(tmp_path, manifest)
    (root / "frontend/src/guide.ts").unlink()

    assert orchestrator.check_guide_catalog(root, kit) == []


# --- the standard in the version's scope ------------------------------------------------------------------------------


def _keys(root: Path) -> list[str]:
    return [s.key for s in orchestrator.missing_icc_standards(root)]


def test_a_catalog_that_is_not_whole_enters_the_versions_scope_with_the_goals_of_the_people(tmp_path, monkeypatch):
    monkeypatch.setattr(app_languages, "kit_version", lambda _path: KIT)
    root = app(tmp_path)
    assert "sprievodca" not in _keys(root)

    _no_steps(root)

    assert "sprievodca" in _keys(root)
    standard = next(s for s in orchestrator.ICC_STANDARDS if s.key == "sprievodca")
    for words in (
        "RIADNA POLOŽKA",
        "„Ciele používateľov“",
        "frontend/src/guide.ts",
        "guideTarget",
        "sablona-aplikacie",
    ):
        assert words in standard.scope_sentence


def test_an_app_with_sprievodca_switched_off_or_without_the_template_is_not_asked(tmp_path, monkeypatch):
    monkeypatch.setattr(app_languages, "kit_version", lambda _path: KIT)
    off = app(tmp_path / "off", {**MANIFEST, "guide": False})
    _no_steps(off)
    plain = tmp_path / "plain"
    write(plain, "frontend/src/App.tsx", "export const App = () => null;\n")

    assert "sprievodca" not in _keys(off)
    assert "sprievodca" not in _keys(plain)


# --- the check before Overenie --------------------------------------------------------------------------------------


def _on_disk(monkeypatch, tmp_path):
    root = app(tmp_path / "ukazka")
    # the template's frame, whole — the frame check goes first
    write(
        root,
        "frontend/src/App.tsx",
        'import { TemplateApp } from "nex-app-kit";\nexport const App = () => <TemplateApp />;\n',
    )
    write(root, "backend/app/kit.py", "from nex_app_kit import AppKit\n\nkit = AppKit(manifest=None)\n")
    write(root, "backend/app/main.py", "from app.kit import kit\n\nkit.install(app)\n")
    write(
        root,
        "docker-compose.yml",
        "services:\n  backend:\n    build: ./backend\n  frontend:\n    build: ./frontend\n"
        "  db:\n    image: postgres:16\n",
    )
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(claude_agent, "_project_type", lambda slug: "standard")
    monkeypatch.setattr(app_template, "template_of_slug", lambda slug: "nsv-standard" if slug == "ukazka" else None)
    monkeypatch.setattr(app_languages, "kit_version", lambda _path: KIT)
    return root


def test_a_whole_catalog_lets_the_boot_check_go_on(monkeypatch, tmp_path):
    _on_disk(monkeypatch, tmp_path)

    assert orchestrator._smoke_compose_target("ukazka")[2] is None


def test_a_catalog_that_is_not_whole_stops_the_boot_check_and_says_where_it_is_switched_off(monkeypatch, tmp_path):
    root = _on_disk(monkeypatch, tmp_path)
    _screen_without_step(root)

    assert orchestrator._smoke_compose_target("ukazka")[2] == (
        False,
        "Katalóg krokov Sprievodcu nie je úplný: obrazovka „Sklad“ (/stock) z ľavého panela nemá v katalógu Sprievodcu "
        "ani jeden krok. Pri špeciálnej aplikácii sa Sprievodca dá vypnúť na stránke projektu.",
    )


# --- the rules the agents read --------------------------------------------------------------------------------------


def test_the_agents_of_a_project_on_the_template_read_how_to_give_sprievodca_its_steps():
    rules = postscaffold.project_type_rules("standard", "nsv-standard")
    for words in (
        "„Ciele používateľov“",
        "frontend/src/guide.ts",
        'guideTarget("<id>")',
        "Kľúč `guide` v manifeste mení len kokpit",
    ):
        assert words in rules
    skill = (postscaffold.SKILLS_TEMPLATE_DIR / "sablona-aplikacie" / postscaffold.SKILL_FILE).read_text(
        encoding="utf-8"
    )
    assert "## Krok Sprievodcu (nex-app-kit od 0.24.0)" in skill
    assert "Sprievodca" not in postscaffold.project_type_rules("standard", None)


# --- the switch on the project page ---------------------------------------------------------------------------------


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """The app's workspace as a real git repository with an origin to push to."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    root = app(tmp_path / "work")
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@icc.sk")
    _git(root, "config", "user.name", "Test")
    _git(root, "remote", "add", "origin", str(origin))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    monkeypatch.setattr(app_languages, "kit_version", lambda _path: KIT)
    monkeypatch.setattr("backend.api.routes.project_guide.chown_workspace", lambda _path: None)
    return root


def _on_template(db, root: Path):
    owner = _user(db)
    project = _project(db, owner)
    project.template = "nsv-standard"
    project.source_path = str(root)
    db.flush()
    _as(db, owner)
    return project


def _manifest(root: Path) -> dict:
    return json.loads((root / "frontend/app.manifest.json").read_text(encoding="utf-8"))


def test_sprievodca_is_on_by_default_and_the_switch_turns_it_off_and_on_in_the_manifest(client, db_session, workspace):
    project = _on_template(db_session, workspace)
    url = f"/api/v1/projects/{project.id}/guide"
    assert client.get(url).json() == {
        "applies": True,
        "on": True,
        "refusal": None,
        "kit_version": KIT,
        "needed": "0.24.0",
        "warnings": [],
    }

    off = client.put(url, json={"on": False})

    assert off.status_code == 200 and off.json()["on"] is False and off.json()["warnings"] == []
    assert _manifest(workspace)["guide"] is False
    assert _git(workspace, "log", "-1", "--format=%s") == app_guide.COMMIT_OFF
    assert _git(workspace, "status", "--porcelain") == ""
    assert _git(workspace, "rev-parse", "HEAD") == _git(workspace, "rev-parse", "origin/main")

    on = client.put(url, json={"on": True})

    assert on.json()["on"] is True
    assert "guide" not in _manifest(workspace) and _manifest(workspace)["nav"] == MANIFEST["nav"]
    assert _git(workspace, "log", "-1", "--format=%s") == app_guide.COMMIT_ON


def test_a_switch_to_the_state_it_is_in_writes_nothing(client, db_session, workspace):
    project = _on_template(db_session, workspace)
    head = _git(workspace, "rev-parse", "HEAD")

    answer = client.put(f"/api/v1/projects/{project.id}/guide", json={"on": True})

    assert answer.json()["on"] is True and _git(workspace, "rev-parse", "HEAD") == head


def test_unsaved_work_in_the_manifest_waits_and_nothing_is_written(client, db_session, workspace):
    project = _on_template(db_session, workspace)
    edit(workspace, "frontend/app.manifest.json", '"Oblasti"', '"Oblasti a súbory"')
    sentence = (
        "V manifeste aplikácie (frontend/app.manifest.json) sú neuložené zmeny — Sprievodcu prepni, keď ich Staviteľ "
        "uloží."
    )

    assert client.get(f"/api/v1/projects/{project.id}/guide").json()["refusal"] == sentence
    refused = client.put(f"/api/v1/projects/{project.id}/guide", json={"on": False})

    assert (refused.status_code, refused.json()["detail"]) == (409, sentence)
    assert "guide" not in _manifest(workspace) and _git(workspace, "log", "-1", "--format=%s") == "init"


def test_a_project_on_a_template_without_sprievodca_is_told_to_upgrade_it(client, db_session, workspace, monkeypatch):
    monkeypatch.setattr(app_languages, "kit_version", lambda _path: "0.23.0")
    project = _on_template(db_session, workspace)
    sentence = (
        "Sprievodcu pozná šablóna od verzie nex-app-kit 0.24.0, projekt stojí na 0.23.0. "
        "Najprv povýš šablónu aplikácie."
    )

    found = client.get(f"/api/v1/projects/{project.id}/guide").json()
    refused = client.put(f"/api/v1/projects/{project.id}/guide", json={"on": False})

    assert (found["applies"], found["on"], found["refusal"]) == (True, False, sentence)
    assert (refused.status_code, refused.json()["detail"]) == (409, sentence)


def test_a_project_without_the_template_has_no_sprievodca(client, db_session):
    owner = _user(db_session)
    project = _project(db_session, owner)
    _as(db_session, owner)

    assert client.get(f"/api/v1/projects/{project.id}/guide").json()["applies"] is False
    refused = client.put(f"/api/v1/projects/{project.id}/guide", json={"on": False})
    assert (refused.status_code, refused.json()["detail"]) == (
        409,
        "Projekt nie je na šablóne aplikácie — Sprievodcu nemá.",
    )


def test_someone_else_s_project_is_neither_read_nor_switched(client, db_session, workspace):
    project = _on_template(db_session, workspace)
    _as(db_session, _user(db_session, role="ha"))

    assert client.get(f"/api/v1/projects/{project.id}/guide").status_code in (403, 404)
    assert client.put(f"/api/v1/projects/{project.id}/guide", json={"on": False}).status_code in (403, 404)
    assert "guide" not in _manifest(workspace)
