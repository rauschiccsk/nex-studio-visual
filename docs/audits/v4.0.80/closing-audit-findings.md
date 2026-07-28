# Záverečný audit v4.0.79 — 53 potvrdených nálezov


## ZÁVAŽNÉ (10)

### [1] The per-role agent permission profile is written to `.claude/agents/<role>/settings.json`, a path the `claude` CLI never loads — the deny rules (git push, git reset --hard, rewriting its own charter) are inert on every build turn.

- **kde:** `backend/services/create_project_postscaffold.py:146`  · trieda: kontrakt-volajuceho
- **škoda:** Every project founded by the cockpit gets ai-agent/auditor `settings.json` files whose `permissions.deny` forbids `Bash(git push:*)`, `Bash(git push --force:*)`, `Bash(git reset --hard:*)`, `Bash(git rm:*)` and `Write/Edit(<PROJECT_ROOT>/.claude/agents/**)` and `Edit(<PROJECT_ROOT>/CLAUDE.md)`. Nothing reads those files: build_claude_argv passes no `--settings`, the CLI's own `--setting-sources` accepts only `user, project, local` (i.e. ~/.claude/settings.json, .claude/settings.json, .claude/settings.local.json), and the 2.1.220 binary contains no `agents/*/settings.json` path literal. What actually governs a dispatched turn is the mounted user config: /home/andros/.claude/settings.json has `permissions.defaultMode = "bypassPermissions"`, and the prod backend mounts that dir rw with CLAUDE_CONFIG_DIR=/home/andros/.claude. So the AI Agent runs with everything auto-approved: it can force-push, hard-reset the workspace, delete tracked files and rewrite its own charter — precisely the acts the profile claims to forbid, and the profile the template says the Manager confirms ("Finálny profil potvrdzuje Manažér"). The backend also asserts the opposite in code comments.
- **dôkaz:** create_project_postscaffold.py:146 — (role_dir / "settings.json").write_text(settings_tpl.read_text(...).replace("<PROJECT_ROOT>", project_root_str))
  where role_dir = <project>/.claude/agents/{ai-agent,auditor}  (line 139)
claude_agent.py:348 — "Unset (default) → today's full-auto build profile, byte-identical (no tool flags — the project settings.json governs)."
claude_agent.py:292-313 (build_claude_argv) — argv is [claude, -p, --output-format, …, --session-id/--resume, --model, --effort, --json-schema, (consult-only tool flags), prompt]; no --settings, no --setting-sources.
`claude --help` (2.1.220): "--setting-sources <sources>  Comma-separated list of setting sources to load (user, project, local)."
`strings` on the 2.1.220 binary: only ".claude/settings.json", "~/.claude/settings.json", "managed-settings.json" — no agents/<role>/settings.json.
/home/andros/.claude/settings.json → 
- **oprava:** Make the profile reach the receiver: either write the resolved role profile to the path the CLI does load for the dispatched turn (a per-turn file passed as `--settings <file>` in build_claude_argv, chosen by role), or drop the file and express the profile as `--allowedTools`/`--disallowedTools` on build turns the same way the consult path already does. Whichever is chosen, the claude_agent.py:348 comment claiming "the project settings.json governs" must stop being written until it is true, and the user-level `defaultMode: bypassPermissions` must not be the effective policy for build turns.

### [2] The release oracle's risk floor is opt-in: `flagship_features`/`safety_properties` are optional schema fields, so an empty declaration silently reduces the Verifikácia release gate to "the app booted".

