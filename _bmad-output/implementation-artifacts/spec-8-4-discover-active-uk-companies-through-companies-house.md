---
title: "Story 8.4: Discover active UK companies through Companies House"
type: 'feature'
created: '2026-11-19'
status: 'done'
route: 'dispatch'
baseline_commit: '5cad70b69c9860592de48958477feaa5fa6ed8d5'
review_loop_iteration: 0
story_key: '8-4-discover-active-uk-companies-through-companies-house'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-8-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-8-3-turn-places-candidates-into-deduplicated-contactable-leads.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-12-confirm-a-uk-lead-s-legal-form-through-companies-house.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** UK supply can only come from CSV, Signal inbox or Places. The engine can't find active UK companies in our Segments from the official register, where the legal form is already known.

**Approach:** Extend `RegistryPort` with `search` (keyword + SIC, paged) and `officers`. A "Run Companies House discovery" form on `/leads/discovery` enqueues a `discover_registry` bulk job per active UK Campaign. The job pages the Segment's `registry` queries, skips known company numbers, reads each profile, discards inactive/dormant companies, creates the Lead through `Discovery.upsert_lead` (AD-16 gate), records the registry legal form (unconfirmed until the site confirms it, FR-12), looks up a website once through Places, and turns kept officers into email-less Contacts. Leads left without a website show under a "website needed" filter on the Leads list, where the Operator can enter one.

## Boundaries & Constraints

**Always:**
- The epics' Story 8.4 ACs are binding (`_bmad-output/planning-artifacts/epics.md`, "Story 8.4").
- **Port.** `search(keyword: str | None, sic_codes: tuple[str, ...], page: int) -> tuple[RegistryCompany, ...]` (Companies House advanced search: `company_name_includes` when a keyword, `sic_codes`, `company_status=active`, `size=20`, `start_index=page*20`; operation `search_registry`) and `officers(company_number) -> tuple[RegistryOfficer, ...]` (`/company/<n>/officers`, `items_per_page=100`; operation `list_officers`; 404 → `()`). Both reuse the adapter's key-per-call, header auth, the shared 600/300 s window, `Metered(..., "companies_house", op)`, the status table (429 → retryable `registry_rate_limited`). `RegistryCompany` gains optional `accounts_category` (profile `accounts.last_accounts.type`, code pattern) and `sic_codes` (tuple of 5-digit codes); `RegistryOfficer(name, role, occupation, resigned)` hides name and occupation from `repr`. The fake supports both.
- **Run order per hit** (all network outside transactions; each outcome counted in the transaction that decides it): number held by any Lead (merged ones included) → `merged`, nothing fetched; profile `None` → seen only; status ≠ `active` or accounts category `dormant` → `inactive`; GB not an enabled Market → `market_blocked`; else one transaction: `upsert_lead(LeadIdentity(name=registered name, segment, ch_number, country_code="GB"), COMPANIES_HOUSE)` (blocked → `suppressed`; matched → `merged`; created → `created`) and, when created, `research.record_legal_form(lead, <registry-only finding>)`. Then website lookup and officers, for created Leads only.
- **Registry-only finding.** `LegalFormFinding` accepts a number with no site quote: status must be `unconfirmed`; legal form, company status, registry quote and `retrieved_at` required. `registry_quote` appends `, accounts category <x>, SIC codes <a b>` only when those are given (existing quotes render unchanged). `LegalFormChecks` recording an unlinked finding leaves a stored registry-only finding (site pointer NULL, registry pointer set) in place. Eligibility stays `uk_legal_form_unconfirmed` until 2.12's site check confirms (FR-12).
- **Website lookup.** One `PlaceSearches.page(registered name, office postcode)` (its own per-call budget check, Places Enterprise). Only the first candidate's `website_uri` is used, through 8.3's site checks (`_site`: denylist, shared platform, robots, homepage loads). A domain no other Lead holds and that passes `assert_contactable(NewLeadSubject(domain), EMAIL, DISCOVERY)` is attached by a new `Discovery` command, which enqueues `enrich_lead`. Otherwise `web_domain` stays NULL. `website_lookup_at = now` whenever the lookup decided (no postcode, no candidate, unusable or taken website, or attached); a budget refusal leaves it NULL. Nothing from the Places candidate is stored.
- **Officers.** Active (no `resigned_on`) natural persons with role `director` or `llp-designated-member` only (any `corporate-*` role ignored). The role tested and stored is the officer's `occupation`; an officer whose occupation is missing or doesn't pass `contacts.role_match(occupation, segment)` is discarded. Name: Companies House "SURNAME, Forenames" becomes "Forenames Surname" (all-caps surname title-cased); a name failing `contacts.check_name` is discarded. Each kept one: `research.add_contact(lead, ContactCandidate(name, role), source=ProvenanceSource.REGISTRY, detail="Companies House API officers, company <number>", retrieved_at=now)`, no email; AD-16 runs inside `add_contact`.
- **Operator website.** `POST /leads/{lead_id}/website` (CSRF, `@personal_data(False)`) calls `Discovery.enter_website(lead_id, url)`: Lead must exist, not be merged, and have no `web_domain`; URL must pass `fetch_policy.url_refusal`, key to a registrable domain, not be a shared platform; the gate must allow the domain. A Lead already holding the domain → merge per FR-9 (older survives, as `set_ch_number`); then `enrich_lead` for the survivor. Refusals re-render with a message, nothing written.
- **Leads list.** A "Website needed" filter (`website=needed`, a GET param) lists live Leads with `web_domain` NULL; each row there carries the website form.
- **Run accounting.** `discovery_runs` source `companies_house`, `location` NULL, new count `inactive`; `no_website` counts created Leads left without a website. 8.3's stale-row recovery fails only `running` rows of its own source.
- No log line, `events` row, payload or error holds a company name, officer name, postcode, keyword, website or Places data: ids, numbers, codes and counts only.
- No test reaches Companies House or Places.

