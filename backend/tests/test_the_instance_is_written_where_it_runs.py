"""Predpis a nastavenia sa zapíšu na stroj, kde inštalácia beží (ICCINT-151).

**Čo tomu predchádzalo.** Kokpit vykresľoval predpis len u seba. Poistka proti rozchodu (v4.40.6)
potom porovnala svoj súbor so súborom na cieli, našla rozdiel a nasadenie zastavila — s radou
*„najprv jeho stav prevezmi"*, hoci prevzatie práve prebehlo. Zmerané 24.09.2026 na MÁGERSTAVE:

    predpis na cieli je iný než ten, ktorý drží kokpit — líšia sa služby: backend, db, frontend,
    migrate. Nasadenie by cieľ prepísalo; najprv jeho stav prevezmi.

Kokpit by teda zacyklil sám seba. A súbor na cieli je navyše to, čo vidí každý, kto sa na ten server
pozrie — keby starol, budúci ručný zásah by vychádzal z nesprávneho predpisu.

⚠️ Čo tieto stráže NEDOVOLIA zmeniť: obsah súborov ide na cieľ cez ŠTANDARDNÝ VSTUP (v ``.env`` sú
tajomstvá a argumenty procesu vidí na cudzom stroji ktokoľvek cez ``ps``), tajomstvá sa pri
nasadzovaní na cudzí stroj čítajú Z CIEĽA (miestna kópia môže niesť iné heslo a zákazník by prišiel
o prístup k vlastnej databáze), a zlyhanie zápisu sa nesmie prehltnúť.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from backend.services import remote_instance
from backend.services import uat_provisioner as P

ZDROJ = """
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: demo
      POSTGRES_PASSWORD: x
      POSTGRES_DB: demo
  backend:
    build:
      context: .
    env_file: [.env]
  frontend:
    build:
      context: ./frontend
