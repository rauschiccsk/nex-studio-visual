/**
 * DEV-511 — the documents from the internet the cockpit downloaded into the project, where the Manažér starts the
 * Špecifikácia: what came, what did not and why, and „Stiahnuť znova“.
 *
 * 25.11.2026 the brief of NEX DualBook left ten documents to the Manažér to download by hand. The Director: „…mám ich
 * vložiť ručne čo ja robiť nebudem.“
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import ProjectReferences from "@/components/version/ProjectReferences";

const { getMock, retryMock } = vi.hoisted(() => ({ getMock: vi.fn(), retryMock: vi.fn() }));

vi.mock("@/services/api/projects", () => ({
  getReferenceDocsApi: getMock,
  retryReferenceDocsApi: retryMock,
}));

const CAME = {
  path: "docs/reference/dph/dph2025.xsd",
  url: "https://ekr.financnasprava.sk/Formulare/XSD/dph2025.xsd",
  description: "Schéma XSD priznania k DPH",
  ok: true,
  said: null,
  size: 9017,
  fetched_at: "2026-11-25T21:20:00+00:00",
};
const REFUSED = {
  path: "docs/reference/banka/prirucka.pdf",
  url: "https://www.unicreditbank.sk/b/prirucka.pdf",
  description: "Príručka banky",
  ok: false,
  said: "web odpovedal 403",
  size: null,
  fetched_at: null,
};

describe("ProjectReferences (DEV-511)", () => {
  beforeEach(() => {
    getMock.mockReset();
    retryMock.mockReset();
  });

  it("shows nothing for a project whose brief listed no documents", async () => {
    getMock.mockResolvedValue({ running: false, items: [] });
    const { container } = render(<ProjectReferences projectId="p1" canRetry />);

    await waitFor(() => expect(getMock).toHaveBeenCalledWith("p1"));
    expect(container).toBeEmptyDOMElement();
  });

  it("lists what came and what did not with the reason, each linked to its source", async () => {
    getMock.mockResolvedValue({ running: false, items: [CAME, REFUSED] });
    render(<ProjectReferences projectId="p1" canRetry />);

    const section = await screen.findByRole("region", { name: "Podklady z internetu" });
    const items = within(section).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    const [came, refused] = items as [HTMLElement, HTMLElement];
    expect(came).toHaveTextContent("✓ Schéma XSD priznania k DPH docs/reference/dph/dph2025.xsd (9 kB)");
    expect(refused).toHaveTextContent(
      "✗ Príručka banky docs/reference/banka/prirucka.pdf — nestiahnuté: web odpovedal 403",
    );
    expect(within(refused).getByRole("link", { name: "Príručka banky" })).toHaveAttribute("href", REFUSED.url);
  });

  it("„Stiahnuť znova“ asks the cockpit and shows that the download goes on", async () => {
    getMock.mockResolvedValue({ running: false, items: [CAME, REFUSED] });
    retryMock.mockResolvedValue({ running: true, items: [CAME, REFUSED] });
    render(<ProjectReferences projectId="p1" canRetry />);

    fireEvent.click(await screen.findByRole("button", { name: "Stiahnuť znova" }));

    await waitFor(() => expect(retryMock).toHaveBeenCalledWith("p1"));
    expect(await screen.findByText("Sťahujem podklady…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stiahnuť znova" })).toBeDisabled();
  });

  it("offers no retry when everything came, nor to someone who may not act on the project", async () => {
    getMock.mockResolvedValue({ running: false, items: [CAME] });
    const { unmount } = render(<ProjectReferences projectId="p1" canRetry />);
    await screen.findByRole("region", { name: "Podklady z internetu" });
    expect(screen.queryByRole("button", { name: "Stiahnuť znova" })).not.toBeInTheDocument();
    unmount();

    getMock.mockResolvedValue({ running: false, items: [CAME, REFUSED] });
    render(<ProjectReferences projectId="p1" canRetry={false} />);
    await screen.findByRole("region", { name: "Podklady z internetu" });
    expect(screen.queryByRole("button", { name: "Stiahnuť znova" })).not.toBeInTheDocument();
  });
});
