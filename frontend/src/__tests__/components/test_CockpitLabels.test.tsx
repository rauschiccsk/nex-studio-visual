/**
 * Slovak display vocabulary across the Vývoj board (CR-V2-021). The 4-phase bar renders Slovak phase
 * labels; deriveActiveAgent surfaces the real active agent (not the nominal current_actor). The v2
 * vocabulary (CR-V2-019) is asserted exhaustively at the bottom.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import PipelineRail, { deriveActiveAgent } from "@/components/cockpit/PipelineRail";
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
import type { ActivityLine, PipelineBoard, PipelineMessage, PipelineState } from "@/services/api/pipeline";

function mkState(): PipelineState {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    version_id: "22222222-2222-2222-2222-222222222222",
    flow_type: "new_version",
    current_stage: "programovanie",
    current_actor: "ai_agent",
    status: "agent_working",
    next_action: "x",
    is_regate: false,
    iteration: 0,
    created_at: "2026-06-27T00:00:00Z",
    updated_at: "2026-06-27T00:00:00Z",
  };
}

describe("Vývoj board Slovak phase labels", () => {
  it("the bar renders the four Slovak phase labels (not raw codes)", () => {
    render(<PipelineRail state={mkState()} viewedPhase="programovanie" onSelectPhase={() => {}} />);
    expect(screen.getByText("Príprava")).toBeInTheDocument();
    expect(screen.getByText("Návrh")).toBeInTheDocument();
    expect(screen.getByText("Programovanie")).toBeInTheDocument();
    expect(screen.getByText("Verifikácia")).toBeInTheDocument();
    // raw codes are only a tooltip, never visible text
    expect(screen.queryByText("priprava")).not.toBeInTheDocument();
  });

  it("R4 (D1/D2): every block_reason maps to a distinct Slovak phrase", () => {
    const reasons: BlockReason[] = ["agent_question", "agent_error", "system_error", "parse_exhaustion"];
    for (const r of reasons) expect(BLOCK_REASON_LABELS[r]).toBeTruthy();
    expect(BLOCK_REASON_LABELS.agent_question).toBe("Agent sa pýta");
    expect(BLOCK_REASON_LABELS.agent_error).toBe("Agent zlyhal");
    expect(new Set(Object.values(BLOCK_REASON_LABELS)).size).toBe(reasons.length);
  });
});

describe("deriveActiveAgent (real active agent, not current_actor)", () => {
  const st = (status: PipelineState["status"]): PipelineState => ({ ...mkState(), status });
  const board = (state: PipelineState, messages: PipelineMessage[] = []): PipelineBoard => ({
    state,
    recent_messages: messages,
  });
  const msg = (author: PipelineMessage["author"]): PipelineMessage => ({
    id: author,
    version_id: "2",
    stage: "programovanie",
    author,
    recipient: "manazer",
    kind: "answer",
    content: "x",
    status: "delivered",
    payload: null,
    created_at: "2026-06-27T00:00:00Z",
    seq: 1,
  });

  it("while working = the latest activity frame's role (not the nominal actor)", () => {
    const activity: ActivityLine[] = [
      { stage: "programovanie", actor: "ai_agent", kind: "status", line: "pracuje…" },
      { stage: "verifikacia", actor: "auditor", kind: "status", line: "pracuje…" },
    ];
    expect(deriveActiveAgent(board(st("agent_working")), activity)).toBe("auditor");
  });

  it("while working with no activity falls back to current_actor", () => {
    expect(deriveActiveAgent(board(st("agent_working")), [])).toBe("ai_agent");
  });

  it("at awaiting_manazer = the latest message author (who just acted)", () => {
    expect(deriveActiveAgent(board(st("awaiting_manazer"), [msg("auditor")]), [])).toBe("auditor");
  });

  it("ignores a system/manazer latest message at rest", () => {
    expect(deriveActiveAgent(board(st("blocked"), [msg("system")]), [])).toBeNull();
    expect(deriveActiveAgent(board(st("blocked"), [msg("manazer")]), [])).toBeNull();
  });
});

// CR-V2-019/021: the v2.0.0 vocabulary — the v1 11-stage STAGE map collapses to FOUR build phases and the
// 7-role map collapses to the v2 participants (AI Agent / Auditor / Manažér + system).
describe("v2 vocabulary collapse", () => {
  it("PHASE_LABELS are exactly the four build phases + the terminal Hotovo", () => {
    const phases: BuildPhase[] = ["priprava", "navrh", "programovanie", "verifikacia", "done"];
    expect(Object.keys(PHASE_LABELS).sort()).toEqual([...phases].sort());
    expect(PHASE_LABELS.priprava).toBe("Príprava");
    expect(PHASE_LABELS.navrh).toBe("Návrh");
    expect(PHASE_LABELS.programovanie).toBe("Programovanie");
    expect(PHASE_LABELS.verifikacia).toBe("Verifikácia");
    expect(PHASE_LABELS.done).toBe("Hotovo");
  });

  it("PHASE_ORDER is the horizontal bar order and PHASE_CODES covers every phase", () => {
    expect(PHASE_ORDER).toEqual(["priprava", "navrh", "programovanie", "verifikacia", "done"]);
    for (const p of PHASE_ORDER) expect(PHASE_CODES[p]).toBeTruthy();
  });

  it("nextPhaseLabel returns the following phase (clamped at Hotovo)", () => {
    expect(nextPhaseLabel("priprava")).toBe("Návrh");
    expect(nextPhaseLabel("navrh")).toBe("Programovanie");
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
