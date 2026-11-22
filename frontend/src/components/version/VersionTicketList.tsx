// VersionTicketList — the tickets put into a version, on its page (DEV-492, DEV-498): each with its identifier linked to
// Plane and its title, the sentence when there is none yet, and „Zaradiť tikety“ to the „Plane tikety“ tab. The caller
// adds its own action beside it (a fast fix: „Spustiť rýchlu opravu“).
import type { ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { ExternalLink, ListPlus } from "lucide-react";

import ErrorNote from "@/components/common/ErrorNote";
import type { VersionTicketItem } from "@/services/api/versions";
import type { HumanError } from "@/services/apiError";

interface Props {
  slug: string;
  tickets: VersionTicketItem[] | null;
  error: HumanError | null;
  /** What the page says while no ticket is in — a warning where the version cannot start without one. */
  empty: string;
  emptyTone: "warning" | "muted";
  children?: ReactNode;
}

export default function VersionTicketList({ slug, tickets, error, empty, emptyTone, children }: Props) {
  const navigate = useNavigate();
  const none = tickets !== null && tickets.length === 0;
  const tone = emptyTone === "warning" ? "text-[var(--color-state-warning-fg)]" : "text-[var(--color-text-muted)]";

  return (
    <>
      {tickets === null && !error && <p className="text-xs text-[var(--color-text-muted)]">Načítavam tikety…</p>}
      {tickets !== null && tickets.length > 0 && (
        <ul className="mb-3 space-y-1">
          {tickets.map((t) => (
            <li key={t.identifier} className="flex items-baseline gap-2 text-sm text-[var(--color-text-primary)]">
              <a
                href={t.url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 font-mono text-xs text-[var(--color-accent-primary)]"
              >
                {t.identifier}
                <ExternalLink className="h-3 w-3" aria-hidden="true" />
              </a>
              <span>{t.title}</span>
            </li>
          ))}
        </ul>
      )}
      {none && <p className={`mb-3 text-xs ${tone}`}>{empty}</p>}
      <ErrorNote error={error} className="mb-2" />
      <div className="flex flex-wrap items-center justify-end gap-2">
        <button
          type="button"
          onClick={() => navigate(`/projects/${slug}/plane-tikety`)}
          className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-border-strong)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)]"
        >
          <ListPlus className="h-3.5 w-3.5" aria-hidden="true" />
          Zaradiť tikety
        </button>
        {children}
      </div>
    </>
  );
}
