// DEV-83 — the choice Poradca tells the manager to make, drawn so it does not get lost in a long answer.
//
// 12.10.2026 the Director: „chcem aby voľba, ktorú treba vybrať bolo vypísané zelenou farbou“. Green means the card
// offers exactly this option (the server checked it against the card) or it is the answer for the agent's question;
// orange means the card does not offer it — a paraphrase, or a card that changed meanwhile — so the manager is not
// sent to look for an option that is not there. The icon carries the same meaning without the colour.
import { Children, type ReactNode } from "react";
import { AlertTriangle, Check, Send } from "lucide-react";

import { choiceCard } from "@/lib/poradcaAnswer";
import type { PoradcaChoice } from "@/types/poradca";

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
 * DEV-135 — the answer Poradca recommends for the agent's question goes into the answer box of Riadiace centrum; the
 * Manažér sends it himself. `blocked` says why the box would not take it now.
 */
export interface AnswerInsert {
  insert: (label: string) => void;
  blocked: string | null;
}

/** Above the answer: what to pick, at one glance. */
export function ChoiceSummary({ choices, answer }: { choices: PoradcaChoice[]; answer?: AnswerInsert }) {
  if (!choices.length) return null;
  return (
    <div
      role="group"
      aria-label="Čo vybrať"
      className="mb-2 space-y-1 rounded-lg border border-[var(--color-border-default)] px-3 py-2 text-sm"
    >
      {choices.map((choice, i) => (
        <div key={i} className="flex flex-wrap items-baseline gap-1.5">
          <span className="text-[var(--color-text-secondary)]">{choice.card ? "Vyber" : "Odpovedz agentovi"}</span>
          <ChoiceBadge choice={choice} />
          {choice.status === "not_on_card" && (
            <span className="text-xs text-[var(--color-state-warning-fg)]">{NOT_ON_CARD_NOTE}</span>
          )}
          {answer && !choice.card && choice.status === "answer" && (
            <button
              type="button"
              onClick={() => answer.insert(choice.label)}
              disabled={!!answer.blocked}
              title={answer.blocked ?? "Vloží odpoveď do poľa odpovede v Riadiacom centre — odošleš ju sám."}
              className="ml-1 inline-flex items-center gap-1 rounded border border-[var(--color-border-default)] px-2 py-0.5 text-[11px] font-medium text-[var(--color-text-secondary)] hover:border-primary-500 hover:text-[var(--color-text-primary)] disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Send className="h-3 w-3" aria-hidden /> Vložiť do odpovede
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

/** The badge for a choice link inside the answer — the server's verdict found by card and text. */
export function InlineChoice({ href, children, choices }: { href: string; children: ReactNode; choices: PoradcaChoice[] }) {
  const card = choiceCard(href);
  const label = Children.toArray(children).join("");
  const found = choices.find((c) => (c.card ?? null) === card && c.label === label);
  return <ChoiceBadge choice={found ?? { card, label, status: card ? "not_on_card" : "answer" }} />;
}
