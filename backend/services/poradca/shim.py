"""Prostredník MCP v kontajneri Poradcu: stdio Claude Code ↔ unixový socket backendu (ICCINT-167).

Claude Code vie servery MCP spúšťať len cez stdio, SSE alebo HTTP; sieť kontajnera je oplotená a backend
nemá byť z nej dosiahnuteľný. Preto backend počúva na unixovom sockete platnom pre jednu otázku a tento
skript len prenáša bajty oboma smermi — nerozumie im, nič nerozhoduje a nič nepridáva. Všetka logika
(aké nástroje, čo smú, filter tajomstiev) je v backende, kde sa nedá zmeniť z kontajnera.

Iba štandardná knižnica: beží v obraze backendu ako používateľ 1000, bez virtuálneho prostredia.
"""

from __future__ import annotations

import socket
import sys
import threading


def _pump_stdin(sock: socket.socket) -> None:
    try:
        while True:
            chunk = sys.stdin.buffer.read1(65536)
            if not chunk:
                break
            sock.sendall(chunk)
    except OSError:
        pass
    finally:
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: shim.py <socket>\n")
        return 2
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(argv[1])
    except OSError as exc:
        sys.stderr.write(f"poradca shim: cannot connect to {argv[1]}: {exc}\n")
        return 1
    threading.Thread(target=_pump_stdin, args=(sock,), daemon=True).start()
    out = sys.stdout.buffer
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        out.write(chunk)
        out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
