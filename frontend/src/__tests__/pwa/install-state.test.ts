import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { readInstallSignals } from "@/pwa/installPrompt";
import {
  decideInstallState,
  isInstallDone,
  type InstallSignals,
  type InstallState,
} from "@/pwa/installState";

/**
 * Deciding the state of the "Nainštalovať aplikáciu" button.
 *
 * The function is pure and there are six signals, so not a sample but ALL 64
 * combinations are walked. A table of cases alone is not enough: it would also pass an
 * implementation with the rules in the wrong order - and the order decides what the app
 * tells the person. Hence the negative counterparts and statements over the whole set.
 *
 * What these tests DO NOT prove: how the button looks and what a click does - that is for
 * the button tests; nor that the offer really appears in a browser (a person verifies that).
 */

const KEYS = [
  "isPreview",
  "isStandalone",
  "hasPrompt",
  "rememberedInstalled",
  "supportsPromptApi",
  "isInsecure",
] as const;

const STATES: InstallState[] = [
  "preview",
  "standalone",
  "ready",
  "installed",
  "unsupported",
  "insecure",
  "unavailable",
];

const SOURCE = resolve(__dirname, "../../pwa/installState.ts");

/** An ordinary browser window that knows installation but currently offers nothing. */
const BASELINE: InstallSignals = {
  isPreview: false,
  isStandalone: false,
  hasPrompt: false,
  rememberedInstalled: false,
  supportsPromptApi: true,
  isInsecure: false,
};

function signals(changes: Partial<InstallSignals>): InstallSignals {
  return { ...BASELINE, ...changes };
}

/** All 64 combinations of the six signals. */
function allCombinations(): InstallSignals[] {
  const out: InstallSignals[] = [];
  for (let mask = 0; mask < 1 << KEYS.length; mask++) {
    const s = {} as Record<string, boolean>;
    KEYS.forEach((key, i) => (s[key] = Boolean(mask & (1 << i))));
    out.push(s as unknown as InstallSignals);
  }
  return out;
}

