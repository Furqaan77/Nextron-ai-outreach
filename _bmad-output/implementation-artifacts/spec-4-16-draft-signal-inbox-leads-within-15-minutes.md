---
title: "Story 4.16: Draft Signal Inbox Leads within 15 minutes"
type: 'feature'
created: '2026-11-20'
status: 'done'
baseline_commit: 'e4feb56edba3703c81f1ad909e7138e7dc2bf362'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-nextron-outreach-engine-2026-11-14/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A Lead submitted through the Signal Inbox (2.16) is researched and scored in minutes, but it then waits for Mac to pick it on "Ready to draft" (4.10). A fresh post should become a grounded Step 1 Draft within 15 minutes, with no hand-picking.

**Approach:** Each Signal Inbox item stores `draft_when_ready` (form checkbox, on by default). When `score_lead` scores a Lead that has an undecided `draft_when_ready` item, the same transaction calls a new one-Lead `Selection.pick_one` (`operator_pick`; `enroll` already enqueues `draft_step` at `soon`) and records the outcome on the item. A refusal shows "Signal Inbox Lead <name> not drafted: <reason>" in the notification area and puts the Lead at the top of "Ready to draft". Requirements: FR-8, FR-19 · AD-8, AD-10, AD-21 · NFR-30, NFR-45 · UX-DR5, UX-DR19. The ACs are Story 4.16 in `epics.md`, verbatim, except where the decisions below say otherwise.

## Boundaries & Constraints

**Always:**
- **Migration** `0065_4_16_draft_when_ready` (down `0064_4_15_draft_failed`). It adds three columns to `signal_inbox_items` (owner `discovery`, PII `none`):
  - `draft_when_ready` (bool, NOT NULL; existing rows backfill `false`);
  - `draft_outcome` (nullable code of at most 32 characters: `enrolled`, `disqualified`, a `PickReason` value or a `RefusalCode` value);
  - `draft_decided_at` (nullable).
  - A CHECK requires `draft_outcome` and `draft_decided_at` to be both NULL or both set.
  - Mapped in `owners.py` and `pii_registry.py`. Keep any existing triggers and CHECKs, and the erasure tombstone behaviour.
- **Form.** One "Draft Step 1 when ready" checkbox per submission (`name="draft_when_ready" value="on"`, `checked`). It applies to every item in that submission.
  - Absent means off. Any value other than `on` is `MalformedForm`.
  - `SubmittedItem` / `submit_signal_items` carry it, and the insert stores it.
  - Reprocess keeps the item's stored value.
- **Trigger point.** It lives in `LeadScores.score_lead`, inside the same `with current().transaction()` as `rescore`, and runs only when built with `selection` and `discovery`.
  - If the result is `SCORED` and the Lead (as rescored) has processed items with `draft_when_ready` and `draft_decided_at IS NULL`, it calls `selection.pick_one(lead_id)`, then `discovery.record_draft_outcome(lead_id, outcome)`. That `@command` marks every such item with the outcome and `now`, and appends one `events` row per item (IDs and codes only).
  - If the result is `DISQUALIFIED`, it records `disqualified` with no pick.
  - If the result is `SKIPPED`, it does nothing; the items stay undecided.
  - Each item is decided only once, so a later rescore never re-picks.
- **`Selection.pick_one(lead_id) -> PickOutcome`** is a `@command` that joins the caller's transaction. It does exactly what `pick` does for one Lead: today's live remaining capacity, `_pick_one` (the same reasons, Campaign choice and `operator_pick` origin), `_record`, `add_picked` on enrollment.
  - `pick` is refactored to share the code, with its behaviour unchanged.
  - It logs `signal_inbox_pick` (`lead_id`, `enrolled`, `reason`) after the work.
- **"Not drafted" set** (read-only: a query in `queries.discovery` plus `Selection.signal_inbox_refusals(now)`). It holds the Leads that meet all of these:
  - they have an item whose `draft_outcome` is neither `enrolled` nor NULL, decided within the last 7 days;
  - they are unmerged;
  - they have no live Enrollment.

  Each entry is (lead_id, Lead name, latest reason).
