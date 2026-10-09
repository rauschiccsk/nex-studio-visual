/// <reference types="vite/client" />

/** DEV-21: build fingerprint, stamped by vite.config.ts into the bundle and into /version.json (one value, two places). */
declare const __BUILD_ID__: string;

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string;
  readonly VITE_APP_VERSION: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
