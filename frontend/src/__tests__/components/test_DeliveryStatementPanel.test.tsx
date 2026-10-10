/**
 * DEV-50 — the delivered-token statement in Náklady: what a version delivered, what is left out and why, the work
 * kind, the amount, what the agent's work cost per 1 000 tokens — and „Vydať súpis“ only when it can be issued.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "@testing-library/jest-dom/vitest";

import DeliveryStatementPanel from "@/components/naklady/DeliveryStatementPanel";
import {
  downloadStatementCsv,
  getDeliveryStatement,
  issueDeliveryStatement,
  setWorkKind,
  type DeliveryStatementView,
} from "@/services/api/deliveryStatements";

vi.mock("@/services/api/deliveryStatements", () => ({
  getDeliveryStatement: vi.fn(),
  issueDeliveryStatement: vi.fn(),
  setWorkKind: vi.fn(),
  downloadStatementCsv: vi.fn(),
}));

function view(over: Partial<DeliveryStatementView["preview"]> = {}, issued: DeliveryStatementView["issued"] = []) {
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
      code: { tokens: 23717, lines: 1828 },
      tests: { tokens: 18912, lines: 1410 },
      docs: { tokens: 38795, lines: 1537 },
      files: [
        { path: "backend/app/inbox.py", kind: "kod", excluded: null, lines: 10, tokens: 120 },
        {
          path: "docs/specs/versions/v1.6.0/customer-requirements.md",
          kind: null,
          excluded: "Zadanie — napísal ho zákazník",
          lines: 43,
          tokens: 0,
        },
      ],
      rate_code: "4.0000",
      rate_docs: "1.5000",
      amount_code_eur: "170.52",
      amount_docs_eur: "58.19",
      amount_eur: "228.71",
      cannot_issue: [],
      calibration: { eur_code: 125.62, eur_docs: 63.78, per_1k_code: 2.95, per_1k_docs: 1.64, complete: true },
      ...over,
    },
    issued,
  } as DeliveryStatementView;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("Súpis dodaných tokenov", () => {
  it("ukáže dodaný kód, skúšky a dokumentáciu, sadzby, sumu a čo stála práca agenta", async () => {
    vi.mocked(getDeliveryStatement).mockResolvedValue(view());

    render(<DeliveryStatementPanel versionId="v-160" />);

    expect(await screen.findByTestId("statement-amount")).toHaveTextContent("Suma: 228,71 €");
    expect(screen.getByTestId("statement-amount-code")).toHaveTextContent("170,52 €");
    expect(screen.getByTestId("statement-amount-docs")).toHaveTextContent("58,19 €");
    expect(screen.getByText(/stav, na ktorom prešla Verifikácia/)).toBeInTheDocument();
    expect(screen.getByText(/kód 23\s717, skúšky 18\s912/)).toBeInTheDocument();
    expect(screen.getByText("38 795")).toBeInTheDocument();
    expect(screen.getByText(/meria o200k_base \(tiktoken 0\.14\.0\)/)).toBeInTheDocument();
    expect(screen.getByTestId("statement-calibration")).toHaveTextContent("2,95 € na 1 000 tokenov");
    expect(screen.getByRole("button", { name: "Vydať súpis" })).toBeEnabled();
  });

  it("⚠️ súbory, ktoré sa nerátajú, ukáže aj s dôvodom", async () => {
    vi.mocked(getDeliveryStatement).mockResolvedValue(view());
    render(<DeliveryStatementPanel versionId="v-160" />);

    await userEvent.click(await screen.findByRole("button", { name: /1 rátaných, 1 vynechaných/ }));

    expect(screen.getByTestId("statement-files")).toHaveTextContent("neráta sa — Zadanie — napísal ho zákazník");
  });

  it("⚠️ rýchlu opravu bez určeného druhu vydať nedá a ponúkne ho určiť", async () => {
    vi.mocked(getDeliveryStatement).mockResolvedValue(
      view({
        work_kind: null,
        amount_code_eur: null,
        amount_docs_eur: null,
        amount_eur: null,
        cannot_issue: ["Pri rýchlej oprave treba určiť … kokpit to nehádá."],
      }),
    );
    vi.mocked(setWorkKind).mockResolvedValue({ work_kind: "fix" });
    render(<DeliveryStatementPanel versionId="v-151" />);

    expect(await screen.findByText(/kokpit to nehádá/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Vydať súpis" })).toBeDisabled();
    expect(screen.getByTestId("statement-amount-code")).toHaveTextContent("—");

    await userEvent.click(screen.getByRole("button", { name: "Oprava chyby v dodanom kóde" }));
    await waitFor(() => expect(setWorkKind).toHaveBeenCalledWith("v-151", "fix"));
    expect(getDeliveryStatement).toHaveBeenCalledTimes(2);
  });

  it("oprava chyby v dodanom kóde má sumu 0 € a povie prečo", async () => {
    vi.mocked(getDeliveryStatement).mockResolvedValue(view({ work_kind: "fix", amount_code_eur: "0.00", amount_docs_eur: "0.00", amount_eur: "0.00" }));
    render(<DeliveryStatementPanel versionId="v-151" />);

    expect(await screen.findByTestId("statement-amount")).toHaveTextContent("0,00 €oprava chyby v dodanom kóde — neúčtuje sa");
  });

  it("vydá súpis a vydaný sa dá stiahnuť", async () => {
    const issued = {
      id: "s-1",
      created_at: "2026-10-10T12:30:00Z",
      work_kind: "change" as const,
      base_sha: "a",
      delivered_sha: "b",
      delivered_source: "verifikacia",
      tokenizer: "o200k_base (tiktoken 0.14.0)",
      tokens_code: 23717,
      tokens_tests: 18912,
      tokens_docs: 38795,
      rate_code: "4.0000",
      rate_docs: "1.5000",
      amount_code_eur: "170.52",
      amount_docs_eur: "58.19",
      amount_eur: "228.71",
      replaces_id: null,
      replaced_at: null,
    };
    vi.mocked(getDeliveryStatement).mockResolvedValueOnce(view()).mockResolvedValue(view({}, [issued]));
    vi.mocked(issueDeliveryStatement).mockResolvedValue(issued);
    render(<DeliveryStatementPanel versionId="v-160" />);

    await userEvent.click(await screen.findByRole("button", { name: "Vydať súpis" }));
    await waitFor(() => expect(issueDeliveryStatement).toHaveBeenCalledWith("v-160", undefined));

    await userEvent.click(await screen.findByRole("button", { name: /Stiahnuť CSV/ }));
    expect(downloadStatementCsv).toHaveBeenCalledWith("s-1", "supis-1.6.0.csv");
    expect(screen.getByTestId("issued-statement")).toHaveTextContent("platný");
    expect(screen.getByTestId("issued-statement")).toHaveTextContent("81 424 tokenov · 228,71 €");
  });

  it("verzia, ktorá ešte nie je hotová, povie len prečo súpis nie je", async () => {
    vi.mocked(getDeliveryStatement).mockResolvedValue(
      view({ blocked: "Verzia ešte nie je hotová — súpis sa robí z dodanej verzie.", code: null }),
    );
    render(<DeliveryStatementPanel versionId="v-170" />);

    expect(await screen.findByText(/Verzia ešte nie je hotová/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Vydať súpis" })).not.toBeInTheDocument();
  });

  describe("jedna verzia — jeden platný súpis (DEV-54)", () => {
    const valid = {
      id: "s-2",
      created_at: "2026-10-10T13:47:36Z",
      work_kind: "change" as const,
      base_sha: "a",
      delivered_sha: "b",
      delivered_source: "verifikacia",
      tokenizer: "o200k_base (tiktoken 0.14.0)",
      tokens_code: 94321,
      tokens_tests: 79569,
      tokens_docs: 76087,
      rate_code: "1.0000",
      rate_docs: "1.0000",
      amount_code_eur: "173.89",
      amount_docs_eur: "76.09",
      amount_eur: "249.98",
      replaces_id: "s-1",
      replaced_at: null,
    };
    const replaced = {
      ...valid,
      id: "s-1",
      created_at: "2026-10-10T13:36:08Z",
      rate_code: "2.0000",
      amount_code_eur: "347.78",
      amount_eur: "423.87",
      replaces_id: null,
      replaced_at: "2026-10-10T13:47:36Z",
    };

    it("⚠️ nový súpis sa vydá až po potvrdení, že nahradí ten platný", async () => {
      vi.mocked(getDeliveryStatement).mockResolvedValue(view({}, [valid, replaced]));
      vi.mocked(issueDeliveryStatement).mockResolvedValue(valid);
      const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
      render(<DeliveryStatementPanel versionId="v-170" />);

      const button = await screen.findByRole("button", { name: "Vydať nový súpis" });
      await userEvent.click(button);
      expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/^Nahradiť súpis z .* za 249,98 €\?/));
      expect(issueDeliveryStatement).not.toHaveBeenCalled();

      await userEvent.click(button);
      await waitFor(() => expect(issueDeliveryStatement).toHaveBeenCalledWith("v-170", "s-2"));
      confirm.mockRestore();
    });

    it("platný súpis je označený, nahradený zašednutý s dátumom nahradenia", async () => {
      vi.mocked(getDeliveryStatement).mockResolvedValue(view({}, [valid, replaced]));
      render(<DeliveryStatementPanel versionId="v-170" />);

      const items = await screen.findAllByTestId("issued-statement");
      expect(items).toHaveLength(2);
      const [first, second] = items as [HTMLElement, HTMLElement];
      expect(first).toHaveTextContent(/^platný.*249,98 €/);
      expect(second).toHaveTextContent(/^nahradený .* · .*423,87 €/);
      expect(second).not.toHaveTextContent("platný");
      expect(second.className).toContain("color-text-muted");
    });
  });
});
