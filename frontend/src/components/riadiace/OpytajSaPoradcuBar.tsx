// A finished version in the Riadiace centrum (ICCINT-167): instead of the message field, an „Opýtaj sa Poradcu" button.
//
// The build agent does not work on a finished version — the message would stay unanswered (the backend rejects it).
// Questions about the project belong to Poradca, who runs alongside the build and only reads; a change from its answer
// is filed as a new version.
//
// DEV-191: the button ASKS — Director 23.10.2026: „Tlačidlo, ktoré ma prepne do chatu poradca nemá zmysel, prepnúť viem
// aj na bočnej lište. Ak kliknem na Opýtať poradcu, malo by sa skutočne opýtať ako to robíme na kartách“. The question
// about the version is handed off like a card's (`@/lib/poradcaReview`) and goes out by itself on Poradca's page.

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { MessageSquare } from "lucide-react";

import {
  finishedVersionCard,
  handOffReview,
  reviewAddress,
} from "@/lib/poradcaReview";

interface Props {
  versionId: string;
  projectName: string;
  versionNumber: string;
}

export default function OpytajSaPoradcuBar({
  versionId,
  projectName,
  versionNumber,
}: Props) {
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);

  function ask() {
    try {
      handOffReview(versionId, finishedVersionCard(projectName, versionNumber));
    } catch (e: unknown) {
      setError(
        e instanceof Error ? e.message : "Odovzdanie Poradcovi zlyhalo.",
      );
      return;
    }
    navigate(reviewAddress(versionId));
  }

  return (
    <div className="flex flex-shrink-0 flex-wrap items-center justify-between gap-2 border-t border-[var(--color-border-default)] bg-[var(--color-surface)] px-3 py-2">
      <p className="text-[11px] text-[var(--color-text-muted)]">
        Verzia je hotová — agent stavby na nej už nepracuje. Poradca ti zhrnie,
        čo priniesla, čo vyskúšať v UAT a na čo si dať pozor pred nasadením na
        PROD; zmenu z jeho odpovede založíš ako novú verziu.
      </p>
      <button
        type="button"
        onClick={ask}
        className="flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500"
      >
        <MessageSquare className="h-3.5 w-3.5" /> Opýtaj sa Poradcu
      </button>
      {error && (
        <p className="w-full text-[11px] text-[var(--color-state-error-fg)]">
          {error}
        </p>
      )}
    </div>
  );
}
