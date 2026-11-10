/**
 * DEV-351 — Riadiace centrum of a version that has not started offers its start; a message cannot start it another way.
 *
 * Director 10.11.2026 at NEX Knowledge 1.1.0: „kde mám stlačiť začatie tvorby špecifikácie, mám prázdne okno.“ The
 * start lived only on the version's page, and the first message typed into the empty Riadiace centrum would have
 * started the version as a conversation, without the five phases — a mode no build ever used.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

const api = vi.hoisted(() => ({ postPipelineActionApi: vi.fn() }));
vi.mock("@/services/api/pipeline", () => api);

import StartBuildBar from "@/components/controlCenter/StartBuildBar";
import ConversationComposer from "@/components/controlCenter/ConversationComposer";
import type { PipelineBoard } from "@/services/api/pipeline";

const unstarted = { state: null, available_actions: ["start"], recent_messages: [] } as unknown as PipelineBoard;
const started = {
  state: { current_stage: "priprava", status: "agent_working" },
  available_actions: [],
  recent_messages: [],
} as unknown as PipelineBoard;

describe("StartBuildBar (DEV-351)", () => {
  it("says the version has not started and starts the specification with one click", async () => {
    const onBoard = vi.fn();
    api.postPipelineActionApi.mockResolvedValue(started);
    render(<StartBuildBar board={unstarted} versionId="v1" onBoard={onBoard} />);

    expect(screen.getByText(/Verzia ešte nebeží/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Spustiť tvorbu špecifikácie" }));

    await waitFor(() => expect(api.postPipelineActionApi).toHaveBeenCalledWith("v1", { action: "start" }));
    expect(onBoard).toHaveBeenCalledWith(started);
  });

  it("shows nothing once the version runs, or while the board is not read", () => {
    const { container, rerender } = render(<StartBuildBar board={started} versionId="v1" onBoard={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<StartBuildBar board={null} versionId="v1" onBoard={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("the message box of a version that has not started (DEV-351)", () => {
  it("is locked and says where to start — a message never starts the version", () => {
    render(<ConversationComposer versionId="v1" onRelay={vi.fn()} notStarted />);

    const box = screen.getByRole("textbox");
    expect(box).toBeDisabled();
    expect(box).toHaveAttribute("placeholder", "Verzia ešte nebeží — spusti tvorbu špecifikácie tlačidlom vyššie.");
  });
});

describe("Riadiace centrum never starts a version from a message (DEV-351)", () => {
  it("its message path holds no start of any mode", async () => {
    const { readFileSync } = await import("node:fs");
    const { resolve, dirname } = await import("node:path");
    const { fileURLToPath } = await import("node:url");
    const path = resolve(dirname(fileURLToPath(import.meta.url)), "../../pages/ControlCenterPage.tsx");
    const page = readFileSync(path, "utf8");
    expect(page).not.toMatch(/action:\s*"start"/);
    expect(page).not.toMatch(/mode:\s*"conversation"/);
    expect(page).toContain("<StartBuildBar");
  });
});
