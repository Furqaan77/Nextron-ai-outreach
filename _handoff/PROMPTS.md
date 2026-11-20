# Prompt templates

Launch every agent with the Agent tool, in the background (`run_in_background: true`), with `model: "opus"` and `effort: "medium"`. Mac asked for this. Replace each `<...>` placeholder.

Before launching a track, the main session prepares its worktree:
```bash
cd "<worktree>" && git status --short | head -3   # must be clean before switching
git checkout -q -b <new-branch> <base-commit>
cd nextron-engine && uv sync --frozen
grep -n "  <x-y>-" ../_bmad-output/implementation-artifacts/sprint-status.yaml   # get the exact sprint key
```

---

## 1. Build a new story (track orchestrator)

```
You are the orchestrator for track <X> of the Nextron Outreach Engine build.

Before anything else, read and obey e:\Nextron AI outreach\_handoff\track-rules.md. Note especially:
- the model/effort rule;
- the machine-load rule (`-n 2` max outside the lock);
- the rule to dispatch patch work as a NEW Agent call.

You orchestrate:
- Subagents must not commit or run the locked full suite; you do both.
- Subagents must not create files outside the worktree.

**Worktree:** <e:\Nextron-...>, branch <branch>, checked out at <commit> (<what that commit contains>).
**Track label:** track-<X>. Scratch subfolder: e:\Nextron AI outreach\_handoff\scratch\track-<X>\

**Task:** build Story <N.M> "<title>" from _bmad-output/planning-artifacts/epics.md.
- Sprint key: <key>.
- Use the bmad-build skill as track-rules §2 describes.
- Read <the specs of the stories it builds on> and deferred-work.md for continuity.

**Context:**
- <what already exists on main and which signatures to use>
- <which prerequisites are NOT in this tree and must be noted as merge touchpoints>
- <the deferred items this story should pick up>
- <the hard rules that apply: never send email, no LinkedIn/Instagram automation, no test reaches real services>

Finish per track-rules §6, then report per §7.
```

## 2. Resume a story that was stopped mid-way

```
You are the orchestrator for track <X> of the Nextron Outreach Engine build.

Before anything else, read and obey e:\Nextron AI outreach\_handoff\track-rules.md. In particular, start every subagent with model "opus" and effort "medium". Subagents must not commit, run the locked suite, or write outside the worktree.

**Worktree:** <e:\Nextron-...>, branch <branch>.
- Base commit: <base>.
- It holds UNCOMMITTED work for Story <N.M> "<title>" (sprint key <key>), from an earlier orchestrator that was stopped.
- The spec is _bmad-output/implementation-artifacts/spec-<key>.md, with status '<status>'.

**Steps:**
1. Inspect `git status`, `git diff --stat`, and the spec: its status, Implementation Notes, and whether a Review Triage Log exists.
2. Work out which bmad-build step it reached:
   - implementation incomplete;
   - review not done;
   - triage written but patches not applied;
   - patches applied.
3. Run the bmad-build skill (track-rules §2) and continue from that step. Don't redo finished steps; verify them with targeted tests (`-n 2`), `tests/contracts`, and `uv run lint-imports`.
4. Finish with the full suite through the lock, spec `done`, sprint key `review`, and one commit.

Finish per track-rules §6, then report per §7.
```

## 3. Finish batch 4 on main (merge agent)

