// VizualChecklistBlock — what the Manažér should check in the Vizuál after one turn (DEV-36).
//
// Director 09.10.2026: „Po vyhotovení vizuálu pred tým linkom na samotný vizual pomohlo by mi krátky popis čo
// všetko treba prekontrolovať vo vizuáli." Each item names the screen, what to do and what to see, with a link
// straight to that screen in the preview; he ticks what he has checked (the count sits by „Schváliť vizuál").
// What the version changes but sample-data screens cannot show is listed apart, so approving the Vizuál is
// not mistaken for approving it.

import { useVizualChecksStore, vizualCheckKey } from "@/store/vizualChecksStore";
import type { VizualChecklist } from "@/services/api/pipeline";

const NOTHING_CHECKED: string[] = [];

interface Props {
  versionId: string;
  /** The message carrying the list — with the item's place, the key it is ticked under. */
  seq: number;
  checklist: VizualChecklist;
}

export default function VizualChecklistBlock({ versionId, seq, checklist }: Props) {
  const checked = useVizualChecksStore((s) => s.checked[versionId] ?? NOTHING_CHECKED);
  const toggle = useVizualChecksStore((s) => s.toggle);
  const heading = checklist.round === "change" ? "Čo po zmene skontrolovať" : "Čo vo Vizuáli skontrolovať";
  const notVerifiable = checklist.not_verifiable ?? [];

  return (
    <section
      aria-label={heading}
      className="my-2 rounded-md border-l-2 border-[var(--color-accent-primary)] bg-[var(--color-accent-primary)]/5 px-3 py-2"
    >
      <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-wider text-[var(--color-accent-primary)]">
        {heading}
      </div>
      <ol className="space-y-1.5">
        {checklist.items.map((item, i) => {
          const key = vizualCheckKey(seq, i);
          const done = checked.includes(key);
          return (
            <li key={key} className="flex items-start gap-2 text-sm text-[var(--color-text-primary)]">
              <input
                type="checkbox"
                checked={done}
                onChange={() => toggle(versionId, key)}
                aria-label={`Skontrolované: ${item.screen}`}
                className="mt-1 h-3.5 w-3.5 shrink-0 accent-[var(--color-accent-primary)]"
              />
              <div className={done ? "opacity-60" : ""}>
                <span className="font-semibold">{item.screen}</span> — {item.action}{" "}
                <span className="text-[var(--color-text-secondary)]">→ {item.expected}</span>
                {item.url && (
                  <>
                    {" "}
                    <a
                      href={item.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="whitespace-nowrap text-xs font-medium text-[var(--color-accent-primary)] underline hover:no-underline"
                    >
                      Otvoriť obrazovku
                    </a>
                  </>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      {notVerifiable.length > 0 && (
        <div className="mt-2 border-t border-[var(--color-border-default)] pt-1.5">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
            Vo Vizuáli sa overiť nedá
          </div>
          <ul className="list-disc space-y-0.5 pl-5 text-xs text-[var(--color-text-secondary)]">
            {notVerifiable.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
