/**
 * Nastavenia → Agenti (ICCINT-167): the model choices come from the backend as FAMILIES, each labelled with
 * the version that last really ran — the page itself names no version, so Opus 5.5 (and whatever comes
 * after it) is selectable without a change in the application.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

const { mockListModels, mockListSettings, mockUpsert } = vi.hoisted(() => ({
  mockListModels: vi.fn(),
  mockListSettings: vi.fn(),
  mockUpsert: vi.fn(),
}));

vi.mock("@/services/api/userAgentSettings", () => ({
  listAgentModelsApi: mockListModels,
  listUserAgentSettingsApi: mockListSettings,
  upsertUserAgentSettingApi: mockUpsert,
}));
vi.mock("@/services/api/systemSettings", () => ({
  listSystemSettingsApi: vi.fn().mockResolvedValue([]),
  updateSystemSettingApi: vi.fn(),
}));
vi.mock("@/services/api/users", () => ({
  listUsersApi: vi.fn().mockResolvedValue([]),
  createUserApi: vi.fn(),
  updateUserApi: vi.fn(),
  deleteUserApi: vi.fn(),
  changePasswordApi: vi.fn(),
}));
vi.mock("@/services/api/auth", () => ({ updateMyProfileApi: vi.fn(), telegramTestApi: vi.fn() }));
vi.mock("@/services/api/userSessions", () => ({
  listUserSessionsApi: vi.fn().mockResolvedValue([]),
  deleteUserSessionApi: vi.fn(),
}));
vi.mock("@/store/authStore", () => ({
  useAuthStore: (sel: (s: unknown) => unknown) =>
    sel({ user: { id: "u1", username: "admin", role: "ri" }, login: vi.fn(), fetchMe: vi.fn() }),
}));

import SettingsPage from "@/pages/SettingsPage";

describe("Nastavenia → Agenti — model by family (ICCINT-167)", () => {
  beforeEach(() => {
    mockListModels.mockReset();
    mockListSettings.mockReset();
    mockUpsert.mockReset();
    mockListModels.mockResolvedValue([
      { id: "opus", last_run_model: "claude-opus-5-5", last_run_at: "2026-10-05T10:00:00Z" },
      { id: "sonnet", last_run_model: null, last_run_at: null },
      { id: "haiku", last_run_model: "claude-haiku-4-5-20251001", last_run_at: "2026-10-04T08:00:00Z" },
    ]);
    mockListSettings.mockResolvedValue([]);
    mockUpsert.mockImplementation(async (role: string, body: object) => ({ agent_role: role, ...body }));
  });

  it("lists the backend's families with the version that last ran, and no version as a value", async () => {
    render(<SettingsPage />);
    fireEvent.click(screen.getByRole("button", { name: "Agenti" }));

    await waitFor(() =>
      expect(screen.getAllByRole("option", { name: "Opus — vždy najnovší (naposledy bežal Opus 5.5)" }).length).toBeGreaterThan(0),
    );
    expect(screen.getAllByRole("option", { name: "Sonnet — vždy najnovší" }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("option", { name: "Haiku — vždy najnovší (naposledy bežal Haiku 4.5)" }).length).toBeGreaterThan(0);
    // No option value is a version — only families reach the backend.
    const values = screen.getAllByRole("option").map((o) => (o as HTMLOptionElement).value);
    expect(values.filter((v) => /claude-/.test(v))).toEqual([]);
  });
});
