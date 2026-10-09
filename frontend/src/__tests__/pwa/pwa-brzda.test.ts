import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Emergency brake (`docs/pwa-brzda/sw.js`).
 *
 * It answers the biggest worry in the requirement: "the user has no way to get rid of
 * it - neither by restarting nor by signing out". The brake turns this around: WE can
 * get rid of it, for everyone at once, without anybody doing anything on their computer.
 *
 * We do everything so that it is never needed. If it is, it will be under pressure - and
 * then it must not turn out not to work. So it is tested like anything else.
 */

const BRAKE = readFileSync(resolve(__dirname, "../../../../docs/pwa-brzda/sw.js"), "utf-8");

interface Environment {
  listeners: Record<string, (e: unknown) => void>;
  deletedCaches: string[];
  unregistered: boolean;
  navigatedWindows: string[];
  skipWaiting: ReturnType<typeof vi.fn>;
}

function runBrake(options?: { cacheKeys?: string[]; openWindows?: string[] }): Environment {
  const env: Environment = {
    listeners: {},
    deletedCaches: [],
    unregistered: false,
    navigatedWindows: [],
    skipWaiting: vi.fn(),
  };

  const self = {
    addEventListener: (kind: string, fn: (e: unknown) => void) => {
      env.listeners[kind] = fn;
    },
    skipWaiting: env.skipWaiting,
    registration: {
      unregister: async () => {
        env.unregistered = true;
        return true;
      },
    },
    clients: {
      matchAll: async () =>
        (
          options?.openWindows ?? [
            "https://studio.example.com/",
            "https://studio.example.com/projects",
          ]
        ).map((url) => ({
          url,
          navigate: async (target: string) => void env.navigatedWindows.push(target),
        })),
    },
  };

  const caches = {
    keys: async () =>
      options?.cacheKeys ?? [
        "nex-studio-visual-shell-ABC",
        "nex-studio-visual-shell-DEF",
        "foreign",
      ],
    delete: async (name: string) => void env.deletedCaches.push(name),
  };

  new Function("self", "caches", BRAKE)(self, caches);
  return env;
}

async function fire(env: Environment, kind: string) {
  let pending: Promise<unknown> | undefined;
  env.listeners[kind]?.({ waitUntil: (p: Promise<unknown>) => void (pending = p) });
  if (pending) await pending;
}

let env: Environment;
beforeEach(() => {
  env = runBrake();
});

describe("emergency brake", () => {
  it("does not wait - it takes control at once, without waiting for windows to close", async () => {
    // If it waited, people would get it only after closing every window of the app.
    await fire(env, "install");

    expect(env.skipWaiting).toHaveBeenCalled();
  });

  it("deletes EVERYTHING the app cached - foreign entries too", async () => {
    await fire(env, "activate");

    expect(env.deletedCaches).toEqual([
      "nex-studio-visual-shell-ABC",
      "nex-studio-visual-shell-DEF",
      "foreign",
    ]);
  });

  it("UNREGISTERS ITSELF - the app runs as an ordinary page from then on", async () => {
    // This is the core of the whole brake. Without it, it would only tidy up and stay hanging.
    await fire(env, "activate");

    expect(env.unregistered).toBe(true);
  });

  it("reloads the open windows so it applies IMMEDIATELY", async () => {
    // Without it people would stay on the old state until a restart - exactly what the
    // requirement warns about.
    await fire(env, "activate");

    expect(env.navigatedWindows).toEqual([
      "https://studio.example.com/",
      "https://studio.example.com/projects",
    ]);
  });

  it("stores NOTHING - there is nothing to cache in the brake", () => {
    expect(BRAKE).not.toContain("caches.open");
    expect(BRAKE).not.toContain("addAll");
    expect(BRAKE).not.toContain("cache.put");
  });

  it("interferes with NOTHING - it has no request handler at all", () => {
    // Negative counterpart: a brake that still handled something would not be a retreat
    // to safety. Without a handler every request goes straight to the network.
    expect(env.listeners.fetch).toBeUndefined();
    expect(BRAKE).not.toContain('addEventListener("fetch"');
  });

  it("survives a step failing", async () => {
    // Under pressure it must not fall over because the browser forbids something.
    const q = runBrake({ openWindows: [] });
    await expect(fire(q, "activate")).resolves.toBeUndefined();
    expect(q.unregistered).toBe(true);
  });
});

describe("the brake can really be deployed", () => {
  it("is a valid file that can be copied over the live one", () => {
    // Under pressure nothing may be edited in - only copied and deployed.
    expect(() => new Function("self", "caches", BRAKE)).not.toThrow();
    expect(BRAKE).toContain('addEventListener("install"');
    expect(BRAKE).toContain('addEventListener("activate"');
  });
});

describe("the emergency procedure is not a dead document", () => {
  const ROOT = resolve(__dirname, "../../../..");
  const procedure = readFileSync(resolve(ROOT, "docs/pwa-nudzovy-postup.md"), "utf-8");

  it("points only at files that REALLY exist", () => {
    // A procedure that sends someone under pressure to a missing file is worse than none — then the guessing starts.
    const paths = [...procedure.matchAll(/`?((?:docs|frontend|scripts)\/[\w./-]+)`?/g)].map((m) => m[1] ?? "");
    expect(paths.length).toBeGreaterThan(0);
    for (const path of paths) {
      expect(
        readFileSync(resolve(ROOT, path), "utf-8").length,
        `the procedure points at ${path}, which is missing or empty`,
      ).toBeGreaterThan(0);
    }
  });

  it("the command that applies the brake targets the live service worker", () => {
    expect(procedure).toContain("cp docs/pwa-brzda/sw.js frontend/public/sw.js");
  });

  it("says what the brake COSTS, not only what it does", () => {
    // A retreat to safety must not pose as a free fix.
    expect(procedure).toMatch(/nedá nainštalovať/i);
    expect(procedure).toMatch(/Návrat späť/i);
  });

  it("says how to tell an old version from another fault", () => {
    expect(procedure).toMatch(/kolega/i); // comparing with a second person
    expect(procedure).toMatch(/[Čč]íslo verzie/);
  });
});

