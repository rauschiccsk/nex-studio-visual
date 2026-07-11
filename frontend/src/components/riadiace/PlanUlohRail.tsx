// PlanUlohRail — the right rail of the Riadiace centrum: the "Plán úloh" three-layer MANAGER MAP (STEP 3,
// docs/architecture/step3-plan-design.md). After the Manažér approves the Špecifikácia, the partner builds the
// task plan from it (in the same conversation); this rail renders that plan as the single source of truth —
// the real task rows in the DB (EPIC → FEAT → TASK), fetched over the EXISTING getTaskPlan endpoint.
//
// Three layers per node (honest-by-construction):
//   L0 (always): number + title + status dot (unified TASK_STATUS labels/tones, cockpit/labels).
//   L1 (always, under L0): the plain-language one-liner (plain_description) via the shared SpecMarkdown.
//                          When empty → a muted "(bez ľudského vysvetlenia)" placeholder — NEVER a silent
//                          fall-back to the technical description.
//   L2 (technical): the programmer detail (description) — shown ONLY on expand, never the default view.
//
// Salvaged from cockpit/TaskPlanPanel: getTaskPlan + refetch-on-message-growth + per-version localStorage
// persistence. The persisted set here tracks which nodes have their L2 technical detail EXPANDED (default =
// empty = technical hidden), under a distinct key so it can't collide with the cockpit panel's collapse set.
//
// The "Zostaviť plán" TRIGGER (MD-1 rec A) renders ONLY when board.available_actions offers `zostav_plan`
// (honest-by-construction, like "Schváliť Špecifikáciu") — the backend gates it (conversation + spec approved
// + plan not yet built). On click it fires the EXISTING postPipelineActionApi and swaps in the fresh board.
//
// STEP 4 (Programovanie, docs/architecture/step4-programovanie-design.md) extends the SAME action slot into a
// mutually-exclusive trigger ladder — `spustit_stavbu` ("Spustiť stavbu", start the build loop),
// `pokracovat` ("Pokračovať v stavbe", resume a paused/token-stopped loop) and `pause` ("Pozastaviť",
// cooperatively hold a running loop) sit beside `zostav_plan`, each gated the same honest-by-construction way
// (available_actions) and firing the same postPipelineActionApi. A running build offers `pause`; a paused one
// offers `pokracovat` — the ladder shows exactly one. A "Práve robím: #N title" banner (board.current_task,
// live during the build) and an amber paused note (status === 'paused') round out the STEP-4 surface.
//
// A build-progress indicator (salvaged from cockpit/TaskPlanPanel, CR-NS-025 Part 2) sits directly above the
// tree: "<done>/<total> úloh hotových" + a slim green bar + "N %", computed live off the SAME fetched plan
// (the message-growth refetch advances it as tasks finish). Shown only when the plan has tasks. All additive,
// no new endpoint/WS/backend change.

import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ChevronDown, ChevronRight, Loader2, Rocket } from "lucide-react";

import { getTaskPlan } from "../../services/api/versions";
import { findCurrentTaskPath, type CurrentTaskPath } from "./currentTaskPath";
import { postPipelineActionApi } from "../../services/api/pipeline";
import type { PipelineActionName, PipelineBoard, PipelineMessage } from "../../services/api/pipeline";
import type {
  TaskPlanResponse,
  TaskPlanEpicNode,
  TaskPlanFeatNode,
  TaskPlanTaskNode,
} from "../../types/task-plan";
import { TASK_STATUS_LABELS, TASK_TYPE_LABELS, TASK_STATUS_TONE, TONE_DOT, verificationUnconfirmed } from "../cockpit/labels";
import { SpecMarkdown } from "../markdown/SpecMarkdown";
import { humanizeApiError, type HumanError } from "../../services/apiError";
import ErrorNote from "../common/ErrorNote";

interface Props {
  versionId: string | null;
  /** Live message stream (board.recent_messages) — the debounce key for tree-freshness refetch. */
  messages: PipelineMessage[];
  /** The live board — carries available_actions (gates the "Zostaviť plán" trigger). */
  board: PipelineBoard | null;
  /** Replace the live board with the fresh one the action returns (setBoard from usePipelineWs). */
  onBoard: (board: PipelineBoard) => void;
}

// Level colour-coding (design §4.5, salvaged): EPIC = purple, FEAT = yellow, TASK = blue, on the node TITLE
// (the status dot keeps the unified palette). Light-readable + dark-readable via -700/-300 (CR-NS-067c).
const EPIC_LEVEL_COLOR = "text-purple-700 dark:text-purple-300";
const FEAT_LEVEL_COLOR = "text-yellow-700 dark:text-yellow-300";
const TASK_LEVEL_COLOR = "text-blue-700 dark:text-blue-300";

