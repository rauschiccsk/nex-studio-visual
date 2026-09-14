"""ICCINT-125 — povinné celoICC štandardy vstupujú do rozsahu verzie, nie cez padnutú bránu.

Nájdené 14.09.2026 pri Verifikácii NEX Inbox v1.5.0. Nie je to chyba agenta ani Audítora — obaja sa
zachovali správne. Je to diera v tom, ako sa skladá rozsah verzie.

Brána vydania spadla TRIKRÁT za sebou na tom istom (správy 2811, 2821, 2831):

    „kontrola Aktualizácie zlyhala — zoznam noviniek neukazuje aktuálnu verziu"

Agent preto vo fáze Verifikácie dostaval obrazovku „Aktualizácie" aj serverovú časť — teda vo fáze,
kde sa už NEMÁ stavať. Audítor to správne označil za nález: obrazovka nebola vo Vizuáli, nie je
v rozsahu verzie, a návrhový dokument výslovne hovorí, že žiadna nová obrazovka nepribudne.

**Oba výroky boli pravdivé naraz.** Karta Aktualizácie je celoICC štandard, ktorý kokpit pri vydaní
vynucuje — ale ten štandard sa nikdy nedostal do rozsahu verzie. Príprava ho nezahrnula, Vizuál ho
nemal odkiaľ vziať, Návrh napísal opak, a Verifikácia ho vymáhala na konci, keď už je neskoro.

Stane sa to pri KAŽDEJ appke, ktorá kartu Aktualizácie nemá — a to sú všetky prevzaté projekty.

⚠️ Neriešime to oslabením brány. Sonda funguje správne a chytila skutočnú medzeru. Chyba je, že
chytila až ona.
"""

from __future__ import annotations

from backend.services import orchestrator


def test_the_mandatory_standards_are_named_in_one_machine_readable_place():
    """Dnes žije karta Aktualizácie iba ako sonda v kokpite. Požiadavka, ktorú nemožno prečítať skôr
    než zlyhá, sa do zadania nedostane — nie zo zlej vôle, ale preto, že ju niet odkiaľ vziať."""
    assert orchestrator.ICC_STANDARDS, "zoznam povinných štandardov neexistuje"
    klucе = {s.key for s in orchestrator.ICC_STANDARDS}
    assert "aktualizacie" in klucе
    for s in orchestrator.ICC_STANDARDS:
        assert s.name and s.scope_sentence, f"{s.key}: štandard bez mena alebo bez vety do rozsahu"


def test_a_project_without_the_updates_tab_is_reported_as_missing_it(tmp_path):
    """Chýbajúci štandard je RIADNA POLOŽKA verzie, nie prekvapenie na konci."""
    (tmp_path / "frontend" / "src").mkdir(parents=True)
    (tmp_path / "frontend" / "src" / "App.tsx").write_text("export const App = () => null;\n", encoding="utf-8")

    chybajuce = orchestrator.missing_icc_standards(tmp_path)

    assert [s.key for s in chybajuce] == ["aktualizacie"]


def test_a_project_that_already_has_it_is_left_alone(tmp_path):
    """Poistka, aby sa z opravy nestalo nariadenie dostavovať už hotové. Appka, ktorá kartu má, sa
    nemá čo dozvedieť o novej povinnosti."""
    src = tmp_path / "frontend" / "src"
    (src / "pages").mkdir(parents=True)
    (src / "pages" / "UpdatesPage.tsx").write_text("export default function UpdatesPage() {}\n", encoding="utf-8")
    (src / "router.tsx").write_text('<Route path="updates" element={<UpdatesPage />} />\n', encoding="utf-8")
    (src / "Sidebar.tsx").write_text('<a href="/updates">Aktualizácie</a>\n', encoding="utf-8")

    assert orchestrator.missing_icc_standards(tmp_path) == []


def test_a_project_without_a_frontend_is_not_asked_for_a_screen(tmp_path):
    """Čistá služba bez obrazoviek nemá kam kartu dať. Vymáhať ju od nej by bola tá istá chyba
    naopak — požiadavka, ktorú nemožno splniť."""
    assert orchestrator.missing_icc_standards(tmp_path) == []


def test_the_priprava_brief_names_the_missing_standard(db_session, monkeypatch, tmp_path):
    """Toto je jadro opravy: požiadavka vstúpi do stavby ZADANÍM, nie padnutou bránou."""
    from tests.test_orchestrator_v2_verifikacia import _make_version

    version, project = _make_version(db_session, project_dial="plna")
    (tmp_path / project.slug / "frontend" / "src").mkdir(parents=True)
    (tmp_path / project.slug / "frontend" / "src" / "App.tsx").write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path)

    brief = orchestrator._priprava_directive(db_session, version.id)

    assert "Aktualizácie" in brief, "zadanie nemenuje chýbajúci štandard — a Vizuál ho nemá odkiaľ vziať"
    assert "rozsahu" in brief.lower()