"""


def _projekt(tmp_path: Path) -> None:
    p = tmp_path / "projects" / "nex-manager"
    p.mkdir(parents=True)
    (p / "docker-compose.yml").write_text(ZDROJ, encoding="utf-8")


def _provision(tmp_path, **kw):
    _projekt(tmp_path)
    return P.provision_uat(
        "nex-manager",
        "mager-prod",
        projects_root=tmp_path / "projects",
        uat_root=tmp_path / "uat",
        prod_root=tmp_path / "customers",
        environment="prod",
        customer_slug="mager",
        app="manager",
        full_project_slug="nex-manager",
        **kw,
    )


class _Ciel:
    """Náhrada cudzieho stroja: pamätá si, čo sa naň zapísalo a čo sa z neho čítalo."""

    def __init__(self, subory: dict[str, str] | None = None, chyba_zapisu: str | None = None):
        self.subory = dict(subory or {})
        self.chyba_zapisu = chyba_zapisu
        self.zapisane: dict[str, tuple[str, int]] = {}
        self.citane: list[str] = []
        self.hostitelia: list[str] = []

    def read_text(self, instance_dir, filename, *, deploy_host, timeout=120):
        self.citane.append(filename)
        self.hostitelia.append(deploy_host)
        return self.subory.get(filename), None

    def write_files(self, instance_dir, files, *, deploy_host, timeout=180):
        self.hostitelia.append(deploy_host)
        if self.chyba_zapisu:
            return self.chyba_zapisu
        self.zapisane.update(files)
        return None


# ── 1. zapisuje sa aj na cieľ ─────────────────────────────────────────────────


def test_the_description_and_settings_land_on_the_target(tmp_path, monkeypatch) -> None:
    """⚠️ Jadro veci: bez tohto zostane predpis len tu a poistka proti rozchodu zastaví nasadenie."""
    ciel = _Ciel()
    monkeypatch.setattr(P, "remote_instance", ciel)

    _provision(tmp_path, deploy_host="mager")

    assert set(ciel.zapisane) == {"docker-compose.yml", ".env"}
    assert set(ciel.hostitelia) == {"mager"}


def test_the_settings_keep_their_private_permissions_on_the_target(tmp_path, monkeypatch) -> None:
    """``.env`` nesie tajomstvá — na cudzom stroji musí byť rovnako zavretý ako tu."""
    ciel = _Ciel()
    monkeypatch.setattr(P, "remote_instance", ciel)

    _provision(tmp_path, deploy_host="mager")

    assert ciel.zapisane[".env"][1] == 0o600
    assert ciel.zapisane["docker-compose.yml"][1] == 0o664


def test_nothing_goes_to_the_target_when_it_runs_here(tmp_path, monkeypatch) -> None:
    """Inštalácia na tomto stroji sa nikam neposiela — inak by sme cez sieť písali sami sebe."""
    ciel = _Ciel()
    monkeypatch.setattr(P, "remote_instance", ciel)

    _provision(tmp_path)

    assert ciel.zapisane == {} and ciel.citane == []


def test_a_failed_write_is_not_swallowed(tmp_path, monkeypatch) -> None:
    """⚠️ Keby sa prehltlo, na cieli zostane starý predpis a zastaví to až poistka o krok neskôr —
    s hláškou o rozchode, ktorý v skutočnosti spôsobilo toto zlyhanie."""
    monkeypatch.setattr(P, "remote_instance", _Ciel(chyba_zapisu="cieľ odmietol zápis"))

    with pytest.raises(ValueError, match="cieľ odmietol zápis"):
        _provision(tmp_path, deploy_host="mager")


# ── 2. tajomstvá sa čítajú Z CIEĽA ────────────────────────────────────────────


def test_the_existing_secrets_are_read_from_the_target_not_from_here(tmp_path, monkeypatch) -> None:
    """⚠️ Najdrahšia stráž v tomto súbore. Miestna kópia môže niesť iné heslo než ostrá inštalácia;
    prepísať ju ním znamená odrezať zákazníka od jeho vlastnej databázy — a to Director zakázal
    výslovne: *„žiadna aktualizácia nemôže vymazať existujúce heslá."*"""
    ciel = _Ciel({".env": "POSTGRES_PASSWORD=heslo-ostrej-prevadzky\n"})
    monkeypatch.setattr(P, "remote_instance", ciel)
    # Miestna kópia nesie INÉ heslo. Keby sa čítala ona, prejde do vykresleného nastavenia.
    miestny = tmp_path / "customers" / "mager" / "nex-manager"
    miestny.mkdir(parents=True)
    (miestny / ".env").write_text("POSTGRES_PASSWORD=heslo-z-miestnej-kopie\n", encoding="utf-8")

    # ``allow_overwrite`` je tu len preto, aby sa stráž dostala k svojej otázke: miestna kópia je
    # ručne písaná a inak by ju zastavila iná poistka. Skúma sa, Z KTORÉHO stroja sa vzalo heslo.
    _provision(tmp_path, deploy_host="mager", allow_overwrite=True)

    zapisane_nastavenia = ciel.zapisane[".env"][0]
    assert "heslo-ostrej-prevadzky" in zapisane_nastavenia
    assert "heslo-z-miestnej-kopie" not in zapisane_nastavenia, "heslo sa vzalo z nesprávneho stroja"
    assert ".env" in ciel.citane, "nastavenia na cieli sa vôbec nečítali"


def test_the_secrets_are_read_out_of_plain_text() -> None:
    """Dvojča k čítaniu zo súboru — cieľ nám nastavenia podáva ako text, nie ako cestu."""
    tajomstva = P.secrets_from_env_text("POSTGRES_PASSWORD=x\nAPP_SECRET=y\nDEBUG=1\n")

    assert tajomstva == {"POSTGRES_PASSWORD": "x", "APP_SECRET": "y"}
    assert P.secrets_from_env_text(None) == {}


# ── 3. ručne písanú inštaláciu na cieli neprepíše ─────────────────────────────


def test_a_hand_authored_description_on_the_target_is_refused(tmp_path, monkeypatch) -> None:
    """Poistka na domácom priečinku o súbore na serveri zákazníka nevie nič — tam musí platiť tiež."""
    ciel = _Ciel({"docker-compose.yml": "name: rucne-pisane\nservices: {}\n"})
    monkeypatch.setattr(P, "remote_instance", ciel)

    with pytest.raises(P.HandAuthoredDeploymentError):
        _provision(tmp_path, deploy_host="mager")


