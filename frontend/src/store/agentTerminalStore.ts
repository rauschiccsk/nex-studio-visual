/**
 * agentTerminalStore — Zustand store owning the lifecycle of the embedded
 * claude CLI session (Coordinator only — the Designer/Implementer/Auditor
 * spawn-terminals were removed in CR-NS-039).
 *
 * Single source of truth so the WebSocket + xterm.js instance hosted by
 * :file:`components/PersistentTerminalsLayer.tsx` survives React Router
 * navigation to/from ``/coordinator`` (CR-NS-004, CR-NS-009). The page
 * :file:`pages/AgentTerminalPage.tsx` consumes this
 * store for chrome rendering and dispatches lifecycle actions; the layer
 * consumes it to know which slots to mount.
 *
 * No ``persist`` middleware — session rows live in the backend; the
 * store is rebuilt by :func:`refresh` after every cold start.
 */

import { create } from "zustand";

import {
  endAgentTerminalSessionApi,
  listAgentTerminalSessionsApi,
  spawnAgentTerminalApi,
  type AgentRole,
  type AgentTerminalSession,
} from "@/services/api/agentTerminal";
import { ApiError } from "@/services/api";

export type SlotStatus = "idle" | "loading" | "spawning" | "ending";

export interface SlotState {
  session: AgentTerminalSession | null;
  status: SlotStatus;
  error: string;
}

const EMPTY_SLOT: SlotState = { session: null, status: "idle", error: "" };

export interface AgentTerminalState {
  // E3(a) (CR-NS-039): Coordinator is the only spawnable interactive terminal.
  coordinator: SlotState;
  /** ``true`` once :func:`refresh` has completed at least once. Layer
   *  uses this to gate "first fetch after login" logic. */
  initialized: boolean;

  /** Fetch active sessions for the current user and distribute by role. */
  refresh: () => Promise<void>;
  /** Spawn a new session for ``(role, projectSlug)`` and bind to slot. */
  spawn: (role: AgentRole, projectSlug: string) => Promise<void>;
  /** End the active session for ``role`` (idempotent) and clear slot. */
  end: (role: AgentRole) => Promise<void>;
  /** Wipe all slots and reset ``initialized`` — call on logout. */
  reset: () => void;
}

const ROLES: readonly AgentRole[] = ["coordinator"];

function setSlot(
  state: AgentTerminalState,
  role: AgentRole,
  patch: Partial<SlotState>,
): Partial<AgentTerminalState> {
  return { [role]: { ...state[role], ...patch } } as Partial<AgentTerminalState>;
}

export const useAgentTerminalStore = create<AgentTerminalState>()((set, get) => ({
  coordinator: EMPTY_SLOT,
  initialized: false,

  async refresh(): Promise<void> {
    set((s) => ({
      coordinator: { ...s.coordinator, status: "loading", error: "" },
    }));
    try {
      const rows = await listAgentTerminalSessionsApi();
      set((s) => {
        const next: Partial<AgentTerminalState> = { initialized: true };
        for (const role of ROLES) {
          const active = rows.find(
            (r) => r.role === role && r.ended_at === null,
          );
          next[role] = {
            ...s[role],
            session: active ?? null,
            status: "idle",
          };
        }
        return next;
      });
    } catch (e) {
      const msg =
        e instanceof ApiError ? e.message : "Nepodarilo sa načítať sessions.";
      set((s) => ({
        coordinator: { ...s.coordinator, status: "idle", error: msg },
        initialized: true,
      }));
    }
  },

  async spawn(role: AgentRole, projectSlug: string): Promise<void> {
    set((s) => setSlot(s, role, { status: "spawning", error: "" }));
    try {
      const row = await spawnAgentTerminalApi({
        role,
        project_slug: projectSlug,
      });
      set((s) => setSlot(s, role, { session: row, status: "idle", error: "" }));
    } catch (e) {
      const msg =
        e instanceof ApiError && e.message
          ? `Nepodarilo sa spustiť session: ${e.message}`
          : "Nepodarilo sa spustiť session.";
      set((s) => setSlot(s, role, { status: "idle", error: msg }));
    }
  },

  async end(role: AgentRole): Promise<void> {
    const slot = get()[role];
    if (!slot.session) return;
    set((s) => setSlot(s, role, { status: "ending", error: "" }));
    try {
      await endAgentTerminalSessionApi(slot.session.id);
      set((s) => setSlot(s, role, { session: null, status: "idle", error: "" }));
    } catch (e) {
      const msg =
        e instanceof ApiError ? e.message : "Nepodarilo sa ukončiť session.";
      set((s) => setSlot(s, role, { status: "idle", error: msg }));
    }
  },

  reset(): void {
    set({
      coordinator: EMPTY_SLOT,
      initialized: false,
    });
  },
}));
