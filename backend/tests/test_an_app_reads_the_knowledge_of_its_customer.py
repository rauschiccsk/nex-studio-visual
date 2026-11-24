"""DEV-410 — an app that reads its customer's NEX Knowledge gets its address from the cockpit, and the deploy says
whether the read works.

16.11.2026: NEX Knowledge 1.1.0 lends its knowledge to the customer's other apps (``/api/v1/app-read/…``, an ``nkapp_``
key) and answers only a call that came straight — never through a proxy or Cloudflare. NEX Legal Watch and NEX
HelpDesk read it; the cockpit knew nothing of the link. Director: „Súhlasím“ (17.11.2026, to doing it now).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from backend.services import deploy as deploy_service
from backend.services import knowledge_link, remote_instance
from backend.services import uat_provisioner as P

KNOWLEDGE = """
name: uat-icc-knowledge
services:
  backend:
    container_name: uat-icc-knowledge-backend
    labels:
      - traefik.http.services.uat-icc-knowledge-backend.loadbalancer.server.port=8000
  frontend:
    container_name: uat-icc-knowledge-frontend
    labels:
      - traefik.http.services.uat-icc-knowledge-frontend.loadbalancer.server.port=80
"""
READER = """
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: app
      POSTGRES_PASSWORD: x
      POSTGRES_DB: app
  backend:
    build:
      context: .
    environment:
      NEX_KNOWLEDGE_BASE_URL: ${NEX_KNOWLEDGE_BASE_URL:-}
  frontend:
    build:
      context: ./frontend
