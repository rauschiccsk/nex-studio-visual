// DEV-36 — reading the Vizuál checklist off a thread message (kept apart from the component for fast refresh).

import type { VizualChecklist } from "@/services/api/pipeline";

/** The list a message carries, when it carries a well-formed one. */
export function vizualChecklistOf(payload: Record<string, unknown> | null): VizualChecklist | null {
  const raw = payload?.vizual_checklist as Partial<VizualChecklist> | undefined;
  if (!raw || typeof raw !== "object" || !Array.isArray(raw.items) || raw.items.length === 0) return null;
  return {
    round: raw.round === "change" ? "change" : "first",
    items: raw.items,
    not_verifiable: Array.isArray(raw.not_verifiable) ? raw.not_verifiable : [],
  };
}
