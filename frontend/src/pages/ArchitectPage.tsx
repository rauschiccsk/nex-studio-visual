import { useParams } from "react-router-dom";

/**
 * Architect chat — used for both project-level and module-level conversations
 * (DESIGN.md § 3.1). When the `code` route param is present, the chat is
 * scoped to that specific module.
 */
function ArchitectPage() {
  const { slug, code } = useParams<{ slug: string; code?: string }>();

  return (
    <section className="space-y-2">
      <h2 className="text-xl font-semibold text-gray-900">
        Architect — {slug ?? "(unknown)"}
        {code ? ` / ${code}` : ""}
      </h2>
      <p className="text-sm text-gray-600">
        {code
          ? "Module-level Architect chat will be rendered here."
          : "Project-level Architect chat will be rendered here."}
      </p>
    </section>
  );
}

export default ArchitectPage;
