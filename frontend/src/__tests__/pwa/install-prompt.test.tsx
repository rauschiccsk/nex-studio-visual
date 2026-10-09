import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  __resetInstallCapture,
  markInstalled,
  readInstallSignals,
  runInstallPrompt,
  startInstallCapture,
  subscribeInstall,
  wasPromptDismissed,
} from "@/pwa/installPrompt";
import { useInstallPrompt } from "@/pwa/useInstallPrompt";

/**
 * HARD GATE: criterion no. 1 - "the app CAPTURES the offer".
 *
 * It stands on its own and is deliberately not mixed into the other button tests: the
 * browser offers installation EXACTLY ONCE, at a moment of its own choosing. If the app
 * does not capture it then, it is gone for good and the button has nothing to trigger -
 * a button that does nothing on click. This version must prevent exactly that.
 *
 * What these tests DO NOT prove: that the offer really appears in a real browser. That
 * cannot run in this environment and is verified by a person.
 */

/** Faithful imitation of the browser offer - an event with extra fields. */
class BrowserOffer extends Event {
  prompt = vi.fn().mockResolvedValue(undefined);
  userChoice: Promise<{ outcome: "accepted" | "dismissed"; platform: string }>;

  constructor(outcome: "accepted" | "dismissed" = "accepted") {
    super("beforeinstallprompt", { cancelable: true });
    this.userChoice = Promise.resolve({ outcome, platform: "web" });
  }
}

/** A screen that only reads the button state - nothing more is needed. */
function ButtonState() {
  const { state } = useInstallPrompt("Test app");
  return <span data-testid="state">{state}</span>;
}

beforeEach(() => {
  __resetInstallCapture();
  localStorage.clear();
  startInstallCapture();
});

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("capture of the install offer", () => {
  it("an offer delivered BEFORE rendering is still available to the button", () => {
    window.dispatchEvent(new BrowserOffer());

    render(<ButtonState />);

    expect(screen.getByTestId("state")).toHaveTextContent("ready");
  });

  it("capturing calls preventDefault - the browser shows no banner of its own", () => {
    const offer = new BrowserOffer();

    window.dispatchEvent(offer);

    expect(offer.defaultPrevented).toBe(true);
  });

  it("until an offer arrives, the app does NOT claim to have one", () => {
    expect(readInstallSignals().hasPrompt).toBe(false);

    render(<ButtonState />);

    expect(screen.getByTestId("state")).not.toHaveTextContent("ready");
  });

  it("an offer WITHOUT a usable trigger is NOT accepted - the app does not look ready", () => {
    // An event with the right name but no `prompt()`. If the app accepted it, the button
    // would light up as ready and the click would end in nothing.
    const bareEvent = new Event("beforeinstallprompt", { cancelable: true });

    window.dispatchEvent(bareEvent);

    expect(bareEvent.defaultPrevented).toBe(true); // the banner is suppressed either way
    expect(readInstallSignals().hasPrompt).toBe(false);
  });

  it("an offer that arrives DURING rendering is not lost", () => {
    // Exactly the gap that the catch-up read closes: the hook reads the state while
    // rendering but subscribes to changes only after. A child renders BETWEEN the two,
    // so the offer arrives into that gap and nobody announces it.
    function GapMessenger() {
      window.dispatchEvent(new BrowserOffer());
      return null;
    }

    render(
      <>
        <ButtonState />
        <GapMessenger />
      </>,
    );

    expect(screen.getByTestId("state")).toHaveTextContent("ready");
  });
});

describe("triggering the captured offer", () => {
  it("the trigger happens in the same step as the click, with no waiting before it", async () => {
    // The browser requires a user gesture and after the first wait it no longer holds
    // it. Measured by deliberately NOT awaiting the result and asserting at once that
    // the trigger has already happened.
    const offer = new BrowserOffer();
    window.dispatchEvent(offer);

    const run = runInstallPrompt();

    expect(offer.prompt).toHaveBeenCalledTimes(1);
    await run;
  });

  it("the offer is used EXACTLY ONCE; a second call returns 'unavailable'", async () => {
    const offer = new BrowserOffer();
    window.dispatchEvent(offer);

    expect(await runInstallPrompt()).toBe("accepted");
    expect(await runInstallPrompt()).toBe("unavailable");
    expect(offer.prompt).toHaveBeenCalledTimes(1);
  });

  it("a dismissal is remembered by the app and nothing crashes", async () => {
    window.dispatchEvent(new BrowserOffer("dismissed"));

    expect(await runInstallPrompt()).toBe("dismissed");
    expect(wasPromptDismissed()).toBe(true);
  });

  it("when the trigger fails, 'unavailable' is returned - the caller has something to show", async () => {
    const offer = new BrowserOffer();
    offer.prompt.mockRejectedValue(new Error("browser refused the trigger"));
    window.dispatchEvent(offer);

    expect(await runInstallPrompt()).toBe("unavailable");
  });
});

