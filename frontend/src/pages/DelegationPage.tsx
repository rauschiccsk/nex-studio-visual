import { useParams } from "react-router-dom";

/**
 * Active delegation + live CC output page. Implemented in a later feat.
 */
function DelegationPage() {
  const { slug } = useParams<{ slug: string }>();

  return (
    <section className="space-y-2">
      <h2 className="text-xl font-semibold text-gray-900">
        Delegation — {slug ?? "(unknown)"}
      </h2>
      <p className="text-sm text-gray-600">
        Active delegation status and live CC output will be rendered here.
      </p>
    </section>
  );
}

export default DelegationPage;
