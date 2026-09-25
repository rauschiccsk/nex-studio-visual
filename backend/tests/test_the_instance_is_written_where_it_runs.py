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


def test_adoption_renders_from_the_project_instead_of_bumping_the_version(tmp_path, monkeypatch) -> None:
    """⚠️ Zmerané na ostrom MÁGERSTAVE 24.09.2026. Prevzatie šlo cestou „do existujúcej inštalácie
    nasadzuj VERZIU, nie stavbu" (ICCINT-133) a vzalo pripnuté obrazy `nexmanager-backend:1.0.0` bez
    akéhokoľvek predpisu na stavbu — len prepísalo číslo na `v1.2.2`. Taký obraz nikto nepostaví ani
    nikde neleží a stráž ICCINT-137 nasadenie zastavila.

    Zmyslom prevzatia je vziať inštaláciu POD SPRÁVU, teda vykresliť ju zo zdrojového projektu;
    náhľad predtým potvrdil, že sa nič nestratí. Povýšenie chráni inštaláciu, ktorú kokpit UŽ
    spravuje — nie tú, ktorú práve preberá.
    """
    pripnute = "name: mager-manager\nservices:\n  backend:\n    image: nexmanager-backend:1.0.0\n"
    ciel = _Ciel({"docker-compose.yml": pripnute, ".env": "POSTGRES_PASSWORD=x\n"})
    monkeypatch.setattr(P, "remote_instance", ciel)

    _provision(tmp_path, deploy_host="mager", allow_overwrite=True)

    zapisany = ciel.zapisane["docker-compose.yml"][0]
    assert "build:" in zapisany, "prevzatie len povýšilo verziu — obraz by sa nemal odkiaľ vziať"
    assert "nexmanager-backend:1.0.0" not in zapisany


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
