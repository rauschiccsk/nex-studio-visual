/**
 * Barrel export for the ``@/types`` module.
 *
 * Every domain type file is re-exported from here so feature code can
 * write ``import type { ProjectRead, UserRole } from "@/types"``
 * without reaching for individual modules.  Keep this list in sync
 * with the files in ``src/types/`` — when you add a new type file, add
 * a matching ``export *`` line below.
 */

// Shared envelopes.
export * from "./common";

// Foundation.
export * from "./user";
export * from "./userSession";

// Projects and modules.
export * from "./project";
export * from "./projectModule";
export * from "./moduleDependency";

// Specifications and design documents.
export * from "./rawSpecification";
export * from "./professionalSpecification";
export * from "./designDocument";

// Knowledge base.
export * from "./kbDocument";

// Architect.
export * from "./architectSession";
export * from "./architectMessage";
export * from "./architect";

// Task hierarchy (Epic → Feat → Task).
export * from "./epic";
export * from "./feat";
export * from "./task";

// Bugs and fix tasks.
export * from "./bug";
export * from "./bugFixTask";

// Delegations and execution logs.
export * from "./delegation";
export * from "./executionLog";
export * from "./autoFixAttempt";

// Guardian.
export * from "./guardian";

// Reporting.
export * from "./reportConfig";

// Migration.
export * from "./migrationBatch";
export * from "./migrationCategoryStatus";
export * from "./migrationIdMap";

// Versions.
export * from "./version";

// Project creation form.
export * from "./project-creation";
