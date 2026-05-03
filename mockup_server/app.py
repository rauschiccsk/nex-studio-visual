"""NEX Studio Mockup Server.

A dedicated HTTP service that makes each project's live UI mockup
reachable at its own port — ``http://localhost:{Project.ui_design_port}``
— so designers / stakeholders can open the draft in a real browser
tab and share the URL.

Each listener serves the latest persisted
``ui_designs.html_preview`` for the matching project. The backend
posts to this service's ``/admin/reload/{project_id}`` endpoint
(running on :data:`ADMIN_PORT`) after every ``html_preview`` write
so the next ``GET /`` on the project's port reflects the change
without restart.

Runs in its own container with ``network_mode: host`` so the
per-project ports land directly on the host — binding the same
range in bridged mode would require ``10100-14999:10100-14999`` in
compose which is heavy and brittle as new projects are added.

Intentionally uses raw ``pg8000`` + SQL rather than SQLAlchemy ORM
so the image stays lightweight (no pydantic / alembic / FastAPI
dependency chain) and lifecycle-independent from the backend.
"""

from __future__ import annotations

import logging
import os
from typing import AsyncIterator
from uuid import UUID

import pg8000.dbapi
from aiohttp import web

logger = logging.getLogger("mockup_server")

ADMIN_PORT = int(os.environ.get("MOCKUP_ADMIN_PORT", "9190"))
DATABASE_URL = os.environ["DATABASE_URL"]

# One listener per project, keyed by project_id for fast reload.
_listeners: dict[UUID, "ProjectListener"] = {}


# ─── DB helpers (raw pg8000 — ORM is overkill for two SELECTs) ──────


def _parse_dsn(url: str) -> dict[str, object]:
    """Translate a ``postgresql+pg8000://user:pass@host:port/db`` URL into
    ``pg8000.dbapi.connect`` kwargs. The ``+driver`` suffix SQLAlchemy
    needs is stripped before parsing.
    """
    from urllib.parse import urlparse

    plain = url.replace("postgresql+pg8000://", "postgresql://", 1)
    parsed = urlparse(plain)
    if parsed.scheme != "postgresql":
        raise ValueError(f"Unsupported DSN scheme: {parsed.scheme!r}")
    return {
        "user": parsed.username or "",
        "password": parsed.password or "",
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "database": (parsed.path or "/").lstrip("/"),
    }


_DSN_KW = _parse_dsn(DATABASE_URL)


def _fetch_all_projects_with_port() -> list[tuple[UUID, int]]:
    conn = pg8000.dbapi.connect(**_DSN_KW)
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, ui_design_port FROM projects WHERE ui_design_port IS NOT NULL")
        rows = [(UUID(str(pid)), int(port)) for pid, port in cur.fetchall()]
        return rows
    finally:
        conn.close()


def _fetch_project_port(project_id: UUID) -> int | None:
    conn = pg8000.dbapi.connect(**_DSN_KW)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT ui_design_port FROM projects WHERE id = %s",
            (str(project_id),),
        )
        row = cur.fetchone()
        if row is None or row[0] is None:
            return None
        return int(row[0])
    finally:
        conn.close()


def _fetch_html_preview(project_id: UUID) -> str | None:
    conn = pg8000.dbapi.connect(**_DSN_KW)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT html_preview FROM ui_designs WHERE project_id = %s ORDER BY created_at DESC LIMIT 1",
            (str(project_id),),
        )
        row = cur.fetchone()
        if row is None or not row[0]:
            return None
        return str(row[0])
    finally:
        conn.close()


# ─── Per-project listener ────────────────────────────────────────────


class ProjectListener:
    """Bound HTTP listener on a project's ``ui_design_port``."""

    def __init__(self, project_id: UUID, port: int) -> None:
        self.project_id = project_id
        self.port = port
        self._html: str = _default_placeholder(project_id)
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    async def start(self) -> None:
        self._load_html()
        app = web.Application()
        app.router.add_get("/", self._handle_root)
        # Sink every other path to the same handler so the mockup can
        # link internally without 404 noise in the logs.
        app.router.add_get("/{tail:.*}", self._handle_root)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host="0.0.0.0", port=self.port)
        await self._site.start()
        logger.info(
            "listener started project=%s port=%s bytes=%s",
            self.project_id,
            self.port,
            len(self._html),
        )

    async def stop(self) -> None:
        if self._site is not None:
            await self._site.stop()
            self._site = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    def reload(self) -> None:
        self._load_html()
        logger.info("listener reloaded project=%s bytes=%s", self.project_id, len(self._html))

    def _load_html(self) -> None:
        html = _fetch_html_preview(self.project_id)
        self._html = html if html else _default_placeholder(self.project_id)

    async def _handle_root(self, _request: web.Request) -> web.Response:
        return web.Response(text=self._html, content_type="text/html", charset="utf-8")


