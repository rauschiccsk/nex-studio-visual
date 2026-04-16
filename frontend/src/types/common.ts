/**
 * Shared, domain-agnostic TypeScript types.
 *
 * Every REST list endpoint on the NEX Studio backend returns the same
 * envelope — see ``backend.schemas.pagination.PaginatedResponse`` —
 * parameterised by the per-resource ``Read`` schema at the router.  This
 * file mirrors that envelope as a TypeScript generic so every feature
 * API service can reuse it without redeclaring the shape.
 *
 * The shape is intentionally narrow: there is no mixing of cursor- /
 * keyset-pagination here because the backend uses strict
 * offset/limit pagination across the board (DESIGN.md § 6 "REST API
 * Architecture").  If that ever changes, update the backend schema
 * first and then this file.
 */

/**
 * Standard paginated list envelope.
 *
 * Mirrors ``backend.schemas.pagination.PaginatedResponse[T]``:
 *
 *   - ``items``  — current page of rows (already serialised to the
 *     resource's ``Read`` schema).
 *   - ``total``  — unfiltered total matching the same query filters.
 *   - ``skip``   — offset that produced this page.
 *   - ``limit``  — page size requested by the caller.
 */
export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  skip: number;
  limit: number;
}
