"""DEV-378 — every installation the cockpit deployed, across all projects: the „Inštalácie“ page.

13.11.2026 the Director: „mám už veľa ikonov na spúšťanie rôznych aplikácii … Je to len niekoľko zákazníkov a už
strácam prehľad … Navrhni mi prosím nejaké riešenie čo bude prehľadné aj pri 50+ zákazníkov.“ On the proposal — a page
with every customer and their apps, UAT and PROD side by side, a version, whether it answers and „Otvoriť“: „Súhlasím,
ideme to robiť.“

The list is read from the cockpit's own deploy records with the deploy matrix's own functions (versions, addresses),
so it cannot go stale and needs no register of its own: an installation appears with its first deploy. A person sees
the installations of the projects they may see (the owner theirs, the admin all); archived projects stay out, and an
installation the cockpit never deployed is not known to it. Whether an installation lives is asked one installation at
a time (:func:`status`) — the address is the cockpit's, never the caller's — and shared for a while. A project on the
private network (DEV-42) whose gate refuses the cockpit at the address (403 — the gate on ANDROS lets in the private
network, not the cockpit's container) is asked inside: whether its backend answers on its health path, the deploy's
own rule for where that is (``uat_provisioner.resolve_be_health_path``).
"""

from __future__ import annotations

import asyncio
import time
import unicodedata
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

import httpx
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config.settings import settings
from backend.core import authz
from backend.db.models.customers import Customer
from backend.db.models.foundation import User
from backend.db.models.projects import Project
from backend.services import claude_agent, deploy, orchestrator, uat_provisioner

ENVIRONMENTS = ("uat", "prod")
HEALTH_PATH = "/api/v1/health"

#: What the page says of an installation — the three answers a person can act on.
UP, ANSWERS, DOWN = "up", "answers", "down"
#: The private gate refused the cockpit at the address — not an answer about the installation behind it.
GATE_REFUSED = "súkromná brána odmietla kokpit"
#: Asked inside the backend's container: the HTTP status of its own health path, the last line printed.
_INSIDE_PROBE = (
    "import urllib.request\n"
    "try:\n"
    "    print(urllib.request.urlopen('http://127.0.0.1:{port}{path}', timeout=5).status)\n"
    "except Exception as exc:\n"
    "    print(getattr(exc, 'code', 0))\n"
)


def _sort_key(text: str) -> str:
    """Alphabetical as a person reads it — „MÁGERSTAV“ among the M, not after the Z."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).casefold()


def _visible_projects(db: Session, user: User) -> list[Project]:
    query = select(Project).where(Project.status != "archived")
    if not authz.is_admin(user):
        query = query.where(Project.created_by == user.id)
    return list(db.execute(query).scalars().all())


def _environment(db: Session, customer: Customer, project: Project, environment: str) -> Optional[dict]:
    version = deploy.current_version(db, customer.id, environment)
    if not version:
        return None
    last = deploy.last_successful_deploy(db, customer.id, environment)
    url = deploy._instance_url(customer, environment, project)
    return {
        "version": version,
        "deployed_at": last.created_at if last is not None else None,
        "url": url,
        # DEV-164: a Web project has several frontends — each by its own address; one frontend is the main address.
        "links": deploy._frontend_urls(url, project),
        "last_attempt_failed": deploy.last_deploy_attempt_failed(db, customer.id, environment),
    }


def list_installations(db: Session, user: User) -> list[dict]:
    """Every customer with an installation, alphabetically, each with its apps alphabetically: per app the project and
    its UAT and PROD (``None`` where not deployed). A customer is one across projects — known by the name of its
    installations' folder (``deploy._customer_dir_slug``), the same in every project."""
    groups: dict[str, dict] = {}
    for project in _visible_projects(db, user):
        customers = db.execute(select(Customer).where(Customer.project_id == project.id)).scalars().all()
        for customer in customers:
            environments = {env: _environment(db, customer, project, env) for env in ENVIRONMENTS}
            if not any(environments.values()):
                continue
            key = deploy._customer_dir_slug(customer)
            group = groups.setdefault(key, {"key": key, "name": customer.name, "apps": []})
            group["apps"].append(
                {
                    "customer_id": customer.id,
                    "project_slug": project.slug,
                    "project_name": project.name,
                    **environments,
                }
            )
    for group in groups.values():
        group["apps"].sort(key=lambda app: _sort_key(app["project_name"]))
    return sorted(groups.values(), key=lambda group: _sort_key(group["name"]))


