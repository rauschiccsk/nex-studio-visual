/**
 * What the cockpit's screens say to a person — string literals, template text and JSX text parsed out of every source
 * file under src/ (tests and generated files left out). Comments are the code's own notes. Read by the rules on the
 * names the screens use (DEV-344 the Staviteľ, DEV-340 the Manažér).
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

export const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "..");

export function sources(dir: string = SRC): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === "__tests__" ? [] : sources(path);
    return /\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) && !name.endsWith(".generated.ts") ? [path] : [];
  });
}

/** Every text a source file says to a person. */
export function texts(file: string): string[] {
  const source = ts.createSourceFile(file, readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true);
  const found: string[] = [];
  const visit = (node: ts.Node) => {
    if (
      ts.isStringLiteral(node) ||
      ts.isNoSubstitutionTemplateLiteral(node) ||
      ts.isTemplateHead(node) ||
      ts.isTemplateMiddle(node) ||
      ts.isTemplateTail(node) ||
      ts.isJsxText(node)
    ) {
      found.push(node.text);
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return found;
}

/** Each text of the screens the pattern finds, as `file: text`. */
export function textsSaying(pattern: RegExp): string[] {
  return sources().flatMap((file) =>
    texts(file)
      .filter((text) => pattern.test(text))
      .map((text) => `${relative(SRC, file)}: ${text.trim().slice(0, 80)}`),
  );
}
