"""Dedo's machine identity (ICCINT-14) — the door that is NOT the users' door.

Charter §4.5 forbade Dedo the API outright; the Director narrowed it on 2026-08-22 to what it was always
aiming at — IMPERSONATION, not access. Dedo may reach NEX Studio Visual over the network, but only as
HIMSELF: his own credential, his own name in the thread, never a user account. The charter also says how
the boundary must hold: *"Hranica má byť vynútená TOKENOM, nie disciplínou volajúceho."*

That sentence is the whole design of this module and of :mod:`backend.api.routes.dedo`:

* **A SEPARATE DOOR, not a wider one.** :func:`require_dedo_identity` gates ONE router. It does not extend
  :func:`backend.core.security.get_current_user`, and Dedo is not a row in ``users`` — either would have
  made him a user with a role, i.e. exactly the impersonation the charter refuses, and would have handed
  him every permission that role carries. What Dedo can do is therefore not a list of checks somebody has
  to remember: it is the set of endpoints mounted behind this dependency. He cannot approve a gate because
  no such endpoint exists on his door — not because something inspects the request and says no.

* **A SEPARATE CREDENTIAL SPACE, so the two doors cannot be crossed.** Dedo's secret travels in its own
  header (:data:`DEDO_TOKEN_HEADER`), not in ``Authorization``. The two directions are then closed by
  construction rather than by comparison:

  - a user's JWT presented to Dedo's door carries no ``X-Dedo-Token`` → 401, whatever the JWT says;
  - Dedo's token presented to a user door is not in ``Authorization`` at all → 401, and even if it were,
    it is not a signed JWT and ``get_current_user`` refuses it.

  This is why the header is dedicated instead of reusing ``Bearer``: with one shared header the crossing
  would be prevented only by the *content* of the credential (a JWT does not equal the token, the token
  does not decode as a JWT), which stops holding the moment someone configures a JWT-shaped token. The
  guard below refuses such a value outright, but the header split is what makes the refusal a safety net
  instead of the only line.

* **NO TOKEN → NO DOOR.** An unconfigured instance answers 503 to every request on Dedo's router. The
  trap this avoids is real and one line wide: ``secrets.compare_digest("", "")`` is *True*, so a naive
  compare would turn "nobody set a token" into "everybody is Dedo". The configuration check therefore runs
  FIRST and returns before any comparison can happen. 503 rather than 401 is deliberate: the request is
  not the problem, the instance is — there is no credential that would work here, and telling the operator
  "this deployment has no Dedo identity" is the difference between a five-minute fix and an afternoon of
  re-issuing tokens. It discloses nothing usable: an instance with no token can do nothing for anyone.

* **THE DOOR VERIFIES; IT DOES NOT POSSESS.** What this instance is configured with is a SHA-256 DIGEST of
  Dedo's token, not the token (:data:`backend.config.settings.Settings.dedo_api_token_sha256`). The reason
  is that this process also runs the AI Agent, with an unrestricted Bash tool, as root, in this container:
  whatever the container holds, the agent can read — out of its own environment, out of ``/proc/1/environ``
  (which keeps what the process started with even after ``os.environ.pop``), out of any mounted secret
  file. And the agent is precisely the party the boundary binds: it RAISES the ``framework_issue`` blocks
  this token clears, so a token it can read is a token it can use to close its own escalation and write in
  the thread as ``dedo``. A digest cannot be replayed: reading everything this container has yields nothing
  that opens the door. That is what makes the boundary the TOKEN's rather than the caller's discipline.
  The plaintext :data:`~backend.config.settings.Settings.dedo_api_token` is still honoured for local runs —
  taken out of ``os.environ`` at import and withheld from every agent spawn (:mod:`backend.core.agent_env`)
  — but it is the weaker configuration, and this module says so where the operator will read it.

The presented secret is compared in constant time, is never logged, never echoed into an error body, and
never appears in this repository — Dedo generates it after deploy and configures its digest here.

NO SHARED EXCEPTION INSTANCES. Every refusal below builds a NEW :class:`HTTPException`. Re-raising one
module-level instance would be the ordinary-looking bug that costs the most here: Python APPENDS a frame to
an exception's ``__traceback__`` on every raise, so a module global would accumulate the frames of every
refusal it ever made, forever — an unauthenticated caller could grow the process's memory without bound by
sending wrong tokens, and each retained frame keeps this function's locals alive, i.e. the configured
credential and the caller's input, reachable from a module global and printable by any renderer that shows
locals (Sentry, ``pytest --showlocals``, ``cgitb``). Fresh instances make both impossible; nothing else has
to remember anything. (:mod:`backend.core.security` builds its 401 per request for the same reason.)
"""

from __future__ import annotations

import hashlib
import logging
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

from backend.config.settings import settings

logger = logging.getLogger(__name__)

#: Header carrying Dedo's machine token. Deliberately NOT ``Authorization`` — see the module docstring.
DEDO_TOKEN_HEADER = "X-Dedo-Token"

#: Who the dependency resolves to. The same participant value the thread is written with
#: (``backend.services.dedo_message.DEDO_PARTICIPANT``) — one name for one identity.
DEDO_IDENTITY = "dedo"

