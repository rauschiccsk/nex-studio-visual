/**
 * DEV-274 — the machine a customer's PROD runs on is chosen on the customer form.
 *
 * 31.10.2026 the Director decided ICC's production belongs on MAGER; the machine lived only in the database
 * (`customers.prod_host`) and no screen could show or change it. The form now offers the machines the cockpit knows,
 * loads the customer's own, sends the choice, and the list says where each customer's PROD runs.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import CustomersPage from "@/pages/CustomersPage";
import type { CustomerRead, ProdMachine } from "@/types/customer";

const { listCustomersMock, updateCustomerMock, machinesMock, contextMock } =
  vi.hoisted(() => ({
    listCustomersMock: vi.fn(),
    updateCustomerMock: vi.fn(),
    machinesMock: vi.fn(),
    contextMock: {
      selectedProject: { slug: "nex-manager", name: "NEX Manager" },
    },
  }));

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => vi.fn() };
});
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: typeof contextMock) => unknown) =>
    selector(contextMock),
}));
vi.mock("@/services/api/customers", () => ({
  listCustomers: listCustomersMock,
  createCustomer: vi.fn(),
  updateCustomer: updateCustomerMock,
  deleteCustomer: vi.fn(),
  listProdMachines: machinesMock,
  getCustomerSettingsContract: () =>
    Promise.resolve({ settings: [], problems: [], legacy_admin: false }),
}));

const MACHINES: ProdMachine[] = [
  { value: "", label: "ANDROS" },
  { value: "mager", label: "MAGER" },
];

const ICC: CustomerRead = {
  id: "cust-icc",
  project_id: "proj-nm",
  name: "ICC s.r.o.",
  slug: "icc",
  subdomain: "icc",
  integrations: null,
  notes: null,
  has_secret: false,
  settings: {},
  secret_settings_set: [],
  prod_host: "",
  prod_machine: "ANDROS",
  created_at: "2026-07-01T00:00:00Z",
  updated_at: "2026-07-01T00:00:00Z",
};

describe("CustomersPage — the PROD machine (DEV-274)", () => {
  beforeEach(() => {
    listCustomersMock.mockReset();
    updateCustomerMock.mockReset();
    machinesMock.mockReset();
  });

  it("says on the list where each customer's PROD runs", async () => {
    machinesMock.mockResolvedValue(MACHINES);
    listCustomersMock.mockResolvedValue([
      ICC,
      {
        ...ICC,
        id: "cust-mag",
        name: "MÁGERSTAV s.r.o.",
        slug: "mager",
        prod_host: "mager",
        prod_machine: "MAGER",
      },
    ]);

    render(<CustomersPage />);

    expect(
      await screen.findByText("PROD na stroji ANDROS"),
    ).toBeInTheDocument();
    expect(screen.getByText("PROD na stroji MAGER")).toBeInTheDocument();
  });

  it("moves ICC to MAGER: the form offers the machines, loads ICC's and sends the choice", async () => {
    machinesMock.mockResolvedValue(MACHINES);
    listCustomersMock.mockResolvedValue([ICC]);
    updateCustomerMock.mockResolvedValue({
      ...ICC,
      prod_host: "mager",
      prod_machine: "MAGER",
    });
    render(<CustomersPage />);
    fireEvent.click(await screen.findByTitle("Upraviť zákazníka"));

    const select = (await screen.findByLabelText(
      /^Ostrý stroj \(PROD\)/,
    )) as HTMLSelectElement;
    expect([...select.options].map((o) => o.textContent)).toEqual([
      "ANDROS",
      "MAGER",
    ]);
    expect(select.value).toBe("");
    expect(screen.getByText(/Zmena nič nepresunie/)).toBeInTheDocument();

    fireEvent.change(select, { target: { value: "mager" } });
    fireEvent.click(screen.getByRole("button", { name: /Uložiť/ }));

    await waitFor(() => expect(updateCustomerMock).toHaveBeenCalledTimes(1));
    expect(updateCustomerMock).toHaveBeenCalledWith(
      "cust-icc",
      expect.objectContaining({ prod_host: "mager" }),
    );
  });

  it("opens a customer already on MAGER with MAGER chosen", async () => {
    machinesMock.mockResolvedValue(MACHINES);
    listCustomersMock.mockResolvedValue([
      { ...ICC, prod_host: "mager", prod_machine: "MAGER" },
    ]);
    render(<CustomersPage />);
    fireEvent.click(await screen.findByTitle("Upraviť zákazníka"));

    expect(
      (
        (await screen.findByLabelText(
          /^Ostrý stroj \(PROD\)/,
        )) as HTMLSelectElement
      ).value,
    ).toBe("mager");
  });
});
