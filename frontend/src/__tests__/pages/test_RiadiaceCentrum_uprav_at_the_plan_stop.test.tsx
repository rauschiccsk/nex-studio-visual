/**
 * DEV-297 — Poradca's instruction for the task plan goes back to the agent as „Uprav“, by the Manažér's own click.
 *
 * 03.11.2026, NEX Manager 1.5.0 at the stop after its task plan: the Director pressed „Vložiť do Riadiaceho centra“,
 * the instruction landed in the conversation box, and the only button there sent it as a QUESTION — twice. The agent
 * may not change the plan on a question and asked for „Uprav“, a button the screen did not have. His path, walked
 * with the real Poradca button and the real Riadiace centrum.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import type { PipelineBoard } from "@/services/api/pipeline";

const h = vi.hoisted(() => ({
  ws: { board: null as unknown },
  ctx: {
    selectedProject: { slug: "nex-manager", name: "NEX Manager" },
    selectedVersion: null as { versionId: string; versionNumber: string } | null,
    setSelectedVersion: (v: { versionId: string; versionNumber: string }) => {
      h.ctx.selectedVersion = v;
    },
  },
  postPipelineActionApi: vi.fn(),
  relayPipelineMessageApi: vi.fn(),
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
  relayPipelineMessageApi: h.relayPipelineMessageApi,
  postPipelineActionApi: h.postPipelineActionApi,
  getCiStatusApi: vi.fn().mockResolvedValue({ stav: "unknown", detail: "—", sha: null }),
}));
vi.mock("@/services/api/poradca", () => ({
  stopPoradcaApi: vi.fn(),
  saveRequestToBacklogApi: vi.fn(),
  attachAgentTicketsApi: vi.fn(),
}));
vi.mock("@/components/riadiace/ConversationThread", () => ({ default: () => <div /> }));
vi.mock("@/components/riadiace/PhaseBar", () => ({ default: () => <div /> }));
vi.mock("@/components/riadiace/HonestStatusStrip", () => ({ default: () => <div /> }));
vi.mock("@/components/riadiace/PlanUlohRail", () => ({ default: () => <div /> }));

import PoradcaAnswer from "@/components/poradca/PoradcaAnswer";
import { UPRAV_HINT, UPRAV_INSTRUCTION_HINT } from "@/components/riadiace/ConversationComposer";
import RiadiaceCentrumPage from "@/pages/RiadiaceCentrumPage";

const VERSION = "v-150";
const INSTRUCTION = "Uprav plán: 35.2.1 dátum vydania nepíše, zapisuje ho kokpit.";
/** The board at the plan stop as the route sends it: no ``schvalit`` (tasks remain), „Spustiť programovanie“. */
const PLAN_STOP = {
  state: {
    current_stage: "programovanie",
    status: "awaiting_manazer",
    block_reason: null,
    next_action: "Manažér: pozri plán úloh a spusti programovanie (Spustiť programovanie), alebo plán vráť na úpravu (Uprav).",
  },
  available_actions: ["ask", "pokracovat", "uprav", "zopakovat_konzultaciu"],
  recent_messages: [],
} as unknown as PipelineBoard;
const SCOPE = {
  id: VERSION,
  version_number: "1.5.0",
  stage: "programovanie",
  status: "awaiting_manazer",
  instruction_open: true,
  instruction_closed_reason: null,
};
const MESSAGE = {
  id: "m1",
  author: "poradca",
  content: "Navrhujem plán vrátiť.",
  steps: [],
  status: "done",
  instruction: INSTRUCTION,
  backlog_request: null,
  captured_backlog_number: null,
  choices: [],
  ticket_proposal: null,
  plane_identifier: null,
  plane_url: null,
  agent_tickets: [],
};

beforeEach(() => {
  window.localStorage.clear();
  h.ctx.selectedVersion = null;
  h.ws.board = PLAN_STOP;
  h.postPipelineActionApi.mockReset().mockResolvedValue(PLAN_STOP);
  h.relayPipelineMessageApi.mockReset();
});