#: Shortest token this door will accept as "configured". A three-character secret is not meaningfully
#: different from an empty one, and the empty case is the one failure mode that must never read as "open";
#: refusing a stub value keeps that guarantee from depending on how carefully the operator filled the env.
#: ``openssl rand -hex 32`` (64 chars) is the intended shape.
MIN_TOKEN_LENGTH = 32

#: Length of a SHA-256 hex digest — the shape :data:`~backend.config.settings.Settings.dedo_api_token_sha256`
#: must have to count as configured. A malformed digest matches nothing, so it is treated as "no identity"
#: (503, which tells the operator) rather than as a door that silently refuses everyone (401, which does not).
_DIGEST_LENGTH = 64
_HEX_DIGITS = frozenset("0123456789abcdef")

_NOT_CONFIGURED_DETAIL = (
    "Dedova strojová identita nie je na tejto inštancii nastavená — nastav premennú prostredia "
    f"DEDO_API_TOKEN_SHA256 (SHA-256 odtlačok tokenu, {_DIGEST_LENGTH} hex znakov; token má mať aspoň "
    f"{MIN_TOKEN_LENGTH} znakov) a reštartuj backend."
)

_UNAUTHORIZED_DETAIL = "Neplatná strojová identita."

_dedo_token_header = APIKeyHeader(name=DEDO_TOKEN_HEADER, auto_error=False)


def _not_configured() -> HTTPException:
    """A FRESH 503 — see "NO SHARED EXCEPTION INSTANCES" in the module docstring."""
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_NOT_CONFIGURED_DETAIL)


def _unauthorized() -> HTTPException:
    """A FRESH 401 — see "NO SHARED EXCEPTION INSTANCES" in the module docstring."""
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=_UNAUTHORIZED_DETAIL)


def _looks_like_a_jwt(value: str) -> bool:
    """Whether ``value`` is shaped like a signed JWT (``eyJ…`` header, three dot-separated parts)."""
    return value.count(".") == 2 and value.startswith("ey")


def _configured_digest() -> str:
    """The SHA-256 hex digest this door admits, or ``""`` when the instance has no identity worth the name.

    ONE function decides "is there an identity here", from either shape, and every rejection of a
    configuration returns ``""`` — so the 503 branch in :func:`require_dedo_identity` is the single place
    that answers an unconfigured instance, and no configuration mistake can fall through into a comparison.
    That matters more than it looks: ``secrets.compare_digest("", "")`` is *True*, so any path that reaches
    a comparison with nothing configured would turn "nobody set a secret" into "everybody is Dedo".
    """
    digest = (settings.dedo_api_token_sha256 or "").strip().lower()
    if digest:
        if len(digest) != _DIGEST_LENGTH or not set(digest) <= _HEX_DIGITS:
            logger.error("Dedo door: refused — DEDO_API_TOKEN_SHA256 is not a %d-character hex digest", _DIGEST_LENGTH)
            return ""
        return digest

    # The weaker, plaintext configuration (see the module docstring). Same three rules as ever: long
    # enough to be a secret, and not a JWT — a JWT-shaped secret would be replayable against the USER
    # doors, which is the one crossing this design exists to prevent, and it is refused here, where the
    # setting is owned, rather than by teaching ``get_current_user`` about Dedo (that would couple the two
    # doors this module keeps apart).
    token = (settings.dedo_api_token or "").strip()
    if len(token) < MIN_TOKEN_LENGTH:
        return ""
    if _looks_like_a_jwt(token):
        logger.error("Dedo door: refused — the configured machine identity looks like a JWT, not an opaque secret")
        return ""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def require_dedo_identity(presented: str | None = Depends(_dedo_token_header)) -> str:
    """Admit the request only as Dedo's machine identity; return :data:`DEDO_IDENTITY`.

    Order matters and is load-bearing: "is there an identity at all" is answered BEFORE any comparison, so
    an unconfigured instance can never fall into ``compare_digest("", "")`` and admit everyone.

    The presented credential is checked for SHAPE before it is checked for value — too short to be a secret,
    or shaped like a JWT, is refused outright. On the digest configuration that shape check is the only
    place those rules can live (a digest says nothing about the plaintext behind it), and enforcing them on
    the presented side means a stub or JWT secret opens this door under NEITHER configuration.

    Raises:
        HTTPException 503: this instance has no Dedo identity configured (the door does not exist yet).
        HTTPException 401: the ``X-Dedo-Token`` header is missing, malformed, or does not match. The refusal
            never names, quotes, truncates or masks either value — a masked secret is still a leaked one.
    """
    configured = _configured_digest()
    if not configured:
        logger.warning("Dedo door: refused — this instance has no machine identity configured")
        raise _not_configured()

    if not presented or len(presented) < MIN_TOKEN_LENGTH or _looks_like_a_jwt(presented):
        logger.warning("Dedo door: refused — request carried no usable %s header", DEDO_TOKEN_HEADER)
        raise _unauthorized()

    if not secrets.compare_digest(hashlib.sha256(presented.encode("utf-8")).hexdigest(), configured):
        logger.warning("Dedo door: refused — %s did not match this instance's machine identity", DEDO_TOKEN_HEADER)
        raise _unauthorized()

    return DEDO_IDENTITY
