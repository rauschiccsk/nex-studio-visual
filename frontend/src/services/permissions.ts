// Who may operate a project — the ONE place the answer is written down.
//
// Since the ownership simplification (Director, 2026-07-28) a project belongs to the single user who
// created it: he may do everything on it, nobody else sees it, and the single ACCOUNT named `admin` may
// do everything everywhere. The Shuhari roles no longer decide anything about projects — they govern the
// Knowledge Base, user administration and the credentials store, and each of those keeps its own check.
//
// This file exists because the predicate had no home: `user?.role === "ri"` was retyped inline at four
// places, and the Knowledge Base used the same literal for a DIFFERENT question. Anyone rewriting those
// by pattern would silently have widened KB access. A named function makes the two worlds impossible to
// confuse, and mirrors `backend/core/authz.py` exactly.

import type { AuthUser } from "@/services/api/auth";

/** The account that may reach every project. An ACCOUNT, not a role — mirrors `authz.ADMIN_USERNAME`. */
export const ADMIN_USERNAME = "admin";

export function isAdminAccount(user: AuthUser | null | undefined): boolean {
  return user?.username === ADMIN_USERNAME;
}

/** The whole project rule, client-side: his own project, or the admin account. */
export function mayOperateProject(
  user: AuthUser | null | undefined,
  projectCreatedBy: string | null | undefined,
): boolean {
  if (!user) return false;
  return user.id === projectCreatedBy || isAdminAccount(user);
}
