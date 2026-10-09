import { render, screen, fireEvent, act, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { InstallButton } from "@/pwa/InstallButton";
import { __resetInstallCapture, markInstalled, startInstallCapture } from "@/pwa/installPrompt";
import type { InstallState } from "@/pwa/installState";

/**
 * The "Nainštalovať aplikáciu" button.
 *
 * The most valuable test loops over the states in which the button IS ON THE SCREEN:
 * in each of them a click must produce a visible result. A button that does nothing on
 * click is worse than no button - and that is the one rule placed above all others.
 *
 * WARNING: before v1.2.0 this loop walked ALL SEVEN states and enforced that the button
 * is on screen even in an installed app. That is exactly why the defect "the app offers
 * to install an app the person has just installed" passed the gate as done. The test was
 * therefore REWRITTEN in v1.2.1, not adapted to the result: the states were split into
 * those with the button and those without it, and guards were added for the "without" side.
 *
 * States are not induced by a faked hook but by the ENVIRONMENT (address, browser offer,
 * memory) - otherwise the test would measure its own imitation instead of the app.
 * The cockpit has no live preview of itself, so the "preview" state is not reachable here.
 *
 * What these tests DO NOT prove: how the button looks and whether a person finds it.
 * A person judges that.
 */

class BrowserOffer extends Event {
  prompt = vi.fn().mockResolvedValue(undefined);
  userChoice: Promise<{ outcome: "accepted" | "dismissed"; platform: string }>;

  constructor(outcome: "accepted" | "dismissed" = "accepted") {
    super("beforeinstallprompt", { cancelable: true });
    this.userChoice = Promise.resolve({ outcome, platform: "web" });
  }
}

const cleanups: Array<() => void> = [];

/** Gives the window a property the test environment lacks, and registers the cleanup. */
function giveWindow(key: string, value: unknown): void {
  Object.defineProperty(window, key, { configurable: true, value });
  cleanups.push(() => {
    delete (window as unknown as Record<string, unknown>)[key];
  });
}

/**
 * Induces a state through the ENVIRONMENT. Returns the offer when the state has one, so
 * it can be verified that it was really triggered.
 */
function induceState(state: Exclude<InstallState, "preview">): BrowserOffer | null {
  // A browser that KNOWS installation (this property is missing in the test environment,
  // in a real Chrome it has the value `null`).
  const browserKnowsIt = () => giveWindow("onbeforeinstallprompt", null);

  switch (state) {
    case "standalone":
      giveWindow("matchMedia", (query: string) => ({ matches: query.includes("standalone") }));
      return null;
    case "ready": {
      browserKnowsIt();
      const offer = new BrowserOffer();
      window.dispatchEvent(offer);
      return offer;
    }
    case "installed":
      browserKnowsIt();
      markInstalled();
      return null;
    case "unsupported":
      // Nothing needed: the environment behaves like a browser without support.
      return null;
    case "insecure":
      browserKnowsIt();
      giveWindow("isSecureContext", false);
      return null;
    case "unavailable":
      browserKnowsIt();
      return null;
  }
}

/**
 * States in which installing is NOT possible right now (or is possible immediately) -
 * the button stays and states the reason. Hiding it here would look like an app bug.
 */
const STATES_WITH_BUTTON: Exclude<InstallState, "preview">[] = [
  "ready",
  "unsupported",
  "insecure",
  "unavailable",
];

/** States in which installation is DONE - offering it again is a defect (v1.2.1). */
const STATES_WITHOUT_BUTTON: Exclude<InstallState, "preview">[] = ["standalone", "installed"];

const button = () => screen.getByRole("button", { name: "Nainštalovať aplikáciu" });
const buttonIfPresent = () => screen.queryByRole("button", { name: "Nainštalovať aplikáciu" });

beforeEach(() => {
  __resetInstallCapture();
  localStorage.clear();
  startInstallCapture();
});

afterEach(() => {
  cleanups.splice(0).forEach((cleanup) => cleanup());
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("a click ALWAYS has a visible result - in every state with the button", () => {
  it.each(STATES_WITH_BUTTON)("state '%s': the click either triggers installation or opens a dialog with text", async (state) => {
    const offer = induceState(state);
    render(<InstallButton appName="Test app" />);

    fireEvent.click(button());

    if (offer) {
      expect(offer.prompt).toHaveBeenCalledTimes(1);
      return;
    }
    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent?.replace("Rozumiem", "").trim().length).toBeGreaterThan(20);
  });

  it.each(STATES_WITH_BUTTON)("state '%s': the button is on screen and NOT disabled", (state) => {
    induceState(state);
    render(<InstallButton appName="Test app" />);

    const b = button();
    expect(b).toBeInTheDocument();
    expect(b).not.toBeDisabled();
    expect(b).not.toHaveAttribute("hidden");
  });

  it.each(STATES_WITH_BUTTON)("state '%s': the bubble exists everywhere except 'ready' and belongs to the button", (state) => {
    induceState(state);
    render(<InstallButton appName="Test app" />);

    const bubble = screen.queryByRole("tooltip");
    if (state === "ready") {
      expect(bubble).toBeNull();
      expect(button()).not.toHaveAttribute("aria-describedby");
      return;
    }
    expect(bubble).not.toBeNull();
    expect(bubble!.textContent!.trim().length).toBeGreaterThan(20);
    expect(button().getAttribute("aria-describedby")).toBe(bubble!.id);
  });
});

describe("after installation the app does NOT offer installation (v1.2.1)", () => {
  it.each(STATES_WITHOUT_BUTTON)(
    "state '%s': there is NO button in the page tree - neither greyed out nor hidden by an attribute",
    (state) => {
      induceState(state);
      const { container } = render(<InstallButton appName="Test app" />);

      // `queryByRole` alone is not enough: it would overlook a button hidden via `hidden`
      // or `aria-hidden`. The button must not be hidden - it must not be there at all.
      expect(buttonIfPresent()).toBeNull();
      expect(container.querySelectorAll("button")).toHaveLength(0);
      expect(container.textContent).not.toContain("Nainštalovať");
    },
  );

  it.each(STATES_WITHOUT_BUTTON)("state '%s': no bubble is left behind either", (state) => {
    induceState(state);
    render(<InstallButton appName="Test app" />);

    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("SAFETY NET: a fresh offer beats the remembered installation - the button IS on screen", () => {
    // This is the guard without which hiding would be dangerous. When the app is NOT
    // installed (other profile, cleared data), the browser fires the offer again - and
    // then the button MUST return, otherwise the person is stuck with no way out.
    induceState("installed");
    const offer = new BrowserOffer();
    act(() => {
      window.dispatchEvent(offer);
    });

    render(<InstallButton appName="Test app" />);

    expect(buttonIfPresent()).not.toBeNull();
    fireEvent.click(button());
    expect(offer.prompt).toHaveBeenCalledTimes(1);
  });

  it("the confirmation after a finished installation appears EVEN WITHOUT the button", async () => {
    // The button is hidden, not the app's answer. Whoever has just finished installing
    // must learn where to find the app - otherwise hiding would take that away too.
    induceState("unavailable");
    render(<InstallButton appName="Test app" />);

    act(() => {
      window.dispatchEvent(new Event("appinstalled"));
    });

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading")).toHaveTextContent("Hotovo.");
    expect(buttonIfPresent()).toBeNull();

    fireEvent.click(within(dialog).getByRole("button", { name: "Rozumiem" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});

describe("the fallback guide tells the truth (v1.2.1)", () => {
  async function openFallbackGuide(): Promise<string> {
    induceState("unavailable");
    render(<InstallButton appName="Test app" />);
    fireEvent.click(button());
    return (await screen.findByRole("dialog")).textContent ?? "";
  }

  it("names the browser restart as the FIRST cause, before the other two", async () => {
    const text = await openFallbackGuide();

    const restart = text.indexOf("reštart");
    const alreadyInstalled = text.indexOf("už môže byť");
    const dismissed = text.indexOf("potlač");

    expect(restart, "the browser restart is not mentioned in the dialog").toBeGreaterThanOrEqual(0);
    expect(alreadyInstalled, "'the app may already be installed' is not mentioned").toBeGreaterThan(restart);
    expect(dismissed, "'the offer was dismissed and suppressed' is not mentioned").toBeGreaterThan(restart);
  });

  it("gives the browser-menu path CONDITIONALLY and only after the causes", async () => {
    const text = await openFallbackGuide();

    // Before v1.2.0 the dialog stated "Click Install page as app" as a certainty. That
    // item appears in the Chrome menu ONLY when the browser offers installation - i.e.
    // never in this state. A step that cannot be performed is worse than an admission.
    expect(text).toMatch(/[Aa]k .{0,40}vidíš/);
    expect(text.indexOf("ponuk")).toBeGreaterThanOrEqual(0);
    expect(text.indexOf("reštart")).toBeLessThan(text.indexOf("vidíš"));
  });
});

describe("state 'ready'", () => {
  it("the offer is triggered exactly once and after a dismissal NO dialog opens", async () => {
    const offer = induceState("ready") as BrowserOffer;
    Object.assign(offer, {
      userChoice: Promise.resolve({ outcome: "dismissed" as const, platform: "web" }),
    });
    render(<InstallButton appName="Test app" />);

    await act(async () => {
      fireEvent.click(button());
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(offer.prompt).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("the next click after a dismissal DOES show the manual guide - a click must not end in nothing", async () => {
    const offer = induceState("ready") as BrowserOffer;
    Object.assign(offer, {
      userChoice: Promise.resolve({ outcome: "dismissed" as const, platform: "web" }),
    });
    render(<InstallButton appName="Test app" />);
    await act(async () => {
      fireEvent.click(button());
      await Promise.resolve();
      await Promise.resolve();
    });

    fireEvent.click(button());

    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain("Pridať na plochu");
  });
});

describe("after a finished installation", () => {
  it("a confirmation titled 'Hotovo.' appears", async () => {
    induceState("unavailable");
    render(<InstallButton appName="Test app" />);

    act(() => {
      window.dispatchEvent(new Event("appinstalled"));
    });

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("heading")).toHaveTextContent("Hotovo.");
    // The app name comes from the `appName` prop, not from a hard-coded text.
    expect(dialog.textContent).toContain("Test app");
  });

  it("whoever once had the app gets the confirmation too - it is not tied to memory", async () => {
    induceState("installed");
    render(<InstallButton appName="Test app" />);

    act(() => {
      window.dispatchEvent(new Event("appinstalled"));
    });

    await waitFor(() =>
      expect(within(screen.getByRole("dialog")).getByRole("heading")).toHaveTextContent("Hotovo."),
    );
  });
});

describe("the guide dialog", () => {
  it("closes with the button, with the Esc key and with a click outside the card", async () => {
    induceState("unavailable");
    render(<InstallButton appName="Test app" />);

    // with the button
    fireEvent.click(button());
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Rozumiem" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    // with the Esc key
    fireEvent.click(button());
    await screen.findByRole("dialog");
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    // with a click outside the card (a click INSIDE the card does not close it)
    fireEvent.click(button());
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("heading"));
    expect(screen.queryByRole("dialog")).not.toBeNull();
    fireEvent.click(dialog);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("after closing, focus returns to the button, not to the top of the page", async () => {
    induceState("unavailable");
    render(<InstallButton appName="Test app" />);
    const b = button();
    b.focus();

    fireEvent.click(b);
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Rozumiem" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(document.activeElement).toBe(b);
  });
});

describe("the look keeps the approved contract", () => {
  it("outline and text colour come from a TOKEN - no colour literal in the style", () => {
    induceState("unavailable");
    render(<InstallButton appName="Test app" />);

    const style = button().getAttribute("style") ?? "";
    expect(style).toContain("--color-accent-primary");
    expect(style).not.toMatch(/#[0-9a-fA-F]{3,6}|rgb\(/);
  });

  it("dimming does not drop below the measured threshold of 0.85", () => {
    induceState("unavailable");
    render(<InstallButton appName="Test app" />);

    // A lower value means contrast below the 3 : 1 standard - a defect, not a matter of taste.
    expect(button().className).toContain("opacity-[0.85]");
  });

  it("in state 'ready' it is not dimmed", () => {
    induceState("ready");
    render(<InstallButton appName="Test app" />);

    expect(button().className).not.toContain("opacity-[");
  });
});

describe("broken storage", () => {
  it("does not crash the app and the button stays usable", async () => {
    const original = Storage.prototype.getItem;
    Storage.prototype.getItem = function () {
      throw new Error("storing is forbidden in this window");
    };
    try {
      induceState("unavailable");
      render(<InstallButton appName="Test app" />);

      fireEvent.click(button());

      expect(await screen.findByRole("dialog")).toBeInTheDocument();
    } finally {
      Storage.prototype.getItem = original;
    }
  });
});
