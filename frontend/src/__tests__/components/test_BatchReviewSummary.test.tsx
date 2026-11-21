/**
 * DEV-483 — the summary of the fast path with Poradca: the Staviteľ's proposal and Poradca's beside it, agreements
 * approved in one go, the rest left to the cards.
 *
 * Director 21.11.2026: „Následne mi kompaktne a prehľadne ukazál by otázku a návrh staviteľa a taktiež návrh Poradcu.
 * … mne by dali len súhrn na schválenie. Ak niečim nebudem súhlasiť potom to budem riešiť Staviteľom a Poradcom
 * osobitne.“
 *
 * DEV-486 — while the fast path is on, the summary is the only place to decide. Director 21.11.2026 on NEX Inbox
 * 1.11.0: „1. Ak je zapnutá rýchla cesta, potom nechcem mať súčasne na obrazovke aj otázky po jednom - je to mätúce.
 * 2. V paneli "Súhrn s Poradcom" bod 4. má chck box ale nedá sa naň kliknúť. 3 Bod 5. Nemá check box - prečo, ako
 * budeme riešiť?“
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter } from "react-router-dom";

const api = vi.hoisted(() => ({ getBatchReviewApi: vi.fn(), postPipelineActionApi: vi.fn() }));
vi.mock("@/services/api/batchReview", () => ({ getBatchReviewApi: api.getBatchReviewApi }));
vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: api.postPipelineActionApi }));

import BatchReviewSummary from "@/components/controlCenter/BatchReviewSummary";
import DecisionCardsBar from "@/components/controlCenter/DecisionCardsBar";
import { approvable } from "@/lib/batchReview";
import type { BatchReviewRow } from "@/services/api/batchReview";
import type { PipelineBoard } from "@/services/api/pipeline";

const BOARD = { available_actions: ["decide"], recent_messages: [] } as unknown as PipelineBoard;

function row(number: number, state: BatchReviewRow["state"], over: Partial<BatchReviewRow> = {}): BatchReviewRow {
  return {
    key: `d${number}`,
    number,
    question: `Otázka ${number}?`,
    builder_label: `A${number}`,
    builder_option_id: "a",
    poradca_label: state === "agree" ? `A${number}` : `B${number}`,
    poradca_why: `Prečo ${number}.`,
    state,
    option_id: state === "agree" ? "a" : null,
    decided_label: null,
    related: [],
    ...over,
  };
}

function review(rows: BatchReviewRow[], status: "running" | "done" | "failed" = "done", failure: string | null = null) {
  const counts = { agree: 0, disagree: 0, manager: 0, missing: 0, decided: 0 } as Record<string, number>;
  for (const r of rows) counts[r.state] = (counts[r.state] ?? 0) + 1;
  return { status, failure, conversation_id: null, counts, rows };
}

function summary(onBoard = vi.fn(), board = BOARD) {
  render(
    <MemoryRouter>
      <BatchReviewSummary board={board} versionId="v1" onBoard={onBoard} />
    </MemoryRouter>,
  );
  return onBoard;
}

/** A build standing on the cards of a consultation — the cards one by one would be drawn for it. */
function consulting(keys: string[]): PipelineBoard {
  return {
    available_actions: ["decide"],
    recent_messages: [
      {
        id: "m1",
        version_id: "v1",
        stage: "priprava",
        author: "ai_agent",
        recipient: "manazer",
        kind: "consultation",
        content: "Rozhodnutia.",
        status: "sent",
        seq: 10,
        created_at: "2026-11-21T10:00:00Z",
        payload: {
          consultation: {
            id: "c1",
            source: "priprava",
            decisions: keys.map((key, i) => ({
              key,
              question: `Otázka ${i + 1}?`,
              options: [
                { id: "a", label: `A${i + 1}`, recommended: true },
                { id: "b", label: `B${i + 1}` },
              ],
            })),
          },
        },
      },
    ],
  } as unknown as PipelineBoard;
}

const ONE_BY_ONE = /Treba tvoje rozhodnutie/;

beforeEach(() => {
  api.getBatchReviewApi.mockReset();
  api.postPipelineActionApi.mockReset();
});

