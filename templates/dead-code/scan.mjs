// DEV-156 — the TypeScript half of the dead-code check of a project's version (backend/services/dead_code.py).
//
// The cockpit runs this inside a sealed container of its own — no network, the project read-only, a ceiling on memory,
// processes and time — because knip reads a project's configuration by RUNNING it (vite.config.ts): it is foreign code.
// It never writes anything; everything it found goes to stdout as one JSON document.
//
//   node scan.mjs '{"trees": ["head", "base"], "packages": ["frontend"], "budget_ms": 270000}'
//
// What knip writes on its error stream comes from the project's configuration — it goes to this container's error
// stream (the cockpit's log), never into the report a person reads.
//
// For every tree (/work/<tree>) and every package of the project in it:
//   * knip — the cockpit's own configuration (knip.json beside this file), production mode: the tests do not count as
//     a use, and a project cannot switch the check off with a configuration of its own;
//   * the paths the package's code names — the calls of the backend's routes, read with the TypeScript parser (the
//     same reading as the cockpit's own guard, frontend/src/__tests__/test_every_backend_route_has_a_caller.test.ts).
import { spawnSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const HERE = dirname(fileURLToPath(import.meta.url));
const KNIP = join(HERE, "node_modules/knip/bin/knip.js");
const CONFIG = join(HERE, "knip.json");
const WORK = "/work";
/** How long one knip run may take at most (milliseconds); the job's budget is shared among the runs. */
const KNIP_CAP_MS = 120_000;
const METHODS = new Set(["get", "post", "put", "patch", "delete"]);
const ANY = "*";
/** Not where the application calls its backend: what a build left, what only the tests read, and the preview's
 * mock answers of the backend (src/preview/ — the cockpit's Vizuál harness), which name every route they play. */
const SKIPPED_DIRS = new Set([
  "node_modules",
  "dist",
  "build",
  "coverage",
  "__tests__",
  "test",
  "tests",
  "mocks",
  "e2e",
  "preview",
]);
const TEST_FILE = /\.(test|spec)\.[cm]?[jt]sx?$/;
const SOURCE_FILE = /\.[cm]?[jt]sx?$/;

function knip(dir, timeout) {
  const run = spawnSync(
    process.execPath,
    [
      KNIP,
      "--config",
      CONFIG,
      "--production",
      "--no-progress",
      "--no-exit-code",
      "--reporter",
      "json",
    ],
    { cwd: dir, encoding: "utf8", timeout, maxBuffer: 64 * 1024 * 1024 },
  );
  if (run.stderr) process.stderr.write(`${dir}: ${run.stderr.slice(-2000)}\n`);
  if (run.error)
    return {
      error:
        run.error.code === "ETIMEDOUT"
          ? `knip nedobehol do ${Math.round(timeout / 1000)} s`
          : "knip sa nedal spustiť",
    };
  if (run.status !== 0) return { error: `knip skončil kódom ${run.status}` };
  // The report is knip's last line — a project's configuration may print before it.
  const last = run.stdout
    .trim()
    .split("\n")
    .reverse()
    .find((line) => line.startsWith("{"));
  try {
    return { issues: JSON.parse(last ?? "").issues ?? [] };
  } catch {
    return { error: "knip nevrátil čitateľný výsledok" };
  }
}

/** A literal as a path pattern: ``${…}`` becomes ``{}``, the query goes; ``null`` when it does not look like a path. */
function pattern(node) {
  let text;
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node))
    text = node.text;
  else if (ts.isTemplateExpression(node))
    text =
      node.head.text +
      node.templateSpans.map((s) => `{}${s.literal.text}`).join("");
  else return null;
  // An address loses its scheme and host (`https://${host}/api/x` → `/api/x`), any its query.
  const address = /^(https?|wss?|\{\}):\/\//.test(text);
  const path = (
    address ? "/" + text.split("://")[1].split("/").slice(1).join("/") : text
  ).split("?")[0];
  const startsLikeAPath = path.startsWith("/") || path.startsWith("{}/");
  return startsLikeAPath && path.split("/").some((s) => s && s !== "{}")
    ? path
    : null;
}

/** Every path a source file names: with its method when it is the first argument of ``x.get(…)`` & co., else any. */
function callsIn(name, text) {
  const kind = name.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const file = ts.createSourceFile(
    name,
    text,
    ts.ScriptTarget.Latest,
    true,
    kind,
  );
  const calls = [];
  const visit = (node) => {
    const path = pattern(node);
    if (path) {
      const call = node.parent;
      const verb =
        call &&
        ts.isCallExpression(call) &&
        call.arguments[0] === node &&
        ts.isPropertyAccessExpression(call.expression)
          ? call.expression.name.text
          : null;
      calls.push({
        method: verb && METHODS.has(verb) ? verb.toUpperCase() : ANY,
        path,
      });
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return calls;
}

function sources(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory())
      return SKIPPED_DIRS.has(entry.name) ? [] : sources(path);
    return SOURCE_FILE.test(entry.name) &&
      !TEST_FILE.test(entry.name) &&
      !entry.name.endsWith(".d.ts")
      ? [path]
      : [];
  });
}

const job = JSON.parse(process.argv[2]);
const deadline = Date.now() + (job.budget_ms ?? KNIP_CAP_MS);
const runs = job.trees.flatMap((tree) =>
  job.packages.map((pkg) => join(WORK, tree, pkg)),
);
let left = runs.filter((dir) => existsSync(join(dir, "package.json"))).length;
const out = {};
for (const tree of job.trees) {
  out[tree] = {};
  for (const pkg of job.packages) {
    const dir = join(WORK, tree, pkg);
    if (!existsSync(join(dir, "package.json"))) continue;
    const share = Math.min(
      KNIP_CAP_MS,
      Math.max(1000, Math.floor((deadline - Date.now()) / Math.max(left, 1))),
    );
    left -= 1;
    const result = existsSync(join(dir, "node_modules", ".bin"))
      ? knip(dir, share)
      : { error: "balíky nie sú nainštalované (node_modules)" };
    const src = join(dir, "src");
    result.calls = existsSync(src)
      ? sources(src).flatMap((f) => callsIn(f, readFileSync(f, "utf8")))
      : [];
    out[tree][pkg] = result;
  }
}
process.stdout.write(JSON.stringify(out));
