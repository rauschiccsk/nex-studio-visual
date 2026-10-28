"""DEV-253 — a project founded on an app template keeps the template's frame, and the cockpit says so before Overenie.

The left panel, sign-in, Ako začať, Aktualizácie, Nastavenia and the server's standard interface come from nex-app-kit.
Director 28.10.2026: „Na ľavom panely vždy rovnako pomenovať záložky … aby sa to dalo ľahko doplňať bez potreby
refaktoringu jednotlivých aplikácii.“ A frame rebuilt inside the app is the drift the template exists to end: the
boot check refuses it before anything is built, one sentence per problem, and the Aktualizácie gate recognises the
template's own page instead of asking for one the app must not write. The Vizuál brief tells such a project about the
frame instead of the left-panel rule and the preview harness it does not build.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services import app_template, claude_agent, orchestrator
from tests.test_orchestrator_v2_vizual import _make_version

APP_TSX = """\
import { BrowserRouter, Route } from "react-router-dom";
import { TemplateApp } from "nex-app-kit";
import { Page } from "nex-shared";

// <Route path="/settings" element={<Mine />} /> — a comment is not a route
export function App() {
  return (
    <BrowserRouter>
      <TemplateApp manifest={manifest} overview={<Page title="Prehľad" />}>
        <Route path="/areas" element={<Page title="Oblasti" />} />
      </TemplateApp>
    </BrowserRouter>
  );
}
"""

KIT_PY = "from nex_app_kit import AppKit\n\nkit = AppKit(manifest=None)\n"
MAIN_PY = """\
from fastapi import FastAPI

from app.kit import kit
from app.routes import areas

app = FastAPI()
kit.install(app)
app.include_router(areas.router, prefix="/api/v1")
# @router.post("/auth/login") — a comment is not a route
"""
AREAS_PY = """\
from fastapi import APIRouter

router = APIRouter(prefix="/areas")


@router.get("")
def list_areas():
    return []
"""


def frame(root: Path, template: str = "nsv-standard") -> Path:
    """A whole frame, in the scaffold's shape."""
    (root / "frontend" / "src").mkdir(parents=True)
    (root / "frontend" / "app.manifest.json").write_text(
        json.dumps({"template": template, "name": "NEX Ukážka", "initials": "NU", "auth": "password", "nav": []}),
        encoding="utf-8",
    )
    (root / "frontend" / "src" / "App.tsx").write_text(APP_TSX, encoding="utf-8")
    (root / "backend" / "app" / "routes").mkdir(parents=True)
    (root / "backend" / "app" / "kit.py").write_text(KIT_PY, encoding="utf-8")
    (root / "backend" / "app" / "main.py").write_text(MAIN_PY, encoding="utf-8")
    (root / "backend" / "app" / "routes" / "areas.py").write_text(AREAS_PY, encoding="utf-8")
    return root


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


NO_TEMPLATE_APP = "frontend nevykresľuje rám šablóny — <TemplateApp> z nex-app-kit vo frontend/src/App.tsx"
NO_APP_KIT = "server nestojí na šablóne — AppKit z nex_app_kit a kit.install(app) v backend/app/"


def test_the_scaffold_s_frame_is_whole(tmp_path):
    assert orchestrator.check_template_frame(frame(tmp_path), "nsv-standard") == []


@pytest.mark.parametrize(
    "spoil,sentence",
    [
        (
            lambda r: (r / "frontend" / "app.manifest.json").unlink(),
            "chýba frontend/app.manifest.json — manifest, z ktorého rám berie názov, prihlásenie a menu",
        ),
        (
            lambda r: write(r, "frontend/app.manifest.json", "{ nie json"),
            "frontend/app.manifest.json nie je platný JSON",
        ),
        (
            lambda r: write(r, "frontend/app.manifest.json", json.dumps({"template": "iny-vzhlad"})),
            "frontend/app.manifest.json hovorí o šablóne „iny-vzhlad“, projekt je založený na „nsv-standard“",
        ),
        (
            lambda r: write(r, "frontend/src/App.tsx", APP_TSX.replace("<TemplateApp manifest", "<Shell manifest")),
            NO_TEMPLATE_APP,
        ),
        (
            lambda r: write(
                r, "frontend/src/App.tsx", APP_TSX.replace('import { TemplateApp } from "nex-app-kit";', "")
            ),
            NO_TEMPLATE_APP,
        ),
        (
            lambda r: write(r, "backend/app/main.py", MAIN_PY.replace("kit.install(app)", "")),
            NO_APP_KIT,
        ),
        (
            lambda r: write(r, "backend/app/kit.py", KIT_PY.replace("AppKit(", "dict(")),
            NO_APP_KIT,
        ),
    ],
)
def test_a_frame_that_is_not_whole_is_said_in_one_sentence(tmp_path, spoil, sentence):
    root = frame(tmp_path)
    spoil(root)

    assert orchestrator.check_template_frame(root, "nsv-standard") == [sentence]


