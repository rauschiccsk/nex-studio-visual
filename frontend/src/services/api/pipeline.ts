// Orchestration Cockpit API client (F-007, CR-NS-018 Phase 4).
//
// Backend owns the pipeline state; the cockpit is a thin board over it. Types
// mirror backend/schemas/pipeline.py exactly.

import api from "../api";
import type { components } from "./pipeline.generated";
import type { PaginatedResponse } from "../../types/common";

// Debug-attach (CR-NS-018 §10) targets ANY pipeline agent's orchestrator session — a deliberately
// separate type from the spawn `AgentRole` (E3(a)/CR-NS-039 narrowed that to "coordinator"). NOT
// `PipelineActor` (that includes customer/director, which have no attachable orchestrator session).
export type DebugAttachRole = "coordinator" | "designer" | "implementer" | "auditor";

// ── stage / actor / status enums (GENERATED — single source of truth, v0.7.0 R2) ─────────────
//
// These unions are no longer hand-mirrored from the backend CHECK constraints; they are derived
// from `pipeline.generated.ts` (produced by `npm run codegen` ← OpenAPI ← the Pydantic `Literal`
// schemas in backend/schemas/pipeline.py). `pipeline.ts` stays the stable import surface, so
// consumers (ExchangePanel.tsx et al.) import these names unchanged while the VALUES track the
// backend automatically — a BE change that isn't regenerated fails the CI drift-gate (R2-c).
type PipelineSchemas = components["schemas"];

export type PipelineStage = PipelineSchemas["PipelineStateRead"]["current_stage"];

export type PipelineActor = PipelineSchemas["PipelineStateRead"]["current_actor"];

// `system` is message-only (a participant, never a state actor) — the BE keeps author/recipient as
// plain str (out of R2 scope), so this composite stays hand-written over the generated actor union.
export type PipelineParticipant = PipelineActor | "system";

export type PipelineStatus = PipelineSchemas["PipelineStateRead"]["status"];

// R4 (D1): the persisted reason a pipeline is `blocked` (generated → single source with the BE Literal +
// DB CHECK). `NonNullable` strips the `| null` so the union is the four canonical values.
export type BlockReason = NonNullable<PipelineSchemas["PipelineStateRead"]["block_reason"]>;

export type PipelineMessageKind = PipelineSchemas["PipelineMessageRead"]["kind"];

// ── row types ────────────────────────────────────────────────────────────────

export interface PipelineState {
  id: string;
  version_id: string;
  flow_type: string;
  current_stage: PipelineStage;
  current_actor: PipelineActor;
  status: PipelineStatus;
  next_action: string;
  is_regate: boolean;
  iteration: number;
  // R4 (D1): why the pipeline is `blocked` — authoritative; the banner + action bar derive question-vs-error
  // from this. `null`/absent (legacy rows, or not blocked) → the FE falls back to the `isErrorBlock` heuristic.
  block_reason?: BlockReason | null;
  created_at: string;
  updated_at: string;
}

// R4 (D3): the latest Coordinator relay/escalation triage in front of the Director (mirrors BE CoordinatorTriage).
export interface CoordinatorTriage {
  triage_class?: string | null;
  confidence?: number | null;
  proposed_action?: string | null;
}

// R4 (D4): one `is_autonomous` Coordinator decision in the board roll-up.
export interface AutonomousDecision {
  task?: number | null;
  action?: string | null;
  rationale?: string | null;
  confidence?: number | null;
}

// R4 (D4): board roll-up of autonomous Coordinator decisions (count + the recent few).
export interface AutonomousDecisionsSummary {
  count: number;
  recent: AutonomousDecision[];
}

// R4 (D5): per-role agent liveness for the rail staleness chips.
export type AgentLiveness = "idle" | "active" | "stale";
export interface AgentSession {
  role: PipelineActor;
  status: AgentLiveness;
}

export interface PipelineMessage {
  id: string;
  version_id: string;
  stage: PipelineStage;
  author: PipelineParticipant;
  recipient: PipelineParticipant;
  kind: PipelineMessageKind;
  content: string;
  status: string;
  payload: Record<string, unknown> | null;
  created_at: string;
  // Monotonic insertion order (CR-NS-018) — authoritative ordering for both the REST
  // board and incremental WS message_added frames; sort by it, don't trust arrival.
  seq: number;
}

export interface PipelineBoard {
  state: PipelineState | null;
  recent_messages: PipelineMessage[];
  // Deterministic unresolved Gate E gap count (CR-NS-018 §5) — the close-gate value,
  // not the Customer's self-reported findings array.
  gate_e_open_findings?: number;
  // Backend-authoritative set of Director actions valid to offer right now (WS-C1, CR-NS-030).
  // The action bar renders only these; absent → fall back to the FE's own hardcoded logic.
  available_actions?: PipelineActionName[];
  // Build-readiness facts (WS-C1, CR-NS-030): the FE disables the final-approve / end-build buttons
  // when not satisfiable (all_tasks_done false → a todo remains; build_open_findings > 0 → a
  // failed/unverified task). Absent → permissive (don't disable). Mirrors gate_e_open_findings.
  all_tasks_done?: boolean;
  build_open_findings?: number;
  // The build task currently in focus (WS-C2, CR-NS-035) — the "kto je na rade" board shows "#N: title".
  current_task?: { number: number; title: string } | null;
  // gate_g FAIL re-gate proposal (CR-NS-057 §F2.4) — the inferred target + rationale, present only at
  // gate_g / awaiting_director|blocked. Absent → the FE shows a plain "Verdikt FAIL".
  regate_proposal?: { entry_stage: PipelineStage; reason?: string } | null;
  // R4 (D3): the latest Coordinator relay/escalation triage in front of the Director — present only at a
  // settled state with such a directive. Absent/null → render nothing.
  coordinator_triage?: CoordinatorTriage | null;
  // R4 (D4): board roll-up of autonomous Coordinator decisions; the FE renders the line only when count > 0.
  autonomous_decisions_summary?: AutonomousDecisionsSummary | null;
  // R4 (D5): per-role agent liveness for the rail staleness chips. Absent on an older board → no indicator.
  agent_sessions?: AgentSession[];
}

