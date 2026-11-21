import "@testing-library/jest-dom/vitest";
import { configure } from "@testing-library/react";
import { afterEach, beforeEach } from "vitest";

// How long findBy… / waitFor wait for the screen to show what a test expects. Testing Library's 1 s is measured on a
// fast machine: 22.10.2026 GitHub's runner took 140 s for the run that took 76 s on the commit before, and a Poradca
// test whose text appeared after 1 068 ms failed though nothing was wrong (CI run 39829404278). A test that fails
// still fails — it only waits longer before saying so; what it asserts does not change.
configure({ asyncUtilTimeout: 5000 });

// Isolate persisted UI state (localStorage) between tests so a component that writes a preference
// (e.g. TaskPlanPanel's per-version expand/collapse set — CR-V2-023) cannot leak into the next test.
// Defensive: some suites stub `localStorage` with a partial object (no `clear`) — skip cleanly there.
beforeEach(() => {
  try {
    window.localStorage?.clear?.();
  } catch {
    /* storage unavailable / stubbed in this suite — nothing to isolate */
  }
});

// DEV-485: React's warning about two children with the same key fails the test that rendered them. 21.11.2026 the panel
// of the fast path got the same key as the panel above it on Nastavenia → Používatelia — both empty while the accounts
// loaded — and the browser kept an orphan copy of the first one on every tab. React said so in every run of the tests;
// nothing listened, because a warning does not fail a test.
const DUPLICATE_KEY = "Encountered two children with the same key";
const duplicateKeys: string[] = [];
const reportError = console.error.bind(console);
console.error = (...args: unknown[]) => {
  const text = args.map(String).join(" ");
  if (text.includes(DUPLICATE_KEY)) duplicateKeys.push(text.slice(0, 300));
  reportError(...args);
};
afterEach(() => {
  if (duplicateKeys.length === 0) return;
  const found = duplicateKeys.splice(0);
  throw new Error(`React rendered two children with the same key (DEV-485):\n${found.join("\n")}`);
});
