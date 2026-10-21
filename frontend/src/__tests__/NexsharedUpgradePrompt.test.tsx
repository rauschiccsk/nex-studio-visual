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
  frontends: [{ directory: "frontend", label: "Aplikácia", current: "0.11.0" }],
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

  it("names the version of each frontend of a Web project and says both move together (DEV-161)", () => {
    const web: NexsharedStatus = {
      ...status,
      current: "0.11.0",
      frontends: [
        { directory: "frontend", label: "Administrácia", current: "0.14.0" },
        { directory: "web", label: "Web", current: "0.11.0" },
      ],
    };
    render(<NexsharedUpgradePrompt status={web} onUpgrade={vi.fn()} onStay={vi.fn()} />);

    const items = screen.getAllByRole("listitem").map((item) => item.textContent);
    expect(items).toEqual(["Administrácia v0.14.0", "Web v0.11.0"]);
    expect(screen.getByText("Povýšenie zmení všetky naraz.")).toBeInTheDocument();
  });

  it("does not list a single frontend", () => {
    render(<NexsharedUpgradePrompt status={status} onUpgrade={vi.fn()} onStay={vi.fn()} />);
    expect(screen.queryByRole("listitem")).toBeNull();
    expect(screen.queryByText("Povýšenie zmení všetky naraz.")).toBeNull();
  });

  it("disables the actions while busy", () => {
    render(<NexsharedUpgradePrompt status={status} busy onUpgrade={vi.fn()} onStay={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Povyšujem/ })).toBeDisabled();
  });
});
