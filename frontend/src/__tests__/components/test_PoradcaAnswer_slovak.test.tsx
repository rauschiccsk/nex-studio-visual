/**
 * DEV-356 — what a person would pass on from an answer is Slovak: under an answer whose block is not, the screen inserts
 * nothing and says why. Director 11.11.2026: „Poradca v NEX Knowledge pokyn pre Staviteľa napísal po anglicky.“
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (sel: (s: unknown) => unknown) =>
    sel({ selectedProject: { slug: "nex-knowledge" }, setSelectedVersion: vi.fn() }),
}));
vi.mock("@/services/api/poradca", () => ({
  stopPoradcaApi: vi.fn(),
  saveRequestToBacklogApi: vi.fn(),
  attachAgentTicketsApi: vi.fn(),
}));

import PoradcaAnswer from "@/components/poradca/PoradcaAnswer";
import { stepLabel } from "@/lib/poradcaAnswer";

const WHY =
  "Kokpit neponúka, čo nie je po slovensky: pokyn pre agenta. Požiadaj o prepísanie po slovensky — cesty a mená z kódu ostanú, ako sú.";
// A long answer marked as a choice — the screen would read it from the text itself and offer it as an instruction.
const LONG_CHOICE = `Odporúčam toto.\n\n<volba>${"Tell the agent to remove only the export keyword and keep the types. ".repeat(4)}</volba>`;

function answer(over: Record<string, unknown>) {
  const message = {
    id: "m-1",
    author: "poradca",
    content: "Odpoveď.",
    steps: [],
    status: "done",
    instruction: null,
    backlog_request: null,
    captured_backlog_number: null,
    choices: [],
    ticket_proposal: null,
    agent_tickets: [],
    plane_identifier: null,
    plane_url: null,
    held_for_answer: false,
    not_in_slovak: null,
    ...over,
  };
  return render(
    <MemoryRouter>
      <PoradcaAnswer message={message as never} scopeVersion={null} />
    </MemoryRouter>,
  );
}

describe("what a person passes on is Slovak (DEV-356)", () => {
  it("under an answer whose block is not Slovak there is no insert, only why — not even a long choice from the text", () => {
    answer({ content: LONG_CHOICE, not_in_slovak: WHY });

    expect(screen.getByRole("status")).toHaveTextContent(WHY);
    expect(screen.queryByRole("button", { name: /Vložiť do Riadiaceho centra/ })).not.toBeInTheDocument();
  });

  it("the same long choice in an answer without the sentence is offered as before", () => {
    answer({ content: LONG_CHOICE });

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Vložiť do Riadiaceho centra/ })).toBeInTheDocument();
  });

  it("the step of the rewrite says what is happening", () => {
    expect(stepLabel("jazyk", "pokyn pre agenta")).toBe("Prepisujem po slovensky — pokyn pre agenta");
  });
});
