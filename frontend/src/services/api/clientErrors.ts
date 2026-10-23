// DEV-192 — what a page of the cockpit crashed on, written to the backend's log.
import api from "../api";
import type { components } from "./pipeline.generated";

type ClientErrorReport = components["schemas"]["ClientErrorReport"];

/** Report a page that crashed while rendering — the backend writes it with who and where. */
export function reportClientErrorApi(report: ClientErrorReport): Promise<void> {
  return api.post<void>("/client-errors", report);
}