**Never:**
- No `budget_class` registration field, notifications or health issue (Story 8.1); no scheduled or automatic run (8.10); no officer email lookup or guessing; no officers for matched Leads; no Places `place_id` stored from the lookup.
- No new dependency.

### Decisions made overnight (pending user review)
1. **8.1 boundary.** As 8.3: before each search page a short transaction runs `budget.allow(BudgetClass.BLOCKABLE, meter.snapshot(now), budget_limits())`; a refusal ends the run `deferred` and the handler returns `DEFERRED`. A `registry_rate_limited` error during a run also ends it `deferred` (closes the deferred 2.12 rate-window item for this caller). Other retryable errors fail the run and are re-raised.
2. **Queries.** Segment `discovery_queries.registry: {keywords: [...], sic_codes: [...]}` (optional in the file and stored form, default empty both; SIC fullmatch `[0-9]{5}`, keyword rules as Places queries). Each keyword runs one search with all the codes; no keywords → one SIC-only search. A Segment with no SIC codes is refused (`no_registry_queries`). Law-PrivateAI: keywords `[solicitors, law, legal]`, SIC `[69102]`. Ops-Automation: keywords `[]`, SIC wholesale `46190, 46690, 46900`, freight `49410, 52103, 52290`, light manufacturing `22290, 25990, 28290, 32990`.
3. **Pages.** `registry_max_pages_per_run` (integer, Budget and limits, default 3, 1–10) caps the whole run, in query order, next page only after a full page of 20. `query_hash` = SHA-256 hex (16) of the canonical queries.
4. **Job.** `discover_registry`: bulk only, priority 30, 1 h, retry limit 2, group `discovery`, key `discover_registry:<campaign ULID>:<query hash>`, payload `{v: 1, campaign_id}`, concurrency key `domain:api.company-information.service.gov.uk`. The run reads the Segment's current queries.
5. **Campaign.** Must be active and its Market's country `GB` and enabled, at request and before each page (else refused `campaign_not_uk` / run `stopped`).
6. **Officer roles.** Using occupation keeps only officers the Segment's FR-13 titles name, so a plain "Director" without a matching occupation creates no personal data (data minimisation).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected |
|---|---|---|
| New active company | ltd, active, full accounts, postcode, Places site ok | Lead (name, ch_number, GB, stage `new`), registry Evidence, unconfirmed legal form, domain attached, `enrich_lead` queued; created +1 |
| Number already held | any stage, merged included | nothing fetched; merged +1 |
| Dissolved / dormant | status `dissolved` or accounts `dormant` | no Lead; inactive +1 |
| Gate blocks | suppression | nothing written; suppressed +1 |
| No usable website | no postcode / no candidate / denylisted / domain held | Lead, `web_domain` NULL, `website_lookup_at` set; no_website +1; no `enrich_lead` |
| Places quota refuses | | Lead, `website_lookup_at` NULL; run continues |
| Officers | active director (occupation "Managing Partner"), resigned director, corporate director, director "Accountant" | one Contact, no email, Provenance `registry` |
| Budget refuses page 2 | | page-1 Leads kept; run `deferred`; job `DEFERRED` |
| 429 mid-run | | run `deferred`; job `DEFERRED` |
| Operator website | domain held by older Lead | merged into it; `enrich_lead` for survivor |
| Operator website | denylisted / shared / suppressed / Lead has domain | refused with message; nothing written |
| Non-UK or inactive Campaign | POST | refused, nothing queued |

