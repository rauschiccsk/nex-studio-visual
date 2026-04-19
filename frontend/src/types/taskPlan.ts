/**
 * TypeScript types for the Task Plan generation pipeline.
 *
 * Mirrors the SSE event schema emitted by
 * ``POST /api/v1/versions/{id}/generate-task-plan``.
 */

/** A single task within a feat. */
export interface TaskPlanTask {
  number: number;
  title: string;
  task_type: "backend" | "frontend" | "migration" | "test" | "docs";
  status: "todo" | "in_progress" | "done" | "failed";
}

/** A feat grouping tasks within an epic. */
export interface TaskPlanFeat {
  id: string;
  number: number;
  title: string;
  status: "todo" | "in_progress" | "done" | "failed";
  tasks: TaskPlanTask[];
}

/** An epic grouping feats within a version. */
export interface TaskPlanEpic {
  id: string;
  number: number;
  title: string;
  status: "planned" | "in_progress" | "done";
  feats: TaskPlanFeat[];
}

/** SSE event emitted during task plan generation. */
export type TaskPlanEvent =
  | { type: "progress"; message: string; percent: number }
  | {
      type: "done";
      plan: TaskPlanEpic[];
      epic_count: number;
      feat_count: number;
      task_count: number;
    }
  | { type: "error"; content: string }
  | { type: "validation_error"; content: string };
