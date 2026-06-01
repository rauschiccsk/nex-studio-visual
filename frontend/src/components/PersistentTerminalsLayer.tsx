/**
 * PersistentTerminalsLayer — mounts the three claude CLI ``<AgentTerminal/>``
 * instances ABOVE :file:`pages/AgentTerminalPage.tsx` so their WebSocket
 * + xterm.js stay alive when React Router navigates between
 * ``/designer``, ``/implementer``, ``/auditor`` (CR-NS-004).
 *
 * Visibility is a pure CSS switch — the active route's slot is
 * ``display: block``; the other two are ``display: none`` (still in the
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
 * (``relative z-10`` + ``bg-slate-900``) so the page header stays visible
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
  if (pathname === "/designer") return "designer";
  if (pathname === "/implementer") return "implementer";
  if (pathname === "/auditor") return "auditor";
  return null;
}

export function PersistentTerminalsLayer() {
  const location = useLocation();
  const user = useAuthStore((s) => s.user);

  const designer = useAgentTerminalStore((s) => s.designer);
  const implementer = useAgentTerminalStore((s) => s.implementer);
  const auditor = useAgentTerminalStore((s) => s.auditor);
  const initialized = useAgentTerminalStore((s) => s.initialized);
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
  const entries: Array<[AgentRole, SlotState]> = [
    ["designer", designer],
    ["implementer", implementer],
    ["auditor", auditor],
  ];

  return (
    <>
      {entries.map(([role, slot]) => {
        if (!slot.session) return null;
        if (!visited.has(role)) return null;
        const visible = activeRole === role;
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
