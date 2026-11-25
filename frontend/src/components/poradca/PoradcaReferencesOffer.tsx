// PoradcaReferencesOffer — the documents from the internet a project's Poradca offers to add (DEV-513), with
// „Stiahnuť do projektu“. 25.11.2026 NEX DualBook was founded without the bank's integration manual: the Projektant
// left it to the Manažér because the bank's site refused to show it to him, though the cockpit downloads it. The
// cockpit downloads into docs/reference/ of the project; what came is on the version page („Podklady z internetu“).
import { useState } from "react";

import ErrorNote from "@/components/common/ErrorNote";
import { addPoradcaReferencesApi } from "@/services/api/poradca";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import type { PoradcaMessage } from "@/types/poradca";

interface Props {
  messageId: string;
  references: NonNullable<PoradcaMessage["references"]>;
}

const STARTED =
  "Kokpit sťahuje podklady do projektu — čo prišlo, uvidíš na stránke verzie v časti „Podklady z internetu“.";
const BUSY = "Kokpit práve sťahuje podklady projektu — keď skončí, stlač „Stiahnuť do projektu“ znova.";

export default function PoradcaReferencesOffer({ messageId, references }: Props) {
  const [said, setSaid] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);

  async function add() {
    setBusy(true);
    try {
      const answer = await addPoradcaReferencesApi(messageId);
      setSaid(answer.started ? STARTED : BUSY);
      setError(null);
    } catch (e) {
      setError(humanizeApiError(e, "Podklady sa nepodarilo stiahnuť do projektu"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="Podklady na stiahnutie" className="mt-2 text-xs">
      <ul className="space-y-0.5 text-[var(--color-text-secondary)]">
        {references.map((one) => (
          <li key={one.path}>
            <a href={one.url} target="_blank" rel="noreferrer" className="underline">
              {one.description}
            </a>{" "}
            <span className="font-mono text-[var(--color-text-muted)]">{one.path}</span>
          </li>
        ))}
      </ul>
      <button
        type="button"
        onClick={() => void add()}
        disabled={busy}
        className="mt-2 rounded-md border border-[var(--color-border-strong)] px-2 py-1 text-xs text-[var(--color-text-primary)] disabled:opacity-50"
      >
        Stiahnuť do projektu
      </button>
      {said && (
        <p role="status" className="mt-1 text-[var(--color-text-muted)]">
          {said}
        </p>
      )}
      <ErrorNote error={error} className="mt-2" />
    </section>
  );
}
