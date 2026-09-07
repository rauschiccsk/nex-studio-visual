"""Short-lived UAT test launch URL for a token-launch app (v4.0.30 — the 'Spustiť' in the UAT tab).

Lets the Manager launch a deployed token-launch app LOGGED-IN, directly from the UAT tab, without going
through NEX Manager — by minting a §4.4-compliant launch token (HS256; ``iss=nex-manager``,
``aud=<module slug>``, ``purpose=module-launch``, ``exp`` under the app's 60 s cap, single-use ``jti``)
with the app's OWN launch key. The key is read from the app's UAT deploy ``.env`` server-side and used
ONLY to sign — it is never returned to the client and never logged.

UAT-only convenience: PROD launches stay via NEX Manager (real users).

⚠️ **ICCINT-61 — the ticket says it plainly: ``sub`` used to be ``uat-test``, a name no Manager has ever
heard of.** The intent was right (the code said "no impersonation") but the reading was wrong, and the
result was a button that COULD NOT work: the app asks its Manager who ``uat-test`` is, the Manager says it
has no such person, and the session ends before the operator sees the app.

Minting for the operator who CLICKED is not impersonation — it is authentication. Impersonation is minting
for somebody ELSE. The clicker is signed in to NEX Studio, clicks with their own hand in their own session,
and the ticket is issued in their name. So the subject is now their login, and when the paired Manager does
not know that login the caller is told so in plain words instead of being handed a ticket that dies.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import httpx
from jose import jwt  # python-jose — the backend's declared JWT lib (matches auth.py / core.security)

from backend.services import uat_provisioner

#: Token lifetime — under the app's hard ``exp - iat <= 60 s`` cap.
_LAUNCH_TTL_SECONDS = 50
#: How long to wait for the paired Manager to say whether it knows the operator. Short on purpose: this
#: runs while somebody is looking at a button they just pressed.
_IDENTITY_TIMEOUT_SECONDS = 5

logger = logging.getLogger(__name__)


def manager_knows_operator(customer_slug: str, project_slug: str, login: str) -> Optional[bool]:
    """Does the app's paired NEX Manager know this operator? ``None`` = could not find out.

    ICCINT-61: without this the launch fails INSIDE the app, and all the operator sees is the app's own
    "Prihlásenie skončilo" — the message that cost five days of looking in the wrong place. Asking first
    turns that into a sentence naming what is actually wrong.

    ``None`` never blocks (same rule as the CI floor, ICCINT-70): not being able to ask is not evidence
    that the operator is unknown, and a check that stops on ignorance is one people learn to route around.
    Only a Manager that answers "no such person" stops the launch.
    """
    env = uat_provisioner._parse_env_file(_uat_env_path(customer_slug, project_slug))
    base_url = env.get(uat_provisioner.MANAGER_BASE_URL_ENV)
    module_slug = env.get("MANAGER_MODULE_SLUG")
    api_key = env.get(uat_provisioner.MANAGER_API_KEY_ENV)
    if not (base_url and module_slug and api_key):
        return None
    try:
        answer = httpx.get(
            f"{base_url.rstrip('/')}/api/v1/identity/{quote(login, safe='')}",
            # The shape the Manager requires — BOTH headers. One of them alone is what broke
            # nex-productcatalogs for two versions.
            headers={"X-Module-Slug": module_slug, "Authorization": f"Bearer {api_key}"},
            timeout=_IDENTITY_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as unreachable:
        logger.warning("spárovaný NEX Manager sa nedá osloviť (%s)", type(unreachable).__name__)
        return None
    if answer.status_code == httpx.codes.NOT_FOUND:
        return False
    if answer.status_code == httpx.codes.OK:
        return True
    # 401/403 means the app's own module key is wrong — a deploy fault, not a verdict about the operator.
    logger.warning("spárovaný NEX Manager odpovedal na otázku o totožnosti stavom %s", answer.status_code)
    return None


def _uat_env_path(customer_slug: str, project_slug: str) -> Path:
    """The token-launch app's UAT deploy ``.env`` — mirrors uat_provisioner's per-customer path."""
    return uat_provisioner.UAT_ROOT / customer_slug / project_slug / ".env"


def build_uat_launch_url(customer_slug: str, project_slug: str, uat_url: str, *, subject: str) -> Optional[str]:
    """Return ``<uat_url>/api/v1/launch?lt=<token>`` for a token-launch app's UAT deploy, or ``None`` when
    the deploy has no launch key/slugs wired (not token-launch, or no paired NEX Manager). The signing key
    is used ONLY to sign — never returned, never logged."""
    if not uat_url:
        return None
    env = uat_provisioner._parse_env_file(_uat_env_path(customer_slug, project_slug))
    key = env.get("MANAGER_LAUNCH_SIGNING_KEY")
    module_slug = env.get("MANAGER_MODULE_SLUG")
    deploy_slug = env.get("MANAGER_DEPLOY_SLUG")
    if not (key and module_slug and deploy_slug):
        return None
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "iss": "nex-manager",
            "aud": module_slug,
            "sub": subject,
            "deploy": deploy_slug,
            "purpose": "module-launch",
            "jti": str(uuid.uuid4()),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(seconds=_LAUNCH_TTL_SECONDS)).timestamp()),
        },
        key,
        algorithm="HS256",
    )
    return f"{uat_url.rstrip('/')}/api/v1/launch?lt={token}"


#: The app's session endpoint — the same convention the launch path follows. Named separately because it is
#: the step that MATTERS: this week's failure returned a clean 303 from the launch and died right after it.
_SESSION_PATH = "/api/v1/session"
#: The probe runs inside a deploy, so it may not hang on it.
_DOOR_TIMEOUT_SECONDS = 10


def uat_door_opens(customer_slug: str, project_slug: str, uat_url: str, *, subject: str) -> tuple[Optional[bool], str]:
    """Does a real launch ticket actually get somebody INTO the deployed app? ``None`` = could not tell.

    ICCINT-69. NEX Studio already checks that a token app DECLARES its launch settings (the July gate, after
    an app deployed "successfully" and failed hours later at the click). nex-productcatalogs declared
    everything correctly and still could not be opened for five days: it introduced itself to its NEX Manager
    with one header where two are required, and then read a field the Manager does not send. Declaration was
    right, the contract was not.

    So this does not ASK the app whether it thinks the door works — it opens the door. Mint a real ticket,
    present it, and follow through to the session. ⚠️ The follow-through is the whole point: the failure this
    is built from returned a clean ``303`` from the launch and only died on the NEXT request, so a probe that
    stopped at the redirect would have called that broken app healthy.

    ``None`` never warns (the rule the CI floor and the identity pre-check follow): not being able to tell is
    not evidence that the door is shut, and an alarm that cries on ignorance is one people learn to ignore.
    """
    launch_url = build_uat_launch_url(customer_slug, project_slug, uat_url, subject=subject)
    if not launch_url:
        return None, "vstupenka sa nedá vyraziť (appka nemá spárovaný NEX Manager)"
    try:
        with httpx.Client(follow_redirects=False, timeout=_DOOR_TIMEOUT_SECONDS) as client:
            opened = client.get(launch_url)
            if opened.status_code >= 400:
                return False, f"appka vstupenku odmietla (stav {opened.status_code})"
            if not opened.cookies:
                return None, "appka po vstupe nevrátila sedenie, takže sa nedá pokračovať"
            session = client.get(f"{uat_url.rstrip('/')}{_SESSION_PATH}", cookies=opened.cookies)
    except httpx.HTTPError as unreachable:
        return None, f"nasadená appka sa nedá osloviť ({type(unreachable).__name__})"

    if session.status_code == httpx.codes.NOT_FOUND:
        return None, f"appka nemá {_SESSION_PATH}, takže sa vstup nedá dopovedať"
    if session.status_code == httpx.codes.OK:
        return True, "vstup jedným kliknutím overený až po prihlásené sedenie"
    # THE case this exists for: the ticket was accepted and the session died right after — which is what a
    # contract mismatch with the neighbour looks like from outside.
    return False, (
        f"appka vstupenku prijala, ale sedenie hneď nato skončilo (stav {session.status_code}) — "
        "appka sa so svojím NEX Managerom nezhodne na tvare, hoci nastavenia má správne"
    )
