"""Pipeline WebSocket connection registry + presence (F-007 §6/§9, CR-NS-018 Phase 3).

A process-global, in-memory registry of live board connections per version. The
``POST /pipeline/{version_id}/action`` handler broadcasts ``state_changed`` /
``message_added`` to all sockets of that version; the same registry is the §9
**presence signal** — Phase 5 reads ``present_director_ids`` to decide whether a
Manažér needs a Telegram nudge (only when they have no live board socket). (The
method names keep the ``director`` slug — operator/status-VALUE relabel only,
CR-V2-004; the engine-consumer re-wire is owned by CR-V2-009.)

Single backend process is assumed (NEX Studio runs one); a multi-worker
deployment would need an external pub/sub — explicitly out of scope.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import WebSocket

logger = logging.getLogger(__name__)


@dataclass
class _Conn:
    """One live board connection: the user + their current ``away`` annotation (E6, CR-NS-038).

    ``away`` is the Manažér's explicit "stepped away from the computer" toggle — set via an inbound
    WS presence message; it does NOT affect presence/broadcast, only whether the Telegram nudge gate
    treats this connection as active."""

    user_id: UUID
    away: bool = False


class PipelineWsRegistry:
    """Tracks board connections per ``version_id`` — each keyed by its socket, carrying an ``away`` flag."""

    def __init__(self) -> None:
        self._conns: dict[UUID, dict[WebSocket, _Conn]] = defaultdict(dict)
        self._lock = asyncio.Lock()

    async def connect(self, version_id: UUID, ws: WebSocket, user_id: UUID) -> None:
        async with self._lock:
            self._conns[version_id][ws] = _Conn(user_id=user_id)

    async def disconnect(self, version_id: UUID, ws: WebSocket) -> None:
        async with self._lock:
            conns = self._conns.get(version_id)
            if not conns:
                return
            conns.pop(ws, None)
            if not conns:
                self._conns.pop(version_id, None)

    async def set_away(self, version_id: UUID, ws: WebSocket, away: bool) -> None:
        """Update one connection's ``away`` annotation (E6, CR-NS-038). No-op if the socket is gone."""
        async with self._lock:
            conn = self._conns.get(version_id, {}).get(ws)
            if conn is not None:
                conn.away = bool(away)

    async def broadcast(self, version_id: UUID, event: dict[str, Any]) -> None:
        """Send ``event`` (JSON) to every socket of ``version_id``.

        Never raises — a failing socket is pruned, not propagated.
        """
        async with self._lock:
            targets = list(self._conns.get(version_id, {}).keys())
        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_json(event)
            except Exception:  # noqa: BLE001 — socket may be closed mid-broadcast
                dead.append(ws)
        for ws in dead:
            await self.disconnect(version_id, ws)

    # ── presence reads ──────────────────────────────────────────────────────────────────────────
    # Both reads are SYNC and lock-free BY DESIGN (CR-NS-038 review). Single-process asyncio: a sync
    # method runs to completion without an await point, so the event loop cannot interleave the
    # lock-holding mutators (connect/disconnect/set_away) mid-iteration — the read sees a consistent
    # snapshot of `_conns` and each `_Conn.away`. INVARIANT: if either is ever made async (e.g. to add
    # `await self._lock`), it MUST then hold the lock — an async read could otherwise interleave a
    # `_conns` structural mutation and hit "dict changed size during iteration".
    def present_director_ids(self, version_id: UUID) -> set[UUID]:
        """User ids with a live board socket for ``version_id`` (§9 raw presence read)."""
        return {c.user_id for c in self._conns.get(version_id, {}).values()}

    def active_director_ids(self, version_id: UUID) -> set[UUID]:
        """User ids with ≥1 **non-away** live board socket (E6, CR-NS-038) — the presence read the
        Telegram-nudge gate uses, so an away Manažér (board open but stepped away) is still pinged."""
        return {c.user_id for c in self._conns.get(version_id, {}).values() if not c.away}

    def away_director_ids(self, version_id: UUID) -> set[UUID]:
        """User ids with ≥1 live board socket explicitly toggled **away** (E6, CR-NS-038) — the
        Manažér(s) who stepped away from an OPEN board and therefore want the out-of-band Telegram
        nudge sent to their OWN chat (Class J fix). Same lock-free single-snapshot read as the siblings."""
        return {c.user_id for c in self._conns.get(version_id, {}).values() if c.away}


#: Process-global registry shared by the route handlers + the WS endpoint.
registry = PipelineWsRegistry()
