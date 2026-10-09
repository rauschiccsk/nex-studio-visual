import { describe, expect, it } from "vitest";

import { guideFor, type InstallGuide } from "@/pwa/installGuides";

/**
 * Manual installation guides.
 *
 * Tested on REAL strings that browsers identify themselves with - not invented ones. An
 * invented string proves only that the condition works on itself; a real one reveals
 * that the strings contain each other (Edge also identifies as Chrome, Chrome carries
 * "Safari" in its string, browsers on iPhone each identify differently).
 *
 * What these tests DO NOT prove: that the items in browser menus are really named as
 * written. That cannot be verified in this environment (no browser runs here) and a
 * person checks it - which is why every guide has a catch-all sentence.
 */

/** Strings from real browsers (June 2024) - not invented. */
const UA = {
  chromeWindows:
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
  chromeLinux:
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
  edgeWindows:
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
  chromeAndroid:
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
  edgeAndroid:
    "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36 EdgA/126.0.0.0",
  samsungAndroid:
    "Mozilla/5.0 (Linux; Android 13; SAMSUNG SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/23.0 Chrome/115.0.0.0 Mobile Safari/537.36",
  firefoxAndroid: "Mozilla/5.0 (Android 14; Mobile; rv:126.0) Gecko/126.0 Firefox/126.0",
  firefoxWindows:
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
  firefoxLinux: "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
  safariMac:
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
  safariIphone:
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
  chromeIphone:
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/126.0.0.0 Mobile/15E148 Safari/604.1",
  firefoxIphone:
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) FxiOS/126.0 Mobile/15E148 Safari/605.1.15",
  ipad:
    "Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
} as const;

const CATCH_ALL_SENTENCE = "Ak to v ponuke nevidíš";
const GENERIC_TITLE = "Inštalácia z ponuky prehliadača";

function hasCatchAllSentence(guide: InstallGuide): boolean {
  return guide.steps.some((step) => step.includes(CATCH_ALL_SENTENCE));
}

describe("seven guides", () => {
  it.each([
    ["Chrome on desktop", UA.chromeWindows, "Inštalácia v prehliadači Chrome"],
    ["Edge on desktop", UA.edgeWindows, "Inštalácia v prehliadači Edge"],
    ["Chrome on Android", UA.chromeAndroid, "Inštalácia na Androide"],
    ["Safari on Mac", UA.safariMac, "Inštalácia v Safari na Macu"],
    ["Safari on iPhone", UA.safariIphone, "Inštalácia na iPhone alebo iPade"],
    ["Firefox on desktop", UA.firefoxWindows, "Firefox na počítači inštaláciu nepodporuje"],
    ["unknown browser", "Unknown/1.0", GENERIC_TITLE],
  ])("%s gets its own guide and never an empty one", (_name, ua, title) => {
    const guide = guideFor(ua);

    expect(guide.title).toBe(title);
    expect(guide.steps.length).toBeGreaterThan(0);
    for (const step of guide.steps) expect(step.trim().length).toBeGreaterThan(0);
  });
});

describe("detection order - the strings contain each other", () => {
  it("Edge gets the Edge guide although it also identifies as Chrome", () => {
    expect(UA.edgeWindows).toContain("Chrome");

    expect(guideFor(UA.edgeWindows).title).toBe("Inštalácia v prehliadači Edge");
  });

  it("iPhone gets the iOS guide even when the browser identifies as Chrome or Firefox", () => {
    // On iPhone it is always Safari underneath, whatever the browser is called.
    for (const ua of [UA.chromeIphone, UA.firefoxIphone, UA.ipad]) {
      expect(guideFor(ua).title).toBe("Inštalácia na iPhone alebo iPade");
    }
  });

  it("Chrome gets the Chrome guide although its string carries \"Safari\"", () => {
    expect(UA.chromeWindows).toContain("Safari");

    expect(guideFor(UA.chromeWindows).title).toBe("Inštalácia v prehliadači Chrome");
  });

  it("every browser on Android gets the Android guide", () => {
    for (const ua of [
      UA.chromeAndroid,
      UA.edgeAndroid,
      UA.samsungAndroid,
      UA.firefoxAndroid,
    ]) {
      expect(guideFor(ua).title).toBe("Inštalácia na Androide");
    }
  });
});

describe("Firefox - the only \"not possible\" in the whole catalogue", () => {
  it("on desktop it tells the truth AND an alternative, not a bare \"not possible\"", () => {
    for (const ua of [UA.firefoxWindows, UA.firefoxLinux]) {
      const guide = guideFor(ua);

      expect(guide.impossible).toBe(true);
      expect(guide.steps.join(" ")).toContain("Chrome alebo Edge");
    }
  });

  it("on a PHONE it must not get \"not possible\" - the app can be pinned there", () => {
    // The only place in the catalogue where a wrong order would lead to a FALSEHOOD, not
    // just an imprecise guide: it would send a phone user to another browser for nothing.
    const guide = guideFor(UA.firefoxAndroid);

    expect(guide.impossible).toBeUndefined();
    expect(guide.title).toBe("Inštalácia na Androide");
  });
});

describe("no step poses as a certainty (v1.2.1)", () => {
  it("Chrome on desktop does NOT promise the menu item - it states it conditionally", () => {
    // That item appears in the Chrome menu ONLY when the browser offers installation. A
    // person opened the menu following our guide and the item was not there - the guide
    // promised a door that is locked exactly when the person needs it.
    for (const ua of [UA.chromeWindows, UA.chromeLinux]) {
      const guide = guideFor(ua);
      const steps = guide.steps.join(" ");

      expect(steps).not.toContain("Klikni na Inštalovať stránku ako aplikáciu");
      expect(steps).toMatch(/[Aa]k .{0,60}vidíš/);
    }
  });

  it("the catch-all sentence admits the item may not be there AT ALL", () => {
    // Before v1.2.0 it read "look for Install or Add to home screen" - which is again just
    // another way of claiming it is somewhere.
    const guide = guideFor(UA.chromeWindows);

    expect(guide.steps.join(" ")).toContain("neponúka");
  });
});

describe("never empty, never the app name", () => {
  it("an unknown string - including the one from the test environment - gives the generic guide", () => {
    for (const ua of [navigator.userAgent, "", "Unknown/1.0", "curl/8.0"]) {
      const guide = guideFor(ua);

      expect(guide.title).toBe(GENERIC_TITLE);
      expect(guide.steps.length).toBeGreaterThan(0);
    }
  });

  it("the catch-all sentence is everywhere except the generic guide and desktop Firefox", () => {
    for (const [name, ua] of Object.entries(UA)) {
      const guide = guideFor(ua);
      const shouldHave = guide.title !== GENERIC_TITLE && !guide.impossible;

      expect(hasCatchAllSentence(guide), `${name}`).toBe(shouldHave);
    }
    expect(hasCatchAllSentence(guideFor("Unknown/1.0"))).toBe(false);
  });

  it("no guide mentions the app name - the catalogue belongs to the whole family", () => {
    for (const ua of [...Object.values(UA), "", "Unknown/1.0"]) {
      const guide = guideFor(ua);

      expect(`${guide.title} ${guide.steps.join(" ")}`).not.toMatch(/NEX|Studio|Visual/);
    }
  });
});
