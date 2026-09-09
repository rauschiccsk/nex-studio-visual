/**
 * DecisionCardsBar — Manažér vidí, koľké kolo konzultácie beží (ICCINT-72).
 *
 * ⚠️ Panel rozhodnutí bol do 08.09.2026 bez jedinej stráže — pritom je to obrazovka, pri ktorej Manažér
 * sedí najdlhšie (na nex-productcatalogs v0.2.0 päť kôl za dva a pol hodiny, 23 rozhodnutí).
 *
 * Číslo kola sa dovtedy objavilo až v okamihu eskalácie, teda keď už bolo po všetkom. „Rozhodnutie 3 z 5“
 * na karte hovorí o rozhodnutiach v TOMTO kole, nie o kolách — ľahko sa to zamení a znie to ako koniec.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import DecisionCardsBar from "@/components/riadiace/DecisionCardsBar";
import type { PipelineBoard } from "@/services/api/pipeline";

function board(consultation: Record<string, unknown>): PipelineBoard {
  return {
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
        created_at: "2026-09-08T10:00:00Z",
        payload: { consultation },
      },
    ],
  } as unknown as PipelineBoard;
}

const decisions = [
  { key: "d1", question: "Košík, alebo objednávka na jeden klik?", options: [{ id: "a", label: "Košík" }, { id: "b", label: "Jeden klik" }] },
];

function bar(consultation: Record<string, unknown>) {
  return <DecisionCardsBar board={board(consultation)} versionId="v1" onBoard={vi.fn()} />;
}

describe("DecisionCardsBar — kolo konzultácie", () => {
  it("povie, koľké kolo beží a koľko ich môže prísť", () => {
    render(bar({ id: "c1", source: "auditor_upfront", decisions, round: 2, round_max: 5 }));
    expect(screen.getByText(/kolo konzultácie 2 z 5/i)).toBeInTheDocument();
  });

  it("pri poslednom kole to povie nahlas — inak Manažér čaká na kolo, ktoré nepríde", () => {
    render(bar({ id: "c1", source: "auditor_upfront", decisions, round: 5, round_max: 5 }));
    expect(screen.getByText(/posledné\. ďalšie kolo nebude/i)).toBeInTheDocument();
  });

  it("skoršie kolo sa za posledné nevydáva", () => {
    render(bar({ id: "c1", source: "auditor_upfront", decisions, round: 3, round_max: 5 }));
    expect(screen.queryByText(/ďalšie kolo nebude/i)).not.toBeInTheDocument();
  });

  it("starý záznam bez čísla kola riadok neukáže — vymyslené číslo by bolo horšie než žiadne", () => {
    render(bar({ id: "c1", source: "auditor_upfront", decisions }));
    expect(screen.queryByText(/kolo konzultácie/i)).not.toBeInTheDocument();
    // …ale samotná karta sa ukázať MUSÍ, inak by chýbajúci údaj zhasol celý panel.
    expect(screen.getByText(/košík, alebo objednávka/i)).toBeInTheDocument();
  });
});

/**
 * Kolá opráv po Verifikácii strop NEMAJÚ (ICCINT-97).
 *
 * ``AUDITOR_LOOP_MAX`` ohraničuje samočinnú slučku agent↔Auditor; keď na kartu odpovie človek,
 * počítadlo sa nuluje. Napísať na takú kartu „z piatich“ by bola lož — ale mlčať o čísle znamená, že
 * Manažér prejde šesť kôl a nedozvie sa to (zmerané 09.09.2026 na NEX Manager 1.1.0).
 */
describe("DecisionCardsBar — kolá opráv bez stropu", () => {
  it("povie číslo kola aj bez stropu", () => {
    render(bar({ id: "f1", source: "verifikacia_fix", decisions, round: 6 }));
    expect(screen.getByText(/kolo opráv 6/i)).toBeInTheDocument();
  });

  it("nevymyslí si strop, ktorý neplatí", () => {
    render(bar({ id: "f1", source: "verifikacia_fix", decisions, round: 6 }));
    expect(screen.queryByText(/z 5/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/posledné/i)).not.toBeInTheDocument();
  });

  it("bez čísla kola nepovie nič — staršie záznamy ho nemajú", () => {
    render(bar({ id: "f1", source: "verifikacia_fix", decisions }));
    expect(screen.queryByText(/kolo opráv/i)).not.toBeInTheDocument();
  });
});
