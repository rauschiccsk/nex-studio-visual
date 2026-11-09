"""DEV-330 — a deploy of an app the NEX Manager already knows, whose installation lacks its key there, gets a new key.

09.11.2026 NEX Websites 1.1.0 went onto ICC's UAT and could not be opened from NEX Manager: „Aplikácia ešte nie je
prepojená s NEX Managerom“ (503). An app on the template asks the Manager who the person is and shows its own key for
it (``NEX_MANAGER_API_KEY``). The Manager hands that key out once, when it registers the app, and keeps only its hash.
The 1.0 deploy registered NEX Websites while it did not use the key, so it was written nowhere; every later deploy
found the module registered and went on without a key (measured: the key's length in the app 0). The Manager has
a door for a new key of a module it knows (``POST /modules/{id}/rotate-identity-key``) under the same provisioning
token the cockpit registers with. Director 09.11.2026 to the fix: „Súhlasím“ — a new key only where the installation
has none, so a working key is never made invalid.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from backend.services import uat_provisioner as P
from tests.test_a_redeploy_pairs_the_app_with_its_manager_again import COMPOSE, _manager

MANAGER = P.PairedManager({"PROVISIONING_TOKEN": "prov-FAKE"}, api_url="http://manager:8000")
KNOWN = [{"id": "m-1", "slug": "nex-websites", "base_url": "https://uat-icc-websites.isnex.eu"}]


@pytest.fixture()
def manager_api(monkeypatch):
    """The Manager's module API: who asked what, and the answer each door gives."""
    calls: list[tuple[str, str]] = []
    answers = {"rotate": (200, {"identity_key": "rotated-FAKE"}), "modules": (200, KNOWN)}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        assert request.headers.get("X-Provisioning-Token") == "prov-FAKE"
        if request.url.path.endswith("/rotate-identity-key"):
            status, body = answers["rotate"]
        elif request.method == "POST":
            status, body = 201, {"identity_key": "created-FAKE"}
        else:
            status, body = answers["modules"]
        return httpx.Response(status, content=json.dumps(body))

    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return calls, answers


def _register(needs_key: bool):
    return P.ensure_module_registered(
        MANAGER,
        slug="nex-websites",
        display_name="NEX Websites",
        app_url="https://uat-icc-websites.isnex.eu",
        needs_key=needs_key,
    )


def test_a_known_app_without_its_key_gets_a_new_one(manager_api):
    calls, _ = manager_api

    assert _register(needs_key=True) == ("rotated-FAKE", None)
    assert calls == [("GET", "/api/v1/modules"), ("POST", "/api/v1/modules/m-1/rotate-identity-key")]


def test_a_known_app_that_has_its_key_keeps_it_no_new_key_is_issued(manager_api):
    calls, _ = manager_api

    assert _register(needs_key=False) == (None, None)
    assert calls == [("GET", "/api/v1/modules")]


def test_a_new_app_is_registered_and_gets_its_first_key_as_before(manager_api):
    calls, answers = manager_api
    answers["modules"] = (200, [])

    assert _register(needs_key=True) == ("created-FAKE", None)
    assert calls == [("GET", "/api/v1/modules"), ("POST", "/api/v1/modules")]


def test_a_manager_that_will_not_give_a_new_key_does_not_sink_the_deploy_and_says_what_to_do(manager_api):
    _, answers = manager_api
    answers["rotate"] = (500, {"detail": "x"})

    key, warning = _register(needs_key=True)

    assert key is None
    assert warning == P.NEW_KEY_REFUSED.format(slug="nex-websites", reason="HTTPStatusError")
    assert "Nasadiť znova" in warning


# ── the deploy asks for a new key exactly when the installation has none ───────────────────────────────────────────

ENV_1_0 = "MANAGER_LAUNCH_SIGNING_KEY=\nMANAGER_MODULE_SLUG=nex-inbox\nMANAGER_DEPLOY_SLUG=\nINVOICE_PREFIX=INB\n"
ENV_1_1 = ENV_1_0 + "NEX_MANAGER_BASE_URL=\nNEX_MANAGER_API_KEY=\n"


