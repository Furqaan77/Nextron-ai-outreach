# Integration merge rules (Nextron Outreach Engine)

You merge one track branch into `main`.

**Where you work.** Use **only** `e:\Nextron AI outreach`, the `main` integration checkout. Start every shell command with `cd "e:/Nextron AI outreach"`. Never touch any `e:\Nextron-*` worktree: other agents are building in them.

## Steps
1. **Start.** `git status` must be clean. Then run:
   `git -c user.name="Mac" -c user.email="mac@localhost" merge --no-ff <branch> -m "Merge branch '<branch>': <story summary>"`

   The message body ends with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
2. **Resolve conflicts by keeping both sides.** Merge the lists: tables in `owners.py` and `pii_registry.py`, model imports, `main.py` registrations, doctor checks, settings, `test_source_tree` `FILES`, contract pins, README tables and `deferred-work.md` entries.
   - In `sprint-status.yaml`, keep every story's most advanced status and set `last_updated` to now.
   - Don't change product behaviour beyond what the merge needs.
3. **Keep the migration chain linear.** `tests/contracts/test_persistence_contracts.py` requires one linear Alembic chain in which each file `nextron-engine/nextron/db/alembic/versions/NNNN_<story>_<slug>.py` has a revision id equal to its file stem, numbered 0001, 0002, … with no gaps.
   - Main's existing migrations keep their numbers.
   - Renumber the incoming branch's **new** migrations, in their own order, to follow main's head: `git mv` each file, and set its `revision`, its `down_revision` and its docstring "Revision ID" / "Revises" lines.
   - Then update every literal the chain depends on:
     - the `HEAD` constants in `tests/contracts/test_persistence_contracts.py` and `tests/unit/test_migrations.py`;
     - the assert in `tests/app/test_serve_process.py`;
     - the chain probes in `test_persistence_contracts.py`, which assume "the next number" (next is head+1, the wrong number is head+2, the "revision N" text, the length probe, the two-heads case);
     - `real_chain_plus` in `tests/unit/test_serve.py` (head+1);
     - `test_source_tree` `FILES`;
     - the README migrations table;
     - any docstring naming the migration.

     Find them with a grep over `nextron-engine/nextron` and `nextron-engine/tests` for the old and new ids. Sample backup rows in tests that merely contain an old revision string are data, not head checks.
   - Confirm there is a single head by loading the project's script directory: `head_revision()` and `chain_problems()` in `nextron/db/migrations.py`.
4. **Pinned lists in both branches.** If both branches extended a pinned list, such as the doctor check ids (`test_doctor_contracts.py`, `DOCTOR_IDS` in `test_runner_lifespan.py`, `READINESS` in `tests/dashboard.py`, the README, the `build_doctor_checks` docstring) or the health issue sources, combine both additions in the registry order the code produces.
5. **Verify.**
   - Run `cd nextron-engine && uv sync --frozen`.
   - Then run the full suite only through the lock, as a background Bash call (run_in_background: true), and wait for its completion notice:

     `python "e:\Nextron AI outreach\_handoff\suite_lock.py" "e:\Nextron AI outreach\nextron-engine" main-merge`

   - Expect every test to pass apart from the 2 usual skips (`ai_eval` and symlink), and 29 contracts kept.
   - If anything fails, find the root cause in the merge and fix it. Never weaken or skip a test.
   - **After any fix made after a full-suite run, run the full suite again.** Main must never be left on an unverified fix.
6. **Finish.** Commit the merge, or amend nothing: if you fixed things after the merge commit, make a follow-up commit `fix(merge): …` with the same identity and trailer. Then `git status` must be clean. Never push.

## Never rewrite history
Don't use filter-branch, rebase, amend or reset on commits already made. When committing a merge whose message would include git's "# Conflicts:" list, pass the final message with `-m` or `-F` so that list never lands in it.

## Machine load
Outside the lock, use `-n 2` at most, and never run two pytest runs at once.

## Report back (concise)
- the merge commit hash, and any follow-up commit;
- the files that had conflicts, and any non-obvious resolution;
- the renumbering, old to new, with each down revision;
- the pytest count and lint-imports result;
- the new migration head;
- anything the tracks must know.