def test_a_template_app_only_in_a_comment_is_not_rendered(tmp_path):
    root = frame(tmp_path)
    write(
        root, "frontend/src/App.tsx", "// <TemplateApp>\n" + APP_TSX.replace("<TemplateApp manifest", "<div manifest")
    )

    assert orchestrator.check_template_frame(root, "nsv-standard") == [NO_TEMPLATE_APP]


def test_an_own_left_panel_sign_in_or_settings_in_the_frontend_is_said_by_file(tmp_path):
    root = frame(tmp_path)
    write(
        root,
        "frontend/src/layout/Shell.tsx",
        'import { AppShell, Sidebar as Panel, Page, type SidebarProps } from "nex-shared";\n',
    )
    write(root, "frontend/src/pages/Login.tsx", 'import { LoginForm } from "nex-shared";\n')
    write(
        root,
        "frontend/src/types.ts",
        'import type { Sidebar } from "nex-shared";\nimport { type Header } from "nex-shared";\n',
    )

    assert orchestrator.check_template_frame(root, "nsv-standard") == [
        "frontend/src/layout/Shell.tsx skladá vlastný rám z nex-shared (AppShell, Sidebar) — rám je zo šablóny",
        "frontend/src/pages/Login.tsx skladá vlastný rám z nex-shared (LoginForm) — rám je zo šablóny",
    ]


@pytest.mark.parametrize("path", ["/", "/login", "/getting-started", "/updates", "/settings", "/settings/users"])
def test_an_own_route_over_a_frame_page_is_said(tmp_path, path):
    root = frame(tmp_path)
    write(
        root,
        "frontend/src/App.tsx",
        APP_TSX.replace('<Route path="/areas"', f'<Route path="{path}" element={{<Mine />}} />\n<Route path="/areas"'),
    )

    assert orchestrator.check_template_frame(root, "nsv-standard") == [
        f"frontend/src/App.tsx má vlastnú cestu „{path}“ — tú stránku má rám šablóny"
    ]


@pytest.mark.parametrize(
    "code,route",
    [
        ('@router.post("/auth/login")\ndef login(): ...\n', "/auth/login"),
        ('@router.get("/api/v1/release-notes")\ndef notes(): ...\n', "/api/v1/release-notes"),
        ('@router.get("/launch")\ndef a(): ...\n@router.post("/launch")\ndef b(): ...\n', "/launch"),
        ('router = APIRouter(prefix="/api/v1/users")\n', "/api/v1/users"),
        ('app.include_router(router, prefix="/sessions")\n', "/sessions"),
        ('@app.api_route("/system-settings/{key}", methods=["PATCH"])\ndef s(): ...\n', "/system-settings/{key}"),
    ],
)
def test_an_own_route_of_the_standard_interface_on_the_server_is_said_once(tmp_path, code, route):
    root = frame(tmp_path)
    write(root, "backend/app/routes/mine.py", code)

    assert orchestrator.check_template_frame(root, "nsv-standard") == [
        f"backend/app/routes/mine.py má vlastnú cestu servera „{route}“ — štandardné rozhranie má šablóna"
    ]


def test_a_server_file_that_does_not_parse_is_no_frame_of_its_own(tmp_path):
    root = frame(tmp_path)
    write(root, "backend/app/broken.py", "def (:\n")

    assert orchestrator.check_template_frame(root, "nsv-standard") == []


