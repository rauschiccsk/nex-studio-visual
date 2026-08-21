/**
 * The pinned project is stored PER USER (v4.0.90).
 *
 * It used to live under one shared key, which leaked: Nazar opened the cockpit and saw
 * the Director's pinned project in the sidebar. v4.0.33 fixed the leak by clearing the
 * pin on EVERY login — safe, but it punished the far commoner case of the same person
 * coming back after a session expired, who then had to hunt for their project again.
 *
 * Scoping the key fixes both at once. These tests hold both halves: another user's pin
 * must be unreachable, and your own must survive.
 */
import { beforeEach, describe, expect, it } from "vitest";

import {
  activeContextKey,
  scopeActiveContextTo,
  useActiveContextStore,
} from "@/store/activeContextStore";

const PIN = { slug: "nex-productcatalogs", name: "NEX ProductCatalogs" };
const OTHER = { slug: "nex-shopify", name: "NEX Shopify" };

beforeEach(async () => {
  window.localStorage.clear();
  await scopeActiveContextTo(undefined);
  useActiveContextStore.getState().setSelectedProject(null);
});

describe("per-user pin", () => {
  it("writes under a key that carries the username", async () => {
    await scopeActiveContextTo("rausch");
    useActiveContextStore.getState().setSelectedProject(PIN);
    expect(window.localStorage.getItem(activeContextKey("rausch"))).toContain(
      "nex-productcatalogs",
    );
  });

  it("gives a returning user their own pin back", async () => {
    await scopeActiveContextTo("rausch");
    useActiveContextStore.getState().setSelectedProject(PIN);

    await scopeActiveContextTo(undefined); // logout
    await scopeActiveContextTo("rausch"); // ...and back

    expect(useActiveContextStore.getState().selectedProject?.slug).toBe(PIN.slug);
  });

  it("never hands one user another user's pin", async () => {
    await scopeActiveContextTo("rausch");
    useActiveContextStore.getState().setSelectedProject(PIN);

    await scopeActiveContextTo("teliuk");

    expect(useActiveContextStore.getState().selectedProject).toBeNull();
  });

  it("keeps two users' pins apart", async () => {
    await scopeActiveContextTo("rausch");
    useActiveContextStore.getState().setSelectedProject(PIN);
    await scopeActiveContextTo("teliuk");
    useActiveContextStore.getState().setSelectedProject(OTHER);

    await scopeActiveContextTo("rausch");
    expect(useActiveContextStore.getState().selectedProject?.slug).toBe(PIN.slug);
    await scopeActiveContextTo("teliuk");
    expect(useActiveContextStore.getState().selectedProject?.slug).toBe(OTHER.slug);
  });

  it("no pin ever lands in the anonymous slot", async () => {
    // The anonymous slot is what gets read BEFORE the username is known, so a real
    // selection must never reach it — otherwise the next person to open the browser
    // sees it before signing in.
    await scopeActiveContextTo("rausch");
    useActiveContextStore.getState().setSelectedProject(PIN);
    expect(window.localStorage.getItem(activeContextKey(undefined)) ?? "").not.toContain(
      PIN.slug,
    );
  });
});
