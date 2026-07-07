/**
 * ConversationComposer — the framework_issue lock (Director observation #6).
 *
 * When the build is blocked on an agent → Dedo escalation (``block_reason='framework_issue'``), the Manažér
 * cannot fix a NEX Studio bug — the composer is HARD-DISABLED and shows the "wait for Dedo" banner. These
 * pin that: the banner renders, the textarea + send button are disabled, and a normal (non-blocked) composer
 * stays interactive.
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import { ConversationComposer } from "@/components/riadiace/ConversationComposer";

const noopRelay = vi.fn(async () => ({ deferred: false }));

describe("ConversationComposer — framework_issue lock (Director obs #6)", () => {
  it("shows the 'wait for Dedo' banner and disables the composer when frameworkBlocked", () => {
    render(<ConversationComposer onRelay={noopRelay} frameworkBlocked />);

    // The banner names Dedo + tells the Manažér they cannot fix it via Uprav.
    expect(screen.getByRole("alert")).toHaveTextContent(/musí opraviť Dedo/i);
    expect(screen.getByRole("alert")).toHaveTextContent(/Počkaj na Deda/i);

    // Both the input and the send button are disabled — no move for the Manažér here.
    expect(screen.getByRole("textbox")).toBeDisabled();
    expect(screen.getByRole("button", { name: /Poslať/ })).toBeDisabled();
  });

  it("stays interactive (no banner, input enabled) when NOT frameworkBlocked", () => {
    render(<ConversationComposer onRelay={noopRelay} />);

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox")).not.toBeDisabled();
  });

  // obs #4 (batch-2): the composer disables native spellcheck so Slovak (and English) words are not underlined
  // in this Slovak-primary internal tool — `lang="sk"` alone doesn't suppress underlines without a browser SK
  // dictionary, so spellCheck={false} is the guarantee (matching SlovakTextarea's choice).
  it("renders the textarea with spellcheck disabled (obs #4)", () => {
    render(<ConversationComposer onRelay={noopRelay} />);
    expect(screen.getByRole("textbox")).toHaveAttribute("spellcheck", "false");
  });
});
