/**
 * DEV-94 — the Knowledge Base page says when a change it made did not reach GitHub.
 *
 * The copy of the Knowledge Base on MAGER reads GitHub. Creating, saving or deleting a document wrote only the file
 * on ANDROS, so the change never got there and nobody knew. Now the server commits the document and sends it; when
 * it cannot, its answer carries the reason and the page shows it — for every one of the three writes.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "@testing-library/jest-dom/vitest";

const { apiMock } = vi.hoisted(() => ({
  apiMock: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));

vi.mock("@/services/api", () => ({ api: apiMock }));
vi.mock("@/services/api/rag", () => ({ getKbIndexStatus: vi.fn(() => Promise.reject(new Error("off"))) }));
vi.mock("@/store/authStore", () => ({
  useAuthStore: (sel: (s: unknown) => unknown) => sel({ user: { id: "u1", username: "rausch", role: "ri" } }),
}));
vi.mock("@/store/sessionStore", () => ({
  useSessionStore: { getState: () => ({ knowledgeDocPath: "icc/POSTUP.md", setKnowledgeDocPath: vi.fn() }) },
}));

import KnowledgeBasePage from "@/pages/KnowledgeBasePage";

const REASON =
  "Odoslanie Znalostnej bázy na GitHub zlyhalo: Could not resolve host. Kokpit to skúsi odoslať znova sám.";
const DOC = { relative_path: "icc/POSTUP.md", filename: "POSTUP.md", category: "icc", size_bytes: 10 };

beforeEach(() => {
  window.localStorage.clear();
  Object.values(apiMock).forEach((f) => f.mockReset());
  apiMock.get.mockImplementation(async (url: string) => {
    if (url.includes("categories")) return { categories: ["icc"] };
    if (url.includes("content")) return { relative_path: DOC.relative_path, content: "# Postup\n" };
    return { documents: [DOC], count: 1 };
  });
});

async function create() {
  render(<KnowledgeBasePage />);
  await userEvent.click(await screen.findByRole("button", { name: /Nový/ }));
  await userEvent.type(screen.getByPlaceholderText("Popisný názov..."), "Nový postup");
  await userEvent.type(screen.getByPlaceholderText(/Názov dokumentu/), "# Nový postup");
  await userEvent.click(screen.getByRole("button", { name: /^Uložiť$/ }));
  await waitFor(() => expect(apiMock.post).toHaveBeenCalled());
}

async function save() {
  render(<KnowledgeBasePage />);
  await userEvent.click(await screen.findByRole("button", { name: /Upraviť/ }));
  await userEvent.click(screen.getByRole("button", { name: /Uložiť zmeny/ }));
  await waitFor(() => expect(apiMock.put).toHaveBeenCalled());
}

async function remove() {
  render(<KnowledgeBasePage />);
  await userEvent.click(await screen.findByRole("button", { name: /Zmazať/ }));
  await userEvent.click(screen.getByRole("button", { name: "Áno" }));
  await waitFor(() => expect(apiMock.delete).toHaveBeenCalled());
}

const WRITES = [
  ["creating a document", "post", create],
  ["saving a document", "put", save],
  ["deleting a document", "delete", remove],
] as const;

describe("DEV-94 — Knowledge Base page: a change that stayed on ANDROS is said", () => {
  it.each(WRITES)("%s that did not reach GitHub says so, with the reason", async (_, method, write) => {
    apiMock[method].mockResolvedValue({ unpublished: REASON });

    await write();

    const notice = await screen.findByRole("status");
    expect(notice).toHaveTextContent(
      `Zmena je uložená na ANDROSe, ale na GitHub sa nedostala — kópia Znalostnej bázy na MAGERi ju zatiaľ nemá. ${REASON}`,
    );
  });

  it.each(WRITES)("%s that reached GitHub says nothing more", async (_, method, write) => {
    apiMock[method].mockResolvedValue({ unpublished: null });

    await write();

    // the page reloads its list after the write — by then the answer has been read
    await waitFor(() =>
      expect(apiMock.get.mock.calls.filter(([url]) => url === "/knowledge/categories")).toHaveLength(2),
    );
    expect(screen.queryByText(/na GitHub sa nedostala/)).not.toBeInTheDocument();
  });

  it("the notice goes away when he closes it", async () => {
    apiMock.put.mockResolvedValue({ unpublished: REASON });
    await save();

    await userEvent.click(await screen.findByRole("button", { name: "Zavrieť upozornenie" }));

    expect(screen.queryByText(/na GitHub sa nedostala/)).not.toBeInTheDocument();
  });
});
