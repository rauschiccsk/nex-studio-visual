/**
 * DEV-90 — the UAT tab says what to try in a version: the agent's test guide, or an older version's release-note
 * changes, with each customer's UAT link and the login of its first admin.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import UatGuidePanel from "@/components/deploy/UatGuidePanel";
import { getUatGuide } from "@/services/api/deploy";
import type { DeployMatrixRow, UatGuide } from "@/types/deploy";

vi.mock("@/services/api/deploy", () => ({ getUatGuide: vi.fn() }));

const ROWS = [
  { customer_id: "c1", customer_name: "MÁGERSTAV", uat_url: "https://uat-magerstav-inbox.isnex.eu" },
  { customer_id: "c2", customer_name: "Bez UAT", uat_url: null },
] as unknown as DeployMatrixRow[];

const GUIDE: UatGuide = {
  version_number: "1.8.0",
  source: "guide",
  sections: [
    {
      title: "Detail faktúry v Chrome 109",
      screen: "Faktúry → detail",
      data: "faktúra EMOS",
      steps: ["Otvor zoznam faktúr.", "Klikni na faktúru EMOS."],
      expected: ["Detail sa otvorí aj s PDF."],
    },
  ],
  nothing_to_test: null,
  changes: [],
  logins: [
    { customer_id: "c1", login: "z@magerstav.sk" },
    { customer_id: "c2", login: null },
  ],
};

beforeEach(() => vi.mocked(getUatGuide).mockReset());

describe("UatGuidePanel (DEV-90)", () => {
  it("shows the guide of the newest version step by step, with the UAT link and the login", async () => {
    vi.mocked(getUatGuide).mockResolvedValue(GUIDE);
    render(<UatGuidePanel slug="nex-inbox" versions={["1.8.0", "1.7.1"]} rows={ROWS} />);

    const change = await screen.findByRole("heading", { name: "Detail faktúry v Chrome 109" });
    const card = change.closest("li") as HTMLElement;
    expect(within(card).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Otvor zoznam faktúr.",
      "Klikni na faktúru EMOS.",
    ]);
    expect(within(card).getByText("Očakávané: Detail sa otvorí aj s PDF.")).toBeInTheDocument();
    expect(within(card).getByText(/Obrazovka: Faktúry → detail/)).toBeInTheDocument();
    expect(getUatGuide).toHaveBeenCalledWith("nex-inbox", "1.8.0");
    expect(screen.getByRole("link", { name: "UAT" })).toHaveAttribute("href", "https://uat-magerstav-inbox.isnex.eu");
    expect(screen.getByText("prihlásenie: z@magerstav.sk")).toBeInTheDocument();
    expect(screen.queryByText(/Bez UAT/)).toBeNull();
  });

  it("asks for the version picked", async () => {
    vi.mocked(getUatGuide).mockResolvedValue(GUIDE);
    render(<UatGuidePanel slug="nex-inbox" versions={["1.8.0", "1.7.1"]} rows={ROWS} />);
    await screen.findByRole("heading", { name: "Detail faktúry v Chrome 109" });

    fireEvent.change(screen.getByLabelText("Verzia, ktorú skúšaš"), { target: { value: "1.7.1" } });

    await waitFor(() => expect(getUatGuide).toHaveBeenLastCalledWith("nex-inbox", "1.7.1"));
  });

  it("shows an older version's release-note changes and says the guide did not exist", async () => {
    vi.mocked(getUatGuide).mockResolvedValue({
      ...GUIDE,
      source: "release_notes",
      sections: [],
      changes: ["Export do CSV pri zozname faktúr.", "Rýchlejšie hľadanie."],
    });
    render(<UatGuidePanel slug="nex-inbox" versions={["1.7.0"]} rows={ROWS} />);

    expect(await screen.findByText(/Návod na skúšku pre túto verziu nevznikol/)).toBeInTheDocument();
    expect(screen.getByText("Export do CSV pri zozname faktúr.")).toBeInTheDocument();
    expect(screen.getByText("Rýchlejšie hľadanie.")).toBeInTheDocument();
  });

  it("says when there is nothing to try, and when there is nothing to show", async () => {
    vi.mocked(getUatGuide).mockResolvedValue({ ...GUIDE, sections: [], nothing_to_test: "oprava len v skúškach CI." });
    const { unmount } = render(<UatGuidePanel slug="nex-inbox" versions={["1.7.2"]} rows={ROWS} />);
    expect(await screen.findByText("Nie je čo skúšať: oprava len v skúškach CI.")).toBeInTheDocument();
    unmount();

    vi.mocked(getUatGuide).mockResolvedValue({ ...GUIDE, source: "none", sections: [] });
    render(<UatGuidePanel slug="nex-inbox" versions={["0.1.0"]} rows={ROWS} />);
    expect(await screen.findByText(/nemá návod na skúšku ani poznámky k vydaniu/)).toBeInTheDocument();
  });

  it("is not there when no version can go to UAT", () => {
    const { container } = render(<UatGuidePanel slug="nex-inbox" versions={[]} rows={ROWS} />);

    expect(container).toBeEmptyDOMElement();
    expect(getUatGuide).not.toHaveBeenCalled();
  });
});
