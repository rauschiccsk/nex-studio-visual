"""Short-lived UAT test launch URL for a token-launch app (v4.0.30 — the 'Spustiť' in the UAT tab).

Lets the Manager launch a deployed token-launch app LOGGED-IN, directly from the UAT tab, without going
through NEX Manager — by minting a §4.4-compliant launch token (HS256; ``iss=nex-manager``,
``aud=<module slug>``, ``purpose=module-launch``, ``exp`` under the app's 60 s cap, single-use ``jti``)
with the app's OWN launch key. The key is read from the app's UAT deploy ``.env`` server-side and used
ONLY to sign — it is never returned to the client and never logged.

UAT-only convenience: PROD launches stay via NEX Manager (real users). ``sub`` is a clearly-labelled
TEST identity, never a real user — no impersonation.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from jose import jwt  # python-jose — the backend's declared JWT lib (matches auth.py / core.security)

from backend.services import uat_provisioner

#: The launch token's subject — a clearly-labelled UAT test identity (NOT a real user / no impersonation).
UAT_TEST_SUBJECT = "uat-test"
#: Token lifetime — under the app's hard ``exp - iat <= 60 s`` cap.
_LAUNCH_TTL_SECONDS = 50


def _uat_env_path(customer_slug: str, project_slug: str) -> Path:
    """The token-launch app's UAT deploy ``.env`` — mirrors uat_provisioner's per-customer path."""
    return uat_provisioner.UAT_ROOT / customer_slug / project_slug / ".env"


def build_uat_launch_url(customer_slug: str, project_slug: str, uat_url: str) -> Optional[str]:
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
            "sub": UAT_TEST_SUBJECT,
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
