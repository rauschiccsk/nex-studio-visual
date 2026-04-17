/**
 * Unit tests for {@link LoginPage}.
 *
 * Tests cover:
 *   1. Successful form submission → redirect to /
 *   2. Error display on 401 (invalid credentials)
 *   3. Error display on network failure
 *   4. Required-field validation (empty submit)
 *   5. Redirect to ?next= path on success
 */

import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/* ------------------------------------------------------------------ */
/*  Mocks                                                              */
/* ------------------------------------------------------------------ */

// Mock react-router-dom — provide navigate + searchParams stubs
const navigateMock = vi.fn();
let searchParamsMap = new URLSearchParams();

vi.mock("react-router-dom", () => ({
  useNavigate: () => navigateMock,
  useSearchParams: () => [searchParamsMap],
}));

// Mock authStore — expose login as a controllable mock
const loginMock: Mock = vi.fn();
vi.mock("@/store/authStore", () => ({
  useAuthStore: (selector: (s: Record<string, unknown>) => unknown) =>
    selector({ login: loginMock }),
}));

// Mock api module — provide real ApiError class
vi.mock("@/services/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    data: unknown;
    constructor(status: number, message: string, data: unknown = null) {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.data = data;
    }
  },
  TOKEN_STORAGE_KEY: "nex_studio_token",
}));

/* ------------------------------------------------------------------ */
/*  Setup                                                              */
/* ------------------------------------------------------------------ */

beforeEach(() => {
  vi.resetAllMocks();
  searchParamsMap = new URLSearchParams();
});

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

async function importPage() {
  const mod = await import("@/pages/LoginPage");
  return mod.default;
}

/** Create a real ApiError instance from the mocked module. */
async function makeApiError(status: number, message: string) {
  const mod = await import("@/services/api");
  return new (mod.ApiError as new (s: number, m: string) => Error)(
    status,
    message,
  );
}

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe("LoginPage", () => {
  it("renders the login form with heading", async () => {
    const LoginPage = await importPage();
    render(<LoginPage />);

    expect(screen.getByText("NEX Studio")).toBeInTheDocument();
    expect(screen.getByText("Sign in to your account")).toBeInTheDocument();
    expect(screen.getByTestId("login-username")).toBeInTheDocument();
    expect(screen.getByTestId("login-password")).toBeInTheDocument();
    expect(screen.getByTestId("login-submit")).toBeInTheDocument();
  });

  it("redirects to / on successful login", async () => {
    loginMock.mockResolvedValueOnce(undefined);
    const user = userEvent.setup();

    const LoginPage = await importPage();
    render(<LoginPage />);

    await user.type(screen.getByTestId("login-username"), "admin");
    await user.type(screen.getByTestId("login-password"), "secret123");
    await user.click(screen.getByTestId("login-submit"));

    await waitFor(() => {
      expect(loginMock).toHaveBeenCalledWith("admin", "secret123");
    });

    expect(navigateMock).toHaveBeenCalledWith("/", { replace: true });
  });

  it("redirects to ?next= path on successful login", async () => {
    loginMock.mockResolvedValueOnce(undefined);
    searchParamsMap = new URLSearchParams("next=/projects/my-project");
    const user = userEvent.setup();

    const LoginPage = await importPage();
    render(<LoginPage />);

    await user.type(screen.getByTestId("login-username"), "admin");
    await user.type(screen.getByTestId("login-password"), "pass");
    await user.click(screen.getByTestId("login-submit"));

    await waitFor(() => {
      expect(navigateMock).toHaveBeenCalledWith("/projects/my-project", {
        replace: true,
      });
    });
  });

  it("shows error message on 401 (invalid credentials)", async () => {
    const apiError = await makeApiError(401, "Unauthorized");
    loginMock.mockRejectedValueOnce(apiError);
    const user = userEvent.setup();

    const LoginPage = await importPage();
    render(<LoginPage />);

    await user.type(screen.getByTestId("login-username"), "admin");
    await user.type(screen.getByTestId("login-password"), "wrong");
    await user.click(screen.getByTestId("login-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("login-error")).toBeInTheDocument();
    });

    expect(screen.getByTestId("login-error")).toHaveTextContent(
      "Invalid username or password.",
    );
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it("shows error message on non-401 API error", async () => {
    const apiError = await makeApiError(500, "Internal Server Error");
    loginMock.mockRejectedValueOnce(apiError);
    const user = userEvent.setup();

    const LoginPage = await importPage();
    render(<LoginPage />);

    await user.type(screen.getByTestId("login-username"), "admin");
    await user.type(screen.getByTestId("login-password"), "pass");
    await user.click(screen.getByTestId("login-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("login-error")).toBeInTheDocument();
    });

    expect(screen.getByTestId("login-error")).toHaveTextContent(
      "Internal Server Error",
    );
  });

  it("shows network error on non-ApiError failure", async () => {
    loginMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const user = userEvent.setup();

    const LoginPage = await importPage();
    render(<LoginPage />);

    await user.type(screen.getByTestId("login-username"), "admin");
    await user.type(screen.getByTestId("login-password"), "pass");
    await user.click(screen.getByTestId("login-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("login-error")).toBeInTheDocument();
    });

    expect(screen.getByTestId("login-error")).toHaveTextContent(
      "Network error. Please check your connection.",
    );
  });

  it("shows validation errors when submitting empty fields", async () => {
    const user = userEvent.setup();

    const LoginPage = await importPage();
    render(<LoginPage />);

    await user.click(screen.getByTestId("login-submit"));

    await waitFor(() => {
      const alerts = screen.getAllByRole("alert");
      expect(alerts.length).toBeGreaterThanOrEqual(2);
    });

    expect(screen.getByText("Username is required.")).toBeInTheDocument();
    expect(screen.getByText("Password is required.")).toBeInTheDocument();
    expect(loginMock).not.toHaveBeenCalled();
  });

  it("disables submit button while loading", async () => {
    // Never-resolving promise to keep loading state
    loginMock.mockReturnValueOnce(new Promise(() => {}));
    const user = userEvent.setup();

    const LoginPage = await importPage();
    render(<LoginPage />);

    await user.type(screen.getByTestId("login-username"), "admin");
    await user.type(screen.getByTestId("login-password"), "pass");
    await user.click(screen.getByTestId("login-submit"));

    await waitFor(() => {
      expect(screen.getByTestId("login-submit")).toBeDisabled();
    });

    expect(screen.getByTestId("login-submit")).toHaveTextContent(
      "Signing in\u2026",
    );
  });
});
