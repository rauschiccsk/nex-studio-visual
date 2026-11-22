// PreparedTicketDialog — „Založiť tiket“ from a text the cockpit prepared (DEV-492): a change that does not work in UAT,
// the cleanup of old dead code.
//
// Both used to open the „Rýchla oprava“ dialog and start a fix from the text. With one way for every version (Director
// 22.11.2026: „Treba mať jednotný postup a to najprv verzia a potom priradzovanie tiketov.“) the text becomes a ticket
// of the project's module; the Manažér reads it, may edit it, picks the work kind and files it — then puts it into a
// version or a fast fix on the „Plane tikety“ tab. Nothing starts.
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Loader2, Ticket } from "lucide-react";

import WorkKindChoice from "@/components/common/WorkKindChoice";
import ErrorNote from "@/components/common/ErrorNote";
import { filePreparedTicketApi, type PreparedTicketFounded } from "@/services/api/planeTickets";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import type { WorkKind } from "@/lib/workKind";

export interface PreparedText {
  title: string;
  description: string;
  source: "uat" | "dead_code";
  /** Preselected when the text says it (a change that does not work is a fix); otherwise he picks. */
  workKind: WorkKind | null;
}

interface Props {
  slug: string;
  prepared: PreparedText;
  onClose: () => void;
}

const FIELD =
  "w-full rounded-lg border border-[var(--color-border-default)] bg-[var(--color-surface)] px-3 py-2 text-sm " +
  "text-[var(--color-text-primary)] focus:border-primary-500 focus:outline-none";
const SECONDARY =
  "rounded-lg border border-[var(--color-border-default)] px-3 py-1.5 text-xs font-medium " +
  "text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)] disabled:opacity-50";

export default function PreparedTicketDialog({ slug, prepared, onClose }: Props) {
  const navigate = useNavigate();
  const [title, setTitle] = useState(prepared.title);
  const [description, setDescription] = useState(prepared.description);
  const [workKind, setWorkKind] = useState<WorkKind | null>(prepared.workKind);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);
  const [filed, setFiled] = useState<PreparedTicketFounded | null>(null);

  const ready = title.trim() !== "" && description.trim() !== "" && workKind !== null;

  async function file() {
    if (!ready || busy || workKind === null) return;
    setBusy(true);
    setError(null);
    try {
      setFiled(
        await filePreparedTicketApi(slug, {
          title: title.trim(),
          description: description.trim(),
          work_kind: workKind,
          source: prepared.source,
        }),
      );
    } catch (err: unknown) {
      setError(humanizeApiError(err, "Založenie tiketu zlyhalo"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex overflow-y-auto bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="prepared-ticket-title"
    >
      <div className="m-auto w-full max-w-lg rounded-xl border border-[var(--color-border-default)] bg-[var(--color-canvas)] p-5 shadow-xl">
        <div className="mb-1 flex items-center gap-2">
          <Ticket className="h-4 w-4 text-[var(--color-accent-primary)]" aria-hidden="true" />
          <h3 id="prepared-ticket-title" className="text-sm font-bold text-[var(--color-text-primary)]">
            Založiť tiket
          </h3>
        </div>
        {filed ? (
          <>
            <p role="status" className="mt-2 text-sm text-[var(--color-text-secondary)]">
              Tiket{" "}
              <a href={filed.url} target="_blank" rel="noreferrer" className="font-mono text-[var(--color-accent-primary)]">
                {filed.identifier}
              </a>{" "}
              je založený. Zaraď ho do verzie alebo do rýchlej opravy na karte „Plane tikety“.
            </p>
            <div className="mt-4 flex items-center justify-end gap-2">
              <button type="button" onClick={onClose} className={SECONDARY}>
                Zavrieť
              </button>
              <button
                type="button"
                onClick={() => navigate(`/projects/${slug}/plane-tikety`)}
                className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500"
              >
                Prejsť na Plane tikety
              </button>
            </div>
          </>
        ) : (
          <>
            <p className="mb-3 text-xs text-[var(--color-text-muted)]">
              Kokpit text pripravil — prečítaj ho, uprav podľa potreby a založ z neho tiket projektu. Nič sa
              nespustí: tiket potom zaradíš do verzie alebo do rýchlej opravy.
            </p>
            <label htmlFor="prepared-ticket-name" className="mb-1 block text-xs font-medium text-[var(--color-text-secondary)]">
              Názov tiketu
            </label>
            <input
              id="prepared-ticket-name"
              lang="sk"
              spellCheck={true}
              value={title}
              maxLength={255}
              onChange={(e) => setTitle(e.target.value)}
              className={FIELD}
            />
            <label
              htmlFor="prepared-ticket-text"
              className="mb-1 mt-3 block text-xs font-medium text-[var(--color-text-secondary)]"
            >
              Text tiketu
            </label>
            <textarea
              id="prepared-ticket-text"
              data-no-draft="text pripravil kokpit zo skúšky alebo z kontroly kódu — pri ďalšom otvorení ho pripraví znova"
              lang="sk"
              spellCheck={true}
              rows={8}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className={`${FIELD} resize-none`}
            />
            <div className="mt-3">
              <WorkKindChoice name="prepared-ticket-kind" value={workKind} onChange={setWorkKind} disabled={busy} />
            </div>
            <ErrorNote
              error={error}
              className="mt-2 rounded-lg border border-[var(--color-state-error-bg)] bg-[var(--color-state-error-bg)] px-3 py-2"
            />
            <div className="mt-4 flex items-center justify-end gap-2">
              <button type="button" onClick={onClose} disabled={busy} className={SECONDARY}>
                Zavrieť
              </button>
              <button
                type="button"
                onClick={file}
                disabled={!ready || busy}
                title={workKind ? undefined : "Najprv zvoľ druh práce — oprava chyby, zmena alebo dar"}
                className="flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500 disabled:opacity-50"
              >
                {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : null}
                Založiť tiket
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