describe("Riadiace centrum at the plan stop — „Uprav“ (DEV-297)", () => {
  it("Poradca's instruction, then „Uprav“: the plan goes back with it, never as a question", async () => {
    render(
      <MemoryRouter initialEntries={["/poradca"]}>
        <Routes>
          <Route
            path="/poradca"
            element={<PoradcaAnswer message={MESSAGE as never} scopeVersion={SCOPE as never} projectVersions={[SCOPE] as never} />}
          />
          <Route path="/riadiace-centrum" element={<RiadiaceCentrumPage />} />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: /Vložiť do Riadiaceho centra/ }));

    const box = await screen.findByPlaceholderText(/Napíš AI Agentovi/);
    await waitFor(() => expect(box).toHaveValue(INSTRUCTION));
    // DEV-298: the line says which button sends Poradca's instruction.
    expect(screen.getByText(UPRAV_INSTRUCTION_HINT)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Uprav" }));

    await waitFor(() =>
      expect(h.postPipelineActionApi).toHaveBeenCalledWith(VERSION, { action: "uprav", payload: { comment: INSTRUCTION } }),
    );
    expect(h.relayPipelineMessageApi).not.toHaveBeenCalled();
    await waitFor(() => expect(box).toHaveValue(""));
  });

  it("at a phase gate „Upraviť“ of the approval bar is the one — the box carries no second", () => {
    h.ws.board = {
      ...PLAN_STOP,
      state: { ...PLAN_STOP.state, current_stage: "navrh" },
      available_actions: ["ask", "schvalit", "uprav", "zopakovat_konzultaciu"],
    } as unknown as PipelineBoard;
    h.ctx.selectedVersion = { versionId: VERSION, versionNumber: "1.5.0" };
    render(
      <MemoryRouter>
        <RiadiaceCentrumPage />
      </MemoryRouter>,
    );

    expect(screen.getByRole("button", { name: "Upraviť" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Uprav" })).not.toBeInTheDocument();
    expect(screen.queryByText(UPRAV_HINT)).not.toBeInTheDocument();
  });
});

/**
 * DEV-298 — the main button is the one to press. 03.11.2026 13:17 the Director inserted Poradca's instruction at the
 * plan stop of 4.109.0 and it went out as a question again: „Poslať“ was the blue button and Enter sent it, „Uprav“ the
 * grey one beside it. An instruction from Poradca is work, never a question. Director: „hlavným tlačidlom má byť ten
 * čo treba kliknúť.“
 */
describe("Poradca's instruction goes out as „Uprav“ — the main button and Enter (DEV-298)", () => {
  function insertFromPoradca() {
    return render(
      <MemoryRouter initialEntries={["/poradca"]}>
        <Routes>
          <Route
            path="/poradca"
            element={<PoradcaAnswer message={MESSAGE as never} scopeVersion={SCOPE as never} projectVersions={[SCOPE] as never} />}
          />
          <Route path="/riadiace-centrum" element={<RiadiaceCentrumPage />} />
        </Routes>
      </MemoryRouter>,
    );
  }

  async function boxWithInstruction() {
    insertFromPoradca();
    fireEvent.click(screen.getByRole("button", { name: /Vložiť do Riadiaceho centra/ }));
    const box = await screen.findByPlaceholderText(/Napíš AI Agentovi/);
    await waitFor(() => expect(box).toHaveValue(INSTRUCTION));
    return box;
  }

  it("„Uprav“ is the main button, „Poslať“ the other one", async () => {
    await boxWithInstruction();

    expect(screen.getByRole("button", { name: "Uprav" })).toHaveAttribute("data-primary", "true");
    expect(screen.getByRole("button", { name: "Poslať" })).toHaveAttribute("data-primary", "false");
    expect(screen.getByText(UPRAV_INSTRUCTION_HINT)).toBeInTheDocument();
    expect(screen.queryByText(UPRAV_HINT)).not.toBeInTheDocument();
  });

  it("Enter sends it as „Uprav“, never as a question", async () => {
    const box = await boxWithInstruction();

    fireEvent.keyDown(box, { key: "Enter" });

    await waitFor(() =>
      expect(h.postPipelineActionApi).toHaveBeenCalledWith(VERSION, { action: "uprav", payload: { comment: INSTRUCTION } }),
    );
    expect(h.relayPipelineMessageApi).not.toHaveBeenCalled();
  });

  it("…also after he edited it", async () => {
    const box = await boxWithInstruction();
    const edited = `${INSTRUCTION} Bod 7: nič viac.`;

    fireEvent.change(box, { target: { value: edited } });
    expect(screen.getByRole("button", { name: "Uprav" })).toHaveAttribute("data-primary", "true");
    fireEvent.keyDown(box, { key: "Enter" });

    await waitFor(() =>
      expect(h.postPipelineActionApi).toHaveBeenCalledWith(VERSION, { action: "uprav", payload: { comment: edited } }),
    );
    expect(h.relayPipelineMessageApi).not.toHaveBeenCalled();
  });

  it("…and after the page is opened again", async () => {
    const first = insertFromPoradca();
    fireEvent.click(screen.getByRole("button", { name: /Vložiť do Riadiaceho centra/ }));
    const box = await screen.findByPlaceholderText(/Napíš AI Agentovi/);
    await waitFor(() => expect(box).toHaveValue(INSTRUCTION));
    fireEvent.change(box, { target: { value: `${INSTRUCTION} Doplnené.` } });
    first.unmount();

    render(
      <MemoryRouter>
        <RiadiaceCentrumPage />
      </MemoryRouter>,
    );
    const again = screen.getByPlaceholderText(/Napíš AI Agentovi/);
    await waitFor(() => expect(again).toHaveValue(`${INSTRUCTION} Doplnené.`));

    expect(screen.getByRole("button", { name: "Uprav" })).toHaveAttribute("data-primary", "true");
  });

  it("his own text: „Poslať“ is the main button and Enter sends a message", async () => {
    h.ctx.selectedVersion = { versionId: VERSION, versionNumber: "1.5.0" };
    h.relayPipelineMessageApi.mockResolvedValue({ deferred: false, board: PLAN_STOP });
    render(
      <MemoryRouter>
        <RiadiaceCentrumPage />
      </MemoryRouter>,
    );
    const box = screen.getByPlaceholderText(/Napíš AI Agentovi/);

    fireEvent.change(box, { target: { value: "Koľko úloh má plán?" } });
    expect(screen.getByRole("button", { name: "Poslať" })).toHaveAttribute("data-primary", "true");
    expect(screen.getByText(UPRAV_HINT)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Uprav" })).toHaveAttribute("data-primary", "false");
    fireEvent.keyDown(box, { key: "Enter" });

    await waitFor(() => expect(h.relayPipelineMessageApi).toHaveBeenCalledWith(VERSION, "Koľko úloh má plán?"));
    expect(h.postPipelineActionApi).not.toHaveBeenCalled();
  });

  it("once sent, the next text of his own is a message again", async () => {
    h.relayPipelineMessageApi.mockResolvedValue({ deferred: false, board: PLAN_STOP });
    const box = await boxWithInstruction();
    fireEvent.click(screen.getByRole("button", { name: "Uprav" }));
    await waitFor(() => expect(box).toHaveValue(""));

    fireEvent.change(box, { target: { value: "Ďakujem, ešte otázka." } });
    expect(screen.getByRole("button", { name: "Poslať" })).toHaveAttribute("data-primary", "true");
    fireEvent.keyDown(box, { key: "Enter" });

    await waitFor(() => expect(h.relayPipelineMessageApi).toHaveBeenCalledWith(VERSION, "Ďakujem, ešte otázka."));
  });
});