</frozen-after-approval>

## Code Map

All under `nextron-engine/`.
- `nextron/ports/registry.py` -- add `RegistryOfficer`, `RegistryCompany.accounts_category/sic_codes` (defaults), `search`, `officers`, `SIC_CODE_PATTERN`; keep `search_companies`/`company` unchanged.
- `nextron/adapters/companies_house/__init__.py` -- `/advanced-search/companies` and `/company/<n>/officers`; read `accounts.last_accounts.type` and `sic_codes` in `document_company` and search items; same `_get`, `_reserve`, `_answer`. 2.12 docstring table extends.
- `tests/fakes/registry.py` -- `searches_by` keyed `(keyword, sic_codes, page)` and `officers` scripts; ops `search_registry`, `list_officers`.
- `nextron/domain/segments.py` -- `DiscoveryQueries(places, registry=RegistryQueries((), ()))`; parse/stored form; `seed/segments.yaml` (Decision 2).
- `nextron/domain/legal_form.py` -- registry-only finding; `registry_quote(accounts_category=None, sic_codes=())`.
- `nextron/app/research/api.py:1195` `record_legal_form` -- accept registry-only (no site re-check). `nextron/app/discovery/legal_form.py` -- keep registry-only on unlinked.
- `nextron/domain/contacts.py:122` -- `ProvenanceSource.REGISTRY = "registry"`; check its readers (`web/leads_page.py` `SOURCE_LABELS` "Companies House", `assign`, export) by grepping `ProvenanceSource.`.
- New `nextron/domain/registry_discovery.py` -- job name, payload fields, concurrency key, `registry_query_hash`, `officer_name(raw)`, `kept_officer(officer, segment)`.
- `nextron/domain/place_discovery.py` -- `Outcome.INACTIVE`, `SOURCE_COMPANIES_HOUSE`.
- Migration `db/alembic/versions/0039_8_4_registry_discovery.py` (rev `0039_8_4_registry_discovery`, down `0038_8_3_discovery_runs`): rebuild `discovery_runs` (source CHECK `places`/`companies_house`, `location` nullable with CHECK `(source = 'places') = (location IS NOT NULL)`, count `inactive` default 0 in counts CHECK); `leads.website_lookup_at` (add_column, owner `discovery`, PII `none`); rebuild `provenance_records` (copy_from its 0031 shape; source_kind CHECK + `registry`) and re-create `trg_provenance_records_erased_update`/`_insert` exactly as 0032. Update `db/models`, `owners.py`, `pii_registry.py`, `db/repos/discovery/runs.py` (`fail_running(source, at)`; count INACTIVE; insert location None), `db/repos/discovery/leads.py` (`set_web_domain`, `set_website_lookup_at`, website-needed list query).
- `nextron/app/discovery/api.py` -- `@command attach_web_domain(lead_id, domain) -> bool`, `@command record_website_lookup(lead_id)`, `@use_case enter_website(lead_id, url)` with `WebsiteRefused(reason)`; reuse `_enqueue_crawl`, `merge`, `_survivor`.
- `nextron/app/discovery/place_ingest.py` -- make `_site`/`_domain`/`_origin` reusable (module function `own_site(crawler, website_uri)`); `fail_running` by source.
- New `nextron/app/discovery/registry_ingest.py` -- `RegistryDiscovery(clock, meter, registry, place_searches, crawler, discovery, research, queue)` with `request_run(campaign_id)`, `run(campaign_id) -> bool`, `ingest_hit(...)`; shape after `PlaceDiscovery`.
- `nextron/app/queries/discovery.py` -- `lead_list(..., website_needed=False)`.
- `nextron/jobs/handlers.py` + `main.build_job_types`/`build_app` -- handler `discover_registry`; build `RegistryDiscovery` with the SAME `registry` instance `LegalFormChecks` uses (shared rate window); `app.state.registry_discovery`.
- Web: `discovery_page.py`/`discovery.html` (form: UK active Campaigns; `POST /leads/discovery/companies-house`; runs table shows "—" for NULL location and the inactive count), `leads_page.py`/`leads.html` (filter + per-row form), `web/app.py` routes (`/leads/discovery/...` before `/leads/{lead_id}`); `create_app(registry_discovery=...)`, `tests/dashboard.py`.
- Settings: `settings_schema.py` `registry_max_pages_per_run`; `tests/contracts/test_metering_contracts.py` limiter list.
- Tests: `queue_on` from `tests/app/test_leads.py`; `ScriptedPlaces`, `ScriptedCrawler`; head bumps per track rules (`test_persistence_contracts`, `test_migrations`, `test_serve_process`, `test_serve`, `test_source_tree FILES`); job, web-route, template and settings lists; `tests/contracts/test_places_storage_terms.py` `PLACES_IMPORTERS` + `registry_ingest.py`; AD-16 manifest/scenarios (`discovery.registry_ingest`, `discovery.enter_website`, `discovery.attach_web_domain`); adapter-boundary contract driver for the two new operations.

