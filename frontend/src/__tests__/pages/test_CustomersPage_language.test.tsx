/**
 * DEV-395 — the language a customer's installations start in is chosen on the customer form, only for an app that
 * speaks more than Slovak; an app that speaks Slovak alone offers and sends nothing.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import CustomersPage from "@/pages/CustomersPage";
import type { CustomerRead, CustomerSettingsContract } from "@/types/customer";

const { listCustomersMock, updateCustomerMock, contractMock, contextMock } = vi.hoisted(() => ({
  listCustomersMock: vi.fn(),
  updateCustomerMock: vi.fn(),
  contractMock: vi.fn(),
  contextMock: { selectedProject: { slug: "orthodox-hermenia", name: "Orthodox Hermenia" } },
}));

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => vi.fn() };
});
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: typeof contextMock) => unknown) => selector(contextMock),
}));
vi.mock("@/services/api/customers", () => ({
  listCustomers: listCustomersMock,
  createCustomer: vi.fn(),
  updateCustomer: updateCustomerMock,
  deleteCustomer: vi.fn(),
  listProdMachines: () => Promise.resolve([]),
  getCustomerSettingsContract: contractMock,
}));

const SKIT: CustomerRead = {
  id: "cust-skit",
  project_id: "proj-ohm",
  name: "SKIT o.z.",
  slug: "skit",
  subdomain: "skit",
  integrations: null,
  notes: null,
  has_secret: false,
  settings: {},
  secret_settings_set: [],
  prod_host: "mager",
  prod_machine: "MAGER",
  language: "sk",
  created_at: "2026-11-15T00:00:00Z",
  updated_at: "2026-11-15T00:00:00Z",
};

function contract(languages: CustomerSettingsContract["languages"]): CustomerSettingsContract {
  return { settings: [], problems: [], legacy_admin: false, languages };
}

describe("CustomersPage — the installation's language (DEV-395)", () => {
  beforeEach(() => {
    listCustomersMock.mockReset().mockResolvedValue([SKIT]);
    updateCustomerMock.mockReset().mockResolvedValue(SKIT);
    contractMock.mockReset();
  });

  it("offers the app's languages, loads the customer's and sends the choice", async () => {
    contractMock.mockResolvedValue(
      contract([
        { code: "hu", label: "maďarčina" },
        { code: "sk", label: "slovenčina" },
      ]),
    );
    render(<CustomersPage />);
    fireEvent.click(await screen.findByTitle("Upraviť zákazníka"));

    const select = (await screen.findByLabelText(/^Jazyk inštalácie/)) as HTMLSelectElement;
    expect([...select.options].map((o) => o.textContent)).toEqual([
      "Hlavný jazyk aplikácie (maďarčina)",
      "maďarčina",
      "slovenčina",
    ]);
    expect(select.value).toBe("sk");

    fireEvent.change(select, { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: /Uložiť/ }));

    await waitFor(() => expect(updateCustomerMock).toHaveBeenCalledTimes(1));
    expect(updateCustomerMock).toHaveBeenCalledWith("cust-skit", expect.objectContaining({ language: "" }));
  });

  it("an app that speaks Slovak alone offers nothing and sends nothing", async () => {
    contractMock.mockResolvedValue(contract([]));
    render(<CustomersPage />);
    fireEvent.click(await screen.findByTitle("Upraviť zákazníka"));
    await waitFor(() => expect(contractMock).toHaveBeenCalled());

    expect(screen.queryByLabelText(/^Jazyk inštalácie/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Uložiť/ }));

    await waitFor(() => expect(updateCustomerMock).toHaveBeenCalledTimes(1));
    expect(updateCustomerMock.mock.calls[0]?.[1]).not.toHaveProperty("language");
  });
});
