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
const RATIONALE = "Odporúčam spustiť pripravenú opravu — nezávislý kritik ju preveril.";
const ORIGIN = "Nález, ktorý tu bol vždy — nevznikol z tvojho rozhodnutia.";
const MY_DRAFT = "moja rozpísaná otázka";
const NEW_DRAFT_KEY = "nex.draft.poradca.new.nex-inbox";

function card(
  source: string,
  technicalDetail = `## Verifikácia FAIL\n\n${FINDING}`,
  roundMax: number | null = null,
): PipelineBoard {
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
            round_max: roundMax,
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
                rationale: RATIONALE,
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
  api.getPoradcaReviewConversationApi.mockResolvedValue({ conversation_id: null });
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
    for (const part of [ORIGIN, EXPLANATION, CRITIC, FINDING, "Spustiť pripravenú opravu (odporúčané)", RATIONALE]) {
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

    tree(card("verifikacia_fix"), `/poradca?verzia=${VERSION}`); // a visit that did not come from a button
    expect(await composer()).toHaveValue("");
    await new Promise((r) => setTimeout(r, 50));
    expect(api.createPoradcaConversationApi).toHaveBeenCalledTimes(1);
  });

  it("a card never taken is not sent by a later visit that did not come from the button", async () => {
    handOffReview(VERSION, {
      position: 1,
      total: 1,
      round: 2,
      roundMax: null,
      request: REQUEST,
      question: QUESTION,
      options: [],
    });

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

  // DEV-106 (Director 13.10.2026 approved the requests): every card has the button; a card the agent prepared asks
  // which option to choose, a card with the Auditor's findings asks to check the finding.
  it.each([
    ["the Auditor's upfront review", "auditor_upfront", REQUEST],
    ["a contradiction with the Vizuál", "vizual_conflict", "Poraď mi, ktorú možnosť zvoliť a prečo."],
    ["the agent's own question", "agent_ambiguity", "Poraď mi, ktorú možnosť zvoliť a prečo."],
  ])("every card has the button — %s ends with its own request", async (_kind, source, request) => {
    tree(card(source, undefined, 5));
    fireEvent.click(reviewButton());

    await waitFor(() => expect(api.createPoradcaConversationApi).toHaveBeenCalledTimes(1));
    const question: string = api.createPoradcaConversationApi.mock.lastCall?.[1] ?? "";
    expect(question.startsWith(`Rozhodnutie 1 z 1 — ${QUESTION}\nKolo konzultácie 4 z 5.\n${ORIGIN}`)).toBe(true);
    expect(question.endsWith(`\n\n${request}`)).toBe(true);
  });
});

