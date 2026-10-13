/**
 * DEV-86 — what NS Code does with the list of a project's files: the tree, the icon of a file, the quick open.
 */

export interface TreeNode {
  name: string;
  /** The path from the project's root; a folder's without the trailing slash. */
  path: string;
  folder: boolean;
  children: TreeNode[];
}

/** The tree of the paths — folders first, then files, each by name as VS Code sorts them. */
export function buildTree(paths: string[]): TreeNode[] {
  const root: TreeNode = { name: "", path: "", folder: true, children: [] };
  for (const path of paths) {
    let node = root;
    const parts = path.split("/");
    parts.forEach((name, i) => {
      const folder = i < parts.length - 1;
      const at = parts.slice(0, i + 1).join("/");
      let child = node.children.find((c) => c.name === name && c.folder === folder);
      if (!child) {
        child = { name, path: at, folder, children: [] };
        node.children.push(child);
      }
      node = child;
    });
  }
  const sort = (n: TreeNode) => {
    n.children.sort((a, b) => Number(b.folder) - Number(a.folder) || a.name.localeCompare(b.name, "en"));
    n.children.forEach(sort);
  };
  sort(root);
  return root.children;
}

/** The folders on the way to a file — opened when a link points into them. */
export function foldersOf(path: string): string[] {
  const parts = path.split("/").slice(0, -1);
  return parts.map((_p, i) => parts.slice(0, i + 1).join("/"));
}

export type FileKind = "code" | "data" | "text" | "image" | "config" | "shell" | "other";

const KINDS: [FileKind, RegExp][] = [
  ["code", /\.(py|ts|tsx|js|jsx|mjs|cjs|css|scss|html|sql|go|rs|java|cs|php|rb|vue|svelte)$/i],
  ["data", /\.(json|ya?ml|toml|xml|csv|lock)$/i],
  ["text", /\.(md|txt|rst|adoc)$/i],
  ["image", /\.(png|jpe?g|gif|svg|webp|ico|bmp)$/i],
  ["shell", /\.(sh|bash|ps1|bat|cmd)$/i],
  ["config", /(^|\/)(\.[^/]*|Dockerfile[^/]*|Makefile|[^/]*\.(ini|cfg|conf|env\.example))$/i],
];

/** What kind of file a name is — picks its icon in the tree. */
export function fileKind(name: string): FileKind {
  return KINDS.find(([, re]) => re.test(name))?.[0] ?? "other";
}

/**
 * The quick open (Ctrl+P): paths whose letters contain the query in order, best first — a match in the file name
 * before one spread over the folders, then the shorter path. Case does not matter.
 */
export function quickOpen(paths: string[], query: string, limit = 50): string[] {
  const q = query.trim().toLowerCase();
  if (!q) return paths.slice(0, limit);
  const scored: [number, string][] = [];
  for (const path of paths) {
    const lower = path.toLowerCase();
    const name = lower.slice(lower.lastIndexOf("/") + 1);
    let score: number;
    if (name.includes(q)) score = 0;
    else if (lower.includes(q)) score = 1;
    else if (inOrder(lower, q)) score = 2;
    else continue;
    scored.push([score * 10_000 + path.length, path]);
  }
  return scored
    .sort((a, b) => a[0] - b[0] || a[1].localeCompare(b[1]))
    .slice(0, limit)
    .map(([, p]) => p);
}

function inOrder(text: string, letters: string): boolean {
  let at = 0;
  for (const ch of letters) {
    at = text.indexOf(ch, at);
    if (at < 0) return false;
    at += 1;
  }
  return true;
}

/** The language VS Code would highlight a file in — from the editor's own list of extensions and file names. */
export function languageOf(
  path: string,
  languages: { id: string; extensions?: string[]; filenames?: string[] }[],
): string {
  const name = path.slice(path.lastIndexOf("/") + 1);
  const lower = name.toLowerCase();
  const byName = languages.find((l) => l.filenames?.some((f) => f.toLowerCase() === lower));
  if (byName) return byName.id;
  const byExtension = languages
    .flatMap((l) => (l.extensions ?? []).map((e) => [e.toLowerCase(), l.id] as const))
    .filter(([e]) => lower.endsWith(e))
    .sort((a, b) => b[0].length - a[0].length)[0];
  return byExtension?.[1] ?? "plaintext";
}

/** DEV-87 — every folder holding a changed or new file, so the tree marks the way to it as VS Code does. */
export function foldersWithChanges(changes: Record<string, string>): Set<string> {
  return new Set(Object.keys(changes).flatMap(foldersOf));
}
