"""The cockpit's one door to Plane, the ICC ticket register (DEV-117).

**Why.** Director 14.10.2026: Poradca, the Zásobník and a „Plane tikety" tab are to found and show tickets in Plane.
Until now only the Dedos wrote there, through their gate (``/opt/dedo/nastroje/icc_ticket.py``), which reads every
write back and says whether it holds. The cockpit writes the same way, through this one module:

* **its own identity** — a Plane account of its own with its own API key (``PLANE_API_TOKEN``), never a Dedo's key
  and never a person's account. The key's own rate limit (429) is the cockpit's, so it takes nothing from the Dedos;
* **every write read back** — what the person is told is what Plane returned, not what was sent;
* **429 is not retried in a loop** — the person hears that Plane is busy and tries again;
* **a project's tickets live in the DEV register under the module named after the application** (rule
  „Vývoj patrí do DEV, s modulom aplikácie"); a missing module is created.

DEV-218: a ticket is also read by its identifier („DEV-186“) — the one the agent asks for. Only a ticket of the DEV
register: Director 24.10.2026 „Úplne všetko čo sa týka vývojových prác majú by zachytené v tiketoch DEV. Preto v zadaní
nikde nemá byť iný tiket uvedený ako DEV.“ A ticket of another register is said to need taking over into DEV.

Synchronous (``httpx.Client``); callers in async code run it through ``run_blocking``.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from backend.config.settings import settings

#: The Plane state a new ticket gets unless told otherwise — work nobody has started (rule „Todo je nezačatá práca").
DEFAULT_STATE = "Todo"


class PlaneError(Exception):
    """A sentence for the person: what went wrong with Plane and what to do about it."""


class PlaneMismatch(PlaneError):
    """Plane accepted a write but reads back something else — the write is not to be reported as done."""


class PlaneNotFound(PlaneError):
    """Plane answered 404 — what was asked for is not there."""


#: A ticket's identifier as the registers write it: the register's identifier, a dash, the number („DEV-186“).
TICKET_IDENTIFIER = re.compile(r"([A-Za-z][A-Za-z0-9]{0,11})-(\d{1,7})")


def ticket_identifier(identifier: str, register: str) -> str:
    """``identifier`` as ``register`` writes it („dev-186“ → „DEV-186“) — else :class:`PlaneError` with the sentence:
    not a ticket's number, or a ticket of another register, whose content belongs taken over into ``register``."""
    wanted = identifier.strip()
    match = TICKET_IDENTIFIER.fullmatch(wanted)
    own = register.upper()
    if not match:
        raise PlaneError(f"„{wanted}“ nie je číslo tiketu — napíš ho tak, ako ho píše Plane, napríklad {own}-186.")
    prefix, number = match.group(1).upper(), int(match.group(2))
    if prefix != own:
        raise PlaneError(
            f"Vývojová práca sa vedie v tiketoch {own} — tiket {prefix}-{number} kokpit nečíta, jeho obsah treba "
            f"prevziať do tiketu {own}."
        )
    return f"{own}-{number}"


@dataclass(frozen=True)
class TicketRef:
    id: str
    sequence_id: int
    identifier: str
    url: str


@dataclass
class _Cache:
    project: Optional[dict] = None
    states: Optional[dict[str, str]] = None
    labels: Optional[list[dict]] = None
    extra: dict = field(default_factory=dict)


def name_key(name: str) -> str:
    """A name as Plane compares it — case and spacing do not matter."""
    return " ".join((name or "").split()).casefold()


def text_to_html(text: str) -> str:
    """Plain text with blank-line paragraphs → the HTML Plane stores (``description_html``)."""
    paragraphs = [p for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()]
    return "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in paragraphs) or "<p></p>"


def _html_to_text(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value or "")).split())


