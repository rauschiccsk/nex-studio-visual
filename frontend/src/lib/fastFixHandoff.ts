// DEV-91 — a change that does not work in UAT becomes the text of a fast fix the manager starts himself.
//
// Nothing is started by itself: the text lands in the „Rýchla oprava“ dialog of the project, which opens on arrival
// (`?rychla-oprava=1`), and the manager reads it, picks the work kind and clicks. Text already in that dialog is never
// overwritten — the new one goes under it. The dialog says where the text came from: text that appears by itself
// must not look like the manager's own earlier draft (the same rule as Poradca's handoff).
import { draftKey } from "@/hooks/useDraft";
import { appendInstruction } from "@/lib/poradcaHandoff";
import { FAST_FIX_PARAM } from "@/lib/projectLinks";

const SURFACE = "rychla-oprava";
const ORIGIN_SUFFIX = ".origin";
const ORIGIN_UAT = "uat";

/** How the fast-fix dialog labels a text prepared from a UAT test — one text, shown until he edits it. */
export const FROM_UAT_LABEL = "Zadanie pripravené zo skúšky v UAT — prečítaj ho, uprav podľa potreby a spusti sám.";

export interface BrokenChange {
  version: string;
  customer: string;
  title: string;
  steps: string[];
  expected: string[];
  note: string;
}

/** The fast fix's brief, from the guide's change and what the manager saw instead. */
export function fastFixText(c: BrokenChange): string {
  const lines = [`V UAT u zákazníka ${c.customer} vo verzii ${c.version} nefunguje: ${c.title}.`];
  if (c.steps.length) lines.push("Kroky:", ...c.steps.map((s, i) => `${i + 1}. ${s}`));
  for (const e of c.expected) lines.push(`Očakávané: ${e}`);
  if (c.note.trim()) lines.push(`Čo sa stalo: ${c.note.trim()}`);
  lines.push("Oprav to tak, aby sa stalo očakávané.");
  return lines.join("\n");
}

function originKey(slug: string): string {
  return `${draftKey(SURFACE, slug)}${ORIGIN_SUFFIX}`;
}

/** Hand the text to the project's fast-fix dialog; returns where to go. Throws when the browser refuses to store it. */
export function handOffFastFix(slug: string, text: string): string {
  const key = draftKey(SURFACE, slug) as string;
  try {
    window.localStorage.setItem(key, appendInstruction(window.localStorage.getItem(key) ?? "", text));
    window.localStorage.setItem(originKey(slug), ORIGIN_UAT);
  } catch {
    throw new Error("Prehliadač nedovolil uložiť text — skopíruj ho ručne do Rýchlej opravy.");
  }
  return `/projects/${slug}?${FAST_FIX_PARAM}=1`;
}

/** Whether the dialog's text was prepared from a UAT test and the manager has not touched it yet. */
export function fastFixFromUat(slug: string | undefined): boolean {
  if (!slug) return false;
  try {
    return window.localStorage.getItem(originKey(slug)) === ORIGIN_UAT;
  } catch {
    return false;
  }
}

/** He edited the text or started the fix — it is his now. */
export function forgetFastFixOrigin(slug: string | undefined): void {
  if (!slug) return;
  try {
    window.localStorage.removeItem(originKey(slug));
  } catch {
    /* a label is a convenience — never a reason to break the screen */
  }
}
