/**
 * DEV-297 — every action the engine offers that carries the Manažér's text has a control on Riadiace centrum.
 *
 * 03.11.2026 NEX Manager 1.5.0 stopped at its task plan. The cockpit, the agent and Poradca all told the Manažér to
 * press „Uprav“; the engine offered it, but no control on the screen sent it — Poradca's instruction for the plan went
 * out from the conversation box as a QUESTION, twice. Director: „ja som nič nevložil, je to automatizovaný proces,
 * ja stlačil som len tlačidlo.“
 *
 * The states are the engine's own (`offered-actions.generated.json`, written by
 * `backend/scripts/dump_offered_actions.py` from `determine_available_actions`; `tests/test_offered_actions_export.py`
 * fails when it goes stale). For each one the real page is rendered with its real bars, every field gets a text and
 * every button is pressed; what reached the engine is compared with what it offered. The guard asks „was an offered
 * action left without a control?“ — a state the engine learns tomorrow is checked without anyone adding it here.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

import ControlCenterPage from "@/pages/ControlCenterPage";
import { blockRecoveryOwnsInput } from "@/components/controlCenter/blockRecovery";
import { postPipelineActionApi, relayPipelineMessageApi, type PipelineBoard } from "@/services/api/pipeline";
import offered from "../fixtures/offered-actions.generated.json";

interface Row {
  flow_type: string;
  current_stage: string;
  status: string;
  block_reason: string | null;
  resume: string | null;
  pause_reason: string | null;
  available_actions: string[];
}

const { ws } = vi.hoisted(() => ({ ws: { board: null as unknown } }));

vi.mock("@/hooks/usePipelineWs", () => ({
  usePipelineWs: () => ({
    board: ws.board,
    activity: [],
    reconnecting: false,
    error: null,
    accessDenied: false,
    versionGone: false,
    setBoard: vi.fn(),
  }),
}));
vi.mock("@/store/authStore", () => {
  const auth = { user: { role: "ri", username: "admin" } };
  return { useAuthStore: (selector: (s: typeof auth) => unknown) => selector(auth) };
});
vi.mock("@/store/activeContextStore", () => {
  const context = {
    selectedProject: { slug: "nex-manager", name: "NEX Manager" },
    selectedVersion: { versionId: "v-150", versionNumber: "1.5.0" },
  };
  return { useActiveContextStore: (selector: (s: typeof context) => unknown) => selector(context) };
});
vi.mock("@/hooks/useVizualPreview", () => ({
  useVizualPreview: () => ({ preview: null, restarting: false, error: null, restart: vi.fn() }),
}));
vi.mock("@/services/api/pipeline", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/api/pipeline")>()),
  postPipelineActionApi: vi.fn(),
  relayPipelineMessageApi: vi.fn(),
  getCiStatusApi: vi.fn().mockResolvedValue({ stav: "unknown", detail: "—", sha: null }),
}));
// What only shows — the thread, the plan, the phase — sends nothing the Manažér types.
vi.mock("@/components/controlCenter/ConversationThread", () => ({ default: () => <div /> }));
vi.mock("@/components/controlCenter/PhaseBar", () => ({ default: () => <div /> }));
vi.mock("@/components/controlCenter/HonestStatusStrip", () => ({ default: () => <div /> }));
vi.mock("@/components/controlCenter/TaskPlanRail", () => ({ default: () => <div /> }));
vi.mock("@/components/controlCenter/WhereWeLeftOff", () => ({ default: () => <div /> }));
vi.mock("@/components/controlCenter/PrivateFilesPanel", () => ({ default: () => <div /> }));
vi.mock("@/components/controlCenter/BuildStatus", () => ({ default: () => <div /> }));
vi.mock("@/components/controlCenter/LivePreviewBar", () => ({ default: () => <div /> }));

/** The actions that carry what the Manažér writes. */
const TEXT_ACTIONS = ["ask", "answer", "uprav", "decide"] as const;
const TEXT = "Pokyn Manažéra k práci agenta.";

const CONSULTATION = {
  id: "m-c",
  version_id: "v-150",
  stage: "navrh",
  author: "ai_agent",
  recipient: "manazer",
  kind: "consultation",
  content: "Rozhodnutia.",
  status: "sent",
  seq: 10,
  created_at: "2026-11-03T10:00:00Z",
  payload: {
    consultation: {
      id: "c1",
      source: "auditor_upfront",
      decisions: [{ key: "d1", question: "Košík, alebo jeden klik?", options: [{ id: "a", label: "Košík" }] }],
    },
  },
};

function board(row: Row, actions: string[]): PipelineBoard {
  return {
    state: {
      current_stage: row.current_stage,
      status: row.status,
      block_reason: row.block_reason,
      flow_type: row.flow_type,
      resume_after_framework_fix: row.resume === "framework_fix",
      resume_after_restart: row.resume === "restart",
      pause_reason: row.pause_reason,
      next_action: "",
      current_actor: "ai_agent",
    },
    available_actions: actions,
    recent_messages: row.block_reason === "decision_needed" ? [CONSULTATION] : [],
  } as unknown as PipelineBoard;
}

