/**
 * DEV-318 — Nápady: a new idea talked over with Poradca, and the two ways it ends under the latest brief.
 *
 * Director 06.11.2026: „návrh nemusí vždy znamenať nový projekt, ak napríklad počas návrhu zistíme, že danú úlohu bude
 * lepšie pridať do existujúceho projektu, tak aby tam bola aj taká možnosť.“ The latest brief offers its outcome; an
 * older one was rewritten and offers nothing; an ended idea goes on in the project's Poradca.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
  useAuthStore: (sel: (s: unknown) => unknown) => sel({ token: null, user: { id: "me", username: "zoltan" } }),
}));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: unknown) => unknown) => sel({ setSelectedProject: vi.fn() }),
}));

import IdeasPage from "@/pages/IdeasPage";

const STATUS = { ready: true, problems: [], running: 0, max_concurrent: 3, attachments_max_count: 0 };
const brief = (id: string, name: string) => ({
  id,
  author: "poradca",
  content: "Zadanie.",
  status: "done",
  steps: [],
  attachments: [],
  idea_project: { name, slug: "orthodox-register", work_kind: "change", brief: "## Prečo" },
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
  return <div data-testid="where">{useLocation().pathname + useLocation().search}</div>;
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

describe("Nápady (DEV-318)", () => {
  it("lists my ideas with where each stands and starts a new one from the rough idea", async () => {
    ideasApi.listIdeasApi.mockResolvedValue([idea("brief_ready"), { ...idea("founded"), id: "i2", title: "Sklad", outcome_project_name: "NEX Sklad" }]);
    ideasApi.createIdeaApi.mockResolvedValue({ ...idea("open"), id: "i3", conversation: { messages: [] } });
    renderAt("/napady");

    // The list stands at once, empty („Zatiaľ žiadny nápad.“), and fills when the ideas arrive — wait for them
    // (CI 10.11.2026 found it still empty on a slower runner).
    const list = await screen.findByRole("list", { name: "Moje nápady" });
    expect(await within(list).findByText(/Zadanie hotové/)).toBeInTheDocument();
    expect(within(list).getByText(/Založený ako NEX Sklad/)).toBeInTheDocument();

    fireEvent.change(await screen.findByPlaceholderText(/Opíš svoj nápad/), { target: { value: "Matrika pre farnosť" } });
    fireEvent.click(screen.getByRole("button", { name: /Opýtať sa/ }));
    await waitFor(() => expect(ideasApi.createIdeaApi).toHaveBeenCalledWith("Matrika pre farnosť", []));
  });

  it("offers the outcome only under the latest brief and opens the prefilled form", async () => {
    ideasApi.listIdeasApi.mockResolvedValue([idea("brief_ready")]);
    ideasApi.getIdeaApi.mockResolvedValue(idea("brief_ready"));
    poradcaApi.getPoradcaConversationApi.mockResolvedValue({
      id: "c1",
      messages: [brief("m-old", "Stará matrika"), brief("m-new", "Orthodox Register")],
    });
    renderAt("/napady/i1");

    const outcome = await screen.findByTestId("idea-outcome");
    expect(screen.getAllByTestId("idea-outcome")).toHaveLength(1);
    expect(outcome).toHaveTextContent("Orthodox Register");
    fireEvent.click(within(outcome).getByRole("button", { name: /Založiť projekt/ }));
    expect(screen.getByTestId("where")).toHaveTextContent("/projects/new?napad=i1&odpoved=m-new");
  });

  it("an ended idea says where it went and takes no more messages here", async () => {
    const ended = idea("founded", { outcome_project_slug: "orthodox-register", outcome_project_name: "Orthodox Register", plane_identifier: "DEV-400" });
    ideasApi.listIdeasApi.mockResolvedValue([ended]);
    ideasApi.getIdeaApi.mockResolvedValue(ended);
    poradcaApi.getPoradcaConversationApi.mockResolvedValue({ id: "c1", messages: [brief("m-new", "Orthodox Register")] });
    renderAt("/napady/i1");

    expect(await screen.findByText(/Z nápadu vznikol projekt Orthodox Register; zadanie je vo verzii 0.1.0. Tiket v DEV: DEV-400./)).toBeInTheDocument();
    await screen.findByText("Zadanie."); // the brief answer is on screen — and offers nothing any more
    expect(screen.queryByTestId("idea-outcome")).toBeNull();
    expect(screen.getByText(/Nápad skončil v projekte/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Zahodiť nápad/ })).toBeNull();
  });
});