## Tasks & Acceptance

**Execution:**
- [x] port, adapter, fake -- as Code Map; `tests/unit/test_companies_house_adapter.py` (search params, officers parsing, 404, 429, metering ops, window shared).
- [x] segments + seed; legal_form registry-only; ProvenanceSource; domain module -- with unit tests.
- [x] migration, models, owners, PII, repos, queries -- `test_migrations` probes incl. CHECKs and the re-created triggers.
- [x] Discovery commands, `RegistryDiscovery`, handler, job type, wiring.
- [x] Discovery page form, Leads list filter + website POST.
- [x] `tests/app/test_registry_discovery.py` -- every matrix row; officer naming; page cap; stopped run; failure status; nothing sensitive in logs.
- [x] `tests/app/test_discovery_page.py`, `tests/unit/test_leads_page.py`/web tests for the filter and POST.
- [x] contracts and lists; `README.md` "Companies House discovery" section, migration row, job list.

**Acceptance Criteria:**
- Given the targeted tests, `tests/contracts` and `lint-imports`, when run, then all pass and no request leaves the machine.
- Given a stored Segment definition without `registry`, when read, then it parses with empty registry queries.

## Implementation Notes

Built as specified, with these implementer choices (flagged where the spec left room):

- **Port** (`nextron/ports/registry.py`). `RegistryCompany` gains `accounts_category=None` and
  `sic_codes=()` (distinct `[0-9]{5}` codes); `RegistryOfficer(name, role, occupation,
  resigned)` (name/occupation hidden from `repr`; role must match `LEGAL_FORM_PATTERN`).
  `SIC_CODE_PATTERN`, `ACCOUNTS_CATEGORY_PATTERN` and `DORMANT` live in
  `nextron.domain.legal_form` (segments needs the SIC pattern and the domain can't import ports);
  the port re-exports them and adds `PAGE_SIZE` (20).
- **Adapter.** `search` → `/advanced-search/companies` (`company_status=active`,
  `company_name_includes` only with a keyword, `sic_codes` comma-joined, `size=20`,
  `start_index=page*20`), op `search_registry`; items read like a profile (`company_name`,
  `registered_office_address`, `sic_codes`). **A search 404 is `()`** (the advanced-search API
  answers 404 when nothing matches; treating it as `registry_refused` would fail every empty
  search) — *overnight choice*. Neither keyword nor codes, a blank keyword, a non-5-digit code or
  a bad page → `registry_refused` with no request. `officers` → `/company/<n>/officers`
  (`items_per_page=100`), op `list_officers`, 404 → `()`, `resigned` = non-blank
  `resigned_on`, occupation whitespace-collapsed (None if blank or >200). Profile reads
  `accounts.last_accounts.type` and `sic_codes`. Same key-per-call, window, `Metered`, status
  table.
- **Fake.** `ScriptedRegistry(searches_by=, officers=)`; the officers script is the attribute
  `officer_lists` (`officers` is the method).
- **Segments.** `RegistryQueries(keywords=(), sic_codes=())`, `DiscoveryQueries(places,
  registry=RegistryQueries())`; `registry` optional in file and stored form (always written by
  `definition_text`, so stored definitions get a new version on the next seed sync); keywords
  follow the query text rule and may be empty; SIC codes are quoted texts in the YAML (the strict
  YAML would read them as ints).
- **Registry quote.** The accounts category and SIC codes are inserted *before*
  `, registered name <name>` (not after it), so the name stays last and the 300-character cut at
  the name's end still works; quotes without them render unchanged.
- **Registry-only finding.** `LegalFormFinding` with a number and no site: must be `unconfirmed`
  with legal form, company status, registry quote and `retrieved_at` (`registry_only`
  property). `Research.record_legal_form` needed no change (it re-checks only a site quote).
  `LegalFormChecks._apply` skips recording an unlinked finding (including the conflict case on
  the Lead itself) when the stored legal form is registry-only (`site_evidence_id` NULL,
  `registry_evidence_id` set). A company whose type is unknown records **no** legal form (the
  finding requires one) — *overnight choice*.
- **Domain** `nextron/domain/registry_discovery.py`: job/key/payload constants,
  `registry_searches`, `registry_query_hash` (SHA-256 of sorted-key JSON, 16 hex),
  `is_inactive`, `officer_name` (surname title-cased only when all capitals; extra commas read
  as spaces; no comma → kept), `kept_officer` (Protocol `OfficerFacts`; returns a
  `ContactCandidate(name, role=occupation)`), `officers_detail`. The page-cap constants
  (`MAX_REGISTRY_PAGES_PER_RUN` 10, default 3) live in `place_discovery.py` to avoid a
  `settings_schema` → `contacts` → `geo` → `settings_schema` import cycle.
  `place_discovery`: `Outcome.INACTIVE`, `SOURCE_COMPANIES_HOUSE`, `SOURCES`.
- **Migration** `0039_8_4_registry_discovery` (down `0038_8_3_discovery_runs`): discovery_runs
  rebuilt from 0038's exact table (`inactive` server default 0, `location` nullable,
  `ck_discovery_runs_location`, widened source and counts checks; drops use `op.f`);
  `leads.website_lookup_at` added in place; `provenance_records` rebuilt from 0031's shape with
  `registry` and both 0032 erasure triggers re-created verbatim. Model, `owners.py`
  (discovery), `pii_registry.py` (none) updated. `runs_repo.fail_running(source, at)`, new
  `runs_repo.add_outcome(run, outcome)` (counts `no_website` without `seen`);
  `leads_repo.set_website_lookup_at`, `listed/count_listed(website_needed=)`;
  `queries.discovery.lead_list(website_needed=False)`.
