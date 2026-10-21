/**
 * DEV-120 — the Vizuál tab left the sidebar; the live preview opens from the Riadiace centrum in its own window.
 *
 * Director 15.10.2026: „Radšej odstráňme záložku "Vizuál". … Preto vizuál ja púšťam v Chrome a tak viem s tým pracovať.“
 */

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import ZivyNahladBar from "@/components/riadiace/ZivyNahladBar";
import type { PipelineBoard, VizualPreview } from "@/services/api/pipeline";

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

  it("DEV-127: a dead preview says why and starts again with one click", () => {
    const onRestart = vi.fn();
    const dead: VizualPreview = {
      needed: true,
      state: "stopped",
      url: null,
      reason: "Error: Živý náhľad potrebuje texty vydaní",
    };
    render(<ZivyNahladBar board={board("vizual", "https://vizual-nex-manager.isnex.eu")} preview={dead} onRestart={onRestart} />);
    expect(screen.getByText("Živý náhľad nebeží — Error: Živý náhľad potrebuje texty vydaní")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Otvoriť živý náhľad" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Spustiť náhľad znova" }));
    expect(onRestart).toHaveBeenCalledOnce();
  });

  it("DEV-127: while it starts again the button waits and a failure is said", () => {
    const dead: VizualPreview = { needed: true, state: "missing", url: null, reason: null };
    render(
      <ZivyNahladBar
        board={board("vizual", null)}
        preview={dead}
        restarting
        error="Spustenie náhľadu zlyhalo — Živý náhľad sa nepodarilo spustiť."
        onRestart={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "Spúšťam náhľad…" })).toBeDisabled();
    expect(screen.getByText("Spustenie náhľadu zlyhalo — Živý náhľad sa nepodarilo spustiť.")).toBeInTheDocument();
  });

  it("DEV-127: a running preview opens at the address the cockpit measured", () => {
    const live: VizualPreview = { needed: true, state: "running", url: "https://vizual-nex-manager.isnex.eu", reason: null };
    render(<ZivyNahladBar board={board("vizual", null)} preview={live} />);
    expect(screen.getByRole("link", { name: "Otvoriť živý náhľad" })).toHaveAttribute("href", "https://vizual-nex-manager.isnex.eu");
  });
});

describe("ZivyNahladBar — a Web project (DEV-162)", () => {
  const admin = {
    name: "admin",
    label: "Administrácia",
    directory: "frontend",
    state: "running" as const,
    url: "https://vizual-kariera.isnex.eu",
    reason: null,
  };
  const web = { ...admin, name: "web", label: "Web", directory: "web", url: "https://vizualweb-kariera.isnex.eu" };
  const both: VizualPreview = { needed: true, state: "running", url: admin.url, reason: null, frontends: [admin, web] };

  it("opens each frontend's preview by its name", () => {
    render(<ZivyNahladBar board={board("vizual", admin.url)} preview={both} />);

    const links = screen.getAllByRole("link").map((link) => [link.textContent, link.getAttribute("href")]);
    expect(links).toEqual([
      ["Otvoriť: Administrácia", "https://vizual-kariera.isnex.eu"],
      ["Otvoriť: Web", "https://vizualweb-kariera.isnex.eu"],
    ]);
  });

  it("a web preview that is down stops both links and says which one", () => {
    const down = { ...web, state: "stopped" as const, url: null, reason: "Error: port 5173 is in use" };
    const webDown: VizualPreview = { ...both, state: "stopped", reason: "Web: Error: port 5173 is in use", frontends: [admin, down] };
    render(<ZivyNahladBar board={board("vizual", admin.url)} preview={webDown} onRestart={vi.fn()} />);

    expect(screen.getByText("Živý náhľad nebeží — Web: Error: port 5173 is in use")).toBeInTheDocument();
    expect(screen.queryByRole("link")).toBeNull();
  });
});
