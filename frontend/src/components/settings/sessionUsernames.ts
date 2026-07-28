/**
 * Who owns a session — the index behind the Relácie tab's "Používateľ" column.
 *
 * The kit's SessionsPanel renders that column through a `resolveUsername(userId)` callback and falls back
 * to the raw id. The page used to build the index from the user directory alone, and `GET /users` is
 * ri-only while the Relácie tab itself is ha+ — so a Medior saw a column of bare UUIDs next to a per-row
 * "Odvolať" and had to decide whether to cut somebody's session without knowing whose it was.
 *
 * The session rows now carry their owner's `username` (resolved server-side by the list endpoint), which is
 * available to every role allowed on the tab. The directory is still merged in when the caller may read it,
 * as a fallback for any row that arrived without a name (e.g. a deleted owner).
 */

/** The fields this module needs off a listed session — the owner id and the name the API resolved. */
export interface SessionOwnerRef {
  user_id: string;
  username?: string | null;
}

/** The fields this module needs off a directory user (empty list for anyone but an admin). */
export interface DirectoryUserRef {
  id: string;
  username: string;
}

/**
 * Map user id → display name, from the session rows first and the (possibly empty) user directory second.
 * Ids with no name anywhere are absent, so the caller can fall back to the id rather than print "undefined".
 */
export function buildUsernameIndex(
  sessions: readonly SessionOwnerRef[],
  users: readonly DirectoryUserRef[],
): Record<string, string> {
  const index: Record<string, string> = {};
  for (const session of sessions) {
    if (session.username) index[session.user_id] = session.username;
  }
  for (const user of users) index[user.id] = user.username;
  return index;
}
