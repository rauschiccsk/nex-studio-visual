/**
 * DEV-340 — a sentence of the UAT guide gets the full address of the customer being tried in place of its placeholder.
 *
 * 10.11.2026 Author Asistent 1.0.1's guide gave the Manažér „https://<adresa-inštalácie>/api/v1/health“ to put
 * together himself, while the cockpit knew TATA's address.
 */
import { describe, expect, it } from "vitest";

import { addressesOf, withAddresses } from "@/lib/uatAddresses";
import type { UatGuide } from "@/types/deploy";

const TATA = { "<adresa-inštalácie>": "https://uat-tata-author-asistent.int.isnex.eu" };

describe("withAddresses (DEV-340)", () => {
  it("puts TATA's address in the sentence Author Asistent 1.0.1 wrote, code marks and all", () => {
    expect(
      withAddresses("prehliadač, adresa inštalácie (napríklad `https://<adresa-inštalácie>/api/v1/health`)", TATA),
    ).toEqual([
      { text: "prehliadač, adresa inštalácie (napríklad " },
      { url: "https://uat-tata-author-asistent.int.isnex.eu/api/v1/health" },
      { text: ")" },
    ]);
  });

  it("leaves the sentence's full stop out of the address and takes a placeholder without its scheme", () => {
    expect(withAddresses("Otvor <adresa-inštalácie>/login. Potom sa prihlás.", TATA)).toEqual([
      { text: "Otvor " },
      { url: "https://uat-tata-author-asistent.int.isnex.eu/login" },
      { text: ". Potom sa prihlás." },
    ]);
  });

  it("names each frontend of a Web project by its own placeholder", () => {
    const icc = {
      "<adresa-administrácie>": "https://uat-icc-career-asistent-admin.isnex.eu",
      "<adresa-webu>": "https://uat-icc-career-asistent.isnex.eu",
    };
    expect(withAddresses("Otvor https://<adresa-administrácie>, potom https://<adresa-webu>/register", icc)).toEqual([
      { text: "Otvor " },
      { url: "https://uat-icc-career-asistent-admin.isnex.eu" },
      { text: ", potom " },
      { url: "https://uat-icc-career-asistent.isnex.eu/register" },
    ]);
  });

  it("keeps the sentence as it is for a customer without an address", () => {
    expect(withAddresses("Otvor https://<adresa-inštalácie>/api/v1/health.", {})).toEqual([
      { text: "Otvor https://<adresa-inštalácie>/api/v1/health." },
    ]);
  });
});

describe("addressesOf (DEV-340)", () => {
  const guide = {
    addresses: [
      { customer_id: "tata", placeholder: "<adresa-inštalácie>", url: "https://uat-tata-author-asistent.int.isnex.eu" },
      { customer_id: "icc", placeholder: "<adresa-inštalácie>", url: "https://uat-icc-author-asistent.int.isnex.eu" },
    ],
  } as unknown as UatGuide;

  it("takes the addresses of the customer being tried, and none when its UAT has no installation", () => {
    expect(addressesOf(guide, "tata", true)).toEqual(TATA);
    expect(addressesOf(guide, "tata", false)).toEqual({});
  });
});
