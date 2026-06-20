/**
 * PipelineMessageBubble rendering (CR-NS-053 Pillar A §A.3).
 *
 * The Coordinator's synthesis (payload.is_synthesis) is the PRIMARY Director-facing message
 * (prominent primary rail + "Zhrnutie" badge); a raw worker gate_report is SECONDARY (dimmed +
 * "pôvodný report"); a normal message (question/answer) is neither.
 */

import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import PipelineMessageBubble from "@/components/cockpit/PipelineMessageBubble";
import type { PipelineMessage } from "@/services/api/pipeline";

function mkMessage(overrides: Partial<PipelineMessage> = {}): PipelineMessage {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    version_id: "22222222-2222-2222-2222-222222222222",
    stage: "gate_a",
    author: "designer",
    recipient: "director",
    kind: "gate_report",
    content: "hotovo",
    status: "delivered",
    payload: null,
    created_at: "2026-06-13T00:00:00Z",
    seq: 1,
    ...overrides,
  };
}

describe("PipelineMessageBubble — synthesis rendering (CR-NS-053 §A.3)", () => {
  it("renders a synthesis (payload.is_synthesis) as primary: 'Zhrnutie' badge + prominent primary rail", () => {
    const { container } = render(
      <PipelineMessageBubble
        message={mkMessage({ author: "coordinator", kind: "answer", payload: { is_synthesis: true } })}
      />,
    );
    expect(screen.getByText("Zhrnutie")).toBeInTheDocument();
    expect(screen.queryByText("pôvodný report")).not.toBeInTheDocument();
    // prominent primary rail (vs the per-author accent / dim of a raw report)
    expect((container.firstChild as HTMLElement).className).toContain("border-primary-500");
  });

  it("renders a raw worker gate_report as secondary: dimmed + 'pôvodný report'", () => {
    const { container } = render(
      <PipelineMessageBubble message={mkMessage({ author: "designer", kind: "gate_report", payload: null })} />,
    );
    expect(screen.getByText("pôvodný report")).toBeInTheDocument();
    expect(screen.queryByText("Zhrnutie")).not.toBeInTheDocument();
    expect((container.firstChild as HTMLElement).className).toContain("opacity-60");
  });

  it("renders a normal message (question) as neither synthesis nor raw-report", () => {
    const { container } = render(
      <PipelineMessageBubble message={mkMessage({ author: "designer", kind: "question", payload: null })} />,
    );
    expect(screen.getByText("question")).toBeInTheDocument();
    expect(screen.queryByText("Zhrnutie")).not.toBeInTheDocument();
    expect(screen.queryByText("pôvodný report")).not.toBeInTheDocument();
    expect((container.firstChild as HTMLElement).className).not.toContain("opacity-60");
  });

  it("does NOT dim a coordinator gate_report (only worker-authored raw reports are secondary)", () => {
    const { container } = render(
      <PipelineMessageBubble message={mkMessage({ author: "coordinator", kind: "gate_report", payload: null })} />,
    );
    expect(screen.queryByText("pôvodný report")).not.toBeInTheDocument();
    expect((container.firstChild as HTMLElement).className).not.toContain("opacity-60");
  });

  // CR-2 (v0.7.3): a Director-facing brief (relay / verify, payload.is_director_brief) shares the synthesis's
  // prominent primary rail, badged "Na rade".
  it("renders a Director-facing brief (payload.is_director_brief) as primary: 'Na rade' badge + prominent primary rail", () => {
    const { container } = render(
      <PipelineMessageBubble
        message={mkMessage({ author: "coordinator", kind: "gate_report", payload: { is_director_brief: true } })}
      />,
    );
    expect(screen.getByText("Na rade")).toBeInTheDocument();
    expect(screen.queryByText("Zhrnutie")).not.toBeInTheDocument();
    // a coordinator-authored brief is never dimmed as a raw worker report
    expect(screen.queryByText("pôvodný report")).not.toBeInTheDocument();
    expect((container.firstChild as HTMLElement).className).toContain("border-primary-500");
    expect((container.firstChild as HTMLElement).className).not.toContain("opacity-60");
  });

  // CR-NS-055 Pillar B (§B.3): an autonomous Coordinator decision renders distinctly.
  it("renders an autonomous decision (payload.is_autonomous) with the 'Koordinátor rozhodol' badge + amber rail", () => {
    const { container } = render(
      <PipelineMessageBubble
        message={mkMessage({
          author: "coordinator",
          kind: "notification",
          content: "Koordinátor rozhodol: reset úlohy",
          payload: { is_autonomous: true, action: "coordinator_reset_task" },
        })}
      />,
    );
    expect(screen.getByText("Koordinátor rozhodol")).toBeInTheDocument();
    expect(screen.queryByText("Zhrnutie")).not.toBeInTheDocument();
    expect((container.firstChild as HTMLElement).className).toContain("border-amber-500");
  });
});

