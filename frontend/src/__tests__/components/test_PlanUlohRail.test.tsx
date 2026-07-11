/**
 * Component tests for PlanUlohRail — the "Plán úloh" three-layer MANAGER MAP (STEP 3,
 * docs/architecture/step3-plan-design.md).
 *
 * Pins the honest-by-construction contract:
 *   - L0 (title + status) and L1 (plain-language line) render for EVERY node; L2 (technical) is hidden by default.
 *   - An empty plain_description shows a muted "(bez ľudského vysvetlenia)" placeholder — NEVER a silent
 *     fall-back to the technical description.
 *   - The technical detail appears only AFTER expanding the node (persisted per version).
 *   - The "Zostaviť plán" trigger renders ONLY when board.available_actions offers `zostav_plan`, and fires
 *     the EXISTING postPipelineActionApi → onBoard on click.
 *   - The plan refetches when the live message stream grows (tree-freshness).
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render as rtlRender, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter } from "react-router-dom";

import PlanUlohRail from "@/components/riadiace/PlanUlohRail";
import { findCurrentTaskPath } from "@/components/riadiace/currentTaskPath";
import { getTaskPlan } from "@/services/api/versions";
import { postPipelineActionApi } from "@/services/api/pipeline";
import type { PipelineBoard, PipelineMessage, PipelineState } from "@/services/api/pipeline";
import type { TaskPlanResponse, TaskPlanTaskNode, TaskNodeStatus } from "@/types/task-plan";

// PlanUlohRail now uses useNavigate (the "Prejsť na nasadenie" done-state button), so it must render inside a
// Router. Wrap every render in a MemoryRouter via the RTL `wrapper` option (the wrapper persists across
// rerender), so all existing `render(<PlanUlohRail .../>)` call sites work unchanged.
const render = (ui: Parameters<typeof rtlRender>[0]) => rtlRender(ui, { wrapper: MemoryRouter });

vi.mock("@/services/api/versions", () => ({ getTaskPlan: vi.fn() }));
// The component's only runtime dependency on the pipeline module is postPipelineActionApi; the board/message
// types are type-only (erased). A minimal mock avoids executing the real module.
vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: vi.fn() }));

const PLAN: TaskPlanResponse = {
  plan: [
    {
      id: "e1",
      number: 1,
      title: "Základ systému",
      status: "in_progress",
      plain_description: "Toto je epik po ľudsky.",
      feats: [
        {
          id: "f1",
          number: 1,
          title: "Schéma databázy",
          status: "in_progress",
          plain_description: "Feat po ľudsky.",
          description: "Feat technický: migrácie a modely.",
          tasks: [
            {
              id: "t1",
              number: 1,
              title: "GL tabuľky",
              task_type: "migration",
              status: "in_progress",
              priority: "normal",
              checklist_type: null,
              description: "Technický detail T1: alembic 080.",
              plain_description: "Úloha T1 po ľudsky.",
            },
            {
              id: "t2",
              number: 2,
              title: "AP tabuľky",
              task_type: "migration",
              status: "todo",
              priority: "normal",
              checklist_type: null,
              description: "Technický detail T2: alembic 081.",
              plain_description: "", // empty → muted placeholder, NEVER the technical text
            },
          ],
        },
      ],
    },
  ],
  epic_count: 1,
  feat_count: 1,
  task_count: 2,
};

const EMPTY_PLAN: TaskPlanResponse = { plan: [], epic_count: 0, feat_count: 0, task_count: 0 };

function mkTask(id: string, number: number, status: TaskNodeStatus): TaskPlanTaskNode {
  return {
    id,
    number,
    title: `Úloha ${number}`,
    task_type: "migration",
    status,
    priority: "normal",
    checklist_type: null,
    description: `Technický detail ${id}.`,
    plain_description: `Úloha ${id} po ľudsky.`,
  };
}

// A single epic/feat carrying 5 leaf tasks, 2 of them done → the build-progress indicator reads "2/5" + "40 %".
const PROGRESS_PLAN: TaskPlanResponse = {
  plan: [
    {
      id: "e1",
      number: 1,
      title: "Základ systému",
      status: "in_progress",
      plain_description: "Epik po ľudsky.",
      feats: [
        {
          id: "f1",
          number: 1,
          title: "Schéma databázy",
          status: "in_progress",
          plain_description: "Feat po ľudsky.",
          description: "Feat technický.",
          tasks: [
            mkTask("t1", 1, "done"),
            mkTask("t2", 2, "done"),
            mkTask("t3", 3, "in_progress"),
            mkTask("t4", 4, "todo"),
            mkTask("t5", 5, "todo"),
          ],
        },
      ],
    },
  ],
  epic_count: 1,
  feat_count: 1,
  task_count: 5,
};

function mkBoard(over: Partial<PipelineBoard> = {}): PipelineBoard {
  return { state: null, recent_messages: [], ...over };
}

function mkState(over: Partial<PipelineState> = {}): PipelineState {
  return {
    id: "s1",
    version_id: "v1",
    flow_type: "new_version",
    current_stage: "programovanie",
    current_actor: "ai_agent",
    status: "agent_working",
    next_action: "",
    is_regate: false,
    iteration: 0,
    block_reason: null,
    created_at: "2026-07-04T00:00:00Z",
    updated_at: "2026-07-04T00:00:00Z",
    ...over,
  };
}

function mkMsg(seq: number): PipelineMessage {
  return {
    id: `m${seq}`,
    version_id: "v1",
    stage: "priprava",
    author: "ai_agent",
    recipient: "manazer",
    kind: "question",
    content: "",
    status: "delivered",
    payload: {},
    created_at: "2026-07-04T00:00:00Z",
    seq,
  };
}

describe("PlanUlohRail — three-layer manager map (STEP 3)", () => {
  beforeEach(() => {
    vi.mocked(getTaskPlan).mockReset();
    vi.mocked(postPipelineActionApi).mockReset();
    vi.mocked(getTaskPlan).mockResolvedValue(PLAN);
  });

  it("renders L0 (title + status) and L1 (plain line) for every node; L2 technical is hidden by default", async () => {
    render(<PlanUlohRail versionId="v1" messages={[]} board={mkBoard()} onBoard={() => {}} />);

    // L0 — titles + status labels.
    expect(await screen.findByText(/Základ systému/)).toBeInTheDocument();
    expect(screen.getByText(/Schéma databázy/)).toBeInTheDocument();
    expect(screen.getByText(/GL tabuľky/)).toBeInTheDocument();
    expect(screen.getByText(/AP tabuľky/)).toBeInTheDocument();
    expect(screen.getAllByText("Prebieha").length).toBeGreaterThan(0); // in_progress
    expect(screen.getByText("Čaká")).toBeInTheDocument(); // todo (t2)

    // L1 — the plain-language line, always visible.
    expect(screen.getByText(/Toto je epik po ľudsky/)).toBeInTheDocument();
    expect(screen.getByText(/Feat po ľudsky/)).toBeInTheDocument();
    expect(screen.getByText(/Úloha T1 po ľudsky/)).toBeInTheDocument();

    // L2 — technical detail hidden until expand.
    expect(screen.queryByText(/Technický detail T1/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Feat technický/)).not.toBeInTheDocument();
  });

  it("shows a muted placeholder (NOT the technical description) when plain_description is empty", async () => {
    render(<PlanUlohRail versionId="v1" messages={[]} board={mkBoard()} onBoard={() => {}} />);
    await screen.findByText(/GL tabuľky/);

    // t2 has an empty plain_description → the muted placeholder (t2 is the only empty node here).
    expect(screen.getByText("(bez ľudského vysvetlenia)")).toBeInTheDocument();
    // Must NOT silently fall back to t2's technical description.
    expect(screen.queryByText(/Technický detail T2/)).not.toBeInTheDocument();
  });

  it("reveals the L2 technical detail only after expanding the node, and persists the choice per version", async () => {
    render(<PlanUlohRail versionId="v1" messages={[]} board={mkBoard()} onBoard={() => {}} />);
    const taskRow = (await screen.findByText(/GL tabuľky/)).closest("button");
    expect(taskRow).not.toBeNull(); // a node WITH technical detail is an interactive toggle

    // Hidden before expand.
    expect(screen.queryByText(/Technický detail T1/)).not.toBeInTheDocument();

    fireEvent.click(taskRow!);

    // Visible after expand — under the node.
    expect(await screen.findByText(/Technický detail T1/)).toBeInTheDocument();
    // Persisted under the version-scoped key.
    expect(window.localStorage.getItem("nex_planrail_expanded_v1")).toContain("t1");
  });

  it("shows the 'Zostaviť plán' trigger ONLY when offered, and fires postPipelineActionApi → onBoard on click", async () => {
    const onBoard = vi.fn();
    const fresh = mkBoard({ available_actions: [] });
    vi.mocked(getTaskPlan).mockResolvedValue(EMPTY_PLAN);
    vi.mocked(postPipelineActionApi).mockResolvedValue(fresh);

    render(
      <PlanUlohRail
        versionId="v1"
        messages={[]}
        board={mkBoard({ available_actions: ["zostav_plan"] })}
        onBoard={onBoard}
      />,
    );

    const btn = await screen.findByRole("button", { name: /Zostaviť plán/ });
    fireEvent.click(btn);

    await waitFor(() => expect(postPipelineActionApi).toHaveBeenCalledWith("v1", { action: "zostav_plan" }));
    await waitFor(() => expect(onBoard).toHaveBeenCalledWith(fresh));
  });

  it("hides the 'Zostaviť plán' trigger when the backend does not offer it (honest-by-construction)", async () => {
    vi.mocked(getTaskPlan).mockResolvedValue(EMPTY_PLAN);
    render(
      <PlanUlohRail
        versionId="v1"
        messages={[]}
        board={mkBoard({ available_actions: ["approve_spec"] })}
        onBoard={() => {}}
      />,
    );
    await screen.findByText(/Plán sa objaví/);
    expect(screen.queryByRole("button", { name: /Zostaviť plán/ })).not.toBeInTheDocument();
  });

  it("refetches the plan when the live message stream grows (tree-freshness)", async () => {
    const { rerender } = render(<PlanUlohRail versionId="v1" messages={[]} board={mkBoard()} onBoard={() => {}} />);
    await screen.findByText(/Základ systému/);
    expect(getTaskPlan).toHaveBeenCalledTimes(1);

    rerender(<PlanUlohRail versionId="v1" messages={[mkMsg(1)]} board={mkBoard()} onBoard={() => {}} />);
    await waitFor(() => expect(getTaskPlan).toHaveBeenCalledTimes(2));
  });
});

describe("PlanUlohRail — Programovanie build controls (STEP 4)", () => {
  beforeEach(() => {
    vi.mocked(getTaskPlan).mockReset();
    vi.mocked(postPipelineActionApi).mockReset();
    vi.mocked(getTaskPlan).mockResolvedValue(PLAN);
  });

  it("shows the 'Práve robím' banner when board.current_task is present, hides it when null", async () => {
    const { rerender } = render(
      <PlanUlohRail
        versionId="v1"
        messages={[]}
        board={mkBoard({
          current_task: { number: 7, title: "Nastav GL tabuľky" },
          state: mkState({ status: "agent_working" }),
        })}
        onBoard={() => {}}
      />,
    );
    // Banner visible: label + "#N title".
    expect(await screen.findByText("Práve robím:")).toBeInTheDocument();
    expect(screen.getByText(/#7 Nastav GL tabuľky/)).toBeInTheDocument();

    // current_task cleared (build finished / left Programovanie) → banner gone.
    rerender(
      <PlanUlohRail versionId="v1" messages={[]} board={mkBoard({ current_task: null })} onBoard={() => {}} />,
    );
    await waitFor(() => expect(screen.queryByText("Práve robím:")).not.toBeInTheDocument());
  });

  it("shows 'Spustiť stavbu' ONLY when offered, and fires postPipelineActionApi(spustit_stavbu) → onBoard", async () => {
    const onBoard = vi.fn();
    const fresh = mkBoard({ available_actions: [] });
    vi.mocked(postPipelineActionApi).mockResolvedValue(fresh);

    render(
      <PlanUlohRail
        versionId="v1"
        messages={[]}
        board={mkBoard({ available_actions: ["spustit_stavbu"] })}
        onBoard={onBoard}
      />,
    );

    const btn = await screen.findByRole("button", { name: /Spustiť stavbu/ });
    fireEvent.click(btn);

    await waitFor(() => expect(postPipelineActionApi).toHaveBeenCalledWith("v1", { action: "spustit_stavbu" }));
    await waitFor(() => expect(onBoard).toHaveBeenCalledWith(fresh));
  });

  it("hides 'Spustiť stavbu' when the backend does not offer it (honest-by-construction)", async () => {
    render(
      <PlanUlohRail
        versionId="v1"
        messages={[]}
        board={mkBoard({ available_actions: ["approve_spec"] })}
        onBoard={() => {}}
      />,
    );
    await screen.findByText(/Základ systému/);
    expect(screen.queryByRole("button", { name: /Spustiť stavbu/ })).not.toBeInTheDocument();
  });

  it("shows 'Pokračovať v stavbe' ONLY when offered, and fires postPipelineActionApi(pokracovat) → onBoard", async () => {
    const onBoard = vi.fn();
    const fresh = mkBoard({ available_actions: [] });
    vi.mocked(postPipelineActionApi).mockResolvedValue(fresh);

    render(
      <PlanUlohRail
        versionId="v1"
        messages={[]}
        board={mkBoard({ available_actions: ["pokracovat"], state: mkState({ status: "paused" }) })}
        onBoard={onBoard}
      />,
    );

    const btn = await screen.findByRole("button", { name: /Pokračovať v stavbe/ });
    fireEvent.click(btn);

    await waitFor(() => expect(postPipelineActionApi).toHaveBeenCalledWith("v1", { action: "pokracovat" }));
    await waitFor(() => expect(onBoard).toHaveBeenCalledWith(fresh));
  });

  it("shows the amber paused note above 'Pokračovať v stavbe' when status is paused, and NOT otherwise", async () => {
    const { rerender } = render(
      <PlanUlohRail
        versionId="v1"
        messages={[]}
        board={mkBoard({ available_actions: ["pokracovat"], state: mkState({ status: "paused" }) })}
        onBoard={() => {}}
      />,
    );
    expect(await screen.findByText(/Stavba pozastavená \(token-limit\)/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Pokračovať v stavbe/ })).toBeInTheDocument();

    // pokracovat still offered but not paused (mid-loop resume affordance) → no amber note.
    rerender(
      <PlanUlohRail
        versionId="v1"
        messages={[]}
        board={mkBoard({ available_actions: ["pokracovat"], state: mkState({ status: "agent_working" }) })}
        onBoard={() => {}}
      />,
    );
    await waitFor(() => expect(screen.queryByText(/Stavba pozastavená/)).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: /Pokračovať v stavbe/ })).toBeInTheDocument();
  });

  it("renders at most one trigger button — the ladder is mutually exclusive even if the BE offers several", async () => {
    render(
      <PlanUlohRail
        versionId="v1"
        messages={[]}
        board={mkBoard({ available_actions: ["zostav_plan", "spustit_stavbu", "pokracovat"] })}
        onBoard={() => {}}
      />,
    );
    // First rung wins: zostav_plan renders, the other two do not.
    expect(await screen.findByRole("button", { name: /Zostaviť plán/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Spustiť stavbu/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Pokračovať v stavbe/ })).not.toBeInTheDocument();
  });
});

// The two controls the STEP-3 salvage dropped — surfaced as missing regressions by the live crash-test.
// (1) the build-progress indicator (CR-NS-025 Part 2, reintroduced from cockpit/TaskPlanPanel), and
// (2) the "Pozastaviť" pause rung (the BE already offers `pause` on a running Programovanie loop).
describe("PlanUlohRail — salvaged regressions: build-progress indicator + Pozastaviť", () => {
  beforeEach(() => {
    vi.mocked(getTaskPlan).mockReset();
    vi.mocked(postPipelineActionApi).mockReset();
    vi.mocked(getTaskPlan).mockResolvedValue(PLAN);
  });

  it("shows the build-progress indicator (done/total + %) from the fetched plan, and hides it when no plan", async () => {
    vi.mocked(getTaskPlan).mockResolvedValue(PROGRESS_PLAN);
    const { rerender } = render(
      <PlanUlohRail versionId="v1" messages={[]} board={mkBoard()} onBoard={() => {}} />,
    );

    // 2 of 5 tasks done → "2/5 úloh hotových" + "40 %" + a fill bar at 40 % width.
    expect(await screen.findByText(/2\/5 úloh hotových/)).toBeInTheDocument();
    expect(screen.getByText(/40\s*%/)).toBeInTheDocument();
    expect(screen.getByTestId("planrail-progress-fill")).toHaveStyle({ width: "40%" });

    // No plan (empty) → the indicator disappears (hidden on the degenerate zero-tasks / no-plan state).
    vi.mocked(getTaskPlan).mockResolvedValue(EMPTY_PLAN);
    rerender(<PlanUlohRail versionId="v1" messages={[mkMsg(1)]} board={mkBoard()} onBoard={() => {}} />);
    await waitFor(() => expect(screen.queryByTestId("planrail-progress-fill")).not.toBeInTheDocument());
  });

  it("shows 'Pozastaviť' ONLY when offered, and fires postPipelineActionApi(pause) → onBoard on click", async () => {
    const onBoard = vi.fn();
    const fresh = mkBoard({ available_actions: [] });
    vi.mocked(postPipelineActionApi).mockResolvedValue(fresh);

    render(
      <PlanUlohRail
        versionId="v1"
        messages={[]}
        board={mkBoard({ available_actions: ["pause"], state: mkState({ status: "agent_working" }) })}
        onBoard={onBoard}
      />,
    );

    const btn = await screen.findByRole("button", { name: /Pozastaviť/ });
    fireEvent.click(btn);

    await waitFor(() => expect(postPipelineActionApi).toHaveBeenCalledWith("v1", { action: "pause" }));
    await waitFor(() => expect(onBoard).toHaveBeenCalledWith(fresh));
  });

  it("hides 'Pozastaviť' when the backend does not offer it (honest-by-construction)", async () => {
    render(
      <PlanUlohRail
        versionId="v1"
        messages={[]}
        board={mkBoard({ available_actions: ["approve_spec"] })}
        onBoard={() => {}}
      />,
    );
    await screen.findByText(/Základ systému/);
    expect(screen.queryByRole("button", { name: /Pozastaviť/ })).not.toBeInTheDocument();
  });

  it("running→paused ladder shows exactly one build-loop button: Pozastaviť while running, Pokračovať when paused", async () => {
    const { rerender } = render(
      <PlanUlohRail
        versionId="v1"
        messages={[]}
        board={mkBoard({ available_actions: ["pause"], state: mkState({ status: "agent_working" }) })}
        onBoard={() => {}}
      />,
    );
    // Running build → Pozastaviť shows, Pokračovať does not.
    expect(await screen.findByRole("button", { name: /Pozastaviť/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Pokračovať v stavbe/ })).not.toBeInTheDocument();

    // Paused build → the existing Pokračovať shows, Pozastaviť does not.
    rerender(
      <PlanUlohRail
        versionId="v1"
        messages={[]}
        board={mkBoard({ available_actions: ["pokracovat"], state: mkState({ status: "paused" }) })}
        onBoard={() => {}}
      />,
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Pokračovať v stavbe/ })).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: /Pozastaviť/ })).not.toBeInTheDocument();
  });
});

// STEP 5 (Kontrola, docs/architecture/step5-kontrola-design.md): once the Programovanie build is FINISHED, the
// BE offers `skontrolovat` — the last rung of the mutually-exclusive ladder, AFTER `pause`.
describe("PlanUlohRail — Kontrola trigger (STEP 5)", () => {
  beforeEach(() => {
    vi.mocked(getTaskPlan).mockReset();
    vi.mocked(postPipelineActionApi).mockReset();
    vi.mocked(getTaskPlan).mockResolvedValue(PLAN);
  });

  it("shows 'Skontrolovať' ONLY when offered, and fires postPipelineActionApi(skontrolovat) → onBoard on click", async () => {
    const onBoard = vi.fn();
    const fresh = mkBoard({ available_actions: [] });
    vi.mocked(postPipelineActionApi).mockResolvedValue(fresh);

    render(
      <PlanUlohRail
        versionId="v1"
        messages={[]}
        board={mkBoard({ available_actions: ["skontrolovat"] })}
        onBoard={onBoard}
      />,
    );

    // The muted intro + the primary button.
    expect(await screen.findByText(/partner sám prekontroluje robotu oproti Špecifikácii/)).toBeInTheDocument();
    const btn = await screen.findByRole("button", { name: /Skontrolovať/ });
    fireEvent.click(btn);

    await waitFor(() => expect(postPipelineActionApi).toHaveBeenCalledWith("v1", { action: "skontrolovat" }));
    await waitFor(() => expect(onBoard).toHaveBeenCalledWith(fresh));
  });

  it("hides 'Skontrolovať' when the backend does not offer it (honest-by-construction)", async () => {
    render(
      <PlanUlohRail
        versionId="v1"
        messages={[]}
        board={mkBoard({ available_actions: ["approve_spec"] })}
        onBoard={() => {}}
      />,
    );
    await screen.findByText(/Základ systému/);
    expect(screen.queryByRole("button", { name: /Skontrolovať/ })).not.toBeInTheDocument();
  });

  it("is the LAST rung — a running build (pause) still wins over skontrolovat if the BE offers both", async () => {
    render(
      <PlanUlohRail
        versionId="v1"
        messages={[]}
        board={mkBoard({ available_actions: ["pause", "skontrolovat"], state: mkState({ status: "agent_working" }) })}
        onBoard={() => {}}
      />,
    );
    // canPause precedes canCheck in the ladder → Pozastaviť renders, Skontrolovať does not.
    expect(await screen.findByRole("button", { name: /Pozastaviť/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Skontrolovať/ })).not.toBeInTheDocument();
  });
});

// STEP 6 (Hotovo, docs/architecture/step6-hotovo-design.md): once the Kontrola has run, the BE offers `hotovo`
// — the Manažér's TERMINAL sign-off rung, appended AFTER `skontrolovat` so it is the LAST rung of the ladder.
describe("PlanUlohRail — Hotovo trigger (STEP 6)", () => {
  beforeEach(() => {
    vi.mocked(getTaskPlan).mockReset();
    vi.mocked(postPipelineActionApi).mockReset();
    vi.mocked(getTaskPlan).mockResolvedValue(PLAN);
  });

  it("shows 'Označiť ako hotové' ONLY when offered, and fires postPipelineActionApi(hotovo) → onBoard on click", async () => {
    const onBoard = vi.fn();
    const fresh = mkBoard({ available_actions: [] });
    vi.mocked(postPipelineActionApi).mockResolvedValue(fresh);

    render(
      <PlanUlohRail
        versionId="v1"
        messages={[]}
        board={mkBoard({ available_actions: ["hotovo"] })}
        onBoard={onBoard}
      />,
    );

    // The muted intro + the primary button.
    expect(await screen.findByText(/keď si spokojný, označ verziu ako hotovú/)).toBeInTheDocument();
    const btn = await screen.findByRole("button", { name: /Označiť ako hotové/ });
    fireEvent.click(btn);

    await waitFor(() => expect(postPipelineActionApi).toHaveBeenCalledWith("v1", { action: "hotovo" }));
    await waitFor(() => expect(onBoard).toHaveBeenCalledWith(fresh));
  });

  it("hides 'Označiť ako hotové' when the backend does not offer it (honest-by-construction)", async () => {
    render(
      <PlanUlohRail
        versionId="v1"
        messages={[]}
        board={mkBoard({ available_actions: ["approve_spec"] })}
        onBoard={() => {}}
      />,
    );
    await screen.findByText(/Základ systému/);
    expect(screen.queryByRole("button", { name: /Označiť ako hotové/ })).not.toBeInTheDocument();
  });

  it("is the LAST rung — Skontrolovať (canCheck) still wins over hotovo if the BE offers both", async () => {
    render(
      <PlanUlohRail
        versionId="v1"
        messages={[]}
        board={mkBoard({ available_actions: ["skontrolovat", "hotovo"] })}
        onBoard={() => {}}
      />,
    );
    // canCheck precedes canFinish in the ladder → Skontrolovať renders, Označiť ako hotové does not.
    expect(await screen.findByRole("button", { name: /Skontrolovať/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Označiť ako hotové/ })).not.toBeInTheDocument();
  });

  it("renders the done note + a real 'Prejsť na nasadenie' button once the version is done (mode-agnostic)", async () => {
    render(
      <PlanUlohRail
        versionId="v1"
        messages={[]}
        board={mkBoard({ available_actions: [], state: mkState({ status: "done", mode: "conversation" }) })}
        onBoard={() => {}}
      />,
    );
    // A completed version points at deployment with a REAL button (self-sufficiency: an action beats a greyed
    // sentence). Shown for ANY done build (mode-agnostic), not only conversation mode.
    expect(await screen.findByText(/Verzia je hotová a pripravená na nasadenie/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Prejsť na nasadenie/ })).toBeInTheDocument();
    // The build-loop rungs are gone (no available_actions).
    expect(screen.queryByRole("button", { name: /Označiť ako hotové/ })).not.toBeInTheDocument();
  });
});

// Director observation #3 — REAL subtree collapse (the chevron hides the whole subtree, not just L2 detail) plus
// smart auto-collapse: done work folds away by default, the live active task force-shows, a manual toggle wins.
describe("PlanUlohRail — subtree collapse + smart auto-collapse (Director #3)", () => {
  beforeEach(() => {
    vi.mocked(getTaskPlan).mockReset();
    vi.mocked(postPipelineActionApi).mockReset();
    window.localStorage.clear();
  });

  // A plan with NO in-progress task and NO done EPIC/FEAT → nothing auto-collapses / force-expands, so the manual
  // chevron is the ONLY thing that moves the subtree (isolates the collapse mechanic from the auto behaviours).
  const STABLE_PLAN: TaskPlanResponse = {
    plan: [
      {
        id: "e1",
        number: 1,
        title: "Epik A",
        status: "in_progress",
        plain_description: "Epik po ľudsky.",
        feats: [
          {
            id: "f1",
            number: 1,
            title: "Feat A",
            status: "in_progress",
            plain_description: "Feat po ľudsky.",
            description: "Feat technický.",
            tasks: [mkTask("t1", 1, "todo"), mkTask("t2", 2, "todo")],
          },
        ],
      },
    ],
    epic_count: 1,
    feat_count: 1,
    task_count: 2,
  };

  // A one-feat plan whose FEAT status is parameterised — lets a test flip it in_progress → done across a refetch.
  // Its leaf tasks are `done` throughout: task-done never collapses (only EPIC/FEAT done does), so they stay a
  // clean visibility probe for the feat's collapse.
  function doneFeatPlan(featStatus: "in_progress" | "done"): TaskPlanResponse {
    return {
      plan: [
        {
          id: "e1",
          number: 1,
          title: "Epik A",
          status: "in_progress",
          plain_description: "Epik po ľudsky.",
          feats: [
            {
              id: "f1",
              number: 1,
              title: "Feat A",
              status: featStatus,
              plain_description: "Feat po ľudsky.",
              description: "Feat technický.",
              tasks: [mkTask("t1", 1, "done"), mkTask("t2", 2, "done")],
            },
          ],
        },
      ],
      epic_count: 1,
      feat_count: 1,
      task_count: 2,
    };
  }

  it("collapsing a FEAT hides its tasks + their descriptions — only the FEAT's own header line remains", async () => {
    vi.mocked(getTaskPlan).mockResolvedValue(STABLE_PLAN);
    render(<PlanUlohRail versionId="v1" messages={[]} board={mkBoard()} onBoard={() => {}} />);

    // Subtree visible before collapse.
    expect(await screen.findByText(/Úloha 1/)).toBeInTheDocument();
    expect(screen.getByText(/Úloha 2/)).toBeInTheDocument();
    expect(screen.getByText(/Úloha t1 po ľudsky/)).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("planrail-chevron-f1"));

    // The FEAT header row stays; every task + description below it is gone, incl. the feat's OWN L1 plain line.
    expect(screen.getByText(/Feat A/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(/Úloha 1/)).not.toBeInTheDocument());
    expect(screen.queryByText(/Úloha 2/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Úloha t1 po ľudsky/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Feat po ľudsky/)).not.toBeInTheDocument();
  });

  it("collapsing an EPIC hides all its feats + tasks — only the EPIC's own header line remains", async () => {
    vi.mocked(getTaskPlan).mockResolvedValue(STABLE_PLAN);
    render(<PlanUlohRail versionId="v1" messages={[]} board={mkBoard()} onBoard={() => {}} />);

    expect(await screen.findByText(/Feat A/)).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("planrail-chevron-e1"));

    expect(screen.getByText(/Epik A/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(/Feat A/)).not.toBeInTheDocument());
    expect(screen.queryByText(/Úloha 1/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Epik po ľudsky/)).not.toBeInTheDocument();
  });

  it("persists the collapsed set to nex_planrail_collapsed_<v> and rehydrates on remount", async () => {
    vi.mocked(getTaskPlan).mockResolvedValue(STABLE_PLAN);
    const { unmount } = render(<PlanUlohRail versionId="v1" messages={[]} board={mkBoard()} onBoard={() => {}} />);
    await screen.findByText(/Feat A/);

    fireEvent.click(screen.getByTestId("planrail-chevron-f1"));

    // Persisted under the DISTINCT collapsed key (not the expanded one).
    await waitFor(() =>
      expect(window.localStorage.getItem("nex_planrail_collapsed_v1")).toContain("f1"),
    );
    expect(window.localStorage.getItem("nex_planrail_expanded_v1") ?? "").not.toContain("f1");

    unmount();

    // Fresh mount reads the collapsed set back → f1 starts collapsed (its tasks stay hidden).
    render(<PlanUlohRail versionId="v1" messages={[]} board={mkBoard()} onBoard={() => {}} />);
    await screen.findByText(/Feat A/);
    await waitFor(() => expect(screen.queryByText(/Úloha 1/)).not.toBeInTheDocument());
  });

  it("a FEAT already 'done' on first load starts collapsed (done work out of the way by default)", async () => {
    vi.mocked(getTaskPlan).mockResolvedValue(doneFeatPlan("done"));
    render(<PlanUlohRail versionId="v1" messages={[]} board={mkBoard()} onBoard={() => {}} />);

    // Header renders, subtree does not — and the collapse is persisted.
    expect(await screen.findByText(/Feat A/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(/Úloha 1/)).not.toBeInTheDocument());
    await waitFor(() =>
      expect(window.localStorage.getItem("nex_planrail_collapsed_v1")).toContain("f1"),
    );
  });

  it("auto-collapses a FEAT the MOMENT its status transitions to 'done' (fire-once on the transition)", async () => {
    vi.mocked(getTaskPlan).mockResolvedValue(doneFeatPlan("in_progress"));
    const { rerender } = render(
      <PlanUlohRail versionId="v1" messages={[]} board={mkBoard()} onBoard={() => {}} />,
    );
    // Not done yet → subtree visible.
    expect(await screen.findByText(/Úloha 1/)).toBeInTheDocument();

    // Feat flips to done; the message-growth refetch delivers the new status.
    vi.mocked(getTaskPlan).mockResolvedValue(doneFeatPlan("done"));
    rerender(<PlanUlohRail versionId="v1" messages={[mkMsg(1)]} board={mkBoard()} onBoard={() => {}} />);

    // The `* → done` transition auto-collapses it → tasks gone, header remains.
    await waitFor(() => expect(screen.queryByText(/Úloha 1/)).not.toBeInTheDocument());
    expect(screen.getByText(/Feat A/)).toBeInTheDocument();
  });

  it("force-expands the ancestors of an in-progress task even when collapsed — without mutating the saved set", async () => {
    // Saved state: e1 + f1 collapsed.
    window.localStorage.setItem("nex_planrail_collapsed_v1", JSON.stringify(["e1", "f1"]));
    const activePlan: TaskPlanResponse = {
      plan: [
        {
          id: "e1",
          number: 1,
          title: "Epik A",
          status: "in_progress",
          plain_description: "Epik po ľudsky.",
          feats: [
            {
              id: "f1",
              number: 1,
              title: "Feat A",
              status: "in_progress",
              plain_description: "Feat po ľudsky.",
              description: "Feat technický.",
              tasks: [mkTask("t1", 1, "in_progress"), mkTask("t2", 2, "todo")],
            },
          ],
        },
      ],
      epic_count: 1,
      feat_count: 1,
      task_count: 2,
    };
    vi.mocked(getTaskPlan).mockResolvedValue(activePlan);
    render(<PlanUlohRail versionId="v1" messages={[]} board={mkBoard()} onBoard={() => {}} />);

    // The active (in_progress) task is visible despite e1 + f1 being in the saved collapsed set.
    expect(await screen.findByText(/Úloha 1/)).toBeInTheDocument();
    // The override is render-time only — the saved set is byte-for-byte unchanged.
    expect(window.localStorage.getItem("nex_planrail_collapsed_v1")).toBe(JSON.stringify(["e1", "f1"]));
  });

  it("keeps a done node the user manually re-expanded open — a manual toggle wins over auto-collapse", async () => {
    vi.mocked(getTaskPlan).mockResolvedValue(doneFeatPlan("done"));
    const { rerender } = render(
      <PlanUlohRail versionId="v1" messages={[]} board={mkBoard()} onBoard={() => {}} />,
    );
    // Done feat starts collapsed.
    await screen.findByText(/Feat A/);
    await waitFor(() => expect(screen.queryByText(/Úloha 1/)).not.toBeInTheDocument());

    // Manually expand it via the chevron.
    fireEvent.click(screen.getByTestId("planrail-chevron-f1"));
    expect(await screen.findByText(/Úloha 1/)).toBeInTheDocument();

    // A refetch that STILL reports the feat as done must not re-collapse it (transition already fired once).
    rerender(<PlanUlohRail versionId="v1" messages={[mkMsg(1)]} board={mkBoard()} onBoard={() => {}} />);
    await waitFor(() => expect(getTaskPlan).toHaveBeenCalledTimes(2));
    expect(screen.getByText(/Úloha 1/)).toBeInTheDocument();
  });

  it("keeps the chevron and the title as SEPARATE interactions — the chevron collapses, the title reveals L2", async () => {
    vi.mocked(getTaskPlan).mockResolvedValue(STABLE_PLAN);
    render(<PlanUlohRail versionId="v1" messages={[]} board={mkBoard()} onBoard={() => {}} />);
    await screen.findByText(/Feat A/);

    // Clicking the FEAT title reveals its L2 technical detail — and does NOT collapse the subtree.
    fireEvent.click(screen.getByRole("button", { name: /Feat A/ }));
    expect(await screen.findByText(/Feat technický/)).toBeInTheDocument();
    expect(screen.getByText(/Úloha 1/)).toBeInTheDocument(); // subtree still shown
    expect(window.localStorage.getItem("nex_planrail_expanded_v1")).toContain("f1");
    expect(window.localStorage.getItem("nex_planrail_collapsed_v1") ?? "").not.toContain("f1");

    // Clicking the chevron collapses the subtree — the technical reveal is irrelevant once the node is folded.
    fireEvent.click(screen.getByTestId("planrail-chevron-f1"));
    await waitFor(() => expect(screen.queryByText(/Úloha 1/)).not.toBeInTheDocument());
    expect(screen.queryByText(/Feat technický/)).not.toBeInTheDocument();
  });
});

// Director observation #4 — the "Práve robím" banner shows the FULL EPIC › FEAT › TASK hierarchy (not a bare
// "#N title") when the current task is located in the fetched plan tree, and falls back to "#N title" when not.
describe("PlanUlohRail — full task-reference hierarchy in the banner (Director #4)", () => {
  beforeEach(() => {
    vi.mocked(getTaskPlan).mockReset();
    vi.mocked(postPipelineActionApi).mockReset();
    vi.mocked(getTaskPlan).mockResolvedValue(PLAN);
  });

  it("renders the 'E# epic › F# feat › T# task' path when the current task is in the plan", async () => {
    render(
      <PlanUlohRail
        versionId="v1"
        messages={[]}
        board={mkBoard({
          current_task: { number: 2, title: "AP tabuľky" },
          state: mkState({ status: "agent_working" }),
        })}
        onBoard={() => {}}
      />,
    );
    // t2 (number 2) lives under feat f1 "Schéma databázy" under epic e1 "Základ systému".
    expect(
      await screen.findByText("E1 Základ systému › F1 Schéma databázy › T2: AP tabuľky"),
    ).toBeInTheDocument();
    expect(screen.getByText("Práve robím:")).toBeInTheDocument();
  });

  it("falls back to '#N title' when the current task number is not in the plan tree", async () => {
    render(
      <PlanUlohRail
        versionId="v1"
        messages={[]}
        board={mkBoard({
          current_task: { number: 99, title: "Neznáma úloha" },
          state: mkState({ status: "agent_working" }),
        })}
        onBoard={() => {}}
      />,
    );
    await screen.findByText(/Základ systému/); // the plan has loaded (99 is absent from it)
    expect(screen.getByText(/#99 Neznáma úloha/)).toBeInTheDocument();
    // The fallback carries no hierarchy separator.
    expect(screen.queryByText(/›/)).not.toBeInTheDocument();
  });
});

// The pure ancestry lookup behind Director #4 — matched by number across the whole version (honest-by-construction).
describe("findCurrentTaskPath — pure ancestry lookup (Director #4)", () => {
  it("returns the epic/feat/task ancestry (number + title) for a task in the plan", () => {
    expect(findCurrentTaskPath(PLAN, { number: 2, title: "AP tabuľky" })).toEqual({
      epic: { number: 1, title: "Základ systému" },
      feat: { number: 1, title: "Schéma databázy" },
      task: { number: 2, title: "AP tabuľky" },
    });
  });

  it("returns null when the task number is absent (⇒ banner fallback)", () => {
    expect(findCurrentTaskPath(PLAN, { number: 99, title: "x" })).toBeNull();
  });

  it("returns null for a null plan or a null current task", () => {
    expect(findCurrentTaskPath(null, { number: 1, title: "x" })).toBeNull();
    expect(findCurrentTaskPath(PLAN, null)).toBeNull();
  });
});

// obs #3 follow-up (ux-batch2-followup Correction 1) — the REAL fix. `seenStatusRef` resets on every mount, so the
// auto-collapse-on-done effect used to treat every already-`done` EPIC/FEAT as a fresh `* → done` transition on the
// first plan-fetch after a REMOUNT, re-collapsing done nodes the Manažér had manually expanded (clobbering the
// persisted expand across a tab switch). The fix seeds `seen` from the plan on the first pass and applies the
// done-on-load default ONLY the first time a version is ever seen (collapsed localStorage key ABSENT); once the key
// exists the persisted set is respected verbatim. The null-first hydration (batch-2) + hydratedRef gating are kept.
describe("PlanUlohRail — done nodes survive a remount, not re-collapsed (obs #3 real fix)", () => {
  beforeEach(() => {
    vi.mocked(getTaskPlan).mockReset();
    vi.mocked(postPipelineActionApi).mockReset();
    window.localStorage.clear();
  });

  // One epic, two feats: f1 in_progress (the Manažér manually collapsed it — persisted), f2 done (an
  // auto-collapse-on-done target). Their leaf tasks carry distinct titles so each subtree is a clean probe.
  const TWO_FEAT_PLAN: TaskPlanResponse = {
    plan: [
      {
        id: "e1",
        number: 1,
        title: "Epik A",
        status: "in_progress",
        plain_description: "Epik po ľudsky.",
        feats: [
          {
            id: "f1",
            number: 1,
            title: "Feat Jedna",
            status: "in_progress",
            plain_description: "Feat jedna po ľudsky.",
            description: "Feat jedna technický.",
            tasks: [mkTask("t1", 1, "todo")],
          },
          {
            id: "f2",
            number: 2,
            title: "Feat Dva",
            status: "done",
            plain_description: "Feat dva po ľudsky.",
            description: "Feat dva technický.",
            tasks: [mkTask("t2", 2, "done")],
          },
        ],
      },
    ],
    epic_count: 1,
    feat_count: 2,
    task_count: 2,
  };

  // A fully-done subtree the Manažér reviewed (nex-payables shape): one in_progress EPIC (a stable container that
  // never collapses) carrying two DONE feats, each with a done leaf task under a distinct title — a clean probe.
  const DONE_FEATS_PLAN: TaskPlanResponse = {
    plan: [
      {
        id: "e1",
        number: 1,
        title: "Epik A",
        status: "in_progress",
        plain_description: "Epik po ľudsky.",
        feats: [
          {
            id: "f1",
            number: 1,
            title: "Feat Jedna",
            status: "done",
            plain_description: "Feat jedna po ľudsky.",
            description: "Feat jedna technický.",
            tasks: [mkTask("t1", 1, "done")],
          },
          {
            id: "f2",
            number: 2,
            title: "Feat Dva",
            status: "done",
            plain_description: "Feat dva po ľudsky.",
            description: "Feat dva technický.",
            tasks: [mkTask("t2", 2, "done")],
          },
        ],
      },
    ],
    epic_count: 1,
    feat_count: 2,
    task_count: 2,
  };

  it("restores the persisted collapsed set when versionId is null on first render and resolves late", async () => {
    // The Manažér had manually collapsed f1 (an in_progress feat) in a prior session.
    window.localStorage.setItem("nex_planrail_collapsed_v1", JSON.stringify(["f1"]));
    vi.mocked(getTaskPlan).mockResolvedValue(TWO_FEAT_PLAN);

    // Remount with versionId still null (the prop arrives async) → nothing hydrated yet.
    const { rerender } = render(
      <PlanUlohRail versionId={null} messages={[]} board={mkBoard()} onBoard={() => {}} />,
    );
    // versionId resolves a tick later (the store/query lands).
    rerender(<PlanUlohRail versionId="v1" messages={[]} board={mkBoard()} onBoard={() => {}} />);

    // The tree loads; f1's subtree stays hidden — its saved collapse was restored despite the null-first mount.
    expect(await screen.findByText(/Feat Jedna/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(/Úloha 1/)).not.toBeInTheDocument());
    // The saved collapse was not clobbered — f1 is still persisted.
    expect(window.localStorage.getItem("nex_planrail_collapsed_v1")).toContain("f1");
  });

  it("does NOT re-collapse a manually-expanded done FEAT on remount when the collapsed key already exists (discriminating)", async () => {
    // The Manažér was here before (key PRESENT) and had expanded the done feats (neither f1 nor f2 in the set).
    // A remount (fresh instance → seenStatusRef reset) must respect that persisted expand, NOT re-collapse. This
    // is the REAL obs #3 scenario — it FAILS against the pre-fix component (which re-collapses done-on-remount).
    window.localStorage.setItem("nex_planrail_collapsed_v1", JSON.stringify([]));
    vi.mocked(getTaskPlan).mockResolvedValue(DONE_FEATS_PLAN);

    render(<PlanUlohRail versionId="v1" messages={[]} board={mkBoard()} onBoard={() => {}} />);

    // Both done feats stay EXPANDED — their leaf tasks render (the persisted expand survived the remount).
    expect(await screen.findByText(/Úloha 1/)).toBeInTheDocument();
    expect(screen.getByText(/Úloha 2/)).toBeInTheDocument();
    // localStorage was NOT re-written to collapse the done feats — the persisted set is respected verbatim.
    const saved = window.localStorage.getItem("nex_planrail_collapsed_v1") ?? "";
    expect(saved).not.toContain("f1");
    expect(saved).not.toContain("f2");
  });

  it("still applies the done-on-load default the FIRST time a version is ever seen (collapsed key absent)", async () => {
    // Key ABSENT (cleared in beforeEach) → first-ever visit → the done-on-load default folds the done work away.
    // Proves the fix DIDN'T just disable auto-collapse-on-load — it gated it to the first-ever visit per version.
    vi.mocked(getTaskPlan).mockResolvedValue(DONE_FEATS_PLAN);

    render(<PlanUlohRail versionId="v1" messages={[]} board={mkBoard()} onBoard={() => {}} />);

    expect(await screen.findByText(/Epik A/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(/Úloha 1/)).not.toBeInTheDocument());
    expect(screen.queryByText(/Úloha 2/)).not.toBeInTheDocument();
    await waitFor(() => {
      const saved = window.localStorage.getItem("nex_planrail_collapsed_v1") ?? "";
      expect(saved).toContain("f1");
      expect(saved).toContain("f2");
    });
  });
});
