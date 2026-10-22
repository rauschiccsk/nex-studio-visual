import "@testing-library/jest-dom/vitest";
import { configure } from "@testing-library/react";
import { beforeEach } from "vitest";

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
