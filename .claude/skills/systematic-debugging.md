---
name: systematic-debugging
description: Root-cause-first debugging protocol for NEX Studio. Invoke when a test fails, a production behaviour is wrong, a migration or build errors out, or the user reports "it doesn't work". Forces evidence collection before any code change.
---

# Systematic Debugging — Root Cause Before Fix

Forces discipline: **no code change until the bug is understood**.
Ad-hoc "try stuff until it works" debugging is how subtle regressions
land in NEX Studio. This skill replaces it with a four-phase protocol.

## Phase 1 — REPRODUCE

Before any theorising:

1. Capture the **exact** error output (stack trace, HTTP status,
   failing assertion, browser console, backend log line).
   - Do not paraphrase the error. Paste verbatim.
2. Identify the **minimal trigger** — one URL, one test, one keystroke.
3. Confirm it is **deterministic** (fails every time) or document the
   flakiness pattern (fails 2× out of 5, only on Monday, etc.).
4. Snapshot the environment: backend image tag, recent migration
   number, relevant system_settings values, docker logs timestamp.

**Stop here** if you cannot reproduce. A bug you cannot reproduce is
not a bug to fix — it is a bug to instrument.

## Phase 2 — LOCATE

Narrow to the smallest unit that misbehaves:

1. Read the relevant file(s) — no assumptions, per CLAUDE.md §14.
   - Use the stack trace to jump straight to the offending frame.
   - Use `git log -p <file>` to see what changed recently.
2. Bisect if it used to work:
   - `git log --oneline <file>` → find the candidate commits.
   - Check out the commit before → does it still fail? Binary search.
3. Confirm location with a targeted experiment:
   - Add a `logger.info("...")` or `print()` at the suspected line.
   - Re-run the reproducer.
   - Remove the log before committing (unless it is genuinely useful).

## Phase 3 — EXPLAIN (root cause)

**Do not skip this phase.** The temptation to jump to "I know the fix"
is where bad fixes are born.

1. Write out the root cause in one sentence:
   - "The chat auto-save never fired because ``chatBuffer`` was
     captured by a stale closure — see commit `ec9a24d`."
2. Identify the **class** of bug, not just the instance:
   - Stale closure, race condition, null deref, SQL type mismatch,
     missing migration, Docker port binding, timeout tuning, …
3. Ask: **what invariant was violated?** — name it. If the invariant
   doesn't exist in the codebase yet, note that the fix should
   establish it.
4. Ask: **why did no test catch this?** — the missing test is the
   first artefact of the fix (RED in TDD terms).

## Phase 4 — FIX + PREVENT

1. Write the test that proves the bug (RED per :mod:`tdd` skill).
2. Apply the minimal fix that turns it GREEN.
3. Consider the **blast radius** — is this bug present in sibling
   modules (e.g. the stale-closure bug in ProfSpec chat was also
   lurking in UIDesign chat)?
4. Document in the commit body:
   - The reproducer
   - The root cause (one sentence)
   - The invariant being restored or introduced
   - Any known siblings left for follow-up

## Anti-patterns (NEX Studio § reference)

Do **not** do these when debugging:

- **Destructive Overwrite** (CLAUDE.md §9) — rewriting the whole file
  to "clean it up while I'm here". Modify only what the bug requires.
- **Phantom Execution** (CLAUDE.md §9) — claiming a fix works without
  running the reproducer. The fix is proven only by the RED → GREEN
  transition, not by your reasoning.
- **Skip Root Cause** — jumping straight to a workaround because "the
  deadline is tight". Workarounds accumulate into tech debt. If time
  pressure is real, apply the workaround and **create a task** to
  revisit the root cause.

## When the root cause is external

Sometimes the bug is in a third-party library, in the DB, or in a
configuration file. The protocol still applies:

- Reproduce it at the boundary (minimal script that hits the library).
- Locate the failing call in their code if feasible.
- Explain — is this a known issue (check their issue tracker), a
  misconfiguration on our side, a version mismatch?
- Fix: update, pin, patch, or work around with a comment pointing at
  the upstream issue + invariant we restored locally.

## Output contract

At the end of a debugging session you should have:

1. A commit (or series of commits) with a failing-then-passing test
2. A commit message body that states the root cause in one sentence
3. No unexplained `print` / `logger.info` statements left behind
4. A memory note (per auto-memory system) if the bug pattern is
   likely to recur or is non-obvious from the current codebase
