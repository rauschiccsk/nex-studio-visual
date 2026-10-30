/**
 * CiGateBar — DEV-261: after Programovanie the step to Verifikácia waits for the CI of the pushed commit. While it
 * runs, „Prejsť na overenie" stands greyed out with the backend's reason; a red CI offers „Vrátiť agentovi na opravu"
 * (opravit_podla_ci) instead. Honest-by-construction: nothing without `board.ci_gate`, no fix button unless offered.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import CiGateBar from "@/components/riadiace/CiGateBar";
import { postPipelineActionApi } from "@/services/api/pipeline";
import type { PipelineBoard } from "@/services/api/pipeline";

type CiGate = NonNullable<PipelineBoard["ci_gate"]>;

vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: vi.fn() }));

const PENDING = "Programovanie je hotové — čaká sa na kontroly na GitHube; „Prejsť na overenie“ sa ponúkne, keď dobehnú.";
const RED = "Kontroly na GitHube po Programovaní zlyhali: CI #65 zlyhal krok test. Vráť prácu agentovi na opravu.";

function boardWith(gate: CiGate | null, actions: string[] = ["ask", "uprav"]): PipelineBoard {
  return {
    state: { current_stage: "programovanie" },
    recent_messages: [],
    available_actions: actions,
    ci_gate: gate,
  } as unknown as PipelineBoard;
}
const NEXT_BOARD = { state: { current_stage: "programovanie" } } as unknown as PipelineBoard;

describe("CiGateBar — the step to Verifikácia waits for CI (DEV-261)", () => {
  beforeEach(() => {
    vi.mocked(postPipelineActionApi).mockReset();
    vi.mocked(postPipelineActionApi).mockResolvedValue(NEXT_BOARD);
  });

  it("renders nothing when nothing holds the step", () => {
    const { container } = render(<CiGateBar board={boardWith(null, ["schvalit"])} versionId="v-1" onBoard={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("while CI runs shows „Prejsť na overenie“ greyed out with the backend's reason", () => {
    render(<CiGateBar board={boardWith({ state: "pending", message: PENDING })} versionId="v-1" onBoard={vi.fn()} />);
    expect(screen.getByText("Čakám na kontroly na GitHube")).toBeInTheDocument();
    expect(screen.getByText(PENDING)).toBeInTheDocument();
    const step = screen.getByRole("button", { name: "Prejsť na overenie" });
    expect(step).toBeDisabled();
    fireEvent.click(step);
    expect(postPipelineActionApi).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /Vrátiť agentovi na opravu/ })).not.toBeInTheDocument();
  });

  it("on a red CI offers the fix instead of the step and sends it with one click", async () => {
    const onBoard = vi.fn();
    render(
      <CiGateBar
        board={boardWith({ state: "red", message: RED }, ["ask", "opravit_podla_ci", "uprav"])}
        versionId="v-1"
        onBoard={onBoard}
      />,
    );
    expect(screen.getByText("Kontroly na GitHube zlyhali")).toBeInTheDocument();
    expect(screen.getByText(RED)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Prejsť na overenie" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Vrátiť agentovi na opravu/ }));
    await waitFor(() => expect(postPipelineActionApi).toHaveBeenCalledWith("v-1", { action: "opravit_podla_ci" }));
    await waitFor(() => expect(onBoard).toHaveBeenCalledWith(NEXT_BOARD));
  });

  it("on a red CI shows no fix button unless the backend offers it", () => {
    render(<CiGateBar board={boardWith({ state: "red", message: RED })} versionId="v-1" onBoard={vi.fn()} />);
    expect(screen.getByText(RED)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
