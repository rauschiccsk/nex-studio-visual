/**
 * DEV-442 — „Súhlasím“ in Poradca, under an answer that asks the person's consent.
 *
 * Director 20.11.2026: „chcel by som mať také isté tlačidlo aj v Poradca, avšak s tým rozdielom, že toto tlačidlo sa
 * objaví ak Poradca pýta odo mňa súhlas.“ Poradca marks such an answer with `<suhlas/>` (charter §5); the cockpit offers
 * the one-click answer under the last answer only, never guesses it from the text and never shows the mark.
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
    selectedProject: { slug: "demo", name: "Demo" } as {
      slug: string;
      name: string;
    } | null,
    selectedVersion: null as {
      versionId: string;
      versionNumber: string;
    } | null,
    setSelectedVersion: vi.fn(),
  },
}));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: unknown) => unknown) => sel(ctx.state),
}));
vi.mock("@/store/authStore", () => ({
  useAuthStore: (sel: (s: unknown) => unknown) =>
    sel({ token: "t", user: { id: "me", username: "tibor" } }),
}));

class SilentSocket {
  onmessage: ((m: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  close() {}
}
vi.stubGlobal("WebSocket", SilentSocket);

import PoradcaPage from "@/pages/PoradcaPage";
import { answerForDisplay, asksConsent } from "@/lib/poradcaAnswer";

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
    version_number:
      versionId === "v13" ? "1.3.0" : versionId === "v11" ? "1.1.0" : null,
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
  content:
    "Agent stojí na teste.\n<pokyn-pre-agenta>Oprav test_login.</pokyn-pre-agenta>",
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
  api.getPoradcaStatusApi.mockResolvedValue({
    ready: true,
    problems: [],
    running: 0,
    max_concurrent: 3,
  });
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

const ASKS =
  "Staviteľ má zapísať výnimku a trasu nemazať.\n\nSúhlasíš, aby Staviteľ výnimku zapísal?\n<suhlas/>";
const plain = (id: string, content: string) => ({
  ...ANSWER_WITH_INSTRUCTION,
  id,
  content,
  instruction: null,
});
const question = (id: string) => ({
  ...plain(id, "A čo ďalej?"),
  author: "human",
});

describe("Poradca — „Súhlasím“ when it asks for consent (DEV-442)", () => {
  it("an answer that asks for consent has the button; one click sends „Súhlasím“ as the person's message", async () => {
    api.getPoradcaConversationApi.mockResolvedValue(
      conversation("v13", [plain("m2", ASKS)]),
    );
    api.askPoradcaApi.mockResolvedValue(
      conversation("v13", [plain("m2", ASKS), question("m3")]),
    );
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /Súhlasím/ }));

    await waitFor(() =>
      expect(api.askPoradcaApi).toHaveBeenCalledWith("c1", "Súhlasím", []),
    );
  });

  it("an answer that asks nothing has no button", async () => {
    api.getPoradcaConversationApi.mockResolvedValue(
      conversation("v13", [plain("m2", "Agent stojí na teste login.")]),
    );
    renderPage();

    await screen.findByText(/Agent stojí na teste login/);
    expect(screen.queryByRole("button", { name: /Súhlasím/ })).toBeNull();
  });

  it("an older answer that asked, already answered, has no button", async () => {
    api.getPoradcaConversationApi.mockResolvedValue(
      conversation("v13", [
        plain("m2", ASKS),
        question("m3"),
        plain("m4", "Zapísané, nič ďalšie netreba."),
      ]),
    );
    renderPage();

    await screen.findByText(/Zapísané, nič ďalšie netreba/);
    expect(screen.queryByRole("button", { name: /Súhlasím/ })).toBeNull();
  });

  it("the mark is the cockpit's, never the person's to read", async () => {
    api.getPoradcaConversationApi.mockResolvedValue(
      conversation("v13", [plain("m2", ASKS)]),
    );
    renderPage();

    await screen.findByText(/Súhlasíš, aby Staviteľ výnimku zapísal/);
    expect(document.body.textContent).not.toMatch(/suhlas\/?>/);
    expect(answerForDisplay(ASKS)).not.toMatch(/<suhlas/);
  });

  it("the cockpit does not guess consent from the words — only the mark counts", () => {
    expect(asksConsent("Súhlasíš, aby Staviteľ výnimku zapísal?")).toBe(false);
    expect(asksConsent(ASKS)).toBe(true);
    expect(asksConsent("Hotovo.\n<suhlas />")).toBe(true);
  });
});
