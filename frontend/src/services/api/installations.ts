// DEV-378 — the „Inštalácie“ page: every installation the cockpit deployed, by customer, and whether one lives.
import api from "../api";
import type { components } from "./pipeline.generated";

export type InstallationCustomer = components["schemas"]["InstallationCustomer"];
export type InstallationApp = components["schemas"]["InstallationApp"];
export type InstallationEnvironment = components["schemas"]["InstallationEnvironment"];
export type InstallationStatus = components["schemas"]["InstallationStatus"];
export type Environment = "uat" | "prod";

/** Every customer with an installation the person may see — the owner's projects, the admin's all. */
export function listInstallationsApi(): Promise<InstallationCustomer[]> {
  return api.get<InstallationCustomer[]>("/installations");
}

/** Whether one installation lives — the cockpit asks it at its own address and shares the answer for a while. */
export function getInstallationStatusApi(customerId: string, environment: Environment): Promise<InstallationStatus> {
  return api.get<InstallationStatus>(`/installations/${customerId}/${environment}/status`);
}
