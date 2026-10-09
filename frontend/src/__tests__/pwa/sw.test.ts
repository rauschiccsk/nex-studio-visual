import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * DEV-21 — the live service worker, the main safeguard of the whole installable app (ported from NEX Manager).
 *
 * A service worker is the mechanism that can keep serving an old version for days. This one does not, because it
 * never stores the app — but that is a property of the CODE, and code changes. One extra line a year from now
 * („and cache this too") would silently bring back exactly the harm the NEX Manager brief warns about; the cockpit
 * is deployed several times a day, so here it would show the same day.
 *
 * `sw.js` is a classic service-worker script and cannot be imported, so it runs in a controlled environment with a
 * stand-in `self`, `caches` and `fetch`, and the test watches what it really does.
 */

const SW_CODE = readFileSync(resolve(__dirname, "../../../public/sw.js"), "utf-8");

type Listener = (e: unknown) => void;

interface Env {
  listeners: Record<string, Listener>;
  openedCaches: string[];
  stored: string[];
  deleted: string[];
  matchCalls: string[];
  order: string[];
  storedAtRuntime: string[];
  skipWaiting: ReturnType<typeof vi.fn>;
  claim: ReturnType<typeof vi.fn>;
  cacheMatchReturns: Response | undefined;
}

/** Run `sw.js` with a stand-in environment and return what happened in it. */
function runSw(options?: { location?: string; cacheKeys?: string[]; networkAnswers?: string }): Env {
  const env: Env = {
    listeners: {},
    openedCaches: [],
    stored: [],
    deleted: [],
    matchCalls: [],
    order: [],
    storedAtRuntime: [],
    skipWaiting: vi.fn(),
    claim: vi.fn(),
    cacheMatchReturns: { ok: true, body: "offline" } as unknown as Response,
  };

  const self = {
    location: options?.location ?? "https://example.sk/sw.js?v=ABC123",
    addEventListener: (type: string, fn: Listener) => {
      env.listeners[type] = fn;
    },
    skipWaiting: env.skipWaiting,
    clients: { claim: env.claim },
  };

  const caches = {
    open: async (name: string) => {
      env.openedCaches.push(name);
      return {
        addAll: async (list: string[]) => void env.stored.push(...list),
        // If someone added caching of responses on the way, the app would start being stored through the back
        // door — `addAll` would never know.
        put: async (request: { url: string }) => void env.storedAtRuntime.push(request.url),
      };
    },
    keys: async () => options?.cacheKeys ?? [],
    delete: async (name: string) => void env.deleted.push(name),
    match: async (path: string) => {
      env.matchCalls.push(path);
      env.order.push("cache");
      return env.cacheMatchReturns;
    },
  };

  const fetchMock = vi.fn(async () => {
    env.order.push("network");
    if (options?.networkAnswers !== undefined) {
      return { fromNetwork: options.networkAnswers } as unknown as Response;
    }
    throw new Error("no network");
  });

  new Function("self", "caches", "fetch", "URL", "Response", SW_CODE)(
    self,
    caches,
    fetchMock,
    URL,
    class {
      constructor(
        public body: string,
        public init?: ResponseInit,
      ) {}
    },
  );
  return env;
}

/** Fire an event and wait for what it asked for through waitUntil / respondWith. */
async function fire(env: Env, type: string, event: Record<string, unknown>) {
  let awaited: Promise<unknown> | undefined;
  const e = {
    ...event,
    waitUntil: (pr: Promise<unknown>) => void (awaited = pr),
    respondWith: (pr: Promise<unknown>) => void (awaited = pr),
  };
  env.listeners[type]?.(e);
  return { e, result: awaited ? await awaited : undefined, responded: awaited !== undefined };
}

let env: Env;
beforeEach(() => {
  env = runSw();
});

describe("the service worker does NOT store the cockpit", () => {
  it("stores ONLY the offline notice and the mark — nothing of the app", async () => {
    await fire(env, "install", {});

    expect(env.stored).toEqual(["/offline.html", "/icon.svg", "/icon-192.png"]);
  });

  it("the stored list holds neither the start page nor the bundle", async () => {
    // Through those two an old version would come back. They must never be there.
    await fire(env, "install", {});

    expect(env.stored).not.toContain("/index.html");
    expect(env.stored).not.toContain("/");
    expect(env.stored.some((c) => c.startsWith("/assets"))).toBe(false);
  });

  it("takes control right after installing", async () => {
    await fire(env, "install", {});
    expect(env.skipWaiting).toHaveBeenCalled();

    await fire(env, "activate", {});
    expect(env.claim).toHaveBeenCalled();
  });
});

describe("the service worker stands in the way of nothing but navigation", () => {
  it.each(["cors", "no-cors", "same-origin"])("a %s request passes UNTOUCHED", async (mode) => {
    // The bundle, the API and images must follow the server's headers. If the service worker served them, it
    // could serve stale ones.
    const { responded } = await fire(env, "fetch", { request: { mode, url: "/assets/a.js" } });

    expect(responded).toBe(false);
    expect(env.matchCalls).toEqual([]);
  });

  it("a navigation is handled", async () => {
    const { responded } = await fire(env, "fetch", { request: { mode: "navigate", url: "/" } });
    expect(responded).toBe(true);
  });
});

describe("the cache is touched only AFTER the network fails", () => {
  it("the order is always network first, cache after", async () => {
    await fire(env, "fetch", { request: { mode: "navigate", url: "/" } });

    // The other way round, the cockpit would serve a stored page even with a working network.
    expect(env.order[0]).toBe("network");
    expect(env.order).toEqual(["network", "cache"]);
  });

  it("when the network ANSWERS, the cache is not touched AT ALL", async () => {
    // The negative twin of the order: „network first" alone would pass something that still reaches into the
    // cache after a successful network and serves what is stored.
    const q = runSw({ networkAnswers: "live page from the server" });

    const { result } = await fire(q, "fetch", { request: { mode: "navigate", url: "/" } });

    expect(q.matchCalls).toEqual([]);
    expect(q.order).toEqual(["network"]);
    expect((result as { fromNetwork: string }).fromNetwork).toBe("live page from the server");
  });

  it("after a successful answer it stores NOTHING", async () => {
    // Caching on the way would put the cockpit into storage through the back door — and an old version would have
    // somewhere to come from.
    const q = runSw({ networkAnswers: "page" });

    await fire(q, "fetch", { request: { mode: "navigate", url: "/" } });

    expect(q.storedAtRuntime).toEqual([]);
    expect(q.openedCaches).toEqual([]);
  });

  it("when the network fails it returns the offline notice", async () => {
    const { result } = await fire(env, "fetch", { request: { mode: "navigate", url: "/" } });

    expect(env.matchCalls).toEqual(["/offline.html"]);
    expect(result).toBeDefined();
  });

  it("when the stored notice is missing it returns a readable sentence — not nothing", async () => {
    env.cacheMatchReturns = undefined;

    const { result } = await fire(env, "fetch", { request: { mode: "navigate", url: "/" } });

    expect(result).toBeDefined();
    expect((result as { body: string }).body).toContain("Nie ste pripojení");
  });
});

describe("the storage is tied to the build fingerprint", () => {
  it("the storage name carries the fingerprint from the registration address", async () => {
    await fire(env, "install", {});
    expect(env.openedCaches).toEqual(["nex-studio-visual-shell-ABC123"]);
  });

  it("old storages are deleted on taking control, the current one stays", async () => {
    // Without it an old offline notice would stay in the browser forever.
    const q = runSw({
      location: "https://example.sk/sw.js?v=NEW",
      cacheKeys: ["nex-studio-visual-shell-OLD", "nex-studio-visual-shell-NEW", "something-else"],
    });

    await fire(q, "activate", {});

    expect(q.deleted).toContain("nex-studio-visual-shell-OLD");
    expect(q.deleted).not.toContain("nex-studio-visual-shell-NEW");
  });
});
