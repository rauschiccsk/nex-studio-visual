/**
 * API client for the Port Registry.
 *
 * Provides real-time port availability checks and port suggestion
 * for the "New Project" form.
 *
 * Maps to backend routes (Task 24.5):
 *
 *   - ``GET /ports/check?port={port}``                   → checkPortAvailability
 *   - ``GET /ports/suggest?type={backend|frontend|db}``   → suggestNextAvailablePort
 */

import api from "../api";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface PortCheckResponse {
  port: number;
  available: boolean;
  conflicting_project?: string;
}

interface PortSuggestionResponse {
  port: number;
  type: "backend" | "frontend" | "db";
}

// ---------------------------------------------------------------------------
// API functions
// ---------------------------------------------------------------------------

/**
 * Check whether a given port number is available (not assigned to any
 * existing project).
 *
 * @returns ``true`` if the port is free, ``false`` if it conflicts.
 */
export async function checkPortAvailability(port: number): Promise<boolean> {
  const resp = await api.get<PortCheckResponse>("/ports/check", {
    params: { port },
  });
  return resp.available;
}

/**
 * Ask the backend to suggest the next available port for a given service
 * type (backend, frontend, or db).
 *
 * The backend determines the suggestion based on the ICC port registry
 * conventions.
 *
 * @returns The suggested port number.
 */
export async function suggestNextAvailablePort(
  type: "backend" | "frontend" | "db",
): Promise<number> {
  const resp = await api.get<PortSuggestionResponse>("/ports/suggest", {
    params: { type },
  });
  return resp.port;
}
