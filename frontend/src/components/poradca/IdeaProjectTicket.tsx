// DEV-360 — the ticket of a project founded from an idea, when founding it failed: one button tries again.
//
// Director 11.11.2026 (NEX Archive founded under the account alex, „Tiket v DEV sa nezaložil“): „Máme ten istý problém
// ako minule.“ The cockpit said Andros Dedo would found the ticket and offered no second try. Now the founded idea
// without its ticket says so and offers „Založiť tiket projektu v DEV“; what Plane or the account lacks, the sentence
// from the backend says (`ticket_error`), and it stays until the next try.

import { useState } from "react";

import { ideaTicketApi } from "@/services/api/ideas";
import { humanizeApiError } from "@/services/apiError";
import type { Idea, IdeaOutcome } from "@/types/poradca";

interface Props {
  idea: Idea;
  /** The idea as the backend returned it — with its ticket when it was founded. */
  onFiled: (outcome: IdeaOutcome) => void;
}

export default function IdeaProjectTicket({ idea, onFiled }: Props) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  if (idea.state !== "founded" || idea.plane_identifier) return null;

  async function file() {
    setBusy(true);
    setProblem(null);
    try {
      const outcome = await ideaTicketApi(idea.id);
      if (outcome.ticket_error) setProblem(outcome.ticket_error);
      onFiled(outcome);
    } catch (e: unknown) {
      setProblem(humanizeApiError(e, "Tiket projektu sa nepodarilo založiť").message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-shrink-0 flex-col gap-1 border-b border-[var(--color-border-default)] bg-[var(--color-state-warning-bg)] px-4 py-2 text-xs text-[var(--color-state-warning-fg)]">
      <div className="flex flex-wrap items-center gap-3">
        <span>Projekt ešte nemá tiket v DEV.</span>
        <button
          type="button"
          onClick={() => void file()}
          disabled={busy}
          className="underline disabled:opacity-50"
        >
          {busy ? "Zakladám tiket…" : "Založiť tiket projektu v DEV"}
        </button>
      </div>
      {problem && <p role="alert">{problem}</p>}
    </div>
  );
}
