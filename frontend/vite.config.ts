import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteStaticCopy } from "vite-plugin-static-copy";
import path from "path";

export default defineConfig({
  plugins: [
    react(),
    // Copy the bundled Slovak Hunspell dictionary (dictionary-sk) into
    // /dictionaries/sk/ so SlovakTextarea can fetch it at runtime. The
    // upstream package has a restrictive ``exports`` map that blocks
    // direct subpath imports, hence the static-copy detour.
    viteStaticCopy({
      targets: [
        { src: "node_modules/dictionary-sk/index.aff", dest: "dictionaries/sk" },
        { src: "node_modules/dictionary-sk/index.dic", dest: "dictionaries/sk" },
      ],
    }),
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
