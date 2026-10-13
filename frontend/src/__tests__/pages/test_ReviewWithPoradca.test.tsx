/**
 * DEV-103 — the review-with-Poradca button on the verification decision card: Poradca opens on the build's version
 * and the question composed from the card goes out by itself.
 *
 * Director 13.10.2026: he copied the card's heading into Poradca by hand and typed the same request under it every
 * time — needless work that could be automated.
 *
 * These tests walk his path through the REAL card and the REAL Poradca page; only the API is a mock.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

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
  buildPoradcaWsUrl: vi.fn(() => "ws://test/ws"),
  getPoradcaAttachmentApi: vi.fn(),
}));
vi.mock("@/services/api/poradca", () => api);

const ctx = vi.hoisted(() => ({
  state: {
    selectedProject: { slug: "nex-inbox", name: "NEX Inbox" },
    selectedVersion: { versionId: "v180", versionNumber: "1.8.0" },
    setSelectedVersion: vi.fn(),
  },
}));
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: unknown) => unknown) => sel(ctx.state),
}));
vi.mock("@/store/authStore", () => ({
  useAuthStore: (sel: (s: unknown) => unknown) => sel({ token: "t", user: { id: "me", username: "zoltan" } }),
}));

class SilentSocket {
  onmessage: ((m: { data: string }) => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  close() {}
}
vi.stubGlobal("WebSocket", SilentSocket);

import DecisionCardsBar from "@/components/riadiace/DecisionCardsBar";
import { handOffReview } from "@/lib/poradcaReview";
import PoradcaPage from "@/pages/PoradcaPage";
import type { PipelineBoard } from "@/services/api/pipeline";

const VERSION = "v180";
const REQUEST = "Prekontroluj opodstatnenosť tohto nálezu a ako to vyriešiť.";
const QUESTION = "Verifikácia našla blokujúcu chybu. Ako chceš pokračovať?";
const EXPLANATION = "Kontrola CI na GitHube je červená — RELEASE_NOTES generated-file check.";
const CRITIC = "Navrhnutú opravu nezávisle PREVERIL kritik (accept — je vynútená konštrukciou).";
const FINDING = "- CI aee11b5: RELEASE_NOTES generated-file check zlyhal";
const MY_DRAFT = "moja rozpísaná otázka";
const NEW_DRAFT_KEY = "nex.draft.poradca.new.nex-inbox";

function card(source: string, technicalDetail = `## Verifikácia FAIL\n\n${FINDING}`): PipelineBoard {
  return {
    available_actions: ["decide"],
    state: { status: "blocked", block_reason: "decision_needed", current_stage: "verifikacia" },
    recent_messages: [
      {
        id: "m1",
        version_id: VERSION,
        stage: "verifikacia",
        author: "ai_partner",
        recipient: "manazer",
        kind: "consultation",
        content: "Verifikácia našla chybu.",
        status: "sent",
        seq: 4594,
        created_at: "2026-10-13T10:00:00Z",
        payload: {
          consultation: {
            id: "verifikacia-fix-v180-4",
            source,
            round: 4,
            intro: "Verifikácia našla chybu — potrebné je tvoje rozhodnutie.",
            decisions: [
              {
                key: "verifikacia_fix_next",
                question: QUESTION,
                origin: "objav",
                explanation: `${EXPLANATION}\n\n${CRITIC}`,
                technical_detail: technicalDetail,
                options: [
                  { id: "accept_fix", label: "Spustiť pripravenú opravu", recommended: true },
                  { id: "guide", label: "Usmerniť opravu (napíš vlastný pokyn)" },
                  { id: "hold", label: "Zatiaľ podržať (rozhodnem neskôr)" },
                ],
                allow_free_text: true,
              },
            ],
          },
        },
      },
    ],
  } as unknown as PipelineBoard;
}

function conversation(question: string) {
  return {
    id: "c9",
    project_id: "p1",
    version_id: VERSION,
    version_number: "1.8.0",
    author_id: "me",
    author_name: "Zoltán",
    title: "Verifikácia našla blokujúcu chybu",
    running: true,
    created_at: "2026-10-13T10:01:00Z",
    updated_at: "2026-10-13T10:01:00Z",
    messages: [{ id: "q1", author: "human", content: question, steps: [], status: "done", attachments: [] }],
  };
}

/** The address Poradca stands on — a mark left in it would send a later card again on the way back. */
function Address() {
  return <output data-testid="address">{useLocation().search}</output>;
}

