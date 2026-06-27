/**
 * PersistentTerminalsLayer — mounts the persistent **break-glass** claude CLI
 * ``<AgentTerminal/>`` (raw xterm PTY) ABOVE :file:`pages/AgentTerminalPage.tsx`
 * so its WebSocket + xterm.js stay alive when React Router navigates to/from
 * ``/ai-agent`` (CR-NS-004; narrowed to the single terminal in CR-NS-039;
 * route + slot re-keyed ``/coordinator``→``/ai-agent`` / ``coordinator``→
 * ``ai-agent`` in CR-V2-022, OQ-7).
 *
 * v2.0.0 (CR-V2-015/022): the AI Agent tab's PRIMARY surface is the
 * event-rendered transcript + relay rendered by the page itself — NOT this raw
 * byte stream. This layer keeps the raw xterm alive only for the break-glass
 * debug PTY, mounted at z-0 beneath the page and revealed when the page asks for
 * it; the page header chrome + transcript sit on top.
 *
 * Visibility is a pure CSS switch — the active route's slot is
 * ``display: block``; an inactive slot is ``display: none`` (still in the
 * DOM, WS still pumping, scrollback preserved).
 *
 * Lifecycle:
 *
 *   * Mount → if Director and store not yet ``initialized`` → ``refresh()``
 *   * Auth change to ``null`` → ``reset()`` (terminals unmount cleanly)
 *   * Lazy spawn — the visited set tracks roles the user has actually
 *     navigated to; a slot whose session exists in the backend but the
 *     user has not opened yet stays unmounted (no idle WS).
 *
 * Layout — the layer is mounted as a sibling of ``<Outlet/>`` inside
 * :file:`components/layout/AppLayout.tsx`'s ``<main>`` which is set to
 * ``relative``. Each slot is ``absolute inset-0 z-0`` and is overlaid by
 * :file:`pages/AgentTerminalPage.tsx`'s opaque header chrome
 * (``relative z-10`` + ``bg-[var(--color-surface)]``) so the page header stays visible
 * on top of the terminal viewport without any offset math or portals.
 */

import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";

import { AgentTerminal } from "@/components/AgentTerminal";
import { TOKEN_STORAGE_KEY } from "@/services/api";
import type { AgentRole } from "@/services/api/agentTerminal";
import {
  useAgentTerminalStore,
  type SlotState,
} from "@/store/agentTerminalStore";
import { useAuthStore } from "@/store/authStore";

function matchActiveRole(pathname: string): AgentRole | null {
  // E3(a) (CR-NS-039): the AI Agent is the only interactive (break-glass) terminal route.
  // CR-V2-019 (OQ-7): the route was renamed /coordinator → /ai-agent; the old path now redirects there
  // (App.tsx). CR-V2-022 re-keyed the store AgentRole to "ai-agent" (the backend wire/charter slug). Match
  // the live /ai-agent path so the persistent break-glass terminal (WS + xterm scrollback) survives.
  if (pathname === "/ai-agent") return "ai-agent";
  return null;
}

export function PersistentTerminalsLayer() {
  const location = useLocation();
  const user = useAuthStore((s) => s.user);

  const aiAgent = useAgentTerminalStore((s) => s["ai-agent"]);
  const initialized = useAgentTerminalStore((s) => s.initialized);
  const breakGlassOpen = useAgentTerminalStore((s) => s.breakGlassOpen);
  const refresh = useAgentTerminalStore((s) => s.refresh);
  const reset = useAgentTerminalStore((s) => s.reset);

  const isDirector = user?.role === "ri";

  // Init + teardown driven by auth. The store rebuilds itself from the
  // backend after every login; ``reset()`` clears it on logout so a
  // subsequent login starts fresh.
  useEffect(() => {
    if (isDirector && !initialized) void refresh();
    if (!user) reset();
  }, [isDirector, user, initialized, refresh, reset]);

  // Lazy spawn — slot's WS only attaches after the first time the user
  // has actually navigated to that role's route. A session sitting in
  // the BE for an unvisited role stays detached.
  const [visited, setVisited] = useState<Set<AgentRole>>(new Set());
  useEffect(() => {
    const role = matchActiveRole(location.pathname);
    if (role && !visited.has(role)) {
      setVisited(new Set([...visited, role]));
    }
  }, [location.pathname, visited]);

  const token =
    typeof window !== "undefined"
      ? window.localStorage.getItem(TOKEN_STORAGE_KEY)
      : null;

  if (!isDirector || !token) return null;

  const activeRole = matchActiveRole(location.pathname);
  const entries: Array<[AgentRole, SlotState]> = [["ai-agent", aiAgent]];

  return (
    <>
      {entries.map(([role, slot]) => {
        if (!slot.session) return null;
        if (!visited.has(role)) return null;
        // v2 (CR-V2-022): the raw xterm reveals only on the AI Agent route AND when the Manažér opted into
        // the break-glass console; otherwise it stays mounted (WS pumping, scrollback alive) but hidden
        // behind the page's event-rendered transcript.
        const visible = activeRole === role && breakGlassOpen;
        return (
          <div
            key={role}
            className="absolute inset-0"
            style={{ display: visible ? "block" : "none", zIndex: 0 }}
          >
            <AgentTerminal
              key={slot.session.id}
              sessionId={slot.session.id}
              token={token}
              onEnded={() => void refresh()}
            />
          </div>
        );
      })}
    </>
  );
}
