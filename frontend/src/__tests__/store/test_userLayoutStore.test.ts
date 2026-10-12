/**
 * DEV-82 — the person's layout comes from the server once per signed-in person; a change shows at once and stays only
 * when the server keeps it; another person signing in never sees the previous one's layout.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("@/services/api/userLayout", () => ({ getUserLayout: vi.fn(), saveUserLayout: vi.fn() }));
vi.mock("@/services/api/auth", () => ({
  loginApi: vi.fn(async () => ({ access_token: "t", user: { id: "u2", username: "tibor", role: "ri" } })),
  logoutApi: vi.fn(),
  getMeApi: vi.fn(),
}));

import { getUserLayout, saveUserLayout, type UserLayout } from "@/services/api/userLayout";
import { useAuthStore } from "@/store/authStore";
import { useUserLayoutStore } from "@/store/userLayoutStore";

const LAYOUT: UserLayout = {
  sidebar_width: 300,
  sidebar_collapsed: false,
  plan_width: null,
  limits: { sidebar_width: { min: 180, max: 360, default: 224 }, plan_width: { min: 280, max: 640, default: 320 } },
};

beforeEach(() => {
  vi.mocked(getUserLayout).mockReset();
  vi.mocked(saveUserLayout).mockReset();
  useUserLayoutStore.setState({ layout: null });
});

describe("useUserLayoutStore", () => {
  it("reads the layout from the server; without an answer the screen keeps its defaults", async () => {
    vi.mocked(getUserLayout).mockResolvedValueOnce(LAYOUT).mockRejectedValueOnce(new Error("offline"));

    await useUserLayoutStore.getState().load();
    expect(useUserLayoutStore.getState().layout).toEqual(LAYOUT);

    useUserLayoutStore.setState({ layout: null });
    await useUserLayoutStore.getState().load();
    expect(useUserLayoutStore.getState().layout).toBeNull();
  });

  it("a change shows at once and the server's answer is what stays", async () => {
    useUserLayoutStore.setState({ layout: LAYOUT });
    let answer!: (v: typeof LAYOUT) => void;
    vi.mocked(saveUserLayout).mockReturnValue(new Promise((r) => (answer = r)));

    const saving = useUserLayoutStore.getState().save({ plan_width: 400 });
    expect(useUserLayoutStore.getState().layout?.plan_width).toBe(400);
    answer({ ...LAYOUT, plan_width: 400 });
    await saving;

    expect(saveUserLayout).toHaveBeenCalledWith({ plan_width: 400 });
    expect(useUserLayoutStore.getState().layout?.plan_width).toBe(400);
  });

  it("a change the server refuses goes back", async () => {
    useUserLayoutStore.setState({ layout: LAYOUT });
    vi.mocked(saveUserLayout).mockRejectedValue(new Error("422"));

    await useUserLayoutStore.getState().save({ sidebar_width: 999 });

    expect(useUserLayoutStore.getState().layout).toEqual(LAYOUT);
  });

  it("another person signing in starts without the previous one's layout", async () => {
    useUserLayoutStore.setState({ layout: LAYOUT });

    await useAuthStore.getState().login("tibor", "x");

    expect(useUserLayoutStore.getState().layout).toBeNull();
  });
});