function tree(board: PipelineBoard, start = "/riadiace-centrum") {
  return render(
    <MemoryRouter initialEntries={[start]}>
      <Routes>
        <Route
          path="/riadiace-centrum"
          element={<DecisionCardsBar board={board} versionId={VERSION} onBoard={vi.fn()} />}
        />
        <Route
          path="/poradca"
          element={
            <>
              <PoradcaPage />
              <Address />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

/** Poradca's status answering after the project did — the order a slow server gives. */
function statusLater(status: Record<string, unknown>) {
  api.getPoradcaStatusApi.mockImplementation(() => new Promise((r) => setTimeout(() => r(status), 40)));
}

const reviewButton = () => screen.getByRole("button", { name: /Overiť s Poradcom/ });
const composer = () => screen.findByPlaceholderText(/Opýtaj sa Poradcu/) as Promise<HTMLTextAreaElement>;

beforeEach(() => {
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
        version_number: "1.8.0",
        stage: "verifikacia",
        status: "blocked",
        instruction_open: true,
        instruction_closed_reason: null,
      },
    ],
  });
  api.listPoradcaConversationsApi.mockResolvedValue([]);
  api.createPoradcaConversationApi.mockImplementation(async (_slug: string, question: string) =>
    conversation(question),
  );
  api.getPoradcaConversationApi.mockImplementation(async () => conversation("…"));
  window.localStorage.clear();
});