# ── whether an installation lives ──────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Status:
    state: str  # UP | ANSWERS | DOWN
    version: Optional[str]
    detail: str


_cache: dict[str, tuple[float, Status]] = {}


def _ask(url: str) -> Status:
    """One question to the installation's health endpoint — ``up`` when it says ``ok``, ``answers`` when the app
    answers without such an endpoint, ``down`` when nothing of the app answers (a certificate, a timeout, the router's
    own „404 page not found“, a server error)."""
    try:
        answer = httpx.get(url + HEALTH_PATH, timeout=settings.installation_status_timeout_seconds)
    except httpx.ConnectError as exc:
        reason = "certifikát nie je platný" if "CERTIFICATE_VERIFY_FAILED" in str(exc) else "nedá sa pripojiť"
        return Status(DOWN, None, reason)
    except httpx.TimeoutException:
        return Status(DOWN, None, "neodpovedá včas")
    except httpx.HTTPError:
        return Status(DOWN, None, "nedá sa pripojiť")
    json_answer = "application/json" in answer.headers.get("content-type", "")
    body = answer.json() if json_answer else None
    if answer.status_code == 200 and isinstance(body, dict) and body.get("status") == "ok":
        return Status(UP, str(body.get("version") or "") or None, "")
    if json_answer and answer.status_code < 500:
        return Status(ANSWERS, None, "aplikácia odpovedá, stav nehlási")
    if answer.status_code >= 500:
        return Status(DOWN, None, f"chyba servera (HTTP {answer.status_code})")
    if answer.status_code == 403:
        return Status(DOWN, None, GATE_REFUSED)
    return Status(DOWN, None, "inštalácia nebeží")


async def _ask_inside(customer: Customer, project: Project, environment: str) -> Status:
    """A private installation, asked inside its backend's container on the machine it runs on: does the backend answer
    on its own health path (the deploy's rule for where that is) with 200."""
    compose = orchestrator._uat_compose_path(
        deploy._instance_slug(customer, environment),
        environment=environment,
        customer_slug=deploy._customer_dir_slug(customer),
        full_project_slug=project.slug,
    )
    try:
        services = (yaml.safe_load(compose.read_text(encoding="utf-8")) or {}).get("services") or {}
    except (OSError, yaml.YAMLError):
        return Status(DOWN, None, "predpis inštalácie sa nedá prečítať")
    backend = uat_provisioner.identify_service_roles(services).get("backend")
    if backend is None:
        return Status(DOWN, None, "inštalácia nemá backend")
    port = uat_provisioner.detect_internal_port(services[backend], 8000)
    path = uat_provisioner.resolve_be_health_path(services[backend], claude_agent.PROJECTS_ROOT / project.slug)
    deploy_host = (customer.prod_host or None) if environment == "prod" else None
    command = ["docker", "compose", "-f", str(compose), "exec", "-T", backend, "python", "-c"]
    rc, out = await orchestrator._compose_smoke_step(
        [*command, _INSIDE_PROBE.format(port=port, path=path)],
        int(settings.installation_status_timeout_seconds) * 2,
        env=orchestrator._verify_env_for_target(deploy_host),
    )
    code = (out or "").strip().splitlines()[-1] if rc == 0 and (out or "").strip() else ""
    if code == "200":
        return Status(UP, None, "")
    if code.isdigit() and code != "0":
        return Status(DOWN, None, f"zvnútra odpovedá chybou (HTTP {code})")
    return Status(DOWN, None, "zvnútra inštalácie neodpovedá")


async def status(db: Session, user: User, customer_id: UUID, environment: str) -> Status:
    """Whether the ``environment`` installation of the customer row lives — the address is worked out here, never taken
    from the caller; the answer is shared for ``installation_status_cache_seconds``."""
    if environment not in ENVIRONMENTS:
        raise ValueError(f"Prostredie „{environment}“ neexistuje.")
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise LookupError("Zákazník neexistuje.")
    project = db.get(Project, customer.project_id)
    authz.authorize_project(user, project)
    if not deploy.current_version(db, customer.id, environment):
        raise LookupError("Táto inštalácia ešte nie je nasadená.")
    url = deploy._instance_url(customer, environment, project)
    now = time.monotonic()
    cached = _cache.get(url)
    if cached is not None and now - cached[0] < settings.installation_status_cache_seconds:
        return cached[1]
    answer = await asyncio.to_thread(_ask, url)
    if project.private_network and answer.detail == GATE_REFUSED:
        answer = await _ask_inside(customer, project, environment)
    _cache[url] = (now, answer)
    return answer