- **kde:** `backend/services/pipeline_status.py:203`  · trieda: brana-co-nebezi
- **škoda:** For a NEW project founded through the cockpit: the AI Agent's Návrh skeleton pass omits (or empties) `flagship_features` — which Pydantic accepts without error, without retry, without a log line. `_declared_release_coverage` then returns (0,0), and `_evaluate_release_coverage` checks only `total > 0`. The `release_smoke_test.sh` the cockpit seeds into that project (create_project_postscaffold.py:488, verbatim copy2) ships exactly 3 floor assertions — boot, `alembic upgrade head`, release-notes shape — and ZERO feature/negative assertions (templates/release_smoke_test.sh:119-134 are comment blocks only). Result: the Auditor's Verifikácia settles PASS with the detail string `release acceptance PASS — 3 assertions (0 feature / 0 negative; declared 0 feature / 0 safety)`. The Manager sees a green release for an app that has demonstrated none of the features it was built for and refused none of the operations it must refuse — the precise outcome orchestrator.py:5041 declares must never happen ("proving the app BOOTS is not proving it does what the spec promises nor that it refuses what the spec forbids"). Nothing anywhere else compensates: `_release_coverage_brief` (orchestrator.py:5385) returns an empty string when nothing is declared, so the Auditor's adversarial brief silently drops the negative-test mandate too. The engine's directive at orchestrator.py:2016 states the field is `≥1` — that requirement is enforced by no code.
- **dôkaz:** pipeline_status.py:203  `flagship_features: list[str] = Field(default_factory=list)`
pipeline_status.py:206  `safety_properties: list[SafetyProperty] = Field(default_factory=list)`
pipeline_status.py:361-362 — same two fields, same optional shape, on the Návrh gate_report model.
Contrast, same file: `epics: list[...] = Field(min_length=1)` (:199), `feats: list[...] = Field(min_length=1)` (:182), `tasks: list[TaskPlanTask] = Field(min_length=1)` (:213) — the plan STRUCTURE is enforced, the release COVERAGE declaration is not.
orchestrator.py:5372-5373  `n_features = len(features) if isinstance(features, list) else 0` → (0, 0).
orchestrator.py:4990-4991  `if not total:  # None (no sentinel) or 0 — the anti-empty floor.` — the only remaining check.
Codified in the suite: backend/tests/test_acceptance_smoke.py:596-598 — `# no declaration + ≥1 assertion → PASS (backward compatible)` / `ok, de
- **oprava:** Put `Field(min_length=1)` on `flagship_features` in both `TaskPlanSkeleton` (pipeline_status.py:203) and the gate_report model (:361) so an undeclared release coverage is a parse failure the existing parse-retry surfaces, exactly as `epics`/`feats`/`tasks` already are. Additionally, in `_evaluate_release_coverage` (orchestrator.py:4980), FAIL when `coverage_req == (0, 0)` and the stack is a web app (`stack.roles["backend"] is not None`, already known at orchestrator.py:5049) — the (0,0) backward-compatibility path should apply only to the pure lib/worker case that already SKIPs at :5056.

### [3] Deleting a customer silently cascades away the deploy_events rows that are the sole evidence the project hard-delete PROD guard relies on

- **kde:** `backend/services/customer.py:270`  · trieda: nicive-bez-poistky
- **škoda:** deploy_events.customer_id is ON DELETE CASCADE (migrations/versions/076_v2_deploy_events_audit_log.py:84-89). customer_service.delete has no guard whatsoever against a customer with live UAT/PROD deployments — it deletes the row and lets Postgres erase every deploy and accept event for that customer. project_had_prod_deploy answers purely from those rows. So the sequence DELETE /customers/{id} then DELETE /projects/{id} passes a guard that would otherwise have returned 409 "už bol nasadený do PROD — archivuj": the project row goes, and with it kb_writer.delete_project, the RAG vectors, optionally the GitHub repo, and shutil.rmtree of /opt/projects/<slug> (projects.py:1100). The customer's PROD compose at /opt/customers/<customer>/<project>/docker-compose.yml carries ABSOLUTE build contexts into that workspace (uat_provisioner._abs_build_context, line 950-955), so the running production containers survive but can never again be rebuilt, restarted from build, or migrated — the source is gone. The same cascade also destroys the §3.5 acceptance audit trail (who accepted which version for which customer). The FE confirmation says only "Odstráni sa aj jeho uložený tajný kľúč" — it never mentions deployment history, so the operator has no way to know what he is agreeing to.
- **dôkaz:** backend/services/customer.py:267-271 —
    customer = get_by_id(db, customer_id)
    credential_id = customer.credential_id
    db.delete(customer)
    db.flush()
(no deployment check of any kind)
migrations/versions/076_v2_deploy_events_audit_log.py:84-89 —
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="CASCADE", name="fk_deploy_events_customer_id")
backend/services/deploy.py:266-272 — project_had_prod_deploy selects DeployEvent rows only
backend/api/routes/projects.py:1020-1026 — `if deploy_service.project_had_prod_deploy(db, project_id): raise HTTPException(409, ...)`
frontend/src/pages/CustomersPage.tsx:194 — `window.confirm(\`Odstrániť zákazníka ${c.name}? Odstráni sa aj jeho uložený tajný kľúč.\`)`
- **oprava:** Mirror the project-delete policy in customer_service.delete: refuse (ValueError → 409) when the customer has any deploy_event with status='ok' — a customer with deployment history may only be archived, not deleted. Additionally change deploy_events.customer_id to ON DELETE RESTRICT (or NO ACTION) in a new migration so the audit log can never be cascaded away by any future caller, and extend the FE confirmation to name what is destroyed.

### [4] Charter provisioning overwrites a pre-existing project's root CLAUDE.md and rmtree's its .claude/agents dirs on the SUCCESS path, guarded only by "the directory exists"

- **kde:** `backend/services/create_project_postscaffold.py:161`  · trieda: nicive-bez-poistky
- **škoda:** provision_v2_agent_charters is called from create_project with PROJECTS_ROOT / project.slug (projects.py:823). Its only precondition is that project_root and .claude both exist (line 113-119) — which is precisely TRUE for every live project on this host, and is the condition under which it proceeds rather than skips. In the brownfield founding mode described in the previous finding, registering an existing directory therefore rewrites that project's root CLAUDE.md from the template (destroying any hand-authored project instructions, including uncommitted ones) and shutil.rmtree's .claude/agents/designer, .claude/agents/implementer and .claude/agents/customer. I verified /opt/projects/nex-manager currently holds .claude/agents/designer and .claude/agents/implementer — both would be deleted. Nothing warns the operator, nothing is recorded, and the write happens on the success path, so there is no error to read afterwards. The codebase already knows the correct pattern one file away: project_memory.seed_memory returns None rather than clobber an existing MEMORY.md ("Never clobber the agent's accumulated memory", project_memory.py:131-133).
- **dôkaz:** backend/services/create_project_postscaffold.py:113-119 —
    claude_dir = project_root / ".claude"
    if not project_root.is_dir() or not claude_dir.is_dir():
        logger.info("v2 charter provisioning SKIPPED — no scaffold on disk ...")
        return
backend/services/create_project_postscaffold.py:161-164 —
        (project_root / "CLAUDE.md").write_text(
            universal_tpl.read_text(encoding="utf-8").replace("{{PROJECT_NAME}}", project_name),
            encoding="utf-8",
        )
backend/services/create_project_postscaffold.py:173 —
        shutil.rmtree(claude_dir / "agents" / v1_dir, ignore_errors=True)
backend/services/project_memory.py:131-133 —
    if target.exists():
        # Never clobber the agent's accumulated memory.
        return None
- **oprava:** Give provision_v2_agent_charters the seed_memory posture: take an explicit `overwrite: bool = False` argument, and when the root CLAUDE.md already exists and does not carry the template's own generated-by header, skip the rewrite and the v1-dir rmtree and return a warning the create route surfaces, instead of silently normalising somebody else's project.

### [5] X.1 and X.2 capture `$?` after a pipe into `tee`, so a failed `docker compose build` is recorded as PASS.

- **kde:** `templates/auditor-activity-x-runbook.md:24`  · trieda: falosny-uspech
- **škoda:** `$?` after a pipeline is the exit status of the LAST element — `tee` — which returns 0 whenever it can write its log file. I verified this: `false 2>&1 | tee /tmp/x.log; echo $?` prints 0. So `EXIT` is always 0, the `if [ $EXIT -ne 0 ]` guard never fires, and the runbook prints "PASS X.1" for a build that failed. Identical at line 60 for X.2. The follow-up guards do not rescue it: `docker images | grep -q "<slug>-backend"` and `docker run --rm <slug>-backend test -x /app/.venv/bin/uvicorn` both succeed against a STALE image left under the same tag by a previous successful build — which is precisely the state after a failed rebuild. The Auditor therefore reports "PASS X.1: backend image obsahuje runtime binárky" while the image under audit is not the code under audit, and §21.5 lets the release verdict go PASS on it.
- **dôkaz:** docker compose build backend 2>&1 | tee /tmp/audit-<slug>-x1-backend-build.log
EXIT=$?
if [ $EXIT -ne 0 ]; then
    echo "FAIL X.1: docker compose build backend exit $EXIT"
    echo "Log v /tmp/audit-<slug>-x1-backend-build.log"
    exit 1
fi
echo "PASS X.1"
- **oprava:** Add `set -o pipefail` to both snippets (X.1 line 21, X.2 line 58), or drop the `EXIT=$?` idiom entirely and use `if ! docker compose build backend > log 2>&1; then …`. Additionally, `docker compose build --no-cache --pull` plus an image-ID comparison before/after would close the stale-image hole the charter §21.1 claims X.1 covers ("Docker image build, no cache" — the runbook does not pass `--no-cache`).

### [6] Every post-scaffold step of Create Project can fail while the route returns 201 and the cockpit shows a fully-created project — the failure exists only in the backend container's log.

- **kde:** `backend/services/create_project_postscaffold.py:283`  · trieda: preruseny-na-hranici
- **škoda:** This is the exemplar defect sitting directly on the path the Manager asked about. `run_post_scaffold_steps()` is declared `-> None` and every internal failure terminates in `logger.warning(...); return`. The route calls it at backend/api/routes/projects.py:889, ignores it, commits, and returns `ProjectRead` — a schema with no field for any of this (verified: no warn/cicd/runner/smoke/scaffold field in `ProjectRead`). Concretely: the Manager ticks "Zapnúť CI/CD" on the new-project form. If the token env var is absent, `_provision_ci_runner` logs "SKIPPED" (line 675) and returns; if `docker run` exits non-zero it logs "FAILED" (line 727) and returns. Either way `ci.yml` is still pushed and targets the self-hosted label `andros-ubuntu-<slug>`, for which no runner now exists — so every CI job for that project queues forever. The code comment at line 52 names this as a real past incident ("else every job queues forever (the nex-shopify gap, Director 2026-07-16)"). The same silence covers `_wire_cicd_workflow`, `_wire_precommit_hook`, `_enable_branch_protection`, `_run_smoke_test` and `_commit_and_push_scaffold_finalisation`. The module docstring's own contract — "Manažér môže re-run / wire manually ak treba" — is unsatisfiable, because "ak treba" is precisely the fact that is withheld from him. His only route to the truth is `docker logs` on the backend container. Note this survived today's create-project pass: commit abcb0bc fixed the hollow-project case with the words "Every downstream step has its own silent skip branch, so nothing complained", but left these branches silent.
- **dôkaz:** backend/services/create_project_postscaffold.py:283-287 —
    except Exception as exc:  # noqa: BLE001 — best-effort by contract: never abort the create
        logger.warning(
            "Post-scaffold best-effort step failed (slug=%s) — project still created, finish manually: %s",
            slug,
            exc,
        )

and the per-step swallows, e.g. line 727:
    if run.returncode != 0:
        logger.warning("CI runner provisioning FAILED (slug=%s): %s", slug, run.stderr.strip())
        return

The caller, backend/api/routes/projects.py:889, discards nothing because nothing is returned:
        run_post_scaffold_steps(
            target=project.source_path or "",
            slug=project.slug,
            ...
        )
- **oprava:** Give the step runner a return value and a channel to the screen, exactly as `DeployOutcome.warnings` did for deploy. Have `run_post_scaffold_steps` accumulate a `list[str]` of Slovak notes (one per failed/skipped step, naming the step and the remedy) and return it; add `scaffold_warnings: list[str] = Field(default_factory=list)` to `ProjectRead`; populate it in the create route; render it on the post-create screen the same way `DeployMatrixPage.tsx:536` renders `result.warnings`. Keep the never-abort contract — the point is that partial success must be *visible*, not that it must fail.

### [7] The Stage-4 rollback now deletes the whole project workspace, destroying pre-existing files NEX Studio never created — one line after the function whose contract is to keep them

- **kde:** `backend/api/routes/projects.py:863`  · trieda: regresia
- **škoda:** Founding a project whose slug matches a directory that already exists under /opt/projects (the brownfield/adopt path that abcb0bc deliberately preserved: "The opt-out survives for what it is actually for: adopting a workspace that already exists on disk") now destroys that directory on any Stage-4 push failure. Concretely: operator creates project slug X; /opt/projects/X already holds a checkout with .git; the UI auto-fills repo_url from github_org+slug, so `stage4_should_run` is true; `git push -u origin main` is rejected (remote already has commits → non-fast-forward, or the local branch is not `main` → "src refspec main does not match any" — both routine on an adopted repo); `rollback_partial_state` removes .git, then `_discard_orphaned_workspace` `shutil.rmtree`s the entire directory. The operator's source tree is gone, irreversibly, and the 500 message tells him it was intentional ("Local workspace removed so the same project name can be created again"). `_workspace_safe_to_remove` only proves the path is under PROJECTS_ROOT — it has no notion of whether we created it, which is exactly the provenance test 509f4d4 built for `uat_provisioner` (`GENERATED_BY_MARKER` / `assert_writable_instance_dir`) and did not apply here.
- **dôkaz:** backend/api/routes/projects.py:855-871 —
            except GitPushVerificationError as exc:
                # K-002: clean up local .git so re-run is idempotent
                rollback_partial_state(...)
                db.rollback()
                _discard_orphaned_workspace(project.source_path, project.slug)

backend/services/template_bootstrap.py (rollback_partial_state docstring) — "Removes the local ``.git`` directory (project files stay so the next create-project re-run can resume idempotently)."

backend/api/routes/projects.py:329 — the only guard: `if not source_path or not _workspace_safe_to_remove(source_path, PROJECTS_ROOT): return`
backend/api/routes/projects.py:278-287 — `_workspace_safe_to_remove` checks only `ws != r and ws.is_relative_to(r) and ws.is_dir()`.

backend/services/template_bootstrap.py (invoke_init_script) — the brownfield branch this collides with: `brownf
- **oprava:** Only remove a workspace this create actually scaffolded. Capture whether the target directory existed (and was non-empty) before Stage 3 — `invoke_init_script` already computes exactly that as `brownfield` — thread it out on `BootstrapResult`, and make `_discard_orphaned_workspace` a no-op when it is true. Keep `rollback_partial_state`'s documented behaviour (drop .git, keep files) for the adopted case and say so in the 500 detail instead of claiming the workspace was removed.

### [8] The orphaned-workspace cleanup was added at two of the four post-scaffold failure exits; the Stage-3 exit — the one most likely to leave a half-scaffolded tree — still leaks a permanently dead slug

- **kde:** `backend/api/routes/projects.py:801`  · trieda: regresia
- **škoda:** init.sh runs under `set -euo pipefail` and writes CLAUDE.md at line 250, then keeps going through skills/docs copies, `git init`, `git config core.hooksPath .githooks` and `git commit` (which fires the freshly-copied project pre-commit hook) at line 518. Any failure after line 250 — a timeout, a git identity problem, a failing hook — exits non-zero, `invoke_init_script` raises `TemplateBootstrapError`, and this handler rolls the DB row back and returns 500 while leaving /opt/projects/<slug> on disk holding CLAUDE.md. That is precisely the state abcb0bc identified as fatal: no DB row for DELETE /projects/{id} to act on, and a retry cannot succeed because init.sh:143 refuses a target that already holds CLAUDE.md without --force, which the cockpit never passes. The slug is dead forever and no cockpit action can clear it. The timeout branch already admits this in its own message ("partial state may exist at {source_path}, manual cleanup required") — manual cleanup means a terminal, which is the failure mode the whole audit was chartered against. The same gap exists on the outer `except ValueError` (line 907) and `except OSError` (line 910), both reachable after a successful scaffold: `project_memory.seed_memory` calls `target.write_text` with no OSError handling of its own.
- **dôkaz:** backend/api/routes/projects.py:799-806 —
        try:
            invoke_init_script(db, project)
        except TemplateBootstrapError as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Filesystem bootstrap failed: {exc}",
            ) from exc
(no `_discard_orphaned_workspace` call — contrast lines 826 and 863, where abcb0bc added it)

backend/api/routes/projects.py:907-915 — `except ValueError: db.rollback()` / `except OSError: db.rollback()`, neither cleans up.

/home/icc/knowledge/templates/claude-project/init.sh:22 `set -euo pipefail`; :143-144 `if [[ "$DRY_RUN" != "1" && -f "$TARGET/CLAUDE.md" && "$FORCE" != "1" ]]; then err "$TARGET/CLAUDE.md already exists (use --force to overwrite)"`; :250 `copy_file "CLAUDE.md.tmpl" "CLAUDE.md" yes`; :518 `git add …` / `git commit`.

ba
- **oprava:** Wrap the whole post-`project_service.create` body so every rollback path runs the same cleanup, rather than bolting the call onto individual `raise` sites: on any exception after Stage 3, call `_discard_orphaned_workspace` once (subject to the provenance guard from the finding above), then re-raise. Add a red-green test that makes init.sh fail after it has written CLAUDE.md and asserts the second create for the same slug succeeds.

### [9] A failed template bootstrap leaves the scaffolded directory behind, permanently blocking that project name from ever being created in the cockpit again

- **kde:** `/opt/projects/nex-studio-visual/backend/api/routes/projects.py:801`  · trieda: pripravenost
- **škoda:** Any failure inside init.sh AFTER it has written CLAUDE.md — the nex-shared lock guard (init.sh:471-485 `exit 1`), a git commit failure, a full disk, or the 60 s subprocess timeout — rolls the DB row back but leaves /opt/projects/<slug>/ on disk with CLAUDE.md in it. The Manager retries: Stage 1 sees the GitHub repo already exists (treated as success), Stage 3 runs init.sh again, and init.sh refuses because CLAUDE.md is present and the cockpit never passes --force. The slug is now dead forever from the cockpit — DELETE /projects/{id} cannot help (the row was rolled back, so there is nothing to delete), and the only recovery is a shell on ANDROS. The Manager sees the same failure on every retry with nothing telling him a leftover directory is the cause. The two sibling failure paths (charter provisioning, projects.py:826; Stage-4 push, projects.py:863) BOTH call _discard_orphaned_workspace for exactly this reason; the bootstrap path — the one most likely to fail, since it is the step that does all the filesystem work — does not.
- **dôkaz:** projects.py:799-806:
        try:
            invoke_init_script(db, project)
        except TemplateBootstrapError as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Filesystem bootstrap failed: {exc}",
            ) from exc

No _discard_orphaned_workspace call — compare projects.py:824-830 which has one. Its own docstring (projects.py:315-325) states the harm verbatim: "Without this, one half-way failure kills that project name FOREVER … a retry cannot succeed either, because init.sh refuses a target that already holds a CLAUDE.md unless --force, which we never pass."

init.sh:143-145 is the refusal:
    if [[ "$DRY_RUN" != "1" && -f "$TARGET/CLAUDE.md" && "$FORCE" != "1" ]]; then
        err "$TARGET/CLAUDE.md already exists (use --force to overwrite)"

Reproduced: I interrupte
- **oprava:** Call `_discard_orphaned_workspace(project.source_path, project.slug)` in the `except TemplateBootstrapError` block at projects.py:801, immediately after `db.rollback()` and before raising — identical to the charter-provisioning and Stage-4 handlers. Then the 500 message can honestly say the workspace was removed and the same name can be used again.

### [10] The new-project screen discards every actionable error detail and replaces it with "try again in a moment", which is false advice for every failure the create can actually produce

- **kde:** `/opt/projects/nex-studio-visual/frontend/src/pages/NewProjectPage.tsx:237`  · trieda: pripravenost
- **škoda:** `humanizeApiError(...).message` alone is stored; the `.detail` field carrying the backend's real text is thrown away, and the banner renders a bare string rather than <ErrorNote>. So for HTTP 500 the Manager reads exactly "Projekt sa nepodarilo vytvoriť — chyba na strane servera — skús to o chvíľu znova." and nothing else. That sentence is wrong for every 500 this endpoint raises: the greenfield refusal, a bad slug, a bootstrap crash, a failed push — none of them clear on their own, so the Manager retries indefinitely. The damage is sharpest on the greenfield refusal, where the backend deliberately authored a full plain-Slovak paragraph naming the cause and both remedies ("Doplň cestu k init.sh, alebo projekt zakladaj len nad už existujúcim priečinkom") — the Manager never sees one word of it. On 409/422 it is equally blind: a port conflict reads "položka sa medzičasom zmenila alebo už existuje" with no indication that a port is the problem, which port, or who holds it, even though _validate_ports built that sentence for him.
- **dôkaz:** NewProjectPage.tsx:234-238:
    } catch (err: unknown) {
      // Audit Theme 2: the raw backend detail … used to surface verbatim. Frame it in plain Slovak; the raw text stays as the detail.
      setFormError(humanizeApiError(err, "Projekt sa nepodarilo vytvoriť").message);

The comment claims "the raw text stays as the detail" — `.message` drops it. `formError` is a plain string rendered at NewProjectPage.tsx:534-538 as `{formError}` in a <div>, never through <ErrorNote>.

apiError.ts:22 — reasonFor(status): `if (status >= 500) return "chyba na strane servera — skús to o chvíľu znova";`
apiError.ts:5-6 promises the opposite: "keeping the raw technical text available separately (for a collapsible 'Technický detail')", and ErrorNote.tsx exists solely to render it.

The text that is being suppressed, template_bootstrap.py:180-185:
    raise TemplateBootstrapError(
        "Automatické za
- **oprava:** Change `formError` to `HumanError | null`, store the whole `humanizeApiError(err, "Projekt sa nepodarilo vytvoriť")` object, and render it with `<ErrorNote error={formError} className="rounded-lg bg-[var(--color-state-error-bg)] …" />` exactly as NewVersionPage.tsx:473 does. Additionally, for a 500 whose detail begins with "Filesystem bootstrap failed:", suppress the "skús to o chvíľu znova" clause — retrying is guaranteed not to help.


## STREDNÉ (28)

### [11] Branch protection is sent to `gh api` with `-f` (string) where GitHub requires JSON null/integer, so the PUT is rejected 422 every time and the failure is only logged.

- **kde:** `backend/services/create_project_postscaffold.py:832`  · trieda: kontrakt-volajuceho
- **škoda:** Reachable from the New Project form (frontend/src/pages/NewProjectPage.tsx:516 checkbox → backend/api/routes/projects.py:897 → _enable_branch_protection). Whenever the Manager ticks "branch protection", the PUT body carries the strings "null"/"null"/"1" where GitHub's Update-branch-protection endpoint requires `required_status_checks: object|null`, `restrictions: object|null` and `required_approving_review_count: integer` → 422, no protection applied. The whole post-scaffold block is best-effort (line 283 `except Exception` → logger.warning) and the create still returns 201, so the cockpit reports a founded project while `main` is left unprotected — force-push and direct-push to main stay open on every new repo, and the only trace is one backend WARNING nobody reads. github_validation.py:213 documents the opposite ("branch protection is applied post-push").
- **dôkaz:** args = ["gh", "api", "--method", "PUT", api_path,
  "-f", "required_status_checks=null",
  "-F", "enforce_admins=false",
  "-f", "required_pull_request_reviews[required_approving_review_count]=1",
  "-f", "restrictions=null", ...]

Proven against the real receiver (gh 2.87.2 on this host, request dumped with --verbose against an unreachable host so nothing was mutated):
  {"allow_force_pushes": false, "enforce_admins": false,
   "required_pull_request_reviews": {"required_approving_review_count": "1"},
   "required_status_checks": "null", "restrictions": "null"}
gh's own help: "-f/--raw-field … add static string parameters"; "-F/--field has magic type conversion … literal values true, false, null, and integer numbers get converted to appropriate JSON types".
- **oprava:** Use `-F` for the three typed fields: `-F required_status_checks=null`, `-F restrictions=null`, `-F required_pull_request_reviews[required_approving_review_count]=1` (keep `-f` only for genuine strings). Additionally, surface a failed protection step to the caller instead of swallowing it in the best-effort warning, since it is the one post-scaffold step with a security consequence.

### [12] port_block_size is a live, editable setting that the port suggester honours but the project-create validator overrides with a hardcoded 10-alignment rule, so any non-multiple-of-10 block size makes the auto-filled new-project form permanently un-submittable.

- **kde:** `backend/api/routes/projects.py:178`  · trieda: vyhlasene-nevynutene
- **škoda:** The Manager edits "Veľkosť bloku portov" in Nastavenia → Systém → Rozsah portov (the setting is rendered by SystemSettingsPanel via the `port_` prefix and is editable by any `ri` user) to e.g. 5 or 15 — a plausible edit, since its own description says only "Štandard: bloky po 10". `GET /projects/ports/suggest-block` then honours the setting and returns base = 10100 + k*block_size (10105, 10115, …). NewProjectPage auto-fills those three fields on mount. On submit, `_validate_ports` rejects with HTTP 422 "backend_port 10105 must be 10-aligned (10100, 10110, 10120, ...) per D-020". The cockpit has handed the Manager a port it then refuses, with an error naming a rule that contradicts the setting he just changed. Every founding attempt via the auto-fill path fails until he either reverts the setting (no message tells him to) or hand-types a 10-aligned port outside his own configured block grid. This is precisely the manager's stated concern: it blocks founding a new project.
- **dôkaz:** backend/api/routes/projects.py:176-187 — `bp = payload.backend_port` / `if bp is not None and bp >= 10100:` / `if bp % 10 != 0:` → 422 "must be 10-aligned (10100, 10110, 10120, ...) per D-020". Nothing here reads `port_block_size`. Contrast backend/services/port_registry.py:783 `block_size = _port_block_size(db)` and :794 `for base in range(range_min, range_max + 1, block_size):` — the suggester DOES honour it, as does backend/api/routes/projects.py:516 `block_size=system_setting_service.get_int(db, "port_block_size")`. Registry declaration: backend/services/system_setting.py:184-192 `"port_block_size": _Default(value="10", ..., label="Veľkosť bloku portov", unit="portov", description="Koľko portov dostane jeden projekt. Štandard: bloky po 10 …")`. Frontend consumes the base at frontend/src/pages/NewProjectPage.tsx:157-159 `setBackendPort(String(block.base)); setFrontendPort(String(block
- **oprava:** Replace the literal 10 in the alignment/layout check with the configured block size: read `system_setting_service.get_int(db, "port_block_size")` in `_validate_ports` and assert `bp % block_size == 0` (and derive the frontend/db offsets from the same value), and do the same in `template_bootstrap._port_base_from_backend` (pass the block size in). Also replace the literal `10100` guard with `system_setting_service.get_int(db, "port_range_min")` so lowering the range does not silently disable the layout check. If D-020 truly mandates a fixed 10 forever, then the honest fix is the opposite: reject a non-multiple-of-10 value in `upsert` validation for `port_block_size` so the dial cannot be turned to a value the create path will refuse.

### [13] SystemSettingUpdate declares value min_length=1, which makes the registry-documented "empty = feature off" state of template_init_script_path and reserved_port_ranges impossible to restore through the only API that exists.

- **kde:** `backend/schemas/system_setting.py:75`  · trieda: vyhlasene-nevynutene
- **škoda:** Two settings document EMPTY as a legitimate, functional mode. `reserved_port_ranges`: "Prázdne = žiadne rezervácie". `template_init_script_path`: "Prázdne = automatické zakladanie je vypnuté". Both ship empty. The moment the Manager types a value once, he can never take it back: `PATCH /api/v1/system-settings/{key}` with `value: ""` is rejected by Pydantic with 422 before it ever reaches `service.upsert`, which itself explicitly accepts empty (`_validate_value_for_type("", "string")` returns immediately at system_setting.py:606-607). Concretely: the Manager mistypes a reserved range (say `10110_10159`), `reserved_ranges_status` logs "Malformed entries … are NOT being enforced" and port_registry.py:672 instructs him in Slovak to fix it in Nastavenia — but he can only replace it, never clear it, so a cockpit that should declare "no external reservations" can never say so again. Same for turning auto-bootstrap back off on a brownfield-only install.
- **dôkaz:** backend/schemas/system_setting.py:75 — `value: str = Field(..., min_length=1)` inside `class SystemSettingUpdate`, which is the payload type at backend/api/routes/system_settings.py:50-53 (`@router.patch("/{key}")` … `payload: SystemSettingUpdate`). The service layer disagrees: backend/services/system_setting.py:604-607 `def _validate_value_for_type(value, value_type): if value_type == "string": return`. The two keys whose defaults are deliberately empty: backend/services/system_setting.py:214-223 (`"template_init_script_path": _Default(value="", … "Prázdne = automatické zakladanie je vypnuté …")`) and :245-256 (`"reserved_port_ranges": _Default(value="", … "Prázdne = žiadne rezervácie …")`). Both are rendered as editable rows: frontend/src/pages/SettingsPage.tsx:225-227 passes every non-dial, non-price key as `rest` to `SystemSettingsPanel`.
- **oprava:** Drop `min_length=1` from `SystemSettingUpdate.value` (backend/schemas/system_setting.py:75). Emptiness is already type-checked per key by `_validate_value_for_type`, and the two keys that must not be empty are not enforced by a length rule anyway. If some key genuinely must reject empty, express that per key in `_Default` (e.g. an `allow_empty: bool`) and check it in `upsert`, rather than with a blanket schema constraint that contradicts the registry's own documented defaults.

### [14] The Settings panel's save handler silently returns on an empty draft: the "Uložiť" button stays enabled, the click fires no request, and no error or confirmation is shown.

- **kde:** `frontend/node_modules/nex-shared/dist/index.js:1239`  · trieda: vyhlasene-nevynutene
- **škoda:** The Manager clears a text setting (e.g. `reserved_port_ranges`, to remove a mistyped range) and clicks "Uložiť". `dirty` is true (`"" !== storedValue`), so the button is enabled and reads "Uložiť". The handler returns before calling `onSave` — no network request, no `saveErrors` entry, no "✓ Uložené" flash. The screen is indistinguishable from a screen that has not been clicked. He will click it repeatedly and conclude the cockpit is frozen, or worse, navigate away believing the value was cleared while the old value is still governing port allocation for the next project he founds. This is a second, independent lock on top of the backend's `min_length=1`: fixing the backend alone would still leave the click dead, and fixing only this one would surface a raw 422.
- **dôkaz:** frontend/node_modules/nex-shared/dist/index.js:1239 (inside `handleSaveSetting`, `SystemSettingsPanel`, from `nex-shared` pinned at `github:rauschiccsk/nex-shared#v0.19.0` per frontend/package.json:24) — `const draft = (drafts[key] ?? "").toString(); if (!draft.trim() && draft !== "0" && draft.toLowerCase() !== "false") return;`. The button that calls it is enabled purely on dirtiness (same file, in the row render): `disabled: saving || !dirty` with `const dirty = draft !== s.value;` and label `saving ? "Ukladám…" : dirty ? "Uložiť" : "Uložené"`. The panel is mounted with every non-dial, non-price key at frontend/src/pages/SettingsPage.tsx:268-275.
- **oprava:** In nex-shared's `SystemSettingsPanel.handleSaveSetting`, drop the empty-draft early return and let the save proceed (the backend is the authority on whether empty is valid); if a guard is wanted, it must set `saveErrors[key]` with a reason instead of returning silently. Then bump the `nex-shared` pin in frontend/package.json. As an interim measure inside this repo, the same fix cannot be applied locally — the panel is a dependency — so the pin bump is the only correct route.

### [15] The new-project form ships an enabled checkbox, "Vývoj na zákazku (povoľuje odchýliť sa od jednotného firemného dizajnu)", whose value is stored, is write-once, and is read by nothing.

- **kde:** `frontend/src/pages/NewProjectPage.tsx:529`  · trieda: vyhlasene-nevynutene
- **škoda:** On the founding screen the Manager is shown a live checkbox whose Slovak label makes a present-tense promise: it "permits deviating from the unified company design". He ticks it for a bespoke customer project. Nothing in the engine ever reads the flag, so the build proceeds under the unified design exactly as if the box were unticked, and no screen ever tells him otherwise. Worse, the field is excluded from ProjectUpdate by design, so he cannot revisit the decision later — he has made an irreversible choice on a control that decides nothing. This lands on the exact screen the manager asked to certify as ready.
- **dôkaz:** frontend/src/pages/NewProjectPage.tsx:522-530 — an enabled `<input type="checkbox" checked={customDevelopment} onChange={…}>` with `<span>Vývoj na zákazku (povoľuje odchýliť sa od jednotného firemného dizajnu)</span>`; sent at :223 `custom_development_enabled: customDevelopment`. Persisted: backend/services/project.py:219 `custom_development_enabled=data.custom_development_enabled` → backend/db/models/projects.py:67 (migration 081). The model itself admits the truth at backend/db/models/projects.py:64-66: "an INERT stored datum in STEP 6 (no behaviour binds to it yet — the deviation gate is a future scope)" — but that admission appears only in Python source, never in the UI. Repo-wide consumers (`grep -rn custom_development_enabled`, excluding generated contract and tests): NewProjectPage.tsx, types/project.ts, models/projects.py, migration 081, services/project.py:219, schemas/project.p
- **oprava:** Until a deviation gate reads the flag, render the checkbox disabled with a tooltip stating that it records the intent but no behaviour applies it yet — the project's own house rule is "vypnuté, nie skryté" (disabled with a reason, never silently absent). Alternatively remove the control from the create form and keep the column as pure stored intent set elsewhere. Do not leave an enabled control on the founding screen whose label promises an effect in the present tense.

### [16] settings.claude_cli_path decides the health endpoint's claude_cli_available verdict but not which binary is executed — the live agent path hardcodes the literal "claude".

- **kde:** `backend/services/claude_agent.py:293`  · trieda: vyhlasene-nevynutene
- **škoda:** `CLAUDE_CLI_PATH` reads as the knob that says where the Claude CLI is, and docker-compose.yml sets it explicitly, reinforcing that impression. An operator pointing it at a non-PATH binary (a pinned version at /opt/claude/bin/claude, a wrapper script) gets `GET /health` reporting `claude_cli_available: true`, because `shutil.which` happily resolves the absolute path — while every actual agent turn execs the literal `"claude"` from PATH. The reverse is equally live: with `claude` absent from PATH but CLAUDE_CLI_PATH pointing at a real binary, the cockpit reports healthy and the first Programovanie turn of a newly founded project dies with a raw FileNotFoundError from `asyncio.create_subprocess_exec`. The health surface exists to be trusted before a build; on this field it reports on a binary the engine never runs.
- **dôkaz:** Declared: backend/config/settings.py:34 `claude_cli_path: str = "claude"`; advertised as a live knob at docker-compose.yml:91 `CLAUDE_CLI_PATH: "claude"`. Read by exactly two places: backend/api/routes/health.py:16-18 `def _check_claude_cli_available() -> bool: return shutil.which(settings.claude_cli_path) is not None` (surfaced at health.py:43 as `"claude_cli_available"`), and backend/services/claude_subprocess.py:89 `cmd = [settings.claude_cli_path, "-p"]` — which is dead: `grep -rn "run_claude_stream|claude_subprocess" backend/ scripts/` finds only the definition itself and a comment at backend/services/system_setting.py:138-141 confirming "`run_claude_stream` (claude_subprocess.py) has no production caller left; the live agent path is `claude_agent.py`". The live path ignores the setting: backend/services/claude_agent.py:293 `args = ["claude", "-p", "--output-format", "stream-json", 
- **oprava:** In `build_claude_argv` (backend/services/claude_agent.py:292-295), replace the two `"claude"` literals with `settings.claude_cli_path`, keeping the sidecar's `--entrypoint` in sync in backend/services/consult_sandbox.py. If instead the binary is meant to be fixed by the image, delete `claude_cli_path` from Settings and the `CLAUDE_CLI_PATH` line from docker-compose.yml, and have health.py check `shutil.which("claude")` directly — the same treatment already applied to backend_port/frontend_port, which are at least labelled DECLARED, NOT HONOURED in settings.py:11-17.

### [17] The pre-commit hook the cockpit installs into every new project runs no ESLint, while the CI workflow it installs alongside makes `npm run lint` a hard gate.

- **kde:** `templates/pre-commit-hook.sh:40`  · trieda: brana-co-nebezi
- **škoda:** Every project founded with "Enable CI/CD" gets both files from the same post-scaffold step (create_project_postscaffold.py:274-276: `_wire_cicd_workflow` then `_wire_precommit_hook`). The hook checks backend ruff and frontend `type-check` and then prints `pre-commit: ✓ Lint checks passed`; the CI Lint job runs `npm run type-check` AND `npm run lint`. So the generated project's AI Agent commits a file with an ESLint error, the hook green-lights it with an explicit "Lint checks passed", the push lands, and the CI Lint job goes red — which is verbatim the failure the hook exists to prevent (create_project_postscaffold.py:526-531: "Blocks locally any commit that the CI Lint stage would reject, so the AI Agent can never push known-red code (the root of the recurring 'CI / lint Failed' on generated projects)"). The cockpit repaired its OWN hook today (.githooks/pre-commit:81-86 now runs eslint with the comment "eslint is a CI gate … so it belongs here too — same command, so a commit that passes the hook passes CI") and left the template it ships to every customer project behind.
- **dôkaz:** templates/pre-commit-hook.sh:40-44 — the entire frontend branch:
```
if [[ "${NEEDS_FRONTEND}" == "1" && -f frontend/package.json ]]; then
    echo "pre-commit: frontend — type-check"
    ( cd frontend && npm run type-check ) \
        || { echo "❌ frontend type-check FAILED"; exit 1; }
fi
```
templates/pre-commit-hook.sh:46  `echo "pre-commit: ✓ Lint checks passed"`
templates/github-actions-workflow.yml:67-72 — the CI Lint job for the same project:
```
      - name: Frontend type-check + lint
        if: ${{ hashFiles('frontend/package.json') != '' }}
        run: |
          cd frontend
          npm run type-check
          npm run lint
```
The generated frontend does have the script: /home/icc/knowledge/templates/claude-project/frontend-skeleton/package.json → `"lint": "eslint ."`.
The fixed local counterpart: .githooks/pre-commit:81-86 runs `npm run lint` and exits 1 on failure.
- **oprava:** Append the eslint block to templates/pre-commit-hook.sh, mirroring .githooks/pre-commit:81-86 — `( cd frontend && npm run lint ) || { echo "❌ ESLint FAILED"; exit 1; }` inside the existing `NEEDS_FRONTEND` branch, so the hook and the CI Lint job run byte-identical commands.

### [18] The frontend skeleton that every new project is founded from pins nex-shared v0.11.0 — a build whose tokens.css names Inter and JetBrains Mono but ships zero @font-face rules and no font files, so today's typography bug is reproduced verbatim in every project the cockpit creates.

- **kde:** `/home/icc/knowledge/templates/claude-project/frontend-skeleton/package.json:16`  · trieda: ziada-nedodava
- **škoda:** The cockpit's live `template_init_script_path` = /home/icc/knowledge/templates/claude-project/init.sh (verified in the prod DB and verified present + executable inside nex-studio-visual-prod-backend-1). init.sh:452 copies frontend-skeleton/. into <project>/frontend/ for every Create Project. That skeleton installs nex-shared v0.11.0, whose dist/tokens.css sets `--font-sans: Inter, …` / `--font-mono: "JetBrains Mono", "Fira Code", …` while containing 0 occurrences of @font-face and 0 occurrences of url(), and whose git tree contains no fonts/ directory and no .woff2 at all. Result: every newly founded app asks the browser for Inter and JetBrains Mono and gets neither — the OS fallback renders instead (DejaVu Sans / Arial on Linux, Segoe UI on Windows, Helvetica on macOS; Consolas/Monaco for code), so the same app looks different on every machine and code blocks lose the intended mono metrics. This is not hypothetical: three already-founded projects sit on this pin — /opt/projects/nex-horizont, /opt/projects/nex-marina, /opt/projects/nex-websites — and nex-websites' installed frontend/node_modules/nex-shared/dist/tokens.css still declares Inter with `grep -c "@font-face" = 0` and no fonts/ dir. The two fixes shipped today (nex-shared v0.18.0 Inter, v0.19.0 JetBrains Mono) reached only nex-studio-visual itself, which pins v0.19.0. A newly founded project starts eight minor versions behind and unstyled; the cockpit's own nex-shared upgrade nudge (/api/v1/projects/{id}/nexshared-status) can only flag it after the fact, and only if someone opens the project and acts on it.
- **dôkaz:** frontend-skeleton/package.json:16 →  "nex-shared": "github:rauschiccsk/nex-shared#v0.11.0",
frontend-skeleton/package-lock.json:5481-5483 → "node_modules/nex-shared": { "version": "0.11.0", "resolved": "git+ssh://git@github.com/rauschiccsk/nex-shared.git#ecac15be5ddfae3575bd33b96ff914b4e0cac55c" }
`git show v0.11.0:src/tokens.css` → line 35: `--font-sans: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;` line 36: `--font-mono: "JetBrains Mono", "Fira Code", Consolas, Monaco, monospace;`
`git show v0.11.0:dist/tokens.css | grep -c "@font-face"` → 0 ; `| grep -c "url("` → 0 ; `git ls-tree -r --name-only v0.11.0 | grep -c "woff\|fonts/"` → 0
init.sh:452 → `cp -r "${FRONTEND_SKELETON}/." "${TARGET}/frontend/"`
frontend-skeleton/src/index.css:23 → `font-family: Inter, system-ui, -apple-system, sans-serif;` (a second, independent naming of Inter, hardcoded rather th
- **oprava:** Bump the skeleton to the font-bearing release in BOTH files — package.json:16 to `github:rauschiccsk/nex-shared#v0.19.0` and package-lock.json:5481-5483 to version 0.19.0 at that tag's commit (b06c3a92e9c091a967ec5be47041b8a198069cd8, the SHA nex-studio-visual's own lock resolves). Both must move together: init.sh already contains a guard at ~line 465 that fails the scaffold loudly when the package.json tag and the lockfile tag disagree, because `npm ci` obeys the lock and would silently install the wrong build. While there, change frontend-skeleton/src/index.css:23 to `font-family: var(--font-sans);` so the body follows the token instead of restating the family, and re-check whether the `:root:not(.dark) { --color-text-muted: #64748b; }` override at index.css:8-10 is still needed — v0.19.0 ships that exact value at the source and its own comment says apps can drop it. Longer term the real hole is that nothing links the skeleton's pin to the current nex-shared tag: a release of nex-shared should fail or warn if the founding skeleton is left behind, otherwise the next library fix will strand new projects the same way.

### [19] Founding a project whose slug matches an existing on-disk workspace can rmtree that live workspace: the guard proves containment under /opt/projects, never ownership

- **kde:** `backend/api/routes/projects.py:332`  · trieda: nicive-bez-poistky
- **škoda:** Precondition (verified in code, not inferred): project_service.create checks slug uniqueness against the DB ONLY (backend/services/project.py:184-186) — nothing checks the disk — and source_path defaults to /opt/projects/{slug}. When template_init_script_path is empty (the shipped DEFAULT, backend/services/system_setting.py:214, and the state of any fresh or forked cockpit — v3 at :9207 and v4 at :9217 have separate databases, so a project registered in one is absent from the other's DB), invoke_init_script takes the documented BROWNFIELD branch and registers the existing directory without scaffolding (template_bootstrap.py:175-192). The create then proceeds on that live tree. Stage 4 sets origin to https://github.com/<org>/<slug>.git and runs `git push -u origin main` (template_bootstrap.py:404). Against a repo that already has history this fails non-fast-forward — or, even on a successful push, the K-001 verify compares local HEAD to `ls-remote origin HEAD`, which points at the remote's DEFAULT branch, so a repo whose default is not `main` mismatches. Either raises GitPushVerificationError, and the handler then runs rollback_partial_state (`rm -rf <target>/.git`, template_bootstrap.py:485-491 — the entire git history) followed by _discard_orphaned_workspace, which shutil.rmtree's the whole directory. I verified on this host that /opt/projects/nex-manager, nex-payables, nex-shopify, nex-websites all have .git and .claude and would each satisfy the guard exactly. The 500 message tells the Manager "Local workspace removed so the same project name can be created again" — it asserts the workspace was this call's to remove, which in the brownfield case is false.
- **dôkaz:** backend/api/routes/projects.py:287 — `return ws != r and ws.is_relative_to(r) and ws.is_dir()`  (the whole of _workspace_safe_to_remove: existence + containment, nothing about provenance)
backend/api/routes/projects.py:330-332 —
    if not source_path or not _workspace_safe_to_remove(source_path, PROJECTS_ROOT):
        return
    try:
        shutil.rmtree(source_path)
backend/api/routes/projects.py:856-863 —
            except GitPushVerificationError as exc:
                rollback_partial_state(target=project.source_path, repo_full_name=repo_full_name, delete_github_repo=False)
                db.rollback()
                _discard_orphaned_workspace(project.source_path, project.slug)
backend/services/template_bootstrap.py:485-491 — `subprocess.run(["rm", "-rf", str(git_dir)], ...)`
backend/services/project.py:184-186 — `if _get_by_slug(db, data.slug) is not None: raise ValueError(.
- **oprava:** Apply to /opt/projects the same provenance rule uat_provisioner.assert_writable_instance_dir now applies to /opt/uat and /opt/customers. Two parts: (a) in create_project, before any scaffolding, refuse with 409 when the resolved source_path already exists and is non-empty unless the caller explicitly opted into brownfield registration (an explicit flag on ProjectCreate, never a default); (b) make _discard_orphaned_workspace destroy only a workspace this request created — capture `pre_existing = Path(source_path).exists()` before Stage 3 and pass it in, returning early (log only) when true. Same guard must gate rollback_partial_state's `rm -rf .git`.

### [20] The pre-commit hook seeded into every NEW project announces "Lint checks passed" without running eslint, which the CI it claims to mirror does gate on — today's fix to the cockpit's own hook was not propagated to the template.

- **kde:** `templates/pre-commit-hook.sh:46`  · trieda: falosny-uspech
- **škoda:** `_wire_precommit_hook` (backend/services/create_project_postscaffold.py:525-541) copies this file verbatim into `.githooks/pre-commit` of every project created with CI/CD enabled, and its own header states its purpose: "Blocks the commit if the local Lint checks fail, so a build we already know CI will reject never gets pushed (mirrors the CI `lint` stage …)". But the CI template it must mirror runs BOTH `npm run type-check` AND `npm run lint` (templates/github-actions-workflow.yml:71-72), while this hook runs only type-check and then prints "✓ Lint checks passed". A generated project's AI Agent commits an eslint-failing frontend change, the hook green-lights it, the push turns CI red — the exact failure mode the hook was introduced to prevent (v4.0.29, "so generated projects stay CI-green"). Today's commit 3051298 added the eslint block to the cockpit's own `.githooks/pre-commit` (lines 78-86, with the comment "eslint is a CI gate …, so it belongs here too — same command, so a commit that passes the hook passes CI") and left this template, last touched in dcbee8c, behind.
- **dôkaz:** if [[ "${NEEDS_FRONTEND}" == "1" && -f frontend/package.json ]]; then
    echo "pre-commit: frontend — type-check"
    ( cd frontend && npm run type-check ) \
        || { echo "❌ frontend type-check FAILED"; exit 1; }
fi

echo "pre-commit: ✓ Lint checks passed"
- **oprava:** Port the eslint block from .githooks/pre-commit:78-86 into this template's frontend section: `( cd frontend && npm run lint ) || { echo "❌ ESLint FAILED"; exit 1; }`. Because the file is copied verbatim at scaffold time, only new projects pick it up — existing generated projects keep the old hook.

### [21] The cockpit's landing page renders "Žiadne projekty — Vytvor prvý projekt" when the project list request FAILED, because the catch is empty.

- **kde:** `frontend/src/pages/DashboardPage.tsx:73`  · trieda: falosny-uspech
- **škoda:** `DashboardPage` is the index route (App.tsx:51) — the first screen after login. `api` throws `ApiError` on any non-2xx (services/api.ts:80, via nex-shared `createApiClient`), so a 500, a backend restart, or a transient network drop lands in `.catch(() => {})`; `loading` then flips false with `projects` still `[]`, and the page renders the dedicated empty state at lines 99-117: "Žiadne projekty" / "Vytvor prvý projekt a začni pracovať v NEX Studio." A non-expert Manager (the Tibor/Nazar case) is told, as a fact, that the installation contains no projects, and is invited to found a duplicate of one that already exists. The same file's sibling pages get this right — ProjectsPage.tsx:151 and ProjectDetailPage.tsx:201 both `setError("Nepodarilo sa načítať projekty.")`.
- **dôkaz:**     listProjectsApi({ limit: 6, status: "active" })
      .then((res) => { if (!cancelled) setProjects(res.items); })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false); });
- **oprava:** Mirror ProjectsPage.tsx:151 — add a `loadError` state set in the catch via `humanizeApiError(err, "Nepodarilo sa načítať projekty")`, and render that banner instead of the "Žiadne projekty" empty state when it is set. Zero projects and "we could not ask" must not render the same screen.

### [22] The Špecifikácia document viewer claims "Zatiaľ tu nie sú žiadne dokumenty" when the document-list or document-content request failed.

- **kde:** `frontend/src/pages/SpecifikaciaPage.tsx:166`  · trieda: falosny-uspech
- **škoda:** This page is the Manager's only window onto the Waterfall deliverables (the gap recorded as "v3: manažér nevidí dokumenty"). On a failed `listProjectSpecs()` — 500, backend restart, unreadable repo — the catch swallows it, `docs` stays `[]`, and the page renders the empty state at line 365: "Zatiaľ tu nie sú žiadne dokumenty. Vznikajú v Riadiacom centre — v rozhovore s AI Agentom sa dohodnete na zadaní a AI ich priebežne zapisuje", with a button to the Riadiace centrum. The Manager is told the Designer produced nothing and is steered toward re-running a phase whose output is sitting on disk. The comment on the catch calls this "honest", which is the opposite of what it does. Line 187 compounds it: if the LIST loads but the CONTENT fetch fails, `body` stays null and the page renders that same "no documents" text underneath a row of document pills it just drew — a self-contradicting screen.
- **dôkaz:**       .catch(() => {
        /* unreachable / none → honest "nothing agreed yet" empty state */
      })
      .finally(() => {
        if (!cancelled) setDocsLoading(false);
      });
- **oprava:** Add a `docsError` state set in both catches (lines 166 and 187) with `humanizeApiError(err, "Dokumenty sa nepodarilo načítať")`, and branch the render at line 359 on it — show the failure plus a retry, and reserve the "Zatiaľ tu nie sú žiadne dokumenty" copy for a request that actually returned an empty list. The sibling catch at line 207 already shows the right instinct ("never falsely claim Schválená").

### [23] The Create Project full smoke test logs "K-004 full smoke test PASS" after the /health probe failed, or after it was skipped entirely.

- **kde:** `backend/services/create_project_postscaffold.py:469`  · trieda: falosny-uspech
- **škoda:** The `for … else` at lines 453-458 is the health-probe-exhausted branch: after six failed `curl -sf` attempts it logs a WARNING and then falls straight through the `finally` to line 469, which logs "K-004 full smoke test PASS". The same happens when `backend_port` is None (line 434) and the probe never runs at all. So the only outcome that reaches line 469 having actually proven anything is the `break` at line 451 — every other path reaching it proved nothing and still says PASS. `run_post_scaffold_steps` returns None and surfaces nothing to the UI, so this log line is the ONLY record of whether a freshly founded project's stack answers HTTP. Whoever later investigates why the new project does not run greps the backend log, reads PASS, and rules out the stack. Note the build-FAIL (line 408) and up-FAIL (line 427) paths correctly `return` before the PASS line — the health path is the one that leaks.
- **dôkaz:**             else:
                logger.warning(
                    "K-004 full smoke /health endpoint not reachable in 30s (slug=%s, url=%s)",
                    slug,
                    health_url,
                )
    finally:
        # Cleanup — always run docker compose down -v even if up failed
        subprocess.run(
            ["docker", "compose", "down", "-v"], …
        )

    logger.info("K-004 full smoke test PASS (slug=%s)", slug)
- **oprava:** Track the probe outcome (e.g. `healthy = False`, set True at the `break`) and make line 469 conditional: log PASS only when `healthy` is True, log "K-004 full smoke test INCONCLUSIVE — /health neoverené" when the probe was skipped, and keep the existing WARNING as the terminal statement when it was exhausted. Better still, thread the outcome back through `run_post_scaffold_steps` so the create response can tell the Manager the new project's stack was not verified.

### [24] The Settings → Users tab renders an empty user table with no error when GET /users fails.

- **kde:** `frontend/src/pages/SettingsPage.tsx:510`  · trieda: falosny-uspech
- **škoda:** `loadUsers` swallows the failure and leaves `users` at `[]`; `UsersPanel` (from nex-shared) then renders its table over an empty array with no error surface of its own — I read the bundled implementation in node_modules/nex-shared/dist/index.js, which only sets error state inside its own create/edit/delete handlers, never for the list it is handed. An `ri` admin therefore sees a user-management screen reporting that the installation has no users, after a 500 or a backend restart. Concretely: the admin re-creates a colleague's account (which then 409s on the unique username) or concludes accounts were lost. The same file treats its two sibling loads honestly — line 437 sets `settingsLoadError`, line 580 sets `sessionsLoadError`, line 475 sets `agentsLoadError` — so the Users tab is the lone silent one, and its own comment admits it ("matches the old page's silent failure").
- **dôkaz:**   const loadUsers = useCallback(() => {
    if (usersLoaded) return;
    listUsersApi({ limit: 100 })
      .then((res) => {
        setUsers(res.items);
        setUsersLoaded(true);
      })
      .catch(() => {
        /* matches the old page's silent failure → empty table */
      });
  }, [usersLoaded]);
- **oprava:** Add a `usersLoadError` state set in the catch with `humanizeApiError(e, "Nepodarilo sa načítať používateľov")`, and render it in `UsersTab` above the panel exactly as the Sessions and Agents tabs already do.

### [25] The Auditor charter's Activity X sub-activity numbering does not match the runbook it declares canonical, so the hot-fix release rule skips the /health check it claims to cover.

- **kde:** `.claude/agents/auditor/CLAUDE.md:782`  · trieda: falosny-uspech
- **škoda:** §21.1 defines X.3 = "Bootability check", X.4 = "Health endpoint verify (GET /health returns 200)", X.5 = "Functional smoke". The runbook §21.2 names as "single source of truth" numbers them differently: X.3 = Database migrations, X.4 = Full stack up + healthy, X.5 = Health endpoint — and contains no functional-smoke step at all. So an Auditor doing a hot-fix release audit under §21.3 ("minimum X.3 + X.4 (boot + health)") runs migrations plus the vacuous stack-healthy loop and never touches `GET /health`, while the charter's own parenthetical records that boot AND health were verified. Combined with the X.4 defect above, a hot-fix release can be signed off having proven nothing about the running application, and the audit report table will say so in two PASS cells.
- **dôkaz:** - **X.3** — Bootability check (compose up, container reaches healthy state)
- **X.4** — Health endpoint verify (`GET /health` returns 200)
- **X.5** — Functional smoke (1-3 critical user paths run successfully)
…
- **Hot-fix release** — minimum X.3 + X.4 (boot + health) na FE alebo BE
  podľa scope hot-fixu.
- **oprava:** Renumber one side so the two agree — the runbook is the executable artifact, so align §21.1 to it (X.1 backend build, X.2 frontend build, X.3 DB migrations, X.4 full stack healthy, X.5 health endpoint) and restate §21.3's hot-fix minimum as "X.4 + X.5 (boot + health)". Separately, either add the missing functional-smoke step to the runbook or drop the claim that Activity X covers it — today the charter promises a check that has no implementation anywhere.

### [26] Port-registry warnings are written in Slovak explicitly for the Manager and sent by the route, but the frontend's response type omits the field, so the new-project form discards them.

- **kde:** `frontend/src/services/api/projects.ts:103`  · trieda: preruseny-na-hranici
- **škoda:** `ReservedRangesStatus.warnings` (backend/services/port_registry.py:659) carries a docstring that states its purpose outright: "Operator-facing Slovak warnings for the Settings / project forms. Slovak because these strings are rendered to the Manažér verbatim." The backend does its half correctly — `/projects/ports/suggest-block` populates `warnings` at backend/api/routes/projects.py:517 into `PortBlockSuggestResponse.warnings` (backend/schemas/project.py:105). The frontend then severs it: the hand-written `PortBlockSuggestion` interface declares only `base` and `block_size`, so `api.get<PortBlockSuggestion>` types the field out of existence and `NewProjectPage.tsx:156-160` reads only `block.base`. The warning fires on every single call, not rarely: the registry default for `reserved_port_ranges` is the empty string (backend/services/system_setting.py:245-246), so `configured=False` and the first warning is always generated. Its text tells the Manager that the only guards on his three auto-filled ports are the cockpit's own project table and what Docker happens to be publishing *right now* — so a neighbouring service that is temporarily stopped, or one not in Docker at all, is invisible, and he should set the ranges in Nastavenia. He is never shown this. The module's own comments record that this already happened: the nex-websites / nex-manager-frontend double-book on port 10111 that "went unnoticed for twelve days" (port_registry.py:388-391). The second warning is worse — it names malformed entries that are silently NOT being enforced, i.e. a guard the operator believes protects a range that is in fact wide open. The same field is dropped from `/ports/suggest` too (`suggestPortApi`, projects.ts:97-101, types the response as `{ suggested_port: number }`).
- **dôkaz:** frontend/src/services/api/projects.ts:103-115 —
  export interface PortBlockSuggestion {
    base: number;
    block_size: number;
  }
  export function suggestPortBlockApi(): Promise<PortBlockSuggestion> {
    return api.get<PortBlockSuggestion>("/projects/ports/suggest-block");
  }

Backend sends it — backend/api/routes/projects.py:514-518:
    return PortBlockSuggestResponse(
        base=base,
        block_size=system_setting_service.get_int(db, "port_block_size"),
        warnings=port_registry_service.reserved_ranges_status(db).warnings,
    )

Grep confirms no consumer: `grep -rn "warnings" frontend/src --include=*.ts --include=*.tsx | grep -v generated` returns only deploy.ts:49 and DeployMatrixPage.tsx.
- **oprava:** Add `warnings: string[]` to `PortBlockSuggestion` (and give `suggestPortApi` a named return type with the same field), then render them in `NewProjectPage.tsx` beside the three port inputs — the `portsNote` slot already exists at line 167 for exactly this kind of message. The strings are already Slovak and Manager-facing; they need no rewording, only a surface.

### [27] Zamietnutá požiadavka v Zásobníku zmizne z oboch pohľadov a niet spôsobu, ako ju vrátiť

- **kde:** `frontend/src/pages/BacklogPage.tsx:145`  · trieda: slepe-ulicky-ui
- **škoda:** `backlogItems` filtruje na `open|included`, `realizedItems` na `realized`. Položka so stavom `rejected` teda nepatrí ani do jedného z dvoch tabov („Zásobník“ / „História“) a z obrazovky nenávratne zmizne. Tlačidlo „Zamietnuť“ (riadok 439) nemá potvrdenie ani undo, a „Zmazať“ sa ponúka len pre `status === "open"` (riadok 446), takže zamietnutú položku už nejde ani odstrániť. Jedno omylom kliknuté „Zamietnuť“ = požiadavka je v DB, ale pre Manažéra neexistuje. Backend pritom návrat povoľuje — PATCH /api/v1/backlog/{id} berie `status` ako voľné pole (backend/schemas/backlog.py:39), takže dáta sú zachrániteľné len cez API, nie z kokpitu. Že sa zobrazenie zamýšľalo, dokazujú mŕtve kľúče `rejected` v STATUS_LABEL (riadok 43) a STATUS_CLS (riadok 36), ktoré sa nikdy nevykreslia.
- **dôkaz:** riadok 145: `const backlogItems = items.filter((i) => i.status === "open" || i.status === "included");`; riadok 146: `const realizedItems = items.filter((i) => i.status === "realized");`; riadok 21: `type View = "backlog" | "history";` — tretí pohľad neexistuje.
- **oprava:** Pridať tretí tab „Zamietnuté ({n})“ so zoznamom `items.filter(i => i.status === "rejected")` a tlačidlom „Vrátiť do zásobníka“ volajúcim `updateBacklogApi(id, { status: "open" })`; k „Zamietnuť“ doplniť potvrdenie.

### [28] `passlib` is a dead dependency whose removal silently deletes `bcrypt`, the module auth actually imports — deleting it breaks every login

- **kde:** `pyproject.toml:19`  · trieda: mrtvy-kod
- **škoda:** `backend/services/auth.py:18` does `import bcrypt`, but `bcrypt` is NOT a declared dependency anywhere. It reaches the production image only as an optional extra of `passlib`, which is itself never imported by a single line of code. The prod image is built by `poetry export --without dev` → requirements.txt (Dockerfile:137), so the whole auth stack rests on that transitive edge. This is a live trap for exactly the cleanup the Manager just ordered: a dead-dependency sweep sees `passlib` with zero importers, removes it, `bcrypt` vanishes from the lock, and the backend dies at import time on `backend/services/auth.py:18` — no login, no cockpit, no founding a new project. The failure appears at container start, far from the pyproject edit that caused it, and `grep passlib` in the source tree returns nothing to explain why.
- **dôkaz:** pyproject.toml:19 — `passlib = {version = "^1.7", extras = ["bcrypt"]}`
Repo-wide grep for `passlib` outside poetry.lock returns exactly two hits, both in pyproject.toml itself (line 19 declaration, line 60 `"ignore::DeprecationWarning:passlib"`). Zero imports.
backend/services/auth.py:18 — `import bcrypt`
backend/services/auth.py:29-33 — `return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))`
poetry.lock passlib block — `bcrypt = {version = ">=3.1.0", optional = true, markers = "extra == \"bcrypt\""}`; `passlib` is the ONLY package in the lock that depends on bcrypt.
- **oprava:** Declare the real dependency and drop the phantom one: add `bcrypt = "^5.0"` to `[tool.poetry.dependencies]`, remove the `passlib` line (19) and the now-pointless `"ignore::DeprecationWarning:passlib"` filterwarning (60), then `poetry lock` and confirm `bcrypt` survives as a top-level entry.

### [29] UAT first-time runbook still mandates a manual nginx vhost step that the deploy script explicitly no longer performs

- **kde:** `docs/runbooks/UAT_FIRST_TIME_SETUP.md:58`  · trieda: klamlivy-text
- **škoda:** The runbook is the Director's first-time UAT setup procedure. Krok 4 states that `scripts/uat-deploy.py` writes `/etc/nginx/sites-available/uat-<slug>.conf` and that without symlinking + reloading nginx the UAT URL is unreachable. Routing moved to Traefik: uat-deploy.py's own docstring says there is no nginx step, and uat_provisioner.py says the nginx-vhost rendering was dropped. An operator following this runbook waits for a config file that is never written, runs `sudo nginx -t` on a nonexistent vhost, and concludes the deploy failed when it actually succeeded. Krok 6's file list compounds it: `templates/uat/` is claimed to hold three files but contains only one.
- **dôkaz:** docs/runbooks/UAT_FIRST_TIME_SETUP.md:58 — "`scripts/uat-deploy.py` zapíše config do `/etc/nginx/sites-available/uat-<slug>.conf` a printne pokyn"; :66 — "Bez tohto kroku URL `https://uat-<slug>.isnex.eu` nie je accessible."  Contradicted by scripts/uat-deploy.py:10 — "Routing is via Traefik (Phase-1 infra) — there is **no nginx step** any more; the rendered compose carries the Traefik labels + joins ``nex-proxy-net``" and :64 — `"Routing": "Traefik (nex-proxy-net) — no manual nginx step"`; and backend/services/uat_provisioner.py:24 — "The nginx-vhost rendering is dropped (Traefik replaces it)."  Also :102 claims "`templates/uat/` (docker-compose.yml.j2, .env.example, nginx-uat-vhost.conf)" — `ls templates/uat/` returns only `docker-compose.yml.j2`.  Also :81 points at "`poetry install` v `/opt/projects/nex-studio/`" (wrong repo — this is /opt/projects/nex-studio-visual) and :52 assigns 
- **oprava:** Rewrite Krok 4 as a Traefik prerequisite (the `nex-proxy-net` external network must exist; no per-slug host config), correct the `templates/uat/` file list to the single `docker-compose.yml.j2`, fix the repo path in Krok 5 to `/opt/projects/nex-studio-visual/`, and drop the Koordinátor reference in Krok 3.

### [30] README Quick Start prints host ports nothing listens on, and the DB port it names belongs to a different project

- **kde:** `README.md:11`  · trieda: klamlivy-text
- **škoda:** README tells a newcomer that after `docker compose up -d` the backend is at localhost:9176, the frontend at localhost:9177 and the database at 9178. docker-compose.yml publishes 9213/9214/9215 (9176/9177 are container-internal only). 9176 and 9177 get connection-refused. Worse, 9178 IS bound on this host — by `nex-studio-db-1`, the OLD nex-studio project's Postgres. Anyone who follows the README and points psql or a migration at localhost:9178 believing it is nex-studio-visual's database is operating on another project's live data.
- **dôkaz:** README.md:11-12 — "# Backend:  http://localhost:9176 / # Frontend: http://localhost:9177"; :27-29 table rows "Backend | 9176", "Frontend | 9177", "Database | 9178 | PostgreSQL 16 (mapped from 5432)".  Reality — docker-compose.yml:36 `- "9213:9176"`, :132 `- "9214:9177"`, :15 `- "9215:5432"`.  `docker ps` on this host: `nex-studio-visual-db-1  0.0.0.0:9215->5432/tcp` and, separately, `nex-studio-db-1  0.0.0.0:9178->5432/tcp`.
- **oprava:** Correct the Quick Start URLs and the Architecture table to 9213 / 9214 / 9215, and note that 9216-9218 are the deployed prod bindings (nex-studio-visual-prod-*) so the two are not confused.

### [31] Architect setup guide documents a container path and mount that do not exist — its verification step reports failure on a healthy system

- **kde:** `docs/ARCHITECT_SETUP.md:61`  · trieda: klamlivy-text
- **škoda:** The guide states docker-compose.yml already mounts the host Claude config at /root/.claude:ro and that CLAUDE_CONFIG_DIR is /root/.claude. Neither is true: the mount target is /home/andros/.claude (rw) and CLAUDE_CONFIG_DIR is /home/andros/.claude. Its Step 3 verification (`docker compose exec backend ls -la /root/.claude/`) therefore fails on a perfectly working install, and the guide's own remedy for that failure — "restart the backend", then "authenticate first" — loops forever. The 'What gets created' block and the two `chmod 600 .../credentials.json` commands also name a file that does not exist (auth here is the CLAUDE_CODE_OAUTH_TOKEN env var). README.md:46 and :62 repeat the same two false claims.
- **dôkaz:** docs/ARCHITECT_SETUP.md:61 — "- /home/andros/.claude:/root/.claude:ro"; :71 — "docker compose exec backend ls -la /root/.claude/"; :104 — "| `CLAUDE_CONFIG_DIR` | `/root/.claude` |"; :42 — "├── credentials.json    # OAuth tokens (sensitive!)".  Reality — docker-compose.yml:38 `- /home/andros/.claude:/home/andros/.claude` and :90 `CLAUDE_CONFIG_DIR: "/home/andros/.claude"`.  Verified live: `docker exec nex-studio-visual-prod-backend-1 sh -c 'ls -d /root/.claude; echo CFGDIR=$CLAUDE_CONFIG_DIR'` → "ls: cannot access '/root/.claude': No such file or directory" / "CFGDIR=/home/andros/.claude".  `ls -a /home/andros/.claude | grep -i cred` → no match (no credentials file of any name).
- **oprava:** Replace every /root/.claude occurrence with /home/andros/.claude in ARCHITECT_SETUP.md (lines 61, 71, 104, 139, 153) and README.md (46, 62); replace the credentials.json section with the CLAUDE_CODE_OAUTH_TOKEN env-var flow that docker-compose.yml:92 actually uses.

### [32] Create-project checkbox promises automatic deployment after every change; the CI template it enables has no deploy job

- **kde:** `frontend/src/pages/NewProjectPage.tsx:502`  · trieda: klamlivy-text
- **škoda:** The `enable_cicd` checkbox is labelled "Automaticky zostaviť a nasadiť po každej zmene" (automatically build AND DEPLOY after every change). The workflow it seeds is a four-job pipeline — lint, test, build, migrate — with no deployment step of any kind. A Manager founding a project ticks this expecting changes to reach the customer automatically; nothing is ever deployed and nothing reports an error, because deployment is a wholly separate manual Nasadenie/UAT flow (as GettingStartedPage.tsx step 6 correctly describes). The false promise is on the founding screen itself, which is exactly the surface under review.
- **dôkaz:** frontend/src/pages/NewProjectPage.tsx:502 — `<span>Automaticky zostaviť a nasadiť po každej zmene</span>` bound to `enableCicd` (:498-499).  The seeded workflow, templates/github-actions-workflow.yml:3 — "# Default 4-job pipeline: lint + test + build + migrate. Adjust per-project as needed."; its only jobs are `lint:` (:23), `test:` (:74), `build:` (:141), `migrate:` (:158).  `grep -ni 'deploy' templates/github-actions-workflow.yml` returns nothing; the two `docker compose up -d db` hits (:106, :176) are the test/migrate Postgres.
- **oprava:** Relabel to what the flag does — e.g. "Automaticky kontrolovať a zostaviť po každej zmene (testy, zostavenie, migrácie)" — or add the deploy job the label promises. Nasadenie stays manual per the Nasadenie/UAT flow.

### [33] "Vývoj na zákazku" checkbox claims to permit deviating from the unified design; nothing in the system reads the flag

- **kde:** `frontend/src/pages/NewProjectPage.tsx:529`  · trieda: klamlivy-text
- **škoda:** The label states the option "povoľuje odchýliť sa od jednotného firemného dizajnu" — present tense, a capability grant. The value is persisted to `projects.custom_development_enabled` and echoed back in the API, but no code anywhere reads it to change behaviour: the only non-test consumers are the schema, the ORM column, the create-service assignment, the migration copier and the FE type. The developer comment two hundred lines above the label says so outright. A Manager who ticks it at founding believes they have unlocked design freedom for that project; the constraint is unchanged and there is no signal that the choice did nothing — and the flag is create-only, so it cannot be revisited later.
- **dôkaz:** frontend/src/pages/NewProjectPage.tsx:529 — `<span>Vývoj na zákazku (povoľuje odchýliť sa od jednotného firemného dizajnu)</span>`.  Contradicted by the same file's own comment at :81-83 — "STEP 6 (R9): \"Vývoj na zákazku\" — create-only flag, the only switch that later permits deviating from the unified company design. **Inert data in STEP 6 (no behaviour binds to it yet).** Default unchecked."  Full consumer sweep (`grep -rn 'custom_development_enabled|customDevelopment'` over backend + frontend, tests excluded) yields only: backend/schemas/project.py:195,318; backend/db/models/projects.py:67; backend/services/project.py:219 (`custom_development_enabled=data.custom_development_enabled`); backend/services/migration/{transforms.py:98,copier.py:90}; migrations/versions/081_*.py; frontend/src/types/project.ts:66,106 — every one of them storage or transport, none a behavioural branch.
- **oprava:** Per the disabled-not-hidden rule, render the checkbox disabled with a tooltip stating the capability is not yet wired, or reword the label to describe it as a recorded intent ("Označiť projekt ako vývoj na zákazku") until a consumer exists.

### [34] The P0 credentials prohibition in CLAUDE.md names the old instance's store; this instance's store is a different directory

- **kde:** `CLAUDE.md:161`  · trieda: klamlivy-text
- **škoda:** §4 FORBIDDEN #6 is an absolute security rule and it names exactly one path: `/opt/data/nex-studio/credentials/**`. This repo's credential store is `/opt/data/nex-studio-visual/credentials`, mounted rw into the backend and served through the ri-gated /api/v1/credentials route. An agent reading its charter literally — which the charter demands — sees the visual store as unnamed by the prohibition and may Read it directly, bypassing the API governance the rule exists to enforce. The store is empty today, so the breach is latent rather than live, but the rule is wrong the moment the first credential is stored.
- **dôkaz:** CLAUDE.md:160-162 — "6. **NEVER read NEX Studio credentials store priamo** — `/opt/data/nex-studio/credentials/**` je gated cez REST API `/api/v1/credentials` s JWT `ri`".  Actual store — docker-compose.yml:52 `- /opt/data/nex-studio-visual/credentials:/opt/data/nex-studio-visual/credentials:rw`, and backend/constants/paths.py:17 "(``/opt/data/nex-studio-visual/credentials``, the ``nex-studio-visual-*`` image tags);".  Both directories exist on the host; `grep -n 'credentials|/opt/data' .claude/agents/*/settings.json .claude/settings.json` returns nothing, so no deny-glob backstops the stale text.
- **oprava:** Change the path in CLAUDE.md:161 to `/opt/data/nex-studio-visual/credentials/**`, and add a matching absolute deny glob to the three .claude/agents/*/settings.json files so the rule is enforced and not merely written.

### [35] A failed git-status preflight permanently disables "Uložiť Zadanie" on the New Version page with no on-screen way to clear it — and it fails deterministically for any project whose workspace is not a git repo

- **kde:** `frontend/src/pages/NewVersionPage.tsx:140`  · trieda: regresia
- **škoda:** `GET /projects/{id}/git-status` answers 400 whenever `source_path` is set but the directory is not a git repo (`_project_root` raises "Zdrojová cesta nie je git repozitár"). The route has a deliberate escape hatch for a project with NO source_path (returns clean:true) but none for this case. On the frontend that 400 sets `gitError`, so `treeUnverified` is true and the submit button at line 509 is `disabled` — with no `title`, so it is greyed for no stated reason. Meanwhile `gitStatus` stays null, so `isDirty` is false and `DirtyTreeGuard` (rendered only under `isDirty && gitStatus`) never mounts — and its "Uložiť ich" / "Zahodiť" buttons are the ONLY places `setGitError(null)` is ever called. `refreshGitStatus` sets `gitError` on failure and never clears it on success. Result: the Manager cannot found a version for that project at all, from the cockpit, ever. Two fully scaffolded, non-git workspaces of exactly this shape exist on this host right now (/opt/projects/nex-horizont and /opt/projects/nex-marina — CLAUDE.md, .claude, docs, frontend, MEMORY.md, no .git; the shape `rollback_partial_state` leaves behind). The same latch also fires on a merely transient 500, where the only cure is a browser reload that nothing on the screen suggests. The sibling fix in the very same commit (VersionDetailPage's unreadable-Zadanie panel) added a "Skúsiť znova" button for exactly this reason; this screen got the block without the escape.
- **dôkaz:** frontend/src/pages/NewVersionPage.tsx:140 — `const treeUnverified = gitError !== null;`
frontend/src/pages/NewVersionPage.tsx:345-360 — `{!savedVersion && (isDirty || gitError) && ( … {isDirty && gitStatus && (<DirtyTreeGuard … onCommit={handleCommitTree} onDiscard={handleDiscardTree} />)} <ErrorNote error={gitError} … /> )}` — on a failed load only the ErrorNote renders; no control.
frontend/src/pages/NewVersionPage.tsx:507 — `disabled={saving || !project || isDirty || treeUnverified}` with no `title` attribute.
frontend/src/pages/NewVersionPage.tsx (refreshGitStatus) — `setGitStatus(gs); return gs;` on success; `gitError` is never reset there.
backend/services/git_state.py:39-42 — `if not root.is_dir() or not (root / ".git").exists(): raise ValueError(f"Zdrojová cesta nie je git repozitár: {source_path}")`
backend/api/routes/projects.py:609-614 — `if not project.source_path: return {"c
- **oprava:** Two changes. (1) Give the unverified state a control, the way VersionDetailPage does: render a "Skúsiť znova" button next to the ErrorNote whenever `gitError` is set, and have `refreshGitStatus` call `setGitError(null)` on a successful read. (2) Treat "workspace is not a git repo" as its own answer rather than a 400 — return e.g. `{clean: true, not_a_repo: true}` from `get_git_status`, so a project with no git history is founded normally instead of being blocked by a guard that has nothing to guard.

### [36] Moving accept() before the version lookup turned the 4004 close into an unbounded 1-per-second reconnect loop, because onopen resets the backoff counter every cycle

- **kde:** `backend/api/routes/pipeline.py:686`  · trieda: regresia
- **škoda:** b4e4546 moved `await websocket.accept()` above the `version is None` check so a 4003 could reach the browser. The 4004 (version not found) close now also arrives AFTER a successful handshake, which the frontend treats as a real connection. Sequence for a pinned-then-deleted version (the pin lives in activeContextStore, so reopening Riadiace centrum after someone deletes the version is the ordinary path): browser fires `open` → `ws.onopen` runs `attempt = 0` and `everConnectedRef.current = true` → server immediately closes 4004 → `onclose` falls through to `scheduleReconnect` (only 4003 is latched) → `delay = Math.min(1000 * 2 ** 0, 15000)` = 1000 ms → reconnect → `onopen` resets `attempt` to 0 again. The exponential backoff can never grow past its first step: one WebSocket handshake plus one `GET /pipeline/{id}` REST call every second, forever, per open tab, against a version that will never exist. The user sees a permanent amber "reconnecting" banner (`setReconnecting(true)`) and the REST 404's message is wiped on every cycle by `setError(null)` in `scheduleReconnect`. Before this commit the close happened pre-accept, `onopen` never fired, and the backoff correctly grew to its 15 s cap.
- **dôkaz:** backend/api/routes/pipeline.py:684-690 —
        # Authenticated → open the socket, so every refusal below reaches the client AS A CODE.
        await websocket.accept()
        version = db.get(Version, version_id)
        if version is None:
            await websocket.close(code=4004)  # not found
            return

frontend/src/hooks/usePipelineWs.ts:29 — `const _WS_CLOSE_FORBIDDEN = 4003;` (4004 is not handled anywhere)
frontend/src/hooks/usePipelineWs.ts (ws.onopen) — `attempt = 0; // reset backoff after a successful connect` … `everConnectedRef.current = true;`
frontend/src/hooks/usePipelineWs.ts (ws.onclose) — `if (ev?.code === _WS_CLOSE_FORBIDDEN) { denyAccess(); return; } scheduleReconnect();`
frontend/src/hooks/usePipelineWs.ts (scheduleReconnect) — `const delay = Math.min(1000 * 2 ** attempt, 15000); attempt += 1;`
- **oprava:** Latch 4004 the same way 4003 is latched — a version that does not exist will not start existing on retry. Add a permanent-refusal branch for it in `ws.onclose` (with its own message, "Táto verzia už neexistuje", rather than the access-denied sentence), so the socket stops and the screen says what happened. Separately, do not reset `attempt` in `onopen` until the connection has survived long enough to be real (e.g. reset on the first received frame, not on handshake), so any future post-accept close cannot pin the backoff at its first step.

### [37] The cockpit accepts project slugs that init.sh rejects, so the create dies with a raw English regex after the GitHub repo has already been created

- **kde:** `/opt/projects/nex-studio-visual/backend/schemas/project.py:144`  · trieda: pripravenost
- **škoda:** Neither the frontend nor the backend enforces init.sh's slug contract. init.sh requires ^[a-z][a-z0-9-]*[a-z0-9]$ — start with a letter, end alphanumeric, minimum two characters. The frontend's /^[a-z0-9-]+$/ and the schema's min_length=1 both allow a leading digit, a single character, and a trailing hyphen. The realistic case is a leading digit: the Manager types the project name "3D Konfigurátor", nameToSlug produces "3d-konfigurator", the form validates, and the POST proceeds. Stage 1 creates the private GitHub repository, then Stage 3 dies with exit 1 and the create 500s. Result: no project, an orphaned private repo on GitHub that nobody cleans up (projects.py:722 documents this as accepted), and — because of the finding above — the screen says only "chyba na strane servera — skús to o chvíľu znova". Retrying with the same name fails identically, and nothing anywhere tells the Manager that his project name may not begin with a number.
- **dôkaz:** backend/schemas/project.py:144-148:
    slug: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="URL-safe identifier, unique across the system.",
    )
— no `pattern=`. Also no slug validation anywhere in backend/services/project.py create().

frontend/src/pages/NewProjectPage.tsx:191-192:
    else if (!/^[a-z0-9-]+$/.test(slug))
      next.slug = "Identifikátor: iba malé písmená, čísla a pomlčky (napr. nex-ledger).";

init.sh:108:
    [[ "$SLUG" =~ ^[a-z][a-z0-9-]*[a-z0-9]$ ]] || err "--slug must be kebab-case (^[a-z][a-z0-9-]*[a-z0-9]\$)"

Ran init.sh against each shape the cockpit lets through:
  slug=3d-konfigurator → ERROR: --slug must be kebab-case (^[a-z][a-z0-9-]*[a-z0-9]$)
  slug=a               → ERROR: --slug must be kebab-case (^[a-z][a-z0-9-]*[a-z0-9]$)
  slug=nex-ledger-     → ERROR: --slug must be kebab-case (^[a-z][a-z0-9-]*[a-z0-
- **oprava:** Add `pattern=r"^[a-z][a-z0-9-]*[a-z0-9]$"` (and min_length=2) to ProjectCreate.slug in backend/schemas/project.py:144 so the request is refused with a 422 BEFORE the GitHub repo is created, and tighten the frontend regex in NewProjectPage.tsx:191 to the same expression with a Slovak message that says the identifier must begin with a letter and end with a letter or digit.

### [38] Changing the documented "where source code lives" setting silently produces projects with no agent charters, which are reported as created and then fail at the first build

- **kde:** `/opt/projects/nex-studio-visual/backend/api/routes/projects.py:823`  · trieda: pripravenost
- **škoda:** source_path is computed from the editable `default_source_path_template` system setting, but charter provisioning, memory seeding and the agent's own cwd are all hardcoded to PROJECTS_ROOT = /opt/projects. If an ri edits that setting in Nastavenia → Systém — where it is presented as an ordinary, supported option ("Kam sa štandardne uloží zdrojový kód projektu") — init.sh scaffolds correctly at the new location, but provision_v2_agent_charters looks at /opt/projects/<slug>, finds nothing, and RETURNS WITHOUT RAISING, logging an INFO line that misattributes the cause to "dry-run / disabled bootstrap". The create commits and returns 201. The Manager then founds a version, presses "Spustiť tvorbu špecifikácie", and the pipeline blocks on "Agent dispatch failed: Charter (Pravidlá agenta) missing … Re-create the project through NEX Studio v2" — advice that cannot work, since re-creating takes the identical silent path. This is the same hollow-project failure that commit abcb0bc closed for the empty-init-path case, surviving through a second door: the guard checks that a workspace exists, never that the charters landed.
- **dôkaz:** backend/services/project.py:195-197:
    source_tmpl = system_setting_service.get_str(db, "default_source_path_template")
    …
    source_path = data.source_path or source_tmpl.format(slug=data.slug)

backend/api/routes/projects.py:816-823:
        from backend.services.claude_agent import PROJECTS_ROOT
        …
            provision_v2_agent_charters(PROJECTS_ROOT / project.slug, project.slug, project.name)

backend/services/create_project_postscaffold.py:113-119 — the silent exit:
    claude_dir = project_root / ".claude"
    if not project_root.is_dir() or not claude_dir.is_dir():
        logger.info(
            "v2 charter provisioning SKIPPED — no scaffold on disk for slug=%s (dry-run / disabled bootstrap)",
            slug,
        )
        return

backend/services/claude_agent.py:29 — `PROJECTS_ROOT = Path("/opt/projects")` (a literal, not derived from the setting), and claud
- **oprava:** Make the invariant explicit and enforced. In create_project, after invoke_init_script, assert that Path(project.source_path).resolve() == (PROJECTS_ROOT / project.slug).resolve() and raise a 422 in Slovak naming the offending setting if it does not; and change the `return` in provision_v2_agent_charters (create_project_postscaffold.py:118) to raise ProvisioningError whenever the bootstrap was NOT a dry run — a missing scaffold at that point is a real failure, not a legitimate skip.


## KOZMETICKÉ (15)

### [39] guardian_enabled is settable at create and update, is echoed back by ProjectRead, and is read by no code — the cockpit affirms a "Guardian review" regime that does not exist anywhere in the product.

- **kde:** `backend/schemas/project.py:191`  · trieda: vyhlasene-nevynutene
- **škoda:** A caller of `POST /api/v1/projects` or `PATCH /api/v1/projects/{id}` sets `guardian_enabled: true` on the strength of the field's own description, "Whether Guardian review is enabled for this project." The value is persisted to a NOT NULL column, and `GET /api/v1/projects/{id}` returns `guardian_enabled: true` forever after. No review of any kind runs, because no Guardian feature exists: there is no guardian service, no guardian route, and no UI control. For a Manager (or an agent acting on the contract) auditing whether review gating is on for a project, the cockpit answers "yes" to a question it cannot actually act on — a stored value that reads back as a fact about behaviour that is not true.
- **dôkaz:** backend/schemas/project.py:191-194 — `guardian_enabled: bool = Field(default=False, description="Whether Guardian review is enabled for this project.")` (ProjectCreate); :288-291 the same in ProjectUpdate ("Updated Guardian-enabled flag."); :317 in ProjectRead. Persisted at backend/db/models/projects.py:62 `guardian_enabled = Column(Boolean, nullable=False, server_default="false")` and written at backend/services/project.py:218 `guardian_enabled=data.guardian_enabled` / :263 in the updatable-field list. `grep -rl guardian --include=*.py backend/` returns only: schemas/project.py, schemas/user.py (a docstring), schemas/bug.py (a docstring), schemas/pagination.py, db/models/projects.py, services/project.py, services/migration/{transforms,copier}.py, and one test — i.e. only declaration, storage and migration; no consumer. `grep -rn guardian frontend/src --include=*.tsx` (excluding tests) r
- **oprava:** Either delete the field from ProjectCreate/ProjectUpdate/ProjectRead and drop the column in a migration (the honest option, matching how the five inert "Priebeh / AI" settings were removed rather than wired up in backend/services/system_setting.py:127-157), or, if it is a deliberate seam for future scope, rewrite the three descriptions to say so verbatim — "stored only; no behaviour reads this in v4" — the same way backend/db/models/projects.py:64-66 already discloses it for `custom_development_enabled`. Also fix the two dangling `backend.schemas.guardian` cross-references in schemas/user.py:12 and schemas/bug.py:14.

### [40] The Activity X release gate cannot fail on an unhealthy stack — its health loop falls through to exit 0 — and no code path installs the workflow into any project.

- **kde:** `templates/release-gate-workflow.yml:57`  · trieda: brana-co-nebezi
- **škoda:** Two defects in one file. (a) Cannot fail: `Activity X.4 Full stack healthy` loops 30 times and `break`s on success, but there is no `exit 1` on exhaustion — the loop's last executed command is `sleep 5`, which returns 0, so under the default `bash -e` runner shell the step and the job go green for a stack that never became healthy. Worse, the filters `docker ps --filter "health=unhealthy"` / `"health=starting"` count nothing at all when the services declare no HEALTHCHECK, so `UNHEALTHY=0 && STARTING=0` is true on the FIRST iteration and the gate prints "PASS X.4" against a stack that may be crash-looping. Activity X is the gate that is supposed to reject a `v*.*.*` release tag, and per the file header (line 8-9) "Bez Activity X PASS audit verdict NEMÔŽE byť PASS". (b) Invoked by nothing: a repo-wide grep finds exactly one reference to this filename outside itself — .claude/agents/auditor/CLAUDE.md:800, which tells the Auditor the gate exists. `create_project_postscaffold.py` seeds `github-actions-workflow.yml` (:29), `pre-commit-hook.sh` (:32), `release_smoke_test.sh` (:34) and `ci_render_dotenv.py` (:36) and never this one. So the Auditor is pointed at a release gate that runs in no project — and the moment anyone follows the charter and copies it in, they get a gate that green-lights a dead stack.
- **dôkaz:** templates/release-gate-workflow.yml:57-67:
```
      - name: Activity X.4 Full stack healthy
        run: |
          docker compose up -d
          for i in {1..30}; do
            UNHEALTHY=$(docker ps --filter "health=unhealthy" -q | wc -l)
            STARTING=$(docker ps --filter "health=starting" -q | wc -l)
            if [ $UNHEALTHY -eq 0 ] && [ $STARTING -eq 0 ]; then
              echo "PASS X.4"; break
            fi
            sleep 5
          done
```
No `exit 1` follows the loop (the next line, :69, is the `Activity X.5` step).
Contrast the correct idiom the cockpit's own CI now uses — .github/workflows/ci.yml:274-280: `if [ "${status}" != healthy ]; then echo "::error::…deploy FAILED"; … exit 1; fi`.
Seeding: `grep -rn release-gate-workflow.yml` over backend/, scripts/, templates/, .github/ returns only `.claude/agents/auditor/CLAUDE.md:800`.
- **oprava:** After the loop, add `if [ "$UNHEALTHY" -ne 0 ] || [ "$STARTING" -ne 0 ]; then docker compose ps; docker compose logs --tail=100; echo "::error::stack never became healthy"; exit 1; fi`, and gate on the project's own services via `docker compose ps -q` + `docker inspect -f '{{if .State.Health}}…{{else}}none{{end}}'` (the ci.yml:254-282 idiom) so a service with no declared healthcheck is reported rather than counted as passing. Then decide the file's status: either wire it into `_wire_cicd_workflow` as an opt-in seed, or delete it and remove the .claude/agents/auditor/CLAUDE.md:800 pointer — a release gate no project runs should not be advertised to the Auditor as one.

### [41] Frontend Epic types declare a `module_id` field for a column dropped from the database in migration 070.

- **kde:** `frontend/src/types/epic.ts:45`  · trieda: preruseny-na-hranici
- **škoda:** `EpicRead.module_id` is declared non-optional (`string | null`) and documented as "``null`` denotes a project-level epic", but the column no longer exists: the `Epic` model in backend/db/models/tasks.py:19-40 has no `module_id`, none of `EpicCreate`/`EpicUpdate`/`EpicRead` in backend/schemas/epic.py declares it, and backend/services/migration/copier.py:12 states plainly "NEVER reads/writes ``epics.module_id`` (dropped in v2, migration 070)" — migrations/versions/070_drop_multi_module.py confirms. No current component reads it, so nothing is broken today; the defect is that the type asserts something untrue. Any future code writing `epic.module_id` type-checks clean and receives `undefined`, so the documented `=== null` project-level test silently takes the wrong branch. Reported as cosmetic precisely because the harm is latent rather than live.
- **dôkaz:** frontend/src/types/epic.ts:41-45 —
  /** Serialised representation of an epic row. */
  export interface EpicRead {
    id: string;
    project_id: string;
    module_id: string | null;

backend/services/migration/copier.py:12 —
  * NEVER reads/writes ``epics.module_id`` (dropped in v2, migration 070).
- **oprava:** Delete `module_id` from `EpicCreate`, `EpicUpdate` and `EpicRead` in frontend/src/types/epic.ts, along with the two doc comments that describe its semantics (lines 22 and 32). Leave frontend/src/types/kbDocument.ts alone — that `module_id` belongs to a different table and is unaffected.

### [42] Tlačidlo „+ Nový prístup“ na obrazovke Prístupy nerobí pri chybe načítania vôbec nič

- **kde:** `frontend/src/pages/CredentialsPage.tsx:180`  · trieda: slepe-ulicky-ui
- **škoda:** Hlavička s tlačidlom „+ Nový prístup“ sa vykresľuje nepodmienene (riadky 174-187), ale celé telo obrazovky je za `{!accessError && (...)}` (riadok 195). Keď `listCredentials()` zlyhá — 401 po vypršaní tokenu, 403 pre neoprávnenú rolu (backend/api/routes/credentials.py:44 `Depends(require_ri_role)`) alebo obyčajná sieťová chyba pri nedostupnom backende — `handleStartCreate` nastaví `mode="create"`, no formulár sa nikdy nevykreslí. Používateľ klikne a neudeje sa absolútne nič: žiadny formulár, žiadna hláška, žiadna zmena. Navyše `loadList()` beží len raz pri mounte (riadok 62-64), takže po prechodnej sieťovej chybe niet na obrazovke tlačidla „Skúsiť znova“ — jediná cesta von je odnavigovať preč a späť.
- **dôkaz:** riadky 179-186: `<div className="ml-auto"><button onClick={handleStartCreate} …>+ Nový prístup</button></div>` mimo akejkoľvek podmienky; riadok 195: `{!accessError && (` obaľuje ľavý zoznam aj pravý detail/edit/create panel až po riadok 356.
- **oprava:** Buď tlačidlo pri `accessError` deaktivovať s `title` s dôvodom (pravidlo „vypnuté, nie skryté“), alebo ho vykresľovať len keď `!accessError`; a do chybového bloku na riadku 189 pridať tlačidlo „Skúsiť znova“ volajúce `loadList()`.

### [43] Celý slovenský spellchecker je mŕtvy, no build stále kopíruje 3,5 MB slovník do nasadeného frontendu

- **kde:** `frontend/src/components/editor/SlovakTextarea.tsx:61`  · trieda: slepe-ulicky-ui
- **škoda:** `SlovakTextarea` nemá v produkčnom kóde ani jedného importéra (jediný výskyt mimo súboru je komentár v teste). Je zároveň jediným konzumentom `services/spellchecker.ts` aj `components/editor/SpellSuggestionMenu.tsx` — teda celý podsystém (222 + 74 + ~100 riadkov) je nedosiahnuteľný. Všetky reálne `<textarea>` a `<input>` v appke navyše nastavujú `spellCheck={false}` (BacklogPage.tsx:289,359; CredentialsPage.tsx:291,324; SchvalitBar.tsx:108; ConversationComposer.tsx:122), takže kontrola pravopisu nefunguje nikde. Napriek tomu vite.config.ts:19-20 pri každom builde kopíruje `dictionary-sk/index.aff` + `index.dic` (spolu 3,5 MB) do `/dictionaries/sk/` — každý deploy vozí 3,5 MB statiky, ktorú nikdy nikto nestiahne, plus závislosti `dictionary-sk` a `nspell` v package.json.
- **dôkaz:** grep -rn "SlovakTextarea" frontend/src → components/editor/SlovakTextarea.tsx:61 (definícia) a __tests__/components/test_ConversationComposer.test.tsx:39 (komentár). vite.config.ts:19: `{ src: "node_modules/dictionary-sk/index.aff", dest: "dictionaries/sk" }`. `du -sh node_modules/dictionary-sk` → 3.5M.
- **oprava:** Buď nasadiť `SlovakTextarea` na miesta, kde má Manažér písať prózu (ConversationComposer, BacklogPage popis, KnowledgeBasePage editor) a zrušiť tam `spellCheck={false}`, alebo zmazať SlovakTextarea + SpellSuggestionMenu + services/spellchecker.ts, blok viteStaticCopy vo vite.config.ts a závislosti dictionary-sk/nspell.

### [44] TaskPlanPanel je mŕtvy komponent, ktorého 320-riadková testovacia sada stále beží a predstiera pokrytie

- **kde:** `frontend/src/components/cockpit/TaskPlanPanel.tsx:103`  · trieda: slepe-ulicky-ui
- **škoda:** 277-riadkový komponent nemá v produkčnom kóde importéra — nahradil ho `components/riadiace/PlanUlohRail.tsx`, ktorý v hlavičke (riadky 13, 29) priznáva, že z neho funkcionalitu „salvagoval“. Jediný importér je `__tests__/components/test_TaskPlanPanel.test.tsx` (320 riadkov, ~25 assertov). Tá sada je aktívna a zelená, takže CI hlási pokrytie správania (stavy plánu, progress bar, rollup rodičovských stavov, jednotné farby), ktoré na žiadnej obrazovke nikto nevidí. Regresia v PlanUlohRail — jedinom skutočne vykresľovanom paneli — nič z toho nezachytí, no report vyzerá pokrytý.
- **dôkaz:** grep -rn "TaskPlanPanel" frontend/src → mimo vlastného súboru len komentáre (PlanUlohRail.tsx:13,29,515; labels.ts:117; task-plan.ts:7; versions.ts:94) a importy v __tests__/components/test_TaskPlanPanel.test.tsx:14. RiadiaceCentrumPage.tsx:40 importuje PlanUlohRail, nie TaskPlanPanel.
- **oprava:** Zmazať components/cockpit/TaskPlanPanel.tsx aj __tests__/components/test_TaskPlanPanel.test.tsx; ak niektorý z tam testovaných prípadov v test_PlanUlohRail.test.tsx chýba, preniesť ho tam.

### [45] ComingSoonPage je mŕtva stránka a komentár v Sidebare tvrdí, že na ňu smerujú tri živé routy

- **kde:** `frontend/src/pages/ComingSoonPage.tsx:21`  · trieda: slepe-ulicky-ui
- **škoda:** ComingSoonPage nemá importéra — App.tsx ju neimportuje a routy /zakaznici, /uat, /prod už mieria na skutočné stránky (App.tsx:83-85). Komentár v Sidebare (riadky 260-264) však stále tvrdí „ich PAGES land in Milestone G … Until then the routes resolve to a lightweight 'pripravuje sa' placeholder (App.tsx) so they never 404“. Kto sa podľa tohto komentára rozhoduje, hľadá placeholder, ktorý neexistuje, a mylne predpokladá, že Zákazníci/UAT/PROD sú nehotové — pritom sú to plne funkčné obrazovky (CustomersPage, DeployMatrixPage).
- **dôkaz:** grep -rn "ComingSoon" frontend/src → len 4 výskyty, všetky vo vlastnom súbore (riadky 2, 14, 21). App.tsx:9-28 importuje 21 stránok, ComingSoonPage medzi nimi nie je.
- **oprava:** Zmazať frontend/src/pages/ComingSoonPage.tsx a opraviť komentár v Sidebar.tsx:260-264 tak, aby uvádzal skutočný stav (stránky sú hotové).

### [46] The entire `LiveDocumentService` generator trio has no production caller — its docstring justifies its survival by naming a consumer that does not exist

- **kde:** `backend/services/live_documents.py:20`  · trieda: mrtvy-kod
- **škoda:** All three public generators — `generate_history_entry` (:64), `generate_status_md` (:91), `generate_phase_summary_entry` (:197) — are invoked only from `tests/test_live_documents.py`. CR-V2-016 retired the writer half and made `MEMORY.md` the single source of truth, but the generator half was left behind with a docstring that reads as a deliberate, live design decision. A maintainer auditing what renders project status will read lines 19-27, believe a Vývoj tab consumes `generate_status_md`, and preserve ~270 LOC of service, 88 LOC of schema, and a ~540-line test suite that keeps it all green. The test file is what makes it look exercised: CI passes, coverage counts it, and nothing signals that no user-facing path ever reaches this code.
- **dôkaz:** backend/services/live_documents.py:25-27 — "A caller that wants a *rendered view* of the current tree calls ``generate_status_md`` and renders it (e.g. in a Vývoj tab); no file is written"
Repo-wide grep (excluding docs/) for `generate_status_md`: hits only in live_documents.py:20,22,25,91 (docstring + def) and tests/test_live_documents.py:21,289,413,420,429,444,462,472,493,520,534. No route, no service, no frontend call.
Same for `generate_history_entry` / `generate_phase_summary_entry` — every call site is in tests/test_live_documents.py.
The retirement is confirmed live: backend/api/routes/tasks.py:245 and backend/api/routes/feats.py:241 both note "CR-V2-016: the old STATUS.md / HISTORY.md write side-effect ... "; backend/api/dependencies.py:40 — "``STATUS.md`` / ``HISTORY.md`` writers were retired (``MEMORY.md`` is the ...)".
- **oprava:** Delete `backend/services/live_documents.py`, `backend/schemas/live_documents.py` and `tests/test_live_documents.py`. If a rendered status view is genuinely wanted later, it belongs on the `MEMORY.md` path that actually won R-DOUBLEWRITE, not on a resurrected second renderer.

### [47] `backend/services/knowledge_search.py` is a fully dead 189-line module whose docstring claims a live consumer

- **kde:** `backend/services/knowledge_search.py:1`  · trieda: mrtvy-kod
- **škoda:** The module has zero importers anywhere in the repository — not in routes, not in services, not even in tests. Its docstring states it is "Used by the Workflow chat (M4 milestone) to fetch project-scoped knowledge context", which is false. Meanwhile the live KB search path is `backend/rag/reader.py` driven by `backend/api/routes/rag.py`. The harm is a fork in the ground truth: anyone fixing KB search behaviour (scoring, dedup, path-traversal guards, Qdrant filter shape) can land the fix in this file, see tests stay green because nothing covers it, ship it, and observe zero change in the running cockpit. It also duplicates security-relevant logic — `read_document` at :159 carries its own path-traversal check — creating a second, unreviewed copy of a guard that matters.
- **dôkaz:** backend/services/knowledge_search.py:17-19 — "Used by the Workflow chat (M4 milestone) to fetch project-scoped knowledge context."
Repo-wide grep for `knowledge_search` / `knowledge-search` / `knowledgeSearch` / `KnowledgeSearch` returns only: knowledge_search.py:3 (its own docstring), two session-log entries, and one build-plan table row. Zero import statements, zero call sites.
backend/api/routes/rag.py:36-37 imports `from backend.rag import reader` and calls `reader.search(...)` (:97), `reader.get_document(...)` (:134), `reader.list_documents(...)` (:157) — the actual live path.
No dynamic-import machinery exists to reach it: grep for `importlib|__import__|pkgutil|getattr(sys.modules|globals()[` across backend/ returns nothing.
- **oprava:** Delete `backend/services/knowledge_search.py`.

### [48] `claude_subprocess.run_claude_stream` has no production caller and its docstrings point at a settings key that was deleted today

- **kde:** `backend/services/claude_subprocess.py:46`  · trieda: mrtvy-kod
- **škoda:** The repo already knows this module is dead — `backend/services/system_setting.py:138` states `run_claude_stream` "has no production caller left; the live agent path is claude_agent.py". Today's fix acted on that knowledge by deleting the five `Priebeh / AI` settings keys, but left the 193-line module in place. Two of its docstrings now instruct callers to resolve the timeout from `claude_stream_timeout_seconds` — a key that no longer exists and that `backend/tests/test_system_setting_service.py:98` actively forbids from ever coming back. So the module gives working-looking instructions that are guaranteed to fail, and a 300-plus-line test file keeps it green so nothing flags it. A developer wiring a new streaming path will follow those instructions and get a KeyError or a silent default from a registry that will never hold that key.
- **dôkaz:** backend/services/claude_subprocess.py:41 — "``system_settings_service.get_int(db, \"claude_stream_timeout_seconds\")``;"
backend/services/claude_subprocess.py:61 — "``claude_stream_timeout_seconds``); otherwise the"
backend/services/system_setting.py:138-141 — "``claude_stream_timeout_seconds`` — ``run_claude_stream`` (claude_subprocess.py) has no production caller left; the live agent path is ``claude_agent.py``, whose backstop is the env-level ``Settings.claude_invoke_timeout``, not this key."
backend/tests/test_system_setting_service.py:97-104 — `RETIRED_PIPELINE_AI_KEYS = ("claude_stream_timeout_seconds", ...)` regression guard.
Repo-wide grep for `claude_subprocess`: the only non-docs, non-test hit is the system_setting.py:139 comment declaring it dead. backend/services/claude_agent.py (704 lines) is the live path.
- **oprava:** Delete `backend/services/claude_subprocess.py` and `tests/services/test_claude_subprocess.py`.

### [49] `html2canvas` is a production npm dependency with zero importers

- **kde:** `frontend/package.json:22`  · trieda: mrtvy-kod
- **škoda:** Nothing in `frontend/src`, `vite.config.ts` or `index.html` references it, and it is not a peer dependency of `nex-shared` (whose peers are react, react-dom, react-markdown, remark-gfm, tailwindcss, zustand) nor required by any other installed package. It is carried in the lockfile and installed on every CI run and every image build for no reason, and it reads as live: a maintainer seeing a screenshot-capture library in `dependencies` will reasonably assume the cockpit has a screenshot feature wired somewhere and go looking for it.
- **dôkaz:** frontend/package.json:22 — `"html2canvas": "^1.4.1",` under `dependencies`.
Repo-wide grep for `html2canvas` / `html-2-canvas` / `html2Canvas` / `Html2Canvas` (excluding node_modules, .git, package-lock) returns exactly one hit: that declaration line.
`grep -rl html2canvas node_modules/*/package.json` returns only `node_modules/html2canvas/package.json` — no other package depends on it.
nex-shared peerDependencies: {react, react-dom, react-markdown, remark-gfm, tailwindcss, zustand} — html2canvas absent.
- **oprava:** `npm uninstall html2canvas` and commit the updated package.json + package-lock.json.

### [50] Agent charters shipped into every new project cite cockpit-repo paths that do not exist there, and open with an unapproved-draft banner

- **kde:** `templates/agent-shared-base.md:9`  · trieda: klamlivy-text
- **škoda:** This template is concatenated ahead of both role charters and written to `<project>/.claude/agents/{ai-agent,auditor}/CLAUDE.md`, then injected as the system prompt via --append-system-prompt. Its first substantive lines are a ⚠️ FLAG telling the agent its own rules are an unapproved draft pending Manager review, sourced from `docs/architecture/nex-studio-v2-design.md §5.1` — a path that exists only in the cockpit repo. The ai-agent charter repeats the same dangling reference and further attributes the closed enum sets to `backend/db/models/pipeline.py` and `backend/services/pipeline_status.py`, which in a founded project either do not exist or resolve to the project's own unrelated backend files. This is the identical failure mode as the CLAUDE.md §16 anchors fixed today: an agent obeying "read before you think" opens the cited file, finds nothing, and must guess whether the rule still holds.
- **dôkaz:** templates/agent-shared-base.md:8-10 — "> ⚠️ **FLAG — návrh obsahu na revíziu Manažérom (CR-V2-007).** Štruktúra a zámer vychádzajú z `docs/architecture/nex-studio-v2-design.md` §5.1 (Shared base). Presné znenie je návrh — Manažér ho schvaľuje/upravuje."  Shipped verbatim: `sed -n '1,14p' /opt/projects/nex-shopify/.claude/agents/ai-agent/CLAUDE.md` reproduces it byte-for-byte, while `ls /opt/projects/nex-shopify/docs/architecture` → "No such file or directory".  templates/ai-agent-charter.md:10 repeats the same design-doc reference; :220-221 — "presné množiny drží `backend/db/models/pipeline.py` (`STAGE_VALUES`) a `backend/services/pipeline_status.py` (`STAGES` / `BLOCK_KINDS`)" — neither path exists in nex-shopify.  Concatenation site: create_project_postscaffold.py:141-144.
- **oprava:** Strip the CR-V2-007 draft banner from the shipped copy (keep provenance in the cockpit repo's own docs), and requalify the enum provenance as "NEX Studio engine internals" rather than bare project-relative paths — the values are already inlined at ai-agent-charter.md:216-219, so no anchor is needed.

### [51] The OpenAPI-exported board description enumerates a four-phase bar that omits Vizuál — the phase this product exists for

- **kde:** `backend/schemas/pipeline.py:103`  · trieda: klamlivy-text
- **škoda:** This Pydantic docstring is exported into the OpenAPI schema and regenerated verbatim into the FE contract (frontend/src/services/api/pipeline.generated.ts:3986-3988). It tells every consumer of the contract that the Vývoj board renders "Príprava → Návrh → Programovanie → Verifikácia → Hotovo" — five names with Vizuál missing — and calls the model 4-phase. The engine has six stage values including `vizual`, pinned by test_engine_pipeline_has_five_phases_plus_done. Anyone building against the contract (or an agent reading it as ground truth for the stage enum) reproduces a phase strip with no Vizuál, the one gate at which the build stops for the Manager's approval.
- **dôkaz:** backend/schemas/pipeline.py:101 — "Vývoj board snapshot: current 4-phase state + the most recent messages (CR-V2-021)."; :103-104 — "The v2 Vývoj board (design §4.4.2) renders a horizontal 4-phase bar (Príprava → Návrh → Programovanie → Verifikácia → Hotovo)".  Engine truth — backend/tests/test_shipped_docs_match_engine.py:47 `assert STAGE_VALUES == ("priprava", "navrh", "vizual", "programovanie", "verifikacia", "done")`; the FE agrees, frontend/src/components/cockpit/labels.ts:40 `export const PHASE_ORDER: BuildPhase[] = ["priprava", "navrh", "vizual", "programovanie", "verifikacia", "done"];`.  Mirrored into the shipped contract at frontend/src/services/api/pipeline.generated.ts:3988.
- **oprava:** Say five-phase and insert Vizuál in the arrow list in backend/schemas/pipeline.py:101-104 (and the twin docstring at backend/api/routes/pipeline.py:79-86), then regenerate the FE contract. The stale "4-phase" wording in frontend/src/components/cockpit/labels.ts:12 and :29-30 sits directly above six-entry maps and should go with it.

### [52] docker-compose justifies the writable shared-knowledge-base mount with live-document writers that were retired

- **kde:** `docker-compose.yml:39`  · trieda: klamlivy-text
- **škoda:** The comment above the `/home/icc/knowledge:rw` mount states the backend writes per-project STATUS.md / HISTORY.md / ARCHITECT.md live documents. Those DB-driven writers were retired in CR-V2-016 — MEMORY.md is now the single source of truth and the write side-effects were removed from the task/feat/project-create routes. The stated reason for granting the container write access to the shared ICC knowledge base is therefore no longer true; the only surviving writer is the KB-folder cleanup on project delete. Anyone auditing why this broad mount is rw is given a retired justification and cannot judge whether it is still warranted.
- **dôkaz:** docker-compose.yml:39-42 — "# rw — backend writes per-project live documents\n# (STATUS.md / HISTORY.md / ARCHITECT.md) under projects/{slug}/.\n# See docs/architect/live-docs-port.md §3.\n- /home/icc/knowledge:/home/icc/knowledge:rw".  Contradicted by backend/api/dependencies.py:37-40 — "CR-V2-016: the live-document write endpoints that used to depend on this (project create, task / feat completion) are gone — the DB-driven ``STATUS.md`` / ``HISTORY.md`` writers were retired (``MEMORY.md`` is the single source of truth)."; also backend/api/routes/projects.py:773 and routes/tasks.py:245 / routes/feats.py:241.  Remaining KnowledgeBaseWriter call site: routes/projects.py:953, the delete_project KB-folder removal.
- **oprava:** Replace the comment with the surviving reason — KB folder removal on project delete (routes/projects.py:953) plus RAG indexing — or drop to :ro if no writer needs it.

### [53] README's backend development instructions cd into a directory that no longer exists

- **kde:** `README.md:69`  · trieda: klamlivy-text
- **škoda:** The Development / Backend section opens with `cd /opt/nex-studio-src`. That path was removed in the 2026-05-03 migration to /opt/projects (recorded in docker-compose.yml:1-4) and does not exist on this host. A developer copy-pasting the block gets "No such file or directory" and then runs `poetry install` / `poetry run pytest` in whatever directory they happened to be in.
- **dôkaz:** README.md:68-72 — "```bash\ncd /opt/nex-studio-src\npoetry install --no-interaction\npoetry run pytest\n```".  `ls -ld /opt/nex-studio-src` → "No such file or directory".  docker-compose.yml:2-3 records the move: "# Was implicit `nex-studio-src` (from old /opt/nex-studio-src dir). # Migrated 2026-05-03 per STRUCTURE.md".
- **oprava:** Change to `cd /opt/projects/nex-studio-visual`.
