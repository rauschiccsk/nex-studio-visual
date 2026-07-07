# Spec: PlanUlohRail — real subtree collapse + smart auto-collapse

**Author:** Dedo · **Date:** 2026-07-07 · **For:** Implementer · Director observation #3.
**File:** `frontend/src/components/riadiace/PlanUlohRail.tsx` (the v3 "Plán úloh" rail in RiadiaceCentrumPage).

## Current bug
The chevron on an EPIC/FEAT toggles the node's **L2 technical detail** (the `expanded` set), and `{children}` (PlanNode line ~205) is rendered **unconditionally** — so "closing" a node hides only its technical text, NOT its child feats/tasks. Collapsing does nothing to the subtree.

## Required behaviour (Director)
1. **Collapse a FEAT** → ALL its tasks (each task's header + its plain/L1 description + any L2 detail) disappear; only the FEAT's own single header line remains. **Collapse an EPIC** → all its feats + their tasks disappear; only the EPIC header line remains.
2. **Persist** the collapsed state per version (localStorage), survives reload + navigation.
3. **Auto-expand active during build:** if the currently active/in-progress task (from the board/plan status — e.g. an `agent_working`/in-progress node) sits inside a collapsed EPIC/FEAT, that ancestor must be force-expanded so the active task is visible. This overrides the collapsed state (render-time), it does NOT erase the user's saved choice.
4. **Auto-collapse on done:** the MOMENT a FEAT's status becomes `done` (Hotovo) → collapse it. Same for an EPIC when it becomes `done`. Fire once on the transition (compare previous vs new status), not on every render (else the user can never keep a done node open).
5. The user can always manually toggle (chevron) — a manual toggle wins and is remembered.

## Implementation
- Add a `collapsed: Set<string>` state (node ids of collapsed EPIC/FEAT), persisted under a NEW localStorage key `nex_planrail_collapsed_<versionId>` (distinct from the existing `nex_planrail_expanded_*` L2 set — do NOT reuse it; different semantics). Default empty = all expanded (whole plan visible).
- **Separate the two interactions** (Director-approved): the left **chevron** on EPIC/FEAT = collapse/expand CHILDREN (toggles `collapsed`); the **L2 technical detail** keeps its own reveal via the existing `expanded` set but move its trigger off the chevron onto the node's title/text click. Tasks (leaf, no children) keep only the technical-detail reveal.
- Gate children: render an EPIC's feats only when `!collapsed.has(epic.id)`; a FEAT's tasks only when `!collapsed.has(feat.id)`. A collapsed node renders just its header row (number, title, status dot, chevron) — no children, no technical detail.
- **Auto-expand active (req 3):** compute the ancestor ids of the active task and, at render, treat them as expanded regardless of `collapsed` (a derived `effectiveCollapsed = collapsed − activeAncestors`). Don't mutate the saved set.
- **Auto-collapse on done (req 4):** in an effect keyed on the plan, diff each EPIC/FEAT's status against its previous value (keep a ref of the last-seen statuses); on any `* → done` transition add that node id to `collapsed` (and persist). First load: nodes already `done` start collapsed too (Director wants done work out of the way by default).
- Chevron affordance: show the collapse chevron on nodes that HAVE children (epics always, feats with ≥1 task). ChevronDown = expanded, ChevronRight = collapsed.

## Tests (vitest, mirror existing test_PlanUlohRail)
- Collapsing a FEAT hides its tasks (+ their descriptions); only the FEAT row renders. Same for EPIC.
- Collapsed state persists to `nex_planrail_collapsed_<v>` + rehydrates.
- A `done` FEAT/EPIC starts collapsed; a `* → done` transition auto-collapses.
- An active (in-progress) task force-expands its ancestors even when collapsed (saved set unchanged).
- Manual expand of a done node stays expanded (manual wins).

## Out of scope
Backend, the cockpit `TaskPlanPanel` (separate component), styling beyond what's needed. Report the diff + `npm run test` result; STOP + ask if the active-task signal or a status field is ambiguous — do not invent.
