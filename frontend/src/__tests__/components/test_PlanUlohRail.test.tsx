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
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import PlanUlohRail from "@/components/riadiace/PlanUlohRail";
import { getTaskPlan } from "@/services/api/versions";
import { postPipelineActionApi } from "@/services/api/pipeline";
import type { PipelineBoard, PipelineMessage, PipelineState } from "@/services/api/pipeline";
import type { TaskPlanResponse, TaskPlanTaskNode, TaskNodeStatus } from "@/types/task-plan";

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

  it("renders the honest terminal note (no cross-domain button) once the version is done in conversation mode", async () => {
    render(
      <PlanUlohRail
        versionId="v1"
        messages={[]}
        board={mkBoard({ available_actions: [], state: mkState({ status: "done", mode: "conversation" }) })}
        onBoard={() => {}}
      />,
    );
    // Static plain note pointing at the SEPARATE deploy domain — never a rung/button.
    expect(await screen.findByText(/Nasadenie \(UAT\/PROD\) je samostatný krok/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Označiť ako hotové/ })).not.toBeInTheDocument();
  });
});
