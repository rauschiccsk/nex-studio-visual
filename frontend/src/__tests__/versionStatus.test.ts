/**
 * Stav verzie sa číta rovnako na oboch obrazovkách — a neznámy stav sa nevydáva za „Plánované".
 * (ICCINT-50)
 *
 * Do 03.09.2026 mali ProjectDetailPage aj VersionDetailPage vlastnú kópiu tých istých dvoch funkcií,
 * obe s prepadom `return "Plánované"`. Dokončená verzia teda niesla `done` — a zobrazila sa ako
 * „Plánované", teda presne ako tá, ktorá sa nikdy nezačala.
 */
import { describe, expect, it } from "vitest";

import { versionStatusCls, versionStatusLabel } from "@/components/cockpit/labels";

describe("stav verzie", () => {
  it("pomenuje všetky štyri stavy životného cyklu", () => {
    expect(versionStatusLabel("planned")).toBe("Plánované");
    expect(versionStatusLabel("active")).toBe("Prebieha");
    expect(versionStatusLabel("done")).toBe("Hotové");
    expect(versionStatusLabel("released")).toBe("Vydané");
  });

  it("neznámy stav NEVYDÁVA za „Plánované“", () => {
    // Prepadové „Plánované" je tichá lož: hotová verzia sa tak zobrazovala ako nezačatá.
    expect(versionStatusLabel("nieco_ine")).not.toBe("Plánované");
    expect(versionStatusLabel("nieco_ine")).toBe("nieco_ine");
  });

  it("hotové a vydané nevyzerajú rovnako", () => {
    // Nasadenie je samostatný krok, ktorý pri „hotové" ešte neprebehol.
    expect(versionStatusCls("done")).not.toBe(versionStatusCls("released"));
  });

  it("hotové nevyzerá ako rozrobené", () => {
    expect(versionStatusCls("done")).not.toBe(versionStatusCls("active"));
  });
});
