/**
 * DEV-72 — which settings a project asks of each customer, and which a customer still lacks.
 */

import type { CustomerSetting, CustomerSettingsContract } from "@/types/customer";

export function customerSettings(contract: CustomerSettingsContract | null): CustomerSetting[] {
  return (contract?.settings ?? []).filter((s) => s.kind === "customer" || s.kind === "customer_secret");
}

/** The required settings a customer has no value for — by the app's words. */
export function missingRequired(
  contract: CustomerSettingsContract | null,
  settings: Record<string, string>,
  storedSecrets: string[],
): string[] {
  return customerSettings(contract)
    .filter((s) => s.required)
    .filter((s) => (s.kind === "customer_secret" ? !storedSecrets.includes(s.key) : !settings[s.key]))
    .map((s) => s.label);
}
