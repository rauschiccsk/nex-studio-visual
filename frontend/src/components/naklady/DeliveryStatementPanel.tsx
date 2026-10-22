// DeliveryStatementPanel — the delivered-token statement of a version (DEV-50), in Náklady next to what the work
// cost. What a version delivered (code, tests, documentation — counted with o200k_base), what is left out and why,
// the work kind (a fix of our own error is never billed), the rates from Nastavenia, the amount of every line (the
// total is their sum), and what the agent's work cost per 1 000 delivered tokens — so the rates rest on data.
// „Vydať súpis“ freezes it; an issued one downloads as CSV. A version has one valid statement (DEV-54): issuing
// again asks first and replaces it — the old one stays in the list, greyed and marked as replaced.
// DEV-75: the lines are the billing category of the task that wrote the code × kind, each with full value, discount
// and amount; the version's discounts and its tasks' categories are confirmed or changed here before issuing.

import { useCallback, useEffect, useState } from "react";
import { Download, FileText, Loader2 } from "lucide-react";

import ErrorNote from "@/components/common/ErrorNote";
import StatementLines from "@/components/naklady/StatementLines";
import StatementTasks from "@/components/naklady/StatementTasks";
import VersionDiscounts from "@/components/naklady/VersionDiscounts";
import { humanizeApiError, type HumanError } from "@/services/apiError";
import {
  downloadStatementCsv,
  getDeliveryStatement,
  issueDeliveryStatement,
  setBillingDiscounts,
  setBillingRates,
  setTaskCategories,
  setWorkKind,
  type DeliveryStatementView,
  type RateChange,
  type StatementPreview,
} from "@/services/api/deliveryStatements";
import { WORK_KIND_OPTIONS, type WorkKind } from "@/lib/workKind";
import { formatDateTime } from "@/utils/format";

const int = new Intl.NumberFormat("sk-SK");
const eur = (v: string | number | null | undefined) =>
  v == null ? "—" : `${Number(v).toLocaleString("sk-SK", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`;
const when = (iso: string) => formatDateTime(iso);
const rate = (v: string) => `${Number(v).toLocaleString("sk-SK", { maximumFractionDigits: 4 })} € / 1 000 tokenov`;

const KIND_LABELS: Record<string, string> = { kod: "kód", skusky: "skúšky", dokumentacia: "dokumentácia" };

/** A rate as typed — with a decimal comma or point; "" = back to Nastavenia; `undefined` = not a rate. */
function parseRate(text: string): string | null | undefined {
  const t = text.trim().replace(",", ".");
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) && n > 0 ? t : undefined;
}

/**
 * DEV-74 — the rates this version's statement counts with: its own, or those in Nastavenia; changed here for this
 * version only. An issued statement keeps its rates — a new one replacing it counts with the new ones.
 */
