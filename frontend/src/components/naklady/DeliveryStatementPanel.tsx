// DeliveryStatementPanel — the delivered-token statement of a version (DEV-50), in Náklady next to what the work
// cost. What a version delivered (code, tests, documentation — counted with o200k_base), what is left out and why,
// the work kind (a fix of our own error is never billed), the rates from Nastavenia, the amount of every line (the
// total is their sum), and what the agent's work cost per 1 000 delivered tokens — so the rates rest on data.
// „Vydať súpis“ freezes it; an issued one downloads as CSV.

import { useCallback, useEffect, useState } from "react";
import { Download, FileText, Loader2 } from "lucide-react";

import ErrorNote from "@/components/common/ErrorNote";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import {
  downloadStatementCsv,
  getDeliveryStatement,
  issueDeliveryStatement,
  setWorkKind,
  type DeliveryStatementView,
} from "@/services/api/deliveryStatements";
import { WORK_KIND_OPTIONS, type WorkKind } from "@/lib/workKind";

const int = new Intl.NumberFormat("sk-SK");
const eur = (v: string | number | null | undefined) =>
  v == null ? "—" : `${Number(v).toLocaleString("sk-SK", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`;
const rate = (v: string) => `${Number(v).toLocaleString("sk-SK", { maximumFractionDigits: 4 })} € / 1 000 tokenov`;

const KIND_LABELS: Record<string, string> = { kod: "kód", skusky: "skúšky", dokumentacia: "dokumentácia" };

