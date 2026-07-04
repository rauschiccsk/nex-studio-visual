/**
 * agentTerminalStore — Zustand store owning the lifecycle of the embedded
 * claude CLI session (the AI Agent — the single v2 doer; the v1 Coordinator
 * spawn role was re-keyed to ``ai-agent`` in CR-V2-022, the Designer/
 * Implementer/Auditor spawn-terminals were removed in CR-NS-039).
 *
 * In v2.0.0 this raw PTY session is the **break-glass debug** path
 * (CR-V2-015): the first-class Manažér↔AI-Agent surface is the Riadiace centrum's
 * event-rendered transcript + engine relay (spine STEP 1).
 * This store still owns the PTY lifecycle so the break-glass console (hosted by
 * :file:`components/PersistentTerminalsLayer.tsx`) survives React Router
 * navigation to/from ``/ai-agent`` (CR-NS-004, CR-NS-009, OQ-7 rename).
 *
 * No ``persist`` middleware — session rows live in the backend; the
 * store is rebuilt by :func:`refresh` after every cold start. The slot key is
 * the ``AgentRole`` value (``"ai-agent"``, the backend wire/charter slug) so
 * dynamic ``state[role]`` indexing matches the backend row ``role`` verbatim.
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
  // CR-V2-022 (OQ-7 follow-on): the AI Agent is the only spawnable interactive (break-glass) terminal.
  // Keyed by the ``AgentRole`` value (``"ai-agent"``) so it matches the backend row ``role`` for dynamic
  // ``state[role]`` access (refresh/spawn/end). Quoted because the slug carries a hyphen.
  "ai-agent": SlotState;
  /** ``true`` once :func:`refresh` has completed at least once. Layer
   *  uses this to gate "first fetch after login" logic. */
  initialized: boolean;

  /** CR-V2-022: whether the break-glass raw-PTY console is revealed. The AI Agent tab's primary surface is
   *  the event-rendered transcript; the raw xterm (owned by PersistentTerminalsLayer) bleeds through ONLY
   *  when the Manažér opts in. ``false`` keeps the raw PTY mounted+pumping but hidden behind the transcript. */
  breakGlassOpen: boolean;

  /** Fetch active sessions for the current user and distribute by role. */
  refresh: () => Promise<void>;
  /** Spawn a new session for ``(role, projectSlug)`` and bind to slot. */
  spawn: (role: AgentRole, projectSlug: string) => Promise<void>;
  /** End the active session for ``role`` (idempotent) and clear slot. */
  end: (role: AgentRole) => Promise<void>;
  /** Toggle the break-glass raw-PTY console reveal (CR-V2-022). */
  setBreakGlassOpen: (open: boolean) => void;
  /** Wipe all slots and reset ``initialized`` — call on logout. */
  reset: () => void;
}

const ROLES: readonly AgentRole[] = ["ai-agent"];

function setSlot(
  state: AgentTerminalState,
  role: AgentRole,
  patch: Partial<SlotState>,
): Partial<AgentTerminalState> {
  return { [role]: { ...state[role], ...patch } } as Partial<AgentTerminalState>;
}

export const useAgentTerminalStore = create<AgentTerminalState>()((set, get) => ({
  "ai-agent": EMPTY_SLOT,
  initialized: false,
  breakGlassOpen: false,

  async refresh(): Promise<void> {
    set((s) => ({
      "ai-agent": { ...s["ai-agent"], status: "loading", error: "" },
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
        "ai-agent": { ...s["ai-agent"], status: "idle", error: msg },
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

  setBreakGlassOpen(open: boolean): void {
    set({ breakGlassOpen: open });
  },

  reset(): void {
    set({
      "ai-agent": EMPTY_SLOT,
      initialized: false,
      breakGlassOpen: false,
    });
  },
}));
