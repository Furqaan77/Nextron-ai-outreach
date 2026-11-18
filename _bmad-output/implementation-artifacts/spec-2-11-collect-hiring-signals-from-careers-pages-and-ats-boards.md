---
title: 'Story 2.11: Collect hiring Signals from careers pages and ATS boards'
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: '98d0250856afbbbec85eacec7f1897ba97314ab5'
review_loop_iteration: 0
story_key: '2-11-collect-hiring-signals-from-careers-pages-and-ats-boards'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-9-detect-signals-with-evidence-from-the-crawled-pages.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-10-find-named-decision-makers-and-pick-the-primary-contact.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Story 2.5 records the ATS links on each Lead's pages and Story 2.9 asks Claude for the job postings on them, but nothing stores a posting or turns "they're hiring for this problem now" into a dated Event. Story 2.10's Primary Contact choice (Controller for AP/AR hiring) and Story 2.15's scores need those Events, and a closed posting must stop counting.

Requirements: FR-7, FR-11, FR-49 · AD-7, AD-9, INT-10 · NFR-6.

**Approach:**
- **Careers pages.** `extract_lead`'s apply keeps the `job_postings` Claude returned for careers pages when the title is in the page text.
- **ATS boards.** A new `check_job_boards` job reads the board tokens from the Lead's recorded ATS links and calls the Greenhouse, Lever and Ashby public JSON endpoints through a new `JobBoardPort` adapter, inside the egress filter, metered as `ats`.
- **Events.** Postings go in a new `job_postings` table. A posting that matches a hiring Signal type's terms becomes one Event with rule-made Quote Evidence. A posting no longer listed closes and its Event stops being live (`EventClosed`).
- **Recheck.** `recheck_event` re-checks one posting.
- **Lead page.** It gains a Job postings section.
- **Migration** `0030_2_11_job_postings`.

The acceptance criteria are Story 2.11 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**

- **Pure domain** (standard library and `nextron.domain` only).
  - **New `nextron/domain/postings.py`:**
    - `CHECK_JOB_BOARDS_JOB`, `RECHECK_EVENT_JOB`, `SIGNAL_ID_FIELD`, `MAX_TITLE = 300` and `MAX_EXCERPT = 300`;
    - `PostingSource` (`careers_page`, `greenhouse`, `lever`, `ashby`), `PostingStatus` (`live`, `closed`), and `ATS_SOURCES`;
    - `BOARD_HOSTS`, read-only: `boards.greenhouse.io` and `job-boards.greenhouse.io` → greenhouse, `jobs.lever.co` → lever, `jobs.ashbyhq.com` → ashby;
    - `Board(source, token)`;
    - `board_from_link(url, ats_hosts) -> Board | None` (Decision 4) and `source_enabled(source, ats_hosts)`;
    - `excerpt(description, term=None) -> str | None` (Decision 8);
    - `PostingDiscardCause` (`unknown_block`, `not_careers_page`, `title_empty`, `title_too_long`, `title_not_in_page`);
    - `validate_postings(parsed, pages, careers_ids, now)`: the kept postings, the refusals and the dropped dates (Decisions 5 and 17).
  - **`nextron/domain/signals.py` gains `match_hiring(title, description, segment) -> HiringMatch(signal_type, in_title, term) | None`.**
    - The candidates are the Segment's types whose `sources` meet `HIRING_SOURCES`, in Segment order.
    - Each type's `match_terms` are tried in order. A term matches as a whole phrase, case-insensitively, on `quotes.normalise`d text: `_has_phrase` made public as `has_phrase`.
    - The title is searched for every type first, then the description.
