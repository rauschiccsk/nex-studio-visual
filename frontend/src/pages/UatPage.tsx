import DeployMatrixPage from "@/components/deploy/DeployMatrixPage";

/**
 * 🧪 UAT — the per-customer UAT deploy + acceptance tab (CR-V2-027, design §3.3/§3.5).
 *
 * A version × customer matrix: per customer, the version on its UAT test
 * instance, a link to the live UAT URL, a Nasadiť action (deploy a verified
 * version) and an Akceptovať action that records the Manažér's UAT acceptance
 * (who/when/version/customer) and opens PROD for that pair. Project-scoped.
 */
export default function UatPage() {
  return <DeployMatrixPage environment="uat" />;
}
