import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";
import { randomBytes } from "node:crypto";

// DEV-21: the build fingerprint — computed ONCE per build and written to TWO places: into the bundle
// (`__BUILD_ID__`) and into `/version.json`. One value written twice, so they cannot drift apart. VITE_APP_VERSION
// cannot serve: it comes from outside, and when nobody supplies it the new-version check would go blind unnoticed.
const buildId = `${new Date().toISOString().replace(/[-:.]/g, "").slice(0, 15)}-${randomBytes(3).toString("hex")}`;

export default defineConfig({
  define: { __BUILD_ID__: JSON.stringify(buildId) },
  plugins: [
    react(),
    // Tailwind v4 via the Vite plugin (E1 Phase A, CR-NS-047) — replaces the v3
    // postcss/tailwind.config setup; config now lives in src/index.css (@theme).
    tailwindcss(),
    {
      // DEV-21: `/version.json` — what an open window asks to learn that a newer cockpit is out.
      name: "nex-version-stamp",
      apply: "build",
      generateBundle(_options, bundle) {
        this.emitFile({
          type: "asset",
          fileName: "version.json",
          source: JSON.stringify({
            buildId,
            // For people only, to see what runs. Only buildId is ever compared.
            version: process.env.VITE_APP_VERSION ?? "0.0.0-dev",
            builtAt: new Date().toISOString(),
          }),
        });
        // Guard: the fingerprint MUST also be in the bundle, or the app would have nothing to compare with
        // /version.json and the new-version check would go blind in silence. Fail the build instead.
        const inBundle = Object.values(bundle).some((c) => c.type === "chunk" && c.code.includes(buildId));
        if (!inBundle) {
          this.error(
            `Build fingerprint (${buildId}) not found in the bundle — the app could not compare it with ` +
              "/version.json. Check `define: { __BUILD_ID__ }` and that BUILD_ID is really used in the code.",
          );
        }
      },
    },
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 9177,
    proxy: {
      "/api": {
        target: "http://localhost:9176",
        changeOrigin: true,
      },
    },
  },
});