- **Notification line.** It is a computed line, not a stored notification: `Signal Inbox Lead <name> not drafted: <reason text>` (autoescaped) for each entry, newest first, at most 10. It is served by `GET /notifications` next to "signal shortfall". The reason texts reuse `ready_page.REASON_TEXTS`, and `disqualified` reads "Lead disqualified".
- **"Ready to draft".** `ReadyCandidate` gains `signal_inbox_refused: bool`. `ready_order` puts refused Leads first, ordered among themselves by the existing rules, then everything else unchanged.
- **Speed test.** It uses fakes for every port, an activated `draft_sequence` and `grounding_check` (as in 4.15's `activate`), and a site that needs no render. The runner advances the `FrozenClock` by 60 s per job.
  - Submitting one item with idle lanes yields revision 1 with a non-`pending` grounding result, written no later than `submitted_at + 15 min`.
  - Every job runs on the interactive lane, and the drafting/grounding AI calls use `soon`/`interactive`.
  - The revision's `cited_signal_id` is the item's Event Signal, and that Signal's `first_seen_at` equals `submitted_at`.
- **AD-16.** The scenario `scoring.signal_inbox_pick` (`Selection.pick_one`): on `Blocked`, no guarded table gains a row.

**Never:**
- sending email;
- a new job type, table, trigger, setting or dependency;
- picking from a GET;
- a stored notification row holding a Lead name;
- drafting a Lead whose item had the checkbox off;
- bypassing capacity, the AD-16 gate or `enroll`'s checks;
- Steps 2–4, batching or fallback copy (4.17–4.19).

### Decisions made overnight (pending user review)
1. The checkbox is one per submission, not per item. Existing items backfill `false`, so old items never trigger drafting.
2. Each item is decided once (`draft_outcome`/`draft_decided_at`), so a rescore never re-enrolls. A `SKIPPED` score leaves the item undecided, so a merged Lead's survivor decides it.
3. A disqualified Lead counts as "not drafted" (`disqualified`), so Mac learns why.
4. The notification is computed from stored outcomes, not a `Notify.alert` row, so no Lead name is copied into `notifications.text`. It disappears once the Lead is enrolled or merged, or after 7 days. "Top of Ready to draft" uses the same set.
5. "Within 15 minutes" is shown in simulated time at 60 s per job, a conservative per-job latency.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Error handling |
|---|---|---|---|
| Happy | box on, capacity, eligible | Enrollment `operator_pick`, `draft_step` interactive, outcome `enrolled`, rev 1 + grounding ≤15 min | N/A |
| Box off | `draft_when_ready` false | scored only; no pick, no outcome | N/A |
| No capacity | remaining 0 | outcome `over_capacity`; line "not drafted: …"; Lead first on Ready | N/A |
| No Primary Contact / no Campaign / not eligible / already enrolled / Campaign paused | as named | the matching reason recorded; no Enrollment, no Draft | N/A |
| Suppressed Contact | AD-16 blocks | outcome `blocked`; no guarded row | N/A |
| Rescore later | item decided | no second pick | N/A |
| Disqualified | Lead disqualified | outcome `disqualified`, no pick | N/A |
| Picked by hand later | refused Lead enrolled | line and top-of-list gone | N/A |
| Bad checkbox value | `draft_when_ready=yes` | 400 `MalformedForm`, nothing stored | N/A |

</frozen-after-approval>

## Code Map

All paths are under `nextron-engine/`.

- **Signal Inbox submit and form:**
  - `nextron/web/app.py:2111-2137` holds the routes.
  - `nextron/web/signal_inbox_page.py` has `SUBMIT_FIELDS` and `submitted_rows` (:121). `submitted_rows` rejects unknown fields; add `draft_when_ready`.
  - `nextron/web/templates/signal_inbox_panel.html` holds the form (:9-39).
  - `nextron/domain/signal_inbox_item.py` has `SubmittedItem` (:198) and `check_items` (:238).
  - `nextron/app/discovery/api.py:521-560` is `submit_signal_items`.
  - `nextron/db/repos/discovery/signal_inbox.py` has `insert` (:106), `set_status`, `reparent`, `of_leads` and the tombstone. Add `undecided_draft_items(lead_id)` / `record_draft_outcome` / `refusals(since)`.
  - `nextron/db/models/leads.py:193` is `SignalInboxItemRecord`.
  - Migration template: `nextron/db/alembic/versions/0054_2_16_signal_inbox.py`.
  - Registries: `owners.py:532`, `pii_registry.py:577`.
  - Queries: `nextron/app/queries/discovery.py` (`signal_item` :246, `lead` :160).
- **Scoring:**
  - `nextron/app/scoring/lead_scores.py:117`: `score_lead`. Add the `discovery=` keyword (`LeadScores(clock, scoring, queue, *, selection=None, discovery=None)`).
  - `nextron/app/scoring/selection.py`: `_figures`, `_record`, `_pick_one` (:349) and `pick` (:369). Add `pick_one` and `signal_inbox_refusals`, and set the flag in `ready_list`.
  - `nextron/domain/scoring.py:701,730`: `ReadyCandidate` and `ready_order`.
  - `nextron/main.py:1492-1496` builds the services. Pass `discovery=`.
- **Notifications:** `nextron/web/app.py:697` (`shortfall_text`), `:704-728` (`area_context`) and `:1964-1974` (`notification_list`); the template `nextron/web/templates/notifications.html:19-21`; reason texts in `nextron/web/ready_page.py:44-62`.
- **Chain and test harness:**
  - The `World` in `tests/app/test_signal_inbox.py:98-245` provides `run_jobs` (:193), `build_world` (:248), the expected job order (:310) and `world.site(long=True)` (:361, which needs no render).
  - Extend it, or add a new `tests/app/test_signal_inbox_drafting.py`, combining:
    - `build_job_types(..., grounding=, step_drafts=)`;
    - `LeadScores` with `selection` and `discovery`;
    - Campaign, inbox and Variant seeding from `tests/app/test_enroll.World` and `tests/app/test_draft_slots.py:60`;
    - `activate(...)`, `ok()` and `answer()` from `tests/app/test_step_drafting.py:72,109,113`.
  - The `score_lead` handler (`jobs/handlers.py:599`) doesn't pass a lane, and none is needed.
  - `signals.first_seen_at` is at `db/models/signals.py:176`; `draft_revisions.cited_signal_id` is at `db/models/drafting.py:245`.
- **Pins:**
  - HEAD in `tests/contracts/test_persistence_contracts.py:83`, `tests/unit/test_migrations.py:39` (chain probe :2050) and `tests/app/test_serve_process.py:374`;
  - `test_source_tree` `FILES`;
  - `tests/contracts/test_ad16_callers.py` (new Caller and scenario).
- **Merge touchpoints** (not in this tree; renumber at merge):
  - 4.17 `8318a90`: its migration, also 0065; the `submit_bulk_ai`/`poll_ai_batches` jobs; the `bulk_ai_submit_at` setting.
  - 4.18 `640bb27`: `draft_sequence` v2 becomes the default (the speed test's activation and proposal must follow); the `draft_deadline` setting.
  - 5.2 `fb8d52b` (OutboundMessage) and 5.3 `e183ef5` (card details): Draft/approval fixtures.
  - 6.6 `59433fc`: reputation pauses can change cold caps and capacity in tests.

## Tasks & Acceptance

**Execution:**
- [x] Migration, model, repo, owners/PII, queries. Pins and a migration test (existing rows get `false`; the CHECK).
- [x] Domain `SubmittedItem` flag; form parse, template checkbox and web tests (on, off, bad value).
- [x] `Selection.pick_one`, the `pick` refactor, `signal_inbox_refusals` and the `ready_order` flag, with unit and app tests.
- [x] `Discovery.record_draft_outcome`; the `LeadScores.score_lead` hook; `main.py` wiring; app tests for every matrix row.
- [x] The notification line, with a web test (escaped name, gone after enrollment).
- [x] The AD-16 scenario.
- [x] The end-to-end speed test.
- [x] `nextron-engine/README.md`: a Story 4.16 section.

**Acceptance Criteria:**
- Given an enrolled Signal Inbox Lead, then the Enrollment, the `draft_step` job and the item's outcome were written in the same unit of work as the score.
- Given a refusal, then no `enrollments`, `drafts` or `draft_step` row exists for that Lead, and the notification area and the Ready list show it.

## Implementation Notes

- **Migration** `0065_4_16_draft_when_ready` (down `0064_4_15_draft_failed`): `batch_alter_table(recreate="always", copy_from=<0054's exact table>)` adds `draft_when_ready` (Boolean, server default 0, NOT NULL), `draft_outcome` (String 32), `draft_decided_at` (UTC text) and `ck_signal_inbox_items_draft_decided` (`(draft_outcome IS NULL) = (draft_decided_at IS NULL)`). No value CHECK on the outcome: `nextron.domain.signal_inbox_item.is_draft_outcome` (lowercase snake_case, at most 32) guards the one writer. The table has no trigger; every row, check, index and FK is kept (`test_0065_...`). Owners: `discovery`; PII: `none`. Model: `SignalInboxItemRecord` (the server default is `FALSE_DEFAULT`, because the class's `text` column hides `sqlalchemy.text`).
- **Domain** (`signal_inbox_item`): `SubmittedItem.draft_when_ready` (default `False`), `check_items(rows, *, draft_when_ready=False)`, `DRAFT_FIELD`/`DRAFT_ON`, `DRAFT_ENROLLED`/`DRAFT_DISQUALIFIED`, `REFUSAL_WINDOW` (7 days), `REFUSALS_SHOWN` (10), `is_draft_outcome`. `scoring.ReadyCandidate.signal_inbox_refused: bool = False`; `ready_order` sorts on `not signal_inbox_refused` first, then the unchanged key.
- **Repo/queries** (`repos.discovery.signal_inbox`): `insert(..., draft_when_ready=False)`, `StoredItem` gains the three columns (so `of_leads`/`unlinked` and the subject access export carry them), `undecided_draft_items(lead_id)`, `record_draft_outcome(item_ids, outcome, at)`, `refusals(since, enrolled)` (joins `leads` for the name and `merged_into IS NULL`). `queries.discovery.undecided_draft_items` and `signal_inbox_refusals(since)` (one per Lead: its newest refusal) with `SignalInboxRefusal`.
- **Discovery**: `submit_signal_items` stores each item's flag; new `@command record_draft_outcome(lead_id, outcome) -> int` (one `events` row per item: `processed` -> `processed`, cause the outcome, `cause_ref` the Lead, actor `system:discovery`).
- **Selection**: `pick` now runs `_picks(chosen, now)` inside its transaction (behaviour unchanged); `@command pick_one(lead_id)` runs `_picks((lead_id,), now)` in the caller's transaction and logs nothing; `signal_inbox_refusals(now)` filters the query by `queries.sequencing.live_enrollment`; `ready_list` sets `ReadyCandidate`/`ReadyRow.signal_inbox_refused` (the Ready page shows a "Signal Inbox: not drafted" badge).
- **LeadScores**: `discovery=` is typed by a local `DraftOutcomes` Protocol, because the app DAG makes `scoring` and `discovery` independent siblings (contract c6): scoring never imports discovery; `main.py` (and `tests/dashboard.py`) pass the `Discovery`. `_draft_when_ready` runs inside `score_lead`'s transaction after `rescore` and returns the pick (if one ran); after the commit `score_lead` logs `signal_inbox_pick` (`lead_id`, `enrolled`, `reason`). A skipped score decides nothing; items a merge moves to the survivor stay undecided until the survivor is next scored (nothing re-scores it on merge).
- **Web**: `signal_inbox_page.submitted_draft_when_ready` (absent False, `on` True, other values `MalformedForm(wrong_fields)`, twice `repeated_field`); the panel's checkbox (ticked by default; a refused submission keeps it as posted). `GET /notifications` adds `ready_page.not_drafted_items(...)` from `selection.signal_inbox_refusals(now())[:10]`, rendered in `<ul class="signal-not-drafted" hx-disable>` through `|plain`, each line linked to its Lead's page `/leads/{lead_id}`.
- **AD-16**: manifest entry and scenario `scoring.signal_inbox_pick` (`Selection\.pick_one`), the same expectations as `scoring.pick`.
- **Tests**: `tests/app/test_signal_inbox_drafting.py` (every matrix row, the same-unit-of-work AC by a failing outcome recorder, the 7-day window, argument checks), `tests/app/test_signal_inbox_speed.py` (the speed test), `tests/unit/test_signal_inbox_page.py` (checkbox on/off/bad/twice/kept), `tests/unit/test_ready_page.py` (escaped line, top-of-list badge, gone after a hand pick, at most 10), `tests/unit/test_ready_order.py`, `tests/unit/test_migrations.py::test_0065_...`; HEAD pins and chain probes bumped (0066/0067), `test_source_tree` `FILES`.
- **Implementation decisions to review** (the frozen section could not hold them):
  1. *Signal Inbox Leads have no country.* `_apply_website` creates the Lead without `country_code`, so `research` resolves its author's Contact to no Market and `enroll` refuses it (`missing_tz_or_market`); the item is then shown as "not drafted: the Contact has no timezone or Market". Nothing here changes how a Lead gets its country (that would be a discovery/research behaviour change outside this spec). The speed test therefore submits a post about an organization that is already a US Lead (as a CSV import leaves it), deletes that import's queued crawl ("idle lanes"), and publishes the COO's address on the team page so `find_contact_email` finds it (Published; US Eligibility passes). A follow-up story should decide how a post-created Lead gets its country, or the happy path stays limited to known Leads.
  2. *Speed test lanes.* Every job on the way to the Draft runs on the interactive lane; Story 2.14's `verify_contact_email` (queued by `find_contact_email` for the found address) runs on the bulk lane as it always has, and nothing waits for it. The test asserts that exactly that job is the only bulk one. The grounding check's AI call is at `interactive` (its job runs on the interactive lane), drafting's at `soon`.
  3. In the speed test 11 jobs run; revision 1, its `pass` grounding result and the item's outcome are all written well before `submitted_at + 15 min`.
- **Review patches (pass 1)**: #1 `tests/app/test_signal_inbox_drafting.py::test_build_app_wires_lead_scores_to_the_selection_and_discovery` (the built `LeadScores` has a `Discovery` and the app's `Selection`, and is the one `build_job_types(scores=...)` and `create_app(lead_scores=...)` get); #2 `pick_one` no longer logs, `score_lead` logs `signal_inbox_pick` after its commit when a pick ran (`SIGNAL_INBOX_PICK` moved to `lead_scores`), the happy-path test asserts the fields and the failing-recorder test asserts no line; #3 the README, the `lead_scores` docstring and these notes say a survivor's moved items wait for its next score; #4 `not_drafted_items` links each line to `/leads/{lead_id}` (`READY_HREF` removed; test and `notifications.html` comment updated); #5 `repo.record_draft_outcome` also requires `draft_decided_at IS NULL`, `draft_when_ready IS TRUE` and `status = ItemStatus.PROCESSED`, `undecided_draft_items` uses `ItemStatus.PROCESSED`, new `test_the_repo_never_overwrites_a_decided_item`; #6 `DRAFT_OUTCOME_LENGTH = MAX_DRAFT_OUTCOME`; #7 `test_a_refused_submission_keeps_the_box_as_it_was` is parametrized ticked/unticked, and the over-long `test_ready_page.py` docstring line is wrapped.


## Spec Change Log

## Review Triage Log

Pass 1: blind hunter (B), edge-case hunter (E), verification gap (V). 24 raw findings, 17 after deduplication: 7 patched, 4 deferred, 6 rejected.

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| 1 | V1 | `build_app`'s `LeadScores(..., discovery=, selection=)` wiring untested; dropping either silently disables the feature | medium | Every test builds its own `LeadScores`; `_draft_when_ready` returns silently on a None. | patch: a `build_app` wiring test like `test_step_drafting.py:690-716` |
| 2 | B2, E4, E9 | `signal_inbox_pick` is logged inside `score_lead`'s open transaction, so it can report a rolled-back Enrollment | low | `pick_one` logs before `score_lead`'s `with current().transaction()` closes; every other log line in this module is written after the commit. | patch: log after the commit in `score_lead`, with a test (IDs and codes only) |
| 3 | E7, B5 | Docs claim "a merged Lead's survivor decides its items", but nothing re-scores the survivor on merge | low | `reparent_lead` only moves the rows; the survivor decides them only when it is next scored. | patch: correct the wording in the README and the `lead_scores` docstring |
| 4 | E8, B7 (part) | The "not drafted" line links to `/leads/ready`, where a disqualified or no-Primary-Contact Lead isn't listed | low | `ready_list` drops those Leads; `not_drafted_items` throws away `lead_id`. | patch: link each line to the Lead page `/leads/{lead_id}` |
| 5 | B11 | `repo.record_draft_outcome` updates by id only; "decided once" rests on the caller alone | low | Its WHERE has no `draft_decided_at IS NULL`. | patch: add `draft_decided_at IS NULL`, `draft_when_ready`, `status = processed` to the WHERE |
| 6 | B12 | The `"processed"` literal in the repo; `DRAFT_OUTCOME_LENGTH` copies `MAX_DRAFT_OUTCOME` | low | The model module already imports from the domain module. | patch: use `ItemStatus.PROCESSED` and `MAX_DRAFT_OUTCOME` |
| 7 | B14 (part) | No test that a refused submission keeps the box ticked; one docstring line is over-long | low | The tests cover only the unticked case. | patch |
| 8 | B1 | A Lead created from a post has no country, so `enroll` refuses `missing_tz_or_market` and the real post-only path never drafts | high | Pre-existing in 2.16: `_apply_website` sets no `country_code`. Fixing it changes discovery and research, which this story's intent doesn't cover. | defer (high), flagged in the report |
| 9 | E1, E2 | An item whose chain stops before `score_lead` stays undecided, with no draft and no "not drafted" line | medium | The chain's failure paths (dead job, deferred) never reach the hook; a sweep would be new surface. | defer |
| 10 | B4, B13 | A long-undecided item (SKIPPED score) can enroll the Lead much later through a Re-score or reopen | medium | `undecided_draft_items` has no age limit; this is rare (it needs a skipped score first). | defer |
| 11 | E5 | A refused Lead that was hand-enrolled and whose Enrollment ended within 7 days reappears as "not drafted" | low | Real, but rare, and the fix adds a query branch. | defer (low) |
| 12 | E3, B3 | A raising `pick_one`/`enroll` rolls back the score | false | The frozen intent requires the pick in the same transaction as the score; a raise makes the job retry and fail visibly (dead-job alert). | reject |
| 13 | E6 | After a successful submit the box shows ticked again | false | The AC says "on by default"; a fresh form shows the default. | reject |
| 14 | B6 | Reprocess onto a different Lead keeps the old decision | false | Reprocess works only on `flagged` items, which have no Lead and no decision (`ck_signal_inbox_items_processed`). | reject |
| 15 | B7 (part), B8 | No dismiss action; `over_capacity` is final with no next-day retry | false | Spec decisions 2 and 4: decided once; computed lines that leave after enrollment, merge or 7 days. | reject (would change the approved spec) |
| 16 | B9 | The refusal query is unbounded with N+1 `live_enrollment` calls | low | Single-operator volume over a 7-day window is a handful of rows. | reject |
| 17 | B10, B14 (rest) | The count ignores the new lines; a cross-module test helper; a redundant assert | low | The count was already stored-only (9.4's due lines are excluded the same way); the rest is cosmetic. | reject |

## Verification

- **Final gate (orchestrator):** full suite through `suite_lock.py` on the patched tree: 20163 passed, 7 skipped; `lint-imports` 29 kept, 0 broken. Merge touchpoints (4.17 migration 0065 clash, 4.18 `draft_sequence` v2 default and `draft_deadline`, 5.2, 5.3, 6.6) are listed in the Code Map.

**Commands** (run in `nextron-engine/`, with `--basetemp` under the track scratch folder and `-n 2` at most):
- `uv run pytest -q <touched test files>`: all pass.
- `uv run pytest -q tests/contracts`: all pass.
- `uv run lint-imports`: every contract kept.
