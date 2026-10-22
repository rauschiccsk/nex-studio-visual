/**
 * Slovak display vocabulary from components/cockpit/labels.ts (CR-V2-021). Pins the 4-phase / 3-participant
 * v2 vocabulary + the block-reason phrases + the decision-CTA banner palette.
 *
 * Spine STEP 1: decoupled from the CUT phase-rail component — the render-based "bar renders the phase labels" case
 * and the deriveActiveAgent cases moved out with the deleted component. labels.ts is KEPT, so its vocabulary
 * assertions stay here (the single source of truth for the display dictionary).
 */

import { describe, expect, it } from "vitest";

import {
  BLOCK_REASON_LABELS,
  PHASE_LABELS,
  PHASE_ORDER,
} from "@/components/cockpit/labels";
import type { BuildPhase } from "@/components/cockpit/labels";
import type { BlockReason } from "@/services/api/pipeline";

describe("cockpit block_reason phrases", () => {
  it("R4 (D1/D2): every block_reason maps to a distinct Slovak phrase", () => {
    // CR-V2-041: + "decision_needed" (an interactive consultation).
    // Director observation #6: + "framework_issue" (agent → Dedo escalation).
    const reasons: BlockReason[] = [
      "agent_question",
      "decision_needed",
      "agent_error",
      "system_error",
      "parse_exhaustion",
      "framework_issue",
      // ICCINT-43: + "check_failed" — a check the ENGINE ran came back negative. Its phrase is the only one
      // here that names nobody, which is exactly why it could not keep borrowing "Agent zlyhal".
      "check_failed",
      // DEV-7: + "schema_approval" — a database change only Ri may approve; a card with „Schváliť", not a question.
      "schema_approval",
    ];
    for (const r of reasons) expect(BLOCK_REASON_LABELS[r]).toBeTruthy();
    expect(BLOCK_REASON_LABELS.schema_approval).toBe("Treba schváliť štruktúru databázy");
    expect(BLOCK_REASON_LABELS.agent_question).toBe("Agent sa pýta");
    expect(BLOCK_REASON_LABELS.agent_error).toBe("Agent zlyhal");
    expect(BLOCK_REASON_LABELS.check_failed).toBe("Kontrola neprešla");
    expect(BLOCK_REASON_LABELS.check_failed).not.toMatch(/zlyhal/);
    expect(BLOCK_REASON_LABELS.framework_issue).toBe(
      "NEX Studio má chybu — rieši ju náš technický tím",
    );
    expect(new Set(Object.values(BLOCK_REASON_LABELS)).size).toBe(
      reasons.length,
    );
  });
});

// CR-V2-019/021: the v2.0.0 vocabulary — the v1 11-stage STAGE map collapses to FOUR build phases and the
// 7-role map collapses to the v2 participants (AI Agent / Auditor / Manažér + system).
describe("v2 vocabulary collapse", () => {
  it("PHASE_LABELS are exactly the five build phases + the terminal Hotovo", () => {
    // CR-1 (nex-studio-visual): the Vizuál live-preview phase sits between Návrh and Programovanie.
    const phases: BuildPhase[] = [
      "priprava",
      "navrh",
      "vizual",
      "programovanie",
      "verifikacia",
      "done",
    ];
    expect(Object.keys(PHASE_LABELS).sort()).toEqual([...phases].sort());
    expect(PHASE_LABELS.priprava).toBe("Príprava");
    expect(PHASE_LABELS.navrh).toBe("Návrh");
    expect(PHASE_LABELS.vizual).toBe("Vizuál");
    expect(PHASE_LABELS.programovanie).toBe("Programovanie");
    expect(PHASE_LABELS.verifikacia).toBe("Verifikácia");
    expect(PHASE_LABELS.done).toBe("Hotovo");
  });

  it("PHASE_ORDER is the horizontal bar order", () => {
    expect(PHASE_ORDER).toEqual([
      "priprava",
      "navrh",
      "vizual",
      "programovanie",
      "verifikacia",
      "done",
    ]);
  });
});
