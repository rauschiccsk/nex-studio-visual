/**
 * DEV-73 — the billing tab lists every version for invoicing.
 *
 * 12.10.2026 only the selected version's statement was visible (1.7.1, a fix of our own error, 0 €) and the versions
 * between 1.5.0 and 1.7.1 looked lost. Here they are all, with where each stands, the totals, and each version's own
 * statement opened in place.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import BillingTab from "@/components/naklady/BillingTab";
import type { BillingOverview, BillingRow } from "@/services/api/deliveryStatements";

const { billingMock, panelMock } = vi.hoisted(() => ({ billingMock: vi.fn(), panelMock: vi.fn() }));

vi.mock("@/services/api/deliveryStatements", () => ({ getProjectBilling: billingMock }));
vi.mock("@/components/naklady/DeliveryStatementPanel", () => ({
  default: (props: { versionId: string; onChange?: () => void }) => {
    panelMock(props);
    return (
      <button type="button" onClick={() => props.onChange?.()}>
        súpis {props.versionId}
      </button>
    );
  },
}));

const row = (over: Partial<BillingRow>): BillingRow => ({
  version_id: "v",
  version_number: "0.0.0",
  flow_type: "new_version",
  state: "ready",
  reason: null,
  work_kind: "change",
  base_sha: "aaaaaaa1",
  delivered_sha: "bbbbbbb2",
  delivered_source_label: "stav, na ktorom prešla Verifikácia",
  tokens_code: 1000,
  tokens_docs: 100,
  preview_amount_eur: "1.00",
  issued: null,
  differs: false,
  rate_code: "2",
  rate_docs: "1",
  own_rates: false,
  ...over,
});

const issued = (amount: string) => ({
  id: "s",
  created_at: "2026-10-10T15:06:04Z",
  work_kind: "change" as const,
  base_sha: "ec28fb9",
  delivered_sha: "48de080",
  delivered_source: "verifikacia",
  tokenizer: "o200k_base",
  tokens_code: 173890,
  tokens_tests: 0,
  tokens_docs: 76087,
  rate_code: "1",
  rate_docs: "1",
  amount_code_eur: null,
  amount_docs_eur: null,
  amount_eur: amount,
  replaces_id: null,
  replaced_at: null,
  lines: null,
});

const INBOX_1210: BillingOverview = {
  rows: [
    row({ version_id: "v171", version_number: "1.7.1", flow_type: "fast_fix", work_kind: "fix", state: "issued", issued: issued("0.00"), preview_amount_eur: "0.00" }),
    row({ version_id: "v170", version_number: "1.7.0", state: "issued", issued: issued("249.98"), preview_amount_eur: "423.87", differs: true, estimate_amount_eur: "230.00", estimate_spread_pct: 26 }),
    row({ version_id: "v160", version_number: "1.6.0", preview_amount_eur: "124.06" }),
    row({ version_id: "v156", version_number: "1.5.6", flow_type: "fast_fix", work_kind: null, state: "needs_kind", preview_amount_eur: null, reason: "Pri rýchlej oprave treba určiť druh práce." }),
    row({ version_id: "v150", version_number: "1.5.0", preview_amount_eur: "539.48" }),
  ],
  issued_total_eur: "249.98",
  ready_total_eur: "663.54",
  needs_kind: 1,
};

describe("BillingTab (DEV-73)", () => {
  beforeEach(() => {
    billingMock.mockReset();
    panelMock.mockReset();
    billingMock.mockResolvedValue(INBOX_1210);
  });

  it("lists every version of 12.10 with where it stands and the totals", async () => {
    render(<BillingTab slug="nex-inbox" />);

    for (const v of ["1.7.1", "1.7.0", "1.6.0", "1.5.6", "1.5.0"]) {
      expect(await screen.findByText(v)).toBeInTheDocument();
    }
    expect(screen.getByText("249,98 €", { selector: "div.text-xl" })).toBeInTheDocument();
    expect(screen.getByText("663,54 €")).toBeInTheDocument();
    expect(screen.getByText("treba určiť druh práce")).toBeInTheDocument();
    expect(screen.getByText("539,48 €")).toBeInTheDocument();
    expect(
      screen.getByText(/Platí vydaný súpis\. Podľa dnešných sadzieb by bol 423,87 €/),
    ).toBeInTheDocument();
  });

  it("DEV-154: the orientačná cena given after Návrh stands beside the statement, only where there was one", async () => {
    render(<BillingTab slug="nex-inbox" />);

    expect(await screen.findByText("orientačná cena cca 230,00 € ± 26 %")).toBeInTheDocument();
    expect(screen.getAllByText(/orientačná cena/)).toHaveLength(1);
  });

  it("opens a version's own statement in place and reloads after it changes", async () => {
    render(<BillingTab slug="nex-inbox" />);
    const opens = await screen.findAllByRole("button", { name: "Otvoriť súpis verzie" });
    expect(opens).toHaveLength(5);

    fireEvent.click(opens[2]!); // 1.6.0
    expect(panelMock).toHaveBeenLastCalledWith(expect.objectContaining({ versionId: "v160" }));
    expect(screen.getByRole("button", { name: "Skryť súpis" })).toHaveAttribute("aria-expanded", "true");

    fireEvent.click(screen.getByRole("button", { name: "súpis v160" })); // the panel issued a statement
    await waitFor(() => expect(billingMock).toHaveBeenCalledTimes(2));
  });
});
