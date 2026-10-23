/**
 * DEV-191 — „Opýtaj sa Poradcu“ at a finished version asks Poradca, the way „Overiť s Poradcom“ does on the cards.
 *
 * Director 23.10.2026: „Tlačidlo, ktoré ma prepne do chatu poradca nemá zmysel, prepnúť viem aj na bočnej lište. Ak
 * kliknem na Opýtať poradcu, malo by sa skutočne opýtať ako to robíme na kartách keď agent nájde nejaký problém.“
 * On NEX Inbox 1.10.1 the button opened an empty conversation and nothing was asked. These tests walk the path through
 * the REAL button and the REAL Poradca page; only the API is a mock.
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
  getPoradcaReviewConversationApi: vi.fn(),
  getPoradcaConversationApi: vi.fn(),
  askPoradcaApi: vi.fn(),
  setPoradcaScopeApi: vi.fn(),
  renamePoradcaConversationApi: vi.fn(),
  deletePoradcaConversationApi: vi.fn(),
  stopPoradcaApi: vi.fn(),
  saveRequestToBacklogApi: vi.fn(),
  buildPoradcaWsUrl: vi.fn(() => "ws://test/ws"),
  getPoradcaAttachmentApi: vi.fn(),
}));
vi.mock("@/services/api/poradca", () => api);

vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: unknown) => unknown) =>
    sel({
      selectedProject: { slug: "nex-inbox", name: "NEX Inbox" },
      selectedVersion: { versionId: "v1101", versionNumber: "1.10.1" },
      setSelectedVersion: vi.fn(),
    }),
}));
vi.mock("@/store/authStore", () => ({
  useAuthStore: (sel: (s: unknown) => unknown) =>
    sel({ token: "t", user: { id: "me", username: "zoltan" } }),
}));

class SilentSocket {
  onmessage: ((m: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  close() {}
}
vi.stubGlobal("WebSocket", SilentSocket);

import OpytajSaPoradcuBar from "@/components/riadiace/OpytajSaPoradcuBar";
import PoradcaPage from "@/pages/PoradcaPage";

const VERSION = "v1101";
const ASKED =
  "Verzia 1.10.1 projektu NEX Inbox je hotová. Zhrň mi, čo priniesla, čo mám vyskúšať v UAT a na čo si dať pozor " +
  "pred nasadením na PROD.";

function conversation(id: string, question: string) {
  return {
    id,
    project_id: "p1",
    version_id: VERSION,
    version_number: "1.10.1",
    author_id: "me",
    author_name: "Zoltán",
    title: "1.10.1 — po dokončení",
    running: true,
    created_at: "2026-10-23T10:01:00Z",
    updated_at: "2026-10-23T10:01:00Z",
    messages: [
      {
        id: "q1",
        author: "human",
        content: question,
        steps: [],
        status: "done",
        attachments: [],
      },
    ],
  };
}

function tree() {
  return render(
    <MemoryRouter initialEntries={["/riadiace-centrum"]}>
      <Routes>
        <Route
          path="/riadiace-centrum"
          element={
            <OpytajSaPoradcuBar
              versionId={VERSION}
              projectName="NEX Inbox"
              versionNumber="1.10.1"
            />
          }
        />
        <Route path="/poradca" element={<PoradcaPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

const askButton = () =>
  screen.getByRole("button", { name: /Opýtaj sa Poradcu/ });

beforeEach(() => {
  window.localStorage.clear();
  Object.values(api).forEach((f) => "mockReset" in f && f.mockReset());
  api.buildPoradcaWsUrl.mockReturnValue("ws://test/ws");
  api.getPoradcaStatusApi.mockResolvedValue({
    ready: true,
    problems: [],
    running: 0,
    max_concurrent: 3,
    question_max_chars: 20_000,
  });
  api.getPoradcaContextApi.mockResolvedValue({
    project_id: "p1",
    project_slug: "nex-inbox",
    project_name: "NEX Inbox",
    versions: [
      {
        id: VERSION,
        version_number: "1.10.1",
        stage: "done",
        status: "done",
        instruction_open: false,
        instruction_closed_reason:
          "Verzia je hotová — zmena patrí do novej verzie.",
      },
    ],
  });
  api.listPoradcaConversationsApi.mockResolvedValue([]);
  api.getPoradcaReviewConversationApi.mockResolvedValue({
    conversation_id: null,
  });
  api.createPoradcaConversationApi.mockImplementation(
    async (_slug: string, q: string) => conversation("c1", q),
  );
});

describe("„Opýtaj sa Poradcu“ at a finished version asks", () => {
  it("one click founds the version's conversation with the question about it — nothing to type", async () => {
    tree();

    fireEvent.click(askButton());

    await waitFor(() =>
      expect(api.createPoradcaConversationApi).toHaveBeenCalledTimes(1),
    );
    expect(api.createPoradcaConversationApi).toHaveBeenCalledWith(
      "nex-inbox",
      ASKED,
      VERSION,
      [],
      true,
    );
  });

  it("a second click goes on in the version's conversation instead of founding another", async () => {
    api.getPoradcaReviewConversationApi.mockResolvedValue({
      conversation_id: "c7",
    });
    api.getPoradcaConversationApi.mockResolvedValue({
      ...conversation("c7", "Prvá otázka."),
      running: false,
    });
    api.askPoradcaApi.mockImplementation(async (_id: string, q: string) =>
      conversation("c7", q),
    );
    tree();

    fireEvent.click(askButton());

    await waitFor(() =>
      expect(api.askPoradcaApi).toHaveBeenCalledWith("c7", ASKED, []),
    );
    expect(api.createPoradcaConversationApi).not.toHaveBeenCalled();
  });

  it("when Poradca cannot run, the question waits in the box and the page says why", async () => {
    api.getPoradcaStatusApi.mockResolvedValue({
      ready: false,
      problems: ["chýba obraz"],
      running: 0,
      max_concurrent: 3,
    });
    tree();

    fireEvent.click(askButton());

    expect(
      await screen.findByText("Poradca teraz nevie bežať: chýba obraz"),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByPlaceholderText(/Opýtaj sa Poradcu/)).toHaveValue(
        ASKED,
      ),
    );
    expect(api.createPoradcaConversationApi).not.toHaveBeenCalled();
  });
});
