import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { DirtyTreeGuard } from "../components/riadiace/DirtyTreeGuard";
import type { GitStatus } from "@/services/api/projects";

const dirty: GitStatus = {
  clean: false,
  dirty_count: 2,
  files: [
    { code: "M", path: "backend/app/config.py" },
    { code: "??", path: "new.txt" },
  ],
  truncated: false,
};

describe("DirtyTreeGuard (v4.0.25 founding preflight)", () => {
  it("shows the count, reveals the file list on demand, and wires commit", () => {
    const onCommit = vi.fn();
    render(<DirtyTreeGuard status={dirty} onCommit={onCommit} onDiscard={vi.fn()} />);

    expect(screen.getByText(/Projekt má 2 zmeny/)).toBeInTheDocument();

    // file list is hidden until toggled
    expect(screen.queryByText("backend/app/config.py")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Zobraziť zmeny" }));
    expect(screen.getByText("backend/app/config.py")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Uložiť ich" }));
    expect(onCommit).toHaveBeenCalledTimes(1);
  });

  it("requires a confirm before discarding (destructive)", () => {
    const onDiscard = vi.fn();
    render(<DirtyTreeGuard status={dirty} onCommit={vi.fn()} onDiscard={onDiscard} />);

    // first click only arms the confirm — nothing is discarded yet
    fireEvent.click(screen.getByRole("button", { name: "Zahodiť" }));
    expect(onDiscard).not.toHaveBeenCalled();

    // the armed confirm button actually discards
    fireEvent.click(screen.getByRole("button", { name: /Naozaj zahodiť 2 zmeny/ }));
    expect(onDiscard).toHaveBeenCalledTimes(1);
  });

  it("disables the actions while busy", () => {
    render(<DirtyTreeGuard status={dirty} busy onCommit={vi.fn()} onDiscard={vi.fn()} />);
    expect(screen.getByRole("button", { name: /Ukladám/ })).toBeDisabled();
  });
});
