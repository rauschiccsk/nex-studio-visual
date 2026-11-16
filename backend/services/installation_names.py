"""The names of an installation on another machine lead to that machine (DEV-405).

16.11.2026 the Manažér deployed NEX Manager 1.6.0 to PROD for SKIT o.z. on MAGER and the cockpit said „Nasadené“,
yet the app did not open. Its name ``skit-manager.icc.sk`` had no record of its own in DNS, so the zone's catch-all
record ``*.icc.sk`` sent it to a web hosting that answered 403 — and the check after the deploy took any answer below
500 but 404 for the app's. The names of ICC and MÁGERSTAV worked because the Director had created their records in
Cloudflare by hand; for a new customer nobody did, and the cockpit could not. Director: „Kokpit musí vedieť urobiť od
zadania do nasadenia na PROD, bez Deda.“

So before an installation starts on a machine whose names the cockpit looks after (:attr:`ProdMachine.address`), every
name of its routers is asked in public DNS. A name that does not lead to the machine and has no record of its own in
Cloudflare gets one — ``A`` to the machine's address, not proxied (the machine is reached over Tailscale) — made with
the cockpit's own key, which writes the DNS of the zone only. A record somebody else made is never rewritten. After
the deploy the route is believed only when its names lead to the machine (:func:`check`).

The zone is the truth, public resolvers only remember it. 16.11.2026 the redeploy of SKIT o.z. created the record at
11:13:31 and the check after the deploy still got the catch-all from public resolvers, which keep an answer up to its
TTL (300 s) — by 11:18 all of them showed MAGER, yet the deploy had been recorded as failed. So a name counts as
leading to the machine when public DNS says so or the zone itself does; how long devices may still see the old
answer is said, not waited for.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Optional

import httpx
import yaml

from backend.config.settings import ProdMachine, settings
from backend.services import prod_machines, public_route

#: DNS-over-HTTPS answer codes that are answers: the name exists, or it does not.
_ANSWERED = (0, 3)
#: The record type of an IPv4 address in a DNS-over-HTTPS answer.
_TYPE_A = 1
#: How long a comment Cloudflare keeps on a record of a zone on its free plan.
COMMENT_MAX = 100


class NameLookupFailed(Exception):
    """Public DNS gave no answer — nothing can be said about where the name leads."""


class CloudflareRefused(Exception):
    """Cloudflare did not do what was asked; the message is a sentence for the Manažér."""


def machine(deploy_host: Optional[str]) -> Optional[ProdMachine]:
    """The machine of ``deploy_host`` when its names are the cockpit's to look after, else ``None``."""
    try:
        found = prod_machines.machine((deploy_host or "").strip() or None)
    except ValueError:  # a machine the cockpit does not know — its installation is neither named nor routed by it
        return None
    return found if found and found.address else None


def public_answer(name: str) -> tuple[list[str], int]:
    """``(the IPv4 addresses public DNS gives for name, how many seconds a resolver may still keep that answer)`` —
    no addresses when the name does not exist.

    Asked of a public resolver over HTTPS, not of this machine's resolver, whose cache may hold an older answer."""
    try:
        response = httpx.get(
            settings.public_dns_url,
            params={"name": name, "type": "A"},
            headers={"accept": "application/dns-json"},
            timeout=settings.public_dns_timeout_seconds,
        )
        response.raise_for_status()
        answer = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise NameLookupFailed(type(exc).__name__) from exc
    status = answer.get("Status") if isinstance(answer, dict) else None
    if status not in _ANSWERED:
        raise NameLookupFailed(f"DNS status {status}")
    records = [rec for rec in answer.get("Answer") or [] if rec.get("type") == _TYPE_A]
    remembered = max((int(rec.get("TTL") or 0) for rec in records), default=0)
    return sorted({str(rec.get("data")) for rec in records}), remembered


