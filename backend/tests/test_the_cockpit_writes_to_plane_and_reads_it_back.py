"""DEV-117 — the cockpit writes to Plane under its own identity, and only what Plane reads back counts as written.

Director 14.10.2026 wants Poradca, the Zásobník and a „Plane tikety" tab to found and show tickets. This is the door
they all go through: the cockpit's own key, one module per application in the DEV register, every write read back,
429 said once and not retried, and the key never handed to an agent.
"""

from __future__ import annotations

import itertools
import json
import os
from types import SimpleNamespace

import httpx
import pytest

from backend.core.agent_env import agent_env
from backend.services import plane_tickets
from backend.services.plane_gateway import PlaneError, PlaneGateway, PlaneMismatch

KEY = "cockpit-key"
P = "/api/v1/workspaces/icc/projects/p1"


class FakePlane:
    """Plane's API as far as the cockpit uses it, in memory."""

    def __init__(
        self,
        *,
        status: int | None = None,
        drop_labels: bool = False,
        ignore_patch: bool = False,
        drop_comments: bool = False,
    ):
        self.status, self.drop_labels, self.ignore_patch = status, drop_labels, ignore_patch
        self.drop_comments = drop_comments
        self.requests: list[httpx.Request] = []
        self.labels = [
            {"id": "l-task", "name": "Typ: Task"},
            {"id": "l-bug", "name": "Typ: Bug"},
            {"id": "l-andros", "name": "Zodpovedný: Andros Dedo"},
        ]
        self.modules: list[dict] = []
        self.issues: dict[str, dict] = {}
        self.module_issues: dict[str, list[str]] = {}
        self.comments: dict[str, list[dict]] = {}
        self._seq = itertools.count(1)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status:
            return httpx.Response(self.status, json={"detail": "nope"})
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else {}
        if path == "/api/v1/users/me/":
            return httpx.Response(200, json={"email": "nex-studio@icc.sk"})
        if path == "/api/v1/workspaces/icc/projects/":
            return httpx.Response(200, json={"results": [{"id": "p1", "identifier": "DEV"}]})
        if path == f"{P}/states/":
            return httpx.Response(
                200, json={"results": [{"id": "s-todo", "name": "Todo"}, {"id": "s-ip", "name": "In Progress"}]}
            )
        if path == f"{P}/labels/":
            return httpx.Response(200, json={"results": self.labels})
        if path == f"{P}/modules/":
            if method == "POST":
                self.modules.append({"id": f"m{len(self.modules) + 1}", "name": body["name"]})
                return httpx.Response(201, json=self.modules[-1])
            return httpx.Response(200, json={"results": self.modules})
        if path == f"{P}/issues/" and method == "POST":
            issue = {
                "id": f"i{len(self.issues) + 1}",
                "sequence_id": next(self._seq),
                "name": body["name"],
                "description_html": body["description_html"],
                "state": body["state"],
                "labels": [] if self.drop_labels else body["labels"],
                "assignees": body["assignees"],
            }
            self.issues[issue["id"]] = issue
            return httpx.Response(201, json=issue)
        parts = path.removeprefix(P + "/").strip("/").split("/")
        if parts[0] == "issues" and len(parts) == 2:
            if method == "PATCH" and not self.ignore_patch:
                self.issues[parts[1]].update(body)
            return httpx.Response(200, json=self.issues[parts[1]])
        if parts[0] == "issues" and parts[2:] == ["comments"]:
            if method == "POST":
                if not self.drop_comments:
                    self.comments.setdefault(parts[1], []).append({"comment_html": body["comment_html"]})
                return httpx.Response(201, json={})
            return httpx.Response(200, json={"results": self.comments.get(parts[1], [])})
        if parts[0] == "modules" and parts[2:] == ["module-issues"]:
            if method == "POST":
                self.module_issues.setdefault(parts[1], []).extend(body["issues"])
                return httpx.Response(201, json={})
            return httpx.Response(200, json={"results": [{"id": i} for i in self.module_issues.get(parts[1], [])]})
        return httpx.Response(404, json={"detail": f"unknown {method} {path}"})


