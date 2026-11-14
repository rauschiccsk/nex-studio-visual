"""DEV-378 — the „Inštalácie“ page: every installation the cockpit deployed, by customer, UAT and PROD side by side.

13.11.2026 the Director: „mám už veľa ikonov na spúšťanie rôznych aplikácii … Je to len niekoľko zákazníkov a už
strácam prehľad … Navrhni mi prosím nejaké riešenie čo bude prehľadné aj pri 50+ zákazníkov.“ On the page as explained
(by customer, a version and whether it answers, „Otvoriť“, search and filters, from the cockpit's deploy records, the
owner sees theirs and the admin all, archived projects out): „Súhlasím, ideme to robiť.“
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from backend.core.authz import ADMIN_USERNAME
from backend.db.models.customers import Customer
from backend.db.models.deploy import DeployEvent
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.services import installations as I


def _user(db, username=None) -> User:
    user = User(
        username=username or f"u_{uuid.uuid4().hex[:8]}",
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        role="ri",
    )
    db.add(user)
    db.flush()
    return user


def _project(db, owner: User, slug: str, name: str, status: str = "active") -> Project:
    project = Project(
        name=name, slug=slug, type="standard", auth_mode="password", description="d", created_by=owner.id, status=status
    )
    db.add(project)
    db.flush()
    return project


def _customer(db, project: Project, name: str, slug: str) -> Customer:
    customer = Customer(project_id=project.id, name=name, slug=slug, subdomain=slug)
    db.add(customer)
    db.flush()
    return customer


def _deployed(db, customer: Customer, environment: str, version: str, status: str = "ok") -> None:
    db.add(
        DeployEvent(
            customer_id=customer.id,
            project_id=customer.project_id,
            version_number=version,
            environment=environment,
            event_type="deploy",
            status=status,
        )
    )
    db.flush()


@pytest.fixture
def world(db_session):
    """Two owners: ICC s.r.o. and MÁGERSTAV s.r.o. on NEX Manager of the first, ICC on NEX Websites of the second."""
    rausch, zoltan = _user(db_session), _user(db_session)
    admin = db_session.query(User).filter_by(username=ADMIN_USERNAME).one_or_none() or _user(db_session, ADMIN_USERNAME)
    manager = _project(db_session, rausch, f"nex-manager-{uuid.uuid4().hex[:4]}", "NEX Manager")
    websites = _project(db_session, zoltan, f"nex-websites-{uuid.uuid4().hex[:4]}", "NEX Websites")
    shelved = _project(db_session, rausch, f"nex-shopify-{uuid.uuid4().hex[:4]}", "NEX Shopify", status="archived")
    icc_manager = _customer(db_session, manager, "ICC s.r.o.", "icc")
    mager_manager = _customer(db_session, manager, "MÁGERSTAV s.r.o.", "mager")
    icc_websites = _customer(db_session, websites, "ICC s.r.o.", "icc")
    _customer(db_session, manager, "Nový s.r.o.", "novy")  # founded, never deployed
    _deployed(db_session, icc_manager, "uat", "1.6.0")
    _deployed(db_session, icc_manager, "prod", "1.6.0")
    _deployed(db_session, mager_manager, "uat", "1.6.0")
    _deployed(db_session, icc_websites, "uat", "1.1.0")
    _deployed(db_session, icc_websites, "uat", "1.2.0", status="failed")  # a failed attempt keeps the last good one
    _deployed(db_session, _customer(db_session, shelved, "ANDROS s.r.o.", "andros"), "uat", "0.2.0")
    return {"rausch": rausch, "zoltan": zoltan, "admin": admin, "manager": manager, "websites": websites}


def _mine(groups, *projects):
    """The groups narrowed to these test projects — the database of the suite may hold others."""
    slugs = {p.slug for p in projects}
    out = []
    for group in groups:
        apps = [a for a in group["apps"] if a["project_slug"] in slugs]
        if apps:
            out.append({**group, "apps": apps})
    return out


def test_the_admin_sees_every_customer_alphabetically_each_app_with_uat_and_prod(db_session, world):
    groups = _mine(I.list_installations(db_session, world["admin"]), world["manager"], world["websites"])

    assert [(g["name"], [a["project_name"] for a in g["apps"]]) for g in groups] == [
        ("ICC s.r.o.", ["NEX Manager", "NEX Websites"]),
        ("MÁGERSTAV s.r.o.", ["NEX Manager"]),
    ]
    icc_manager = groups[0]["apps"][0]
    assert icc_manager["uat"]["version"] == icc_manager["prod"]["version"] == "1.6.0"
    assert icc_manager["uat"]["url"].startswith("https://uat-icc-")
    assert groups[1]["apps"][0]["prod"] is None  # MÁGERSTAV has no PROD yet


def test_a_failed_attempt_keeps_the_last_good_version_and_says_it_failed(db_session, world):
    groups = _mine(I.list_installations(db_session, world["admin"]), world["websites"])

    uat = groups[0]["apps"][0]["uat"]
    assert uat["version"] == "1.1.0" and uat["last_attempt_failed"] is True


def test_an_owner_sees_only_the_installations_of_their_projects(db_session, world):
    groups = _mine(I.list_installations(db_session, world["zoltan"]), world["manager"], world["websites"])

    assert [(g["name"], [a["project_name"] for a in g["apps"]]) for g in groups] == [("ICC s.r.o.", ["NEX Websites"])]


def test_archived_projects_and_customers_never_deployed_stay_out(db_session, world):
    names = {g["name"] for g in I.list_installations(db_session, world["admin"])}
    apps = {a["project_name"] for g in I.list_installations(db_session, world["admin"]) for a in g["apps"]}

    assert "Nový s.r.o." not in names
    assert "NEX Shopify" not in apps


# ── whether an installation lives ──────────────────────────────────────────────────────────────────────────────────


class _Answer:
    def __init__(self, code, body=None, content_type="application/json"):
        self.status_code, self._body, self.headers = code, body, {"content-type": content_type}

    def json(self):
        return self._body


@pytest.fixture
def asked(monkeypatch):
    calls = []
    answers = {}

    def get(url, timeout):
        calls.append(url)
        answer = answers.get("next")
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(I.httpx, "get", get)
    monkeypatch.setattr(I, "_cache", {})
    return calls, answers


def _icc_manager(db, world) -> Customer:
    return db.query(Customer).filter_by(project_id=world["manager"].id, slug="icc").one()


@pytest.mark.parametrize(
    ("answer", "state", "detail"),
    [
        (_Answer(200, {"status": "ok", "version": "1.6.0"}), "up", ""),
        (_Answer(404, {"detail": "not found"}), "answers", "aplikácia odpovedá, stav nehlási"),
        (_Answer(404, None, "text/plain"), "down", "inštalácia nebeží"),
        (_Answer(502, None, "text/html"), "down", "chyba servera (HTTP 502)"),
        (
            httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate"),
            "down",
            "certifikát nie je platný",
        ),
        (httpx.ReadTimeout("slow"), "down", "neodpovedá včas"),
    ],
)
async def test_the_state_says_what_the_installation_answered(db_session, world, asked, answer, state, detail):
    calls, answers = asked
    answers["next"] = answer

    result = await I.status(db_session, world["rausch"], _icc_manager(db_session, world).id, "uat")

    assert (result.state, result.detail) == (state, detail)
    assert calls == [result_url := calls[0]] and result_url.endswith("/api/v1/health")
    assert result_url.startswith("https://uat-icc-")  # the cockpit's address, never the caller's


async def test_the_answer_is_shared_for_a_while_one_question_per_installation(db_session, world, asked):
    calls, answers = asked
    answers["next"] = _Answer(200, {"status": "ok", "version": "1.6.0"})
    customer = _icc_manager(db_session, world)

    await I.status(db_session, world["rausch"], customer.id, "uat")
    await I.status(db_session, world["admin"], customer.id, "uat")

    assert len(calls) == 1


async def test_a_stranger_may_not_ask_and_an_undeployed_environment_is_not_asked(db_session, world, asked):
    from fastapi import HTTPException

    calls, answers = asked
    answers["next"] = _Answer(200, {"status": "ok"})
    customer = _icc_manager(db_session, world)
    mager = db_session.query(Customer).filter_by(project_id=world["manager"].id, slug="mager").one()

    with pytest.raises(HTTPException) as refused:
        await I.status(db_session, world["zoltan"], customer.id, "uat")
    assert refused.value.status_code == 403
    with pytest.raises(LookupError):
        await I.status(db_session, world["rausch"], mager.id, "prod")
    assert calls == []


@pytest.mark.parametrize(
    ("checks", "state", "detail"),
    [({"database": ("ok", "")}, "up", ""), (None, "down", "zvnútra inštalácie neodpovedá")],
)
async def test_a_private_installation_is_asked_inside_as_the_deploy_asks_it(
    db_session, world, asked, monkeypatch, checks, state, detail
):
    """DEV-378: 14.11.2026 three private UATs (…int.isnex.eu) read „neodpovedá“ on the live page — the private gate
    refused the cockpit's container with 403. Asked inside, as the deploy asks; never at the address."""
    calls, _ = asked
    world["manager"].private_network = True
    db_session.flush()
    seen = []

    async def instance_health(compose, project_slug, deploy_host=None):
        seen.append((str(compose), project_slug, deploy_host))
        return checks

    monkeypatch.setattr(I.orchestrator, "instance_health", instance_health)

    result = await I.status(db_session, world["rausch"], _icc_manager(db_session, world).id, "uat")

    assert (result.state, result.detail) == (state, detail)
    assert calls == []
    slug = world["manager"].slug
    assert seen[0][0].endswith(f"/icc/{slug}/docker-compose.yml") and seen[0][1:] == (slug, None)
