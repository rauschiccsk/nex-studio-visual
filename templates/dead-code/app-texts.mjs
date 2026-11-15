// DEV-395 — the texts check of a project whose app speaks more than one language (backend/services/app_languages.py).
//
//   node app-texts.mjs <check-texts.mjs> <project frontend dir> <languages, main first> <max source bytes>
//
// It only PARSES the project's files with the TypeScript parser installed beside this file — nothing of the project
// runs. The project's src/ is first copied into a folder of its own, regular files only: a link in the checkout never
// leads the reading anywhere else on this server. Then:
//   * every catalog src/locales/<language>.ts is read — the texts of the main one are the keys; a key another catalog
//     lacks is a missing translation, a key only another catalog has is one the main catalog never gave;
//   * the kit's own checker (bin/check-texts.mjs of the release the project builds with — the rule of the app's own
//     src/test/texts.test.ts) lists the texts written into the code instead of the catalogs.
// Everything goes to stdout as one JSON document: {missing, extra, unreadable, hard}.
import { lstatSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, relative } from "node:path";
import { pathToFileURL } from "node:url";
import ts from "typescript";

const [checkerPath, frontendDir, languageList, maxBytesArg] = process.argv.slice(2);
const languages = languageList.split(",");
const maxBytes = Number(maxBytesArg);

let copied = 0;
function copyRegular(from, to) {
  mkdirSync(to, { recursive: true });
  for (const name of readdirSync(from)) {
    if (name === "node_modules") continue;
    const source = join(from, name);
    const info = lstatSync(source);
    if (info.isDirectory()) copyRegular(source, join(to, name));
    else if (info.isFile() && /\.tsx?$/.test(name)) {
      copied += info.size;
      if (copied > maxBytes) throw new Error(`the sources exceed ${maxBytes} bytes`);
      writeFileSync(join(to, name), readFileSync(source));
    }
  }
}

function unwrap(node) {
  while (node && (ts.isSatisfiesExpression(node) || ts.isAsExpression(node) || ts.isParenthesizedExpression(node) || ts.isTypeAssertionExpression(node))) {
    node = node.expression;
  }
  return node;
}

/** The keys of the catalog `code` — or the reason they cannot be read. */
function catalogKeys(path, code) {
  let text;
  try {
    text = readFileSync(path, "utf8");
  } catch {
    return { missingFile: true };
  }
  const file = ts.createSourceFile(path, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  for (const statement of file.statements) {
    if (!ts.isVariableStatement(statement)) continue;
    for (const declaration of statement.declarationList.declarations) {
      if (!ts.isIdentifier(declaration.name) || declaration.name.text !== code) continue;
      const object = unwrap(declaration.initializer);
      if (!object || !ts.isObjectLiteralExpression(object)) return { why: `\`${code}\` nie je zoznam textov v tvare { "kľúč": "text" }` };
      const keys = [];
      for (const property of object.properties) {
        if (ts.isPropertyAssignment(property) && (ts.isStringLiteral(property.name) || ts.isIdentifier(property.name) || ts.isNoSubstitutionTemplateLiteral(property.name) || ts.isNumericLiteral(property.name))) {
          keys.push(property.name.text);
        } else {
          return { why: `text na riadku ${file.getLineAndCharacterOfPosition(property.getStart()).line + 1} nie je v tvare "kľúč": text` };
        }
      }
      return { keys };
    }
  }
  return { why: `súbor nemá \`export const ${code} = { … }\`` };
}

const work = mkdtempSync(join(tmpdir(), "app-texts-"));
try {
  const src = join(work, "src");
  copyRegular(join(frontendDir, "src"), src);
  const report = { missing: {}, extra: {}, unreadable: {}, hard: [] };
  const catalogs = Object.fromEntries(languages.map((code) => [code, catalogKeys(join(src, "locales", `${code}.ts`), code)]));
  for (const [code, read] of Object.entries(catalogs)) if (read.why) report.unreadable[code] = read.why;
  const main = catalogs[languages[0]];
  if (main.keys) {
    for (const code of languages.slice(1)) {
      const other = catalogs[code];
      if (!other.keys) continue;
      const missing = main.keys.filter((k) => !other.keys.includes(k));
      const extra = other.keys.filter((k) => !main.keys.includes(k));
      if (missing.length) report.missing[code] = missing;
      if (extra.length) report.extra[code] = extra;
    }
  }
  const kit = await import(pathToFileURL(checkerPath).href);
  for (const path of kit.sourcesUnder(src)) {
    for (const [line, what, text] of kit.hardTexts(path, readFileSync(path, "utf8"))) {
      report.hard.push(`frontend/${relative(work, path)}:${line} (${what}): ${String(text).slice(0, 80)}`);
    }
  }
  process.stdout.write(JSON.stringify(report));
} finally {
  rmSync(work, { recursive: true, force: true });
}
