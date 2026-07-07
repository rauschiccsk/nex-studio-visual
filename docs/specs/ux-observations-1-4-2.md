# Spec: 3 UX observation fixes (#1 SK spellcheck, #4 task refs, #2 live-activity height)

**Author:** Dedo · **Date:** 2026-07-07 · **For:** Implementer. Director observations #1, #4, #2. All FRONTEND (`/opt/projects/nex-studio/frontend`).

## #1 — Slovak spellcheck in editors (everywhere)
Root cause: browser spellcheck does NOT inherit `lang` from `<html lang="sk">`; each `<textarea>`/`<input type=text>` needs its OWN `lang="sk"`. Only KnowledgeBasePage + SlovakTextarea have it.

**Fix:** add `lang="sk"` to each user-facing text editor element below (SK+EN both pass; do NOT disable spellcheck). For code fields (slug/repo url) keep/leave `spellCheck={false}` where already present.
- `components/riadiace/ConversationComposer.tsx:76` (textarea)
- `pages/BacklogPage.tsx:272, :337`
- `pages/CredentialsPage.tsx:270, :321`
- `pages/NewProjectPage.tsx:377`
- `pages/NewVersionPage.tsx:261`
- `pages/ProjectDetailPage.tsx:568`
- `pages/VersionDetailPage.tsx:185`
- `pages/CustomersPage.tsx:216, :226`
- `components/riadiace/SpecApprovalBar.tsx:66` (input type=text)

(Also sweep for any other user-facing free-text `<textarea>`/`<input type="text">` you find and give them `lang="sk"` — but NOT code/number/url fields.)

## #4 — Task references show the full hierarchy
Root cause: the `CurrentBuildBanner` in `components/riadiace/PlanUlohRail.tsx:165-174` renders `board.current_task` = `{number, title}` only → "Práve robím: #5 AP tabuľky" gives no EPIC/FEAT context. The full hierarchy IS in the fetched `plan` tree (`plan.plan[] → feats[] → tasks[]`).

**Fix:** add a helper in PlanUlohRail that, given `plan` + `board.current_task`, locates the task node (match by number within the tree) and collects its ancestor EPIC + FEAT (number + title). Render the banner as the full path, e.g. **`Práve robím: E1 Základ › F2 Schéma › T5: AP tabuľky`** (keep it compact + readable; use the epic/feat/task numbers + titles). If the task can't be located in the tree, fall back to the current `#{number} {title}`. Keep it O(n) traversal; matching by number is safe (honest-by-construction numbering).

## #2 — "ŽIVÁ AKTIVITA AGENTA" inconsistent height
Root cause: `components/cockpit/PipelineActivityFeed.tsx:28` has a hardcoded `max-h-32` (128px). Small content → short; grows to 128px then scrolls → LOOKS like it randomly truncates. It's the same fixed cap at different content sizes.

**Fix:** make it a **consistent, predictable** panel: change `max-h-32` to a taller fixed height (`max-h-64`, 256px) keeping `overflow-y-auto` so it's always the same size and scrolls when full. Add a short comment that this is a deliberate consistent fixed height (not content-adaptive). (Do NOT remove the cap — it must not squeeze the message thread above.)

## Tests + verify
- Add/adjust vitest where it's cheap (esp. #4 — a test that the banner renders the epic›feat›task path when the current task is in the plan, and falls back to `#N` when not).
- Run the frontend suite (`npm run test`) + `tsc`/`vite build`. Report the per-file diff + test result. STOP + ask only if genuinely ambiguous.
