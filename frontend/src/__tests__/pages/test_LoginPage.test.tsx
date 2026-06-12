/**
 * Unit tests for {@link LoginPage} (reconciled with the current inline form —
 * E1 Phase A / CR-NS-047 FE-test cleanup).
 *
 * Current LoginPage: useNavigate + useLocation (redirect to `location.state.from`
 * or "/"); id-based Slovak inputs (Používateľské meno / Heslo); a single generic
 * error ("Nesprávne prihlasovacie údaje.") on any login failure; submit disabled
 * while loading or when fields are empty.
 */

import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// ── Mocks ───────────────────────────────────────────────────────────────────

const navigateMock = vi.fn();
let locationMock: { state: unknown } = { state: null };

vi.mock("react-router-dom", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-router-dom")>();
  return {
    ...actual,
    useNavigate: () => navigateMock,
    useLocation: () => ({ pathname: "/login", search: "", hash: "", key: "t", state: locationMock.state }),
  };
});

const loginMock: Mock = vi.fn();
let tokenValue: string | null = null;
vi.mock("@/store/authStore", () => ({
  useAuthStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({ login: loginMock, token: tokenValue }),
}));

beforeEach(() => {
  vi.clearAllMocks();
  locationMock = { state: null };
  tokenValue = null;
});

async function importPage() {
  return (await import("@/pages/LoginPage")).default;
}

// ── Tests ────────────────────────────────────────────────────────────────────

describe("LoginPage", () => {
  it("renders the heading, Slovak fields and the submit button", async () => {
    const LoginPage = await importPage();
    render(<LoginPage />);

    expect(screen.getByText("NEX Studio")).toBeInTheDocument();
    expect(screen.getByText("Prihláste sa na svoj účet")).toBeInTheDocument();
    expect(screen.getByLabelText(/používateľské meno/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/heslo/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /prihlásiť sa/i })).toBeInTheDocument();
  });

  it("disables the submit button until both fields are filled", async () => {
    const LoginPage = await importPage();
    render(<LoginPage />);
    const submit = screen.getByRole("button", { name: /prihlásiť sa/i });
    expect(submit).toBeDisabled();

    await userEvent.type(screen.getByLabelText(/používateľské meno/i), "zoltan");
    await userEvent.type(screen.getByLabelText(/heslo/i), "secret");
    expect(submit).toBeEnabled();
  });

  it("redirects to / on successful login", async () => {
    loginMock.mockResolvedValue(undefined);
    const LoginPage = await importPage();
    render(<LoginPage />);

    await userEvent.type(screen.getByLabelText(/používateľské meno/i), "zoltan");
    await userEvent.type(screen.getByLabelText(/heslo/i), "secret");
    await userEvent.click(screen.getByRole("button", { name: /prihlásiť sa/i }));

    await waitFor(() => expect(loginMock).toHaveBeenCalledWith("zoltan", "secret"));
    expect(navigateMock).toHaveBeenCalledWith("/", { replace: true });
  });

  it("redirects to location.state.from on successful login", async () => {
    locationMock = { state: { from: { pathname: "/cockpit" } } };
    loginMock.mockResolvedValue(undefined);
    const LoginPage = await importPage();
    render(<LoginPage />);

    await userEvent.type(screen.getByLabelText(/používateľské meno/i), "zoltan");
    await userEvent.type(screen.getByLabelText(/heslo/i), "secret");
    await userEvent.click(screen.getByRole("button", { name: /prihlásiť sa/i }));

    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith("/cockpit", { replace: true }));
  });

  it("shows the error message when login fails", async () => {
    loginMock.mockRejectedValue(new Error("401"));
    const LoginPage = await importPage();
    render(<LoginPage />);

    await userEvent.type(screen.getByLabelText(/používateľské meno/i), "zoltan");
    await userEvent.type(screen.getByLabelText(/heslo/i), "bad");
    await userEvent.click(screen.getByRole("button", { name: /prihlásiť sa/i }));

    expect(await screen.findByText(/nesprávne prihlasovacie údaje/i)).toBeInTheDocument();
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it("redirects away if already authenticated (token present)", async () => {
    tokenValue = "existing-token";
    const LoginPage = await importPage();
    render(<LoginPage />);
    await waitFor(() => expect(navigateMock).toHaveBeenCalledWith("/", { replace: true }));
  });
});
