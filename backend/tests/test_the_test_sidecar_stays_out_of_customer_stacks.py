"""Bočný kontajner s testami nepatrí do zákazníckeho stacku (ICCINT-60).

Existuje preto, aby CI spustilo sadu proti skutočnému Postgresu. Nasadenie ho skopírovalo spolu so
všetkým ostatným a v UAT nemá testovaciu databázu (``TEST_DATABASE_URL`` sa vyrenderuje prázdna a
správne sa vynechá, ICCINT-58), takže pri každom nasadení zlyhá — 727 chýb na nex-productcatalogs
0.1.2. Je to šum, ktorý vyzerá presne ako rozbitá appka a zakryje skutočné zlyhanie vedľa seba.

⚠️ **Rozpoznáva sa podľa obsahu, nie podľa názvu** — rovnako ako migračná služba
(``has_alembic_migrate_service``). Názov služby je vec autora appky, správanie je naša.
"""

from __future__ import annotations

from typing import Any, Optional

from backend.services import uat_provisioner as u

ROLES: dict[str, Optional[str]] = {"backend": "backend", "frontend": "frontend", "db": "db"}


def _svc(**kwargs: Any) -> dict[str, Any]:
    return dict(kwargs)


def test_a_service_that_asks_for_a_test_database_says_what_it_is() -> None:
    assert u.is_test_runner_service("test", _svc(environment={"TEST_DATABASE_URL": "x"}), ROLES) is True
    # A ``KEY=value`` list is the other legal compose form — reading only mappings would miss half the apps.
    assert u.is_test_runner_service("qa", _svc(environment=["TEST_DATABASE_URL=x"]), ROLES) is True


def test_the_app_itself_is_never_excluded() -> None:
    """Tri guľky do vlastnej nohy, ktoré toto pravidlo nesmie vystreliť: appka bez backendu, bez frontendu
    alebo bez databázy je horšia než hlučné nasadenie."""
    for role_name in ("backend", "frontend", "db"):
        svc = _svc(environment={"TEST_DATABASE_URL": "x"})
        assert u.is_test_runner_service(role_name, svc, ROLES) is False, (
            f"{role_name} sa vynechal zo stacku — zákazník by dostal appku bez kusu"
        )


def test_the_migrate_service_is_never_excluded() -> None:
    """Stack na ňu čaká cez ``service_completed_successfully``; vynechať ju znamená zaseknuté nasadenie."""
    svc = _svc(command=["alembic", "upgrade", "head"], environment={"TEST_DATABASE_URL": "x"})
    assert u.is_test_runner_service("migrate", svc, ROLES) is False


def test_a_service_others_wait_for_is_load_bearing() -> None:
    src = {
        "test": _svc(environment={"TEST_DATABASE_URL": "x"}),
        "backend": _svc(depends_on=["test"]),
    }
    assert u._depended_on_by_others("test", src) is True
    assert u._depended_on_by_others("backend", src) is False


def test_the_real_project_compose_yields_exactly_the_test_service() -> None:
    """⚠️ Nie vymyslený súbor — TEN, na ktorom sa chyba stala.

    Vymyslené vstupy potvrdzujú predstavu autora pravidla. Toto číta skutočný ``docker-compose.yml``
    projektu nex-productcatalogs a žiada, aby sa vynechala práve jedna služba a práve tá.
    """
    from pathlib import Path

    project = Path("/opt/projects/nex-productcatalogs")
    if not (project / "docker-compose.yml").is_file():
        import pytest

        pytest.skip("projekt nie je na disku")

    services = u.load_source_compose(project)["services"]
    roles = u.identify_service_roles(services)
    skipped = {
        name
        for name, svc in services.items()
        if u.is_test_runner_service(name, svc, roles) and not u._depended_on_by_others(name, services)
    }

    assert skipped == {"test"}, f"vynechalo sa {skipped or 'nič'} — malo sa vynechať práve 'test'"


def test_the_rendered_stack_parks_the_sidecar_behind_a_profile() -> None:
    """A to hlavné: nie čo si pravidlo myslí, ale čo naozaj skončí v súbore u zákazníka.

    Služba s profilom sa obyčajným ``docker compose up`` nespustí — a zároveň v súbore ostáva, takže
    vyrenderovaný compose je naďalej verná kópia originálu a kto ho chce spustiť, môže cez
    ``--profile ci``. Vymazať ju by znamenalo, že zákazníkov súbor tvrdí o projekte nepravdu.
    """
    from pathlib import Path

    project = Path("/opt/projects/nex-productcatalogs")
    if not (project / "docker-compose.yml").is_file():
        import pytest

        pytest.skip("projekt nie je na disku")

    source = u.load_source_compose(project)
    compose = u.build_uat_compose(
        slug="acme-app",
        project="nex-productcatalogs",
        project_path=project,
        source=source,
        roles=u.identify_service_roles(source["services"]),
        db_user="app",
        db_name="app",
        customer_slug="acme",
        app="app",
    )
    services = compose["services"]

    assert services["test"].get("profiles") == [u.TEST_RUNNER_PROFILE], (
        "bočný kontajner s testami sa u zákazníka stále spúšťa"
    )
    assert "test" in services, "kontajner sa vymazal — súbor má ostať vernou kópiou, len sa nespúšťať"
    for part in ("backend", "frontend", "db", "migrate"):
        assert "profiles" not in services[part], f"{part} sa omylom odparkoval a appka by nenabehla"