"""


def _reader(tmp_path: Path, monkeypatch, *, reads: bool = True) -> None:
    monkeypatch.setattr(P, "_docker_image_exists", lambda image: True)
    project = tmp_path / "projects" / "nex-legal-watch"
    project.mkdir(parents=True)
    compose = (
        READER
        if reads
        else READER.replace("    environment:\n      NEX_KNOWLEDGE_BASE_URL: ${NEX_KNOWLEDGE_BASE_URL:-}\n", "")
    )
    (project / "docker-compose.yml").write_text(compose, encoding="utf-8")
    example = "SECRET_KEY=change-me\n" + ("NEX_KNOWLEDGE_BASE_URL=\n" if reads else "")
    (project / ".env.example").write_text(example, encoding="utf-8")


def _knowledge_installed(tmp_path: Path) -> None:
    here = tmp_path / "uat" / "icc" / "nex-knowledge"
    here.mkdir(parents=True)
    (here / "docker-compose.yml").write_text(KNOWLEDGE, encoding="utf-8")


def _deploy(tmp_path: Path):
    return P.provision_uat(
        "nex-legal-watch",
        "icc",
        projects_root=tmp_path / "projects",
        uat_root=tmp_path / "uat",
        customer_slug="icc",
        app="legal-watch",
        full_project_slug="nex-legal-watch",
        version="0.1.0",
    )


# --- the address ----------------------------------------------------------------------------------------------------


def test_the_address_is_the_backend_of_the_customers_knowledge_on_the_machines_network(tmp_path) -> None:
    _knowledge_installed(tmp_path)

    assert (
        P.knowledge_address(tmp_path / "uat" / "icc" / "nex-knowledge", None) == "http://uat-icc-knowledge-backend:8000"
    )
    assert P.knowledge_address(tmp_path / "uat" / "skit" / "nex-knowledge", None) is None


def test_on_another_machine_the_address_is_read_there(tmp_path, monkeypatch) -> None:
    asked: list = []

    def _read(instance_dir, filename, *, deploy_host, timeout=120):
        asked.append((str(instance_dir), filename, deploy_host))
        return KNOWLEDGE.replace("uat-icc", "icc"), None

    monkeypatch.setattr(remote_instance, "read_text", _read)

    url = P.knowledge_address(Path("/opt/customers/icc/nex-knowledge"), "mager")

    assert url == "http://icc-knowledge-backend:8000"
    assert asked == [("/opt/customers/icc/nex-knowledge", "docker-compose.yml", "mager")]


def test_a_new_installation_gets_the_address(tmp_path, monkeypatch) -> None:
    _reader(tmp_path, monkeypatch)
    _knowledge_installed(tmp_path)

    result = _deploy(tmp_path)

    assert P._parse_env_file(result.env_path)["NEX_KNOWLEDGE_BASE_URL"] == "http://uat-icc-knowledge-backend:8000"
    assert P.KNOWLEDGE_MISSING not in result.warnings


def test_a_redeploy_writes_the_address_anew(tmp_path, monkeypatch) -> None:
    """The link is the cockpit's, like the Manager's pairing — not a value the installation keeps (ICCINT-104)."""
    _reader(tmp_path, monkeypatch)
    first = _deploy(tmp_path)
    assert P._parse_env_file(first.env_path)["NEX_KNOWLEDGE_BASE_URL"] == ""
    assert P.KNOWLEDGE_MISSING in first.warnings

    _knowledge_installed(tmp_path)
    again = _deploy(tmp_path)

    assert again.env_path == first.env_path
    assert P._parse_env_file(again.env_path)["NEX_KNOWLEDGE_BASE_URL"] == "http://uat-icc-knowledge-backend:8000"


def test_an_app_that_does_not_read_it_gets_nothing(tmp_path, monkeypatch) -> None:
    _reader(tmp_path, monkeypatch, reads=False)
    _knowledge_installed(tmp_path)

    result = _deploy(tmp_path)

    assert "NEX_KNOWLEDGE_BASE_URL" not in P._parse_env_file(result.env_path)
    assert P.KNOWLEDGE_MISSING not in result.warnings


# --- the read after the deploy ------------------------------------------------------------------------------------


def _installation(tmp_path: Path) -> Path:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services:\n  backend:\n    container_name: uat-icc-legal-watch-backend\n", encoding="utf-8")
    return compose


@pytest.mark.parametrize(
    ("outcome", "said"),
    [
        ("OK 3", (knowledge_link.READS.format(areas="3"), [])),
        ("NOLINK", (None, [])),
        ("NOURL", (None, [P.KNOWLEDGE_MISSING])),
        ("NOKEY", (None, [knowledge_link.NO_KEY])),
        ("HTTP 401", (None, [knowledge_link.KEY_REFUSED])),
        ("HTTP 404", (None, [knowledge_link.NOT_STRAIGHT])),
        ("HTTP 500", (None, [knowledge_link.ANSWERED.format(code="500")])),
        (
            "DOWN URLError",
            (None, [knowledge_link.UNREACHABLE.format(why="URLError", service="uat-icc-legal-watch-backend")]),
        ),
    ],
)
def test_the_read_after_the_deploy_says_what_happened(tmp_path, monkeypatch, outcome, said) -> None:
    asked: list = []
    monkeypatch.setattr(knowledge_link, "_read", lambda container, host: asked.append((container, host)) or outcome)

    assert knowledge_link.check(_installation(tmp_path), None) == said
    assert asked == [("uat-icc-legal-watch-backend", None)]


def test_the_read_never_shows_the_key() -> None:
    """The key stays in the container: the read prints only its outcome."""
    assert "print(key" not in knowledge_link._READ and "print(url" not in knowledge_link._READ
    assert all(line.startswith("print(") is False or "key" not in line for line in knowledge_link._READ.splitlines())


@pytest.mark.asyncio
async def test_the_deploy_says_the_read(db_session, monkeypatch) -> None:
    from tests.test_deploy_service import _make_customer, _make_project, _make_user, _make_version

    user = _make_user(db_session)
    project = _make_project(db_session, user=user, slug=f"nex-legal-watch-{uuid.uuid4().hex[:6]}")
    _make_version(db_session, project, "0.1.0")
    customer = _make_customer(db_session, project)
    monkeypatch.setattr(
        knowledge_link, "check", lambda compose, host: (knowledge_link.READS.format(areas="2"), [P.KNOWLEDGE_MISSING])
    )

    async def _runner(**_kwargs):  # the provisioner said the knowledge is missing; the read says it too
        return deploy_service.RunnerResult(True, "OK", "https://uat-icc-legal-watch.isnex.eu", [P.KNOWLEDGE_MISSING])

    event, _url, _bumped = await deploy_service.deploy(
        db_session,
        customer.id,
        version_number="0.1.0",
        environment="uat",
        actor_id=user.id,
        deploy_runner=_runner,
    )

    assert event.detail.endswith(knowledge_link.READS.format(areas="2"))
    assert list(event.warnings).count(P.KNOWLEDGE_MISSING) == 1
