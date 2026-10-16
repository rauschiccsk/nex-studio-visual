// Poradca (ICCINT-167) — conversations with a read-and-advise-only agent. Backend: `backend/api/routes/poradca.py`.
// gets here via `npm run codegen` and the type-check catches it.

import type { components } from "@/services/api/pipeline.generated";

type S = components["schemas"];

export type PoradcaMessage = S["PoradcaMessageRead"];
export type PoradcaConversation = S["PoradcaConversationRead"];
export type PoradcaConversationDetail = S["PoradcaConversationDetail"];
export type PoradcaStatus = S["PoradcaStatus"];
export type PoradcaProjectContext = S["PoradcaProjectContext"];
export type PoradcaVersionInfo = S["PoradcaVersionInfo"];
export type PoradcaBacklogSaved = S["PoradcaBacklogSaved"];
/** DEV-138 — the conversation for the cards of a version in its current phase; `null` = the card founds one. */
export type PoradcaReviewConversation = S["PoradcaReviewConversation"];
/** DEV-83 — a choice Poradca marked in its answer, checked against the card it names. */
export type PoradcaChoice = S["PoradcaChoiceRead"];
/** DEV-119 — the ticket Poradca proposes and the preview „Založiť tiket" opens with. */
export type PoradcaTicketDraft = S["PoradcaTicketDraft"];
export type PoradcaTicketCreate = S["PoradcaTicketCreate"];
export type PoradcaTicketFounded = S["PoradcaTicketFounded"];

/** Live progress event — tool and target, never content. */
export type PoradcaEvent =
  | { type: "step"; message_id: string; tool: string; target: string }
  | { type: "queued"; message_id: string; limit: number }
  | { type: "finished"; message_id: string; status: string }
  | { type: "deleted" };
