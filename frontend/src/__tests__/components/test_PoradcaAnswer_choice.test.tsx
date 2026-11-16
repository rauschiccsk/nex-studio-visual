/**
 * DEV-83 — the choice Poradca tells the manager to make is green, and orange when the card does not offer it.
 * 12.10.2026 the Director: in a long answer the option to pick got lost.
 *
 * DEV-208 — it is drawn once; the summary above the answer showed it a second time.
 * 23.10.2026 the Director: „zbytočne je vypísaná 2x čo treba vybrať (zelená) stačí len raz“.
 *
 * DEV-401 — once, at the end of the answer right above its button; in the text it is plain words.
 * 16.11.2026 the Director: „aby zelený text nebol na začiatku, ale na konci pred tlačidlom“.
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
  it("is green once, at the end of the answer — in the text it is plain words", () => {
    const { container } = answer(CONTENT, [ON_CARD, PARAPHRASE, ANSWER]);

    expect(screen.queryByLabelText("Čo vybrať")).toBeNull();
    const green = container.querySelectorAll('[data-choice="on_card"], [data-choice="answer"]');
    expect([...green].map((b) => b.textContent)).toEqual([
      "karta 2:Zhasnúť hneď, keď sa doručovanie obnoví",
      "Áno, súhlasím",
    ]);
    const summary = screen.getByTestId("poradca-choices");
    for (const badge of green) expect(summary).toContainElement(badge as HTMLElement);
    // The text keeps the words, plain: no badge, no link inside the paragraphs of the answer.
    const sentence = [...container.querySelectorAll("p")].find((p) => p.textContent?.includes("Na karte 2 vyber"));
    expect(sentence?.textContent).toContain("Na karte 2 vyber Zhasnúť hneď, keď sa doručovanie obnoví");
    expect(sentence?.querySelector("[data-choice], a")).toBeNull();
    expect(screen.queryByText(/<volba/)).toBeNull();
  });

  it("is orange once, with the warning beside it at the end, when the card does not offer it", () => {
    const { container } = answer(CONTENT, [ON_CARD, PARAPHRASE, ANSWER]);

    const orange = container.querySelectorAll('[data-choice="not_on_card"]');
    expect([...orange].map((b) => b.textContent)).toEqual(["karta 1:posielať po kúskoch"]);
    expect(orange[0]).toHaveAttribute("title", NOT_ON_CARD_NOTE);
    expect(screen.getAllByText(NOT_ON_CARD_NOTE)).toHaveLength(1);
    const summary = screen.getByTestId("poradca-choices");
    expect(summary).toContainElement(orange[0] as HTMLElement);
    expect(within(summary).getByText(NOT_ON_CARD_NOTE)).toBeInTheDocument();
  });

  it("leaves an answer without a marked choice as it was", () => {
    const { container } = answer("Len odpoveď s [odkazom](https://example.com).", []);

    expect(container.querySelectorAll("[data-choice]")).toHaveLength(0);
    expect(screen.queryByLabelText("Čo vybrať")).toBeNull();
    expect(screen.getByRole("link", { name: "odkazom" })).toHaveAttribute("href", "https://example.com");
  });
});
