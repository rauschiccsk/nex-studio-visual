/**
 * Tests for project creation TypeScript type definitions.
 *
 * These are compile-time structural tests — they verify that the exported
 * interfaces have the expected shape and that values conforming to the
 * interfaces are accepted by TypeScript.
 */

import { describe, it, expect } from "vitest";
import type {
  GitHubRepoValidationError,
  PortValidationError,
  ProjectCreationFormData,
  SlugValidationError,
} from "@/types/project-creation";

describe("ProjectCreationFormData", () => {
  it("accepts a fully populated form object", () => {
    const form: ProjectCreationFormData = {
      name: "My Project",
      slug: "my-project",
      category: "singlemodule",
      description: "A test project",
      github_repo: "https://github.com/org/repo",
      backend_port: 10100,
      frontend_port: 10101,
      db_port: 10102,
      ui_design_port: 10103,
    };

    expect(form.name).toBe("My Project");
    expect(form.slug).toBe("my-project");
    expect(form.category).toBe("singlemodule");
    expect(form.description).toBe("A test project");
    expect(form.github_repo).toBe("https://github.com/org/repo");
    expect(form.backend_port).toBe(10100);
    expect(form.frontend_port).toBe(10101);
    expect(form.db_port).toBe(10102);
  });

  it("accepts null port values", () => {
    const form: ProjectCreationFormData = {
      name: "No Ports",
      slug: "no-ports",
      category: "multimodule",
      description: "",
      github_repo: "",
      backend_port: null,
      frontend_port: null,
      db_port: null,
      ui_design_port: null,
    };

    expect(form.backend_port).toBeNull();
    expect(form.frontend_port).toBeNull();
    expect(form.db_port).toBeNull();
  });

  it("accepts multimodule category", () => {
    const form: ProjectCreationFormData = {
      name: "Multi",
      slug: "multi",
      category: "multimodule",
      description: "",
      github_repo: "",
      backend_port: null,
      frontend_port: null,
      db_port: null,
      ui_design_port: null,
    };

    expect(form.category).toBe("multimodule");
  });

  it("has all expected keys", () => {
    const form: ProjectCreationFormData = {
      name: "",
      slug: "",
      category: "singlemodule",
      description: "",
      github_repo: "",
      backend_port: null,
      frontend_port: null,
      db_port: null,
      ui_design_port: null,
    };

    const keys = Object.keys(form).sort();
    expect(keys).toEqual([
      "backend_port",
      "category",
      "db_port",
      "description",
      "frontend_port",
      "github_repo",
      "name",
      "slug",
      "ui_design_port",
    ]);
  });
});

describe("PortValidationError", () => {
  it("accepts a complete error object", () => {
    const error: PortValidationError = {
      port: 10100,
      field: "backend_port",
      message: "Port 10100 is already in use",
      conflicting_project: "other-project",
    };

    expect(error.port).toBe(10100);
    expect(error.field).toBe("backend_port");
    expect(error.message).toBe("Port 10100 is already in use");
    expect(error.conflicting_project).toBe("other-project");
  });

  it("accepts error without conflicting_project (optional)", () => {
    const error: PortValidationError = {
      port: 5432,
      field: "db_port",
      message: "Port is reserved",
    };

    expect(error.conflicting_project).toBeUndefined();
  });

  it("accepts all valid field values", () => {
    const fields: PortValidationError["field"][] = [
      "backend_port",
      "frontend_port",
      "db_port",
    ];

    fields.forEach((field) => {
      const error: PortValidationError = {
        port: 8080,
        field,
        message: "test",
      };
      expect(error.field).toBe(field);
    });
  });
});

describe("SlugValidationError", () => {
  it("accepts a complete error object", () => {
    const error: SlugValidationError = {
      slug: "existing-project",
      message: "Slug is already taken",
    };

    expect(error.slug).toBe("existing-project");
    expect(error.message).toBe("Slug is already taken");
  });
});

describe("GitHubRepoValidationError", () => {
  it("accepts a complete error object", () => {
    const error: GitHubRepoValidationError = {
      repo_url: "https://github.com/org/nonexistent",
      message: "Repository not found",
    };

    expect(error.repo_url).toBe("https://github.com/org/nonexistent");
    expect(error.message).toBe("Repository not found");
  });
});

describe("re-export from barrel", () => {
  it("types are accessible via @/types barrel", async () => {
    // Dynamic import to verify barrel re-exports work
    const types = await import("@/types");
    // The barrel file re-exports everything — we just verify
    // the module resolves without error
    expect(types).toBeDefined();
  });
});
