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


def _key(name: str) -> str:
    return " ".join((name or "").split()).casefold()


def text_to_html(text: str) -> str:
    """Plain text with blank-line paragraphs → the HTML Plane stores (``description_html``)."""
    paragraphs = [p for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()]
    return "".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in paragraphs) or "<p></p>"


def _html_to_text(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value or "")).split())


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
            raise PlaneError(f"Plane požiadavku odmietol: {response.status_code} {detail}".strip())
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
                _key(s["name"]): s["id"] for s in self._results(self._request("GET", self._project_path("states/")))
            }
        return self._cache.states

    def state_id(self, name: str) -> str:
        found = self.states().get(_key(name))
        if not found:
            raise PlaneError(f"Evidencia {self.project_identifier} nemá stĺpec „{name}“.")
        return found

    def labels(self) -> list[dict]:
        if self._cache.labels is None:
            self._cache.labels = self._results(self._request("GET", self._project_path("labels/")))
        return self._cache.labels

    def label_id(self, name: str) -> str:
        found = [lab["id"] for lab in self.labels() if _key(lab.get("name", "")) == _key(name)]
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
        same = [m for m in self.modules() if _key(m.get("name", "")) == _key(name)]
        if len(same) > 1:
            raise PlaneError(f"V evidencii {self.project_identifier} sú dva moduly „{name}“ — treba ich zlúčiť.")
        if same:
            return same[0]
        self._request("POST", self._project_path("modules/"), {"name": name})
        created = [m for m in self.modules() if _key(m.get("name", "")) == _key(name)]
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