class _Manager:
    """The Manager's registry as the deploy meets it: a key comes out at registration, or when asked anew."""

    def __init__(self):
        self.modules: set[str] = set()
        self.issued = 0
        self.asked: list[bool] = []

    def register(self, manager, *, slug, display_name, app_url, needs_key=False):
        self.asked.append(needs_key)
        if slug in self.modules and not needs_key:
            return None, None
        self.modules.add(slug)
        self.issued += 1
        return f"module-key-{self.issued}-FAKE", None


def _deploy(tmp_path: Path, monkeypatch, registry: _Manager, example: str, version: str):
    project = tmp_path / "projects" / "nex-inbox"
    project.mkdir(parents=True, exist_ok=True)
    (project / "docker-compose.yml").write_text(COMPOSE, encoding="utf-8")
    (project / ".env.example").write_text(example, encoding="utf-8")
    monkeypatch.setattr(P, "_docker_image_exists", lambda image: True)
    monkeypatch.setattr(P, "ensure_module_registered", registry.register)
    result = P.provision_uat(
        "nex-inbox",
        "icc-uat",
        projects_root=tmp_path / "projects",
        uat_root=tmp_path / "uat",
        prod_root=tmp_path / "customers",
        environment="uat",
        customer_slug="mager",
        app="inbox",
        full_project_slug="nex-inbox",
        version=version,
    )
    return result, P._parse_env_file(result.env_path)


def test_the_version_that_starts_using_the_key_gets_one_from_the_manager_that_knows_the_app(tmp_path, monkeypatch):
    registry = _Manager()
    _manager(tmp_path, "launch-key-FAKE")
    _deploy(tmp_path, monkeypatch, registry, ENV_1_0, "1.0.1")  # registered; the key the app did not use is gone

    result, env = _deploy(tmp_path, monkeypatch, registry, ENV_1_1, "1.1.0")

    assert registry.asked == [False, True]
    assert env["NEX_MANAGER_API_KEY"] == "module-key-2-FAKE"
    # what the version added is said too — the installation of 1.0 had neither the address nor the key
    renewed = P.PAIRING_RENEWED.format(what="adresa NEX Managera, nová registrácia v NEX Manageri")
    assert renewed in result.warnings, result.warnings


def test_an_installation_that_has_its_key_never_asks_for_a_new_one(tmp_path, monkeypatch):
    registry = _Manager()
    _manager(tmp_path, "launch-key-FAKE")
    _, first = _deploy(tmp_path, monkeypatch, registry, ENV_1_1, "1.1.0")

    result, again = _deploy(tmp_path, monkeypatch, registry, ENV_1_1, "1.1.1")

    assert registry.asked[-1] is False
    assert again["NEX_MANAGER_API_KEY"] == first["NEX_MANAGER_API_KEY"] == "module-key-1-FAKE"
    assert not [w for w in result.warnings if w.startswith("Aplikácia sa znova prepojila")]


def test_the_installation_the_failed_deploy_left_with_an_empty_key_gets_one_and_says_so(tmp_path, monkeypatch):
    """NEX Websites on ICC's UAT as 09.11.2026 left it: the Manager's address filled, the key's line empty (measured
    without the value: the line is there once, with nothing after ``=``)."""
    registry = _Manager()
    _manager(tmp_path, "launch-key-FAKE")
    first, _ = _deploy(tmp_path, monkeypatch, registry, ENV_1_0, "1.0.1")
    left = "NEX_MANAGER_BASE_URL=http://uat-mager-manager-backend:8000\nNEX_MANAGER_API_KEY=\n"
    first.env_path.write_text(first.env_path.read_text(encoding="utf-8") + left, encoding="utf-8")

    result, env = _deploy(tmp_path, monkeypatch, registry, ENV_1_1, "1.1.0")

    assert registry.asked == [False, True]
    assert env["NEX_MANAGER_API_KEY"] == "module-key-2-FAKE"
    assert P.PAIRING_RENEWED.format(what="nová registrácia v NEX Manageri") in result.warnings, result.warnings
