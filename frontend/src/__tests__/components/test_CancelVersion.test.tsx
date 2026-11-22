/**
 * DEV-491, DEV-492 — „Zrušiť verziu“ on the version's page (not in the Riadiace centrum).
 *
 * Director 22.11.2026: „Zrušenie verzie nemá byť v Riadiacom centre - nepatrí tam. Má byť vo Verzie - Otvoriť.“ A
 * version that did nothing yet goes away whole; the server says whether it takes the cancel and why not.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

const { cancelVersion } = vi.hoisted(() => ({ cancelVersion: vi.fn() }));
vi.mock("@/services/api/versions", () => ({ cancelVersionApi: cancelVersion }));

import CancelVersion from "@/components/version/CancelVersion";
import { ApiError } from "@/services/api";
import type { Version } from "@/types/version";

const STARTED = "Verzia 2.0.0 sa už začala stavať — zrušiť sa dá len verzia, ktorá ešte nič neurobila.";

function version(over: Partial<Version> = {}): Version {
  return {
    id: "v-1",
    version_number: "1.1.1",
    status: "planned",
    fast_fix: true,
    cancellable: true,
    cancel_refusal: null,
    ...over,
  } as Version;
}

const opener = () => screen.getByRole("button", { name: "Zrušiť verziu" });
const confirmButton = () => screen.getAllByRole("button", { name: "Zrušiť verziu" })[1]!;

beforeEach(() => {
  vi.clearAllMocks();
  cancelVersion.mockResolvedValue({ version_number: "1.1.1", plane_warning: null });
});

describe("CancelVersion", () => {
  it("is not there on a finished version", () => {
    const { container: done } = render(<CancelVersion version={version({ status: "done" })} onCancelled={vi.fn()} />);
    const { container: released } = render(
      <CancelVersion version={version({ status: "released" })} onCancelled={vi.fn()} />,
    );

    expect(done).toBeEmptyDOMElement();
    expect(released).toBeEmptyDOMElement();
  });

  it("is greyed with the server's reason when the version cannot be cancelled now", () => {
    render(
      <CancelVersion
        version={version({ version_number: "2.0.0", fast_fix: false, status: "active", cancellable: false, cancel_refusal: STARTED })}
        onCancelled={vi.fn()}
      />,
    );

    expect(opener()).toBeDisabled();
    expect(opener()).toHaveAttribute("title", STARTED);
  });

  it("asks first, says what goes and cancels nothing on „Ponechať“", () => {
    const onCancelled = vi.fn();
    render(<CancelVersion version={version()} onCancelled={onCancelled} />);

    fireEvent.click(opener());
    expect(screen.getByRole("dialog")).toHaveTextContent(
      "Rýchla oprava 1.1.1 ešte nič neurobila. Zruší sa celá a jej číslo 1.1.1 sa uvoľní pre ďalšiu verziu.",
    );
    expect(screen.getByRole("dialog")).toHaveTextContent("Tikety zaradené do verzie sa v Plane vrátia do Todo.");
    fireEvent.click(screen.getByRole("button", { name: "Ponechať" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(cancelVersion).not.toHaveBeenCalled();
    expect(onCancelled).not.toHaveBeenCalled();
  });

  it("a regular version that never started is named as a version", () => {
    render(<CancelVersion version={version({ version_number: "2.0.0", fast_fix: false })} onCancelled={vi.fn()} />);

    fireEvent.click(opener());

    expect(screen.getByRole("dialog")).toHaveTextContent("Verzia 2.0.0 ešte nič neurobila.");
  });

  it("cancels on confirmation and leaves the page", async () => {
    const onCancelled = vi.fn();
    render(<CancelVersion version={version()} onCancelled={onCancelled} />);
    fireEvent.click(opener());
    fireEvent.click(confirmButton());

    await waitFor(() => expect(onCancelled).toHaveBeenCalledTimes(1));
    expect(cancelVersion).toHaveBeenCalledWith("v-1");
  });

  it("when a ticket did not go back to Todo, says so before leaving", async () => {
    const warning = "Verzia je zrušená, ale tiket DEV-489 sa v Plane nevrátil do Todo: Plane neodpovedá.";
    cancelVersion.mockResolvedValue({ version_number: "1.1.1", plane_warning: warning });
    const onCancelled = vi.fn();
    render(<CancelVersion version={version()} onCancelled={onCancelled} />);
    fireEvent.click(opener());
    fireEvent.click(confirmButton());

    expect(await screen.findByRole("status")).toHaveTextContent(warning);
    expect(onCancelled).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Rozumiem" }));
    expect(onCancelled).toHaveBeenCalledTimes(1);
  });

  it("a refusal from the server stays in the dialog in words, the version stays", async () => {
    const working = "Staviteľ na verzii 1.1.1 práve pracuje — zrušiť ju pôjde, keď ťah skončí.";
    cancelVersion.mockRejectedValue(new ApiError(409, "Conflict", { detail: working }));
    const onCancelled = vi.fn();
    render(<CancelVersion version={version()} onCancelled={onCancelled} />);
    fireEvent.click(opener());
    fireEvent.click(confirmButton());

    expect(await screen.findByText(/Zrušenie verzie zlyhalo/)).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toHaveTextContent(working);
    expect(onCancelled).not.toHaveBeenCalled();
  });
});