// localStorage key for the EXPANDED-node set (which nodes show their L2 technical detail), scoped per version.
// Distinct prefix from the cockpit panel's `nex_taskplan_collapsed_*` — different semantics, no collision.
function expandedStorageKey(versionId: string): string {
  return `nex_planrail_expanded_${versionId}`;
}

function readExpanded(versionId: string): Set<string> {
  if (typeof window === "undefined") return new Set();
  try {
    const raw = window.localStorage.getItem(expandedStorageKey(versionId));
    if (!raw) return new Set();
    const ids = JSON.parse(raw) as unknown;
    return Array.isArray(ids) ? new Set(ids.filter((id): id is string => typeof id === "string")) : new Set();
  } catch {
    return new Set();
  }
}

function writeExpanded(versionId: string, expanded: Set<string>): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(expandedStorageKey(versionId), JSON.stringify([...expanded]));
  } catch {
    // Quota / disabled storage — the rail still works in-session, just doesn't persist.
  }
}

// localStorage key for the COLLAPSED-node set (EPIC/FEAT ids whose CHILDREN are hidden — a real subtree
// collapse), scoped per version. Intentionally DISTINCT from `nex_planrail_expanded_*` above: the two sets
// carry different semantics (this hides the subtree; that reveals L2 technical detail) and must never collide.
function collapsedStorageKey(versionId: string): string {
  return `nex_planrail_collapsed_${versionId}`;
}

function readCollapsed(versionId: string): Set<string> {
  if (typeof window === "undefined") return new Set();
  try {
    const raw = window.localStorage.getItem(collapsedStorageKey(versionId));
    if (!raw) return new Set();
    const ids = JSON.parse(raw) as unknown;
    return Array.isArray(ids) ? new Set(ids.filter((id): id is string => typeof id === "string")) : new Set();
  } catch {
    return new Set();
  }
}

function writeCollapsed(versionId: string, collapsed: Set<string>): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(collapsedStorageKey(versionId), JSON.stringify([...collapsed]));
  } catch {
    // Quota / disabled storage — the rail still works in-session, just doesn't persist.
  }
}

// Whether a collapsed set has EVER been persisted for this version (obs #3 real fix). Distinct from
// `readCollapsed`, which returns an EMPTY Set for BOTH an absent key and an empty `[]` — indistinguishable, yet the
// difference is load-bearing: an absent key = the version has never been visited (apply the done-on-load default
// once), an empty `[]` = the Manažér has been here and expanded everything (respect it verbatim, never re-collapse).
function collapsedKeyExists(versionId: string): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(collapsedStorageKey(versionId)) !== null;
  } catch {
    return false;
  }
}

// L0 trailing status chip — dot colour from the unified palette (CR-NS-028) + Slovak label.
function StatusDot({ status }: { status: string }) {
  return (
    <span className="inline-flex flex-shrink-0 items-center gap-1 text-[10px] text-[var(--color-text-secondary)]">
      <span className={`h-1.5 w-1.5 rounded-full ${TONE_DOT[TASK_STATUS_TONE[status] ?? "neutral"]}`} />
      {TASK_STATUS_LABELS[status] ?? "Neznámy stav"}
    </span>
  );
}

// L1 — the plain-language line. Empty ⇒ a muted italic placeholder; NEVER the technical description.
function PlainLine({ text }: { text: string }) {
  const trimmed = (text ?? "").trim();
  if (!trimmed) {
    return (
      <p className="mt-0.5 pl-[1.125rem] pr-1 text-[11px] italic text-[var(--color-text-muted)]">
        (bez ľudského vysvetlenia)
      </p>
    );
  }
  return (
    <SpecMarkdown
      body={trimmed}
      className="mt-0.5 pl-[1.125rem] pr-1 text-[11px] leading-snug text-[var(--color-text-secondary)]"
    />
  );
}

// L2 — the technical detail, rendered only when the node is expanded.
function TechnicalDetail({ text }: { text: string }) {
  return (
    <div className="ml-[1.125rem] mt-1 rounded border-l-2 border-[var(--color-border-default)] bg-[var(--color-surface-hover)] px-2 py-1">
      <div className="mb-0.5 text-[9px] font-medium uppercase tracking-wide text-[var(--color-text-muted)]">
        Technický detail
      </div>
      <SpecMarkdown body={text} className="text-[11px] leading-snug text-[var(--color-text-muted)]" />
    </div>
  );
}

