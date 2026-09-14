/**
 * ICCINT-123 — karta povie, PREČO tá otázka vznikla.
 *
 * Karta dovtedy ukázala otázku a možnosti, ale nie to, prečo otázka vznikla. Manažér nevie rozlíšiť,
 * či je to dôsledok jeho vlastného rozhodnutia, alebo nález, ktorý tam ležal od začiatku. Vidí len,
 * že otázok pribudlo — a to si prirodzene vysvetlí ako rozpad.
 *
 * Skutočný priebeh NEX Inboxu v1.5.0: štvrté kolo vyzeralo najhoršie a bol to ČISTÝ OBJAV —
 * nevyvolalo ho žiadne rozhodnutie Manažéra. Bez tejto vety to z karty nijako nevyplýva.
 *
 * ⚠️⚠️ AKO ÚDAJ, NIKDY AKO ODPORÚČANIE. Pri tej stavbe boli DVE miesta, kde bola drahšia cesta
 * správna. Keby sa z počtu znovu otváraných rozhodnutí stal tlak na lacnejšiu voľbu, vyrobí
 * polovičné opravy — teda presne to, čo v1.5.0 opravovala.
 */

import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DecisionCardsBar from "@/components/riadiace/DecisionCardsBar";
import { postPipelineActionApi, type PipelineBoard } from "@/services/api/pipeline";

vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: vi.fn() }));

function boardWith(decision: Record<string, unknown>): PipelineBoard {
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
            intro: "Treba tvoje rozhodnutie.",
            decisions: [
              {
                key: "k1",
                question: "Ako ďalej?",
                options: [
                  { id: "a", label: "Možnosť A", recommended: true },
                  { id: "b", label: "Možnosť B" },
                ],
                ...decision,
              },
            ],
          },
        },
      },
    ],
  } as unknown as PipelineBoard;
}

describe("Rozhodovacia karta — odkiaľ otázka prišla (ICCINT-123)", () => {
  beforeEach(() => vi.mocked(postPipelineActionApi).mockReset());

  it("⚠️ pri objave povie, že to NEVYVOLALO tvoje rozhodnutie", () => {
    render(<DecisionCardsBar board={boardWith({ origin: "objav" })} versionId="v1" onBoard={vi.fn()} />);
    expect(screen.getByText(/nevznikol z tvojho rozhodnutia/i)).toBeInTheDocument();
  });

  it("pri dôsledku menuje, z čoho vyplýva", () => {
    render(
      <DecisionCardsBar
        board={boardWith({ origin: "dosledok", origin_of: "rozsah kontrol" })}
        versionId="v1"
        onBoard={vi.fn()}
      />,
    );
    expect(screen.getByText(/rozsah kontrol/)).toBeInTheDocument();
  });

  it("pri odklade povie, že to bol plán", () => {
    render(<DecisionCardsBar board={boardWith({ origin: "odklad" })} versionId="v1" onBoard={vi.fn()} />);
    expect(screen.getByText(/vedome odložili/i)).toBeInTheDocument();
  });

  it("stará karta bez pôvodu vyzerá ako doteraz — žiadna vymyslená veta", () => {
    render(<DecisionCardsBar board={boardWith({})} versionId="v1" onBoard={vi.fn()} />);
    expect(screen.queryByText(/nevznikol z tvojho rozhodnutia/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/vedome odložili/i)).not.toBeInTheDocument();
    expect(screen.getByText("Ako ďalej?")).toBeInTheDocument();
  });

  it("⚠️⚠️ značka „odporúčané“ zostáva tam, kam ju dal Agent — údaj o pôvode ňou nehýbe", () => {
    // Najdôležitejšia veta celého tiketu. Keby sa z pôvodu stal tlak na lacnejšiu voľbu, vyrobí
    // polovičné opravy — presne to, čo v1.5.0 opravovala.
    render(
      <DecisionCardsBar
        board={boardWith({ origin: "dosledok", origin_of: "rozsah kontrol" })}
        versionId="v1"
        onBoard={vi.fn()}
      />,
    );
    const odporucane = screen.getByText(/Možnosť A/).closest("button");
    expect(odporucane?.textContent).toMatch(/odporúčané/i);
    expect(screen.getByText(/Možnosť B/).closest("button")?.textContent).not.toMatch(/odporúčané/i);
  });
});

