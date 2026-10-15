// PlaneTicketsPage — „Plane tikety“ (DEV-120): the tickets of the pinned project's module in the DEV register.
// Director 14.10.2026: „Chcel by som pridať novú záložku "Plane tikety" tu by som videl tikety pre konkrétny projekt.“
// Identifier with a link to Plane, title, column, type, work kind, who works on it, last change; open or finished.
// A ticket the Zásobník founded names its requirement (REQ-N) and the version it is in.

import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Loader2 } from "lucide-react";

import { listProjectPlaneTicketsApi, type ProjectPlaneTickets } from "@/services/api/planeTickets";
import { humanizeApiError } from "@/services/apiError";

type View = "open" | "closed";

const VIEW_LABEL: Record<View, string> = { open: "Otvorené", closed: "Uzavreté" };

function when(value: string | null | undefined): string {
  if (!value) return "";
  return new Date(value).toLocaleString("sk-SK", { day: "numeric", month: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function PlaneTicketsPage() {
  const { slug } = useParams<{ slug: string }>();
  const [view, setView] = useState<View>("open");
  const [data, setData] = useState<ProjectPlaneTickets | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!slug) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    listProjectPlaneTicketsApi(slug, view === "closed")
      .then((res) => {
        if (!cancelled) setData(res);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(humanizeApiError(e, "Tikety z Plane sa nepodarilo načítať").message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [slug, view]);

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <h1 className="text-base font-bold text-[var(--color-text-primary)] mb-1">Plane tikety</h1>
      <p className="text-xs text-[var(--color-text-muted)] mb-4">
        Tikety projektu v evidencii DEV
        {data?.module ? (
          <>
            {" "}
            — modul <span className="text-[var(--color-text-secondary)]">{data.module}</span>
          </>
        ) : null}
        . Tiket zo Zásobníka ukazuje svoju požiadavku (REQ-N) a verziu.
      </p>

      <div role="tablist" aria-label="Tikety" className="flex items-center border-b border-[var(--color-border-default)] mb-4">
        {(["open", "closed"] as View[]).map((v) => (
          <button
            key={v}
            type="button"
            role="tab"
            aria-selected={view === v}
            onClick={() => setView(v)}
            className={`px-4 py-2 text-xs font-medium border-b-2 -mb-px transition-colors ${
              view === v
                ? "border-primary-500 text-primary-400"
                : "border-transparent text-[var(--color-text-muted)] hover:text-[var(--color-text-secondary)]"
            }`}
          >
            {VIEW_LABEL[v]}
          </button>
        ))}
      </div>

      {loading && (
        <div className="flex items-center gap-2 py-10 justify-center text-sm text-[var(--color-text-muted)]">
          <Loader2 className="w-4 h-4 animate-spin" /> Načítavam tikety z Plane…
        </div>
      )}
      {!loading && error && (
        <div className="rounded-lg bg-[var(--color-state-error-bg)] p-4 text-sm text-[var(--color-state-error-fg)]">{error}</div>
      )}
      {!loading && !error && data && data.tickets.length === 0 && (
        <div className="rounded-xl border border-dashed border-[var(--color-border-default)] p-10 text-center text-sm text-[var(--color-text-muted)]">
          {data.module === null
            ? "Projekt ešte nemá modul v evidencii DEV — vznikne s prvým tiketom, napríklad zo Zásobníka."
            : view === "open"
              ? "Žiadne otvorené tikety."
              : "Žiadne uzavreté tikety."}
        </div>
      )}
      {!loading && !error && data && data.tickets.length > 0 && (
        <ul className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-canvas)] divide-y divide-[var(--color-border-default)]">
          {data.tickets.map((t) => (
            <li key={t.identifier} className="p-4">
              <div className="flex flex-wrap items-center gap-2">
                <a
                  href={t.url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center text-xs font-mono text-[var(--color-accent-primary)] underline pointer-coarse:min-h-11 pointer-coarse:min-w-11"
                >
                  {t.identifier}
                </a>
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--color-surface-hover)] text-[var(--color-text-secondary)]">
                  {t.state}
                </span>
                {t.ticket_type && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--color-surface-hover)] text-[var(--color-text-secondary)]">
                    {t.ticket_type}
                  </span>
                )}
                {t.work_kind_label && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-[var(--color-state-info-bg)] text-[var(--color-state-info-fg)]">
                    {t.work_kind_label}
                  </span>
                )}
                {t.requirement && (
                  <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-[var(--color-accent-primary)]/15 text-[var(--color-accent-primary)]">
                    {t.requirement}
                    {t.requirement_version ? ` · ${t.requirement_version}` : ""}
                  </span>
                )}
              </div>
              <div className="text-sm text-[var(--color-text-primary)] mt-1">{t.title}</div>
              <div className="text-xs text-[var(--color-text-muted)] mt-0.5">
                {t.doer ? `Robí: ${t.doer}` : "Robí: —"}
                {t.updated_at ? ` · zmenený ${when(t.updated_at)}` : ""}
              </div>
              {t.warning && <div className="text-xs text-[var(--color-state-warning-fg)] mt-1">{t.warning}</div>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
