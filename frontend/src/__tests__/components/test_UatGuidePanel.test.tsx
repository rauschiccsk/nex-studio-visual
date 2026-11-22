/**
 * DEV-90 — the UAT tab says what to try in a version: the agent's test guide, or an older version's release-note
 * changes, with each customer's UAT link and the login of its first admin.
 * DEV-91 — each change is ticked off as tried, or marked as not working, which prepares a fast fix.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render as rtlRender, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { ReactElement } from "react";
import "@testing-library/jest-dom/vitest";

import UatGuidePanel from "@/components/deploy/UatGuidePanel";
import { getUatChecks, getUatGuide, setUatCheck } from "@/services/api/deploy";
import type { DeployMatrixRow, UatGuide, UatProgress } from "@/types/deploy";

vi.mock("@/services/api/deploy", () => ({ getUatGuide: vi.fn(), getUatChecks: vi.fn(), setUatCheck: vi.fn() }));
vi.mock("@/services/api/planeTickets", () => ({ filePreparedTicketApi: vi.fn() }));

function render(ui: ReactElement) {
  return rtlRender(
    <MemoryRouter initialEntries={["/uat"]}>
      <Routes>
        <Route path="/uat" element={ui} />
      </Routes>
    </MemoryRouter>,
  );
}

const PROGRESS: UatProgress = {
  version_number: "1.8.0",
  items: ["Detail faktúry v Chrome 109"],
  checks: [],
  tested: 0,
  total: 1,
  untested: ["Detail faktúry v Chrome 109"],
  broken: [],
};

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

beforeEach(() => {
  vi.mocked(getUatGuide).mockReset();
  vi.mocked(getUatChecks).mockReset().mockResolvedValue(PROGRESS);
  vi.mocked(setUatCheck).mockReset();
  window.localStorage.clear();
});

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
    expect(screen.queryByText("Bez UAT:")).toBeNull(); // no UAT link line for a customer without UAT
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

  // ── DEV-340 ─────────────────────────────────────────────────────────────────────────────────────────────────

  it("shows the full address of the customer being tried in place of the guide's placeholder, as a link", async () => {
    const tataUrl = "https://uat-tata-author-asistent.int.isnex.eu";
    vi.mocked(getUatGuide).mockResolvedValue({
      ...GUIDE,
      sections: [
        {
          title: "Stav aplikácie",
          screen: "prehliadač, adresa inštalácie (napríklad `https://<adresa-inštalácie>/api/v1/health`)",
          data: null,
          steps: ["Otvor https://<adresa-inštalácie>/api/v1/health."],
          expected: ["ukáže sa verzia 1.0.1"],
        },
      ],
      addresses: [
        { customer_id: "tata", placeholder: "<adresa-inštalácie>", url: tataUrl },
        { customer_id: "new", placeholder: "<adresa-inštalácie>", url: "https://uat-new-author-asistent.int.isnex.eu" },
      ],
    });
    const rows = [
      { customer_id: "tata", customer_name: "TATA", uat_url: tataUrl, uat_version: "1.0.1" },
      { customer_id: "new", customer_name: "Nový zákazník", uat_url: null, uat_version: null },
    ] as unknown as DeployMatrixRow[];
    render(<UatGuidePanel slug="author-asistent" versions={["1.0.1"]} rows={rows} />);

    const card = (await screen.findByRole("heading", { name: "Stav aplikácie" })).closest("li") as HTMLElement;
    const links = within(card).getAllByRole("link", { name: `${tataUrl}/api/v1/health` });
    expect(links.map((a) => a.getAttribute("href"))).toEqual([`${tataUrl}/api/v1/health`, `${tataUrl}/api/v1/health`]);
    expect(within(card).getByText(/Obrazovka:/).textContent).toBe(
      `Obrazovka: prehliadač, adresa inštalácie (napríklad ${tataUrl}/api/v1/health)`,
    );
    expect(card.textContent).not.toContain("<adresa-inštalácie>");

    // a customer without a UAT installation has no address to link — the sentence stays as the guide wrote it
    fireEvent.change(screen.getByLabelText("Zákazník, u ktorého skúšaš"), { target: { value: "new" } });
    await waitFor(() => expect(within(card).queryAllByRole("link")).toEqual([]));
    expect(card.textContent).toContain("Otvor https://<adresa-inštalácie>/api/v1/health.");
  });

  // ── DEV-91 ──────────────────────────────────────────────────────────────────────────────────────────────────

  it("ticks a change off as tried for the customer that runs the version, and counts it", async () => {
    vi.mocked(getUatGuide).mockResolvedValue(GUIDE);
    vi.mocked(setUatCheck).mockResolvedValue({
      ...PROGRESS,
      checks: [{ item: "Detail faktúry v Chrome 109", status: "tested", note: null, checked_by: "Z", updated_at: "x" }],
      tested: 1,
      untested: [],
    });
    const rows = [{ ...ROWS[1], uat_version: "1.7.1" }, { ...ROWS[0], uat_version: "1.8.0" }] as DeployMatrixRow[];
    render(<UatGuidePanel slug="nex-inbox" versions={["1.8.0"]} rows={rows} />);
    expect(await screen.findByText("Otestované 0 z 1")).toBeInTheDocument();
    expect(getUatChecks).toHaveBeenCalledWith("c1", "1.8.0");

    fireEvent.click(screen.getByRole("checkbox", { name: "Vyskúšané" }));

    await waitFor(() =>
      expect(setUatCheck).toHaveBeenCalledWith("c1", {
        version_number: "1.8.0",
        item: "Detail faktúry v Chrome 109",
        status: "tested",
        note: null,
      }),
    );
    expect(await screen.findByText("Otestované 1 z 1")).toBeInTheDocument();
    expect(screen.getByLabelText("Zákazník, u ktorého skúšaš")).toHaveValue("c1");
  });

  it("turns a change that does not work into a ticket he files himself — nothing starts (DEV-492)", async () => {
    vi.mocked(getUatGuide).mockResolvedValue(GUIDE);
    vi.mocked(setUatCheck).mockResolvedValue({ ...PROGRESS, broken: ["Detail faktúry v Chrome 109"] });
    render(<UatGuidePanel slug="nex-inbox" versions={["1.8.0"]} rows={ROWS} />);
    fireEvent.click(await screen.findByRole("button", { name: "Nefunguje" }));
    fireEvent.change(screen.getByLabelText("Čo sa stalo?"), { target: { value: "Biela stránka." } });

    fireEvent.click(screen.getByRole("button", { name: "Pripraviť tiket" }));

    expect(await screen.findByRole("dialog", { name: "Založiť tiket" })).toBeInTheDocument();
    expect(setUatCheck).toHaveBeenCalledWith("c1", {
      version_number: "1.8.0",
      item: "Detail faktúry v Chrome 109",
      status: "broken",
      note: "Biela stránka.",
    });
    expect(screen.getByLabelText("Názov tiketu")).toHaveValue("Nefunguje v UAT: Detail faktúry v Chrome 109");
    const text = (screen.getByLabelText("Text tiketu") as HTMLTextAreaElement).value;
    expect(text).toContain("V UAT u zákazníka MÁGERSTAV vo verzii 1.8.0 nefunguje: Detail faktúry v Chrome 109.");
    expect(text).toContain("Čo sa stalo: Biela stránka.");
  });

  it("shows a change that does not work with what happened, and takes the record back", async () => {
    vi.mocked(getUatGuide).mockResolvedValue(GUIDE);
    vi.mocked(getUatChecks).mockResolvedValue({
      ...PROGRESS,
      checks: [{ item: "Detail faktúry v Chrome 109", status: "broken", note: "Biela stránka.", checked_by: "Z", updated_at: "x" }],
      untested: [],
      broken: ["Detail faktúry v Chrome 109"],
    });
    vi.mocked(setUatCheck).mockResolvedValue(PROGRESS);
    render(<UatGuidePanel slug="nex-inbox" versions={["1.8.0"]} rows={ROWS} />);

    expect(await screen.findByText(/Nefunguje: Biela stránka\./)).toBeInTheDocument();
    expect(screen.getByText(/nefunguje 1/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Zrušiť záznam" }));

    await waitFor(() =>
      expect(setUatCheck).toHaveBeenCalledWith("c1", {
        version_number: "1.8.0",
        item: "Detail faktúry v Chrome 109",
        status: null,
        note: null,
      }),
    );
  });
});
