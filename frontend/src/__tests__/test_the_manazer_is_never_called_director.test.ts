/**
 * DEV-340 — the person working with the cockpit is the Manažér; no screen calls anybody „Director“.
 *
 * 10.11.2026 the Nápady screen said „do ktorej verzie pôjde, rozhodne Director“ and people took it for their company's
 * director. The rule reads every text the screens say (screenTexts), so a new screen cannot bring the word back; what
 * the agents are given and what the backend says are guarded beside it (tests/test_the_manazer_is_never_called_director.py).
 */
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { SRC, texts, textsSaying } from "./screenTexts";

/** „Director“ in any form (Directora, Directorovi) — never the English „Directory“. */
const OLD_NAME = /\bDirector(?!y|ies)/;

describe("the Manažér is never called Director on a screen (DEV-340)", () => {
  it("no text of the cockpit's screens says Director", () => {
    expect(textsSaying(OLD_NAME)).toEqual([]);
  });

  it("the rule reads the Nápady screen and sees every form of the word", () => {
    expect(texts(join(SRC, "components/poradca/IdeaOutcomeActions.tsx")).join(" ")).toContain("rozhodne Manažér");
    for (const sentence of ["rozhodne Director", "rozhodnutie Directora", "Napíš Directorovi"]) {
      expect(OLD_NAME.test(sentence)).toBe(true);
    }
    for (const sentence of ["Directory", "rozhodne Manažér"]) {
      expect(OLD_NAME.test(sentence)).toBe(false);
    }
  });
});
