---
title: 'Story 2.9: Detect Signals with Evidence from the crawled pages'
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: '689874306572671ac41950cdc25624632505a078'
review_loop_iteration: 0
story_key: '2-9-detect-signals-with-evidence-from-the-crawled-pages'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-8-version-extraction-prompts-and-pin-the-active-route-tuple.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-5-crawl-a-lead-s-website-politely-inside-the-egress-filter.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Story 2.5 stores each Lead's visible page text, and Stories 2.7–2.8 can call Claude with a pinned prompt, but nothing turns pages into Signals. Nothing stores Evidence, and nothing gives a Lead a headquarters timezone. Stories 2.10, 2.11 and 2.15 need Signals and Evidence that code has verified.

Requirements: FR-10, FR-11, FR-57 · AD-3, AD-7, AD-8, AD-10, AD-11, AD-18 · NFR-24.

**Approach:**
- **Job.** `enrich_lead`'s apply transaction enqueues a new `extract_lead` job.
- **Call.** That job checks the budget, reads the active `extract_signals` tuple, and sends the Lead's changed pages through `LlmPort` in one call.
- **Validate and store.** Code validates every proposal and stores Evidence, Signals and their links (migration `0022_2_9_signals_evidence`). It reconciles them with the Lead's earlier Evidence and sets `leads.hq_tz`.
- **Display.** The Lead page gains Signals and Evidence sections.
- **Wiring.** The Claude gateway is wired in `main`.

The acceptance criteria are Story 2.9 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**

- **Pure domain** (standard library and `nextron.domain` only).
  - **`nextron/domain/signals.py`:**
    - `age_days(event_date, first_seen_at, now)` counts whole UTC days from the event date, else from first seen, and is never negative.
    - `weight_factor(signal_class, tier, age)` and `is_live(signal_class, tier, age)` give:
      - **Tier 1 Event:** a factor of 1.0 through day 30, then a linear fall to 0 at day 90, i.e. `(90 − age) / 60`. It is live while `age < 90`.
      - **Tier 2 and Tier 3 Events:** a factor of 1.0 and live through day 90; after that 0 and expired.
      - **Attributes:** 1.0 and always live.
  - **`nextron/domain/extraction.py`** holds:
    - the constants `EXTRACT_LEAD_JOB = "extract_lead"`, `ROUTE = "extract_signals"`, `BUDGET_CLASS = BudgetClass.BLOCKABLE`, `MAX_QUOTE = 300`, `MAX_VALUE = 200`, `MAX_SIZE_NUMBER = 1_000_000` and `PAGE_TEXT_BUDGET = 180_000` characters;
    - the enums `EvidenceSource` (`website`, `ats`, `registry`, `signal_inbox`, `csv`, `referral`, `operator`), `FactKind` (the 3 profile-fact and 5 size-clue kinds of `ExtractSignalsV1`) and `ExtractionOutcome` (`ok`, `refused`, `truncated`, `invalid`, `failed`, `no_activation`, `no_pages`, `lead_merged`);
    - and these functions:
      - `allot(texts, budget)`: water-filling. Each page gets an equal share of the budget, and what short pages leave over is shared among the rest. A page is cut to its share, never dropped.
      - `stated_date(quote, value)`: is the date written in the quote? See Decision 4.
      - `number_in(number, quote)`: does a digit run in the quote equal `number`? Comma thousands separators are allowed.
      - `validate(parsed, segment, pages, now)`: the validated observations, the discards and the dropped dates. `pages` maps each page id sent to its URL, `fetched_at` and full stored text.
      - `reconcile(live, proposed, texts)`: a plan of rows to confirm, insert, supersede and retract.
      - `headquarters(locations)`: the zone and its Evidence id, or `None`.
  - **`nextron/domain/geo.py`** gains `place_in(text) -> Location | None`, the parser that Decision 8 describes.
- **Validation (AD-18).** `signal_type` is stripped and lowercased first. A proposal is kept only when all of these hold:
  1. Its `block_id` is a page sent in this call.
  2. Its quote, normalised with `quotes.normalise`, is between 1 and 300 characters and passes `quote_in` against that page's full stored text.
  3. **A Signal:** its type is one of the Lead's Segment's quotable types, i.e. its Evidence includes `quote`.
  4. **A size clue:** `1 ≤ number ≤ MAX_SIZE_NUMBER` and `number_in(number, quote)`.
  5. **A profile fact:** `value`, stripped, is between 1 and 200 characters.

  Every refusal appends one `events` row. The fields are:
  - entity `leads`, the Lead's id;
  - `to_status`: `signal_discarded`, `profile_fact_discarded`, `size_clue_discarded` or `event_date_discarded`;
  - `cause`: `unknown_signal_type`, `unknown_block`, `quote_empty`, `quote_too_long`, `quote_not_in_page`, `number_out_of_range`, `number_not_in_quote`, `value_invalid`, `date_not_in_quote` or `date_in_future`;
  - `cause_ref`: the cited page id when it is one of the pages sent;
  - actor: `tuple_.actor`.

  A duplicate (same claim, page and normalised quote) is dropped without an event. `job_postings` are ignored (Story 2.11).
