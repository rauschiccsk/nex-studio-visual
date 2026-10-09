// DEV-7: what both approval surfaces say about the version's database schema — the Návrh approval (SchvalitBar)
// and the Programovanie stop (SchemaApprovalBar). One wording, two readers.

import type { DatabaseSchema } from "@/services/api/pipeline";

/** Shown to anyone whose role may not approve a database change (icc/SCHEMA_GOVERNANCE.md). */
export const SCHEMA_APPROVER_ONLY =
  "Štruktúru databázy schvaľuje len Ri — tento krok schváli účet s rolou Ri.";

/** What the change is, measured against the approved schema in the Knowledge Base. */
export function schemaChangeSentence(schema: DatabaseSchema): string {
  if (!schema.kb_exists) return "Je to prvá schéma databázy projektu — v Znalostnej báze ešte nie je.";
  return `Oproti schválenej schéme v Znalostnej báze pribúda ${schema.added_lines} a ubúda ${schema.removed_lines} riadkov.`;
}

/** The Špecifikácia page opened right on the version's schema document. */
export function schemaDocLink(schema: DatabaseSchema): string {
  return `/specifikacia?doc=${encodeURIComponent(schema.path)}`;
}
