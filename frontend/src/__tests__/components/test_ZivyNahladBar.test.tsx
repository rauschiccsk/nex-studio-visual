/**
 * DEV-120 — the Vizuál tab left the sidebar; the live preview opens from the Riadiace centrum in its own window.
 *
 * Director 15.10.2026: „Radšej odstráňme záložku "Vizuál". … Preto vizuál ja púšťam v Chrome a tak viem s tým pracovať.“
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import ZivyNahladBar from "@/components/riadiace/ZivyNahladBar";
import type { PipelineBoard } from "@/services/api/pipeline";

function board(stage: string, url: string | null): PipelineBoard {
  return { state: { current_stage: stage }, vizual_url: url } as unknown as PipelineBoard;
}

describe("ZivyNahladBar (DEV-120)", () => {
  it("offers the running preview in its own window during the Vizuál step", () => {
    render(<ZivyNahladBar board={board("vizual", "https://vizual-career.isnex.eu")} />);
    const link = screen.getByRole("link", { name: "Otvoriť živý náhľad" });
    expect(link).toHaveAttribute("href", "https://vizual-career.isnex.eu");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("says the preview is starting while its address is not known yet", () => {
    render(<ZivyNahladBar board={board("vizual", null)} />);
    expect(screen.getByText("Živý náhľad aplikácie sa spúšťa…")).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("shows nothing outside the Vizuál step, even with an address left from a past run", () => {
    const { container } = render(<ZivyNahladBar board={board("programovanie", "https://vizual-career.isnex.eu")} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("shows nothing before the board arrives", () => {
    const { container } = render(<ZivyNahladBar board={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
