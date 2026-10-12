// BillingTab — the billing tab of the costs page (DEV-73): every version of the project for invoicing, the newest first.
//
// 12.10.2026 the statement of only the selected version was visible (1.7.1, a fix of our own error, 0 €) and the
// versions between 1.5.0 and 1.7.1 looked lost although nothing was. Here each version says where it stands — issued,
// ready to issue, waiting for its work kind, not countable, still in build — with its amount, and opens its own
// statement in place (the same panel as under „Verzia“), so a statement is issued without switching the version.

import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";

import ErrorNote from "@/components/common/ErrorNote";
import DeliveryStatementPanel from "@/components/naklady/DeliveryStatementPanel";
import { WORK_KIND_OPTIONS } from "@/lib/workKind";
import { getProjectBilling, type BillingOverview, type BillingRow } from "@/services/api/deliveryStatements";
import { humanizeApiError, type HumanError } from "@/services/apiError";

const eur = (v: string | number | null | undefined) =>
  v == null ? "—" : `${Number(v).toLocaleString("sk-SK", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`;
const day = (iso: string) => new Date(iso).toLocaleDateString("sk-SK");
const sha = (v: string | null) => (v ? v.slice(0, 7) : "?");

const STATE: Record<BillingRow["state"], { label: string; tone: string }> = {
  issued: { label: "vydaný", tone: "bg-[var(--color-state-success-bg)] text-[var(--color-state-success-fg)]" },
  ready: { label: "nevydaný", tone: "bg-[var(--color-state-warning-bg)] text-[var(--color-state-warning-fg)]" },
  needs_kind: {
    label: "treba určiť druh práce",
    tone: "bg-[var(--color-state-warning-bg)] text-[var(--color-state-warning-fg)]",
  },
  cannot: { label: "nedá sa vydať", tone: "bg-[var(--color-state-error-bg)] text-[var(--color-state-error-fg)]" },
  blocked: { label: "nedá sa spočítať", tone: "bg-[var(--color-state-error-bg)] text-[var(--color-state-error-fg)]" },
  not_done: { label: "verzia nie je hotová", tone: "bg-[var(--color-surface)] text-[var(--color-text-muted)]" },
};

function kindLabel(row: BillingRow): string {
  const kind = WORK_KIND_OPTIONS.find((o) => o.value === row.work_kind)?.label;
  const flow = row.flow_type === "fast_fix" ? "rýchla oprava" : row.flow_type === "new_version" ? "nová verzia" : null;
  return [flow, kind ?? (row.flow_type === "fast_fix" ? "druh práce neurčený" : null)].filter(Boolean).join(" · ");
}

function Total({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-surface-elevated)] p-4">
      <div className="text-[10px] uppercase tracking-widest text-[var(--color-text-muted)]">{label}</div>
      <div className="mt-1 text-xl font-semibold text-[var(--color-text-primary)]">{value}</div>
      {hint && <div className="mt-0.5 text-[11px] text-[var(--color-text-muted)]">{hint}</div>}
    </div>
  );
}

export default function BillingTab({ slug }: { slug: string }) {
  const [billing, setBilling] = useState<BillingOverview | null>(null);
  const [error, setError] = useState<HumanError | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    getProjectBilling(slug)
      .then((b) => !cancelled && setBilling(b))
      .catch((e: unknown) => !cancelled && setError(humanizeApiError(e, "Fakturáciu sa nepodarilo načítať")));
    return () => {
      cancelled = true;
    };
  }, [slug, tick]);

  if (error) {
    return (
      <div className="rounded-lg border border-[var(--color-state-error-bg)] bg-[var(--color-state-error-bg)] p-4 text-sm text-[var(--color-state-error-fg)]">
        <ErrorNote error={error} />
      </div>
    );
  }
  if (!billing) {
    return (
      <div className="flex items-center gap-2 py-10 text-sm text-[var(--color-text-muted)]">
        <Loader2 className="h-4 w-4 animate-spin" /> Počítam súpisy všetkých verzií…
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <p className="text-xs text-[var(--color-text-muted)]">
        Súpis dodávky patrí vždy jednej verzii a počíta len to, čo dodala ona — od dodaného stavu predchádzajúcej
        verzie. Pri každej verzii otvoríš jej súpis a vydáš ho priamo tu.
      </p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Total label="Vydané súpisy spolu" value={eur(billing.issued_total_eur)} />
        <Total
          label="Pripravené na vydanie"
          value={eur(billing.ready_total_eur)}
          hint="náhľad podľa dnešných sadzieb"
        />
        <Total
          label="Čaká na určenie druhu práce"
          value={`${billing.needs_kind}`}
          hint="rýchle opravy — oprava chyby sa neúčtuje, zmena áno"
        />
      </div>

      <ul className="divide-y divide-[var(--color-border-default)] rounded-xl border border-[var(--color-border-default)] bg-[var(--color-canvas)]">
        {billing.rows.map((row) => {
          const state = STATE[row.state];
          const expanded = open === row.version_id;
          const amount = row.issued ? row.issued.amount_eur : row.state === "ready" ? row.preview_amount_eur : null;
          return (
            <li key={row.version_id} className="p-3">
              <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="font-mono text-sm font-bold text-primary-400">{row.version_number}</span>
                    <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${state.tone}`}>
                      {state.label}
                      {row.issued ? ` ${day(row.issued.created_at)}` : ""}
                    </span>
                    <span className="text-xs text-[var(--color-text-secondary)]">{kindLabel(row)}</span>
                    {row.own_rates && (
                      <span
                        className="rounded-full border border-[var(--color-border-default)] px-2 py-0.5 text-[10px] text-[var(--color-text-secondary)]"
                        title="Táto verzia počíta s vlastnou sadzbou namiesto sadzby z Nastavení."
                      >
                        vlastná sadzba
                      </span>
                    )}
                  </div>
                  {row.delivered_sha && (
                    <div className="mt-1 text-[11px] text-[var(--color-text-muted)]">
                      <span className="font-mono">
                        {sha(row.base_sha)} → {sha(row.delivered_sha)}
                      </span>
                      {row.delivered_source_label ? ` (${row.delivered_source_label})` : ""}
                    </div>
                  )}
                  {row.reason && row.state !== "issued" && (
                    <div className="mt-1 text-[11px] text-[var(--color-text-muted)]">{row.reason}</div>
                  )}
                  {row.differs && (
                    <div className="mt-1 text-[11px] text-[var(--color-state-warning-fg)]">
                      Platí vydaný súpis. Podľa dnešných sadzieb by bol {eur(row.preview_amount_eur)} — nová suma platí
                      len po výslovnom nahradení súpisu.
                    </div>
                  )}
                </div>
                <div className="text-right">
                  <div className="text-sm font-semibold text-[var(--color-text-primary)]">{eur(amount)}</div>
                  {row.state === "ready" && <div className="text-[10px] text-[var(--color-text-muted)]">náhľad</div>}
                </div>
              </div>
              {row.state !== "not_done" && (
                <button
                  type="button"
                  aria-expanded={expanded}
                  onClick={() => setOpen(expanded ? null : row.version_id)}
                  className="mt-2 flex items-center gap-1 text-xs text-primary-400 hover:text-primary-300"
                >
                  {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                  {expanded ? "Skryť súpis" : "Otvoriť súpis verzie"}
                </button>
              )}
              {expanded && (
                <div className="mt-2">
                  <DeliveryStatementPanel versionId={row.version_id} onChange={reload} />
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
