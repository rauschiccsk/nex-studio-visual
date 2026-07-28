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
import { ApiError } from "@/services/api";
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

/**
 * Deploy-safe identifiers (audit fix).
 *
 * "Skratka" / "Subdoména" ARE the customer's deploy identity — the backend derives the instance directory
 * from `(subdomain or slug).lower()` and validates it against `^[a-z0-9][a-z0-9-]*$`. The form used to accept
 * anything short enough, so `andros s.r.o.` saved fine and detonated days later as a failed deploy, with
 * nothing on screen linking the two. These pin the rule to the moment of typing.
 */
describe("CustomersPage — deploy-safe identifiers", () => {
  beforeEach(() => {
    listCustomersMock.mockReset();
    createCustomerMock.mockReset();
    updateCustomerMock.mockReset();
    deleteCustomerMock.mockReset();
  });

  async function openAddForm() {
    listCustomersMock.mockResolvedValue([]);
    render(<CustomersPage />);
    await screen.findByText(/Zatiaľ žiadni zákazníci/);
    fireEvent.click(screen.getByRole("button", { name: /Pridať zákazníka/ }));
  }

  it("shows the rule while typing and refuses to save a slug the deploy path cannot use", async () => {
    await openAddForm();

    fireEvent.change(screen.getByLabelText(/Názov/), { target: { value: "ANDROS s.r.o." } });
    const slugInput = screen.getByLabelText(/Skratka/);
    fireEvent.change(slugInput, { target: { value: "andros s.r.o." } });

    // Taught immediately, at the field — not days later at deploy time.
    expect(screen.getByText(/Skratka smie obsahovať len malé písmená a-z/)).toBeInTheDocument();
    expect(slugInput).toHaveAttribute("aria-invalid", "true");

    fireEvent.click(screen.getByRole("button", { name: /Uložiť/ }));

    // The request is never sent — the mistake is fixable right here.
    await waitFor(() => expect(screen.getAllByText(/Skratka smie obsahovať/).length).toBeGreaterThan(1));
    expect(createCustomerMock).not.toHaveBeenCalled();
  });

  it("refuses a subdomain the deploy path cannot use, naming the subdomain (not the slug)", async () => {
    await openAddForm();

    fireEvent.change(screen.getByLabelText(/Názov/), { target: { value: "ANDROS" } });
    fireEvent.change(screen.getByLabelText(/Skratka/), { target: { value: "andros" } });
    fireEvent.change(screen.getByLabelText(/Subdoména/), { target: { value: "andros_uat" } });

    expect(screen.getByText(/Subdoména smie obsahovať len malé písmená a-z/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Uložiť/ }));
    await waitFor(() => expect(screen.getAllByText(/Subdoména smie obsahovať/).length).toBeGreaterThan(1));
    expect(createCustomerMock).not.toHaveBeenCalled();
  });

  it("lowercases the identifiers as they are typed, so what is stored is what gets deployed", async () => {
    await openAddForm();
    createCustomerMock.mockResolvedValue({});

    fireEvent.change(screen.getByLabelText(/Názov/), { target: { value: "ANDROS" } });
    const slugInput = screen.getByLabelText(/Skratka/);
    const subdomainInput = screen.getByLabelText(/Subdoména/);
    fireEvent.change(slugInput, { target: { value: "ANDROS" } });
    fireEvent.change(subdomainInput, { target: { value: "ANDROS-UAT" } });

    expect(slugInput).toHaveValue("andros");
    expect(subdomainInput).toHaveValue("andros-uat");

    fireEvent.click(screen.getByRole("button", { name: /Uložiť/ }));
    await waitFor(() => expect(createCustomerMock).toHaveBeenCalledTimes(1));
    expect(createCustomerMock).toHaveBeenCalledWith(
      "demo",
      expect.objectContaining({ slug: "andros", subdomain: "andros-uat" }),
    );
  });

  it("keeps a legacy mixed-case customer editable instead of trapping it in its own validation", async () => {
    // A row saved before the rule existed. Loading it must not put the form into a state it cannot leave.
    listCustomersMock.mockResolvedValue([{ ...CUSTOMER, slug: "ANDROS", subdomain: "ANDROS" }]);
    updateCustomerMock.mockResolvedValue({});
    render(<CustomersPage />);
    await screen.findByText("ICC s.r.o.");

    fireEvent.click(await screen.findByTitle("Upraviť zákazníka"));
    expect(screen.getByLabelText(/Skratka/)).toHaveValue("andros"); // canonicalised on load
    expect(screen.queryByText(/Skratka smie obsahovať/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Uložiť/ }));
    await waitFor(() => expect(updateCustomerMock).toHaveBeenCalledTimes(1));
    expect(updateCustomerMock).toHaveBeenCalledWith("cust-1", expect.objectContaining({ slug: "andros" }));
  });

  it("explains a 409 instance-directory collision as a subdomain clash, not a duplicate skratka", async () => {
    await openAddForm();
    createCustomerMock.mockRejectedValue(
      new ApiError(
        409,
        "Customer 'a' already exists in this project with instance directory 'shared' — two customers cannot share one instance directory",
      ),
    );

    fireEvent.change(screen.getByLabelText(/Názov/), { target: { value: "B" } });
    fireEvent.change(screen.getByLabelText(/Skratka/), { target: { value: "b" } });
    fireEvent.change(screen.getByLabelText(/Subdoména/), { target: { value: "shared" } });
    fireEvent.click(screen.getByRole("button", { name: /Uložiť/ }));

    // The message names the actual fix (change the subdomain) — the old wording blamed the skratka, which
    // was not the field in conflict.
    expect(await screen.findByText(/tú istú subdoménu/)).toBeInTheDocument();
    expect(screen.queryByText("Zákazník s touto skratkou už v projekte existuje.")).not.toBeInTheDocument();
  });
});