def html_to_paragraphs(value: str) -> str:
    """The HTML Plane stores → plain text with its paragraphs and line breaks kept (DEV-122: a ticket's text is a
    fast fix's directive, so its shape matters)."""
    text = re.sub(r"(?i)<br\s*/?>", "\n", value or "")
    text = re.sub(r"(?i)</(p|div|li|h[1-6])>", "\n\n", text)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    lines = [" ".join(line.split()) for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


class PlaneGateway:
    """Reads and writes one Plane project (by its identifier, e.g. ``DEV``) under the cockpit's own identity."""

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        workspace: Optional[str] = None,
        project_identifier: Optional[str] = None,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self.base_url = (base_url if base_url is not None else settings.plane_url).rstrip("/")
        self._token = token if token is not None else settings.plane_api_token
        self.workspace = workspace or settings.plane_workspace
        self.project_identifier = project_identifier or settings.plane_dev_project
        self._client = client
        self._cache = _Cache()

    # --- transport ---------------------------------------------------------------------------------------------

    def _request(self, method: str, path: str, payload: Optional[dict] = None) -> Any:
        if not self.base_url or not self._token:
            raise PlaneError(
                "Kokpit nemá prístup do Plane — chýba adresa alebo kľúč "
                "(PLANE_URL, PLANE_API_TOKEN v nastavení kokpitu)."
            )
        url = f"{self.base_url}/api/v1/workspaces/{self.workspace}/{path.lstrip('/')}"
        client = self._client or httpx.Client(timeout=30)
        try:
            response = client.request(
                method, url, json=payload, headers={"X-Api-Key": self._token, "Content-Type": "application/json"}
            )
        except httpx.HTTPError as exc:
            raise PlaneError(f"Plane na {self.base_url} je nedostupný ({exc.__class__.__name__}).") from exc
        finally:
            if self._client is None:
                client.close()
        if response.status_code == 429:
            raise PlaneError("Plane je práve preťažený (príliš veľa požiadaviek) — skús to o chvíľu znova.")
        if response.status_code in (401, 403):
            raise PlaneError(
                "Plane prístup kokpitu odmietol — kľúč je neplatný, alebo účet kokpitu nie je členom evidencie "
                f"{self.project_identifier} ({response.status_code})."
            )
        if response.status_code >= 400:
            detail = ""
            try:
                body = response.json()
                detail = str(body.get("detail") or body.get("error") or body)[:200] if isinstance(body, dict) else ""
            except ValueError:
                detail = response.text[:200]
            refused = PlaneNotFound if response.status_code == 404 else PlaneError
            raise refused(f"Plane požiadavku odmietol: {response.status_code} {detail}".strip())
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise PlaneError("Plane odpovedal niečím, čo nie je JSON.") from exc

    @staticmethod
    def _results(body: Any) -> list[dict]:
        if isinstance(body, dict) and isinstance(body.get("results"), list):
            return body["results"]
        return body if isinstance(body, list) else []

    # --- the project and its vocabulary ---------------------------------------------------------------------------

    def project(self) -> dict:
        if self._cache.project is None:
            found = [
                p
                for p in self._results(self._request("GET", "projects/"))
                if (p.get("identifier") or "").upper() == self.project_identifier.upper()
            ]
            if not found:
                raise PlaneError(f"Evidencia {self.project_identifier} v Plane nie je alebo ju účet kokpitu nevidí.")
            self._cache.project = found[0]
        return self._cache.project

    def _project_path(self, rest: str) -> str:
        return f"projects/{self.project()['id']}/{rest.lstrip('/')}"

    def states(self) -> dict[str, str]:
        if self._cache.states is None:
            self._cache.states = {
                name_key(s["name"]): s["id"] for s in self._results(self._request("GET", self._project_path("states/")))
            }
        return self._cache.states

    def state_names(self) -> dict[str, tuple[str, str]]:
        """Every column by its id: ``(name, group)`` — the group (``completed``, ``cancelled``, …) says whether a
        ticket is closed, whatever the column is called (DEV-120)."""
        return {
            s["id"]: (s.get("name", ""), s.get("group", ""))
            for s in self._results(self._request("GET", self._project_path("states/")))
        }

    def state_id(self, name: str) -> str:
        found = self.states().get(name_key(name))
        if not found:
            raise PlaneError(f"Evidencia {self.project_identifier} nemá stĺpec „{name}“.")
        return found

    def labels(self) -> list[dict]:
        if self._cache.labels is None:
            self._cache.labels = self._results(self._request("GET", self._project_path("labels/")))
        return self._cache.labels

    def label_id(self, name: str) -> str:
        found = [lab["id"] for lab in self.labels() if name_key(lab.get("name", "")) == name_key(name)]
        if not found:
            raise PlaneError(f"Evidencia {self.project_identifier} nemá štítok „{name}“.")
        return found[0]

    def modules(self) -> list[dict]:
        """Live modules — Plane deletes and archives softly, such a module is still listed but does not count."""
        return [
            m
            for m in self._results(self._request("GET", self._project_path("modules/")))
            if not m.get("archived_at") and not m.get("deleted_at")
        ]

    def ensure_module(self, name: str) -> dict:
        """The module of an application — found by its name, created when it is missing, read back either way."""
        same = [m for m in self.modules() if name_key(m.get("name", "")) == name_key(name)]
        if len(same) > 1:
            raise PlaneError(f"V evidencii {self.project_identifier} sú dva moduly „{name}“ — treba ich zlúčiť.")
        if same:
            return same[0]
        self._request("POST", self._project_path("modules/"), {"name": name})
        created = [m for m in self.modules() if name_key(m.get("name", "")) == name_key(name)]
        if len(created) != 1:
            raise PlaneMismatch(f"Modul „{name}“ sa v evidencii {self.project_identifier} po založení nenašiel.")
        return created[0]

    # --- tickets -----------------------------------------------------------------------------------------------

    def ticket_url(self, issue: dict) -> str:
        return f"{self.base_url}/{self.workspace}/projects/{self.project()['id']}/issues/{issue['id']}"

    def _ref(self, issue: dict) -> TicketRef:
        return TicketRef(
            id=issue["id"],
            sequence_id=int(issue.get("sequence_id") or 0),
            identifier=f"{self.project()['identifier']}-{issue.get('sequence_id')}",
            url=self.ticket_url(issue),
        )

    def module_tickets(self, module_id: str) -> list[dict]:
        """The live tickets of a module — Plane deletes and archives softly, such a ticket is not shown (DEV-120)."""
        return [
            issue
            for issue in self._results(self._request("GET", self._project_path(f"modules/{module_id}/module-issues/")))
            if not issue.get("deleted_at") and not issue.get("archived_at")
        ]

    def find_module(self, module_id: str | None, name: str) -> dict | None:
        """A project's module without founding one — by the id it remembers, else by its name (DEV-120)."""
        live = self.modules()
        by_id = [m for m in live if module_id and m.get("id") == module_id]
        if by_id:
            return by_id[0]
        by_name = [m for m in live if name_key(m.get("name", "")) == name_key(name)]
        if len(by_name) > 1:
            raise PlaneError(f"V evidencii {self.project_identifier} sú dva moduly „{name}“ — treba ich zlúčiť.")
        return by_name[0] if by_name else None

    def get_ticket(self, issue_id: str) -> dict:
        return self._request("GET", self._project_path(f"issues/{issue_id}/"))

    def create_ticket(
        self,
        *,
        title: str,
        description: str,
        module: dict,
        labels: tuple[str, ...] = (),
        state: str = DEFAULT_STATE,
        assignee_ids: tuple[str, ...] = (),
    ) -> TicketRef:
        """Found a ticket in ``module`` (from :meth:`ensure_module` or :meth:`modules`) and read all of it back.

        Raises :class:`PlaneMismatch` when Plane keeps something else than was asked — the ticket then exists,
        and the sentence says what differs, so nobody reports it as written.
        """
        wanted_state = self.state_id(state)
        wanted_labels = sorted(self.label_id(name) for name in labels)
        created = self._request(
            "POST",
            self._project_path("issues/"),
            {
                "name": title,
                "description_html": text_to_html(description),
                "state": wanted_state,
                "labels": wanted_labels,
                "assignees": list(assignee_ids),
            },
        )
        if not created.get("id"):
            raise PlaneMismatch("Plane tiket nezaložil — odpoveď nenesie jeho identifikátor.")
        ref = self._ref(created)
        self._request("POST", self._project_path(f"modules/{module['id']}/module-issues/"), {"issues": [ref.id]})

        back = self.get_ticket(ref.id)
        differs: list[str] = []
        if back.get("name") != title:
            differs.append("názov")
        if back.get("state") != wanted_state:
            differs.append("stĺpec")
        if sorted(back.get("labels") or []) != wanted_labels:
            differs.append("štítky")
        if sorted(back.get("assignees") or []) != sorted(assignee_ids):
            differs.append("pridelenie")
        if _html_to_text(back.get("description_html", "")) != _html_to_text(text_to_html(description)):
            differs.append("popis")
        in_module = self._results(self._request("GET", self._project_path(f"modules/{module['id']}/module-issues/")))
        if not any((i.get("id") or i.get("issue")) == ref.id for i in in_module):
            differs.append(f"modul {module.get('name')}")
        if differs:
            raise PlaneMismatch(
                f"Tiket {ref.identifier} vznikol, ale Plane drží iné než sa zapisovalo: {', '.join(differs)}."
            )
        return ref

    def comment(self, issue_id: str, text: str) -> None:
        """Add a comment and read it back among the ticket's comments."""
        body = text_to_html(text)
        self._request("POST", self._project_path(f"issues/{issue_id}/comments/"), {"comment_html": body})
        comments = self._results(self._request("GET", self._project_path(f"issues/{issue_id}/comments/")))
        if not any(_html_to_text(c.get("comment_html", "")) == _html_to_text(body) for c in comments):
            raise PlaneMismatch("Komentár sa po zápise medzi komentármi tiketu nenašiel.")

    def set_state(self, issue_id: str, state: str) -> None:
        wanted = self.state_id(state)
        self._request("PATCH", self._project_path(f"issues/{issue_id}/"), {"state": wanted})
        if self.get_ticket(issue_id).get("state") != wanted:
            raise PlaneMismatch(f"Tiket sa do stĺpca „{state}“ neposunul.")

    # --- a ticket of this register by its identifier (DEV-218) ------------------------------------------------------

    def member_names(self) -> dict[str, str]:
        """The people of the workspace by their id → the name they go by (first and last name, else the display one)."""
        names = {}
        for m in self._results(self._request("GET", "members/")):
            full = " ".join(p for p in (m.get("first_name"), m.get("last_name")) if p)
            names[m.get("id", "")] = full or m.get("display_name") or ""
        return names

    def ticket_by_identifier(self, identifier: str) -> tuple[dict, list[dict]]:
        """``(ticket, its live comments oldest first)`` of the ticket ``identifier`` of this register (``DEV-186``).

        Raises :class:`PlaneError` with the sentence for a person: not this register's ticket
        (:func:`ticket_identifier`), not there, or deleted. Plane refuses an identifier in small letters (measured
        24.10.2026) — it is sent in capitals."""
        ident = ticket_identifier(identifier, self.project_identifier)
        try:
            issue = self._request("GET", f"issues/{ident}/")
        except PlaneNotFound as exc:
            raise PlaneError(f"Tiket {ident} v Plane nie je.") from exc
        if issue.get("deleted_at"):
            raise PlaneError(f"Tiket {ident} je v Plane zmazaný.")
        comments = [
            c
            for c in self._results(self._request("GET", self._project_path(f"issues/{issue['id']}/comments/")))
            if not c.get("deleted_at")
        ]
        return issue, sorted(comments, key=lambda c: c.get("created_at") or "")

    def who_am_i(self) -> str:
        """The e-mail of the account the key belongs to — proves which identity the cockpit writes under."""
        url = f"{self.base_url}/api/v1/users/me/"
        client = self._client or httpx.Client(timeout=30)
        try:
            response = client.get(url, headers={"X-Api-Key": self._token or ""})
        except httpx.HTTPError as exc:
            raise PlaneError(f"Plane na {self.base_url} je nedostupný ({exc.__class__.__name__}).") from exc
        finally:
            if self._client is None:
                client.close()
        if response.status_code != 200:
            raise PlaneError(f"Plane kľúč kokpitu neuznal ({response.status_code}).")
        return str(response.json().get("email") or "bez mena")
