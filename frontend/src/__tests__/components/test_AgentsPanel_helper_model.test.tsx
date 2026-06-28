/**
 * AgentsPanel — the optional per-role "helper model" selector (CR-V2-038), as NEX Studio consumes it
 * from the shared nex-shared kit. The AI Agent spawns dynamic helpers; the selector lets the Manažér pick
 * the model those helpers run on (default = cheap/fast). It must appear ONLY for the helper-spawning role
 * (ai_agent), never for the Auditor, and only when helper-model options are injected.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import { AgentsPanel } from "nex-shared";

const ROLES = [
  { id: "ai_agent", label: "AI Agent" },
  { id: "auditor", label: "Auditor" },
];
const MODELS = [
  { id: "claude-opus-4-8", label: "Opus 4.8" },
  { id: "claude-haiku-4-5-20251001", label: "Haiku 4.5" },
];
const EFFORTS = ["low", "max"];

describe("AgentsPanel — helper model selector (CR-V2-038)", () => {
  it("shows the helper-model selector ONLY for roles in helperModelRoleIds", () => {
    render(
      <AgentsPanel
        roles={ROLES}
        models={MODELS}
        efforts={EFFORTS}
        helperModels={MODELS}
        helperModelRoleIds={["ai_agent"]}
        drafts={{}}
        onSave={vi.fn()}
      />,
    );
    // exactly one "Model pomocníkov" — under AI Agent, not the Auditor
    expect(screen.getAllByText("Model pomocníkov")).toHaveLength(1);
  });

  it("omits the helper-model selector entirely when helperModels is not given", () => {
    render(<AgentsPanel roles={ROLES} models={MODELS} efforts={EFFORTS} drafts={{}} onSave={vi.fn()} />);
    expect(screen.queryByText("Model pomocníkov")).toBeNull();
  });

  it("persists the chosen helper model via onSave", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    render(
      <AgentsPanel
        roles={[{ id: "ai_agent", label: "AI Agent" }]}
        models={MODELS}
        efforts={EFFORTS}
        helperModels={MODELS}
        helperModelRoleIds={["ai_agent"]}
        drafts={{}}
        onSave={onSave}
      />,
    );
    // comboboxes for the single role: [0]=Model, [1]=Úroveň, [2]=Model pomocníkov
    const helperSelect = screen.getAllByRole("combobox")[2];
    if (!helperSelect) throw new Error("helper-model select not rendered");
    fireEvent.change(helperSelect, { target: { value: "claude-opus-4-8" } });
    fireEvent.click(screen.getByText("Uložiť"));
    await waitFor(() =>
      expect(onSave).toHaveBeenCalledWith(
        "ai_agent",
        expect.objectContaining({ helperModel: "claude-opus-4-8" }),
      ),
    );
  });
});
