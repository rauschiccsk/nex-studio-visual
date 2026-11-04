"""DEV-306 — after a deploy, the name a service calls its backend by leads to the backend of ITS installation only.

04.11.2026 on ANDROS the name „backend“, asked from the frontend of the UAT of ICC's NEX Manager, answered with the
backends of ten installations in the shared Traefik network, and of ten requests through that frontend's nginx its
own backend answered one. :func:`uat_provisioner.links_to_own_backend` writes the link that prevents it; this looks at
the running machine, the way DEV-42 looks at private access, and says what still leads elsewhere.

Docker is reached as the deploy reaches it (:func:`remote_instance.docker_env`). The two ``_…`` primitives are the
only places that run Docker; the suite stubs them (``tests/conftest.py``).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

import yaml

from backend.services import remote_instance, uat_provisioner


def _docker(args: list[str], deploy_host: Optional[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=remote_instance.docker_env(deploy_host),
    )


def _resolved(container: str, name: str, deploy_host: Optional[str]) -> Optional[set[str]]:
    """The addresses *name* resolves to inside *container*; ``None`` when it could not be asked."""
    try:
        out = _docker(["exec", container, "getent", "hosts", name], deploy_host)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return {line.split()[0] for line in out.stdout.splitlines() if line.split()}


def _addresses(container: str, deploy_host: Optional[str]) -> Optional[set[str]]:
    """The addresses *container* has on its networks; ``None`` when they could not be read."""
    try:
        out = _docker(
            ["inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}", container], deploy_host
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return set(out.stdout.split()) if out.returncode == 0 else None


def check(compose_path: Path, deploy_host: Optional[str]) -> list[str]:
    """The sentences for the deploy screen: a service the cockpit linked to its backend that reaches another one, or
    that could not be asked. Nothing when every one reaches its own."""
    try:
        compose = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    services = compose.get("services") or {} if isinstance(compose, dict) else {}
    backend = uat_provisioner._find_backend(services)
    backend_container = (services.get(backend) or {}).get("container_name") if backend else None
    if not backend or not isinstance(backend_container, str):
        return []
    callers = [
        (name, svc["container_name"])
        for name, svc in services.items()
        if isinstance(svc, dict) and backend in (svc.get("links") or []) and isinstance(svc.get("container_name"), str)
    ]
    if not callers:
        return []
    own = _addresses(backend_container, deploy_host)
    said = []
    for name, container in callers:
        reached = _resolved(container, backend, deploy_host)
        if own is None or reached is None:
            said.append(f"Kokpit nevedel overiť, kam z {name} vedie meno „{backend}“ — skontroluj to pred akceptáciou.")
        elif not reached <= own:
            elsewhere = ", ".join(sorted(reached - own))
            said.append(
                f"⚠️ Z {name} vedie meno „{backend}“ aj mimo backendu tejto inštalácie ({elsewhere}) — "
                "požiadavky cez jej nginx môžu dostať iné aplikácie. Skontroluj to skôr, než verziu akceptuješ "
                "alebo nasadíš ďalej."
            )
    return said
