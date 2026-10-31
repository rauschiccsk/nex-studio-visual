"""DEV-272 — a deploy into an existing installation pairs the app with its customer's NEX Manager again.

28.10.2026 the UAT NEX Manager of MÁGERSTAV was rebuilt („Postaviť nanovo“): new keys, an empty database. Its NEX Inbox
kept the old launch key and was no longer registered — „Z NEX Managera sa nedá otvoriť: nex-inbox: podpisový kľúč
modulu sa nezhoduje s kľúčom NEX Managera … (urobí to technický tím)“. A redeploy of NEX Inbox kept every value it
found, the pairing too, and the registration it made threw away the module key the Manager hands out once. Now the
pairing comes from the Manager on every deploy where it runs, as in a render from scratch, and the warning names the
click that puts it right.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from backend.services import launch_pairing as L
from backend.services import uat_provisioner as P

COMPOSE = textwrap.dedent(
    """
    services:
      postgres:
        image: postgres:16-alpine
      backend:
        build: ./backend
      frontend:
        build: ./frontend
    """
)

ENV_EXAMPLE = textwrap.dedent(
    """
    MANAGER_LAUNCH_SIGNING_KEY=
    MANAGER_MODULE_SLUG=nex-inbox
    MANAGER_DEPLOY_SLUG=
    NEX_MANAGER_BASE_URL=
    NEX_MANAGER_API_KEY=
    INVOICE_PREFIX=INB
    """
)

MANAGER_COMPOSE = textwrap.dedent(
    """
    services:
      backend:
        container_name: uat-mager-manager-backend
        labels:
          - traefik.http.services.uat-mager-manager-api.loadbalancer.server.port=8000
    """
)


def _manager(tmp_path: Path, key: str) -> None:
    """MÁGERSTAV's UAT NEX Manager as its installation holds it — rebuilt = a new key."""
    folder = tmp_path / "uat" / "mager" / "nex-manager"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "docker-compose.yml").write_text(MANAGER_COMPOSE, encoding="utf-8")
    (folder / ".env").write_text(
        f"LAUNCH_SIGNING_KEY={key}\nDEPLOY_SLUG=uat-mager\nPROVISIONING_TOKEN=prov-FAKE\n", encoding="utf-8"
    )


class _Registry:
    """The Manager's module registry: a module key comes out once, when the module is created."""

    def __init__(self):
        self.modules: set[str] = set()
        self.issued = 0

    def register(self, manager_env_path, base_url, *, slug, display_name, app_url):
        assert base_url == "http://uat-mager-manager-backend:8000"
        if slug in self.modules:
            return None, None
        self.modules.add(slug)
        self.issued += 1
        return f"module-key-{self.issued}-FAKE", None

    def rebuilt(self):
        self.modules.clear()


def _deploy(tmp_path: Path, version: str):
    return P.provision_uat(
        "nex-inbox",
        "mager-uat",
        projects_root=tmp_path / "projects",
        uat_root=tmp_path / "uat",
        prod_root=tmp_path / "customers",
        environment="uat",
        customer_slug="mager",
        app="inbox",
        full_project_slug="nex-inbox",
        version=version,
    )


def _setup(tmp_path: Path, monkeypatch) -> _Registry:
    project = tmp_path / "projects" / "nex-inbox"
    project.mkdir(parents=True)
    (project / "docker-compose.yml").write_text(COMPOSE, encoding="utf-8")
    (project / ".env.example").write_text(ENV_EXAMPLE, encoding="utf-8")
    monkeypatch.setattr(P, "_docker_image_exists", lambda image: True)
    registry = _Registry()
    monkeypatch.setattr(P, "ensure_module_registered", registry.register)
    return registry


def _env(result) -> dict[str, str]:
    return P._parse_env_file(result.env_path)


def test_a_redeploy_after_the_manager_was_rebuilt_pairs_the_app_again(tmp_path, monkeypatch):
    registry = _setup(tmp_path, monkeypatch)
    _manager(tmp_path, "old-launch-key-FAKE")
    first = _deploy(tmp_path, "1.10.2")
    own = first.env_path.read_text(encoding="utf-8").replace("INVOICE_PREFIX=INB", "INVOICE_PREFIX=MAG")
    first.env_path.write_text(own, encoding="utf-8")  # a value the installation set for itself

    _manager(tmp_path, "new-launch-key-FAKE")  # 28.10.2026: „Postaviť nanovo“ of the Manager
    registry.rebuilt()
    result = _deploy(tmp_path, "1.10.3")

    env = _env(result)
    assert env["MANAGER_LAUNCH_SIGNING_KEY"] == "new-launch-key-FAKE"
    assert env["NEX_MANAGER_API_KEY"] == "module-key-2-FAKE", "the key the Manager handed out once was thrown away"
    assert env["MANAGER_DEPLOY_SLUG"] == "uat-mager" and env["NEX_MANAGER_BASE_URL"] == (
        "http://uat-mager-manager-backend:8000"
    )
    assert env["INVOICE_PREFIX"] == "MAG", "the installation's own value was overwritten"
    assert P.PAIRING_RENEWED.format(what="kľúč spustenia, nová registrácia v NEX Manageri") in result.warnings, (
        result.warnings
    )
    manager_env = P._parse_env_file(tmp_path / "uat" / "mager" / "nex-manager" / ".env")
    assert L.mismatches(manager_env, env, "nex-inbox") == []


