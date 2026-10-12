/**
 * DEV-62 — every page of the cockpit, read from the router itself (`src/App.tsx`).
 *
 * The guard asks "is any page left unchecked?", not "is the list complete?": a page added to the router is
 * checked the moment it exists. A redirect (`<Navigate>`) is no page of its own and is skipped; a path parameter
 * must have a value in {@link PARAMS}, otherwise the guard stops with the parameter's name instead of silently
 * leaving the page out.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { PROJECT, VERSION } from "./fixtures";

const APP = fileURLToPath(new URL("../src/App.tsx", import.meta.url));

/** The value every path parameter takes — the project and the version the fixtures describe. */
export const PARAMS: Record<string, string> = {
  slug: PROJECT.slug,
  versionId: VERSION.id,
};

export interface Page {
  /** The router's own path, e.g. `projects/:slug`. */
  pattern: string;
  /** The address the guard opens, parameters filled in. */
  url: string;
  /** Pages outside the signed-in frame (the login) are opened without a session. */
  signedIn: boolean;
}

const ROUTE = /<Route\b([^>]*?)element=\{\s*<(\w+)/gs;

export function pages(source: string = readFileSync(APP, "utf-8")): Page[] {
  const found: Page[] = [];
  for (const [, attrs = "", element = ""] of source.matchAll(ROUTE)) {
    if (element === "Navigate") continue;
    const path = /\bpath="([^"]*)"/.exec(attrs)?.[1];
    const index = /\bindex\b/.test(attrs);
    if (path === undefined && !index) continue; // the layout route that wraps the signed-in pages
    if (path === "*") continue;
    const pattern = index ? "/" : path!.startsWith("/") ? path! : `/${path}`;
    const url = pattern.replace(/:(\w+)/g, (_m, name: string) => {
      const value = PARAMS[name];
      if (value === undefined) {
        throw new Error(`the guard has no value for the parameter ":${name}" of ${pattern} — add it to PARAMS`);
      }
      return value;
    });
    found.push({ pattern, url, signedIn: pattern !== "/login" });
  }
  if (found.length === 0) throw new Error(`no route found in ${APP} — the guard would measure nothing`);
  return found;
}
