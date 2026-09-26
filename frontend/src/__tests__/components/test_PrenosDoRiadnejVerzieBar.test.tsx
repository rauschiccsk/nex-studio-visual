/**
 * PrenosDoRiadnejVerzieBar (ICCINT-139) — odpoveď na otázku o dráhe sa dá aj vykonať.
 *
 * AI Agent na začiatku NEX Inbox 1.5.2 napísal, že rozsah presahuje rýchlu opravu — tri body menia
 * Špecifikáciu a dva žiadajú prestavbu údajov v ostrej prevádzke so 117 faktúrami — a žiadal o potvrdenie.
 * Manažér potvrdil. Stavba aj tak dobehla ako rýchla oprava a migrácia databázy prešla ĽAHKOU kontrolou.
 *
 * ⚠️ Čo tieto stráže NEDOVOLIA zmeniť:
 *  - panel sa NEVYKRESLÍ, kým backend tú akciu neponúka (inak by tlačidlo svietilo tam, kde nič neurobí);
 *  - text povie, ČO sa stane — že vznikne nová verzia a táto stavba sa pozastaví;
 *  - zlyhanie sa ukáže a panel zostane použiteľný (stavba na serveri sa nezmenila).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import PrenosDoRiadnejVerzieBar from "@/components/riadiace/PrenosDoRiadnejVerzieBar";
import { postPipelineActionApi, type PipelineBoard } from "@/services/api/pipeline";

vi.mock("@/services/api/pipeline", () => ({ postPipelineActionApi: vi.fn() }));

function board(akcie: string[]): PipelineBoard {
  return { state: null, recent_messages: [], available_actions: akcie } as unknown as PipelineBoard;
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("Prenos rýchlej opravy do riadnej verzie", () => {
  it("⚠️ bez ponuky backendu sa nevykreslí nič", () => {
    const { container } = render(
      <PrenosDoRiadnejVerzieBar board={board(["ask", "uprav"])} versionId="v-1" onBoard={vi.fn()} />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("keď backend akciu ponúka, povie aj čo sa stane", () => {
    render(<PrenosDoRiadnejVerzieBar board={board(["na_riadnu_verziu"])} versionId="v-1" onBoard={vi.fn()} />);

    expect(screen.getByText(/beží ako rýchla oprava/i)).toBeInTheDocument();
    expect(screen.getByText(/založí novú verziu s tým istým zadaním/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Preniesť do riadnej verzie/i })).toBeInTheDocument();
  });

  it("kliknutie pošle práve tú akciu a vráti čerstvú stavbu", async () => {
    const dalsia = board([]);
    vi.mocked(postPipelineActionApi).mockResolvedValue(dalsia);
    const onBoard = vi.fn();
    render(<PrenosDoRiadnejVerzieBar board={board(["na_riadnu_verziu"])} versionId="v-1" onBoard={onBoard} />);

    fireEvent.click(screen.getByRole("button", { name: /Preniesť do riadnej verzie/i }));

    await waitFor(() =>
      expect(postPipelineActionApi).toHaveBeenCalledWith("v-1", { action: "na_riadnu_verziu" }),
    );
    await waitFor(() => expect(onBoard).toHaveBeenCalledWith(dalsia));
  });

  it("⚠️ zlyhanie sa ukáže — nezmizne bez slova", async () => {
    vi.mocked(postPipelineActionApi).mockRejectedValue(new Error("engine odmietol"));
    const onBoard = vi.fn();
    render(<PrenosDoRiadnejVerzieBar board={board(["na_riadnu_verziu"])} versionId="v-1" onBoard={onBoard} />);

    fireEvent.click(screen.getByRole("button", { name: /Preniesť do riadnej verzie/i }));

    expect(await screen.findByText(/Prenos do riadnej verzie zlyhal/i)).toBeInTheDocument();
    expect(onBoard).not.toHaveBeenCalled();
  });
});
