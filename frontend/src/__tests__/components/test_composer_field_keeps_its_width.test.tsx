/**
 * DEV-222 — the field for the agent keeps the whole row when a line says where its text came from.
 *
 * 24.10.2026 Author Asistent 0.1.0 in Vizuál: with Poradca's instruction in the field, the line „Pokyn od Poradcu —
 * prečítaj ho, uprav podľa potreby a odošli sám.“ stood in the same row as the field and the buttons; the browser
 * gave it the width of its sentence and the field got a third of the panel — the text broke into short lines and
 * the conversation above shrank to one. Director: „pole čo obsahuje text pre agenta je príliš úzke a tým pádom zle
 * čitateľné, je tam dosť miesta, preto treba rozšíriť.“
 *
 * jsdom lays nothing out, so the rule is pinned as structure: the row holds the field and its buttons only — no
 * line of text — and the line about the text's origin stands above it.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import ConversationComposer from "@/components/riadiace/ConversationComposer";
import { handOffInstruction } from "@/lib/poradcaHandoff";

const noopRelay = vi.fn(async () => ({ deferred: false }));

function rowOf(field: HTMLElement): HTMLElement {
  const row = field.parentElement;
  if (!row) throw new Error("the field has no row");
  return row;
}

beforeEach(() => window.localStorage.clear());

describe("DEV-222 — the field keeps its width", () => {
  it.each([
    [
      "Poradca's instruction",
      () => handOffInstruction("v222", "Jednotné pojmy v celej aplikácii."),
      /Pokyn od Poradcu/,
    ],
    [
      "a restored draft",
      () => window.localStorage.setItem("nex.draft.rozhovor.v222", "moja rozpísaná veta"),
      /Obnovený rozpísaný text/,
    ],
  ])("with %s the line about its origin stands above the field, not beside it", (_what, arrange, line) => {
    arrange();
    render(<ConversationComposer onRelay={noopRelay} versionId="v222" />);

    const field = screen.getByRole("textbox");
    const row = rowOf(field);
    const label = screen.getByText(line);

    expect(row.contains(label)).toBe(false);
    expect(label.compareDocumentPosition(row) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(row.querySelectorAll("p")).toHaveLength(0);
  });

  it("the row holds the field and its buttons only", () => {
    handOffInstruction("v222", "Jednotné pojmy v celej aplikácii.");
    render(<ConversationComposer onRelay={noopRelay} versionId="v222" />);

    const row = rowOf(screen.getByRole("textbox"));

    expect([...row.children].map((c) => c.tagName)).toEqual(["TEXTAREA", "DIV", "BUTTON"]);
    expect(row.querySelector("div")).toContainElement(screen.getByRole("button", { name: /Priložiť súbor/ }));
  });
});
