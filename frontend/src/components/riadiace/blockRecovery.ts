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

/** The one gate both readers ask. */
export function blockRecoveryOwnsInput(
  state:
    | { status?: string | null; block_reason?: BlockReason | null }
    | null
    | undefined,
): boolean {
  const reason = state?.block_reason ?? null;
  if (state?.status !== "blocked" || !reason) return false;
  return (
    reason === "agent_question" ||
    isErrorReason(reason) ||
    isCheckReason(reason)
  );
}
