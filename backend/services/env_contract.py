"""Who provides each setting of an app — the markers in its ``.env.example`` (DEV-72).

12.10.2026 a manager wanted to deploy career-asistent for a new customer. The cockpit hands an app the customer's
secret only as ``ADMIN_INITIAL_PASSWORD``; career-asistent creates its first admin from ``BOOTSTRAP_ADMIN_EMAIL``
and ``BOOTSTRAP_ADMIN_PASSWORD``, so the deploy would have rendered an empty e-mail and a random password and the
installation would have had no admin at all. Nor could the manager enter anything else the customer has to
provide (the mailbox, the outgoing mail), and the app's public address — which it must know exactly — was never
passed, although the cockpit itself puts it into the Traefik route.

The app says it in its own ``.env.example``, in a comment of the block right above the variable:

* ``# @customer: „text for the manager“`` — the manager enters the value for each customer;
* ``# @customer-secret: „text“`` — the same, but a secret: kept in the credentials store, never shown back;
* ``required`` after the marker (``# @customer required: „…“``) — no deploy without a value;
* ``# @public-url`` — the cockpit writes the installation's public address (in a Web project: the web's).
* ``# @public-url-admin`` — a Web project's administration address (``…-admin``; DEV-164).

The text is in quotes because it is shown to a person (the cockpit's language rule: Slovak only in displayed text).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

CUSTOMER = "customer"
CUSTOMER_SECRET = "customer_secret"
PUBLIC_URL = "public_url"
#: DEV-164: a Web project's administration has an address of its own (``…-admin``); ``@public-url`` is the web's.
PUBLIC_URL_ADMIN = "public_url_admin"

_KINDS = {
    "customer": CUSTOMER,
    "customer-secret": CUSTOMER_SECRET,
    "public-url": PUBLIC_URL,
    "public-url-admin": PUBLIC_URL_ADMIN,
}

#: ``# @<word> [required][: text]`` — any ``# @word`` is a marker, so a misspelt one is reported, not ignored.
_MARKER = re.compile(r"^#\s*@([\w-]+)(?:\s+(required))?\s*(?::\s*(.*?))?\s*$")
#: A variable's line — also what Poradca's copy of the template keeps of it (``secret_files.names_only``, DEV-177).
VARIABLE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")
#: The quotes a manager-facing text may stand in — typographic, straight, or the two mixed („…" is common).
_QUOTED = re.compile(r"^[„\"“](.*)[“”\"]$")

#: The legacy contract (v4.0): the customer's „Tajný kľúč“ is the password of the app's ``admin`` login.
LEGACY_ADMIN_PASSWORD = "ADMIN_INITIAL_PASSWORD"

#: A variable ending so is a secret: the provisioner gives it a random value unless something provides it.
SECRET_SUFFIXES = ("_password", "_secret", "_key", "_token")


@dataclass(frozen=True)
class Setting:
    """One variable whose value does not come from the app's template."""

    key: str
    kind: str
    required: bool
    label: str


@dataclass(frozen=True)
class Contract:
    settings: tuple[Setting, ...]
    #: Sentences for the manager about markers that mean nothing — misspelt, or standing above no variable.
    problems: tuple[str, ...]

    def of_kind(self, *kinds: str) -> list[Setting]:
        return [s for s in self.settings if s.kind in kinds]

    @property
    def customer_keys(self) -> set[str]:
        return {s.key for s in self.of_kind(CUSTOMER, CUSTOMER_SECRET)}

    @property
    def public_url_keys(self) -> set[str]:
        return {s.key for s in self.of_kind(PUBLIC_URL)}

    @property
    def public_url_admin_keys(self) -> set[str]:
        return {s.key for s in self.of_kind(PUBLIC_URL_ADMIN)}


def _label(raw: str | None, key: str) -> str:
    text = (raw or "").strip()
    quoted = _QUOTED.match(text)
    return (quoted.group(1) if quoted else text).strip() or key


