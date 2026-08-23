/**
 * The cockpit shows what the backend actually said (ICCINT-22).
 *
 * Found by the Director while accepting ICCINT-6: he tried to save a port from another system's reserved
 * block. The refusal was correct, but the screen read only "Nastavenia projektu sa nepodarilo uložiť —
 * zadané údaje nie sú v poriadku", while the backend had answered precisely — naming the block's owner.
 *
 * The cause was here: this helper replaced the backend's sentence with a canned phrase keyed on the HTTP
 * status and filed the truth under a collapsible. EVERY error in the cockpit passes through it, so it was
 * not one bad message — it was the app knowing the answer and withholding it, everywhere.
 */
import { describe, expect, it } from "vitest";

import { humanizeApiError } from "@/services/apiError";
import { ApiError } from "@/services/api";

const RESERVED =
  "Port 10225 (Backend) patrí do rezervovaného bloku, ktorý má pridelený nex-payables. " +
  "Vyber port z bloku tohto projektu.";

describe("humanizeApiError", () => {
  it("shows the backend's sentence instead of a canned reason", () => {
    const out = humanizeApiError(new ApiError(422, RESERVED), "Nastavenia projektu sa nepodarilo uložiť");

    expect(out.message).toContain("nex-payables");
    expect(out.message).toContain("Nastavenia projektu sa nepodarilo uložiť");
    // The canned phrase must NOT replace a real answer — that is the whole defect.
    expect(out.message).not.toContain("zadané údaje nie sú v poriadku");
  });

  it("falls back to the canned reason when the backend said nothing usable", () => {
    // A bare 500 or a FastAPI validation object (which renders as "[object Object]") carries no sentence
    // to show, and THAT is what the canned reason is for.
    for (const err of [new ApiError(500, ""), new ApiError(422, "[object Object]")]) {
      const out = humanizeApiError(err, "Uloženie zlyhalo");
      expect(out.message).toContain("Uloženie zlyhalo");
      expect(out.message).not.toContain("[object Object]");
    }
    expect(humanizeApiError(new ApiError(500, ""), "X").message).toContain("chyba na strane servera");
    expect(humanizeApiError(new ApiError(422, "[object Object]"), "X").message).toContain(
      "zadané údaje nie sú v poriadku",
    );
  });

  it("keeps the HTTP status out of the sentence and in the technical detail", () => {
    const out = humanizeApiError(new ApiError(422, RESERVED), "Uloženie zlyhalo");
    // The manager reads the sentence; the status code is for whoever debugs it.
    expect(out.message).not.toContain("422");
    expect(out.detail).toContain("HTTP 422");
  });

  it("a non-ApiError still yields a sentence, never a blank screen", () => {
    expect(humanizeApiError(new Error("boom"), "Akcia zlyhala").message).toContain("Akcia zlyhala");
    expect(humanizeApiError(undefined, "Akcia zlyhala").message).toContain("Akcia zlyhala");
  });
});
