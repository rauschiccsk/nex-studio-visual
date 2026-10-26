"""DEV-230 — the Aktualizácie gate checks that the Updates page is wired, not that it lives at ``/updates``.

26.10.2026, Author Asistent 0.1.0, Verifikácia: the app booted and the gate stopped it — „chýba /updates route
v routeri“. Its Updates page was wired: ``<Route path="aktualizacie" element={<UpdatesPage />} />`` and a menu item
leading to ``/aktualizacie``. The template had ``/updates``; the approved Vizuál moved every path of the app to Slovak,
and the approved Návrh lists „/aktualizacie | Aktualizácie | zo šablóny“. The agent was never told the path is fixed;
the gate spoke only at Verifikácia. Director: „Áno, súhlasím, založ oba tikety a oprav to“.

Pinned: a route that renders the Updates page passes at any path, in either router form; the menu must lead to that
path; a route to another page, or a menu to another path, does not pass; the standard's sentence no longer names
``/updates`` as the path.
"""

from __future__ import annotations

from backend.services import orchestrator

#: Author Asistent's own wiring, as it stands in its App.tsx and Sidebar.tsx.
AA_APP = '<Route path="diela" element={<WorksPage />} />\n<Route path="aktualizacie" element={<UpdatesPage />} />\n'
AA_SIDEBAR = '{item("/diela", "📚", "Diela")}\n{item("/aktualizacie", "✨", "Aktualizácie")}\n'


def _project(tmp_path, *, app: str, sidebar: str, router_file: str = "App.tsx"):
    src = tmp_path / "frontend" / "src"
    (src / "pages").mkdir(parents=True)
    (src / "components" / "layout").mkdir(parents=True)
    (src / "pages" / "UpdatesPage.tsx").write_text("export function UpdatesPage() {\n  return null;\n}\n")
    (src / router_file).write_text(app)
    (src / "components" / "layout" / "Sidebar.tsx").write_text(sidebar)
    return tmp_path


def test_author_asistents_updates_at_aktualizacie_pass(tmp_path):
    assert orchestrator._check_aktualizacie_frontend(_project(tmp_path, app=AA_APP, sidebar=AA_SIDEBAR)) is None


def test_the_element_may_come_before_the_path(tmp_path):
    app = '<Route element={<UpdatesPage />} path="aktualizacie" />\n'

    assert orchestrator._check_aktualizacie_frontend(_project(tmp_path, app=app, sidebar=AA_SIDEBAR)) is None


def test_a_data_router_object_at_any_path_passes(tmp_path):
    router = (
        'export const router = createBrowserRouter([\n  { path: "novinky", element: <UpdatesPage /> },\n'
        '  { path: "diela", Component: WorksPage },\n]);\n'
    )
    sidebar = '<NavItem onClick={() => navigate("/novinky")} />\n'

    project = _project(tmp_path, app=router, sidebar=sidebar, router_file="router.tsx")

    assert orchestrator._check_aktualizacie_frontend(project) is None


def test_a_route_to_another_page_is_not_the_updates_route(tmp_path):
    app = '<Route path="aktualizacie" element={<WorksPage />} />\n'

    msg = orchestrator._check_aktualizacie_frontend(_project(tmp_path, app=app, sidebar=AA_SIDEBAR))

    assert msg == "chýba cesta v routeri, ktorá ukáže stránku Aktualizácie"


def test_the_menu_must_lead_to_the_updates_path(tmp_path):
    sidebar = '{item("/diela", "📚", "Diela")}\n'

    msg = orchestrator._check_aktualizacie_frontend(_project(tmp_path, app=AA_APP, sidebar=sidebar))

    assert msg == "chýba položka v menu, ktorá vedie na stránku Aktualizácie (/aktualizacie)"


def test_the_standards_sentence_does_not_fix_the_path():
    sentence = next(s.scope_sentence for s in orchestrator.ICC_STANDARDS if s.key == "aktualizacie")

    assert "cesta /updates" not in sentence
    assert "cesta v routeri" in sentence
