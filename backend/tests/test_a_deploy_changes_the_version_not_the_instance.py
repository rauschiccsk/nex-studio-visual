"""Nasadenie verzie mení VERZIU, nie stavbu inštalácie (ICCINT-133).

Zistené 15.09.2026 pri prvom skutočnom nasadení NEX Inbox 1.5.0 na UAT MAGERSTAVU. Nasadenie
prestavalo celu instalaciu podla vyvojoveho projektu a zhodilo appku na sedem minut.

    ziva instalacia:      postgres  alembic-init   inbox-net   nex_inbox_mager   mager
    co vyrobilo nasadenie: db        migrate        inbox-dev-net nex_inbox_dev    dev

ICCINT-130 to riesil vymenuvanim udajov, ktore treba preniest. Za dva dni to boli STYRI kola a
kazde naslo dalsi. Vymenuvanie nema koniec, lebo zoznam toho, cim sa instalacia lisi od vyvojoveho
projektu, nie je uzavrety.

**Nas vlastny standard hovori spravne.** ``scripts/deploy-prod.sh``, ktorym nasadzujeme NEX Studio:

    # ONLY the image tags. Routing, ports, volumes and env are hand-maintained on this file
    # and nothing here may touch them.

Ten isty princip patri aj k zakaznikovi: prve nasadenie do prazdneho priecinka vykresli, kazde
dalsie zmeni LEN verziu.
"""

from __future__ import annotations

from pathlib import Path

from backend.services import uat_provisioner as P

ZIVY = """\
# NEX Inbox v1.4.0 — UAT pre MAGERSTAV
name: uat-mager-inbox
networks:
  inbox-net:
    ipam:
      config:
        - subnet: 192.168.48.0/24
services:
  postgres:
    image: postgres:16-alpine
    container_name: uat-mager-inbox-postgres
    environment:
      POSTGRES_DB: nex_inbox_mager
    networks: [inbox-net]
  alembic-init:
    image: nex-inbox-backend:v1.4.0
    command: ["python", "-m", "alembic", "upgrade", "head"]
    networks: [inbox-net]
  backend:
    image: nex-inbox-backend:v1.4.0
    container_name: uat-mager-inbox-backend
    volumes:
      - ./originals:/var/lib/inbox/originals
      - /mnt/mager-edocs-inbox-uat:/var/lib/inbox/genesis-out
    networks: [inbox-net]
"""


def _instalacia(tmp_path: Path) -> Path:
    d = tmp_path / "mager" / "nex-inbox"
    d.mkdir(parents=True)
    (d / "docker-compose.yml").write_text(ZIVY, encoding="utf-8")
    (d / ".env").write_text(
        "POSTGRES_DB=nex_inbox_mager\nTENANT_SLUG=mager\nPOSTGRES_PASSWORD=tajne\nPROJECT_VERSION=1.4.0\n",
        encoding="utf-8",
    )
    return d


# -- Stavba instalacie sa nasadenim NEMENI ------------------------------------


def test_services_keep_their_names(tmp_path):
    """Premenovanie sluzby nie je kozmetika: ``docker compose`` povazuje ``db`` za NOVU sluzbu a
    ``postgres`` za sirotu, ktoru nezastavi — a stara ostane visiet na sieti, ktoru nova potrebuje.
    Presne na tom 15.09.2026 nasadenie spadlo, uz po zastaveni backendu."""
    d = _instalacia(tmp_path)
    novy = P.render_version_bump(d, version="1.5.0")
    assert sorted(novy["services"]) == ["alembic-init", "backend", "postgres"]


def test_the_database_name_is_the_instances_own(tmp_path):
    """Najhorsi z prepisov. ``nex_inbox_dev`` na tom stroji NEEXISTUJE, takze appka po nasadeni
    nema kam siahnut."""
    d = _instalacia(tmp_path)
    novy = P.render_version_bump(d, version="1.5.0")
    assert novy["services"]["postgres"]["environment"]["POSTGRES_DB"] == "nex_inbox_mager"


