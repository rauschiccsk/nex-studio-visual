"""Prevzatie existujúceho projektu — zadám priečinok, zvyšok si systém prečíta (ICCINT-85).

Prevzatie sa dovtedy robilo formulárom pre *zakladanie*: manažér musel vedieť a ručne prepísať porty,
adresu repozitára, typ aj spôsob prihlasovania. Keď sa pomýlil, evidencia začala tvrdiť niečo iné, než
je na disku — a projekt beží podľa disku, nie podľa evidencie. Pritom to všetko na disku UŽ JE.

Director 09.09.2026: *„zadám len názov projektu — napríklad nex-manager — a všetko ostatné urobí systém.“*

**Zásada, ktorú tieto stráže držia:** čo je na disku, to sa prečíta; čo sa prečítať nedá, na to sa opýta;
**nič sa nevymýšľa**. Uhádnutý údaj by vyzeral ako zistený, a to je horšie než prázdne políčko.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.services.project_adoption import discover


def _project(tmp_path: Path, name: str, compose: str | None = None, charter: str | None = None) -> Path:
    root = tmp_path / name
    root.mkdir()
    if charter is not None:
        (root / "CLAUDE.md").write_text(charter, encoding="utf-8")
    if compose is not None:
        (root / "docker-compose.yml").write_text(compose, encoding="utf-8")
    return root


# ── porty: tvary, ktoré sa v NAŠICH projektoch naozaj vyskytujú ────────────────


def test_ports_are_read_the_way_our_projects_actually_write_them(tmp_path) -> None:
    """⚠️ Zmerané 09.09.2026 na štyroch skutočných projektoch — a ani jeden tvar nestačí sám.

    nex-manager: služby ``backend``/``frontend``/``db``, vnútorné porty 8000/80/5432.
    nex-studio: tie isté mená, ale vnútorné porty 9176/9177 — podľa portu by sa rola neurčila.
    nex-payables: frontend sa volá ``web`` a väzba je ``"127.0.0.1:10220:8000"`` s poznámkou za ňou —
    podľa mena by sa rola neurčila a trojdielny tvar by rozobral zle.
    """
    root = _project(
        tmp_path,
        "vzor",
        compose="""
services:
  db:
    ports:
      - "127.0.0.1:10222:5432"   # len lokálne
  backend:
    ports:
      - "127.0.0.1:10220:8000"
  web:
    ports:
      - "10221:80"
""",
    )
    found = discover(root, "vzor")
    assert (found.backend_port, found.frontend_port, found.db_port) == (10220, 10221, 10222)


def test_a_service_whose_inner_port_is_unusual_is_still_recognised(tmp_path) -> None:
    """NEX Studio má vnútri porty 9176/9177 — rola sa určí podľa MENA služby, nie podľa čísla."""
    root = _project(
        tmp_path,
        "studio",
        compose="""
services:
  backend:
    ports: ["9176:9176"]
  frontend:
    ports: ["9177:9177"]
  db:
    ports: ["9178:5432"]