- **Migration `0022_2_9_signals_evidence`** (down revision `0021_4_2_campaigns`; `downgrade()` raises). Owner `research` throughout. Every model, `owners.py`, `pii_registry.py` and `models/__init__.py` match it.
  - **`evidence`:**
    - `id`, `lead_id` (FK `leads`, indexed), and `type` (`quote`, `absence` or `count`);
    - `quote` (normalised, ≤ 300), `sought` (≤ 300) and `number`;
    - `fact` (a `FactKind` or NULL) and `value` (≤ 200);
    - `pages_checked` (a JSON array of URLs), `source` (an `EvidenceSource`), `source_url` and `retrieved_at`;
    - `extracted_by` (`rule`, `operator`, or `ai:<route>@<version>`);
    - `person_contact_id` (FK `contacts`), `superseded_by` (FK `evidence`), `retracted_at`, `last_confirmed_at` and `created_at`.

    Named CHECKs enforce the shape of each type:
    - a quote has a quote and no `sought`;
    - an absence or count has a `sought` and no quote;
    - a count has a number, and an absence has none;
    - a profile fact has a value, and only a profile fact has one;
    - a size fact has a number;
    - `superseded_by` and `retracted_at` are never both set.

    Triggers, following the `company_facts` precedent:
    - `trg_evidence_no_delete`;
    - `trg_evidence_immutable`: every column except `lead_id`, `superseded_by`, `retracted_at` and `last_confirmed_at` is fixed, and `superseded_by` and `retracted_at` can't change once set.

    PII: `quote` and `value` are `redact`; everything else is `none`.
  - **`signals`:** `id`, `lead_id` (FK, indexed), `signal_type` (64), `signal_class` (`event` or `attribute`), `tier` (1–3), `score_only`, `event_date` (Date; an Event only), `first_seen_at` and `retracted_at`.
  - **`signal_evidence`:** `id`, `signal_id` and `evidence_id` (FKs; unique pair; indexed on `evidence_id`).
  - **`extraction_runs`:** `id`, `lead_id` (FK, indexed), `crawl_run_id` (FK, nullable), `route`, `actor` (nullable), `outcome` (an `ExtractionOutcome`), `error_code`, `pages_sent`, `pages_truncated`, `stored` and `discarded` (≥ 0), and `started_at` and `ended_at`.
  - **`leads`:** the profile group, owner `research`, gains nullable `hq_tz` (64) and `hq_evidence_id` (a ULID with no FK, because an FK would create a leads↔evidence cycle). They are added with `batch_alter_table("leads")` and `add_column` only, with no rebuild and no CHECK, so code keeps them NULL together.
- **`SignalExtractions(clock, meter, llm, research)`** lives in `nextron/app/research/extraction.py`, and the `api.py` docstring points to it. `@use_case extract_lead(lead_id, run_id) -> bool` runs in three phases:
  1. **First transaction.**
     - When `allow(BUDGET_CLASS, meter.snapshot(now), budget_limits())` refuses, it returns `False` and nothing else happens.
     - An unknown or merged Lead logs `extraction_skipped` and returns `True`.
     - Otherwise it reads the Segment and `active_tuple(ROUTE)`. With no tuple, it records a `no_activation` run.
     - **Pages** are those with text whose `changed_at` is later than the `started_at` of the Lead's last `ok` run (every page with text when there is none). With no page, it records a `no_pages` run.
     - **Blocks.** Pages go in `PageKind` order, then by URL, cut by `allot`. The call's blocks are `[UntrustedBlock(f"lead:{id}", "signal_types", "signal_types", signal_catalogue([segment])), *UntrustedBlock(f"lead:{id}", "website", page.id, text)]`.
  2. **The call**, with no transaction open: `llm.run(tuple_, blocks, Urgency.BULK)`.
     - On an `AdapterError`, it records a `failed` run (its code) in a transaction and re-raises. The runner retries only a retryable one.
     - `refused`, `truncated`, `invalid` or `LlmFailed` records that outcome (the code, or the category for a refusal), stores nothing and returns `True`.
  3. **Apply transaction** after `ok`.
     - A Lead merged meanwhile records `lead_merged` and stores nothing.
     - Otherwise it validates and reconciles each page sent (Decisions 6 and 7), stores rows with `source=website`, `extracted_by=tuple_.actor`, `retrieved_at`/`last_confirmed_at` = the page's `fetched_at` and `pages_checked=[url]`, and links Signals (Decision 3).
     - **Retraction.** It retracts every non-retracted Signal left with no live Evidence.
     - **Notifications.** It publishes `EvidenceChanged(lead_id, evidence_id)` for each Evidence superseded or retracted.
     - **Headquarters.** It recomputes the headquarters from all live `location` Evidence. On a change it writes `hq_tz` and `hq_evidence_id` and calls `research.resolve_market` for each of the Lead's Contacts.
     - **Run record.** It records the `ok` run with its counts and logs `signals_extracted`, with IDs and counts only.
- **`Research._resolve`** passes the Lead's stored `hq_tz` as `lead_hq_tz`. **`Research.reparent_lead`** also moves the merged Lead's `evidence` and `signals` rows; `extraction_runs` and `hq_*` stay.
- **`WebsiteCrawls._apply`.** When the run stored at least one page and the Lead isn't merged, it enqueues `extract_lead`:
  - scope: the Lead; discriminator: `run_id`;
  - payload `{v, lead_id, run_id}`;
  - concurrency key `domain:<site>`.
- **The job type** `extract_lead`:
  - lanes: bulk by default, or interactive;
  - priority 45, `max_runtime` 15 min, retry limit 3;
  - group `enrichment`;
  - `handlers.extract_lead(extractions)` returns `DEFERRED` on `False`.