- **Port and adapter.**
  - **`nextron/ports/job_board.py`:**
    - `BoardPosting(title, url, posted_date, description)` (text hidden from `repr`) and `BoardListing(postings)`;
    - `BOARD_NOT_FOUND` (404 or 410), `BOARD_UNAVAILABLE` (429 or 5xx, retryable), `BOARD_FAILED` and `BOARD_INVALID` (malformed JSON or shape), collected in `BOARD_CODES`;
    - `@runtime_checkable JobBoardPort.postings(board) -> BoardListing`.
  - **`nextron/adapters/ats/__init__.py`: `AtsBoards(*, meter, fetcher=None)`.** `fetcher` is a `JsonFetcher` protocol that `HttpCrawler` satisfies. With no fetcher, `default_fetcher(meter)` is looked up at call time, so the live guard can refuse it.
    - **The request.** `postings` builds the provider's endpoint (Decision 2) and calls `fetch_json(url, site=<the provider's registrable domain>)`, which:
      - goes through `fetch_resource`, accepting only `application/json` and `+json`;
      - uses operation `board` and `provider="ats"`;
      - is paced at `MIN_INTERVAL_S`, with no deadline beyond the fetch's own.
    - **The answer.** It decodes the JSON and maps it per provider:
      - **Greenhouse:** `jobs[]` with `title`, `absolute_url` and `first_published`, and `content` HTML-unescaped and reduced to visible text with `crawler.parsing.parse_html`.
      - **Lever:** a list with `text`, `hostedUrl`, `createdAt` (milliseconds) and `descriptionPlain` plus `additionalPlain`.
      - **Ashby:** `jobs[]` with `title`, `jobUrl`, `publishedAt` and `descriptionPlain`, skipping `isListed: false`.
    - **What it skips and caps.** A posting with no non-empty title, or no absolute http(s) URL of at most 2,048 characters, is skipped. A future or unreadable date is `None`. A description is cut to 20,000 characters.
  - **`HttpCrawler.fetch_resource` and `_fetch` gain a keyword `provider: str = PROVIDER`.** Nothing else in the crawler changes.
- **Migration `0030_2_11_job_postings`.** Its down revision is `0029_2_9_tag_managers`, and `downgrade()` raises. Everything is owned by `research`.
  - **`job_postings`:**
    - `id`, and `lead_id` (FK, indexed);
    - `source` (CHECK `PostingSource`) and `board_token` (100), NULL exactly for `careers_page` (CHECK);
    - `url` (2,048), `title` (300) and `description` (300, nullable);
    - `posted_date` (Date, nullable), `first_seen_at` and `last_checked_at`;
    - `status` (CHECK `PostingStatus`) and `closed_at`, set exactly when closed (CHECK);
    - `signal_id` (FK `signals`, nullable, indexed) and `evidence_id` (FK `evidence`, nullable);
    - unique `(lead_id, url, title)`.

    PII: `title` and `description` are `redact`; every other column is `none`.
  - **`crawl_runs` gains nullable Integers with no CHECK, added in place:** `careers_postings`, `careers_events`, `greenhouse_postings`, `greenhouse_events`, `lever_postings`, `lever_events`, `ashby_postings`, `ashby_events` and `board_errors`.
- **Storing postings** (shared by every path; `app/research/postings.py`, one transaction each).
  - **A new posting** is inserted `live` with `first_seen_at` = `last_checked_at` = the check time. For a careers posting, the check time is the page's `fetched_at`.
  - **A listed live posting** has `last_checked_at` updated, and its Evidence confirmed.
  - **A listed closed posting** reopens (Decision 10).
  - **The Event.** A new or reopened posting with a `match_hiring` match becomes one Event:
    - `insert_signal`: the type's class and Tier; `event_date` = `posted_date`; `first_seen_at` = the posting's.
    - Its one Quote Evidence:
      - `quote` per Decision 8;
      - `source` `ats` or `website`;
      - `source_url` and `pages_checked` `[url]`;
      - `retrieved_at` and `last_confirmed_at` = the check time;
      - `extracted_by` `rule`.
    - The posting's `signal_id` and `evidence_id` point to them.
  - **Closing** (Decision 9): `status` `closed`, `closed_at` = now, its live Evidence retracted, then `retract_unsupported`. It publishes `EvidenceChanged` for each retracted row and `EventClosed(signal_id, lead_id)` for each posting that had an Event.
- **Careers pages (in `SignalExtractions._apply`, after the Signals reconcile and before `retract_unsupported`).**
  - **Validation.** `validate_postings` runs on the answer's `job_postings`. Each refusal appends one `events` row with these fields:
    - `to_status` `job_posting_discarded`, or `job_posting_date_discarded` with 2.9's date causes;
    - `cause`;
    - `cause_ref` the page id when sent;
    - actor `tuple_.actor`.

    Refusals count in the run's `discarded`.
  - **Storage.** Kept postings are stored (source `careers_page`).
  - **The page rule.** On each careers page sent, a live posting not proposed stays live while its title still passes `quote_in` against the new text. Otherwise it closes.
  - **Counts.** It writes `careers_postings` (listed) and `careers_events` (created) on the extraction's crawl run when at least one careers page was sent.
  - **2.9's `validate`** refuses a Signal of a hiring type with the new cause `hiring_type` (Decision 14).
- **`JobBoards(clock, meter, boards, crawler, queue)`** (`app/research/job_boards.py`).
  - **`@use_case check_job_boards(lead_id, run_id) -> bool`:**
    1. **First transaction.**
       - `allow(BudgetClass.BLOCKABLE, …)` refuses: return `False`.
       - An unknown or merged Lead logs `job_boards_skipped`, enqueues nothing, and returns `True`.
       - Otherwise it reads `ats_hosts` and the boards (Decision 11).
    2. **The calls**, with no transaction open: `boards.postings(board)` for each board, sorted. An `AdapterError` is kept per board.
    3. **Apply.** A Lead merged meanwhile stores nothing. Otherwise:
       - an answer is applied to that board's postings;
       - `BOARD_NOT_FOUND` closes every live posting of the board;
       - any other error changes nothing and adds to `board_errors`.

       It writes the ATS counts on the crawl run (0 for a source with no board). It enqueues `select_primary_contact`, with scope the Lead, `tick`, `{v, lead_id}` and `domain:<web_domain>`. After the commit it logs `job_boards_checked` (IDs and counts only).
  - **`@use_case recheck_event(signal_id)`:**
    - **Skips.** It skips, logging `recheck_skipped` with a reason, when there is no posting with that `signal_id`, when the posting is closed, when the Lead is unknown or merged, or when the ATS posting's source isn't `source_enabled`.
    - **An ATS posting** re-checks its board through the same apply.
    - **A careers posting:**
      - It reads `<origin>/robots.txt` and applies the crawl's robots rule: `WebsiteCrawls._rule`, moved to a module function `robots_rule`.
      - Disallowed: skipped.
      - Allowed: `fetch_page(url, site=<registrable domain of url>, min_interval_s=<interval>)`. A 2xx closes or confirms that page's live postings by the page rule. A 404 or 410 closes them.
    - **Failures.** A retryable `AdapterError` is re-raised. Any other failure logs `recheck_failed` and changes nothing.
- **The chain.** `PeopleExtractions._select` becomes `_next_stage(lead_id, run_id, web_domain)`: at the same points, it enqueues `check_job_boards` with scope the Lead, `tick`, `{v, lead_id, run_id}` and `domain:<web_domain>`.
- **Jobs.**

  | Job | Lanes (default first) | Priority | Max runtime | Retries | Group |
  |---|---|---|---|---|---|
  | `check_job_boards` | bulk, interactive | 45 | 10 min | 3 | ENRICHMENT |
  | `recheck_event` | interactive, bulk | 50 | 5 min | 3 | RECHECK |

  - `check_job_boards` returns `DEFERRED` on `False`.
  - `recheck_event`: scope the Signal, `tick`, payload `{v, signal_id}`.
  - The handlers check the ULIDs.
- **Merges.** `reparent_lead` moves each posting whose `(url, title)` the survivor lacks. A colliding live posting stays on the merged Lead and is closed, with its Event, after the Evidence move.
- **The Lead page.** A "Job postings" section (`hx-disable`, `|plain`) shows title, source label, posted date, first seen and status. Live postings come first, then the newest first seen. With none, it says "No job postings yet.". It reads through `queries.research.job_postings`.
- **Wiring and guards.**
  - `build_app(..., job_board=None)` builds `AtsBoards(meter=meter, fetcher=http_crawler)` and passes `JobBoards` to `build_job_types(..., job_boards=)`.
  - `live_guard`: `ats: Guarded(("default_fetcher",))`.
  - c5 lists `nextron.adapters.ats`.
  - `PORT_FAKES`: `JobBoardPort` → `tests/fakes/job_board.py` `ScriptedJobBoards`.
  - `FETCHING_ADAPTERS` gains an `ats` driver on `fetch_json`.

**Never:**
- No test reaches the network (`ScriptedJobBoards`, `ScriptedCrawler`, or `httpx2.MockTransport` behind fake resolvers).
- No job site is ever queried. Board tokens come only from the Lead's own `ats_links`, and no setting or parameter loosens the egress filter or the endpoint table.
- No edit to the prompts, schemas, `SHIPPED_CANDIDATES` or the gateway.
- No title, description, URL, token or quote in a log line, `events` row, error or job payload.
- No email finding (2.13), legal form (2.12) or scoring (2.15).

### Decisions made overnight (pending user review)

1. **The chain.** `check_job_boards` takes `select_primary_contact`'s old place after the people step, and it enqueues selection itself, so selection sees the hiring Events. Careers postings are applied in `extract_lead`'s apply, because Claude's answer isn't stored. Story 2.12 inserts `check_legal_form` after `check_job_boards`.
2. **Endpoints are held in code.**
   - `ats_hosts` lists board hosts. A link is used only when its host is in `ats_hosts` **and** in `BOARD_HOSTS`.
   - The call goes to the provider's fixed public endpoint:
     - `https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`;
     - `https://api.lever.co/v0/postings/{token}?mode=json`;
     - `https://api.ashbyhq.com/posting-api/job-board/{token}`.
   - Redirects are pinned to the provider's registrable domain. A configured host with no endpoint in code is never called (INT-10: an endpoint must be checked first).
