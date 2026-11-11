/**
 * DEV-360 — a founded idea without its ticket in DEV offers one button that founds it.
 *
 * Director 11.11.2026 (NEX Archive founded under the account alex, „Tiket v DEV sa nezaložil“): „Máme ten istý problém
 * ako minule.“ The founding said Andros Dedo would found the ticket, and nothing on the screen tried again.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

const ideasApi = vi.hoisted(() => ({
  listIdeasApi: vi.fn(),
  createIdeaApi: vi.fn(),
  getIdeaApi: vi.fn(),
  throwAwayIdeaApi: vi.fn(),
  ideaAddedApi: vi.fn(),
  ideaFoundedApi: vi.fn(),
  ideaTicketApi: vi.fn(),
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
    sel({ token: null, user: { id: "me", username: "alex" } }),
}));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: unknown) => unknown) =>
    sel({ setSelectedProject: vi.fn() }),
}));

import IdeasPage from "@/pages/IdeasPage";

const NO_PERSON =
  "Konto alex nemá zodpovedného v Plane. Manažér ho nastaví v Nastaveniach, karta Používatelia, časť „Zodpovedný v Plane“; potom pri nápade stlač „Založiť tiket projektu v DEV“.";

const founded = (extra = {}) => ({
  id: "i1",
  title: "NEX Archive",
  state: "founded",
  conversation_id: "c1",
  author_name: "Alexander Honest",
  running: false,
  cost: 1.2,
  talk_in_project: true,
  outcome_project_slug: "nex-archive",
  outcome_project_name: "NEX Archive",
  plane_identifier: null,
  plane_url: null,
  created_at: "2026-11-11T09:00:00Z",
  updated_at: "2026-11-11T10:00:00Z",
  ...extra,
});

function renderIdea(shown: ReturnType<typeof founded>) {
  ideasApi.listIdeasApi.mockResolvedValue([shown]);
  ideasApi.getIdeaApi.mockResolvedValue(shown);
  return render(
    <MemoryRouter initialEntries={["/napady/i1"]}>
      <Routes>
        <Route path="/napady/:ideaId" element={<IdeasPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  Object.values(ideasApi).forEach((f) => f.mockReset());
  poradcaApi.getPoradcaStatusApi.mockResolvedValue({
    ready: true,
    problems: [],
    running: 0,
    max_concurrent: 3,
    attachments_max_count: 0,
  });
  poradcaApi.getPoradcaConversationApi.mockResolvedValue({ id: "c1", messages: [] });
  vi.stubGlobal(
    "WebSocket",
    class {
      close() {}
    },
  );
});

describe("the ticket of a founded project (DEV-360)", () => {
  it("a founded idea without its ticket offers the button, and the ticket it founds is shown", async () => {
    renderIdea(founded());
    ideasApi.ideaTicketApi.mockResolvedValue({
      ...founded({ plane_identifier: "DEV-361", plane_url: "http://p/361" }),
      ticket_error: null,
    });

    expect(await screen.findByText("Projekt ešte nemá tiket v DEV.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Založiť tiket projektu v DEV" }));

    expect(await screen.findByText(/Tiket v DEV: DEV-361\./)).toBeInTheDocument();
    expect(ideasApi.ideaTicketApi).toHaveBeenCalledWith("i1");
    expect(screen.queryByRole("button", { name: "Založiť tiket projektu v DEV" })).toBeNull();
  });

  it("what is still missing stays on the screen in the backend's words, and the button stays", async () => {
    renderIdea(founded());
    ideasApi.ideaTicketApi.mockResolvedValue({ ...founded(), ticket_error: NO_PERSON });

    fireEvent.click(await screen.findByRole("button", { name: "Založiť tiket projektu v DEV" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(NO_PERSON);
    expect(screen.getByRole("button", { name: "Založiť tiket projektu v DEV" })).toBeEnabled();
  });

  it("a founded idea with its ticket offers no button", async () => {
    renderIdea(founded({ plane_identifier: "DEV-400", plane_url: "http://p/400" }));

    expect(await screen.findByText(/Tiket v DEV: DEV-400\./)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Založiť tiket projektu v DEV" })).toBeNull();
  });

  it("an idea added to a Zásobník offers no button — its requirement has a ticket of its own", async () => {
    renderIdea(founded({ state: "added", outcome_backlog_number: 7 }));

    expect(await screen.findByText(/ako REQ-7\./)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Založiť tiket projektu v DEV" })).toBeNull();
  });
});
