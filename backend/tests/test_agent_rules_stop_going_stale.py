"""Pravidlá agenta prestávajú zastarávať (ICCINT-51).

02.09.2026: do šablóny charty pribudlo nové pravidlo pre rýchlu dráhu. Overenie ukázalo, že sa
nedostalo do ŽIADNEHO existujúceho projektu — charta sa píše raz, pri založení, a engine ju pri
každom spustení agenta číta z tej zamrznutej kópie.

Namerané vtedy: šesť chárt na disku, každá z iného dňa (06.07. až 21.08.), dĺžky 230 až 358 riadkov,
šablóna 304. Agent nex-payables nepoznal takmer dva mesiace opráv — vrátane tých, ktoré vznikli
práve preto, že sa niečo pokazilo.

Podstatné je, čo z toho plynulo dopredu: každé ďalšie zlepšenie pravidiel by sa mlčky zastavilo
pred existujúcimi projektmi. Príčina by sa opravila správne — a oprava by sa nedoručila.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from backend.services import create_project_postscaffold as pps


def _project(tmp_path: Path, charter_text: str = "STARÉ PRAVIDLÁ\n") -> Path:
    root = tmp_path / "projekt"
    for role in ("ai-agent", "auditor"):
        d = root / ".claude" / "agents" / role
        d.mkdir(parents=True)
        (d / "CLAUDE.md").write_text(charter_text, encoding="utf-8")
    return root


def test_a_stale_charter_is_rewritten_from_the_template(tmp_path) -> None:
    root = _project(tmp_path)
    refreshed = pps.refresh_v2_agent_charters(root, "projekt")

    assert refreshed == 2, "obnovili sa obe roly"
    text = (root / ".claude" / "agents" / "ai-agent" / "CLAUDE.md").read_text(encoding="utf-8")
    assert "STARÉ PRAVIDLÁ" not in text
    # Charta = spoločný základ + rolová šablóna, presne ako pri založení.
    base = (pps.NEX_STUDIO_TEMPLATES / pps._AGENT_SHARED_BASE).read_text(encoding="utf-8").rstrip()
    assert text.startswith(base)


def test_the_new_fast_lane_rule_actually_arrives(tmp_path) -> None:
    """Konkrétny prípad, ktorý tiket spustil: pravidlo bolo v šablóne a v projektoch nie."""
    root = _project(tmp_path)
    pps.refresh_v2_agent_charters(root, "projekt")
    text = (root / ".claude" / "agents" / "ai-agent" / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Rýchla dráha — kde končí" in text


def test_an_already_current_charter_is_left_alone(tmp_path) -> None:
    """Druhý beh nemá čo prepisovať — inak by sa čas úpravy menil pri každej stavbe."""
    root = _project(tmp_path)
    assert pps.refresh_v2_agent_charters(root, "projekt") == 2
    assert pps.refresh_v2_agent_charters(root, "projekt") == 0


def test_a_missing_project_never_sinks_the_build(tmp_path) -> None:
    """Agent by v najhoršom prípade bežal podľa starších pravidiel — čo je dnešný stav, nie zhoršenie.
    Zhodiť kvôli tomu stavbu by bolo horšie než tá zastaranosť."""
    assert pps.refresh_v2_agent_charters(tmp_path / "niet-ho", "niet-ho") == 0


def test_an_adopted_project_is_skipped_by_the_caller() -> None:
    """TOTO je tá stráž, na ktorej všetko stojí.

    ``provision_v2_agent_charters`` prepisuje rolové charty BEZ OHĽADU na ``adopted`` — ten príznak
    v nej riadi len upratovanie adresárov v1. Obnova by teda prevzatému projektu prepísala jeho
    vlastné pravidlá, a tie sú podľa CLAUDE.md §1 jeho. Stráž musí byť na strane volajúceho.
    """
    from backend.services import orchestrator

    src = inspect.getsource(orchestrator.apply_action)
    assert "refresh_v2_agent_charters" in src, "štart stavby chartu neobnovuje"
    assert "not _proj.adopted" in src, "obnova sa spúšťa aj na prevzatom projekte"


def test_the_project_remembers_it_was_adopted() -> None:
    """Hodnota sa dovtedy vypočítala pri zakladaní, použila raz a zabudla — engine ju pri spúšťaní
    agenta nemal odkiaľ vziať, takže tie dva prípady nevedel rozlíšiť."""
    from backend.db.models.projects import Project

    assert "adopted" in Project.__table__.columns


def test_creating_a_project_writes_the_flag_down() -> None:
    import backend.api.routes.projects as routes

    src = inspect.getsource(routes)
    assert "project.adopted = not scaffolded_here" in src
