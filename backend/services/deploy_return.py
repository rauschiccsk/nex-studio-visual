"""The return to the previous version after a PROD deploy failed once its containers were replaced (DEV-291).

**Why.** A PROD deploy builds the images while the running version serves, backs the database up, replaces the
containers and only then migrates and checks that the application answers. When one of the last steps fails, the old
containers are gone and the new ones do not serve: the application stood still until somebody deployed the previous
version by hand — although its images were still on the machine (``_uprac_stare_verzie`` runs only after a success).
For NEX Manager 1.5.0, which moves its people into the template's tables, that would have stopped opening every other
application of the customer too. Director, 02.11.2026: „Súhlasím“.

**When it may go back.** Only when the database kept the migration revision it had before the containers were
replaced: the previous code then meets the schema it was written for. A changed revision — or one that cannot be read —
means the previous version would run over a schema it does not know, so nothing is started and the report says that the
way back is the backup. An installation without a database has nothing a newer version could have changed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import yaml

from backend.config.settings import settings
from backend.core.offload import BlockingWorkTimedOut, run_blocking
from backend.services import deploy_progress, predeploy_backup, remote_instance

#: The installation has no database — nothing a newer version could have changed.
NO_DATABASE = "bez databázy"
#: Every revision alembic has written (one per branch head), in one line.
REVISION_SH = (
    f"exec psql {predeploy_backup._DB} -Atc \"SELECT string_agg(version_num, ',' ORDER BY version_num) "
    'FROM alembic_version"'
)
#: A database container that is being recreated cannot be asked yet — so many tries, this far apart.
REVISION_ATTEMPTS = 6
REVISION_RETRY_DELAY = 5


async def database_revision(compose: Path, *, deploy_host: Optional[str]) -> Optional[str]:
    """The migration revision of the installation's database → :data:`NO_DATABASE` when it has none, ``None`` when it
    cannot be read (no ``alembic_version``, the database does not answer). Never raises."""
    from backend.services import orchestrator

    try:
        service = predeploy_backup._db_service(compose)
    except (OSError, yaml.YAMLError):
        return None
    if not service:
        return NO_DATABASE
    env = remote_instance.docker_env(deploy_host)
    cmd = predeploy_backup._compose(compose, "exec", "-T", service, "sh", "-c", REVISION_SH)
    for attempt in range(REVISION_ATTEMPTS):
        code, out = await predeploy_backup._run(cmd, env, settings.database_revision_timeout)
        lines = out.splitlines()
        if code == 0:
            revision = lines[-1].strip() if lines else ""
            return revision or None  # an empty table says nothing either
        # Only a container that is not up yet is asked again; a database without alembic_version fails at once.
        if attempt == REVISION_ATTEMPTS - 1 or not orchestrator._exec_not_ready(out):
            return None
        await asyncio.sleep(REVISION_RETRY_DELAY)
    return None


def previous_version(compose_text: str) -> Optional[str]:
    """The version the previous compose ran — from the tags of its images (``icc-manager-backend:v1.4.2``)."""
    from backend.services import orchestrator

    try:
        services = (yaml.safe_load(compose_text) or {}).get("services") or {}
    except yaml.YAMLError:
        return None
    found = set()
    for svc in services.values():
        _repo, _, tag = str((svc or {}).get("image") or "").rpartition(":")
        parsed = orchestrator._image_version(tag)
        if parsed:
            found.add(parsed)
    return ".".join(str(part) for part in max(found)) if found else None


async def return_to_previous(
    result,
    revision_before: Optional[str],
    *,
    project_slug: str,
    customer_slug: str,
    app: str,
    deploy_host: Optional[str],
    private: bool,
) -> tuple[bool, str]:
    """After a PROD deploy failed with its new version started: the previous version again, when the database allows.

    ``result`` is the provisioning of this deploy (its ``previous_files``, ``compose_path``, ``env_path``, ``uat_dir``).
    Returns ``(returned, sentence)``: ``returned`` is True when the previous files were put back and the previous
    version was started — the sentence says whether it answers. False means nothing was touched, and the sentence says
    why. Never raises; the sentence never carries a secret.
    """
    from backend.services import orchestrator

    compose_path: Path = result.compose_path
    previous = dict(getattr(result, "previous_files", None) or {})
    if "docker-compose.yml" not in previous:
        return False, "Predošlá verzia na cieli nie je (prvé nasadenie), kokpit sa nemá kam vrátiť."
    revision_after = None if revision_before is None else await database_revision(compose_path, deploy_host=deploy_host)
    if revision_before is None or revision_after is None:
        return False, (
            "Stav databázy pred nasadením alebo po ňom sa nedal zmerať, preto kokpit predošlú verziu nespustil — "
            "mohla by bežať nad schémou, ktorú nepozná. Návrat znamená obnovu zálohy."
        )
    if revision_after != revision_before:
        return False, (
            f"Databáza sa pri nasadení zmenila (revízia migrácií {revision_before} → {revision_after}), preto kokpit "
            "predošlú verziu nespustil — bežala by nad schémou, ktorú nepozná. Návrat znamená obnovu zálohy."
        )

    deploy_progress.krok(compose_path.parent, deploy_progress.KROK_NAVRAT)
    version = previous_version(previous["docker-compose.yml"][0])
    named = f" {version}" if version else ""
    # The files go back where the installation runs and into this machine's copy, which ``docker compose`` reads here
    # (ICCINT-151) — the two must not part.
    try:
        compose_path.write_text(previous["docker-compose.yml"][0], encoding="utf-8")
        if ".env" in previous:
            result.env_path.write_text(previous[".env"][0], encoding="utf-8")
            result.env_path.chmod(0o600)
    except OSError as exc:
        return True, f"Návrat na predošlú verziu{named} zlyhal pri zápise predpisu ({exc}) — inštalácia nebeží."
    if (deploy_host or "").strip():
        try:
            failure = await run_blocking(
                remote_instance.write_files,
                result.uat_dir,
                previous,
                deploy_host=deploy_host,
                cap=settings.deploy_return_timeout,
            )
        except BlockingWorkTimedOut as exc:
            failure = str(exc)
        if failure:
            return True, f"Návrat na predošlú verziu{named} zlyhal pri zápise na cieľ ({failure}) — inštalácia nebeží."

    stamp = {"APP_VERSION": version, "VITE_APP_VERSION": version} if version else {}
    env = orchestrator._docker_env_for_target(orchestrator.project_env(stamp), deploy_host)
    limit = settings.deploy_return_timeout
    deadline = asyncio.get_running_loop().time() + limit
    cmd = ["docker", "compose", "-f", str(compose_path), "up", "-d", "--force-recreate", "--no-build"]
    started, failure = await orchestrator._deploy_compose_step(cmd, env, deadline, limit)
    if not started:
        return True, f"Predpis predošlej verzie{named} je späť, ale nespustila sa: {failure} — inštalácia nebeží."
    serves, why = await orchestrator._verify_uat_serves(
        project_slug,
        f"{customer_slug}-{app}",
        environment="prod",
        customer_slug=customer_slug,
        app=app,
        full_project_slug=project_slug,
        deploy_host=deploy_host,
        private=private,
    )
    if not serves:
        return True, f"Predošlá verzia{named} sa znova spustila, ale neodpovedá: {why}"
    return True, (
        f"Kokpit vrátil inštaláciu na predošlú verziu{named}: databáza ostala v pôvodnom stave (revízia migrácií "
        f"{revision_before}), predošlá verzia znova beží a odpovedá. Dáta sú nedotknuté."
    )
