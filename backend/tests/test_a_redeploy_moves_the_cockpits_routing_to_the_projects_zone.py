"""DEV-423 — a redeploy moves the routing the cockpit wrote to the project's zone; an installation's own stays.

17.11.2026 NEX Manager 1.7.0 was deployed to the UAT of SKIT o.z. The project had been switched to the private network
the day before, so the cockpit named and checked the installation ``uat-skit-manager.int.isnex.eu`` — but a redeploy
kept the installation's routing, which the cockpit itself had written in the public zone. The installation answered
at ``uat-skit-manager.isnex.eu`` only, the check got 404 and the deploy ended „neoverené“. Director 17.11.2026:
„Súhlasím“.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from backend.services import uat_provisioner as P

PROJECT = {
    "services": {
        "db": {"image": "postgres:16", "environment": {"POSTGRES_USER": "nm", "POSTGRES_DB": "nm"}},
        "backend": {"build": {"context": "./backend"}, "depends_on": ["db"]},
        "frontend": {"build": {"context": "./frontend"}, "depends_on": ["backend"]},
    }
}
PUBLIC = "uat-skit-manager.isnex.eu"
PRIVATE = "uat-skit-manager.int.isnex.eu"


@pytest.fixture
def manager(tmp_path, monkeypatch) -> tuple[Path, Path]:
    projects, uat = tmp_path / "projects", tmp_path / "uat"
    project = projects / "nex-manager"
    for d in ("backend", "frontend"):
        (project / d).mkdir(parents=True)
    (project / "docker-compose.yml").write_text(yaml.safe_dump(PROJECT), encoding="utf-8")
    (project / ".env.example").write_text("# @public-url\nAPP_PUBLIC_URL=https://example.invalid\nSECRET_KEY=\n")
    monkeypatch.setattr(P, "_docker_image_exists", lambda image: True)
    return projects, uat


def _deploy(manager: tuple[Path, Path], version: str, *, private: bool) -> P.ProvisionResult:
    projects, uat = manager
    return P.provision_uat(
        "nex-manager",
        "skit",
        version=version,
        projects_root=projects,
        uat_root=uat,
        customer_slug="skit",
        app="manager",
        full_project_slug="nex-manager",
        private=private,
    )


def _compose_path(manager: tuple[Path, Path]) -> Path:
    return manager[1] / "skit" / "nex-manager" / "docker-compose.yml"


def _hosts(manager: tuple[Path, Path]) -> dict[str, list[str]]:
    services = yaml.safe_load(_compose_path(manager).read_text(encoding="utf-8"))["services"]
    return {
        name: sorted(label.split("=", 1)[1] for label in P._labels_as_list(svc.get("labels")) if ".rule=" in label)
        for name, svc in services.items()
        if any(".rule=" in label for label in P._labels_as_list(svc.get("labels")))
    }


def _env(manager: tuple[Path, Path]) -> dict[str, str]:
    return P._parse_env_file(manager[1] / "skit" / "nex-manager" / ".env")


def test_the_private_network_switched_on_moves_the_installation_to_the_private_zone(manager) -> None:
    _deploy(manager, "1.6.0", private=False)
    password = _env(manager)["POSTGRES_PASSWORD"]

    result = _deploy(manager, "1.7.0", private=True)

    assert _hosts(manager) == {
        "backend": [f"Host(`{PRIVATE}`) && PathPrefix(`/api`)"],
        "frontend": [f"Host(`{PRIVATE}`)"],
    }
    assert _env(manager)["APP_PUBLIC_URL"] == f"https://{PRIVATE}"
    assert _env(manager)["POSTGRES_PASSWORD"] == password, "the installation's database password changed"
    assert (
        "Smerovanie: projekt má prístup len zo súkromnej siete — inštalácia je odteraz na adrese "
        f"{PRIVATE}; doterajšie meno {PUBLIC} už do nej nevedie."
    ) in result.warnings


def test_the_private_network_switched_off_moves_it_back(manager) -> None:
    _deploy(manager, "1.6.0", private=True)

    result = _deploy(manager, "1.7.0", private=False)

    assert _hosts(manager)["frontend"] == [f"Host(`{PUBLIC}`)"]
    assert _env(manager)["APP_PUBLIC_URL"] == f"https://{PUBLIC}"
    assert (
        "Smerovanie: projekt už nemá prístup len zo súkromnej siete — inštalácia je odteraz na adrese "
        f"{PUBLIC}; doterajšie meno {PRIVATE} už do nej nevedie."
    ) in result.warnings


def test_a_routing_of_its_own_stays_in_place(manager) -> None:
    _deploy(manager, "1.6.0", private=False)
    path = _compose_path(manager)
    path.write_text(path.read_text(encoding="utf-8").replace(PUBLIC, "manager.skit.sk"), encoding="utf-8")

    result = _deploy(manager, "1.7.0", private=True)

    assert _hosts(manager)["frontend"] == ["Host(`manager.skit.sk`)"]
    assert not [w for w in result.warnings if w.startswith("Smerovanie:")]


def test_a_redeploy_in_the_same_zone_changes_nothing_of_the_routing(manager) -> None:
    _deploy(manager, "1.6.0", private=True)
    before = _hosts(manager)

    result = _deploy(manager, "1.7.0", private=True)

    assert _hosts(manager) == before
    assert not [w for w in result.warnings if w.startswith("Smerovanie:")]


def test_only_the_routing_moves() -> None:
    """The other labels of a routed service stay, and a service the cockpit does not route gets nothing."""
    bumped = {
        "services": {
            "frontend": {"labels": ["nex.customer=SKIT", "traefik.http.routers.a.rule=Host(`old`)"], "networks": ["n"]},
            "db": {"labels": ["nex.role=db"]},
        }
    }
    former = {"services": {"frontend": {"labels": ["traefik.http.routers.a.rule=Host(`old`)"]}, "db": {}}}
    now = {"services": {"frontend": {"labels": ["traefik.http.routers.a.rule=Host(`new`)"]}, "db": {}}}

    assert P.cockpits_routing_into_the_projects_zone(bumped, now, former, network="nex-proxy-net")

    assert bumped["services"]["frontend"]["labels"] == ["nex.customer=SKIT", "traefik.http.routers.a.rule=Host(`new`)"]
    assert "nex-proxy-net" in P._networks_as_list(bumped["services"]["frontend"]["networks"])
    assert bumped["services"]["db"] == {"labels": ["nex.role=db"]}


def test_an_installation_nobody_routes_is_not_moved() -> None:
    unrouted = {"services": {"worker": {"labels": ["nex.role=worker"]}}}

    assert not P.cockpits_routing_into_the_projects_zone(unrouted, unrouted, unrouted, network="nex-proxy-net")
