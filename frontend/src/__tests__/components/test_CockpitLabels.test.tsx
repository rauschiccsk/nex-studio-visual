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
  DECISION_BANNER,
  nextPhaseLabel,
  PHASE_CODES,
  PHASE_LABELS,
  PHASE_ORDER,
  V2_ROLE_LABELS,
} from "@/components/cockpit/labels";
import type { BuildPhase, V2Participant } from "@/components/cockpit/labels";
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
    ];
    for (const r of reasons) expect(BLOCK_REASON_LABELS[r]).toBeTruthy();
    expect(BLOCK_REASON_LABELS.agent_question).toBe("Agent sa pýta");
    expect(BLOCK_REASON_LABELS.agent_error).toBe("Agent zlyhal");
    expect(BLOCK_REASON_LABELS.framework_issue).toBe("NEX Studio má chybu — rieši ju náš technický tím");
    expect(new Set(Object.values(BLOCK_REASON_LABELS)).size).toBe(reasons.length);
  });
});

// CR-V2-019/021: the v2.0.0 vocabulary — the v1 11-stage STAGE map collapses to FOUR build phases and the
// 7-role map collapses to the v2 participants (AI Agent / Auditor / Manažér + system).
describe("v2 vocabulary collapse", () => {
  it("PHASE_LABELS are exactly the five build phases + the terminal Hotovo", () => {
    // CR-1 (nex-studio-visual): the Vizuál live-preview phase sits between Návrh and Programovanie.
    const phases: BuildPhase[] = ["priprava", "navrh", "vizual", "programovanie", "verifikacia", "done"];
    expect(Object.keys(PHASE_LABELS).sort()).toEqual([...phases].sort());
    expect(PHASE_LABELS.priprava).toBe("Príprava");
    expect(PHASE_LABELS.navrh).toBe("Návrh");
    expect(PHASE_LABELS.vizual).toBe("Vizuál");
    expect(PHASE_LABELS.programovanie).toBe("Programovanie");
    expect(PHASE_LABELS.verifikacia).toBe("Verifikácia");
    expect(PHASE_LABELS.done).toBe("Hotovo");
  });

  it("PHASE_ORDER is the horizontal bar order and PHASE_CODES covers every phase", () => {
    expect(PHASE_ORDER).toEqual(["priprava", "navrh", "vizual", "programovanie", "verifikacia", "done"]);
    for (const p of PHASE_ORDER) expect(PHASE_CODES[p]).toBeTruthy();
  });

  it("nextPhaseLabel returns the following phase (clamped at Hotovo)", () => {
    expect(nextPhaseLabel("priprava")).toBe("Návrh");
    // CR-1: Návrh → Vizuál → Programovanie.
    expect(nextPhaseLabel("navrh")).toBe("Vizuál");
    expect(nextPhaseLabel("vizual")).toBe("Programovanie");
    expect(nextPhaseLabel("programovanie")).toBe("Verifikácia");
    expect(nextPhaseLabel("verifikacia")).toBe("Hotovo");
    expect(nextPhaseLabel("done")).toBe("Hotovo");
  });

  it("V2_ROLE_LABELS are exactly {AI Agent, Auditor, Manažér, system} — no v1 roles", () => {
    const roles: V2Participant[] = ["ai_agent", "auditor", "manazer", "system"];
    expect(Object.keys(V2_ROLE_LABELS).sort()).toEqual([...roles].sort());
    expect(V2_ROLE_LABELS.ai_agent).toBe("AI Agent");
    expect(V2_ROLE_LABELS.auditor).toBe("Audítor");
    expect(V2_ROLE_LABELS.manazer).toBe("Manažér");
    for (const dead of ["coordinator", "designer", "customer", "implementer"]) {
      expect(Object.keys(V2_ROLE_LABELS)).not.toContain(dead);
    }
  });
});

describe("decision-CTA banner palette", () => {
  it("DECISION_BANNER is tone-aware (amber awaiting / red blocked), token-disciplined (no raw pastels)", () => {
    expect(DECISION_BANNER.amber).toContain("var(--color-state-warning-bg)");
    expect(DECISION_BANNER.red).toContain("var(--color-state-error-bg)");
    expect(DECISION_BANNER.blue).toBeUndefined();
    expect(DECISION_BANNER.green).toBeUndefined();
  });
});
