/**
 * DEV-155 — every route of the backend has a caller: a screen of the cockpit, Dedo's door, or a caller its own
 * definition names („Called by: …“ in the route's docstring, which the generated client carries).
 *
 * Director 20.10.2026: „hĺbkovú kontrolu mrtvých súborov a kódu, všetko to, čo projekt obsahuje a vôbec sa nepoužíva.“
 * 22.10.2026 the cockpit carried 40 routes nobody called — the CRUD of the first version's Implementer for bugs, epics,
 * feats and tasks, resets, RAG listings, single-item reads no screen makes. A route nobody calls is code to keep, test
 * and secure for nothing; this turns red on the commit that adds one. The routes come from the generated client (CI
 * keeps it in step with the backend); the calls are read with the TypeScript parser, not with a pattern over text.
 */
import { describe, expect, it } from "vitest";
import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";
import ts from "typescript";

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const GENERATED = join(SRC, "services/api/pipeline.generated.ts");
const METHODS = new Set(["get", "post", "put", "patch", "delete"]);
/** Dedo's door — its routes are the charter's, guarded by the backend's TestNothingBeyondTheCharter. */
const DEDO_DOOR = "/api/v1/dedo/";
const CALLED_BY = "Called by:";
const ANY = "*";

interface Route {
  method: string;
  path: string;
  calledBy: boolean;
}

interface Call {
  method: string;
  path: string;
}

/** Every operation of the generated client's ``paths`` — and whether its definition names an outside caller. */
function routesOf(text: string): Route[] {
  const file = ts.createSourceFile("generated.ts", text, ts.ScriptTarget.Latest, true);
  const routes: Route[] = [];
  file.forEachChild((node) => {
    if (!ts.isInterfaceDeclaration(node) || node.name.text !== "paths") return;
    for (const item of node.members) {
      if (!ts.isPropertySignature(item) || !item.name || !ts.isStringLiteral(item.name)) continue;
      if (!item.type || !ts.isTypeLiteralNode(item.type)) continue;
      for (const op of item.type.members) {
        if (!ts.isPropertySignature(op) || op.questionToken || !ts.isIdentifier(op.name)) continue;
        if (!METHODS.has(op.name.text)) continue;
        const doc = text.slice(op.getFullStart(), op.getStart(file));
        routes.push({ method: op.name.text.toUpperCase(), path: item.name.text, calledBy: doc.includes(CALLED_BY) });
      }
    }
  });
  return routes;
}

/** A literal as a path pattern: ``${…}`` becomes ``{}``, the query goes, a leading ``/api/v1`` goes. */
function pattern(node: ts.Node): string | null {
  let text: string;
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) text = node.text;
  else if (ts.isTemplateExpression(node)) text = node.head.text + node.templateSpans.map((s) => `{}${s.literal.text}`).join("");
  else return null;
  const path = text.split("?")[0]!.replace(/^(?:\{\})?\/api\/v1(?=\/)/, "");
  const startsLikeAPath = path.startsWith("/") || path.startsWith("{}/");
  return startsLikeAPath && path.split("/").some((s) => s && s !== "{}") ? path : null;
}

/** Every path a source file names: with its method when it is the first argument of ``x.get(…)`` & co., else any. */
function callsIn(name: string, text: string): Call[] {
  const kind = name.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const file = ts.createSourceFile(name, text, ts.ScriptTarget.Latest, true, kind);
  const calls: Call[] = [];
  const visit = (node: ts.Node) => {
    const path = pattern(node);
    if (path) {
      const call = node.parent;
      const verb =
        call && ts.isCallExpression(call) && call.arguments[0] === node && ts.isPropertyAccessExpression(call.expression)
          ? call.expression.name.text
          : null;
      calls.push({ method: verb && METHODS.has(verb) ? verb.toUpperCase() : ANY, path });
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return calls;
}

/** ``/projects/{slug}/billing`` against ``/projects/{}/billing``; a pattern that starts with ``{}/`` is a helper's tail. */
function reaches(route: string, path: string): boolean {
  let want = route.replace(/^\/api\/v1(?=\/)/, "").split("/").filter(Boolean);
  let got = path.split("/").filter(Boolean);
  if (path.startsWith("{}/")) {
    got = got.slice(1);
    if (got.length > want.length) return false;
    want = want.slice(want.length - got.length);
  }
  if (want.length !== got.length) return false;
  return want.every((segment, i) => segment === got[i] || (got[i] === "{}" && segment.startsWith("{")));
}

function orphans(routes: Route[], calls: Call[]): string[] {
  return routes
    .filter((r) => !r.path.startsWith(DEDO_DOOR) && !r.calledBy)
    .filter((r) => !calls.some((c) => (c.method === ANY || c.method === r.method) && reaches(r.path, c.path)))
    .map((r) => `${r.method} ${r.path}`);
}

function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === "__tests__" ? [] : sources(path);
    const code = /\.(ts|tsx)$/.test(entry.name) && !entry.name.endsWith(".d.ts");
    return code && path !== GENERATED ? [path] : [];
  });
}

describe("DEV-155 — every backend route has a caller", () => {
  it("no route of the backend is left without a caller", () => {
    const calls = sources(SRC).flatMap((f) => callsIn(f, readFileSync(f, "utf8")));
    const routes = routesOf(readFileSync(GENERATED, "utf8"));

    expect(routes.length).toBeGreaterThan(100);
    expect(orphans(routes, calls)).toEqual([]);
  });

  it("the check finds a route nobody calls and leaves the called ones", () => {
    const routes = routesOf(`export interface paths {
      "/api/v1/items/{item_id}": { get: operations["a"]; delete: operations["b"]; post?: never; };
      "/api/v1/items/{item_id}/files": { get: operations["c"]; };
      "/api/v1/exports/{export_id}": { get: operations["d"]; };
      "/api/v1/reports": {
        /** @description Served into an iframe. Called by: the board, which hands out this URL. */
        get: operations["e"];
      };
      "/api/v1/dedo/waiting": { get: operations["f"]; };
    }`);
    const calls = callsIn(
      "screen.tsx",
      "// don't be fooled by a quote\n" +
        "api.get<X>(`/api/v1/items/${id}?full=1`);\n" + // in the call: GET only
        "const csv = `/exports/${id}`;\n" + // built aside: any method
        "downloadFile(`${base(id)}/files`, 'f');", // a helper's tail
    );

    expect(routes.map((r) => `${r.method} ${r.path}`)).toEqual([
      "GET /api/v1/items/{item_id}",
      "DELETE /api/v1/items/{item_id}",
      "GET /api/v1/items/{item_id}/files",
      "GET /api/v1/exports/{export_id}",
      "GET /api/v1/reports",
      "GET /api/v1/dedo/waiting",
    ]);
    expect(orphans(routes, calls)).toEqual(["DELETE /api/v1/items/{item_id}"]);
  });
});
