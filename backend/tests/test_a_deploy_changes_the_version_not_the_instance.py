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

import pytest

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


# -- Premenna, ktoru NOVA VERZIA potrebuje, sa k instalacii dostane (ICCINT-104) -----


ZDROJ_S_NOVOU_PREMENNOU = {
    "services": {
        "db": {"image": "postgres:16-alpine", "environment": {"POSTGRES_DB": "nex_inbox_dev"}},
        "backend": {
            "image": "nex-inbox-backend:v1.5.0",
            "build": {"context": "./backend"},
            "environment": {
                "APP_VERSION": "${VITE_APP_VERSION:-1.0.0}",
                "TENANT_SLUG": "dev",
            },
        },
    }
}


def test_a_new_env_key_the_version_needs_reaches_the_instance(tmp_path):
    """24.09.2026, ICCINT-104: NEX Manager 1.2.2 naucil chrbticu hlasit svoju verziu a projekt si
    do svojho compose napisal, odkial ma to cislo dostat. Nasadena instalacia to cislo aj tak
    nedostala — instalacia sa pri opakovanom nasadeni neprestavuje, takze NOVA premenna sa k nej
    nema ako dostat. Chrbtica potom hlasi ``0.0.0-dev`` a zelene ``Nasadene`` to zakryje."""
    d = _instalacia(tmp_path)

    novy = P.render_version_bump(
        d, version="1.5.0", project_slug="nex-inbox", source=ZDROJ_S_NOVOU_PREMENNOU, project_path=tmp_path
    )

    prostredie = novy["services"]["backend"].get("environment") or {}
    assert "APP_VERSION" in prostredie, "premenna, ktoru nova verzia potrebuje, k instalacii nedosla"
    assert prostredie["APP_VERSION"] == "${VITE_APP_VERSION:-1.0.0}"


def test_an_existing_value_is_never_overwritten_by_the_source(tmp_path):
    """Druhy smer — a je to septembrova lekcia: prestavba instalacie prepisala OSTRE hodnoty
    vyvojovymi (``nex_inbox_dev``, ``dev``). Doplnit chybajucu premennu sa smie; prepisat
    existujucu nikdy."""
    d = _instalacia(tmp_path)

    novy = P.render_version_bump(
        d, version="1.5.0", project_slug="nex-inbox", source=ZDROJ_S_NOVOU_PREMENNOU, project_path=tmp_path
    )

    assert novy["services"]["postgres"]["environment"]["POSTGRES_DB"] == "nex_inbox_mager", (
        "nasadenie prepisalo databazu instalacie hodnotou zo zdrojaku"
    )


def test_what_was_added_is_named(tmp_path):
    """Doplnenie sa nesmie stat ticho. Kto to potom hlada, ma vidiet, CO pribudlo — rovnako, ako
    sa dnes nahlas hovori o chybajucej sluzbe."""
    d = _instalacia(tmp_path)

    chyba = P.env_keys_missing_against_source(d, ZDROJ_S_NOVOU_PREMENNOU)

    assert chyba == {"backend": ["APP_VERSION"]}, chyba


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


# -- Povysena znacka musi mat kto postavit (ICCINT-137) -----------------------
#
# Zakazana operacia: nasadenie povysi znacku na obraz, ktory NEEXISTUJE a nema ho kto postavit.
# ``up -d --build`` postavi len sluzbu s ``build:``; prevzata instalacia ho nema (obrazy su rucne
# pripnute), takze compose obraz iba hlada — a register nemame. 15.09.2026 to zhodilo nasadenie
# NEX Inboxu 1.5.1 a nechalo instalaciu ukazovat na obraz, ktory neexistuje: jeden restart a UAT
# by nenabehol.

ZDROJ = {
    "services": {
        "db": {"image": "postgres:16-alpine"},
        "migrate": {"build": {"context": "."}, "image": "nex-inbox-backend:dev"},
        "backend": {"build": {"context": "."}, "image": "nex-inbox-backend:dev"},
        "frontend": {"build": "./frontend", "image": "nex-inbox-frontend:dev"},
    }
}