3. **The EU boards** (`job-boards.eu.greenhouse.io`, `jobs.eu.lever.co`) are not added. Their endpoints and terms are unchecked; this goes to `deferred-work.md`.
4. **Board tokens.**
   - **Greenhouse:**
     - on `/embed/job_board`, `/embed/job_board/js` or `/embed/job_app`, the `for` query value;
     - otherwise the first path segment, which must not be `embed`.
   - **Lever and Ashby:** the first path segment.
   - **The pattern:** a token must match `[A-Za-z0-9][A-Za-z0-9_-]{0,99}`; otherwise `None`.
5. **Careers postings.**
   - Only a `block_id` that is a sent page of kind `careers` counts (`not_careers_page` otherwise).
   - The title is `quotes.normalise`d, 1 to 300 characters, and passes `quote_in` against the page's full text.
   - The posting's `url` is the page's URL: the model's `url` can't be verified and is ignored.
   - The model's quote, when it is 1 to 300 characters and in the page, is the description. Otherwise the description is NULL.
   - A repeat of the same page and title is dropped silently.
6. **Identity is `(lead, url, title)`.** Careers postings share their page's URL, so "once per Lead and URL" includes the title.
7. **A posting is matched once,** when it is stored or reopened. Later checks only confirm it or close it.
8. **The quote.**
   - Matched in the title: the title.
   - Matched only in the description: the posting's stored description.
     - **ATS:** `excerpt(description, term)`, the normalised description when it is at most 300 characters. Otherwise it is a window of at most 300 characters, cut on whitespace, that contains the term's first whole-phrase occurrence.
     - **Careers:** the validated model quote.
   - An unmatched ATS posting stores `excerpt(description)`, the first 300 characters cut on whitespace.
9. **Closing.**
   - **What closes a posting:** a successful board answer that doesn't list its URL and title, or a 404 or 410 answer. On a sent careers page, a title gone from the text. Any other failure changes nothing, and `last_checked_at` stays.
   - **What leaves it alone:** an ATS posting whose board isn't checked, and a careers page that wasn't sent.
10. **Reopening.** A closed posting listed again becomes `live` with `closed_at` NULL. It gets a new Event and new Evidence, because the old ones stay retracted.
11. **The boards to check** are those from the Lead's current links, plus the boards of its live ATS postings whose source is still `source_enabled`. A board whose link left the site still closes its postings.
12. **No retry inside `check_job_boards`.** A failed board counts in `board_errors` and waits for the next check. Before a send, `recheck_event` re-checks (spine AD-5), and it re-raises a retryable error.
13. **`recheck_event` is not budget-checked.** It is on the sending path and its calls are free. A careers recheck obeys robots.txt and never stores page text.
14. **Hiring Events come only from postings.** 2.9's `validate` refuses a hiring-type Signal (`hiring_type`), so one posting never makes two Events. Hiring Signals 2.9 stored before this change aren't migrated (no production data yet).
15. **Counts.** The `crawl_runs` counts are added in place with no CHECK (as 2.10 did). NULL means "not checked in this run".
16. **Merges.** A colliding posting is closed on the merged Lead, so the survivor keeps one Event per posting. Its notifications go out.
17. **Dates.**
    - **Careers:** a `posted_date` counts only when 2.9's `stated_date` finds it in the quote and it isn't in the future. Otherwise it is dropped with a `job_posting_date_discarded` row.
    - **ATS:** the date comes from the provider's field (Greenhouse `first_published`, Lever `createdAt`, Ashby `publishedAt`), read as a UTC date.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected |
|---|---|---|
| ATS | Ops Lead; link `boards.greenhouse.io/acme/jobs/1`; the board lists "Accounts Payable Clerk" (2026-11-10) and "Sales Rep" | 2 live postings; 1 Tier 1 Event dated 2026-11-10 with Quote "Accounts Payable Clerk" (`ats`, `rule`); `greenhouse_postings` 2, `greenhouse_events` 1; 1 `ats` call; selection queued, Controller preferred |
| Careers | Law careers page with "Legal Operations Manager"; the model adds a title not in the text | 1 posting and Event (`website`); 1 `title_not_in_page` row |
| Description | "Coordinator", whose description says "client intake" | Event; the quote is the excerpt holding "client intake" |
| Closed | the next check doesn't list it | `closed` with `closed_at`; Evidence and Signal retracted; `EvidenceChanged` and `EventClosed` |
| Not called | the host was removed from `ats_hosts`; an unmapped host; an Indeed link | no call |
| Board errors | 404; a 500 or a timeout | postings closed; unchanged, `board_errors` 1 |
| Budget | `allow` refuses | `DEFERRED`; no call |
| Recheck | an ATS posting; a careers posting whose title left the page | one board call, same rule; robots then page, closed |
| Lead page | live and closed postings with `<b>` in a title | the section shows them, escaped |

