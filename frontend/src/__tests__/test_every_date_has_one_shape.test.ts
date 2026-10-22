/**
 * DEV-153 — every date on the cockpit's screen has one shape, shaped in one place (``utils/format.ts``).
 *
 * 20.10.2026 the screen shaped dates itself in 15 places of 12 files with the browser's ``sk-SK`` — „20. 10. 2026
 * 8:30:15“ in one, „9. 10. 18:52“ in another. The Director at NEX Inbox: „Chcel by som zbytočné medzery odstrániť
 * "19.10.2026 06:31"“; for the cockpit: „Súhlasím“. A screen that shapes a date itself turns this red on the commit
 * that adds it. It scans the source because the point is coverage: a number's ``toLocaleString`` stays allowed.
 */
import { describe, expect, it } from "vitest";
import { readdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join, relative, resolve } from "node:path";

import { formatDate, formatDateTime, formatShortDateTime, formatTime } from "@/utils/format";

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const THE_ONE_PLACE = "utils/format.ts";
const DATE_OPTION = /\b(day|month|year|hour|minute|second|weekday)\s*:/;

function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === "__tests__" ? [] : sources(path);
    return /\.(ts|tsx)$/.test(entry.name) && !entry.name.endsWith(".d.ts") ? [path] : [];
  });
}

/** The text of the call's arguments — from the opening parenthesis to its match. */
function argumentsAt(source: string, open: number): string {
  let depth = 0;
  for (let j = open; j < source.length; j += 1) {
    if (source[j] === "(") depth += 1;
    else if (source[j] === ")") {
      depth -= 1;
      if (depth === 0) return source.slice(open, j + 1);
    }
  }
  return source.slice(open);
}

/** The receiver of a call ending just before *end* — an identifier, or a parenthesised / called expression whole. */
function receiverBefore(source: string, end: number): string {
  let j = end - 1;
  if (source[j] === ")") {
    let depth = 0;
    for (; j >= 0; j -= 1) {
      if (source[j] === ")") depth += 1;
      else if (source[j] === "(") {
        depth -= 1;
        if (depth === 0) break;
      }
    }
  }
  let k = j;
  while (k > 0 && /[\w$.]/.test(source[k - 1] ?? "")) k -= 1;
  if (source.slice(Math.max(0, k - 4), k) === "new ") k -= 4;
  return source.slice(k, end);
}

/** Every place in *source* that shapes a date itself. */
export function datesShapedHere(source: string): string[] {
  const found: string[] = [];
  const dateNames = new Set([...source.matchAll(/\b(?:const|let|var)\s+(\w+)\s*(?::\s*Date\s*)?=\s*new Date\(/g)].map((m) => m[1]));
  for (const m of source.matchAll(/\b(toLocaleDateString|toLocaleTimeString)\s*\(|Intl\.DateTimeFormat/g)) {
    found.push(m[0]);
  }
  for (const m of source.matchAll(/\.toLocaleString\s*\(/g)) {
    const at = m.index ?? 0;
    const receiver = receiverBefore(source, at);
    const args = argumentsAt(source, at + m[0].length - 1);
    if (receiver.startsWith("new Date") || dateNames.has(receiver) || DATE_OPTION.test(args)) {
      found.push(`${receiver}.toLocaleString${args}`.replace(/\s+/g, " ").slice(0, 120));
    }
  }
  return found;
}

describe("every date has one shape", () => {
  it("no screen shapes a date itself — only utils/format.ts does", () => {
    const offenders = sources(SRC)
      .map((path) => relative(SRC, path))
      .filter((rel) => rel !== THE_ONE_PLACE)
      .flatMap((rel) => datesShapedHere(readFileSync(join(SRC, rel), "utf8")).map((what) => `${rel}: ${what}`));

    expect(offenders).toEqual([]);
  });

  it("the guard finds every way a date was shaped on 20.10.2026 and leaves a number alone", () => {
    expect(datesShapedHere('const when = (iso: string) => new Date(iso).toLocaleString("sk-SK");')).toHaveLength(1);
    expect(datesShapedHere('return new Date(v).toLocaleDateString("sk-SK");')).toHaveLength(1);
    expect(datesShapedHere('return d.toLocaleTimeString("sk-SK", { hour: "2-digit" });')).toHaveLength(1);
    expect(datesShapedHere('const d = new Date(iso);\nreturn d.toLocaleString("sk-SK");')).toHaveLength(1);
    expect(datesShapedHere('return at.toLocaleString("sk-SK", {\n  day: "numeric",\n  month: "numeric",\n});')).toHaveLength(1);
    expect(datesShapedHere('new Intl.DateTimeFormat("sk-SK").format(d)')).toHaveLength(1);
    expect(datesShapedHere('`${Number(v).toLocaleString("sk-SK", { maximumFractionDigits: 2 })} €`')).toEqual([]);
    expect(datesShapedHere('return Math.round(n).toLocaleString("sk-SK");')).toEqual([]);
  });
});

describe("the one shape", () => {
  const at = new Date(2026, 9, 9, 8, 5, 42); // 9 October 2026, 08:05:42 local time

  it("is the date of NEX Inbox — no spaces, no seconds, day and month two-digit", () => {
    expect(formatDate(at)).toBe("09.10.2026");
    expect(formatDateTime(at)).toBe("09.10.2026 08:05");
    expect(formatShortDateTime(at)).toBe("09.10. 08:05");
    expect(formatTime(at)).toBe("08:05");
  });

  it("reads the backend's ISO text and says „—“ for no date", () => {
    expect(formatDateTime(at.toISOString())).toBe("09.10.2026 08:05");
    for (const empty of [null, undefined, "", "not a date"]) {
      expect([formatDate(empty), formatDateTime(empty), formatShortDateTime(empty), formatTime(empty)]).toEqual([
        "—",
        "—",
        "—",
        "—",
      ]);
    }
  });
});
