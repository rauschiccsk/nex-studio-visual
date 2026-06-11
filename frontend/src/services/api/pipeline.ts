// Orchestration Cockpit API client (F-007, CR-NS-018 Phase 4).
//
// Backend owns the pipeline state; the cockpit is a thin board over it. Types
// mirror backend/schemas/pipeline.py exactly.

import api from "../api";
import type { AgentRole } from "./agentTerminal";
import type { PaginatedResponse } from "../../types/common";

// ── stage / actor / status enums (mirror backend CHECK constraints) ──────────

export type PipelineStage =
  | "kickoff"
  | "gate_a"
  | "gate_b"
  | "gate_c"
  | "gate_d"
  | "gate_e"
  | "task_plan"
  | "build"
  | "gate_g"
  | "release"
  | "done";

export type PipelineActor =
  | "coordinator"
  | "designer"
  | "customer"
  | "implementer"
  | "auditor"
  | "director";

export type PipelineParticipant = PipelineActor | "system";

export type PipelineStatus = "agent_working" | "awaiting_director" | "blocked" | "paused" | "done";

export type PipelineMessageKind =
  | "kickoff"
  | "question"
  | "answer"
  | "gate_report"
  | "directive"
  | "approval"
  | "return"
  | "verdict"
  | "notification";

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
  created_at: string;
  updated_at: string;
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
  triage_class: "spec_problem" | "programmer_guidance" | "nex_studio_bug" | "director_decision";
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
  role: AgentRole;
  project_slug: string;
  pid: number;
}

// ── REST ─────────────────────────────────────────────────────────────────────

export function getPipelineBoardApi(versionId: string, limit = 50): Promise<PipelineBoard> {
  return api.get<PipelineBoard>(`/pipeline/${versionId}?limit=${limit}`);
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
  role: AgentRole,
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
