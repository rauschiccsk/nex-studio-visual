/**
 * DEV-72 — the customer form asks for what the app marks in its `.env.example`.
 *
 * 12.10.2026 the form had a „Tajný kľúč“ that career-asistent never read and a raw JSON nobody read; the e-mail and
 * password of the app's first admin had no field. Now the app's marked settings are fields in its own words, a secret
 * is write-only („uložené“), the „Tajný kľúč“ is offered only to an app that reads it, and the list says which
 * required setting a customer still lacks.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";

import CustomersPage from "@/pages/CustomersPage";
import type { CustomerRead, CustomerSettingsContract } from "@/types/customer";

const { listCustomersMock, createCustomerMock, updateCustomerMock, contractMock } = vi.hoisted(() => ({
  listCustomersMock: vi.fn(),
  createCustomerMock: vi.fn(),
  updateCustomerMock: vi.fn(),
  contractMock: vi.fn(),
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
  getCustomerSettingsContract: contractMock,
}));

const CAREER: CustomerSettingsContract = {
  settings: [
    { key: "APP_PUBLIC_URL", kind: "public_url", required: false, label: "APP_PUBLIC_URL" },
    { key: "BOOTSTRAP_ADMIN_EMAIL", kind: "customer", required: true, label: "E-mail prvého správcu" },
    { key: "BOOTSTRAP_ADMIN_PASSWORD", kind: "customer_secret", required: true, label: "Heslo prvého správcu" },
    { key: "SMTP_HOST", kind: "customer", required: false, label: "Server odchádzajúcej pošty" },
  ],
  problems: [],
  legacy_admin: false,
};

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
  created_at: "2026-10-12T08:00:00Z",
  updated_at: "2026-10-12T08:00:00Z",
};

describe("CustomersPage — the app's own settings (DEV-72)", () => {
  beforeEach(() => {
    listCustomersMock.mockReset();
    createCustomerMock.mockReset();
    updateCustomerMock.mockReset();
    contractMock.mockReset();
    contractMock.mockResolvedValue(CAREER);
  });

  it("names what a customer still lacks before a deploy", async () => {
    listCustomersMock.mockResolvedValue([ICC]);
    render(<CustomersPage />);

    expect(await screen.findByText("Pred nasadením doplň: Heslo prvého správcu")).toBeInTheDocument();
    expect(screen.queryByText(/tajný kľúč uložený/)).not.toBeInTheDocument();
  });

  it("asks for the app's settings in its own words and not for a secret it never reads", async () => {
    listCustomersMock.mockResolvedValue([ICC]);
    render(<CustomersPage />);
    fireEvent.click(await screen.findByTitle("Upraviť zákazníka"));

    expect(await screen.findByText("Nastavenia aplikácie pre tohto zákazníka")).toBeInTheDocument();
    expect(screen.getByDisplayValue("spravca@icc.sk")).toBeInTheDocument();
    expect(screen.getByText("Heslo prvého správcu")).toBeInTheDocument();
    expect(screen.getByText(/APP_PUBLIC_URL\) dopĺňa kokpit sám/)).toBeInTheDocument();
    expect(screen.queryByText("Tajný kľúč (pre zákazníka)")).not.toBeInTheDocument();
  });

  it("sends every plain setting and only the secrets typed in now", async () => {
    listCustomersMock.mockResolvedValue([{ ...ICC, secret_settings_set: ["BOOTSTRAP_ADMIN_PASSWORD"] }]);
    updateCustomerMock.mockResolvedValue(ICC);
    render(<CustomersPage />);
    fireEvent.click(await screen.findByTitle("Upraviť zákazníka"));

    const password = (await screen.findByPlaceholderText("uložené — prázdne = bez zmeny")) as HTMLInputElement;
    expect(password.type).toBe("password");
    fireEvent.change(screen.getByDisplayValue("spravca@icc.sk"), { target: { value: "admin@icc.sk" } });
    fireEvent.click(screen.getByRole("button", { name: /Uložiť/ }));

    await waitFor(() => expect(updateCustomerMock).toHaveBeenCalledTimes(1));
    const payload = updateCustomerMock.mock.calls[0]![1];
    expect(payload.settings).toEqual({ BOOTSTRAP_ADMIN_EMAIL: "admin@icc.sk", SMTP_HOST: "" });
    expect(payload.secret_settings).toEqual({});
  });

  it("an app that reads the „Tajný kľúč“ keeps it", async () => {
    contractMock.mockResolvedValue({ settings: [], problems: [], legacy_admin: true });
    listCustomersMock.mockResolvedValue([ICC]);
    render(<CustomersPage />);
    fireEvent.click(await screen.findByTitle("Upraviť zákazníka"));

    expect(await screen.findByText("Tajný kľúč (pre zákazníka)")).toBeInTheDocument();
    expect(screen.queryByText("Nastavenia aplikácie pre tohto zákazníka")).not.toBeInTheDocument();
  });
});
