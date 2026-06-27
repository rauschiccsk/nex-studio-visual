import "@testing-library/jest-dom/vitest";
import { beforeEach } from "vitest";

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
