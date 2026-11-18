---
title: "Story 8.3: Turn Places candidates into deduplicated, contactable Leads"
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: 'de1f9cd4a1be11b3b60ec26182310ced53666d21'
review_loop_iteration: 0
story_key: '8-3-turn-places-candidates-into-deduplicated-contactable-leads'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-8-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-8-2-places-search-adapter-that-keeps-only-what-the-maps-terms-allow.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Story 8.2 can fetch Places search pages, but nothing turns a candidate into a Lead. Discovery must add only new, lawful organizations, named and keyed from their own website (never from Maps data), and feed each into the research chain.

**Approach:** A "Run Places discovery" form on a new Discovery page enqueues a `discover_places` bulk job per (active Campaign, location). The job pages through the Campaign Segment's Places queries via `PlaceSearches.search_page`, and ingests each candidate in memory: Market check, dedup by `place_id`, own-website fetch through `CrawlerPort`, then `Discovery.upsert_lead` (AD-16 gate), coordinates and `enrich_lead` in the same transaction. Each run is recorded in a new `discovery_runs` table and listed on the page.

## Boundaries & Constraints

**Always:**
- The epics' Story 8.3 ACs are binding (`_bmad-output/planning-artifacts/epics.md`, "Story 8.3").
- Lead name and `web_domain` come only from the fetched site: domain = `domain.identity.registrable_domain(final_url)`; name = `og:site_name`, else `<title>`, whitespace-collapsed, control/format chars removed, cut to `leads.MAX_NAME_LENGTH`; if neither yields a valid name, the registrable domain is the name. `display_name` is never read by ingest code.
- Before any fetch: a `website_uri` that is missing, fails `fetch_policy.check_url`/`is_denied`, or keys to a shared platform (`leads.is_shared_platform`) → "no own website", nothing fetched. Robots.txt is read first (`research.api.robots_rule`, `MIN_INTERVAL_S`); a disallowed homepage, a fetch `AdapterError`, a non-2xx, a non-HTML answer, or a final URL on a denylisted/shared-platform domain → "no own website".
- Per-candidate order: Market check → `place_id` match (survivor via `merged_into`; no fetch; "merged") → website checks/fetch → one `uow.transaction()`: `upsert_lead(LeadIdentity(name, segment_id, web_domain, place_id=…, country_code=…), LeadSource.PLACES)`; created → set `place_lat`/`place_lng`/`place_coords_at = now` together, and `enrich_lead` enqueued (`upsert_lead` now enqueues it for `PLACES` as for `CSV`); blocked → "suppressed"; matched → "merged". The run's counts update in that same transaction. All network I/O is outside transactions.
- `place_searches.search_page` returning `None` → the run ends `deferred` and the handler returns `DEFERRED`. Every `discover_places` job carries concurrency key `domain:places.googleapis.com`, so Places requests never run concurrently (closes the 8.2 deferred quota race for this caller).
- Payload: `{v: 1, campaign_id, location, country_code}` only (Operator data). Never `website_uri`, `display_name`, coordinates or page tokens in a payload, `events`, `discovery_runs`, or logs.
- Extend `tests/contracts/test_places_storage_terms.py`: `PLACES_IMPORTERS` gains every new importer; its planted run drives a full `discover_places` job (created, merged and blocked candidates) and scans every text cell (incl. `jobs.payload`, `events`, `discovery_runs`) and log record.
- AD-16 contract (`tests/contracts/test_ad16_callers.py`) gains a Places-ingest scenario proving the gate precedes the insert.

**Never:**
- No `budget_class`, notifications or health issue for quotas (Story 8.1); no Companies House or aggregator discovery; no automatic or scheduled Places run (8.10); no nav entry change.
- No Places name, address, phone, review or `website_uri` text stored anywhere; no column fed from a `PlaceCandidate` other than `place_id`, `place_lat`, `place_lng`, `place_coords_at`.
- No new dependency (HTML title parsing uses stdlib `html.parser`).

