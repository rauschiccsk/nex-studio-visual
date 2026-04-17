/**
 * Unit tests for the centralized API client (``services/api.ts``).
 *
 * Covers:
 *   - JWT Bearer token injection from localStorage
 *   - 401 response interceptor (clears token + calls registered callback)
 *   - ``getCurrentUser()`` convenience helper
 *   - ``logoutUser()`` convenience helper
 *   - ``registerAuthCallback`` wiring
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  api,
  ApiError,
  TOKEN_STORAGE_KEY,
  registerAuthCallback,
  getCurrentUser,
  logoutUser,
} from "@/services/api";

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

/** Create a ``Response``-like object that ``fetch`` would return. */
function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 401 ? "Unauthorized" : "OK",
    headers: new Headers({ "content-type": "application/json" }),
    text: () => Promise.resolve(JSON.stringify(body)),
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

/** Create a 204 No Content response. */
function noContentResponse(): Response {
  return {
    ok: true,
    status: 204,
    statusText: "No Content",
    headers: new Headers(),
    text: () => Promise.resolve(""),
    json: () => Promise.reject(new Error("no body")),
  } as unknown as Response;
}

/* ------------------------------------------------------------------ */
/*  Setup                                                              */
/* ------------------------------------------------------------------ */

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);

  // Clear any stored token between tests.
  window.localStorage.clear();

  // Stub location.assign to prevent jsdom navigation errors.
  Object.defineProperty(window, "location", {
    value: {
      ...window.location,
      pathname: "/projects",
      search: "",
      assign: vi.fn(),
    },
    writable: true,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  // Clear registered callback
  registerAuthCallback(() => {});
});

/* ------------------------------------------------------------------ */
/*  JWT token injection                                                */
/* ------------------------------------------------------------------ */

describe("JWT Bearer token injection", () => {
  it("attaches Authorization header when token exists in localStorage", async () => {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, "test-jwt-token");
    fetchMock.mockResolvedValueOnce(jsonResponse({ ok: true }));

    await api.get("/test");

    const [, init] = fetchMock.mock.calls[0]!;
    expect(init.headers.Authorization).toBe("Bearer test-jwt-token");
  });

  it("does not attach Authorization header when no token exists", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ ok: true }));

    await api.get("/test");

    const [, init] = fetchMock.mock.calls[0]!;
    expect(init.headers.Authorization).toBeUndefined();
  });

  it("skips Authorization header when skipAuth is true", async () => {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, "test-jwt-token");
    fetchMock.mockResolvedValueOnce(jsonResponse({ ok: true }));

    await api.get("/test", { skipAuth: true });

    const [, init] = fetchMock.mock.calls[0]!;
    expect(init.headers.Authorization).toBeUndefined();
  });

  it("does not overwrite a caller-provided Authorization header", async () => {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, "test-jwt-token");
    fetchMock.mockResolvedValueOnce(jsonResponse({ ok: true }));

    await api.get("/test", {
      headers: { Authorization: "Bearer custom-token" },
    });

    const [, init] = fetchMock.mock.calls[0]!;
    expect(init.headers.Authorization).toBe("Bearer custom-token");
  });
});

/* ------------------------------------------------------------------ */
/*  401 interceptor                                                    */
/* ------------------------------------------------------------------ */

describe("401 response interceptor", () => {
  it("clears token from localStorage on 401", async () => {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, "expired-token");
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: "Not authenticated" }, 401),
    );

    await expect(api.get("/protected")).rejects.toThrow(ApiError);
    expect(window.localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull();
  });

  it("redirects to /login with next param on 401", async () => {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, "expired-token");
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: "Not authenticated" }, 401),
    );

    await expect(api.get("/protected")).rejects.toThrow(ApiError);
    expect(window.location.assign).toHaveBeenCalledWith(
      "/login?next=%2Fprojects",
    );
  });

  it("does not redirect when already on /login", async () => {
    Object.defineProperty(window, "location", {
      value: {
        ...window.location,
        pathname: "/login",
        search: "",
        assign: vi.fn(),
      },
      writable: true,
    });

    window.localStorage.setItem(TOKEN_STORAGE_KEY, "expired-token");
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: "Not authenticated" }, 401),
    );

    await expect(api.get("/protected")).rejects.toThrow(ApiError);
    expect(window.location.assign).not.toHaveBeenCalled();
  });

  it("does not redirect when skipAuthRedirect is true", async () => {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, "some-token");
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: "Not authenticated" }, 401),
    );

    await expect(
      api.post("/auth/logout", undefined, { skipAuthRedirect: true }),
    ).rejects.toThrow(ApiError);

    // Token should still be there — skipAuthRedirect skips the whole handler.
    expect(window.location.assign).not.toHaveBeenCalled();
  });

  it("invokes registered auth callback on 401", async () => {
    const callback = vi.fn();
    registerAuthCallback(callback);

    window.localStorage.setItem(TOKEN_STORAGE_KEY, "expired-token");
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: "Not authenticated" }, 401),
    );

    await expect(api.get("/protected")).rejects.toThrow(ApiError);
    expect(callback).toHaveBeenCalledOnce();
  });

  it("still cleans up even if auth callback throws", async () => {
    registerAuthCallback(() => {
      throw new Error("store error");
    });

    window.localStorage.setItem(TOKEN_STORAGE_KEY, "expired-token");
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: "Not authenticated" }, 401),
    );

    await expect(api.get("/protected")).rejects.toThrow(ApiError);
    // localStorage should still be cleared despite callback error.
    expect(window.localStorage.getItem(TOKEN_STORAGE_KEY)).toBeNull();
    expect(window.location.assign).toHaveBeenCalled();
  });
});

