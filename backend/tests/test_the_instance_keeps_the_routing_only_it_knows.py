"""Smerovanie, ktoré vie len bežiaca inštalácia, prežije nasadenie (ICCINT-151).

**Čo tomu predchádzalo.** Generátor vie publikovať aplikáciu jediným spôsobom a má ho zapísaný
natvrdo: sieť ``nex-proxy-net``, meno ``<zákazník>-<aplikácia>.isnex.eu``, vstup ``web``, bez
certifikátu. Ostrá inštalácia MÁGERSTAVU je publikovaná inak — zmerané 24.09.2026 z jej predpisu:

    vonkajšia sieť   nex-ts-net (+ vnútorná manager-net)
    mená             magerstav-manager.icc.sk  a  mager.tail5c98e2.ts.net
    vstupné body     websecure  a  mager-manager
    certifikáty      cf  a  tailscale
    ciest spolu      štyri (dve mená × frontend/backend)

Nie je to chýbajúca hodnota, ale iný TVAR. Smerovanie preto patrí medzi údaje, ktoré vie len bežiaca
inštalácia — rovnako ako pripojené priečinky zákazníka a ručne pridelené podsiete (ICCINT-130):
*bežiaci súbor je jediný záznam, ktorý sa nemôže rozísť so skutočnosťou.*

⚠️ Čo tieto stráže NEDOVOLIA zmeniť: k vlastnému smerovaniu inštalácie sa NEPRIDÁVA naše (dve
smerovania na jednej službe si odporujú a to naše ukazuje na sieť, ktorá na cieli nemusí byť), siete
sa preberajú VŠETKÝM službám (inak databáza osamie) a inštalácia bez vlastného smerovania musí naše
dostať ako doteraz.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from backend.services import uat_provisioner

#: Výrez z ostrého predpisu MÁGERSTAVU (23.09.2026) — skrátený, ale tvarovo verný.
MAGERSTAV = """name: mager-manager
services:
  db:
    image: postgres:16-alpine
    networks:
      - manager-net
  backend:
    image: nexmanager-backend:1.0.0
    networks:
      - manager-net
      - nex-ts-net
    labels:
      - traefik.enable=true
      - traefik.docker.network=nex-ts-net
      - traefik.http.routers.mager-manager-sec-api.rule=Host(`magerstav-manager.icc.sk`) && PathPrefix(`/api`)
      - traefik.http.routers.mager-manager-sec-api.entrypoints=websecure
      - traefik.http.routers.mager-manager-sec-api.tls.certresolver=cf
      - traefik.http.routers.mager-manager-ts-api.rule=Host(`mager.tail5c98e2.ts.net`) && PathPrefix(`/api`)
      - traefik.http.routers.mager-manager-ts-api.tls.certresolver=tailscale
  frontend:
    image: nexmanager-frontend:1.0.0
    networks:
      - manager-net
      - nex-ts-net
    labels:
      - traefik.enable=true
      - traefik.http.routers.mager-manager-sec.rule=Host(`magerstav-manager.icc.sk`)
      - traefik.http.routers.mager-manager-sec.tls.certresolver=cf
networks:
  manager-net:
    driver: bridge
  nex-ts-net:
    external: true
"""

#: Zdrojový projekt — taký, z akého sa inštalácia kedysi vykreslila.
ZDROJ = """services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: demo
      POSTGRES_PASSWORD: x
      POSTGRES_DB: demo
  backend:
    build: ./backend
  frontend:
    build: ./frontend