### Decisions made overnight (pending user review)
1. **8.1 boundary.** Only 8.2's per-call seam is used; `deferred` = runner's existing `DEFERRED` (re-queued in 1 h). A re-run starts from page 1 (page tokens are never stored), creating a new run row.
2. **Pages.** `places_max_pages_per_run` (integer, Budget and limits, default 3, 1–3: Text Search returns at most 3 pages) caps the whole run: queries run in seed order, each paged until no token, stopping when the run's page total hits the cap. `query_hash` = SHA-256 hex (first 16) of the run's queries joined by `\n`.
3. **Locations setting.** `places_discovery_locations` (list, Engine section, default empty): each item `<location> | <CC>` (e.g. `Columbus, OH | US`), location 1–100 chars, CC in `domain.geo`/`COUNTRY_CODES`; a Rule refuses any other. Campaign's locations = items whose CC equals its Market's country code.
4. **Location hash** discriminator: first 16 lowercase hex chars of SHA-256 of `<location>|<CC>`; `domain.job._is_discriminator` accepts `[0-9a-f]{16}`.
5. **Page.** `GET /leads/discovery` (registered before `/leads/{lead_id}`, linked from the Leads list, `@personal_data(False)`, `Cache-Control: no-store`) and `POST /leads/discovery/places` (CSRF). Run statuses: `running`, `finished`, `deferred`, `failed` (an exception: recorded, then re-raised), `stopped` (Campaign no longer active at run time: nothing fetched).
6. PII class of every `discovery_runs` column: `none` (Operator settings, counts, ids).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected |
|---|---|---|
| New org | site title "Acme Law", `display_name` "Zed Maps Name" | Lead "Acme Law", place_id, domain, coords, stage `new`, `enrich_lead` queued; created +1 |
| No website / denylisted / shared platform / load fails | | no Lead; no_website +1; denylisted URL never fetched |
| place_id held by any Lead (incl. Lost/merged) | | no fetch, no Lead; merged +1 |
| Domain held by Lead without place_id | | place_id attached; merged +1 |
| Gate blocks | suppressed domain | nothing written; suppressed +1 |
| Country not an enabled Market | | no Lead; market_blocked +1 |
| Quota refuses page 2 | | page-1 Leads kept; run `deferred`; job `DEFERRED` |
| Double submit | live job exists | same job returned, no second job |
| Campaign not active / location not its Market's | POST | refused with message, nothing enqueued |

</frozen-after-approval>

## Code Map

All under `nextron-engine/`.
- `nextron/app/discovery/api.py:258-319` -- `upsert_lead`: enqueue `_enqueue_crawl` for `LeadSource.PLACES` too; add `@command record_place_coords(lead_id, lat, lng)` (sets all three, `now`).
- `nextron/app/discovery/places.py` -- 8.2 seam `PlaceSearches.search_page` (returns `None` when refused). Reuse, don't change behaviour.
- New `nextron/app/discovery/place_ingest.py` -- `PlaceDiscovery(clock, place_searches, crawler, discovery, queue)`: `@use_case request_run(campaign_id, location_index) -> Enqueued` (validates; enqueues `discover_places`), `@use_case run(campaign_id, location, country_code) -> bool` (False = deferred), `ingest_candidate(run, candidate, ...)`. Model the robots/home fetch on `app/research/api.py:1611-1670` (`_read_robots`, `robots_rule`, `_home`).
- New `nextron/domain/place_discovery.py` -- `DISCOVER_PLACES_JOB`, `location_hash`, `parse_location`, `site_name(html) -> str | None`, `PLACES_CONCURRENCY_KEY`, run statuses, count names.
- `nextron/domain/job.py:366` -- accept 16-hex discriminator.
- `nextron/domain/settings_schema.py` -- two settings (Decisions 2, 3).
- Data: `nextron/db/models/` new `DiscoveryRun` model (`discovery_runs`: id ULID, source, campaign_id, segment_id, country_code, location, query_hash, status, started_at, finished_at, seen, created, merged, suppressed, no_website, market_blocked); migration `db/alembic/versions/0038_8_3_discovery_runs.py` (rev `0038_8_3_discovery_runs`, down `0037_8_2_place_coords`); `db/owners.py` (discovery), `db/pii_registry.py`; repo `db/repos/discovery/runs.py`; `db/repos/discovery/leads.py` `set_place_coords`.
- `nextron/app/queries/discovery.py` -- `recent_runs(limit=20)`.
- `nextron/jobs/handlers.py` -- `discover_places(place_discovery)` handler (pattern `check_legal_form` :307); `main.build_job_types` register bulk-only; `main.build_app` builds `PlaceDiscovery` with `site_crawler`, keeps `app.state.place_discovery`.
- Web: new `nextron/web/discovery_page.py` + `templates/discovery.html` (pattern `lead_import_page.py`); routes in `web/app.py` before `/leads/{lead_id}`; link in `templates/leads.html`.
- Tests: `tests/fakes/crawler.py` (`ScriptedCrawler`, recording), `tests/fakes/places.py` (`ScriptedPlaces`), `queue_on` from `tests/app/test_leads.py`; head bumps per track-rules §3 (`test_persistence_contracts`, `test_migrations`, `test_serve_process`, `test_source_tree FILES`); job lists (grep `purge_place_coords` in tests); settings lists.