def test_the_bumped_service_gets_a_way_to_be_built(tmp_path):
    """Bez ``build:`` compose povyseny obraz iba hlada. Parovat sa musi podla MENA OBRAZU, nie podla
    mena sluzby: ``alembic-init`` potrebuje ``nex-inbox-backend``, ktory vo zdroji stavia sluzba
    ``backend`` — mena sluzieb sa nezhoduju a uloha ``migrate`` v ``identify_service_roles`` ani
    neexistuje, takze parovanie cez nu by tuto sluzbu ticho minulo."""
    d = _instalacia(tmp_path)
    zdrojovy_projekt = tmp_path / "projects" / "nex-inbox"
    zdrojovy_projekt.mkdir(parents=True)

    novy = P.render_version_bump(d, version="1.5.0", source=ZDROJ, project_path=zdrojovy_projekt)

    assert novy["services"]["backend"]["build"]["context"] == str(zdrojovy_projekt)
    assert novy["services"]["alembic-init"]["build"]["context"] == str(zdrojovy_projekt), (
        "alembic-init nedostal build — parovalo sa podla mena sluzby, nie podla mena obrazu"
    )


def test_a_foreign_image_never_gains_a_build(tmp_path):
    """Postgres sa neberie zo zdrojakov. Keby dostal ``build:``, nasadenie by prestavovalo databazu."""
    d = _instalacia(tmp_path)
    zdrojovy_projekt = tmp_path / "projects" / "nex-inbox"
    zdrojovy_projekt.mkdir(parents=True)

    novy = P.render_version_bump(d, version="1.5.0", source=ZDROJ, project_path=zdrojovy_projekt)

    assert "build" not in novy["services"]["postgres"]


def test_the_instances_own_build_is_not_overwritten(tmp_path):
    """Ked si instalacia ``build:`` nesie sama, je to jej rozhodnutie — nasadenie do neho nesiaha."""
    d = _instalacia(tmp_path)
    obsah = (d / "docker-compose.yml").read_text(encoding="utf-8")
    (d / "docker-compose.yml").write_text(
        obsah.replace(
            "  backend:\n    image: nex-inbox-backend:v1.4.0",
            "  backend:\n    build: /vlastna/cesta\n    image: nex-inbox-backend:v1.4.0",
        ),
        encoding="utf-8",
    )
    zdrojovy_projekt = tmp_path / "projects" / "nex-inbox"
    zdrojovy_projekt.mkdir(parents=True)

    novy = P.render_version_bump(d, version="1.5.0", source=ZDROJ, project_path=zdrojovy_projekt)

    assert novy["services"]["backend"]["build"] == "/vlastna/cesta"


def test_without_a_source_nothing_changes(tmp_path):
    """Protivaha: volanie bez zdroja sa sprava presne ako dosial."""
    d = _instalacia(tmp_path)

    novy = P.render_version_bump(d, version="1.5.0")

    assert "build" not in novy["services"]["backend"]


def test_the_carry_over_actually_reaches_the_written_file(tmp_path):
    """⚠️ Spoj, nie okolie spoja. Skusky vyssie volaju ``render_version_bump`` priamo, takze by presli
    aj vtedy, keby ``provision_uat`` zdroj neodovzdal — a v priecinku zakaznika by aj tak stal compose
    bez ``build:``, teda presne to, co 15.09.2026 zhodilo nasadenie."""
    if not (Path("/opt/projects/nex-inbox") / "docker-compose.yml").is_file():
        pytest.skip("vzorovy projekt nie je na tomto stroji — nekontrolovane")

    d = tmp_path / "skuska" / "nex-inbox"
    d.mkdir(parents=True)
    # Prevzata instalacia hlavicku UZ nesie — prevzatie ju tam zapise. Bez nej by zasiahla straz na
    # rucne pisane nasadenia a skuska by merala ju, nie to, co ma merat.
    (d / "docker-compose.yml").write_text(f"# {P.GENERATED_BY_MARKER}\n{ZIVY}", encoding="utf-8")
    (d / ".env").write_text("POSTGRES_DB=x\nPOSTGRES_PASSWORD=y\nPROJECT_VERSION=1.4.0\n", encoding="utf-8")

    P.provision_uat(
        "nex-inbox",
        "skuska-uat",
        version="1.5.9",
        uat_root=tmp_path,
        customer_slug="skuska",
        app="nex-inbox",
        full_project_slug="nex-inbox",
    )
    napisane = (d / "docker-compose.yml").read_text(encoding="utf-8")

    assert ":v1.5.9" in napisane or ":1.5.9" in napisane, "verzia sa do suboru nedostala"
    assert "build:" in napisane, "zapisal sa compose bez build: — povysenu znacku nema kto postavit"


# -- Compose, ktory sa neda nasadit, sa NEZAPISE ------------------------------


