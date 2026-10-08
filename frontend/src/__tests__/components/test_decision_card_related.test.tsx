/**
 * DEV-34 — karta povie, s ktorou inou kartou jej voľba súvisí a prečo.
 *
 * NEX Inbox 1.7.0, 08.10.2026: dve rozhodnuté karty si odporovali (karta 7 × 9, karta 1 × 2) a nikto to nevidel,
 * kým to nenašla ďalšia previerka alebo Poradca. Agent teraz súvislosť zapíše pri písaní kariet (`related`)
 * a karta ju Manažérovi ukáže číslom a otázkou tej druhej karty — pri rozhodovaní, nie po ňom.
 */

import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import DecisionCardsBar from "@/components/riadiace/DecisionCardsBar";
import type { PipelineBoard } from "@/services/api/pipeline";

vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: vi.fn() }));

const OPTIONS = [
  { id: "a", label: "Možnosť A", recommended: true },
  { id: "b", label: "Možnosť B" },
];

function board(related: unknown[] | undefined): PipelineBoard {
  return {
    state: { current_stage: "navrh", status: "blocked", block_reason: "decision_needed" },
    available_actions: ["decide"],
    recent_messages: [
      {
        id: "m1",
        seq: 10,
        author: "ai_agent",
        recipient: "manazer",
        kind: "consultation",
        content: "Treba tvoje rozhodnutie.",
        payload: {
          consultation: {
            id: "c1",
            decisions: [
              { key: "vypadok", question: "Kedy je to výpadok?", options: OPTIONS, related },
              { key: "kopia", question: "Čo pri chýbajúcej kópii faktúry?", options: OPTIONS },
            ],
          },
        },
      },
    ],
  } as unknown as PipelineBoard;
}

describe("Rozhodovacia karta — súvisiace karty (DEV-34)", () => {
  it("menuje druhú kartu číslom a otázkou a povie prečo", () => {
    render(
      <DecisionCardsBar
        board={board([{ key: "kopia", why: "Nové podmienky výpadku nesmú zachytiť chybu miestnej kópie." }])}
        versionId="v1"
        onBoard={vi.fn()}
      />,
    );
    expect(
      screen.getByText(
        "Súvisí s kartou 2 („Čo pri chýbajúcej kópii faktúry?“): Nové podmienky výpadku nesmú zachytiť chybu miestnej kópie.",
      ),
    ).toBeInTheDocument();
  });

  it("karta bez súvislostí nič také nepíše", () => {
    render(<DecisionCardsBar board={board(undefined)} versionId="v1" onBoard={vi.fn()} />);
    expect(screen.queryByText(/Súvisí s kartou/)).not.toBeInTheDocument();
  });
});
