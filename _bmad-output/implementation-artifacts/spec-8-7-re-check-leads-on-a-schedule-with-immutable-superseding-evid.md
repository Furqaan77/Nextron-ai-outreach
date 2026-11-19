---
title: 'Story 8.7: Re-check Leads on a schedule with immutable, superseding Evidence'
type: 'feature'
created: '2026-11-19'
status: 'done'
route: 'dispatch'
baseline_commit: 'e530b3856def5a5034f17ebdb61df802354a7475'
review_loop_iteration: 0
story_key: '8-7-re-check-leads-on-a-schedule-with-immutable-superseding-evid'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-8-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Research runs once per Lead, so a Lead outside a Sequence never gains new Events, and its Evidence is never re-confirmed or superseded. That means selection never sees fresh reasons to write.

**Approach:**
- A daily `recheck_leads` tick picks the due Leads and enqueues one blockable, bulk-lane `recheck_lead` job each.
- That job calls `research.refresh(lead, mode)`. Mode `full` reuses the Epic 2 crawl chain inline. Mode `jobs` runs `check_job_boards` and the rest of its chain.
- `crawl_runs` records the mode.
- An unchanged page re-confirms its Evidence without an AI call.
- A Tier change writes an `events` row.
- The Lead page shows superseded Evidence linked to its replacement, and the last re-check date.

## Boundaries & Constraints

**Always:**
- **Selection.** The `recheck_leads` handler selects every Lead that meets all of these:
  - it has a `web_domain` and is not merged;
  - its stage is not in `CLOSED_STAGES` (Lost, Disqualified) or `PARKED_STAGES` (`not_now`);
  - it has no `active` or `paused` Enrollment (`queries.sequencing.live_enrollment`);
  - `compliance_api.assert_contactable(NewLeadSubject(web_domain), Channel.EMAIL, Purpose.DISCOVERY)` returns `Allowed`;
  - if it has a Primary Contact, that Contact's `evaluate_eligibility` reasons are empty or all `recontact_not_allowed`.
- **Mode.** `full` when the Lead has no full crawl, or its last one is at least `recheck_full_days` old. Otherwise `jobs` when it has no hiring check, or its last one is at least `recheck_jobs_days` old. Otherwise the Lead isn't due.
- **Order and limit.** Due Leads go oldest first, by their last crawl's `started_at` (none first), then by id, up to `recheck_daily_limit`.
- **Enqueue.** Each gets `recheck_lead`, scope the Lead ULID, discriminator the local date (`sending_policy.local_date(now, sender_timezone)`), payload `{v, lead_id, mode}`.
- **Settings.** Three new INTEGER settings in the ENGINE section, all at least 1:
  - `recheck_full_days`, default 30;
  - `recheck_jobs_days`, default 7;
  - `recheck_daily_limit`, default 40 (maximum 1000).
- **Budget.** `refresh` checks first, in a transaction: `allow(RECHECK_LEAD_BUDGET_CLASS = BudgetClass.BLOCKABLE, meter.snapshot(now), budget_limits())`. A refusal returns `False`, and the handler returns `DEFERRED`. No crawl, board or AI call is made.
- **Re-validation.** A Lead that has since stopped qualifying (unknown, merged, closed, parked, live Enrollment) is logged `recheck_skipped` with a reason, and `refresh` returns `True`.
- **The 8.1 seam.** The `recheck_lead` registration in `main.build_job_types` carries the comment `# Story 8.1: budget_class=BudgetClass.BLOCKABLE` on its own line, so 8.1's merge can add the field there.
- **Mode `full`** calls `WebsiteCrawls.enrich_lead(lead_id, may_retry=…, lane=Lane.BULK, mode=CrawlMode.RECHECK_FULL)` inline. The chain then runs unchanged: extraction skips unchanged pages, supersedes or retracts on changed ones, and continues through job boards and scoring.
- **Mode `jobs`** inserts a `crawl_runs` row inline with:
  - `mode=recheck_jobs`, `outcome=ok`;
  - `site_domain` set to the Lead's domain;
  - zero page counts, and started and ended at now.

  It then calls `JobBoards.check_job_boards(lead_id, run_id, lane=Lane.BULK)`. That writes the board counts into the run, closes postings no longer listed, publishes `EventClosed`, and continues to `check_legal_form` → Primary → `score_lead`.
