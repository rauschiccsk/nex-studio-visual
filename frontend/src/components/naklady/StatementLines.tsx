// StatementLines — the lines of a delivery statement (DEV-75): the billing category of the task that wrote the code ×
// (code and tests, documentation), work without a task on lines of its own in the version's category. Each line shows
// its full value, the discount and the amount, so a warranty fix or a gift is visible on the invoice with what it was
// worth; the total is the sum of the amounts.

import type { StatementLine } from "@/services/api/deliveryStatements";

const int = new Intl.NumberFormat("sk-SK");
const eur = (v: string | number | null | undefined) =>
  v == null ? "—" : `${Number(v).toLocaleString("sk-SK", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} €`;
const pct = (v: string) => `${Number(v).toLocaleString("sk-SK", { maximumFractionDigits: 2 })} %`;
const rate = (v: string) => `${Number(v).toLocaleString("sk-SK", { maximumFractionDigits: 4 })} €`;

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
            <th className="py-1 text-right font-normal">Riadkov</th>
            <th className="py-1 text-right font-normal">Tokenov</th>
            <th className="py-1 text-right font-normal" title="€ za 1 000 tokenov">
              Sadzba
            </th>
            <th className="py-1 text-right font-normal">Plná hodnota</th>
            <th className="py-1 text-right font-normal">Zľava</th>
            <th className="py-1 text-right font-normal">Suma</th>
          </tr>
        </thead>
        <tbody className="text-[var(--color-text-primary)]">
          {lines.map((ln) => (
            <tr key={ln.label} data-testid="statement-line">
              <td className="py-1 pr-2">
                {ln.label}
                {ln.tasks.length > 0 && (
                  <span className="block text-[10px] text-[var(--color-text-muted)]">
                    úlohy {ln.tasks.map((t) => t.label).join(", ")}
                  </span>
                )}
              </td>
              <td className="py-1 text-right">{int.format(ln.lines)}</td>
              <td className="py-1 text-right">{int.format(ln.tokens)}</td>
              <td className="py-1 text-right">{rate(ln.rate)}</td>
              <td className="py-1 text-right">{eur(ln.full_eur)}</td>
              <td className="py-1 text-right">
                {Number(ln.discount_pct) > 0 ? (
                  <>
                    −{eur(ln.discount_eur)}
                    <span className="block text-[10px] text-[var(--color-text-muted)]">
                      {pct(ln.discount_pct)}
                      {ln.discount_label ? ` · ${ln.discount_label}` : ""}
                    </span>
                  </>
                ) : (
                  "—"
                )}
              </td>
              <td className="py-1 text-right font-medium">{eur(ln.amount_eur)}</td>
            </tr>
          ))}
          <tr className="border-t border-[var(--color-border-default)] font-medium">
            <td className="py-1">Spolu</td>
            <td className="py-1 text-right">{int.format(lines.reduce((s, ln) => s + ln.lines, 0))}</td>
            <td className="py-1 text-right">{int.format(lines.reduce((s, ln) => s + ln.tokens, 0))}</td>
            <td />
            <td className="py-1 text-right">{eur(full)}</td>
            <td className="py-1 text-right">{discount > 0 ? `−${eur(discount)}` : "—"}</td>
            <td className="py-1 text-right" data-testid="statement-lines-total">
              {eur(total)}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}