</frozen-after-approval>

## Code Map

- **The crawler** (`nextron/adapters/crawler/__init__.py`):
  - `fetch_resource` (l.382);
  - `PROVIDER` (l.126, metered at l.435), which gets the `provider` keyword;
  - `Resource` (l.223: a non-2xx is returned with an empty body, not raised);
  - `check_target` (l.276).
  - `parsing.parse_html` reduces HTML to visible text.
- **The renderer** (`nextron/adapters/renderer/__init__.py:152`): its `ResourceFetcher` protocol is the pattern for `JsonFetcher`.
- **Ports:** the shape follows `nextron/ports/renderer.py` (codes, `*_CODES`, frozen dataclasses, `@runtime_checkable`).
- **Fetch policy and fakes:**
  - `tests/contracts/test_fetch_policy_contracts.py`: `FETCHING_ADAPTERS` (l.136), the renderer driver pattern, and `test_every_built_fetching_adapter_has_a_driver` (l.221). The `ats` driver uses `site=None`.
  - `tests/contracts/test_test_isolation.py:411-423` is the exact adapter set; add `ats`. `fake_problems` needs a no-argument fake whose parameters match.
  - `tests/live_guard.py`: `LIVE_CONSTRUCTORS` (l.90) and `ADAPTER_PACKAGES` (l.138).
  - `tests/fakes/__init__.py:50` `PORT_FAKES`; `tests/fakes/crawler.py` `ScriptedCrawler` is the model.
  - `pyproject.toml` c5 (l.289-318) and `tests/contracts/test_contract_coverage.py:56,66`.
- **Domain:**
  - `domain/signals.py` `_has_phrase` (l.144);
  - `domain/segments.py` `HIRING_SOURCES` (l.120) and `SignalType.match_terms`;
  - `domain/extraction.py` `validate` (l.613), `DiscardCause`, `stated_date`, `MAX_QUOTE` and `EvidenceSource.ATS`;
  - `domain/quotes.py` `normalise` and `quote_in`;
  - `domain/crawl.py` `PageKind.CAREERS`, `MIN_INTERVAL_S`, `crawl_interval` and `PAYLOAD_VERSION`/`LEAD_ID_FIELD`/`RUN_ID_FIELD`/`DOMAIN_KEY_PREFIX`;
  - `domain/job.py` `TICK`, `VERSION_FIELD` and `JobGroup.RECHECK`;
  - `domain/notifications.py` `EventClosed` (l.136) and `EvidenceChanged`;
  - `settings_schema.py` `Key.ATS_HOSTS`;
  - `domain/identity.py` `registrable_domain`.
- **The app:**
  - `app/research/extraction.py`: `SignalExtractions._apply` (l.412-446); call the careers step before `retract_unsupported` (l.433). `_in_scope` (l.462) already excludes `rule` rows.
  - `app/research/people.py`: `_select` (l.402) and its callers (l.344, 399, 422, 453).
  - `app/research/api.py`:
    - `WebsiteCrawls._rule` (l.1186) → module `robots_rule`;
    - `enrich_lead`'s budget transaction (l.1092);
    - `reparent_lead` (l.728) → `crawl_repo.reparent`, then `signals_repo.reparent`, then postings;
    - `_upsert_ats_link`.
- **Persistence:**
  - `db/repos/research/signals.py` (`insert_evidence`, `insert_signal`, `confirm_evidence`, `retract_evidence`, `retract_unsupported`, `evidence`);
  - `db/repos/research/crawl.py` (`ats_links`, `run`), plus a new `set_posting_counts`;
  - a new `db/repos/research/postings.py`;
  - `db/models/crawl.py`: `JobPostingRecord` after `AtsLinkRecord`, and the counts on `CrawlRunRecord`;
  - `owners.py:268` and `pii_registry.py:287`;
  - the migration pattern of `0025` (create) and `0028` (`batch_alter_table` add).
- **Head bumps** to `0030_2_11_job_postings`:
  - `test_persistence_contracts.py`: `HEAD` (l.81), the probes at l.804-816 → `0031_*`/`0032_*`, and `lead_scoped_columns` (l.540-548 and 577-585) + `job_postings.lead_id`;
  - `tests/unit/test_migrations.py:39`, plus a 0030 test;
  - `tests/app/test_serve_process.py:324`;
  - `tests/unit/test_serve.py:833,847` (`0031_more`);
  - `test_source_tree.FILES`.
- **Jobs:**
  - `jobs/handlers.py` (copy `extract_lead`, l.256);
  - `main.build_job_types` (l.1038), with every call site (grep `build_job_types(`);
  - the `test_job_contracts` service map, `names()` and shapes (l.390-597);
  - `test_runner_lifespan` (l.213, 536, 738);
  - `test_engine_health.py:327` and `test_health_page.py:280-401` (a Recheck group appears);
  - `test_backup_contracts` `_NO_SERVICES`;
  - `tests/dashboard.py:166,336`;
  - `main.build_app` (l.922-938) and the stale docstring at l.167.
- **Web:**
  - `web/app.py` `lead_page` (l.1306);
  - `web/leads_page.py` `lead_context` (l.739), with a `PostingRow` like `SignalRow`;
  - `templates/lead.html` (a section after the Contacts include);
  - `tests/unit/test_leads_page.py` (`World` l.206; the view reads only queries, l.1347).
- **Tests to adapt:**
  - `tests/app/test_signal_extraction.py` (`World` l.141, `ok(**lists)` already has `job_postings`);
  - `test_people_extraction.py` and `test_primary_contact.py`: their `select_primary_contact` enqueue assertions now see `check_job_boards`.
