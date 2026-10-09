/**
 * DEV-32 — a half-written Knowledge Base document survives leaving the screen.
 *
 * The editor held its text only in component state: he starts a document, clicks over to look something up,
 * comes back — the page opens in browse mode and the text is gone. Now the text comes back when he opens the
 * editor again, marked as his own earlier draft; a successful save forgets it.
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
  useSessionStore: { getState: () => ({ knowledgeDocPath: null, setKnowledgeDocPath: vi.fn() }) },
}));

import KnowledgeBasePage from "@/pages/KnowledgeBasePage";

const TEXT = "# Postup inštalácie\n\nPrvý krok.";
const KEY = "nex.draft.kb-novy-dokument.znalostna-baza";
const content = () => screen.getByPlaceholderText(/Názov dokumentu/);

beforeEach(() => {
  window.localStorage.clear();
  Object.values(apiMock).forEach((f) => f.mockReset());
  apiMock.get.mockImplementation(async (url: string) =>
    url.includes("categories") ? { categories: ["icc"] } : { documents: [], count: 0 },
  );
  apiMock.post.mockResolvedValue({});
});

describe("DEV-32 — Znalostná báza: rozpísaný dokument", () => {
  it("comes back when he opens „Nový“ again, says it is his, and is forgotten after saving", async () => {
    const first = render(<KnowledgeBasePage />);
    await userEvent.click(await screen.findByRole("button", { name: /Nový/ }));
    await userEvent.type(content(), TEXT);
    first.unmount(); // he went to look something up

    render(<KnowledgeBasePage />);
    await userEvent.click(await screen.findByRole("button", { name: /Nový/ }));
    expect(content()).toHaveValue(TEXT);
    expect(screen.getByText("Obnovený rozpísaný text — pokračuj, alebo ho prepíš.")).toBeInTheDocument();

    await userEvent.type(screen.getByPlaceholderText("Popisný názov..."), "Postup");
    await userEvent.click(screen.getByRole("button", { name: /Uložiť/ }));
    await waitFor(() => expect(apiMock.post).toHaveBeenCalled());
    await waitFor(() => expect(window.localStorage.getItem(KEY)).toBeNull());
  });
});
