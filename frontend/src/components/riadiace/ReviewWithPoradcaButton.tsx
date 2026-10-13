// The review-with-Poradca button (DEV-103) — the verification decision card taken to Poradca as it stands: the card
// is handed off and Poradca opens on its version, where the question composed from it goes out by itself.
// See `@/lib/poradcaReview`.

import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { MessageSquare } from "lucide-react";

import { handOffReview, reviewAddress, type ReviewCard } from "@/lib/poradcaReview";

interface Props {
  versionId: string;
  card: ReviewCard;
}

export default function ReviewWithPoradcaButton({ versionId, card }: Props) {
  const navigate = useNavigate();
  const [error, setError] = useState<string | null>(null);

  function review() {
    try {
      handOffReview(versionId, card);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Odovzdanie karty Poradcovi zlyhalo.");
      return;
    }
    navigate(reviewAddress(versionId));
  }

  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={review}
        title="Poradca dostane túto kartu a preverí, či je nález opodstatnený a ako ho vyriešiť. Odporúčanú voľbu ti označí."
        className="flex items-center gap-1.5 rounded border border-[var(--color-border-default)] px-2.5 py-1 text-[11px] font-medium text-[var(--color-text-secondary)] hover:border-primary-500 hover:text-[var(--color-text-primary)]"
      >
        <MessageSquare className="h-3.5 w-3.5" aria-hidden="true" /> Overiť s Poradcom
      </button>
      {error && <p className="mt-1 text-[11px] text-[var(--color-state-error-fg)]">{error}</p>}
    </div>
  );
}