def test_adoption_may_overwrite_it_deliberately(tmp_path, monkeypatch) -> None:
    """Prevzatie je práve to vedomé rozhodnutie, ktoré tú poistku raz odomkne."""
    ciel = _Ciel({"docker-compose.yml": "name: rucne-pisane\nservices: {}\n"})
    monkeypatch.setattr(P, "remote_instance", ciel)

    _provision(tmp_path, deploy_host="mager", allow_overwrite=True)

    assert "docker-compose.yml" in ciel.zapisane


# ── 4. ako to ide na cieľ ─────────────────────────────────────────────────────


def test_the_content_never_travels_in_the_command_line() -> None:
    """⚠️ V ``.env`` sú tajomstvá a argumenty procesu vidí na cudzom stroji ktokoľvek cez ``ps``."""
    cmd = remote_instance._write_cmd(Path("/opt/customers/mager/nex-manager"))

    assert "-i" in cmd, "bez štandardného vstupu by obsah musel ísť do príkazu"
    assert any("tar x" in c for c in cmd)
    assert not any("PASSWORD" in c or "base64" in c for c in cmd)


def test_the_payload_carries_the_permissions_and_the_owner() -> None:
    """Práva nesie samotný balík — nie príkaz po ňom, ktorý by sa dal zabudnúť."""
    payload = remote_instance._tar_payload({".env": ("TAJNE=1\n", 0o600)})

    with tarfile.open(fileobj=io.BytesIO(payload)) as tar:
        info = tar.getmember(".env")
        assert info.mode == 0o600
        assert (info.uid, info.gid) == (remote_instance.UID, remote_instance.GID)
        assert tar.extractfile(info).read() == b"TAJNE=1\n"
        # Bez dátumu by súbor na cieli niesol 1.1.1970 a pri porovnávaní „čo je novšie" by vždy
        # prehral — aj keď je z nich najčerstvejší. Zmerané na MAGERi 24.09.2026.
        assert info.mtime > 1_700_000_000, "zapísaný súbor by na cieli mal dátum z roku 1970"


def test_writing_may_create_the_folder_but_reading_never_may() -> None:
    """Zápis priečinok vyrobiť MÁ (prvé nasadenie), čítanie po sebe nesmie nechať nič."""
    zapis = remote_instance._write_cmd(Path("/opt/customers/mager/nex-manager"))
    citanie = remote_instance._read_cmd(Path("/opt/customers/mager/nex-manager"), ".env")

    assert "-v" in zapis and any("mkdir -p /target" in c for c in zapis)
    assert "-v" not in citanie
    assert any(c.endswith(",readonly") for c in citanie)


def test_a_missing_file_on_the_target_is_not_an_error(monkeypatch) -> None:
    """Pred prvým nasadením tam nie je nič — to je bežný stav, nie porucha."""

    class _Vysledok:
        returncode = 1
        stdout = ""
        stderr = "cat: can't open '/target/.env': No such file or directory"

    monkeypatch.setattr(remote_instance.subprocess, "run", lambda *a, **k: _Vysledok())
    text, chyba = remote_instance.read_text(Path("/x"), ".env", deploy_host="mager")

    assert (text, chyba) == (None, None)


def test_an_unreachable_target_is_an_error(monkeypatch) -> None:
    """Nedostupný cieľ je opak prázdneho — vtedy o ňom nevieme nič a nesmie sa prepisovať."""

    class _Vysledok:
        returncode = 255
        stdout = ""
        stderr = "ssh: connect to host 100.109.5.21 port 22: Connection refused"

    monkeypatch.setattr(remote_instance.subprocess, "run", lambda *a, **k: _Vysledok())
    text, chyba = remote_instance.read_text(Path("/x"), ".env", deploy_host="mager")

    assert text is None and chyba and "nedal prečítať" in chyba


def test_the_target_machine_is_named_in_the_docker_environment() -> None:
    assert remote_instance.docker_env("mager")["DOCKER_HOST"] == "ssh://mager"
    assert remote_instance.docker_env(None) is None
    assert remote_instance.docker_env("   ") is None


