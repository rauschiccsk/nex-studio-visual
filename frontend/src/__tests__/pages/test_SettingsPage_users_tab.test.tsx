/**
 * DEV-485 — Nastavenia → Používatelia: every panel under the accounts is on the page exactly once, also when the
 * accounts arrive after the first render (as they always do).
 *
 * 21.11.2026 the Director, with a screenshot: „nevidím tam možnosť nastavenia rýchlej cesty.“ The page showed
 * „Zodpovedný v Plane“ twice: the panel of the fast path (DEV-483) got the same key as the panel above it — both were
 * empty while the accounts loaded, and React kept an orphan copy of the first one, even on the other tabs.
 */
import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

const accounts = vi.hoisted(() => {
  let release: (page: unknown) => void = () => {};
  const later = new Promise<unknown>((resolve) => {
    release = resolve;
  });
  return { later, release: (page: unknown) => release(page) };
});

vi.mock("@/services/api/users", () => ({
  listUsersApi: vi.fn(() => accounts.later),
  createUserApi: vi.fn(),
  updateUserApi: vi.fn(),
  deleteUserApi: vi.fn(),
  changePasswordApi: vi.fn(),
  listPlaneResponsibleApi: vi.fn().mockResolvedValue({ accounts: [], names: [] }),
  setPlaneResponsibleApi: vi.fn(),
}));
vi.mock("@/services/api/batchReview", () => ({
  listBatchReviewAccountsApi: vi.fn().mockResolvedValue([]),
  setBatchReviewApi: vi.fn(),
  getBatchReviewApi: vi.fn().mockResolvedValue(null),
}));
vi.mock("@/services/api/userAgentSettings", () => ({
  listAgentModelsApi: vi.fn().mockResolvedValue([]),
  listUserAgentSettingsApi: vi.fn().mockResolvedValue([]),
  upsertUserAgentSettingApi: vi.fn(),
}));
vi.mock("@/services/api/systemSettings", () => ({
  listSystemSettingsApi: vi.fn().mockResolvedValue([]),
  updateSystemSettingApi: vi.fn(),
}));
vi.mock("@/services/api/auth", () => ({
  updateMyProfileApi: vi.fn(),
  telegramTestApi: vi.fn(),
  getAccentColorsApi: vi.fn().mockResolvedValue([]),
}));
vi.mock("@/services/api/userSessions", () => ({
  listUserSessionsApi: vi.fn().mockResolvedValue([]),
  deleteUserSessionApi: vi.fn(),
}));
vi.mock("@/store/authStore", () => ({
  useAuthStore: (sel: (s: unknown) => unknown) =>
    sel({ user: { id: "u1", username: "admin", role: "ri" }, login: vi.fn(), fetchMe: vi.fn() }),
}));

import SettingsPage from "@/pages/SettingsPage";

const USERS = [
  { id: "u1", username: "admin", email: "a@example.com", role: "ri", is_active: true, first_name: "Ad", last_name: "Min" },
  { id: "u2", username: "tibor", email: "t@example.com", role: "ri", is_active: true, first_name: "Ti", last_name: "Bor" },
];

describe("Nastavenia → Používatelia — each panel once (DEV-485)", () => {
  it("shows each panel's heading once after the accounts arrive, and none of them on another tab", async () => {
    render(<SettingsPage />);
    fireEvent.click(screen.getByRole("tab", { name: "Používatelia" }));
    await screen.findByRole("heading", { name: "Zodpovedný v Plane" });

    await act(async () => accounts.release({ items: USERS, total: USERS.length }));
    await waitFor(() => expect(screen.getAllByText("tibor").length).toBeGreaterThan(0));

    expect(screen.getAllByRole("heading", { name: "Zodpovedný v Plane" })).toHaveLength(1);
    expect(screen.getAllByRole("heading", { name: "Otázky Staviteľa najprv s Poradcom" })).toHaveLength(1);

    fireEvent.click(screen.getByRole("tab", { name: "Relácie" }));
    expect(screen.queryByRole("heading", { name: "Zodpovedný v Plane" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Otázky Staviteľa najprv s Poradcom" })).toBeNull();
  });
});
