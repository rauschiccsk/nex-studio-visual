/**
 * DEV-331 — an idea in the side panel of Nápady is renamed and deleted exactly as a Poradca conversation is.
 *
 * Director 09.11.2026: „Je možné založiť nový nápad, na bočnom paneli vidím ich, ale neviem premenovať a vymazať.
 * Chcel by som aby aj tu presne tak fungovali ako u Poradca.“ The row is Poradca's own row (PoradcaConversationItem):
 * the pencil renames in place, the trash asks right in the row. An idea that ended in a project leaves only the list —
 * its talk stays in the project's Poradca (Director: „Súhlasím“).
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

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
  renamePoradcaConversationApi: vi.fn(),
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

const idea = (id: string, title: string, state: string, extra = {}) => ({
  id,
  title,
  state,
  conversation_id: `c-${id}`,
  author_name: "Zoltán Rausch",
  running: false,
  cost: null,
  outcome_project_slug: null,
  outcome_project_name: null,
  outcome_backlog_number: null,
  plane_identifier: null,
  plane_url: null,
  created_at: "2026-11-06T10:00:00Z",
  updated_at: "2026-11-06T10:00:00Z",
  ...extra,
});

const OPEN = idea("i1", "Matrika", "open");
const ENDED = idea("i2", "Sklad", "founded", {
  talk_in_project: true,
  outcome_project_slug: "nex-sklad",
  outcome_project_name: "NEX Sklad",
});

function page() {
  return render(
    <MemoryRouter initialEntries={["/napady"]}>
      <Routes>
        <Route path="/napady" element={<IdeasPage />} />
        <Route path="/napady/:ideaId" element={<IdeasPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

const list = () => screen.findByRole("list", { name: "Moje nápady" });
const rowOf = async (title: string) => (await screen.findByText(title)).closest("li") as HTMLElement;

beforeEach(() => {
  Object.values(ideasApi).forEach((f) => f.mockReset());
  poradcaApi.renamePoradcaConversationApi.mockReset();
  poradcaApi.getPoradcaStatusApi.mockResolvedValue(STATUS);
  ideasApi.listIdeasApi.mockResolvedValue([OPEN, ENDED]);
  vi.stubGlobal(
    "WebSocket",
    class {
      close() {}
    },
  );
  page();
});

describe("Nápady: rename and delete in the side panel (DEV-331)", () => {
  it("every idea has Poradca's pencil and trash in its row", async () => {
    await list();
    for (const title of ["Matrika", "Sklad"]) {
      const row = await rowOf(title);
      expect(within(row).getByRole("button", { name: "Premenovať nápad" })).toBeInTheDocument();
      expect(within(row).getByRole("button", { name: "Vymazať nápad" })).toBeInTheDocument();
    }
  });

  it("the pencil renames the idea in place — through its conversation — and the list shows the new name", async () => {
    poradcaApi.renamePoradcaConversationApi.mockResolvedValue({});
    const row = await rowOf("Matrika");
    fireEvent.click(within(row).getByRole("button", { name: "Premenovať nápad" }));
    ideasApi.listIdeasApi.mockResolvedValue([{ ...OPEN, title: "Pravoslávna matrika" }, ENDED]);

    const field = screen.getByRole("textbox", { name: "Názov nápadu" });
    fireEvent.change(field, { target: { value: "Pravoslávna matrika" } });
    fireEvent.submit(field.closest("form") as HTMLFormElement);

    await waitFor(() => expect(poradcaApi.renamePoradcaConversationApi).toHaveBeenCalledWith("c-i1", "Pravoslávna matrika"));
    expect(await screen.findByText("Pravoslávna matrika")).toBeInTheDocument();
  });

  it("an idea that ended nowhere is deleted for good, asked right in its row", async () => {
    ideasApi.throwAwayIdeaApi.mockResolvedValue(undefined);
    const row = await rowOf("Matrika");
    fireEvent.click(within(row).getByRole("button", { name: "Vymazať nápad" }));

    const ask = await screen.findByRole("group", { name: "Potvrdenie vymazania" });
    expect(ask).toHaveTextContent("Vymazať „Matrika“ natrvalo?");
    ideasApi.listIdeasApi.mockResolvedValue([ENDED]);
    fireEvent.click(within(ask).getByRole("button", { name: /^Vymazať$/ }));

    await waitFor(() => expect(ideasApi.throwAwayIdeaApi).toHaveBeenCalledWith("i1"));
    await waitFor(() => expect(screen.queryByText("Matrika")).toBeNull());
  });

  it("an idea that ended in a project leaves the list only — the row says its talk stays in the project", async () => {
    ideasApi.throwAwayIdeaApi.mockResolvedValue(undefined);
    const row = await rowOf("Sklad");
    fireEvent.click(within(row).getByRole("button", { name: "Vymazať nápad" }));

    const ask = await screen.findByRole("group", { name: "Potvrdenie vymazania" });
    expect(ask).toHaveTextContent("Odstrániť „Sklad“ zo zoznamu Nápadov?");
    expect(ask).toHaveTextContent("Rozhovor ostane v Poradcovi projektu NEX Sklad.");
    fireEvent.click(within(ask).getByRole("button", { name: /^Odstrániť$/ }));

    await waitFor(() => expect(ideasApi.throwAwayIdeaApi).toHaveBeenCalledWith("i2"));
  });
});
