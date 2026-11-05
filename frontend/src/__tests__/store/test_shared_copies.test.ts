/**
 * DEV-312 — a change made on one screen of the cockpit shows on every other one, without reloading the page.
 *
 * 05.11.2026 (measured in the code, 4.114.1): Nastavenia → Používatelia changed the own row and refreshed only its list
 * — the signed-in person in the left panel and Moje konto stayed old; project settings renamed a project and the chosen
 * project kept its old name in Fakturácia, Zákazníci, Metriky, Poznámky, Poradca and the left panel. The Director: „…
 * prosím raz a navždy zabezpečiť, aby nič také už viackrát sa nestalo ani v kokpite ani v našich aplikáciách.“ The
 * client of nex-shared tells every change (onServerChange); the shared stores listen. Walked with the real client.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { deployCustomer } from "@/services/api/deploy";
import { updateProjectApi } from "@/services/api/projects";
import { updateUserApi } from "@/services/api/users";
import { useActiveContextStore } from "@/store/activeContextStore";
import { useAuthStore, type AuthUser } from "@/store/authStore";

const ME = { id: "me", username: "zoltan", email: "rausch@isnex.eu", first_name: "Zoltán", last_name: "Rausch", role: "ri" };

/** The server: who is signed in, the project and the version as they are now; and what was asked. */
function serve(now: { me: typeof ME; project: Record<string, string>; version: Record<string, string> }) {
  const asked: string[] = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    const method = (init.method ?? "GET").toUpperCase();
    const path = url.replace(/^.*\/api\/v1/, "");
    asked.push(`${method} ${path}`);
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    if (method === "GET" && path === "/auth/me") return json(now.me);
    if (method === "GET" && path === "/projects/p1") return json(now.project);
    if (method === "GET" && path === "/versions/v1") return json(now.version);
    return json({ id: "x" });
  });
  return asked;
}

beforeEach(() => {
  useAuthStore.setState({ token: "t", user: ME as unknown as AuthUser });
  useActiveContextStore.setState({
    selectedProject: { slug: "nex-knowledge", name: "NEX Knowledge" },
    selectedVersion: { versionId: "v1", versionNumber: "0.2.1" },
  });
});
afterEach(() => vi.unstubAllGlobals());

describe("the signed-in person (DEV-312)", () => {
  it("own row changed in Používatelia refreshes the person the left panel and Moje konto show", async () => {
    serve({ me: { ...ME, email: "rausch@icc.sk" }, project: {}, version: {} });

    await updateUserApi("me", { email: "rausch@icc.sk" } as never);

    await vi.waitFor(() => expect(useAuthStore.getState().user?.email).toBe("rausch@icc.sk"));
  });

  it("another person's row changed leaves the signed-in person alone, without asking again", async () => {
    const asked = serve({ me: ME, project: {}, version: {} });

    await updateUserApi("eva", { first_name: "Evka" } as never);
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(asked).not.toContain("GET /auth/me");
  });
});

describe("the chosen project and version (DEV-312)", () => {
  it("a renamed project shows its new name everywhere, the chosen version stays", async () => {
    serve({ me: ME, project: { id: "p1", slug: "nex-knowledge", name: "NEX Knowledge ICC" }, version: {} });

    await updateProjectApi("p1", { name: "NEX Knowledge ICC" } as never);

    await vi.waitFor(() =>
      expect(useActiveContextStore.getState().selectedProject).toEqual({ slug: "nex-knowledge", name: "NEX Knowledge ICC" }),
    );
    expect(useActiveContextStore.getState().selectedVersion).toEqual({ versionId: "v1", versionNumber: "0.2.1" });
  });

  it("another project renamed leaves the chosen one as it is", async () => {
    serve({ me: ME, project: { id: "p1", slug: "nex-manager", name: "NEX Manager 2" }, version: {} });

    await updateProjectApi("p1", { name: "NEX Manager 2" } as never);
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(useActiveContextStore.getState().selectedProject).toEqual({ slug: "nex-knowledge", name: "NEX Knowledge" });
  });

  it("a deploy that made the chosen version 1.0.0 shows the new number", async () => {
    serve({ me: ME, project: {}, version: { id: "v1", version_number: "1.0.0" } });

    await deployCustomer("c1", { version_number: "0.2.1", environment: "prod" } as never);

    await vi.waitFor(() =>
      expect(useActiveContextStore.getState().selectedVersion).toEqual({ versionId: "v1", versionNumber: "1.0.0" }),
    );
  });
});
