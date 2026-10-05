/**
 * Poradca (ICCINT-167): obrazovka rozhovorov. Rozhranie je atrapa; WebSocket je nahradený tichým stubom.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

const api = vi.hoisted(() => ({
  getPoradcaStatusApi: vi.fn(),
  getPoradcaContextApi: vi.fn(),
  listPoradcaConversationsApi: vi.fn(),
  createPoradcaConversationApi: vi.fn(),
  getPoradcaConversationApi: vi.fn(),
  askPoradcaApi: vi.fn(),
  setPoradcaScopeApi: vi.fn(),
  stopPoradcaApi: vi.fn(),
  newVersionFromPoradcaApi: vi.fn(),
  buildPoradcaWsUrl: vi.fn(() => "ws://test/ws"),
}));
vi.mock("@/services/api/poradca", () => api);

const ctx = vi.hoisted(() => ({
  state: {
    selectedProject: { slug: "demo", name: "Demo" } as { slug: string; name: string } | null,
    selectedVersion: null as { versionId: string; versionNumber: string } | null,
    setSelectedVersion: vi.fn(),
  },
}));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: unknown) => unknown) => sel(ctx.state),
}));
vi.mock("@/store/authStore", () => ({
  useAuthStore: (sel: (s: unknown) => unknown) => sel({ token: "t", user: { id: "me", username: "tibor" } }),
}));

class SilentSocket {
  onmessage: ((m: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  close() {}
}
vi.stubGlobal("WebSocket", SilentSocket);

import PoradcaPage from "@/pages/PoradcaPage";

const V_RUNNING = {
  id: "v13",
  version_number: "1.3.0",
  stage: "programovanie",
  status: "agent_working",
  instruction_open: true,
  instruction_closed_reason: null,
};
const V_DONE = {
  id: "v11",
  version_number: "1.1.0",
  stage: "done",
  status: "done",
  instruction_open: false,
  instruction_closed_reason: "Verzia je hotová — zmena patrí do novej verzie.",
};

function conversation(versionId: string | null, messages: unknown[]) {
  return {
    id: "c1",
    project_id: "p1",
    version_id: versionId,
    version_number: versionId === "v13" ? "1.3.0" : versionId === "v11" ? "1.1.0" : null,
    author_id: "me",
    author_name: "Tibor",
    title: "Prečo agent stojí?",
    running: false,
    created_at: "2026-10-05T10:00:00Z",
    updated_at: "2026-10-05T10:00:00Z",
    messages,
  };
}

const ANSWER_WITH_INSTRUCTION = {
  id: "m2",
  author: "poradca",
  content: "Agent stojí na teste.\n<pokyn-pre-agenta>Oprav test_login.</pokyn-pre-agenta>",
  steps: [{ tool: "zaznam_agenta", target: "posledných 80 krokov" }],
  status: "done",
  model: "claude-opus-5-5",
  input_tokens: 1000,
  output_tokens: 100,
  cost: 0.42,
  duration_seconds: 80,
  error: null,
  created_at: "2026-10-05T10:00:01Z",
  finished_at: "2026-10-05T10:01:21Z",
  instruction: "Oprav test_login.",
  new_version_request: null,
  captured_version_id: null,
};

function renderPage(url = "/poradca?c=c1") {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/poradca" element={<PoradcaPage />} />
        <Route path="/riadiace-centrum" element={<div>RIADIACE CENTRUM</div>} />
        <Route path="/projects" element={<div>PROJEKTY</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  Object.values(api).forEach((f) => "mockReset" in f && f.mockReset());
  api.buildPoradcaWsUrl.mockReturnValue("ws://test/ws");
  api.getPoradcaStatusApi.mockResolvedValue({ ready: true, problems: [], running: 0, max_concurrent: 3 });
  api.getPoradcaContextApi.mockResolvedValue({
    project_id: "p1",
    project_slug: "demo",
    project_name: "Demo",
    versions: [V_RUNNING, V_DONE],
  });
  api.listPoradcaConversationsApi.mockResolvedValue([conversation("v13", [])]);
  ctx.state.selectedProject = { slug: "demo", name: "Demo" };
  ctx.state.setSelectedVersion = vi.fn();
  window.localStorage.clear();
});

describe("PoradcaPage", () => {
  it("without a pinned project offers the way to Projekty", () => {
    ctx.state.selectedProject = null;
    renderPage("/poradca");
    expect(screen.getByText("Nemáš vybraný projekt")).toBeInTheDocument();
  });

  it("shows the scope with build phase and status, and the conversation list", async () => {
    api.getPoradcaConversationApi.mockResolvedValue(conversation("v13", []));
    renderPage();
    const select = (await screen.findByLabelText("O čom sa rozprávame")) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("v13"));
    expect(screen.getByRole("option", { name: "1.3.0 — programovanie, agent pracuje" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "1.1.0 — hotová" })).toBeInTheDocument();
    expect(screen.getByText("Prečo agent stojí?")).toBeInTheDocument();
  });

  it("a running answer shows what Poradca is doing and can be stopped", async () => {
    api.getPoradcaConversationApi.mockResolvedValue(
      conversation("v13", [
        { ...ANSWER_WITH_INSTRUCTION, status: "running", content: "", instruction: null, model: null, cost: null },
      ]),
    );
    api.stopPoradcaApi.mockResolvedValue({ stopping: true });
    renderPage();
    expect(await screen.findByText("Rozoberám kroky agenta stavby — posledných 80 krokov")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Opýtaj sa Poradcu/)).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /Zastaviť/ }));
    await waitFor(() => expect(api.stopPoradcaApi).toHaveBeenCalledWith("m2"));
  });

  it("puts the instruction into the Riadiace centrum draft — never sends it", async () => {
    window.localStorage.setItem("nex.draft.rozhovor.v13", "moja rozpísaná veta");
    api.getPoradcaConversationApi.mockResolvedValue(conversation("v13", [ANSWER_WITH_INSTRUCTION]));
    renderPage();
    expect(await screen.findByText("trvalo 1 min 20 s")).toBeInTheDocument();
    expect(screen.getByText(/0,42\s€ · Opus 5\.5/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Vložiť do Riadiaceho centra/ }));
    expect(await screen.findByText("RIADIACE CENTRUM")).toBeInTheDocument();
    expect(window.localStorage.getItem("nex.draft.rozhovor.v13")).toBe("moja rozpísaná veta\n\nOprav test_login.");
    expect(window.localStorage.getItem("nex.draft.rozhovor.v13.origin")).toBe("poradca");
    expect(ctx.state.setSelectedVersion).toHaveBeenCalledWith({ versionId: "v13", versionNumber: "1.3.0" });
  });

  it("greys the insert out with the reason when the build cannot take it", async () => {
    api.getPoradcaConversationApi.mockResolvedValue(conversation("v11", [ANSWER_WITH_INSTRUCTION]));
    renderPage();
    const button = await screen.findByRole("button", { name: /Vložiť do Riadiaceho centra/ });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", "Verzia je hotová — zmena patrí do novej verzie.");
  });

  it("a new conversation is asked with the chosen scope", async () => {
    api.createPoradcaConversationApi.mockResolvedValue(conversation(null, []));
    renderPage("/poradca?verzia=v13");
    const select = (await screen.findByLabelText("O čom sa rozprávame")) as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe("v13"));
    fireEvent.change(screen.getByPlaceholderText(/Opýtaj sa Poradcu/), { target: { value: "Čo je v UAT?" } });
    fireEvent.click(screen.getByRole("button", { name: /Opýtať sa/ }));
    await waitFor(() => expect(api.createPoradcaConversationApi).toHaveBeenCalledWith("demo", "Čo je v UAT?", "v13"));
  });

  it("says why it cannot run instead of accepting a question", async () => {
    api.getPoradcaStatusApi.mockResolvedValue({ ready: false, problems: ["chýba obraz"], running: 0, max_concurrent: 3 });
    renderPage("/poradca");
    expect(await screen.findByText("Poradca teraz nevie bežať: chýba obraz")).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Opýtaj sa Poradcu/)).toBeDisabled();
  });
});