# ── 5. prevzatie vykresľuje, nepovyšuje ───────────────────────────────────────


def test_adoption_keeps_the_instance_identity_and_still_gets_a_buildable_image(tmp_path, monkeypatch) -> None:
    """⚠️ OBRÁTENÉ 25.09.2026 — a hovorím to nahlas, lebo je to druhá obrátená stráž za dva dni.

    24.09. tu stálo, že prevzatie musí inštaláciu VYKRESLIŤ zo zdrojového projektu. Napísal som to
    po tom, čo prevzatie MÁGERSTAV Managera zlyhalo na „obrazoch, ktoré sa nemajú odkiaľ vziať".
    Prešlo to, lebo stavba Managera je projektu podobná.

    O deň neskôr zmerané na NEX Inboxe: to isté by mu prepísalo mená služieb (`postgres`→`db`) aj
    meno úložiska (`postgres-data`→`inbox_dev_pg_data`). Appka by naštartovala s PRÁZDNOU databázou
    a faktúry zákazníka by zostali vedľa, nedotknuté, ale bez odkazu.

    Skutočná príčina bola inde: stavba sa párovala len podľa mena obrazu, a ručne písaná inštalácia
    si ho zvolila sama — `nexmanager-backend` oproti `nex-manager-backend`, rozdiel jedna pomlčka.

    Prevzatie teda ide tou istou cestou ako každé nasadenie: inštalácii ponechá jej vlastné mená a
    úložiská, a obraz aj tak musí byť z čoho postaviť.
    """
    pripnute = (
        "name: mager-manager\n"
        "services:\n"
        "  postgres:\n    image: postgres:16-alpine\n"
        "  backend:\n    image: nexmanager-backend:1.0.0\n"
        "volumes:\n  postgres-data: null\n"
    )
    ciel = _Ciel({"docker-compose.yml": pripnute, ".env": "POSTGRES_PASSWORD=x\n"})
    monkeypatch.setattr(P, "remote_instance", ciel)

    _provision(tmp_path, deploy_host="mager", allow_overwrite=True)

    zapisany = ciel.zapisane["docker-compose.yml"][0]
    assert "postgres:" in zapisany and "postgres-data" in zapisany, "prevzatie prepísalo mená inštalácie"
    assert "  db:" not in zapisany, "služba sa premenovala podľa projektu — appka by stratila adresu k dátam"
    assert "build:" in zapisany, "obraz by sa nemal odkiaľ vziať"
    assert "nexmanager-backend:1.0.0" not in zapisany, "verzia sa nepovýšila"


def test_an_ordinary_redeploy_still_only_changes_the_version(tmp_path, monkeypatch) -> None:
    """Poistka proti tomu, aby oprava zrušila pravidlo z ICCINT-133. Do inštalácie, ktorú kokpit už
    spravuje, sa nasadzuje VERZIA, nie stavba — prestavba raz zhodila Inbox na sedem minút."""
    nase = (
        f"# {P.GENERATED_BY_MARKER}\nname: mager-manager\n"
        "services:\n  backend:\n    image: mager-manager-backend:1.0.0\n"
    )
    ciel = _Ciel({"docker-compose.yml": nase, ".env": "POSTGRES_PASSWORD=x\n"})
    monkeypatch.setattr(P, "remote_instance", ciel)
    monkeypatch.setattr(P, "_docker_image_exists", lambda _o: True)

    _provision(tmp_path, deploy_host="mager")

    zapisany = ciel.zapisane["docker-compose.yml"][0]
    assert "mager-manager-backend" in zapisany, "inštalácia sa prestavala podľa zdroja"


