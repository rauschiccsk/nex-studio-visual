/**
 * Build fingerprint: the single value compared with what the server reports.
 *
 * It is produced on every build in `vite.config.ts` and written to two places at once:
 * into the bundle (here) and into `/version.json` (served by the web server). So these
 * are not two values to keep in sync; it is one value written twice.
 *
 * This is the only file in `pwa/` bound to its surroundings; when the module moves
 * into `nex-shared`, only this file gets replaced.
 */
export const BUILD_ID: string =
  typeof __BUILD_ID__ === "string" && __BUILD_ID__ ? __BUILD_ID__ : "dev";
