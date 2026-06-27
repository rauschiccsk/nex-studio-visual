/**
 * AgentTerminal — xterm.js wrapper bound to a backend PTY-backed claude
 * CLI session over a WebSocket.
 *
 * The component owns:
 *
 * * the :class:`xterm.Terminal` instance + fit / weblinks / webgl addons
 * * the :class:`WebSocket` connection lifecycle (open → bidirectional
 *   pump → close)
 * * a ``ResizeObserver`` keeping the PTY cols/rows in sync with the
 *   parent container
 *
 * The parent page (``AgentTerminalPage``) is responsible for:
 *
 * * spawning the session via ``POST /agent-terminal/spawn``
 * * passing the resulting ``sessionId`` + the auth ``token`` down here
 * * choosing when to unmount this component (e.g. on Change project /
 *   End session)
 *
 * Wire protocol (matches ``backend/api/routes/agent_terminal.py``):
 *
 *   ←  ``{"type": "output", "data": "<utf-8 string>"}``
 *   ←  ``{"type": "end", "exit_code": int|null, "terminated_by": str}``
 *   ←  ``{"type": "write_rejected", "reason": str}``  (CR-V2-015 single-writer guard)
 *   →  ``{"type": "input", "data": "<utf-8 string>"}``
 *   →  ``{"type": "resize", "rows": int, "cols": int}``
 *
 * v2.0.0 (CR-V2-015/022): this raw-PTY xterm is the BREAK-GLASS debug path only — the first-class
 * Manažér↔AI-Agent channel is the AI Agent tab's event-rendered transcript + engine relay. When the engine
 * is driving the warm ``claude`` session, a raw keystroke here is refused (single-writer guard) and the
 * server sends a ``write_rejected`` frame; we surface it inline and steer the Manažér to the relay.
 */

import { useEffect, useRef, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { WebglAddon } from "@xterm/addon-webgl";
import "@xterm/xterm/css/xterm.css";

import { buildAgentTerminalWsUrl } from "@/services/api/agentTerminal";

export interface AgentTerminalProps {
  sessionId: string;
  token: string;
  /** Called when the WS connection closes (session ended or network drop). */
  onEnded?: (reason: string) => void;
}

export function AgentTerminal({ sessionId, token, onEnded }: AgentTerminalProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const [status, setStatus] = useState<"connecting" | "open" | "closed">("connecting");

  useEffect(() => {
    if (!containerRef.current) return;

    const term = new Terminal({
      fontFamily: '"JetBrains Mono", "Fira Code", "Cascadia Code", "Menlo", monospace',
      fontSize: 13,
      lineHeight: 1.2,
      cursorBlink: true,
      cursorStyle: "block",
      allowTransparency: false,
      scrollback: 10000,
      theme: {
        background: "#0f172a", // slate-900
        foreground: "#e2e8f0", // slate-200
        cursor: "#a3e635", // lime-400
        selectionBackground: "#475569", // slate-600
        black: "#0f172a",
        red: "#f87171",
        green: "#4ade80",
        yellow: "#fbbf24",
        blue: "#60a5fa",
        magenta: "#c084fc",
        cyan: "#22d3ee",
        white: "#e2e8f0",
        brightBlack: "#475569",
        brightRed: "#fca5a5",
        brightGreen: "#86efac",
        brightYellow: "#fcd34d",
        brightBlue: "#93c5fd",
        brightMagenta: "#d8b4fe",
        brightCyan: "#67e8f9",
        brightWhite: "#f1f5f9",
      },
    });

    const fit = new FitAddon();
    term.loadAddon(fit);
    term.loadAddon(new WebLinksAddon());

    term.open(containerRef.current);

    // WebGL addon is best-effort — Chrome/Firefox should accept it but
    // headless test environments may not. Fall back silently to canvas.
    try {
      const webgl = new WebglAddon();
      term.loadAddon(webgl);
    } catch {
      // canvas renderer kicks in automatically
    }

    fit.fit();
    termRef.current = term;
    fitRef.current = fit;

    const ws = new WebSocket(buildAgentTerminalWsUrl(sessionId, token));
    wsRef.current = ws;

    ws.onopen = () => {
      setStatus("open");
      // Sync initial PTY size to current rendered terminal dimensions.
      ws.send(
        JSON.stringify({
          type: "resize",
          rows: term.rows,
          cols: term.cols,
        }),
      );
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "output" && typeof msg.data === "string") {
          term.write(msg.data);
        } else if (msg.type === "end") {
          const reason: string = msg.terminated_by ?? "exited";
          term.write(`\r\n\x1b[33m[session ended: ${reason}]\x1b[0m\r\n`);
          onEnded?.(reason);
        } else if (msg.type === "write_rejected") {
          // CR-V2-015 single-writer guard: the engine is driving this session, so the raw keystroke was
          // dropped. Surface the design-mandated hint inline and point the Manažér at the engine relay.
          term.write(
            "\r\n\x1b[33m[engine práve pracuje — správa sa pošle po dokončení ťahu; použi vstupné pole AI Agenta (relay)]\x1b[0m\r\n",
          );
        }
      } catch {
        // Malformed frame — ignore.
      }
    };

    ws.onclose = () => {
      setStatus("closed");
    };

    ws.onerror = () => {
      term.write("\r\n\x1b[31m[connection error]\x1b[0m\r\n");
    };

    // Pipe user keystrokes → server.
    const dataDisposer = term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "input", data }));
      }
    });

    // Pipe terminal resize → server.
    const resizeDisposer = term.onResize(({ cols, rows }) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "resize", rows, cols }));
      }
    });

    // Parent-container size changes (window resize, sidebar toggle) → fit
    // the terminal grid, which triggers ``onResize`` above.
    const ro = new ResizeObserver(() => {
      try {
        fit.fit();
      } catch {
        // Container may be detached briefly during route transitions.
      }
    });
    ro.observe(containerRef.current);

    term.focus();

    return () => {
      ro.disconnect();
      dataDisposer.dispose();
      resizeDisposer.dispose();
      try {
        ws.close();
      } catch {
        // already closed
      }
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
      wsRef.current = null;
    };
    // Only re-create on session/token change — typical use case is
    // mount-once-per-session (parent unmounts on End / Change project).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, token]);

  return (
    <div className="relative h-full w-full bg-[var(--color-canvas)]">
      <div ref={containerRef} className="absolute inset-0" />
      {status === "connecting" && (
        <div className="pointer-events-none absolute right-3 top-3 rounded bg-[var(--color-surface-hover)] px-2 py-1 text-[10px] text-[var(--color-text-secondary)]">
          Connecting…
        </div>
      )}
    </div>
  );
}
