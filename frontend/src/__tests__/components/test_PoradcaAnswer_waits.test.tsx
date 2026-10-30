/**
 * DEV-264 — an answer in which Poradca asks the person offers nothing it prepared: that waits for the answer.
 * The Director 30.10.2026: „V prípade otázok, najprv treba počkať na moju odpoveď až potom pokračovať.“
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

const WAITS = "Poradca čaká na tvoju odpoveď — čo pripravil, ponúkne až po nej.";
const LONG_CHOICE = `Odporúčam toto.\n\n<volba>${"Agentovi napíš, aby on_setting_change zapisoval aj model. ".repeat(5)}</volba>\n\nSúhlasíš?`;

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
    ...over,
  };
  return render(
    <MemoryRouter>
      <PoradcaAnswer message={message as never} scopeVersion={null} />
    </MemoryRouter>,
  );
}

describe("Poradca waits for the answer to its question (DEV-264)", () => {
  it("under an answer that asks there is no button, only why", () => {
    answer({ content: LONG_CHOICE, held_for_answer: true });

    expect(screen.getByRole("status")).toHaveTextContent(WAITS);
    expect(screen.queryByRole("button", { name: /Vložiť do Riadiaceho centra/ })).not.toBeInTheDocument();
  });

  it("under an answer that does not ask the prepared instruction is offered and nothing is said", () => {
    answer({ instruction: "Doplň zápis modelu do denníka podľa §6.4." });

    expect(screen.getByRole("button", { name: /Vložiť do Riadiaceho centra/ })).toBeInTheDocument();
    expect(screen.queryByText(WAITS)).not.toBeInTheDocument();
  });
});