- **Docs:** `nextron-engine/README.md`:
  - a "Hiring Signals and job postings" section after "Decision-makers and the Primary Contact" (l.3882);
  - the migrations table (l.2111);
  - the Jobs list (l.2189).

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/postings.py`, `domain/signals.py` (`match_hiring`, `has_phrase`), `domain/extraction.py` (`hiring_type`) -- the pure rules, with `tests/unit/test_postings_domain.py` and `test_signals_domain.py` covering the Law terms (intake, legal operations, knowledge management, innovation) and the Ops terms (data entry, order entry, accounts payable, accounts receivable, AP/AR, dispatch), every token form, `excerpt` and `validate_postings`.
- [x] `nextron/ports/job_board.py`, `nextron/adapters/ats/__init__.py`, `adapters/crawler/__init__.py` (`provider`), `tests/fakes/job_board.py`, `tests/fakes/__init__.py`, `tests/live_guard.py`, `pyproject.toml` -- the port, the adapter and the guards, with `tests/unit/test_ats_adapter.py` (each provider's JSON on a `MockTransport`, skips, codes, one `ats` call per request, the endpoint and site pin).
- [x] `nextron/db/alembic/versions/0030_2_11_job_postings.py`, `db/models/crawl.py`, `owners.py`, `pii_registry.py`, `db/repos/research/postings.py`, `crawl.py`, `app/queries/research.py` -- the table, the counts and the reads.
- [x] `nextron/app/research/postings.py`, `job_boards.py`, `extraction.py`, `people.py`, `api.py` -- the store, the careers step, both use cases, the chain, the reparent and `robots_rule`.
- [x] `nextron/jobs/handlers.py`, `nextron/main.py` -- both job types and the wiring.
- [x] `nextron/web/app.py`, `web/leads_page.py`, `templates/lead.html` -- the section.
- [x] Tests:
  - `tests/app/test_job_boards.py`: every matrix row on pages a real crawl stored (`ScriptedCrawler`) with `ScriptedJobBoards`, reopening, the merge collision, the counts, the selection enqueue and the log keys;
  - the careers rows in `test_signal_extraction.py`;
  - the Lead page test (escaped);
  - the contract, head and job-list updates.
- [x] `nextron-engine/README.md`, `deferred-work.md` (Decisions 3 and 12; the 2.9 undated-Event entry is answered for hiring Events) -- docs.

**Acceptance Criteria:**
- Given the full suite, when `pytest` and `lint-imports` run, then everything passes (`ai_eval` skipped) and every contract is kept.
- Given `grep` over `nextron/`, when it looks for `job_postings` writes, then only `db/repos/research` holds them, and only `app/research` calls them.

## Implementation Notes

Built as specified. What the spec left open, and where the code had to choose:

- **`nextron/domain/postings.py`.**
  - Besides the spec's names it holds `TOKEN` (the board-token pattern), `posting_title(title)`
    (an ATS title normalised, 1 to 300 characters, else `None`), `PostingDiscarded`
    (`job_posting_discarded`, `job_posting_date_discarded`), `CareersPosting(page_id, url, title,
    description, posted_date)`, `PostingDiscard(status, cause, page_id)` and
    `PostingValidation(postings, discards, dropped_dates)`. Titles and descriptions never show in a
    `repr`. `Board` is ordered (source, then token) and refuses a non-ATS source or a token that
    isn't `TOKEN`.
  - `board_from_link` reads the host with `urlsplit` (lowercased) and compares it with the
    `ats_hosts` entries stripped and lowercased; the Greenhouse `for` value must be the only one
    (two `for=` give `None`); a path segment is used as written (a percent-escape fails the
    pattern). `source_enabled(careers_page, ...)` is `False` (no host); callers use it for ATS
    sources only.
  - `excerpt`: a term is found as a whole phrase on the normalised text, case-insensitively, by
    comparing casefolded windows (so the window holds the original-case occurrence). A window is
    centred on the term, then each edge moves to whitespace; a word the window's edge would cut
    is left out, and a window with no whitespace to cut on is cut at its edge. A term that isn't
    found (only possible when casefolding changes a length) gives the start.
  - `validate_postings` checks, in order: block, careers page, title (empty, too long, not in the
    page); a kept repeat (same page and normalised title) is dropped silently *before* its
    description and date are read, so a repeat writes no date row. A `posted_date` needs the
    validated description (the model's quote, 1 to 300 characters and in the page): with none,
    or when `stated_date` doesn't find the date in it, the cause is `date_not_in_quote`; a date
    after `now`'s UTC date is `date_in_future`. Dropped dates aren't counted in the run's
    `discarded` (2.9's rule).
- **`nextron/domain/signals.py`.** `_has_phrase` became `has_phrase` (its one caller updated).
  `HiringMatch.signal_type` is the matched `SignalType` itself (the store needs its class, Tier and
  `score_only`). `match_hiring` normalises and casefolds both text and term.
- **`nextron/domain/extraction.py`.** `DiscardCause.HIRING_TYPE` (`hiring_type`), checked right
  after `unknown_signal_type` (a quotable type whose `sources` meet `HIRING_SOURCES`).
- **The port and the adapter.**
  - `BoardPosting` hides its title, URL and description from the `repr` and type-checks its
    fields; `BoardListing` holds a tuple.
  - `AtsBoards.fetch_json(url, *, site)` is public (the fetch-policy driver calls it). A 2xx body
    is decoded by `crawler.parsing.decode` (its charset, else UTF-8), a byte-order mark dropped,
    then `json.loads` (`ValueError` or `RecursionError` is `board_invalid`). The fetcher's own
    `AdapterError`s pass through (the error contract forbids re-raising them). `postings` raises
    `board_failed` before any request for a board whose source has no endpoint or whose token
    isn't `TOKEN`. `ENDPOINTS` and `READERS` are read-only maps.
  - **The future-date check** in the adapter uses the real UTC date (the adapter has no clock);
    the app drops a board date after the check's UTC date too (`postings.listed_from_board`, on
    the clock), so the stored result is deterministic.
  - A URL is kept when it is a `str` of at most 2,048 characters with no whitespace, an
    `http(s)` scheme and a host. A Lever description is `descriptionPlain` and `additionalPlain`
    joined by a line break; an empty description is `None`.
- **Migration `0030_2_11_job_postings`** (down `0029_2_9_tag_managers`). Lengths: `source` 16,
  `board_token` 100, `url` 2,048, `title` and `description` 300, `status` 8. CHECKs
  `ck_job_postings_source`, `_board_token`, `_status`, `_closed_at` (the model's compile to the
  same SQL); `uq_job_postings_lead_id_url_title`; `ix_job_postings_lead_id` and
  `ix_job_postings_signal_id`. The nine `crawl_runs` counts are added with
  `batch_alter_table(...).add_column` (in place). `StoredCrawlRun` carries them (defaults
  `None`); `crawl_repo.set_posting_counts(run_id, counts)` refuses an unknown name.
- **`nextron/db/repos/research/postings.py`**: `insert_posting`, `posting(lead, url, title)`,
  `by_signal`, `for_lead` (by `first_seen_at`, then `id`), `touch`, `reopen` (live, `closed_at`
  NULL, the new description and date, its Event cleared), `set_event`, `close`, and `reparent`
  (a `NOT EXISTS` on the survivor's `(url, title)`; returns the ids that stayed).
- **`nextron/app/research/postings.py`** (the store). `Listed(url, title, posted_date, text)`:
  `text` is an ATS posting's full description or a careers posting's validated quote, so one
  rule serves both (`excerpt` of a text of at most 300 characters is the text). The stored
  description is `excerpt(text, term)` when the term was only in the description, else
  `excerpt(text)`. A reopened posting takes the description and posted date it lists now and is
  matched again. A careers page's other live postings still in its text are touched, confirmed
  and counted as listed. `close` publishes `EvidenceChanged` as it retracts; `publish_closed`
  publishes `EventClosed` after the caller's `retract_unsupported` (in `extract_lead`, 2.9's own
  call, so 2.9's `signals_retracted` count includes a closed posting's Signal).
  `last_checked_at` and `last_confirmed_at` never move back (`max` with the stored value).
- **`extract_lead`'s careers step** runs on every `ok` apply (so a posting citing a non-careers
  page is still refused and recorded); `_Call` gains `careers_ids` (the careers pages sent, in
  send order). The counts are written only when a careers page was sent and the crawl run
  exists. The `signals_extracted` log line is unchanged.
- **The chain.** `PeopleExtractions._next_stage(lead_id, run_id, web_domain)` enqueues
  `check_job_boards` with the `run_id` `extract` was given (the job payload's ULID, so the
  handler's check holds), not the possibly-`None` `crawl_run_id`.
- **`JobBoards`** (`nextron/app/research/job_boards.py`).
  - Each board answer is stamped with the clock's time after its call; that is its check time.
  - A Lead merged during the calls logs `job_boards_skipped` (`merged_lead`), writes nothing and
    enqueues nothing. The counts are written only when `run_id` names an existing crawl run.
  - `job_boards_checked` carries `lead_id`, `run_id`, `boards`, `board_errors`, `listed`,
    `events`, `reopened` and `closed`.
  - `recheck_event` skips with `no_posting`, `posting_closed`, `unknown_lead`, `merged_lead`,
    `source_disabled` or `robots_disallowed`. An ATS recheck's apply writes no counts and queues
    no selection. A careers recheck: robots.txt at `MIN_INTERVAL_S` (a retryable error raised; a
    non-retryable one read by `robots_rule` as disallow-all, so skipped); then the page; a 2xx
    HTML page applies the page rule (`apply_page` with nothing proposed); a 404 or 410 closes the
    page's postings (the rule with an empty text); any other status, or a 2xx that isn't HTML,
    logs `recheck_failed` (`error_code` `http_<status>`). An apply logs `event_rechecked`
    (`signal_id`, `lead_id`, `listed`, `closed`, `reopened`).
- **`robots_rule`** is `WebsiteCrawls._rule` moved to a module function of
  `nextron/app/research/api.py`, unchanged.
- **Merges.** `Research.reparent_lead` calls `_reparent_postings` after `signals_repo.reparent`:
  colliding rows stay on the merged Lead; when any is live, `close_and_publish` runs on the
  survivor (which holds their Evidence and Signals now).
- **Jobs and wiring.** `handlers.check_job_boards` and `handlers.recheck_event` with a
  `CheckingJobBoards` protocol (`SIGNAL_ID_FIELD` checked as a ULID). `build_job_types(...,
  job_boards=)`; every caller passes it. `build_app` binds `site_crawler` (the injected crawler,
  else the `HttpCrawler`) for both `WebsiteCrawls` and `JobBoards`, and builds `AtsBoards(meter=
  meter, fetcher=http_crawler)` unless `job_board=` is injected. The stale `SignalExtractions`
  line of `main`'s docstring now names `people`.
- **The Lead page.** `lead_context(..., postings=())` gives `PostingRow(title, source, posted,
  first_seen, status)`; `POSTING_SOURCE_LABELS` and `POSTING_STATUS_LABELS`; the posted date
  reads "—" when unknown. `queries.research.job_postings` sorts live first, then the newest
  first seen (then `id`, descending). The section (`lead-postings`, `posting-table`) follows the
  Contacts panel.
- **Tests adapted.** Story 2.9's tests quoted `law_hiring_legal_ops`, which `validate` now
  refuses: `tests/unit/test_extraction_domain.py` quotes `law_ai_confidentiality_post` (and
  `law_expansion` as the second claim) and gains two `hiring_type` rows;
  `tests/app/test_signal_extraction.py` quotes `QUOTED = "law_expansion"` (Tier 2) by default;
  `test_contact_form_only` follows it. `test_primary_contact`'s AP/AR test stores the posting's
  Event directly (`insert_event`), and its job test builds the selection payload from the queued
  `check_job_boards` job. `test_people_extraction` asserts the `check_job_boards` job (key,
  payload with `run_id`). `EvidenceRecorder` also records `EventClosed`.
- **Tests added.** `tests/unit/test_postings_domain.py` (every token form, `source_enabled`,
  `posting_title`, `excerpt`, `validate_postings`, and `match_hiring` on the shipped Law and Ops
  terms), `tests/unit/test_ats_adapter.py` (each provider's JSON on a `MockTransport`, skips,
  dates, the cap, every code, one `ats` call per request, the endpoint and site pin, the live
  fetcher refused), the careers rows in `tests/app/test_signal_extraction.py`,
  `tests/app/test_job_boards.py` (every ATS and recheck row, reopening, the merge collision, the
  counts, the chain and both handlers, the log line), the Lead page tests, the migration test,
  `test_extraction_contracts` (the `job_postings` fields `validate_postings` reads), the wiring
  test in `test_runner_lifespan`, and the head, `FILES`, job-list, health-group, live-guard and
  fetch-policy updates.

**For the merge into `main`:** `job_postings.title` and `description` are PII `redact`; Story
3.13's erasure on `main` (`nextron/db/erasure.py`) may need to cover them (or classify why not).

**Implementation verification** (targeted; the full suite is the orchestrator's gate):
`tests/contracts`, 1430 passed and 10 skipped (the opt-in `ai_eval` check and nine AD-16 callers
not built yet); the 37 touched and new test files (every `build_job_types` caller, the
extraction, people, primary, crawl, render, job-board, Lead page, health, lifespan, migration,
serve, domain and adapter tests, and `test_extraction_contracts` with its new check), 1945
passed. `lint-imports`: 29 kept, 0 broken. No network, job site or real board was reached.

**Review fixes (pass 1)** (they supersede the bullets above where they differ):

- **A careers recheck of a page that needs a render.** A 2xx HTML page whose fetched text
  `needs_render` (its titles were checked against the rendered text the crawl stored) logs
  `recheck_failed` with `needs_render` (`job_boards.NEEDS_RENDER`) and changes nothing.
- **A careers recheck keeps robots.txt's failure code.** When `robots_rule` gives a code (a
  robots.txt 429 or other non-4xx status, or a non-retryable `AdapterError`), the page isn't
  fetched: a code `domain.crawl.is_retryable` accepts raises `AdapterError(True, code)`; any
  other logs `recheck_failed` with it. Only a true disallow logs `robots_disallowed`. A page
  answering 429 or a 5xx raises `AdapterError(True, http_<status>)`; other non-2xx statuses
  (except 404 and 410) stay `recheck_failed`.
- **The Lead's current links.** `check_job_boards` reads only the `ats_links` rows whose `run_id`
  is the Lead's latest crawl run (`crawl.latest_run`; no run, no links); the boards of live ATS
  postings whose source is still enabled are added as before.
- **A date that overflows.** The adapter's ISO date reading catches `OverflowError` (e.g.
  `0001-01-01T00:00:00+05:00` to UTC) as unreadable: `None`.
- **`recheck_event`'s default lane is bulk** (AD-10's runner table); its lanes stay bulk and
  interactive.
- **Tests.** The careers recheck pages are long enough not to need a render; new cases for a
  page that needs a render, robots.txt failures (retryable raises, others log, no page fetched),
  the page's 429 and 5xx (raised) and 403 (logged), robots.txt's crawl delay on the page fetch,
  a link only an older crawl run saw, the overflowing date, and the `run_id` of the
  `check_job_boards` payload after a bad people result.
- **`deferred-work.md`.** The 2.9 undated-Event entry is "partly answered": undated careers
  postings and a reopened posting still age from the posting's first seen.

**Final verification** (on the final tree, after the review fixes): targeted files with `tests/contracts`, 2,160 passed and 10 skipped; the full suite through `suite_lock.py` (track-A) passed on its first run: 13,611 passed and 12 skipped. `lint-imports`: 29 kept, 0 broken. No test reached the network.

## Spec Change Log

- **Review pass 1, 2026-11-18: `recheck_event` runs on the bulk lane by default (spine-forced deviation, overnight, pending user review).**
  - **Trigger:** Review Triage Log entry 9 (BH12). The frozen Jobs table gives `recheck_event` the lanes "interactive, bulk", with interactive as the default. The spine's AD-10 runner table puts "re-checks" on the `bulk` lane and keeps the single `interactive` thread for Operator-facing work. The spine is binding.
  - **Change (code, not the frozen text):** `recheck_event`'s default lane is `bulk`; its lanes stay `{bulk, interactive}`.
  - **Avoids:** a careers recheck (robots.txt plus the page, up to 30 s each) holding the only interactive thread while Mac is using the dashboard.
  - **KEEP:** priority 50, a 5-minute `max_runtime`, 3 retries and group `RECHECK`.
  - The frozen text is unchanged; Mac reviews it with the overnight decisions, as with Story 2.9's job key.

## Review Triage Log

Pass 1 (blind hunter BH, edge-case hunter EC, verification gap VG): 35 raw findings, deduplicated to 27 entries. 9 patched, 2 deferred and 16 rejected. Six of the rejections are spec-mandated; four of those get `deferred-work.md` entries, as story precedent does for spec-rejected items.

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | BH1 + EC3: a careers recheck tests titles against `fetch_page`'s raw visible text. A page under 500 characters was stored as Story 2.6's rendered text, so every posting on a JavaScript careers page closes just before a send. | medium | True: `_recheck_page` never renders. The titles were validated against the rendered text the crawl stored, and `needs_render` pages are exactly the widget-built careers pages. | patch: when the fetched text `needs_render`, log `recheck_failed` (`needs_render`) and change nothing |
| 2 | BH2 + EC5 + VG4: a robots.txt 429, 5xx or adapter failure becomes disallow-all and is logged `robots_disallowed`. A 429 or 5xx page is `recheck_failed` and never retried, unlike a board's `board_unavailable`. | medium | True: `allows, interval, _ = robots_rule(answer)` drops the code. Decision 12 says a recheck re-raises a retryable error, and the crawl treats `http_429`/`http_5xx` as retryable (`domain.crawl.is_retryable`). | patch: a robots failure code is `recheck_failed` with that code, or a raised retryable `AdapterError` when `is_retryable`; a page 429 or 5xx raises a retryable `AdapterError` |
| 3 | VG1: no test shows the careers recheck's page fetch using the robots.txt Crawl-delay interval. | medium | Pre-verified: every recheck robots.txt has no Crawl-delay, so passing `MIN_INTERVAL_S` passes. | patch (test) |
| 4 | VG2: no test covers the recheck's robots.txt `AdapterError` (retryable raises; non-retryable fetches no page). | medium | Pre-verified: no test scripts an `AdapterError` for robots.txt. | patch (test, with entry 2's behaviour) |
| 5 | VG3: the people step's `_finished` path could enqueue `check_job_boards` with the extraction run's id and no test notices. | medium | Pre-verified: `test_a_bad_result_records_its_outcome_and_still_selects` counts jobs only. | patch (test) |
| 6 | EC1: a board date such as `0001-01-01T00:00:00+05:00` makes `astimezone(UTC)` raise `OverflowError`, which escapes `JobBoards._ask` and kills the job (and the Lead's selection). | low | True: `_iso_date` catches only `fromisoformat`'s `ValueError`. Board JSON is untrusted. The fix is a direct correction. | patch |
| 7 | BH8: `_boards` checks every ATS link ever recorded (`ats_links` rows are never deleted), not the Lead's current links as Decision 11 says. | low | True: `crawl_repo.ats_links(lead_id)` with no run filter. A board the site stopped linking keeps being queried and can make new Events. Aligning the code with the spec is a direct filter. | patch: only links whose `run_id` is the Lead's latest crawl run; live postings' boards as before |
| 8 | BH11: the 2.9 deferred entry's new status says it is "answered for hiring Events", but undated careers postings and a reopened posting (which keeps its `first_seen_at`, per the spec) still age from first seen. | low | True: the wording claims more than the code does. Docs only. | patch (reword the status) |
| 9 | BH12: `recheck_event` defaults to the interactive lane, but the spine's runner table puts re-checks on `bulk`. Also, Decision 13 ("not budget-checked") contradicts the spine's budget rule. | medium | Lane: true (spine AD-10, l.282: bulk runs "re-checks"; interactive is for Operator work). Budget: false. The spine's rule lists what the budget **may** block ("block only discovery, enrichment and re-check work"); a recheck makes no AI or quota call. | patch (default lane `bulk`; Spec Change Log) |
| 10 | BH3: a recheck that can't check (robots disallow, source disabled, a failure) leaves no durable outcome, so AD-5's send gate would re-enqueue it and skip the Step 1 every tick, with the reason only in logs. | medium | True, but the consumer (the sender, AD-5) isn't built, and its policy for an unverifiable posting belongs to that story. The skip is the safe side: no stale claim is sent. | defer |
| 11 | BH13: `job_postings.title` is `redact`, but it is in `uq_job_postings_lead_id_url_title`, and careers postings share their page's URL, so a naive tombstone erasure would break the index. No retention rule purges closed postings' text. | medium | True as a hazard: no erasure code exists in this tree (Story 3.13 is backlog). | defer |
| 12 | BH4 + EC10: one vacancy on the careers page and on its board makes two postings and two hiring Events. | low | True, but the AC already makes one Event per posting, so a Lead often has several hiring Events of one type (three AP clerk openings), and Story 2.15 must aggregate per type anyway. Cross-source de-duplication needs a hand-over rule when one copy closes. | reject (noted for Story 2.15) |
| 13 | BH5 + VG5: the `extract_signals` catalogue still lists the hiring types `validate` now always refuses. That costs output tokens, writes `hiring_type` rows and adds to `discarded`; the ai_eval sets still expect them. | low | True. The fix changes the model's input and the eval sets, which belongs with a prompt version change. | reject + deferred-work entry |
| 14 | BH6: description matching over the whole ATS description (up to 20,000 characters) turns boilerplate ("we embrace innovation", "orders dispatched same day") into an Event for every posting on the board. | medium | True. But the AC and the frozen `match_hiring` rule say title **or** description; a boilerplate guard changes the approved rule. | reject (spec-mandated) + deferred-work entry |
| 15 | BH7 + EC13: a careers posting whose page is no longer sent, and an ATS posting whose source left `ats_hosts`, never close. | medium | True by Decisions 9 and 11 (unsent pages and unchecked boards are left alone). | reject (spec-mandated) + deferred-work entry |
| 16 | BH9 + EC11: no cap on boards per Lead; a Greenhouse `content=true` answer over 5 MB fails every check. | low | ICP firms are small, with one or two boards and few postings. The fallback (fetch without content) adds a branch and a second endpoint. | reject |
| 17 | BH10 + EC2: when the budget defers `check_job_boards`, or the job dies, `select_primary_contact` isn't queued. | low | True, but a refusal means the month's AI budget is spent, which already pauses extraction and drafting. The deferred job runs the selection once the budget allows. A dead job alerts. | reject |
| 18 | BH14: the Job postings section has no link to the posting, no closed date and no row limit. | low | The AC lists exactly title, source, posted date, first seen and status. | reject |
| 19 | BH15: a Segment's new `match_terms` never re-match live postings. | low | True by Decision 7 (matched once). The epic also says a seed sync never re-scores Leads. | reject (spec-mandated) |
| 20 | EC4: a careers page sent with unrendered text after a failed render closes its postings, which reopen with new Events after the next good render. | low | Real only when renders succeed intermittently. Skipping the close on short pages would stop genuinely small careers pages ever closing. | reject |
| 21 | EC6: the page rule keeps "Paralegal" live while "Senior Paralegal" remains on the page (a substring). | medium | True, but the frozen rule is "stays live while its title still passes `quote_in`"; a word-boundary test changes it. | reject (spec-mandated) + deferred-work entry |
| 22 | EC7: a listed `(url, title)` already stored under another source or board is touched, and escapes the listing board's close rule. | low | Needs one URL and title on two boards of one Lead, or a careers page URL equal to an ATS posting URL; neither happens in practice. | reject |
| 23 | EC8: in a merge, when the survivor's colliding row is closed or has no Event, closing the merged row's Event leaves the survivor without one. | low | Needs both Leads to hold the same posting in different states. The survivor's next board check reopens a closed row. | reject |
| 24 | EC9: a `recheck_event` applied between `check_job_boards`' calls and its apply can be overwritten by the stale answer. | low | A window of seconds. The next check corrects it. The fix needs a concurrency key the sender doesn't have, or timestamp guards. | reject |
| 25 | EC12: hiring Signals stored by Story 2.9 before this change stay live. | low | Decision 14: not migrated, since there is no production data yet. | reject (spec-mandated) |
| 26 | EC14: "recheck_event re-checks one posting" but applies the whole board or page. | false | The spec says "An ATS posting re-checks its board through the same apply", which is what the code does. | reject |
| 27 | EC15: the Law and Ops term tests are in `test_postings_domain.py`, not `test_signals_domain.py` as the task says. | low | True and documented in the Implementation Notes (they load the shipped seed, which the domain test file's rule excludes). The coverage exists. | reject |

## Verification

**Commands** (from `nextron-engine/`):
- `uv run pytest -q <touched test files> tests/contracts` -- all pass (implementer).
- `uv run lint-imports` -- every contract kept.
- The full suite runs only through `suite_lock.py`, by the orchestrator.
