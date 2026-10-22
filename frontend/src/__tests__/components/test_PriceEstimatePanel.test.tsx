/**
 * DEV-154 — the orientačná cena of a version: after Návrh, from the tasks of its plan. It says what it rests on, how it
 * is made up, when it rests on too few finished versions, and that it is no invoice; before the plan it says nothing.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import PriceEstimatePanel from "@/components/version/PriceEstimatePanel";
import {
  amountWords,
  fromVersionsWords,
  onVersionsWords,
  tasksWords,
} from "@/lib/priceEstimate";
import * as api from "@/services/api/priceEstimate";
import { formatEur } from "@/utils/format";

vi.mock("@/services/api/priceEstimate", () => ({
  getPriceEstimateApi: vi.fn(),
}));

// The screen collapses the no-break space of sk-SK grouping; formatEur itself is checked with it below.
const NBSP = "\u00a0";
const SP = " ";
const ESTIMATE: api.PriceEstimate = {
  tasks: 42,
  first_version: false,
  code_tokens: 210_000,
  docs_tokens: 30_000,
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

afterEach(() => vi.clearAllMocks());

describe("Orientačná cena on the version's page", () => {
  it("shows the amount with its spread, what it rests on and how it is made up", async () => {
    vi.mocked(api.getPriceEstimateApi).mockResolvedValue(ESTIMATE);
    render(<PriceEstimatePanel versionId="v1" />);

    expect(
      await screen.findByText(`cca 1${SP}450,00 € ± 26 %`),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Odhad podľa 42 úloh v pláne verzie, z 3 hotových verzií — ďalších verzií projektov.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        `≈ 210${SP}000 tokenov × 2,00 € za 1${SP}000 = 420,00 €`,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(`≈ 30${SP}000 tokenov × 1,00 € za 1${SP}000 = 30,00 €`),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/^Opiera sa len o 3 hotové verzie, preto je hrubý/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Nie je to faktúra: účtuje sa podľa súpisu dodávky, keď je verzia dodaná.",
      ),
    ).toBeInTheDocument();
    expect(api.getPriceEstimateApi).toHaveBeenCalledWith("v1");
  });

  it("resting on enough versions it does not call itself rough", async () => {
    vi.mocked(api.getPriceEstimateApi).mockResolvedValue({
      ...ESTIMATE,
      samples: 12,
      enough: true,
    });
    render(<PriceEstimatePanel versionId="v1" />);

    expect(await screen.findByText(/z 12 hotových verzií/)).toBeInTheDocument();
    expect(screen.queryByText(/Opiera sa len o/)).not.toBeInTheDocument();
  });

  it("the first version of a new project says it is measured on first versions, without a spread from one", async () => {
    vi.mocked(api.getPriceEstimateApi).mockResolvedValue({
      ...ESTIMATE,
      first_version: true,
      samples: 1,
      spread_pct: null,
    });
    render(<PriceEstimatePanel versionId="v1" />);

    expect(await screen.findByText(`cca 1${SP}450,00 €`)).toBeInTheDocument();
    expect(
      screen.getByText(
        "Odhad podľa 42 úloh v pláne verzie, z 1 hotovej verzie — prvých verzií projektov, ktoré stavajú kostru aplikácie.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/^Opiera sa len o 1 hotovú verziu,/),
    ).toBeInTheDocument();
  });

  it("before the plan, or for a fix of our error, there is no panel at all", async () => {
    vi.mocked(api.getPriceEstimateApi).mockResolvedValue(null);
    const { container } = render(<PriceEstimatePanel versionId="v1" />);

    await waitFor(() =>
      expect(
        screen.queryByText("Počítam orientačnú cenu…"),
      ).not.toBeInTheDocument(),
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("a failed load says so instead of showing nothing", async () => {
    vi.mocked(api.getPriceEstimateApi).mockRejectedValue(new Error("boom"));
    render(<PriceEstimatePanel versionId="v1" />);

    expect(
      await screen.findByText(/Orientačnú cenu sa nepodarilo načítať/),
    ).toBeInTheDocument();
  });

  it("the words follow the number", () => {
    expect([tasksWords(1), tasksWords(2), tasksWords(5)]).toEqual([
      "1 úlohy",
      "2 úloh",
      "5 úloh",
    ]);
    expect([fromVersionsWords(1), fromVersionsWords(4)]).toEqual([
      "1 hotovej verzie",
      "4 hotových verzií",
    ]);
    expect([1, 2, 4, 5].map(onVersionsWords)).toEqual([
      "1 hotovú verziu",
      "2 hotové verzie",
      "4 hotové verzie",
      "5 hotových verzií",
    ]);
    expect(amountWords({ amount_eur: "93.03", spread_pct: 0 })).toBe(
      "cca 93,03 € ± 0 %",
    );
    expect(formatEur("1234.5")).toBe(`1${NBSP}234,50 €`);
    expect(formatEur(null)).toBe("—");
  });
});