- **Run history.** `crawl_runs.mode` ∈ {`initial`, `recheck_full`, `recheck_jobs`}. Existing and normal crawls are `initial`.
  - `crawl_repo.latest_run` and `run_count` ignore `recheck_jobs` rows, so ATS links and extraction keep reading the last real crawl.
  - The last full crawl is `latest_run`.
  - The last hiring check is the latest run, of any mode, whose `board_errors` is not null.
- **Unchanged pages.** A re-crawled page whose `content_hash` is unchanged confirms, in the crawl's apply transaction, each live (not superseded, not retracted) Evidence of that Lead whose `source_url` equals the page URL. Only `last_confirmed_at` changes (`confirm_evidence`, at the fetch time), and no `extract_signals` call is made for it.
- **Tier events.** `Scoring.rescore` and `Scoring.retier` append an `events` row when the stored Tier changes, including from none:
  - `entity="leads"`, the Lead id;
  - `from_status` `tier_<x>` or None, `to_status` `tier_<y>`;
  - cause `lead_rescored` or `lead_retiered`, actor `scoring.ACTOR`.
- **Lead page.**
  - "Last re-check: <date>" shows the latest `recheck_*` run, or "never".
  - Superseded Evidence from `evidence_history` is listed, labeled "superseded", and linked to its replacement's anchor `#evidence-<id>`.
- **Conventions.**
  - The trigger is `Every(RECHECK_LEADS_PERIOD = 24 h, at_startup=True)` in `build_triggers`.
  - Both job types are bulk-only and in `JobGroup.RECHECK`.
  - An AD-16 callers contract scenario is added for the new `assert_contactable` call.
  - No test reaches the network.

**Never:**
- Never UPDATE an Evidence row's quote, type, source or other observation columns. Re-checks only insert, supersede, retract or confirm, through the existing repo functions.
- Never call `recheck_lead` work on an essential path.
- Never build `drafting.flag_stale`, `domain.grounding.is_stale`, the queue `stale` badge or the `EventClosed` → "Event expired" pull-back. Those belong to Stories 4.20, 5.1 and 5.9.
- No new dependency.

### Decisions made overnight (pending user review)

1. **The stale and expired subscribers are deferred.** `drafting.flag_stale`, `domain.grounding.is_stale` and the `EventClosed` pull-back don't exist yet; Stories 4.20 and 5.9 own them. 8.7 proves only that `EvidenceChanged(lead_id, evidence_id)` and `EventClosed` are published on a re-check, and leaves the subscribers to those stories. Both are logged in `deferred-work.md`.
2. **Mode `jobs` continues the chain** after `check_job_boards`: legal form, Primary, `score_lead`. Without that, a new hiring Event never re-scores the Lead. Only ATS boards are re-checked in `jobs` mode; careers-page postings refresh on the next full re-check.
3. **A Lead with no Primary Contact is still re-checked,** because research may find one. The domain gate `assert_contactable` still applies.
4. **The trigger is a 24-hour `Every` at startup,** like `purge_place_coords`. The local-date key makes a second same-day enqueue a no-op while its job is live.
5. **No provider quota is checked in `refresh`.** Crawling has none; each downstream metered stage (boards, registry, finder, AI) already gates itself.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Due full | last full crawl 31 days ago | `recheck_lead` with mode `full`; new run `recheck_full` | N/A |
| Due jobs | full crawl 10 days ago, hiring check 8 days ago | mode `jobs`; run `recheck_jobs` with board counts | N/A |
| Not due | full crawl 10 days ago, hiring check 2 days ago | no job | N/A |
| Excluded | Lost, Disqualified, `not_now`, merged, no domain, live or paused Enrollment, suppressed domain, Primary failing `do_not_contact` | not selected | N/A |
| Recontact-only | Primary's only failing reason is `recontact_not_allowed` | selected | N/A |
| Limit | 45 due, limit 40 | the 40 oldest are enqueued | N/A |
| Unchanged page | same hash on re-crawl | no LLM call; Evidence `last_confirmed_at` advances; other columns same | N/A |
| Changed value | 9 → 12 lawyers | new Evidence inserted; old one has `superseded_by`; `EvidenceChanged` published | an UPDATE of the quote raises (trigger) |
| Quote vanished | changed page, quote gone | `retracted_at`; Signal with no live Evidence retracted; `EvidenceChanged` | N/A |
| Posting delisted | jobs mode, board no longer lists it | posting closed; `EventClosed` published | N/A |
| Tier B→A | new Tier 1 Event | `score_lead` sets Tier A; `events` row `tier_b`→`tier_a` | N/A |
| Budget 100% | AI budget spent | `DEFERRED`; crawler, LLM and board fakes all show 0 calls | N/A |