// CurrentBuildBanner (STEP 4) — a compact "Práve robím: …" banner pinned at the top of the rail body. Fed by
// board.current_task (populated by the BE ONLY during Programovanie); the caller hides this entirely when
// current_task is null. The blue dot pulses while the agent is actively working (status agent_working) —
// derived from the live status, never guessed. This is SEPARATE from the per-node live in_progress dot: this
// is the single "what am I on right now" line for the whole build, not a tree node.
// Director observation #4: when the task is located in the plan tree (`path`) the banner shows the full
// "E1 <epic> › F2 <feat> › T5: <task>" hierarchy for context; otherwise it falls back to the bare "#N title".
function CurrentBuildBanner({
  path,
  fallback,
  working,
}: {
  path: CurrentTaskPath | null;
  fallback: { number: number; title: string };
  working: boolean;
}) {
  return (
    <div className="flex flex-shrink-0 items-center gap-2 border-b border-[var(--color-border-default)] bg-[var(--color-surface-hover)] px-4 py-2">
      <span className={`h-1.5 w-1.5 flex-shrink-0 rounded-full bg-blue-500 ${working ? "animate-pulse" : ""}`} />
      <span className="min-w-0 truncate text-[11px] text-[var(--color-text-secondary)]">
        <span className="font-medium text-[var(--color-text-primary)]">Práve robím:</span>{" "}
        {path
          ? `E${path.epic.number} ${path.epic.title} › F${path.feat.number} ${path.feat.title} › T${path.task.number}: ${path.task.title}`
          : `#${fallback.number} ${fallback.title}`}
      </span>
    </div>
  );
}

