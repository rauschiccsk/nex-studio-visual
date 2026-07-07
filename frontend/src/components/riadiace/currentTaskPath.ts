// currentTaskPath — the pure ancestry lookup behind the "Práve robím" banner's full task reference (Director
// observation #4). Extracted from PlanUlohRail.tsx so the component file exports only components (react-refresh)
// and the lookup can be unit-tested in isolation.

import type { TaskPlanResponse } from "../../types/task-plan";

// The EPIC › FEAT › TASK ancestry of the current build task, each level as number + title.
export interface CurrentTaskPath {
  epic: { number: number; title: string };
  feat: { number: number; title: string };
  task: { number: number; title: string };
}

// Locate board.current_task inside the fetched plan tree so the banner can show the FULL hierarchy instead of a
// bare "#N title". current_task carries only {number, title}; the ancestry lives in the tree. Match the leaf
// TASK by number — task numbers are honest-by-construction sequential across the WHOLE version
// (orchestrator.current_build_task orders the version's tasks by number), so a by-number match is safe. Single
// O(n) pass; returns null when the task isn't in the tree yet ⇒ the caller falls back to "#N title".
export function findCurrentTaskPath(
  plan: TaskPlanResponse | null,
  current: { number: number; title: string } | null | undefined,
): CurrentTaskPath | null {
  if (!plan || !current) return null;
  for (const epic of plan.plan) {
    for (const feat of epic.feats) {
      for (const task of feat.tasks) {
        if (task.number === current.number) {
          return {
            epic: { number: epic.number, title: epic.title },
            feat: { number: feat.number, title: feat.title },
            task: { number: task.number, title: task.title },
          };
        }
      }
    }
  }
  return null;
}
