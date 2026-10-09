import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "@/App";
import { UpdateBanner } from "@/pwa/UpdateBanner";
import { COUNT_KEY } from "@/pwa/versionCheck";

const reload = vi.fn();

beforeEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
  localStorage.clear();
  reload.mockClear();
  setPath("/");
});

/**
 * `location.reload` cannot be redefined in jsdom, so the whole object is replaced - but
 * with `origin` and `href`, otherwise the router could not assemble an address from it.
 */
function setPath(path: string) {
  window.history.pushState({}, "", path);
  Object.defineProperty(window, "location", {
    value: {
      reload,
      assign: vi.fn(),
      replace: vi.fn(),
      origin: "http://localhost:3000",
      href: `http://localhost:3000${path}`,
      protocol: "http:",
      host: "localhost:3000",
      hostname: "localhost",
      port: "3000",
      pathname: path,
      search: "",
      hash: "",
    },
    writable: true,
    configurable: true,
  });
}

describe("the new-version banner", () => {
  it("offers a reload, not an alarm", () => {
    render(<UpdateBanner onApply={vi.fn()} onDismiss={vi.fn()} />);

    const bar = screen.getByRole("status");
    expect(bar).toHaveTextContent("Je k dispozícii novšia verzia.");
    expect(screen.getByRole("button", { name: "Načítať" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Zavrieť" })).toBeInTheDocument();
    // `status` is a notice, `alert` would be an error - this is an offer.
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("is pinned to the BOTTOM so it does not clash with the session warning", () => {
    render(<UpdateBanner onApply={vi.fn()} onDismiss={vi.fn()} />);

    const wrapper = screen.getByRole("status");
    expect(wrapper.className).toContain("bottom-0");
    expect(wrapper.className).not.toContain("top-0");
  });

  it("does NOT block clicking through to the rest of the screen", () => {
    render(<UpdateBanner onApply={vi.fn()} onDismiss={vi.fn()} />);

    // The wrapper spans the full width, so without this it would cover the content below.
    const wrapper = screen.getByRole("status");
    expect(wrapper.className).toContain("pointer-events-none");
    expect(wrapper.firstElementChild?.className).toContain("pointer-events-auto");
  });

  it("'Načítať' and 'Zavrieť' report their clicks", async () => {
    const onApply = vi.fn();
    const onDismiss = vi.fn();
    render(<UpdateBanner onApply={onApply} onDismiss={onDismiss} />);

    await userEvent.click(screen.getByRole("button", { name: "Načítať" }));
    expect(onApply).toHaveBeenCalledTimes(1);
    expect(onDismiss).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Zavrieť" }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
    expect(onApply).toHaveBeenCalledTimes(1);
  });
});

describe("wiring of the banner in the app", () => {
  /** The server reports another version; the guard ceiling is used up, so the banner is offered. */
  function serverReportsOtherVersion() {
    sessionStorage.setItem(COUNT_KEY, "2");
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ buildId: "OTHER-VERSION" }),
    } as unknown as Response);
  }

  it("it appears on the LOGIN page too - i.e. above the router", async () => {
    // The core point: if the banner lived in the layout, only signed-in people would see
    // it, and the person before signing in - who needs the new version most - would not.
    serverReportsOtherVersion();
    setPath("/login");

    render(<App />);

    expect(await screen.findByText("Je k dispozícii novšia verzia.")).toBeInTheDocument();
    // We really are on the login page, not inside the app.
    expect(screen.getByText("Prihláste sa na svoj účet")).toBeInTheDocument();
  });

  it("when the versions match, there is no banner", async () => {
    const f = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ buildId: "dev" }), // matches the fingerprint in development
    } as unknown as Response);
    setPath("/login");

    render(<App />);

    await waitFor(() => expect(f).toHaveBeenCalled());
    expect(screen.queryByText("Je k dispozícii novšia verzia.")).toBeNull();
  });

  it("clicking 'Načítať' reloads the app", async () => {
    serverReportsOtherVersion();
    setPath("/login");

    render(<App />);
    await screen.findByText("Je k dispozícii novšia verzia.");
    await userEvent.click(screen.getByRole("button", { name: "Načítať" }));

    expect(reload).toHaveBeenCalled();
  });

  it("closing hides the banner", async () => {
    serverReportsOtherVersion();
    setPath("/login");

    render(<App />);
    await screen.findByText("Je k dispozícii novšia verzia.");
    await userEvent.click(screen.getByRole("button", { name: "Zavrieť" }));

    await waitFor(() =>
      expect(screen.queryByText("Je k dispozícii novšia verzia.")).toBeNull(),
    );
  });

  it("the login page does NOT carry the install button", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({ buildId: "dev" }),
    } as unknown as Response);
    setPath("/login");

    render(<App />);

    await screen.findByText("Prihláste sa na svoj účet");
    expect(screen.queryByRole("button", { name: "Nainštalovať aplikáciu" })).toBeNull();
  });
});
