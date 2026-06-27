/**
 * AgentTranscript — full-body + question rendering (CR-V2-032).
 *
 * The AI Agent tab previously rendered only the one-line summary (`content`), hiding the agent's full
 * human-readable report (`payload.report`) and its actual questions (`payload.question`) — so the
 * Manažér saw a terse "constatation" instead of a dialogue. These tests pin the fix: render the report
 * body and surface the question as a highlighted "your turn" block.
 */

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import { AgentTranscript } from "@/components/agent/AgentTranscript";
import type { PipelineMessage } from "@/services/api/pipeline";

function agentMsg(payload: Record<string, unknown> | null, content = "Jednoriadkové zhrnutie."): PipelineMessage {
  return {
    id: "m1",
    version_id: "v1",
    stage: "priprava",
    author: "ai_agent",
    recipient: "manazer",
    kind: "question",
    content,
    status: "delivered",
    payload,
    created_at: "2026-06-27T00:00:00Z",
    seq: 1,
  };
}

describe("AgentTranscript — full body + question (CR-V2-032)", () => {
  it("renders the agent's report body and its question, not just the one-line summary", () => {
    render(
      <AgentTranscript
        messages={[agentMsg({ report: "Toto je plný výsledok analýzy XYZ.", question: "Aký terminál chceš?" })]}
        activity={[]}
        working={false}
      />,
    );
    expect(screen.getByText(/plný výsledok analýzy XYZ/)).toBeInTheDocument(); // report body
    expect(screen.getByText(/Aký terminál chceš/)).toBeInTheDocument(); // the actual question
    expect(screen.getByText(/na rade si ty/i)).toBeInTheDocument(); // the highlighted question block label
  });

  it("falls back to the one-line content when there is no report payload", () => {
    render(<AgentTranscript messages={[agentMsg(null, "Len zhrnutie ABC.")]} activity={[]} working={false} />);
    expect(screen.getByText(/Len zhrnutie ABC/)).toBeInTheDocument();
  });
});