describe("the summary of the fast path (DEV-483)", () => {
  it("shows the question with both proposals; only agreements are offered, and pre-checked", async () => {
    api.getBatchReviewApi.mockResolvedValue(review([row(1, "agree"), row(2, "disagree"), row(3, "manager")]));
    summary();

    const table = await screen.findByRole("region", { name: "Súhrn s Poradcom" });
    expect(within(table).getByText("zhoda 1 · nezhoda 1 · rozhoduješ ty 1")).toBeInTheDocument();
    const second = within(table).getByText("2. Otázka 2?").closest("li") as HTMLElement;
    expect(second).toHaveTextContent("Staviteľ: A2");
    expect(second).toHaveTextContent("Poradca: B2 — Prečo 2.");
    expect(second).toHaveTextContent("⚠ nezhoda");
    expect(within(table).getAllByRole("checkbox")).toHaveLength(1);
    expect(within(table).getByRole("checkbox", { name: "Schváliť kartu 1" })).toBeChecked();
    expect(within(table).getByRole("button", { name: /Schváliť označené \(1\)/ })).toBeEnabled();
  });

  it("approves the checked agreements in one go through decide, marked as the summary's", async () => {
    api.getBatchReviewApi.mockResolvedValue(review([row(1, "agree"), row(2, "agree"), row(3, "disagree")]));
    const next = { available_actions: ["decide"], recent_messages: [] };
    api.postPipelineActionApi.mockResolvedValue(next);
    const onBoard = summary();

    fireEvent.click(await screen.findByRole("button", { name: /Schváliť označené \(2\)/ }));

    await waitFor(() => expect(onBoard).toHaveBeenCalledWith(next));
    expect(api.postPipelineActionApi.mock.calls).toEqual([
      ["v1", { action: "decide", payload: { decision_key: "d1", option_id: "a", via: "batch_review" } }],
      ["v1", { action: "decide", payload: { decision_key: "d2", option_id: "a", via: "batch_review" } }],
    ]);
  });

  it("an agreement he unchecks is his to decide, and every card related to it waits — without a checkbox", async () => {
    api.getBatchReviewApi.mockResolvedValue(review([row(1, "agree"), row(2, "agree", { related: [1] }), row(3, "agree")]));
    summary();

    fireEvent.click(await screen.findByRole("checkbox", { name: "Schváliť kartu 1" }));

    expect(screen.queryByRole("checkbox", { name: "Schváliť kartu 2" })).toBeNull();
    expect(screen.getByText("⏸ čaká na kartu 1")).toBeInTheDocument();
    expect(screen.getByText("Súvisí s kartou 1 — zaškrtne sa, keď kartu 1 rozhodneš.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Schváliť označené \(1\)/ })).toBeEnabled();
  });

  it("while Poradca answers it says so — and no card one by one is on the screen", async () => {
    api.getBatchReviewApi.mockResolvedValue(review([row(1, "waiting"), row(2, "waiting")], "running"));
    summary(vi.fn(), consulting(["d1", "d2"]));
    expect(await screen.findByText(/Poradca posudzuje naraz všetky karty \(2\)/)).toBeInTheDocument();
    expect(screen.queryByText(ONE_BY_ONE)).toBeNull();
    expect(screen.queryByText(/Rozhodnutie 1 z 2/)).toBeNull();
  });

  it("a failure is one sentence, and the cards come back one by one so the build never stands", async () => {
    api.getBatchReviewApi.mockResolvedValue(review([row(1, "missing")], "failed", "Poradca súhrn nedokončil."));
    summary(vi.fn(), consulting(["d1"]));
    expect(await screen.findByText("Poradca súhrn nedokončil.")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Súhrn s Poradcom" })).toBeNull();
    expect(screen.getByText(ONE_BY_ONE)).toBeInTheDocument();
  });

  it("without a summary the cards come one by one as before; without cards to decide it asks nothing", async () => {
    api.getBatchReviewApi.mockResolvedValue(null);
    summary(vi.fn(), consulting(["d1"]));
    expect(await screen.findByText(ONE_BY_ONE)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Súhrn s Poradcom" })).toBeNull();

    api.getBatchReviewApi.mockClear();
    summary(vi.fn(), { available_actions: [], recent_messages: [] } as unknown as PipelineBoard);
    expect(api.getBatchReviewApi).not.toHaveBeenCalled();
  });
});

describe("the summary is the only place to decide (DEV-486)", () => {
  it("with the summary done, the cards one by one are not on the screen", async () => {
    api.getBatchReviewApi.mockResolvedValue(review([row(1, "agree"), row(2, "agree")]));
    summary(vi.fn(), consulting(["d1", "d2"]));

    await screen.findByRole("region", { name: "Súhrn s Poradcom" });
    expect(screen.queryByText(ONE_BY_ONE)).toBeNull();
    expect(screen.queryByText(/Rozhodnutie \d z 2/)).toBeNull();
  });

  it("a card he decides himself opens under its row, and deciding it there sends his decision", async () => {
    api.getBatchReviewApi.mockResolvedValue(review([row(1, "agree"), row(2, "manager"), row(3, "disagree")]));
    const next = { available_actions: ["decide"], recent_messages: [] };
    api.postPipelineActionApi.mockResolvedValue(next);
    const onBoard = summary(vi.fn(), consulting(["d1", "d2", "d3"]));

    const table = await screen.findByRole("region", { name: "Súhrn s Poradcom" });
    const second = within(table).getByText("2. Otázka 2?").closest("li") as HTMLElement;
    expect(within(second).getByText("Rozhodnutie 2 z 3")).toBeInTheDocument();
    expect(within(second).getByTestId("poradca-proposal")).toHaveTextContent("Poradca navrhuje: B2 — Prečo 2.");
    const third = within(table).getByText("3. Otázka 3?").closest("li") as HTMLElement;
    expect(third).toHaveTextContent("Rozhodneš ju tu po karte 2.");
    expect(within(third).queryByText(/Rozhodnutie 3 z 3/)).toBeNull();
    expect(screen.queryByText(ONE_BY_ONE)).toBeNull();

    fireEvent.click(within(second).getByRole("button", { name: /^B2/ }));
    fireEvent.click(within(second).getByRole("button", { name: "Rozhodnúť" }));

    await waitFor(() => expect(onBoard).toHaveBeenCalledWith(next));
    expect(api.postPipelineActionApi.mock.calls).toEqual([
      ["v1", { action: "decide", payload: { decision_key: "d2", option_id: "b", note: undefined } }],
    ]);
  });

  it("a card waiting for another has no checkbox, says which card, and is checked once that card is decided", async () => {
    api.getBatchReviewApi.mockResolvedValueOnce(review([row(1, "manager"), row(2, "agree", { related: [1] })]));
    const board = consulting(["d1", "d2"]);
    const { rerender } = render(
      <MemoryRouter>
        <BatchReviewSummary board={board} versionId="v1" onBoard={vi.fn()} />
      </MemoryRouter>,
    );

    const table = await screen.findByRole("region", { name: "Súhrn s Poradcom" });
    expect(within(table).queryByRole("checkbox", { name: "Schváliť kartu 2" })).toBeNull();
    expect(within(table).getByText("⏸ čaká na kartu 1")).toBeInTheDocument();
    expect(within(table).getByRole("button", { name: /Schváliť označené \(0\)/ })).toBeDisabled();

    api.getBatchReviewApi.mockResolvedValueOnce(
      review([row(1, "decided", { decided_label: "B1" }), row(2, "agree", { related: [1] })]),
    );
    const decided = { ...board, recent_messages: [...board.recent_messages, { id: "m2", seq: 11 }] } as unknown as PipelineBoard;
    rerender(
      <MemoryRouter>
        <BatchReviewSummary board={decided} versionId="v1" onBoard={vi.fn()} />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("checkbox", { name: "Schváliť kartu 2" })).toBeChecked();
    expect(screen.queryByText("⏸ čaká na kartu 1")).toBeNull();
    expect(screen.getByRole("button", { name: /Schváliť označené \(1\)/ })).toBeEnabled();
  });
});

describe("the rule of what goes with the approval", () => {
  it("relations hold in both directions and through a chain", () => {
    const rows = [row(1, "disagree"), row(2, "agree"), row(3, "agree", { related: [2] }), row(4, "agree")];
    rows[1] = { ...rows[1], related: [1] } as BatchReviewRow;

    const { keys, heldBy } = approvable(rows, new Set());

    expect([...keys]).toEqual(["d4"]);
    expect(heldBy.get("d2")).toBe(1);
    expect(heldBy.get("d3")).toBe(2);
  });

  it("a decided card neither goes again nor holds the others", () => {
    const { keys } = approvable([row(1, "decided"), row(2, "agree", { related: [1] })], new Set());
    expect([...keys]).toEqual(["d2"]);
  });
});

describe("the card left from the summary carries Poradca's proposal", () => {
  it("shows Poradca's choice and why under the card's question", () => {
    const board = {
      available_actions: ["decide"],
      recent_messages: [
        {
          id: "m1",
          version_id: "v1",
          stage: "navrh",
          author: "ai_agent",
          recipient: "manazer",
          kind: "consultation",
          content: "Rozhodnutia.",
          status: "sent",
          seq: 10,
          created_at: "2026-11-21T10:00:00Z",
          payload: {
            consultation: {
              id: "c1",
              source: "auditor_upfront",
              decisions: [
                {
                  key: "d2",
                  question: "Otázka 2?",
                  options: [
                    { id: "a", label: "A2", recommended: true },
                    { id: "b", label: "B2" },
                  ],
                },
              ],
            },
          },
        },
      ],
    } as unknown as PipelineBoard;
    render(
      <MemoryRouter>
        <DecisionCardsBar board={board} versionId="v1" onBoard={vi.fn()} proposals={{ d2: row(2, "disagree") }} />
      </MemoryRouter>,
    );

    expect(screen.getByTestId("poradca-proposal")).toHaveTextContent("Poradca navrhuje: B2 — Prečo 2.");
  });
});
