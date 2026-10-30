/**
 * DEV-265 — a statement line that names many tasks names a few and „… a ďalších N“, so the table does not break.
 * 30.10.2026 a line of NEX Knowledge 0.1.0 named 31 tasks; the number columns broke after their digits.
 */

import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import StatementLines from "@/components/naklady/StatementLines";
import type { StatementLine } from "@/services/api/deliveryStatements";

const task = (i: number) => ({ label: `${i}.1.1`, title: `Úloha ${i}`, lines: 1, tokens: 100, kilotokens: "0.1" });

function line(count: number): StatementLine {
  return {
    category: "request",
    kind: "code",
    tasked: true,
    label: "Požiadavka zákazníka — kód a skúšky",
    lines: 1,
    tokens: 280_600,
    kilotokens: "280.6",
    rate: "2.0000",
    full_eur: "561.20",
    discount_pct: "0.00",
    discount_label: null,
    discount_eur: "0.00",
    amount_eur: "561.20",
    tasks: Array.from({ length: count }, (_, i) => task(i + 1)),
  };
}

describe("StatementLines — a line with many tasks (DEV-265)", () => {
  it("names six tasks and how many more, and opens and closes the whole list", () => {
    render(<StatementLines lines={[line(31)]} full="561.20" total="561.20" />);

    expect(screen.getByText(/^úlohy 1\.1\.1, 2\.1\.1, 3\.1\.1, 4\.1\.1, 5\.1\.1, 6\.1\.1$/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "… a ďalších 25" }));
    expect(screen.getByText(/^úlohy 1\.1\.1, .*, 31\.1\.1$/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /a ďalších/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "skryť" }));
    expect(screen.getByRole("button", { name: "… a ďalších 25" })).toBeInTheDocument();
  });

  it("names every task of a line with six or fewer, without a button", () => {
    render(<StatementLines lines={[line(6)]} full="561.20" total="561.20" />);

    expect(screen.getByText(/^úlohy 1\.1\.1, .*, 6\.1\.1$/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
