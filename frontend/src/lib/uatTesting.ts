// DEV-91 — what the UAT acceptance question says about the testing. Pure — tests in `test_uatTesting`.
import type { UatProgress } from "@/types/deploy";

/** DEV-91 — what the acceptance question says about the testing: „Otestované N z M.“ and what is left. */
export function acceptanceTesting(progress: UatProgress): string {
  if (!progress.total) return "";
  const parts = [`Otestované ${progress.tested} z ${progress.total}.`];
  const untested = progress.untested ?? [];
  const broken = progress.broken ?? [];
  if (untested.length) parts.push(`Neotestované: ${untested.join("; ")}.`);
  if (broken.length) parts.push(`Nefunguje: ${broken.join("; ")}.`);
  return parts.join(" ") + " ";
}

/**
 * DEV-247 — what was tried across every version the customer's UAT carries, for the acceptance question. Each change
 * is named with its version („1.4.0: Založenie skupiny…“); a single version reads exactly as before.
 */
export function combineProgress(progresses: UatProgress[]): UatProgress {
  const [only] = progresses;
  if (progresses.length === 1 && only) return only;
  const named = (p: UatProgress, item: string) =>
    `${p.version_number}: ${item}`;
  return {
    version_number: progresses[progresses.length - 1]?.version_number ?? "",
    items: progresses.flatMap((p) => (p.items ?? []).map((i) => named(p, i))),
    checks: progresses.flatMap((p) =>
      (p.checks ?? []).map((c) => ({ ...c, item: named(p, c.item) })),
    ),
    tested: progresses.reduce((sum, p) => sum + p.tested, 0),
    total: progresses.reduce((sum, p) => sum + p.total, 0),
    untested: progresses.flatMap((p) =>
      (p.untested ?? []).map((i) => named(p, i)),
    ),
    broken: progresses.flatMap((p) => (p.broken ?? []).map((i) => named(p, i))),
  };
}
