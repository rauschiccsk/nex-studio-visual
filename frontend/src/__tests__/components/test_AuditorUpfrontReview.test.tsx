/**
 * AuditorUpfrontReview — the independent Auditor's UPFRONT verdict + findings, pinned at the Návrh
 * decision point (CR-V2-039). Without it the Manažér could approve a spec the Auditor flagged as holed.
 */

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import { AuditorUpfrontReview } from "@/components/cockpit/AuditorUpfrontReview";
import type { PipelineMessage } from "@/services/api/pipeline";

function msg(over: Partial<PipelineMessage>): PipelineMessage {
  return {
    id: over.id ?? "m1",
    version_id: "v1",
    stage: "navrh",
    author: "auditor",
    recipient: "manazer",
    kind: "verdict",
    content: "",
    status: "delivered",
    payload: null,
    created_at: "2026-06-28T00:00:00Z",
    seq: over.seq ?? 1,
    ...over,
  } as PipelineMessage;
}

describe("AuditorUpfrontReview (CR-V2-039)", () => {
  it("renders the upfront verdict + findings with the blocking warning", () => {
    const messages = [
      msg({
        seq: 71,
        payload: {
          upfront_review: true,
          findings: ["A: náklady nadhodnotené ~2,2×", "B: Telegram cez zdieľaný bot", "C: kontajner vs lokálni agenti"],
          proposed_fix: "Deduplikuj podľa message.id; vlastný bot; backend na hostiteli.",
        },
      }),
    ];
    render(<AuditorUpfrontReview messages={messages} />);
    expect(screen.getByText(/nezávislá predbežná previerka/)).toBeInTheDocument();
    expect(screen.getByText(/3 nálezy — vyrieš \(Uprav\) pred schválením/)).toBeInTheDocument();
    expect(screen.getByText(/náklady nadhodnotené/)).toBeInTheDocument();
  });

  it("renders nothing when there is no upfront verdict", () => {
    const { container } = render(
      <AuditorUpfrontReview messages={[msg({ kind: "gate_report", payload: { report: "x" } })]} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("ignores a non-upfront verdict (e.g. the Verifikácia end verdict)", () => {
    const { container } = render(
      <AuditorUpfrontReview
        messages={[msg({ stage: "verifikacia", payload: { findings: ["x"], upfront_review: false } })]}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("shows a PASS state when the upfront verdict has no findings", () => {
    render(<AuditorUpfrontReview messages={[msg({ payload: { upfront_review: true, findings: [] } })]} />);
    expect(screen.getByText(/bez nálezov \(v poriadku\)/)).toBeInTheDocument();
  });

  // Honest #13: a fail-open review (crashed / parse-failed → treated as "no hole") must NOT read as a clean
  // pass. The card warns the Manažér that the safety net didn't run.
  it("warns 'neprebehla' when the review failed via the system notification (current backend)", () => {
    render(
      <AuditorUpfrontReview
        messages={[
          msg({
            seq: 80,
            author: "system",
            kind: "notification",
            content: "Upfront previerka Auditora sa nepodarila ani po opakovaných pokusoch — pokračuje sa bez nej.",
            payload: null,
          }),
        ]}
      />,
    );
    expect(screen.getByText(/Predbežná previerka neprebehla — posúď návrh sám/)).toBeInTheDocument();
  });

  it("warns 'neprebehla' when the review is flagged failed in the payload", () => {
    render(
      <AuditorUpfrontReview
        messages={[
          msg({ seq: 81, author: "system", kind: "notification", content: "x", payload: { upfront_review_failed: true } }),
        ]}
      />,
    );
    expect(screen.getByText(/Predbežná previerka neprebehla/)).toBeInTheDocument();
  });

  it("a LATER successful verdict supersedes an earlier fail-open note (shows the verdict, not the warning)", () => {
    render(
      <AuditorUpfrontReview
        messages={[
          msg({ id: "f", seq: 10, author: "system", kind: "notification", content: "Upfront previerka sa nepodarila", payload: null }),
          msg({ id: "v", seq: 20, payload: { upfront_review: true, findings: [] } }),
        ]}
      />,
    );
    expect(screen.getByText(/bez nálezov \(v poriadku\)/)).toBeInTheDocument();
    expect(screen.queryByText(/Predbežná previerka neprebehla/)).not.toBeInTheDocument();
  });
});
