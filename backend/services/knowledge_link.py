"""DEV-410 — after a deploy, an app that reads its customer's NEX Knowledge reads it: one read, from the app's own
backend, at the address the cockpit wrote (:func:`uat_provisioner.knowledge_address`).

**Why.** NEX Knowledge 1.1.0 lends its knowledge to the customer's other apps (``/api/v1/app-read/…``, an ``nkapp_``
key) and answers only a call that came straight, never through a proxy or Cloudflare. NEX Legal Watch and NEX
HelpDesk read it; the cockpit knew nothing of the link (16.11.2026). It now writes the address and says, after the
deploy, whether the read works — and if not, why, in words the Manažér can act on.

**How.** Docker is reached as the deploy reaches it (:func:`remote_instance.docker_env`); :func:`_read` is the one
place that runs it (the suite stubs it, ``tests/conftest.py``). The app's key never leaves the container: the read
runs there and says only its outcome.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

import yaml

from backend.services import remote_instance, uat_provisioner

#: The read, run in the app's backend: the address the cockpit wrote, the app's own ``nkapp_`` key (whatever the app
#: calls the setting — the key's form names it), one GET of the areas lent to the app. Prints the outcome only.
_READ = """
import json, os, urllib.error, urllib.request
url = os.environ.get("{url}")
if url is None:
    print("NOLINK"); raise SystemExit
if not url:
    print("NOURL"); raise SystemExit
key = next((v for v in os.environ.values() if v.startswith("nkapp_")), "")
if not key:
    print("NOKEY"); raise SystemExit
headers = {{"Authorization": "Bearer " + key}}
request = urllib.request.Request(url.rstrip("/") + "/api/v1/app-read/areas", headers=headers)
try:
    with urllib.request.urlopen(request, timeout=10) as answer:
        print("OK", len(json.load(answer).get("areas", [])))
except urllib.error.HTTPError as error:
    print("HTTP", error.code)
except Exception as error:
    print("DOWN", type(error).__name__)
""".format(url=uat_provisioner.KNOWLEDGE_URL_ENV)

#: What the deploy says of the read — never the key, never its value.
READS = "Prepojenie s NEX Knowledge: aplikácia ju číta priamo (sprístupnené oblasti: {areas})."
NO_KEY = (
    "Prepojenie s NEX Knowledge: aplikácia nemá kľúč aplikácie (nkapp_…) — vydá ho správca NEX Knowledge a zadá sa "
    "pri zákazníkovi v kokpite; dovtedy NEX Knowledge nečíta."
)
KEY_REFUSED = (
    "Prepojenie s NEX Knowledge: kľúč aplikácie NEX Knowledge odmietla (401) — správca NEX Knowledge ho vymení a nový "
    "sa zadá pri zákazníkovi v kokpite."
)
NOT_STRAIGHT = (
    "Prepojenie s NEX Knowledge: odpoveď 404 — NEX Knowledge tak odpovedá na volanie cez proxy, alebo aplikácii nie "
    "je sprístupnené nič; spojenie musí ísť priamo."
)
ANSWERED = "Prepojenie s NEX Knowledge: NEX Knowledge odpovedala {code} — aplikácia ju nečíta."
UNREACHABLE = (
    "Prepojenie s NEX Knowledge: zo služby {service} sa na NEX Knowledge nedá dostať ({why}) — obe inštalácie musia "
    "bežať na tom istom stroji a služba v sieti nex-proxy-net."
)
NOT_CHECKED = "Prepojenie s NEX Knowledge sa po nasadení nedalo overiť ({why})."


def _read(container: str, deploy_host: Optional[str]) -> Optional[str]:
    """The outcome the read printed in ``container``, or ``None`` when Docker did not run it."""
    try:
        done = subprocess.run(
            ["docker", "exec", container, "python3", "-c", _READ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=remote_instance.docker_env(deploy_host),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def _reading_containers(compose_path: Path, deploy_host: Optional[str]) -> list[str]:
    """DEV-506 — the containers the read is tried from: every running service that is given the address of NEX
    Knowledge (the HelpDesk reads it in its worker), else the backend (an app that gets it another way)."""
    if deploy_host:
        text, _error = remote_instance.read_text(compose_path.parent, compose_path.name, deploy_host=deploy_host)
    else:
        text = compose_path.read_text(encoding="utf-8") if compose_path.is_file() else None
    try:
        data = yaml.safe_load(text or "") or {}
    except yaml.YAMLError:
        return []
    services = [svc for svc in ((data.get("services") or {}).values() if isinstance(data, dict) else ())]
    services = [
        svc
        for svc in services
        if isinstance(svc, dict) and svc.get("container_name") and not uat_provisioner.ends_after_its_run(svc)
    ]
    readers = [
        str(svc["container_name"])
        for svc in services
        if uat_provisioner.KNOWLEDGE_URL_ENV in uat_provisioner.declared_env_keys(svc)
    ]
    if readers:
        return readers
    return [str(svc["container_name"]) for svc in services if str(svc["container_name"]).endswith("-backend")][:1]


def _said(outcome: Optional[str], container: str) -> tuple[Optional[str], list[str]]:
    """What one read in ``container`` means: ``(the success sentence, [])`` or ``(None, [why not])``."""
    if outcome is None:
        return None, [NOT_CHECKED.format(why=f"Docker v kontajneri {container} neodpovedal")]
    word, _, rest = outcome.partition(" ")
    if word == "NOLINK":
        return None, []
    if word == "OK":
        return READS.format(areas=rest or "0"), []
    if word == "NOURL":
        return None, [uat_provisioner.KNOWLEDGE_MISSING]
    if word == "NOKEY":
        return None, [NO_KEY]
    if word == "HTTP":
        return None, [{"401": KEY_REFUSED, "404": NOT_STRAIGHT}.get(rest, ANSWERED.format(code=rest))]
    if word == "DOWN":
        return None, [UNREACHABLE.format(why=rest, service=container)]
    return None, [NOT_CHECKED.format(why=outcome[:80])]


def check(compose_path: Path, deploy_host: Optional[str]) -> tuple[Optional[str], list[str]]:
    """``(what the deploy says on success, warnings)`` of a read of NEX Knowledge from every service of the installation
    that reads it (DEV-506; the backend for an app that gets the address another way). The first one that cannot read
    it is said, named. ``(None, [])`` for an app that does not read NEX Knowledge."""
    success: Optional[str] = None
    for container in _reading_containers(compose_path, deploy_host):
        said, warnings = _said(_read(container, deploy_host), container)
        if warnings:
            return None, warnings
        success = success or said
    return success, []