def test_the_version_bump_reads_the_target_not_the_local_copy(tmp_path, monkeypatch) -> None:
    """⚠️ Povýšenie verzie čítalo MIESTNU kópiu. Na MÁGERSTAVE to bola kópia zo 14.07., kým na cieli
    ležal predpis z 23.09. — tá istá trieda chyby ako pri tajomstvách a faktoch, len na treťom mieste."""
    na_cieli = (
        f"# {P.GENERATED_BY_MARKER}\nname: mager-manager\n"
        "services:\n  backend:\n    image: mager-manager-backend:1.0.0\n    container_name: zo-septembra\n"
    )
    ciel = _Ciel({"docker-compose.yml": na_cieli, ".env": "POSTGRES_PASSWORD=x\n"})
    monkeypatch.setattr(P, "remote_instance", ciel)
    monkeypatch.setattr(P, "_docker_image_exists", lambda _o: True)
    miestny = tmp_path / "customers" / "mager" / "nex-manager"
    miestny.mkdir(parents=True)
    (miestny / "docker-compose.yml").write_text(
        f"# {P.GENERATED_BY_MARKER}\nname: mager-manager\n"
        "services:\n  backend:\n    image: mager-manager-backend:1.0.0\n    container_name: z-jula\n",
        encoding="utf-8",
    )
    (miestny / ".env").write_text("POSTGRES_PASSWORD=x\n", encoding="utf-8")

    _provision(tmp_path, deploy_host="mager")

    zapisany = ciel.zapisane["docker-compose.yml"][0]
    assert "zo-septembra" in zapisany, "povýšenie vychádzalo zo starej miestnej kópie"
    assert "z-jula" not in zapisany


def test_the_local_working_copy_of_the_settings_comes_from_the_target(tmp_path, monkeypatch) -> None:
    """⚠️ Túto stráž si vypýtala mutácia, ktorá prešla nezachytená (24.09.2026).

    Miestny `.env` nie je ozdoba: `docker compose` ho číta na TOMTO stroji, keď spúšťa inštaláciu na
    cudzom. Keby zostal starý, kontajnery na cieli by dostali hodnoty z miestnej kópie — teda presne
    ten rozchod, ktorý celý tento tiket rieši.
    """
    nase = f"# {P.GENERATED_BY_MARKER}\nname: mager-manager\nservices:\n  backend:\n    image: a:1\n"
    ciel = _Ciel({"docker-compose.yml": nase, ".env": "POSTGRES_PASSWORD=x\nZNACKA=zo-septembra\n"})
    monkeypatch.setattr(P, "remote_instance", ciel)
    monkeypatch.setattr(P, "_docker_image_exists", lambda _o: True)
    miestny = tmp_path / "customers" / "mager" / "nex-manager"
    miestny.mkdir(parents=True)
    (miestny / "docker-compose.yml").write_text(nase, encoding="utf-8")
    (miestny / ".env").write_text("POSTGRES_PASSWORD=x\nZNACKA=z-jula\n", encoding="utf-8")

    _provision(tmp_path, deploy_host="mager")

    miestne_nastavenia = (miestny / ".env").read_text(encoding="utf-8")
    assert "zo-septembra" in miestne_nastavenia, "miestna pracovná kópia zostala pri starých hodnotách"
    assert "z-jula" not in miestne_nastavenia


# ── 6. záloha patrí tam, kde býva originál ────────────────────────────────────


class _CielSoZapisom(_Ciel):
    """Cieľ, ktorý si zapísané súbory aj pamätá ako ďalší obsah — aby sa dalo čítať späť."""

    def write_files(self, instance_dir, files, *, deploy_host, timeout=180):
        chyba = super().write_files(instance_dir, files, deploy_host=deploy_host, timeout=timeout)
        if chyba is None:
            for meno, (obsah, _prava) in files.items():
                self.subory[meno] = obsah
        return chyba


def test_the_hand_written_files_are_set_aside_on_the_target(monkeypatch) -> None:
    """⚠️ Zmerané na MÁGERSTAVE 24.09.2026: odkladanie prebehlo len na stroji kokpitu, takže na cieli
    sa živý predpis prepísal BEZ zálohy — a tu sa odložila júlová kópia, ktorá už týždne nebola tým
    živým. Poistka nesplnila ani jedno."""
    from backend.services import instance_adoption as ia

    ciel = _CielSoZapisom({"docker-compose.yml": "name: rucne\n", ".env": "TAJNE=1\n"})
    monkeypatch.setattr(ia, "remote_instance", ciel)

    odlozene = ia._set_aside_on_target(Path("/opt/customers/mager/nex-manager"), "mager")

    assert sorted(odlozene) == [".env.pre-nex-studio", "docker-compose.yml.pre-nex-studio"]
    assert ciel.zapisane[".env.pre-nex-studio"][0] == "TAJNE=1\n"
    assert ciel.zapisane[".env.pre-nex-studio"][1] == 0o600, "záloha nastavení musí zostať zavretá"
    assert ciel.zapisane["docker-compose.yml.pre-nex-studio"][0] == "name: rucne\n"


