import api from "../api";

/** A text file read from under ``/opt/projects/<slug>/`` (backend ``GET /project-specs/content``). */
export interface ProjectSpecContent {
  relative_path: string;
  content: string;
  is_text: boolean;
}

/**
 * Read a project file by its repo-relative path (CR-V2-035). The Vývoj phase tabs use this to render the
 * FULL durable artifact — the Špecifikácia (``specification.md``) / návrhový dokument (``design.md``) —
 * so the Manažér can read the whole document before approving, not just the gate_report summary.
 */
export function getProjectSpecContent(slug: string, path: string): Promise<ProjectSpecContent> {
  return api.get<ProjectSpecContent>("/project-specs/content", { params: { slug, path } });
}

/** One filesystem entry under ``/opt/projects/<slug>/docs/`` (backend ``GET /project-specs/list``). */
export interface ProjectSpecDoc {
  /** Path relative to ``/opt/projects/``, e.g. ``nex-payables/docs/specs/versions/v1.0.0/design.md``. */
  relative_path: string;
  filename: string;
  /** Parent folder within ``/opt/projects/``, e.g. ``nex-payables/docs/specs/versions/v1.0.0``. */
  category: string;
  size_bytes: number;
  is_directory: boolean;
}

export interface ProjectSpecListResponse {
  documents: ProjectSpecDoc[];
  count: number;
}

/**
 * List every doc under each project's ``docs`` folder (a FLAT list across all projects). The Špecifikácia
 * page filters it to the pinned project + version's ``docs/specs`` folder so the Manažér can open EVERY
 * document the AI produced (design.md, DATABASE_SCHEMAS.md, customer-requirements.md, …), not just the spec.
 */
export function listProjectSpecs(): Promise<ProjectSpecListResponse> {
  return api.get<ProjectSpecListResponse>("/project-specs/list");
}
