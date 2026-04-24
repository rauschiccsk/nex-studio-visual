---
name: tdd
description: Formalized RED-GREEN-REFACTOR test-driven development loop for NEX Studio commits. Invoke when starting a new feature or bug fix that has testable behavior (new endpoint, service function, validation rule, edge case).
---

# TDD — Red / Green / Refactor

NEX Studio-specific TDD protocol. Use **before** writing any new
behavior-carrying code. Skip for pure refactors, config-only edits,
documentation, and cosmetic UI tweaks.

## The three phases

### 🔴 RED — Write the failing test first

1. Locate the correct test module:
   - Backend service → `tests/test_<service>.py` or `tests/services/test_<...>.py`
   - Backend router → `tests/test_<name>_router.py`
   - Frontend component → rare; prefer manual browser verification per CLAUDE.md §4
2. Write **one** test that captures the new expected behavior.
   - Assert the intended result, not the implementation steps.
   - Use the existing fixture + factory helpers; do not invent your own.
3. Run the test and **confirm it fails with a meaningful error**:
   ```bash
   poetry run pytest tests/test_<file>.py::Test<Class>::test_<case> -x
   ```
4. If it passes accidentally → the test is wrong, not the code. Tighten it.

### 🟢 GREEN — Minimum code to make it pass

1. Implement the **smallest** change that makes the red test pass.
2. Do not refactor, do not extend beyond what the test demands.
3. Re-run the failing test → must pass.
4. Run the surrounding suite — confirm no regression:
   ```bash
   poetry run pytest tests/test_<file>.py -q
   ```

### 🧼 REFACTOR — Clean up with the safety net

1. Now improve naming, extract helpers, tighten types, reduce duplication.
2. Each edit → run the test suite. If red, revert and try smaller step.
3. Stop when the code is clean. Do not chase perfection — CLAUDE.md §9
   forbids premature abstraction.

## When NOT to use TDD

- One-line configuration / settings changes
- Moving code without behavior change
- Documentation-only edits
- UI styling tweaks (no assertable behavior)
- Sweeping rename refactors (regex + test suite is the safety net)

## Interaction with CLAUDE.md workflow

TDD does **not** bypass the Default Workflow (§1 — diagnóza → návrh →
čakanie na schválenie → implementácia).

- The **plan** Zoltán schváli is still the starting gate.
- Once approved, TDD runs **inside** the implementation phase as the
  internal discipline of how the code gets written.
- The RED step is not a "request to implement" — it is the first
  artefact of an already-approved task.

## Self-verification alignment

After REFACTOR, continue with CLAUDE.md §4 self-verification (ruff,
full test suite, FE type-check). TDD narrows the feedback loop for
one feature; §4 verifies the whole commit didn't break anything else.

## Commit message hint

When TDD was used, note it briefly in the body:

```
feat(x): bump retry count to 5

Tests: tests/test_retry.py::TestBackoff::test_five_attempts (RED → GREEN)
```

This helps during `ultrareview` and future `git log` archaeology —
reviewers can spot that behavior was test-locked from the start.