def test_an_existing_backup_on_the_target_is_never_overwritten(monkeypatch) -> None:
    """Druhé prevzatie by inak uložilo vedľa NAŠU vlastnú kópiu a originál by zmizol (ICCINT-87)."""
    from backend.services import instance_adoption as ia

    ciel = _CielSoZapisom(
        {
            "docker-compose.yml": "name: nase\n",
            "docker-compose.yml.pre-nex-studio": "name: povodne\n",
        }
    )
    monkeypatch.setattr(ia, "remote_instance", ciel)

    odlozene = ia._set_aside_on_target(Path("/opt/customers/mager/nex-manager"), "mager")

    assert odlozene == []
    assert ciel.zapisane == {}, "existujúca záloha sa prepísala"


def test_an_unreadable_target_stops_the_adoption(monkeypatch) -> None:
    """⚠️ Ticho preskočená poistka je horšia než žiadna: prevzatie by pokračovalo BEZ zálohy a
    človek by sa to dozvedel až vtedy, keď by sa chcel vracať."""
    from backend.services import instance_adoption as ia

    class _Nedostupny:
        def read_text(self, *_a, **_k):
            return None, "na cieľový stroj sa nedalo pozrieť: spojenie odmietnuté"

    monkeypatch.setattr(ia, "remote_instance", _Nedostupny())

    with pytest.raises(OSError, match="odkladanie ručných súborov zlyhalo"):
        ia._set_aside_on_target(Path("/opt/customers/mager/nex-manager"), "mager")


def test_the_backup_goes_to_the_machine_the_instance_lives_on(tmp_path, monkeypatch) -> None:
    """⚠️ Túto stráž si vypýtala mutácia, ktorá prešla nezachytená (25.09.2026): stráže volali
    vzdialenú vetvu priamo, takže samotné ROZHODNUTIE — kam sa odkladá — nestrážil nikto. A pritom
    je to celá podstata opravy."""
    from backend.services import instance_adoption as ia

    ciel = _CielSoZapisom({"docker-compose.yml": "name: rucne\n"})
    monkeypatch.setattr(ia, "remote_instance", ciel)
    miestny = tmp_path / "mager" / "nex-manager"
    miestny.mkdir(parents=True)
    (miestny / "docker-compose.yml").write_text("name: miestna-kopia\n", encoding="utf-8")

    odlozene = ia.set_aside_hand_authored(miestny, deploy_host="mager")

    assert odlozene == ["docker-compose.yml.pre-nex-studio"]
    assert ciel.zapisane["docker-compose.yml.pre-nex-studio"][0] == "name: rucne\n", "odložila sa miestna kópia"
    assert not (miestny / "docker-compose.yml.pre-nex-studio").exists(), (
        "záloha vznikla tu namiesto na cieli — presne tá chyba z MÁGERSTAVU"
    )


def test_a_local_instance_is_still_backed_up_locally(tmp_path, monkeypatch) -> None:
    """Opačný smer: bez cieľa sa nesmie nič posielať po sieti — testovacie inštalácie bývajú tu."""
    from backend.services import instance_adoption as ia

    ciel = _CielSoZapisom()
    monkeypatch.setattr(ia, "remote_instance", ciel)
    miestny = tmp_path / "icc" / "nex-demo"
    miestny.mkdir(parents=True)
    (miestny / "docker-compose.yml").write_text("name: miestna\n", encoding="utf-8")

    odlozene = ia.set_aside_hand_authored(miestny)

    assert odlozene == ["docker-compose.yml.pre-nex-studio"]
    assert (miestny / "docker-compose.yml.pre-nex-studio").read_text(encoding="utf-8") == "name: miestna\n"
    assert ciel.zapisane == {}, "miestna záloha sa posielala na cudzí stroj"


