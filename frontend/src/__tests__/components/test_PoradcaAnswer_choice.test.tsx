/**
 * DEV-83 — the choice Poradca tells the manager to make is green, summed up above the answer, and orange when the card
 * does not offer it. 12.10.2026 the Director: in a long answer the option to pick got lost.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

import PoradcaAnswer from "@/components/poradca/PoradcaAnswer";
import { NOT_ON_CARD_NOTE } from "@/components/poradca/ChoiceBadge";
import type { PoradcaChoice } from "@/types/poradca";

vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: unknown) => unknown) =>
    sel({ selectedProject: { slug: "nex-inbox" }, setSelectedVersion: vi.fn() }),
}));
vi.mock("@/services/api/poradca", () => ({ stopPoradcaApi: vi.fn(), saveRequestToBacklogApi: vi.fn() }));

const ON_CARD: PoradcaChoice = {
  card: 2,
  label: "Zhasnúť hneď, keď sa doručovanie obnoví",
  status: "on_card",
  question: "Kedy zhasne upozornenie?",
};
const PARAPHRASE: PoradcaChoice = { card: 1, label: "posielať po kúskoch", status: "not_on_card", question: "Q" };
const ANSWER: PoradcaChoice = { card: null, label: "Áno, súhlasím", status: "answer", question: null };

function answer(content: string, choices: PoradcaChoice[]) {
  const message = {
    id: "m1",
    author: "poradca",
    content,
    steps: [],
    status: "done",
    instruction: null,
    backlog_request: null,
    captured_backlog_number: null,
    choices,
  };
  return render(
    <MemoryRouter>
      <PoradcaAnswer message={message as never} scopeVersion={null} />
    </MemoryRouter>,
  );
}

const CONTENT =
  'Agent má plán.\n\nNa karte 2 vyber <volba karta="2">Zhasnúť hneď, keď sa doručovanie obnoví</volba>.\n' +
  'Na karte 1 <volba karta="1">posielať po kúskoch</volba>.\n\nAgentovi odpovedz <volba>Áno, súhlasím</volba>.';

describe("Poradca's marked choice (DEV-83)", () => {
  it("is green in the text and summed up above the answer", () => {
    const { container } = answer(CONTENT, [ON_CARD, PARAPHRASE, ANSWER]);

    const summary = screen.getByLabelText("Čo vybrať");
    expect(within(summary).getAllByText("Vyber")).toHaveLength(2);
    expect(within(summary).getByText("Odpovedz agentovi")).toBeInTheDocument();
    const green = container.querySelectorAll('[data-choice="on_card"], [data-choice="answer"]');
    // in the summary and in the text, each green choice twice
    expect([...green].map((b) => b.textContent)).toEqual([
      "karta 2:Zhasnúť hneď, keď sa doručovanie obnoví",
      "Áno, súhlasím",
      "karta 2:Zhasnúť hneď, keď sa doručovanie obnoví",
      "Áno, súhlasím",
    ]);
    expect(screen.queryByText(/<volba/)).toBeNull();
  });

  it("is orange with a warning when the card does not offer it", () => {
    const { container } = answer(CONTENT, [ON_CARD, PARAPHRASE, ANSWER]);

    const orange = container.querySelectorAll('[data-choice="not_on_card"]');
    expect([...orange].map((b) => b.textContent)).toEqual(["karta 1:posielať po kúskoch", "karta 1:posielať po kúskoch"]);
    expect(orange[0]).toHaveAttribute("title", NOT_ON_CARD_NOTE);
    expect(screen.getByText(NOT_ON_CARD_NOTE)).toBeInTheDocument();
  });

  it("leaves an answer without a marked choice as it was", () => {
    const { container } = answer("Len odpoveď s [odkazom](https://example.com).", []);

    expect(container.querySelectorAll("[data-choice]")).toHaveLength(0);
    expect(screen.queryByLabelText("Čo vybrať")).toBeNull();
    expect(screen.getByRole("link", { name: "odkazom" })).toHaveAttribute("href", "https://example.com");
  });
});
