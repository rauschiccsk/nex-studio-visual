/**
 * TypeScript types for the task-plan tree (F-007 task-plan node, CR-NS-020 CR-5).
 *
 * Mirrors the ``GET /versions/{version_id}/task-plan`` response
 * (``backend.api.routes.versions._TaskPlanResponse``): the EPIC → FEAT → TASK
 * decomposition the Designer materialized, with per-node status, consumed by the
 * cockpit ``TaskPlanPanel``.
 */

/** Epic lifecycle status (``epics.status``). */
export type EpicNodeStatus = "planned" | "in_progress" | "done";

/** Feat / Task lifecycle status (``feats.status`` / ``tasks.status``). */
export type TaskNodeStatus = "todo" | "in_progress" | "done" | "failed";

export interface TaskPlanTaskNode {
  id: string;
  number: number;
  title: string;
  task_type: string;
  status: TaskNodeStatus;
  priority: string;
  checklist_type: string | null;
  description: string;
}

export interface TaskPlanFeatNode {
  id: string;
  number: number;
  title: string;
  status: TaskNodeStatus;
  tasks: TaskPlanTaskNode[];
}

export interface TaskPlanEpicNode {
  id: string;
  number: number;
  title: string;
  status: EpicNodeStatus;
  feats: TaskPlanFeatNode[];
}

export interface TaskPlanResponse {
  plan: TaskPlanEpicNode[];
  epic_count: number;
  feat_count: number;
  task_count: number;
}