## Tasks & Acceptance

**Execution:**
- [x] domain, settings, job discriminator -- as Code Map.
- [x] model, migration, owners, PII, repos, query -- as Code Map.
- [x] `upsert_lead` change, `record_place_coords`, `PlaceDiscovery`, handler, job type, wiring.
- [x] Discovery page, POST, Leads-list link.
- [x] `tests/app/test_place_discovery.py` -- every matrix row with `ScriptedPlaces` + recording `ScriptedCrawler`; robots disallow; pages cap; deferral; failure status; `display_name` never used.
- [x] `tests/unit/test_place_discovery_domain.py` -- `site_name`, `parse_location`, `location_hash`, discriminator.
- [x] `tests/app/test_discovery_page.py` -- GET lists runs + no-store; POST CSRF, enqueue, duplicate, refusals.
- [x] contracts: storage-terms extension, AD-16 scenario, migration/job/settings lists.
- [x] `README.md` -- "Places discovery" section, migration row, job list.

**Acceptance Criteria:**
- Given the targeted tests, `tests/contracts` and `lint-imports`, when run, then all pass and no request leaves the machine.

## Implementation Notes

Built as specified, with these implementer choices (flagged for review where they read the spec
literally differently):

- **Payload `location` is the location hash, `country_code` lowercase.** `nextron.domain.job`'s
  payload rules accept only ULIDs, ints, dates and tokens `[a-z0-9_:.-]{1,64}`: "Columbus, OH"
  and "US" are refused. The payload keeps the spec's field names `{v, campaign_id, location,
  country_code}` but `location` holds the 16-hex hash and `country_code` the code in lowercase;
  the run looks the location text up again in `places_discovery_locations` (hash and code must
  both match). A location removed from the setting before the job runs, or an unknown Campaign:
  `places_run_skipped` is logged, no run row, the job completes. This keeps the Operator's
  location text out of `jobs.payload` too. *Conservative overnight choice; pending user review.*
- **The form posts the location hash, not an index** (`request_run(campaign_id,
  location_hash)`), so a settings edit between showing the page and submitting can't silently
  pick another location; an unknown hash is refused (`location_not_in_market`).
- **`PlaceSearches.page(...)`** was added to 8.2's seam: the same per-call check and request as
  `search_page`, without `@use_case`, because a use case can't call another use case and the run
  pages inside its own. `search_page` now delegates to it (behaviour unchanged).
- **Code layout.** `nextron/domain/place_discovery.py` (job/hash/location/`site_name`/statuses/
  outcomes), `nextron/app/discovery/place_ingest.py` (`PlaceDiscovery`, `PlacesRunRefused`,
  `configured_locations`), `nextron/db/models/discovery.py` (`DiscoveryRunRecord`),
  `nextron/db/repos/discovery/runs.py`, migration `0038_8_3_discovery_runs` (down
  `0037_8_2_place_coords`; CHECKs on source, status, country code, `finished_at` NULL exactly
  while `running`, non-negative counts; FKs to `campaigns`, `segments`; index on `started_at`),
  `queries.discovery.recent_runs`, `leads_repo.set_place_coords`, `Discovery.record_place_coords`
  (`@command`), `upsert_lead` enqueues `enrich_lead` for `CRAWLED_SOURCES = {csv, places}`.
- **Per-candidate flow** (`ingest_candidate`): one short transaction for the invalid-place-id /
  Market / place-id checks (each counted there when it decides), the robots + homepage fetch
  with no transaction, then one transaction for `upsert_lead`, `record_place_coords` and the
  count. A `place_id` that fails `identity.normalize_place_id` is counted as seen only (no
  outcome column fits). The robots.txt is read at the website's own origin (no `www.` retry);
  any `AdapterError` there means disallow-all (`research.api.robots_rule`). Robots and homepage
  use the origin of `website_uri` (its path/query are never fetched or stored).
- **`site_name`** takes the first `og:site_name` meta (by `property` or `name`, any case) with
  text, else the first `<title>` anywhere (an unclosed title's text counts); returns `None` when
  the cleaned text fails `leads.check_name`, and the domain is then the name.
- **Job type.** `discover_places`: bulk only, priority 30, 1 h, retry limit 2, group
  `discovery`. `build_job_types(place_discovery=None)` defaults to a provider that raises (like
  `domain_checks`), so no existing caller changed. Job key discriminator: `domain.job` accepts
  `[0-9a-f]{16}` (`HASH_PATTERN`).
- **Settings.** `places_max_pages_per_run` is declared in Budget and limits (Key order right
  after `ai_budget_alert_pct`) and `places_discovery_locations` in Engine (after `ats_hosts`);
  `LOCATION_RULE` uses `place_discovery.is_location_item` (codes must be uppercase).
  `tests/contracts/test_metering_contracts.py` (which forbade any non-quota setting naming
  Places) now lists both in `PLACES_LIMITERS` with the reason they can only narrow use.
- **Web.** `GET /leads/discovery` and `POST /leads/discovery/places`, both
  `@personal_data(False)`, registered before `/leads/{lead_id}`; `create_app(...,
  place_discovery=...)` is a new required argument (`tests/dashboard.py` builds one on
  `ScriptedPlaces`/`ScriptedCrawler`). The form needs `campaign_id` and `location`, allows
  `csrf_token`, refuses anything else (400). Link added on the Leads list.
- **Tests.** New: `tests/app/test_place_discovery.py` (53: every matrix row, robots disallow and
  unreadable robots, crawl delay, og:site_name/title/domain naming, redirect final URL, no
  coordinates, pages cap, query order, quota deferral on page 2, Places error before/after page
  1, stopped and skipped runs, the handler (deferred then finished; bad payloads), the request
  (job key, payload, lane, concurrency key, duplicate, refusals)), `tests/app/test_discovery_page.py`
  (16), `tests/unit/test_place_discovery_domain.py` (40). Contracts: storage-terms (allowlist +
  planted `discover_places` run with created/merged/blocked candidates scanning every text cell
  and log record), AD-16 (`discovery.places_ingest` manifest entry + scenario on
  `PlaceDiscovery.ingest_candidate`), job/web/settings/metering lists, head bumps (persistence
  chain probes now `0039_*`/`0040_*`, `test_migrations` new 0038 probe, `test_serve_process`,
  `test_serve` `0039_more`), source tree `FILES`.
- **Existing tests whose helpers used `LeadSource.PLACES` to avoid a crawl job**:
  `tests/app/test_website_crawl.py` `World.lead` now defaults to `SIGNAL_INBOX`, and its "only a
  created CSV Lead is queued" test now expects the Places Lead's job too. Other helpers that
  create Places Leads (`test_legal_form_check`, `test_signal_extraction`, `test_signal_store`,
  `test_contact_form_only`, `test_place_coords_purge`, `test_leads`) passed unchanged in the
  targeted runs.

- **Review pass 1 patches (implementer).** `request_run` also refuses (`location_not_in_market`)
  when the Campaign's Market country isn't an enabled Market, and refuses a Segment with no
  Places queries (new reason `no_places_queries`, its own page message). Before each page,
  `_pages` re-checks in a short transaction that the Campaign may still run and that its
  Market's country is the run's; otherwise the run ends `stopped` and the handler returns
  normally. `_start` first ends every `running` `discovery_runs` row `failed` at now
  (`runs_repo.fail_running`, logged `places_runs_recovered`), since all `discover_places` jobs
  share one concurrency key. The place ID used for `by_place_id` and `LeadIdentity` is the value
  `normalize_place_id` returns. `site_name` skips blank `og:site_name` values, so a later
  non-blank one wins. New tests: those behaviours, `record_place_coords` (columns and each
  refusal), `build_app` wiring of `place_discovery`, the 0038 CHECKs (`sqlite3.IntegrityError`
  inserts), the location rule and page cap through the schema, and `recent_runs` order, limit
  and argument check. Docstring and import-order tidy-ups in `main.py`, `handlers.py`,
  `queries/discovery.py`. Ruff is not configured in this project.

- **Final gate.** Full suite through `suite_lock.py` on the patched tree: 15,405 passed, 13 skipped; `lint-imports` 29 kept, 0 broken. A pre-patch targeted run had one engine-startup timeout under load (`test_serve_process`); the full run was clean.

## Spec Change Log

## Review Triage Log

Pass 1 (blind hunter 12, edge-case hunter 9, verification gap 4; 25 raw, 19 after dedup).

| # | Source | Finding | Verdict | Route / evidence |
|---|---|---|---|---|
| 1 | blind, edge | `DEFERRED` is returned after page 1's Leads were committed, against `domain/job.py`'s "defer only before any work" rule | low | reject: the frozen matrix row "Quota refuses page 2: page-1 Leads kept; run deferred; job DEFERRED" and Decision 1 mandate it; the re-run is idempotent (merges) and, the quota being monthly, re-defers before buying page 1 again |
| 2 | blind | a run whose country isn't an enabled Market buys every page and counts each candidate `market_blocked` | medium | patch: `request_run` also refuses (`REFUSED_LOCATION`) a location whose country isn't an enabled Market; the per-candidate check stays for a Market disabled after queueing |
| 3 | blind, edge | Campaign active / Market country checked only at start; pausing a Campaign mid-run keeps buying pages and creating Leads | low | patch: before each page re-check `campaign_active` and that the Campaign's Market country is the run's; else end the run `stopped` |
| 4 | blind, edge | a crash leaves a `discovery_runs` row `running` forever | low | patch: `_start` ends every `running` row `failed` first (all `discover_places` jobs share one concurrency key, so any such row is stale) |
| 5 | blind | a Segment with no Places queries is queued and finishes with zeros, no reason given | low | patch: `request_run` refuses with a new reason `REFUSED_NO_QUERIES` |
| 6 | blind | 1 h `max_runtime` has no per-run time budget against crawl delays | low | reject: robots_rule bounds the interval; a retryable overrun only re-runs idempotently |
| 7 | blind | `record_place_coords` branches untested | low | patch: unit tests for its refusals and the three columns with `now` |
| 8 | blind | location options aren't filtered by Campaign | low | reject: the server refuses a mismatch with a clear message; filtering needs client script |
| 9 | blind | Recent runs lacks finish time and explains no statuses/gaps | low | reject: the AC's listed columns are shown |
| 10 | blind | a known or suppressed domain is fetched again before the dedup | low | reject: the spec's per-candidate order (place_id, then site, then upsert) |
| 11 | blind | E302 blank line in `queries/discovery.py`, overlong/unindented docstring lines in `main.py`, unsorted import in `handlers.py` | low | patch: direct corrections |
| 12 | blind | spec/sprint not finished, no full suite | false | the workflow had not reached steps 4–5; the gate runs before commit |
| 13 | edge | `_end(FAILED)` raising inside `except` masks the original error | low | reject: a DB failure there is itself the more urgent error; no demonstrated path |
| 14 | edge | `place_id` used un-normalised for the `by_place_id` lookup and identity | low | patch: use `normalize_place_id`'s result for both |
| 15 | edge | the homepage's redirect target host's robots.txt is never read | low | reject: only the title and domain are read from the one page the egress-filtered crawler already fetched |
| 16 | edge | a whitespace-only first `og:site_name` stops the search for a later one | low | patch: skip blank values |
| 17 | edge | spec says `search_page`, code calls new `PlaceSearches.page` | false | `search_page` delegates to `page`; behaviour identical, recorded in Implementation Notes |
| 18 | verif | `build_app`'s `place_discovery` provider to `build_job_types` is untested | medium | patch: test modelled on `test_build_app_builds_domain_checks_on_its_dnspython_dns` |
| 19 | verif | 0038's CHECKs, the two settings' rules, and `recent_runs` order/limit/argument check are untested | medium | patch: IntegrityError inserts in `test_migrations`, schema tests in `test_settings_schema`, a 21-run newest-first test and argument checks |

Deferred: hourly re-deferral of a refused run adds a zero-count `deferred` row each hour until the quota resets (from #1).

## Verification

**Commands:**
- `cd nextron-engine && uv run pytest -q tests/contracts tests/app/test_place_discovery.py tests/app/test_discovery_page.py tests/unit/test_place_discovery_domain.py` -- all pass
- `cd nextron-engine && uv run lint-imports` -- all contracts kept
