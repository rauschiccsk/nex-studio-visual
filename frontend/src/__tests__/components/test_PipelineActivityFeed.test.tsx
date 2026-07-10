/**
 * Component tests for PipelineActivityFeed (CR-NS-018 live agent activity).
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import PipelineActivityFeed from "@/components/cockpit/PipelineActivityFeed";
import { humanizeActivityLine } from "@/components/cockpit/humanizeActivityLine";
import type { ActivityLine } from "@/services/api/pipeline";

function line(over: Partial<ActivityLine> = {}): ActivityLine {
  return { stage: "programovanie", actor: "ai_agent", kind: "tool", line: "číta x.md", ...over };
}

describe("PipelineActivityFeed", () => {
  it("renders each activity line", () => {
    render(
      <PipelineActivityFeed
        activity={[line({ line: "číta spec.md" }), line({ line: "spúšťa: pytest -q", kind: "tool" })]}
      />,
    );
    expect(screen.getByText("číta spec.md")).toBeInTheDocument();
    expect(screen.getByText("spúšťa: pytest -q")).toBeInTheDocument();
  });

  it("shows a starting placeholder when empty", () => {
    render(<PipelineActivityFeed activity={[]} />);
    expect(screen.getByText(/Agent štartuje/)).toBeInTheDocument();
  });

  it("always shows the live header", () => {
    render(<PipelineActivityFeed activity={[line()]} />);
    expect(screen.getByText(/Živá aktivita agenta/)).toBeInTheDocument();
  });

  // Bug 2 (cockpit-timeout-and-activity-fix.md): a long line must WRAP + be fully readable, not clip to
  // one row. The Tailwind `truncate` class (overflow-hidden + ellipsis + nowrap) was the cause.
  it("wraps a long activity line instead of truncating it", () => {
    const long = "toto je veľmi dlhý riadok živej aktivity agenta ".repeat(8).trim();
    render(<PipelineActivityFeed activity={[line({ line: long })]} />);
    const el = screen.getByText(long);
    expect(el).not.toHaveClass("truncate");
    expect(el).toHaveClass("whitespace-pre-wrap");
    expect(el).toHaveClass("break-words");
  });

  // Bug 2 (same theme): the feed must not leak raw internal sentinel markers — strip the `<<<…>>>`
  // markers + the raw JSON payload and show the human prose that preceded them.
  it("strips leaked internal markers and raw JSON to a human line", () => {
    render(
      <PipelineActivityFeed
        activity={[line({ line: 'skladám plán úloh <<<TASK_PLAN_JSON>>> {"verdict":"reject","corrected_scope":""}' })]}
      />,
    );
    expect(screen.queryByText(/<<<TASK_PLAN_JSON>>>/)).not.toBeInTheDocument();
    expect(screen.getByText("skladám plán úloh")).toBeInTheDocument();
  });
});

describe("humanizeActivityLine", () => {
  it("leaves a clean line untouched", () => {
    expect(humanizeActivityLine("číta spec.md")).toBe("číta spec.md");
    // A legit command with brackets must NOT be clipped (only marker-bearing lines are collapsed).
    expect(humanizeActivityLine('spúšťa: pytest -q -k "test[1]"')).toBe('spúšťa: pytest -q -k "test[1]"');
  });

  it("strips sentinel markers + the JSON payload, keeping preceding prose", () => {
    expect(humanizeActivityLine('hotové <<<PIPELINE_STATUS>>> {"stage":"programovanie"}')).toBe("hotové");
    expect(humanizeActivityLine("plán <<<END_TASK_PLAN_JSON>>>")).toBe("plán");
  });

  it("falls back to a human placeholder when only machine noise remains", () => {
    const out = humanizeActivityLine('<<<TASK_PLAN_JSON>>> {"verdict":"reject"}');
    expect(out).not.toContain("<<<");
    expect(out).not.toContain("{");
    expect(out.length).toBeGreaterThan(0);
  });
});
