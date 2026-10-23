// DEV-91 — a change that does not work in UAT becomes the text of a fast fix the manager starts himself.
//
// Nothing is started by itself: the text lands in the „Rýchla oprava“ dialog of the project, which opens on arrival
// (`?rychla-oprava=1`), and the manager reads it, picks the work kind and clicks. Text already in that dialog is never
// overwritten — the new one goes under it. The dialog says where the text came from: text that appears by itself
// must not look like the manager's own earlier draft (the same rule as Poradca's handoff). DEV-156: the cleanup of the
// code nobody uses arrives the same way, with its own label.
import { draftKey } from "@/hooks/useDraft";
import { appendInstruction } from "@/lib/poradcaHandoff";
import { FAST_FIX_PARAM } from "@/lib/projectLinks";

const SURFACE = "rychla-oprava";
const ORIGIN_SUFFIX = ".origin";
const ORIGIN_UAT = "uat";
export const ORIGIN_DEAD_CODE = "dead-code";
type Origin = typeof ORIGIN_UAT | typeof ORIGIN_DEAD_CODE;

/** How the fast-fix dialog labels a text prepared from a UAT test — one text, shown until he edits it.
 * @internal — exported for its test (DEV-155). */
export const FROM_UAT_LABEL = "Zadanie pripravené zo skúšky v UAT — prečítaj ho, uprav podľa potreby a spusti sám.";
/** DEV-156: the same for the cleanup the check of the code nobody uses prepared.
 * @internal — exported for its test (DEV-155). */
export const FROM_DEAD_CODE_LABEL =
  "Zadanie pripravené z kontroly nepoužívaného kódu — prečítaj ho, uprav podľa potreby a spusti sám.";
const LABELS: Record<Origin, string> = { [ORIGIN_UAT]: FROM_UAT_LABEL, [ORIGIN_DEAD_CODE]: FROM_DEAD_CODE_LABEL };

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

/** The label of a dialog text prepared elsewhere that the manager has not touched yet — ``null`` for his own text. */
export function fastFixOriginLabel(slug: string | undefined): string | null {
  if (!slug) return null;
  try {
    const origin = window.localStorage.getItem(originKey(slug));
    return origin && origin in LABELS ? LABELS[origin as Origin] : null;
  } catch {
    return null;
  }
}

/** DEV-156: the dialog's text was prepared by a check of the cockpit (the page put it there itself) — say so until he
 * edits it. */
export function markFastFixOrigin(slug: string | undefined, origin: Origin): void {
  if (!slug) return;
  try {
    window.localStorage.setItem(originKey(slug), origin);
  } catch {
    /* a label is a convenience — never a reason to break the screen */
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