/** Source without comments - for statements about CODE, not about explanations. */
function codeWithoutComments(): string {
  return readFileSync(SOURCE, "utf-8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\/\/.*$/gm, "");
}

describe("the state table", () => {
  it("1 - live preview is running -> preview", () => {
    expect(decideInstallState(signals({ isPreview: true }))).toBe("preview");
  });

  it("2 - the app runs in an installed window -> standalone", () => {
    expect(decideInstallState(signals({ isStandalone: true }))).toBe("standalone");
  });

  it("3 - the offer is captured -> ready", () => {
    expect(decideInstallState(signals({ hasPrompt: true }))).toBe("ready");
  });

  it("4 - we saw the installation finish -> installed", () => {
    expect(decideInstallState(signals({ rememberedInstalled: true }))).toBe("installed");
  });

  it("5 - the browser does not know this way -> unsupported", () => {
    expect(decideInstallState(signals({ supportsPromptApi: false }))).toBe("unsupported");
  });

  it("6 - the page is not on a secure address -> insecure", () => {
    expect(decideInstallState(signals({ isInsecure: true }))).toBe("insecure");
  });

  it("7 - none of the above -> unavailable", () => {
    expect(decideInstallState(signals({}))).toBe("unavailable");
  });
});

describe("negative counterparts - without them the table would pass a wrong order", () => {
  it("a captured offer NEVER yields 'unsupported' or 'installed'", () => {
    const s = signals({ hasPrompt: true, supportsPromptApi: false, rememberedInstalled: true });

    const state = decideInstallState(s);

    expect(state).toBe("ready");
    expect(state).not.toBe("unsupported");
    expect(state).not.toBe("installed");
  });

  it("in its own window installation is NOT offered, even when an offer is captured", () => {
    expect(decideInstallState(signals({ isStandalone: true, hasPrompt: true }))).toBe("standalone");
  });

  it("a fresh offer BEATS the remembered installation - memory is older than fact", () => {
    expect(decideInstallState(signals({ hasPrompt: true, rememberedInstalled: true }))).toBe("ready");
  });

  it("in preview 'ready' NEVER arises, even if an offer were captured", () => {
    expect(decideInstallState(signals({ isPreview: true, hasPrompt: true }))).toBe("preview");
  });

  it("for a browser without support its own sentence comes out, NOT the one about https", () => {
    // In desktop Firefox the advice "open it over https" would mislead: the app cannot
    // be installed there even over a secure address.
    const state = decideInstallState(signals({ supportsPromptApi: false, isInsecure: true }));

    expect(state).toBe("unsupported");
    expect(state).not.toBe("insecure");
  });
});

describe("the whole set of 64 combinations", () => {
  it("every combination returns exactly one of the seven states", () => {
    for (const s of allCombinations()) {
      expect(STATES).toContain(decideInstallState(s));
    }
  });

  it("every one of the seven states is reachable - none is dead", () => {
    // A branch moved to the wrong place becomes unreachable and the table of cases would
    // not reveal it on its own: each of its rows would keep passing.
    const reached = new Set(allCombinations().map(decideInstallState));

    for (const state of STATES) {
      expect(reached, `state '${state}' comes out of no combination`).toContain(state);
    }
  });

  it("preview overrides everything", () => {
    for (const s of allCombinations().filter((s) => s.isPreview)) {
      expect(decideInstallState(s)).toBe("preview");
    }
  });

  it("its own window overrides everything except preview", () => {
    for (const s of allCombinations().filter((s) => !s.isPreview && s.isStandalone)) {
      expect(decideInstallState(s)).toBe("standalone");
    }
  });

  it("'ready' is NEVER claimed without a captured offer", () => {
    for (const s of allCombinations().filter((s) => !s.hasPrompt)) {
      expect(decideInstallState(s)).not.toBe("ready");
    }
  });

  it("'insecure' is NEVER claimed when we do not know it", () => {
    // The core of the whole design: do not claim a reason we do not know.
    for (const s of allCombinations().filter((s) => !s.isInsecure)) {
      expect(decideInstallState(s)).not.toBe("insecure");
    }
  });

  it("certainty about the address is not dropped into the 'we do not know' bucket", () => {
    const affected = allCombinations().filter(
      (s) =>
        !s.isPreview &&
        !s.isStandalone &&
        !s.hasPrompt &&
        !s.rememberedInstalled &&
        s.supportsPromptApi &&
        s.isInsecure,
    );

    expect(affected.length).toBeGreaterThan(0);
    for (const s of affected) {
      expect(decideInstallState(s)).toBe("insecure");
    }
  });
});

describe("a finished action versus an action that is just not possible right now (v1.2.1)", () => {
  it("exactly two states are DONE: its own window and installed", () => {
    const done = STATES.filter(isInstallDone);

    expect(done).toEqual(["standalone", "installed"]);
  });

  it("in the other five the action is merely NOT POSSIBLE right now - nothing is hidden there", () => {
    // The difference the whole of v1.2.1 stands on: what is not possible right now gets
    // explained; what is done is not shown at all.
    for (const state of STATES.filter((s) => !isInstallDone(s))) {
      expect(isInstallDone(state), `state '${state}'`).toBe(false);
    }
    expect(STATES.filter((s) => !isInstallDone(s))).toHaveLength(5);
  });

  it("SAFETY NET: with a fresh offer a done state NEVER comes out, even with a remembered installation", () => {
    // Hiding the button is safe solely thanks to this: when the app is not installed, the
    // browser fires the offer again and the state stops being done. All combinations are
    // walked - a single exception would leave the person in a dead end.
    const withOffer = allCombinations().filter((s) => !s.isPreview && !s.isStandalone && s.hasPrompt);

    expect(withOffer.length).toBeGreaterThan(0);
    for (const s of withOffer) {
      expect(isInstallDone(decideInstallState(s))).toBe(false);
    }
  });
});

describe("portability into the shared kit", () => {
  it("the module has no dependency and does not touch the browser", () => {
    // Comments are stripped first: a check over the whole file would shout even when
    // someone merely MENTIONS the word "window" in an explanation - and a false alarm is
    // worse than no check.
    const code = codeWithoutComments();

    expect(code).not.toMatch(/^\s*import\s/m);
    for (const forbidden of ["window", "navigator", "document", "localStorage", "import.meta"]) {
      expect(code, `the module touches ${forbidden}`).not.toContain(forbidden);
    }
  });

  it("every decision branch has a sentence above it saying WHY it stands there", () => {
    // The order of branches is binding and the only thing that holds it in the code is
    // the justification. Without it someone reorders them in good faith during an edit.
    const lines = readFileSync(SOURCE, "utf-8").split("\n");
    const branches = lines
      .map((line, index) => ({ line, index }))
      .filter(({ line }) => /^ {2}(if \(s\.|if \(!s\.|return ")/.test(line));

    expect(branches).toHaveLength(7);
    for (const { line, index } of branches) {
      expect(
        lines[index - 1]!.trim().startsWith("//"),
        `without an explanation: ${line.trim()}`,
      ).toBe(true);
    }
  });
});

describe("from the browser to the state - what the app really knows about the window", () => {
  // The decision is pure, but the rule "do not claim a reason we do not know" can be
  // broken already when READING the signals. So the real reader (`readInstallSignals`)
  // goes together with the decision here: without it, swapping certainty for a plain
  // negation would pass unnoticed.

  function setProperty(key: string, value: unknown): () => void {
    Object.defineProperty(window, key, { configurable: true, value });
    return () => {
      delete (window as unknown as Record<string, unknown>)[key];
    };
  }

  it("when we know NOTHING about address security, the app does NOT claim insecurity", () => {
    // In this environment `isSecureContext` does not exist - i.e. "we do not know".
    expect((window as unknown as Record<string, unknown>).isSecureContext).toBeUndefined();

    expect(readInstallSignals().isInsecure).toBe(false);
    expect(decideInstallState(readInstallSignals())).not.toBe("insecure");
  });

  it("an explicit 'not secure' the app says aloud", () => {
    const cleanups = [setProperty("isSecureContext", false), setProperty("onbeforeinstallprompt", null)];
    try {
      expect(readInstallSignals().isInsecure).toBe(true);
      expect(decideInstallState(readInstallSignals())).toBe("insecure");
    } finally {
      cleanups.forEach((c) => c());
    }
  });

  it("missing detection of its own window crashes nothing and does not claim a window", () => {
    // `"matchMedia" in window` is often true even where the value is `undefined` - hence
    // support is checked by VALUE. Without it every test of the app would fail here.
    expect(typeof window.matchMedia).toBe("undefined");

    expect(() => readInstallSignals()).not.toThrow();
    expect(readInstallSignals().isStandalone).toBe(false);
  });

  it("running in its own window is recognised", () => {
    const cleanup = setProperty("matchMedia", (query: string) => ({
      matches: query.includes("standalone"),
    }));
    try {
      expect(decideInstallState(readInstallSignals())).toBe("standalone");
    } finally {
      cleanup();
    }
  });
});
