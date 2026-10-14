"""DEV-102 — the check after a deploy asks the address the project really lives at, private or public.

13.10.2026, Career Asistent 0.1.1 on UAT for ICC: the app ran and answered at its private address
``uat-icc-career-asistent.int.isnex.eu`` (a project reachable only from the private network, DEV-42), yet the deploy
was reported failed — „verejná adresa uat-icc-career-asistent.isnex.eu nie je dostupná — smerovanie zlyhalo: 404“.
The check built the address itself, always in the public zone; every other place takes it from
``uat_provisioner.domain_suffix`` — „the ONE place that decides (DEV-42)“.

The expected address is still derived from the naming rule, never read off the router's label: a wrong label is exactly
what the check exists to catch (andros-payables, 10.07.2026).
"""

from __future__ import annotations

import ast
import contextlib
import io
import types
import urllib.request
from pathlib import Path
from unittest import mock

import pytest

from backend.services import orchestrator

BACKEND = Path(__file__).resolve().parents[1] / "backend"
COMPOSE = "services:\n  backend:\n    build: .\n  frontend:\n    build: ./frontend\n"


class _Containers:
    """``docker compose`` as the probes see it — every container answers; records the Host the route probe sends."""

    def __init__(self) -> None:
        self.hosts: list[str] = []

    def _urlopen(self, url, timeout=None):  # noqa: ARG002 — the probe's own signature
        if isinstance(url, urllib.request.Request):
            self.hosts.append(url.get_header("Host"))
        return types.SimpleNamespace(status=200)

    async def __call__(self, cmd, timeout, env=None):  # noqa: ARG002 — _compose_smoke_step's signature
        if "python" not in cmd:
            return 0, ""
        source = cmd[cmd.index("-c") + 1]
        out, code = io.StringIO(), 0
        with mock.patch.object(urllib.request, "urlopen", self._urlopen), contextlib.redirect_stdout(out):
            try:
                exec(compile(source, "<probe>", "exec"), {})  # noqa: S102 — the cockpit's own probe
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
        return code, out.getvalue()


@pytest.fixture
def career_on_uat(monkeypatch, tmp_path) -> _Containers:
    (tmp_path / "uat" / "icc" / "career-asistent").mkdir(parents=True)
    (tmp_path / "uat" / "icc" / "career-asistent" / "docker-compose.yml").write_text("services: {}\n")
    (tmp_path / "projects" / "career-asistent").mkdir(parents=True)
    (tmp_path / "projects" / "career-asistent" / "docker-compose.yml").write_text(COMPOSE)
    monkeypatch.setattr(orchestrator, "UAT_ROOT", tmp_path / "uat")
    monkeypatch.setattr(orchestrator.claude_agent, "PROJECTS_ROOT", tmp_path / "projects")

    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(orchestrator.asyncio, "sleep", _no_sleep)
    containers = _Containers()
    monkeypatch.setattr(orchestrator, "_compose_smoke_step", containers)
    return containers


@pytest.mark.parametrize(
    ("private", "address"),
    [(True, "uat-icc-career-asistent.int.isnex.eu"), (False, "uat-icc-career-asistent.isnex.eu")],
)
async def test_the_route_is_asked_at_the_projects_own_address(career_on_uat, private, address):
    ok, detail = await orchestrator._verify_uat_serves(
        "career-asistent",
        "icc-career-asistent",
        customer_slug="icc",
        app="career-asistent",
        full_project_slug="career-asistent",
        private=private,
    )

    assert (ok, detail) == (True, "OK")
    assert career_on_uat.hosts and set(career_on_uat.hosts) == {address}


async def test_a_deploy_hands_the_projects_choice_to_the_check(monkeypatch):
    seen: list[bool] = []

    async def _check(*_a, private=False, **_k):
        seen.append(private)
        return False, "stop here"

    async def _step(*_a, **_k):
        return True, ""

    async def _migrated(*_a, **_k):
        return True, ""

    async def _no_provenance(*_a, **_k):
        return {}

    monkeypatch.setattr(orchestrator, "_verify_uat_serves", _check)
    monkeypatch.setattr(orchestrator, "_deploy_compose_step", _step)
    monkeypatch.setattr(orchestrator, "_run_post_up_migration", _migrated)
    monkeypatch.setattr(orchestrator, "_build_provenance_env", _no_provenance)
    monkeypatch.setattr(orchestrator, "_uat_compose_path", lambda *a, **k: Path(__file__))

    async def _backed_up(*_a, **_k):
        # DEV-116's backup before ``up`` is not what this test is about; it has its own tests.
        from backend.services.predeploy_backup import BackupOutcome

        return BackupOutcome(True, "")

    monkeypatch.setattr(orchestrator.predeploy_backup, "back_up", _backed_up)

    await orchestrator._run_prod_deploy("career-asistent", "icc", "career-asistent", "career-asistent", private=True)

    assert seen == [True]


def _python_sources() -> list[Path]:
    return [p for p in BACKEND.rglob("*.py") if "tests" not in p.relative_to(BACKEND).parts]


def test_only_the_naming_rule_composes_an_address_in_the_public_zone():
    """A host in the public zone built anywhere else skips the private choice — the very bug of 13.10.2026."""
    users = []
    for path in _python_sources():
        if path.name == "uat_provisioner.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            name = node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else None
            if name == "UAT_DOMAIN_SUFFIX":
                users.append(f"{path.relative_to(BACKEND)}:{node.lineno}")

    assert users == []


def test_every_deploy_tells_the_check_whether_the_project_is_private():
    """Every call of the deploy runners and of the check outside tests passes ``private=`` — a forgotten one falls back
    to the public zone without a word."""
    names = {"_run_uat_deploy", "_run_prod_deploy", "_verify_uat_serves"}
    calls, silent = 0, []
    for path in _python_sources():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else None
            if name in names:
                calls += 1
                if not any(kw.arg == "private" for kw in node.keywords):
                    silent.append(f"{path.relative_to(BACKEND)}:{node.lineno} {name}")

    assert calls >= 6, "the rule found no deploy calls — it would measure nothing"
    assert silent == []
