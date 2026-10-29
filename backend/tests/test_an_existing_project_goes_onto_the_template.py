"""DEV-254 — an existing project goes onto the app template, its screens kept.

Director 29.10.2026: „Začneme s projektom nex-knowledge“ — NEX Knowledge was built before the template. The cockpit adds
what makes it a project on the template (the manifest, nex-app-kit on both sides, nex-shared at least as new as the
release was proven with) in one commit and remembers the template; the project's screens, routes and tables stay. The
next version moves them into the frame: Príprava gets it as a scope item, the boot check refuses a frame that is not
whole. A project with a version in progress or an installation is not converted, and the Manažér is told why.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.db.models.customers import Customer
from backend.db.models.deploy import DeployEvent
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.db.models.versions import Version
from backend.services import app_template, orchestrator
from backend.services.app_template import AppTemplateError, TemplateChoice

KIT = "github:rauschiccsk/nex-app-kit#v0.3.0"
SHARED_028 = "github:rauschiccsk/nex-shared#v0.28.0"
PYPROJECT = """[project]
name = "nex-knowledge-backend"
dependencies = [
    "fastapi>=0.115,<1",
    "pg8000>=1.31,<2",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3,<9",
]
"""
PACKAGE = {
    "name": "nex-knowledge-frontend",
    "dependencies": {"nex-shared": "github:rauschiccsk/nex-shared#v0.25.0", "react": "^19.0.0", "zustand": "^5"},
    "devDependencies": {"msw": "^2.15.0", "vite": "^6.0.0"},
}
#: What the template's frontend asks for (scaffold/nsv-standard/frontend/package.json, abridged).
SCAFFOLD_PACKAGE = {
    "dependencies": {
        "@tailwindcss/typography": "^0.5",
        "nex-app-kit": KIT,
        "nex-shared": SHARED_028,
        "react": "^19.1.0",
    },
    "devDependencies": {"msw": "^2.0.0", "vitest": "^3.0.0"},
}
MANIFEST = {"template": "nsv-standard", "name": "NEX Knowledge", "initials": "NK", "auth": "manager", "nav": []}


class World:
    """GitHub, the kit's renderer, npm and git — answered as they answer, every call recorded."""

    def __init__(self, *, npm_writes: str | None = "0.3.0", fail: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.npm_writes = npm_writes
        self.fail = fail

    def __call__(self, argv: list[str], cwd: str | None = None, **_kwargs) -> subprocess.CompletedProcess:
        self.calls.append(argv)
        what = " ".join(argv[:2])
        if self.fail and self.fail in what:
            return subprocess.CompletedProcess(argv, 1, "", "zlyhalo")
        if what == "git ls-remote":
            return subprocess.CompletedProcess(argv, 0, "x\trefs/tags/v0.2.0\ny\trefs/tags/v0.3.0\n", "")
        if argv[0] == "python3":
            out = Path(argv[argv.index("--out") + 1])
            (out / "frontend").mkdir(parents=True)
            (out / "frontend" / "app.manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
            (out / "frontend" / "package.json").write_text(json.dumps(SCAFFOLD_PACKAGE), encoding="utf-8")
        if argv[0] == "npm" and self.npm_writes:
            lock = {"packages": {"node_modules/nex-app-kit": {"version": self.npm_writes}}}
            (Path(cwd) / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, "", "")


def no_fetch(_version, destination: Path, _run) -> None:
    destination.mkdir(parents=True)


def project_on_disk(root: Path, package: dict = PACKAGE, pyproject: str = PYPROJECT) -> Path:
    (root / ".git").mkdir(parents=True)
    (root / "frontend").mkdir()
    (root / "frontend" / "package.json").write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")
    (root / "frontend" / "package-lock.json").write_text('{"packages": {}}', encoding="utf-8")
    (root / "backend").mkdir()
    (root / "backend" / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    return root


def files(root: Path) -> dict[str, str | None]:
    return {
        rel: (root / rel).read_text(encoding="utf-8") if (root / rel).exists() else None
        for rel in app_template._CONVERTED_PATHS
    }


def convert(root: Path, world: World):
    return app_template.convert_project(
        root,
        TemplateChoice("nsv-standard", ai=True),
        name="NEX Knowledge",
        slug="nex-knowledge",
        auth_mode="token",
        ports=(10260, 10261, 10262),
        run=world,
        fetch_kit=no_fetch,
    )


# ── the conversion ──────────────────────────────────────────────────────────────────────────────────────────────


def test_the_project_gets_the_manifest_and_the_kit_and_keeps_everything_of_its_own(tmp_path):
    root = project_on_disk(tmp_path)
    world = World()

    assert convert(root, world) == "0.3.0"

    assert json.loads((root / "frontend" / "app.manifest.json").read_text(encoding="utf-8")) == MANIFEST
    assert json.loads((root / "frontend" / "package.json").read_text(encoding="utf-8")) == {
        "name": "nex-knowledge-frontend",
        "dependencies": {
            "@tailwindcss/typography": "^0.5",
            "nex-app-kit": KIT,
            "nex-shared": SHARED_028,
            "react": "^19.0.0",
            "zustand": "^5",
        },
        "devDependencies": {"msw": "^2.15.0", "vite": "^6.0.0", "vitest": "^3.0.0"},
    }
    pyproject = (root / "backend" / "pyproject.toml").read_text(encoding="utf-8")
    assert pyproject == PYPROJECT.replace(
        "dependencies = [\n", f'dependencies = [\n    "{app_template.kit_server_dependency("0.3.0")}",\n', 1
    )
    npm = next(argv for argv in world.calls if argv[0] == "npm")
    assert npm[-2:] == [KIT, SHARED_028]  # git dependencies npm must re-resolve by name
    add, commit = (argv for argv in world.calls if argv[0] == "git" and argv[1] in ("add", "commit"))
    assert add[3:] == list(app_template._CONVERTED_PATHS)
    assert commit[-1] == "feat(template): onto the app template nsv-standard — its manifest and nex-app-kit v0.3.0"


def test_a_newer_nex_shared_of_the_project_stays(tmp_path):
    package = {
        **PACKAGE,
        "dependencies": {**PACKAGE["dependencies"], "nex-shared": "github:rauschiccsk/nex-shared#v0.30.0"},
    }
    root = project_on_disk(tmp_path, package=package)
    world = World()

    convert(root, world)

    deps = json.loads((root / "frontend" / "package.json").read_text(encoding="utf-8"))["dependencies"]
    assert deps["nex-shared"] == "github:rauschiccsk/nex-shared#v0.30.0"
    assert next(argv for argv in world.calls if argv[0] == "npm")[-1] == KIT


@pytest.mark.parametrize(
    "fail,npm_writes,sentence",
    [
        ("npm install", "0.3.0", "Frontendu sa nepodarilo pripraviť zoznam balíkov so šablónou (npm install)."),
        (None, "0.2.0", "Frontendu sa nepodarilo pripraviť zoznam balíkov so šablónou (npm install)."),
        ("git commit", "0.3.0", "Prevod na šablónu sa nepodarilo zapísať do gitu projektu."),
        ("render.py", "0.3.0", "Kostru šablóny sa nepodarilo vyplniť: zlyhalo"),
    ],
)
def test_a_failed_step_puts_every_file_back_and_is_said(tmp_path, fail, npm_writes, sentence):
    root = project_on_disk(tmp_path)
    before = files(root)

    with pytest.raises(AppTemplateError) as caught:
        convert(root, World(fail=fail, npm_writes=npm_writes))

    assert str(caught.value) == sentence
    assert files(root) == before


@pytest.mark.parametrize(
    "prepare,sentence",
    [
        (
            lambda r: (r / "frontend" / "app.manifest.json").write_text("{}", encoding="utf-8"),
            "Projekt už má manifest šablóny (frontend/app.manifest.json) — prevod sa druhýkrát nespúšťa.",
        ),
        (
            lambda r: (r / "backend" / "pyproject.toml").write_text(
                '[project]\nname = "x"\ndependencies = ["fastapi"]\n', encoding="utf-8"
            ),
            "Server projektu nemá zoznam závislostí v [project].dependencies (po jednej na riadok) — prevod na "
            "šablónu doň nevie doplniť nex-app-kit.",
        ),
        (
            lambda r: (r / "frontend" / "package.json").unlink(),
            "Projektu chýba frontend/package.json — prevod na šablónu ho potrebuje.",
        ),
        (lambda r: (r / ".git").rmdir(), "Projekt nemá na disku git — prevod na šablónu nemá kam zapísať."),
    ],
)
def test_what_cannot_be_converted_is_said_before_github_is_asked(tmp_path, prepare, sentence):
    root = project_on_disk(tmp_path)
    prepare(root)
    world = World()

    with pytest.raises(AppTemplateError) as caught:
        convert(root, world)

    assert str(caught.value) == sentence
    assert world.calls == []


def test_the_kit_goes_first_into_the_server_s_own_list_only():
    elsewhere = '[project]\nname = "x"\n\n[tool.other]\ndependencies = [\n    "a",\n]\n'

    assert app_template.with_server_dependency(PYPROJECT, "kit").startswith(
        '[project]\nname = "nex-knowledge-backend"\ndependencies = [\n    "kit",\n    "fastapi>=0.115,<1",\n'
    )
    assert app_template.with_server_dependency(elsewhere, "kit") is None


# ── who may be converted ────────────────────────────────────────────────────────────────────────────────────────


def _project(db, **changes) -> Project:
    s = uuid.uuid4().hex[:8]
    user = User(username=f"pr_{s}", email=f"pr_{s}@t.local", password_hash="x", role="ri")
    db.add(user)
    db.flush()
    data = dict(
        name=f"P{s}",
        slug=f"p-{s}",
        type="standard",
        auth_mode="token",
        description="prevod",
        created_by=user.id,
        source_path=f"/opt/projects/p-{s}",
        backend_port=10260,
        frontend_port=10261,
        db_port=10262,
    )
    data.update(changes)
    if data["type"] == "web":
        data["web_port"] = 10263  # a Web project has the port of its public web
    project = Project(**data)
    db.add(project)
    db.flush()
    return project


def test_a_project_built_without_the_template_may_be_converted(db_session):
    assert app_template.conversion_refusal(db_session, _project(db_session)) is None


@pytest.mark.parametrize(
    "changes,sentence",
    [
        ({"template": "nsv-standard"}, "Projekt už je na šablóne aplikácie."),
        ({"type": "web"}, "Na šablónu sa zatiaľ prevádza len bežný projekt — Web projekt nie."),
        ({"source_path": None}, "Projekt nemá zdrojový priečinok, prevod nemá kam zapísať."),
    ],
)
def test_a_project_that_cannot_be_converted_says_why(db_session, changes, sentence):
    assert app_template.conversion_refusal(db_session, _project(db_session, **changes)) == sentence


def test_a_version_in_progress_waits(db_session):
    project = _project(db_session)
    db_session.add(Version(project_id=project.id, version_number="0.1.0", status="active"))
    db_session.add(Version(project_id=project.id, version_number="0.2.0", status="planned"))
    db_session.flush()

    assert app_template.conversion_refusal(db_session, project) == (
        "Projekt má rozpracovanú verziu 0.1.0 — prevod počká, kým ju dokončíš."
    )


@pytest.mark.parametrize("environment", ["uat", "prod"])
def test_an_installation_waits_for_the_carry_over_of_its_people(db_session, environment):
    project = _project(db_session)
    customer = Customer(project_id=project.id, name="ICC", slug=f"icc{uuid.uuid4().hex[:6]}")
    db_session.add(customer)
    db_session.flush()
    failed = DeployEvent(
        customer_id=customer.id,
        project_id=project.id,
        version_number="0.1.0",
        environment=environment,
        event_type="deploy",
        status="failed",
    )
    db_session.add(failed)
    db_session.flush()
    assert app_template.conversion_refusal(db_session, project) is None  # a failed deploy left nothing behind

    db_session.add(
        DeployEvent(
            customer_id=customer.id,
            project_id=project.id,
            version_number="0.1.0",
            environment=environment,
            event_type="deploy",
            status="ok",
        )
    )
    db_session.flush()

    assert app_template.conversion_refusal(db_session, project) == (
        "Projekt už má nasadenú inštaláciu — pri prevode treba jeho používateľov, relácie a nastavenia preniesť do "
        "tabuliek šablóny a to kokpit zatiaľ nevie."
    )


# ── the routes ──────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_form_is_offered_the_conversion_or_told_why_not(client, db_session):
    offered = client.get(f"/api/v1/projects/{_project(db_session).id}/app-template-conversion").json()
    refused = client.get(f"/api/v1/projects/{_project(db_session, type='web').id}/app-template-conversion").json()

    assert offered["available"] is True and offered["reason"] is None
    assert offered["default_template"] == "nsv-standard"
    assert [t["id"] for t in offered["templates"]] == ["nsv-standard"]
    assert refused["available"] is False
    assert refused["reason"] == "Na šablónu sa zatiaľ prevádza len bežný projekt — Web projekt nie."


def test_the_conversion_remembers_the_template(client, db_session):
    project = _project(db_session)
    calls = []

    def fake_convert(target, choice, **kwargs):
        calls.append((target, choice, kwargs))
        return "0.3.0"

    with patch.object(app_template, "convert_project", fake_convert):
        response = client.post(
            f"/api/v1/projects/{project.id}/app-template-conversion", json={"template": "nsv-standard", "ai": True}
        )

    assert response.status_code == 200, response.text
    assert response.json() == {"template": "nsv-standard", "kit_version": "0.3.0"}
    [(target, choice, kwargs)] = calls
    assert str(target) == project.source_path
    assert choice == TemplateChoice("nsv-standard", ai=True)
    assert kwargs == {
        "name": project.name,
        "slug": project.slug,
        "auth_mode": "token",
        "ports": (10260, 10261, 10262),
    }
    db_session.refresh(project)
    assert project.template == "nsv-standard"


def test_a_refused_or_failed_conversion_is_said_and_remembers_nothing(client, db_session):
    on_template = _project(db_session, template="nsv-standard")
    project = _project(db_session)

    refused = client.post(
        f"/api/v1/projects/{on_template.id}/app-template-conversion", json={"template": "nsv-standard"}
    )
    unknown = client.post(f"/api/v1/projects/{project.id}/app-template-conversion", json={"template": "iny-vzhlad"})
    with patch.object(app_template, "convert_project", side_effect=AppTemplateError("Prevod sa nepodaril.")):
        failed = client.post(
            f"/api/v1/projects/{project.id}/app-template-conversion", json={"template": "nsv-standard"}
        )

    assert (refused.status_code, refused.json()["detail"]) == (400, "Projekt už je na šablóne aplikácie.")
    assert (unknown.status_code, unknown.json()["detail"]) == (400, "Šablóna aplikácie „iny-vzhlad“ neexistuje.")
    assert (failed.status_code, failed.json()["detail"]) == (500, "Prevod sa nepodaril.")
    db_session.refresh(project)
    assert project.template is None


# ── the version that moves the screens ──────────────────────────────────────────────────────────────────────────


def test_until_the_frame_is_whole_the_version_gets_the_move_in_its_scope(tmp_path):
    root = tmp_path / "nk"
    (root / "frontend" / "src").mkdir(parents=True)
    (root / "frontend" / "app.manifest.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    (root / "frontend" / "src" / "App.tsx").write_text('import { AppShell } from "nex-shared";\n', encoding="utf-8")

    missing = [s.key for s in orchestrator.missing_icc_standards(root)]
    problems = orchestrator._check_template_frame_standard(root)

    assert "ram_sablony" in missing
    assert problems == "; ".join(orchestrator.check_template_frame(root, "nsv-standard"))
    assert "frontend nevykresľuje rám šablóny" in problems


def test_a_project_without_a_manifest_has_no_frame_to_move(tmp_path):
    (tmp_path / "frontend" / "src").mkdir(parents=True)

    assert orchestrator._check_template_frame_standard(tmp_path) is None


@pytest.mark.parametrize(
    "manifest,said",
    [("{ nie json", "frontend/app.manifest.json nie je platný JSON"), ("{}", "manifest nemenuje šablónu")],
)
def test_a_manifest_that_names_no_template_is_said(tmp_path, manifest, said):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "app.manifest.json").write_text(manifest, encoding="utf-8")

    assert orchestrator._check_template_frame_standard(tmp_path) == said