describe("DEV-103 — the review-with-Poradca button on the verification card", () => {
  it("opens Poradca on the build's version and sends the card's question by itself", async () => {
    tree(card("verifikacia_fix"));
    fireEvent.click(reviewButton());

    await waitFor(() => expect(api.createPoradcaConversationApi).toHaveBeenCalledTimes(1));
    const [slug, question, versionId, images] = api.createPoradcaConversationApi.mock.lastCall ?? [];
    expect([slug, versionId, images]).toEqual(["nex-inbox", VERSION, []]);
    expect(question.startsWith(`Rozhodnutie 1 z 1 — ${QUESTION}\nKolo opráv 4.`)).toBe(true);
    for (const part of [EXPLANATION, CRITIC, FINDING, "Spustiť pripravenú opravu (odporúčané)"]) {
      expect(question).toContain(part);
    }
    expect(question.endsWith(`\n\n${REQUEST}`)).toBe(true);
    // The conversation opens with his question in it — nothing to copy, nothing to press.
    expect(await screen.findByText(/Prekontroluj opodstatnenosť/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("address").textContent).toBe(`?verzia=${VERSION}&c=c9`));
  });

  it("the question goes out once — coming back to Poradca on that version sends nothing more", async () => {
    const { unmount } = tree(card("verifikacia_fix"));
    fireEvent.click(reviewButton());
    await waitFor(() => expect(api.createPoradcaConversationApi).toHaveBeenCalledTimes(1));
    unmount();

    tree(card("verifikacia_fix"), `/poradca?verzia=${VERSION}`); // „Opýtaj sa Poradcu" leads here the same way
    expect(await composer()).toHaveValue("");
    await new Promise((r) => setTimeout(r, 50));
    expect(api.createPoradcaConversationApi).toHaveBeenCalledTimes(1);
  });

  it("a card never taken is not sent by a later visit that did not come from the button", async () => {
    handOffReview(VERSION, { position: 1, total: 1, round: 2, question: QUESTION, options: [] });

    tree(card("verifikacia_fix"), `/poradca?verzia=${VERSION}`);

    expect(await composer()).toHaveValue("");
    await new Promise((r) => setTimeout(r, 50));
    expect(api.createPoradcaConversationApi).not.toHaveBeenCalled();
  });

  it("fits the question into the limit Poradca reports — even when that answer comes last", async () => {
    statusLater({ ready: true, problems: [], running: 0, max_concurrent: 3, question_max_chars: 3_000 });
    tree(card("verifikacia_fix", `## Verifikácia FAIL\n\n${`${FINDING}\n`.repeat(200)}`));
    fireEvent.click(reviewButton());

    await waitFor(() => expect(api.createPoradcaConversationApi).toHaveBeenCalledTimes(1));
    const question: string = api.createPoradcaConversationApi.mock.lastCall?.[1] ?? "";
    expect(question.length).toBeLessThanOrEqual(3_000);
    expect(question.endsWith(REQUEST)).toBe(true);
  });

  it("Poradca cannot run: the question waits in the box under his own draft, not sent", async () => {
    window.localStorage.setItem(NEW_DRAFT_KEY, MY_DRAFT);
    statusLater({ ready: false, problems: ["chýba obraz"], running: 0, max_concurrent: 3, question_max_chars: 20_000 });
    tree(card("verifikacia_fix"));
    fireEvent.click(reviewButton());

    expect(await screen.findByText("Poradca teraz nevie bežať: chýba obraz")).toBeInTheDocument();
    const box = await composer();
    await waitFor(() => expect(box.value.startsWith(`${MY_DRAFT}\n\nRozhodnutie 1 z 1 — ${QUESTION}`)).toBe(true));
    expect(box.value.endsWith(REQUEST)).toBe(true);
    expect(api.createPoradcaConversationApi).not.toHaveBeenCalled();
    // The card's visit is over even though nothing went out — the way back must not take a later card.
    await waitFor(() => expect(screen.getByTestId("address").textContent).toBe(`?verzia=${VERSION}`));
  });

  it("a refused send leaves the question in the box with the reason, under his own draft", async () => {
    window.localStorage.setItem(NEW_DRAFT_KEY, MY_DRAFT);
    api.createPoradcaConversationApi.mockRejectedValue(new Error("Poradca je preťažený"));
    tree(card("verifikacia_fix"));
    fireEvent.click(reviewButton());

    const box = await composer();
    await waitFor(() => expect(box.value.startsWith(`${MY_DRAFT}\n\nRozhodnutie 1 z 1 — ${QUESTION}`)).toBe(true));
    expect(screen.getByText(/Otázku sa nepodarilo poslať/)).toBeInTheDocument();
  });

  it("a sent question leaves his own half-written question where it was", async () => {
    window.localStorage.setItem(NEW_DRAFT_KEY, MY_DRAFT);
    tree(card("verifikacia_fix"));
    fireEvent.click(reviewButton());

    await waitFor(() => expect(api.createPoradcaConversationApi).toHaveBeenCalledTimes(1));
    await screen.findByText(/Prekontroluj opodstatnenosť/);
    expect(window.localStorage.getItem(NEW_DRAFT_KEY)).toBe(MY_DRAFT);
  });

  it("a card of a version this project lacks is not sent anywhere — it waits in the box, with why", async () => {
    api.getPoradcaContextApi.mockResolvedValue({
      project_id: "p2",
      project_slug: "nex-inbox",
      project_name: "NEX Inbox",
      versions: [],
    });
    tree(card("verifikacia_fix"));
    fireEvent.click(reviewButton());

    const box = await composer();
    await waitFor(() => expect(box.value.startsWith(`Rozhodnutie 1 z 1 — ${QUESTION}`)).toBe(true));
    expect(screen.getByText(/verzia karty v tomto projekte nie je/i)).toBeInTheDocument();
    expect(api.createPoradcaConversationApi).not.toHaveBeenCalled();
  });

  it("the button is on the verification card only — other cards keep their own questions", () => {
    tree(card("auditor_upfront"));
    expect(screen.getByText(QUESTION)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Overiť s Poradcom/ })).not.toBeInTheDocument();
  });
});
