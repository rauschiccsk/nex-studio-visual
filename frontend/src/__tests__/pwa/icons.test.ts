import { readFileSync, existsSync } from "node:fs";
import { createHash } from "node:crypto";
import { inflateSync } from "node:zlib";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Raster forms of the app mark.
 *
 * They are produced by `scripts/generate-icons.py` from the template `public/icon.svg` -
 * they are not drawn a second time by hand, otherwise the forms would drift apart. The
 * generator writes the template fingerprint into every PNG; the test below compares it
 * with the current template, so changing the template without regenerating the icons
 * does not pass silently.
 */

/**
 * Reads a brand colour from the shared kit instead of writing the number here: a test
 * with a written colour stops claiming "the app matches the library" and starts claiming
 * "the app matches the number somebody wrote then" - and stays SILENT after the next
 * upgrade. Comments are stripped first (the library has multi-line comments carrying hex
 * values), and a missing token is an ERROR, never a fallback value: a guard that passes
 * when its yardstick is lost is worse than none. `--color-primary-600` is declared in the
 * library only in the `@theme` (light) block.
 */
const TOKENS = resolve(__dirname, "../../../node_modules/nex-shared/dist/tokens.css");

function lightTokenRgb(name: string): [number, number, number] {
  const lines = readFileSync(TOKENS, "utf-8").replace(/\/\*[\s\S]*?\*\//g, " ").split("\n");
  const start = lines.findIndex((l) => l.startsWith("@theme {"));
  if (start === -1) throw new Error(`No '@theme {' block in ${TOKENS} - the guard would lose its yardstick.`);
  const end = lines.findIndex((l, i) => i > start && l.trim() === "}");
  const block = lines.slice(start + 1, end === -1 ? undefined : end).join("\n");
  const m = block.match(new RegExp(`(?:^|\\n)\\s*${name.replace(/-/g, "\\-")}\\s*:\\s*([^;]+);`));
  if (!m) throw new Error(`Token '${name}' is not in the library. No fallback value on purpose.`);
  const raw = m[1]!.trim();
  const hex = raw.match(/^#([0-9a-fA-F]{6})$/);
  if (!hex) throw new Error(`Token '${name}' is not a six-digit hex colour, but '${raw}'.`);
  const n = Number.parseInt(hex[1]!, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

const root = resolve(__dirname, "../../..");
const publicDir = resolve(root, "public");

const manifest = JSON.parse(
  readFileSync(resolve(publicDir, "manifest.webmanifest"), "utf-8"),
) as { icons: { src: string; sizes: string; purpose?: string }[] };

const templateFingerprint = createHash("sha256")
  .update(readFileSync(resolve(publicDir, "icon.svg"), "utf-8"))
  .digest("hex")
  .slice(0, 16);

interface Png {
  width: number;
  height: number;
  templateFingerprint: string | null;
  pixel: (x: number, y: number) => [number, number, number, number];
}

function loadPng(name: string): Png {
  const data = readFileSync(resolve(publicDir, name));
  expect(data.subarray(0, 8)).toEqual(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]));

  let i = 8;
  let width = 0;
  let height = 0;
  let templateFingerprint: string | null = null;
  const idat: Buffer[] = [];

  while (i < data.length) {
    const length = data.readUInt32BE(i);
    const kind = data.subarray(i + 4, i + 8).toString("ascii");
    const payload = data.subarray(i + 8, i + 8 + length);
    if (kind === "IHDR") {
      width = payload.readUInt32BE(0);
      height = payload.readUInt32BE(4);
      expect(payload[8]).toBe(8); // 8 bits per channel
      expect(payload[9]).toBe(6); // RGBA
    } else if (kind === "tEXt") {
      const [key, value] = payload.toString("latin1").split("\0");
      if (key === "nex-icon-source") templateFingerprint = value ?? null;
    } else if (kind === "IDAT") {
      idat.push(payload);
    }
    i += 12 + length;
  }

  const raw = inflateSync(Buffer.concat(idat));
  const pixel = (x: number, y: number): [number, number, number, number] => {
    const offset = y * (width * 4 + 1) + 1 + x * 4; // +1 = filter byte at the start of the row
    return [raw[offset]!, raw[offset + 1]!, raw[offset + 2]!, raw[offset + 3]!];
  };
  return { width, height, templateFingerprint, pixel };
}

/** How far from the center the white monogram reaches. */
function monogramReach(name: string): number {
  const png = loadPng(name);
  const center = png.width / 2;
  let farthest = 0;
  for (let y = 0; y < png.height; y += 2) {
    for (let x = 0; x < png.width; x += 2) {
      const [r, g, b] = png.pixel(x, y);
      if (r > 200 && g > 200 && b > 200) {
        farthest = Math.max(farthest, Math.hypot(x - center, y - center));
      }
    }
  }
  return farthest;
}

describe("the app mark in raster forms", () => {
  it("ALL forms promised in the manifest really exist", () => {
    // The manifest may promise only what can be delivered - otherwise the browser finds
    // no icon at install time and uses a substitute or none.
    for (const icon of manifest.icons) {
      const name = icon.src.replace(/^\//, "");
      expect(existsSync(resolve(publicDir, name)), `missing ${icon.src}`).toBe(true);
    }
  });

  it.each([
    ["icon-192.png", 192],
    ["icon-512.png", 512],
    ["icon-512-maskable.png", 512],
  ])("%s has the promised size %i", (name, expected) => {
    const png = loadPng(name);
    expect(png.width).toBe(expected);
    expect(png.height).toBe(expected);
    // And that size matches what the manifest claims about it.
    const fromManifest = manifest.icons.find((i) => i.src === `/${name}`);
    expect(fromManifest?.sizes).toBe(`${expected}x${expected}`);
  });

  it.each(["icon-192.png", "icon-512.png", "icon-512-maskable.png"])(
    "%s is derived from the CURRENT template",
    (name) => {
      // When icon.svg changes and the icons are not regenerated, this test fails.
      expect(loadPng(name).templateFingerprint).toBe(templateFingerprint);
    },
  );

  it("the regular form has rounded corners - the corner is transparent", () => {
    const png = loadPng("icon-512.png");
    expect(png.pixel(1, 1)[3]).toBe(0); // corner outside the rounding
    expect(png.pixel(256, 256)[3]).toBe(255); // the center is solid
  });

  it("the MASKABLE form fills the whole canvas - otherwise corners would stay empty after cropping", () => {
    const png = loadPng("icon-512-maskable.png");
    for (const [x, y] of [
      [0, 0],
      [511, 0],
      [0, 511],
      [511, 511],
    ]) {
      const [r, g, b, a] = png.pixel(x!, y!);
      expect(a, `corner ${x},${y} must be solid`).toBe(255);
      // The brand colour is READ from the library - `--color-primary-600` is declared in it
      // only in the light block, so that is where it is asked. It is the same colour the
      // brand wears in the sidebar, so the icon and the sidebar cannot drift apart.
      expect([r, g, b]).toEqual(lightTokenRgb("--color-primary-600"));
    }
  });

  it("the MASKABLE form keeps the monogram in the safe zone", () => {
    // Systems crop the icon to a circle with a diameter of ~80 % of the side. Anything
    // white outside it would lose its edges.
    const reach = monogramReach("icon-512-maskable.png");
    expect(reach).toBeGreaterThan(0); // the monogram is really there
    expect(reach).toBeLessThan(512 * 0.4);
  });

  it("shrinking was applied ONLY to the maskable form", () => {
    // Negative counterpart: "is in the safe zone" alone proves nothing - an unshrunk
    // monogram fits the zone too. Only the comparison shows that shrinking really happened
    // and was not applied to the regular form by mistake.
    expect(monogramReach("icon-512-maskable.png")).toBeLessThan(
      monogramReach("icon-512.png") * 0.9,
    );
  });
});
