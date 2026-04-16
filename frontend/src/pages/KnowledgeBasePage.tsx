import { useParams } from "react-router-dom";

/**
 * Knowledge Base browser page. Implemented in a later feat.
 */
function KnowledgeBasePage() {
  const { slug } = useParams<{ slug: string }>();

  return (
    <section className="space-y-2">
      <h2 className="text-xl font-semibold text-gray-900">
        Knowledge Base — {slug ?? "(unknown)"}
      </h2>
      <p className="text-sm text-gray-600">
        KB browser will be rendered here.
      </p>
    </section>
  );
}

export default KnowledgeBasePage;
