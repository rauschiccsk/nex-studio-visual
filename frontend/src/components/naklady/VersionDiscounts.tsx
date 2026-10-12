// VersionDiscounts — the discount of each billing category this version's statement counts with (DEV-75): its own, or
// the one in Settings (a bug fix 100 % as a warranty fix, a customer request 0 %, a gift 100 %); changed here for this
// version only, every change kept. An issued statement keeps its discounts — a new one replacing it counts with these.

import { useState } from "react";

import { BILLING_CATEGORIES, CATEGORY_LABELS, CATEGORY_NAMES, type BillingCategory } from "@/lib/billingCategory";
import type { CategoryValues, DiscountChange, StatementPreview } from "@/services/api/deliveryStatements";

const when = (iso: string) => new Date(iso).toLocaleString("sk-SK");
const pct = (v: string | null) => (v == null ? "z Nastavení" : `${Number(v).toLocaleString("sk-SK")} %`);

/** A discount as typed — a decimal comma or point; "" = back to Nastavenia; `undefined` = not 0 to 100. */
function parsePercent(text: string): string | null | undefined {
  const t = text.trim().replace(",", ".").replace(/\s*%$/, "");
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) && n >= 0 && n <= 100 ? t : undefined;
}

export default function VersionDiscounts({
  preview,
  changes,
  busy,
  onSave,
}: {
  preview: StatementPreview;
  changes: DiscountChange[];
  busy: boolean;
  onSave: (discounts: CategoryValues<string | null>) => void;
}) {
  const [open, setOpen] = useState(false);
  const [typed, setTyped] = useState<CategoryValues<string>>({
    request: "",
    bug: "",
    gift: "",
  });
  const [invalid, setInvalid] = useState(false);
  const own = BILLING_CATEGORIES.some((c) => preview.discounts_own[c]);
  const last = changes[0];

  function start() {
    const value = (c: BillingCategory) =>
      preview.discounts_own[c] ? Number(preview.discounts[c]).toString().replace(".", ",") : "";
    setTyped({
      request: value("request"),
      bug: value("bug"),
      gift: value("gift"),
    });
    setInvalid(false);
    setOpen(true);
  }

  function save(values: CategoryValues<string>) {
    const parsed = Object.fromEntries(BILLING_CATEGORIES.map((c) => [c, parsePercent(values[c])])) as CategoryValues<
      string | null | undefined
    >;
    if (BILLING_CATEGORIES.some((c) => parsed[c] === undefined)) {
      setInvalid(true);
      return;
    }
    onSave(parsed as CategoryValues<string | null>);
    setOpen(false);
  }

  return (
    <div
      className="mb-3 rounded border border-[var(--color-border-default)] px-2 py-1.5 text-[11px] text-[var(--color-text-muted)]"
      data-testid="version-discounts"
    >
      <div>
        Zľavy tejto verzie:{" "}
        {BILLING_CATEGORIES.map((c, i) => (
          <span key={c}>
            {i > 0 && ", "}
            {CATEGORY_NAMES[c]} {pct(preview.discounts[c])} (
            {preview.discounts_own[c] ? `vlastná, v Nastaveniach ${pct(preview.settings_discounts[c])}` : "z Nastavení"}
            )
          </span>
        ))}
        .
      </div>
      {last && (
        <div>
          Naposledy zmenil{last.changed_by ? ` ${last.changed_by}` : "i"} {when(last.created_at)}:{" "}
          {BILLING_CATEGORIES.filter((c) => last.previous[c] !== last.current[c])
            .map((c) => `${CATEGORY_NAMES[c]} ${pct(last.previous[c])} → ${pct(last.current[c])}`)
            .join(", ")}
          .
        </div>
      )}
      <button
        type="button"
        aria-expanded={open}
        disabled={busy}
        onClick={() => (open ? setOpen(false) : start())}
        className="mt-1 text-primary-400 hover:text-primary-300 disabled:opacity-50 pointer-coarse:min-h-11"
      >
        {open ? "Zrušiť zmenu zliav" : "Zmeniť zľavy pre túto verziu"}
      </button>
      {open && (
        <div className="mt-2 space-y-2">
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            {BILLING_CATEGORIES.map((c) => (
              <label key={c} className="block">
                <span className="text-[var(--color-text-secondary)]">{CATEGORY_LABELS[c]} (%)</span>
                <input
                  inputMode="decimal"
                  spellCheck={false}
                  value={typed[c]}
                  onChange={(e) => setTyped((t) => ({ ...t, [c]: e.target.value }))}
                  placeholder={`z Nastavení: ${Number(preview.settings_discounts[c]).toLocaleString("sk-SK")}`}
                  className="mt-1 w-full rounded border border-[var(--color-border-default)] bg-[var(--color-surface)] px-2 py-1 text-sm text-[var(--color-text-primary)]"
                />
              </label>
            ))}
          </div>
          {invalid && (
            <p role="alert" className="text-[var(--color-state-error-fg)]">
              Zľava je číslo od 0 do 100 — prázdne pole znamená zľavu z Nastavení.
            </p>
          )}
          <p>
            Prázdne pole = zľava z Nastavení. Vydaný súpis sa nezmení — novú zľavu použije až súpis, ktorý ho nahradí.
          </p>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() => save(typed)}
              className="rounded bg-primary-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-primary-500 disabled:opacity-50"
            >
              Uložiť zľavy
            </button>
            {own && (
              <button
                type="button"
                disabled={busy}
                onClick={() => save({ request: "", bug: "", gift: "" })}
                className="rounded border border-[var(--color-border-default)] px-2.5 py-1 text-xs text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] disabled:opacity-50"
              >
                Vrátiť zľavy z Nastavení
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