export default function DeliveryStatementPanel({ versionId }: { versionId: string }) {
  const [view, setView] = useState<DeliveryStatementView | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<HumanError | null>(null);
  const [showFiles, setShowFiles] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    getDeliveryStatement(versionId)
      .then(setView)
      .catch((e: unknown) => setError(humanizeApiError(e, "Súpis dodaných tokenov sa nepodarilo načítať")))
      .finally(() => setLoading(false));
  }, [versionId]);

  useEffect(() => {
    load();
  }, [load]);

  async function act(run: () => Promise<unknown>, failure: string) {
    setBusy(true);
    setError(null);
    try {
      await run();
      load();
    } catch (e: unknown) {
      setError(humanizeApiError(e, failure));
    } finally {
      setBusy(false);
    }
  }

  const p = view?.preview;
  const counted = p?.files.filter((f) => f.kind) ?? [];
  const excluded = p?.files.filter((f) => !f.kind) ?? [];
  const codeTokens = (p?.code?.tokens ?? 0) + (p?.tests?.tokens ?? 0);
  const kindLabel = (k: WorkKind | null) => WORK_KIND_OPTIONS.find((o) => o.value === k)?.label ?? "neurčený";

  return (
    <section
      className="mb-6 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-surface)] p-4"
      data-testid="delivery-statement"
    >
      <h2 className="mb-1 flex items-center gap-1.5 text-sm font-semibold text-[var(--color-text-secondary)]">
        <FileText className="h-4 w-4" />
        Súpis dodaných tokenov{p ? ` — verzia ${p.version_number}` : ""}
      </h2>
      <p className="mb-3 text-xs text-[var(--color-text-muted)]">
        Podklad k faktúre: kód, skúšky a dokumentácia, ktoré verzia naozaj dodala — nie tokeny, ktoré spotreboval
        agent.
      </p>
      <ErrorNote error={error} className="mb-2" />
      {loading && !view && <Loader2 className="h-4 w-4 animate-spin text-[var(--color-text-muted)]" />}
      {p?.blocked && <p className="text-xs text-[var(--color-text-muted)]">{p.blocked}</p>}

      {p && !p.blocked && (
        <>
          <p className="mb-2 text-[11px] text-[var(--color-text-muted)]">
            Od stavu kódu <span className="font-mono">{p.base_sha?.slice(0, 7)}</span> po{" "}
            <span className="font-mono">{p.delivered_sha?.slice(0, 7)}</span> (
            {p.delivered_source_label ?? p.delivered_source}) · meria {p.tokenizer}
          </p>

          <div className="mb-3 text-xs">
            <span className="text-[var(--color-text-secondary)]">Druh práce: </span>
            <span className="font-medium text-[var(--color-text-primary)]">{kindLabel(p.work_kind)}</span>
            <span className="ml-2 inline-flex gap-1">
              {WORK_KIND_OPTIONS.filter((o) => o.value !== p.work_kind).map((o) => (
                <button
                  key={o.value}
                  type="button"
                  disabled={busy}
                  onClick={() => act(() => setWorkKind(versionId, o.value), "Druh práce sa nepodarilo uložiť")}
                  className="rounded border border-[var(--color-border-default)] px-1.5 py-0.5 text-[11px] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] disabled:opacity-50"
                >
                  {p.work_kind ? `Zmeniť na: ${o.label}` : o.label}
                </button>
              ))}
            </span>
          </div>

          <table className="mb-2 w-full text-xs">
            <thead className="text-left text-[var(--color-text-muted)]">
              <tr>
                <th className="py-1 font-normal">Druh</th>
                <th className="py-1 text-right font-normal">Riadkov</th>
                <th className="py-1 text-right font-normal">Tokenov</th>
                <th className="py-1 text-right font-normal">Sadzba</th>
                <th className="py-1 text-right font-normal">Suma</th>
              </tr>
            </thead>
            <tbody className="text-[var(--color-text-primary)]">
              <tr>
                <td className="py-1">
                  Kód a skúšky{" "}
                  <span className="text-[var(--color-text-muted)]">
                    (kód {int.format(p.code?.tokens ?? 0)}, skúšky {int.format(p.tests?.tokens ?? 0)})
                  </span>
                </td>
                <td className="py-1 text-right">{int.format((p.code?.lines ?? 0) + (p.tests?.lines ?? 0))}</td>
                <td className="py-1 text-right">{int.format(codeTokens)}</td>
                <td className="py-1 text-right">{rate(p.rate_code)}</td>
                <td className="py-1 text-right" data-testid="statement-amount-code">
                  {eur(p.amount_code_eur)}
                </td>
              </tr>
              <tr>
                <td className="py-1">Dokumentácia</td>
                <td className="py-1 text-right">{int.format(p.docs?.lines ?? 0)}</td>
                <td className="py-1 text-right">{int.format(p.docs?.tokens ?? 0)}</td>
                <td className="py-1 text-right">{rate(p.rate_docs)}</td>
                <td className="py-1 text-right" data-testid="statement-amount-docs">
                  {eur(p.amount_docs_eur)}
                </td>
              </tr>
            </tbody>
          </table>
          <p className="mb-2 text-sm font-semibold text-[var(--color-text-primary)]" data-testid="statement-amount">
            Suma: {eur(p.amount_eur)}
            {p.work_kind === "fix" && (
              <span className="ml-2 text-xs font-normal text-[var(--color-text-muted)]">
                oprava chyby v dodanom kóde — neúčtuje sa
              </span>
            )}
          </p>

          {p.calibration && (
            <p className="mb-2 text-[11px] text-[var(--color-text-muted)]" data-testid="statement-calibration">
              Práca agenta na verzii: kód a skúšky {eur(p.calibration.eur_code)}
              {p.calibration.per_1k_code != null && ` (≈ ${eur(p.calibration.per_1k_code)} na 1 000 tokenov)`},
              dokumentácia {eur(p.calibration.eur_docs)}
              {p.calibration.per_1k_docs != null && ` (≈ ${eur(p.calibration.per_1k_docs)} na 1 000 tokenov)`}
              {!p.calibration.complete && " — časť práce sa nedá oceniť, skutočná cena je vyššia"}.
            </p>
          )}

          {p.cannot_issue.map((why) => (
            <p
              key={why}
              className="mb-1 rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-xs text-amber-700 dark:text-amber-300"
            >
              {why}
            </p>
          ))}

          <div className="mt-2 flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={busy || p.cannot_issue.length > 0}
              onClick={() => act(() => issueDeliveryStatement(versionId), "Súpis sa nepodarilo vydať")}
              className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500 disabled:opacity-50"
            >
              Vydať súpis
            </button>
            <button
              type="button"
              onClick={() => setShowFiles((v) => !v)}
              className="text-xs text-[var(--color-text-secondary)] underline"
            >
              {showFiles ? "Skryť súbory" : `Súbory: ${counted.length} rátaných, ${excluded.length} vynechaných`}
            </button>
          </div>

          {showFiles && (
            <table className="mt-2 w-full text-[11px]" data-testid="statement-files">
              <tbody>
                {[...counted, ...excluded].map((f) => (
                  <tr key={f.path} className={f.kind ? "" : "text-[var(--color-text-muted)]"}>
                    <td className="py-0.5 pr-2 font-mono">{f.path}</td>
                    <td className="py-0.5 pr-2">{f.kind ? KIND_LABELS[f.kind] ?? f.kind : `neráta sa — ${f.excluded}`}</td>
                    <td className="py-0.5 text-right">{f.kind ? int.format(f.lines) : ""}</td>
                    <td className="py-0.5 text-right">{f.kind ? int.format(f.tokens) : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}

      {view && view.issued.length > 0 && (
        <div className="mt-4">
          <h3 className="mb-1 text-xs font-semibold text-[var(--color-text-secondary)]">Vydané súpisy</h3>
          <ul className="space-y-1 text-xs">
            {view.issued.map((s) => (
              <li key={s.id} className="flex items-center justify-between gap-2" data-testid="issued-statement">
                <span>
                  {new Date(s.created_at).toLocaleString("sk-SK")} · {kindLabel(s.work_kind)} ·{" "}
                  {int.format(s.tokens_code + s.tokens_tests + s.tokens_docs)} tokenov · {eur(s.amount_eur)}
                </span>
                <button
                  type="button"
                  onClick={() =>
                    act(
                      () => downloadStatementCsv(s.id, `supis-${view.preview.version_number}.csv`),
                      "Súpis sa nepodarilo stiahnuť",
                    )
                  }
                  className="flex items-center gap-1 text-[var(--color-text-secondary)] underline hover:text-[var(--color-text-primary)]"
                >
                  <Download className="h-3 w-3" />
                  Stiahnuť CSV
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