describe("memory of a finished installation", () => {
  it("a finished installation writes the marker and resets the offer", () => {
    window.dispatchEvent(new BrowserOffer());
    const notices: unknown[] = [];
    subscribeInstall((notice) => notices.push(notice));

    window.dispatchEvent(new Event("appinstalled"));

    const signals = readInstallSignals();
    expect(signals.rememberedInstalled).toBe(true);
    expect(signals.hasPrompt).toBe(false);
    expect(notices).toContain("installed");
  });

  it("SELF-CORRECTION: a new offer erases the remembered installation", () => {
    window.dispatchEvent(new Event("appinstalled"));
    expect(readInstallSignals().rememberedInstalled).toBe(true);

    // The browser offers installation only to someone who does NOT have the app
    // installed - so the person uninstalled it meanwhile and our memory no longer holds.
    window.dispatchEvent(new BrowserOffer());

    expect(readInstallSignals().rememberedInstalled).toBe(false);
  });

  it("a SILENT storage failure is detected - the write is verified by reading back", () => {
    const original = Storage.prototype.setItem;
    // Storage that throws no error and stores NOTHING. A guard built on "did not throw"
    // would claim success here.
    Storage.prototype.setItem = function () {};
    try {
      expect(markInstalled()).toBe(false);
    } finally {
      Storage.prototype.setItem = original;
    }
  });

  it("broken storage crashes nothing and the confirmation is not lost", () => {
    const notices: unknown[] = [];
    subscribeInstall((notice) => notices.push(notice));
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function () {
      throw new Error("storing is forbidden in this window");
    };
    try {
      expect(markInstalled()).toBe(false);
      expect(() => readInstallSignals()).not.toThrow();

      window.dispatchEvent(new Event("appinstalled"));

      // The person installed the app like anyone else and must learn where to find it,
      // so the confirmation rests on the event, not on a successful write.
      expect(notices).toContain("installed");
    } finally {
      Storage.prototype.setItem = original;
    }
  });
});

describe("the cockpit has no live preview of itself", () => {
  it("the preview signal is never raised, whatever the environment says", () => {
    vi.stubEnv("VITE_PREVIEW", "1");

    expect(readInstallSignals().isPreview).toBe(false);
  });
});

describe("the app really enables the capture - and BEFORE rendering", () => {
  // The tests above prove the module captures the offer once somebody turns it on. This
  // proves the other half: that the app turns it on by itself, before it starts drawing.
  // Without it the whole gate would be green for an app that never starts the capture.
  const order: string[] = [];
  const startInstallCaptureMock = vi.fn(() => {
    order.push("capture");
  });
  const renderMock = vi.fn(() => {
    order.push("render");
  });
  const registerServiceWorkerMock = vi.fn();

  beforeEach(() => {
    vi.resetModules();
    order.length = 0;
    startInstallCaptureMock.mockClear();
    renderMock.mockClear();
    document.body.innerHTML = '<div id="root"></div>';
    vi.doMock("@/pwa/installPrompt", () => ({ startInstallCapture: startInstallCaptureMock }));
    vi.doMock("react-dom/client", () => {
      const client = { createRoot: () => ({ render: renderMock }) };
      return { ...client, default: client };
    });
    registerServiceWorkerMock.mockClear();
    vi.doMock("@/pwa/registerServiceWorker", () => ({ registerServiceWorker: registerServiceWorkerMock }));
    vi.doMock("@/App", () => ({ default: () => null }));
  });

  afterEach(() => {
    vi.doUnmock("@/pwa/installPrompt");
    vi.doUnmock("react-dom/client");
    vi.doUnmock("@/pwa/registerServiceWorker");
    vi.doUnmock("@/App");
  });

  it("the capture is enabled before the first render", async () => {
    await import("@/main");

    await vi.waitFor(() => expect(renderMock).toHaveBeenCalled());
    expect(startInstallCaptureMock).toHaveBeenCalledTimes(1);
    expect(order[0]).toBe("capture");
  });

  it("the cockpit registers its service worker — without it the browser does not offer the installation", async () => {
    await import("@/main");

    await vi.waitFor(() => expect(renderMock).toHaveBeenCalled());
    expect(registerServiceWorkerMock).toHaveBeenCalledTimes(1);
  });
});
