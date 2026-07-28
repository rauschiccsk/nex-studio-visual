/**
 * GettingStartedPage ("Ako začať") — the onboarding contract for a NON-EXPERT operator (audit 2026-07-28).
 *
 * This page is the only place a Tibor/Nazar learns what to expect from a build. It taught FOUR phases and
 * never mentioned Vizuál — precisely the phase that STOPS the build and waits for their approval. An
 * operator who was promised four phases reads that stop as the app being stuck.
 *
 * These pin the page's phase list to PHASE_ORDER (the cockpit's own source of truth), so adding a phase to
 * the engine without teaching it here fails loudly.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import GettingStartedPage from "@/pages/GettingStartedPage";
import { PHASE_LABELS, PHASE_ORDER } from "@/components/cockpit/labels";

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => vi.fn() };
});

describe("GettingStartedPage — phase onboarding", () => {
  it("names every build phase the cockpit can show", () => {
    render(<GettingStartedPage />);
    // 'done' ("Hotovo") is an end state, not a phase the operator waits through — it is covered by the
    // deployment step ("Keď je verzia Hotová…") rather than the phase list.
    const phases = PHASE_ORDER.filter((p) => p !== "done");
    for (const phase of phases) {
      const label = PHASE_LABELS[phase];
      expect(screen.getAllByText(label, { exact: false }).length).toBeGreaterThan(0);
    }
  });

  it("explains Vizuál as an approval that gates programming", () => {
    render(<GettingStartedPage />);
    expect(screen.getByText("Vizuál")).toBeInTheDocument();
    // The operator must learn that the build WAITS for them here, and that approving is binding.
    expect(screen.getByText(/živý náhľad aplikácie/i)).toBeInTheDocument();
    expect(screen.getByText(/Bez tvojho schválenia sa ďalej nepokračuje/i)).toBeInTheDocument();
  });

  it("promises the correct number of phases in the step title", () => {
    render(<GettingStartedPage />);
    expect(screen.getByText(/Sleduj päť fáz/)).toBeInTheDocument();
    expect(screen.queryByText(/štyri fázy/)).not.toBeInTheDocument();
  });
});
