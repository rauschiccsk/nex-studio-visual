"""Plane's API as far as the cockpit uses it, in memory.

Shared by the tests of every door to Plane (DEV-117, DEV-121). DEV-218: other registers than DEV, each with whether
the cockpit's account is its member, and a ticket read by its identifier — as Plane answers (measured 24.10.2026: a
register the account is not a member of is 403, an identifier in small letters too, a missing ticket 404).
"""

from __future__ import annotations

import itertools
import json

import httpx

from backend.services.plane_gateway import PlaneGateway

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
            {"id": "l-zoltan", "name": "Zodpovedný: Zoltán"},
            {"id": "l-fix", "name": "Druh práce: Oprava (BUG)"},
            {"id": "l-gift", "name": "Druh práce: Dar (GIFT)"},
            {"id": "l-change", "name": "Druh práce: Požiadavka (CHR)"},
        ]
        self.modules: list[dict] = []
        self.issues: dict[str, dict] = {}
        self.module_issues: dict[str, list[str]] = {}
        self.comments: dict[str, list[dict]] = {}
        self._seq = itertools.count(1)
        #: Every register the account sees — ``is_member`` decides whether it may read its tickets.
        self.registers: list[dict] = [{"id": "p1", "identifier": "DEV", "is_member": True}]
        self.members: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status:
            return httpx.Response(self.status, json={"detail": "nope"})
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else {}
        if path == "/api/v1/users/me/":
            return httpx.Response(200, json={"email": "nex-studio@icc.sk"})
        if path == "/api/v1/workspaces/icc/projects/":
            return httpx.Response(200, json={"results": self.registers})
        if path == "/api/v1/workspaces/icc/members/":
            return httpx.Response(200, json=self.members)
        if path.startswith("/api/v1/workspaces/icc/issues/"):
            return self._by_identifier(path.removeprefix("/api/v1/workspaces/icc/issues/").strip("/"))
        for register in self.registers:
            other = f"/api/v1/workspaces/icc/projects/{register['id']}"
            if register["id"] != "p1" and path.startswith(other + "/"):
                if not register.get("is_member"):
                    return httpx.Response(403, json={"detail": "not a member"})
                path = P + path.removeprefix(other)
        if path == f"{P}/states/":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"id": "s-todo", "name": "Todo", "group": "unstarted"},
                        {"id": "s-ip", "name": "In Progress", "group": "started"},
                        {"id": "s-done", "name": "Done", "group": "completed"},
                        {"id": "s-cancelled", "name": "Cancelled", "group": "cancelled"},
                    ]
                },
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
                "updated_at": f"2026-10-15T08:{len(self.issues):02d}:00Z",
                "deleted_at": None,
                "archived_at": None,
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
            # Plane returns the whole tickets of the module (measured 15.10.2026), soft-deleted ones included.
            return httpx.Response(
                200, json={"results": [self.issues.get(i, {"id": i}) for i in self.module_issues.get(parts[1], [])]}
            )
        return httpx.Response(404, json={"detail": f"unknown {method} {path}"})

    def _by_identifier(self, identifier: str) -> httpx.Response:
        prefix, _, number = identifier.rpartition("-")
        register = next((r for r in self.registers if r["identifier"] == prefix), None)
        if register is None or not register.get("is_member"):
            return httpx.Response(403, json={"detail": "You do not have permission to perform this action."})
        for issue in self.issues.values():
            if issue.get("project", "p1") == register["id"] and str(issue.get("sequence_id")) == number:
                return httpx.Response(200, json=issue)
        return httpx.Response(404, json={"detail": "The requested resource does not exist."})


def gateway_for(plane: FakePlane, token: str = KEY) -> PlaneGateway:
    client = httpx.Client(transport=httpx.MockTransport(plane.handler))
    return PlaneGateway(
        base_url="http://plane.test", token=token, workspace="icc", project_identifier="DEV", client=client
    )
