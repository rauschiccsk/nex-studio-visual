/**
 * DEV-437 — a stored customer secret reads as asterisks; only the Administrator shows it with the eye, one secret on
 * that click; showing it is no change.
 *
 * The Director, 19.11.2026: „ak zadám token majú tam byť stále hviezdičky "*********" a na konci riadku možnosť
 * zobrazenia daného tokenu, toto bude mať len Administrátor.“
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import CustomersPage from "@/pages/CustomersPage";
import type { CustomerRead, CustomerSettingsContract } from "@/types/customer";


/** What a stored secret reads as (SecretInput) — the same asterisks for every secret. */
const STORED_MASK = "••••••••••";
const { listCustomersMock, createCustomerMock, updateCustomerMock, contractMock, revealMock, authUser } = vi.hoisted(() => ({
  listCustomersMock: vi.fn(),
  createCustomerMock: vi.fn(),
  updateCustomerMock: vi.fn(),
  contractMock: vi.fn(),
  revealMock: vi.fn(),
  authUser: { current: null as null | { username: string } },
}));

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return { ...actual, useNavigate: () => vi.fn() };
});
vi.mock("@/store/activeContextStore", () => ({
  useActiveContextStore: (selector: (s: { selectedProject: { slug: string; name: string } }) => unknown) =>
    selector({ selectedProject: { slug: "career-asistent", name: "Career Asistent" } }),
}));
vi.mock("@/services/api/customers", () => ({
  listCustomers: listCustomersMock,
  createCustomer: createCustomerMock,
  updateCustomer: updateCustomerMock,
  deleteCustomer: vi.fn(),
  // DEV-274: no other machine known — the form offers no choice of the PROD machine.
  listProdMachines: () => Promise.resolve([]),
  getCustomerSettingsContract: contractMock,
  revealCustomerSecret: revealMock,
}));
vi.mock("@/store/authStore", () => ({
  useAuthStore: (selector: (s: { user: unknown }) => unknown) => selector({ user: authUser.current }),
}));

const ICC: CustomerRead = {
  id: "cust-icc",
  project_id: "proj-career",
  name: "ICC s.r.o.",
  slug: "icc",
  subdomain: "icc",
  integrations: null,
  notes: null,
  has_secret: true,
  settings: { BOOTSTRAP_ADMIN_EMAIL: "spravca@icc.sk" },
  secret_settings_set: [],
  prod_host: "",
  prod_machine: "ANDROS",
  created_at: "2026-10-12T08:00:00Z",
  updated_at: "2026-10-12T08:00:00Z",
};


const HELPDESK: CustomerSettingsContract = {
  settings: [{ key: "TWILIO_AUTH_TOKEN", kind: "customer_secret", required: true, label: "Auth Token účtu Twilio" }],
  problems: [],
  legacy_admin: true,
};

beforeEach(() => {
  vi.clearAllMocks();
  contractMock.mockResolvedValue(HELPDESK);
  listCustomersMock.mockResolvedValue([{ ...ICC, has_secret: true, secret_settings_set: ["TWILIO_AUTH_TOKEN"] }]);
  updateCustomerMock.mockResolvedValue(ICC);
  revealMock.mockImplementation((_id: string, key: string) =>
    Promise.resolve({ key, value: key === "ADMIN_INITIAL_PASSWORD" ? "Init-1" : "tok-123" }),
  );
});

async function openCard() {
  render(<CustomersPage />);
  fireEvent.click(await screen.findByTitle("Upraviť zákazníka"));
  return (await screen.findByLabelText("Auth Token účtu Twilio")) as HTMLInputElement;
}

describe("CustomersPage — stored secrets as asterisks, the Administrator's eye (DEV-437)", () => {
  it("a stored secret reads as asterisks — always as many — and stays hidden", async () => {
    authUser.current = { username: "admin" };
    const token = await openCard();

    expect(token.placeholder).toBe(STORED_MASK);
    expect(token.type).toBe("password");
    expect(token.value).toBe("");
  });

  it("the Administrator's eye shows the stored secret, and hides it again", async () => {
    authUser.current = { username: "admin" };
    const token = await openCard();

    fireEvent.click(screen.getByRole("button", { name: "Zobraziť: Auth Token účtu Twilio" }));

    await waitFor(() => expect(token.value).toBe("tok-123"));
    expect(token.type).toBe("text");
    expect(revealMock).toHaveBeenCalledWith(ICC.id, "TWILIO_AUTH_TOKEN");
    fireEvent.click(screen.getByRole("button", { name: "Skryť: Auth Token účtu Twilio" }));
    expect(token.value).toBe("");
    expect(token.type).toBe("password");
  });

  it("the Administrator sees the initial admin password the same way", async () => {
    authUser.current = { username: "admin" };
    await openCard();

    fireEvent.click(screen.getByRole("button", { name: "Zobraziť: Inicializačné heslo administrátora" }));

    await waitFor(() => expect(revealMock).toHaveBeenCalledWith(ICC.id, "ADMIN_INITIAL_PASSWORD"));
    expect(await screen.findByDisplayValue("Init-1")).toBeInTheDocument();
  });

  it("showing a secret is no change — saving sends only what was typed", async () => {
    authUser.current = { username: "admin" };
    const token = await openCard();
    fireEvent.click(screen.getByRole("button", { name: "Zobraziť: Auth Token účtu Twilio" }));
    await waitFor(() => expect(token.value).toBe("tok-123"));

    fireEvent.click(screen.getByRole("button", { name: /Uložiť/ }));

    await waitFor(() => expect(updateCustomerMock).toHaveBeenCalledTimes(1));
    const payload = updateCustomerMock.mock.calls[0]![1];
    expect(payload.secret_settings).toEqual({});
    expect(payload.secret).toBeNull();
  });

  it("anyone else gets no eye and nothing is asked of the vault", async () => {
    authUser.current = { username: "manazer" };
    await openCard();

    expect(screen.queryByRole("button", { name: /Zobraziť:/ })).toBeNull();
    expect(revealMock).not.toHaveBeenCalled();
  });

  it("a secret not stored yet has no eye", async () => {
    authUser.current = { username: "admin" };
    listCustomersMock.mockResolvedValue([{ ...ICC, has_secret: false, secret_settings_set: [] }]);
    const token = await openCard();

    expect(token.placeholder).toBe("uloží sa bezpečne");
    expect(screen.queryByRole("button", { name: /Zobraziť:/ })).toBeNull();
  });
});
