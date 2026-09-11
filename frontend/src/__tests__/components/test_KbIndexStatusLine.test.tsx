/**
 * KbIndexStatusLine — ICCINT-111.
 *
 * Zmerané 10.09.2026: z 205 súborov Znalostnej bázy nesedelo 113 a nikde to nebolo vidieť. Oprava má
 * dve polovice — dorovnávať rozdiel A ukázať ho. Keby sa dorovnával potichu, tichý rozchod by len
 * zmenil podobu.
 *
 * Najdôležitejšia je tu tretia skúška: keď sa stav zistiť NEDÁ, nesmie z toho vyjsť „sedí".
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import { KbIndexStatusLine } from "@/components/kb/KbIndexStatusLine";
import { getKbIndexStatus } from "@/services/api/rag";

vi.mock("@/services/api/rag", () => ({ getKbIndexStatus: vi.fn() }));

const stav = (over = {}) => ({
  on_disk: 205,
  indexed: 205,
  out_of_sync: 0,
  missing: 0,
  stale: 0,
  orphaned: 0,
  last_indexed_at: "2026-09-11T09:00:00Z",
  sample: [],
  ...over,
});

beforeEach(() => vi.clearAllMocks());

describe("ICCINT-111 — rozchod Znalostnej bázy a vyhľadávania je vidieť", () => {
  it("keď všetko sedí, povie to pokojne", async () => {
    vi.mocked(getKbIndexStatus).mockResolvedValue(stav());
    render(<KbIndexStatusLine />);
    expect(await screen.findByText(/vyhľadávanie sedí/i)).toBeInTheDocument();
  });

  it("keď nesedí, povie KOĽKO — nie len že niečo nie je v poriadku", async () => {
    vi.mocked(getKbIndexStatus).mockResolvedValue(
      stav({ indexed: 92, out_of_sync: 113, missing: 71, stale: 42, sample: ["icc/STRUCTURE.md"] }),
    );
    render(<KbIndexStatusLine />);
    // Presne to číslo, ktoré sme namerali naživo.
    expect(await screen.findByText(/nesedí: 113 dokumentov/i)).toBeInTheDocument();
  });

  it("keď sa to zistiť NEDÁ, nesmie z toho vyjsť „sedí“", async () => {
    vi.mocked(getKbIndexStatus).mockRejectedValue(new Error("503"));
    render(<KbIndexStatusLine />);

    expect(await screen.findByText(/nedá sa zistiť/i)).toBeInTheDocument();
    expect(screen.queryByText(/vyhľadávanie sedí/i)).toBeNull();
  });

  it("kým odpoveď nepríde, netvrdí nič", () => {
    vi.mocked(getKbIndexStatus).mockReturnValue(new Promise(() => {}));
    const { container } = render(<KbIndexStatusLine />);
    expect(container.textContent).toBe("");
  });
});
