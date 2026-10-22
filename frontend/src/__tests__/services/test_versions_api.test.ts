/**
 * Unit tests for the Version API client.
 *
 * These tests mock the global ``fetch`` function to verify that each
 * API helper issues the correct HTTP method, URL and body without
 * hitting a real backend.
 */

import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";
import {
  listVersions,
  getVersion,
  createVersion,
  updateVersion,
  readZadanie,
} from "@/services/api/versions";
import type {
  Version,
  VersionCreate,
  VersionUpdate,
} from "@/types/version";

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

/** Build a minimal ``Version`` fixture for mock responses. */
function makeVersion(overrides: Partial<Version> = {}): Version {
  return {
    id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    project_id: "11111111-2222-3333-4444-555555555555",
    version_number: "1.0.0",
    name: null,
    status: "planned",
    description: null,
    target_date: null,
    release_date: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    epic_count: 0,
    epics_done: 0,
    bug_count: 0,
    ...overrides,
  };
}

/** Create a ``Response``-like object that ``fetch`` would return. */
function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: "OK",
    headers: new Headers({ "content-type": "application/json" }),
    text: () => Promise.resolve(JSON.stringify(body)),
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

/* ------------------------------------------------------------------ */
/*  Setup                                                              */
/* ------------------------------------------------------------------ */

let fetchMock: Mock;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  // Provide a stub localStorage so the api client doesn't throw.
  vi.stubGlobal("localStorage", {
    getItem: vi.fn(() => "test-jwt-token"),
    setItem: vi.fn(),
    removeItem: vi.fn(),
  });
});

/* ------------------------------------------------------------------ */
/*  Tests                                                              */
/* ------------------------------------------------------------------ */

describe("listVersions", () => {
  it("sends GET /projects/{projectId}/versions", async () => {
    const versions = [makeVersion()];
    fetchMock.mockResolvedValueOnce(jsonResponse(versions));

    const result = await listVersions("proj-1");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/projects/proj-1/versions");
    expect(init.method).toBe("GET");
    expect(result).toEqual(versions);
  });
});

describe("getVersion", () => {
  it("sends GET /versions/{id}", async () => {
    const version = makeVersion({ version_number: "2.0.0" });
    fetchMock.mockResolvedValueOnce(jsonResponse(version));

    const result = await getVersion("ver-1");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/versions/ver-1");
    expect(init.method).toBe("GET");
    expect(result).toEqual(version);
  });
});

describe("readZadanie", () => {
  it("sends GET /versions/{id}/zadanie and returns the saved file content", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ content: "Zadanie: postav X." }));

    const result = await readZadanie("ver-1");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/versions/ver-1/zadanie");
    expect(init.method).toBe("GET");
    expect(result).toEqual({ content: "Zadanie: postav X." });
  });
});

describe("createVersion", () => {
  it("sends POST /projects/{projectId}/versions with body", async () => {
    const payload: VersionCreate = {
      version_number: "1.0.0",
      name: "Initial release",
    };
    const created = makeVersion({ version_number: "1.0.0", name: "Initial release" });
    fetchMock.mockResolvedValueOnce(jsonResponse(created));

    const result = await createVersion("proj-1", payload);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/projects/proj-1/versions");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(payload);
    expect(result).toEqual(created);
  });
});

describe("updateVersion", () => {
  it("sends PATCH /versions/{id} with body", async () => {
    const payload: VersionUpdate = { name: "Renamed", status: "active" };
    const updated = makeVersion({ name: "Renamed", status: "active" });
    fetchMock.mockResolvedValueOnce(jsonResponse(updated));

    const result = await updateVersion("ver-1", payload);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/versions/ver-1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual(payload);
    expect(result).toEqual(updated);
  });
});

