/**
 * API client for the Version resource.
 *
 * Maps to the backend routes defined in ``backend.api.routes.versions``:
 *
 *   - ``GET    /projects/{projectId}/versions``        → listVersions
 *   - ``POST   /projects/{projectId}/versions``        → createVersion
 *   - ``GET    /versions/{id}``                        → getVersion
 *   - ``GET    /projects/{projectId}/zadanie-na-disku`` → peekZadanieOnDisk
 *   - ``GET    /versions/{id}/nastavenia``             → getVersionSettings
 *   - ``PATCH  /versions/{id}``                        → updateVersion
 *   - ``POST   /versions/{id}/release``                → releaseVersion
 */

import api from "../api";
import type { Version, VersionCreate, VersionUpdate } from "../../types/version";
import type { TaskPlanResponse } from "../../types/task-plan";

/**
 * List every version belonging to a project, ordered by
 * ``version_number DESC``.
 */
export function listVersions(projectId: string): Promise<Version[]> {
  return api.get<Version[]>(`/projects/${projectId}/versions`);
}

/** Fetch a single version by its UUID. */
export function getVersion(id: string): Promise<Version> {
  return api.get<Version>(`/versions/${id}`);
}

/** Create a new version scoped to the given project. */
export function createVersion(
  projectId: string,
  data: VersionCreate,
): Promise<Version> {
  return api.post<Version>(`/projects/${projectId}/versions`, data);
}

/**
 * Persist a version's free-text **Zadanie** (CR-V2-024, design §4.3).
 *
 * Saves the brief to ``docs/specs/versions/v<N>/customer-requirements.md``
 * in the project workspace — the exact file the Príprava phase reads when
 * the Manažér clicks "Spustiť tvorbu špecifikácie". Create-or-overwrite.
 */
export function writeZadanie(
  versionId: string,
  content: string,
): Promise<{ relative_path: string; status: string }> {
  return api.put<{ relative_path: string; status: string }>(
    `/versions/${versionId}/zadanie`,
    { content },
  );
}

/**
 * Read a version's saved Zadanie (customer-requirements.md content, "" if not yet created). The editor loads
 * from HERE — the saved file is the source of truth, NOT version.description (2026-06-30 fix).
 */
export function readZadanie(versionId: string): Promise<{ content: string }> {
  return api.get<{ content: string }>(`/versions/${versionId}/zadanie`);
}

/**
 * Čo pre toto číslo verzie na disku UŽ leží — ešte pred jej založením (ICCINT-90).
 *
 * `readZadanie` sa pýta cez `versionId`, takže odpovie až vtedy, keď verzia existuje. Pri zakladaní
 * verzie ešte žiadne `versionId` niet — a práve vtedy sa Manažér potrebuje dozvedieť, že v priečinku
 * pripravené Zadanie je. Bez toho ho prehliadne a napíše vlastné vedľa neho.
 *
 * `content` je `""`, keď súbor neexistuje — bežný prípad, nie chyba.
 */
export function peekZadanieOnDisk(
  projectId: string,
  versionNumber: string,
): Promise<{ content: string; relative_path: string }> {
  return api.get<{ content: string; relative_path: string }>(
    `/projects/${projectId}/zadanie-na-disku?version_number=${encodeURIComponent(versionNumber)}`,
  );
}

/**
 * Čo sa na verzii ešte dá meniť (ICCINT-100).
 *
 * Číslo verzie nesie priečinok s dokumentmi (`docs/specs/versions/v<číslo>/`), takže sa smie meniť
 * len dovtedy, kým podľa neho nič nevzniklo. Panel Nastavenia verzie sa pýta sem, aby vedel pole buď
 * povoliť, alebo ho **zamknúť s dôvodom** — zamknuté pole bez vysvetlenia je to isté ako pole, ktoré
 * ticho nefunguje.
 *
 * Zámok sám vynucuje engine v `PATCH`; toto je len to, čo o ňom obrazovka potrebuje vedieť dopredu.
 */
export function getVersionSettings(
  id: string,
): Promise<{ version_number_lock_reason: string | null }> {
  return api.get<{ version_number_lock_reason: string | null }>(`/versions/${id}/nastavenia`);
}

/** Partially update a version's mutable fields. */
export function updateVersion(
  id: string,
  data: VersionUpdate,
): Promise<Version> {
  return api.patch<Version>(`/versions/${id}`, data);
}

/**
 * Trigger the release gate for a version.
 *
 * Sets ``status = 'released'`` and ``release_date = today`` on success.
 * Returns HTTP 422 when blocking EPICs remain.
 */
export function releaseVersion(id: string): Promise<Version> {
  return api.post<Version>(`/versions/${id}/release`);
}

/**
 * Permanently delete a version.
 *
 * Only allowed when the version has no EPICs and is not ``released``.
 * Returns HTTP 204 on success.
 */
export function deleteVersion(id: string): Promise<void> {
  return api.delete<void>(`/versions/${id}`);
}

/**
 * Fetch the EPIC → FEAT → TASK task-plan tree (+ per-node status + counts) the
 * Designer materialized for a version (F-007 task-plan node, CR-NS-020 CR-5).
 * Drives the cockpit ``TaskPlanPanel``; reuses the existing backend endpoint.
 */
export function getTaskPlan(versionId: string): Promise<TaskPlanResponse> {
  return api.get<TaskPlanResponse>(`/versions/${versionId}/task-plan`);
}