def parse(text: str) -> Contract:
    """The markers of one ``.env.example``. A marker belongs to the first variable below it in the same comment
    block; a blank line ends the block."""
    settings: list[Setting] = []
    problems: list[str] = []
    pending: list[tuple[int, str, bool, str]] = []  # (line, kind, required, label) waiting for their variable
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            problems.extend(f"značka na riadku {n} nestojí nad žiadnou premennou" for n, *_ in pending)
            pending = []
            continue
        if line.startswith("#"):
            marker = _MARKER.match(line)
            if marker:
                word, required, label = marker.group(1), bool(marker.group(2)), marker.group(3)
                if word not in _KINDS:
                    problems.append(f"neznáma značka „@{word}“ na riadku {number}")
                else:
                    pending.append((number, _KINDS[word], required, label or ""))
            continue
        variable = VARIABLE.match(line)
        if not variable:
            continue
        key = variable.group(1)
        for _n, kind, required, label in pending:
            settings.append(Setting(key=key, kind=kind, required=required, label=_label(label, key)))
        if len(pending) > 1:
            problems.append(f"premenná {key} má viac značiek naraz")
        pending = []
    problems.extend(f"značka na riadku {n} nestojí nad žiadnou premennou" for n, *_ in pending)
    return Contract(settings=tuple(settings), problems=tuple(problems))


def for_project(project_path: Path) -> Contract:
    """The contract of a project's working tree — the same ``.env.example`` the provisioner renders from."""
    path = project_path / ".env.example"
    if not path.is_file():
        return Contract(settings=(), problems=())
    return parse(path.read_text(encoding="utf-8"))


def declares_legacy_admin(project_path: Path) -> bool:
    """Whether the app takes its ``admin`` password from ``ADMIN_INITIAL_PASSWORD`` (the „Tajný kľúč“ field)."""
    path = project_path / ".env.example"
    if not path.is_file():
        return False
    return any(
        line.strip().startswith(f"{LEGACY_ADMIN_PASSWORD}=") for line in path.read_text(encoding="utf-8").splitlines()
    )


def unknowable_admin_secrets(contract: Contract, template_keys: Sequence[str]) -> list[str]:
    """Admin credentials the deploy would fill with a random value nobody knows — a secret variable that names the
    admin, is not the legacy ``ADMIN_INITIAL_PASSWORD`` and is not provided by the customer.

    The rule is the danger itself, not a missing marker: NEX Manager seeds its first admin with a documented default
    password and has no such variable, so it deploys as before; career-asistent's ``BOOTSTRAP_ADMIN_PASSWORD`` would
    have been random and the installation unreachable.
    """
    provided = contract.customer_keys
    return sorted(
        k
        for k in template_keys
        if "ADMIN" in k.upper()
        and k.lower().endswith(SECRET_SUFFIXES)
        and k != LEGACY_ADMIN_PASSWORD
        and k not in provided
    )


def deploy_blockers(contract: Contract, *, template_keys: Sequence[str], values: Mapping[str, str]) -> list[str]:
    """Why a deploy must not start — one sentence each, for the manager; ``[]`` = nothing stops it.

    ``template_keys`` are the variables of the app's ``.env.example``; ``values`` are what the manager entered for
    the customer (settings and secrets together).
    """
    blockers = [f"Nastavenia aplikácie (.env.example) sa nedajú prečítať: {p}." for p in contract.problems]
    missing = [s.label for s in contract.of_kind(CUSTOMER, CUSTOMER_SECRET) if s.required and not values.get(s.key)]
    if missing:
        blockers.append(
            "Zákazníkovi chýbajú povinné nastavenia: " + "; ".join(missing) + ". Doplň ich v sekcii Zákazníci."
        )
    unknowable = unknowable_admin_secrets(contract, template_keys)
    if unknowable:
        blockers.append(
            "Heslo správcu aplikácie by dostalo náhodnú hodnotu, ktorú nikto nepozná ("
            + ", ".join(unknowable)
            + "), a do nasadenej aplikácie by sa nikto neprihlásil. Treba ju upraviť rýchlou opravou: v jej "
            ".env.example označiť heslo správcu značkou @customer-secret required (a e-mail správcu @customer "
            "required), potom ich zadať v sekcii Zákazníci."
        )
    return blockers


def customer_values(contract: Contract, settings: Mapping[str, str], secrets: Mapping[str, str]) -> dict[str, str]:
    """What the manager entered, limited to what the app declares — nothing else ever reaches its ``.env``."""
    declared = {s.key: s.kind for s in contract.settings}
    values = {k: v for k, v in settings.items() if declared.get(k) == CUSTOMER and v}
    values.update({k: v for k, v in secrets.items() if declared.get(k) == CUSTOMER_SECRET and v})
    return values


def undeclared(contract: Contract, settings: Sequence[str], secrets: Sequence[str]) -> list[str]:
    """Keys a customer may not set: not declared, or declared as the other kind (a secret is never a plain
    setting, so it cannot end up readable on the customer row)."""
    declared = {s.key: s.kind for s in contract.settings}
    return sorted(
        [k for k in settings if declared.get(k) != CUSTOMER]
        + [k for k in secrets if declared.get(k) != CUSTOMER_SECRET]
    )
