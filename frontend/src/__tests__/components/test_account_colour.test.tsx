/**
 * DEV-320 — every window of the cockpit shows whose it is: the account's colour in a band across the top and beside
 * its name in the top bar, and the name in the window's title (the taskbar). The person picks the colour in Nastavenia
 * from the server's palette.
 *
 * Director 07.11.2026: „aby som mohol paralelne púšťať viac kokpitov … (aj farebne rozlíšené)“ — each account runs in
 * its own Chrome profile, and an app window does not show the profile's colour, so the cockpit has to.
 *
 * DEV-322 — the window's title is the account's name alone (the taskbar preview showed „NEX Studio Visual - Alexan…“;
 * Director: „NEX Visual študio je zbytočný informácia, vídim to podľa ikony NSV“). Leaving the signed-in layout gives
 * the title back, so the sign-in page does not keep the last person's name.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MemoryRouter } from "react-router-dom";

const api = vi.hoisted(() => ({
  getAccentColorsApi: vi.fn(),
  updateMyProfileApi: vi.fn(),
}));
vi.mock("@/services/api/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/api/auth")>()),
  getAccentColorsApi: api.getAccentColorsApi,
  updateMyProfileApi: api.updateMyProfileApi,
}));
vi.mock("@/hooks/useTheme", () => ({ useTheme: () => ({ isDark: false, toggleDark: vi.fn() }) }));

import Topbar from "@/components/layout/Topbar";
import AccountBand from "@/components/layout/AccountBand";
import AccountColorPicker from "@/components/settings/AccountColorPicker";
import { useAuthStore } from "@/store/authStore";

const PALETTE = ["#2563eb", "#16a34a", "#dc2626", "#9333ea", "#ea580c", "#0891b2", "#db2777", "#ca8a04"];
const ZOLTAN = {
  id: "u-zoltan",
  username: "zoltan",
  email: "zoltan@example.com",
  role: "ri" as const,
  is_active: true,
  first_name: "Zoltán",
  last_name: "Rausch",
  accent_color: "#dc2626",
  created_at: "2026-11-07T14:41:23Z",
  updated_at: "2026-11-07T14:41:23Z",
};

beforeEach(() => {
  useAuthStore.setState({ user: { ...ZOLTAN } });
  api.getAccentColorsApi.mockResolvedValue(PALETTE);
  api.updateMyProfileApi.mockImplementation(async (data: { accent_color: string }) => ({ ...ZOLTAN, ...data }));
});
afterEach(() => {
  vi.clearAllMocks();
  document.title = "";
});

describe("a window says whose it is", () => {
  it("the top bar names the account beside its colour", () => {
    render(
      <MemoryRouter initialEntries={["/projects"]}>
        <Topbar />
      </MemoryRouter>,
    );
    const chip = screen.getByTestId("account-chip");
    expect(chip).toHaveTextContent("Zoltán Rausch");
    expect(screen.getByTestId("account-chip-colour")).toHaveStyle({ backgroundColor: "#dc2626" });
  });

  it("a band across the top has the account's colour and the window's title names the account", () => {
    render(<AccountBand />);
    expect(screen.getByTestId("account-band")).toHaveStyle({ backgroundColor: "#dc2626" });
    expect(document.title).toBe("Zoltán Rausch");
  });

  it("leaving the signed-in layout gives the window its plain title back", () => {
    const { unmount } = render(<AccountBand />);
    expect(document.title).toBe("Zoltán Rausch");
    unmount();
    expect(document.title).toBe("NEX Studio Visual");
  });

  it("an account without a name is named by its username", () => {
    useAuthStore.setState({ user: { ...ZOLTAN, first_name: null, last_name: null, username: "rausch" } });
    render(<AccountBand />);
    expect(document.title).toBe("rausch");
  });
});

describe("the person picks the colour in Nastavenia", () => {
  it("offers the server's palette with the account's colour chosen", async () => {
    render(<AccountColorPicker />);
    const swatches = await screen.findAllByRole("button", { name: /^Farba / });
    expect(swatches).toHaveLength(PALETTE.length);
    expect(screen.getByRole("button", { name: "Farba #dc2626" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Farba #2563eb" })).toHaveAttribute("aria-pressed", "false");
  });

  it("a click saves the colour and the window takes it at once", async () => {
    render(
      <>
        <AccountColorPicker />
        <AccountBand />
      </>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Farba #16a34a" }));
    await waitFor(() => expect(api.updateMyProfileApi).toHaveBeenCalledWith({ accent_color: "#16a34a" }));
    await waitFor(() => expect(screen.getByTestId("account-band")).toHaveStyle({ backgroundColor: "#16a34a" }));
  });

  it("a colour that did not save says so and the band keeps the old one", async () => {
    api.updateMyProfileApi.mockRejectedValueOnce(new Error("422"));
    render(
      <>
        <AccountColorPicker />
        <AccountBand />
      </>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Farba #16a34a" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Farbu sa nepodarilo uložiť");
    expect(screen.getByTestId("account-band")).toHaveStyle({ backgroundColor: "#dc2626" });
  });
});
