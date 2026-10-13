/**
 * DEV-86 — NS Code: the files of the selected project, read-only and without secrets. Maps to
 * ``backend.api.routes.ns_code``; the server decides what may be shown, once for the list, the file and the search.
 */

import api from "@/services/api";
import type { components } from "@/services/api/pipeline.generated";

export type CodeFiles = components["schemas"]["CodeFilesRead"];
export type CodeFile = components["schemas"]["CodeFileRead"];
export type CodeSearch = components["schemas"]["CodeSearchRead"];
/** DEV-87 — the file as it was: at the last commit, or where the version began. */
export type CodeOriginal = components["schemas"]["CodeOriginalRead"];

const base = (slug: string) => `/projects/${encodeURIComponent(slug)}/code`;

export function getCodeFiles(slug: string): Promise<CodeFiles> {
  return api.get<CodeFiles>(`${base(slug)}/files`);
}

export function getCodeFile(slug: string, path: string): Promise<CodeFile> {
  return api.get<CodeFile>(`${base(slug)}/file?path=${encodeURIComponent(path)}`);
}

export function searchCode(slug: string, query: string): Promise<CodeSearch> {
  return api.get<CodeSearch>(`${base(slug)}/search?q=${encodeURIComponent(query)}`);
}

/** DEV-87 — `head`: the last commit; `base`: where `versionId` began (the delivery statement's start). */
export function getCodeOriginal(
  slug: string,
  path: string,
  against: "head" | "base",
  versionId?: string,
): Promise<CodeOriginal> {
  const version = against === "base" && versionId ? `&version_id=${encodeURIComponent(versionId)}` : "";
  return api.get<CodeOriginal>(`${base(slug)}/original?path=${encodeURIComponent(path)}&against=${against}${version}`);
}
