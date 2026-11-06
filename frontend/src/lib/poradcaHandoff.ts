// „Vložiť do Riadiaceho centra" (ICCINT-167, DEV-22) — an instruction from Poradca for the build's input.
//
// The instruction is NEVER sent. It is handed off as PENDING for the version, and the box of Riadiace
// centrum that currently takes the Manažér's text picks it up: the block-recovery bar while the build waits
// on an agent question / error / check, the current Decision Card during a consultation (DEV-26), the
// conversation box otherwise. Which box that is decides ONE rule — `inputOwner` — and it is applied only once
// the build state is known.
//
// DEV-22 (08.10.2026): the handoff used to write straight into the conversation box's draft. While the agent
// waited on a question that box is collapsed to a one-line pointer, so the instruction landed in a box nobody
// could see — the screen blinked and "nothing happened".
//
// Text already in the receiving box is never overwritten — the instruction goes under it. The box then says
// where the text came from: text that appears by itself must not look like the Manažér's own earlier draft.

import type { InputOwner } from "@/components/controlCenter/blockRecovery";
import { draftKey } from "@/hooks/useDraft";
import type { PoradcaVersionInfo } from "@/types/poradca";

export type HandoffSurface = InputOwner;

const PENDING_PREFIX = "nex.poradca.pending.";
const ORIGIN_SUFFIX = ".origin";
const ORIGIN_VALUE = "poradca";
/** Fired on `window` whenever an instruction is handed off — a box that is live right now takes it at once. */
export const HANDOFF_EVENT = "nex-poradca-handoff";
/** How a box labels Poradca's instruction the Manažér has not touched yet — one text for every box. */
export const FROM_PORADCA_LABEL = "Pokyn od Poradcu — prečítaj ho, uprav podľa potreby a odošli sám.";

function pendingKey(versionId: string): string {
  return PENDING_PREFIX + versionId;
}

function originKey(surface: HandoffSurface, versionId: string): string {
  return (draftKey(surface, versionId) as string) + ORIGIN_SUFFIX;
}

/** `instruction` under `existing`, separated by a blank line; unchanged when `existing` already holds it. */
export function appendInstruction(existing: string, instruction: string): string {
  const add = instruction.trim();
  if (!add) return existing;
  if (existing.includes(add)) return existing;
  return existing.trim() ? `${existing.trimEnd()}\n\n${add}` : add;
}

/** DEV-294 — the version whose Riadiace centrum takes Poradca's instruction, or why none does.
 *
 * The conversation's own version while its field takes it. A conversation about a finished version, or about the whole
 * project, hands it to the build that runs now — the pinned version first, otherwise the only one whose field takes
 * it. 03.11.2026 the Director asked about NEX Manager 1.5.0 in the conversation of the finished 1.4.0 and the button
 * stayed grey: „stlačím tlačidlo ako obvykle a upresnenie pôjde do riadiaceho centra.“ A build that is not finished
 * but does not take it now (it waits for the cockpit) is not bypassed into another version. */
export function instructionTarget(
  scope: PoradcaVersionInfo | null,
  versions: readonly PoradcaVersionInfo[],
  pinnedId: string | null,
): { version: PoradcaVersionInfo | null; blocked: string | null } {
  if (scope?.instruction_open) return { version: scope, blocked: null };
  if (!scope || scope.stage === "done") {
    const open = versions.filter((v) => v.instruction_open);
    const chosen = open.find((v) => v.id === pinnedId) ?? (open.length === 1 ? open[0] : null);
    if (chosen) return { version: chosen, blocked: null };
    if (open.length > 1) {
      return { version: null, blocked: "Stavia sa viac verzií — v Projektoch pripni tú, do ktorej pokyn patrí." };
    }
  }
  return {
    version: null,
    blocked: scope
      ? (scope.instruction_closed_reason ?? "Pole Riadiaceho centra teraz pokyn neprijme.")
      : "Žiadna verzia projektu teraz pokyn neprijme — stavba nebeží.",
  };
}

/** Hand an instruction off to the build's Riadiace centrum. Throws when the browser refuses to store it. */
export function handOffInstruction(versionId: string, instruction: string): void {
  try {
    const key = pendingKey(versionId);
    const next = appendInstruction(window.localStorage.getItem(key) ?? "", instruction);
    window.localStorage.setItem(key, next);
  } catch {
    throw new Error("Prehliadač nedovolil uložiť text — skopíruj pokyn ručne.");
  }
  window.dispatchEvent(new CustomEvent(HANDOFF_EVENT, { detail: { versionId } }));
}

/** The pending instruction for this version, removed from the slot (a box takes it exactly once). */
export function takeHandoff(versionId: string): string | null {
  try {
    const key = pendingKey(versionId);
    const text = window.localStorage.getItem(key);
    window.localStorage.removeItem(key);
    return text && text.trim() ? text : null;
  } catch {
    return null;
  }
}

export function markFromPoradca(versionId: string, surface: HandoffSurface): void {
  try {
    window.localStorage.setItem(originKey(surface, versionId), ORIGIN_VALUE);
  } catch {
    /* the label is a courtesy; the text itself is already in the box */
  }
}

/** Is the draft of this box Poradca's instruction (and not yet edited by the Manažér)? */
export function draftCameFromPoradca(
  versionId: string | null | undefined,
  surface: HandoffSurface = "rozhovor",
): boolean {
  if (!versionId) return false;
  try {
    return window.localStorage.getItem(originKey(surface, versionId)) === ORIGIN_VALUE;
  } catch {
    return false;
  }
}

/** The text is his now — he edited or sent it. */
export function forgetPoradcaOrigin(
  versionId: string | null | undefined,
  surface: HandoffSurface = "rozhovor",
): void {
  if (!versionId) return;
  try {
    window.localStorage.removeItem(originKey(surface, versionId));
  } catch {
    /* nothing to do */
  }
}
