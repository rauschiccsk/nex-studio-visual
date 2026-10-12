/** DEV-91 — what the UAT acceptance question says about the testing. */

import { describe, expect, it } from "vitest";

import { acceptanceTesting } from "@/lib/uatTesting";
import type { UatProgress } from "@/types/deploy";

const progress = (over: Partial<UatProgress>): UatProgress => ({
  version_number: "1.8.0",
  items: ["A", "B", "C"],
  checks: [],
  tested: 0,
  total: 3,
  untested: [],
  broken: [],
  ...over,
});

describe("acceptanceTesting", () => {
  it("says how much was tested and what is left or does not work", () => {
    expect(acceptanceTesting(progress({ tested: 1, untested: ["B"], broken: ["C"] }))).toBe(
      "Otestované 1 z 3. Neotestované: B. Nefunguje: C. ",
    );
  });

  it("says only the count when everything was tried", () => {
    expect(acceptanceTesting(progress({ tested: 3 }))).toBe("Otestované 3 z 3. ");
  });

  it("says nothing for a version with no changes to try", () => {
    expect(acceptanceTesting(progress({ items: [], total: 0 }))).toBe("");
  });
});
