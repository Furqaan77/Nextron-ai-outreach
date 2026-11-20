# Rules for every build track (Nextron Outreach Engine)

The build runs as parallel tracks. Each track has its own git worktree and branch under `e:\Nextron-*`.
`e:\Nextron AI outreach` (branch `main`) is the integration checkout, and only the main session and its merge agents use it.

## 1. Stay in your worktree
- Work **only** under the worktree path named in your prompt.
- Never modify files, run git or run tests in any other `e:\Nextron-*` folder or in `e:\Nextron AI outreach`.
  - **One exception:** you may write your scratch files under `e:\Nextron AI outreach\_handoff\scratch\<track label>\`, and `suite_lock.py` creates its lock folder in `_handoff`. Git ignores `_handoff`. Never touch anything else in that checkout.
- Use absolute paths, and start every shell command with `cd "<your worktree>"` or `cd "<your worktree>/nextron-engine"`.
- The `.venv` is synced. Run `uv sync --frozen` if `uv.lock` changed.
- **Model and effort (user's instruction, 2026-11-18):** start every subagent with the Agent tool parameters `model: "opus"` and `effort: "medium"` (Opus 5.5, medium effort). This includes implementers, reviewers and patchers, and every subagent the bmad-build workflow starts.
- Every subagent you start (implementer, reviewers, patcher) must get this rules file's path at the top of its prompt, along with your worktree path.
- When `bmad-build` tells you to run `render_skill.py`, set `{project-root}` to your worktree. Every `{project-root}` path means your worktree.
- Read `<worktree>\CLAUDE.md` and `<worktree>\HANDOFF.md` §3–§4 first.
- Never edit the root `HANDOFF.md` or root `README.md`; the main session keeps those. Story docs go in `nextron-engine/README.md`.

- **Scratch files go in your own track subfolder:** `e:\Nextron AI outreach\_handoff\scratch\<track label>\`, for example `e:\Nextron AI outreach\_handoff\scratch\track-D\`. This covers helper scripts, diffs and logs. Tracks run at the same time and overwrite each other's same-named files.

## 2. Workflow
- Invoke the Skill tool with skill `bmad-build`. Its args name the story, the sprint key and "Project root: <worktree>". Also give:
  - "Governing architecture: _bmad-output/planning-artifacts/architecture/architecture-nextron-outreach-engine-2026-11-14/ARCHITECTURE-SPINE.md."
  - "The user prefers no checkpoint pauses."
- Follow workflow steps 1–5 exactly, including the three-reviewer review (blind hunter, edge-case hunter, verification gap), triage, patching and re-verification.
- **Dispatch patch work as a NEW Agent call** (model opus, effort medium) with the triage rows and the context it needs. Don't SendMessage the old implementer: an orchestrator waiting on a messaged agent gets forced to hand back early, and the result goes astray. You may also apply small patches yourself.
- Write the complete Review Triage Log into the spec as soon as triage is decided, before dispatching any patch work.
- Read only the specs you need for continuity: the ones named in your prompt, plus `deferred-work.md`.
- Keep your own context lean. Use targeted reads and greps rather than whole large files.

## 3. Engine conventions
- **Units of work.** Use `@use_case` or `@command` with `BEGIN IMMEDIATE`. GETs are read-only units that read through `nextron.app.queries.*`. Keep file and network I/O outside transactions.
- **Routes and forms.**
  - Every route needs `@personal_data(...)`.
  - Don't use `include_router`.
  - Forms need `method="post"`, `hx-post` and `{{ csrf_field() }}`.
- **Migrations.**
  - Name them `versions/NNNN_<epic>_<story>_<slug>.py`. The revision id equals the file stem, is 31 characters or fewer, and uses underscores. `downgrade()` raises.
  - Take the next free number **in your tree**. Other tracks may take the same number; the main session renumbers and re-chains at merge.
  - Reference the revision id through the existing `HEAD` constants: `tests/contracts/test_persistence_contracts.py`, `tests/unit/test_migrations.py` and the `tests/app/test_serve_process.py` assert. Bump the chain probes and `test_source_tree` `FILES`.
  - Every column must be mapped in `owners.py` and classified in `pii_registry.py`.
- **Adapters.**
  - Classify every new adapter in `tests/live_guard.py`, add it to import contract c5, and give it a fake in `PORT_FAKES`.
  - Read credentials with `read_credential` at call time.
- **Tests.**
  - Tests run as `NEXTRON_ENV=test`. Live adapters, the real keyring and non-loopback network raise there.
  - Never reach a real service, and use temporary dirs only.
  - No test may bind a fixed port: take a free port as `free_port()` does. If a fixed port is unavoidable, mark the test `xdist_group("fixed_port")`.
  - Keep tests deterministic with no long sleeps.
- **`nextron/web/static/app.css`** is marked `-text` and has mixed line endings: its first 293 lines end in CRLF. Edit it byte-preserving, with Python `open(..., newline="")` or the Edit tool. Never use Git Bash `sed`, which strips the CRs.
- **Navigation pins.** The nav now includes `("/leads", "Leads/Pipeline")`. A new `GET /leads/<word>` route must be registered before `/leads/{lead_id}`.
- **Required settings.** As of main `ea09802`, seven settings are required: `sender_name`, `sender_timezone`, `alert_chat_id`, `sender_signature`, `legal_name`, `postal_address` and `privacy_notice_url`. A test that lists `required_unset()`, doctor's "all ok" settings or the whole Settings form must include every required setting.
- **The contact gate (3.4).** `Research.add_contact` and `Discovery.upsert_lead` call `assert_contactable` first.
  - A test that needs a Contact holding an already-suppressed key can't create it through `add_contact`.
  - Instead, write the suppression entry after creating the Contact, or use the `gate_bypassed()` pattern in `tests/app/test_suppression_panel_commands.py`.
  - Every new outreach path must call `compliance_api.assert_contactable(...)` and add a scenario to `tests/contracts/test_ad16_callers.py`.
- **Main as of `e530b38`** (18613 tests, head `0054_2_16_signal_inbox`, next migration 0055). `create_app` also requires `place_discovery=`, `assist_accounts=`, `pipeline=`, `channel_permissions=`, `selection=` and `signal_inbox=`. There is one `Pipeline(clock).erase_pii`. Health sources: jobqueue, budget, backups, playbook, sending, erasure, unsub_poll, pipeline (+compliance).
- **Main as of `43f9c1b`** (16866 tests, head `0042_4_9_drafts`, next migration 0043):
  - `build_job_types` requires `test_runs`, `job_boards`, `legal_forms` and `scores`.
  - `create_app` requires `lead_scores=`.
  - `PrimaryContacts(clock, research, queue)`.
  - `Erasures(..., queue=...)`.
  - `crawl_repo` page writes need `mailto=`.
  - `contacts` has 18 tombstone-fixed columns.
  - Health issue sources: jobqueue, budget, backups, playbook, sending, erasure, compliance.
- **Older signature notes, as of `4d46a06`:**
  - `build_job_types` needs `extractions`, `primary` and `test_runs`, among others.
  - `EligibilityContact` requires `provenance_recorded`.
  - The Lead page Contacts panel (`lead_contacts.html`) has 8 columns.
  - Any new `contacts` column must recreate `trg_contacts_tombstone` (the erasure tombstone trigger).
- **Engine children in tests.** A test that starts a real engine child must pass `tests.live_guard.child_env`; a contract enforces this.
- **`Discovery(clock, pipeline, queue)` needs a job queue.** In tests, copy the `queue_on(clock)` helper from `tests/app/test_leads.py`.
- **Dependencies.** A new runtime or dev dependency is pinned exactly and added to `tests/contracts/test_lockfile.py`. Any library the spine doesn't pin is a genuine gap: record it as an overnight decision.

## 4a. Full-suite policy (speed without losing quality, 2026-11-17)
The full suite is the quality gate. Running it more than the gates need only lengthens the lock queue.

- **Machine load (2026-11-18): the PC ran out of resources and crashed with five tracks.** Targeted test runs outside the lock must use `-n 2` at most, or no xdist at all. Never run two targeted pytest runs at the same time, and never pass `-n auto`.
- **Disk (2026-11-19): C: is nearly full.** Every targeted pytest run must pass `--basetemp=e:\Nextron AI outreach\_handoff\scratch\<track label>\pt` so test temp files go to E:. `suite_lock.py` already does this for full runs.
- **Implementer and patcher subagents never run the full suite.** They run:
  - the test files for the code they touched;
  - `uv run pytest -q tests/contracts`;
  - `uv run lint-imports`.
- **The orchestrator runs the full suite through the lock once, on the final tree after the review patches.** That run is the gate before the commit. If it fails, fix the cause and run it again.
- **Step 3's verification before review** uses the targeted tests plus `tests/contracts` and `lint-imports`, not the full suite.
- **A second gate follows.** The main session runs the full suite again after merging into `main`.

## 4. Full suite: only through the lock
Run it as a background Bash call (run_in_background: true) and wait for the completion notice:

`python "e:\Nextron AI outreach\_handoff\suite_lock.py" "<worktree>\nextron-engine" <track label>`

It runs `pytest -q -n 6 --dist loadgroup`, or serial if the tree lacks pytest-xdist, then `lint-imports`. It exits 0 only if both pass.

Targeted runs of a few files don't need the lock. Exclude `uv.lock` and the vendored `htmx-2.0.11.min.js` from review diffs.

**Known flake.** Under parallel load, `tests/unit/test_job_runner.py::test_start_runs_one_realtime_one_interactive_the_bulk_threads_and_the_keeper` (and `test_a_second_start_raises_and_stop_is_idempotent`) sometimes fails: a job thread outlives the 0.05 s `shutdown_grace`. **The fix is on `main` (merge `496e2ba`).** A tree based on `496e2ba` or later shouldn't see it; if it does, report it.
- If that is the **only** failure in a suite run, re-run the suite once.
- Don't modify that test or `nextron/jobs/runner.py` for it.
- Mention it in your report if it happened.

## 5. Standing user instructions (Mac asked for unattended building)
- **Every BMAD checkpoint is pre-approved.** Answer "Approve and continue", and keep the spec as one; don't split it.
- **For a genuine intent gap,** choose the conservative, compliance-safe option. Record it in the spec under `### Decisions made overnight (pending user review)`. Never ask questions.
- **The architecture spine (AD-1..AD-22) and the planning docs are binding.**
- **Never:**
  - send email from nextronai.com;
  - automate LinkedIn or Instagram;
  - put secrets in files, the DB, logs or job payloads;
  - send any outbound email without human approval.