def test_the_build_recipe_is_matched_by_role_when_the_image_name_differs(tmp_path) -> None:
    """⚠️ Jadro opravy z 25.09.2026. Ručne písaná inštalácia si meno obrazu zvolila sama a nemusí
    sedieť s tým, čo projekt vyrobí: ostrý NEX Manager má `nexmanager-backend`, projekt `nex-manager`
    vyrobí `nex-manager-backend`. Rozdiel je jedna pomlčka — a stačil na to, aby kokpit nevedel, kto
    ten obraz stavia, a nasadenie zastavil."""
    import yaml

    _projekt(tmp_path)
    instalacia = tmp_path / "inst"
    instalacia.mkdir()
    (instalacia / "docker-compose.yml").write_text(
        "name: mager-manager\n"
        "services:\n"
        "  postgres:\n    image: postgres:16-alpine\n"
        "  backend:\n    image: nexmanager-backend:1.0.0\n"
        "  frontend:\n    image: nexmanager-frontend:1.0.0\n",
        encoding="utf-8",
    )
    source = P.load_source_compose(tmp_path / "projects" / "nex-manager")

    povysene = P.render_version_bump(
        instalacia,
        version="v1.2.2",
        project_slug="nex-manager",
        source=source,
        project_path=tmp_path / "projects" / "nex-manager",
    )

    sluzby = povysene["services"]
    assert sluzby["backend"].get("build"), "stavba sa nespárovala — meno obrazu nesedí, rola áno"
    assert sluzby["frontend"].get("build")
    assert sluzby["postgres"].get("build") is None, "cudzí obraz sa zo zdrojákov brať nesmie"

    povodne = yaml.safe_load((instalacia / "docker-compose.yml").read_text(encoding="utf-8"))
    assert P.unbuildable_images(povodne, povysene, image_exists=lambda _o: False) == []


def test_the_service_and_volume_names_of_the_instance_are_never_rewritten(tmp_path) -> None:
    """⚠️ Prípad NEX Inboxu. Meno služby je adresa, na ktorú sa appka k databáze pripája; meno
    úložiska je miesto, kde tie dáta ležia. Prepísať ich znamená naštartovať appku s prázdnou
    databázou, kým pôvodná leží vedľa bez odkazu."""
    _projekt(tmp_path)
    instalacia = tmp_path / "inst"
    instalacia.mkdir()
    (instalacia / "docker-compose.yml").write_text(
        "name: mager-inbox\n"
        "services:\n"
        "  postgres:\n    image: postgres:16-alpine\n"
        "  alembic-init:\n    image: nex-manager-backend:1.0.0\n"
        "  backend:\n    image: nex-manager-backend:1.0.0\n"
        "volumes:\n  postgres-data: null\n",
        encoding="utf-8",
    )
    source = P.load_source_compose(tmp_path / "projects" / "nex-manager")

    povysene = P.render_version_bump(
        instalacia,
        version="v1.5.6",
        project_slug="nex-manager",
        source=source,
        project_path=tmp_path / "projects" / "nex-manager",
    )

    assert sorted(povysene["services"]) == ["alembic-init", "backend", "postgres"]
    assert list(povysene["volumes"]) == ["postgres-data"]


# ── 7. po zlyhaní sa predpis na cieli vráti ───────────────────────────────────


class _VysledokProvisioningu:
    fe_service = "frontend"
    warnings: list[str] = []
    uat_dir = Path("/opt/customers/mager/nex-inbox")
    previous_remote_files = {"docker-compose.yml": ("name: povodny\n", 0o664), ".env": ("TAJNE=1\n", 0o600)}


def test_a_failed_deploy_puts_the_previous_description_back(monkeypatch) -> None:
    """⚠️ Zmerané 25.09.2026 na ostrom NEX Inboxe. Nasadenie prekročilo časový limit, ale predpis na
    MAGERi už ukazoval na 1.5.6, ktorej obrazy sa nepostavili. Kontajnery bežali ďalej na 1.5.5, takže
    zákazník nič nespozoroval — pri najbližšom reštarte by však appka nenaštartovala vôbec."""
    from backend.services import deploy as D

    ciel = _Ciel()
    monkeypatch.setattr("backend.services.remote_instance.write_files", ciel.write_files)

    detail = D._vrat_predpis_po_zlyhani(False, "deploy prekročil časový limit", _VysledokProvisioningu(), "mager")

    assert set(ciel.zapisane) == {"docker-compose.yml", ".env"}
    assert ciel.zapisane["docker-compose.yml"][0] == "name: povodny\n"
    assert "vrátený" in detail


