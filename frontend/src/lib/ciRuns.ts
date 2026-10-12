/**
 * DEV-84 — what the repository tab does with the runs: the filters, the search and the duration in words.
 */

import type { CiRun } from "@/services/api/ciRuns";

export interface RunFilter {
  /** A run state (`success`, `failure`, …) or "" for all. */
  state: string;
  branch: string;
  actor: string;
  /** Searched in the subject, the workflow, the branch, the commit, the task and the run number. */
  text: string;
}

export const NO_FILTER: RunFilter = { state: "", branch: "", actor: "", text: "" };

export function filterRuns(runs: CiRun[], f: RunFilter): CiRun[] {
  const needle = f.text.trim().toLocaleLowerCase("sk");
  return runs.filter((r) => {
    if (f.state && r.state !== f.state) return false;
    if (f.branch && r.branch !== f.branch) return false;
    if (f.actor && r.actor !== f.actor) return false;
    if (!needle) return true;
    const haystack = [r.subject, r.workflow, r.branch, r.sha, r.task?.label ?? "", `#${r.number}`]
      .join(" ")
      .toLocaleLowerCase("sk");
    return haystack.includes(needle);
  });
}

/** The distinct values of one field, in the order the runs bring them (newest first). */
export function choices(runs: CiRun[], pick: (r: CiRun) => string): string[] {
  return [...new Set(runs.map(pick).filter(Boolean))];
}

/** „45 s“, „11 min 56 s“, „1 h 3 min“ — empty when unknown. */
export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "";
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ${seconds % 60} s`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

/** „zobrazených 12 z 100“ with the Slovak plural of the participle. */
export function shownCount(shown: number, loaded: number): string {
  const word = shown === 1 ? "zobrazený" : shown >= 2 && shown <= 4 ? "zobrazené" : "zobrazených";
  return `${shown} ${word} z ${loaded}`;
}
