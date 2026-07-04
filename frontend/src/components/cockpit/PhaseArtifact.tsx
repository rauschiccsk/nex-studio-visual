// A phase's durable artifact (CR-V2-021, design §4.4.2 "Tab contents — kept forever, per version").
//
// Each phase persists its output as a durable record: the phase's gate_report / verdict message carries the
// human-readable markdown in ``payload.report`` (PREP = Špecifikácia, NÁVRH = design doc incl. task plan,
// VERIFIKÁCIA = Auditor verdict). This renders the LATEST such artifact for the viewed phase as Markdown so
// a finished phase stays viewable after the build completes (no vanish — the old task-plan pain). When there
// is no artifact yet (the phase hasn't produced its report), the panel shows a phase-appropriate placeholder.

import { useEffect, useState } from "react";

import { SpecMarkdown } from "../markdown/SpecMarkdown";
import type { PipelineMessage } from "../../services/api/pipeline";
import { getProjectSpecContent } from "../../services/api/projectSpecs";
import { useActiveContextStore } from "../../store/activeContextStore";
import type { BuildPhase } from "./labels";

// CR-V2-035: phases that persist a full FILE artifact — render the WHOLE document so the Manažér can read
// it before approving, not just the gate_report summary. ``priprava`` → Špecifikácia, ``navrh`` → design
// doc; both live at the version spec path ``docs/specs/versions/v<N>/<file>``.
const PHASE_ARTIFACT_FILE: Partial<Record<BuildPhase, string>> = {
  priprava: "specification.md",
  navrh: "design.md",
};

// The latest message for ``phase`` carrying a renderable artifact body (``payload.report`` — the durable
// markdown — or, as a fallback, the message ``content`` of the phase's gate_report / verdict turn).
export function latestPhaseArtifact(messages: PipelineMessage[], phase: BuildPhase): string | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (!m || m.stage !== phase) continue;
    if (m.kind !== "gate_report" && m.kind !== "verdict") continue;
    const report = (m.payload as { report?: string } | null)?.report;
    const body = (report && report.trim()) || (m.content && m.content.trim());
    if (body) return body;
  }
  return null;
}

interface Props {
  phase: BuildPhase;
  messages: PipelineMessage[];
  placeholder: string;
}

export function PhaseArtifact({ phase, messages, placeholder }: Props) {
  const project = useActiveContextStore((s) => s.selectedProject);
  const version = useActiveContextStore((s) => s.selectedVersion);
  const summary = latestPhaseArtifact(messages, phase); // gate_report summary — the fallback

  // CR-V2-035: for a file-backed phase, read the FULL artifact (specification.md / design.md). The
  // gate_report summary stays the fallback (while loading, on error, or for library projects with no
  // checkout). Re-fetched when the artifact appears (``summary`` flips truthy at the phase's gate_report).
  const fileName = PHASE_ARTIFACT_FILE[phase];
  const slug = project?.slug;
  const versionNumber = version?.versionNumber;
  const hasSummary = Boolean(summary); // re-fetch the file once the phase's gate_report (summary) appears
  const [fileBody, setFileBody] = useState<string | null>(null);
  const [loadingFile, setLoadingFile] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setFileBody(null);
    if (!fileName || !slug || !versionNumber) return;
    const path = `docs/specs/versions/v${versionNumber}/${fileName}`;
    setLoadingFile(true);
    getProjectSpecContent(slug, path)
      .then((res) => {
        if (!cancelled && res.is_text && res.content.trim()) setFileBody(res.content);
      })
      .catch(() => {
        /* not written yet / unreadable → fall back to the gate_report summary */
      })
      .finally(() => {
        if (!cancelled) setLoadingFile(false);
      });
    return () => {
      cancelled = true;
    };
  }, [fileName, slug, versionNumber, hasSummary]);

  const body = fileBody || summary;
  if (!body) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-center text-xs text-[var(--color-text-muted)]">
        {loadingFile ? "Načítavam dokument…" : placeholder}
      </div>
    );
  }
  return (
    <SpecMarkdown
      body={body}
      className="prose prose-sm dark:prose-invert max-w-none px-4 py-3 text-sm text-[var(--color-text-primary)]"
    />
  );
}

export default PhaseArtifact;