def _where(addresses: list[str]) -> str:
    return ", ".join(addresses) if addresses else "nikam (meno neexistuje)"


def _first_up(text: str) -> str:
    """The clause as the start of a sentence — only its first letter raised (``str.capitalize`` would lower MAGER)."""
    return text[:1].upper() + text[1:]


def _elsewhere(name: str, addresses: list[str], target: ProdMachine) -> str:
    return f"meno {name} vedie na {_where(addresses)}, nie na stroj {target.label} v Tailscale ({target.address})"


def names(compose: dict[str, Any], target: ProdMachine) -> list[str]:
    """The names of the installation in the machine's zone — the ones people open and the cockpit looks after. A
    router on a name of another kind (MAGER's Tailscale name ``mager.tail5c98e2.ts.net`` on an entrypoint of the
    installation's own, with Tailscale's certificate) is resolved by Tailscale, not by public DNS, and is left alone."""
    return [name for name in public_route.route_hosts(compose) if name.endswith(f".{target.zone}")]


def check(compose: dict[str, Any], deploy_host: Optional[str]) -> tuple[str, str]:
    """``(state, what was seen)`` in :mod:`public_route`'s verdicts: ``ok`` when every name of the installation leads
    to its machine (or the machine's names are not the cockpit's to look after), ``down`` naming where a wrong one
    leads — another server answers there —, ``skip`` when public DNS did not answer."""
    target = machine(deploy_host)
    if target is None:
        return public_route.STATE_OK, ""
    wrong: list[str] = []
    for name in names(compose, target):
        try:
            addresses, _remembered = public_answer(name)
        except NameLookupFailed as exc:
            return public_route.STATE_SKIP, f"verejné DNS na meno {name} neodpovedá ({exc})"
        if addresses != [target.address] and not _zone_leads_there(name, target):
            wrong.append(_elsewhere(name, addresses, target))
    if wrong:
        return public_route.STATE_DOWN, "; ".join(wrong) + " — odpovedá tam iný server"
    return public_route.STATE_OK, ""


def check_cap(compose: dict[str, Any]) -> float:
    """How long :func:`check` may take for ``compose`` — one DNS question per name, and a margin."""
    return settings.public_dns_timeout_seconds * max(1, len(public_route.route_hosts(compose))) + 10


def _cloudflare(method: str, path: str, **kwargs: Any) -> Any:
    """One call of Cloudflare's API with the cockpit's key — its ``result``, or :class:`CloudflareRefused` with
    Cloudflare's own words."""
    try:
        response = httpx.request(
            method,
            f"{settings.cloudflare_api_url}{path}",
            headers={"Authorization": f"Bearer {settings.cloudflare_dns_token}"},
            timeout=settings.cloudflare_timeout_seconds,
            **kwargs,
        )
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise CloudflareRefused(f"Cloudflare neodpovedá ({type(exc).__name__})") from exc
    if not isinstance(body, dict) or not body.get("success"):
        errors = body.get("errors") if isinstance(body, dict) else None
        said = "; ".join(str(e.get("message", "")) for e in errors or [] if isinstance(e, dict))
        raise CloudflareRefused(f"Cloudflare odmietol: {said or f'HTTP {response.status_code}'}")
    return body.get("result")


def _zone_id(zone: str) -> str:
    zones = _cloudflare("GET", "/zones", params={"name": zone}) or []
    if not zones:
        raise CloudflareRefused(f"kľúč kokpitu zónu {zone} v Cloudflare nevidí")
    return str(zones[0]["id"])


def _zone_records(name: str, target: ProdMachine) -> list[dict[str, Any]]:
    """The records the zone itself holds for ``name`` — what its nameservers serve, not what a resolver remembers."""
    return _cloudflare("GET", f"/zones/{_zone_id(target.zone)}/dns_records", params={"name": name}) or []


