"""MCP shim in the Poradca container: Claude Code stdio ↔ the backend's unix socket (ICCINT-167).

Claude Code can launch MCP servers only via stdio, SSE or HTTP; the container network is fenced off and the backend
must not be reachable from it. So the backend listens on a unix socket valid for one question and this
script only carries bytes in both directions — it understands nothing, decides nothing and adds nothing. All the logic
(which tools, what they may do, the secrets filter) is in the backend, where it cannot be changed from the container.

Standard library only: it runs in the backend image as user 1000, without a virtual environment.
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
