/**
 * CustomersPage — the edit-customer flow (obs #6, batch-2).
 *
 * The Zákazníci tab could previously only add + delete customers. These pin the new EDIT affordance:
 *   - the pencil button loads a customer's fields into the form and switches it to edit mode (pre-populated);
 *   - submitting in edit mode calls updateCustomer (PATCH) — NOT createCustomer — and refreshes the list.
 * The write-only secret is intentionally left blank (never echoed back), so a blank secret PATCHes null =
 * "leave the stored secret unchanged".
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import CustomersPage from "@/pages/CustomersPage";
import type { CustomerRead } from "@/types/customer";

// ── Hoisted mocks ─────────────────────────────────────────────────────────────

const { listCustomersMock, createCustomerMock, updateCustomerMock, deleteCustomerMock, contextMock } =
  vi.hoisted(() => ({
    listCustomersMock: vi.fn(),
    createCustomerMock: vi.fn(),
    updateCustomerMock: vi.fn(),
    deleteCustomerMock: vi.fn(),
    contextMock: {
      selectedProject: { slug: "demo", name: "Demo" } as { slug: string; name: string } | null,
    },
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
  createCustomer: createCustomerMock,
  updateCustomer: updateCustomerMock,
  deleteCustomer: deleteCustomerMock,
}));

const CUSTOMER: CustomerRead = {
  id: "cust-1",
  project_id: "proj-1",
  name: "ICC s.r.o.",
  slug: "icc",
  subdomain: "icc",
  integrations: { erp: "nex-genesis" },
  notes: "Interný zákazník.",
  has_secret: true,
  created_at: "2026-07-01T00:00:00Z",
  updated_at: "2026-07-01T00:00:00Z",
};

describe("CustomersPage — edit-customer flow (obs #6)", () => {
  beforeEach(() => {
    listCustomersMock.mockReset();
    createCustomerMock.mockReset();
    updateCustomerMock.mockReset();
    deleteCustomerMock.mockReset();
  });

  it("enters edit mode with the customer's fields pre-populated", async () => {
    listCustomersMock.mockResolvedValue([CUSTOMER]);
    render(<CustomersPage />);
    await screen.findByText("ICC s.r.o.");

    // Click the row's pencil → the form opens in edit mode, pre-populated.
    fireEvent.click(screen.getByTitle("Upraviť zákazníka"));

    expect(screen.getByText("Upraviť zákazníka")).toBeInTheDocument(); // the edit-mode form heading
    expect(screen.getByDisplayValue("ICC s.r.o.")).toBeInTheDocument(); // name
    expect(screen.getAllByDisplayValue("icc").length).toBeGreaterThanOrEqual(2); // slug + subdomain
    expect(screen.getByDisplayValue("Interný zákazník.")).toBeInTheDocument(); // notes
    expect(screen.getByDisplayValue(/nex-genesis/)).toBeInTheDocument(); // integrations JSON
  });

  it("submits an edit via updateCustomer (PATCH), not createCustomer, and refreshes the list", async () => {
    listCustomersMock.mockResolvedValue([CUSTOMER]);
    updateCustomerMock.mockResolvedValue({ ...CUSTOMER, name: "ICC upravené" });
    render(<CustomersPage />);
    await screen.findByText("ICC s.r.o.");
    expect(listCustomersMock).toHaveBeenCalledTimes(1); // initial load

    // findByTitle (not getByTitle): under full-suite parallelism the pencil button can render a tick after
    // the row text — wait for it so the assertion is order/timing-independent (pre-existing flake).
    fireEvent.click(await screen.findByTitle("Upraviť zákazníka"));
    fireEvent.change(screen.getByDisplayValue("ICC s.r.o."), { target: { value: "ICC upravené" } });
    fireEvent.click(screen.getByRole("button", { name: /Uložiť/ }));

    // The UPDATE path is taken with the customer id + edited fields (blank secret ⇒ null = leave unchanged).
    await waitFor(() => expect(updateCustomerMock).toHaveBeenCalledTimes(1));
    expect(updateCustomerMock).toHaveBeenCalledWith(
      "cust-1",
      expect.objectContaining({ name: "ICC upravené", slug: "icc", secret: null }),
    );
    // The add path was never taken.
    expect(createCustomerMock).not.toHaveBeenCalled();
    // The list refreshed after the successful update (a second load).
    await waitFor(() => expect(listCustomersMock).toHaveBeenCalledTimes(2));
  });
});
