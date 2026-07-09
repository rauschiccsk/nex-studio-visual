# Cockpit — add the missing `schvalit` approval button (Návrh/plan gate)

Director flagged live 2026-07-09 (regression): the Návrh gate (`current_stage=navrh`,
`status=awaiting_manazer`, after the Auditor review) offers `available_actions={uprav, ask, schvalit}`,
but the cockpit has NO button for `schvalit` — only `SpecApprovalBar` (which handles `approve_spec` at
the Príprava/spec gate). So the manager must type free-text "Schvaľujem" (unreliable → the AI re-asks).
Add the missing approval button. Branch `v2.0.0-dev`. Self-verify: FE build+lint+test (+ full `pytest`
from root if any backend touched — this is FE-only, so pytest just for safety).

## Change — a `SchvalitBar` mirroring `SpecApprovalBar`
New `frontend/src/components/riadiace/SchvalitBar.tsx`, a near-copy of
`frontend/src/components/riadiace/SpecApprovalBar.tsx` (same styling/props/honest-by-construction
pattern), differing only:
- **Gate:** render nothing unless `board?.available_actions?.includes("schvalit")` (NOT approve_spec).
- **Primary action:** "Schváliť plán" → `postPipelineActionApi(versionId, {action:"schvalit", payload: comment ? {comment} : undefined})`.
- **Secondary action (Director asked for it):** "Upraviť" → `postPipelineActionApi(versionId, {action:"uprav", payload: comment ? {comment} : undefined})` — the comment becomes the rework instruction. `uprav` is in the same available_actions set. Style it as the secondary/outline button (like the "Prezrieť Špecifikáciu" secondary), primary = "Schváliť plán".
- **Consequence copy (accurate):** approving advances Návrh → Programovanie (the build). e.g.
  "Schválením potvrdíš návrh a plán; projekt sa posunie do stavby (Programovanie)."
- **Review affordance:** a small "Prezrieť plán / špecifikáciu" secondary that navigates to `/specifikacia`
  (same target SpecApprovalBar uses) — optional but keep it for parity.
- Same error handling, `submitting` state, `lang="sk" spellCheck={false}` on the comment input.

## Mount it
In `frontend/src/pages/RiadiaceCentrumPage.tsx`, mount `<SchvalitBar board={board} versionId={versionId}
onBoard={setBoard} />` right next to `<SpecApprovalBar … />` (~line 153, the same conditional slot above
the plan rail). Both are honest-by-construction (each renders only when its action is offered), so at most
one shows at a time — no conflict.

## Also (small, same theme): audit the other offered actions have a button
Confirm every manager-facing action the backend can OFFER has a cockpit affordance: `approve_spec`
(SpecApprovalBar ✓), `zostav_plan`/`spustit_stavbu`/`pause`/`pokracovat` (PlanUlohRail trigger ladder ✓),
`schvalit` (this fix), `decide` (ChangeRequest/consultation). If any OTHER offered action has no button,
note it in the report (don't necessarily build it now) — `schvalit` is the confirmed-missing one.

## Tests (RED→GREEN)
- SchvalitBar renders the "Schváliť plán" + "Upraviť" buttons when `available_actions` includes
  `schvalit`, and renders NOTHING when it doesn't (honest-by-construction).
- Clicking "Schváliť plán" calls `postPipelineActionApi` with `action:"schvalit"`; "Upraviť" with
  `action:"uprav"`; the comment is threaded into payload; `onBoard` is called with the returned board.
- FE build + lint + test green.
