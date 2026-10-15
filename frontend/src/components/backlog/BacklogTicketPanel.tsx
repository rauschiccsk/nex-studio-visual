// BacklogTicketPanel — „Založiť tiket“ at a requirement of the Zásobník (DEV-121). The cockpit offers a draft the
// person edits — title, description, work kind, type and who is responsible — and one click founds the ticket in the
// project's module of the DEV register. The requirement and the ticket stay linked; a second click returns the same
// ticket, and from then on the ticket follows the requirement on its own.

import { useEffect, useState } from "react";
import { Check, Loader2, X } from "lucide-react";

import WorkKindChoice from "@/components/common/WorkKindChoice";
import { TICKET_TYPES } from "@/lib/ticketTypes";
import { createBacklogTicketApi, getBacklogTicketDraftApi } from "@/services/api/backlog";
import { humanizeApiError } from "@/services/apiError";
import type { WorkKind } from "@/lib/workKind";
import type { BacklogItemRead, BacklogTicketDraft, TicketType } from "@/types/backlog";

const FIELD =
  "w-full bg-[var(--color-surface)] border border-[var(--color-border-default)] rounded px-3 py-1.5 text-xs text-[var(--color-text-primary)] focus:outline-none focus:border-primary-500";

interface Props {
  item: BacklogItemRead;
  onFounded: (item: BacklogItemRead) => void;
  onClose: () => void;
}

export default function BacklogTicketPanel({ item, onFounded, onClose }: Props) {
  const [draft, setDraft] = useState<BacklogTicketDraft | null>(null);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [workKind, setWorkKind] = useState<WorkKind | null>(null);
  const [ticketType, setTicketType] = useState<TicketType>("Task");
  const [responsible, setResponsible] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    getBacklogTicketDraftApi(item.id)
      .then((d) => {
        if (cancelled) return;
        setDraft(d);
        setTitle(d.title);
        setDescription(d.description);
        setTicketType(d.ticket_type);
        setResponsible(d.responsible ?? "");
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(humanizeApiError(e, "Náhľad tiketu sa nepodarilo načítať").message);
      });
    return () => {
      cancelled = true;
    };
  }, [item.id]);

  async function found() {
    if (!workKind || !responsible || !title.trim()) return;
    setBusy(true);
    setError("");
    try {
      const updated = await createBacklogTicketApi(item.id, {
        title: title.trim(),
        description,
        work_kind: workKind,
        ticket_type: ticketType,
        responsible,
      });
      onFounded(updated);
    } catch (e: unknown) {
      setError(humanizeApiError(e, "Založenie tiketu zlyhalo").message);
    } finally {
      setBusy(false);
    }
  }

  const nobody = draft !== null && !draft.plane_identifier && draft.responsible_options.length === 0;
  const missing = !workKind ? "Vyber druh práce." : !responsible ? "Vyber, kto je zodpovedný." : "";

  return (
    <div
      role="region"
      aria-label={`Tiket pre REQ-${item.number}`}
      className="mt-2 space-y-2 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-surface)] p-3"
    >
      {draft === null && !error && (
        <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
          <Loader2 size={13} className="animate-spin" /> Pripravujem náhľad tiketu…
        </div>
      )}
      {draft?.plane_identifier && (
        <div className="text-xs text-[var(--color-text-secondary)]">
          Tiket už existuje:{" "}
          <a href={draft.plane_url ?? undefined} target="_blank" rel="noreferrer" className="font-mono underline">
            {draft.plane_identifier}
          </a>
        </div>
      )}
      {draft && !draft.plane_identifier && (
        <>
          <p className="text-xs text-[var(--color-text-muted)]">
            Tiket vznikne v Plane v module projektu. Jeho stav sa potom mení sám: zaradenie do verzie, nasadenie na
            PROD a zamietnutie požiadavky.
          </p>
          <input
            lang="sk"
            spellCheck={true}
            aria-label="Názov tiketu"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className={FIELD}
          />
          <textarea
            lang="sk"
            spellCheck={true}
            data-no-draft="náhľad tiketu sa skladá z požiadavky — pri ďalšom otvorení sa zloží znova, a založený tiket drží text v Plane"
            aria-label="Popis tiketu"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            className={FIELD}
          />
          <WorkKindChoice name={`tiket-druh-${item.id}`} value={workKind} onChange={setWorkKind} disabled={busy} />
          <div className="flex flex-col gap-2 sm:flex-row">
            <label className="flex flex-col gap-1 text-xs text-[var(--color-text-secondary)]">
              Typ
              <select
                value={ticketType}
                onChange={(e) => setTicketType(e.target.value as TicketType)}
                className={FIELD}
              >
                {TICKET_TYPES.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs text-[var(--color-text-secondary)]">
              Zodpovedný
              <select value={responsible} onChange={(e) => setResponsible(e.target.value)} className={FIELD}>
                <option value="">— vyber —</option>
                {draft.responsible_options.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {nobody && (
            <p className="text-xs text-[var(--color-status-error)]">
              V evidencii DEV nie je nikto v skupine „Zodpovedný“ — tiket sa nedá založiť.
            </p>
          )}
        </>
      )}
      {error && <p className="text-xs text-[var(--color-status-error)]">{error}</p>}
      <div className="flex items-center gap-2">
        {draft && !draft.plane_identifier && (
          <button
            onClick={found}
            disabled={busy || nobody || !!missing || !title.trim()}
            title={missing || undefined}
            className="flex items-center gap-1 rounded bg-primary-600 px-2.5 py-1.5 text-xs text-white hover:bg-primary-500 disabled:opacity-40"
          >
            {busy ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />} Založiť tiket
          </button>
        )}
        <button
          onClick={onClose}
          className="flex items-center gap-1 rounded bg-[var(--color-surface-active)] px-2.5 py-1.5 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)]"
        >
          <X size={13} /> Zavrieť
        </button>
        {draft && !draft.plane_identifier && missing && (
          <span className="text-xs text-[var(--color-text-muted)]">{missing}</span>
        )}
      </div>
    </div>
  );
}