</frozen-after-approval>

## Code Map

All paths are under `nextron-engine/`.

**Scheduling and jobs**
- `nextron/jobs/triggers.py:81`: `Trigger.RECHECK_LEADS` already exists.
- `nextron/main.py:2019` `build_triggers`: add the trigger, modelled on `purge_place_coords` at :2056.
- `nextron/main.py:1572` `build_job_types`: register `recheck_leads` and `recheck_lead`.
  - The template is `recheck_event` at :1836, but make both bulk-only.
  - Add a `rechecks=` provider with a raising stub default, like `no_place_discovery` at :1953.
  - `build_app` builds the service and keeps it on `app.state`.
- `nextron/jobs/handlers.py`: add the handlers.
  - `recheck_leads` is a tick that calls `rechecks().enqueue_due()`.
  - `recheck_lead` returns `DEFERRED` when `refresh` returns `False`; see the `enrich_lead` handler at :264-272.
- `nextron/domain/job.py:369`: the `<ulid>:<YYYY-MM-DD>` key is already valid.

**The re-check itself**
- New `nextron/domain/recheck.py`, all pure:
  - `RecheckMode` (`full`, `jobs`);
  - `RECHECK_LEADS_JOB`, `RECHECK_LEAD_JOB`, `RECHECK_LEADS_PERIOD`;
  - `RECHECK_LEAD_BUDGET_CLASS`;
  - `mode_due(last_full, last_hiring, now, full_days, jobs_days) -> RecheckMode | None`;
  - `eligibility_allows(decision) -> bool`.
- `nextron/domain/crawl.py`: add `CrawlMode` (`initial`, `recheck_full`, `recheck_jobs`).
- New `nextron/app/research/recheck.py`: `Rechecks(clock, meter, crawls, job_boards, queue, compliance)`, with:
  - `@use_case enqueue_due() -> int`;
  - `refresh(lead_id, mode, *, may_retry) -> bool`.
  - This is the "`research.refresh`" the story names. Export it the way the other research services are exported.
- `nextron/app/research/api.py`:
  - `WebsiteCrawls.enrich_lead` (around :1766) takes `mode: CrawlMode = CrawlMode.INITIAL` and passes it to `insert_run`.
  - `_upsert_page` (:2043) knows when a hash is unchanged. On a hash match, call `signals_repo.confirm_evidence` for the matching live Evidence.
  - `confirm_evidence` at `repos/research/signals.py:240` takes the later of the stored and fetched times, as `extraction._carry_out` :557 does.
- `nextron/app/research/job_boards.py:241` `check_job_boards`: unchanged. `_boards` (:203) reads `latest_run`, which now ignores `recheck_jobs` rows.

**Data**
- `nextron/db/models/crawl.py:128` `CrawlRunRecord`: add `mode`, `String`, not null, server default `'initial'`, with a CHECK.
- Migration `db/alembic/versions/0055_8_7_crawl_run_mode.py`: rev `0055_8_7_crawl_run_mode`, down `0054_2_16_signal_inbox`; `downgrade()` raises.
- `db/owners.py` (research) and `db/pii_registry.py` (not PII).
- `nextron/db/repos/research/crawl.py`:
  - `insert_run` :261 takes `mode`;
  - `latest_run` :312 and `run_count` :329 exclude `recheck_jobs`;
  - add `last_hiring_check(lead_id)`, `last_recheck(lead_id)` and `recheck_candidates()`.
  - Check every other `latest_run` caller still wants "the last real crawl".