def _gateway(plane: FakePlane, token: str = KEY) -> PlaneGateway:
    client = httpx.Client(transport=httpx.MockTransport(plane.handler))
    return PlaneGateway(
        base_url="http://plane.test", token=token, workspace="icc", project_identifier="DEV", client=client
    )


class _Db:
    flushed = 0

    def flush(self):
        self.flushed += 1


ZOLTAN = SimpleNamespace(first_name="Zoltán", last_name="Rausch", username="rausch")


def _file(plane: FakePlane, project, **over):
    args = dict(
        project=project,
        user=ZOLTAN,
        source="zasobnik",
        title="Faktúra FLORASERVIS sa nespracuje z ISDOC",
        description="Čo sa deje: …",
        labels=("Typ: Bug",),
        doer="Zoltán Rausch",
    )
    args.update(over)
    return plane_tickets.file_ticket(_Db(), _gateway(plane), **args)


def test_a_ticket_lands_in_the_projects_module_says_who_founded_it_and_is_read_back():
    plane = FakePlane()
    project = SimpleNamespace(name="NEX Inbox", plane_module_id=None)

    ref = _file(plane, project)

    assert ref.identifier == "DEV-1"
    assert plane.modules == [{"id": "m1", "name": "NEX Inbox"}], "the missing module is created"
    assert project.plane_module_id == "m1", "and remembered on the project"
    assert plane.module_issues == {"m1": [ref.id]}
    issue = plane.issues[ref.id]
    assert issue["state"] == "s-todo" and issue["labels"] == ["l-bug"]
    assert issue["description_html"].startswith("<p>Robí: Zoltán Rausch</p>")
    assert issue["description_html"].endswith(
        "<p>Založil: Zoltán Rausch cez NEX Studio Visual (zo Zásobníka), projekt NEX Inbox.</p>"
    )


def test_the_remembered_module_wins_over_the_projects_name():
    plane = FakePlane()
    plane.modules = [{"id": "m-kept", "name": "NEX Inbox (staré meno)"}, {"id": "m-other", "name": "NEX Inbox"}]
    project = SimpleNamespace(name="NEX Inbox", plane_module_id="m-kept")

    ref = _file(plane, project)

    assert plane.module_issues == {"m-kept": [ref.id]}
    assert len(plane.modules) == 2, "no module was created"


def test_a_problem_of_the_cockpit_goes_to_its_own_module():
    plane = FakePlane()
    plane.modules = [{"id": "m-nsv", "name": "NEX Studio Visual"}]
    project = SimpleNamespace(name="NEX Inbox", plane_module_id=None)

    ref = _file(plane, project, module_name="NEX Studio Visual", doer="Andros Dedo", source="poradca")

    assert plane.module_issues == {"m-nsv": [ref.id]}
    assert project.plane_module_id is None, "a cockpit ticket does not rebind the project"


def test_what_plane_keeps_differently_is_not_reported_as_written():
    plane = FakePlane(drop_labels=True)

    with pytest.raises(PlaneMismatch, match=r"Tiket DEV-1 vznikol, ale Plane drží iné než sa zapisovalo: štítky\."):
        _file(plane, SimpleNamespace(name="NEX Inbox", plane_module_id=None))


def test_an_unknown_label_stops_before_anything_is_written():
    plane = FakePlane()

    with pytest.raises(PlaneError, match="nemá štítok „Typ: Neexistuje“"):
        _file(plane, SimpleNamespace(name="NEX Inbox", plane_module_id=None), labels=("Typ: Neexistuje",))
    assert not any(r.method == "POST" and r.url.path.endswith("/issues/") for r in plane.requests)


