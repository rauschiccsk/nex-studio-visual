"""Server MCP Poradcu na unixovom sockete jednej otázky (ICCINT-167, návrh §4.2).

Hovorí podmnožinou protokolu MCP, ktorú Claude Code pri nástrojoch používa — ``initialize``,
``notifications/initialized``, ``ping``, ``tools/list``, ``tools/call`` — správami JSON-RPC oddelenými
novým riadkom (prenos stdio; prostredník :mod:`.shim` ich len prenáša). Verziu protokolu vracia tú, ktorú
poslal klient: táto podmnožina sa medzi verziami nemení a vlastná pevná verzia by sa raz rozišla s Claude
Code bez toho, aby to niekto zmenil.

Každý výstup nástroja prejde filtrom tajomstiev a stropom dĺžky skôr, než opustí backend; chyba nástroja
sa vráti ako ``isError`` s vetou pre agenta, nie ako pád spojenia. O každom volaní sa ohlási KROK
(nástroj a cieľ) — nikdy obsah.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from backend.services.poradca.secrets_filter import SecretFilter

logger = logging.getLogger(__name__)

#: Najdlhší výstup jedného nástroja, ktorý dostane agent — dlhší sa skráti s poznámkou.
MAX_OUTPUT_CHARS = 60_000

_LINE_LIMIT = 4 * 1024 * 1024


class ToolError(Exception):
    """Chyba, ktorú agent dostane ako výsledok nástroja (``isError``) — veta, nie výpis zásobníka."""


@dataclass(frozen=True)
class Tool:
    """Jeden nástroj Poradcu."""

    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], Awaitable[str]]
    #: Cieľ pre priebeh („backend/x.py", „uat · backend") — z argumentov, nikdy z výsledku.
    target: Callable[[dict], str]


StepCallback = Callable[[str, str], Awaitable[None]]


class McpServer:
    """Server jednej otázky. ``start`` začne počúvať, ``close`` zruší socket aj otvorené spojenia."""

    def __init__(
        self,
        socket_path: str,
        tools: list[Tool],
        secret_filter: SecretFilter,
        on_step: Optional[StepCallback] = None,
    ) -> None:
        self._socket_path = socket_path
        self._tools = {t.name: t for t in tools}
        self._filter = secret_filter
        self._on_step = on_step
        self._server: Optional[asyncio.base_events.Server] = None

    async def start(self) -> None:
        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)
        self._server = await asyncio.start_unix_server(self._handle, path=self._socket_path, limit=_LINE_LIMIT)
        # Kontajner beží ako 1000 a socket vytvoril backend (root); priečinok socketu vidí len táto otázka.
        os.chmod(self._socket_path, 0o666)

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), timeout=5)
            except asyncio.TimeoutError:
                pass
            self._server = None
        try:
            os.unlink(self._socket_path)
        except OSError:
            pass

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    await self._send(writer, _error(None, -32700, "parse error"))
                    continue
                reply = await self.dispatch(message)
                if reply is not None:
                    await self._send(writer, reply)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    @staticmethod
    async def _send(writer: asyncio.StreamWriter, message: dict) -> None:
        writer.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
        await writer.drain()

    async def dispatch(self, message: Any) -> Optional[dict]:
        """Odpoveď na jednu správu JSON-RPC; ``None`` pre oznámenie (bez ``id``)."""
        if not isinstance(message, dict):
            return _error(None, -32600, "invalid request")
        method = message.get("method")
        msg_id = message.get("id")
        params = message.get("params") or {}
        if msg_id is None:
            return None  # oznámenie (napr. notifications/initialized) — bez odpovede
        if method == "initialize":
            return _result(
                msg_id,
                {
                    "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "poradca", "version": "1"},
                },
            )
        if method == "ping":
            return _result(msg_id, {})
        if method == "tools/list":
            return _result(
                msg_id,
                {
                    "tools": [
                        {"name": t.name, "description": t.description, "inputSchema": t.input_schema}
                        for t in self._tools.values()
                    ]
                },
            )
        if method == "tools/call":
            return _result(msg_id, await self._call(params.get("name"), params.get("arguments") or {}))
        return _error(msg_id, -32601, f"method not found: {method}")

    async def _call(self, name: Any, arguments: Any) -> dict:
        tool = self._tools.get(name) if isinstance(name, str) else None
        if tool is None:
            return _tool_text(f"Nástroj {name!r} neexistuje.", is_error=True)
        if not isinstance(arguments, dict):
            return _tool_text("Argumenty nástroja musia byť objekt.", is_error=True)
        if self._on_step is not None:
            try:
                target = tool.target(arguments)
            except Exception:  # noqa: BLE001 — priebeh nesmie zhodiť nástroj
                target = ""
            try:
                await self._on_step(tool.name, self._filter(target)[:200])
            except Exception:  # noqa: BLE001
                logger.exception("poradca: on_step failed")
        try:
            output = await tool.handler(arguments)
            is_error = False
        except ToolError as exc:
            output, is_error = str(exc), True
        except Exception:  # noqa: BLE001 — chyba nástroja je výsledok pre agenta, nie pád otázky
            logger.exception("poradca: tool %s failed", tool.name)
            output, is_error = f"Nástroj {tool.name} zlyhal na strane kokpitu.", True
        output = self._filter(output or "")
        if len(output) > MAX_OUTPUT_CHARS:
            output = output[:MAX_OUTPUT_CHARS] + f"\n… (skrátené na {MAX_OUTPUT_CHARS} znakov)"
        return _tool_text(output, is_error=is_error)


def _tool_text(text: str, *, is_error: bool) -> dict:
    out: dict = {"content": [{"type": "text", "text": text}]}
    if is_error:
        out["isError"] = True
    return out


def _result(msg_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}