**Selection inputs** (read through `nextron.app.queries.*` and `compliance_api`)
- Stage: `domain/pipeline_stage.py:127-128`.
- `queries.sequencing.live_enrollment` :52.
- `compliance/api.py:986` `assert_contactable` and :901 `evaluate_eligibility`.
- `domain/eligibility.py:133` `RECONTACT_NOT_ALLOWED`.
- For the Primary Contact lookup, see `scoring/selection.py` `_primary`.

**Settings**
- `nextron/domain/settings_schema.py`: add the `Key` members (:178) and the `Setting` entries next to `CRAWL_MAX_PAGES` (:1304).
- Update the lists of every setting:
  - `tests/contracts/test_settings_contracts.py:144`;
  - `tests/unit/test_settings_schema.py:538` and :573;
  - `tests/unit/test_settings_page.py:587`;
  - `tests/app/test_telegram_secrecy.py:218`.

**Scoring**
- `nextron/app/scoring/api.py:165` `rescore` and :217 `retier`: read the old Tier from `scores_repo.stored_row`, then call `nextron/db/eventlog.py:79` `append`.

**Lead page**
- `web/leads_page.py:864/1008` (`EvidenceRow`, `evidence_row`).
- `web/templates/lead.html:59-80`.
- `queries.research.evidence_history` :326; add `queries.research.last_recheck`.
- Give each Evidence row `id="evidence-<id>"`.

**Contract updates**
- Head bumps: `tests/contracts/test_persistence_contracts.py:83`, `tests/unit/test_migrations.py:39`, `tests/app/test_serve_process.py:372`, and `test_source_tree` `FILES`.
- `tests/contracts/test_job_contracts.py`: the name lists (~:635, ~:763), the spec table (~:823-857), the trigger asserts (~:1388) and the docstring.
- `tests/app/test_engine_health.py:359`: the RECHECK group.
- `tests/contracts/test_ad16_callers.py`.

**Fakes and harness**
- `tests/fakes/llm.py` `ScriptedLlm.calls`, `tests/fakes/crawler.py` `ScriptedCrawler`, `tests/fakes/job_board.py` `ScriptedJobBoards` and `tests/fakes/usage_meter.py`.
- The chain harness to copy is `tests/app/test_signal_inbox.py:117`.

## Tasks & Acceptance

**Execution:**
- [x] Domain: `domain/recheck.py` and `CrawlMode` in `domain/crawl.py`, with unit tests for `mode_due` boundaries (exactly N days is due) and for `eligibility_allows`.
- [x] Data: the model column, the migration, owners, the PII registry and the repo changes. Repo tests cover mode filtering, `last_hiring_check`, and an UPDATE of `evidence.quote`/`type`/`source` raising.
- [x] Settings: the three settings and the setting-list updates.
- [x] Research:
  - `enrich_lead` mode;
  - the unchanged-page confirm in `_upsert_page`/apply;
  - `app/research/recheck.py` (`enqueue_due`, `refresh`).
- [x] Scoring: Tier-change `events` rows in `rescore` and `retier`.
- [x] Wiring: handlers, `build_job_types` (with the 8.1 seam comment), `build_triggers`, `build_app`, and the job, health and AD-16 contracts.
- [x] Web: the Lead page's last re-check date and superseded Evidence with its link.
- [x] Tests in new `tests/app/test_recheck.py`: every I/O Matrix row, with frozen clocks and fakes only. Assert `EvidenceChanged` (lead and Evidence ids) and `EventClosed` on the event bus. Then the head bumps, and `nextron-engine/README.md` story docs.

**Acceptance Criteria:**
- Given a seeded DB with the excluded and due Leads of the matrix, when the `recheck_leads` tick runs, then exactly the due Leads get `recheck_lead:<lead>:<local date>` jobs, with the right modes, on the bulk lane.
- Given a full re-check where all pages are unchanged, when the chain runs to completion, then `ScriptedLlm.calls` is empty for `extract_signals`, and the cited Evidence has only `last_confirmed_at` changed.
- Given the AI budget at 100%, when `recheck_lead` runs, then it returns `DEFERRED` and the crawler, board and LLM fakes recorded no call.
- Given a Lead re-checked today, when its page is opened, then it shows "Last re-check: <date>", and any superseded Evidence is labeled "superseded" with a link to its replacement.

