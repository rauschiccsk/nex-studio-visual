/**
 * DEV-247 — the UAT guide shows every version the customer's UAT carries that the customer has not accepted.
 *
 * 28.10.2026 NEX Manager for MÁGERSTAV: PROD on 1.2.2, UAT on 1.4.1 — a fast fix carrying the never-accepted 1.3.0 and
 * 1.4.0 (groups). The guide showed 1.4.1 alone: „Nie je čo skúšať“. Director: „Áno, súhlasím, založ tiket a oprav to“.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render as rtlRender, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import "@testing-library/jest-dom/vitest";

import UatGuidePanel from "@/components/deploy/UatGuidePanel";
import { combineProgress } from "@/lib/uatTesting";
import { getUatChecks, getUatGuide, setUatCheck } from "@/services/api/deploy";
import type { DeployMatrixRow, UatGuide, UatProgress } from "@/types/deploy";

vi.mock("@/services/api/deploy", () => ({ getUatGuide: vi.fn(), getUatChecks: vi.fn(), setUatCheck: vi.fn() }));

const ROWS = [
  {
    customer_id: "mager",
    customer_name: "MÁGERSTAV",
    uat_url: "https://uat-mager-manager.isnex.eu",
    uat_version: "1.4.1",
    uat_versions_to_test: ["1.3.0", "1.4.0", "1.4.1"],
  },
] as unknown as DeployMatrixRow[];

function guide(version: string, titles: string[], nothing: string | null = null): UatGuide {
  return {
    version_number: version,
    source: "guide",
    sections: titles.map((title) => ({ title, screen: null, data: null, steps: ["Skús."], expected: ["Funguje."] })),
    nothing_to_test: nothing,
    changes: [],
    logins: [{ customer_id: "mager", login: "admin@magerstav.sk" }],
  };
}

function progress(version: string, items: string[], tested: string[] = []): UatProgress {
  return {
    version_number: version,
    items,
    checks: tested.map((item) => ({ item, status: "tested", note: null, checked_by: "Zoltán", updated_at: "2026-10-28T12:00:00Z" })),
    tested: tested.length,
    total: items.length,
    untested: items.filter((i) => !tested.includes(i)),
    broken: [],
  } as unknown as UatProgress;
}

const GUIDES: Record<string, UatGuide> = {
  "1.3.0": guide("1.3.0", ["Ikona aplikácie"]),
  "1.4.0": guide("1.4.0", ["Založenie skupiny s vlastníkom", "Pridelenie aplikácie skupine"]),
  "1.4.1": guide("1.4.1", [], "na obrazovkách NEX Managera sa nič nemení"),
};
const PROGRESS: Record<string, UatProgress> = {
  "1.3.0": progress("1.3.0", ["Ikona aplikácie"], ["Ikona aplikácie"]),
  "1.4.0": progress("1.4.0", ["Založenie skupiny s vlastníkom", "Pridelenie aplikácie skupine"]),
  "1.4.1": progress("1.4.1", []),
};

function render(rows: DeployMatrixRow[] = ROWS, versions = ["1.4.1", "1.2.2"]) {
  return rtlRender(
    <MemoryRouter>
      <UatGuidePanel slug="nex-manager" versions={versions} rows={rows} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.mocked(getUatGuide).mockReset().mockImplementation((_slug, version) => Promise.resolve(GUIDES[version] as UatGuide));
  vi.mocked(getUatChecks).mockReset().mockImplementation((_c, version) => Promise.resolve(PROGRESS[version] as UatProgress));
  vi.mocked(setUatCheck)
    .mockReset()
    .mockImplementation((_c, body) => Promise.resolve(PROGRESS[body.version_number] as UatProgress));
  window.localStorage.clear();
});

describe("UatGuidePanel — every version not accepted (DEV-247)", () => {
  it("shows the walkthrough of each version under its number, oldest first", async () => {
    render();

    const headings = await screen.findAllByRole("heading", { level: 3, name: /^Verzia / });
    expect(headings.map((h) => h.textContent)).toEqual(["Verzia 1.3.0", "Verzia 1.4.0", "Verzia 1.4.1"]);
    expect(await screen.findByText("Založenie skupiny s vlastníkom")).toBeInTheDocument();
    expect(screen.getByText("Ikona aplikácie")).toBeInTheDocument();
    expect(screen.getByText(/Nie je čo skúšať: na obrazovkách NEX Managera sa nič nemení/)).toBeInTheDocument();
  });

  it("says why the older versions are there", async () => {
    render();

    expect(
      await screen.findByText(
        "Na UAT je verzia 1.4.1 a nesie aj verzie 1.3.0, 1.4.0, ktoré MÁGERSTAV ešte neakceptoval — vyskúšaj všetky.",
      ),
    ).toBeInTheDocument();
  });

  it("counts what was tried across all of them", async () => {
    render();

    expect(await screen.findByText("Otestované 1 z 3")).toBeInTheDocument();
  });

  it("records a try with the version the change belongs to", async () => {
    render();

    const block = (await screen.findByText("Pridelenie aplikácie skupine")).closest("li") as HTMLElement;
    fireEvent.click(within(block).getByRole("checkbox"));

    await waitFor(() =>
      expect(setUatCheck).toHaveBeenCalledWith("mager", {
        version_number: "1.4.0",
        item: "Pridelenie aplikácie skupine",
        status: "tested",
        note: null,
      }),
    );
  });

  it("asks the server about each of the versions", async () => {
    render();

    await screen.findByText("Založenie skupiny s vlastníkom");
    expect(vi.mocked(getUatGuide).mock.calls.map((c) => c[1]).sort()).toEqual(["1.3.0", "1.4.0", "1.4.1"]);
    expect(vi.mocked(getUatChecks).mock.calls.map((c) => c[1]).sort()).toEqual(["1.3.0", "1.4.0", "1.4.1"]);
  });

  it("a version picked by hand, or one customer's own version, looks as it always did", async () => {
    const one = [{ ...ROWS[0], uat_versions_to_test: ["1.4.1"] }] as unknown as DeployMatrixRow[];
    render(one);

    expect(await screen.findByText(/Nie je čo skúšať/)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 3, name: /^Verzia / })).not.toBeInTheDocument();
    expect(screen.queryByText(/nesie aj verzie/)).not.toBeInTheDocument();
  });
});

describe("combineProgress (DEV-247)", () => {
  it("adds the versions up for the acceptance question, naming each change with its version", () => {
    const all = combineProgress([PROGRESS["1.3.0"] as UatProgress, PROGRESS["1.4.0"] as UatProgress]);

    expect(all.tested).toBe(1);
    expect(all.total).toBe(3);
    expect(all.untested).toEqual(["1.4.0: Založenie skupiny s vlastníkom", "1.4.0: Pridelenie aplikácie skupine"]);
  });

  it("one version reads exactly as before", () => {
    expect(combineProgress([PROGRESS["1.4.0"] as UatProgress])).toEqual(PROGRESS["1.4.0"]);
  });
});
