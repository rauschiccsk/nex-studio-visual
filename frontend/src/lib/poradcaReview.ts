// The review-with-Poradca button (DEV-103) — a verification decision card taken to Poradca without copying anything.
//
// Director 13.10.2026: he copied the card's heading into Poradca by hand and wrote under it a request to check whether
// the finding is justified and how to solve it (REVIEW_REQUEST). The card now hands itself off as PENDING for its
// version and opens Poradca with `?verzia=<id>&overit=1`; Poradca takes it exactly once, composes the question and
// sends it as a new conversation about that version. Only the visit marked in the address sends it: a card Poradca
// never took (the project was not open there) must not go out later, on a visit that came from somewhere else.
//
// The answer comes back the way every answer does — the recommended choice marked for the card (DEV-83), an
// instruction through „Vložiť do Riadiaceho centra" (DEV-22).

/** The sentence the Director wrote under every card he took to Poradca. */
export const REVIEW_REQUEST = "Prekontroluj opodstatnenosť tohto nálezu a ako to vyriešiť.";
/** Marks the one visit to Poradca that came from the button. */
export const REVIEW_PARAM = "overit";
/** Where a shortened part says the rest is — the card itself, which Poradca reads too. */
const SHORTENED = "… (skrátené — celé znenie je na karte)";

const SLOT_PREFIX = "nex.poradca.review.";

/** What the question is composed of — the card as the manager sees it. */
export interface ReviewCard {
  /** „Rozhodnutie N z M" — the card's number among the decisions of this consultation. */
  position: number;
  total: number;
  /** Round of fixes after verification; `null` on old records that do not carry it. */
  round: number | null;
  question: string;
  /** The Auditor's plain summary and the critic's word on the proposed fix. */
  explanation?: string;
  /** The Auditor's findings with the proposed fix. */
  technicalDetail?: string;
  options: { label: string; recommended?: boolean }[];
}

function compose(card: ReviewCard, explanation: string, detail: string): string {
  const head = [`Rozhodnutie ${card.position} z ${card.total} — ${card.question}`];
  if (card.round != null) head.push(`Kolo opráv ${card.round}.`);
  const parts = [head.join("\n")];
  if (explanation) parts.push(explanation);
  if (detail) parts.push(`Technický detail:\n${detail}`);
  if (card.options.length > 0) {
    const options = card.options.map((o) => (o.recommended ? `${o.label} (odporúčané)` : o.label));
    parts.push(`Možnosti na karte: ${options.join(" · ")}`);
  }
  parts.push(REVIEW_REQUEST);
  return parts.join("\n\n");
}

/** `text` cut to `room` characters with a note where the rest is; never half of a character. */
function shorten(text: string, room: number): string {
  if (text.length <= room) return text;
  const keep = room - SHORTENED.length - 1;
  if (keep <= 0) return SHORTENED;
  return `${text
    .slice(0, keep)
    .replace(/[\uD800-\uDBFF]$/, "")
    .trimEnd()}\n${SHORTENED}`;
}

/**
 * The card as a question for Poradca, at most `maxChars` long (the limit Poradca's status reports). What gives way
 * first is the technical detail, then the explanation — the heading, the options and the request stay whole.
 */
export function reviewQuestion(card: ReviewCard, maxChars: number): string {
  let explanation = card.explanation?.trim() ?? "";
  let detail = card.technicalDetail?.trim() ?? "";
  const over = () => compose(card, explanation, detail).length - maxChars;
  if (over() > 0) detail = shorten(detail, detail.length - over());
  if (over() > 0) explanation = shorten(explanation, explanation.length - over());
  return compose(card, explanation, detail);
}

/** Where the button leads: Poradca on the card's version, the visit marked as the card's. */
export function reviewAddress(versionId: string): string {
  return `/poradca?${new URLSearchParams({ verzia: versionId, [REVIEW_PARAM]: "1" }).toString()}`;
}

/** Hand the card off for Poradca. A second click replaces the first. Throws when the browser refuses to store it. */
export function handOffReview(versionId: string, card: ReviewCard): void {
  try {
    window.localStorage.setItem(SLOT_PREFIX + versionId, JSON.stringify(card));
  } catch {
    throw new Error("Prehliadač nedovolil odovzdať kartu Poradcovi — opýtaj sa ho v Poradcovi sám.");
  }
}

function isCard(value: unknown): value is ReviewCard {
  const c = value as Partial<ReviewCard> | null;
  return (
    !!c &&
    typeof c.question === "string" &&
    c.question.trim().length > 0 &&
    typeof c.position === "number" &&
    typeof c.total === "number" &&
    Array.isArray(c.options)
  );
}

/** The card handed off for this version, removed from the slot (Poradca sends it exactly once). */
export function takeReview(versionId: string): ReviewCard | null {
  try {
    const key = SLOT_PREFIX + versionId;
    const raw = window.localStorage.getItem(key);
    window.localStorage.removeItem(key);
    if (!raw) return null;
    const card: unknown = JSON.parse(raw);
    return isCard(card) ? card : null;
  } catch {
    return null;
  }
}
