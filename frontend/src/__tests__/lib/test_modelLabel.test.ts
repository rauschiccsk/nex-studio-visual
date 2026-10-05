/**
 * Mená modelov (ICCINT-167): z úplného mena, ktoré hlási Claude Code, sa skladá meno pre človeka bez
 * zoznamu známych verzií — aj verzia, ktorá ešte nevyšla, sa musí ukázať správne.
 */

import { describe, expect, it } from "vitest";

import { familyLabel, modelDisplayName, modelOptionLabel } from "@/utils/modelLabel";

describe("modelDisplayName", () => {
  it.each([
    ["claude-opus-5-5", "Opus 5.5"],
    ["claude-opus-5", "Opus 5"],
    ["claude-sonnet-5-5", "Sonnet 5.5"],
    ["claude-haiku-4-5-20251001", "Haiku 4.5"],
    // Verzia, ktorú kokpit nikdy nevidel — žiadny zoznam ju nesmie potrebovať.
    ["claude-opus-7-2", "Opus 7.2"],
    ["opus", "Opus"],
  ])("%s → %s", (id, expected) => {
    expect(modelDisplayName(id)).toBe(expected);
  });

  it("returns a name without any family word unchanged — raw beats invented", () => {
    expect(modelDisplayName("2025")).toBe("2025");
  });
});

describe("modelOptionLabel", () => {
  it("names the version that last really ran", () => {
    expect(
      modelOptionLabel({ id: "opus", last_run_model: "claude-opus-5-5", last_run_at: "2026-10-05T10:00:00Z" }),
    ).toBe("Opus — vždy najnovší (naposledy bežal Opus 5.5)");
  });

  it("claims no version before the family has ever run", () => {
    expect(modelOptionLabel({ id: "sonnet", last_run_model: null, last_run_at: null })).toBe(
      "Sonnet — vždy najnovší",
    );
  });
});

describe("familyLabel", () => {
  it("capitalises the family", () => {
    expect(familyLabel("haiku")).toBe("Haiku");
  });
});
