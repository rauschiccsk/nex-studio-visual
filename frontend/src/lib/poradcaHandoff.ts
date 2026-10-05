// „Vložiť do Riadiaceho centra" (ICCINT-167) — pokyn od Poradcu do poľa rozhovoru stavby.
//
// Pokyn sa NEODOŠLE: zapíše sa do toho istého rozpísaného textu, ktorý pole Riadiaceho centra obnovuje
// (useDraft, ICCINT-30), a odošle ho človek sám, keď si ho prečíta. Rozpísaný text, ktorý tam už bol,
// sa neprepíše — pokyn sa pridá pod neho. Pole potom povie, odkiaľ text prišiel: text, ktorý sa objaví sám,
// nesmie vyzerať ako jeho vlastný starší koncept.

import { draftKey } from "@/hooks/useDraft";

const SURFACE = "rozhovor";
const ORIGIN_SUFFIX = ".origin";

function keyFor(versionId: string): string {
  return draftKey(SURFACE, versionId) as string;
}

export function insertInstructionDraft(versionId: string, instruction: string): void {
  const key = keyFor(versionId);
  try {
    const existing = window.localStorage.getItem(key) ?? "";
    const next = existing.trim() ? `${existing.trimEnd()}\n\n${instruction.trim()}` : instruction.trim();
    window.localStorage.setItem(key, next);
    window.localStorage.setItem(key + ORIGIN_SUFFIX, "poradca");
  } catch {
    throw new Error("Prehliadač nedovolil uložiť text — skopíruj pokyn ručne.");
  }
}

/** Prišiel rozpísaný text tejto verzie od Poradcu (a človek ho ešte neupravil)? */
export function draftCameFromPoradca(versionId: string | null | undefined): boolean {
  if (!versionId) return false;
  try {
    return window.localStorage.getItem(keyFor(versionId) + ORIGIN_SUFFIX) === "poradca";
  } catch {
    return false;
  }
}

/** Text už je jeho — upravil ho alebo odoslal. */
export function forgetPoradcaOrigin(versionId: string | null | undefined): void {
  if (!versionId) return;
  try {
    window.localStorage.removeItem(keyFor(versionId) + ORIGIN_SUFFIX);
  } catch {
    /* nič */
  }
}
