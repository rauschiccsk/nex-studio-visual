/**
 * DEV-75 — the statement's lines by the billing category of the task that wrote the code, each with its full value,
 * discount and amount; the version's discounts and its tasks' categories, confirmed or changed before issuing.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "@testing-library/jest-dom/vitest";

import DeliveryStatementPanel from "@/components/naklady/DeliveryStatementPanel";
import {
  getDeliveryStatement,
  setBillingDiscounts,
  setTaskCategories,
  type DeliveryStatementView,
  type StatementLine,
} from "@/services/api/deliveryStatements";

vi.mock("@/services/api/deliveryStatements", () => ({
  getDeliveryStatement: vi.fn(),
  issueDeliveryStatement: vi.fn(),
  setWorkKind: vi.fn(),
  setBillingRates: vi.fn(),
  setBillingDiscounts: vi.fn(),
  setTaskCategories: vi.fn(),
  downloadStatementCsv: vi.fn(),
}));

function line(over: Partial<StatementLine>): StatementLine {
  return {
    category: "request",
    kind: "code",
    tasked: true,
    label: "Požiadavka zákazníka — kód a skúšky",
    lines: 8_900,
    tokens: 170_000,
    rate: "2.0000",
    full_eur: "340.00",
    discount_pct: "0.00",
    discount_label: null,
    discount_eur: "0.00",
    amount_eur: "340.00",
    tasks: [{ label: "20.1.1", title: "Export", lines: 8_900, tokens: 170_000 }],
    ...over,
  };
}

const GIFT = line({
  category: "gift",
  label: "Dar — pozornosť ICC — kód a skúšky",
  lines: 400,
  tokens: 7_000,
  full_eur: "14.00",
  discount_pct: "100.00",
  discount_label: "pozornosť ICC",
  discount_eur: "14.00",
  amount_eur: "0.00",
  tasks: [{ label: "21.1.1", title: "Tmavý režim", lines: 400, tokens: 7_000 }],
});
const UNTASKED = line({
  kind: "docs",
  tasked: false,
  label: "Práca bez úlohy — dokumentácia (Požiadavka zákazníka)",
  lines: 1_200,
  tokens: 41_000,
  rate: "1.0000",
  full_eur: "41.00",
  amount_eur: "41.00",
  tasks: [],
});

function view(
  over: Partial<DeliveryStatementView["preview"]> = {},
  discount_changes: DeliveryStatementView["discount_changes"] = [],
): DeliveryStatementView {
  return {
    preview: {
      version_number: "1.7.0",
      blocked: null,
      work_kind: "change",
      base_sha: "ec28fb9ab8d3bbbb",
      delivered_sha: "48de080aaaaaaaaa",
      delivered_source: "verifikacia",
      delivered_source_label: "stav, na ktorom prešla Verifikácia",
      tokenizer: "o200k_base (tiktoken 0.14.0)",
      code: { tokens: 170_000, lines: 8_000 },
      tests: { tokens: 7_000, lines: 1_300 },
      docs: { tokens: 41_000, lines: 1_200 },
      files: [],
      rate_code: "2.0000",
      rate_docs: "1.0000",
      settings_rate_code: "2.0000",
      settings_rate_docs: "1.0000",
      rate_code_own: false,
      rate_docs_own: false,
      amount_code_eur: "340.00",
      amount_docs_eur: "41.00",
      amount_eur: "381.00",
      cannot_issue: [],
      calibration: null,
      version_category: "request",
      discounts: { request: "0.00", bug: "100.00", gift: "100.00" },
      settings_discounts: { request: "0.00", bug: "100.00", gift: "100.00" },
      discounts_own: { request: false, bug: false, gift: false },
      lines: [line({}), UNTASKED, GIFT],
      tasks: [
        {
          id: "t1",
          label: "20.1.1",
          title: "Export",
          epic_title: "Exporty",
          own_category: null,
          category: "request",
          lines: 8_900,
          tokens: 170_000,
        },
        {
          id: "t2",
          label: "20.2.1",
          title: "Tlač",
          epic_title: "Exporty",
          own_category: null,
          category: "request",
          lines: 0,
          tokens: 0,
        },
        {
          id: "t3",
          label: "21.1.1",
          title: "Tmavý režim",
          epic_title: "Vzhľad",
          own_category: "gift",
          category: "gift",
          lines: 400,
          tokens: 7_000,
        },
      ],
      full_eur: "395.00",
      ...over,
    },
    issued: [],
    rate_changes: [],
    discount_changes,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getDeliveryStatement).mockResolvedValue(view());
});

describe("Statement lines by category (DEV-75)", () => {
  it("shows every line with its full value, discount and amount, and the total of the amounts", async () => {
    render(<DeliveryStatementPanel versionId="v-170" />);

    const rows = await screen.findAllByTestId("statement-line");
    // The thousands separator of sk-SK is a non-breaking space.
    expect(rows.map((r) => r.textContent?.replace(/\u00a0/g, " "))).toEqual([
      "Požiadavka zákazníka — kód a skúškyúlohy 20.1.18 900170 0002 €340,00 €—340,00 €",
      "Práca bez úlohy — dokumentácia (Požiadavka zákazníka)1 20041 0001 €41,00 €—41,00 €",
      "Dar — pozornosť ICC — kód a skúškyúlohy 21.1.14007 0002 €14,00 €−14,00 €100 % · pozornosť ICC0,00 €",
    ]);
    expect(screen.getByTestId("statement-lines-total")).toHaveTextContent("381,00 €");
    expect(screen.getByTestId("statement-amount")).toHaveTextContent(
      "Suma: 381,00 €plná hodnota 395,00 €, zľava 14,00 €",
    );
    expect(screen.queryByTestId("statement-amount-code")).toBeNull();
  });

  it("without the work kind there are no lines yet — the two kinds stay, without amounts", async () => {
    vi.mocked(getDeliveryStatement).mockResolvedValue(
      view({
        work_kind: null,
        version_category: null,
        lines: [],
        amount_code_eur: null,
        amount_docs_eur: null,
        amount_eur: null,
        full_eur: null,
      }),
    );
    render(<DeliveryStatementPanel versionId="v-156" />);

    expect(await screen.findByTestId("statement-amount-code")).toHaveTextContent("—");
    expect(screen.queryByTestId("statement-lines")).toBeNull();
  });
});

describe("Task categories in the statement (DEV-75)", () => {
  it("counts the tasks by category and opens them by epic", async () => {
    render(<DeliveryStatementPanel versionId="v-170" />);

    const toggle = await screen.findByRole("button", {
      name: /Kategórie úloh: 3 úlohy — požiadavka zákazníka 2, dar 1/,
    });
    await userEvent.click(toggle);

    expect(screen.getByText("20. Exporty")).toBeInTheDocument();
    expect(screen.getByText("21. Vzhľad")).toBeInTheDocument();
    expect(screen.getByLabelText("Kategória úlohy 21.1.1")).toHaveValue("gift");
    expect(screen.getByLabelText("Kategória úlohy 20.1.1")).toHaveDisplayValue("podľa verzie (požiadavka zákazníka)");
    expect(screen.getByText(/v dodávke nič nenapísala/)).toBeInTheDocument();
  });

  it("changes one task, or every task of an epic at once, and counts the statement again", async () => {
    vi.mocked(setTaskCategories).mockResolvedValue({ changed: 1 });
    render(<DeliveryStatementPanel versionId="v-170" />);
    await userEvent.click(await screen.findByRole("button", { name: /Kategórie úloh/ }));

    await userEvent.selectOptions(screen.getByLabelText("Kategória úlohy 20.1.1"), "bug");
    await waitFor(() => expect(setTaskCategories).toHaveBeenCalledWith("v-170", ["t1"], "bug"));
    await waitFor(() => expect(getDeliveryStatement).toHaveBeenCalledTimes(2));

    await userEvent.selectOptions(screen.getByLabelText("Kategória všetkých úloh celku 20"), "gift");
    await waitFor(() => expect(setTaskCategories).toHaveBeenLastCalledWith("v-170", ["t1", "t2"], "gift"));
  });
});

describe("The version's discounts (DEV-75)", () => {
  it("says where each discount comes from and who changed them last", async () => {
    vi.mocked(getDeliveryStatement).mockResolvedValue(
      view(
        {
          discounts: { request: "0.00", bug: "100.00", gift: "50.00" },
          discounts_own: { request: false, bug: false, gift: true },
        },
        [
          {
            created_at: "2026-10-12T10:00:00Z",
            changed_by: "Zoltán Rausch",
            previous: { request: null, bug: null, gift: null },
            current: { request: null, bug: null, gift: "50.00" },
          },
        ],
      ),
    );
    render(<DeliveryStatementPanel versionId="v-170" />);

    const box = await screen.findByTestId("version-discounts");
    expect(box).toHaveTextContent("dar 50 % (vlastná, v Nastaveniach 100 %)");
    expect(box).toHaveTextContent("oprava chyby 100 % (z Nastavení)");
    expect(box).toHaveTextContent(/Naposledy zmenil Zoltán Rausch .*: dar z Nastavení → 50 %\./);
  });

  it("saves the typed discounts — empty means Nastavenia — and refuses one over 100", async () => {
    vi.mocked(setBillingDiscounts).mockResolvedValue([]);
    render(<DeliveryStatementPanel versionId="v-170" />);
    const box = await screen.findByTestId("version-discounts");
    await userEvent.click(within(box).getByRole("button", { name: "Zmeniť zľavy pre túto verziu" }));

    await userEvent.type(within(box).getByLabelText("Dar — pozornosť ICC (%)"), "150");
    await userEvent.click(within(box).getByRole("button", { name: "Uložiť zľavy" }));
    expect(within(box).getByRole("alert")).toHaveTextContent("od 0 do 100");
    expect(setBillingDiscounts).not.toHaveBeenCalled();

    await userEvent.clear(within(box).getByLabelText("Dar — pozornosť ICC (%)"));
    await userEvent.type(within(box).getByLabelText("Dar — pozornosť ICC (%)"), "50,5");
    await userEvent.click(within(box).getByRole("button", { name: "Uložiť zľavy" }));
    await waitFor(() =>
      expect(setBillingDiscounts).toHaveBeenCalledWith("v-170", { request: null, bug: null, gift: "50.5" }),
    );
  });

  it("returns every discount to Nastavenia", async () => {
    vi.mocked(setBillingDiscounts).mockResolvedValue([]);
    vi.mocked(getDeliveryStatement).mockResolvedValue(
      view({ discounts_own: { request: true, bug: false, gift: false } }),
    );
    render(<DeliveryStatementPanel versionId="v-170" />);
    const box = await screen.findByTestId("version-discounts");
    await userEvent.click(within(box).getByRole("button", { name: "Zmeniť zľavy pre túto verziu" }));

    await userEvent.click(within(box).getByRole("button", { name: "Vrátiť zľavy z Nastavení" }));

    await waitFor(() =>
      expect(setBillingDiscounts).toHaveBeenCalledWith("v-170", { request: null, bug: null, gift: null }),
    );
  });
});
