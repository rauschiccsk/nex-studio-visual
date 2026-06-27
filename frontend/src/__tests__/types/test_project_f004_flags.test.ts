/**
 * F-004 flags compile-time tests pre ProjectCreate type.
 *
 * Verifies že the surviving F-004 boolean flags sú accepted by TypeScript v
 * ProjectCreate payload type (used by createProjectApi). The ``enable_coordinator``
 * flag was retired with the v2 two-agent model (CR-V2-024); ``type`` (archetype)
 * and ``auth_mode`` are now MANDATORY (CR-V2-005).
 */

import { describe, it, expect } from "vitest";
import type { ProjectCreate } from "@/types/project";

describe("ProjectCreate F-004 setup flags", () => {
  it("accepts the surviving setup flags with explicit values", () => {
    const payload: ProjectCreate = {
      name: "Test",
      slug: "test",
      type: "standard",
      auth_mode: "password",
      description: "F-004 test",
      created_by: "user-uuid",
      enable_cicd: true,
      full_smoke: true,
      enable_branch_protection: true,
    };

    expect(payload.enable_cicd).toBe(true);
    expect(payload.full_smoke).toBe(true);
    expect(payload.enable_branch_protection).toBe(true);
  });

  it("flags are optional — payload bez nich je platný", () => {
    const payload: ProjectCreate = {
      name: "Minimal",
      slug: "minimal",
      type: "standard",
      auth_mode: "password",
      description: "",
      created_by: "user-uuid",
    };

    expect(payload.enable_cicd).toBeUndefined();
    expect(payload.full_smoke).toBeUndefined();
    expect(payload.enable_branch_protection).toBeUndefined();
  });

  it("F-004 spec defaults: all flags OFF", () => {
    // Default policy podľa spec §4 form: all setup flags OFF (opt-in).
    const payload: ProjectCreate = {
      name: "Defaults",
      slug: "defaults",
      type: "web",
      auth_mode: "token",
      description: "",
      created_by: "user-uuid",
      enable_cicd: false, // default OFF
      full_smoke: false, // default OFF
      enable_branch_protection: false, // default OFF (per Q-7 Dedo approval)
    };

    expect(payload.enable_cicd).toBe(false);
    expect(payload.full_smoke).toBe(false);
    expect(payload.enable_branch_protection).toBe(false);
  });
});
