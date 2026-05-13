/**
 * TypeScript type definitions for the ``User`` domain object.
 *
 * Mirrors ``backend.schemas.user`` — field names, nullability and
 * default semantics match the Pydantic schemas exactly.  Dates are
 * ISO-8601 strings (not ``Date`` instances) because that is the wire
 * format FastAPI emits and we never want a silent implicit conversion
 * in the UI layer.  UUIDs are strings for the same reason.
 */

/**
 * Mirrors the CHECK constraint ``role IN ('ri', 'ha', 'shu')`` on the
 * ``users`` table.
 *
 *   - ``ri``  — Director / Senior (full access)
 *   - ``ha``  — Medior (project member)
 *   - ``shu`` — Junior (restricted)
 */
export type UserRole = "ri" | "ha" | "shu";

/** Payload for creating a new user (``POST /api/v1/users``). */
export interface UserCreate {
  /** Login name — unique across the system. Max 50 chars. */
  username: string;
  /** Contact email — unique across the system. Max 255 chars. */
  email: string;
  /** Plaintext password (min 5, max 128 chars). Hashed server-side.
   *  Min 5 — Director directive 2026-05-13, NEX Studio is internal. */
  password: string;
  /** Access level: ``ri`` | ``ha`` | ``shu``. */
  role: UserRole;
  /** Soft-disable flag; defaults to ``true`` on the server. */
  is_active?: boolean;
  /** Given name. Optional — legacy users may not have it. Max 100 chars. */
  first_name?: string | null;
  /** Family name. Optional — legacy users may not have it. Max 100 chars. */
  last_name?: string | null;
}

/**
 * Partial update for an existing user (``PATCH /api/v1/users/{id}``).
 *
 * ``id`` and ``created_at`` are immutable; ``updated_at`` is managed by
 * the ORM.  All remaining fields are optional to support PATCH-style
 * semantics.  Password changes use a separate endpoint
 * (``POST /users/{id}/change-password``).
 */
export interface UserUpdate {
  username?: string;
  email?: string;
  role?: UserRole;
  is_active?: boolean;
  first_name?: string | null;
  last_name?: string | null;
}

/**
 * Serialised representation of a user row.
 *
 * The backend deliberately excludes ``password_hash`` from the response
 * to prevent leaking credential hashes to API clients.
 */
export interface UserRead {
  id: string;
  username: string;
  email: string;
  role: UserRole;
  is_active: boolean;
  /** Given name (nullable — legacy users may not have it). */
  first_name?: string | null;
  /** Family name (nullable — legacy users may not have it). */
  last_name?: string | null;
  /** ISO-8601 timestamp. */
  created_at: string;
  /** ISO-8601 timestamp. */
  updated_at: string;
}
