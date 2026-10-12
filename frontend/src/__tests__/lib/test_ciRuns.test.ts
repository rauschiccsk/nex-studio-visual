/** DEV-84 — the duration and the count of the repository tab in Slovak. */

import { describe, it, expect } from "vitest";

import { duration, shownCount } from "@/lib/ciRuns";

describe("the repository tab's words (DEV-84)", () => {
  it.each([
    [null, ""],
    [0, "0 s"],
    [45, "45 s"],
    [716, "11 min 56 s"],
    [3780, "1 h 3 min"],
  ])("%s seconds read as „%s“", (seconds, words) => {
    expect(duration(seconds)).toBe(words);
  });

  it.each([
    [1, "1 zobrazený z 100"],
    [3, "3 zobrazené z 100"],
    [5, "5 zobrazených z 100"],
    [0, "0 zobrazených z 100"],
  ])("%s shown reads as „%s“", (shown, words) => {
    expect(shownCount(shown, 100)).toBe(words);
  });
});
