"""DEV-329 — the Verifikácia rehearses the deploy: the cockpit's own installation, built and started, before Hotovo.

09.11.2026 the Director asked why for a year no new project went from the cockpit to its customer without Dedo mending
something. Measured in the cockpit's records: the first UAT deploy failed for 5 of 8 projects, 16 of 83 UAT deploys and
10 of 32 PROD deploys failed, and 74 fixes went into the deploy path since June. The cause: the cockpit verifies the app
one way and deploys it another. The agent, the CI and the Verifikácia run the PROJECT's compose; the deploy rewrites it
into an INSTALLATION's compose by rules of its own (roles of services, names of images, and for an existing installation
parts of the old one) — and nobody ran that result before „Nasadiť“. NEX Websites 1.1.0 passed its Verifikácia the same
day and failed on ICC's UAT (DEV-328). Director to the fix: „Súhlasím“; „Prosím spraviť všetky štyri“.

So the Verifikácia now writes the installation with the deploy's own code (``uat_provisioner.provision_uat``) — for a
new customer and, from a copy, for every existing UAT installation of the project — into a throwaway folder, builds its
images from the verified commit (``verified_build``) and starts them. What makes it a rehearsal is only its identity
(:func:`isolated`): its own compose name, no Traefik route, no published port, no outside network, its own empty volumes
and the synthetic settings of a first install — never a customer's data or ``.env``. Then it is taken down.

Not rehearsed, and said so: moving a customer's real data (the rehearsal database is empty), the machine MAGER (the
build runs on ANDROS) and the pairing with a customer's NEX Manager — the deploy itself checks the door afterwards.
"""

from __future__ import annotations

import asyncio
import copy
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from backend.core.agent_env import project_env
from backend.services import build_provenance, verified_build
from backend.services import uat_provisioner as P

#: The customer a rehearsal installs for — a folder in a throwaway root only, never a real customer.
REHEARSAL_CUSTOMER = "skuska-nasadenia"
#: The compose names of rehearsals — a prefix nothing real carries, so a leftover is recognised as one.
REHEARSAL_NAME = "skuska-nasadenia"
#: The tag of the images a rehearsal builds — never the tag of an installation's image (``v1.1.0``).
REHEARSAL_TAG = "skuska"
#: How much of a failed step's log goes into the finding.
_LOG_TAIL_LINES = 40
#: A rehearsal is opened as a new installation, then as each customer's: the label the finding names.
NEW_INSTALLATION = "nová inštalácia"


@dataclass(frozen=True)
class Rendered:
    """One installation the deploy would write: what it is, and its compose and settings in the throwaway folder."""

    label: str
    compose: Path
    env: Path


def _existing_installations(uat_root: Path, project_slug: str) -> list[Path]:
    """Every UAT installation of the project — its folder ``<uat_root>/<customer>/<project>`` — set aside ones not."""
    found = []
    for compose in sorted(uat_root.glob(f"*/{project_slug}/docker-compose.yml")):
        customer = compose.parent.parent.name
        if customer.startswith((".", "_")) or customer == REHEARSAL_CUSTOMER:
            continue
        found.append(compose)
    return found


def render_targets(
    project_slug: str, version: str, *, projects_root: Path, uat_root: Path, work: Path
) -> list[Rendered]:
    """Write, into ``work``, every installation the deploy of ``version`` would write — with the deploy's own code.

    A new customer's installation first; then, for every existing UAT installation, a first install for that customer
    (its synthetic settings) overwritten with a copy of the installation's own compose, and the deploy into it — the
    version bump that kept the old build of NEX Websites on 09.11.2026. Nothing outside ``work`` is written."""
    app = P.derive_uat_slug(project_slug)

    def provision(root: Path, customer: str) -> P.ProvisionResult:
        return P.provision_uat(
            project_slug,
            f"{customer}-uat",
            version=version,
            projects_root=projects_root,
            uat_root=root,
            prod_root=root / "prod",
            environment="uat",
            customer_slug=customer,
            app=app,
            full_project_slug=project_slug,
        )

    fresh = provision(work / "nova", REHEARSAL_CUSTOMER)
    rendered = [Rendered(NEW_INSTALLATION, fresh.compose_path, fresh.env_path)]
    for existing in _existing_installations(uat_root, project_slug):
        customer = existing.parent.parent.name
        root = work / f"zakaznik-{customer}"
        first = provision(root, customer)
        shutil.copyfile(existing, first.compose_path)  # the installation's compose as it stands — not its .env
        again = provision(root, customer)
        rendered.append(Rendered(f"inštalácia zákazníka {customer}", again.compose_path, again.env_path))
    return rendered


def _isolated_mount(mount: Any) -> Any:
    """A service's mount in a rehearsal: a folder of the machine becomes an empty volume at the same place."""
    if isinstance(mount, dict):
        if mount.get("type") == "bind":
            return {"type": "volume", "target": mount.get("target")}
        return mount
    text = str(mount)
    source, sep, rest = text.partition(":")
    if sep and source.startswith(("/", ".", "~", "$")):
        return rest.split(":", 1)[0]
    return mount


