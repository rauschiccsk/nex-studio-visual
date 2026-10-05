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

    // The banner explains, in plain Slovak (no "Dedo"/"framework" jargon), that the technical team resolves it.
    expect(screen.getByRole("alert")).toHaveTextContent(/technický tím/i);

    // Both the input and the send button are disabled — no move for the Manažér here.
    expect(screen.getByRole("textbox")).toBeDisabled();
    expect(screen.getByRole("button", { name: /Poslať/ })).toBeDisabled();
  });

  it("stays interactive (no banner, input enabled) when NOT frameworkBlocked", () => {
    render(<ConversationComposer onRelay={noopRelay} />);

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox")).not.toBeDisabled();
  });

  // SUPERSEDED obs #4 (ICCINT-11, 23.08.2026). That observation reasoned that `lang="sk"` cannot suppress
  // underlines "without a browser SK dictionary", and switched spellcheck OFF. The premise was wrong: Chrome
  // ships a Slovak dictionary, it was simply not enabled in chrome://settings/languages while English (US)
  // was — so Slovak prose was checked against an ENGLISH dictionary, which is why every correct word was
  // flagged. Switching it off removed the squiggles AND the feature: a genuine typo went unflagged too.
  // A message to the AI Agent is prose, so it is checked, and `lang="sk"` names the dictionary to use.
  it("spellchecks the textarea as Slovak — it is prose, not an identifier", () => {
    render(<ConversationComposer onRelay={noopRelay} />);
    const box = screen.getByRole("textbox");
    expect(box).toHaveAttribute("spellcheck", "true");
    expect(box).toHaveAttribute("lang", "sk");
  });

  // nex-studio-visual crash-test 2026-07-13: when a recovery bar above owns the input (blockedAbove), the
  // always-open composer COLLAPSES to a slim pointer — no second textarea, so the screen has exactly ONE input.
  it("collapses to a pointer (no textarea) when blockedAbove", () => {
    render(<ConversationComposer onRelay={noopRelay} blockedAbove />);
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Poslať/ })).not.toBeInTheDocument();
    expect(screen.getByText(/lištu vyššie/i)).toBeInTheDocument();
  });

  // #2 (Director 2026-07-13): at the Vizuál gate this composer IS the change-request channel, so the
  // placeholder names it explicitly (and the approval bar drops its own text box → one input on screen).
  it("names the change-request channel in the placeholder when atVizual", () => {
    render(<ConversationComposer onRelay={noopRelay} atVizual />);
    expect(screen.getByPlaceholderText(/požiadavku na zmenu vizuálu/i)).toBeInTheDocument();
  });
});

// ICCINT-167: pokyn vložený z Poradcu — pole musí povedať, odkiaľ sa text vzal; nesmie sa tváriť ako jeho
// vlastný starší koncept, a keď ho človek upraví, je jeho.
describe("ConversationComposer — pokyn od Poradcu (ICCINT-167)", () => {
  it("labels an inserted instruction as Poradca's and drops the label once he edits it", async () => {
    const { insertInstructionDraft } = await import("@/lib/poradcaHandoff");
    const { fireEvent } = await import("@testing-library/react");
    window.localStorage.clear();
    insertInstructionDraft("v9", "Oprav test_login.");
    render(<ConversationComposer onRelay={noopRelay} versionId="v9" />);
    expect(screen.getByRole("textbox")).toHaveValue("Oprav test_login.");
    expect(screen.getByText(/Pokyn od Poradcu/)).toBeInTheDocument();
    expect(screen.queryByText(/Obnovený rozpísaný text/)).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Oprav test_login, prosím." } });
    expect(screen.queryByText(/Pokyn od Poradcu/)).not.toBeInTheDocument();
    expect(window.localStorage.getItem("nex.draft.rozhovor.v9.origin")).toBeNull();
    expect(noopRelay).not.toHaveBeenCalled();
  });

  it("an ordinary restored draft keeps its own label", () => {
    window.localStorage.clear();
    window.localStorage.setItem("nex.draft.rozhovor.v8", "moja veta");
    render(<ConversationComposer onRelay={noopRelay} versionId="v8" />);
    expect(screen.getByText(/Obnovený rozpísaný text/)).toBeInTheDocument();
    expect(screen.queryByText(/Pokyn od Poradcu/)).not.toBeInTheDocument();
  });
});
