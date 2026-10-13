// BillingPage — the billing of a project (DEV-73): the delivery statements of all its versions, issued from one place.
//
// It was a third view of the costs page; on 13.10.2026 the Director asked for it as a sidebar tab of its own, right
// below the costs tab. Invoicing is its own errand — the cost tables are about what the work cost us.

import { useParams } from "react-router-dom";

import BillingTab from "@/components/naklady/BillingTab";
import { useActiveContextStore } from "@/store/activeContextStore";

export default function BillingPage() {
  const { slug } = useParams<{ slug: string }>();
  const selectedProject = useActiveContextStore((s) => s.selectedProject);
  const projectName = selectedProject?.slug === slug ? selectedProject?.name : slug;

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <h1 className="text-base font-bold text-[var(--color-text-primary)] mb-2">Fakturácia — {projectName}</h1>
      {slug && <BillingTab slug={slug} />}
    </div>
  );
}
