// The review-with-Poradca button (DEV-103, on every decision card since DEV-106) — a decision card taken to Poradca
// without copying anything.
//
// Director 13.10.2026: he copied the card's heading into Poradca by hand and wrote under it a request to check whether
// the finding is justified and how to solve it (REVIEW_REQUEST). The card now hands itself off as PENDING for its
// version and opens Poradca with `?verzia=<id>&overit=1`; Poradca takes it exactly once, composes the question and
// sends it as a new conversation about that version. Only the visit marked in the address sends it: a card Poradca
// never took (the project was not open there) must not go out later, on a visit that came from somewhere else.
//
// The answer comes back the way every answer does — the recommended choice marked for the card (DEV-83), an
// instruction through „Vložiť do Riadiaceho centra" (DEV-22).
//
// DEV-135: the agent's question in Riadiace centrum (a blocked build waiting on the Manažér's answer) goes the same
// way — Director 16.10.2026: „Chýba mi tu tlačidlo ktorým odošleme pre Poradcu.“ It is a card of its own kind: the
// question, where the build stands, what the agent found, and the request; Poradca's recommended answer comes back
// into the answer box through „Vložiť do odpovede“.
//
// DEV-191: „Opýtaj sa Poradcu“ at a finished version goes the same way too — Director 23.10.2026: „Ak kliknem na Opýtať
// poradcu, malo by sa skutočne opýtať ako to robíme na kartách“. It asks what the version brought, what to try in UAT
// and what to watch before PROD; the version's conversation after the build (phase „done“) takes every such question.

import { PHASE_LABELS, type BuildPhase } from "@/components/cockpit/labels";
import type { PipelineBoard, PipelineMessage } from "@/services/api/pipeline";

/** The sentence the Director wrote under every card he took to Poradca — for a card with the Auditor's findings. @internal — exported for its test (DEV-155). */
export const REVIEW_REQUEST = "Prekontroluj opodstatnenosť tohto nálezu a ako to vyriešiť.";
/** DEV-106 — for a card the agent prepared: a choice between proposals, not a finding (Director 13.10.2026). @internal — exported for its test (DEV-155). */
export const CHOICE_REQUEST = "Poraď mi, ktorú možnosť zvoliť a prečo.";
/** DEV-135 — for the agent's question in Riadiace centrum. */
const AGENT_QUESTION_REQUEST = "Poraď mi, ako agentovi odpovedať.";
/** DEV-135 — what precedes the agent's own report under its question, and the change the build describes. */
const AGENT_WROTE = "Pred otázkou agent napísal:";
const BUILD_CHANGE = "Zmena, ktorú stavba navrhuje:";
/** DEV-135 — for a database change the build waits to have approved (DEV-7): accepting it is the other answer. */
const SCHEMA_QUESTION_REQUEST = "Poraď mi, či túto zmenu databázy prijať; ak nie, ako agentovi odpovedať.";
/** The consultations that carry the Auditor's findings; the engine writes the source on every card (DEV-106). */
const FINDING_SOURCES = new Set(["verifikacia_fix", "verifikacia_fail", "auditor_upfront"]);
/** Marks the one visit to Poradca that came from the button. */
export const REVIEW_PARAM = "overit";
/** Where a shortened part says the rest is — the card itself, which Poradca reads too. */
const SHORTENED = "… (skrátené — celé znenie je na karte)";

const SLOT_PREFIX = "nex.poradca.review.";

/** What the question is composed of — the card as the manager sees it. */
interface DecisionReview {
  kind?: "decision";
  /** „Rozhodnutie N z M" — the card's number among the decisions of this consultation. */
  position: number;
  total: number;
  /** The round; `null` on old records that do not carry it. */
  round: number | null;
  /** The cap of consultation rounds; `null` for fix rounds after verification, which have none. */
  roundMax: number | null;
  /** The sentence the question ends with — {@link reviewRequestFor} the card's source. */
  request: string;
  question: string;
  /** Where the question came from, as the card says it. */
  origin?: string;
  /** The plain explanation — on a verification card the Auditor's summary and the critic's word. */
  explanation?: string;
  /** The technical detail — on a verification card the Auditor's findings with the proposed fix. */
  technicalDetail?: string;
  options: { label: string; recommended?: boolean }[];
  rationale?: string;
}

/** DEV-135 — the agent's question the build waits on, as the Manažér sees it in Riadiace centrum. */
export interface AgentQuestionReview {
  kind: "agent_question";
  question: string;
  /** Where the build stands and what it waits for. */
  origin: string;
  /** What the agent found before it asked — its report, or the change the build describes — and whose it is. */
  explanation?: string;
  explanationTitle?: string;
  request: string;
}

/** DEV-191 — a finished version in Riadiace centrum: what its „Opýtaj sa Poradcu“ asks. */
interface FinishedVersionReview {
  kind: "version_done";
  /** „Verzia 1.10.1 projektu NEX Inbox je hotová.“ */
  question: string;
  request: string;
}

/** DEV-191 — what „Opýtaj sa Poradcu“ asks about a finished version (Director 23.10.2026). */
const FINISHED_VERSION_REQUEST =
  "Zhrň mi, čo priniesla, čo mám vyskúšať v UAT a na čo si dať pozor pred nasadením na PROD.";

export type ReviewCard = DecisionReview | AgentQuestionReview | FinishedVersionReview;

