/**
 * "The server is back" — a connection to the server dropped and has come back (DEV-63).
 *
 * Every deploy restarts the backend, and every open window's live connection drops with it. The moment it comes back
 * is the moment a new version can exist, so whoever holds such a connection announces it and the version watch asks
 * at once. A window event keeps this module free of the app: the connection that knows and the watch that asks do
 * not import each other.
 */
export const SERVER_BACK_EVENT = "nex:server-back";

export function announceServerBack(): void {
  window.dispatchEvent(new Event(SERVER_BACK_EVENT));
}