## Implementation Notes

- **Use cases and the inline chain.** The unit of work lets an entry point call only one
  `@use_case`, and only a running use case may open `uow.transaction()`. So `Rechecks.refresh` is
  the `recheck_lead` job's `@use_case`, and the stages it runs inline were split: `WebsiteCrawls.
  enrich_lead` (still the `@use_case`) now delegates to a plain `crawl_lead(..., mode=)`, and
  `JobBoards.check_job_boards` to a plain `check_boards(...)`; both run "inside the caller's
  running use case, with no transaction open", like `PlaceSearches.page` (Story 8.3). `refresh`
  calls those. Behaviour of the existing jobs is unchanged.
- **Domain.** `nextron/domain/recheck.py`: `RecheckMode`, `RECHECK_LEADS_JOB`,
  `RECHECK_LEAD_JOB`, `RECHECK_LEADS_PERIOD` (24 h), `RECHECK_LEAD_BUDGET_CLASS`, `MODE_FIELD`,
  `mode_due` (exactly N days is due; naive times and periods below 1 raise `ValueError`) and
  `eligibility_allows`. `CrawlMode` is in `nextron/domain/crawl.py`.
- **Data.** Migration `0055_8_7_crawl_run_mode` (down `0054_2_16_signal_inbox`) rebuilds
  `crawl_runs` with `batch_alter_table(recreate="always", copy_from=<the table at 0054>)`, adding
  `mode` VARCHAR(16) NOT NULL DEFAULT 'initial' with `ck_crawl_runs_mode`. A plain `ALTER TABLE
  ADD COLUMN ... CHECK` was tried first but leaves the CHECK where the schema contract can't read
  it, so the rebuild follows Story 3.2's pattern. Owners (`research`) and PII (`none`) updated.
  `crawl_repo`: `insert_run(mode=initial)`, `StoredCrawlRun.mode`, `latest_run`/`run_count` ignore
  `recheck_jobs`, `last_hiring_check`, `last_recheck`, `recheck_candidates() -> {lead:
  RecheckFacts(last_full_at, last_hiring_at, last_run_at)}` (one grouped query). Other
  `latest_run` callers checked: `queries.research.latest_crawl_run` (Lead page outcome) and
  `render_pages`' plan both want the last real crawl; `run_count` keys `extract_lead` jobs, and a
  `recheck_full` run still increments it.
- **Selection pool.** `queries.discovery.recheck_pool(excluded_stages)` (leads repo
  `recheck_pool`, reading `pipeline_stage` read-only as `listed` does) gives the not-merged Leads
  with a website and an allowed stage; `Rechecks._skip_reason` applies every rule (also used when
  the job runs). Due Leads are sorted, then walked through `_skip_reason` until the limit, and all
  gate calls happen before the first enqueue.
- **Unchanged pages.** `WebsiteCrawls._apply` reads each page's stored row before the upsert;
  pages whose hash is unchanged go to `_confirm_unchanged`, which confirms every live Evidence of
  the Lead with that `source_url` at `max(last_confirmed_at, fetched_at)`. Extraction already
  sends only pages changed since its last ok run, so an all-unchanged re-check makes no
  `extract_signals` call (it records a `no_pages` run).
- **Tier events.** `Scoring.rescore` (scored path only) and `retier` append the `events` row
  through `_record_tier_change` when the stored Tier differs (none included); a disqualified
  rescore appends none (the Pipeline's close records its own event).
- **Wiring.** `handlers.recheck_leads`/`recheck_lead` (`RECHECK_LEAD_RETRIES = 3`; a bad `mode`
  is `InvalidJob`); `build_job_types(rechecks=)` with a raising `_no_rechecks` default; both job
  types bulk-only in `JobGroup.RECHECK`, `recheck_lead` carrying the `# Story 8.1:
  budget_class=BudgetClass.BLOCKABLE` line; `build_triggers` registers `Trigger.RECHECK_LEADS`
  last; `build_app` builds `Rechecks(clock, meter, crawls, job_boards, queue, compliance)` and
  keeps it on `app.state.rechecks`.
- **Lead page.** `lead_context(history=, last_recheck=)`; the header shows "Last re-check: <date>"
  or "never"; superseded rows (`superseded_rows`) are listed under the Evidence table with
  `id="evidence-<id>"`, labeled "superseded", each linking to its replacement's anchor.
- **Choices within the spec.** The `recheck_lead` job also carries the concurrency key
  `domain:<web_domain>` (one site crawled by one job at a time, as `enrich_lead`). "Last crawl"
  for ordering is the last run of any mode. The re-validation in `refresh` reuses every selection
  rule (suppressed domain and Primary eligibility too), the conservative reading.
- **Tests.** `tests/unit/test_recheck_domain.py`, `tests/app/test_recheck.py` (every matrix row
  and the repo filters), Lead page tests in `tests/unit/test_leads_page.py`, the 0055 migration
  test in `tests/unit/test_migrations.py`, the job contracts, health groups and the AD-16
  `research.recheck` scenario. Known limitation logged in `deferred-work.md`: a JavaScript page
  that Story 2.6 rendered always looks changed on a re-crawl (raw vs rendered hash).
- **Verification fixes.** `rechecks_enqueued` logged a `created` extra, which `logging` refuses
  (a reserved `LogRecord` attribute); it is now `enqueued`. Test scaffolding in
  `tests/app/test_recheck.py`: a Lead's `web_domain` is cleared together with a `place_id`
  (`ck_leads_identity_key`), settings are set one use case per unit of work, the LLM calls are
  cleared before the changed-page re-check, and the selection test now also excludes a Primary
  failing `asked_not_to_be_contacted` (with `recontact_not_allowed`). Its "unchecked run" Lead is
  due in mode `jobs`: a `recheck_jobs` run with no board check isn't a hiring check, so the last
  one is the 10-day-old run (the spec's rule; the test had expected it not due). Story 2.9's
  identical-quote test now expects the unchanged about page's Evidence confirmed too (the new
  unchanged-page confirm), and the lifespan tests count the ninth trigger, `recheck_leads`.
- **Review patches (pass 1).** A: a jobs-mode `refresh` reuses the Lead's unchecked
  `recheck_jobs` run (`crawl.unchecked_jobs_run`: `board_errors` NULL) instead of inserting
  another; `crawl.last_recheck` and `recheck_candidates`' last-run fact leave unchecked
  `recheck_jobs` runs out (`_COUNTED`). B, C: `enqueue_due` skips, before the limit counts it, a
  Lead with a live `recheck_lead` job of any date or a live `enrich_lead` job, through the new
  `queries.jobqueue.has_live_job(job_type, scope)` (`jobs.live_in_scope`: type plus key prefix
  `<type>:<scope>:`, `LIVE_WHERE`); a second same-day tick now picks 0. D: `SupersededRow.linked`
  is true only when the replacement is a rendered live or superseded row; otherwise "replaced by
  newer Evidence" is plain text. G: the `recheck_lead` docstring says `may_retry` matters only in
  mode `full`. L: long docstring lines wrapped, two blank lines before `Rechecking`, new imports
  sorted. N: `_confirm_unchanged` confirms only Evidence whose `source` is `website`. P:
  `recheck_full_days` and `recheck_jobs_days` are 1 to 3650 (`MAX_RECHECK_DAYS`). Tests: R (a
  refused oldest Lead leaves the limit of 2 to the next two), S (full mode passes `may_retry`),
  T and N (a retracted, a superseded and a Signal Inbox row keep `last_confirmed_at`), A, B, C,
  D, the schema maximum; `Bench.tick` marks the world's queued `enrich_lead` jobs done first
  (their crawl ran outside the queue), and so does the AD-16 `research.recheck` tick scenario.
  README updated.

## Spec Change Log

## Review Triage Log

Review pass 1 had 25 raw findings: Blind Hunter (BH) 13, Edge Case Hunter (EC) 8, Verification Gap (VG) 3 gaps plus 1 other. They dedupe to 20 entries: 11 patched, 0 deferred and 9 rejected.

| # | Findings | Verdict | Evidence | Route |
|---|---|---|---|---|
| A | BH1, EC3 | medium | `refresh` commits the `recheck_jobs` run before `check_boards`. A deferral (`False`) or a raise leaves a count-less run, each retry adds another, and the runs move `last_recheck` and the order. | patch: reuse the Lead's unchecked `recheck_jobs` run (`board_errors` null) on retry; `last_recheck` ignores unchecked `recheck_jobs` runs |
| B | BH2, EC2 | medium | Keys are per local date and the queue dedups only live same-key jobs, so a deferred or still-queued job from yesterday gets a second job today. | patch: `enqueue_due` skips a Lead with any live `recheck_lead` job |
| C | BH3, EC1 | medium | A new Lead whose first `enrich_lead` is still queued has no run, so it is due `full` and is crawled twice. The first crawl's work is also labelled a re-check. | patch: skip a Lead with a live `enrich_lead` job (the spec still makes "no crawl" due `full`) |
| D | BH5, EC6, VG other | low | A replacement that was later retracted is neither live nor superseded, so `#evidence-<id>` has no target. | patch: link only when the replacement row is rendered; otherwise plain text |
| E | BH4 | low | The gate walk inside one `BEGIN IMMEDIATE` grows with the number of refused due Leads. A one-operator dataset (hundreds of Leads) makes this negligible, and the fix (caps or SQL push-down) adds complexity. | reject |
| F | BH6 | low | `last_hiring_check` is used only by tests. The tests pin the same definition `recheck_candidates` uses, and there is no runtime harm. | reject |
| G | BH7 | low | `check_boards` never raises for a board error (Decision 12 of 2.11), so `may_retry` is moot in jobs mode, but the handler docstring implies otherwise. | patch: fix the docstring |
| H | BH8 | low | The first Tier writes `None→tier_x` with cause `lead_rescored`, which is what the frozen spec says ("including from none", cause `lead_rescored`). | reject: would change the approved spec |
| I | BH9 | false | With `jobs_days ≥ full_days` every re-check is `full`, and the full chain runs `check_job_boards` too, so hiring checks still happen. | reject |
| J | BH10 | low | The unused confirmation count is cosmetic. | reject |
| K | BH11 | low | The Lead page's "Last re-check" uses the same `day()` (UTC) as the page's other dates; it is consistent within the page. The always-true guard is harmless. | reject |
| L | BH12 | low | Docstring lines over 100 characters, a single blank line before a class, and unsorted imports. Each is a direct correction. | patch |
| M | BH13 | low | No CSS for the new elements; the text label "superseded" carries the meaning. Unlikely to bother anyone. | reject |
| N | EC4 | low | `_confirm_unchanged` confirms any live Evidence with that `source_url`, including Signal Inbox Evidence (quoted from pasted text, not the page). | patch: confirm only Evidence whose source is the crawled website |
| O | EC5 | maybe-false | Whether Evidence `source_url` can hold a redirect's final URL that differs from `crawled_pages.url`. If true, the Evidence is only not re-confirmed: low. | reject (would be low) |
| P | EC7 | low | A huge day count overflows `timedelta` and breaks every tick. Adding a maximum is a direct correction. | patch: `maximum(3650)` on both day settings |
| Q | EC8 | low | Won and deal-stage Leads without a live Enrollment are re-checked. The frozen selection lists exactly the excluded stages, and re-research sends nothing. | reject: would change the approved spec |
| R | VG1 | gap | No test makes the daily limit bind while one of the oldest due Leads is refused. | patch: add the test |
| S | VG2 | gap | Passing `may_retry` through to `crawl_lead` in full mode is untested. | patch: parametrized test with a retryable crawler failure |
| T | VG3 | gap | Nothing proves that superseded or retracted Evidence on an unchanged page keeps its `last_confirmed_at`. | patch: extend the unchanged-page test |

## Verification

**Commands** (run from `nextron-engine/`, with `-n 2` at most):
- `uv run pytest -q -n 2 tests/app/test_recheck.py tests/unit tests/contracts`: expected all pass.
- `uv run lint-imports`: expected all contracts KEPT.
