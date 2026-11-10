/**
 * DEV-350 — a follow-up idea is named by what the person wants: the part he wrote after „Čo potrebujem:“, or, when he
 * wrote nothing, by the project it follows — never by the cockpit's own first sentence (10.11.2026 the first one was
 * named „Nadväzuje na projekt NEX Legal Watch (nex-legal-watch), ktorý vznikol z nápadu …“).
 */
import { describe, expect, it } from "vitest";

import { followUpStart, followUpTitle } from "@/lib/ideas";

const START = followUpStart({
  title: "NEX Legal Watch",
  outcome_project_name: "NEX Legal Watch",
  outcome_project_slug: "nex-legal-watch",
});

describe("followUpTitle (DEV-350)", () => {
  it("is what the person wrote after „Čo potrebujem:“ — its first line", () => {
    expect(
      followUpTitle(`${START} Čítanie pre inú aplikáciu\nďalšie podrobnosti`),
    ).toBe("Čítanie pre inú aplikáciu");
  });

  it("is the project it follows when he wrote nothing", () => {
    expect(followUpTitle(START)).toBe("Nadväzuje na NEX Legal Watch");
  });

  it("is nothing for an idea that follows no project", () => {
    expect(followUpTitle("Matrika pre farnosť")).toBeNull();
  });
});
