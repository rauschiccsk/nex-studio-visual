/**
 * DEV-221 — sent while the Decision Cards wait, Dedo's „Vrátiť agentovi na doplnenie“ says what it does to them.
 *
 * 24.10.2026 Author Asistent 0.1.0: the Director sent Dedo's proposal in the middle of the cards; the bar said
 * „AI Agent dostane tento text ako pokyn a vráti sa k rozrobenej práci — nič sa nezačína odznova“, and the cards
 * he had decided never reached the agent. The engine now takes them along; the bar says so before the click.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import DedoProposalBar from "@/components/riadiace/DedoProposalBar";
import type { PipelineBoard } from "@/services/api/pipeline";

vi.mock("@/services/api/pipeline", () => ({
  sendDedoProposalApi: vi.fn(),
  rejectDedoProposalApi: vi.fn(),
  getPipelineBoardApi: vi.fn(),
}));
vi.mock("@/hooks/useOpenVersionCockpit", () => ({ useOpenVersionCockpit: () => vi.fn() }));

const AT_THE_CARDS =
  "Otvorené karty rozhodnutia sa tým zrušia. Karty, ktoré si už rozhodol, dostane AI Agent spolu s týmto textom " +
  "a znovu sa na ne nepýta; na ostatné sa ďalšie kolo spýta, len ak ešte platia.";
const ORDINARY = "AI Agent dostane tento text ako pokyn a vráti sa k rozrobenej práci — nič sa nezačína odznova.";

function board(blockReason: string | null, action = "uprav"): PipelineBoard {
  return {
    state: { status: blockReason ? "blocked" : "awaiting_manazer", block_reason: blockReason, current_stage: "navrh" },
    recent_messages: [],
    available_actions: blockReason === "decision_needed" ? ["decide", "ask"] : ["ask", "uprav"],
    dedo_proposal: { message_id: "m-1", content: "Pokyn.", proposed_action: action, created_at: "2026-10-24T17:00:00Z" },
  } as unknown as PipelineBoard;
}

const intro = () => screen.getByText(/Text napísal Dedo/).textContent ?? "";

describe("DEV-221 — the proposal bar at the Decision Cards", () => {
  it("says the cards are cancelled and the decided ones go to the agent with the text", () => {
    render(<DedoProposalBar board={board("decision_needed")} versionId="v-1" onBoard={vi.fn()} />);

    expect(intro().endsWith(AT_THE_CARDS)).toBe(true);
    expect(intro()).not.toContain(ORDINARY);
    expect(screen.getByRole("button", { name: "Vrátiť agentovi na doplnenie" })).toBeInTheDocument();
  });

  it.each([
    ["at a settled phase", null],
    ["at an agent's question", "agent_question"],
  ])("%s it says what it always said", (_when, blockReason) => {
    render(<DedoProposalBar board={board(blockReason)} versionId="v-1" onBoard={vi.fn()} />);

    expect(intro().endsWith(ORDINARY)).toBe(true);
  });

  it("an answer to the card at the cards keeps its own sentence — it does not cancel them", () => {
    render(<DedoProposalBar board={board("decision_needed", "decide")} versionId="v-1" onBoard={vi.fn()} />);

    expect(intro()).toContain("Karta sa tým vyrieši.");
    expect(intro()).not.toContain("zrušia");
  });
});
