// Orchestration Cockpit API client (F-007, CR-NS-018 Phase 4).
//
// Backend owns the pipeline state; the cockpit is a thin board over it. Types
// mirror backend/schemas/pipeline.py exactly.

import api from "../api";
import type { components } from "./pipeline.generated";
import type { PaginatedResponse } from "../../types/common";

// Debug-attach (CR-V2-015) break-glass targets a v2 orchestrator session — the two agents only. These are
// CHARTER-PATH SLUGS (hyphen, e.g. `ai-agent`), which the BE bridges to the DB role value (underscore,
// `ai_agent`) via `db_role_for_charter_slug`; do not pass the underscore form to the debug-terminal route.
export type DebugAttachRole = "ai-agent" | "auditor";

// ── stage / actor / status enums (GENERATED — single source of truth, v0.7.0 R2) ─────────────
//
// These unions are no longer hand-mirrored from the backend CHECK constraints; they are derived
// from `pipeline.generated.ts` (produced by `npm run codegen` ← OpenAPI ← the Pydantic `Literal`
// schemas in backend/schemas/pipeline.py). `pipeline.ts` stays the stable import surface, so
// consumers (the cockpit panels et al.) import these names unchanged while the VALUES track the
// backend automatically — a BE change that isn't regenerated fails the CI drift-gate (R2-c).
type PipelineSchemas = components["schemas"];

export type PipelineStage = PipelineSchemas["PipelineStateRead"]["current_stage"];

export type PipelineActor = PipelineSchemas["PipelineStateRead"]["current_actor"];

// `manazer` (the human operator) and `system` are message-only participants (never a state ACTOR — the
// actor union is the two agents). The BE keeps author/recipient as plain str, so this composite stays
// hand-written over the generated actor union + the two human/system authors.
export type PipelineParticipant = PipelineActor | "manazer" | "system";

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

// Per-agent liveness for the who's-up staleness chips (CR-V2-021 — v2: the two agents only).
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
  // Backend-authoritative set of schvaľovacie-body actions valid to offer right now (WS-C1, CR-NS-030;
  // dial-governed v2 verbs). The action bar renders only these; absent → fall back to the FE's own logic.
  available_actions?: PipelineActionName[];
  // Build-readiness facts (WS-C1, CR-NS-030): the FE disables the Programovanie sign-off button when not
  // satisfiable (all_tasks_done false → a todo remains; build_open_findings > 0 → a failed/unverified task)
  // and drives the Programovanie split-view task progress. Absent → permissive (don't disable).
  all_tasks_done?: boolean;
  build_open_findings?: number;
  // The Programovanie task currently in focus (WS-C2, CR-NS-035) — the who's-up status shows "#N: title".
  current_task?: { number: number; title: string } | null;
  // Per-agent liveness for the who's-up staleness chips (the two v2 agents). Absent → no indicator.
  agent_sessions?: AgentSession[];
  // CR-V2-056 (reality-anchoring): is this version VERIFIED right now, COMPUTED live from the repo (the PASS
  // verdict's bound commit SHA vs the current HEAD), not a stored 'done'. `verified_provenance === 'sha_drift'`
  // means it WAS verified but the code moved past the verified commit — the board flags a stale-PASS warning
  // instead of a frozen green. Absent (older board) → no drift indicator.
  verified?: boolean;
  verified_provenance?: string;
}

// ── action requests ──────────────────────────────────────────────────────────

// The dial-governed v2 schvaľovacie-body verbs (CR-V2-009; mirrors orchestrator._ACTIONS). The v1
// Gate-E / gate_g / Coordinator / release action verbs (approve / return / fix / leave / verdict-PASS-gate
// / apply_coordinator_recommendation / end_gate_e / end_build / continue_build / rerun_release_audit /
// surgical_fix / uat_accept / retry_publish / accept_merged) are dropped with the 4-phase model.
export type PipelineActionName =
  | "start" // Spustiť tvorbu špecifikácie
  | "approve_spec" // Schváliť špecifikáciu (end Príprava — ALWAYS mandatory, dial-independent)
  | "schvalit" // Schváliť (dial-governed advance after Návrh / Programovanie / Verifikácia)
  | "uprav" // Uprav (rework the current phase / "Skús znova" on an error block)
  | "pokracovat" // Pokračovať (resume a paused Programovanie loop)
  | "verdict" // the Auditor's Verifikácia verdict (PASS / FAIL)
  | "ask" // open a direct AI-Agent consult
  | "answer" // answer an agent QUESTION on a blocked state
  | "pause" // cooperative pause of the Programovanie loop
  | "decide" // CR-V2-041: pick one consultation Decision Card option (decision_needed)
  | "overit_znovu"; // CR-V2-057: "Over znova" — re-verify a drifted version (re-run Verifikácia vs current HEAD)

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

// CR-V2-015 / SPIKE-IO Model B: the result of relaying a Manažér message to the AI Agent. The engine is the
// SOLE writer to the warm `claude` session — the message becomes the next `--resume` turn (never a raw
// keystroke). `deferred === true` ⇒ a turn was in flight and the message was ENQUEUED behind it (it lands at
// the next turn boundary); `false` ⇒ it dispatched immediately. Mirrors backend PipelineRelayResponse.
export interface PipelineRelayResponse {
  deferred: boolean;
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

// CR-V2-015 / SPIKE-IO Model B: relay a Manažér message to the AI Agent as the engine's next turn. This is
// the canonical Manažér→AI-Agent channel for the read-only AI Agent tab — the message is RELAYED by the
// engine (the sole writer to the warm `claude` session), never keystroked into the PTY. `deferred === true`
// means a turn was in flight and the message is queued behind it.
export function relayPipelineMessageApi(
  versionId: string,
  text: string,
): Promise<PipelineRelayResponse> {
  return api.post<PipelineRelayResponse>(`/pipeline/${versionId}/relay`, { text });
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

// CR-V2-018 Helpers feed — the AI Agent's ephemeral sub-agent (Task) spawns, captured from the stream-json
// and broadcast over the pipeline WS whenever the active set changes. `count === 0` ⇒ the panel HIDES (the
// last helper finished). `line` is the Slovak "+ N pomocníci" header; `helpers` are the per-helper one-liners
// (spawn order). The Auditor is never a helper (independence — enforced backend-side).
export interface HelpersFeed {
  stage: PipelineStage;
  count: number;
  line: string;
  helpers: string[];
}

// WS frame shapes pushed by the backend (backend/api/routes/pipeline.py + services/pipeline_runner.py).
export type PipelineWsFrame =
  | { type: "state_changed"; board: PipelineBoard } // initial snapshot on connect
  | { type: "state_changed"; state: PipelineState } // delta after an action
  | { type: "message_added"; message: PipelineMessage }
  | ({ type: "agent_activity" } & ActivityLine) // live stream while agent_working
  | ({ type: "helpers" } & HelpersFeed) // CR-V2-018: ephemeral helper feed (panel hides at count 0)
  // CR-V2-015: the raw-PTY single-writer guard frame. The AI Agent tab relays through the engine (never raw
  // keystrokes), so this is mainly the break-glass console's signal; surfaced here so a frame on the shared
  // socket is handled gracefully rather than silently dropped.
  | { type: "write_rejected"; reason: string };
