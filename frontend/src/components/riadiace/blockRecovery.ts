// Which blocked states the BlockRecoveryBar owns — and therefore which ones take the input over from the
// always-open composer.
//
// This lives in its own file for one reason: it has TWO readers. BlockRecoveryBar renders on it, and
// RiadiaceCentrumPage de-emphasises the composer on it. The page used to carry a hand-copied list of the
// same reasons, and the copy went stale the moment a reason was added (ICCINT-43) — the bar claimed the
// input while the composer still believed it was in charge. One list, two importers, no drift.

import type { BlockReason } from "@/services/api/pipeline";

/** Something genuinely FAILED — the agent's turn, an engine step, or the parse. Painted red, offered
 *  "Skús znova": the same input really might succeed on a retry. */
export const ERROR_REASONS: BlockReason[] = [
  "agent_error",
  "system_error",
  "parse_exhaustion",
];

/** A CHECK the engine ran came back negative (ICCINT-43). Nobody failed, so this must not be painted red
 *  under "Niečo zlyhalo" — and "Skús znova" is the wrong offer, because repeating an unchanged build
 *  repeats the same result. What moves it forward is an instruction, not another attempt. */
export const CHECK_REASONS: BlockReason[] = ["check_failed"];

export const DEFAULT_RETRY = "Skús to prosím znova.";
/** Sent when the Manažér adds no steer of his own: point the agent at the measurement, never at chance. */
export const DEFAULT_CHECK_FIX =
  "Kontrola po oprave neprešla — zisti príčinu z výpisu posledného behu a oprav ju.";

export function isErrorReason(reason: BlockReason | null): boolean {
  return !!reason && ERROR_REASONS.includes(reason);
}

export function isCheckReason(reason: BlockReason | null): boolean {
  return !!reason && CHECK_REASONS.includes(reason);
}

/** Which box of Riadiace centrum takes the Manažér's text — and with it Poradca's instruction (DEV-22). */
export type InputOwner = "rozhovor" | "odpoved" | "karta";

/**
 * DEV-26: every reason a build can block on, with the box that takes the text then; `null` — none does (only
 * our technical team moves the build on). A Record over the generated `BlockReason`, so a reason the backend
 * adds fails the type check here until someone decides where its text goes. A default is how it broke: on
 * 08.10.2026 `decision_needed` fell through to the chat, and Poradca's instruction for a Decision Card was
 * sent as a chat message instead.
 */
export const BLOCKED_INPUT_OWNER: Record<BlockReason, InputOwner | null> = {
  agent_question: "odpoved",
  agent_error: "odpoved",
  system_error: "odpoved",
  parse_exhaustion: "odpoved",
  check_failed: "odpoved",
  decision_needed: "karta",
  framework_issue: null,
};

type OwnerState =
  | { status?: string | null; block_reason?: BlockReason | null; current_stage?: string | null }
  | null
  | undefined;

/** The box that takes the text in this state — `null` while the state is unknown or no box takes it. */
export function inputOwner(state: OwnerState): InputOwner | null {
  if (!state) return null;
  if (state.current_stage === "done") return null;
  if (state.status !== "blocked" || !state.block_reason) return "rozhovor";
  const owner = BLOCKED_INPUT_OWNER[state.block_reason];
  // `null` is a decision (no box takes it); only a reason this build of the screen does not know falls back.
  return owner === undefined ? "rozhovor" : owner;
}

/** The one gate both readers ask. */
export function blockRecoveryOwnsInput(state: OwnerState): boolean {
  return state?.status === "blocked" && !!state.block_reason && BLOCKED_INPUT_OWNER[state.block_reason] === "odpoved";
}