- **Discovery commands** (`app/discovery/api.py`): `website_domain(url)` (moved from
  `place_ingest._domain`, reused by both), `@command attach_web_domain` (False when held or
  blocked; ValueError for a non-registrable/shared domain, merged or already-websited Lead),
  `@command record_website_lookup`, `@use_case enter_website` → `EnteredWebsite(lead_id,
  merged)` / `WebsiteRefused(reason)` with reasons `lead_unknown`, `lead_has_website`,
  `url_refused`, `shared_platform`, `blocked`. An entered address without `://` is read as
  `https://…` — *overnight choice (usability)*. The gate runs before the held-domain merge.
- **`place_ingest`**: `_site` became module `own_site(crawler, website_uri)` returning
  `OwnSite`; behaviour unchanged.
- **`RegistryDiscovery`** (`app/discovery/registry_ingest.py`) per the spec's run order.
  Choices: `request_run` refuses `campaign_not_active` (inactive/unknown) separately from
  `campaign_not_uk`; a stopped/non-UK run still writes a `stopped` row with country `GB`. The
  per-page check re-tests the Campaign first (→ `stopped`) then the budget (→ `deferred`). A
  name that `place_discovery.clean_name` rejects, a profile `None`, or a **non-retryable**
  profile error counts seen only; a non-retryable officers error stores no officers; retryable
  ones re-raise (rate-limited → run `deferred`, others → `failed`). A Places `AdapterError` in
  the website lookup is treated like a quota refusal (undecided, `website_lookup_at` NULL) so
  one Places hiccup doesn't fail the run — *overnight choice*. A created Lead left without a
  website (undecided included) adds 1 to `no_website`. Logs: `registry_run_*`,
  `registry_hit_ingested` (run, outcome, lead id, website outcome code, contact count) — no
  names, postcodes, keywords or URLs.
