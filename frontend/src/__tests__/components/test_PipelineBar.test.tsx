/**
 * Version-card PipelineBar completion semantics (CR-NS-075).
 *
 * STEPS = 7 fixed segments. Green = done, purple = in-progress highlight,
 * grey = remaining. The bar maps the epics-done RATIO onto the scale, not
 * the raw count:
 *   - released            → every segment green (shipped = complete)
 *   - active + partway     → proportional green + one purple "current" segment
 *   - planned / 0-epic     → all grey (no purple)
 */

import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import { PipelineBar } from "@/pages/ProjectDetailPage";
import type { Version, VersionStatus } from "@/types/version";

const GREEN = "bg-[var(--color-status-success)]";
const PURPLE = "bg-primary-500";
const GREY = "bg-[var(--color-surface-active)]";

function mkVersion(over: Partial<Version> & { status: VersionStatus }): Version {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    project_id: "22222222-2222-2222-2222-222222222222",
    version_number: "v0.1",
    name: null,
    description: null,
    target_date: null,
    release_date: null,
    created_at: "2026-06-15T00:00:00Z",
    updated_at: "2026-06-15T00:00:00Z",
    epic_count: 0,
    epics_done: 0,
    bug_count: 0,
    ...over,
  };
}

/** Classify each of the 7 segment divs as G(reen) / P(urple) / .(grey). */
function segments(container: HTMLElement): string[] {
  // The bar is the first div; its 7 direct children are the segments.
  const bar = container.querySelector("div")!;
  const divs = Array.from(bar.children) as HTMLDivElement[];
  return divs.map((d) => {
    if (d.className.includes(GREEN)) return "G";
    if (d.className.includes(PURPLE)) return "P";
    if (d.className.includes(GREY)) return ".";
    return "?";
  });
}

describe("PipelineBar — completion semantics (CR-NS-075)", () => {
  it("released version → all 7 segments green", () => {
    const { container } = render(
      <PipelineBar version={mkVersion({ status: "released", epic_count: 3, epics_done: 3 })} />,
    );
    expect(segments(container)).toEqual(["G", "G", "G", "G", "G", "G", "G"]);
  });

  it("released with 0 epics still reads complete (all green)", () => {
    const { container } = render(
      <PipelineBar version={mkVersion({ status: "released", epic_count: 0, epics_done: 0 })} />,
    );
    expect(segments(container)).toEqual(["G", "G", "G", "G", "G", "G", "G"]);
  });

  it("fully-done active version → all green, no purple", () => {
    // round(3/3 * 7) = 7 → filled === STEPS → no in-progress segment.
    const { container } = render(
      <PipelineBar version={mkVersion({ status: "active", epic_count: 3, epics_done: 3 })} />,
    );
    expect(segments(container)).toEqual(["G", "G", "G", "G", "G", "G", "G"]);
  });

  it("partway active version → proportional green + one purple", () => {
    // round(1/3 * 7) = 2 green, segment 3 purple, rest grey.
    const { container } = render(
      <PipelineBar version={mkVersion({ status: "active", epic_count: 3, epics_done: 1 })} />,
    );
    expect(segments(container)).toEqual(["G", "G", "P", ".", ".", ".", "."]);
  });

  it("planned version → all grey, no purple", () => {
    const { container } = render(
      <PipelineBar version={mkVersion({ status: "planned", epic_count: 3, epics_done: 0 })} />,
    );
    expect(segments(container)).toEqual([".", ".", ".", ".", ".", ".", "."]);
  });

  it("active 0-epic version → all grey, no purple", () => {
    const { container } = render(
      <PipelineBar version={mkVersion({ status: "active", epic_count: 0, epics_done: 0 })} />,
    );
    expect(segments(container)).toEqual([".", ".", ".", ".", ".", ".", "."]);
  });
});
