import { useParams } from "react-router-dom";

/**
 * MIG module control panel page. Implemented in a later feat.
 */
function MigrationPage() {
  const { slug } = useParams<{ slug: string }>();

  return (
    <section className="space-y-2">
      <h2 className="text-xl font-semibold text-gray-900">
        Migration — {slug ?? "(unknown)"}
      </h2>
      <p className="text-sm text-gray-600">
        MIG module control panel will be rendered here.
      </p>
    </section>
  );
}

export default MigrationPage;