"""


@pytest.fixture
def projekt(tmp_path) -> Path:
    p = tmp_path / "projects" / "nex-manager"
    p.mkdir(parents=True)
    (p / "docker-compose.yml").write_text(ZDROJ, encoding="utf-8")
    return p


def _vykresli(projekt: Path, existujuci: str | None) -> dict:
    compose = uat_provisioner.build_compose_for_instance(
        project_path=projekt,
        slug="mager-prod",
        project="nex-manager",
        environment="prod",
        customer_slug="mager",
        app="manager",
        existing_compose_text=existujuci,
    )
    return yaml.safe_load(uat_provisioner.render_uat_compose(compose))


def _labely(predpis: dict, sluzba: str) -> list[str]:
    return uat_provisioner._labels_as_list((predpis.get("services") or {}).get(sluzba, {}).get("labels"))


# ── 1. vlastné smerovanie prežije ─────────────────────────────────────────────


def test_both_published_names_survive(projekt) -> None:
    """⚠️ Jadro veci: MÁGERSTAV je dostupný pod dvomi menami. Generátor vie jedno."""
    predpis = _vykresli(projekt, MAGERSTAV)
    text = yaml.safe_dump(predpis)

    assert "magerstav-manager.icc.sk" in text
    assert "mager.tail5c98e2.ts.net" in text


def test_the_certificates_survive(projekt) -> None:
    """Bez certifikátu sa k aplikácii nedá dostať cez https — a obe cesty ho majú, každá iný."""
    text = yaml.safe_dump(_vykresli(projekt, MAGERSTAV))

    assert "certresolver=cf" in text
    assert "certresolver=tailscale" in text


def test_the_external_network_survives_with_its_definition(projekt) -> None:
    """Samotné meno siete nestačí — bez ``external: true`` by ju compose chcel vyrobiť nanovo."""
    predpis = _vykresli(projekt, MAGERSTAV)

    assert predpis["networks"]["nex-ts-net"] == {"external": True}
    assert predpis["networks"]["manager-net"] == {"driver": "bridge"}


# ── 2. naše smerovanie sa k tomu NEPRIDÁVA ────────────────────────────────────


def test_our_own_routing_is_not_added_on_top(projekt) -> None:
    """⚠️ Dve smerovania na jednej službe si odporujú a to naše by ukazovalo na cudziu sieť."""
    text = yaml.safe_dump(_vykresli(projekt, MAGERSTAV))

    assert "isnex.eu" not in text, "generátor pridal svoje meno k menám zákazníka"
    assert uat_provisioner.PROXY_NETWORK not in text, (
        "generátor pridal sieť, ktorá na serveri zákazníka nemusí existovať — `up` by spadol na štarte"
    )


def test_the_header_does_not_claim_a_network_the_file_does_not_use(projekt) -> None:
    """Hlavičku číta človek na cudzom serveri. Veta o ``nex-proxy-net`` by tam bola nepravdivá."""
    compose = uat_provisioner.build_compose_for_instance(
        project_path=projekt,
        slug="mager-prod",
        project="nex-manager",
        environment="prod",
        customer_slug="mager",
        app="manager",
        existing_compose_text=MAGERSTAV,
    )
    text = uat_provisioner.render_uat_compose(compose)
    hlavicka = "\n".join(r for r in text.splitlines() if r.startswith("#"))

    assert uat_provisioner.PROXY_NETWORK not in hlavicka
    assert "carries its OWN" in hlavicka


# ── 3. siete dostanú VŠETKY služby ────────────────────────────────────────────


def test_every_service_gets_the_networks_not_just_the_routed_ones(projekt) -> None:
    """⚠️ Keby vlastnú sieť dostal len frontend s backendom, databáza by zostala na ``default`` a
    aplikácia by ju prestala vidieť — chyba, ktorá vyzerá ako rozbitá appka, nie ako stratený riadok."""
    predpis = _vykresli(projekt, MAGERSTAV)
    siete = {m: uat_provisioner._networks_as_list(s.get("networks")) for m, s in predpis["services"].items()}

    assert siete["db"] == ["manager-net"], f"databáza osamela: {siete['db']}"
    assert "manager-net" in siete["backend"], "backend by databázu nevidel"


# ── 4. bez vlastného smerovania sa nič nemení ─────────────────────────────────


def test_an_instance_without_its_own_routing_still_gets_ours(projekt) -> None:
    """Poistka proti tomu, aby sa z opravy stala nová diera. Prvé nasadenie tu musí smerovanie dostať."""
    text = yaml.safe_dump(_vykresli(projekt, None))

    assert uat_provisioner.PROXY_NETWORK in text, "nová inštalácia zostala bez smerovania"
    assert "mager-manager.isnex.eu" in text


def test_a_plain_instance_without_labels_is_not_mistaken_for_one(projekt) -> None:
    """Inštalácia bez labelov vlastné smerovanie NEMÁ — nesmie sa tak tváriť."""
    bez_labelov = "name: mager-manager\nservices:\n  backend:\n    image: a\n"
    text = yaml.safe_dump(_vykresli(projekt, bez_labelov))

    assert uat_provisioner.PROXY_NETWORK in text


# ── 5. samotné rozhodovanie ───────────────────────────────────────────────────


def test_the_question_is_answered_off_the_instance() -> None:
    fakty = uat_provisioner.facts_from_compose_text(MAGERSTAV)

    assert uat_provisioner.has_own_routing(fakty) is True
    assert uat_provisioner.has_own_routing(None) is False
    assert uat_provisioner.has_own_routing(uat_provisioner.facts_from_compose_text(None)) is False


def test_a_non_traefik_label_is_not_routing() -> None:
    """Label ešte nie je smerovanie. Inak by inštalácia s ľubovoľnou poznámkou prišla o našu cestu."""
    iba_poznamka = "services:\n  backend:\n    labels:\n      - com.example.owner=tibor\n"
    fakty = uat_provisioner.facts_from_compose_text(iba_poznamka)

    assert fakty.service_labels == {"backend": ["com.example.owner=tibor"]}
    assert uat_provisioner.has_own_routing(fakty) is False


def test_the_routing_is_read_in_both_compose_spellings() -> None:
    """Compose pozná labely aj siete v dvoch tvaroch. Inštalácia si tvar nevyberá podľa nás."""
    slovnikom = (
        "services:\n  backend:\n    labels:\n      traefik.enable: 'true'\n    networks:\n      manager-net: null\n"
    )
    fakty = uat_provisioner.facts_from_compose_text(slovnikom)

    assert fakty.service_labels == {"backend": ["traefik.enable=true"]}
    assert fakty.service_networks == {"backend": ["manager-net"]}
    assert uat_provisioner.has_own_routing(fakty) is True
