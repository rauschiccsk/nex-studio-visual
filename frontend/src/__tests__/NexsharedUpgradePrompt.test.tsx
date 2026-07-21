import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { NexsharedUpgradePrompt } from "../components/riadiace/NexsharedUpgradePrompt";
import type { NexsharedStatus } from "@/services/api/projects";

const status: NexsharedStatus = {
  current: "0.11.0",
  latest: "0.15.0",
  behind: 2,
  up_to_date: false,
  changelog: [{ version: "0.15.0", body: "- `[vzhľad]` slovenské labely" }],
};

describe("NexsharedUpgradePrompt (#3 auto-notify)", () => {
  it("shows the version gap + behind badge + changelog and wires the actions", () => {
    const onUpgrade = vi.fn();
    const onStay = vi.fn();
    render(<NexsharedUpgradePrompt status={status} onUpgrade={onUpgrade} onStay={onStay} />);

    expect(screen.getByText("2 verzie pozadu")).toBeInTheDocument();
    expect(screen.getByText(/slovenské labely/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Povýšiť na v0.15.0" }));
    expect(onUpgrade).toHaveBeenCalledWith("0.15.0");

    fireEvent.click(screen.getByRole("button", { name: "Ostať na v0.11.0" }));
    expect(onStay).toHaveBeenCalledTimes(1);
  });

  it("disables the actions while busy", () => {
    render(<NexsharedUpgradePrompt status={status} busy onUpgrade={vi.fn()} onStay={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Povyšujem/ })).toBeDisabled();
  });
});
