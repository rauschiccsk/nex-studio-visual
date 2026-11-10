// Nápady (DEV-318) — what the screens say about an idea and where its outcome leads.

import type { Idea } from "@/types/poradca";

/** The address of the form Nový projekt opened from an idea names the idea and the answer whose brief prefills it. */
export const IDEA_PARAM = "napad";
export const ANSWER_PARAM = "odpoved";

/** Where the form Nový projekt is opened from an idea — it reads both and prefills itself from the answer's brief. */
export function newProjectFromIdea(ideaId: string, messageId: string): string {
  return `/projects/new?${new URLSearchParams({ [IDEA_PARAM]: ideaId, [ANSWER_PARAM]: messageId }).toString()}`;
}

/** How the list names where an idea stands (the backend derives it — `ideas.state`). */
export function ideaStateLabel(idea: Idea): string {
  switch (idea.state) {
    case "brief_ready":
      return "Zadanie hotové";
    case "founded":
      return `Založený ako ${idea.outcome_project_name ?? idea.outcome_project_slug ?? "projekt"}`;
    case "added":
      return `Pridaný do ${idea.outcome_project_name ?? idea.outcome_project_slug ?? "projektu"}`;
    default:
      return "Rozpracovaný";
  }
}

/** DEV-318 — what the project's page says about the idea it was founded from (`fromIdea` of its navigation state). */
export interface FromIdea {
  ideaId: string;
  title: string;
  ticket: string | null;
  ticketUrl: string | null;
  /** The brief did not reach the project (a refusal), or its ticket was not filed — the sentence why. */
  problem: string | null;
}

/** DEV-350 — how a new idea that follows a founded project starts: the cockpit says which project and where its brief
 *  is, the person says what he needs; the Projektant reads the rest itself. */
export function followUpStart(
  idea: Pick<Idea, "title" | "outcome_project_name" | "outcome_project_slug">,
): string {
  return (
    `Nadväzuje na projekt ${idea.outcome_project_name} (${idea.outcome_project_slug}), ktorý vznikol z nápadu ` +
    `„${idea.title}“. Jeho zadanie je v dokumentácii projektu: docs/specs/versions/v0.1.0/customer-requirements.md.` +
    "\n\nČo potrebujem:"
  );
}
