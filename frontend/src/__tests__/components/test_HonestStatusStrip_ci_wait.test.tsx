/**
 * HonestStatusStrip — a version waiting for its checks on GitHub (DEV-308).
 *
 * 05.11.2026, the fast fix 1.0.1 of NEX Knowledge: the next step said „… čaká na kontrolu na GitHube … Kokpit to
 * dorieši sám.“ while this strip read „Čaká na súhlas“ — and nothing could be approved. The Director: „na obrazovke
 * nie je mi jasné či niečo ešte beží alebo je to hotové“. The strip reads the board's CI gate, the one the backend
 * sets after Programovanie (DEV-261) and after Verifikácia (DEV-201).
 */

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import HonestStatusStrip from "@/components/riadiace/HonestStatusStrip";
import type { CiGate, PipelineState } from "@/services/api/pipeline";

const WAITING = "Čaká na kontroly na GitHube — kokpit pokračuje sám";
const FAILED = "Kontroly na GitHube zlyhali — vráť verziu agentovi na opravu";

function strip(stage: string, ciGate: CiGate | null) {
  return (
    <HonestStatusStrip
      state={{ current_stage: stage, status: "awaiting_manazer" } as PipelineState}
      projectName="NEX Knowledge"
      versionNumber="1.0.1"
      reconnecting={false}
      error={null}
      ciGate={ciGate}
    />
  );
}

describe("HonestStatusStrip — the checks on GitHub (DEV-308)", () => {
  it("after Verifikácia it says the cockpit goes on by itself, not that it waits for approval", () => {
    render(strip("verifikacia", { state: "pending", message: "…" }));
    expect(screen.getByText(WAITING)).toBeInTheDocument();
    expect(screen.queryByText("Čaká na súhlas")).not.toBeInTheDocument();
  });

  it("after Programovanie it says the same", () => {
    render(strip("programovanie", { state: "pending", message: "…" }));
    expect(screen.getByText(WAITING)).toBeInTheDocument();
  });

  it("red checks name the move that is left", () => {
    render(strip("programovanie", { state: "red", message: "…" }));
    expect(screen.getByText(FAILED)).toBeInTheDocument();
    expect(screen.queryByText("Čaká na súhlas")).not.toBeInTheDocument();
  });

  it("without a gate a settled version still waits for approval", () => {
    render(strip("verifikacia", null));
    expect(screen.getByText("Čaká na súhlas")).toBeInTheDocument();
  });
});