def test_two_modules_of_one_application_stop_the_write():
    plane = FakePlane()
    plane.modules = [{"id": "a", "name": "NEX Inbox"}, {"id": "b", "name": "nex  inbox"}]

    with pytest.raises(PlaneError, match="dva moduly"):
        _file(plane, SimpleNamespace(name="NEX Inbox", plane_module_id=None))


@pytest.mark.parametrize(
    "status, sentence",
    [
        (429, "Plane je práve preťažený (príliš veľa požiadaviek) — skús to o chvíľu znova."),
        (401, "kľúč je neplatný, alebo účet kokpitu nie je členom evidencie DEV (401)"),
        (403, "kľúč je neplatný, alebo účet kokpitu nie je členom evidencie DEV (403)"),
    ],
)
def test_a_refusal_is_said_once_and_not_retried(status, sentence):
    plane = FakePlane(status=status)

    with pytest.raises(PlaneError) as caught:
        _gateway(plane).project()

    assert sentence in str(caught.value)
    assert len(plane.requests) == 1


def test_without_a_key_the_cockpit_says_it_has_no_access_and_asks_nothing():
    plane = FakePlane()

    with pytest.raises(PlaneError, match="Kokpit nemá prístup do Plane"):
        _gateway(plane, token="").project()
    assert plane.requests == []


def test_every_request_carries_the_cockpits_own_key():
    plane = FakePlane()
    _file(plane, SimpleNamespace(name="NEX Inbox", plane_module_id=None))

    assert plane.requests and {r.headers.get("X-Api-Key") for r in plane.requests} == {KEY}


def test_a_comment_and_a_state_are_read_back():
    plane = FakePlane()
    gateway = _gateway(plane)
    ref = _file(plane, SimpleNamespace(name="NEX Inbox", plane_module_id=None))

    gateway.comment(ref.id, "Zaradené do verzie 1.8.1.")
    gateway.set_state(ref.id, "In Progress")

    assert plane.issues[ref.id]["state"] == "s-ip"
    with pytest.raises(PlaneMismatch, match="neposunul"):
        stuck = FakePlane(ignore_patch=True)
        stuck_ref = _file(stuck, SimpleNamespace(name="NEX Inbox", plane_module_id=None))
        _gateway(stuck).set_state(stuck_ref.id, "In Progress")


def test_who_am_i_names_the_account_the_key_belongs_to():
    assert _gateway(FakePlane()).who_am_i() == "nex-studio@icc.sk"


def test_the_plane_key_never_reaches_an_agent(monkeypatch):
    monkeypatch.setitem(os.environ, "PLANE_API_TOKEN", "s3cr3t-plane-key")

    assert "PLANE_API_TOKEN" not in agent_env()
    assert "s3cr3t-plane-key" not in agent_env().values()


def test_a_comment_plane_did_not_keep_is_not_reported_as_written():
    plane = FakePlane(drop_comments=True)
    ref = _file(plane, SimpleNamespace(name="NEX Inbox", plane_module_id=None))

    with pytest.raises(PlaneMismatch, match="Komentár sa po zápise medzi komentármi tiketu nenašiel"):
        _gateway(plane).comment(ref.id, "Zaradené do verzie 1.8.1.")


def test_the_check_says_whose_key_it_is_and_never_prints_it(monkeypatch, capsys):
    from backend.scripts import plane_check

    plane = FakePlane()
    monkeypatch.setattr(plane_check, "PlaneGateway", lambda: _gateway(plane))

    assert plane_check.main([]) == 0

    out = capsys.readouterr()
    assert out.out.splitlines()[:2] == ["kľúč patrí kontu: nex-studio@icc.sk", "evidencia: DEV ()"]
    assert KEY not in out.out + out.err


def test_the_check_says_a_refusal_as_a_sentence(monkeypatch, capsys):
    from backend.scripts import plane_check

    monkeypatch.setattr(plane_check, "PlaneGateway", lambda: _gateway(FakePlane(status=401)))

    assert plane_check.main([]) == 1
    assert capsys.readouterr().err.strip() == "Plane kľúč kokpitu neuznal (401)."