/* ------------------------------------------------------------------ */
/*  getCurrentUser helper                                              */
/* ------------------------------------------------------------------ */

describe("getCurrentUser", () => {
  it("sends GET /api/v1/auth/me with token", async () => {
    const user = {
      id: "aaa",
      username: "admin",
      email: "admin@test.com",
      role: "ri",
      is_active: true,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    };

    window.localStorage.setItem(TOKEN_STORAGE_KEY, "valid-token");
    fetchMock.mockResolvedValueOnce(jsonResponse(user));

    const result = await getCurrentUser();

    expect(result).toEqual(user);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toContain("/api/v1/auth/me");
    expect(init.method).toBe("GET");
    expect(init.headers.Authorization).toBe("Bearer valid-token");
  });
});

/* ------------------------------------------------------------------ */
/*  logoutUser helper                                                  */
/* ------------------------------------------------------------------ */

describe("logoutUser", () => {
  it("sends POST /api/v1/auth/logout with skipAuthRedirect", async () => {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, "valid-token");
    fetchMock.mockResolvedValueOnce(noContentResponse());

    await logoutUser();

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toContain("/api/v1/auth/logout");
    expect(init.method).toBe("POST");
  });

  it("does not trigger 401 redirect on expired token during logout", async () => {
    window.localStorage.setItem(TOKEN_STORAGE_KEY, "expired-token");
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: "Not authenticated" }, 401),
    );

    // Should throw ApiError but NOT redirect (skipAuthRedirect).
    await expect(logoutUser()).rejects.toThrow(ApiError);
    expect(window.location.assign).not.toHaveBeenCalled();
  });
});

/* ------------------------------------------------------------------ */
/*  URL construction                                                   */
/* ------------------------------------------------------------------ */

describe("URL construction", () => {
  it("prefixes path with /api/v1", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ ok: true }));
    await api.get("/test/endpoint");
    const [url] = fetchMock.mock.calls[0]!;
    expect(url).toContain("/api/v1/test/endpoint");
  });

  it("does not double-prefix paths already starting with /api/v1", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ ok: true }));
    await api.get("/api/v1/test/endpoint");
    const [url] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/v1/test/endpoint");
  });

  it("appends query parameters", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ ok: true }));
    await api.get("/test", { params: { page: 1, active: true } });
    const [url] = fetchMock.mock.calls[0]!;
    expect(url).toContain("?page=1&active=true");
  });
});

/* ------------------------------------------------------------------ */
/*  Error handling                                                     */
/* ------------------------------------------------------------------ */

describe("error handling", () => {
  it("throws ApiError with status and parsed body on non-2xx", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ detail: "Not found" }, 404),
    );

    try {
      await api.get("/missing");
      expect.fail("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      const apiErr = err as ApiError;
      expect(apiErr.status).toBe(404);
      expect(apiErr.message).toBe("Not found");
      expect(apiErr.data).toEqual({ detail: "Not found" });
    }
  });

  it("extracts first validation error message from array detail", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse(
        { detail: [{ loc: ["body", "name"], msg: "field required" }] },
        422,
      ),
    );

    try {
      await api.post("/create", {});
      expect.fail("should have thrown");
    } catch (err) {
      const apiErr = err as ApiError;
      expect(apiErr.status).toBe(422);
      expect(apiErr.message).toBe("field required");
    }
  });
});