- **Jobs/wiring.** `handlers.discover_registry` + `DiscoveringRegistry`;
  `build_job_types(registry_discovery=None)` with a raising default (`no_registry_discovery`);
  `build_app` builds one `CompaniesHouseRegistry` shared by `LegalFormChecks` and
  `RegistryDiscovery`, keeps `app.state.registry_discovery`. `create_app` gains
  `registry_discovery=` **and `discovery=`** (needed for `enter_website`); `tests/dashboard.py`
  builds both on a `ScriptedRegistry`.
- **Web.** Discovery page: a "Run Companies House discovery" form listing active Campaigns
  whose Market is an enabled GB one (`POST /leads/discovery/companies-house`,
  `@personal_data(False)`); the runs table shows "— (GB)" for no location and a new Inactive
  column. Leads list: a "Website" select (`website=needed`); in that mode the table moves
  `hx-disable` onto its text cells and each row gets `lead_website_form.html` (a `<td>`).
  `POST /leads/{lead_id}/website` (`@personal_data(False)`) answers only that cell: the notice
  and either the form again or a link to the Lead that kept the website — it never shows the
  Lead's name or the typed text, which is what lets it honour `@personal_data(False)`.
- **Settings.** `registry_max_pages_per_run` (Budget and limits, 1–10, default 3). It doesn't
  name Places, so the metering contract's `PLACES_LIMITERS` needed no entry.
