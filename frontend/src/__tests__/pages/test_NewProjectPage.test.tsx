/**
 * Unit tests for {@link NewProjectPage} and {@link NewProjectForm}.
 *
 * Tests cover:
 *   1. Form renders all fields
 *   2. Slug auto-generation from name on blur
 *   3. GitHub repo auto-derive from slug (short format rauschiccsk/slug)
 *   4. repoTouchedByUser flag — stops auto-derive after manual edit
 *   5. slugTouchedByUser flag — stops auto-generation after manual edit
 *   6. GitHub repo format validation (org/repo short format)
 *   7. Required field validation (name, slug)
 *   8. Category icon-button grid works (singlemodule / multimodule)
 *   9. Port auto-suggest fires on slug change
 *  10. Submit calls onSubmit with correct data
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

/* ------------------------------------------------------------------ */
/*  Mocks                                                              */
/* ------------------------------------------------------------------ */

const navigateMock = vi.fn();

vi.mock("react-router-dom", () => ({
  useNavigate: () => navigateMock,
}));

vi.mock("@/services/api", () => ({
  api: {
    post: vi.fn(),
    get: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
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

// Port registry mock — default: suggest returns 9100, check returns available
const suggestNextAvailablePortMock = vi.fn();
const checkPortAvailabilityMock = vi.fn();

vi.mock("@/services/api/port-registry", () => ({
  suggestNextAvailablePort: suggestNextAvailablePortMock,
  checkPortAvailability: checkPortAvailabilityMock,
}));

/* ------------------------------------------------------------------ */
/*  Setup                                                              */
/* ------------------------------------------------------------------ */

beforeEach(() => {
  vi.resetAllMocks();
  suggestNextAvailablePortMock.mockResolvedValue(9100);
  checkPortAvailabilityMock.mockResolvedValue(true);
});

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

async function importForm() {
  const mod = await import("@/components/projects/NewProjectForm");
  return mod.default;
}

async function importPage() {
  const mod = await import("@/pages/NewProjectPage");
  return mod.default;
}

/* ------------------------------------------------------------------ */
/*  NewProjectForm — render                                            */
/* ------------------------------------------------------------------ */

describe("NewProjectForm", () => {
  it("renders all form fields", async () => {
    const NewProjectForm = await importForm();
    render(<NewProjectForm onSubmit={vi.fn()} />);

    expect(screen.getByTestId("project-name")).toBeInTheDocument();
    expect(screen.getByTestId("project-slug")).toBeInTheDocument();
    expect(screen.getByTestId("project-repo")).toBeInTheDocument();
    expect(screen.getByTestId("project-category")).toBeInTheDocument();
    expect(screen.getByTestId("category-singlemodule")).toBeInTheDocument();
    expect(screen.getByTestId("category-multimodule")).toBeInTheDocument();
    expect(screen.getByTestId("project-description")).toBeInTheDocument();
    expect(screen.getByTestId("backend-port")).toBeInTheDocument();
    expect(screen.getByTestId("frontend-port")).toBeInTheDocument();
    expect(screen.getByTestId("db-port")).toBeInTheDocument();
    expect(screen.getByTestId("submit-button")).toBeInTheDocument();
  });

  it("auto-generates slug from name on blur", async () => {
    const user = userEvent.setup();
    const NewProjectForm = await importForm();
    render(<NewProjectForm onSubmit={vi.fn()} />);

    const nameInput = screen.getByTestId("project-name");
    const slugInput = screen.getByTestId("project-slug") as HTMLInputElement;

    await user.type(nameInput, "My Cool Project");
    fireEvent.blur(nameInput);

    expect(slugInput.value).toBe("my-cool-project");
  });

  it("auto-derives github_repo from slug on name blur (short format)", async () => {
    const user = userEvent.setup();
    const NewProjectForm = await importForm();
    render(<NewProjectForm onSubmit={vi.fn()} />);

    const nameInput = screen.getByTestId("project-name");
    const repoInput = screen.getByTestId("project-repo") as HTMLInputElement;

    await user.type(nameInput, "NEX Horizont");
    fireEvent.blur(nameInput);

    expect(repoInput.value).toBe("rauschiccsk/nex-horizont");
  });

  it("auto-derives github_repo when slug is manually changed", async () => {
    const user = userEvent.setup();
    const NewProjectForm = await importForm();
    render(<NewProjectForm onSubmit={vi.fn()} />);

    const slugInput = screen.getByTestId("project-slug");
    const repoInput = screen.getByTestId("project-repo") as HTMLInputElement;

    await user.type(slugInput, "custom-slug");

    expect(repoInput.value).toBe("rauschiccsk/custom-slug");
  });

  it("stops auto-deriving repo after user manually edits repo (repoTouchedByUser)", async () => {
    const user = userEvent.setup();
    const NewProjectForm = await importForm();
    render(<NewProjectForm onSubmit={vi.fn()} />);

    const nameInput = screen.getByTestId("project-name");
    const repoInput = screen.getByTestId("project-repo");

    // User manually sets a custom repo (short format)
    await user.type(repoInput, "myorg/custom-repo");

    // Now typing name and blurring should NOT overwrite repo
    await user.type(nameInput, "Some Project");
    fireEvent.blur(nameInput);

    expect((repoInput as HTMLInputElement).value).toBe("myorg/custom-repo");
  });

  it("stops auto-generating slug after user manually edits slug (slugTouchedByUser)", async () => {
    const user = userEvent.setup();
    const NewProjectForm = await importForm();
    render(<NewProjectForm onSubmit={vi.fn()} />);

    const nameInput = screen.getByTestId("project-name");
    const slugInput = screen.getByTestId("project-slug");

    // User manually types a slug
    await user.type(slugInput, "my-manual-slug");

    // Now typing name and blurring should NOT overwrite slug
    await user.type(nameInput, "Different Name");
    fireEvent.blur(nameInput);

    expect((slugInput as HTMLInputElement).value).toBe("my-manual-slug");
  });

  it("shows validation error for invalid github_repo format", async () => {
    const user = userEvent.setup();
    const NewProjectForm = await importForm();
    render(<NewProjectForm onSubmit={vi.fn()} />);

    const repoInput = screen.getByTestId("project-repo");
    await user.type(repoInput, "not-a-valid-repo");

    await waitFor(() => {
      expect(screen.getByTestId("repo-error")).toBeInTheDocument();
    });

    expect(screen.getByTestId("repo-error")).toHaveTextContent("Formát: org/repo");
  });

  it("accepts valid github_repo format without error", async () => {
    const user = userEvent.setup();
    const NewProjectForm = await importForm();
    render(<NewProjectForm onSubmit={vi.fn()} />);

    const repoInput = screen.getByTestId("project-repo");
    await user.type(repoInput, "rauschiccsk/my-repo");

    await waitFor(() => {
      expect(screen.queryByTestId("repo-error")).not.toBeInTheDocument();
    });
  });

  it("shows required validation when submitting with empty name", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    const NewProjectForm = await importForm();
    render(<NewProjectForm onSubmit={onSubmit} />);

    await user.click(screen.getByTestId("submit-button"));

    await waitFor(() => {
      expect(screen.getByText("Názov je povinný.")).toBeInTheDocument();
    });

    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("category defaults to singlemodule and switches via icon buttons", async () => {
    const user = userEvent.setup();
    const NewProjectForm = await importForm();
    render(<NewProjectForm onSubmit={vi.fn()} />);

    // singlemodule selected by default
    expect(screen.getByTestId("category-singlemodule")).toHaveClass("border-primary");
    expect(screen.getByTestId("category-multimodule")).not.toHaveClass("border-primary");

    await user.click(screen.getByTestId("category-multimodule"));

    expect(screen.getByTestId("category-multimodule")).toHaveClass("border-primary");
    expect(screen.getByTestId("category-singlemodule")).not.toHaveClass("border-primary");
  });

  it("auto-suggests 3 consecutive ports when slug is generated", async () => {
    const user = userEvent.setup();
    const NewProjectForm = await importForm();
    render(<NewProjectForm onSubmit={vi.fn()} />);

    await user.type(screen.getByTestId("project-name"), "NEX Ledger");
    fireEvent.blur(screen.getByTestId("project-name"));

    await waitFor(() => {
      expect(
        (screen.getByTestId("backend-port-input") as HTMLInputElement).value,
      ).toBe("9100");
    });

    expect(
      (screen.getByTestId("frontend-port-input") as HTMLInputElement).value,
    ).toBe("9101");
    expect(
      (screen.getByTestId("db-port-input") as HTMLInputElement).value,
    ).toBe("9102");

    expect(screen.getByText("Automaticky navrhnuté porty")).toBeInTheDocument();
  });

  it("calls onSubmit with correct ProjectCreationFormData", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    const NewProjectForm = await importForm();
    render(<NewProjectForm onSubmit={onSubmit} />);

    // Fill name → triggers slug auto-gen on blur + port auto-suggest
    await user.type(screen.getByTestId("project-name"), "Test Project");
    fireEvent.blur(screen.getByTestId("project-name"));

    // Wait for port auto-suggest to resolve
    await waitFor(() => {
      expect(
        (screen.getByTestId("backend-port-input") as HTMLInputElement).value,
      ).toBe("9100");
    });

    await user.type(screen.getByTestId("project-description"), "A test project");

    // Switch to multimodule
    await user.click(screen.getByTestId("category-multimodule"));

    await user.click(screen.getByTestId("submit-button"));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });

    expect(onSubmit).toHaveBeenCalledWith({
      name: "Test Project",
      slug: "test-project",
      category: "multimodule",
      description: "A test project",
      github_repo: "rauschiccsk/test-project",
      backend_port: 9100,
      frontend_port: 9101,
      db_port: 9102,
    });
  });

  it("shows server error when error prop is set", async () => {
    const NewProjectForm = await importForm();
    render(<NewProjectForm onSubmit={vi.fn()} error="Slug already taken" />);

    expect(screen.getByTestId("form-error")).toHaveTextContent("Slug already taken");
  });

  it("disables submit button when loading", async () => {
    const NewProjectForm = await importForm();
    render(<NewProjectForm onSubmit={vi.fn()} loading={true} />);

    expect(screen.getByTestId("submit-button")).toBeDisabled();
    expect(screen.getByTestId("submit-button")).toHaveTextContent("Vytváram projekt");
  });
});

/* ------------------------------------------------------------------ */
/*  NewProjectPage — integration                                       */
/* ------------------------------------------------------------------ */

describe("NewProjectPage", () => {
  it("renders page with heading and form", async () => {
    const NewProjectPage = await importPage();
    render(<NewProjectPage />);

    expect(screen.getByTestId("new-project-page")).toBeInTheDocument();
    expect(screen.getByText("Novy projekt")).toBeInTheDocument();
    expect(screen.getByTestId("new-project-form")).toBeInTheDocument();
  });

  it("back button navigates to /projects", async () => {
    const user = userEvent.setup();
    const NewProjectPage = await importPage();
    render(<NewProjectPage />);

    await user.click(screen.getByTestId("back-button"));

    expect(navigateMock).toHaveBeenCalledWith("/projects");
  });
});
