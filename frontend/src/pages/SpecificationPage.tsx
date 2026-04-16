import { useParams } from "react-router-dom";

/**
 * Specification pipeline page. Implemented in a later feat.
 */
function SpecificationPage() {
  const { slug } = useParams<{ slug: string }>();

  return (
    <section className="space-y-2">
      <h2 className="text-xl font-semibold text-gray-900">
        Specification — {slug ?? "(unknown)"}
      </h2>
      <p className="text-sm text-gray-600">
        Specification pipeline (raw spec → professional spec → DESIGN.md) will
        be rendered here.
      </p>
    </section>
  );
}

export default SpecificationPage;
