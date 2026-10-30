/**
 * DEV-74 — a version's own token rates, set in its statement: the default stays the rate in Nastavenia, the
 * manager changes it for this version only, sees who changed it last, and can return to Nastavenia.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "@testing-library/jest-dom/vitest";

import DeliveryStatementPanel from "@/components/naklady/DeliveryStatementPanel";
import {
  getDeliveryStatement,
  setBillingRates,
  type DeliveryStatementView,
  type RateChange,
} from "@/services/api/deliveryStatements";

vi.mock("@/services/api/deliveryStatements", () => ({
  getDeliveryStatement: vi.fn(),
  issueDeliveryStatement: vi.fn(),
  setWorkKind: vi.fn(),
  setBillingRates: vi.fn(),
  downloadStatementCsv: vi.fn(),
}));

function view(own: boolean, changes: RateChange[] = []): DeliveryStatementView {
  return {
    preview: {
      version_number: "1.6.0",
      blocked: null,
      work_kind: "change",
      base_sha: "4f71c9cfeb59aaaa",
      delivered_sha: "ec28fb9ab8d3bbbb",
      delivered_source: "verifikacia",
      delivered_source_label: "stav, na ktorom prešla Verifikácia",
      tokenizer: "o200k_base (tiktoken 0.14.0)",
      code: { tokens: 23717, lines: 1828, kilotokens: "23.8" },
      tests: { tokens: 18912, lines: 1410, kilotokens: "19.0" },
      docs: { tokens: 38795, lines: 1537, kilotokens: "38.8" },
      code_and_tests_kilotokens: "42.7",
      files: [],
      rate_code: own ? "1.5000" : "2.0000",
      rate_docs: "1.0000",
      settings_rate_code: "2.0000",
      settings_rate_docs: "1.0000",
      rate_code_own: own,
      rate_docs_own: false,
      amount_code_eur: own ? "63.94" : "85.26",
      amount_docs_eur: "38.80",
      amount_eur: own ? "102.74" : "124.06",
      cannot_issue: [],
      calibration: null,
      version_category: "request",
      discounts: { request: "0", bug: "100", gift: "100" },
      settings_discounts: { request: "0", bug: "100", gift: "100" },
      discounts_own: { request: false, bug: false, gift: false },
      lines: [],
      tasks: [],
      full_eur: null,
    },
    issued: [],
    rate_changes: changes,
    discount_changes: [],
  };
}

const CHANGE: RateChange = {
  created_at: "2026-10-12T10:00:00Z",
  changed_by: "Zoltán Rausch",
  previous_rate_code: null,
  previous_rate_docs: null,
  rate_code: "1.5000",
  rate_docs: null,
};

beforeEach(() => {
  vi.clearAllMocks();
});

describe("A version's own rates (DEV-74)", () => {
  it("says where the rates come from and who changed them last", async () => {
    vi.mocked(getDeliveryStatement).mockResolvedValue(view(true, [CHANGE]));
    render(<DeliveryStatementPanel versionId="v160" />);

    expect(await screen.findByText(/kód a skúšky 1,5 € \/ kT \(vlastná, v Nastaveniach 2 € \/ kT/)).toBeInTheDocument();
    expect(screen.getByText(/dokumentácia 1 € \/ kT \(z Nastavení\)/)).toBeInTheDocument();
    expect(screen.getByText(/Naposledy zmenil Zoltán Rausch .*kód z Nastavení → 1,5 €/)).toBeInTheDocument();
  });

  it("saves a rate typed with a decimal comma for this version only and reloads", async () => {
    vi.mocked(getDeliveryStatement).mockResolvedValue(view(false));
    vi.mocked(setBillingRates).mockResolvedValue([CHANGE]);
    const onChange = vi.fn();
    render(<DeliveryStatementPanel versionId="v160" onChange={onChange} />);

    await userEvent.click(await screen.findByRole("button", { name: "Zmeniť sadzby pre túto verziu" }));
    await userEvent.type(screen.getByLabelText("Kód a skúšky (€ / kT)"), "1,5");
    await userEvent.click(screen.getByRole("button", { name: "Uložiť sadzby" }));

    await waitFor(() => expect(setBillingRates).toHaveBeenCalledWith("v160", { rate_code: "1.5", rate_docs: null }));
    await waitFor(() => expect(getDeliveryStatement).toHaveBeenCalledTimes(2));
    expect(onChange).toHaveBeenCalled();
  });

  it("refuses a rate that is no price", async () => {
    vi.mocked(getDeliveryStatement).mockResolvedValue(view(false));
    render(<DeliveryStatementPanel versionId="v160" />);

    await userEvent.click(await screen.findByRole("button", { name: "Zmeniť sadzby pre túto verziu" }));
    await userEvent.type(screen.getByLabelText("Kód a skúšky (€ / kT)"), "0");
    await userEvent.click(screen.getByRole("button", { name: "Uložiť sadzby" }));

    expect(screen.getByRole("alert")).toHaveTextContent("Sadzba musí byť číslo väčšie ako 0");
    expect(setBillingRates).not.toHaveBeenCalled();
  });

  it("returns an own rate to Nastavenia", async () => {
    vi.mocked(getDeliveryStatement).mockResolvedValue(view(true, [CHANGE]));
    vi.mocked(setBillingRates).mockResolvedValue([]);
    render(<DeliveryStatementPanel versionId="v160" />);

    await userEvent.click(await screen.findByRole("button", { name: "Zmeniť sadzby pre túto verziu" }));
    await userEvent.click(screen.getByRole("button", { name: "Vrátiť sadzby z Nastavení" }));

    await waitFor(() => expect(setBillingRates).toHaveBeenCalledWith("v160", { rate_code: null, rate_docs: null }));
  });
});
