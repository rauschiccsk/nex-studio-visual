/**
 * DEV-88 — a file of the project named in the text of Poradca or of the agent becomes a link into NS Code, at the line
 * when the text names one (`backend/main.py:129`). Only a path NS Code shows becomes a link — the list comes from the
 * server, so a secret, an ignored file or a path that does not exist stays plain text.
 */

export interface CodePathHit {
  start: number;
  end: number;
  path: string;
  line: number | null;
}

/** The address NS Code opens a file at (DEV-86). */
export function nsCodeHref(path: string, line: number | null): string {
  const params = new URLSearchParams({ path });
  if (line) params.set("line", String(line));
  return `/ns-code?${params.toString()}`;
}

// A path: segments of letters, digits, `.`, `_`, `@`, `-`, separated by `/`; the last one does not end with a dot, so
// „pozri backend/main.py.“ ends the path before the full stop. Optionally the project's place on the server before it,
// and `:line` after it.
const CANDIDATE = /(\/opt\/projects\/([\w.-]+)\/)?((?:[\w@-][\w.@-]*\/)*[\w@-](?:[\w.@-]*[\w@-])?)(?::(\d+))?/g;

/** The files of the project the text names, as NS Code shows them. */
export function findCodePaths(text: string, shown: ReadonlySet<string>, projectSlug?: string): CodePathHit[] {
  const hits: CodePathHit[] = [];
  for (const m of text.matchAll(CANDIDATE)) {
    const [whole, prefix, slug, path, line] = m;
    if (!path || !shown.has(path)) continue;
    if (prefix && slug !== projectSlug) continue; // another project's file is not this project's NS Code
    // A longer word („xbackend/main.py“, an address) is matched whole and is not on the list — no check needed here.
    const start = m.index ?? 0;
    hits.push({ start, end: start + whole.length, path, line: line ? Number(line) : null });
  }
  return hits;
}

// The few shapes of the HTML tree react-markdown hands its plugins (hast) — named here so no type package is needed.
interface HastText {
  type: "text";
  value: string;
}
interface HastElement {
  type: "element";
  tagName: string;
  properties: Record<string, unknown>;
  children: HastNode[];
}
type HastNode = HastText | HastElement | { type: string; children?: HastNode[] };

/** Marks the links this plugin made — the renderer turns them into links inside the app, not page loads. */
export const CODE_LINK_ATTRIBUTE = "data-code-link";

function split(text: string, shown: ReadonlySet<string>, projectSlug?: string): HastNode[] {
  const hits = findCodePaths(text, shown, projectSlug);
  if (hits.length === 0) return [{ type: "text", value: text }];
  const out: HastNode[] = [];
  let at = 0;
  for (const hit of hits) {
    if (hit.start > at) out.push({ type: "text", value: text.slice(at, hit.start) });
    out.push({
      type: "element",
      tagName: "a",
      properties: { href: nsCodeHref(hit.path, hit.line), dataCodeLink: "" },
      children: [{ type: "text", value: text.slice(hit.start, hit.end) }],
    });
    at = hit.end;
  }
  if (at < text.length) out.push({ type: "text", value: text.slice(at) });
  return out;
}

function walk(node: HastNode, shown: ReadonlySet<string>, projectSlug: string | undefined, inLink: boolean): void {
  if (!("children" in node) || !node.children) return;
  const next: HastNode[] = [];
  for (const child of node.children) {
    if (child.type === "text" && !inLink) {
      next.push(...split((child as HastText).value, shown, projectSlug));
      continue;
    }
    const isLink = child.type === "element" && (child as HastElement).tagName === "a";
    walk(child, shown, projectSlug, inLink || isLink);
    next.push(child);
  }
  node.children = next;
}

/** A rehype plugin: the project's files named in text and in code become links into NS Code. */
export function rehypeCodeLinks(shown: ReadonlySet<string>, projectSlug?: string) {
  return () => (tree: HastNode) => walk(tree, shown, projectSlug, false);
}

/** The plain text of a hast node — what a code block copies, links or not. */
export function textOf(node: unknown): string {
  const n = node as HastNode | undefined;
  if (!n) return "";
  if (n.type === "text") return (n as HastText).value;
  return "children" in n && n.children ? n.children.map(textOf).join("") : "";
}
