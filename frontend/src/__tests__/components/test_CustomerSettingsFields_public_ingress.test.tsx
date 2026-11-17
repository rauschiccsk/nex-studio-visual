/**
 * DEV-409 — an app that declares a narrow public entry shows it in the customer's settings: which paths go out to the
 * internet, and that the cockpit sets the tunnel up and writes the address — nothing for the Manažér to enter.
 * Director 17.11.2026: „Súhlasím“.
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import CustomerSettingsFields from "@/components/customers/CustomerSettingsFields";
import type { CustomerSettingsContract } from "@/types/customer";

const contract = {
  settings: [{ key: "PUBLIC_INGRESS_URL", kind: "public_ingress", required: false, label: "/public/v1/" }],
  problems: [],
  legacy_admin: false,
} as unknown as CustomerSettingsContract;

describe("CustomerSettingsFields — the narrow public entry (DEV-409)", () => {
  it("says which paths go out and that the cockpit writes the address — no field to fill", () => {
    render(
      <CustomerSettingsFields
        contract={contract}
        values={{}}
        onValue={vi.fn()}
        secrets={{}}
        onSecret={vi.fn()}
        storedSecrets={[]}
      />,
    );

    expect(
      screen.getByText(
        "Úzky verejný vstup: z internetu pôjdu do aplikácie len cesty /public/v1/ — tunel Cloudflare zriadi kokpit " +
          "pri nasadení a adresu (PUBLIC_INGRESS_URL) zapíše sám.",
        { normalizer: (text) => text.replace(/\s+/g, " ").trim() },
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
  });
});
