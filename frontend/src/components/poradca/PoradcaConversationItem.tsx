// Jeden rozhovor v zozname Poradcu: otvoriť, premenovať, vymazať (ICCINT-167, v4.42.0).
//
// Director 05.10.2026: „chýba mi premenovanie rozhovoru a vymazanie rozhovoru."
// Premenovanie priamo v zozname — Enter uloží, Esc zruší, odchod z poľa uloží. Vymazanie sa potvrdí priamo
// v riadku; text je potom preč natrvalo, cena ostáva v Nákladoch. Kým Poradca odpovedá, kôš je zašednutý
// s dôvodom (backend by vrátil 409) — akciu, ktorá teraz neprejde, neponúkame ako živú.

import { useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Loader2, Pencil, Trash2 } from "lucide-react";

import { formatDate } from "@/utils/format";
import type { PoradcaConversation } from "@/types/poradca";

const TITLE_MAX_CHARS = 200;
const DELETE_BUSY_REASON = "Kým Poradca odpovedá, rozhovor sa nedá vymazať — najprv odpoveď zastav.";

interface Props {
  conversation: PoradcaConversation;
  active: boolean;
  /** Odpoveď práve beží (zo zoznamu, pri otvorenom rozhovore z jeho živého stavu). */
  busy: boolean;
  /** Meno autora, keď rozhovor nie je môj (admin vidí všetky). */
  authorName: string | null;
  onSelect: () => void;
  /** `true` = uložené; pri chybe ostane pole otvorené s rozpísaným názvom. */
  onRename: (title: string) => Promise<boolean>;
  onDelete: () => Promise<boolean>;
}

type Mode = "view" | "edit" | "confirm";

function oneLine(text: string): string {
  return text.split(/\s+/).filter(Boolean).join(" ");
}

export default function PoradcaConversationItem({
  conversation: c,
  active,
  busy,
  authorName,
  onSelect,
  onRename,
  onDelete,
}: Props) {
  const [mode, setMode] = useState<Mode>("view");
  const [draft, setDraft] = useState(c.title);
  const [working, setWorking] = useState(false);
  // Enter aj odchod z poľa vedú k uloženiu (pole počas ukladania zašedne a prehliadač z neho odíde) — uloží
  // sa raz.
  const settled = useRef(false);

  function startEdit() {
    settled.current = false;
    setDraft(c.title);
    setMode("edit");
  }

  async function save(e?: FormEvent) {
    e?.preventDefault();
    if (settled.current) return;
    settled.current = true;
    const title = oneLine(draft);
    if (!title || title === c.title) {
      setMode("view");
      return;
    }
    setWorking(true);
    const ok = await onRename(title);
    setWorking(false);
    if (ok) setMode("view");
    else settled.current = false;
  }

  function onKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Escape") {
      e.preventDefault();
      setMode("view");
    }
  }

  async function confirmDelete() {
    setWorking(true);
    const ok = await onDelete();
    setWorking(false);
    if (!ok) setMode("view");
  }

  if (mode === "edit") {
    return (
      <li>
        <form onSubmit={(e) => void save(e)} className="px-1 py-1">
          <input
            type="text"
            aria-label="Názov rozhovoru"
            title="Enter uloží, Esc zruší"
            value={draft}
            maxLength={TITLE_MAX_CHARS}
            disabled={working}
            autoFocus
            lang="sk"
            spellCheck={true}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            onBlur={() => void save()}
            className="w-full rounded border border-[var(--color-accent-primary)] bg-[var(--color-surface)] px-1.5 py-1 text-xs text-[var(--color-text-primary)] outline-none"
          />
        </form>
      </li>
    );
  }

  if (mode === "confirm") {
    return (
      <li>
        <div
          role="group"
          aria-label="Potvrdenie vymazania"
          className="rounded-md border border-[var(--color-state-error-fg)]/40 bg-[var(--color-state-error-bg)] px-2 py-1.5"
        >
          <p className="truncate text-[11px] font-medium text-[var(--color-text-primary)]">
            Vymazať „{c.title}“ natrvalo?
          </p>
          <p className="text-[10px] text-[var(--color-text-muted)]">
            Text sa nedá obnoviť; cena ostane v Nákladoch.
          </p>
          <div className="mt-1.5 flex gap-1.5">
            <button
              type="button"
              onClick={() => void confirmDelete()}
              disabled={working}
              className="inline-flex items-center gap-1 rounded bg-red-600 px-2 py-0.5 text-[11px] font-medium text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {working ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
              Vymazať
            </button>
            <button
              type="button"
              onClick={() => setMode("view")}
              disabled={working}
              className="rounded border border-[var(--color-border-default)] px-2 py-0.5 text-[11px] text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)] disabled:cursor-not-allowed disabled:opacity-50"
            >
              Ponechať
            </button>
          </div>
        </div>
      </li>
    );
  }

  return (
    <li>
      <div
        className={`flex items-start rounded-md ${
          active ? "bg-[var(--color-accent-primary)]/10" : "hover:bg-[var(--color-surface-hover)]"
        }`}
      >
        <button type="button" onClick={onSelect} className="min-w-0 flex-1 px-2 py-1.5 text-left">
          <div className="flex items-center gap-1.5">
            {c.running && (
              <span
                className="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-[var(--color-accent-primary)]"
                title="Poradca odpovedá"
              />
            )}
            <span className="truncate text-xs text-[var(--color-text-primary)]">{c.title}</span>
          </div>
          <div className="truncate text-[10px] text-[var(--color-text-muted)]">
            {c.version_number ? `verzia ${c.version_number}` : "celý projekt"} · {formatDate(c.updated_at)}
            {authorName ? ` · ${authorName}` : ""}
          </div>
        </button>
        <div className="flex flex-shrink-0 items-center gap-0.5 pr-1 pt-1.5">
          <button
            type="button"
            aria-label="Premenovať rozhovor"
            title="Premenovať"
            onClick={startEdit}
            className="rounded p-0.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text-primary)]"
          >
            <Pencil className="h-3 w-3" />
          </button>
          <button
            type="button"
            aria-label="Vymazať rozhovor"
            title={busy ? DELETE_BUSY_REASON : "Vymazať"}
            onClick={() => setMode("confirm")}
            disabled={busy}
            className="rounded p-0.5 text-[var(--color-text-muted)] enabled:hover:bg-[var(--color-surface-hover)] enabled:hover:text-[var(--color-state-error-fg)] disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Trash2 className="h-3 w-3" />
          </button>
        </div>
      </div>
    </li>
  );
}
