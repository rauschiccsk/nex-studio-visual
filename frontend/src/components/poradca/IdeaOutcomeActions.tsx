// The two ways a new idea ends (DEV-318, docs/specs/ideas.md §3.6) — under the latest brief Poradca wrote. A click is
// always the person's: „Založiť projekt“ opens the form Nový projekt prefilled from the brief; „Pridať do projektu“
// saves the brief as a requirement of that project's Zásobník (which version takes it, the Director decides — DEV-29).

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { FolderPlus, ListPlus, Loader2 } from "lucide-react";

import ErrorNote from "@/components/common/ErrorNote";
import { newProjectFromIdea } from "@/lib/ideas";
import { ideaAddedApi } from "@/services/api/ideas";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import type { Idea, IdeaOutcome, PoradcaMessage } from "@/types/poradca";

export default function IdeaOutcomeActions({
  idea,
  message,
  onEnded,
}: {
  idea: Idea;
  /** The latest answer with a brief — an older one was rewritten by it. */
  message: PoradcaMessage;
  onEnded: (outcome: IdeaOutcome) => void;
}) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);
  const project = message.idea_project;
  const addition = message.idea_addition;

  async function add() {
    setBusy(true);
    setError(null);
    try {
      onEnded(await ideaAddedApi(idea.id, message.id));
    } catch (e: unknown) {
      setError(humanizeApiError(e, "Zadanie sa nepodarilo pridať do projektu"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      data-testid="idea-outcome"
      className="ml-1 flex max-w-[90%] flex-col gap-2 rounded-lg border border-[var(--color-accent-primary)]/40 bg-[var(--color-accent-primary)]/5 px-3 py-2 text-sm"
    >
      {project && (
        <>
          <p className="text-[var(--color-text-secondary)]">
            Zadanie je hotové pre nový projekt <strong>{project.name ?? project.slug ?? "bez mena"}</strong>.
            Formulár Nový projekt sa vyplní z neho — skontroluješ ho a potvrdíš; zadanie pôjde do verzie 0.1.0 a
            projekt dostane tiket v DEV.
          </p>
          {!project.work_kind && (
            <p role="status" className="text-[12px] text-[var(--color-state-warning-fg)]">
              Zadanie ešte nehovorí, kto prvú verziu platí — zákazník, alebo je to dar ICC. Povedz to Poradcovi a on
              ho doplní; dovtedy sa projekt z nápadu založiť nedá.
            </p>
          )}
          <button
            type="button"
            disabled={!project.work_kind}
            onClick={() => navigate(newProjectFromIdea(idea.id, message.id))}
            className="flex w-fit items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-40"
          >
            <FolderPlus className="h-4 w-4" /> Založiť projekt
          </button>
        </>
      )}
      {addition && (
        <>
          <p className="text-[var(--color-text-secondary)]">
            Zadanie „{addition.title}“ patrí do existujúceho projektu <strong>{addition.project_slug}</strong>. Uloží
            sa do jeho Zásobníka ako požiadavka — do ktorej verzie pôjde, rozhodne Director.
          </p>
          <button
            type="button"
            disabled={busy}
            onClick={() => void add()}
            className="flex w-fit items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <ListPlus className="h-4 w-4" />} Pridať do
            projektu {addition.project_slug}
          </button>
        </>
      )}
      <ErrorNote error={error} />
    </div>
  );
}
