/**
 * Relácie — the "Používateľ" column must name a person, not print a UUID.
 *
 * The tab is open to ha+, but the user directory it used to resolve ids from (`GET /users`) is ri-only, so
 * a Medior was shown bare UUIDs next to a per-row "Odvolať" and had to judge a revoke blind. The session
 * rows now carry the owner's name from the server; the directory is a fallback, not the source.
 */

import { describe, it, expect } from "vitest";

import { buildUsernameIndex } from "@/components/settings/sessionUsernames";

describe("buildUsernameIndex", () => {
  it("names session owners with NO user directory at all (the Medior case)", () => {
    const index = buildUsernameIndex(
      [
        { user_id: "u-1", username: "nazar" },
        { user_id: "u-2", username: "tibor" },
      ],
      [],
    );

    expect(index).toEqual({ "u-1": "nazar", "u-2": "tibor" });
  });

  it("falls back to the directory for a row that arrived without a name", () => {
    const index = buildUsernameIndex([{ user_id: "u-1", username: null }], [{ id: "u-1", username: "zoltan" }]);

    expect(index["u-1"]).toBe("zoltan");
  });

  it("leaves an unresolvable id out, so the caller can show the id instead of 'undefined'", () => {
    const index = buildUsernameIndex([{ user_id: "u-ghost" }], []);

    expect(index["u-ghost"]).toBeUndefined();
  });
});