""",
    )
    found = discover(root, "studio")
    assert (found.backend_port, found.frontend_port, found.db_port) == (9176, 9177, 9178)


def test_a_binding_it_does_not_understand_is_skipped_not_guessed(tmp_path) -> None:
    """Nezrozumiteľný tvar sa preskočí a ohlási ako nedopovedaný. Vymyslené číslo portu by bolo
    horšie než prázdne políčko — evidencia by tvrdila niečo, čo na disku nie je."""
    root = _project(tmp_path, "divny", compose="services:\n  backend:\n    ports: ['tramtarara']\n")
    found = discover(root, "divny")
    assert found.backend_port is None
    assert any("port backendu" in u for u in found.unresolved)


# ── názov ─────────────────────────────────────────────────────────────────────


def test_the_name_comes_from_the_charter_not_from_package_json(tmp_path) -> None:
    """⚠️ ``package.json`` sa na názov použiť NEDÁ — zmerané: nex-manager aj nex-payables tam majú
    doslova ``"name": "frontend"``. Charta je jediné miesto, kde projekt nesie svoje ľudské meno."""
    root = _project(tmp_path, "nex-manager", charter="# NEX Manager — Univerzálny CLAUDE.md\n\nText.\n")
    assert discover(root, "nex-manager").name == "NEX Manager", "chvost je názov dokumentu, nie projektu"


def test_a_folder_without_a_charter_says_so(tmp_path) -> None:
    root = _project(tmp_path, "holy")
    found = discover(root, "holy")
    assert found.name is None
    assert any("názov projektu" in u for u in found.unresolved)


# ── čo sa prečítať nedá ───────────────────────────────────────────────────────


def test_the_login_method_is_never_guessed(tmp_path) -> None:
    """⚠️ Toto je jadro zásady. Spôsob prihlasovania sa z disku spoľahlivo zistiť nedá, a uhádnutá
    hodnota by v prehľade vyzerala ako zistený údaj — manažér by ju odklikol bez pozretia."""
    root = _project(tmp_path, "hocico", charter="# Hocičo\n")
    found = discover(root, "hocico")
    assert any("spôsob prihlasovania" in u for u in found.unresolved)


def test_it_says_where_each_value_came_from(tmp_path) -> None:
    """Prevzatie je zápis do evidencie, ktorý má sedieť s realitou — manažér musí PRED potvrdením
    vidieť, odkiaľ sa čo vzalo, nie sa to dozvedieť potom."""
    root = _project(
        tmp_path,
        "vzor2",
        charter="# Vzor\n",
        compose="services:\n  backend:\n    ports: ['10300:8000']\n",
    )
    found = discover(root, "vzor2")
    assert any("CLAUDE.md" in n for n in found.notes)
    assert any("docker-compose.yml" in n for n in found.notes)


# ── trasy ─────────────────────────────────────────────────────────────────────


def test_the_endpoints_are_mounted_where_the_cockpit_calls_them() -> None:
    """⚠️ Tá istá stráž, ktorá pri ICCINT-78 chýbala a stála jedno nasadenie navyše.

    A navyše poradie: ``/adoptable`` je konkrétna cesta a musí stáť PRED ``/{project_id}``, inak by ju
    tá druhá zožrala ako identifikátor projektu.
    """
    from backend.main import app

    paths = [getattr(route, "path", "") for route in app.routes]
    assert "/api/v1/projects/adoptable" in paths
    assert "/api/v1/projects/adoptable/{slug}" in paths
    assert paths.index("/api/v1/projects/adoptable") < paths.index("/api/v1/projects/{project_id}"), (
        "konkrétnu cestu zožerie tá s identifikátorom"
    )


@pytest.mark.parametrize("slug", ["nex-manager", "nex-payables", "nex-studio", "nex-studio-visual"])
def test_it_reads_the_real_projects_on_this_machine(slug: str) -> None:
    """⚠️ Skúška proti SKUTOČNÝM projektom, nie proti vymyslenému vzoru.

    Presne tie štyri čakajú na prevzatie. Vymyslený vzor by potvrdil len moju predstavu o tom, ako
    tie súbory vyzerajú — a práve tá predstava bola pri portoch dvakrát nesprávna. Mimo ANDROSu sa
    preskočí s uvedeným dôvodom, nie potichu prejde.
    """
    root = Path("/opt/projects") / slug
    if not (root / "docker-compose.yml").is_file():
        pytest.skip(f"{slug} nie je na tomto stroji — nekontrolované")
    found = discover(root, slug)
    assert found.name, f"{slug}: názov sa nepodarilo prečítať"
    assert found.backend_port and found.frontend_port and found.db_port, f"{slug}: porty sa nenašli"
    assert found.repo_url, f"{slug}: repozitár sa nenašiel"


# ── Vyhradený blok patrí tomu, komu je vyhradený (ICCINT-86) ──────────────────
#
# Zmerané 09.09.2026 pri PRVOM prevzatí NEX Managera: kokpit z disku správne prečítal porty
# 10210/10211/10212 a potom odmietol projekt založiť — „patrí do bloku 10210–10219, ktorý je pridelený
# inému systému — nex-manager“. Ten „iný systém“ bol ten istý projekt. Kontrola dostávala len číslo
# portu a identifikátor UPRAVOVANÉHO projektu (pri zakladaní žiadny), takže nemala ako vedieť, kto sa
# pýta. Netýkalo sa to len prevzatia — rovnako by dopadlo bežné založenie nex-payables.


def test_a_project_may_use_the_block_reserved_for_it(db_session, monkeypatch) -> None:
    """⚠️ Jadro ICCINT-86 — bez toho sa nex-manager nedá založiť ani prevziať.

    Pýta sa priamo evidencie, nie pomocnej funkcie vedľa nej: prvá verzia tejto stráže skúšala len
    porovnanie mien, takže keď som pri skúške vypol tú vetvu, ktorá naozaj padala, stráž zostala
    ZELENÁ. Merať treba verdikt, ktorý dostane zakladanie projektu.
    """
    from backend.services import port_registry as pr

    monkeypatch.setattr(pr, "get_host_taken_ports", lambda: {})
    monkeypatch.setattr(pr, "_bind_probe_says_taken", lambda port: False)
    monkeypatch.setattr(
        pr,
        "reserved_ranges_status",
        lambda db: pr.ReservedRangesStatus(
            configured=True,
            ranges=((10210, 10219),),
            owners=(((10210, 10219), "nex-manager"),),
        ),
    )

    verdict = pr.describe_port_availability(db_session, 10210, None, for_project=("nex-manager", "NEX Manager"))
    assert verdict.state == pr.FREE, "projekt nesmie byť odmietnutý z bloku, ktorý má napísaný na seba"

    cudzi = pr.describe_port_availability(db_session, 10210, None, for_project=("nex-payables",))
    assert cudzi.state == pr.TAKEN, "cudzí projekt sa do vyhradeného bloku dostať NESMIE"
    assert cudzi.source == "reserved"


def test_the_owner_is_matched_by_name_as_well_as_by_slug() -> None:
    """Vlastníci sú v evidencii voľný text a nie sú jednotní — zmerané: ``nex-manager``,
    ``nex-payables``, ale aj ``NEX Inbox``, ``NEX Automat``. Porovnanie musí zniesť oboje."""
    from backend.services.port_registry import _same_holder

    assert _same_holder("NEX Inbox", ("nex-inbox", "NEX Inbox")) is True
    assert _same_holder("NEX Automat", ("nex-automat", "NEX Automat")) is True


def test_a_block_reserved_for_somebody_else_stays_shut() -> None:
    """⚠️ Zhovievavosť porovnania nesmie zájsť tak ďaleko, že pustí cudzí projekt do cudzieho bloku.

    To by bola horšia chyba než tá, ktorú opravujeme: dvaja by si sadli na tie isté porty a prišlo by
    sa na to až tým, že jednému prestane appka bežať.
    """
    from backend.services.port_registry import _same_holder

    assert _same_holder("nex-manager", ("nex-payables", "NEX Payables")) is False
    assert _same_holder("icc-website (isnex.ai)", ("nex-manager", "NEX Manager")) is False
    assert _same_holder(None, ("nex-manager",)) is False
    assert _same_holder("nex-manager", ()) is False


def test_the_create_route_says_who_is_asking() -> None:
    """Stráž proti tichému návratu: keby trasa prestala identitu odovzdávať, služba by zase nemala ako
    vedieť, kto sa pýta — a zlyhalo by to až pri zakladaní, nie tu."""
    import inspect

    from backend.api.routes import projects as routes

    src = inspect.getsource(routes._validate_ports)
    assert "for_project=" in src, "trasa neodovzdáva evidencii, kto sa pýta"
    assert "slug" in src and "name" in src, "identita sa musí skladať z názvu aj skratky"


# ── Adresa repozitára v tvare, ktorý zakladanie prijme ────────────────────────
#
# ⚠️ Stálo to jedno nasadenie navyše. Prvá verzia prevzatia vracala adresu tak, ako ju vypísal git —
# ``https://github.com/rauschiccsk/nex-manager.git`` — lenže zakladanie čaká ``owner/repo``. Tak to má
# aj v popise poľa a tak to majú uložené všetky tri existujúce projekty. Prevzatie skončilo na
# „Invalid repository format“ a Manažér videl len holé „Unprocessable Entity“.


def test_the_repository_is_handed_over_in_the_shape_the_create_accepts() -> None:
    from backend.services.project_adoption import _owner_repo

    assert _owner_repo("https://github.com/rauschiccsk/nex-manager.git") == "rauschiccsk/nex-manager"
    assert _owner_repo("git@github.com:rauschiccsk/nex-payables.git") == "rauschiccsk/nex-payables"
    assert _owner_repo("https://github.com/rauschiccsk/nex-studio") == "rauschiccsk/nex-studio"


def test_an_address_it_does_not_understand_is_left_empty_not_mangled() -> None:
    """Prázdne pole a otázka sú lepšie než adresa v tvare, ktorý zakladanie odmietne — presne to sa
    stalo a nedalo sa z toho nič vyčítať."""
    from backend.services.project_adoption import _owner_repo

    assert _owner_repo("nezmysel") is None
    assert _owner_repo("") is None


def test_the_real_projects_hand_over_a_shape_the_create_accepts() -> None:
    """Skúška proti SKUTOČNÝM projektom aj proti skutočnej kontrole zakladania — vymyslený vzor by
    potvrdil len moju predstavu o tvare, a práve tá predstava bola nesprávna."""
    for slug in ("nex-manager", "nex-payables", "nex-studio", "nex-studio-visual"):
        root = Path("/opt/projects") / slug
        if not (root / ".git").exists():
            pytest.skip(f"{slug} nie je na tomto stroji — nekontrolované")
        repo = discover(root, slug).repo_url
        assert repo, f"{slug}: adresa repozitára sa nenašla"
        # Presne tá podmienka, na ktorej zakladanie projektu padlo (github_validation.py).
        assert repo.count("/") == 1, f"{slug}: {repo!r} — zakladanie čaká 'owner/repo'"
        owner, name = repo.split("/")
        assert owner and name


# ── Do prevzatého projektu sa nescaffolduje (ICCINT-85) ───────────────────────
#
# ⚠️ Zmerané 09.09.2026 pri TREŤOM pokuse o prevzatie NEX Managera:
#   „Filesystem bootstrap failed: ERROR: /opt/projects/nex-manager/CLAUDE.md already exists“
#
# Príznak ``adopted`` v systéme bol už predtým a chartre ho rešpektovali — ale zakladanie napriek nemu
# spúšťalo ``init.sh`` VŽDY. Prevzatie cez kokpit teda nikdy nemohlo prejsť; existovala len jeho polovica.


def test_nothing_is_unpacked_into_a_project_that_is_already_there() -> None:
    """⚠️ Jadro: hotový projekt sa nescaffolduje. Jeho vlastná poistka to správne odmietala."""
    import inspect

    from backend.api.routes import projects as routes

    src = inspect.getsource(routes.create_project)
    assert "if not adopting:\n                invoke_init_script" in src, (
        "scaffold sa pri prevzatí spúšťa — init.sh sa zastaví na existujúcom CLAUDE.md"
    )


def test_an_adopted_project_is_not_pushed_over() -> None:
    """Repozitár prevzatého projektu existuje a je odoslaný. Odosielať doň miestny stav by znamenalo
    tlačiť do cudzej histórie niečo, o čo nikto nežiadal."""
    import inspect

    from backend.api.routes import projects as routes

    src = inspect.getsource(routes.create_project)
    assert "not adopting  #" in src.split("stage4_should_run")[1][:200], (
        "odoslanie do repozitára sa pri prevzatí nevynecháva"
    )


def test_an_adopted_project_keeps_its_own_ci_and_is_told_so() -> None:
    """⚠️ Toto je viac než upratovanie: skúšobné spustenie po scaffolde robí ``docker compose down -v``,
    čo by ŽIVÉMU projektu zmazalo databázu. A vynechanie sa nesmie zamlčať — Manažér musí vedieť, že
    prevzatý projekt si CI a ochranu vetvy drží vlastnú.
    """
    import inspect

    from backend.api.routes import projects as routes

    src = inspect.getsource(routes.create_project)
    assert "if adopting\n            else run_post_scaffold_steps(" in src, (
        "kroky po scaffolde sa pri prevzatí spúšťajú"
    )
    assert "Projekt bol prevzatý" in src, "vynechanie sa Manažérovi nehovorí"


# ── Prevzatému projektu sa verzia nevymýšľa (ICCINT-89) ──────────────────────
#
# ⚠️ Zmerané 09.09.2026 na NEX Managerovi hneď po prevzatí: appka bežala ako 1.0.99 a v
# docs/specs/versions/ mala v0.1.0 aj v1.0.0 s poznámkami k vydaniu — kokpit ukazoval jedinú
# „plánovanú“ 0.1.0 a ponúkal „Pridať verziu 0.2.0“.
#
# A nebolo to len nepekné číslo: priečinok docs/specs/versions/v0.1.0/ v tom projekte UŽ EXISTOVAL
# a mal vlastný obsah. Keby sa tá vymyslená verzia rozbehla, písalo by sa do cudzích dokumentov.


def test_the_version_the_project_says_about_itself_is_read(tmp_path) -> None:
    """Číta sa z priečinkov, ktoré si projekt sám vedie — nie z čísla, ktoré mu niekto pridelí."""
    root = _project(tmp_path, "s-historiou", charter="# S históriou\n")
    for v in ("v0.1.0", "v0.9.3", "v1.0.0"):
        (root / "docs" / "specs" / "versions" / v).mkdir(parents=True)

    assert discover(root, "s-historiou").latest_version == "1.0.0"


def test_versions_are_compared_as_numbers_not_as_text(tmp_path) -> None:
    """⚠️ Podľa abecedy je „0.9.0“ väčšie než „0.10.0“ — a prevzatý projekt by tak nadviazal na staršiu
    verziu, než v skutočnosti má."""
    root = _project(tmp_path, "cisla", charter="# Čísla\n")
    for v in ("v0.9.0", "v0.10.0"):
        (root / "docs" / "specs" / "versions" / v).mkdir(parents=True)

    assert discover(root, "cisla").latest_version == "0.10.0"


def test_a_project_without_version_folders_says_so_instead_of_inventing(tmp_path) -> None:
    root = _project(tmp_path, "bez-historie", charter="# Bez\n")
    found = discover(root, "bez-historie")

    assert found.latest_version is None
    assert any("posledná verzia" in u for u in found.unresolved)


def test_a_folder_that_is_not_a_version_number_is_ignored(tmp_path) -> None:
    """V tom priečinku býva aj `release-dates.json` a podobné veci — nie sú to verzie."""
    root = _project(tmp_path, "zmes", charter="# Zmes\n")
    (root / "docs" / "specs" / "versions" / "v1.0.0").mkdir(parents=True)
    (root / "docs" / "specs" / "versions" / "koncepty").mkdir()

    assert discover(root, "zmes").latest_version == "1.0.0"


def test_the_real_projects_report_a_version_that_matches_their_own_folders() -> None:
    """Skúška proti SKUTOČNÝM projektom — vymyslený vzor by potvrdil len moju predstavu o tom, ako tie
    priečinky vyzerajú."""
    import re as _re

    for slug in ("nex-manager", "nex-studio"):
        root = Path("/opt/projects") / slug
        versions = root / "docs" / "specs" / "versions"
        if not versions.is_dir():
            pytest.skip(f"{slug} nie je na tomto stroji — nekontrolované")
        found = discover(root, slug).latest_version
        assert found, f"{slug}: verzia sa nenašla, hoci priečinky má"
        assert (versions / f"v{found}").is_dir(), f"{slug}: {found} neexistuje ako priečinok"
        assert _re.fullmatch(r"\d+\.\d+\.\d+", found)
