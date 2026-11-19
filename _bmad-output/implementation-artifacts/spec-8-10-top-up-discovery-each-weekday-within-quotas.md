---
title: "Story 8.10: Top up discovery each weekday within quotas"
type: 'feature'
created: '2026-11-19'
status: 'done'
route: 'dispatch'
baseline_commit: '8819f875c6ff3c208173850ea9b41fbf15682d6f'
review_loop_iteration: 0
story_key: '8-10-top-up-discovery-each-weekday-within-quotas'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-8-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-8-1-keep-supply-jobs-inside-quotas-and-the-ai-budget.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Discovery (8.3 Places, 8.4 Companies House) runs only when the Operator presses a button, always searches the same queries from page 0, and nothing paces the free Places quota across the month or stops once the day has enough new Leads.

**Approach:** A top-up stage that the weekday `daily_selection` chains in its own transaction enqueues one dated `discover_places` and (UK) `discover_registry` job per active Campaign, unless the Campaign's ready pool is already sufficient. A scheduled run takes the next query/location pair in rotation, spends at most its share of today's Places allowance, and stops at the daily Lead target. The Discovery page and health panel show today's numbers.

## Boundaries & Constraints

**Always:**
- The epics' Story 8.10 ACs are binding, as narrowed by the decisions below.
- **Stage.** `DiscoveryTopUp.chain(local_date, selection_status) -> tuple[Enqueued, ...]` runs inside the caller's open transaction (`RuntimeError` otherwise) and enqueues only when `selection_status` is `ran` or `away` and `local_date` is Monday–Friday. Per active Campaign (`setup_queries.campaign_active`): pool check first; else `discover_places:<campaign ULID>:<YYYY-MM-DD>` when its Segment has Places queries and it has at least one configured location, and `discover_registry:<campaign ULID>:<YYYY-MM-DD>` when its Market is GB and enabled and its Segment has registry queries. Payload `{v: 1, campaign_id, scheduled_for: "<YYYY-MM-DD>"}`. Bulk lane, existing concurrency keys. A second call the same day adds nothing (idempotent keys).
- **Pool.** A `ReadyPool` protocol (`ready_leads(campaign_id) -> int`, `capacity_share(campaign_id, local_date) -> int`) is injected. When it is present and `ready_leads >= discovery_pool_days * capacity_share`, no job is enqueued for that Campaign and a `discovery_runs` row is written: source `places`, status `pool_sufficient`, `ready_pool = n`, `scheduled_for`, started and finished `now`, counts 0. With no provider, the pool is never sufficient.
- **Rotation.** A Places pair is (one Segment Places query, one Campaign location) in seed × setting order; a registry pair is one registry query (each keyword with all SIC codes; a SIC-only query when there are no keywords), location NULL. A pair's `query_hash` is the existing hash of that single query. Its last run is the latest `started_at` of a `finished` row with the same source, `segment_id`, location and `query_hash`. Never-run pairs first in pair order, then oldest last run; pairs whose last run is within `discovery_requery_days` × 24 h of `now` are skipped. No due pair → no row, log `discovery_nothing_due` (source, campaign_id), handler succeeds.
- **Places allowance.** `places_day_allowance(quota, used_before_today, weekdays_left) = max(0, quota - used_before_today) // weekdays_left` (0 when `weekdays_left` is 0). `weekdays_left` counts Monday–Friday from `local_date` to the end of its local calendar month, today included. `used_before_today` = month-to-date Places Enterprise units minus those recorded since the local day's start (`sender_timezone`). Before every Places request of a scheduled run (search page, or a scheduled registry run's website lookup), the run requires `requests_today < allowance`. When it isn't, a Places run ends `paced` (handler succeeds, no defer); a registry website lookup is left undecided (`website_lookup_at` NULL) and the run continues. The existing quota and AI-budget checks still run.
- **Target.** Leads created today = sum of `created` over `discovery_runs` rows started since the local day's start (every source and kind). A scheduled run checks it before each page and each candidate or hit; at or over `discovery_daily_lead_target` it ends `target_reached` (handler succeeds).
- **Display.** The Discovery page and the health panel show: Leads created today / target; Places requests today / today's allowance; the Discovery group's last successful runs (8.1's groups, health panel). The Discovery page adds "Pool sufficient: n Leads ready" per Campaign for today's `pool_sufficient` rows, and its runs table shows the new statuses.
- Manual runs (the 8.3 and 8.4 forms) keep today's behaviour: no rotation, no allowance, no target.
- Weekdays and the day's start use `sender_timezone`.

**Never:**
- No email of any kind, no `daily_selection` job type, trigger or selection logic (8.9), no aggregator job, setting or code (8.5/8.6), no new dependency.
- No Places candidate data, query text or location text in payloads, logs or `events`.

### Decisions made overnight (pending user review)
1. **Missing predecessors.** `daily_selection` (8.9) does not exist on any branch, and Tier, Enrollment and capacity (2.15, 4.8, 4.10) are only on `main`, not on this track's base. 8.10 ships the stage for 8.9 to call in its transaction and the `ReadyPool` protocol for the merge to wire; `build_app` wires no provider and registers no trigger. Until 8.9 lands, scheduled discovery does not fire. Deferred items are logged for both.
2. **Aggregator.** `job_aggregator_enabled` and `discover_aggregator` don't exist (8.6 not built), so no aggregator job is enqueued; logged for 8.6.
3. **Allowance exhausted** ends the run `paced`, not `deferred`: an hourly retry cannot help before tomorrow.
4. **Settings** (Budget and limits): `discovery_pool_days` int 1–60 default 5; `discovery_requery_days` int 1–365 default 30; `discovery_daily_lead_target` int 0–1000 default 60 (0 stops scheduled creation).
5. **Migration** `0040_8_10_discovery_topup`: `discovery_runs` gains nullable `scheduled_for` (ISO date text) and `ready_pool` (integer ≥ 0, set only for `pool_sufficient`), and statuses `target_reached`, `paced`, `pool_sufficient`. PII class `none`.
6. **Rotation counts only `finished` runs,** so a pair cut short (deferred, paced, target reached) comes back first next time. Manual multi-query runs hash differently and don't count toward a pair.
7. **8.4 deferred items.** Registry rotation per query closes "always page 0" in part (each keyword gets its turn). Website-lookup retry and officer resume stay deferred.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected |
|---|---|---|
| Weekday, `ran` | 2 active Campaigns (one GB, one US) | Places job for each, registry job for the GB one; keys dated |
| Saturday or status `weekend`/`no_capacity` | | nothing enqueued |
| Pool sufficient | ready 30, share 6, pool days 5 | no job; `pool_sufficient` row, page says "Pool sufficient: 30 Leads ready" |
| Allowance | quota 1000, 0 used, 22 weekdays left | 45 |
| Allowance | 10 left, 20 weekdays left | 0: run ends `paced`, no request made |
| Shared allowance | allowance 3, 2 requests already today, fake offers 3 pages | one page requested, run `paced` |
| Rotation | pair A ran 40 days ago, B 10 days ago, C never | C, then A; B skipped |
| All pairs recent | | no row, nothing fetched |
| Target | target 60, 59 created today, 3 new candidates | one Lead created, run `target_reached` |
| Registry lookup over allowance | | Lead created, `website_lookup_at` NULL |

</frozen-after-approval>

## Code Map

All paths under `nextron-engine/`.
- `nextron/domain/place_discovery.py:77` `RunStatus` (add 3 statuses; the model's `STATUS_CHECK` follows), `query_hash`, `location_hash`. New pure module `nextron/domain/discovery_topup.py`: `is_weekday`, `weekdays_left(local_date)`, `day_start_utc(local_date, tz)`, `places_day_allowance`, `pool_sufficient(ready, pool_days, share)`, `next_pair(pairs, last_runs, now, requery_days)`, payload field `SCHEDULED_FOR_FIELD`.
- `nextron/app/discovery/place_ingest.py` `PlaceDiscovery`: add `run_scheduled(campaign_id, local_date) -> bool` reusing `_start`/`_pages`/`_may_continue`/`ingest_candidate` with one query and the allowance/target checks; `configured_locations()`, `_market_country`. Keep `run` and `request_run` behaviour.
- `nextron/app/discovery/registry_ingest.py` `RegistryDiscovery`: `run_scheduled(campaign_id, local_date)`; `_website` (line ~465) gains the allowance check for scheduled runs only.
- `nextron/app/discovery/places.py` `PlaceSearches` (8.1 lock, `refusal()`): unchanged; requests today read from `api_usage` (see `nextron/app/ops/api.py` meter / `nextron/db/repos` usage reader; add a read in `queries`).
- New `nextron/app/discovery/topup.py` `DiscoveryTopUp(clock, queue, ready_pool=None)`, exported from `nextron/app/discovery/api.py` docstring list.
- `nextron/db/models/discovery.py` (columns + checks), `nextron/db/repos/discovery/runs.py` (insert with `scheduled_for`/`ready_pool`; `last_finished(source, segment_id)`; `created_since(at)`), migration `nextron/db/alembic/versions/0040_8_10_discovery_topup.py` (rev `0040_8_10_discovery_topup`, down `0039_8_4_registry_discovery`; SQLite batch rebuild of the checks), `nextron/db/owners.py`, `nextron/db/pii_registry.py`.
- `nextron/domain/settings_schema.py` -- the three settings next to `PLACES_MAX_PAGES_PER_RUN` (line ~189, ~1185).
- `nextron/jobs/handlers.py:440,475` -- accept either the manual payload or `{v, campaign_id, scheduled_for}` (valid ISO date) and call `run_scheduled`; `False` → `DEFERRED`.
- `nextron/app/queries/discovery.py` -- `today(now, tz) -> DiscoveryToday(created, target, places_requests, allowance, pool_sufficient rows)`.
- `nextron/web/discovery_page.py`, `templates/discovery.html`, `nextron/web/health_page.py` + its template -- today panel and lines.
- `nextron/main.py` `build_app` -- build `DiscoveryTopUp` (no provider), keep `app.state.discovery_topup`; docstrings.
- Tests to copy: `tests/app/test_place_discovery.py`, `tests/app/test_registry_discovery.py` (worlds, fakes `ScriptedPlaces`/fake registry), `tests/app/test_supply_budget.py`, `queue_on` in `tests/app/test_leads.py`. Head bumps per track rules (`test_persistence_contracts`, `test_migrations`, `test_serve_process`, `test_source_tree FILES`), settings lists, `test_places_storage_terms` importers if new modules import Places.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/discovery_topup.py`, `place_discovery.py`, `settings_schema.py` -- rules, statuses, settings -- unit tests incl. the AC's 45 and 0 examples and weekday/month-end cases
- [x] migration, model, repo, owners, pii -- Decision 5
- [x] `nextron/app/discovery/topup.py`, `place_ingest.py`, `registry_ingest.py`, `handlers.py` -- stage and scheduled runs
- [x] `nextron/app/queries/discovery.py`, web page, health panel, `main.py`
- [x] `tests/app/test_discovery_topup.py` (new) -- every matrix row, the in-transaction guard, idempotent second call, payload validation, and a scan proving no query/location text reaches payloads, logs or `events`
- [x] `nextron-engine/README.md` -- Story 8.10 section; `deferred-work.md` -- Decisions 1, 2, 7 items

**Acceptance Criteria:**
- Given the stage called inside a transaction on a weekday with status `ran`, when it commits, then the dated jobs exist; when the transaction rolls back, then none exist.
- Given today's runs, when the Discovery page and health panel render, then they show Leads created today against the target, Places requests against today's allowance, and the last successful `discover_places` and `discover_registry` runs.

## Implementation Notes

Built as specified, with migration `0040_8_10_discovery_topup` (down `0039_8_4_registry_discovery`) and no new dependency. Implementer choices and flags for review:

- **Domain.** New `nextron/domain/discovery_topup.py`: `is_weekday`, `weekdays_left`, `local_date_of`, `day_start_utc`, `places_day_allowance`, `pool_sufficient`, generic `next_pair(pairs, last_runs, now, requery_days)` (keys in pair order; never-run first, then oldest, a pair is due at exactly `requery_days` × 24 h), `SCHEDULED_FOR_FIELD`, `TOP_UP_STATUSES` (`ran`, `away`) and the settings' bounds. `RunStatus` gains `target_reached`, `paced`, `pool_sufficient`. Settings `discovery_pool_days`, `discovery_requery_days`, `discovery_daily_lead_target` (Budget and limits, labels name no Places so the 8.3 metering contract holds).
- **Migration / model.** `discovery_runs` rebuilt from 0039's exact table: nullable `scheduled_for` (`Date`, ISO text) and `ready_pool`; status check widened; **`ck_discovery_runs_location` relaxed** to `(source = 'places' AND status <> 'pool_sufficient') = (location IS NOT NULL)`, because a `pool_sufficient` row records no run and has no location (the spec's row lists no location); new `ck_discovery_runs_scheduled_for` (a `pool_sufficient` row has a date) and `ck_discovery_runs_ready_pool` (set exactly on `pool_sufficient`, ≥ 0). owners/pii updated (class `none`). The pool row's `query_hash` is the hash of the Segment's whole Places query list and its `country_code` the Market's country.
- **Places requests today needed a new meter read.** The metering contract allows SQL on `api_usage` only in `nextron/db/metering.py` and only `main` may import it, so `UsageMeterPort` gained `units_since(provider, sku, since) -> int` (implemented in `UsageMeter`, `DoctorMeter` and the `InMemoryUsageMeter` fake). Ad-hoc test meters that don't implement it are fine unless they reach the pacing path.
- **Allowance.** `PlaceSearches.day_usage(Pace(local_date, tz)) -> PlacesDay(requests, allowance)`: `requests` = SKU units since the local day's start; `used_before_today` = month-to-date units minus today's units inside the UTC month (so a local day that starts in the previous UTC month never goes negative). The quota month stays UTC (1.13), `weekdays_left` uses the local month, as the spec says. `PlaceSearches.page(..., pace=None)`: with a `pace`, the allowance check runs **inside the same `PLACES_LOCK` and check transaction** as the budget check (after it), so it is atomic with the request; when used up it logs `places_search_paced` and raises the new `Paced` exception (no request). A budget refusal still wins (`None` → `deferred`).
- **Shared helpers.** New `nextron/app/discovery/daily.py`: `sender_timezone()` (UTC while unset, like ops), `local_today`, `created_today` (`runs.created_since(day start)`), `lead_target`, `target_reached`, and the `discovery_nothing_due` log name. A separate module avoids a `topup` ↔ `place_ingest` import cycle.
- **Scheduled Places run** `PlaceDiscovery.run_scheduled(campaign_id, scheduled_for)`: pairs = Segment Places queries × configured locations in the Campaign's Market's country (Market must be enabled), keyed `(location text, query_hash((query,)))`; `runs.last_finished(source, segment_id)` gives the latest `started_at` of `finished` rows by `(location, query_hash)`. Target is checked before each page and each candidate; `Paced` ends the run `paced`; `paced` and `target_reached` return `True` (job completes). Manual `run` passes `pace=None`, so no rotation, allowance or target.
- **Scheduled registry run** `RegistryDiscovery.run_scheduled`: pairs = `registry_searches` each hashed by the new `single_search_hash(keyword, codes)` (`registry_query_hash` of that one search), location NULL; target before each page and hit; `ingest_hit(..., pace=)` passes the pace to `_website`, where `Paced` leaves the lookup undecided (`website_lookup_at` NULL, `no_website` counted, as for a refusal). `run` and `run_scheduled` share the new `_run_started`.
- **Stage** `nextron/app/discovery/topup.py` `DiscoveryTopUp(clock, queue, place_searches, ready_pool=None)` — **signature deviates from the Code Map's `(clock, queue, ready_pool=None)`**: it takes `PlaceSearches` so `today()` can read the allowance. `chain` raises `RuntimeError` outside a transaction, skips (log `discovery_topup_skipped`) unless `ran`/`away` on Mon–Fri, then per active Campaign (stored order, by key): pool check, then `discover_places` (Segment has Places queries, Market enabled, a location in its country) and `discover_registry` (Market GB and enabled, registry searches). Idempotency is the queue's live-key rule; additionally a Campaign's `pool_sufficient` row is written once per date (`runs.has_pool_row`). Logs `discovery_topup_chained` (`date`, `jobs_created`; `created` would clash with `LogRecord.created`).
- **Display.** `DiscoveryTopUp.today() -> DiscoveryToday(local_date, created, target, places_requests, places_allowance, pools)` — **lives on the app object, not in `queries.discovery`** as the Code Map says, because queries can't reach the usage meter; `queries.discovery` gained `created_since(at)` and `pool_rows(date)`. `create_app(..., discovery_topup=None)` (optional, keyword): with one, the Discovery panel shows a "Today" list ("Leads created today: n of target", "Places requests today: n of allowance allowed", "<Campaign>: Pool sufficient: n Leads ready") and the health panel a "Discovery today" list (first two lines) above the last-success groups (8.1's Discovery group already lists `discover_places`/`discover_registry`). Runs table shows the raw new statuses. `tests/dashboard.Dashboard(with_topup=True)` opts in; the default Dashboard has none, so existing page tests are unchanged.
- **Handlers.** A `discover_places`/`discover_registry` payload holding `scheduled_for` must hold a valid ISO date (else `InvalidJob`) and calls `run_scheduled`; `False` → `DEFERRED`. Manual payloads unchanged.
- **`main.build_app`** builds `DiscoveryTopUp(clock, queue, place_searches)` with no `ReadyPool`, passes it to `create_app` and keeps `app.state.discovery_topup`; no trigger (Decision 1).
- **Tests.** `tests/unit/test_discovery_topup_rules.py` (weekday, month-end, leap day, zones, 45/0 allowance, pool, rotation incl. tie and exact boundary, settings). `tests/app/test_discovery_topup.py` (42): every matrix row, in-transaction guard, rollback, idempotent second call, paused/no-location Campaigns, pool short, budget refusal defers with no row, `not_today`/paused skip, manual run unaffected, registry rotation/target/paced lookup, handler payloads, `today()` incl. a non-UTC zone, both pages through `Dashboard`, and a leak scan of `jobs.payload`, `events` and discovery/jobqueue/jobs logs for query, location, Maps and company text. Updated: head constants (`test_persistence_contracts`, `test_migrations`, `test_serve_process`), the chain-probe revisions (0041/0042), `test_source_tree` `FILES`, `test_settings_contracts` Key list, the 0039 migration test (`inactive` no longer last) plus a new 0040 migration test. Also updated for the three new settings: `test_settings_schema` (declared order, schema probe), `test_settings_page` (field order), `test_telegram_secrecy` (the whole Settings form); `test_place_discovery`'s page spy now forwards `pace=`.
- **Review patches (triage rows 1, 2, 7, 12–16).** Row 1: `PlaceSearches.day_usage` now takes `used_before_today` as the units recorded between the local month's start (`day_start_utc(local_date.replace(day=1), tz)`) and the local day's start (two `units_since` reads), so it spreads the same local month `weekdays_left` counts; `requests` unchanged; the budget's quota check still holds the UTC month; tests for America/New_York on 30 Nov 20:00 (allowance 400 of 1,000 with 600 used in Nov) and Asia/Tokyo on 1 Dec 03:00 (November's units ignored: 1000 // 23). Row 2: new `runs.ran_scheduled(source, campaign_id, scheduled_for)`; both `_start_scheduled`s skip with reason `already_ran` (existing skip log, no row) when a run for that Campaign, source and date already ended `finished`, `paced` or `target_reached`; README and `topup`/`place_ingest`/`registry_ingest` docstrings corrected; tests for both sources and for a `paced` run. Row 7: `chain` first works out the Campaign's jobs and applies the pool check (and writes the row) only when at least one would be enqueued; test: with only a GB location the US Campaign gets no row and its pool is never asked, the UK Campaign (both kinds) gets exactly one row and no jobs. Row 12: `settings_schema` import order; `models/discovery.py` docstring reflowed to 100 columns. Row 13: the 0040 migration test now matches each rejection to its check name (`status`, `location`, `scheduled_for`, `ready_pool`), incl. `ready_pool` on a non-pool row, a pool row without `scheduled_for`, a pool row with a location (Places and Companies House). Row 14: the skip test runs over both sources (`not_today`, `campaign_not_active`, `unknown`, and a non-GB Campaign for the registry) and asserts the logged reason; the budget-refusal test runs over both (registry: the AI budget spent); the handler test asserts `DEFERRED` from `discover_registry` (refusals are now exercised before a finished run, since a finished run makes later same-day runs skip). Row 15: `tests/app/test_metering.py` tests the real `UsageMeter.units_since` (at/before/after `since`, failed call, other sku, NULL sku, other provider). Row 16: the real-wiring health-panel test in `tests/unit/test_runner_lifespan.py` asserts "Leads created today: 0 of 60".
- **Gate fix.** The first full-suite gate failed `tests/unit/test_place_discovery_domain.py::test_the_counts_are_seen_then_each_outcome`, which pinned `RunStatus`'s values; it now lists the three Story 8.10 statuses.

### Decisions made overnight (pending user review) — implementer additions
8. **Scheduled-run skips write no row** (unknown Campaign/Segment, `scheduled_for` not today's local date, Campaign no longer active), unlike a manual run's `stopped` row: there is no pair chosen yet, and a job left from an earlier day must not run a second day's search.
9. **`pool_sufficient` rows have no location** (location check relaxed for that status only).

## Spec Change Log

## Review Triage Log

Review pass 1: 3 layers (blind, edge, verification gap), 28 raw findings, 18 after dedup; 8 patched, 0 deferred, 10 rejected (1 false).

| # | Layer | Finding | Verdict | Route |
|---|---|---|---|---|
| 1 | blind, edge, verif | `day_usage` takes month usage from `now`'s UTC month but `weekdays_left` from the local month: west of UTC on the last local evening the allowance becomes the next UTC month's whole quota; east of UTC on the 1st the old month's leftover is divided by the new month's weekdays; the month-start branch is untested | medium | patch: count `used_before_today` as the units between the local month's start and the local day's start (same local month as `weekdays_left`, as the spec states); the hard quota check still holds the UTC month; tests for both boundary zones |
| 2 | blind, edge | "never queues twice in one day" holds only while the dated job is live; a re-chain after it finished runs a second pair | low | patch: a scheduled run skips (log, no row) when a row for the same Campaign, source and `scheduled_for` already ended `finished`, `paced` or `target_reached`; correct the README/docstring claim |
| 3 | blind | a pair cut short by pacing or the target is searched again from page 1 next day | low | reject: Decision 6 (only `finished` counts) is the approved design; would change the approved spec |
| 4 | blind, edge | manual runs hash all queries, so they affect rotation only for single-query Segments | low | reject: Decision 6 states it |
| 5 | blind | a scheduled Places run whose Market was disabled logs `discovery_nothing_due` instead of a skip reason | low | reject: log wording only, nothing runs |
| 6 | edge | `_places_pairs` checks `_enabled_market(country)`, not the Campaign's own Market | false | `markets.country_code` is unique (`uq_markets_country_code`), so the country's Market is the Campaign's |
| 7 | blind, edge | the pool check runs before the queries/Market/location checks, so a Campaign that would get no discovery still gets a `pool_sufficient` row and page line (stored as source `places` even when only Companies House applies) | low | patch: work out which jobs the Campaign would get first; apply the pool check (and write the row) only when at least one would be enqueued |
| 8 | blind | no unique constraint or index for `pool_sufficient` rows; repeat log line | low | reject: `chain` runs inside the caller's `BEGIN IMMEDIATE` transaction, so check-then-insert is serialized; the table is small |
| 9 | blind | the health panel has no guard if `today()` raises; the Today section shows no date; allowance shown while nothing triggers | low | reject: `sender_timezone` and quotas are schema-validated; the figures are accurate; a guard adds untested branches |
| 10 | blind | paced website lookups are never retried | low | reject: already deferred (8.4 #12, Decision 7) |
| 11 | blind, edge | Leads created today are attributed by run start time; concurrent Places and registry runs can overshoot the target slightly | low | reject: the spec defines the count by run start; scheduled runs start after 05:00; overshoot is bounded by one candidate per concurrent run |
| 12 | blind | `settings_schema.py` import out of order; a `models/discovery.py` docstring line over 100 columns and a stray line | low | patch |
| 13 | blind | no test that migration 0040's new checks reject bad rows (`ready_pool` on a non-pool row, a pool row without `scheduled_for`, a pool row with a location); no UK-Campaign pool test | low | patch: add the check-constraint rejections to the 0040 migration test and a UK pool case |
| 14 | verif, blind | the scheduled registry start guards (unknown, not today, paused/non-GB, budget refusal with no row) and the registry handler's `DEFERRED` path are untested | medium | patch: parametrise the existing skip and budget-refusal tests over both sources and assert `DEFERRED` from the registry handler |
| 15 | verif | the real `UsageMeter.units_since` SQL is never tested on stored rows | medium | patch: add a `tests/app/test_metering.py` test (at/before/after `since`, matching sku, other sku, NULL sku) |
| 16 | verif | `build_app`'s wiring of `discovery_topup` into `create_app` is not asserted | medium | patch: assert "Leads created today" in the real-wiring health-panel test in `tests/unit/test_runner_lifespan.py` |
| 17 | verif | registry `_website` swallows `Paced` with no log of its own | low | reject: `PlaceSearches.page` logs `places_search_paced`; documented |
| 18 | edge | `capacity_share` 0 makes the pool sufficient with 0 Leads ready | low | reject: the AC's rule (ready >= days x share) holds literally; no capacity means no top-up is needed |

## Verification

**Commands:**
- `cd e:/Nextron-epic1/nextron-engine && uv run pytest -q -n 2 tests/contracts tests/app/test_discovery_topup.py tests/app/test_place_discovery.py tests/app/test_registry_discovery.py tests/app/test_supply_budget.py tests/unit/test_migrations.py` -- expected: pass
- `uv run lint-imports` -- expected: every contract kept
