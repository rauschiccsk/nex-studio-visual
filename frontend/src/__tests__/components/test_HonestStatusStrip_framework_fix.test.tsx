/**
 * HonestStatusStrip — the status line of a build our technical team just released (ICCINT-13).
 *
 * The strip is the one sentence the Manažér reads first. For a build that stopped on a NEX Studio bug and
 * has just been repaired, the generic "Čaká na súhlas" would be wrong twice over: nothing is being approved,
 * and the reader has been looking at "NEX Studio má chybu" until a moment ago, so silence about the repair
 * reads as "still broken". It must say what changed.
 */

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import HonestStatusStrip from "@/components/riadiace/HonestStatusStrip";
import type { PipelineState } from "@/services/api/pipeline";

function strip(state: Partial<PipelineState> | null) {
  return (
    <HonestStatusStrip
      state={state as PipelineState | null}
      projectName="Demo"
      versionNumber="1.0.0"
      reconnecting={false}
      error={null}
    />
  );
}

describe("HonestStatusStrip — after a NEX Studio fix", () => {
  it("while still blocked it says the technical team is on it", () => {
    render(strip({ current_stage: "programovanie", status: "blocked", block_reason: "framework_issue" }));
    expect(screen.getByText(/rieši ju náš technický tím/)).toBeInTheDocument();
  });

  it("once released it says the bug is fixed and names the next move", () => {
    render(
      strip({ current_stage: "programovanie", status: "awaiting_manazer", resume_after_framework_fix: true }),
    );
    expect(screen.getByText(/opravená/)).toBeInTheDocument();
    expect(screen.getByText(/Pokračovať/)).toBeInTheDocument();
    // Not the generic wait sentence — and no trace of the "still broken" line it replaces.
    expect(screen.queryByText("Čaká na súhlas")).not.toBeInTheDocument();
    expect(screen.queryByText(/rieši ju náš technický tím/)).not.toBeInTheDocument();
  });

  it("an ordinary settled build is unaffected", () => {
    render(strip({ current_stage: "navrh", status: "awaiting_manazer" }));
    expect(screen.getByText("Čaká na súhlas")).toBeInTheDocument();
  });
});
