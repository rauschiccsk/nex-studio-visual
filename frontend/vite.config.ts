import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

export default defineConfig({
  plugins: [
    react(),
    // Tailwind v4 via the Vite plugin (E1 Phase A, CR-NS-047) — replaces the v3
    // postcss/tailwind.config setup; config now lives in src/index.css (@theme).
    tailwindcss(),
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
