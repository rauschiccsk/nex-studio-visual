/**
 * HonestStatusStrip — the read-only Konzultácia indicator (konzultacia-mode.md Part 3).
 *
 * A TERMINAL version (current_stage === 'done' — finished / released) is answerable in read-only advisory
 * mode; the strip shows "Konzultácia — poradím, nič nezmením" so the Manažér knows typing now is advice, not
 * a build. A mid-build version shows NO such indicator.
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

describe("HonestStatusStrip — Konzultácia read-only indicator", () => {
  it("renders the read-only indicator on a terminal (done) version", () => {
    render(strip({ current_stage: "done", status: "done", mode: "conversation" }));
    expect(screen.getByText("Konzultácia — poradím, nič nezmením")).toBeInTheDocument();
  });

  it("renders the read-only indicator on a released version too (current_stage === 'done')", () => {
    render(strip({ current_stage: "done", status: "done", mode: null }));
    expect(screen.getByText("Konzultácia — poradím, nič nezmením")).toBeInTheDocument();
  });

  it("a running consult turn reads 'premýšľam…' (not the generic 'fáza done')", () => {
    render(strip({ current_stage: "done", status: "agent_working", mode: "conversation" }));
    expect(screen.getByText("Konzultácia — premýšľam…")).toBeInTheDocument();
  });

  it("shows NO consult indicator on a mid-build version", () => {
    render(strip({ current_stage: "priprava", status: "agent_working", mode: "conversation" }));
    expect(screen.queryByText("Konzultácia — poradím, nič nezmením")).not.toBeInTheDocument();
  });
});

// Honest #6: a `done` version whose verification could NOT be confirmed (unbound / repo_unreadable /
// hotovo_unbound) reads AMBER "overenie sa nedá potvrdiť", never a green "Hotovo — pripravené na nasadenie".
function stripV(state: Partial<PipelineState>, verifiedProvenance?: string | null) {
  return (
    <HonestStatusStrip
      state={state as PipelineState}
      projectName="Demo"
      versionNumber="1.0.0"
      reconnecting={false}
      error={null}
      verifiedProvenance={verifiedProvenance}
    />
  );
}

describe("HonestStatusStrip — honest verification (#6)", () => {
  it.each(["unbound", "repo_unreadable", "hotovo_unbound"])(
    "reads amber 'overenie sa nedá potvrdiť' on a done version with unconfirmable provenance '%s'",
    (prov) => {
      render(stripV({ current_stage: "done", status: "done", mode: "conversation" }, prov));
      expect(screen.getByText("Hotovo — overenie sa nedá potvrdiť")).toBeInTheDocument();
    },
  );

  it("stays green 'Hotovo — pripravené na nasadenie' when the verification is confirmed (sha_match)", () => {
    render(stripV({ current_stage: "done", status: "done", mode: "conversation" }, "sha_match"));
    expect(screen.getByText("Hotovo — pripravené na nasadenie")).toBeInTheDocument();
    expect(screen.queryByText("Hotovo — overenie sa nedá potvrdiť")).not.toBeInTheDocument();
  });
});