// ── action requests ──────────────────────────────────────────────────────────

export type PipelineActionName =
  | "start"
  | "approve"
  | "return"
  | "ask"
  | "answer"
  | "apply_coordinator_recommendation"
  | "fix"
  | "leave"
  | "verdict"
  | "uat_accept"
  | "end_gate_e"
  | "end_build"
  | "continue_build"
  | "accept_merged"
  | "pause";

// Structured Coordinator proposal (F-008 §2 A1, E7) carried on a coordinator gate_report's
// payload.coordinator_directive. The Director approves it via apply_coordinator_recommendation and the
// orchestrator executes the matching action (F-008 §9).
export interface CoordinatorDirective {
  // Mirrors the BE CoordinatorDirective.triage_class Literal (backend/services/pipeline_status.py).
  // "programmer_routine_question" (CR-NS-103, F-009 §4.6): a routine fast_fix build question the
  // Coordinator answers itself (proposed_action="coordinator_answer_question").
  triage_class:
    | "spec_problem"
    | "programmer_guidance"
    | "nex_studio_bug"
    | "director_decision"
    | "programmer_routine_question";
  proposed_action: string;
  target?: { task_id?: string; role?: string; commit?: string };
  params?: Record<string, unknown>;
  rationale: string;
  confidence: number;
}

export interface PipelineActionRequest {
  action: PipelineActionName;
  payload?: Record<string, unknown>;
}

// Minimal shape of the agent-terminal row returned by the debug-attach endpoint.
export interface DebugTerminalSession {
  id: string;
  role: DebugAttachRole;
  project_slug: string;
  pid: number;
}

// Fast-Fix Lane (F-009, CR-NS-094/095): the result of the "Rýchla oprava" one-prompt entry — the
// auto-created PATCH version's id + its initial board snapshot. Mirrors backend FastFixStartResponse.
export interface FastFixStartResponse {
  version_id: string;
  board: PipelineBoard;
}

// ── REST ─────────────────────────────────────────────────────────────────────

export function getPipelineBoardApi(versionId: string, limit = 50): Promise<PipelineBoard> {
  return api.get<PipelineBoard>(`/pipeline/${versionId}?limit=${limit}`);
}

// Fast-Fix Lane entry (F-009 §3, CR-NS-095): one prompt → the backend auto-creates the next PATCH
// version (vX.Y.Z+1) and starts a `fast_fix` pipeline carrying the Director directive. Returns the new
// version_id (navigate the cockpit to it) + the initial board.
export function startFastFixApi(projectId: string, directive: string): Promise<FastFixStartResponse> {
  return api.post<FastFixStartResponse>("/pipeline/fast-fix", {
    project_id: projectId,
    directive,
  });
}

export function listPipelineMessagesApi(
  versionId: string,
  skip = 0,
  limit = 50,
): Promise<PaginatedResponse<PipelineMessage>> {
  return api.get<PaginatedResponse<PipelineMessage>>(
    `/pipeline/${versionId}/messages?skip=${skip}&limit=${limit}`,
  );
}

export function postPipelineActionApi(
  versionId: string,
  body: PipelineActionRequest,
): Promise<PipelineBoard> {
  return api.post<PipelineBoard>(`/pipeline/${versionId}/action`, body);
}

export function openDebugTerminalApi(
  versionId: string,
  role: DebugAttachRole,
): Promise<DebugTerminalSession> {
  return api.post<DebugTerminalSession>(
    `/pipeline/${versionId}/debug-terminal?role=${encodeURIComponent(role)}`,
  );
}

// ── WebSocket ────────────────────────────────────────────────────────────────

export function buildPipelineWsUrl(versionId: string, token: string): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const base =
    (import.meta.env.VITE_API_BASE_URL as string | undefined) ||
    `${protocol}//${window.location.host}`;
  const wsBase = base.replace(/^http/, "ws");
  return `${wsBase}/api/v1/pipeline/ws/${versionId}?token=${encodeURIComponent(token)}`;
}

// One line of live agent activity (ephemeral — never persisted).
export interface ActivityLine {
  stage: PipelineStage;
  actor: PipelineActor;
  // "status" = a per-turn active-role signal ("…pracuje") that steps the rail
  // through the agents in a round (CR-NS-018).
  kind: "tool" | "text" | "status" | "";
  line: string;
}

// WS frame shapes pushed by the backend (backend/api/routes/pipeline.py).
export type PipelineWsFrame =
  | { type: "state_changed"; board: PipelineBoard } // initial snapshot on connect
  | { type: "state_changed"; state: PipelineState } // delta after an action
  | { type: "message_added"; message: PipelineMessage }
  | ({ type: "agent_activity" } & ActivityLine); // live stream while agent_working
