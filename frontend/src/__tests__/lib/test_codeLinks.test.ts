/** DEV-88 — which words of a text are files of the project NS Code shows. */

import { describe, it, expect } from "vitest";

import { findCodePaths, nsCodeHref } from "@/lib/codeLinks";

const SHOWN = new Set(["backend/main.py", "backend/apps/xml_exporter/ubl_builder.py", "README.md", "Dockerfile"]);
const found = (text: string, slug?: string) =>
  findCodePaths(text, SHOWN, slug).map((h) => [text.slice(h.start, h.end), h.path, h.line]);

describe("the files a text names (DEV-88)", () => {
  it("finds a path with and without a line, and not the full stop after it", () => {
    expect(found("Pozri backend/main.py:129 a README.md.")).toEqual([
      ["backend/main.py:129", "backend/main.py", 129],
      ["README.md", "README.md", null],
    ]);
  });

  it("counts the project's place on the server, but not another project's", () => {
    expect(found("/opt/projects/nex-inbox/backend/main.py:7", "nex-inbox")).toEqual([
      ["/opt/projects/nex-inbox/backend/main.py:7", "backend/main.py", 7],
    ]);
    expect(found("/opt/projects/nex-manager/backend/main.py", "nex-inbox")).toEqual([]);
  });

  it("leaves a secret, a path that does not exist and a bare file name as text", () => {
    expect(found("Hodnota je v .env a v backend/.env.production, kód v backend/nie_je.py a v main.py.")).toEqual([]);
  });

  it("does not find a path inside a longer word or an address", () => {
    expect(found("https://github.com/rauschiccsk/nex-inbox/blob/main/backend/main.py xbackend/main.py")).toEqual([]);
  });

  it("finds a path written from the project's root with ./", () => {
    expect(found("Spusti ./backend/main.py")).toEqual([["backend/main.py", "backend/main.py", null]]);
  });

  it("finds a file without an extension the project has", () => {
    expect(found("Obraz stavia Dockerfile.")).toEqual([["Dockerfile", "Dockerfile", null]]);
  });

  it("builds the address NS Code opens", () => {
    expect(nsCodeHref("backend/main.py", 129)).toBe("/ns-code?path=backend%2Fmain.py&line=129");
    expect(nsCodeHref("README.md", null)).toBe("/ns-code?path=README.md");
  });
});
