/**
 * DEV-225 — the orientačná cena lives on the billing tab, at every version with a plan.
 *
 * 26.10.2026 it was measured only when someone opened the version's page, and the billing tab showed a small grey line
 * of what that left behind. Director: „podľa mňa táto informácia vecne patrí skôr do priečinku Fakturácia“ … „Áno,
 * súhlasím, založ tiket a implementuj“. Pinned: the estimate is a block of its own under the statement's amount — the
 * amount with its spread, what it rests on, how it is made up, rough when it rests on too few finished versions — at
 * the versions that have one and nowhere else, and the page says it is no invoice.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import BillingTab from "@/components/naklady/BillingTab";
import type { BillingOverview, BillingRow, PriceEstimate } from "@/services/api/deliveryStatements";

const { billingMock } = vi.hoisted(() => ({ billingMock: vi.fn() }));

vi.mock("@/services/api/deliveryStatements", () => ({ getProjectBilling: billingMock }));
vi.mock("@/components/naklady/DeliveryStatementPanel", () => ({ default: () => null }));

const ESTIMATE: PriceEstimate = {
  tasks: 42,
  first_version: false,
  code_tokens: 210_000,
  docs_tokens: 30_000,
  code_kilotokens: "210.0",
  docs_kilotokens: "30.0",
  rate_code: "2.0000",
  rate_docs: "1.0000",
  code_eur: "420.00",
  docs_eur: "30.00",
  amount_eur: "1450.00",
  spread_pct: 26,
  samples: 3,
  enough: false,
  created_at: "2026-10-22T19:00:00Z",
};

const row = (over: Partial<BillingRow>): BillingRow => ({
  version_id: "v",
  version_number: "0.0.0",
  flow_type: "new_version",
  state: "ready",
  reason: null,
  work_kind: "change",
  base_sha: "aaaaaaa1",
  delivered_sha: "bbbbbbb2",
  delivered_source_label: null,
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

const billing = (rows: BillingRow[]): BillingOverview => ({
  rows,
  issued_total_eur: "0.00",
  ready_total_eur: "0.00",
  needs_kind: 0,
});

const blocks = () => screen.queryAllByRole("region", { name: "Orientačná cena" });

describe("BillingTab — the orientačná cena (DEV-225)", () => {
  beforeEach(() => billingMock.mockReset());

  it("is a block of its own under the version's amount: the amount, what it rests on, how it is made up", async () => {
    billingMock.mockResolvedValue(
      billing([
        row({ version_id: "v170", version_number: "1.7.0", preview_amount_eur: "1523.40", estimate: ESTIMATE }),
        row({ version_id: "v160", version_number: "1.6.0", estimate: null }),
      ]),
    );
    render(<BillingTab slug="nex-inbox" />);

    await screen.findByText("1.7.0");
    expect(blocks()).toHaveLength(1);
    const block = blocks()[0]!;
    expect(within(block).getByText("cca 1 450,00 € ± 26 %")).toBeInTheDocument();
    expect(block).toHaveTextContent(
      "Orientačná cena — odhad podľa 42 úloh v pláne verzie, z 3 hotových verzií — ďalších verzií projektov.",
    );
    expect(block).toHaveTextContent(
      "Kód a skúšky ≈ 210,0 kT = 420,00 € · dokumentácia ≈ 30,0 kT = 30,00 €",
    );
    expect(block).toHaveTextContent(/Opiera sa len o 3 hotové verzie, preto je hrubý/);
    // under 1.7.0, not under 1.6.0 — the version it belongs to
    expect(screen.getByText("1.7.0").closest("li")).toContainElement(block);
  });

  it("a version still in build carries it before any statement; resting on enough versions it is not rough", async () => {
    billingMock.mockResolvedValue(
      billing([
        row({
          version_number: "0.1.0",
          state: "not_done",
          preview_amount_eur: null,
          estimate: { ...ESTIMATE, first_version: true, amount_eur: "486.40", spread_pct: 57, samples: 6, enough: true },
        }),
      ]),
    );
    render(<BillingTab slug="author-asistent" />);

    const block = (await screen.findAllByRole("region", { name: "Orientačná cena" }))[0]!;
    expect(within(block).getByText("cca 486,40 € ± 57 %")).toBeInTheDocument();
    expect(block).toHaveTextContent("z 6 hotových verzií — prvých verzií projektov, ktoré stavajú kostru aplikácie.");
    expect(block).not.toHaveTextContent(/Opiera sa len o/);
  });

  it("the page says the orientačná cena is no invoice", async () => {
    billingMock.mockResolvedValue(billing([row({ estimate: ESTIMATE })]));
    render(<BillingTab slug="nex-inbox" />);

    expect(
      await screen.findByText(/Orientačná cena je odhad z plánu úloh verzie, nie faktúra — účtuje sa podľa súpisu dodávky\./),
    ).toBeInTheDocument();
  });
});