def _foreign(records: list[dict[str, Any]], target: ProdMachine) -> Optional[dict[str, Any]]:
    """A record of the name that does not lead straight to the machine — the cockpit does not rewrite it."""
    for record in records:
        if record.get("type") != "A" or record.get("content") != target.address or record.get("proxied"):
            return record
    return None


def _zone_leads_there(name: str, target: ProdMachine) -> bool:
    """The zone says ``name`` leads straight to the machine — public resolvers will follow within their TTL."""
    if not settings.cloudflare_dns_token:
        return False
    try:
        records = _zone_records(name, target)
    except CloudflareRefused:
        return False
    return bool(records) and _foreign(records, target) is None


def _remembered_note(seconds: int) -> str:
    """How long a device that asked a moment ago may still be sent where the name led before."""
    if seconds <= 0:
        return ""
    return f" Zariadenie, ktoré sa na meno pýtalo pred chvíľou, ho uvidí najneskôr o {math.ceil(seconds / 60)} min."


def _ensure_name(name: str, target: ProdMachine, owner: str) -> tuple[bool, str]:
    """``(the name leads to the machine, sentence)`` — creates its record when it has none; the sentence says what was
    done, or why the installation must not start."""
    try:
        seen, remembered = public_answer(name)
    except NameLookupFailed as exc:
        return False, f"Verejné DNS na meno {name} neodpovedá ({exc}) — nasadenie nezačalo; skús ho o chvíľu znova."
    if seen == [target.address]:
        return True, ""
    elsewhere = _elsewhere(name, seen, target)
    if not settings.cloudflare_dns_token:
        return False, (
            f"{_first_up(elsewhere)}. Kokpit by meno v Cloudflare založil sám, ale v jeho nastavení chýba kľúč do "
            "Cloudflare (CLOUDFLARE_DNS_TOKEN) — nasadenie nezačalo."
        )
    try:
        records = _zone_records(name, target)
        foreign = _foreign(records, target)
        if foreign is not None:
            proxy = ", cez proxy Cloudflare" if foreign.get("proxied") else ""
            record = f"{foreign.get('type')} → {foreign.get('content')}{proxy}"
            return False, (
                f"Meno {name} má v Cloudflare vlastný záznam ({record}), "
                f"ktorý nevedie na stroj {target.label} v Tailscale ({target.address}). Kokpit cudzí záznam "
                "neprepisuje — nasadenie nezačalo; over, komu meno patrí."
            )
        created = not records
        if created:
            _cloudflare(
                "POST",
                f"/zones/{_zone_id(target.zone)}/dns_records",
                json={
                    "type": "A",
                    "name": name,
                    "content": target.address,
                    "ttl": 1,
                    "proxied": False,
                    "comment": f"NEX Studio Visual — {owner}"[:COMMENT_MAX],
                },
            )
    except CloudflareRefused as exc:
        return False, f"{_first_up(elsewhere)} a záznam v Cloudflare sa nepodarilo založiť: {exc} — nasadenie nezačalo."
    if not created:
        return True, ""
    return True, (
        f"Meno {name} dostalo v Cloudflare záznam na stroj {target.label} v Tailscale ({target.address})."
        + _remembered_note(remembered)
    )


def ensure(compose_path: Path, deploy_host: Optional[str], owner: str) -> tuple[bool, str]:
    """``(every name of the installation leads to its machine, sentence)`` — before the installation starts. Names
    without a record get one; the sentence says which, or why the deploy must not begin. A machine whose names are
    not the cockpit's to look after: ``(True, "")``."""
    target = machine(deploy_host)
    if target is None:
        return True, ""
    try:
        compose = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return False, f"Predpis inštalácie sa nedá prečítať, mená v DNS sa nedajú overiť ({exc}) — nasadenie nezačalo."
    done: list[str] = []
    for name in names(compose, target):
        ok, sentence = _ensure_name(name, target, owner)
        if not ok:
            return False, sentence
        if sentence:
            done.append(sentence)
    return True, " ".join(done)
