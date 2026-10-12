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