def test_a_redeploy_with_nothing_to_renew_says_nothing(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _manager(tmp_path, "launch-key-FAKE")
    first = _deploy(tmp_path, "1.10.2")

    again = _deploy(tmp_path, "1.10.3")

    assert _env(again)["NEX_MANAGER_API_KEY"] == _env(first)["NEX_MANAGER_API_KEY"] == "module-key-1-FAKE"
    assert not [w for w in again.warnings if w.startswith("Aplikácia sa znova prepojila")]


def test_an_app_first_deployed_before_its_manager_is_paired_by_the_next_deploy(tmp_path, monkeypatch):
    """The pairing-gap warning has always said „nasaď NEX Manager a potom túto aplikáciu znova“ — now it is true."""
    _setup(tmp_path, monkeypatch)
    first = _deploy(tmp_path, "1.10.2")
    assert _env(first)["MANAGER_LAUNCH_SIGNING_KEY"] == ""

    _manager(tmp_path, "launch-key-FAKE")
    result = _deploy(tmp_path, "1.10.3")

    assert _env(result)["MANAGER_LAUNCH_SIGNING_KEY"] == "launch-key-FAKE"
    assert _env(result)["NEX_MANAGER_API_KEY"] == "module-key-1-FAKE"


# ── the warning names the click ─────────────────────────────────────────────────────────────────────────────────

MANAGER = {"LAUNCH_SIGNING_KEY": "new", "DEPLOY_SLUG": "uat-mager"}
STALE = {"MANAGER_LAUNCH_SIGNING_KEY": "old", "MANAGER_DEPLOY_SLUG": "uat-mager", "MANAGER_MODULE_SLUG": "nex-inbox"}


def test_the_warning_names_the_redeploy_where_it_repairs():
    said = L.customer_warning(MANAGER, {"nex-inbox": STALE}, redeploy_repairs=True)

    assert said == (
        "Z NEX Managera sa nedá otvoriť: nex-inbox: podpisový kľúč modulu sa nezhoduje s kľúčom NEX Managera. "
        "Aplikácie bežia — pokazené je otvorenie z NEX Managera; oprav ho tlačidlom „Nasadiť znova“ pri nex-inbox."
    )


def test_the_warning_leaves_it_to_the_team_where_a_redeploy_would_not_repair_it():
    on_another_machine = L.customer_warning(MANAGER, {"nex-inbox": STALE}, redeploy_repairs=False)
    other_slug = L.customer_warning(
        MANAGER, {"nex-inbox": {**STALE, "MANAGER_MODULE_SLUG": "inbox"}}, redeploy_repairs=True
    )
    no_manager_key = L.customer_warning({"DEPLOY_SLUG": "uat-mager"}, {"nex-inbox": STALE}, redeploy_repairs=True)

    for said in (on_another_machine, other_slug, no_manager_key):
        assert said is not None and said.endswith("nastavenia treba zladiť (urobí to technický tím).")


def test_an_app_that_does_not_open_from_the_manager_gets_no_pairing():
    env = {"DATABASE_URL": "postgresql://db/app"}

    assert P.renew_pairing(env, {"MANAGER_LAUNCH_SIGNING_KEY": "new", "NEX_MANAGER_API_KEY": "k"}) == []
    assert env == {"DATABASE_URL": "postgresql://db/app"}


def test_the_deploy_offers_the_click_only_where_it_repairs(tmp_path, monkeypatch):
    """An installation on another machine (PROD of MÁGERSTAV on MAGER) is not re-paired by this cockpit."""
    from backend.services import deploy as D
    from backend.services import remote_instance

    envs = {
        "nex-manager": "LAUNCH_SIGNING_KEY=new\nDEPLOY_SLUG=uat-mager\n",
        "nex-inbox": "MANAGER_LAUNCH_SIGNING_KEY=old\n",
    }
    monkeypatch.setattr(
        remote_instance, "read_text", lambda folder, name, *, deploy_host: (envs[Path(folder).name], None)
    )
    monkeypatch.setattr(P, "UAT_ROOT", tmp_path / "uat")
    for app, text in envs.items():
        (tmp_path / "uat" / "mager" / app).mkdir(parents=True)
        (tmp_path / "uat" / "mager" / app / ".env").write_text(text, encoding="utf-8")

    here = D._launch_pairing_warning("mager", "uat", None, ["nex-inbox"])
    there = D._launch_pairing_warning("mager", "prod", "mager", ["nex-inbox"])

    assert here is not None and here.endswith("oprav ho tlačidlom „Nasadiť znova“ pri nex-inbox.")
    assert there is not None and there.endswith("(urobí to technický tím).")
