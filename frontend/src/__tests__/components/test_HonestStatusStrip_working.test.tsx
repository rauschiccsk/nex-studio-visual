/**
 * HonestStatusStrip — kým sa pracuje, pruh povie ČO a ODKEDY (ICCINT-75).
 *
 * Po kliknutí na „Schváliť vizuál“ bežal ťah agenta aj tri minúty a pruh ukazoval iba fázu. Manažér z toho
 * nevedel, či sa pracuje, alebo je rozbité — Director to opísal slovami „nič sa nedeje, nefunguje to“.
 * Ticho pri práci a ticho pri poruche vyzerajú rovnako; klikanie znova pritom spúšťa ďalšie ťahy.
 */

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import HonestStatusStrip from "@/components/riadiace/HonestStatusStrip";
import { elapsedSince } from "@/utils/elapsed";
import type { PipelineState } from "@/services/api/pipeline";

function strip(state: Partial<PipelineState> | null) {
  return (
    <HonestStatusStrip
      state={state as PipelineState | null}
      projectName="Demo"
      versionNumber="1.0.0"
      reconnecting={false}
      error={null}
    />
  );
}

describe("HonestStatusStrip — počas práce", () => {
  it("povie, čo sa práve robí — nielen v ktorej fáze to je", () => {
    render(
      strip({
        current_stage: "vizual",
        status: "agent_working",
        next_action: "Schválenie Vizuálu sa spracúva — skladám dohodnuté do dokumentov.",
      }),
    );
    expect(screen.getByText(/skladám dohodnuté do dokumentov/i)).toBeInTheDocument();
  });

  it("povie aj odkedy sa pracuje", () => {
    const pred_troma_minutami = new Date(Date.now() - 3 * 60 * 1000).toISOString();
    render(
      strip({
        current_stage: "vizual",
        status: "agent_working",
        next_action: "Schválenie Vizuálu sa spracúva.",
        working_since: pred_troma_minutami,
      }),
    );
    expect(screen.getByText(/pracuje sa 3 min/i)).toBeInTheDocument();
  });

  it("keď sa nepracuje, čas sa neukazuje — inak by strašil na usadenej stavbe", () => {
    render(
      strip({
        current_stage: "vizual",
        status: "awaiting_manazer",
        next_action: "Posúď návrh.",
        working_since: new Date(Date.now() - 3 * 60 * 1000).toISOString(),
      }),
    );
    expect(screen.queryByText(/pracuje sa/i)).not.toBeInTheDocument();
    expect(screen.getByText(/posúď návrh/i)).toBeInTheDocument();
  });
});

describe("elapsedSince — ľudsky, nie na sekundu", () => {
  const now = new Date("2026-09-08T12:00:00Z").getTime();
  const pred = (ms: number) => new Date(now - ms).toISOString();

  it("pár sekúnd sa nepočíta na minúty", () => {
    expect(elapsedSince(pred(20_000), now)).toBe("pracuje sa pár sekúnd");
  });

  it("minúty", () => {
    expect(elapsedSince(pred(7 * 60_000), now)).toBe("pracuje sa 7 min");
  });

  it("hodiny s minútami — práve toto odlíši dlhý ťah od zaseknutého", () => {
    expect(elapsedSince(pred(2 * 3_600_000 + 5 * 60_000), now)).toBe("pracuje sa 2 h 5 min");
  });

  it("celé hodiny bez zvyšku", () => {
    expect(elapsedSince(pred(3 * 3_600_000), now)).toBe("pracuje sa 3 h");
  });

  it("bez údaja mlčí — vymyslený čas by bol horší než žiadny", () => {
    expect(elapsedSince(null, now)).toBe("");
    expect(elapsedSince("nedátum", now)).toBe("");
  });
});