def test_network_volumes_and_subnet_are_untouched(tmp_path):
    d = _instalacia(tmp_path)
    novy = P.render_version_bump(d, version="1.5.0")
    assert list(novy["networks"]) == ["inbox-net"]
    assert novy["networks"]["inbox-net"]["ipam"]["config"][0]["subnet"] == "192.168.48.0/24"
    assert "./originals:/var/lib/inbox/originals" in novy["services"]["backend"]["volumes"]


# -- Verzia sa MENI -----------------------------------------------------------


def test_image_tags_are_bumped(tmp_path):
    """To jedine, co nasadenie verzie robit MA."""
    d = _instalacia(tmp_path)
    novy = P.render_version_bump(d, version="1.5.0")
    assert novy["services"]["backend"]["image"] == "nex-inbox-backend:v1.5.0"
    assert novy["services"]["alembic-init"]["image"] == "nex-inbox-backend:v1.5.0"
    assert novy["services"]["postgres"]["image"] == "postgres:16-alpine", "cudzi obraz sa nedvíha"


def test_env_keeps_identity_and_gains_the_version(tmp_path):
    d = _instalacia(tmp_path)
    env = P.env_version_bump(d, version="1.5.0", example={})
    assert env["POSTGRES_DB"] == "nex_inbox_mager"
    assert env["TENANT_SLUG"] == "mager"
    assert env["POSTGRES_PASSWORD"] == "tajne", "tajomstva sa nerotuju"
    assert env["PROJECT_VERSION"] == "1.5.0"


def test_a_new_variable_from_the_version_is_added_but_never_overwrites(tmp_path):
    """Verzia smie priniest NOVU premennu. Prepisat existujucu nesmie — tam zije totoznost."""
    d = _instalacia(tmp_path)
    env = P.env_version_bump(d, version="1.5.0", example={"NOVA_VOLBA": "predvolene", "TENANT_SLUG": "dev"})
    assert env["NOVA_VOLBA"] == "predvolene"
    assert env["TENANT_SLUG"] == "mager", "existujuca hodnota sa prepisala"


# -- Znamy dosledok sa hlasi nahlas -------------------------------------------


def test_the_same_service_under_another_name_is_not_reported_missing(tmp_path):
    """Falosny poplach, zmerany 15.09.2026 na kopii skutocnej instalacie MAGERSTAVU: hlasilo
    ``['db', 'migrate']``, hoci to su TIE ISTE sluzby, len sa v instalacii volaju ``postgres`` a
    ``alembic-init``. Takto by to varovalo pri KAZDEJ prevzatej instalacii — a varovanie, ktore
    chodi vzdy, naucí cloveka ignorovat aj to pravdive.

    Porovnava sa preto ULOHA (databaza, chrbtica, obrazovky), nie meno.
    """
    d = _instalacia(tmp_path)
    zdroj = {
        "services": {
            "db": {"image": "postgres:16-alpine"},
            "migrate": {"image": "nex-inbox-backend", "command": ["alembic", "upgrade", "head"]},
            "backend": {"image": "nex-inbox-backend"},
        }
    }
    assert P.services_missing_against_source(d, zdroj) == []


def test_a_service_the_project_gained_is_reported_not_silently_added(tmp_path):
    """Instalacia novu sluzbu sama nedostane. To sa musi POVEDAT — nie potichu dorobit prestavbou,
    lebo prave prestavba je to, co tento tiket rusi."""
    d = _instalacia(tmp_path)
    zdroj = {
        "services": {
            "backend": {"image": "nex-inbox-backend"},
            "db": {"image": "postgres:16-alpine"},
            "migrate": {"image": "nex-inbox-backend", "command": ["alembic", "upgrade", "head"]},
            "redis": {"image": "redis:7"},
        }
    }
    assert P.services_missing_against_source(d, zdroj) == ["redis"]


def test_an_instance_matching_the_project_reports_nothing(tmp_path):
    d = _instalacia(tmp_path)
    assert (
        P.services_missing_against_source(
            d,
            {"services": {"backend": {"image": "nex-inbox-backend"}, "db": {"image": "postgres:16-alpine"}}},
        )
        == []
    )
