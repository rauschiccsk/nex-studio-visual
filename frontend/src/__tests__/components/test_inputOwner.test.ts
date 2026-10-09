/**
 * DEV-26 — one rule decides which box of Riadiace centrum takes the Manažér's text (and Poradca's instruction).
 *
 * The recovery bar's gate is now read from the same Record; it must still own exactly the reasons it owned
 * before (an agent question, a failed turn, a failed check) — checked against the reason helpers, not a copy.
 */

import { describe, it, expect } from "vitest";

import {
  BLOCKED_INPUT_OWNER,
  blockRecoveryOwnsInput,
  inputOwner,
  isCheckReason,
  isErrorReason,
} from "@/components/riadiace/blockRecovery";
import type { BlockReason } from "@/services/api/pipeline";

const REASONS = Object.keys(BLOCKED_INPUT_OWNER) as BlockReason[];

describe("inputOwner", () => {
  it.each(REASONS)("%s: the recovery bar owns it exactly when it is a question, a failure or a failed check", (reason) => {
    // DEV-7: a database change waiting for Ri is the agent's question too — „Schváliť" is a button above the box,
    // and the box takes the other answer.
    const question = reason === "agent_question" || reason === "schema_approval";
    const owned = question || isErrorReason(reason) || isCheckReason(reason);
    expect(blockRecoveryOwnsInput({ status: "blocked", block_reason: reason })).toBe(owned);
    expect(inputOwner({ status: "blocked", block_reason: reason, current_stage: "navrh" }) === "odpoved").toBe(owned);
  });

  it("a consultation is answered on the Decision Card", () => {
    expect(inputOwner({ status: "blocked", block_reason: "decision_needed", current_stage: "navrh" })).toBe("karta");
  });

  it("no box takes text while our technical team fixes the cockpit, on a finished version, or before the state is known", () => {
    expect(inputOwner({ status: "blocked", block_reason: "framework_issue", current_stage: "navrh" })).toBeNull();
    expect(inputOwner({ status: "awaiting_manazer", block_reason: null, current_stage: "done" })).toBeNull();
    expect(inputOwner(null)).toBeNull();
  });

  it.each(["agent_working", "awaiting_manazer", "paused"])("%s: the conversation box takes it", (status) => {
    expect(inputOwner({ status, block_reason: null, current_stage: "programovanie" })).toBe("rozhovor");
  });
});
