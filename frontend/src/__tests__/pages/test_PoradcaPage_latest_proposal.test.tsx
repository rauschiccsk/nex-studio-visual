/**
 * DEV-431, DEV-432 — Poradca says at an answer that it was put into Riadiace centrum and when; only the newest proposal
 * of the conversation puts something there, an older one is grey with the reason.
 *
 * Director 18.11.2026: „nevidím tu, či som to už urobil alebo nie“ — „Chcem aby toto tlačislo bolo aktívne vždy len pre
 * posledný návrh.“
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
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
  renamePoradcaConversationApi: vi.fn(),
  deletePoradcaConversationApi: vi.fn(),
  stopPoradcaApi: vi.fn(),
  saveRequestToBacklogApi: vi.fn(),
  markHandedOffApi: vi.fn(),
  buildPoradcaWsUrl: vi.fn(() => "ws://test/ws"),
  getPoradcaAttachmentApi: vi.fn(),
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
import { latestProposalId, OLDER_PROPOSAL } from "@/lib/poradcaHandoff";

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
};

function renderPage(url = "/poradca?c=c1") {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/poradca" element={<PoradcaPage />} />
        <Route path="/riadiace-centrum" element={<div>RIADIACE CENTRUM</div>} />
        <Route path="/projects" element={<div>PROJEKTY</div>} />
        <Route path="/projects/:slug/backlog" element={<div>ZÁSOBNÍK</div>} />
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

const answer = (id: string, instruction: string | null, extra = {}) => ({
  ...ANSWER_WITH_INSTRUCTION,
  id,
  content: instruction ? `Navrhujem.\n<pokyn-pre-agenta>${instruction}</pokyn-pre-agenta>` : "Rozumiem, nič netreba.",
  instruction,
  ...extra,
});
const question = (id: string) => ({ ...ANSWER_WITH_INSTRUCTION, id, author: "human", content: "A čo ďalej?", instruction: null });

describe("Poradca — posledný návrh a vložené (DEV-431, DEV-432)", () => {
  it("only the newest proposal puts it into Riadiace centrum; the older one is grey with the reason", async () => {
    api.getPoradcaConversationApi.mockResolvedValue(
      conversation("v13", [answer("m2", "Oprav test_login."), question("m3"), answer("m4", "Oprav test_logout.")]),
    );
    renderPage();

    const buttons = await screen.findAllByRole("button", { name: /Vložiť do Riadiaceho centra/ });

    expect(buttons).toHaveLength(2);
    expect(buttons[0]).toBeDisabled();
    expect(buttons[0]).toHaveAttribute("title", OLDER_PROPOSAL);
    expect(buttons[1]).toBeEnabled();
  });

  it("putting it there records it at the answer and opens Riadiace centrum", async () => {
    api.markHandedOffApi.mockResolvedValue({ handed_off_at: "2026-11-18T15:20:00" });
    api.getPoradcaConversationApi.mockResolvedValue(conversation("v13", [answer("m4", "Oprav test_logout.")]));
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /Vložiť do Riadiaceho centra/ }));

    expect(api.markHandedOffApi).toHaveBeenCalledWith("m4");
    expect(await screen.findByText("RIADIACE CENTRUM")).toBeInTheDocument();
  });

  it("an answer put there says so and when", async () => {
    api.getPoradcaConversationApi.mockResolvedValue(
      conversation("v13", [answer("m4", "Oprav test_logout.", { handed_off_at: "2026-11-18T15:20:00" })]),
    );
    renderPage();

    expect(await screen.findByText("Vložené do Riadiaceho centra 18.11.2026 15:20")).toBeInTheDocument();
  });

  it("an answer never put there says nothing of it", async () => {
    api.getPoradcaConversationApi.mockResolvedValue(conversation("v13", [answer("m4", "Oprav test_logout.")]));
    renderPage();

    await screen.findByRole("button", { name: /Vložiť do Riadiaceho centra/ });
    expect(screen.queryByText(/Vložené do Riadiaceho centra/)).toBeNull();
  });

  it("the newest proposal is the newest answer that offers something — a later plain answer does not take it over", () => {
    const messages = [answer("m2", "Oprav test_login."), question("m3"), answer("m4", null)];

    expect(latestProposalId(messages as never)).toBe("m2");
  });
});