// ── Druhá polovica: cena voľby PRI voľbe, nie po nej ─────────────────────────

function boardWithTwoDecisions(): PipelineBoard {
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
              {
                key: "rozsah",
                question: "Aký rozsah kontrol?",
                options: [
                  { id: "siroky", label: "Široký", recommended: true },
                  { id: "uzky", label: "Úzky" },
                ],
              },
              {
                key: "nasledok",
                question: "A čo s tým?",
                origin: "dosledok",
                origin_of: "rozsah",
                options: [
                  { id: "x", label: "X", recommended: true },
                  { id: "y", label: "Y" },
                ],
              },
            ],
          },
        },
      },
    ],
  } as unknown as PipelineBoard;
}

describe("Rozhodovacia karta — cena voľby (ICCINT-123)", () => {
  beforeEach(() => vi.mocked(postPipelineActionApi).mockReset());

  it("keď voľba znovu otvára už uzavreté rozhodnutie, povie to PRI nej", () => {
    render(<DecisionCardsBar board={boardWithTwoDecisions()} versionId="v1" onBoard={vi.fn()} />);
    expect(screen.getByText(/znovu otvára 1 .*rozhodnutie/i)).toBeInTheDocument();
  });

  it("⚠️⚠️ počet NEHÝBE poradím ani značkou „odporúčané“", () => {
    // Keby sa z tohto údaja stal tlak na lacnejšiu voľbu, vyrobí polovičné opravy. Pri NEX Inbox
    // v1.5.0 boli DVE miesta, kde bola drahšia cesta správna.
    render(<DecisionCardsBar board={boardWithTwoDecisions()} versionId="v1" onBoard={vi.fn()} />);
    const tlacidla = screen.getAllByRole("button").filter((b) => /Široký|Úzky/.test(b.textContent || ""));
    const prve = tlacidla[0];
    expect(prve).toBeDefined();
    expect(prve!.textContent).toMatch(/Široký/);
    expect(prve!.textContent).toMatch(/odporúčané/i);
  });
});

// ── ICCINT-124: riadok so smerom nad kartami ─────────────────────────────────

describe("Nad kartami je vidieť SMER (ICCINT-124)", () => {
  beforeEach(() => vi.mocked(postPipelineActionApi).mockReset());

  it("⚠️ ukáže, koľko sa uzavrelo a ako klesali blokujúce", () => {
    const b = boardWith({});
    (b as unknown as { recent_messages: unknown[] }).recent_messages.push(
      { id: "v1", seq: 1, kind: "verdict", payload: { findings: [{ text: "a", blocking: true }, { text: "b", blocking: true }] } },
      { id: "a1", seq: 2, kind: "answer", payload: { consultation_decision: { key: "x" } } },
      { id: "v2", seq: 3, kind: "verdict", payload: { findings: [{ text: "c", blocking: false }] } },
    );
    render(<DecisionCardsBar board={b} versionId="v1" onBoard={vi.fn()} />);
    expect(screen.getByText(/Uzavretých 1/)).toBeInTheDocument();
    expect(screen.getByText(/blokujúcich 2 → 0/)).toBeInTheDocument();
  });

  it("keď ešte niet čo ukázať, riadok tam nie je", () => {
    render(<DecisionCardsBar board={boardWith({})} versionId="v1" onBoard={vi.fn()} />);
    expect(screen.queryByText(/Uzavretých/)).not.toBeInTheDocument();
  });
});
