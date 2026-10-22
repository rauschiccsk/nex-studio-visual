import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

/**
 * Vitest configuration for NEX Studio frontend tests.
 *
 * All frontend tests live under ``src/__tests__/``.
 * The ``@/`` alias resolves to ``src/`` to match the Vite dev config.
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    include: ["src/__tests__/**/*.test.{ts,tsx}"],
    setupFiles: ["src/__tests__/setup.ts"],
    // A screen test waits up to 5 s for what it expects (setup.ts); the test itself gets more, so a missing text is
    // reported as what was not found — not as a timeout of the whole test (DEV-150).
    testTimeout: 15000,
  },
});
