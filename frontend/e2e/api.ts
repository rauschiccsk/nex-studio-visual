/**
 * DEV-62 — the cockpit's API answered from fixtures, so the guard renders every page in a real browser without a
 * backend and without anybody's account.
 *
 * A fixture is registered under the backend's own path (`/api/v1/projects/{project_id}`) and typed by the response
 * the OpenAPI contract declares for it, so a changed response shape fails `npm run type-check` instead of quietly
 * rendering an empty page. A call nobody answers is recorded: the guard fails on it, because a page fed with nothing
 * shows its empty state and the width check would measure nothing.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import type { Page, Route } from "@playwright/test";

import type { paths } from "../src/services/api/pipeline.generated";

type Method = "get" | "post" | "put" | "patch" | "delete";

/** The JSON body the contract declares for a successful answer of `method path`. */
export type Body<P extends keyof paths, M extends Method> = paths[P] extends Record<M, infer Op>
  ? Op extends { responses: infer R }
    ? R extends { 200: { content: { "application/json": infer B } } }
      ? B
      : R extends { 201: { content: { "application/json": infer B } } }
        ? B
        : never
    : never
  : never;

interface Answer {
  method: string;
  pattern: RegExp;
  path: string;
  body: (url: URL) => unknown;
}

const answers: Answer[] = [];

function compile(path: string): RegExp {
  const escaped = path.replace(/[.*+?^$()|[\]\\]/g, "\\$&").replace(/\{\w+\}/g, "[^/]+");
  return new RegExp(`^${escaped}/?$`);
}

/** Answer `method path` with `body` (a value, or a function of the requested URL). */
export function answer<P extends keyof paths, M extends Method>(
  method: M,
  path: P,
  body: Body<P, M> | ((url: URL) => Body<P, M>),
): void {
  answers.push({
    method: method.toUpperCase(),
    pattern: compile(path as string),
    path: path as string,
    body: typeof body === "function" ? (body as (url: URL) => unknown) : () => body,
  });
}

/**
 * Answer a path the contract does not type (it returns a bare dict). `T` is then the type the frontend itself reads
 * the answer as (e.g. `GitStatus`), so the fixture is still checked against its reader.
 */
export function answerUntyped<T>(method: Method, path: keyof paths, body: T): void {
  answers.push({ method: method.toUpperCase(), pattern: compile(path as string), path: path as string, body: () => body });
}

/** Every path of the backend's contract, read from the generated types (the same file the fixtures are typed by). */
const CONTRACT: { path: string; pattern: RegExp }[] = Array.from(
  readFileSync(fileURLToPath(new URL("../src/services/api/pipeline.generated.ts", import.meta.url)), "utf-8").matchAll(
    /^ {4}"(\/api\/[^"]+)": \{/gm,
  ),
  ([, path]) => ({ path: path!, pattern: compile(path!) }),
);

const literalLength = (path: string): number => path.replace(/\{\w+\}/g, "").length;

/**
 * The answer for a request — only the one registered under the contract path the backend routes it to. The most
 * literal path wins (`/projects/adoptable` before `/projects/{project_id}`): without that, a request to a path with
 * no fixture of its own would get the answer of a neighbouring path and the page would render the wrong data.
 */
function find(method: string, pathname: string): Answer | undefined {
  const target = CONTRACT.filter((c) => c.pattern.test(pathname)).sort(
    (a, b) => literalLength(b.path) - literalLength(a.path),
  )[0];
  if (!target) return undefined;
  return answers.find((a) => a.method === method && a.path === target.path);
}

/** Route every API call of `page` to the fixtures; returns the calls nobody answered. */
export async function serveApi(page: Page): Promise<string[]> {
  const unanswered: string[] = [];
  await page.route("**/api/**", async (route: Route) => {
    const request = route.request();
    const url = new URL(request.url());
    const found = find(request.method(), url.pathname);
    if (!found) {
      unanswered.push(`${request.method()} ${url.pathname}`);
      await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "no fixture" }) });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(found.body(url)) });
  });
  // The agent terminal talks over a WebSocket; nothing answers it, the page must still lay out.
  await page.routeWebSocket(/\/api\//, () => {});
  return unanswered;
}