- **`main.build_app`** takes `llm: LlmPort | None = None`. Without one, it imports `ClaudeGateway` inside the function and builds `ClaudeGateway(secrets, meter=meter)`, so importing `nextron.main` still loads no AI package.
- **The Lead page.**
  - `create_app(..., lead_signals=LeadSignals(clock))`. Its `view(lead_id)` reads the clock once.
  - **Header:** the HQ timezone, or "—".
  - **Signals:** every non-retracted Signal, showing its type name (from the Segment, else the key), Event or Attribute, "Tier N", its event date or "first seen <date>", its age in days, Live or Expired, and a "Score only" marker.
  - **Evidence:** every live Evidence row, showing its type, its quote (or what was sought or counted, with the number and the pages checked), what it supports, its source URL as a `safe_link`, its retrieval time, and "AI-extracted" when `extracted_by` starts with `ai:`.
  - All untrusted text renders through `|plain` inside `hx-disable` containers, and the sections hold no HTMX action.

**Never:**
- No real Claude request in any test (use `ScriptedLlm`).
- No edit to `SHIPPED_CANDIDATES`, the v1 schemas, the prompts or the gateway.
- No `nextron.adapters` import in `nextron.app`.
- Nothing from Stories 2.6, 2.10, 2.11 or 2.15: no contact methods or widgets, no people, no job postings, no scores.
- No prompt text, page text, quote or URL in a log line, `events` row, error or job payload.

### Decisions made overnight (pending user review)

1. **"Contact form only" is deferred to Story 2.6.** That AC needs the contact methods that Story 2.6 detects from scripts before stripping, and 2.6 isn't built. Stored text alone would assert a false Absence when a chat widget exists. 2.9 builds only the Absence shape of `evidence`. The rule moves to Story 2.6, logged in `deferred-work.md`.
2. **Profile facts and size clues are Evidence rows** with `fact` (plus `value` or `number`). The live rows are the Lead's current facts, so there is no separate facts table.
3. **Signal identity.** An Attribute has one non-retracted Signal per type. An Event has one per type and stated event date, with undated Events sharing one. Tier, class and score-only come from the Segment when the Signal is created. `first_seen_at` is the first Evidence's `retrieved_at`.
4. **Event dates.** A date counts only when the quote writes it:
   - **Full date:** ISO, `14 October 2026`, `October 14, 2026` (ordinal suffix optional), or numeric `D/M/YYYY` or `M/D/YYYY` with `/`, `.` or `-`.
   - **Month and year:** only for the 1st of that month.
   - **Year alone:** only for 1 January.
   - **Never** a date in the future.

   Otherwise the Signal is kept undated, with an `event_date_discarded` event. Attributes never carry a date.
