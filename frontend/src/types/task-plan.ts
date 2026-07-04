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
  /** Technical (L2) detail — the programmer's files/functions, shown only on expand. */
  description: string;
  /** Plain-language (L1) one-liner for the Manažér — jargon-free; "" ⇒ FE muted placeholder (STEP 3). */
  plain_description: string;
}

export interface TaskPlanFeatNode {
  id: string;
  number: number;
  title: string;
  status: TaskNodeStatus;
  /** Technical (L2) detail — shown only on expand (STEP 3). */
  description: string;
  /** Plain-language (L1) one-liner for the Manažér (STEP 3). */
  plain_description: string;
  tasks: TaskPlanTaskNode[];
}

export interface TaskPlanEpicNode {
  id: string;
  number: number;
  title: string;
  status: EpicNodeStatus;
  /** Plain-language (L1) one-liner — the Epic's ONLY prose (no technical description column) (STEP 3). */
  plain_description: string;
  feats: TaskPlanFeatNode[];
}

export interface TaskPlanResponse {
  plan: TaskPlanEpicNode[];
  epic_count: number;
  feat_count: number;
  task_count: number;
}
