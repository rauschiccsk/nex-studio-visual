"""DEV-511 — a project founded from an idea gets the documents its brief lists from the internet without anyone
downloading them; the Manažér sees what came and what did not, and tries again with one click.

25.11.2026 the brief of NEX DualBook told the Manažér to download ten documents into ``docs/reference/`` by hand.
Director: „…mám ich vložiť ručne čo ja robiť nebudem.“ — on the proposal: „Súhlasím.“
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes import poradca as poradca_routes
from backend.api.routes import projects as projects_routes
from backend.core.security import get_current_user
from backend.db.session import get_db
from backend.services import reference_docs as R
from tests.test_ideas_end_in_a_project import PAID_BRIEF, _idea_with, _new_project, world  # noqa: F401
from tests.test_ideas_talk_without_a_project import answers  # noqa: F401

XSD = b'<?xml version="1.0"?>\n<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"/>\n'
LISTING = (
    "<podklady>\n"
    "docs/reference/dph/dph2025.xsd | https://ekr.financnasprava.sk/a/dph2025.xsd | Schéma XSD priznania k DPH\n"
    "docs/reference/banka/prirucka.pdf | https://www.unicreditbank.sk/b/prirucka.pdf | Príručka banky\n"
    "</podklady>\n"
)
LISTED_BRIEF = PAID_BRIEF.replace("</zadanie-projektu>", f"{LISTING}</zadanie-projektu>")


def _client(db, user) -> TestClient:
    """The cockpit as far as this needs it: the idea's routes and the project's."""
    app = FastAPI()
    app.include_router(poradca_routes.router, prefix="/api/v1/poradca")
    app.include_router(projects_routes.router, prefix="/api/v1/projects")

    def _db():
        yield db

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


@pytest.fixture
def web(monkeypatch):
    pages: dict[str, httpx.Response] = {
        "https://194.1.4.55/a/dph2025.xsd": httpx.Response(200, content=XSD),
        "https://193.85.205.10/b/prirucka.pdf": httpx.Response(403, content=b"<html>Request Failed</html>"),
    }
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.headers["host"])
        return pages.get(str(request.url), httpx.Response(404))

    names = {"ekr.financnasprava.sk": ["194.1.4.55"], "www.unicreditbank.sk": ["193.85.205.10"]}
    monkeypatch.setattr(R, "_addresses", lambda host: names[host])
    monkeypatch.setattr(R, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    return pages, asked


def _founded(world, brief: str):  # noqa: F811
    db = world["db"]
    idea, (answer,) = _idea_with(world, brief)
    project = _new_project(db, world["me"])
    project.source_path = str(world["root"] / project.slug)
    (world["root"] / project.slug).mkdir(parents=True, exist_ok=True)
    db.flush()
    client = _client(db, world["me"])
    founded = client.post(
        f"/api/v1/poradca/ideas/{idea['id']}/founded", json={"message_id": answer, "project_id": str(project.id)}
    )
    assert founded.status_code == 200, founded.text
    return client, project, idea, answer


def test_the_founding_downloads_what_the_brief_lists_into_the_project(world, web):  # noqa: F811
    _pages, asked = web

    client, project, idea, answer = _founded(world, LISTED_BRIEF)

    root = world["root"] / project.slug
    assert (root / "docs/reference/dph/dph2025.xsd").read_bytes() == XSD
    shown = client.get(f"/api/v1/projects/{project.id}/references").json()
    assert shown == {
        "running": False,
        "items": [
            {
                "path": "docs/reference/dph/dph2025.xsd",
                "url": "https://ekr.financnasprava.sk/a/dph2025.xsd",
                "description": "Schéma XSD priznania k DPH",
                "ok": True,
                "said": None,
                "size": len(XSD),
                "fetched_at": shown["items"][0]["fetched_at"],
            },
            {
                "path": "docs/reference/banka/prirucka.pdf",
                "url": "https://www.unicreditbank.sk/b/prirucka.pdf",
                "description": "Príručka banky",
                "ok": False,
                "said": "web odpovedal 403",
                "size": None,
                "fetched_at": None,
            },
        ],
    }
    assert shown["items"][0]["fetched_at"]
    again = client.post(
        f"/api/v1/poradca/ideas/{idea['id']}/founded", json={"message_id": answer, "project_id": str(project.id)}
    )
    assert again.status_code == 200 and asked == ["ekr.financnasprava.sk", "www.unicreditbank.sk"]


def test_download_again_takes_only_what_did_not_come(world, web):  # noqa: F811
    pages, asked = web
    client, project, _idea, _answer = _founded(world, LISTED_BRIEF)
    pages["https://193.85.205.10/b/prirucka.pdf"] = httpx.Response(200, content=b"%PDF-1.7\n")
    asked.clear()

    retried = client.post(f"/api/v1/projects/{project.id}/references/retry")

    assert retried.status_code == 200, retried.text
    assert retried.json()["running"] is True
    assert asked == ["www.unicreditbank.sk"]
    shown = client.get(f"/api/v1/projects/{project.id}/references").json()
    assert [(one["path"], one["ok"]) for one in shown["items"]] == [
        ("docs/reference/dph/dph2025.xsd", True),
        ("docs/reference/banka/prirucka.pdf", True),
    ]
    refused = client.post(f"/api/v1/projects/{project.id}/references/retry")
    assert (refused.status_code, refused.json()["detail"]) == (409, "Všetky podklady sú stiahnuté.")


def test_a_brief_without_the_list_downloads_nothing(world, web):  # noqa: F811
    _pages, asked = web

    client, project, _idea, _answer = _founded(world, PAID_BRIEF)

    assert asked == []
    assert client.get(f"/api/v1/projects/{project.id}/references").json() == {"running": False, "items": []}
