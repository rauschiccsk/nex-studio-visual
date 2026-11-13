/**
 * DEV-308 — the strip of the Riadiace centrum reads the board's CI gate, not only the bar below it.
 *
 * 05.11.2026, the fast fix 1.0.1 of NEX Knowledge waited for its checks on GitHub after Verifikácia: „Čaká na súhlas“
 * in the strip, „… Kokpit to dorieši sám.“ in the next step. The component knows the sentence (its own test); this
 * walks the real page, so a page that stops handing the gate over shows up here.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter } from "react-router-dom";

import type { PipelineBoard } from "@/services/api/pipeline";

const h = vi.hoisted(() => ({
  ws: { board: null as unknown },
  ctx: {
    selectedProject: { slug: "nex-knowledge", name: "NEX Knowledge" },
    selectedVersion: { versionId: "v-101", versionNumber: "1.0.1" },
    setSelectedVersion: () => {},
  },
}));

vi.mock("@/hooks/usePipelineWs", () => ({
  usePipelineWs: () => ({ board: h.ws.board, activity: [], reconnecting: false, error: null, setBoard: vi.fn() }),
}));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: typeof h.ctx) => unknown) => sel(h.ctx),
}));
vi.mock("@/store/authStore", () => ({
  useAuthStore: (sel: (s: unknown) => unknown) => sel({ token: "t", user: { id: "me", role: "ri" } }),
}));
vi.mock("@/services/api/pipeline", () => ({
  relayPipelineMessageApi: vi.fn(),
  postPipelineActionApi: vi.fn(),
  getCiStatusApi: vi.fn().mockResolvedValue({ stav: "unknown", detail: "—", sha: null }),
}));
vi.mock("@/services/api/poradca", () => ({
  stopPoradcaApi: vi.fn(),
  saveRequestToBacklogApi: vi.fn(),
  attachAgentTicketsApi: vi.fn(),
}));
vi.mock("@/components/controlCenter/ConversationThread", () => ({ default: () => <div /> }));
vi.mock("@/components/controlCenter/PhaseBar", () => ({ default: () => <div /> }));
vi.mock("@/components/controlCenter/TaskPlanRail", () => ({ default: () => <div /> }));

import ControlCenterPage from "@/pages/ControlCenterPage";

const WAITS =
  "Verifikácia prešla — „Schváliť na Hotovo“ sa ponúkne, keď kontroly na GitHube dobehnú. Kokpit to dorieši sám.";

beforeEach(() => {
  window.localStorage.clear();
  h.ws.board = {
    state: {
      current_stage: "verifikacia",
      status: "awaiting_manazer",
      block_reason: null,
      next_action:
        "Verifikácia PASS — čaká na kontrolu na GitHube (CI); Hotovo pôjde schváliť, keď dobehne. Kokpit to dorieši sám.",
    },
    available_actions: ["ask", "verdict"],
    action_labels: { schvalit: "Schváliť na Hotovo" }, // DEV-373: named by the cockpit
    recent_messages: [],
    ci_gate: { state: "pending", message: WAITS },
  } as unknown as PipelineBoard;
});

describe("Riadiace centrum — the strip while the checks on GitHub run (DEV-308)", () => {
  it("says the cockpit goes on by itself and offers no approval", () => {
    render(
      <MemoryRouter>
        <ControlCenterPage />
      </MemoryRouter>,
    );

    expect(screen.getByText("Čaká na kontroly na GitHube — kokpit pokračuje sám")).toBeInTheDocument();
    expect(screen.queryByText("Čaká na súhlas")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Schváliť na Hotovo" })).toBeDisabled();
  });
});