# ── Aktualizácie: the template's page counts ────────────────────────────────────────────────────────────────────


def test_the_aktualizacie_gate_recognises_the_template_s_page(tmp_path):
    assert orchestrator._check_aktualizacie_frontend(frame(tmp_path)) is None


def test_without_the_template_s_frame_the_aktualizacie_gate_asks_as_before(tmp_path):
    root = frame(tmp_path)
    write(root, "frontend/src/App.tsx", APP_TSX.replace("<TemplateApp manifest", "<div manifest"))

    assert orchestrator._check_aktualizacie_frontend(root) == (
        "chýba stránka Aktualizácie (napr. frontend/src/pages/UpdatesPage.tsx)"
    )


# ── the boot check refuses a broken frame before anything is built ──────────────────────────────────────────────


def _on_disk(monkeypatch, tmp_path, template):
    root = frame(tmp_path / "ukazka")
    (root / "docker-compose.yml").write_text(
        "services:\n  backend:\n    build: ./backend\n  frontend:\n    build: ./frontend\n"
        "  db:\n    image: postgres:16\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    monkeypatch.setattr(claude_agent, "_project_type", lambda slug: "standard")
    monkeypatch.setattr(app_template, "template_of_slug", lambda slug: template if slug == "ukazka" else None)
    return root


def test_a_whole_frame_lets_the_boot_check_go_on(monkeypatch, tmp_path):
    _on_disk(monkeypatch, tmp_path, "nsv-standard")

    _compose, roles, verdict = orchestrator._smoke_compose_target("ukazka")

    assert verdict is None
    assert roles["backend"] == "backend" and roles["frontend"] == "frontend"


def test_a_broken_frame_stops_the_boot_check_with_its_sentences(monkeypatch, tmp_path):
    root = _on_disk(monkeypatch, tmp_path, "nsv-standard")
    (root / "frontend" / "app.manifest.json").unlink()
    write(root, "backend/app/routes/mine.py", '@router.post("/auth/login")\ndef login(): ...\n')

    _compose, _roles, verdict = orchestrator._smoke_compose_target("ukazka")

    assert verdict == (
        False,
        "Rám šablóny aplikácie nie je celý: chýba frontend/app.manifest.json — manifest, z ktorého rám berie názov, "
        "prihlásenie a menu; backend/app/routes/mine.py má vlastnú cestu servera „/auth/login“ — štandardné rozhranie "
        "má šablóna.",
    )


def test_a_project_without_a_template_is_not_asked_for_its_frame(monkeypatch, tmp_path):
    root = _on_disk(monkeypatch, tmp_path, None)
    (root / "frontend" / "app.manifest.json").unlink()

    _compose, _roles, verdict = orchestrator._smoke_compose_target("ukazka")

    assert verdict is None


# ── the Vizuál brief ────────────────────────────────────────────────────────────────────────────────────────────


def _brief(db_session, tmp_path, monkeypatch, template):
    version, project = _make_version(db_session)
    project.template = template
    db_session.flush()
    monkeypatch.setattr(claude_agent, "PROJECTS_ROOT", tmp_path)
    (tmp_path / project.slug / "frontend").mkdir(parents=True)
    return orchestrator._vizual_directive(db_session, version.id, None)


def test_a_project_on_a_template_is_told_about_the_frame_not_the_left_panel(db_session, tmp_path, monkeypatch):
    brief = _brief(db_session, tmp_path, monkeypatch, "nsv-standard")

    assert orchestrator._VIZUAL_TEMPLATE_RULES in brief
    assert "previewHandlers({ manifest })" in brief and "`<Route>` vnútri `<TemplateApp>`" in brief
    assert "rememberWidthFor" not in brief
    assert "PREVIEW HARNESS" not in brief


def test_a_project_without_a_template_is_told_as_before(db_session, tmp_path, monkeypatch):
    brief = _brief(db_session, tmp_path, monkeypatch, None)

    assert orchestrator._VIZUAL_TEMPLATE_RULES not in brief
    assert "PREVIEW HARNESS" in brief
    assert orchestrator.layout_rule_for(None) in brief
