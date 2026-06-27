import DeployMatrixPage from "@/components/deploy/DeployMatrixPage";

/**
 * 🚀 PROD — the per-customer production deploy tab (CR-V2-027, design §3.3/§3.5).
 *
 * A version × customer matrix: per customer, the version in production and a
 * Nasadiť action. The never-bypassed acceptance gate applies — PROD Nasadiť is
 * DISABLED until that (version, customer) UAT has been accepted (incident
 * 2026-06-10). Project-scoped; different customers may run different versions.
 */
export default function ProdPage() {
  return <DeployMatrixPage environment="prod" />;
}
