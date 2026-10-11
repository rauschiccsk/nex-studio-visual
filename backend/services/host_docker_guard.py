"""What an agent turn that sees this server's Docker may not do to it, and what the cockpit prepares instead (DEV-70).

11.10.2026 the Auditor of career-asistent (verification — the one phase deliberately left outside the build sandbox,
so that it can boot the app it judges; ICCINT-20) pulled ``mcr.microsoft.com/playwright:v1.50.1-noble`` (2.56 GB)
five times and removed it at the end of every round. Each pull filled ANDROS's internet line (~10-12 Mbit/s, ping
1.1 s) for about twenty minutes and every customer site answered in 4-5 s.

The phase keeps its Docker. Two things change:

* **The browser image is prepared by the cockpit** and kept under the cockpit's own tag
  (:data:`settings.audit_browser_pin`). Removing the public name then only untags it — the layers stay — and the
  cockpit puts the public name back before the next round (:func:`ensure_audit_browser`). Nothing is downloaded
  again.
* **A turn outside the sandbox may not pull and may not prune** (:data:`HOST_DOCKER_DENY`). The rules travel in the
  role profile the CLI is started with (``--settings``) — a ``deny`` there holds even under ``bypassPermissions``,
  where ``--disallowedTools`` does not (verified in :func:`claude_agent.build_claude_argv`). The cockpit writes that
  profile itself, outside the project, so the rule holds for every project and the turn cannot edit it.

What this does NOT cover, said out loud: a deny list cannot name every way to reach the network. ``docker run`` of an
image the server does not have pulls it implicitly; a ``docker build`` pulls its base images. What it stops is the
pattern that happened — fetching and throwing away the same large image round after round.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Optional

from backend.config.settings import settings

logger = logging.getLogger(__name__)

#: Docker commands an agent turn that sees this server's Docker may not run. Pulling goes over the server's internet
#: line, which the customer sites share; pruning would also remove the cockpit's prepared images. Removing an image by
#: name (``docker rmi``) stays allowed — the Auditor cleans up what it built — except by force, which deletes an image
#: under every name it has, the cockpit's included.
HOST_DOCKER_DENY: tuple[str, ...] = (
    "Bash(docker pull:*)",
    "Bash(docker image pull:*)",
    "Bash(docker system prune:*)",
    "Bash(docker image prune:*)",
    "Bash(docker builder prune:*)",
    "Bash(docker buildx prune:*)",
    "Bash(docker rmi -f:*)",
    "Bash(docker rmi --force:*)",
    "Bash(docker image rm -f:*)",
    "Bash(docker image rm --force:*)",
)


def turn_profile(settings_path: Optional[Path], project_slug: str) -> Path:
    """The role profile a turn outside the sandbox is started with: the project's own (when there is one) plus
    :data:`HOST_DOCKER_DENY`, written to the cockpit's own folder — never into the project, where the turn could edit
    it. One file per project and role, rewritten on every turn, so a change of the project's profile is picked up."""
    profile: dict = {}
    if settings_path is not None:
        profile = json.loads(Path(settings_path).read_text(encoding="utf-8"))
    permissions = profile.setdefault("permissions", {})
    deny = list(permissions.get("deny") or [])
    deny += [rule for rule in HOST_DOCKER_DENY if rule not in deny]
    permissions["deny"] = deny
    role = Path(settings_path).parent.name if settings_path is not None else "none"
    target = Path(settings.agent_profile_dir) / f"{project_slug}-{role}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".json.partial")
    partial.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    partial.replace(target)
    return target


def _docker(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, check=False)


def _image_id(reference: str) -> Optional[str]:
    done = _docker("image", "inspect", "--format", "{{.Id}}", reference)
    if done.returncode != 0:
        return None
    return done.stdout.strip() or None


#: What :func:`ensure_audit_browser` found and did — said in the log, and the reason the brief may name the image.
READY, RESTORED, PINNED, PULLED, UNAVAILABLE = "ready", "restored", "pinned", "pulled", "unavailable"


def ensure_audit_browser() -> str:
    """Make the Auditor's browser image present under its public name before a verification turn (DEV-70).

    * the cockpit's tag and the public name point at the same image → ``ready``, nothing happens;
    * only the cockpit's tag is left (the Auditor removed the public name) → the name is put back → ``restored``;
    * only the public name exists (the image was pulled before this check existed) → the cockpit's tag is added →
      ``pinned``;
    * neither → the one pull this server ever needs, then the tag → ``pulled``;
    * a step fails → ``unavailable``; never raises — verification goes on without the prepared image.
    """
    public, pin = settings.audit_browser_image, settings.audit_browser_pin
    try:
        pin_id, public_id = _image_id(pin), _image_id(public)
        if pin_id and pin_id == public_id:
            return READY
        if pin_id:
            return RESTORED if _docker("tag", pin, public).returncode == 0 else UNAVAILABLE
        if not public_id:
            logger.warning("audit browser: %s is not on this server — pulling it once", public)
            if _docker("pull", public, timeout=settings.audit_browser_pull_timeout).returncode != 0:
                return UNAVAILABLE
            if _docker("tag", public, pin).returncode != 0:
                return UNAVAILABLE
            return PULLED
        return PINNED if _docker("tag", public, pin).returncode == 0 else UNAVAILABLE
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("audit browser: %s — verification goes on without the prepared image", exc)
        return UNAVAILABLE
