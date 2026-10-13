/** DEV-86 — the tree, the icons, the quick open and the language of NS Code. */

import { describe, it, expect } from "vitest";

import { buildTree, fileKind, foldersOf, languageOf, quickOpen } from "@/lib/nsCode";

describe("NS Code's own logic (DEV-86)", () => {
  it("builds the tree with folders first, each level by name", () => {
    const tree = buildTree(["README.md", "backend/main.py", "backend/apps/x.py", ".env.example", "a.txt"]);

    expect(tree.map((n) => [n.name, n.folder])).toEqual([
      ["backend", true],
      [".env.example", false],
      ["a.txt", false],
      ["README.md", false],
    ]);
    expect(tree[0]!.children.map((n) => n.path)).toEqual(["backend/apps", "backend/main.py"]);
  });

  it("names the folders on the way to a file", () => {
    expect(foldersOf("backend/apps/xml/ubl.py")).toEqual(["backend", "backend/apps", "backend/apps/xml"]);
    expect(foldersOf("README.md")).toEqual([]);
  });

  it.each([
    ["main.py", "code"],
    ["App.tsx", "code"],
    ["package.json", "data"],
    ["poetry.lock", "data"],
    ["README.md", "text"],
    ["logo.svg", "image"],
    ["deploy.sh", "shell"],
    [".gitignore", "config"],
    ["Dockerfile", "config"],
    ["LICENSE", "other"],
  ])("%s is a %s file", (name, kind) => {
    expect(fileKind(name)).toBe(kind);
  });

  it("opens a file by its name before one whose folders merely spell it, and shorter paths first", () => {
    const paths = ["backend/main.py", "docs/main_page.md", "frontend/src/main.tsx", "m/a/i/n.txt"];

    expect(quickOpen(paths, "main")).toEqual([
      "backend/main.py",
      "docs/main_page.md",
      "frontend/src/main.tsx",
      "m/a/i/n.txt",
    ]);
    expect(quickOpen(paths, "MAIN.PY")).toEqual(["backend/main.py"]);
    expect(quickOpen(paths, "zzz")).toEqual([]);
  });

  it("highlights by the editor's own list of file names and extensions, the longest extension winning", () => {
    const languages = [
      { id: "typescript", extensions: [".ts", ".tsx"] },
      { id: "dockerfile", extensions: [".dockerfile"], filenames: ["Dockerfile"] },
      { id: "xml", extensions: [".xml"] },
      { id: "x-d", extensions: [".d.ts"] },
    ];

    expect(languageOf("frontend/src/App.tsx", languages)).toBe("typescript");
    expect(languageOf("backend/Dockerfile", languages)).toBe("dockerfile");
    expect(languageOf("types/env.d.ts", languages)).toBe("x-d");
    expect(languageOf("LICENSE", languages)).toBe("plaintext");
  });
});
