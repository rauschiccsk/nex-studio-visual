/**
 * StavZostavenia (ICCINT-129, druhá polovica) — Manažér vidí stav zostavenia PRIEBEŽNE.
 *
 * Brána Verifikácie si kedysi pýtala od GitHubu jeden beh a na repozitári s dvomi postupmi rozhodovala
 * hodom mincou. 14.09.2026 prešiel NEX Inbox 1.5.0 do stavu Hotovo s padajúcim zostavením. Tá polovica
 * je opravená — brána číta všetky behy.
 *
 * Zostávalo, že sa Manažér o červenom CI dozvie až NA BRÁNE, teda keď je verzia „hotová" a prerába sa.
 *
 * ⚠️ Čo tieto stráže NEDOVOLIA zmeniť:
 *  - červená sa pomenuje ako zlyhanie a nesie POSTUP aj číslo behu (14.09. brána ohlásila „CI zelené
 *    (beh …)" o behu úplne iného postupu — jediné slovo, na ktoré sa Manažér spoliehal, bolo zlé);
 *  - `unknown` sa NIKDY netvári ako zelená;
 *  - keď sa stav nedá zistiť, radšej sa nezobrazí NIČ než stará hodnota.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import StavZostavenia from "@/components/riadiace/StavZostavenia";
import { useStavZostavenia } from "@/components/riadiace/useStavZostavenia";
import { getCiStatusApi } from "@/services/api/pipeline";

vi.mock("@/services/api/pipeline", () => ({ getCiStatusApi: vi.fn() }));

beforeEach(() => {
  vi.clearAllMocks();
});

/** The page reads the build's CI once (DEV-51) and hands it down; this is that page in miniature. */
function Riadok({ versionId }: { versionId: string | null }) {
  return <StavZostavenia stav={useStavZostavenia(versionId)} />;
}

describe("Stav zostavenia vedľa fáz", () => {
  it("⚠️ červené zostavenie pomenuje aj postup, nielen číslo behu", async () => {
    vi.mocked(getCiStatusApi).mockResolvedValue({
      stav: "red",
      detail: "CI zlyhalo (postup CI, beh 34869173177, failure)",
      sha: "5fb85a6",
    });

    render(<Riadok versionId="v-1" />);

    expect(await screen.findByText(/Zostavenie zlyhalo/i)).toBeInTheDocument();
    expect(screen.getByText(/postup CI, beh 34869173177/)).toBeInTheDocument();
  });

  it("zelené zostavenie povie, že prešlo", async () => {
    vi.mocked(getCiStatusApi).mockResolvedValue({ stav: "green", detail: "CI zelené (postup CI, beh 5)", sha: "abc" });

    render(<Riadok versionId="v-1" />);

    expect(await screen.findByText(/Zostavenie prešlo/i)).toBeInTheDocument();
  });

  it("⚠️ nevedomosť sa netvári ako dobrá správa", async () => {
    vi.mocked(getCiStatusApi).mockResolvedValue({
      stav: "unknown",
      detail: "CI ešte beží (postup CI, beh 9)",
      sha: "abc",
    });

    render(<Riadok versionId="v-1" />);

    expect(await screen.findByText(/zatiaľ nevieme/i)).toBeInTheDocument();
    expect(screen.queryByText(/Zostavenie prešlo/i)).not.toBeInTheDocument();
  });

  it("⚠️ keď sa stav nedá zistiť, nezobrazí sa NIČ — nie stará hodnota", async () => {
    vi.mocked(getCiStatusApi).mockRejectedValue(new Error("sieť"));

    const { container } = render(<Riadok versionId="v-1" />);

    await waitFor(() => expect(getCiStatusApi).toHaveBeenCalled());
    expect(container.querySelector('[data-testid="stav-zostavenia"]')).toBeNull();
  });

  it("⚠️ chyba vyhodená SYNCHRÓNNE nesmie zhodiť obrazovku", () => {
    // Presne ten tvar, ktorý 25.09.2026 zhodil 17 stráží stránky projektu: atrapa modulu nový dopyt
    // nepoznala, takže volanie `undefined` vyhodilo chybu ešte pred vznikom sľubu — a `.catch` ju
    // nezachytil. Naučil som sa to o pár hodín skôr pri inom paneli a zopakoval to tu.
    vi.mocked(getCiStatusApi).mockImplementation(() => {
      throw new TypeError("nie je to funkcia");
    });

    const { container } = render(<Riadok versionId="v-1" />);

    expect(container.querySelector('[data-testid="stav-zostavenia"]')).toBeNull();
  });

  it("bez vybranej verzie sa GitHubu nepýta vôbec", () => {
    render(<Riadok versionId={null} />);

    expect(getCiStatusApi).not.toHaveBeenCalled();
  });
});

describe("DEV-51 — zlyhaný beh jedným klikom", () => {
  it("pri červenom zostavení ponúkne odkaz na ten beh", async () => {
    vi.mocked(getCiStatusApi).mockResolvedValue({
      stav: "red",
      detail: "CI zlyhalo (postup Release smoke gate, beh 38042619283, failure)",
      sha: "48de080",
      url: "https://github.com/rauschiccsk/nex-inbox/actions/runs/38042619283",
    });

    render(<Riadok versionId="v-1" />);

    const odkaz = await screen.findByRole("link", { name: /Pozrieť beh/i });
    expect(odkaz).toHaveAttribute("href", "https://github.com/rauschiccsk/nex-inbox/actions/runs/38042619283");
  });

  it("pri zelenom zostavení odkaz nepridáva", async () => {
    vi.mocked(getCiStatusApi).mockResolvedValue({
      stav: "green",
      detail: "CI zelené (postup CI, beh 5)",
      sha: "abc",
      url: "https://github.com/x/y/actions/runs/5",
    });

    render(<Riadok versionId="v-1" />);

    expect(await screen.findByText(/Zostavenie prešlo/i)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Pozrieť beh/i })).not.toBeInTheDocument();
  });
});