- **Contracts/tests.** AD-16 manifest gains `discovery.registry_ingest`
  (`RegistryDiscovery._decided|_officers`), `discovery.attach_web_domain`,
  `discovery.enter_website`, each with a scenario; `add_contact[registry]` comes automatically.
  Places storage-terms allowlist gains `registry_ingest.py`; adapter-boundary driver gains
  `search` and `officers`; job, route, template, settings and head lists bumped (persistence
  probes now `0040_*`/`0041_*`, `test_serve` `0040_more`); the old 0038 migration probe now
  stops at 0038; new 0039 probe (checks, nullable location, `inactive`, `registry`, both
  triggers still abort on an erased Contact). New: `tests/app/test_registry_discovery.py` (48),
  `tests/unit/test_registry_discovery_domain.py`, adapter tests appended to
  `tests/unit/test_companies_house_adapter.py`, Companies House + website tests appended to
  `tests/app/test_discovery_page.py`.
- **Known gaps (for review).** A 429 between creating a Lead and listing its officers leaves
  that Lead without officers forever (the re-run sees the number as held → `merged`); deferred
  (triage #3). The 0039 row-preservation gap was closed in review pass 1 (triage #11).
- **Verification (implementer, targeted).** `tests/contracts` (all, `-n 2`) pass after fixes;
  the new/touched test files above plus segments, seed, settings, leads page, migrations, serve,
  legal form, place discovery, lead import, people extraction and health lists all pass;
  `lint-imports` 29 kept, 0 broken. The full suite was not run (orchestrator's gate).
- **Review pass 1 patches.** (1) The Companies House adapter reads `"items": null` as no items
  (search, advanced search, officers); a non-list non-null `items` stays `registry_bad_response`
  (new adapter test). (2) `enter_website` decides "has a scheme" by `urlsplit(text).scheme`
  instead of `"://" in text`, so `firm.co.uk/?r=https://x` is read as `https://…`
  (`SCHEME_SEPARATOR` removed; new test). Side effect: a bare `host:port` now parses as a
  scheme and is refused (`REFUSED_URL`). (3) The 0039 migration probe now builds a real
  market/offer/sequence/Campaign chain and keeps a finished 0038 Places run (location, counts),
  asserting it survives with `inactive = 0`, `integrity_check` ok and a rewrite passing the new
  CHECKs — this closes the "known gap" above. (4) New `enter_website` tests: the registry Lead
  is the older Lead (it survives, receives the newer Lead's domain through `merge`, and
  `enrich_lead` is queued for it); and the domain's holder already merged into the Lead itself
  is refused `REFUSED_HAS_WEBSITE` with nothing written — not a bug (unreachable through
  `merge`, which moves a loser's domain to a survivor without one; the state is built by SQL).

- **Final gate (orchestrator).** `tests/unit/test_contacts_domain.py::test_the_sources_and_subjects` gained `registry` (missed by the first run). Full suite through `suite_lock.py` on the final tree: 15,561 passed, 13 skipped; `lint-imports` 29 kept, 0 broken. The first full run also failed `test_job_runner.py::test_the_keeper_thread_ticks_at_once` once under load; it passed alone and in the re-run.

## Spec Change Log

## Review Triage Log

Pass 1 (blind hunter 13, edge-case hunter 10, verification gap 1 + 1 other; 25 raw, 24 after dedup).

| # | Source | Finding | Verdict | Route / evidence |
|---|---|---|---|---|
| 1 | edge | `_pages` stops paging when fewer than 20 *valid* hits come back, though an item dropped for a bad number/name made the raw page full | low | reject: only an item without a valid number or name is dropped (rare); the fix adds a has-more field to the port |
| 2 | edge | `attach_web_domain` raises when the Operator enters a website for the Lead between its creation and the lookup's attach, failing the run | low | reject: a seconds-wide race against a Lead the run just created; the run's retry merges |
| 3 | edge, impl | a run that fails, defers or is retried after a Lead is created but before its website lookup/officers counts the number `merged` next time, so those steps never run | medium | defer: needs a resume marker per Lead (new state), not a direct fix |
| 4 | edge | a retryable officers error fails/defers the run; officers for that Lead are then lost (same root as #3) | medium | defer: grouped with #3 |
| 5 | edge | a created Lead merged by the Operator before its officers are stored makes `add_contact` raise | low | reject: same seconds-wide race as #2 |
| 6 | edge | `officers` reads only the first 100 entries (resigned included) | low | defer: small target firms rarely exceed it; paging adds calls against the rate window |
| 7 | edge | an explicit `"items": null` answer is `registry_bad_response`, failing the run | low | patch: read `items` null as no items (search, advanced search, officers) |
| 8 | edge | `enter_website` decides "has a scheme" by `"://" in text`, so `firm.co.uk/?r=https://x` gets no `https://` and is refused | low | patch: test the scheme with `urllib.parse.urlsplit` |
| 9 | edge | a profile whose number differs from the searched number | low | reject: the register's profile endpoint answers the requested number; no path shown |
| 10 | edge | a non-ULID `lead_id` makes an invalid `hx-target` selector | false | reject: only a hand-crafted POST reaches it, all values are autoescaped, and the refusal still renders |
| 11 | verif | 0039's `discovery_runs` rebuild is tested only on an empty table | medium | patch: seed a 0038 `places` row before the upgrade and assert it survives with `inactive = 0` and its location |
| 12 | verif (other) | an undecided lookup (Places refused or failed) is never retried and is counted `no_website` | low | defer: re-running lookups belongs with 8.1/8.10's scheduling; counting matches the spec's `no_website` definition |
| 13 | blind | the first Places candidate's website is attached without checking its name against the registered name (formation-agent postcodes) | medium | defer and report: the approved spec says "Only the first candidate's `website_uri` is used"; FR-12 still keeps the legal form unconfirmed on a wrong site |
| 14 | blind | each run restarts at page 0, so later keywords are never reached with cap 3 and repeat runs refetch the same companies | medium | defer and report: Decision 3 (frozen) fixes the query order and the run-wide cap |
| 15 | blind | `website_lookup_at` is never read; the filter tests `web_domain IS NULL` | low | reject: the AC defines the column and the "website needed" list as built |
| 16 | blind | a lookup domain another Lead holds leaves a duplicate instead of merging | low | reject: the AC says the result's website is used only if "no other Lead holds" it |
| 17 | blind | a typed website merges two Leads without confirmation | low | reject: the AC requires FR-9 deduplication on entry; the reply says it merged |
| 18 | blind | `company_status_detail` (proposal to strike off) isn't checked | low | defer: the AC defines inactive as status not `active` or dormant accounts |
| 19 | blind | non-corporate company types still become Leads (lookup, crawl, officers) before Eligibility blocks them | medium | defer: the AC creates a Lead for every active company; filtering is a scope change |
| 20 | blind | registered names stay all-caps; `McDONALD` title-cases to `Mcdonald` | low | reject: the AC stores the registered name; Decision in Boundaries fixes the title-casing rule |
| 21 | blind | a 10-page run can exceed `max_runtime` 1 h | low | defer: the default cap is 3 pages; a per-hit time budget is new behaviour |
| 22 | blind | the `provenance_records` snapshot may drift at the merge renumbering | low | reject: none of main's later migrations touch the table; flagged for the main session in the report |
| 23 | blind | the `enter_website` merge where the registry Lead is older, and the `other.id == lead.id` branch, are untested | low | patch: add both tests (merge moves the loser's domain via `KEY_FIELDS`) |
| 24 | blind | the no-JS fallback of the website form returns a bare `<td>` | low | reject: every engine form is HTMX-driven, like the existing panels |
| 25 | blind | the run row doesn't record pages fetched or why it stopped | low | reject: the AC's listed counts are shown |

## Verification

**Commands:**
- `cd nextron-engine && uv run pytest -q -n 2 tests/contracts tests/app/test_registry_discovery.py tests/app/test_discovery_page.py tests/unit/test_companies_house_adapter.py` -- all pass
- `cd nextron-engine && uv run lint-imports` -- all contracts kept