// One node with TWO independent interactions (Director-approved separation, Director observation #3):
//   • the left CHEVRON (only on nodes that HAVE children) toggles a REAL subtree collapse — a collapsed node
//     renders ONLY its L0 header row (number, title, status dot, chevron): no L1 plain line, no L2 technical
//     detail, no children. ChevronDown = expanded, ChevronRight = collapsed.
//   • the node TITLE (a button only when there IS L2 technical detail) toggles that L2 reveal — moved OFF the
//     chevron so the two never conflict. Leaf tasks (no children) carry only this title/technical reveal.
// `bold` renders the epic title heavier; `taskType` is the task's tiny tag.
function PlanNode(props: {
  nodeId: string;
  number: number;
  title: string;
  status: string;
  plain: string;
  technical?: string;
  taskType?: string;
  levelColor: string;
  bold?: boolean;
  hasChildren: boolean;
  isCollapsed: boolean;
  onToggleCollapse: () => void;
  isExpanded: boolean;
  onToggleTechnical: () => void;
  className?: string;
  children?: React.ReactNode;
}) {
  const {
    nodeId,
    number,
    title,
    status,
    plain,
    technical,
    taskType,
    levelColor,
    bold,
    hasChildren,
    isCollapsed,
    onToggleCollapse,
    isExpanded,
    onToggleTechnical,
    className,
    children,
  } = props;
  const hasTechnical = !!(technical ?? "").trim();
  return (
    <div className={className}>
      <div className="flex items-center justify-between gap-2 rounded px-1 py-0.5 hover:bg-[var(--color-surface-hover)]">
        <span className={`flex min-w-0 items-center gap-1.5 ${levelColor}`}>
          {hasChildren ? (
            <button
              type="button"
              onClick={onToggleCollapse}
              aria-expanded={!isCollapsed}
              aria-label={isCollapsed ? "Rozbaliť podúlohy" : "Zbaliť podúlohy"}
              data-testid={`planrail-chevron-${nodeId}`}
              className="flex-shrink-0 rounded p-0.5 hover:bg-[var(--color-surface-active)]"
            >
              {isCollapsed ? <ChevronRight className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
            </button>
          ) : (
            <span className="inline-block h-3 w-3 flex-shrink-0" />
          )}
          {hasTechnical ? (
            <button
              type="button"
              onClick={onToggleTechnical}
              aria-expanded={isExpanded}
              className={`min-w-0 truncate text-left hover:underline ${bold ? "font-medium" : ""}`}
            >
              {number}. {title}
            </button>
          ) : (
            <span className={`truncate ${bold ? "font-medium" : ""}`}>
              {number}. {title}
            </span>
          )}
          {taskType && (
            <span className="flex-shrink-0 text-[9px] uppercase text-[var(--color-text-muted)]">
              {TASK_TYPE_LABELS[taskType] ?? taskType}
            </span>
          )}
        </span>
        <StatusDot status={status} />
      </div>
      {/* A collapsed node is reduced to its single header line — its own L1/L2 and its whole subtree vanish. */}
      {!isCollapsed && <PlainLine text={plain} />}
      {!isCollapsed && hasTechnical && isExpanded && <TechnicalDetail text={technical ?? ""} />}
      {!isCollapsed && children}
    </div>
  );
}

export function PlanUlohRail({ versionId, messages, board, onBoard }: Props) {
  const navigate = useNavigate();
  const [plan, setPlan] = useState<TaskPlanResponse | null>(null);
  const [error, setError] = useState<HumanError | null>(null);
  // The set of nodes whose L2 technical detail is expanded, hydrated from localStorage (per version, per
  // browser) so the choice survives navigation + reload. Default empty ⇒ technical hidden (not the default view).
  const [expanded, setExpanded] = useState<Set<string>>(() => (versionId ? readExpanded(versionId) : new Set()));
  // The set of EPIC/FEAT ids whose CHILDREN are collapsed (a real subtree hide), hydrated per version. Default
  // empty ⇒ the whole plan is visible. Separate from `expanded` above (different semantics + localStorage key).
  const [collapsed, setCollapsed] = useState<Set<string>>(() => (versionId ? readCollapsed(versionId) : new Set()));
  const [triggering, setTriggering] = useState(false);
  const [triggerError, setTriggerError] = useState<HumanError | null>(null);

  // Last-seen status per node id, to detect `* → done` transitions for auto-collapse (req 4) WITHOUT re-collapsing
  // on every render (else the Manažér could never keep a done node open). Reset per version. versionIdRef lets the
  // transition effect persist under the right key while keying only on `plan` (so a version switch — which nulls no
  // plan — never processes a stale tree under the new version's key before its own refetch lands).
  const seenStatusRef = useRef<Map<string, string>>(new Map());
  const versionIdRef = useRef<string | null>(versionId);
  // Whether the persisted collapsed/expanded sets have been loaded for the CURRENT versionId (obs #3). The
  // useState initializers above run once at mount — if `versionId` is still null then (the prop arrives async on a
  // tab remount) they seed EMPTY. Without this guard the auto-collapse effect could then run against the un-hydrated
  // (empty) set and PERSIST done-collapses computed off it, clobbering the Manažér's saved choice. Hydration flips
  // this true; auto-collapse waits on it so it only ever AUGMENTS the restored set, never replaces it.
  const hydratedRef = useRef(false);

  // Hydrate the persisted sets from localStorage on mount AND the moment `versionId` becomes a valid non-null value
  // (obs #3 — not only on a change between two non-null ids). Effects always run on mount, so a tab remount whose
  // initializer saw a null prop first still re-reads the saved collapsed/expanded sets once `versionId` resolves.
  useEffect(() => {
    seenStatusRef.current = new Map();
    versionIdRef.current = versionId;
    if (versionId) {
      setExpanded(readExpanded(versionId));
      setCollapsed(readCollapsed(versionId));
      hydratedRef.current = true;
    } else {
      setExpanded(new Set());
      setCollapsed(new Set());
      hydratedRef.current = false;
    }
  }, [versionId]);

  // Tree-freshness: refetch on mount, on version change, and whenever the live message stream grows, so node
  // statuses track the build loop without a new endpoint/WS field. messages.length is the debounce key.
  useEffect(() => {
    if (!versionId) {
      setPlan(null);
      return;
    }
    let cancelled = false;
    getTaskPlan(versionId)
      .then((p) => {
        if (!cancelled) {
          setPlan(p);
          setError(null);
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(humanizeApiError(e, "Načítanie plánu zlyhalo"));
      });
    return () => {
      cancelled = true;
    };
  }, [versionId, messages.length]);

  // Auto-collapse on done (req 4): keyed on the fetched plan. The effect separates the two behaviours it used to
  // conflate via the empty-`seen` trick (obs #3 real fix) — `seenStatusRef` resets on every mount, so the old
  // "prev is undefined ≠ 'done'" test wrongly treated EVERY already-done node as a fresh transition on the first
  // plan-fetch after a REMOUNT, re-collapsing done nodes the Manažér had manually expanded and clobbering that
  // persisted choice. Now:
  //   • FIRST pass this mount (`seen` empty) — SEED `seen` with all current statuses (so already-done nodes are
  //     never mistaken for transitions on later passes) and apply the done-on-load default ONLY the first time the
  //     version is ever seen (collapsed key ABSENT). If the key already exists, respect the persisted set verbatim
  //     — this is what preserves a manual expand across a tab switch + return.
  //   • SUBSEQUENT passes (`seen` non-empty) — the genuine `* → done` runtime transition (collapse + persist).
  // Uses versionIdRef so it keys only on `plan` — a stale tree from the previous version can't be processed here.
  useEffect(() => {
    const vId = versionIdRef.current;
    // Obs #3: never run auto-collapse before hydration has restored the persisted set — otherwise it would compute
    // done-collapses against an empty set and persist them, clobbering the Manažér's saved manual choice.
    if (!plan || !vId || !hydratedRef.current) return;
    const seen = seenStatusRef.current;

    if (seen.size === 0) {
      // First pass after this mount: record every node's status up front so no already-done node is later read as
      // a fresh transition, and collect the done EPIC/FEAT for the (first-ever-only) done-on-load default.
      const doneOnLoad: string[] = [];
      for (const epic of plan.plan) {
        seen.set(epic.id, epic.status);
        if (epic.status === "done") doneOnLoad.push(epic.id);
        for (const feat of epic.feats) {
          seen.set(feat.id, feat.status);
          if (feat.status === "done") doneOnLoad.push(feat.id);
        }
      }
      // Done-on-load default applies only the FIRST time this version is ever seen (key absent). Once the key
      // exists the persisted set is authoritative — a re-collapse here would clobber a manual expand on remount.
      if (doneOnLoad.length === 0 || collapsedKeyExists(vId)) return;
      setCollapsed((prev) => {
        const next = new Set(prev);
        for (const id of doneOnLoad) next.add(id);
        writeCollapsed(vId, next);
        return next;
      });
      return;
    }

    // Subsequent passes: a node observed transitioning to done DURING this session folds away (manual wins, req 5 —
    // once `seen` records 'done' a later manual EXPAND is never re-collapsed).
    const toCollapse: string[] = [];
    for (const epic of plan.plan) {
      if (epic.status === "done" && seen.get(epic.id) !== "done") toCollapse.push(epic.id);
      seen.set(epic.id, epic.status);
      for (const feat of epic.feats) {
        if (feat.status === "done" && seen.get(feat.id) !== "done") toCollapse.push(feat.id);
        seen.set(feat.id, feat.status);
      }
    }
    if (toCollapse.length === 0) return;
    setCollapsed((prev) => {
      let changed = false;
      const next = new Set(prev);
      for (const id of toCollapse) {
        if (!next.has(id)) {
          next.add(id);
          changed = true;
        }
      }
      if (!changed) return prev;
      writeCollapsed(vId, next);
      return next;
    });
  }, [plan]);

  // Reveal / hide a node's L2 technical detail (title click). Persisted under the `expanded` key.
  const toggleTechnical = useCallback(
    (id: string) => {
      if (!versionId) return;
      setExpanded((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        writeExpanded(versionId, next);
        return next;
      });
    },
    [versionId],
  );

  // Collapse / expand a node's CHILDREN (chevron click). A manual toggle always wins and is remembered (req 5),
  // persisted under the `collapsed` key.
  const toggleCollapse = useCallback(
    (id: string) => {
      if (!versionId) return;
      setCollapsed((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        writeCollapsed(versionId, next);
        return next;
      });
    },
    [versionId],
  );

  // Honest-by-construction triggers: each button exists ONLY when the backend offers that action right now.
  // The three are MUTUALLY EXCLUSIVE by construction (the BE gate offers at most one), and the ladder below
  // renders them as an if/else chain so at most one is ever on screen regardless.
  //   zostav_plan   → "Zostaviť plán"      (STEP 3: build the task plan from the approved Špecifikácia)
  //   spustit_stavbu→ "Spustiť stavbu"     (STEP 4: start the conversation build loop from the approved plan)
  //   pokracovat    → "Pokračovať v stavbe" (STEP 4: resume a paused / token-stopped build loop)
  const canBuildPlan = !!board?.available_actions?.includes("zostav_plan");
  const canProgram = !!board?.available_actions?.includes("spustit_stavbu");
  const canResume = !!board?.available_actions?.includes("pokracovat");
  // A running Programovanie loop offers `pause` (BE determine_available_actions → {"pause"}); the rung below
  // fires the same postPipelineActionApi(action:'pause'). Mutually exclusive with `pokracovat` by construction.
  const canPause = !!board?.available_actions?.includes("pause");
  // STEP 5 (Kontrola): a FINISHED Programovanie build offers `skontrolovat` — the partner honestly re-checks its
  // own robotu against the approved Špecifikácia (boot + acceptance run, stays priprava, never a verdict/deploy).
  // Last rung of the mutually-exclusive ladder (the BE offers it only once the build is complete).
  const canCheck = !!board?.available_actions?.includes("skontrolovat");
  // STEP 6 (Hotovo): once the Kontrola has run, the BE offers `hotovo` — the Manažér's TERMINAL sign-off that
  // makes the version deployable (SHA-bound signature, stays priprava, never a verdict). LAST rung of the
  // mutually-exclusive ladder (appended AFTER canCheck), so it only shows once the check has completed.
  const canFinish = !!board?.available_actions?.includes("hotovo");
  // Honest, derived from the live status: a token-stopped build reads `paused` — the amber note reflects it.
  const isPaused = board?.state?.status === "paused";

  // One handler for all three trigger buttons — reuses the shared triggering/triggerError state + the EXISTING
  // postPipelineActionApi client, then swaps in the fresh board the action returns (onBoard from usePipelineWs).
  async function runTrigger(action: PipelineActionName, failMsg: string) {
    if (!versionId) return;
    setTriggerError(null);
    setTriggering(true);
    try {
      const nextBoard = await postPipelineActionApi(versionId, { action });
      onBoard(nextBoard);
    } catch (err: unknown) {
      setTriggerError(humanizeApiError(err, failMsg));
    } finally {
      setTriggering(false);
    }
  }

  // Build-progress indicator (salvaged from cockpit/TaskPlanPanel, CR-NS-025 Part 2): % of tasks done, live
  // for free off the SAME fetched plan as the tree (the message-growth refetch advances it as tasks finish —
  // no new data/endpoint). done/total are counted over the flattened leaf TASK rows.
  const allTasks: TaskPlanTaskNode[] = (plan?.plan ?? []).flatMap((e) => e.feats.flatMap((f) => f.tasks));
  const doneCount = allTasks.filter((t) => t.status === "done").length;
  const failedCount = allTasks.filter((t) => t.status === "failed").length;
  const totalCount = allTasks.length;
  const donePct = totalCount ? Math.round((doneCount / totalCount) * 100) : 0;
  // Show only when the plan HAS tasks (and no fetch error): hides the no-plan / loading / error states AND the
  // degenerate "epics but zero tasks" case — no confusing "0/0 úloh". totalCount>0 ⇒ a populated tree.
  const showProgress = !error && totalCount > 0;

  // Auto-expand active during build (req 3): the ancestor EPIC/FEAT ids of any IN-PROGRESS task. The active-task
  // signal is the plan's own per-node status (`in_progress`), not board.current_task — current_task carries only
  // {number,title} (no id, number not unique across levels), so the tree's status is the robust, id-bearing source.
  const activeAncestors = new Set<string>();
  for (const epic of plan?.plan ?? []) {
    for (const feat of epic.feats) {
      if (feat.tasks.some((t) => t.status === "in_progress")) {
        activeAncestors.add(epic.id);
        activeAncestors.add(feat.id);
      }
    }
  }
  // Render-time view of the collapse set: an active-task ancestor is force-EXPANDED so the live task stays visible,
  // WITHOUT mutating the saved `collapsed` (the Manažér's choice is remembered; the override lifts when work moves on).
  const effectiveCollapsed = new Set([...collapsed].filter((id) => !activeAncestors.has(id)));

  return (
    <aside
      data-version-id={versionId ?? undefined}
      className="flex h-full min-h-0 flex-col border-l border-[var(--color-border-default)] bg-[var(--color-surface)]"
    >
      <div className="flex flex-shrink-0 items-center justify-between gap-2 border-b border-[var(--color-border-default)] px-4 py-2.5">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">Plán úloh</h2>
        {plan && plan.epic_count > 0 && (
          <span className="text-[10px] text-[var(--color-text-muted)]">
            {plan.epic_count} celkov · {plan.feat_count} funkcií · {plan.task_count} úloh
          </span>
        )}
      </div>

      {/* Trigger ladder — mutually exclusive by construction (one action slot, if/else chain). */}
      {canBuildPlan ? (
        <div className="flex-shrink-0 border-b border-[var(--color-border-default)] px-4 py-3">
          <p className="mb-2 text-xs text-[var(--color-text-muted)]">
            Špecifikácia je schválená — partner z nej zostaví Plán úloh.
          </p>
          <button
            type="button"
            onClick={() => runTrigger("zostav_plan", "Zostavenie plánu zlyhalo")}
            disabled={triggering}
            className="w-full rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {triggering ? "Zostavujem plán…" : "Zostaviť plán"}
          </button>
          <ErrorNote error={triggerError} className="mt-1" />
        </div>
      ) : canProgram ? (
        <div className="flex-shrink-0 border-b border-[var(--color-border-default)] px-4 py-3">
          <p className="mb-2 text-xs text-[var(--color-text-muted)]">Plán úloh je zostavený — spustíme stavbu.</p>
          <button
            type="button"
            onClick={() => runTrigger("spustit_stavbu", "Spustenie stavby zlyhalo")}
            disabled={triggering}
            className="w-full rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {triggering ? "Spúšťam stavbu…" : "Spustiť stavbu"}
          </button>
          <ErrorNote error={triggerError} className="mt-1" />
        </div>
      ) : canResume ? (
        <div className="flex-shrink-0 border-b border-[var(--color-border-default)] px-4 py-3">
          {isPaused && (
            <p className="mb-2 rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-xs text-amber-700 dark:text-amber-300">
              Stavba pozastavená (token-limit) — pokračuj tlačidlom nižšie.
            </p>
          )}
          <button
            type="button"
            onClick={() => runTrigger("pokracovat", "Pokračovanie v stavbe zlyhalo")}
            disabled={triggering}
            className="w-full rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {triggering ? "Pokračujem…" : "Pokračovať v stavbe"}
          </button>
          <ErrorNote error={triggerError} className="mt-1" />
        </div>
      ) : canPause ? (
        <div className="flex-shrink-0 border-b border-[var(--color-border-default)] px-4 py-3">
          <p className="mb-2 text-xs text-[var(--color-text-muted)]">Stavba prebieha — v prípade potreby ju pozastav.</p>
          <button
            type="button"
            onClick={() => runTrigger("pause", "Pozastavenie stavby zlyhalo")}
            disabled={triggering}
            className="w-full rounded-lg border border-amber-500/50 bg-amber-500/10 px-3 py-1.5 text-xs font-medium text-amber-700 transition-colors hover:bg-amber-500/20 disabled:cursor-not-allowed disabled:opacity-50 dark:text-amber-300"
          >
            {triggering ? "Pozastavujem…" : "Pozastaviť"}
          </button>
          <ErrorNote error={triggerError} className="mt-1" />
        </div>
      ) : canCheck ? (
        <div className="flex-shrink-0 border-b border-[var(--color-border-default)] px-4 py-3">
          <p className="mb-2 text-xs text-[var(--color-text-muted)]">
            Programovanie dokončené — partner sám prekontroluje robotu oproti Špecifikácii.
          </p>
          <button
            type="button"
            onClick={() => runTrigger("skontrolovat", "Kontrola zlyhala")}
            disabled={triggering}
            className="w-full rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {triggering ? "Kontrolujem…" : "Skontrolovať"}
          </button>
          <ErrorNote error={triggerError} className="mt-1" />
        </div>
      ) : canFinish ? (
        <div className="flex-shrink-0 border-b border-[var(--color-border-default)] px-4 py-3">
          <p className="mb-2 text-xs text-[var(--color-text-muted)]">
            Kontrola prebehla — keď si spokojný, označ verziu ako hotovú.
          </p>
          <button
            type="button"
            onClick={() => runTrigger("hotovo", "Označenie ako hotové zlyhalo")}
            disabled={triggering}
            className="w-full rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {triggering ? "Označujem…" : "Označiť ako hotové"}
          </button>
          <ErrorNote error={triggerError} className="mt-1" />
        </div>
      ) : board?.state?.status === "done" ? (
        // STEP 6: the version is signed off (terminal 'done') — mode-agnostic (a GUIDED phase-automaton build
        // reaches 'done' too, not only a conversation build). A real "Prejsť na nasadenie" button beats the old
        // greyed sentence with no action: it navigates to Nasadenie (UAT). The left-menu path stays too.
        <div className="flex-shrink-0 border-b border-[var(--color-border-default)] px-4 py-3">
          {/* Honest #6: a `done` version whose verification could NOT be confirmed (repo unreadable / never
              anchored) warns AMBER before the manager deploys — never a silent green "pripravená". */}
          {verificationUnconfirmed(board?.verified_provenance) && (
            <p className="mb-2 rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-xs text-amber-700 dark:text-amber-300">
              Overenie sa nedá potvrdiť — pred nasadením ho over.
            </p>
          )}
          <p className="mb-2 text-xs text-[var(--color-text-muted)]">
            Verzia je hotová a pripravená na nasadenie k zákazníkovi.
          </p>
          <button
            type="button"
            onClick={() => navigate("/uat")}
            className="flex w-full items-center justify-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500"
          >
            <Rocket className="h-3.5 w-3.5" />
            Prejsť na nasadenie
          </button>
        </div>
      ) : null}

      {/* "Práve robím" banner — top of the rail body, populated by the BE only during Programovanie. */}
      {board?.current_task && (
        <CurrentBuildBanner
          path={findCurrentTaskPath(plan, board.current_task)}
          fallback={board.current_task}
          working={board.state?.status === "agent_working"}
        />
      )}

      {/* Build-progress indicator (CR-NS-025 Part 2, salvaged) — overall done/total + % directly above the tree. */}
      {showProgress && (
        <div className="flex-shrink-0 border-b border-[var(--color-border-default)] px-4 py-2.5">
          <div className="mb-1.5 flex items-baseline justify-between gap-2 text-[11px]">
            <span className="text-[var(--color-text-secondary)]">
              {doneCount}/{totalCount} úloh hotových
              {failedCount > 0 && (
                <span className="font-medium text-[var(--color-status-error)]"> · {failedCount} zlyhané</span>
              )}
            </span>
            <span className="font-semibold tabular-nums text-[var(--color-text-primary)]">{donePct} %</span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-[var(--color-surface-hover)]">
            <div
              data-testid="planrail-progress-fill"
              // Always green (CR-NS-028): the fill shows completed progress, and green = done.
              className="h-full rounded-full bg-gradient-to-r from-emerald-500 to-emerald-400 transition-[width] duration-500 ease-out"
              style={{ width: `${donePct}%` }}
            />
          </div>
        </div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2 text-xs">
        {error ? (
          <ErrorNote error={error} className="px-1" />
        ) : !plan ? (
          <p className="flex items-center gap-1.5 px-1 text-[var(--color-text-muted)]">
            <Loader2 className="h-3 w-3 animate-spin" /> Načítavam plán…
          </p>
        ) : plan.plan.length === 0 ? (
          <p className="px-1 text-[11px] text-[var(--color-text-muted)]">
            Plán sa objaví, keď sa dohodneme na špecifikácii.
          </p>
        ) : (
          plan.plan.map((epic: TaskPlanEpicNode) => (
            // Epic has no technical description column — plain_description is its ONLY prose (no L2 toggle). Its
            // chevron collapses/expands the feats beneath it.
            <PlanNode
              key={epic.id}
              nodeId={epic.id}
              className="mb-2"
              number={epic.number}
              title={epic.title}
              status={epic.status}
              plain={epic.plain_description}
              levelColor={EPIC_LEVEL_COLOR}
              bold
              hasChildren={epic.feats.length > 0}
              isCollapsed={effectiveCollapsed.has(epic.id)}
              onToggleCollapse={() => toggleCollapse(epic.id)}
              isExpanded={expanded.has(epic.id)}
              onToggleTechnical={() => toggleTechnical(epic.id)}
            >
              {epic.feats.map((feat: TaskPlanFeatNode) => (
                <PlanNode
                  key={feat.id}
                  nodeId={feat.id}
                  className="ml-3 mt-1.5"
                  number={feat.number}
                  title={feat.title}
                  status={feat.status}
                  plain={feat.plain_description}
                  technical={feat.description}
                  levelColor={FEAT_LEVEL_COLOR}
                  hasChildren={feat.tasks.length > 0}
                  isCollapsed={effectiveCollapsed.has(feat.id)}
                  onToggleCollapse={() => toggleCollapse(feat.id)}
                  isExpanded={expanded.has(feat.id)}
                  onToggleTechnical={() => toggleTechnical(feat.id)}
                >
                  {feat.tasks.map((task: TaskPlanTaskNode) => (
                    // Leaf task — no children (no chevron); keeps only the L2 technical-detail reveal on its title.
                    <PlanNode
                      key={task.id}
                      nodeId={task.id}
                      className="ml-4 mt-1"
                      number={task.number}
                      title={task.title}
                      status={task.status}
                      plain={task.plain_description}
                      technical={task.description}
                      taskType={task.task_type}
                      levelColor={TASK_LEVEL_COLOR}
                      hasChildren={false}
                      isCollapsed={false}
                      onToggleCollapse={() => {}}
                      isExpanded={expanded.has(task.id)}
                      onToggleTechnical={() => toggleTechnical(task.id)}
                    />
                  ))}
                </PlanNode>
              ))}
            </PlanNode>
          ))
        )}
      </div>
    </aside>
  );
}

export default PlanUlohRail;