```
You are finishing an interrupted merge batch on `main` of the Nextron Outreach Engine. Read and obey e:\Nextron AI outreach\_handoff\merge-rules.md, including "Never rewrite history" and "Machine load". Then read e:\Nextron AI outreach\_handoff\CHECKPOINT.md section A.

**State of main:**
- `e:\Nextron AI outreach` is mid-merge of `a44bcf2` (Story 8.4).
- MERGE_HEAD is set, all conflicts are resolved and staged, and the migration is renumbered to `0060_8_4_registry_discovery`.
- The 8 merges before it, since `e530b38`, have not yet been verified by a full suite.

**Steps:**
1. Review the staged 8.4 merge:
   - chain pins for head 0060: HEAD constants, probes expecting 0061 and 0062, `real_chain_plus`, FILES, the README table;
   - `provenance_records` is rebuilt from main's real current shape;
   - 8.4's registry discovery fields agree with 11.5's Segment discovery fields (one representation);
   - `create_app` has `registry_discovery=` and `discovery=`.
2. Run targeted tests (`-n 2`: `tests/contracts` plus the touched tests) and lint-imports. Then commit with the message "Merge commit 'a44bcf2': Story 8.4 discover active UK companies through Companies House".
3. `git merge --no-ff 8819f87` (Story 8.1). Its spec is in e:\Nextron-epic1\_bmad-output\implementation-artifacts\spec-8-1-*.md. Then:
   - Mark `process_signal_item`, `find_contact_email` and `verify_contact_email` as `BudgetClass.BLOCKABLE`.
   - Add an `ESSENTIAL_SAMPLES` entry in `tests/contracts/test_budget_classes.py` for every essential job type on main, for example:
     - score_lead, recompute_tiers, poll_unsubscribes, the 3.12/3.14/3.16 jobs, check_grounding, and the drafting jobs.
   - If the 150%-budget run catches an essential handler asking `allow(BLOCKABLE)`, fix the cause or add a justified `ALLOWED_EXCEPTIONS` entry.
   - Fake budgets passed to health need `status()`.
   - Add health source `discovery` after `sending`.
4. Run ONE full suite through the lock (label main-merge, in the background). If anything fails, fix it, commit `fix(merge): …`, and run the full suite again.
5. Report:
   - merge and fix hashes;
   - final head;
   - the test count;
   - notable resolutions;
   - what the tracks must know.

Don't edit root HANDOFF.md or README.md. Never push. Never touch the e:\Nextron-* worktrees.
```

## 4. Merge a batch of finished stories (merge agent)

```
You merge <N> finished track stories into `main` of the Nextron Outreach Engine. Read and obey e:\Nextron AI outreach\_handoff\merge-rules.md, including "Never rewrite history" and "Machine load".

**Main:** `<commit>`, migration head `<head>`, <tests> tests. <Recent signatures, pins and lists the merge must keep.>

**How to merge:**
- Merge BY COMMIT HASH, in this order. Each merge is its own `git merge --no-ff <hash>`, with a clean message ("Merge commit '<hash>': Story X.Y <summary>"), the Mac identity, and the trailer.
- Renumber each story's new migrations after main's head, in merge order.
- After each merge, run targeted checks (`-n 2`) and lint-imports.
- At the end, run ONE full suite through the lock (main-merge). If it fails, fix it, commit `fix(merge): …`, and re-run.

1. `<hash>`: Story <N.M> <title>. Migration <old name>. <Signatures and conflicts to expect, from the story's report.>
2. ...

Combine all of these:
- export, erasure and coverage counts and section orders;
- settings lists;
- health-source and doctor lists;
- job, trigger and registration orders;
- nav pins and the `/leads/<word>` route order;
- `create_app`, `build_job_types` and `build_app` signatures.

Don't edit root HANDOFF.md or README.md. Never push. Never touch the e:\Nextron-* worktrees. Don't hand back until the final full suite passes on the committed main and `git status` is clean.

**Report:**
- merge and fix hashes;
- the renumbering and final head;
- the test count;
- notable resolutions;
- what the tracks must know.
```

## 5. When a report arrives (main session checklist)
- **A track reports "done":** note its commit for the next merge batch, then give the track its next story (section D of CHECKPOINT.md).
- **A track reports "handed back early":** a subagent's report may come to the main session instead. Forward it to the orchestrator with SendMessage, or resume the orchestrator and tell it to finish.
- **A merge reports "done":** update HANDOFF.md and README.md in a `docs:` commit, then queue the next batch.
- **After every change of state:** keep CHECKPOINT.md current, so the build can always be paused and resumed.
