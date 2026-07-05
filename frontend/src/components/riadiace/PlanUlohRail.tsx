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

import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";

import { getTaskPlan } from "../../services/api/versions";
import { postPipelineActionApi } from "../../services/api/pipeline";
import type { PipelineActionName, PipelineBoard, PipelineMessage } from "../../services/api/pipeline";
import type {
  TaskPlanResponse,
  TaskPlanEpicNode,
  TaskPlanFeatNode,
  TaskPlanTaskNode,
} from "../../types/task-plan";
import { TASK_STATUS_LABELS, TASK_STATUS_TONE, TONE_DOT } from "../cockpit/labels";
import { SpecMarkdown } from "../markdown/SpecMarkdown";

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

// L0 trailing status chip — dot colour from the unified palette (CR-NS-028) + Slovak label.
function StatusDot({ status }: { status: string }) {
  return (
    <span className="inline-flex flex-shrink-0 items-center gap-1 text-[10px] text-[var(--color-text-secondary)]">
      <span className={`h-1.5 w-1.5 rounded-full ${TONE_DOT[TASK_STATUS_TONE[status] ?? "neutral"]}`} />
      {TASK_STATUS_LABELS[status] ?? status}
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

// CurrentBuildBanner (STEP 4) — a compact "Práve robím: #N title" banner pinned at the top of the rail body.
// Fed by board.current_task (populated by the BE ONLY during Programovanie); the caller hides this entirely
// when current_task is null. The blue dot pulses while the agent is actively working (status agent_working) —
// derived from the live status, never guessed. This is SEPARATE from the per-node live in_progress dot: this
// is the single "what am I on right now" line for the whole build, not a tree node.
function CurrentBuildBanner({ number, title, working }: { number: number; title: string; working: boolean }) {
  return (
    <div className="flex flex-shrink-0 items-center gap-2 border-b border-[var(--color-border-default)] bg-[var(--color-surface-hover)] px-4 py-2">
      <span className={`h-1.5 w-1.5 flex-shrink-0 rounded-full bg-blue-500 ${working ? "animate-pulse" : ""}`} />
      <span className="min-w-0 truncate text-[11px] text-[var(--color-text-secondary)]">
        <span className="font-medium text-[var(--color-text-primary)]">Práve robím:</span> #{number} {title}
      </span>
    </div>
  );
}

// One node: L0 header (a button ONLY when it has technical detail to reveal) + L1 plain + L2 technical
// (on expand) + nested children. `bold` renders the epic title heavier; `taskType` is the task's tiny tag.
function PlanNode(props: {
  number: number;
  title: string;
  status: string;
  plain: string;
  technical?: string;
  taskType?: string;
  levelColor: string;
  bold?: boolean;
  isExpanded: boolean;
  onToggle: () => void;
  className?: string;
  children?: React.ReactNode;
}) {
  const { number, title, status, plain, technical, taskType, levelColor, bold, isExpanded, onToggle, className, children } =
    props;
  const hasTechnical = !!(technical ?? "").trim();
  const header = (
    <>
      <span className={`flex min-w-0 items-center gap-1.5 ${levelColor}`}>
        {hasTechnical ? (
          isExpanded ? (
            <ChevronDown className="h-3 w-3 flex-shrink-0" />
          ) : (
            <ChevronRight className="h-3 w-3 flex-shrink-0" />
          )
        ) : (
          <span className="inline-block h-3 w-3 flex-shrink-0" />
        )}
        <span className={`truncate ${bold ? "font-medium" : ""}`}>
          {number}. {title}
        </span>
        {taskType && (
          <span className="flex-shrink-0 text-[9px] uppercase text-[var(--color-text-muted)]">{taskType}</span>
        )}
      </span>
      <StatusDot status={status} />
    </>
  );
  return (
    <div className={className}>
      {hasTechnical ? (
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={isExpanded}
          className="flex w-full items-center justify-between gap-2 rounded px-1 py-0.5 text-left hover:bg-[var(--color-surface-hover)]"
        >
          {header}
        </button>
      ) : (
        <div className="flex items-center justify-between gap-2 px-1 py-0.5">{header}</div>
      )}
      <PlainLine text={plain} />
      {hasTechnical && isExpanded && <TechnicalDetail text={technical ?? ""} />}
      {children}
    </div>
  );
}

export function PlanUlohRail({ versionId, messages, board, onBoard }: Props) {
  const [plan, setPlan] = useState<TaskPlanResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The set of nodes whose L2 technical detail is expanded, hydrated from localStorage (per version, per
  // browser) so the choice survives navigation + reload. Default empty ⇒ technical hidden (not the default view).
  const [expanded, setExpanded] = useState<Set<string>>(() => (versionId ? readExpanded(versionId) : new Set()));
  const [triggering, setTriggering] = useState(false);
  const [triggerError, setTriggerError] = useState<string | null>(null);

  useEffect(() => {
    if (versionId) setExpanded(readExpanded(versionId));
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
        if (!cancelled) setError(e instanceof Error ? e.message : "Načítanie plánu zlyhalo");
      });
    return () => {
      cancelled = true;
    };
  }, [versionId, messages.length]);

  const toggle = useCallback(
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
      setTriggerError(err instanceof Error ? err.message : failMsg);
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

  return (
    <aside
      data-version-id={versionId ?? undefined}
      className="flex h-full min-h-0 flex-col border-l border-[var(--color-border-default)] bg-[var(--color-surface)]"
    >
      <div className="flex flex-shrink-0 items-center justify-between gap-2 border-b border-[var(--color-border-default)] px-4 py-2.5">
        <h2 className="text-sm font-semibold text-[var(--color-text-primary)]">Plán úloh</h2>
        {plan && plan.epic_count > 0 && (
          <span className="text-[10px] text-[var(--color-text-muted)]">
            {plan.epic_count} epic · {plan.feat_count} feat · {plan.task_count} úloh
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
            onClick={() => runTrigger("zostav_plan", "Zostavenie plánu zlyhalo.")}
            disabled={triggering}
            className="w-full rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {triggering ? "Zostavujem plán…" : "Zostaviť plán"}
          </button>
          {triggerError && <p className="mt-1 text-xs text-[var(--color-status-error)]">{triggerError}</p>}
        </div>
      ) : canProgram ? (
        <div className="flex-shrink-0 border-b border-[var(--color-border-default)] px-4 py-3">
          <p className="mb-2 text-xs text-[var(--color-text-muted)]">Plán úloh je zostavený — spustíme stavbu.</p>
          <button
            type="button"
            onClick={() => runTrigger("spustit_stavbu", "Spustenie stavby zlyhalo.")}
            disabled={triggering}
            className="w-full rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {triggering ? "Spúšťam stavbu…" : "Spustiť stavbu"}
          </button>
          {triggerError && <p className="mt-1 text-xs text-[var(--color-status-error)]">{triggerError}</p>}
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
            onClick={() => runTrigger("pokracovat", "Pokračovanie v stavbe zlyhalo.")}
            disabled={triggering}
            className="w-full rounded-lg bg-primary-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-primary-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {triggering ? "Pokračujem…" : "Pokračovať v stavbe"}
          </button>
          {triggerError && <p className="mt-1 text-xs text-[var(--color-status-error)]">{triggerError}</p>}
        </div>
      ) : canPause ? (
        <div className="flex-shrink-0 border-b border-[var(--color-border-default)] px-4 py-3">
          <p className="mb-2 text-xs text-[var(--color-text-muted)]">Stavba prebieha — v prípade potreby ju pozastav.</p>
          <button
            type="button"
            onClick={() => runTrigger("pause", "Pozastavenie stavby zlyhalo.")}
            disabled={triggering}
            className="w-full rounded-lg border border-amber-500/50 bg-amber-500/10 px-3 py-1.5 text-xs font-medium text-amber-700 transition-colors hover:bg-amber-500/20 disabled:cursor-not-allowed disabled:opacity-50 dark:text-amber-300"
          >
            {triggering ? "Pozastavujem…" : "Pozastaviť"}
          </button>
          {triggerError && <p className="mt-1 text-xs text-[var(--color-status-error)]">{triggerError}</p>}
        </div>
      ) : null}

      {/* "Práve robím" banner — top of the rail body, populated by the BE only during Programovanie. */}
      {board?.current_task && (
        <CurrentBuildBanner
          number={board.current_task.number}
          title={board.current_task.title}
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
          <p className="px-1 text-[11px] text-[var(--color-status-error)]">{error}</p>
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
            // Epic has no technical description column — plain_description is its ONLY prose (no L2 toggle).
            <PlanNode
              key={epic.id}
              className="mb-2"
              number={epic.number}
              title={epic.title}
              status={epic.status}
              plain={epic.plain_description}
              levelColor={EPIC_LEVEL_COLOR}
              bold
              isExpanded={expanded.has(epic.id)}
              onToggle={() => toggle(epic.id)}
            >
              {epic.feats.map((feat: TaskPlanFeatNode) => (
                <PlanNode
                  key={feat.id}
                  className="ml-3 mt-1.5"
                  number={feat.number}
                  title={feat.title}
                  status={feat.status}
                  plain={feat.plain_description}
                  technical={feat.description}
                  levelColor={FEAT_LEVEL_COLOR}
                  isExpanded={expanded.has(feat.id)}
                  onToggle={() => toggle(feat.id)}
                >
                  {feat.tasks.map((task: TaskPlanTaskNode) => (
                    <PlanNode
                      key={task.id}
                      className="ml-4 mt-1"
                      number={task.number}
                      title={task.title}
                      status={task.status}
                      plain={task.plain_description}
                      technical={task.description}
                      taskType={task.task_type}
                      levelColor={TASK_LEVEL_COLOR}
                      isExpanded={expanded.has(task.id)}
                      onToggle={() => toggle(task.id)}
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
