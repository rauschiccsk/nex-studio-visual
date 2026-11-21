/**
 * DEV-483 — the summary of the fast path with Poradca: the Staviteľ's proposal and Poradca's beside it, agreements
 * approved in one go, the rest left to the cards.
 *
 * Director 21.11.2026: „Následne mi kompaktne a prehľadne ukazál by otázku a návrh staviteľa a taktiež návrh Poradcu.
 * … mne by dali len súhrn na schválenie. Ak niečim nebudem súhlasiť potom to budem riešiť Staviteľom a Poradcom
 * osobitne.“
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
  render(<BatchReviewSummary board={board} versionId="v1" onBoard={onBoard} />);
  return onBoard;
}

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

  it("an agreement he unchecks stays for the card, and so does every card related to it", async () => {
    api.getBatchReviewApi.mockResolvedValue(review([row(1, "agree"), row(2, "agree", { related: [1] }), row(3, "agree")]));
    summary();

    fireEvent.click(await screen.findByRole("checkbox", { name: "Schváliť kartu 1" }));

    expect(screen.getByRole("checkbox", { name: "Schváliť kartu 2" })).not.toBeChecked();
    expect(screen.getByText("Súvisí s kartou 1, ktorú rozhoduješ sám — rozhodni ju na karte.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Schváliť označené \(1\)/ })).toBeEnabled();
  });

  it("while Poradca answers it says so, and a failure is said instead of a table", async () => {
    api.getBatchReviewApi.mockResolvedValue(review([row(1, "waiting"), row(2, "waiting")], "running"));
    summary();
    expect(await screen.findByText(/Poradca posudzuje naraz všetky karty \(2\)/)).toBeInTheDocument();
  });

  it("a failure is one sentence; the cards wait one by one", async () => {
    api.getBatchReviewApi.mockResolvedValue(review([row(1, "missing")], "failed", "Poradca súhrn nedokončil."));
    summary();
    expect(await screen.findByText("Poradca súhrn nedokončil.")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Súhrn s Poradcom" })).toBeNull();
  });

  it("without cards to decide, or without a review, it shows nothing", async () => {
    api.getBatchReviewApi.mockResolvedValue(null);
    summary();
    await waitFor(() => expect(api.getBatchReviewApi).toHaveBeenCalled());
    expect(screen.queryByRole("region", { name: "Súhrn s Poradcom" })).toBeNull();

    api.getBatchReviewApi.mockClear();
    summary(vi.fn(), { available_actions: [], recent_messages: [] } as unknown as PipelineBoard);
    expect(api.getBatchReviewApi).not.toHaveBeenCalled();
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
