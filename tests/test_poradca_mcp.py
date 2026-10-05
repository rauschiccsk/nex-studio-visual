"""Server MCP Poradcu a prostredník (ICCINT-167, návrh §4.2).

Skúša skutočnú cestu: prostredník :mod:`shim` ako samostatný proces (tak, ako ho spustí Claude Code
v kontajneri) ↔ unixový socket ↔ :class:`McpServer`. Hodnoty tajomstiev sú umelé.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from backend.services.poradca import mcp_server
from backend.services.poradca.mcp_server import McpServer, Tool, ToolError
from backend.services.poradca.secrets_filter import REDACTED, SecretFilter

SHIM = Path(__file__).resolve().parents[1] / "backend" / "services" / "poradca" / "shim.py"
FAKE = "fake-known-secret-0042"


async def _echo(args: dict) -> str:
    return f"stavba beží; heslo {FAKE}; password=fakepw123; poznámka {args.get('x', '')}"


async def _boom(args: dict) -> str:
    raise ToolError("Inštalácia UAT pre tento projekt neexistuje.")


async def _crash(args: dict) -> str:
    raise RuntimeError("internal detail /opt/customers/secret/path")


def _tools() -> list[Tool]:
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    return [
        Tool("stavba", "Stav stavby.", schema, _echo, lambda a: f"verzia {a.get('x', '')}"),
        Tool("zly", "Vždy chyba.", schema, _boom, lambda a: ""),
        Tool("pad", "Spadne.", schema, _crash, lambda a: ""),
    ]


async def _session(tmp_path: Path, requests: list[dict]) -> tuple[list[dict], list[tuple[str, str]]]:
    sock = str(tmp_path / "mcp.sock")
    steps: list[tuple[str, str]] = []

    async def on_step(tool: str, target: str) -> None:
        steps.append((tool, target))

    server = McpServer(sock, _tools(), SecretFilter([FAKE]), on_step)
    await server.start()
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(SHIM),
            sock,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        payload = "".join(json.dumps(r) + "\n" for r in requests).encode()
        expected = sum(1 for r in requests if "id" in r)
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(payload)
        await proc.stdin.drain()
        replies = []
        for _ in range(expected):
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=10)
            replies.append(json.loads(line))
        proc.stdin.close()
        await asyncio.wait_for(proc.wait(), timeout=10)
    finally:
        await server.close()
    return replies, steps


def _req(i: int, method: str, params: dict | None = None) -> dict:
    return {"jsonrpc": "2.0", "id": i, "method": method, "params": params or {}}


@pytest.mark.asyncio
async def test_handshake_list_and_call_through_the_shim(tmp_path):
    replies, steps = await _session(
        tmp_path,
        [
            _req(1, "initialize", {"protocolVersion": "2099-01-01", "capabilities": {}}),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            _req(2, "tools/list"),
            _req(3, "tools/call", {"name": "stavba", "arguments": {"x": "1.2.0"}}),
        ],
    )
    init, listed, called = replies
    # Verziu protokolu vráti tú, ktorú poslal klient.
    assert init["result"]["protocolVersion"] == "2099-01-01"
    assert [t["name"] for t in listed["result"]["tools"]] == ["stavba", "zly", "pad"]
    text = called["result"]["content"][0]["text"]
    assert "stavba beží" in text and "1.2.0" in text
    # Výstup prešiel filtrom — známa hodnota aj tvar.
    assert FAKE not in text and "fakepw123" not in text and REDACTED in text
    assert "isError" not in called["result"]
    # Krok nesie nástroj a cieľ, nie obsah.
    assert steps == [("stavba", "verzia 1.2.0")]


@pytest.mark.asyncio
async def test_tool_error_and_crash_come_back_as_results_not_as_a_dead_connection(tmp_path):
    replies, _ = await _session(
        tmp_path,
        [
            _req(1, "tools/call", {"name": "zly", "arguments": {}}),
            _req(2, "tools/call", {"name": "pad", "arguments": {}}),
            _req(3, "tools/call", {"name": "neexistuje", "arguments": {}}),
            _req(4, "ping"),
        ],
    )
    err, crash, missing, ping = replies
    assert err["result"]["isError"] is True
    assert "neexistuje" in err["result"]["content"][0]["text"]
    # Vnútorný detail pádu sa agentovi neukáže.
    assert crash["result"]["isError"] is True
    assert "/opt/customers" not in crash["result"]["content"][0]["text"]
    assert missing["result"]["isError"] is True
    assert ping["result"] == {}


@pytest.mark.asyncio
async def test_long_output_is_cut(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_server, "MAX_OUTPUT_CHARS", 50)
    replies, _ = await _session(tmp_path, [_req(1, "tools/call", {"name": "stavba", "arguments": {"x": "y" * 500}})])
    text = replies[0]["result"]["content"][0]["text"]
    assert len(text) < 120 and "skrátené" in text


@pytest.mark.asyncio
async def test_unknown_method_is_a_json_rpc_error(tmp_path):
    replies, _ = await _session(tmp_path, [_req(1, "resources/list")])
    assert replies[0]["error"]["code"] == -32601