def isolated(compose: dict[str, Any], name: str, env: Path) -> dict[str, Any]:
    """The installation's compose with an identity of its own — everything it builds and runs stays as it is.

    Its own compose name; no ``container_name`` (the installation's containers keep theirs), no published port, no
    Traefik label, no outside network (the default network of its own name instead); its own empty volumes (a
    volume's real ``name`` or ``external`` would reach the customer's data); a folder of the machine becomes an empty
    volume; images it builds are tagged for the rehearsal; the settings it reads are the rehearsal's ``env``."""
    out = copy.deepcopy(compose)
    out["name"] = name
    out.pop("networks", None)
    if isinstance(out.get("volumes"), dict):
        out["volumes"] = {key: {} for key in out["volumes"]}
    for service, svc in (out.get("services") or {}).items():
        if not isinstance(svc, dict):
            continue
        for key in ("container_name", "ports", "networks", "labels"):
            svc.pop(key, None)
        if svc.get("build") is not None:
            svc["image"] = f"{name}-{service}:{REHEARSAL_TAG}"
        if svc.get("volumes"):
            svc["volumes"] = [_isolated_mount(m) for m in svc["volumes"]]
        if svc.get("env_file") is not None:
            svc["env_file"] = [str(env)]
    return out


def _tail(text: str) -> str:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    return "\n".join(lines[-_LOG_TAIL_LINES:])


async def _run_one(target: Rendered, name: str, project_path: Path, version: str) -> tuple[bool, str]:
    """Build and start one rehearsal installation, check its backend answers, take it down. ``(ok, why)``."""
    from backend.services import orchestrator as O  # the smoke's own steps; imported here — it imports this module

    data = yaml.safe_load(target.compose.read_text(encoding="utf-8")) or {}
    compose = isolated(data, name, target.env)
    path = target.compose.parent / "skuska-nasadenia.yml"
    path.write_text(yaml.safe_dump(compose, sort_keys=False), encoding="utf-8")
    prepared = await asyncio.to_thread(verified_build.prepare, path)
    # The images get the version and the commit the deploy gives them (ICCINT-166) — nothing of the cockpit's own env.
    provenance = await asyncio.to_thread(build_provenance.build_env, path)
    env = project_env({"APP_VERSION": version, "VITE_APP_VERSION": version, **provenance})
    files = ["-f", str(path)] + (["-f", str(prepared.build.override)] if prepared.build else [])
    base = [
        "docker", "compose", "-p", name, "--project-directory", str(target.compose.parent),
        "--env-file", str(target.env), *files,
    ]  # fmt: skip
    try:
        rc, out = await O._compose_smoke_step(
            [*base, "up", "-d", "--build", "--wait"], O.ACCEPTANCE_SMOKE_TIMEOUT, env=env
        )
        if rc != 0:
            return False, f"inštalácia sa nepostavila alebo nespustila:\n{_tail(out)}"
        services = compose.get("services") or {}
        backend = P.identify_service_roles(services).get("backend")
        if backend:
            port = P.detect_internal_port(services[backend], 8000)
            path_ = P.resolve_be_health_path(services[backend], project_path)
            ready, last = await O._await_http_ready(base, backend, port, path=path_, env=env)
            if not ready:
                return False, f"backend neodpovedá ({last})"
        return True, ""
    finally:
        await O._compose_smoke_step([*base, *O._SMOKE_DOWN], 120, env=env)
        await O.throw_away_rehearsal_images(f"{name}-*:{REHEARSAL_TAG}", env)
        verified_build.cleanup(prepared.build)


async def rehearse(
    project_slug: str,
    version: str,
    *,
    projects_root: Optional[Path] = None,
    uat_root: Optional[Path] = None,
) -> tuple[bool, str]:
    """Rehearse the deploy of ``version``: ``(ok, sentence)``. Never raises — a failure is the Verifikácia's finding."""
    from backend.services import claude_agent

    projects = projects_root or claude_agent.PROJECTS_ROOT
    work = Path(tempfile.mkdtemp(prefix=f"{project_slug}-{REHEARSAL_NAME}-"))
    try:
        try:
            targets = await asyncio.to_thread(
                render_targets,
                project_slug,
                version,
                projects_root=projects,
                uat_root=uat_root or P.UAT_ROOT,
                work=work,
            )
        except Exception as exc:  # noqa: BLE001 — the deploy would stop here too; say it, never raise
            return False, f"Skúška nasadenia: kokpit nevedel zapísať predpis inštalácie ({exc})."
        done = []
        for number, target in enumerate(targets):
            name = f"{project_slug}-{REHEARSAL_NAME}-{number}"
            ok, why = await _run_one(target, name, projects / project_slug, version)
            if not ok:
                return False, f"Skúška nasadenia ({target.label}) zlyhala — {why}"
            done.append(target.label)
        return True, (
            f"Skúška nasadenia prešla ({', '.join(done)}): postavené a spustené tak, ako ich nasadí kokpit. Neoverené "
            "sú dáta zákazníka, stroj MAGER a prepojenie s NEX Managerom zákazníka."
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
