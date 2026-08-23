/**
 * ConversationThread — full-body + question rendering (CR-V2-032; spine STEP 1).
 *
 * Retargeted from the CUT agent-transcript test onto the salvage-copy ConversationThread (identical
 * rendering). The thread previously rendered only the one-line summary (`content`), hiding the agent's full
 * human-readable report (`payload.report`) and its actual questions (`payload.question`) — so the Manažér
 * saw a terse "constatation" instead of a dialogue. These tests pin the fix: render the report body and
 * surface the question as a highlighted "your turn" block.
 */

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import { ConversationThread } from "@/components/riadiace/ConversationThread";
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

describe("ConversationThread — full body + question (CR-V2-032)", () => {
  it("renders the agent's report body and its question, not just the one-line summary", () => {
    render(
      <ConversationThread
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
    render(<ConversationThread messages={[agentMsg(null, "Len zhrnutie ABC.")]} activity={[]} working={false} />);
    expect(screen.getByText(/Len zhrnutie ABC/)).toBeInTheDocument();
  });

  // Director observation #6: the system framework_issue message (agent → Dedo escalation) is accented +
  // surfaces the escalation message that went to Dedo.
  it("renders the framework_issue system message with the Dedo badge + the escalation message", () => {
    const systemMsg: PipelineMessage = {
      id: "fi1",
      version_id: "v1",
      stage: "priprava",
      author: "system",
      recipient: "manazer",
      kind: "notification",
      content: "NEX Studio potrebuje opravu (Dedo). Dedo dostal správu, počkaj.",
      status: "delivered",
      payload: { framework_issue: true, dedo_message: "Chýba docker socket mount — treba upraviť NEX Studio." },
      created_at: "2026-07-07T00:00:00Z",
      seq: 2,
    };
    render(<ConversationThread messages={[systemMsg]} activity={[]} working={false} />);
    expect(screen.getByText(/Chyba NEX Studia/)).toBeInTheDocument(); // the escalation badge (plain Slovak, no "Dedo")
    expect(screen.getByText(/Nahlásené technickému tímu/i)).toBeInTheDocument(); // the escalation-message block label
    expect(screen.getByText(/Chýba docker socket mount/)).toBeInTheDocument(); // the actual Dedo message
  });

  // ICCINT-12: the return leg — Dedo answering the escalation is a participant in the thread, labelled as
  // himself and visually his own, so the Manažér can see the answer arrived without reading it as a system
  // notice or as something he said.
  it("renders a Dedo message under his own name and his own accent", () => {
    const dedoMsg: PipelineMessage = {
      id: "d1",
      version_id: "v1",
      stage: "programovanie",
      author: "dedo",
      recipient: "ai_agent",
      kind: "answer",
      content: "Opravené v NEX Studiu v4.0.58 — skús ten build znova.",
      status: "delivered",
      payload: { dedo_reply: true },
      created_at: "2026-08-22T00:00:00Z",
      seq: 3,
    };
    const systemMsg: PipelineMessage = { ...dedoMsg, id: "s1", author: "system", content: "Systémová poznámka." };
    const { container } = render(
      <ConversationThread messages={[dedoMsg, systemMsg]} activity={[]} working={false} />,
    );

    expect(screen.getByText("Dedo")).toBeInTheDocument(); // the author label, not "Systém"
    expect(screen.getByText(/Opravené v NEX Studiu v4.0.58/)).toBeInTheDocument();

    // Distinguishable from `system`: the two bubbles must not share a class list.
    const bubbles = Array.from(container.querySelectorAll("li > div")).map((el) => el.className);
    expect(bubbles).toHaveLength(2);
    expect(bubbles[0]).not.toEqual(bubbles[1]);
    expect(bubbles[0]).toContain("var(--color-state-success-bg)");
  });

  // ICCINT-24: a PROPOSAL is not a message in the conversation — it was never said to the agent. It waits
  // for the Manažér in DedoProposalBar; a bubble here would claim a delivery that did not happen.
  it("does NOT render a Dedo proposal as a bubble in the transcript", () => {
    const proposal: PipelineMessage = {
      id: "p1",
      version_id: "v1",
      stage: "priprava",
      author: "dedo",
      recipient: "manazer",
      kind: "notification",
      content: "Úloha #4 nemá test na zápornú cenu.",
      status: "proposed",
      payload: { dedo_proposal: true, proposed_action: "uprav" },
      created_at: "2026-08-23T00:00:00Z",
      seq: 4,
    };
    const { container } = render(<ConversationThread messages={[proposal]} activity={[]} working={false} />);

    expect(screen.queryByText(/Úloha #4 nemá test/)).not.toBeInTheDocument();
    expect(container.querySelectorAll("li")).toHaveLength(0);
  });

  // The internal self-check chatter this thread hides is the AI Agent's own (author=system) — never the
  // human's. Until ICCINT-24 the predicate omitted the author, so the Manažér's "Uprav" (recorded
  // manazer→ai_agent kind="return") disappeared from his own transcript.
  //
  // NOTE FOR THE DIRECTOR: this narrowing is retroactive — it also un-hides the steers in transcripts of
  // builds that are long finished. It rides in this bundle because the ICCINT-24 send writes exactly this
  // shape and would otherwise be invisible, but it is a decision of its own; see the comment in
  // ConversationThread.tsx. This test is where a "no" would land.
  it("hides the agent's system self-check chatter but never the Manažér's own steer", () => {
    const base = {
      version_id: "v1",
      stage: "programovanie" as const,
      recipient: "ai_agent" as const,
      kind: "return" as const,
      status: "delivered",
      payload: null,
      created_at: "2026-08-23T00:00:00Z",
    };
    const selfCheck = {
      ...base,
      id: "sc1",
      author: "system" as const,
      content: "Self-check 3/5: deliverable 'x' missing on disk.",
      seq: 1,
    };
    const steer = {
      ...base,
      id: "st1",
      author: "manazer" as const,
      content: "Doplň prosím test na zápornú cenu.",
      seq: 2,
    };
    render(<ConversationThread messages={[selfCheck, steer]} activity={[]} working={false} />);

    expect(screen.queryByText(/Self-check 3\/5/)).not.toBeInTheDocument();
    expect(screen.getByText(/Doplň prosím test na zápornú cenu/)).toBeInTheDocument();
  });

  // …and once he sends it, the message in the thread is HIS — with where the wording came from said on it,
  // and Dedo's original kept visible when he rewrote it before sending.
  it("marks a sent proposal on the Manažér's own message and keeps Dedo's original when edited", () => {
    const sent: PipelineMessage = {
      id: "s2",
      version_id: "v1",
      stage: "priprava",
      author: "manazer",
      recipient: "ai_agent",
      kind: "return",
      content: "Doplň test na zápornú cenu. Prosím, začni tým.",
      status: "delivered",
      payload: {
        dedo_proposal_origin: {
          proposal_message_id: "p1",
          original_content: "Doplň test na zápornú cenu.",
          proposed_action: "uprav",
        },
      },
      created_at: "2026-08-23T00:01:00Z",
      seq: 5,
    };
    render(<ConversationThread messages={[sent]} activity={[]} working={false} />);

    expect(screen.getByText(/Podnet od Deda/i)).toBeInTheDocument();
    expect(screen.getByText(/odoslal si ho upravený/i)).toBeInTheDocument();
    expect(screen.getByText("Doplň test na zápornú cenu.")).toBeInTheDocument(); // Dedo's original
  });
});
