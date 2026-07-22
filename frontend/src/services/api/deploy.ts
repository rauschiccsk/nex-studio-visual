import api from "../api";
import type {
  AcceptRequest,
  DeployEventRead,
  DeployMatrix,
  DeployRequest,
  DeployResult,
} from "../../types/deploy";

/**
 * API client for the per-customer Deploy subsystem (v2.0.0, CR-V2-026/027).
 *
 * Maps to `backend.api.routes.deploy`:
 *   - GET  /projects/{slug}/deploy-matrix        → getDeployMatrix
 *   - GET  /projects/{slug}/deploy-events        → listProjectDeployEvents
 *   - GET  /customers/{customerId}/deploy-events → listCustomerDeployEvents
 *   - POST /customers/{customerId}/deploy        → deployCustomer (Nasadiť)
 *   - POST /customers/{customerId}/accept        → acceptCustomerUat (Akceptovať)
 *
 * Secret handling (CLAUDE.md §4/§5, OQ-5): no call here sends or receives secret
 * material; per-customer secrets live only in the backend credentials store.
 */

/** The version × customer matrix feeding the UAT / PROD tabs (design §3.3). */
export function getDeployMatrix(slug: string): Promise<DeployMatrix> {
  return api.get<DeployMatrix>(`/projects/${slug}/deploy-matrix`);
}

/** Mint a short-lived UAT test launch URL for a token-launch app — open it to land LOGGED-IN in the
 * deployed app directly from the UAT tab, without going through NEX Manager (v4.0.30). UAT-only. */
export function uatLaunch(customerId: string, projectSlug: string): Promise<{ launch_url: string }> {
  return api.post(`/customers/${customerId}/uat-launch`, { project_slug: projectSlug });
}

/** Every deploy/accept event for a project (newest first) — the audit trail. */
export function listProjectDeployEvents(slug: string): Promise<DeployEventRead[]> {
  return api.get<DeployEventRead[]>(`/projects/${slug}/deploy-events`);
}

/** Every deploy/accept event for one customer (newest first). */
export function listCustomerDeployEvents(customerId: string): Promise<DeployEventRead[]> {
  return api.get<DeployEventRead[]>(`/customers/${customerId}/deploy-events`);
}

/**
 * Nasadiť — deploy a verified version to a customer's UAT/PROD instance (§3.4).
 * `ri` role only. A PROD deploy is rejected (409) unless the customer's UAT of
 * that version was accepted (§3.5, the never-bypassed gate).
 */
export function deployCustomer(customerId: string, data: DeployRequest): Promise<DeployResult> {
  return api.post<DeployResult>(`/customers/${customerId}/deploy`, data);
}

/**
 * Akceptovať — record a Manažér's UAT acceptance, opening PROD (§3.5). `ri` role
 * only. Logs who/when/version/customer.
 */
export function acceptCustomerUat(customerId: string, data: AcceptRequest): Promise<DeployEventRead> {
  return api.post<DeployEventRead>(`/customers/${customerId}/accept`, data);
}
