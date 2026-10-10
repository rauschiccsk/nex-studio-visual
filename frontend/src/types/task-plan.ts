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

/**
 * DEV-49 — what the agent spent on one node (backend ``metrics.node_spend``): the time it actually worked (its
 * turns), the Náklady price rounded UP once (``eur_complete`` false = part of the spend could not be priced, the
 * reasons in ``unpriced``), and every kind of token the price is made of. ``null`` = not worked on yet.
 */
export interface NodeSpend {
  seconds: number;
  turns: number;
  tokens: { input: number; output: number; cache_read: number; cache_write: number; total: number };
  eur: number | null;
  eur_complete: boolean;
  unpriced: string[];
}

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
  /** DEV-49 — what the agent spent on this task. */
  spend?: NodeSpend | null;
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
  /** DEV-49 — the sum of its tasks. */
  spend?: NodeSpend | null;
}

export interface TaskPlanEpicNode {
  id: string;
  number: number;
  title: string;
  status: EpicNodeStatus;
  /** Plain-language (L1) one-liner — the Epic's ONLY prose (no technical description column) (STEP 3). */
  plain_description: string;
  feats: TaskPlanFeatNode[];
  /** DEV-49 — the sum of its FEATs. */
  spend?: NodeSpend | null;
}

export interface TaskPlanResponse {
  plan: TaskPlanEpicNode[];
  epic_count: number;
  feat_count: number;
  task_count: number;
}
