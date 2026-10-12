/**
 * DEV-82 — the caller's own screen layout, kept on the server: the widths of the side panels and the collapsed
 * sidebar. Maps to ``backend.api.routes.user_layout`` (``GET`` / ``PUT /user-layout``).
 */

import api from "../api";
import type { components } from "@/services/api/pipeline.generated";

export type UserLayout = components["schemas"]["UserLayoutRead"];
/** Only the fields sent change; a width of null returns to the default. */
export type UserLayoutChanges = Partial<Pick<UserLayout, "sidebar_width" | "sidebar_collapsed" | "plan_width">>;

export function getUserLayout(): Promise<UserLayout> {
  return api.get<UserLayout>("/user-layout");
}

export function saveUserLayout(changes: UserLayoutChanges): Promise<UserLayout> {
  return api.put<UserLayout>("/user-layout", changes);
}
