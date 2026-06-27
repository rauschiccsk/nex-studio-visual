/**
 * Component tests for PipelineActivityFeed (CR-NS-018 live agent activity).
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import PipelineActivityFeed from "@/components/cockpit/PipelineActivityFeed";
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
});
