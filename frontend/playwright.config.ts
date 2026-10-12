/**
 * DEV-62 — the browser guard: the cockpit's production build (`npm run build` → `dist/`) served by `vite preview`
 * and opened in Chromium at three widths. Run with `npm run test:e2e` after a build; CI runs it in the job that
 * builds the frontend.
 */

import { defineConfig, devices } from "@playwright/test";

const PORT = 4317;

export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    serviceWorkers: "block",
    // E2E_SCREENSHOTS=1 keeps a picture of every page for a look by eye (test-results/), not only of failures.
    screenshot: process.env.E2E_SCREENSHOTS ? "on" : "only-on-failure",
  },
  projects: [
    {
      name: "mobil",
      use: { ...devices["Desktop Chrome"], viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true },
    },
    {
      name: "tablet",
      use: { ...devices["Desktop Chrome"], viewport: { width: 768, height: 1024 }, isMobile: true, hasTouch: true },
    },
    { name: "pocitac", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } } },
  ],
  webServer: {
    command: `npx vite preview --host 127.0.0.1 --port ${PORT} --strictPort`,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: !process.env.CI,
  },
});