/** The board as the screen gets it: the route drops ``schvalit`` on a conversation build and while tasks remain
 *  (pipeline.py), so a settled phase gate is checked with it and without it. */
function boards(row: Row): Array<{ name: string; board: PipelineBoard }> {
  const name = [row.flow_type, row.current_stage, row.status, row.block_reason, row.resume, row.pause_reason]
    .filter(Boolean)
    .join(" · ");
  const out = [{ name, board: board(row, row.available_actions) }];
  if (row.available_actions.includes("schvalit")) {
    out.push({ name: `${name} · bez schvalit`, board: board(row, row.available_actions.filter((a) => a !== "schvalit")) });
  }
  return out;
}

/** Which offered text actions need no control of their own here, and why — decided, not forgotten. */
function exempt(b: PipelineBoard): Set<string> {
  const state = b.state!;
  // The bar of a question / error / check is the ONE live box (crash-test 13.07.2026: a second box read as
  // „2 editory“) — it sends its own verb and the conversation box collapses.
  if (blockRecoveryOwnsInput(state)) return new Set(TEXT_ACTIONS);
  // At Vizuál the conversation box IS the change-request channel (Director 13.07.2026) — no separate „Uprav“.
  if (state.current_stage === "vizual") return new Set(["uprav"]);
  return new Set();
}

/** What the relay becomes in the engine (`relay_manazer_message`): an answer to the agent's question, else a question. */
function relayedAs(b: PipelineBoard): string {
  return b.state?.status === "blocked" && b.state.block_reason === "agent_question" ? "answer" : "ask";
}

/** Render the page, write into every field and press every button — twice over, so a control that waits for a choice
 *  first (a Decision Card: pick an option, then decide) is reached too. Which controls (by their label) sent which verb. */
async function reachable(b: PipelineBoard): Promise<Map<string, Set<string>>> {
  vi.mocked(postPipelineActionApi).mockReset().mockResolvedValue(b);
  vi.mocked(relayPipelineMessageApi).mockReset().mockResolvedValue({ deferred: false, board: b });
  ws.board = b;
  render(
    <MemoryRouter>
      <ControlCenterPage />
    </MemoryRouter>,
  );
  const sentBy = new Map<string, Set<string>>();
  // Each mock counted on its own — one list of both would shift under a new call and blame the wrong button.
  const actions = () => vi.mocked(postPipelineActionApi).mock.calls.map(([, req]) => req.action as string);
  const relays = () => vi.mocked(relayPipelineMessageApi).mock.calls.map(() => relayedAs(b));
  for (let pass = 0; pass < 2; pass++) {
    for (let i = 0; i < screen.queryAllByRole("button").length; i++) {
      for (const field of screen.queryAllByRole("textbox")) fireEvent.change(field, { target: { value: TEXT } });
      const button = screen.queryAllByRole("button")[i] as HTMLButtonElement | undefined;
      if (!button || button.disabled) continue;
      const label = (button.textContent ?? "").trim() || `#${i}`;
      const [actionsBefore, relaysBefore] = [actions().length, relays().length];
      await act(async () => {
        fireEvent.click(button);
      });
      for (const verb of [...actions().slice(actionsBefore), ...relays().slice(relaysBefore)]) {
        sentBy.set(verb, new Set([...(sentBy.get(verb) ?? []), label]));
      }
    }
  }
  cleanup();
  return sentBy;
}

const rows = (offered as Row[]).flatMap(boards).filter(({ board: b }) =>
  (b.available_actions ?? []).some((a) => (TEXT_ACTIONS as readonly string[]).includes(a)),
);

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe("Riadiace centrum — an offered action carrying the Manažér's text has a control (DEV-297)", () => {
  it("reads the engine's states, among them the stop after the task plan", () => {
    expect(rows.some(({ name }) => name === "new_version · programovanie · awaiting_manazer · bez schvalit")).toBe(true);
  });

  it.each(rows.map((r) => [r.name, r.board] as const))("%s", async (_name, b) => {
    const offeredText = (b.available_actions ?? []).filter((a) => (TEXT_ACTIONS as readonly string[]).includes(a));
    const skip = exempt(b);
    const sentBy = await reachable(b);

    const missing = offeredText.filter((a) => !skip.has(a) && !sentBy.has(a));
    expect(missing).toEqual([]);
    // …and never two controls for one verb: a second „Uprav“ next to „Upraviť“ is the „2 editory“ again.
    const twice = [...sentBy].filter(([, labels]) => labels.size > 1).map(([verb, labels]) => `${verb}: ${[...labels]}`);
    expect(twice).toEqual([]);
    // The ONE live box of a block sends exactly one verb — exempt is about the others, never about all of them.
    if (blockRecoveryOwnsInput(b.state!)) expect(offeredText.filter((a) => sentBy.has(a))).toHaveLength(1);
    // At Vizuál the box is the change channel by decision — there is no „Uprav“ there, not just none required.
    // (A failed turn's „Skús znova“ is the block's own box, above.)
    if (b.state!.current_stage === "vizual" && !blockRecoveryOwnsInput(b.state!)) expect(sentBy.has("uprav")).toBe(false);
  });
});
