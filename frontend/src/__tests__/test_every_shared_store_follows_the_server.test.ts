/**
 * DEV-312 — no shared store of the cockpit keeps server data that another screen can change without following it.
 *
 * 05.11.2026 the signed-in person and the chosen project stayed old after a change made on another screen; both were
 * stores nobody had asked „who else changes this?“. The Director: „… prosím raz a navždy zabezpečiť, aby nič také už
 * viackrát sa nestalo ani v kokpite ani v našich aplikáciách.“ So every store answers it in its own code: it listens to
 * the server's changes (onServerChange of nex-shared), or it says in one line why it needs not —
 * „Kópia údajov zo servera: žiadna — …“ or „Kópia údajov zo servera: mení ich len toto úložisko — …“.
 * A new store without either fails here.
 */

import { describe, expect, it } from "vitest";

/** The cockpit's own code — every module under src/ but the tests, as text. */
const SOURCES = import.meta.glob<string>(["../**/*.{ts,tsx}", "!../__tests__/**", "!../**/*.test.{ts,tsx}"], {
  query: "?raw",
  import: "default",
  eager: true,
});
const ANSWERED = /\bonServerChange\s*\(|Kópia údajov zo servera: (žiadna|mení ich len toto úložisko) — \S/;

function stores(): [string, string][] {
  return Object.entries(SOURCES).filter(
    ([path, text]) => path.startsWith("../store/") || (/from\s+["']zustand["']/.test(text) && /\bcreate\s*[<(]/.test(text)),
  );
}

describe("every shared store of the cockpit (DEV-312)", () => {
  it("finds the stores — the signed-in person and the chosen project among them", () => {
    const paths = stores().map(([path]) => path);
    expect(paths).toContain("../store/authStore.ts");
    expect(paths).toContain("../store/activeContextStore.ts");
  });

  it("listens to the server's changes or says why it needs not", () => {
    const silent = stores()
      .filter(([, text]) => !ANSWERED.test(text))
      .map(([path]) => path);
    expect(silent).toEqual([]);
  });
});
