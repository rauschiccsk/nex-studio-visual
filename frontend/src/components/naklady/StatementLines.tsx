// StatementLines — the lines of a delivery statement (DEV-75): the billing category of the task that wrote the code ×
// (code and tests, documentation), work without a task on lines of its own in the version's category. Each line shows
// its full value, the discount and the amount, so a warranty fix or a gift is visible on the invoice with what it was
// worth; the total is the sum of the amounts. DEV-263: tokens in kT (the server rounds them up to a tenth), no count of
// source lines — the Director 30.10.2026: „stĺpec počet riadkov odstrániť, … je to irelevantný údaj“.

import { useState } from "react";

import type { StatementLine } from "@/services/api/deliveryStatements";
import { formatEur, formatKiloTokens } from "@/utils/format";

const eur = formatEur;
const kt = formatKiloTokens;
const pct = (v: string) => `${Number(v).toLocaleString("sk-SK", { maximumFractionDigits: 2 })} %`;
const rate = (v: string) => `${Number(v).toLocaleString("sk-SK", { maximumFractionDigits: 4 })} €`;
/** DEV-265 — a number column: its figure and unit stay on one line, apart from the column before it. */
const NUM = "py-1 pl-3 text-right whitespace-nowrap";
/** DEV-265 — how many tasks a line names before „… a ďalších N“; NEX Knowledge 0.1.0 named 31 and broke the table. */
const TASKS_SHOWN = 6;

function TaskLabels({ labels }: { labels: string[] }) {
  const [all, setAll] = useState(false);
  const shown = all ? labels : labels.slice(0, TASKS_SHOWN);
  const more = labels.length - shown.length;
  return (
    <span className="block text-[10px] text-[var(--color-text-muted)]">
      úlohy {shown.join(", ")}
      {more > 0 && (
        <button type="button" onClick={() => setAll(true)} className="ml-1 underline">
          … a ďalších {more}
        </button>
      )}
      {all && labels.length > TASKS_SHOWN && (
        <button type="button" onClick={() => setAll(false)} className="ml-1 underline">
          skryť
        </button>
      )}
    </span>
  );
}

export default function StatementLines({
  lines,
  full,
  total,
}: {
  lines: StatementLine[];
  /** Before the discounts. */
  full: string | null;
  total: string | null;
}) {
  const discount = lines.reduce((sum, ln) => sum + Number(ln.discount_eur), 0);
  return (
    <div className="mb-2 overflow-x-auto">
      <table className="w-full min-w-[34rem] text-xs" data-testid="statement-lines">
        <thead className="text-left text-[var(--color-text-muted)]">
          <tr>
            <th className="py-1 font-normal">Riadok súpisu</th>
            <th className={`${NUM} font-normal`}>kT</th>
            <th className={`${NUM} font-normal`} title="€ za kT (1 000 tokenov)">
              Sadzba
            </th>
            <th className={`${NUM} font-normal`}>Plná hodnota</th>
            <th className={`${NUM} font-normal`}>Zľava</th>
            <th className={`${NUM} font-normal`}>Suma</th>
          </tr>
        </thead>
        <tbody className="text-[var(--color-text-primary)]">
          {lines.map((ln) => (
            <tr key={ln.label} data-testid="statement-line">
              <td className="py-1 pr-2">
                {ln.label}
                {ln.tasks.length > 0 && <TaskLabels labels={ln.tasks.map((t) => t.label)} />}
              </td>
              <td className={NUM}>{kt(ln.kilotokens)}</td>
              <td className={NUM}>{rate(ln.rate)}</td>
              <td className={NUM}>{eur(ln.full_eur)}</td>
              <td className="py-1 pl-3 text-right">
                {Number(ln.discount_pct) > 0 ? (
                  <>
                    <span className="whitespace-nowrap">−{eur(ln.discount_eur)}</span>
                    <span className="block text-[10px] text-[var(--color-text-muted)]">
                      {pct(ln.discount_pct)}
                      {ln.discount_label ? ` · ${ln.discount_label}` : ""}
                    </span>
                  </>
                ) : (
                  "—"
                )}
              </td>
              <td className={`${NUM} font-medium`}>{eur(ln.amount_eur)}</td>
            </tr>
          ))}
          <tr className="border-t border-[var(--color-border-default)] font-medium">
            <td className="py-1">Spolu</td>
            <td className={NUM}>{kt(lines.reduce((s, ln) => s + Number(ln.kilotokens), 0))}</td>
            <td />
            <td className={NUM}>{eur(full)}</td>
            <td className={NUM}>{discount > 0 ? `−${eur(discount)}` : "—"}</td>
            <td className={NUM} data-testid="statement-lines-total">
              {eur(total)}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}
