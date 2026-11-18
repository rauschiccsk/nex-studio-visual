/**
 * DEV-408 — Nápady: „Súhlasím“ beside the box answers Projektant with one click, as the Manažér's own message.
 *
 * Director 16.11.2026: „V Nápady chcem aby bolo tlačidlo "Súhlasím". Zbytočne, aby som to vypisoval neustále.“
 * Projektant puts every question so that „Súhlasím“ is a full answer (templates/poradca-ideas-charter.md §3).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

const ideasApi = vi.hoisted(() => ({
  listIdeasApi: vi.fn(),
  createIdeaApi: vi.fn(),
  getIdeaApi: vi.fn(),
  throwAwayIdeaApi: vi.fn(),
  ideaAddedApi: vi.fn(),
  ideaFoundedApi: vi.fn(),
}));
const poradcaApi = vi.hoisted(() => ({
  askPoradcaApi: vi.fn(),
  getPoradcaStatusApi: vi.fn(),
  getPoradcaConversationApi: vi.fn(),
  buildPoradcaWsUrl: () => "ws://test",
  getPoradcaAttachmentApi: vi.fn(),
}));
vi.mock("@/services/api/ideas", () => ideasApi);
vi.mock("@/services/api/poradca", () => poradcaApi);
vi.mock("@/store/authStore", () => ({
  useAuthStore: (sel: (s: unknown) => unknown) =>
    sel({ token: null, user: { id: "me", username: "zoltan" } }),
}));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: unknown) => unknown) =>
    sel({ setSelectedProject: vi.fn() }),
}));

import IdeasPage from "@/pages/IdeasPage";

const STATUS = {
  ready: true,
  problems: [],
  running: 0,
  max_concurrent: 3,
  attachments_max_count: 0,
};
const brief = (id: string, name: string) => ({
  id,
  author: "poradca",
  content: "Zadanie.",
  status: "done",
  steps: [],
  attachments: [],
  idea_project: {
    name,
    slug: "orthodox-register",
    work_kind: "change",
    brief: "## Prečo",
  },
  idea_addition: null,
});
const idea = (state: string, extra = {}) => ({
  id: "i1",
  title: "Matrika",
  state,
  conversation_id: "c1",
  author_name: "Zoltán Rausch",
  running: false,
  cost: 0.42,
  created_at: "2026-11-06T09:00:00Z",
  updated_at: "2026-11-06T10:00:00Z",
  ...extra,
});

function Where() {
  return (
    <div data-testid="where">
      {useLocation().pathname + useLocation().search}
    </div>
  );
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/napady" element={<IdeasPage />} />
        <Route path="/napady/:ideaId" element={<IdeasPage />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  Object.values(ideasApi).forEach((f) => f.mockReset());
  poradcaApi.getPoradcaStatusApi.mockResolvedValue(STATUS);
  poradcaApi.getPoradcaConversationApi.mockReset();
  vi.stubGlobal(
    "WebSocket",
    class {
      close() {}
    },
  );
});

const question = (status = "done") => ({
  ...brief("m-1", "Sklad"),
  content: status === "running" ? "" : "Odporúčam začať skladom. Súhlasíš?",
  status,
  idea_project: null,
});

function openIdea(lastStatus = "done") {
  ideasApi.listIdeasApi.mockResolvedValue([idea("talking")]);
  ideasApi.getIdeaApi.mockResolvedValue(idea("talking"));
  poradcaApi.getPoradcaConversationApi.mockResolvedValue({ id: "c1", messages: [question(lastStatus)] });
  renderAt("/napady/i1");
}

describe("Nápady — Súhlasím jedným klikom (DEV-408)", () => {
  it("sends „Súhlasím“ as the Manažér's message and leaves what he was writing in the box", async () => {
    poradcaApi.askPoradcaApi.mockResolvedValue({ id: "c1", messages: [question()] });
    openIdea();
    const box = await screen.findByPlaceholderText(/Odpovedz Projektantovi/);
    fireEvent.change(box, { target: { value: "rozpísaná myšlienka" } });

    fireEvent.click(await screen.findByRole("button", { name: "Súhlasím" }));

    await waitFor(() => expect(poradcaApi.askPoradcaApi).toHaveBeenCalledWith("c1", "Súhlasím", []));
    expect(box).toHaveValue("rozpísaná myšlienka");
  });

  it("is greyed out with the reason while Projektant answers", async () => {
    openIdea("running");

    const agree = await screen.findByRole("button", { name: "Súhlasím" });

    expect(agree).toBeDisabled();
    expect(agree).toHaveAttribute("title", "Projektant odpovedá — ďalšiu správu pošleš, keď dokončí.");
  });

  it("is not offered before Projektant said anything — a new idea starts with the idea itself", async () => {
    ideasApi.listIdeasApi.mockResolvedValue([]);
    renderAt("/napady");

    await screen.findByPlaceholderText(/Opíš svoj nápad/);

    expect(screen.queryByRole("button", { name: "Súhlasím" })).toBeNull();
  });
});
