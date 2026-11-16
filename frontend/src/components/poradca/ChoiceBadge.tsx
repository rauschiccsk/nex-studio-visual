// DEV-83 — the choice Poradca tells the manager to make, drawn so it does not get lost in a long answer.
//
// 12.10.2026 the Director: „chcem aby voľba, ktorú treba vybrať bolo vypísané zelenou farbou“. Green means the card
// offers exactly this option (the server checked it against the card) or it is the answer for the agent's question;
// orange means the card does not offer it — a paraphrase, or a card that changed meanwhile — so the manager is not
// sent to look for an option that is not there. The icon carries the same meaning without the colour.
import { AlertTriangle, Check } from "lucide-react";

import type { PoradcaChoice } from "@/types/poradca";

/** @internal — exported for its test (DEV-155). */
export const NOT_ON_CARD_NOTE = "Takú možnosť karta nemá — skontroluj kartu";

const GREEN =
  "border-[var(--color-state-success-fg)] bg-[var(--color-state-success-bg)] text-[var(--color-state-success-fg)]";
const ORANGE =
  "border-[var(--color-state-warning-fg)] bg-[var(--color-state-warning-bg)] text-[var(--color-state-warning-fg)]";

function ChoiceBadge({ choice }: { choice: PoradcaChoice }) {
  const onCard = choice.status !== "not_on_card";
  const Icon = onCard ? Check : AlertTriangle;
  return (
    <span
      data-choice={choice.status}
      title={onCard ? undefined : NOT_ON_CARD_NOTE}
      className={`inline-flex items-baseline gap-1 rounded border px-1.5 py-0.5 font-semibold no-underline ${onCard ? GREEN : ORANGE}`}
    >
      <Icon className="h-3.5 w-3.5 shrink-0 self-center" aria-hidden />
      {choice.card ? <span className="whitespace-nowrap text-[0.85em] font-medium">karta {choice.card}:</span> : null}
      <span>{choice.label}</span>
    </span>
  );
}

/**
 * DEV-401 — the choices Poradca marked, drawn once, at the end of the answer right above its one button (Director
 * 16.11.2026: „aby zelený text nebol na začiatku, ale na konci pred tlačidlom“). In the text of the answer a choice stays
 * plain words; here it is green when the card offers it or when it is the answer for the agent's question, orange with
 * the warning in words when the card does not offer it (DEV-83, DEV-208 — once, not twice).
 */
export function ChoiceSummary({ choices }: { choices: PoradcaChoice[] }) {
  if (choices.length === 0) return null;
  return (
    <div data-testid="poradca-choices" className="mt-2 flex flex-col items-start gap-1 text-sm">
      {choices.map((choice) => (
        <span key={`${choice.card ?? ""}:${choice.label}`} className="inline-flex flex-wrap items-baseline gap-1">
          <ChoiceBadge choice={choice} />
          {choice.status === "not_on_card" && (
            <span className="text-xs text-[var(--color-state-warning-fg)]">{NOT_ON_CARD_NOTE}</span>
          )}
        </span>
      ))}
    </div>
  );
}