5. **The page budget.** Changed pages share 180,000 characters, about 90K tokens at worst. A page cut to its share still counts as extracted.
6. **Reconciliation.**
   - **Scope.** Reconciliation runs per page sent and per claim (a Signal type or a `FactKind`).
   - **Identical quote.** A re-proposed identical normalised quote confirms the row.
   - **Quote still present.** A live quote still in the new text is confirmed too: code decides, not whether the model repeated it.
   - **Quote gone.** Remaining live rows whose quote is gone are paired, oldest first, with the remaining new proposals: the new row supersedes the old. Leftovers are inserted or retracted.
   - **Out of scope.** Pages not sent and pages missing from a crawl keep their Evidence (FR-61's re-check is later).
7. **No `EventClosed` on retraction.** `EvidenceChanged` already flags Drafts stale. `EventClosed` stays for postings that close (Story 2.11).
8. **Headquarters.** Places are parsed from the location quote, never from the model's `value`.
   - **US:** a `, XX` state code (optionally followed by a ZIP) is preferred over full state names, and "Washington, D.C." counts as DC.
   - **GB:** `United Kingdom`, `UK`, `U.K.`, `Great Britain`, `England`, `Scotland`, `Wales`, `Northern Ireland`, or a full UK postcode (excluding "New England" and "New South Wales").
   - **Unknown:** both countries, or two states, give `None`.
   - **Which quotes count.** When any location quote names a headquarters (`headquarter*`, `head office`, `HQ`, `main office`, `principal office` or `registered office`), only those quotes count. The zones they give (`geo.location_zone`) must be exactly one; otherwise `hq_tz` stays NULL.
9. **The budget class.** It is the domain constant `BUDGET_CLASS`, and a contract test asserts that every `extract_signals` candidate has it, so `app` never imports `adapters.ai.routes`.
10. **Retries.** A non-retryable `AdapterError` (for example `llm_unknown_route`) is recorded, then raised, so the job goes dead and alerts. A returned `LlmFailed` completes the job.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Notes |
|---|---|---|---|
| First extraction | Law Lead, 3 pages, `ok` with 1 valid Signal, 1 size clue and 1 location | 3 Evidence rows, 1 Signal linked, an `ok` run (`stored` 3), `hq_tz` set when the location resolves | every page sent |
| Unknown type or quote | `signal_type: "made_up"`, a quote not in the page, a 301-character quote | nothing stored; one `events` row each, with the cause codes | the IDs only |
| Size clue | `number` 12 with "twelve lawyers"; `number` 0 | discarded: `number_not_in_quote`, `number_out_of_range` | |
| Re-extraction | the same quote re-proposed on a changed page | only `last_confirmed_at` changes | no `EvidenceChanged` |
| Changed or vanished | the old quote is gone and a new quote has the same claim; a second old quote is gone and has no proposal | the new row supersedes the old; the second is retracted; a Signal left with no live Evidence is retracted; `EvidenceChanged` for each | |
| No work | no activation; no changed page; the budget refuses | `no_activation` or `no_pages` run, no call; `DEFERRED` with nothing written | |
| Bad result | `refused`, `truncated`, `invalid` or `LlmFailed` | that run outcome; nothing stored; the job completes | |
| Retryable | `AdapterError(True)` | a `failed` run, then re-raised | the runner retries |
| Merged meanwhile | the Lead is merged during the call | `lead_merged` run; nothing stored | |
| Lead page | Signals of Tier 1 at 31 days and Tier 2 at 91 days | Live with age 31; Expired with age 91; "AI-extracted" on their Evidence | escaped |

</frozen-after-approval>

## Code Map

- **The crawl** (`nextron/app/research/api.py`):
  - `WebsiteCrawls._apply` (line ~834) enqueues `canonicalize_lead` with `lead_job_keys`; copy that for `extract_lead`.
  - `Research._resolve` (line ~382) passes `lead_hq_tz=None` today.
  - `Research.reparent_lead` (line ~464).
- **Crawl data:**
  - `db/repos/research/crawl.py` gives `StoredPage` and `pages(lead_id)` (with `text` and `changed_at`);
  - `domain/crawl.py` gives `PageKind` order, `PAYLOAD_VERSION`, `LEAD_ID_FIELD`, `RUN_ID_FIELD` and `lead_job_keys`.
- **AI:**
  - `queries.playbook.active_tuple`;
  - `domain/ai.py` (`UntrustedBlock`, `Urgency`, `RouteTuple.actor`);
  - `ports/llm.py` results (`LlmOk.parsed`, a JSON-ready dict with ISO date strings);
  - `domain/segments.py` (`signal_catalogue`, `SignalType`, `SignalClass`, `EvidenceType`, `SizeClue`);
  - `domain/quotes.py` (`normalise`, `quote_in`);
  - `adapters/ai/schemas.py` (the field names only; don't import them in `app`);
  - `tests/fakes/llm.py` `ScriptedLlm`.
- **Budget:** copy `WebsiteCrawls.enrich_lead`'s first transaction (`setup_queries.budget_limits()`, `meter.snapshot`).
- **Segment lookup:** add `queries.setup.segment_by_id(segment_id)`, which needs a `segments_repo.get(id)`.
- **Geography:** `domain/geo.py` (`location_zone`, `us_state_zones()` and the state names in `geodata/us_state_timezones.csv`, `Location`). `domain/contact_market.py` already applies `lead_hq_tz`.
- **Persistence precedents:**
  - triggers: `versions/0017_4_3_company_facts.py`, with its trigger-existence test in `tests/contracts/test_persistence_contracts.py` (~911);
  - tables: `versions/0020_2_5_crawl.py` and `db/models/crawl.py`;
  - the PK rule: each table's PK is a single ULID `id`, so `signal_evidence` needs its own `id`;
  - `eventlog.append`;
  - `uow.publish`;
  - `notifications.EvidenceChanged`.
- **Head bumps to `0022_2_9_signals_evidence`:**
  - `tests/contracts/test_persistence_contracts.py` `HEAD` and its chain probes;
  - `tests/unit/test_migrations.py`;
  - `tests/app/test_serve_process.py`;
  - `tests/unit/test_serve.py` (`00NN_more` probes);
  - `test_source_tree.FILES`;
  - `lead_scoped_columns` in `test_every_owner_of_a_lead_scoped_row_reparents_it_on_a_merge`.
- **Jobs:**
  - `jobs/handlers.py` (copy `enrich_lead`'s handler);
  - `main.build_job_types` (~919) and every caller (grep `build_job_types(`: `tests/dashboard.py` and about 10 test files);
  - `test_job_contracts` `names()`/`specs()`;
  - the health panel and engine-health tests that list the Enrichment group.
- **Wiring:**
  - `main.build_app` (~787: `crawls = WebsiteCrawls(...)`);
  - `tests/unit/test_ai_gateway.py:1021` (keep the assertion and update the docstring: `build_app` builds the gateway lazily);
  - `tests/dashboard.py`, the only caller of `create_app`.
- **Web:**
  - `web/app.py` `lead_page` (~993);
  - `web/leads_page.py` (`lead_context`, `day`, `safe_link`);
  - `templates/lead.html` (the header and the Contacts table carry `hx-disable`).
  - Don't touch `app.css` unless you must, and then edit it byte-preserving.
- **Docs:** `nextron-engine/README.md`: an "Signals and Evidence" section after the crawl's section, plus the job list.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/domain/signals.py`, `domain/extraction.py`, `domain/geo.py` (`place_in`) -- the pure rules above, with their unit tests (`tests/unit/test_signals_domain.py`, `test_extraction_domain.py`, `test_geo.py`), including the 29/30/31/89/90/91 boundaries, each matrix row of `validate` and `reconcile`, the `stated_date` forms, `allot`, and the place and HQ cases.
- [x] `nextron-engine/nextron/db/alembic/versions/0022_2_9_signals_evidence.py`, `db/models/signals.py`, `db/models/leads.py`, `models/__init__.py`, `owners.py`, `pii_registry.py` -- the tables, columns, CHECKs and triggers.
- [x] `nextron-engine/nextron/db/repos/research/signals.py` and `profile.py`, and `nextron/app/queries/research.py` (`signals`, `evidence`, `hq_tz`, `last_ok_extraction`) and `queries/setup.py` (`segment_by_id`) -- reads and writes. Inserting a Signal without at least one Evidence id raises `ValueError` (the repository test).
- [x] `nextron-engine/nextron/app/research/extraction.py` (`SignalExtractions`, `LeadSignals`) and `app/research/api.py` (the enqueue, `_resolve`, `reparent_lead`, the docstring) -- the use case.
- [x] `nextron-engine/nextron/jobs/handlers.py` and `nextron/main.py` -- the handler, the job type, the gateway and the services.
- [x] `nextron-engine/nextron/web/app.py`, `web/leads_page.py`, `templates/lead.html` -- the sections and the header line.
- [x] Tests:
  - `tests/app/test_signal_extraction.py`: every I/O row through `ScriptedLlm`, the blocks sent (the catalogue first, the page ids, the subject, the cut pages), the `hq_tz` change re-resolving Contacts, reparenting, and the enqueue from `enrich_lead`;
  - the repository and trigger tests;
  - a Lead page test;
  - the budget-class contract test;
  - the head bumps, `FILES` and the job-type lists.
- [x] `nextron-engine/README.md` and `_bmad-output/implementation-artifacts/deferred-work.md` (Decision 1, and the Story 3.2 `hq_tz` entry marked done) -- docs.

**Acceptance Criteria:**
- Given the full suite, when `pytest` and `lint-imports` run, then everything passes with every `ai_eval` test skipped, and every contract is kept.
- Given `serve` on a fresh data dir, when it starts, then no AI or HTTP client is built and no usage is recorded.

## Implementation Notes

Built as specified, with one deviation the architecture forced (the job key). What the spec left
open, and where the code had to choose:

- **The `extract_lead` job key (deviation).** The spec's "discriminator: `run_id`" can't be
  built: AD-10's key rule (`nextron.domain.job.idempotency_key`, the spine's "a revision, a date
  or `tick`") refuses a ULID discriminator, and `enqueue` would raise `InvalidJob`. The
  discriminator is the Lead's number of crawl runs once the new run is inserted
  (`crawl_repo.run_count`), a revision that grows by one with each run of a Lead that isn't
  merged, so each crawl run still gets its own job (`extract_lead:<lead>:<n>`), as the spec
  intends. The payload still carries `run_id`. A date discriminator was rejected: a second
  crawl the same day would be absorbed by a running extraction and its changes missed.
- **`nextron/domain/signals.py`.** `age_days` counts UTC calendar dates (`now`'s UTC date minus
  the event date, or minus `first_seen_at`'s UTC date). The arguments are type-checked
  (`TypeError`; a Tier outside 1 to 3 or a negative age is `ValueError`).
- **`nextron/domain/extraction.py`.**
  - Besides the spec's names it holds `PROFILE_FACTS` and `SIZE_FACTS`, `Discarded` (the four
    `to_status` codes) and `DiscardCause` (the ten causes), `EXTRACTED_BY_RULE` and
    `EXTRACTED_BY_OPERATOR`, the block names `SIGNAL_TYPES_BLOCK` and `WEBSITE_BLOCK`, and the
    values `SentPage`, `Claim` (a Signal type key or a `FactKind`), `Observation`, `Discard`,
    `Validation`, `LiveEvidence`, `Superseding`, `Plan`, `LocationQuote` and `Headquarters`.
    Page text, quotes and values never show in a `repr`.
  - `allot` gives each text, shortest first, `budget // texts left` (at most its length), so
    integer division's leftover goes to the longest texts; a budget below the number of texts
    gives some texts `""` (never dropped).
  - `stated_date`: full month names only (any case; no abbreviations), years 1900 to 2099,
    ASCII digits; an ordinal suffix and an `of` (`14th of October, 2026`) are allowed; the
    numeric form needs the same separator twice; a mention is read once, its fullest form
    first, so a year inside a full date isn't also a year alone; a year alone must not touch a
    letter or digit (`2026x`, `12026` aren't years). A day that doesn't exist (`31/02/2026`)
    matches nothing.
  - `number_in` reads maximal ASCII digit runs, with `,` thousands groups of three digits
    (`1,200` is 1,200, never 200; `12,34` is 12 and 34).
  - `validate` checks in the spec's numbered order: block, then quote (empty, too long, not in
    the page), then the Signal type, then the number or value. A proposal is checked before
    its repeat is looked for, so each refused repeat still writes its own `events` row; only a
    kept repeat (same claim, page and normalised quote) is dropped silently. The Event date is
    checked against the quote first (`date_not_in_quote`), then against `now`'s UTC date
    (`date_in_future`; today counts as not future). An Attribute's proposed date is ignored
    with no record (the closed cause list has no code for it). A dropped date is not counted in
    the run's `discarded` (the Signal was kept). An answer that isn't the schema's shape (a
    missing list, an unknown fact kind, a non-integer number, a malformed date) raises
    `ValueError`; the gateway's schema validation makes it unreachable in practice.
  - `reconcile` groups by (URL, claim) in URL order, then Signal claims before fact claims (each
    by name); "oldest first" is `created_at`, then `id`. Rows created in one transaction share
    `created_at`, so their id order (a ULID's random part within a millisecond) decides.
  - `headquarters`: a counted quote with no place, or with a place that has no zone, gives no
    zone; when the zones agree, the Evidence is the first quote (oldest first) that gives the
    zone.
- **`nextron/domain/geo.py`.** `us_state_names()` (code to name, read once from the same
  table; `parse_us_state_zones` is unchanged). `place_in` normalises the text first; a state
  code must be in capitals after a comma and not followed by a letter or digit;
  `Washington, D.C.` (the comma and the dots optional, `Washington DC` too) counts as a code;
  full names are matched as whole words in any case, longest first; `UK` and `U.K.` and a full
  postcode (the space optional) must be in capitals, the British names may be in any case. A
  US state name next to a British marker (`Washington Street, London, UK`) is "both
  countries": `None`, the conservative side.
- **The migration** (`0022_2_9_signals_evidence`, down `0021_4_2_campaigns`).
  - Lengths: `type` 8, `quote` and `sought` 300, `fact` 32, `value` 200, `source` 16,
    `source_url` 2,048, `extracted_by` and `actor` 128, `signal_type`, `route` and `hq_tz` 64,
    `signal_class` 16, `outcome` 32, `error_code` 64.
  - `source_url` is nullable, so an operator's or a rule's Evidence (an Absence lists its
    pages in `pages_checked`) needs no URL; every website row has one.
  - The CHECKs beyond the spec's shapes: `ck_evidence_type`, `_source`, `_fact`,
    `_extracted_by` (`rule`, `operator` or `GLOB 'ai:?*@?*'`), `_lengths` (quote and sought 1
    to 300, value 1 to 200), `_number` (at least 0); `ck_signals_signal_class`, `_tier`,
    `_event_date` (an Event's only); `ck_extraction_runs_outcome` and `_counts`. The shapes are
    `ck_evidence_quote_shape`, `_sought_shape`, `_number_shape`, `_value_shape`, `_size_number`
    and `_not_superseded_and_retracted`.
  - Only the spec's two triggers (`trg_evidence_no_delete`, `trg_evidence_immutable`); no
    `no_id_reuse` trigger. **For Story 3.14:** `quote` and `value` are PII `redact`, and the
    immutability trigger refuses that UPDATE, so erasure must amend the trigger (or redact
    through a new migration's exception).
  - `leads.hq_tz` and `hq_evidence_id` are added with `batch_alter_table("leads")` and
    `add_column` (SQLite adds them in place) and mapped in `db/models/leads.py`.
- **Repositories.** `db/repos/research/signals.py` (`insert_evidence` takes `evidence_type` for
  the `type` column; `confirm_evidence`, `supersede_evidence`, `retract_evidence`,
  `evidence(lead_id, live_only=)`, `insert_signal(..., evidence_ids)` (repeated ids are linked
  once; none raises `ValueError` before any write), `link`, `signals(lead_id, current_only=)`,
  `links`, `retract_unsupported`, `insert_run`, `last_ok_run`, `runs`, `reparent`) and
  `db/repos/research/profile.py` (`headquarters`, `set_headquarters`); `crawl.run_count`;
  `setup/segments.get`.
- **Queries.** `queries.research` also gives `extraction_runs(lead_id)` and
  `lead_signals(lead_id, now, type_names) -> LeadSignalsView(hq_tz, signals, evidence)`, with
  `SignalStatus(signal, name, age, live)` and `EvidenceSupport(evidence, supports)`. They live
  in the queries module so the Lead page's view module keeps Story 2.4's rule (it imports from
  `nextron.app` only `nextron.app.queries.*`); `LeadSignals.view` reads the clock once, the
  Segment's names, and calls it. A stored class or Tier the domain refuses shows as Expired.
- **`SignalExtractions`.**
  - `crawl_run_id` is the payload's `run_id` only when that crawl run exists.
  - The live rows in scope are `quote` rows with `source` `website`, an `extracted_by` starting
    `ai:extract_signals@` (any prompt version) and a `source_url` among the pages sent. A
    Signal row's claim is the type of the first Signal it supports (by Signal id); a live row
    that supports no Signal and has no fact is left alone.
  - A confirmation sets `last_confirmed_at` to the page's `fetched_at`, never earlier than it
    was.
  - A new row's Signal identity uses the Segment's current class of its type; a new Signal's
    `first_seen_at` is the earliest `retrieved_at` of the rows that create it.
  - `EvidenceChanged` goes out after the writes: superseded rows first, then retracted ones,
    each in plan order.
  - The headquarters "change" is the (`hq_tz`, `hq_evidence_id`) pair differing from the
    stored pair; then both are written and every Contact of the Lead is resolved again.
  - Every log line is written after its transaction commits: `extraction_skipped`
    (`lead_id`, `reason`: `unknown_lead` or `merged_lead`), `extraction_finished` for every run
    that stored nothing (`lead_id`, `run_id`, `outcome`, `error_code`) and `signals_extracted`
    (`lead_id`, `run_id`, `pages_sent`, `pages_truncated`, `stored`, `confirmed`, `superseded`,
    `retracted`, `discarded`, `dates_dropped`, `signals_created`, `signals_retracted`,
    `hq_changed`).
- **Jobs and wiring.** `handlers.extract_lead` checks both payload ids (`InvalidJob` when one is
  missing or not a ULID). `build_job_types` takes `extractions=`; every caller passes it (the
  test files and `tests/dashboard.py`). `build_app(..., llm=None)` imports `ClaudeGateway`
  inside the function and builds `ClaudeGateway(secrets, meter=meter)`; `create_app` takes
  `lead_signals`.
- **The Lead page.** The header's "HQ timezone" sits after Country. The Signals table's columns
  are Signal, Class, Tier, Date, Age, Status and Score only (empty when not score-only); the
  Evidence table's are Type, Evidence (`<q>` around a quote; "Sought: <what>" or "Counted:
  <what>: <n>" with a `pages-checked` list of `safe_link`s), Supports (the Signals' names
  joined with "; ", else "<fact label>: <value or number>", else "—"), Source (a `safe_link`
  or "—"), Retrieved (`YYYY-MM-DD HH:MM UTC`) and Extraction ("AI-extracted" or "—"). Empty
  sections say "No Signals yet." and "No Evidence yet.". Each section element carries
  `hx-disable`. `CLASS_LABELS`, `EVIDENCE_TYPE_LABELS` and `FACT_LABELS` name every value.
- **Tests.** `tests/unit/test_signals_domain.py`, `test_extraction_domain.py`, `test_geo.py`
  (names and `place_in`); `tests/app/test_signal_extraction.py` (every matrix row through
  `ScriptedLlm` on pages a real crawl stored, the blocks, the budget cut, the handler, the
  enqueue, the Contacts re-resolved, reparenting, `LeadSignals`), `tests/app/test_signal_store.py`
  (the repository, the triggers and every CHECK); the Lead page tests in
  `tests/unit/test_leads_page.py`; `tests/contracts/test_extraction_contracts.py` (the budget
  class and the fact kinds against the schemas); the head bumps, `FILES`, the trigger set, the
  Lead-scoped columns, and the job-type lists and shapes (`test_job_contracts`,
  `test_runner_lifespan`, `test_engine_health`, `test_health_page`); `test_website_crawl`'s job
  assertions now include `extract_lead`.

**Implementation verification** (targeted; the full suite is the orchestrator's gate): the 30 new and touched test files, 1881 passed; then `tests/contracts` with `test_website_crawl.py` and `test_signal_extraction.py` on the final tree, 1203 passed and 1 skipped (the opt-in `ai_eval` check). `lint-imports`: 29 kept, 0 broken. No real Claude request was made.

**Review fixes (pass 1):**

- **Abbreviated months.** `stated_date` reads a month's three-letter abbreviation and `Sept`
  (each with an optional `.`, any case, whole word) wherever it reads a full name: `Oct 14,
  2026`, `14 Sept. 2026`, `Sep. 2026`, `Jan 2026`. `Mayor`, `Marching` or `Septem` are no
  months (`MONTH_NUMBERS`).
- **Decimals.** `number_in` reads maximal digit runs with their commas and points: a run with a
  decimal point (`1.5`, `12.25`, `1,200.5`) gives no whole number at all; a run of thousands
  groups is one number; other commas split the run (`12,34` is 12 and 34, as before).
- **Control characters in a value.** A profile fact's stripped `value` holding any `Cc`
  character (NUL, a line break, a tab, DEL) is `value_invalid`, so `ck_evidence_lengths` (which
  SQLite's `length()` stops at NUL) can no longer abort the apply transaction.
- **Tests.** Unit tests for each abbreviated form and the look-alike words, the decimals and the
  control characters; app tests for a dated Event linking to an existing Signal of the same type
  and date while another date creates a second Signal, the reconciliation scope (an earlier
  prompt version's website quote is reconciled and retracted, an operator's is left live and
  unconfirmed), a vanished location re-resolving the Contacts back to their fallback zone, and
  the `signals_extracted` and `extraction_finished` log lines (each once, exactly the documented
  keys, no page words or URLs); the Lead page test renders a `count` row ("Counted: <what>:
  1,250" and its pages checked).

**Final verification** (on the final tree, after the review fixes):
- Targeted run (the new and touched test files with `tests/contracts`): 1555 passed and 1 skipped.
- Full suite through `suite_lock.py` (track-E):
  - The first run failed only `tests/unit/test_heartbeat_writer.py::test_a_hung_handler_stops_the_beat_until_it_returns`. The cause was load timing: the test waits 2 s for a runner thread under `-n 6`. It builds its own job types and touches no Story 2.9 code, and it passed 3 of 3 runs alone.
  - The single re-run gave 10728 passed and 3 skipped (the two `ai_eval` tests and the symlink test).
- `lint-imports`: 29 kept, 0 broken.
- No real Claude request was made.

## Spec Change Log

- **Implementation, 2026-11-18: the `extract_lead` job key (Decision 11, overnight, pending user review).**
  - **Trigger:** the frozen Boundaries name the discriminator `run_id`. AD-10's key rule (`domain.job.idempotency_key`) accepts only `tick`, a date or a revision, so `enqueue` would raise `InvalidJob`.
  - **Change:** the discriminator is the Lead's crawl-run count once the new run is inserted (`crawl_repo.run_count`). It grows with every run, so each crawl run still gets its own `extract_lead:<lead>:<n>` job, and the payload still carries `run_id`.
  - **Avoids:** with a date key, a second crawl on the same day would be absorbed by a live extraction and its changes missed.
  - **KEEP:** one extraction job per crawl run.
  - The frozen text is unchanged; Mac reviews it with the overnight decisions.

## Review Triage Log

Pass 1 (blind hunter BH, edge-case hunter EC, verification gap VG): 29 raw findings, deduplicated to 26 entries. 9 patched, 1 deferred and 16 rejected. Of the rejections, 9 are spec-mandated; 6 of those get `deferred-work.md` entries. Story precedent logs spec-rejected items there.

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | VG1: no test links a new dated Event to an existing Signal across runs (`_link`, the date check at `extraction.py:546`). | medium | Pre-verified: every cross-run test is undated, so dropping the date check passes. | patch (test) |
| 2 | VG2: the reconciliation scope rule (any `extract_signals` version; other sources untouched) is untested. | medium | Pre-verified: every row in the tests shares one actor and source. | patch (test) |
| 3 | VG3: no test shows a cleared headquarters re-resolving Contacts. | medium | Pre-verified: the clearing test has no Contact. | patch (test) |
| 4 | VG4: the `signals_extracted` and `extraction_finished` log lines are unchecked (keys, no text). | medium | Pre-verified: only `extraction_skipped` is asserted. | patch (test) |
| 5 | VG5: the "Counted" display of Count Evidence is never rendered. | low | Pre-verified; a cheap fixture row fixes it. | patch (test) |
| 6 | BH8: abbreviated month names (`Oct 14, 2026`, `14 Sept 2026`, `Sep. 2026`) aren't read, so a stated date is dropped. | medium | True: `MONTHS` holds full names only. A dropped date makes an old news Event age from first seen, so it looks fresh. Decision 4 doesn't exclude abbreviations. | patch |
| 7 | EC5: `number_in` reads the `5` of `1.5 million` as a number. | low | True: `_NUMBER` has no decimal guard. Direct regex correction. | patch |
| 8 | EC8: a profile-fact `value` holding U+0000 makes `length()` 0, so `ck_evidence_lengths` aborts the apply and the job dies with no run row. | medium | True: SQLite `length()` stops at the first NUL, and `validate` only checks `1 <= len(value) <= 200`. Rare. | patch: a `value` with a control character is `value_invalid` |
| 9 | BH14 + EC10: the consequences of deferred decisions aren't logged (Evidence on vanished pages stays live, the cut-off tail of a truncated page). | low | True: `deferred-work.md` gained only the Story 2.6 entry. FR-61 (Epic 8) re-checks. | defer (entries written) |
| 10 | BH1: no `no_id_reuse` trigger, so `INSERT OR REPLACE` could overwrite Evidence. | low | No code path issues REPLACE (the repository uses plain inserts), and the spec lists two triggers. | reject |
| 11 | BH2: `quote` and `value` are PII `redact`, but the immutability trigger refuses that UPDATE; page URLs are `none`. | low | No erasure path exists yet, and it fails loudly when built. Page URLs are `none` like Story 2.5's `crawled_pages.url`. Noted for the erasure story. | reject |
| 12 | BH3 + EC1 + EC3: a merge can leave the survivor with duplicate current Signals of one identity, and its `hq_tz` isn't recomputed. | medium | True when both Leads were extracted (e.g. a Companies House merge). The spec says `reparent_lead` moves the rows and `hq_*` stay; folding is new behaviour, as Story 2.10 does for Contacts. | reject (spec-mandated) + deferred-work entry |
| 13 | BH4: an undated Event never gets younger, because a superseding or new undated quote joins the old Signal. | medium | True by Decision 3 (undated Events share one). It matters for hiring Events; Stories 2.11 and 2.15 date postings. | reject (spec-mandated) + deferred-work entry |
| 14 | BH5: a truncated, refused or invalid run has no recovery or visible outcome. | medium | True. The AC says only retryable failures retry and the outcome is recorded (`extraction_runs`); surfacing it is new UI. | reject (spec-mandated) + deferred-work entry |
| 15 | BH6: a missing Claude key (`credential_not_set`) kills every `extract_lead`, with one dead-job alert each. | medium | True: `DeadJobAlerts` alerts per job. Decision 10 makes a non-retryable `AdapterError` dead and alerting. | reject (spec-mandated) + deferred-work entry |
| 16 | BH7: `03/04/2026` accepts both readings. | low | Decision 4 accepts D/M and M/D. | reject (spec-mandated) |
| 17 | BH9: a bare "Washington" reads as WA; "Tbilisi, Georgia" as GA. | low | True but rare on US/UK firm sites, and the fix adds ambiguity branches. Decision 8 matches full names. | reject |
| 18 | BH10: Segment or prompt changes never re-send unchanged pages. | medium | True by the changed-page rule (FR-61: extraction only on changed pages). | reject (spec-mandated) + deferred-work entry |
| 19 | BH11 + EC7: an identical quote with a now-valid date, or a new value, only confirms the row. | low | Decision 6: an identical normalised quote confirms. | reject (spec-mandated) |
| 20 | BH12: a schema-valid answer the domain rejects raises without a run row; the contract test checks only v1 fact kinds. | low | Unreachable under v1 (`Literal` kinds are validated by the gateway). A v2 kind needs a `FactKind` change anyway. | reject |
| 21 | BH13: CHECKs don't require valid or non-empty `pages_checked`, `source_url` on website quotes, or a monotonic `last_confirmed_at`. | low | No writer produces those states; the app sets them. Adding guards for unshown states. | reject |
| 22 | EC2: pages moved to a survivor are never extracted for it. | low | Only URLs the survivor lacks with `changed_at` before its last `ok` run; rare. The fix changes the changed-page rule. | reject |
| 23 | EC4: `, CA` / `, IL` / `, PA` read as US states. | low | Decision 8 prefers `, XX` codes. Two codes give `None`, and a firm suffix usually sits beside its address. | reject (spec-mandated) |
| 24 | EC6: the repeat key ignores `event_date`, so a second dated Event on the same quote is dropped. | low | Rare. Keeping both needs date-aware dedupe and reconcile changes. | reject |
| 25 | EC9: a crawl under another domain key can commit a page fetched before an extraction's `started_at`. | low | Needs a canonicalization domain change plus overlapping jobs. The fix replaces the spec's cut-off rule. Covered by the BH10 deferred entry. | reject |
| 26 | EC10: Evidence on pages that vanished from the site stays live. | — | Same root as #9 (Decision 6). | grouped into #9 |

## Design Notes

```python
# extract_lead, phase 2 (no transaction open)
try:
    result = self._llm.run(tuple_, blocks, Urgency.BULK)
except AdapterError as error:
    self._record(lead_id, run_id, ExtractionOutcome.FAILED, error_code=error.code, ...)
    raise  # the runner retries only error.retryable
```

`reconcile` works per (claim, url). It confirms identical rows first, then rows whose quote is still present, then pairs the remaining gone rows with the remaining new proposals (supersede), and finally inserts or retracts what is left.

## Verification

**Commands:**
- `cd e:/Nextron-track5/nextron-engine && uv run pytest -q <touched test files> tests/contracts` -- expected: all pass.
- `cd e:/Nextron-track5/nextron-engine && uv run lint-imports` -- expected: all contracts kept.
