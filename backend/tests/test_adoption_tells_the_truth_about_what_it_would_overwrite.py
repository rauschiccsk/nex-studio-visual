"""Prevzatie povie pravdu o tom, čo by prepísalo — a inštaláciu číta tam, kde naozaj beží (ICCINT-151).

**Čo tomu predchádzalo.** 24.09.2026, pred prvým ostrým nasadením na MAGER, som pustil náhľad
prevzatia nad ostrou inštaláciou MÁGERSTAVU. Náhľad odpovedal: *prevziať sa dá, nič sa nestratí.*
Bola to nepravda dvakrát:

1. **Pozeral sa na iný stroj.** Priečinok čítal tam, kde beží kokpit (ANDROS). Tam ležala kópia zo
   14. júla, 2 454 bajtov. Skutočný predpis je na MAGERi, z 23. septembra, 4 370 bajtov.
2. **Kontrola „čo sa stratí“ porovnávala názvy, nie obsah.** Zoznam ``REPRODUCIBLE_SERVICE_KEYS``
   obsahuje ``labels`` — teda „labely vieme zapísať“ — a tým bola otázka vybavená, hoci tých
   konkrétnych 25 riadkov smerovania cez Tailscale generátor zapísať nevie ani jeden.

Prevzatie by teda ohlásilo úspech a inštalácia by prišla o celú svoju adresu. Poistka proti rozídeniu
predpisov (nasadenie zastaví, kým sa oba líšia) tu nepomôže — prevzatie ich práve zrovná a tým ju
stíši. Je to presne ten krok, ktorý musí byť pravdivý.

⚠️ Čo tieto stráže NEDOVOLIA zmeniť: náhľad sa nesmie dať zavolať bez toho, aby povedal, čo by
zapísal; čítanie cieľa po sebe nesmie nič nechať; a nedostupný cieľ sa nesmie čítať ako prázdny.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from backend.services import instance_adoption as ia
from backend.services import uat_provisioner
from backend.tests._adoption_render import render_request

# ── podklady ─────────────────────────────────────────────────────────────────

#: Výrez z ostrého predpisu MÁGERSTAVU (23.09.2026) — smerovanie cez Tailscale a vlastná sieť.
MAGERSTAV = """name: mager-manager
services:
  backend:
    image: nexmanager-backend:1.0.0
    networks:
      - manager-net
      - nex-ts-net
    container_name: mager-manager-backend
    labels:
      - traefik.enable=true
      - traefik.docker.network=nex-ts-net
      - traefik.http.routers.mager-manager-sec-api.rule=Host(`magerstav-manager.icc.sk`)
      - traefik.http.routers.mager-manager-sec-api.tls.certresolver=cf
networks:
  manager-net:
    driver: bridge
  nex-ts-net:
    external: true
"""

#: To, čo by generátor na to miesto zapísal: iná sieť, iné smerovanie, tá istá služba.
VYKRESLENE = """name: mager-manager
services:
  backend:
    image: nexmanager-backend:1.2.0
    networks:
      - default
      - nex-proxy-net
    container_name: mager-manager-backend
    labels:
      - traefik.enable=true
      - traefik.http.routers.mager-manager-api.rule=Host(`mager-manager.isnex.eu`)
networks:
  nex-proxy-net:
    external: true
