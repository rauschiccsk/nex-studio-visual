// PlaneTicketFastFix — „Spustiť rýchlu opravu“ at an open ticket of the „Plane tikety“ tab (DEV-122). The ticket's
// title and text are the directive and its work kind is preselected; the person edits what he wants and one click
// starts the fix, as the „Rýchla oprava“ dialog of the project does. The fix and the ticket stay linked: started →
// In Progress, deployed to UAT → a comment, deployed to PROD → Done. When Plane did not follow, the sentence stays on
// the screen with the way to the fix instead of being lost by leaving the page.

import { useState } from "react";
import { Loader2, X, Zap } from "lucide-react";

import WorkKindChoice from "@/components/common/WorkKindChoice";
import UnsavedWorkPanel from "@/components/riadiace/UnsavedWorkPanel";
import { unsavedWorkOf, type UnsavedWorkRefusal } from "@/components/riadiace/unsavedWork";
import { useOpenVersionCockpit } from "@/hooks/useOpenVersionCockpit";
import { startFastFixApi } from "@/services/api/pipeline";
import type { PlaneTicket } from "@/services/api/planeTickets";
import { humanizeApiError } from "@/services/apiError";
import type { WorkKind } from "@/lib/workKind";

const FIELD =
  "w-full bg-[var(--color-surface)] border border-[var(--color-border-default)] rounded px-3 py-1.5 text-xs text-[var(--color-text-primary)] focus:outline-none focus:border-primary-500";

interface Props {
  ticket: PlaneTicket;
  project: { id: string; slug: string; name: string };
  onClose: () => void;
}

function fastFixDirective(ticket: PlaneTicket): string {
  return [ticket.title, ticket.text ?? ""].filter((part) => part.trim()).join("\n\n");
}

export default function PlaneTicketFastFix({ ticket, project, onClose }: Props) {
  const openVersionCockpit = useOpenVersionCockpit();
  const [directive, setDirective] = useState(() => fastFixDirective(ticket));
  const [workKind, setWorkKind] = useState<WorkKind | null>(ticket.work_kind ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // DEV-175: the fix is a new version, founded only over saved work — save or discard it here, then start again.
  const [unsaved, setUnsaved] = useState<UnsavedWorkRefusal | null>(null);
  const [started, setStarted] = useState<{ versionId: string; warning: string } | null>(null);

  async function start() {
    if (!workKind || !directive.trim() || busy) return;
    setBusy(true);
    setError("");
    setUnsaved(null);
    try {
      const res = await startFastFixApi(project.id, directive.trim(), workKind, ticket.issue_id);
      if (res.plane_warning) {
        setStarted({ versionId: res.version_id, warning: res.plane_warning });
        setBusy(false);
        return;
      }
      await openVersionCockpit(res.version_id, { slug: project.slug, name: project.name });
    } catch (e: unknown) {
      const refusal = unsavedWorkOf(e);
      if (refusal) setUnsaved(refusal);
      else setError(humanizeApiError(e, "Spustenie rýchlej opravy zlyhalo").message);
      setBusy(false);
    }
  }

  if (started) {
    return (
      <div
        role="region"
        aria-label={`Rýchla oprava z tiketu ${ticket.identifier}`}
        className="mt-2 space-y-2 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-surface)] p-3"
      >
        <p className="text-xs text-[var(--color-state-warning-fg)]">{started.warning}</p>
        <button
          type="button"
          onClick={() => openVersionCockpit(started.versionId, { slug: project.slug, name: project.name })}
          className="flex items-center gap-1 rounded bg-primary-600 px-2.5 py-1.5 text-xs text-white hover:bg-primary-500 pointer-coarse:min-h-11"
        >
          <Zap size={13} /> Otvoriť rýchlu opravu
        </button>
      </div>
    );
  }

  const missing = !workKind ? "Vyber druh práce." : !directive.trim() ? "Zadanie je prázdne." : "";

  return (
    <div
      role="region"
      aria-label={`Rýchla oprava z tiketu ${ticket.identifier}`}
      className="mt-2 space-y-2 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-surface)] p-3"
    >
      <p className="text-xs text-[var(--color-text-muted)]">
        Vznikne nová opravná verzia projektu a zadaním je text tiketu — uprav ho, ak treba. Tiket sa potom posúva sám:
        spustenie → In Progress, nasadenie na UAT → komentár, nasadenie na PROD → Done.
      </p>
      <textarea
        lang="sk"
        spellCheck={true}
        data-no-draft="zadanie sa skladá z tiketu — pri ďalšom otvorení sa zloží znova z textu v Plane"
        aria-label="Zadanie rýchlej opravy"
        value={directive}
        onChange={(e) => setDirective(e.target.value)}
        rows={6}
        className={FIELD}
      />
      <WorkKindChoice name={`oprava-druh-${ticket.issue_id}`} value={workKind} onChange={setWorkKind} disabled={busy} />
      {unsaved && <UnsavedWorkPanel refusal={unsaved} />}
      {error && <p className="text-xs text-[var(--color-status-error)]">{error}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={start}
          disabled={busy || !!missing}
          title={missing || undefined}
          className="flex items-center gap-1 rounded bg-primary-600 px-2.5 py-1.5 text-xs text-white hover:bg-primary-500 disabled:opacity-40 pointer-coarse:min-h-11"
        >
          {busy ? <Loader2 size={13} className="animate-spin" /> : <Zap size={13} />} Spustiť rýchlu opravu
        </button>
        <button
          type="button"
          onClick={onClose}
          className="flex items-center gap-1 rounded bg-[var(--color-surface-active)] px-2.5 py-1.5 text-xs text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-hover)] pointer-coarse:min-h-11"
        >
          <X size={13} /> Zavrieť
        </button>
        {missing && <span className="text-xs text-[var(--color-text-muted)]">{missing}</span>}
      </div>
    </div>
  );
}