// v0.7.4: the FE guarantees a prominent Director headline (model-independent) for the flagged briefs.
describe("PipelineMessageBubble — FE-guaranteed Director headline (v0.7.4)", () => {
  it("derives a bold first-sentence headline from prose + renders the rest as body (no duplication)", () => {
    render(
      <PipelineMessageBubble
        message={mkMessage({
          author: "coordinator",
          kind: "answer",
          payload: { is_synthesis: true },
          content:
            "Po tom zaseknutí sa rozpis práce nakoniec podaril v plnom rozsahu. Celý plán je teraz schválený a pokračujeme ďalej.",
        })}
      />,
    );
    const headline = screen.getByText("Po tom zaseknutí sa rozpis práce nakoniec podaril v plnom rozsahu.");
    expect(headline.className).toContain("font-semibold");
    // the remainder is the body — present exactly once (getByText throws on a duplicate)
    expect(screen.getByText("Celý plán je teraz schválený a pokračujeme ďalej.")).toBeInTheDocument();
  });

  it("uses a markdown heading's text as the headline (no literal '##') + strips it from the body", () => {
    render(
      <PipelineMessageBubble
        message={mkMessage({
          author: "coordinator",
          kind: "gate_report",
          payload: { is_director_brief: true },
          content: "## Gate A prešla\n\nŠpecifikácia je kompletná, čaká sa na schválenie.",
        })}
      />,
    );
    // heading text once, without the leading '##'; body present once, heading not duplicated into it
    const headline = screen.getByText("Gate A prešla");
    expect(headline.className).toContain("font-semibold");
    expect(screen.queryByText(/##/)).not.toBeInTheDocument();
    expect(screen.getByText("Špecifikácia je kompletná, čaká sa na schválenie.")).toBeInTheDocument();
  });

  it("renders headline-only when stripping leaves an empty body (single line, no trailing detail)", () => {
    render(
      <PipelineMessageBubble
        message={mkMessage({
          author: "coordinator",
          kind: "gate_report",
          payload: { is_director_brief: true },
          content: "## Build dokončený",
        })}
      />,
    );
    const headline = screen.getByText("Build dokončený");
    expect(headline.className).toContain("font-semibold");
    expect(screen.queryByText(/##/)).not.toBeInTheDocument();
    // no separate prose body rendered (nothing left after the headline)
    expect(headline.parentElement?.querySelector(".prose")).toBeNull();
  });

  it("leaves non-flagged (worker/raw) messages unchanged — no derived headline", () => {
    render(
      <PipelineMessageBubble
        message={mkMessage({
          author: "designer",
          kind: "gate_report",
          payload: null,
          content: "## Toto je nadpis\n\ntelo reportu",
        })}
      />,
    );
    // a raw report renders content verbatim through ReactMarkdown (heading stays a heading, no FE headline lead)
    expect(screen.getByRole("heading", { name: "Toto je nadpis" })).toBeInTheDocument();
    expect(screen.getByText("telo reportu")).toBeInTheDocument();
  });
});

// Legible-cockpit-output fix: the agent's full markdown report (payload.report) renders richly in a
// collapsible "CC výstup" card, with the structured payload arrays as labelled sections beneath.
describe("PipelineMessageBubble — rich report rendering (payload.report)", () => {
  it("renders a short report in an expanded 'CC výstup' card with structured markdown", () => {
    render(
      <PipelineMessageBubble
        message={mkMessage({
          payload: { report: "## Dokončené\n\n- Pridaný `export` endpoint\n- Testy zelené" },
        })}
      />,
    );
    expect(screen.getByText("CC výstup")).toBeInTheDocument();
    // markdown is structured: heading + list item with inline code (short → expanded by default)
    expect(screen.getByRole("heading", { name: "Dokončené" })).toBeInTheDocument();
    expect(screen.getByText("export")).toBeInTheDocument(); // inline-code chip
    expect(screen.getByText("Testy zelené")).toBeInTheDocument();
  });

  it("collapses a long report by default and reveals it on toggle", () => {
    const longReport = "## Dokončené\n\n" + Array.from({ length: 20 }, (_, i) => `- riadok ${i}`).join("\n");
    render(<PipelineMessageBubble message={mkMessage({ payload: { report: longReport } })} />);
    // collapsed by default → the body heading is NOT in the DOM yet
    expect(screen.queryByRole("heading", { name: "Dokončené" })).not.toBeInTheDocument();
    const toggle = screen.getByRole("button", { name: /CC výstup/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(screen.getByRole("heading", { name: "Dokončené" })).toBeInTheDocument();
  });

  it("routes a fenced code block to a CodeBlock with a language label", () => {
    render(
      <PipelineMessageBubble
        message={mkMessage({
          payload: { report: "Pozri:\n\n```python\nprint('hi')\n```" },
        })}
      />,
    );
    expect(screen.getByText("python")).toBeInTheDocument(); // CodeBlock language label
    expect(screen.getByText("print('hi')")).toBeInTheDocument();
  });

  it("renders deliverables / findings / commits as labelled sections beneath the body", () => {
    render(
      <PipelineMessageBubble
        message={mkMessage({
          payload: {
            report: "## Hotovo",
            deliverables: ["backend/services/x.py"],
            findings: ["žiadne"],
            commits: ["abc123 feat: x"],
          },
        })}
      />,
    );
    expect(screen.getByText("Výstupy")).toBeInTheDocument();
    expect(screen.getByText("backend/services/x.py")).toBeInTheDocument();
    expect(screen.getByText("Zistenia")).toBeInTheDocument();
    expect(screen.getByText("Commity")).toBeInTheDocument();
    expect(screen.getByText("abc123 feat: x")).toBeInTheDocument();
  });

  it("falls back to plain content when no report is present (no 'CC výstup' card)", () => {
    render(<PipelineMessageBubble message={mkMessage({ content: "iba zhrnutie", payload: null })} />);
    expect(screen.queryByText("CC výstup")).not.toBeInTheDocument();
    expect(screen.getByText("iba zhrnutie")).toBeInTheDocument();
  });
});