"""


def _instalacia(tmp_path: Path, compose: str) -> Path:
    d = tmp_path / "icc" / "nex-demo"
    d.mkdir(parents=True)
    (d / "docker-compose.yml").write_text(compose, encoding="utf-8")
    (d / ".env").write_text("X=1\n", encoding="utf-8")
    return d


# ── 1. náhľad sa nedá položiť bez otázky „čo by si zapísal?“ ──────────────────


def test_the_preview_cannot_be_asked_without_saying_what_would_be_written() -> None:
    """⚠️ Jadro veci. Dovtedy sa náhľad dal zavolať bez tejto odpovede — a bez nej klamal."""
    podpis = inspect.signature(ia.preview)
    render = podpis.parameters["render"]

    assert render.kind is inspect.Parameter.KEYWORD_ONLY, "musí sa pomenovať, nie prihodiť poradím"
    assert render.default is inspect.Parameter.empty, (
        "voliteľné vykreslenie sa raz zabudne a náhľad znovu povie „nič sa nestratí“"
    )


# ── 2. čo by zmizlo: porovnáva sa OBSAH, nie mená vlastností ──────────────────


def test_the_routing_that_would_vanish_is_named() -> None:
    """Presne ten prípad z 24.09.2026: labely zostanú „labelmi“, ale tieto konkrétne zmiznú."""
    straty = ia._co_by_zmizlo(MAGERSTAV, VYKRESLENE)

    assert any("smerovania" in s for s in straty), "strata smerovania sa nespomenula"
    assert any("magerstav-manager.icc.sk" in s for s in straty), (
        "hláška musí ukázať, ČO zmizne — inak sa podľa nej nedá rozhodnúť"
    )


def test_the_network_that_would_vanish_is_named() -> None:
    """Sieť, cez ktorú je zákazník dostupný, je tá najdrahšia strata."""
    straty = ia._co_by_zmizlo(MAGERSTAV, VYKRESLENE)

    assert any("nex-ts-net" in s and "odpojila" in s for s in straty)
    assert any(s == "sieť nex-ts-net by zmizla" for s in straty)


def test_the_old_key_name_check_would_have_let_magerstav_through() -> None:
    """⚠️ Dôkaz, že stará kontrola na tento prípad NESTAČÍ — nie domnienka, meranie.

    Keby toto sčervenalo, znamená to, že kontrola podľa mien vlastností by MÁGERSTAV zastavila a
    celá nová poistka je zbytočná. Doteraz ho pustila.
    """
    fakty = uat_provisioner.facts_from_compose_text(MAGERSTAV)

    assert fakty.unreproducible == {}, "stará kontrola tu nenamietala — preto bolo treba novú"
    assert ia._co_by_zmizlo(MAGERSTAV, VYKRESLENE), "nová kontrola musí namietať tam, kde stará mlčala"


def test_a_service_that_would_disappear_is_a_loss() -> None:
    teraz = "services:\n  backend:\n    image: a\n  worker:\n    image: b\n"
    potom = "services:\n  backend:\n    image: a\n"

    assert any("worker" in s and "zmizla" in s for s in ia._co_by_zmizlo(teraz, potom))


def test_a_published_port_that_would_close_is_a_loss() -> None:
    teraz = 'services:\n  backend:\n    ports:\n      - "8080:80"\n'
    potom = "services:\n  backend:\n    image: a\n"

    assert any("8080:80" in s for s in ia._co_by_zmizlo(teraz, potom))


def test_a_new_version_of_the_image_is_not_a_loss() -> None:
    """Nasadiť novú verziu je zmysel nasadenia. Poistka, ktorá kričí aj na to, sa naučí ignorovať."""
    teraz = "services:\n  backend:\n    image: app:1.0.0\n"
    potom = "services:\n  backend:\n    image: app:1.2.0\n"

    assert ia._co_by_zmizlo(teraz, potom) == []


def test_an_instance_that_matches_what_we_would_write_is_still_adoptable(tmp_path) -> None:
    """Poistka nesmie byť stena: inštalácia bez vlastných zvláštností sa prevziať dá."""
    d = _instalacia(tmp_path, "name: uat-icc-demo\nservices:\n  backend:\n    image: demo:v1\n")

    n = ia.preview(d, render=render_request(tmp_path))

    assert n.can_adopt, f"prevzatie sa zamklo samo: {n.blocking}"


def _magerstav_render(tmp_path):
    """Vykreslenie PRE TÚTO inštaláciu. Keby ukazovalo na iného zákazníka, hlásilo by premenovanie
    zostavy — a to by nebola strata smerovania, ale nesprávne položená otázka."""
    return render_request(
        tmp_path,
        slug="mager-prod",
        project="nex-manager",
        environment="prod",
        customer_slug="mager",
        app="manager",
    )


def test_nothing_is_lost_when_the_instance_already_stands(tmp_path) -> None:
    """⚠️ OBRÁTENÉ 25.09.2026. Dovtedy tu stálo, že služba navyše v inštalácii sa „stratí" — vtedy to
    bola pravda, lebo prevzatie vykresľovalo inštaláciu zo zdrojového projektu.

    Odvtedy sa do stojacej inštalácie nasadzuje VERZIA, nie stavba (ICCINT-133). Jej služby, siete
    ani úložiská sa preto nemajú ako stratiť — a náhľad to musí predpovedať tým istým kódom, akým sa
    zapisuje. Kým predpovedal podľa zdroja, hlásil stratu `postgres`, `alembic-init` a `postgres-data`
    a NEX Inbox sa nedal prevziať, hoci mu nič nehrozilo.
    """
    s_navyse = MAGERSTAV.replace(
        "networks:\n  manager-net:",
        "  worker:\n    image: fronta:1.0.0\nnetworks:\n  manager-net:",
        1,
    )
    d = _instalacia(tmp_path, s_navyse)

    n = ia.preview(d, render=_magerstav_render(tmp_path))

    assert n.can_adopt, f"prevzatie sa zamklo samo: {n.blocking}"


def test_the_preview_predicts_with_the_same_code_that_writes(tmp_path) -> None:
    """⚠️ Stráž proti návratu. Keby náhľad predpovedal podľa zdrojového projektu a zapisovalo sa
    povýšenie verzie, hlásil by straty, ktoré nenastanú — a prevzatie by sa zamklo. Opačne by
    mlčal o stratách, ktoré nastanú. Predpovedať a zapisovať musí ten istý kód."""
    volane: list[str] = []
    povodne = uat_provisioner.version_bump_from_text

    def _zaznamenaj(*a, **k):
        volane.append("povysenie")
        return povodne(*a, **k)

    d = _instalacia(tmp_path, MAGERSTAV)
    uat_provisioner.version_bump_from_text = _zaznamenaj
    try:
        ia.preview(d, render=_magerstav_render(tmp_path))
    finally:
        uat_provisioner.version_bump_from_text = povodne

    assert volane == ["povysenie"], "náhľad predpovedal inou cestou, než akou sa zapisuje"


def test_the_real_magerstav_description_is_adoptable_since_its_routing_is_carried(tmp_path) -> None:
    """⚠️ OBRÁTENÉ 24.09.2026 (v4.40.10). Dovtedy tu stálo, že MÁGERSTAV sa prevziať NEDÁ — a bola to
    pravda: generátor nevedel napísať jeho smerovanie (dve mená, dva vstupné body, dva certifikáty,
    vlastná sieť), takže by oň inštalácia prišla.

    Odvtedy sa smerovanie prenáša z bežiacej inštalácie, tak ako pripojené priečinky a ručne
    pridelené podsiete. Niet čo stratiť, takže prevzatie sa smie ponúknuť. Keby toto sčervenalo,
    znamená to, že prenos smerovania prestal fungovať — a MÁGERSTAV sa prevziať zase nedá.
    """
    d = _instalacia(tmp_path, MAGERSTAV)

    n = ia.preview(d, render=_magerstav_render(tmp_path))

    assert n.can_adopt, f"smerovanie sa prestalo prenášať: {n.blocking}"


# ── 3. keď sa nevie, čo by sa zapísalo, neprepisuje sa ────────────────────────


def test_a_render_that_cannot_run_refuses_instead_of_pretending(tmp_path) -> None:
    """Keď nevieme, čo by sme zapísali, nevieme ani, čo by sme prepísali."""
    d = _instalacia(tmp_path, MAGERSTAV)
    zly = render_request(tmp_path)._replace(project_path=tmp_path / "niet-takeho-projektu")

    n = ia.preview(d, render=zly)

    assert not n.can_adopt
    assert any("nedá vykresliť" in s for s in n.blocking)


# ── 4. inštalácia sa číta tam, kde beží ───────────────────────────────────────


def test_reading_the_target_never_leaves_anything_behind() -> None:
    """⚠️ ``-v`` pri neexistujúcej ceste priečinok na cieli VYROBÍ. Náhľad nesmie nič vyrobiť."""
    cmd = ia._remote_state_cmd(Path("/opt/customers/mager/nex-manager"))

    assert "-v" not in cmd, "krátky zápis pripojenia po sebe na cudzom stroji nechá priečinok"
    pripojenie = cmd[cmd.index("--mount") + 1]
    assert pripojenie.startswith("type=bind,")
    assert pripojenie.endswith(",readonly"), "čítanie cieľa musí byť len na čítanie"
    assert "/opt/customers/mager/nex-manager" in pripojenie


def test_a_missing_directory_on_the_target_is_an_ordinary_answer() -> None:
    """Prvé nasadenie k zákazníkovi: priečinok ešte nie je. To nie je porucha."""
    stav = ia._parse_remote_state(125, "docker: Error …: bind source path does not exist: /opt/customers/x/y")

    assert not stav.exists
    assert stav.unreadable is None, "chýbajúci priečinok nie je nečitateľný cieľ"


def test_an_unreachable_target_is_never_read_as_an_empty_one() -> None:
    """⚠️ Zliať „nedostupný“ s „prázdny“ znamená ponúknuť prepis práve vtedy, keď o cieli nevieme nič."""
    stav = ia._parse_remote_state(255, "ssh: connect to host 100.109.5.21 port 22: Connection refused")

    assert stav.exists, "nedostupný cieľ sa nesmie tváriť ako neexistujúci priečinok"
    assert stav.unreadable and "nedalo pozrieť" in stav.unreadable


def test_an_unreadable_target_refuses_the_adoption(tmp_path, monkeypatch) -> None:
    d = _instalacia(tmp_path, MAGERSTAV)
    monkeypatch.setattr(
        ia,
        "read_instance_state",
        lambda *_a, **_k: ia.InstanceState(True, [], None, "na cieľový stroj sa nedalo pozrieť: …", []),
    )

    n = ia.preview(d, render=render_request(tmp_path))

    assert not n.can_adopt
    assert any("nedalo pozrieť" in s for s in n.blocking)


def test_the_listing_and_the_description_come_from_one_look() -> None:
    """Dva behy = dve pripojenia a stav, ktorý sa medzi nimi môže zmeniť."""
    vystup = ".env\ndocker-compose.yml\n" + ia._ODDELOVAC + "\nname: mager-manager\nservices: {}\n"

    stav = ia._parse_remote_state(0, vystup)

    assert stav.entries == [".env", "docker-compose.yml"]
    assert stav.compose_text and stav.compose_text.startswith("name: mager-manager")


def test_a_target_answer_we_cannot_split_stops_the_adoption() -> None:
    """Keď výstup nemá hranicu, nevieme, kde končí výpis a začína predpis — a nehádame."""
    stav = ia._parse_remote_state(0, "nejaký výstup bez hranice")

    assert stav.unreadable and "rozobrať" in stav.unreadable


@pytest.mark.parametrize("ciel", ["mager", "100.109.5.21"])
def test_the_running_containers_are_counted_on_the_machine_they_run_on(tmp_path, monkeypatch, ciel) -> None:
    """Pýtať sa tunajšieho Dockera na cudzie kontajnery vráti „nič“ — a to sa číta ako „opustené“."""
    videne: list[dict] = []

    class _Vysledok:
        returncode = 0
        stdout = ".env\n" + ia._ODDELOVAC + "\nname: x\n"
        stderr = ""

    def _fake_run(cmd, **kw):
        videne.append({"cmd": cmd, "env": kw.get("env") or {}})
        return _Vysledok()

    monkeypatch.setattr(ia.subprocess, "run", _fake_run)
    ia.read_instance_state(tmp_path / "nic", deploy_host=ciel)

    assert videne, "nič sa nespýtalo"
    for volanie in videne:
        assert volanie["env"].get("DOCKER_HOST") == f"ssh://{ciel}", (
            f"volanie {volanie['cmd'][:3]} šlo na tunajší Docker, nie na cieľ"
        )
    assert any("ps" in v["cmd"] for v in videne), "nikto sa nespýtal, čo na cieli beží"


def test_a_local_instance_is_still_read_locally(tmp_path, monkeypatch) -> None:
    """Bez cieľa sa nesmie nič spúšťať cez ssh — testovacie inštalácie bývajú tu."""
    d = _instalacia(tmp_path, MAGERSTAV)

    def _nesmie(*_a, **_k):
        raise AssertionError("miestna inštalácia sa čítala cez Docker")

    monkeypatch.setattr(ia, "_remote_state_cmd", _nesmie)
    stav = ia.read_instance_state(d)

    assert stav.exists and stav.compose_text == MAGERSTAV
    assert sorted(stav.entries) == [".env", "docker-compose.yml"]


# ── 5. jedny dvere: náhľad a zápis skladajú ten istý predpis ──────────────────


def test_the_writer_and_the_preview_go_through_the_same_door() -> None:
    """⚠️ Keby si zápis skladal predpis po svojom, poistka by strážila výmysel.

    Overuje sa proti SAMOTNÉMU zápisu (jeho zdroju), nie proti napodobenine — tá by potvrdila len
    moju predstavu o ňom.
    """
    zdroj = inspect.getsource(uat_provisioner.provision_uat)

    assert "build_compose_for_instance(" in zdroj, "zápis obišiel spoločné dvere"
    assert "compose = build_uat_compose(" not in zdroj, "zápis si predpis skladá po svojom"


def test_the_description_can_be_read_without_a_disk() -> None:
    """Predpis na inom stroji máme ako text — fakty sa z neho musia dať prečítať rovnako."""
    fakty = uat_provisioner.facts_from_compose_text(MAGERSTAV)
    prazdne = uat_provisioner.facts_from_compose_text(None)

    assert fakty.network_subnets == {} and fakty.host_mounts == {}
    assert prazdne.host_mounts == {} and prazdne.extra_hosts == []
    assert not uat_provisioner.is_provisioner_generated_text(MAGERSTAV)
    assert not uat_provisioner.is_provisioner_generated_text(None), "prázdny text nie je náš predpis"
    assert uat_provisioner.is_provisioner_generated_text(f"# {uat_provisioner.GENERATED_BY_MARKER}\nname: x\n")


def test_a_folder_without_a_description_is_predicted_from_the_project(tmp_path) -> None:
    """⚠️ Túto stráž si vypýtala mutácia, ktorá prešla nezachytená (25.09.2026).

    Priečinok môže existovať a predpis v ňom ešte nie — napríklad po nedokončenom nasadení. Vtedy
    niet čo povyšovať a predpoveď musí vyjsť zo zdrojového projektu. Keby sa aj tu povyšovalo,
    predpoveďou by bol PRÁZDNY predpis a náhľad by o strate mlčal, hoci by sa zapísalo všetko nanovo.
    """
    d = tmp_path / "icc" / "nex-demo"
    d.mkdir(parents=True)
    (d / ".env").write_text("X=1\n", encoding="utf-8")

    vykreslene, chyba = ia._vykresli(render_request(tmp_path), None)

    assert chyba is None, chyba
    assert "services:" in vykreslene and "backend:" in vykreslene, "predpoveď je prázdna"
