/**
 * DEV-134 — „Priradiť k verzii“ offers only the versions that have not been built yet, each with its Slovak status.
 *
 * Director 16.10.2026, assigning the NEX Inbox 1.9.0 requirements: the list offered 1.8.0 … 1.5.0 — all finished —
 * and called each one „(Neznámy stav)“. A finished version never builds again, so a requirement assigned to it would
 * be stranded; and the copy of the version status type did not know the status „done“ at all.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";

import type { BacklogItemRead } from "@/types/backlog";
import type { Version } from "@/types/version";

const { list, versions } = vi.hoisted(() => ({ list: vi.fn(), versions: vi.fn() }));

vi.mock("@/services/api/backlog", () => ({
  listBacklogApi: list,
  updateBacklogApi: vi.fn(),
  getBacklogTicketDraftApi: vi.fn(),
  createBacklogTicketApi: vi.fn(),
  createBacklogApi: vi.fn(),
  deleteBacklogApi: vi.fn(),
}));
vi.mock("@/services/api/projects", () => ({
  listProjectsApi: vi.fn().mockResolvedValue({ items: [{ id: "p1", slug: "nex-inbox", name: "NEX Inbox" }] }),
}));
vi.mock("@/services/api/versions", () => ({ listVersions: versions }));
vi.mock("react-router-dom", async () => ({
  ...(await vi.importActual<typeof import("react-router-dom")>("react-router-dom")),
  useParams: () => ({ slug: "nex-inbox" }),
}));

import BacklogPage from "@/pages/BacklogPage";

const REQUIREMENT: BacklogItemRead = {
  id: "r5",
  project_id: "p1",
  number: 5,
  title: "Výpadok schránky sa ohlási",
  description: null,
  priority: "medium",
  status: "open",
  version_id: null,
  realized_at: null,
  rejection_reason: null,
  plane_identifier: "DEV-131",
  plane_url: null,
  created_at: "2026-10-16T17:00:00Z",
  updated_at: "2026-10-16T17:00:00Z",
};

function version(number: string, status: Version["status"]): Version {
  return { id: `v-${number}`, version_number: number, status } as Version;
}

beforeEach(() => {
  list.mockReset().mockResolvedValue({ items: [REQUIREMENT], total: 1 });
  versions
    .mockReset()
    .mockResolvedValue([
      version("1.9.0", "planned"),
      version("1.8.1", "active"),
      version("1.8.0", "done"),
      version("1.5.0", "released"),
    ]);
});

describe("BacklogPage — Priradiť k verzii (DEV-134)", () => {
  it("offers only versions not built yet, each with its status in Slovak", async () => {
    render(<BacklogPage />);
    await screen.findByText(REQUIREMENT.title);
    fireEvent.click(screen.getByRole("button", { name: "Priradiť k verzii" }));

    const vyber = await screen.findByRole("combobox");
    const moznosti = within(vyber)
      .getAllByRole("option")
      .map((o) => o.textContent);
    expect(moznosti).toEqual(["— vyber verziu —", "1.9.0 (Plánované)", "1.8.1 (Prebieha)"]);
    expect(vyber.textContent).not.toMatch(/Neznámy stav/);
  });
});