def _default_placeholder(project_id: UUID) -> str:
    return (
        "<!DOCTYPE html><html lang='sk'><head><meta charset='utf-8'>"
        "<title>NEX Studio Mockup — zatiaľ prázdne</title>"
        "<style>"
        "body{margin:0;height:100vh;display:flex;align-items:center;"
        "justify-content:center;font-family:system-ui,sans-serif;"
        "background:#0f172a;color:#94a3b8}"
        ".card{border:1px solid #1e293b;border-radius:14px;padding:32px 40px;"
        "text-align:center;max-width:560px}"
        "h1{margin:0 0 8px;color:#e2e8f0;font-size:18px}"
        "code{background:#1e293b;color:#a5b4fc;padding:2px 6px;border-radius:4px}"
        "</style></head><body><div class='card'>"
        "<h1>UI Design ešte nie je vygenerovaný</h1>"
        f"<p>Projekt <code>{project_id}</code> zatiaľ nemá mockup. "
        "V NEX Studio otvor <strong>Krok 2B — UI Design</strong> a klikni "
        "<em>Generovať mockup</em>.</p></div></body></html>"
    )


# ─── Admin channel ───────────────────────────────────────────────────


async def _handle_admin_reload(request: web.Request) -> web.Response:
    project_id_str = request.match_info["project_id"]
    try:
        project_id = UUID(project_id_str)
    except ValueError:
        return web.json_response({"error": "invalid UUID"}, status=400)

    listener = _listeners.get(project_id)
    if listener is None:
        if not await _ensure_listener(project_id):
            return web.json_response({"error": "no ui_design_port assigned to project"}, status=404)
        listener = _listeners[project_id]

    listener.reload()
    return web.json_response({"status": "reloaded", "project_id": str(project_id)})


async def _handle_admin_health(_request: web.Request) -> web.Response:
    return web.json_response(
        {
            "status": "ok",
            "listeners": [{"project_id": str(pid), "port": lst.port} for pid, lst in _listeners.items()],
        }
    )


async def _ensure_listener(project_id: UUID) -> bool:
    if project_id in _listeners:
        return True
    port = _fetch_project_port(project_id)
    if port is None:
        return False
    listener = ProjectListener(project_id, port)
    try:
        await listener.start()
    except OSError as exc:
        logger.error("cannot bind project=%s port=%s: %s", project_id, port, exc)
        return False
    _listeners[project_id] = listener
    return True


async def _bootstrap_listeners() -> None:
    for project_id, port in _fetch_all_projects_with_port():
        try:
            listener = ProjectListener(project_id, port)
            await listener.start()
            _listeners[project_id] = listener
        except OSError as exc:
            logger.error(
                "cannot bind project=%s port=%s on startup: %s",
                project_id,
                port,
                exc,
            )


# ─── aiohttp application factory ─────────────────────────────────────


async def _lifespan(_app: web.Application) -> AsyncIterator[None]:
    """aiohttp ``cleanup_ctx`` hook — bootstrap listeners on start,
    tear them down on shutdown. Spelled as a plain async generator
    because ``cleanup_ctx`` consumes the protocol directly rather
    than calling ``__aenter__``/``__aexit__``.
    """
    await _bootstrap_listeners()
    try:
        yield
    finally:
        for lst in _listeners.values():
            await lst.stop()
        _listeners.clear()


def build_admin_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/admin/health", _handle_admin_health)
    app.router.add_post("/admin/reload/{project_id}", _handle_admin_reload)
    app.cleanup_ctx.append(_lifespan)
    return app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = build_admin_app()
    logger.info("mockup server starting — admin port=%s", ADMIN_PORT)
    web.run_app(app, host="0.0.0.0", port=ADMIN_PORT, access_log=None)


if __name__ == "__main__":
    main()
