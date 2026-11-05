"""DEV-306 — after a deploy, the names of an installation's services lead to the services of ITS installation only.

04.11.2026 on ANDROS the name „backend“, asked from the frontend of the UAT of ICC's NEX Manager, answered with the
backends of ten installations in the shared Traefik network, and of ten requests through that frontend's nginx its
own backend answered one; 05.11.2026 the backends on MAGER saw five „frontend“s (MSG-227).
:func:`uat_provisioner.links_within_installation` writes the links that prevent it; this looks at the running machine,
the way DEV-42 looks at private access, and says what still leads elsewhere — every name of
:func:`uat_provisioner.installation_links`, asked in the container that calls it.

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
    """Every address *name* resolves to inside *container* — ``ahosts``, as nginx takes them all, not ``hosts`` with
    the first only; ``None`` when it could not be asked."""
    try:
        out = _docker(["exec", container, "getent", "ahosts", name], deploy_host)
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
    """The sentences for the deploy screen: a service whose name leads beyond its installation's service, or that could
    not be asked. Nothing when every one reaches its own."""
    try:
        compose = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    if not isinstance(compose, dict):
        return []
    backend = uat_provisioner._find_backend(compose.get("services") or {})
    own: dict[str, Optional[set[str]]] = {}
    said = []
    for link in uat_provisioner.installation_links(compose):
        caller = uat_provisioner.container_of(compose, link.caller)
        target = uat_provisioner.container_of(compose, link.target)
        if not caller or not target:
            continue
        if target not in own:
            own[target] = _addresses(target, deploy_host)
        for name in link.names:
            reached = _resolved(caller, name, deploy_host)
            if own[target] is None or reached is None:
                said.append(
                    f"Kokpit nevedel overiť, kam z {link.caller} vedie meno „{name}“ — skontroluj to pred akceptáciou."
                )
            elif not reached <= own[target]:
                elsewhere = ", ".join(sorted(reached - own[target]))
                if link.target == backend:
                    beyond, harm = "backendu", "požiadavky cez jej nginx môžu dostať iné aplikácie"
                else:
                    beyond, harm = f"služby {link.target}", "volanie tým menom môže dostať iná aplikácia"
                said.append(
                    f"⚠️ Z {link.caller} vedie meno „{name}“ aj mimo {beyond} tejto inštalácie ({elsewhere}) — {harm}. "
                    "Skontroluj to skôr, než verziu akceptuješ alebo nasadíš ďalej."
                )
    return said