// DEV-138 — Director 16.10.2026, NEX Inbox 1.9.0 waiting in Návrh on 9 cards: „ak kliknem v karte agenta na tlačidlo
// "Overiť s Poradcom" každý krát otvorí nový rozhovor. Takou rýchlosťou o chvíľku budem mať zahlcený celý zoznam
// rozhovorov.“ The cards of one version and phase go into ONE conversation — the one the first card founded.
describe("DEV-138 — the cards of one version and phase share one conversation", () => {
  const PHASE_CONVERSATION = "c-navrh";

  function phaseConversation(running: boolean) {
    return {
      ...conversation("Rozhodnutie 1 z 9 — prvá karta"),
      id: PHASE_CONVERSATION,
      title: "1.8.0 — rozhodnutia vo Verifikácii",
      running,
      messages: [
        { id: "q1", author: "human", content: "Rozhodnutie 1 z 9 — prvá karta", steps: [], status: "done", attachments: [] },
        {
          id: "a1",
          author: "poradca",
          content: "Odporúčam možnosť A.",
          steps: [],
          status: running ? "running" : "done",
          attachments: [],
        },
      ],
    };
  }

  it("a card of a phase that already has its conversation goes into it — no new conversation", async () => {
    api.getPoradcaReviewConversationApi.mockResolvedValue({ conversation_id: PHASE_CONVERSATION });
    api.getPoradcaConversationApi.mockImplementation(async () => phaseConversation(false));
    api.askPoradcaApi.mockImplementation(async () => phaseConversation(true));
    tree(card("verifikacia_fix"));
    fireEvent.click(reviewButton());

    await waitFor(() => expect(api.askPoradcaApi).toHaveBeenCalledTimes(1));
    const [id, question] = api.askPoradcaApi.mock.lastCall ?? [];
    expect(id).toBe(PHASE_CONVERSATION);
    expect(question.startsWith(`Rozhodnutie 1 z 1 — ${QUESTION}`)).toBe(true);
    expect(question.endsWith(`\n\n${REQUEST}`)).toBe(true);
    expect(api.getPoradcaReviewConversationApi).toHaveBeenCalledWith("nex-inbox", VERSION);
    expect(api.createPoradcaConversationApi).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.getByTestId("address").textContent).toBe(`?verzia=${VERSION}&c=${PHASE_CONVERSATION}`),
    );
  });

  it("the first card of a phase founds the phase's conversation", async () => {
    tree(card("verifikacia_fix"));
    fireEvent.click(reviewButton());

    await waitFor(() => expect(api.createPoradcaConversationApi).toHaveBeenCalledTimes(1));
    const [slug, , versionId, images, review] = api.createPoradcaConversationApi.mock.lastCall ?? [];
    expect([slug, versionId, images, review]).toEqual(["nex-inbox", VERSION, [], true]);
    expect(api.askPoradcaApi).not.toHaveBeenCalled();
  });

  it("Poradca still answers the previous card: this card waits in the box of that conversation, nothing new", async () => {
    api.getPoradcaReviewConversationApi.mockResolvedValue({ conversation_id: PHASE_CONVERSATION });
    // The conversation arrives after the address changed — the order a slow server gives; the card must wait for it.
    api.getPoradcaConversationApi.mockImplementation(
      () => new Promise((r) => setTimeout(() => r(phaseConversation(true)), 40)),
    );
    tree(card("verifikacia_fix"));
    fireEvent.click(reviewButton());

    expect(await screen.findByText(/Poradca odpovedá — ďalšiu otázku pošleš, keď dokončí/)).toBeInTheDocument();
    const box = await composer();
    await waitFor(() => expect(box.value.startsWith(`Rozhodnutie 1 z 1 — ${QUESTION}`)).toBe(true));
    expect(box.value.endsWith(REQUEST)).toBe(true);
    await new Promise((r) => setTimeout(r, 50));
    expect(api.askPoradcaApi).not.toHaveBeenCalled();
    expect(api.createPoradcaConversationApi).not.toHaveBeenCalled();
  });

  it("the phase's conversation cannot be opened: the card founds a new one instead of getting lost", async () => {
    api.getPoradcaReviewConversationApi.mockResolvedValue({ conversation_id: PHASE_CONVERSATION });
    api.getPoradcaConversationApi.mockRejectedValue(new Error("Rozhovor sa nenašiel."));
    tree(card("verifikacia_fix"));
    fireEvent.click(reviewButton());

    await waitFor(() => expect(api.createPoradcaConversationApi).toHaveBeenCalledTimes(1));
    const [, question, , , review] = api.createPoradcaConversationApi.mock.lastCall ?? [];
    expect(question.startsWith(`Rozhodnutie 1 z 1 — ${QUESTION}`)).toBe(true);
    expect(review).toBe(true);
    expect(api.askPoradcaApi).not.toHaveBeenCalled();
  });

  it("the server cannot say where the card goes: it founds the phase's conversation rather than waiting", async () => {
    api.getPoradcaReviewConversationApi.mockRejectedValue(new Error("Server neodpovedá"));
    tree(card("verifikacia_fix"));
    fireEvent.click(reviewButton());

    await waitFor(() => expect(api.createPoradcaConversationApi).toHaveBeenCalledTimes(1));
    expect(api.createPoradcaConversationApi.mock.lastCall?.[4]).toBe(true);
  });
});