## 5a. Mac's real PC: system safety
- **Never change system state on this PC.** That covers:
  - Task Scheduler: Register, Unregister, Start, Stop and Set cmdlets, `schtasks` writes, and COM writes;
  - services and the registry;
  - firewall, environment and execution-policy settings;
  - Windows Credential Manager outside the test keyring;
  - the real `%LOCALAPPDATA%\Nextron` data dirs.
- **Tests and helper scripts use injected fakes or dry-run modes.** A subagent once registered a real scheduled task while "mocking" one; it must never happen again.
- **Clean up processes.** Kill every process you start, and verify that none is left.

## 6. Finishing (mandatory)
- Wait for every subagent you start to finish; never hand back with work pending.
- If you are forced to hand back early, say so at the top of your report and give the exact remaining steps. The main session will resume you by message.

You are done only when all of these hold:
1. The full suite passed through `suite_lock.py` on the final tree, and `lint-imports` kept every contract.
2. The spec's Implementation Notes match the code.
3. The spec is `status: 'done'`, and the story's sprint-status key is `review`.
4. Everything is committed in your worktree on your branch with `git -c user.name="Mac" -c user.email="mac@localhost" commit`. The message is `feat(engine): …` and ends with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. `git status` is clean afterwards.
5. Never push, merge, rebase or switch branches.

## 7. Report back (concise, one message)
- commit hash
- test count
- lint-imports result
- review counts: raw, deduped, fixed, deferred, rejected
- overnight decisions
- items rejected only because they'd change the approved spec
- each migration's file name, revision id and down revision
- any new dependency
- anything the next story on your track must know