def test_a_successful_deploy_keeps_the_new_description(monkeypatch) -> None:
    """Po úspechu je nový predpis ten správny — vracať ho by znamenalo zahodiť, čo sa práve nasadilo."""
    from backend.services import deploy as D

    ciel = _Ciel()
    monkeypatch.setattr("backend.services.remote_instance.write_files", ciel.write_files)

    D._vrat_predpis_po_zlyhani(True, "OK", _VysledokProvisioningu(), "mager")

    assert ciel.zapisane == {}


def test_a_local_instance_has_nothing_to_put_back(monkeypatch) -> None:
    """Inštalácia na tomto stroji nemá na cieli čo vracať — a nič sa po sieti neposiela."""
    from backend.services import deploy as D

    ciel = _Ciel()
    monkeypatch.setattr("backend.services.remote_instance.write_files", ciel.write_files)

    D._vrat_predpis_po_zlyhani(False, "zlyhalo", _VysledokProvisioningu(), None)

    assert ciel.zapisane == {}


def test_a_failed_restore_is_said_out_loud(monkeypatch) -> None:
    """⚠️ Vtedy na cieli zostal predpis, ktorý tam nepatrí — horší stav než samotné zlyhané nasadenie.
    Zamlčať to znamená nechať mínu, o ktorej nikto nevie."""
    from backend.services import deploy as D

    monkeypatch.setattr("backend.services.remote_instance.write_files", lambda *a, **k: "cieľ neodpovedá")

    detail = D._vrat_predpis_po_zlyhani(False, "zlyhalo", _VysledokProvisioningu(), "mager")

    assert "NEPODARILO" in detail and "cieľ neodpovedá" in detail


def test_the_timeout_is_longer_when_the_build_runs_on_another_machine() -> None:
    """Prenos zdrojového kódu a stavba na hardvéri zákazníka trvajú dlhšie — a legitímne."""
    from backend.services import orchestrator as O

    assert O.REMOTE_DEPLOY_TIMEOUT > O.UAT_DEPLOY_TIMEOUT
    zdroj = __import__("inspect").getsource(O._run_uat_deploy)
    assert "REMOTE_DEPLOY_TIMEOUT if" in zdroj, "dlhší limit sa pri cudzom stroji nepoužije"


def test_provisioning_remembers_what_it_overwrote_on_the_target(tmp_path, monkeypatch) -> None:
    """⚠️ Túto stráž si vypýtala mutácia, ktorá prešla nezachytená (25.09.2026): stráže skúšali len
    toho, kto predpis vracia, nie toho, kto si pôvodný stav zapamätá. Bez zapamätania niet čo vrátiť
    a celá poistka je ozdoba."""
    ciel = _Ciel({"docker-compose.yml": "name: povodny\n", ".env": "POSTGRES_PASSWORD=x\n"})
    monkeypatch.setattr(P, "remote_instance", ciel)
    monkeypatch.setattr(P, "_docker_image_exists", lambda _o: True)

    vysledok = _provision(tmp_path, deploy_host="mager", allow_overwrite=True)

    assert set(vysledok.previous_remote_files) == {"docker-compose.yml", ".env"}
    assert vysledok.previous_remote_files["docker-compose.yml"][0] == "name: povodny\n"
    assert vysledok.previous_remote_files[".env"][1] == 0o600, "záloha nastavení musí zostať zavretá"


def test_a_local_instance_remembers_nothing_to_put_back(tmp_path, monkeypatch) -> None:
    """Inštalácia na tomto stroji sa nikam nezapisuje, takže niet čo vracať."""
    ciel = _Ciel()
    monkeypatch.setattr(P, "remote_instance", ciel)

    vysledok = _provision(tmp_path)

    assert vysledok.previous_remote_files == {}
