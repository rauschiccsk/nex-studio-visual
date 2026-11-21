/**
 * DEV-483 — the summary of the fast path with Poradca: every card with the Staviteľ's proposal and Poradca's beside it.
 *
 * Director 21.11.2026: „Následne mi kompaktne a prehľadne ukazál by otázku a návrh staviteľa a taktiež návrh Poradcu.
 * … mne by dali len súhrn na schválenie. Ak niečim nebudem súhlasiť potom to budem riešiť Staviteľom a Poradcom
 * osobitne.“
 *
 * DEV-486 — the summary is the only place to decide and one button approves every card. Director 21.11.2026: „Rýchla
 * cesta má byť všetky otázky overené poradcom. Chcem videiť na jednom mieste každú jednu otázku, kde je uvedené návrh
 * Staviteľa a návrh Poradcu. Ja to prečítam a ak všetko mi vyhovuje s jednám tlačítkom schválim všetky otázky. Chcem
 * však mať možnosť, že v prípade môjho nesúhlasu podrobne rozobrať a prekonzuľtoať danú otázku. … pri overení Poradca
 * pri cca 80% otázok mal aj nejaké dodatočné informácie pre Staviteľa, tu som to nevidel.“ Before that: „1. Ak je
 * zapnutá rýchla cesta, potom nechcem mať súčasne na obrazovke aj otázky po jednom - je to mätúce.“
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter } from "react-router-dom";

const api = vi.hoisted(() => ({ getBatchReviewApi: vi.fn(), reviewAgainApi: vi.fn(), postPipelineActionApi: vi.fn() }));
vi.mock("@/services/api/batchReview", () => ({
  getBatchReviewApi: api.getBatchReviewApi,
  reviewAgainApi: api.reviewAgainApi,
}));
vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: api.postPipelineActionApi }));

import BatchReviewSummary from "@/components/controlCenter/BatchReviewSummary";
import DecisionCardsBar from "@/components/controlCenter/DecisionCardsBar";
import { approvable } from "@/lib/batchReview";
import type { BatchReviewRow } from "@/services/api/batchReview";
import type { PipelineBoard } from "@/services/api/pipeline";

const BOARD = { available_actions: ["decide"], recent_messages: [] } as unknown as PipelineBoard;
const ONE_BY_ONE = /Treba tvoje rozhodnutie/;

function row(number: number, state: BatchReviewRow["state"], over: Partial<BatchReviewRow> = {}): BatchReviewRow {
  const proposes = state === "agree" || state === "disagree";
  return {
    key: `d${number}`,
    number,
    question: `Otázka ${number}?`,
    builder_label: `A${number}`,
    builder_option_id: "a",
    poradca_label: state === "agree" ? `A${number}` : proposes ? `B${number}` : null,
    poradca_why: proposes ? `Prečo ${number}.` : null,
    poradca_instruction: null,
    state,
    option_id: state === "agree" ? "a" : state === "disagree" ? "b" : null,
    free_text: null,
    decided_label: null,
    related: [],
    ...over,
  };
}

function review(rows: BatchReviewRow[], status: "running" | "done" | "failed" = "done", failure: string | null = null) {
  const counts = { agree: 0, disagree: 0, missing: 0, decided: 0 } as Record<string, number>;
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

beforeEach(() => {
  api.getBatchReviewApi.mockReset();
  api.reviewAgainApi.mockReset();
  api.postPipelineActionApi.mockReset();
});

describe("one place: every card with both proposals and Poradca's instruction (DEV-486)", () => {
  it("shows the question, the Staviteľ's proposal, Poradca's with why and his instruction — no checkboxes", async () => {
    api.getBatchReviewApi.mockResolvedValue(
      review([row(1, "agree"), row(2, "disagree", { poradca_instruction: "Pridaj skúšku na dobropis.\nA aj na prázdny export." })]),
    );
    summary();

    const table = await screen.findByRole("region", { name: "Súhrn s Poradcom" });
    expect(within(table).getByText("zhoda 1 · Poradca inak 1")).toBeInTheDocument();
    const second = within(table).getByText("2. Otázka 2?").closest("li") as HTMLElement;
    expect(second).toHaveTextContent("Staviteľ: A2");
    expect(second).toHaveTextContent("Poradca: B2 — Prečo 2.");
    expect(second).toHaveTextContent("Pokyn pre Staviteľa: Pridaj skúšku na dobropis. A aj na prázdny export.");
    expect(second).toHaveTextContent("⚠ Poradca navrhuje inak");
    expect(within(table).queryAllByRole("checkbox")).toHaveLength(0);
    expect(within(table).getByRole("button", { name: /Schváliť všetko \(2\)/ })).toBeEnabled();
  });

  it("one button approves every card as Poradca proposes, his instruction going with it as the note", async () => {
    api.getBatchReviewApi.mockResolvedValue(
      review([
        row(1, "agree"),
        row(2, "disagree", { poradca_instruction: "Pokyn 2." }),
        row(3, "disagree", { option_id: null, free_text: "Vlastné 3", poradca_label: "Vlastné rozhodnutie: Vlastné 3" }),
      ]),
    );
    const next = { available_actions: ["decide"], recent_messages: [] };
    api.postPipelineActionApi.mockResolvedValue(next);
    const onBoard = summary();

    fireEvent.click(await screen.findByRole("button", { name: /Schváliť všetko \(3\)/ }));

    await waitFor(() => expect(onBoard).toHaveBeenCalledWith(next));
    expect(api.postPipelineActionApi.mock.calls).toEqual([
      ["v1", { action: "decide", payload: { decision_key: "d1", option_id: "a", note: undefined, via: "batch_review" } }],
      ["v1", { action: "decide", payload: { decision_key: "d2", option_id: "b", note: "Pokyn 2.", via: "batch_review" } }],
      ["v1", { action: "decide", payload: { decision_key: "d3", free_text: "Vlastné 3", note: undefined, via: "batch_review" } }],
    ]);
  });
});

describe("a card he does not agree with he consults (DEV-486)", () => {
  it("leaves the approval, opens its card under the row with Poradca's word, and its related card waits", async () => {
    api.getBatchReviewApi.mockResolvedValue(
      review([row(1, "agree"), row(2, "disagree", { poradca_instruction: "Pokyn 2." }), row(3, "agree", { related: [2] })]),
    );
    summary(vi.fn(), consulting(["d1", "d2", "d3"]));

    const table = await screen.findByRole("region", { name: "Súhrn s Poradcom" });
    fireEvent.click(within(table).getByRole("button", { name: "Prekonzultovať kartu 2" }));

    const second = within(table).getByText("2. Otázka 2?").closest("li") as HTMLElement;
    expect(within(second).getByText("Rozhodnutie 2 z 3")).toBeInTheDocument();
    expect(within(second).getByTestId("poradca-proposal")).toHaveTextContent("Poradca navrhuje: B2 — Prečo 2.");
    expect(within(second).getByTestId("poradca-instruction")).toHaveTextContent("Pokyn Poradcu pre Staviteľa: Pokyn 2.");
    expect(within(table).getByText("⏸ čaká na kartu 2")).toBeInTheDocument();
    expect(within(table).getByRole("button", { name: /Schváliť ostatné \(1\)/ })).toBeEnabled();
    expect(screen.queryByText(ONE_BY_ONE)).toBeNull();

    fireEvent.click(within(table).getByRole("button", { name: "Vrátiť do schválenia kartu 2" }));
    expect(within(table).queryByText("Rozhodnutie 2 z 3")).toBeNull();
    expect(within(table).getByRole("button", { name: /Schváliť všetko \(3\)/ })).toBeEnabled();
  });

  it("deciding the consulted card there sends his own decision", async () => {
    api.getBatchReviewApi.mockResolvedValue(review([row(1, "agree"), row(2, "disagree")]));
    const next = { available_actions: ["decide"], recent_messages: [] };
    api.postPipelineActionApi.mockResolvedValue(next);
    const onBoard = summary(vi.fn(), consulting(["d1", "d2"]));

    const table = await screen.findByRole("region", { name: "Súhrn s Poradcom" });
    fireEvent.click(within(table).getByRole("button", { name: "Prekonzultovať kartu 2" }));
    const second = within(table).getByText("2. Otázka 2?").closest("li") as HTMLElement;
    fireEvent.click(within(second).getByRole("button", { name: /^A2/ }));
    fireEvent.click(within(second).getByRole("button", { name: "Rozhodnúť" }));

    await waitFor(() => expect(onBoard).toHaveBeenCalledWith(next));
    expect(api.postPipelineActionApi.mock.calls).toEqual([
      ["v1", { action: "decide", payload: { decision_key: "d2", option_id: "a", note: undefined } }],
    ]);
  });

  it("a card Poradca left out opens for him on its own, and is never approved blindly", async () => {
    api.getBatchReviewApi.mockResolvedValue(review([row(1, "agree"), row(2, "missing")]));
    summary(vi.fn(), consulting(["d1", "d2"]));

    const table = await screen.findByRole("region", { name: "Súhrn s Poradcom" });
    const second = within(table).getByText("2. Otázka 2?").closest("li") as HTMLElement;
    expect(within(second).getByText("Rozhodnutie 2 z 2")).toBeInTheDocument();
    expect(second).toHaveTextContent("Poradca túto kartu neposúdil");
    expect(within(table).getByRole("button", { name: /Schváliť ostatné \(1\)/ })).toBeEnabled();
  });
});

describe("the cards one by one only without a summary (DEV-486)", () => {
  it("with the summary done, the cards one by one are not on the screen", async () => {
    api.getBatchReviewApi.mockResolvedValue(review([row(1, "agree"), row(2, "disagree")]));
    summary(vi.fn(), consulting(["d1", "d2"]));

    await screen.findByRole("region", { name: "Súhrn s Poradcom" });
    expect(screen.queryByText(ONE_BY_ONE)).toBeNull();
    expect(screen.queryByText(/Rozhodnutie \d z 2/)).toBeNull();
  });

  it("while Poradca answers it says so — and no card is on the screen", async () => {
    api.getBatchReviewApi.mockResolvedValue(review([row(1, "waiting"), row(2, "waiting")], "running"));
    summary(vi.fn(), consulting(["d1", "d2"]));
    expect(await screen.findByText(/Poradca posudzuje naraz všetky karty \(2\)/)).toBeInTheDocument();
    expect(screen.queryByText(ONE_BY_ONE)).toBeNull();
    expect(screen.queryByText(/Rozhodnutie 1 z 2/)).toBeNull();
  });

  it("a failure is one sentence with „Posúdiť znova“, and the cards come back one by one so the build never stands", async () => {
    api.getBatchReviewApi.mockResolvedValue(review([row(1, "missing")], "failed", "Poradca súhrn nedokončil."));
    summary(vi.fn(), consulting(["d1"]));
    expect(await screen.findByText("Poradca súhrn nedokončil.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Posúdiť znova/ })).toBeEnabled();
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

describe("„Posúdiť znova“ (DEV-486)", () => {
  it("hands the cards to Poradca again — a summary made before his instructions gets them", async () => {
    api.getBatchReviewApi.mockResolvedValue(review([row(1, "agree"), row(2, "agree")]));
    api.reviewAgainApi.mockResolvedValue(review([row(1, "waiting"), row(2, "waiting")], "running"));
    summary(vi.fn(), consulting(["d1", "d2"]));

    fireEvent.click(await screen.findByRole("button", { name: /Posúdiť znova/ }));

    expect(await screen.findByText(/Poradca posudzuje naraz všetky karty \(2\)/)).toBeInTheDocument();
    expect(api.reviewAgainApi).toHaveBeenCalledWith("v1");
  });

  it("says why when the cards cannot go to Poradca again", async () => {
    api.getBatchReviewApi.mockResolvedValue(review([row(1, "agree")]));
    api.reviewAgainApi.mockRejectedValue(new Error("x"));
    summary(vi.fn(), consulting(["d1"]));

    fireEvent.click(await screen.findByRole("button", { name: /Posúdiť znova/ }));

    expect(await screen.findByText(/Poradcovi sa karty nepodarilo dať znova/)).toBeInTheDocument();
  });
});

describe("the rule of what goes with the approval", () => {
  it("every card with Poradca's proposal goes, minus the consulted and every card related to one — both ways, in a chain", () => {
    const rows = [row(1, "disagree"), row(2, "agree", { related: [1] }), row(3, "agree", { related: [2] }), row(4, "agree")];

    const { keys, heldBy } = approvable(rows, new Set(["d1"]));

    expect([...keys]).toEqual(["d4"]);
    expect(heldBy.get("d2")).toBe(1);
    expect(heldBy.get("d3")).toBe(2);
    expect([...approvable(rows, new Set()).keys]).toEqual(["d1", "d2", "d3", "d4"]);
  });

  it("a decided card neither goes again nor holds the others; a card Poradca left out holds its related ones", () => {
    expect([...approvable([row(1, "decided"), row(2, "agree", { related: [1] })], new Set()).keys]).toEqual(["d2"]);
    expect(approvable([row(1, "missing"), row(2, "agree", { related: [1] })], new Set()).heldBy.get("d2")).toBe(1);
  });
});

describe("the corrections the Staviteľ works in without a card (DEV-487)", () => {
  const CORRECTIONS = ["Doplniť skúšku, že skúška na vzorke nič nedoručí.", "Zladiť typ identifikátora."];

  it("the summary lists them beside the cards — nothing the review found is lost", async () => {
    api.getBatchReviewApi.mockResolvedValue({ ...review([row(1, "agree")]), applied: CORRECTIONS });
    summary();

    const list = await screen.findByRole("region", { name: "Zapracujem bez otázky" });
    expect(within(list).getByText("Zapracujem bez otázky (2)")).toBeInTheDocument();
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual(CORRECTIONS);
    expect(screen.getByRole("button", { name: /Schváliť všetko \(1\)/ })).toBeEnabled();
  });

  it("the cards one by one list them too, and without any there is no list", () => {
    const board = consulting(["d1"]);
    const message = board.recent_messages[0] as unknown as { payload: { consultation: Record<string, unknown> } };
    message.payload.consultation.applied = CORRECTIONS;
    const { unmount } = render(
      <MemoryRouter>
        <DecisionCardsBar board={board} versionId="v1" onBoard={vi.fn()} />
      </MemoryRouter>,
    );
    expect(screen.getByRole("region", { name: "Zapracujem bez otázky" })).toHaveTextContent(CORRECTIONS[0]!);
    unmount();

    render(
      <MemoryRouter>
        <DecisionCardsBar board={consulting(["d1"])} versionId="v1" onBoard={vi.fn()} />
      </MemoryRouter>,
    );
    expect(screen.queryByRole("region", { name: "Zapracujem bez otázky" })).toBeNull();
  });
});

describe("the card under a consulted row carries Poradca's proposal and instruction", () => {
  it("shows Poradca's choice, why and his instruction for the Staviteľ", () => {
    render(
      <MemoryRouter>
        <DecisionCardsBar
          board={consulting(["d1", "d2"])}
          versionId="v1"
          onBoard={vi.fn()}
          proposals={{ d1: row(1, "disagree", { poradca_instruction: "Over aj dobropis." }) }}
        />
      </MemoryRouter>,
    );

    expect(screen.getByTestId("poradca-proposal")).toHaveTextContent("Poradca navrhuje: B1 — Prečo 1.");
    expect(screen.getByTestId("poradca-instruction")).toHaveTextContent("Pokyn Poradcu pre Staviteľa: Over aj dobropis.");
  });
});
