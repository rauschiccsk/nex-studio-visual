// Poradca (ICCINT-167) — typy aliasované z GENEROVANÉHO kontraktu, nie prepísané ručne: zmena na backende
// sa sem dostane cez `npm run codegen` a type-check ju chytí.

import type { components } from "@/services/api/pipeline.generated";

type S = components["schemas"];

export type PoradcaStep = S["PoradcaStep"];
export type PoradcaMessage = S["PoradcaMessageRead"];
export type PoradcaConversation = S["PoradcaConversationRead"];
export type PoradcaConversationDetail = S["PoradcaConversationDetail"];
export type PoradcaStatus = S["PoradcaStatus"];
export type PoradcaProjectContext = S["PoradcaProjectContext"];
export type PoradcaVersionInfo = S["PoradcaVersionInfo"];
export type PoradcaBacklogSaved = S["PoradcaBacklogSaved"];

/** Udalosť živého priebehu — nástroj a cieľ, nikdy obsah. */
export type PoradcaEvent =
  | { type: "step"; message_id: string; tool: string; target: string }
  | { type: "queued"; message_id: string; limit: number }
  | { type: "finished"; message_id: string; status: string }
  | { type: "deleted" };