def test_a_refused_deploy_leaves_the_instance_exactly_as_it_was(tmp_path, monkeypatch):
    """Jadro ICCINT-137. 15.09.2026 nasadenie zapisalo povysenu znacku, POTOM padlo na tom, ze obraz
    neexistuje — a instalacia zostala ukazovat na obraz, ktory nikde nie je. Kontajnery bezali dalej,
    ale jeden restart a UAT by nenabehol. Odmietnutie sa preto musi stat PRED zapisom."""
    if not (Path("/opt/projects/nex-inbox") / "docker-compose.yml").is_file():
        pytest.skip("vzorovy projekt nie je na tomto stroji — nekontrolovane")

    d = tmp_path / "skuska" / "nex-inbox"
    d.mkdir(parents=True)
    # Sluzba, ktorej znacku nasadenie PREPISE (obraz je nas), ale zdroj ju uz nestavia — teda
    # ``build:`` nedostane a povysena znacka nikde nie je. Presne tvar chyby z 15.09.2026.
    zivy = f"# {P.GENERATED_BY_MARKER}\n" + ZIVY + "  worker:\n    image: nex-inbox-worker:v1.4.0\n"
    (d / "docker-compose.yml").write_text(zivy, encoding="utf-8")
    (d / ".env").write_text("POSTGRES_DB=x\nPOSTGRES_PASSWORD=y\nPROJECT_VERSION=1.4.0\n", encoding="utf-8")
    pred_compose = (d / "docker-compose.yml").read_text(encoding="utf-8")
    pred_env = (d / ".env").read_text(encoding="utf-8")
    monkeypatch.setattr(P, "_docker_image_exists", lambda obraz: "worker" not in obraz)

    with pytest.raises(P.UndeployableImageError) as chyba:
        P.provision_uat(
            "nex-inbox",
            "skuska-uat",
            version="1.5.9",
            uat_root=tmp_path,
            customer_slug="skuska",
            app="nex-inbox",
            full_project_slug="nex-inbox",
        )

    assert "nex-inbox-worker:v1.5.9" in str(chyba.value), "odmietnutie nepomenovalo, ktory obraz chyba"
    assert (d / "docker-compose.yml").read_text(encoding="utf-8") == pred_compose, "compose sa zmenil"
    assert (d / ".env").read_text(encoding="utf-8") == pred_env, ".env sa zmenil"


def test_an_image_that_can_be_neither_built_nor_found_is_named():
    compose = {
        "services": {
            "postgres": {"image": "postgres:16-alpine"},
            "backend": {"image": "nex-inbox-backend:v9.9.9"},
            "frontend": {"image": "nex-inbox-frontend:v9.9.9", "build": {"context": "/x"}},
        }
    }

    pred = {
        "services": {
            meno: {**svc, "image": svc["image"].replace("v9.9.9", "v9.9.8")}
            for meno, svc in compose["services"].items()
        }
    }
    chybaju = P.unbuildable_images(pred, compose, image_exists=lambda o: o == "postgres:16-alpine")

    assert chybaju == ["backend=nex-inbox-backend:v9.9.9"], chybaju


def test_an_untouched_foreign_image_is_never_blamed():
    """Falosny poplach by bol horsi nez ta chyba. ``redis:7`` z verejneho registra sa DA stiahnut a
    nasadenie s nim dosial chodilo; zablokovat ho preto, ze este nie je na disku, by zastavilo
    nasadenia, ktore su v poriadku. Hlasi sa len to, comu nasadenie samo prepisalo znacku — take
    obrazy su nase a register na ne nemame."""
    pred = {"services": {"cache": {"image": "redis:7"}, "backend": {"image": "nex-inbox-backend:v1.4.0"}}}
    po = {"services": {"cache": {"image": "redis:7"}, "backend": {"image": "nex-inbox-backend:v1.5.0"}}}

    chybaju = P.unbuildable_images(pred, po, image_exists=lambda o: False)

    assert chybaju == ["backend=nex-inbox-backend:v1.5.0"], chybaju


def test_a_service_that_can_be_built_is_never_reported():
    """Sluzba s ``build:`` si obraz postavi sama — hlasit ju by bol falosny poplach pri KAZDOM
    prvom zriadeni, a varovanie, ktore chodi vzdy, sa prestane citat."""
    pred = {"services": {"backend": {"image": "nic:v0", "build": "."}}}
    po = {"services": {"backend": {"image": "nic:v1", "build": "."}}}

    assert P.unbuildable_images(pred, po, image_exists=lambda o: False) == []


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
