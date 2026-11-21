// DEV-483/DEV-486 — the rules of the summary of the fast path with Poradca, apart from its screen (testable on their own).
import type { BatchReviewRow } from "@/services/api/batchReview";

/** How often the summary is read while Poradca still answers. */
export const POLL_MS = 5000;

/** DEV-486 — a card Poradca proposed something for: „Schváliť všetko“ approves his proposal on it. */
export function proposed(row: BatchReviewRow): boolean {
  return row.state === "agree" || row.state === "disagree";
}

/** DEV-486 — a card the Manažér decides on its card inside the summary: one he consults, or one Poradca left out. */
export function decidesHimself(row: BatchReviewRow, consulted: Set<string>): boolean {
  if (row.state === "decided" || row.state === "waiting") return false;
  return row.state === "missing" || consulted.has(row.key);
}

/** The rows „Schváliť všetko“ approves: every card with Poradca's proposal that he does not consult, minus every card
 * related to one he decides himself — in both directions, until nothing changes. */
export function approvable(rows: BatchReviewRow[], consulted: Set<string>): { keys: Set<string>; heldBy: Map<string, number> } {
  const open = rows.filter((r) => r.state !== "decided");
  const keys = new Set(open.filter((r) => proposed(r) && !consulted.has(r.key)).map((r) => r.key));
  const heldBy = new Map<string, number>();
  let changed = true;
  while (changed) {
    changed = false;
    const manual = open.filter((r) => !keys.has(r.key));
    for (const row of open) {
      if (!keys.has(row.key)) continue;
      const blocker = manual.find((m) => (m.related ?? []).includes(row.number) || (row.related ?? []).includes(m.number));
      if (blocker) {
        keys.delete(row.key);
        heldBy.set(row.key, blocker.number);
        changed = true;
      }
    }
  }
  return { keys, heldBy };
}