/** DEV-191 — the question about a finished version, as a card for Poradca. */
export function finishedVersionCard(projectName: string, versionNumber: string): FinishedVersionReview {
  return {
    kind: "version_done",
    question: `Verzia ${versionNumber} projektu ${projectName} je hotová.`,
    request: FINISHED_VERSION_REQUEST,
  };
}

/** The request a card's question ends with: check the Auditor's finding, or advise which option to choose. */
export function reviewRequestFor(source: string | undefined): string {
  return source && FINDING_SOURCES.has(source) ? REVIEW_REQUEST : CHOICE_REQUEST;
}

function compose(card: ReviewCard, explanation: string, detail: string): string {
  if (card.kind === "version_done") return `${card.question} ${card.request}`;
  if (card.kind === "agent_question") {
    // The agent writes its question as paragraphs with headings — it stands whole, under one line saying whose it is.
    const found = explanation ? `${card.explanationTitle ?? AGENT_WROTE}\n${explanation}` : "";
    return [`Otázka agenta. ${card.origin}`, card.question, found, card.request].filter(Boolean).join("\n\n");
  }
  const head = [`Rozhodnutie ${card.position} z ${card.total} — ${card.question}`];
  if (card.round != null) {
    head.push(
      card.roundMax != null ? `Kolo konzultácie ${card.round} z ${card.roundMax}.` : `Kolo opráv ${card.round}.`,
    );
  }
  if (card.origin?.trim()) head.push(card.origin.trim());
  const parts = [head.join("\n")];
  if (explanation) parts.push(explanation);
  if (detail) parts.push(`Technický detail:\n${detail}`);
  if (card.options.length > 0) {
    const options = card.options.map((o) => (o.recommended ? `${o.label} (odporúčané)` : o.label));
    parts.push(`Možnosti na karte: ${options.join(" · ")}`);
  }
  if (card.rationale?.trim()) parts.push(`Zdôvodnenie na karte: ${card.rationale.trim()}`);
  parts.push(card.request);
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
 * first is the technical detail, then the explanation — the heading, the options, the rationale and the request stay
 * whole.
 */
export function reviewQuestion(card: ReviewCard, maxChars: number): string {
  if (card.kind === "version_done") return compose(card, "", "");
  let explanation = card.explanation?.trim() ?? "";
  let detail = card.kind === "agent_question" ? "" : (card.technicalDetail?.trim() ?? "");
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
    throw new Error("Prehliadač nedovolil odovzdať to Poradcovi — opýtaj sa ho v Poradcovi sám.");
  }
}

function isCard(value: unknown): value is ReviewCard {
  const c = value as Partial<DecisionReview> & Partial<AgentQuestionReview>;
  if (
    !c ||
    typeof c.question !== "string" ||
    !c.question.trim() ||
    typeof c.request !== "string" ||
    !c.request.trim()
  ) {
    return false;
  }
  if (c.kind === "agent_question") return typeof c.origin === "string";
  if ((c as { kind?: string }).kind === "version_done") return true;
  return (
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

const text = (v: unknown): string => (typeof v === "string" ? v.trim() : "");

/** The newest message carrying a question that nobody answered after it — the question the build waits on. */
function openQuestion(messages: PipelineMessage[]): PipelineMessage | null {
  const sorted = [...messages].sort((a, b) => a.seq - b.seq);
  const asked = sorted.filter((m) => text(m.payload?.question)).at(-1);
  if (!asked) return null;
  return sorted.some((m) => m.seq > asked.seq && m.author === "manazer") ? null : asked;
}

/**
 * DEV-135 — the question the blocked build waits on, as a card for Poradca; `null` when the build waits on no
 * question (an error, a check) or says none. The question is the agent's own words when the newest question in the
 * board is still open, otherwise the sentence the build says (`next_action`); a database change carries the change
 * the build describes.
 */
export function agentQuestionCard(board: PipelineBoard | null): AgentQuestionReview | null {
  if (!board?.state) return null;
  const state = board.state;
  const reason = state.block_reason;
  if (state.status !== "blocked" || (reason !== "agent_question" && reason !== "schema_approval")) return null;
  const messages = board.recent_messages ?? [];
  const phase = PHASE_LABELS[state.current_stage as BuildPhase] ?? state.current_stage;
  if (reason === "schema_approval") {
    const question = text(state.next_action);
    if (!question) return null;
    const change = [...messages].sort((a, b) => a.seq - b.seq).filter((m) => m.payload?.database_schema).at(-1);
    return {
      kind: "agent_question",
      question,
      origin: `Stavba stojí vo fáze ${phase} a čaká na schválenie zmeny databázy.`,
      explanation: change ? text(change.content) || undefined : undefined,
      explanationTitle: BUILD_CHANGE,
      request: SCHEMA_QUESTION_REQUEST,
    };
  }
  const asked = openQuestion(messages);
  const question = asked ? text(asked.payload?.question) : text(state.next_action);
  if (!question) return null;
  const found = asked ? text(asked.payload?.report) || text(asked.content) : "";
  return {
    kind: "agent_question",
    question,
    origin: `Stavba stojí vo fáze ${phase} a čaká na moju odpoveď.`,
    explanation: found && found !== question ? found : undefined,
    request: AGENT_QUESTION_REQUEST,
  };
}
