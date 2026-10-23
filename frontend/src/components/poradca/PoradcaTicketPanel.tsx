// PoradcaTicketPanel — „Založiť tiket“ under a Poradca answer (DEV-119). Poradca proposes, the person founds: the
// preview shows the proposal to edit, what the cockpit adds to a ticket of the cockpit, and similar open tickets of the
// module, so the same thing is not filed twice. A ticket of the cockpit goes to Andros Dedo; a ticket of the project
// carries its work kind and Poradca's proposal — the next version or a fast fix — and starts nothing (DEV-29).

import { useEffect, useRef, useState } from "react";
import { Check, Loader2, X } from "lucide-react";

import WorkKindChoice from "@/components/common/WorkKindChoice";
import { TICKET_TYPES } from "@/lib/ticketTypes";
import { foundPoradcaTicketApi, getPoradcaTicketDraftApi } from "@/services/api/poradca";
import { humanizeApiError } from "@/services/apiError";
import type { WorkKind } from "@/lib/workKind";
import type { TicketType } from "@/types/backlog";
import type { PoradcaTicketDraft } from "@/types/poradca";

type Way = "version" | "fast_fix";

const WAYS: { value: Way; label: string }[] = [
  { value: "version", label: "Do ďalšej verzie" },
  { value: "fast_fix", label: "Do rýchlej opravy" },
];

const FIELD =
  "w-full bg-[var(--color-surface)] border border-[var(--color-border-default)] rounded px-3 py-1.5 text-xs text-[var(--color-text-primary)] focus:outline-none focus:border-primary-500";

interface Props {
  messageId: string;
  onFounded: (ticket: { identifier: string; url: string }) => void;
  onClose: () => void;
}

export default function PoradcaTicketPanel({ messageId, onFounded, onClose }: Props) {
  const [draft, setDraft] = useState<PoradcaTicketDraft | null>(null);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [ticketType, setTicketType] = useState<TicketType>("Task");
  const [workKind, setWorkKind] = useState<WorkKind | null>(null);
  const [way, setWay] = useState<Way | null>(null);
  const [responsible, setResponsible] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const actions = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    getPoradcaTicketDraftApi(messageId)
      .then((d) => {
        if (cancelled) return;
        setDraft(d);
        setTitle(d.title);
        setDescription(d.description);
        setTicketType(d.ticket_type);
        setWorkKind(d.work_kind ?? null);
        setWay(d.way ?? null);
        setResponsible(d.responsible ?? "");
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(humanizeApiError(e, "Náhľad tiketu sa nepodarilo načítať").message);
      });
    return () => {
      cancelled = true;
    };
  }, [messageId]);

  // DEV-202 — the preview opens below the answer; under a long one its buttons were below the screen's edge and the
  // Director had to scroll to find „Založiť tiket“ (23.10.2026). Once the preview (or why it failed) is drawn, the
  // view moves just enough for the buttons to be seen.
  useEffect(() => {
    if (draft || error) actions.current?.scrollIntoView?.({ block: "nearest" });
  }, [draft, error]);

  const project = draft?.kind === "project";
  const context = draft?.context ?? [];
  const similar = draft?.similar ?? [];
  const missing = !title.trim()
    ? "Tiket potrebuje názov."
    : !project
      ? ""
      : !workKind
        ? "Vyber druh práce."
        : !way
          ? "Vyber, či tiket navrhuješ do ďalšej verzie, alebo do rýchlej opravy."
          : !responsible
            ? "Vyber, kto je zodpovedný."
            : "";

  async function found() {
    if (!draft || missing || busy) return;
    setBusy(true);
    setError("");
    try {
      const ticket = await foundPoradcaTicketApi(messageId, {
        title: title.trim(),
        description,
        ticket_type: ticketType,
        ...(project ? { work_kind: workKind, way, responsible } : {}),
      });
      onFounded({ identifier: ticket.plane_identifier, url: ticket.plane_url });
    } catch (e: unknown) {
      setError(humanizeApiError(e, "Založenie tiketu zlyhalo").message);
      setBusy(false);
    }
  }

  return (
    <div
      role="region"
      aria-label="Návrh tiketu od Poradcu"
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
            {project
              ? "Tiket vznikne v module projektu v evidencii DEV. Je to návrh — verziu ani rýchlu opravu nezakladá, o tom rozhoduješ ty."
              : "Tiket pre problém kokpitu vznikne v module NEX Studio Visual v evidencii DEV. Robí ho a zodpovedá zaň Andros Dedo."}
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
            data-no-draft="náhľad tiketu sa skladá z odpovede Poradcu — pri ďalšom otvorení sa zloží znova, a založený tiket drží text v Plane"
            aria-label="Popis tiketu"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={4}
            className={FIELD}
          />
          <label className="flex flex-col gap-1 text-xs text-[var(--color-text-secondary)] sm:w-48">
            Typ
            <select value={ticketType} onChange={(e) => setTicketType(e.target.value as TicketType)} className={FIELD}>
              {TICKET_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          {project && (
            <>
              <WorkKindChoice name={`poradca-druh-${messageId}`} value={workKind} onChange={setWorkKind} disabled={busy} />
              <fieldset className="space-y-1" disabled={busy}>
                <legend className="text-xs text-[var(--color-text-secondary)]">Návrh</legend>
                {WAYS.map((w) => (
                  <label key={w.value} className="flex cursor-pointer items-center gap-2 text-xs text-[var(--color-text-primary)]">
                    <input
                      type="radio"
                      name={`poradca-cesta-${messageId}`}
                      value={w.value}
                      checked={way === w.value}
                      onChange={() => setWay(w.value)}
                    />
                    {w.label}
                  </label>
                ))}
              </fieldset>
              <label className="flex flex-col gap-1 text-xs text-[var(--color-text-secondary)] sm:w-48">
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
            </>
          )}
          {context.length > 0 && (
            <div className="text-xs text-[var(--color-text-secondary)]">
              Kokpit k tiketu doplní:
              <ul className="mt-0.5 list-disc pl-5 text-[var(--color-text-muted)]">
                {context.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </div>
          )}
          {similar.length > 0 && (
            <div className="text-xs text-[var(--color-state-warning-fg)]">
              Podobné otvorené tikety v module — pozri, či to nie je ten istý:
              <ul className="mt-0.5 space-y-0.5">
                {similar.map((t) => (
                  <li key={t.identifier}>
                    <a
                      href={t.url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center font-mono underline pointer-coarse:min-h-11"
                    >
                      {t.identifier}
                    </a>{" "}
                    <span className="text-[var(--color-text-secondary)]">
                      {t.title} · {t.state}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
      {error && <p className="text-xs text-[var(--color-status-error)]">{error}</p>}
      <div ref={actions} className="flex flex-wrap items-center gap-2">
        {draft && !draft.plane_identifier && (
          <button
            type="button"
            onClick={found}
            disabled={busy || !!missing}
            title={missing || undefined}
            className="flex items-center gap-1 rounded bg-primary-600 px-2.5 py-1.5 text-xs text-white hover:bg-primary-500 disabled:opacity-40 pointer-coarse:min-h-11"
          >
            {busy ? <Loader2 size={13} className="animate-spin" /> : <Check size={13} />} Založiť tiket
          </button>
        )}
        <button
          type="button"
          onClick={onClose}
          className="flex items-center gap-1 rounded bg-[var(--color-surface-active)] px-2.5 py-1.5 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)] pointer-coarse:min-h-11"
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
