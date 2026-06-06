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

export type PipelineStatus = "agent_working" | "awaiting_director" | "blocked" | "done";

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
}

export interface PipelineBoard {
  state: PipelineState | null;
  recent_messages: PipelineMessage[];
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
  | "pause";

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
