// DEV-483 — the rules of the summary of the fast path with Poradca, apart from its screen (testable on their own).
import type { BatchReviewRow } from "@/services/api/batchReview";

/** How often the summary is read while Poradca still answers. */
export const POLL_MS = 5000;

/** The rows that go with the approval: the agreements he kept checked, minus every card related to one he decides
 * himself — in both directions, until nothing changes.  */
export function approvable(rows: BatchReviewRow[], unchecked: Set<string>): { keys: Set<string>; heldBy: Map<string, number> } {
  const open = rows.filter((r) => r.state !== "decided");
  const keys = new Set(open.filter((r) => r.state === "agree" && !unchecked.has(r.key)).map((r) => r.key));
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

