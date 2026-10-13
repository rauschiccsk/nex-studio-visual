/**
 * DEV-98 — the backlog's bin deletes an open and a rejected request, and only after the manager confirms it.
 *
 * 13.10.2026 the Director wanted to delete a test request: the red reject button stands right beside the small bin, the
 * cockpit received a rejection, and on a rejected request the bin was gone. A request already in a version is history
 * and keeps no bin. The confirmation is the one the notes page asks before deleting for good.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { BacklogItemRead } from "@/types/backlog";

const { list, remove } = vi.hoisted(() => ({ list: vi.fn(), remove: vi.fn() }));

vi.mock("@/services/api/backlog", () => ({
  listBacklogApi: list,
  deleteBacklogApi: remove,
  createBacklogApi: vi.fn(),
  updateBacklogApi: vi.fn(),
}));
vi.mock("@/services/api/projects", () => ({
  listProjectsApi: vi.fn().mockResolvedValue({ items: [{ id: "p1", slug: "nex-inbox", name: "NEX Inbox" }] }),
}));
vi.mock("@/services/api/versions", () => ({ listVersions: vi.fn().mockResolvedValue([]) }));
vi.mock("react-router-dom", async () => ({
  ...(await vi.importActual<typeof import("react-router-dom")>("react-router-dom")),
  useParams: () => ({ slug: "nex-inbox" }),
}));

import BacklogPage from "@/pages/BacklogPage";

function item(number: number, status: BacklogItemRead["status"], title: string): BacklogItemRead {
  return {
    id: `r${number}`,
    project_id: "p1",
    number,
    title,
    description: null,
    priority: "medium",
    status,
    version_id: status === "included" ? "v1" : null,
    realized_at: null,
    created_at: "2026-10-13T08:07:03Z",
    updated_at: "2026-10-13T08:07:18Z",
  };
}

const ITEMS = [
  item(1, "open", "Spracovanie dobropisov"),
  item(2, "rejected", "Skúšobné poznámky"),
  item(3, "included", "Export do CSV"),
];

/** The request a button belongs to — the nearest ancestor naming exactly one REQ. */
function requestOf(el: HTMLElement): string {
  let node: HTMLElement | null = el;
  while (node && (node.textContent ?? "").match(/REQ-\d+/g)?.length !== 1) node = node.parentElement;
  return node?.textContent?.match(/REQ-\d+/)?.[0] ?? "";
}

async function renderPage() {
  render(<BacklogPage />);
  await screen.findByText("Skúšobné poznámky");
}

describe("BacklogPage bin (DEV-98)", () => {
  beforeEach(() => {
    list.mockReset().mockResolvedValue({ items: ITEMS, total: 3 });
    remove.mockReset().mockResolvedValue(undefined);
  });
  afterEach(() => vi.restoreAllMocks());

  it("offers the bin on an open and on a rejected request, never on one already in a version", async () => {
    await renderPage();

    expect(screen.getAllByTitle("Zmazať").map(requestOf)).toEqual(["REQ-1", "REQ-2"]);
  });

  it("deletes only after the manager confirms, naming the request", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    await renderPage();
    const bin = screen.getAllByTitle("Zmazať").find((b) => requestOf(b) === "REQ-2")!;

    fireEvent.click(bin);
    expect(confirm).toHaveBeenCalledWith("Natrvalo zmazať požiadavku REQ-2 „Skúšobné poznámky“? Vrátiť sa to nedá.");
    expect(remove).not.toHaveBeenCalled();

    confirm.mockReturnValue(true);
    fireEvent.click(bin);
    await waitFor(() => expect(remove).toHaveBeenCalledWith("r2"));
  });
});
