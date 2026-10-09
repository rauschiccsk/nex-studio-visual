import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  COUNT_KEY,
  GUARD_KEY,
  MAX_AUTO_RELOADS,
  clearGuardState,
  decide,
  readGuardState,
  recordAutoReload,
  type VersionInput,
} from "@/pwa/versionCheck";

/**
 * Deciding about a newer version and the state of its guard.
 *
 * The guard has two layers and the ceiling overrides. The tests below stand mainly on
 * the ALTERNATING-versions scenario - the one the ceiling exists for: when the server
 * serves two versions at once, the memory of the "last fingerprint" alone is used up on
 * every alternation and the app would reload forever.
 */

beforeEach(() => sessionStorage.clear());
afterEach(() => vi.restoreAllMocks());

const base: VersionInput = {
  localBuildId: "A",
  serverBuildId: "B",
  guard: null,
  reloadCount: 0,
  phase: "startup",
};

describe("the decision table", () => {
  it("server unreachable -> unknown (startup is not blocked)", () => {
    expect(decide({ ...base, serverBuildId: null })).toBe("unknown");
  });
  it("fingerprints match -> current", () => {
    expect(decide({ ...base, serverBuildId: "A" })).toBe("current");
  });
  it("startup, ceiling used up -> offer (the ceiling overrides)", () => {
    expect(decide({ ...base, reloadCount: MAX_AUTO_RELOADS })).toBe("offer");
  });
  it("startup, other guard, ceiling free -> reload", () => {
    expect(decide({ ...base, guard: "C" })).toBe("reload");
  });
  it("startup, guard === server -> offer", () => {
    expect(decide({ ...base, guard: "B" })).toBe("offer");
  });
  it("during work -> offer always, even when everything is free", () => {
    expect(decide({ ...base, phase: "runtime" })).toBe("offer");
  });
});

/** Decision with ONLY the fingerprint layer - no ceiling. Serves as a contrast below. */
function withoutCeiling(server: string, guard: string | null): "reload" | "offer" {
  if (server === "A") return "offer";
  return guard === server ? "offer" : "reload";
}

/** Simulates repeated window startups against a server reporting a list of fingerprints. */
function simulate(serverFingerprints: string[], decider: "real" | "withoutCeiling" = "real") {
  let guard: string | null = null;
  let reloadCount = 0;
  const course: string[] = [];
  for (const server of serverFingerprints) {
    const v =
      decider === "real"
        ? decide({ localBuildId: "A", serverBuildId: server, guard, reloadCount, phase: "startup" })
        : withoutCeiling(server, guard);
    course.push(v);
    if (v === "reload") {
      guard = server;
      reloadCount += 1;
    }
  }
  return { course, reloadCount };
}

const ALTERNATION = ["B", "C", "B", "C", "B", "C", "B", "C"];

describe("alternating versions - the reason the ceiling exists", () => {
  it("two servers with different versions: it stops after at most two reloads", () => {
    const { course, reloadCount } = simulate(ALTERNATION);
    expect(reloadCount).toBeLessThanOrEqual(MAX_AUTO_RELOADS);
    expect(course.slice(MAX_AUTO_RELOADS)).toEqual(
      Array(ALTERNATION.length - MAX_AUTO_RELOADS).fill("offer"),
    );
  });

  it("the fingerprint layer alone would NOT kick in on alternation", () => {
    const { reloadCount } = simulate(ALTERNATION, "withoutCeiling");
    // The memory of the "last fingerprint" is used up on every alternation - it reloads ALWAYS.
    expect(reloadCount).toBe(ALTERNATION.length);
    // And that is exactly what the ceiling prevents:
    expect(simulate(ALTERNATION).reloadCount).toBeLessThanOrEqual(MAX_AUTO_RELOADS);
  });

  it("alternation A->B->A with the ceiling used up -> offer, the third reload does NOT happen", () => {
    // The guard holds "B", the server reports "A" - a different value than the guard
    // remembers. The fingerprint layer would allow another reload; only the ceiling stops it.
    expect(
      decide({
        localBuildId: "A-local",
        serverBuildId: "A",
        guard: "B",
        reloadCount: MAX_AUTO_RELOADS,
        phase: "startup",
      }),
    ).toBe("offer");
  });

  it("the same fingerprint over and over reloads only once - even without the ceiling", () => {
    expect(simulate(["B", "B", "B", "B"], "withoutCeiling").reloadCount).toBe(1);
  });

  it("a legitimate further version during the day reloads normally", () => {
    expect(simulate(["B"]).course).toEqual(["reload"]);
  });
});

describe("guard state", () => {
  it("an empty state reads as zero", () => {
    expect(readGuardState()).toEqual({ guard: null, reloadCount: 0 });
  });

  it("recording advances both layers", () => {
    expect(recordAutoReload("B")).toBe(true);
    expect(readGuardState()).toEqual({ guard: "B", reloadCount: 1 });
    expect(recordAutoReload("C")).toBe(true);
    expect(readGuardState()).toEqual({ guard: "C", reloadCount: 2 });
  });

  it("resetting erases both layers", () => {
    recordAutoReload("B");
    clearGuardState();
    expect(sessionStorage.getItem(GUARD_KEY)).toBeNull();
    expect(sessionStorage.getItem(COUNT_KEY)).toBeNull();
  });

  it("a corrupted counter is taken as zero, not as an error", () => {
    sessionStorage.setItem(COUNT_KEY, "garbage");
    expect(readGuardState().reloadCount).toBe(0);
  });

  it("WHEN THE STATE CANNOT BE SAVED, reloading must NOT be allowed", () => {
    // Without a saved counter the ceiling would never kick in and the app would reload
    // forever - which is a worse failure than the old version.
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("storage unavailable");
    });
    expect(recordAutoReload("B")).toBe(false);
  });

  it("when the state cannot even be read, it pretends to be empty (startup is not blocked)", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("storage unavailable");
    });
    expect(readGuardState()).toEqual({ guard: null, reloadCount: 0 });
    expect(() => clearGuardState()).not.toThrow();
  });

  it("a silent write failure is detected too", () => {
    // setItem throws nothing but stores nothing - without verification it would disable the ceiling.
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => undefined);
    expect(recordAutoReload("B")).toBe(false);
  });
});

describe("the app and the build must agree on the same file", () => {
  // The build produces it, the app looks for it - and each has the name written on its
  // own side. If they drifted apart, the app would get 404, pretend the server cannot
  // answer, and NEVER RECOGNISE A NEW VERSION. Silently, without a single error message.
  const frontendRoot = resolve(__dirname, "../../..");
  const viteConfig = readFileSync(resolve(frontendRoot, "vite.config.ts"), "utf-8");
  const hook = readFileSync(resolve(frontendRoot, "src/pwa/useVersionWatch.ts"), "utf-8");

  it("the build produces exactly the file the app looks for", () => {
    const produced = viteConfig.match(/fileName:\s*"([^"]+)"/)?.[1];
    const sought = hook.match(/versionUrl\s*=\s*"([^"]+)"/)?.[1];

    expect(produced, "vite.config.ts names no produced file").toBeDefined();
    expect(sought, "useVersionWatch.ts has no default URL").toBeDefined();
    expect(`/${produced}`).toBe(sought);
  });
});
