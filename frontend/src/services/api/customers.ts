import api from "../api";
import type {
  CustomerCreate,
  CustomerRead,
  CustomerSettingsContract,
  CustomerUpdate,
} from "../../types/customer";

/** DEV-72: what the project asks of each customer — the settings its `.env.example` marks. */
export function getCustomerSettingsContract(slug: string): Promise<CustomerSettingsContract> {
  return api.get<CustomerSettingsContract>(`/projects/${slug}/customer-settings`);
}

/** List a project's customers (newest first). Project-scoped by slug. */
export function listCustomers(slug: string): Promise<CustomerRead[]> {
  return api.get<CustomerRead[]>(`/projects/${slug}/customers`);
}

/** Register a customer through the form (design §3.2). `ri` role only.
 *  A supplied `secret` goes to the credentials store and is never echoed back. */
export function createCustomer(slug: string, data: CustomerCreate): Promise<CustomerRead> {
  return api.post<CustomerRead>(`/projects/${slug}/customers`, data);
}

export function getCustomer(customerId: string): Promise<CustomerRead> {
  return api.get<CustomerRead>(`/customers/${customerId}`);
}

/** Partial update / secret rotation. `ri` role only. */
export function updateCustomer(customerId: string, data: CustomerUpdate): Promise<CustomerRead> {
  return api.patch<CustomerRead>(`/customers/${customerId}`, data);
}

export function deleteCustomer(customerId: string): Promise<void> {
  return api.delete(`/customers/${customerId}`);
}