function VersionRates({
  preview,
  changes,
  busy,
  onSave,
}: {
  preview: StatementPreview;
  changes: RateChange[];
  busy: boolean;
  onSave: (code: string | null, docs: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState("");
  const [docs, setDocs] = useState("");
  const [invalid, setInvalid] = useState(false);
  const own = preview.rate_code_own || preview.rate_docs_own;
  const last = changes[0];
  const source = (isOwn: boolean, settings: string) =>
    isOwn ? `vlastná, v Nastaveniach ${rate(settings)}` : "z Nastavení";

  function start() {
    setCode(preview.rate_code_own ? Number(preview.rate_code).toString().replace(".", ",") : "");
    setDocs(preview.rate_docs_own ? Number(preview.rate_docs).toString().replace(".", ",") : "");
    setInvalid(false);
    setOpen(true);
  }

  function save(c: string, d: string) {
    const pc = parseRate(c);
    const pd = parseRate(d);
    if (pc === undefined || pd === undefined) {
      setInvalid(true);
      return;
    }
    onSave(pc, pd);
    setOpen(false);
  }

  return (
    <div className="mb-3 rounded border border-[var(--color-border-default)] px-2 py-1.5 text-[11px] text-[var(--color-text-muted)]">
      <div>
        Sadzby tejto verzie: kód a skúšky {rate(preview.rate_code)} ({source(preview.rate_code_own, preview.settings_rate_code)}
        ), dokumentácia {rate(preview.rate_docs)} ({source(preview.rate_docs_own, preview.settings_rate_docs)}).
      </div>
      {last && (
        <div>
          Naposledy zmenil{last.changed_by ? ` ${last.changed_by}` : "i"} {when(last.created_at)}: kód{" "}
          {last.previous_rate_code ? rate(last.previous_rate_code) : "z Nastavení"} →{" "}
          {last.rate_code ? rate(last.rate_code) : "z Nastavení"}, dokumentácia{" "}
          {last.previous_rate_docs ? rate(last.previous_rate_docs) : "z Nastavení"} →{" "}
          {last.rate_docs ? rate(last.rate_docs) : "z Nastavení"}.
        </div>
      )}
      <button
        type="button"
        aria-expanded={open}
        disabled={busy}
        onClick={() => (open ? setOpen(false) : start())}
        className="mt-1 text-primary-400 hover:text-primary-300 disabled:opacity-50"
      >
        {open ? "Zrušiť zmenu sadzieb" : "Zmeniť sadzby pre túto verziu"}
      </button>
      {open && (
        <div className="mt-2 space-y-2">
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <label className="block">
              <span className="text-[var(--color-text-secondary)]">Kód a skúšky (€ / 1 000 tokenov)</span>
              <input
                inputMode="decimal"
                spellCheck={false}
                value={code}
                onChange={(e) => setCode(e.target.value)}
                placeholder={`z Nastavení: ${Number(preview.settings_rate_code).toLocaleString("sk-SK")}`}
                className="mt-1 w-full rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1 text-sm text-[var(--color-text-primary)]"
              />
            </label>
            <label className="block">
              <span className="text-[var(--color-text-secondary)]">Dokumentácia (€ / 1 000 tokenov)</span>
              <input
                inputMode="decimal"
                spellCheck={false}
                value={docs}
                onChange={(e) => setDocs(e.target.value)}
                placeholder={`z Nastavení: ${Number(preview.settings_rate_docs).toLocaleString("sk-SK")}`}
                className="mt-1 w-full rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1 text-sm text-[var(--color-text-primary)]"
              />
            </label>
          </div>
          {invalid && (
            <p role="alert" className="text-[var(--color-state-error-fg)]">
              Sadzba musí byť číslo väčšie ako 0 — prázdne pole znamená sadzbu z Nastavení.
            </p>
          )}
          <p>Prázdne pole = sadzba z Nastavení. Vydaný súpis sa nezmení — novú sadzbu použije až súpis, ktorý ho nahradí.</p>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() => save(code, docs)}
              className="rounded bg-primary-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-primary-500 disabled:opacity-50"
            >
              Uložiť sadzby
            </button>
            {own && (
              <button
                type="button"
                disabled={busy}
                onClick={() => save("", "")}
                className="rounded border border-[var(--color-border-default)] px-2.5 py-1 text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] disabled:opacity-50"
              >
                Vrátiť sadzby z Nastavení
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default function DeliveryStatementPanel({
  versionId,
  onChange,
}: {
  versionId: string;
  /** DEV-73: told after a statement is issued or the work kind decided — the billing overview reloads. */
  onChange?: () => void;
}) {
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
      onChange?.();
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
  const valid = view?.issued.find((s) => !s.replaced_at) ?? null;

  function issue() {
    if (
      valid &&
      !window.confirm(
        `Nahradiť súpis z ${when(valid.created_at)} za ${eur(valid.amount_eur)}? ` +
          "Ostane v zozname ako nahradený a na faktúru sa už nepoužije.",
      )
    )
      return;
    act(() => issueDeliveryStatement(versionId, valid?.id), "Súpis sa nepodarilo vydať");
  }

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

          {p.lines.length > 0 ? (
            <StatementLines lines={p.lines} full={p.full_eur} total={p.amount_eur} />
          ) : (
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
          )}
          <VersionRates
            preview={p}
            changes={view?.rate_changes ?? []}
            busy={busy}
            onSave={(code, docs) =>
              act(() => setBillingRates(versionId, { rate_code: code, rate_docs: docs }), "Sadzby sa nepodarilo uložiť")
            }
          />
          <VersionDiscounts
            preview={p}
            changes={view?.discount_changes ?? []}
            busy={busy}
            onSave={(discounts) => act(() => setBillingDiscounts(versionId, discounts), "Zľavy sa nepodarilo uložiť")}
          />
          <StatementTasks
            tasks={p.tasks}
            fallback={p.version_category}
            busy={busy}
            onSet={(taskIds, category) =>
              act(() => setTaskCategories(versionId, taskIds, category), "Kategóriu úloh sa nepodarilo uložiť")
            }
          />
          <p className="mb-2 text-sm font-semibold text-[var(--color-text-primary)]" data-testid="statement-amount">
            Suma: {eur(p.amount_eur)}
            {p.full_eur != null && p.amount_eur != null && Number(p.full_eur) !== Number(p.amount_eur) && (
              <span className="ml-2 text-xs font-normal text-[var(--color-text-muted)]">
                plná hodnota {eur(p.full_eur)}, zľava {eur(Number(p.full_eur) - Number(p.amount_eur))}
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
              onClick={issue}
              className="rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-primary-500 disabled:opacity-50"
            >
              {valid ? "Vydať nový súpis" : "Vydať súpis"}
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
              <li
                key={s.id}
                className={`flex items-center justify-between gap-2 ${
                  s.replaced_at ? "text-[var(--color-text-muted)]" : "text-[var(--color-text-primary)]"
                }`}
                data-testid="issued-statement"
              >
                <span>
                  {s.replaced_at ? (
                    <span className="mr-1.5">nahradený {when(s.replaced_at)} ·</span>
                  ) : (
                    <span className="mr-1.5 rounded bg-emerald-500/15 px-1 py-0.5 text-[10px] font-medium text-emerald-700 dark:text-emerald-300">
                      platný
                    </span>
                  )}
                  {when(s.created_at)} · {kindLabel(s.work_kind)} ·{" "}
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
