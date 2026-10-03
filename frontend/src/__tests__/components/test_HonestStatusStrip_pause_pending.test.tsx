/**
 * HonestStatusStrip — pauza vyžiadaná, agent ešte dorába úlohu (ICCINT-163).
 *
 * Po „Pozastaviť“ pruh hneď hlásil „Pozastavené“, hoci AI Agent ešte hodinu pracoval na rozrobenej úlohe.
 * Director podľa toho poslal agentovi pokyn — kokpit ho zapísal ako doručený a agent ho nikdy nedostal.
 * Kým slučka pauzu neuplatní, pruh musí povedať pravdu: pozastavuje sa, ale ešte sa pracuje.
 */

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import HonestStatusStrip from "@/components/riadiace/HonestStatusStrip";
import type { PipelineState } from "@/services/api/pipeline";

function strip(state: Partial<PipelineState>) {
  return (
    <HonestStatusStrip
      state={state as PipelineState}
      projectName="Demo"
      versionNumber="1.0.0"
      reconnecting={false}
      error={null}
    />
  );
}

describe("HonestStatusStrip — vyžiadaná pauza", () => {
  it("povie, že sa pozastavuje a agent ešte dorába úlohu — nie že je pozastavené", () => {
    render(strip({ current_stage: "programovanie", status: "agent_working", pause_reason: "manazer" }));
    expect(screen.getByText(/Pozastavujem — AI Agent dokončuje rozrobenú úlohu/)).toBeInTheDocument();
    expect(screen.queryByText(/^Pozastavené$/)).not.toBeInTheDocument();
  });

  it("bez vyžiadanej pauzy ostáva obyčajné „Pracuje na…“", () => {
    render(strip({ current_stage: "programovanie", status: "agent_working", pause_reason: null }));
    expect(screen.getByText(/Pracuje na Demo v1\.0\.0/)).toBeInTheDocument();
    expect(screen.queryByText(/Pozastavujem/)).not.toBeInTheDocument();
  });
});
