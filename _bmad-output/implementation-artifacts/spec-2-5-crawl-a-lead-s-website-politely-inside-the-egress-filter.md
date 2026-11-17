---
title: "Story 2.5: Crawl a Lead's website politely inside the egress filter"
type: 'feature'
created: '2026-11-17'
status: 'done'
route: 'dispatch'
baseline_commit: '5140d0b70cb2599b3ae68297da3ef2b94cee5351'
review_loop_iteration: 0
story_key: '2-5-crawl-a-lead-s-website-politely-inside-the-egress-filter'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-8-version-extraction-prompts-and-pin-the-active-route-tuple.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Leads exist (Story 2.2), but nothing reads their websites. Story 2.9 needs each Lead's team, about, careers, contact, services and news pages stored as visible text with hidden content stripped (AD-18). Story 2.11 needs the ATS links found on them. Every fetch must obey robots.txt, pace itself, stay inside the AD-9 egress filter and the FR-49 denylist, and be metered.

Requirements: FR-10, FR-9, FR-49, FR-57 · AD-7, AD-9, AD-10, AD-18 · NFR-6, NFR-34.

**Approach:**
- **Policy.** `domain/fetch_policy.py` holds the denylist (Story 2.3's module, extended) and the egress rules.
- **Adapter.** A `CrawlerPort` adapter (`adapters/crawler`, httpx2, selectolax, protego) resolves DNS itself, pins the validated IP, re-checks every redirect, paces requests and returns parsed visible text and links.
- **Job.** `WebsiteCrawls.enrich_lead` (the `enrich_lead` job) checks the budget, reads robots.txt, fetches the homepage and the keyword-matched pages, and stores `crawl_runs`, `crawled_pages` and `ats_links` (migration `0013_2_5_crawl`).
- **Follow-ups.** A homepage that lands on another registrable domain enqueues `canonicalize_lead` (discovery). A daily `purge_page_text` nulls text after 30 days. A CSV-created Lead enqueues `enrich_lead`.

The acceptance criteria are Story 2.5 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**

- **`nextron/domain/fetch_policy.py`.** Start from Story 2.3's exact file (`git show ccc7c1b:nextron-engine/nextron/domain/fetch_policy.py`) and its tests (`…:nextron-engine/tests/unit/test_fetch_policy.py`). Keep every public name and behaviour.
  - **Identity.** The file needs `identity.url_host`. Port 2.3's `url_host` change to `nextron/domain/identity.py` verbatim from `git show ccc7c1b -- nextron-engine/nextron/domain/identity.py`: the `url_host` function, the refactored `registrable_domain` and the two docstring hunks. Do **not** port `COUNTRY_ALIASES`. Port 2.3's `url_host` tests into `tests/unit/test_identity.py`.
  - **Denylist additions.** `DENIED_DOMAINS` gains `reed.co.uk`, `totaljobs.com`, `cv-library.co.uk` and `g.page`. The Google Maps host rule matches a `maps` label anywhere left of a `google.*` registrable domain, so `www.maps.google.com` is denied.
  - **Egress rules** (stdlib):
    - `check_url(url) -> FetchTarget(url, scheme, host, port, path)` raises `FetchRefused(reason)`, a `DomainError` with code `fetch_refused` whose message is the reason, never the URL. The reasons, in order:
      - `bad_url`: unparsable, or longer than 2,048 characters;
      - `scheme`: anything but `http` or `https`;
      - `userinfo`: credentials in the URL;
      - `port`: an explicit port other than 80 or 443;
      - `ip_host`: the host is an IP literal;
      - `denylisted`: `is_denied`.
    - `address_refusal(address, own_addresses) -> str | None` refuses, in order:
      - `unspecified`, `loopback`, `link_local`, `multicast`;
      - `cgnat` (100.64.0.0/10), `private` (RFC 1918), `ula` (fc00::/7);
      - `ipv4_embedded` (IPv4-mapped, 6to4, Teredo, and NAT64 `64:ff9b::/96` and `64:ff9b:1::/48`);
      - `not_global`: any other address where `is_global` is false;
      - `own_address`: any address in `own_addresses`.
    - Constants: `MAX_REDIRECTS = 5`, `MAX_PAGE_BYTES = 5 MiB`, `MAX_ROBOTS_BYTES = 512 KiB`, and `PAGE_MEDIA_TYPES` (`text/html`, `application/xhtml+xml`, `application/json`).
- **`nextron/domain/crawl.py`** (stdlib):
  - `USER_AGENT = "NextronBot/1.0 (+https://nextronai.com)"` and `ROBOTS_TOKEN = "NextronBot"`.
  - `PageKind`: `home`, `team`, `about`, `careers`, `contact`, `services`, `news`.
  - `classify_link(path, anchor_text) -> PageKind | None`. Matching is by whole-token keywords in the path or the anchor text. The keywords are:
    - team: team, people, our people, staff, attorneys, lawyers, solicitors;
    - about: about, who we are;
    - careers: careers, career, jobs, vacancies, join us;
    - contact: contact, offices, locations;
    - services: services, practice areas, expertise, what we do;
    - news: news, press, blog, insights.

    A path ending in a non-HTML file extension (pdf, images, office files, archives, media) never matches.
  - `CrawlOutcome`: `ok`, `unreachable`, `robots_disallowed`, `lead_merged`. `OUTCOME_LABELS` maps `unreachable` to "website unreachable".
  - Timing:
    - `crawl_interval(crawl_delay)` is `max(1 s, min(crawl_delay, 10 s))`;
    - `TEXT_RETENTION = 30 days`, `RUN_DEADLINE = 10 min`, `MAX_DEPTH = 2`.
  - Text:
    - `MAX_STORED_TEXT = 100_000` characters;
    - `visible_text(raw)` deletes every `Cf` character (soft hyphen, zero-width ones, BOM, bidi controls), collapses horizontal whitespace within each line, strips lines, drops blank ones and joins them with `\n`, then caps the result at a line boundary;
    - `text_hash(text)` is the SHA-256 hex of the text.
  - `is_retryable(error_code)` is true only for `fetch_timeout` and `http_5xx` codes.
- **Port `nextron/ports/crawler.py`** (`@runtime_checkable CrawlerPort`).
  - `fetch_robots(url, *, min_interval_s) -> Robots(final_url, status, crawl_delay_s, allows)`. `allows` is `Callable[[str], bool] | None` and is set only for a 2xx response.
  - `fetch_page(url, *, site, min_interval_s) -> FetchedPage(url, final_url, status, media_type, text, links, html)`:
    - `html` is hidden from `repr`;
    - a non-2xx response has `text=""`, `html=""` and no links;
    - `links` is a tuple of `PageLink(url, text, tag)`, with `tag` one of `a`, `iframe` or `script`;
    - `site` is `None` or a registrable domain that every hop must stay on.
  - Every failure raises `AdapterError` with one of these codes:
    - `fetch_refused` (a reason from the policy, or `off_site`);
    - `fetch_dns_failed`, `fetch_connect_failed`;
    - `fetch_timeout` (the only retryable one);
    - `fetch_too_many_redirects`, `fetch_too_large`, `fetch_unsupported_type`, `fetch_failed`.
- **Adapter `nextron/adapters/crawler`** (`HttpCrawler(*, meter, http=None, resolve=None, own_addresses=None, monotonic=time.monotonic, sleep=time.sleep)`). The live constructors are `default_http_client`, `default_resolver` and `default_own_addresses`, looked up at call time.
  - **Each hop:**
    1. `check_url`, then the `site` check;
    2. resolve the name itself; every resolved address must pass `address_refusal`, or the hop is refused;
    3. pace the hop;
    4. inside `Metered(meter, "web", "robots" | "page")`, send exactly one GET to the first address: the URL carries the IP, with a `Host` header and `extensions={"sni_hostname": host}`, `follow_redirects=False` and a 30 s total deadline;
    5. on a 301, 302, 303, 307 or 308 with a `Location`, resolve it against the logical URL and start the next hop. The sixth redirect raises `fetch_too_many_redirects` before any request.
  - **The client.** `trust_env=False`, a cookie jar whose policy stores nothing, no keep-alive, and the fixed `User-Agent`.
  - **A 2xx response:**
    - its media type must be in `PAGE_MEDIA_TYPES` (robots: any `text/*`);
    - the size is capped by `Content-Length` and by the bytes read;
    - the charset comes from the header, else `<meta charset>`, else UTF-8 with replacement.
  - **Parsing** uses selectolax `LexborHTMLParser`. `iframe[src]` and `script[src]` are collected first. Then these are removed:
    - `head`, `title`, `script`, `style`, `noscript` and `template`;
    - comments;
    - `[hidden]` and `[aria-hidden="true" i]`;
    - inline `display:none` and `visibility:hidden|collapse`, with CSS comments ignored.

    Then `a[href]` is read from what remains. The body text is joined with newlines at block-element boundaries only, then `visible_text`. Links resolve against `<base href>` or the final URL, with the fragment dropped.
  - **Pacing.** Requests are at least `min_interval_s` (never under 1 s) apart per registrable domain, across hops and calls, and thread-safe.
  - **Robots** are parsed with protego (`utf-8-sig`).
- **Persistence.** Migration `0013_2_5_crawl` (down `0012_2_8_ai_routes`) adds three tables, all owned by `research`. `crawled_pages.text` is `redact`; every other column is `none`.
  - **`crawl_runs`:**
    - `lead_id` (FK);
    - `site_domain`, and `final_domain` (nullable);
    - `started_at`, `ended_at`;
    - `outcome` (CHECK);
    - `error_code` (nullable);
    - the counts `pages_fetched`, `pages_skipped_robots`, `renders` and `errors`, each ≥ 0.
  - **`crawled_pages`:**
    - `lead_id`, `run_id` (FKs);
    - `url`, `final_url`;
    - `kind` (CHECK);
    - `fetched_at`, `http_status`, `content_hash`;
    - `text` (nullable), `text_purge_after`, `changed_at`;
    - unique `(lead_id, url)`.
  - **`ats_links`:**
    - `lead_id`, `run_id`, `page_id` (FKs);
    - `url`, `host`;
    - `tag` (CHECK);
    - `first_seen_at`, `last_seen_at`;
    - unique `(lead_id, url)`.
- **Settings** (section Engine):
  - `crawl_max_pages`: integer 1–15, default 15;
  - `ats_hosts`: a list, default `boards.greenhouse.io`, `job-boards.greenhouse.io`, `jobs.lever.co` and `jobs.ashbyhq.com`. Each item must equal its own `url_host` and must not be `is_denied`.
- **`WebsiteCrawls(clock, meter, crawler, queue)`, `@use_case enrich_lead(lead_id, *, may_retry) -> bool`** (`app/research/api.py`):
  1. **First transaction.** `budget.allow(BLOCKABLE, meter.snapshot(now), budget_limits())` false returns `False`. An unknown or merged Lead, or one without a `web_domain`, logs `crawl_skipped` and returns `True`. Then the settings are read.
  2. **Robots.** `https://<site>/robots.txt`, then `https://www.<site>/robots.txt` after a DNS or connect failure. The origin that answered is the run's origin.
     - 2xx: its rules apply;
     - 4xx: everything is allowed;
     - any other result: everything is disallowed;
     - DNS or connect failure on both: `unreachable`.
  3. **Homepage.** `<origin>/`:
     - disallowed: `robots_disallowed`;
     - an error, a non-2xx status or a non-HTML page: `unreachable`, with `error_code` set to the adapter code or `http_<status>`.

     A final registrable domain other than the site sets `final_domain`, and the rest of the run stays on that domain.
  4. **Other pages.** Breadth-first to `MAX_DEPTH`. Within a depth, links are ordered by `PageKind` order, then by document order. Only same-domain links that `classify_link` matches and that aren't seen yet are taken.
     - A robots-disallowed link adds to `pages_skipped_robots`.
     - The run stops at `crawl_max_pages` stored pages (home included), or at `RUN_DEADLINE`.
     - A failure or a non-2xx status adds to `errors`.
     - A page whose final URL was already stored is skipped.
  5. **Apply transaction.**
     - If the Lead was merged meanwhile, it inserts the run as `lead_merged` and nothing else.
     - Otherwise it inserts the run and upserts the pages: a changed hash or a new page sets `changed_at`, and `text_purge_after` is `fetched_at` + 30 days.
     - It upserts the ATS links: `a`, `iframe` and `script` URLs whose host is in `ats_hosts`.
     - With a `final_domain`, it enqueues `canonicalize_lead`.
  6. **Retry.** After the commit, when `may_retry` holds and the robots or homepage failure `is_retryable`, it raises `AdapterError(True, "crawl_retry", …)`. Then it logs `crawl_finished` with ids, counts and the outcome.
- **`Discovery(clock, pipeline, queue)`.**
  - `upsert_lead` enqueues `enrich_lead` in its transaction when it created a Lead with source `csv` and a `web_domain`.
  - `@use_case canonicalize_lead(lead_id, run_id)` reads the run's `final_domain` through `queries.research` and works on the Lead's survivor:
    - the survivor already holds the domain: nothing;
    - no Lead holds it: `web_domain` is set to it (new repo `set_web_domain`);
    - another Lead holds it: `merge`, the older surviving.

    It logs `lead_canonicalized`.
- **`Research(clock)`:**
  - **`@command reparent_lead(LeadsMerged)`** moves every `crawl_runs` row, and the `crawled_pages` and `ats_links` rows whose `url` the survivor lacks. It is registered after `pipeline`'s.
  - **`@use_case purge_page_text() -> int`** nulls `text` where `text_purge_after <= now`, keeping the hash.
- **Jobs** (`main.build_job_types`, handlers in `jobs/handlers.py`):

  | Job | Lanes (default first) | Priority | Max runtime | Retries | Group |
  |---|---|---|---|---|---|
  | `enrich_lead` | bulk, interactive | 40 | 20 min | 3 | ENRICHMENT |
  | `canonicalize_lead` | bulk, interactive | 45 | 2 min | 2 | ENRICHMENT |
  | `purge_page_text` | bulk | 20 | 10 min | 2 | OTHER |

  - The `enrich_lead` and `canonicalize_lead` keys are `<type>:<lead ulid>:<UTC date>`, with concurrency key `domain:<site>`.
  - Payloads are `{v, lead_id}` and `{v, lead_id, run_id}`.
  - `enrich_lead` passes `may_retry = job.attempts <= 3` and returns `DEFERRED` on `False`.
  - The trigger is `Trigger.PURGE_PAGE_TEXT` with `Every(1 day, at_startup=True)`.
- **Queries** (`app/queries/research.py`): `crawl_run(run_id)`, `latest_crawl_run(lead_id)`, `crawled_pages(lead_id)` and `ats_links(lead_id)`.
- **Wiring and guards.**
  - `build_app` builds `HttpCrawler(meter=meter)` unless a `crawler=` is injected.
  - `tests/live_guard.py` lists `crawler` as `Guarded` with its three constructors.
  - c5 lists `nextron.adapters.crawler`.
  - `PORT_FAKES` maps `CrawlerPort` to `tests/fakes/crawler.py` `ScriptedCrawler`.
- **Contract test** `tests/contracts/test_fetch_policy_contracts.py`. `FETCHING_ADAPTERS` must hold every built adapter package among `crawler`, `renderer` and `ats`. For each one, a denylisted first URL is refused with no resolve and no request, and a redirect to a denylisted host is refused after exactly one request.

**Never:**
- **No test reaches the network.** Tests use `httpx2.MockTransport`, fake resolvers and fake own-address sets.
- **No loophole in production code.** No setting or parameter loosens the egress filter, ports or denylist.
- **No new dependency.**
- **Nothing from later stories:** no renderer or widgets (Story 2.6), no extraction or Evidence (Story 2.9), no ATS calls (Story 2.11), and no Lead page (Story 2.4).
- **Nothing logged that shouldn't be.** No page text, URL query or HTML in logs, events or errors.

### Decisions made overnight (pending user review)

1. **"Website unreachable" on the Lead page.** The Lead page is Story 2.4, which isn't in this tree. This story supplies `queries.research.latest_crawl_run` and `OUTCOME_LABELS` for it.
2. **The CSV trigger** sits in `Discovery.upsert_lead` (CSV source, created, has a website), because Story 2.3's import isn't in this tree. That import calls `upsert_lead`, so it is covered once merged. `Discovery` now takes `queue`.
3. **Canonicalisation keeps Story 2.2's rule:** the older Lead survives a merge, and the final domain stays findable through the merged Lead. With no holder, the old domain is released.
4. **Robots and reachability.**
   - robots.txt is read once per run from the apex host, or from `www.` after a DNS or connect failure.
   - A robots failure other than a 4xx disallows everything for the run.
   - Only `https` is tried.
   - Crawl-delay is honoured, capped at 10 s.
5. **What is followed.** Links are followed to depth 2. The keyword lists above add law-firm words (attorneys, solicitors, practice areas). Hidden links are never followed.
6. **What is stored.**
   - Only 2xx HTML or XHTML pages are stored, one row per Lead and URL, updated in place.
   - Text is capped at 100,000 characters, with format characters removed so verbatim quotes match.
   - Inline-hidden means `display:none` and `visibility:hidden|collapse` only (not opacity), so fade-in content isn't lost.
7. **Address checks.** Every resolved address must pass, IPv4-embedded IPv6 is refused, and IP-literal hosts are refused.
8. **Retries and alerts.** On its last allowed attempt, a timeout or 5xx completes the job instead of failing it, so a flaky site never raises a dead-job alert.
9. **The User-Agent is `NextronBot/1.0 (+https://nextronai.com)`.**
10. **`ats_links` is a new `research` table.** The spine's owner map doesn't list it.
11. **Page URLs are PII class `none`;** only the text is `redact`.
12. **`purge_page_text` runs at every start and every 24 h,** because the PC isn't on at fixed times.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Error handling |
|---|---|---|---|
| Happy crawl | robots allows; home links team, careers, blog post, pdf, off-site, Greenhouse | home, team, careers and blog stored; Greenhouse link recorded; ≥ 1 s apart on a frozen clock | N/A |
| Hidden text | fixture with a script, a comment, `[hidden]`, `aria-hidden`, `display:none` and a soft hyphen | none of the hidden instructions in `text`; the soft hyphen removed | N/A |
| Budget refused | AI spend ≥ budget | no fetch, no row | `DEFERRED` |
| Private target | resolves to 10.x, 127.x, 169.254.x, 100.64.x, 224.x, fc00::, fe80::, ::ffff:10.0.0.1, an own address, or port 8080 | no request | `fetch_refused` |
| Redirects | to a private address; a sixth redirect | one or five requests, then refused | `fetch_refused`; `fetch_too_many_redirects` |
| Denylist | first URL `linkedin.com/x`; redirect to `maps.google.com` | none, or one request | `fetch_refused` |
| Off-domain home | home lands on `new-firm.co.uk` | `final_domain` set; `canonicalize_lead` enqueued | N/A |
| Unreachable | DNS failure, connection refused, or a 404 home | run `unreachable`; job done | no retry |
| Flaky | timeout or 503 | run recorded | retryable, done on the last attempt |
| Robots | 404; 503; disallow `/team` | allow all; disallow all; team skipped and counted | N/A |
| Purge | text 31 days old | `text` NULL, hash kept | N/A |
| CSV Lead | upsert csv, created, with a website | `enrich_lead` queued in the same transaction | N/A |

</frozen-after-approval>

## Code Map

- **Story 2.3's denylist** (read-only, from git objects): `git -C e:/Nextron-track4 show ccc7c1b:<path>` for `nextron-engine/nextron/domain/fetch_policy.py` and `tests/unit/test_fetch_policy.py`, and `git show ccc7c1b -- nextron-engine/nextron/domain/identity.py nextron-engine/tests/unit/test_identity.py` for `url_host`.
- **The httpx2 adapter pattern:** `nextron/adapters/telegram/__init__.py` (`default_http_client`, a lazy `_client()` under a lock, `Metered`, `AdapterError(retryable, code, message)` raised `from None`). Tests: `tests/unit/test_telegram_adapter.py`, with `httpx2.MockTransport` and a recorder.
- **httpx2 facts** (checked):
  - `URL.copy_with(host=ip)` brackets IPv6;
  - a `Host` header you supply is kept;
  - `extensions={"sni_hostname": …}` drives SNI and the hostname check;
  - there is no total timeout, so use a monotonic deadline in the `iter_bytes` loop;
  - `iter_bytes` decompresses, so cap the decoded bytes;
  - the default client keeps cookies;
  - the pool keys on the origin, so set `Limits(max_keepalive_connections=0)`.
- **selectolax 1.0.2.**
  - Use `selectolax.lexbor.LexborHTMLParser`. `node.is_comment_node` and `decompose()` remove comments.
  - `<template>` content isn't in the tree.
  - Without `scripting=True`, a `<noscript>` in the head can move the `<title>` into the body, so decompose `title` too.
  - Iterate with an explicit stack, not recursion.
- **protego 0.7.0:**
  - `Protego.parse(text)`, `can_fetch(url, ROBOTS_TOKEN)`, `crawl_delay(ROBOTS_TOKEN)`;
  - a BOM breaks parsing, so decode `utf-8-sig`.
- **Ports and metering:** `nextron/ports/usage_meter.py` (`Metered`), `nextron/ports/errors.py`, and the `ports/llm.py` style.
- **Budget:** `nextron/domain/budget.py` `allow`, `BudgetClass.BLOCKABLE`, `queries.setup.budget_limits()` and `meter.snapshot(now)`, inside a transaction. See also `ops/api.py:398` and the README at 1083.
- **Jobs:**
  - `nextron/domain/job.py`: `JobType`, `idempotency_key`, the concurrency-key and payload rules (no URLs in payloads), `DEFERRED`;
  - `app/jobqueue/api.py:268` `enqueue(job_type, *, scope, discriminator, payload, run_after=None, lane=None, concurrency_key=None)`;
  - `main.py:621` `build_job_types` and `main.py:672` `build_triggers`;
  - `jobs/triggers.py` (`Trigger.PURGE_PAGE_TEXT`, `Every`);
  - `jobs/handlers.py`, which imports only the domain and the stdlib.
- **Every caller of `build_job_types` and `build_triggers`** must pass the new keywords: `tests/dashboard.py`, `tests/app/test_engine_health.py`, `test_backup_contracts.py`, `test_dead_job_alerts.py`, `test_budget.py`, `test_backups.py`, `test_job_contracts.py` (and its `names()` and `specs()` asserts), `test_notify.py` and `test_runner_lifespan.py:168,462`. `test_health_contracts.py:151` requires `group=`.
- **The I/O-then-apply pattern:** `ops/api.py:845-920` (`Backups.back_up`), `notify/api.py:235` (`Notify.send`), and the `uow.py` docstring.
- **Discovery:**
  - `app/discovery/api.py` (`_survivor`, `_age`, `merge`, `upsert_lead`);
  - `db/repos/discovery/leads.py`, which adds `set_web_domain`;
  - the only constructor call is in `tests/app/test_leads.py:124`.
- **Subscribers:** `main.py:863` `build_subscribers`. `tests/unit/test_serve.py` asserts the registry.
- **Settings:** `domain/settings_schema.py` (`Key`, `SCHEMA`, the list type, `SINK_RECIPIENT_RULE` at 812), plus the tests at `tests/unit/test_settings_schema.py:505`, `tests/contracts/test_settings_contracts.py:132`, `tests/unit/test_settings_page.py:537` and `tests/app/test_telegram_secrecy.py:182`.
- **Persistence:**
  - the precedents `versions/0012_2_8_ai_routes.py` and `db/models/playbook.py`;
  - the new models file `db/models/crawl.py` (not `research.py`, which Story 2.3 adds);
  - repos in `db/repos/research/crawl.py`;
  - `owners.py`, `pii_registry.py` and `models/__init__.py`;
  - head bumps: `test_persistence_contracts.py:72` plus its chain probes at 670–682 (`0013_*` → `0014_*`, and so on), `test_migrations.py:39`, `test_serve_process.py:283`, and `test_serve.py:759,773` (`0013_more` → `0014_more`).
- **Isolation:**
  - `tests/live_guard.py` (`LIVE_CONSTRUCTORS`, `ADAPTER_PACKAGES`);
  - `tests/fakes/__init__.py` `PORT_FAKES`;
  - `tests/contracts/test_test_isolation.py:134` (the port-name set);
  - `pyproject.toml` c5;
  - `tests/contracts/test_source_tree.py` `FILES`.
- **Untrusted blocks (for Story 2.9):** `domain/ai.py` `BLOCK_ID_PATTERN`. A page id (a ULID) is a valid `block_id`.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/identity.py`, `nextron/domain/fetch_policy.py`, `nextron/domain/crawl.py` -- `url_host` (2.3's), the denylist and egress rules, and the crawl rules and text normalisation.
- [x] `nextron/ports/crawler.py`, `nextron/adapters/crawler/__init__.py` (and a parsing module) -- the port and `HttpCrawler`.
- [x] `nextron/db/alembic/versions/0013_2_5_crawl.py`, `db/models/crawl.py`, `models/__init__.py`, `owners.py`, `pii_registry.py`, `db/repos/research/crawl.py`, `db/repos/discovery/leads.py` -- the three tables and their repos, plus `set_web_domain`.
- [x] `nextron/domain/settings_schema.py` -- `crawl_max_pages` and `ats_hosts`.
- [x] `nextron/app/research/api.py`, `app/queries/research.py`, `app/discovery/api.py` -- `WebsiteCrawls`, `Research`, the queries, the CSV enqueue and `canonicalize_lead`.
- [x] `nextron/jobs/handlers.py`, `nextron/main.py` -- three job types, the trigger, the subscriber and the wiring.
- [x] `tests/live_guard.py`, `tests/fakes/crawler.py`, `tests/fakes/__init__.py`, `pyproject.toml` -- the guard, the fake and c5.
- [x] Tests:
  - `tests/unit/test_fetch_policy.py` (2.3's plus the additions and the egress rules), `test_crawl_domain.py`, `test_crawler_adapter.py` (every refused range and port, the redirect cases, pinning, cookies, size, type, timeout, pacing, parsing and the hidden-content fixture);
  - `tests/app/test_website_crawl.py` (every matrix row, reparent, purge, canonicalize, the CSV enqueue);
  - `tests/contracts/test_fetch_policy_contracts.py`;
  - the head bumps, `FILES`, and the job, trigger, settings and subscriber assertions.
- [x] `nextron-engine/README.md` -- a "Website crawling" section, after "Prompts and the active route tuple".

**Acceptance Criteria:**
- Given the full suite, when `pytest` and `lint-imports` run, then everything passes, no test touches the network, and every contract is kept.
- Given `serve` on a fresh data dir, when it starts, then the migration reaches `0013_2_5_crawl` and a `purge_page_text` tick is enqueued at scheduler start.

## Implementation Notes

Built as specified. What the spec left open, and where the code had to choose:

- **`identity` and `fetch_policy` (Story 2.3's, ported).** `url_host`, the refactored
  `registrable_domain` and the two docstring hunks are 2.3's, verbatim, without
  `COUNTRY_ALIASES`; 2.3's `url_host` tests are in `tests/unit/test_identity.py`, and 2.3's
  denylist tests open `tests/unit/test_fetch_policy.py` (`test_the_lists` gains the four
  domains).
- **The egress rules** (`nextron/domain/fetch_policy.py`).
  - `url_refusal(url) -> str | None` is public: the reason `check_url` would raise. An adapter
    can't put a caught exception's text into an `AdapterError`
    (`tests/contracts/test_error_contracts.py`), so it asks for the reason instead of catching
    `FetchRefused`.
  - `bad_url` also covers an empty URL, whitespace or a `Cc` or `Cf` character anywhere, a
    missing host, a port that isn't a number from 0 to 65535, and a host `url_host` refuses.
    A URL with no scheme (`smith-law.co.uk/team`, `//host/x`) is `scheme`.
  - `ip_host` also covers a name whose last label is a decimal or `0x` number (`127.1`,
    `0x7f.1`, `2130706433`), which resolvers read as IPv4, and any host holding `:` or `%`.
  - `FetchTarget.url` is canonical: the scheme lowercased, the `url_host` host, an explicit
    port kept unless it is the scheme's default, an empty path written `/`, the query kept, the
    fragment dropped. `path` is the path alone.
  - `ipv4_embedded` also refuses the deprecated IPv4-compatible `::/96` (`::8.8.8.8`), which
    `is_global` would pass. The first group uses Python 3.14's properties, so
    `::ffff:127.0.0.1` is `loopback`; `::ffff:10.0.0.1` is `ipv4_embedded`.
  - `own_addresses` are compared without an IPv6 scope. `URL_REASONS`, `ADDRESS_REASONS` and
    `OFF_SITE` name the reasons.
- **`nextron/domain/crawl.py`.**
  - `classify_link` tokenises with `[^\W_]+` (letters and digits, any script) after
    lowercasing; the path is percent-decoded first. The path decides when it matches, else the
    anchor text, so `/careers` with "Join our team" is `careers`; within one, the first
    `PageKind` wins. `NON_HTML_EXTENSIONS` lists documents, office files, images, archives,
    audio and video (`;` parameters ignored).
  - `crawl_interval(None)`, a negative delay or a NaN is 1 s; `inf` is 10 s.
  - `visible_text` splits with `str.splitlines` (so U+2028 and form feeds break lines too) and
    collapses each whitespace run with `str.split` (NBSP included). When the first line alone
    passes `MAX_STORED_TEXT`, it is cut at the cap.
  - It also holds `request_interval(min_interval_s)` (a crawler's floor of 1 s; a NaN is 1 s;
    a non-number raises `TypeError`, since an adapter may raise only `AdapterError`),
    `http_code(status)`, `lead_job_keys(site, now)`, the three job names, `PAYLOAD_VERSION`,
    `LEAD_ID_FIELD`, `RUN_ID_FIELD`, `HTML_MEDIA_TYPES` and `LINK_TAGS` (the models read it
    from here: `nextron.db` can't import `nextron.ports`).
  - `OUTCOME_LABELS` labels all four outcomes ("website crawled", "website unreachable",
    "website not crawled: its robots.txt disallows it or could not be read", "Lead merged during
    the crawl").
- **The port** (`nextron/ports/crawler.py`) holds the `FETCH_*` codes. `FetchedPage` hides its
  `text` and `links` from the `repr` as well as its `html`, has an `ok` property, and refuses
  (`ValueError`) a non-2xx page that carries text, HTML or links. `Robots` refuses rules or a
  crawl delay on a non-2xx answer.
- **The adapter** (`nextron/adapters/crawler/__init__.py` and `parsing.py`).
  - `default_http_client(transport=None)` takes an optional transport, so a test can watch what
    the real client sends. Its own timeouts are 30 s per phase (10 s to connect), but each
    request passes `timeout=` the fetch's time left (connect at most 10 s); see the review
    fixes for the one deadline per fetch.
  - Each request also carries `User-Agent`, `Accept` and `Connection: close`, so an injected
    client behaves the same.
  - The first URL and the `site` pin are checked before any live part is built.
  - A resolver that raises `OSError` or `UnicodeError`, returns nothing, or returns something
    that isn't an address is `fetch_dns_failed`; any other resolver exception is
    `fetch_failed`. Only the first address is tried.
  - Pacing keys on the host's registrable domain (the host itself when it has none). A
    request reserves its slot under a lock and then sleeps until it, so a refused hop takes no
    slot. Past 1,024 sites, slots older than 60 s are forgotten.
  - `fetch_robots` has no `site` pin: its redirects may leave the domain, and every hop is still
    filtered. A protego error while parsing is `fetch_failed`; while judging a URL, `False`.
  - `application/json` pages return `visible_text` of the body, with no links and no HTML.
  - Parsing: `br` and `hr` are block boundaries (a line break shows there); a line break inside
    a text node becomes a space, except within `pre`. Embeds come after the anchors in
    `links`. A link's href or src has ASCII whitespace stripped and tabs and newlines removed
    before it is resolved; only `http` and `https` links are kept.
- **Persistence** (`0013_2_5_crawl`). The names: `ix_crawl_runs_lead_id`,
  `ck_crawl_runs_outcome`, `ck_crawl_runs_counts` (one CHECK for the four counts),
  `uq_crawled_pages_lead_id_url`, `ck_crawled_pages_kind`, `uq_ats_links_lead_id_url` and
  `ck_ats_links_tag`; the foreign keys follow the naming convention. Lengths: domains 253, URLs
  2,048, `outcome` 32, `error_code` 64, `kind` 16, hashes 64, `tag` 8.
- **Settings.** Both are appended to `Key`, in the Engine section: "Pages crawled per Lead" and
  "ATS board hosts". The rule is `ats_host` (`is_ats_host`).
- **`WebsiteCrawls.enrich_lead`.**
  - The run's `error_code` is the robots.txt failure's code when robots.txt failed (other than
    a 4xx that isn't 429), else the homepage's; that is what step 6's retry reads, so a
    robots.txt timeout or 5xx retries the job too.
  - A 2xx robots answer without rules (never from the real adapter) disallows everything.
  - A homepage failure adds 1 to `errors`. `pages_fetched` counts the 2xx answers, the homepage,
    a page whose final URL was already stored and a JSON page included. A JSON subpage is
    dropped without counting an error.
  - Only `a` links are followed; a URL is marked seen when it is taken, so a later link to it
    with a matching anchor text can still be taken. The deadline is checked before each
    subpage.
  - ATS links come from stored pages only, as the URL `check_url` writes (no fragment), matched
    on its exact host; within a run the first page holding a URL is its `page_id`.
  - A `lead_merged` run keeps the observed counts, `error_code` and `final_domain`; nothing else
    is written and nothing is enqueued.
  - `crawl_finished` is logged before the retry is raised. A crawl the budget refuses logs
    nothing.
- **`Discovery.canonicalize_lead`** returns its action (`nothing`, `kept`, `moved` or
  `merged`), which `lead_canonicalized` logs. A non-ULID id or an unknown run is `nothing`, and
  so are a shared-platform final domain and a survivor whose `web_domain` isn't the run's
  `site_domain` (see the review fixes).
- **Handlers.** A payload `lead_id` or `run_id` that is missing or isn't a ULID raises
  `InvalidJob`. `handlers.ENRICH_LEAD_RETRIES` (3) is both the job type's retry limit and the
  `may_retry` bound.
- **Tests and fakes.**
  - `ScriptedCrawler(robots=..., pages=...)` answers by URL (a value, an `AdapterError`, or a
    callable that may move the clock), makes the adapter's first-URL checks first, records
    `CrawlCall`s, and raises `AssertionError` for an unscripted URL. `robots_txt(...)` and
    `html_page(...)` build answers with protego and the real parser.
  - The matrix's "a sixth redirect: five requests" is read with the Boundaries: five redirects
    are followed, so six requests are made (the first and five redirected ones), and the sixth
    redirect is refused before a seventh.
  - `tests/contracts/test_adapter_boundary.py` gains an `HttpCrawler` driver.
  - Callers of `build_job_types` pass `crawls`, `discovery` and `research` (each test file gets a
    `no_crawls`, the dashboard harness `no_crawl_jobs`). `tests/app/test_leads.py` builds its
    `Discovery` with a real `JobQueue`.
  - The health panel and engine-health tests now list the Enrichment group (`enrich_lead`,
    `canonicalize_lead`) and `purge_page_text` under Other. The scheduler tests count two
    triggers. `test_health_contracts`'s probe now targets `send_alert`'s group line, because
    `purge_page_text` is in group Other too.
  - `test_serve_process` waits for `page_text_purged` and checks the `purge_page_text:global:tick`
    job.
  - `test_test_isolation`'s classification probe gains the `crawler` package.

**Review fixes (pass 1)** (from the orchestrator's review; each has its tests):

- **Charsets.** A header or `<meta>` charset that isn't a text encoding (`base64`, `hex`,
  `rot13`, ...) or is `undefined` is ignored, and a decode that still raises falls back to
  UTF-8 with replacement, so no codec error escapes the adapter. The domain-name codec is not
  refused by name (SC-8: only `domain/identity.py` may name it): it passes plain ASCII through
  and raises on anything else, which falls back to UTF-8.
- **Odd statuses.** A status outside 100 to 599 is `fetch_failed` (the port values would refuse
  it).
- **The new domain's robots.txt (extends overnight Decision 4).** When the homepage lands on
  another registrable domain, that origin's robots.txt (the final URL's scheme and host) is
  read once before any further page and read as step 2 reads one: a 2xx gives its rules and
  `crawl_interval`; a 4xx other than 429 allows everything; anything else (DNS failures
  included) disallows everything. Its rule and interval govern the rest of the run, and a
  failure's code becomes `error_code` only when the run has none.
- **Shared platforms.** Story 2.3's `is_shared_platform`, `SHARED_PLATFORM_DOMAINS`,
  `SHARED_PLATFORM_BRANDS` and their docstring paragraph are ported verbatim into
  `nextron/domain/leads.py`, with their tests. A homepage that lands on a shared platform ends
  the run `unreachable` with `error_code` `shared_platform`: no page is stored, `final_domain`
  stays NULL and nothing is enqueued. `canonicalize_lead` returns `nothing` for a run whose
  final domain is one.
- **Labels.** `robots_disallowed` reads "website not crawled: its robots.txt disallows it or
  could not be read".
- **Throttling.** A robots.txt 429 is read like a 5xx (disallow everything, `http_429`). A page
  answering 429 or 503 counts as an error and stops the run's fetching; the run stays `ok`
  and is not retried.
- **One deadline per fetch.** `REQUEST_DEADLINE_S` (30 s) starts when a fetch's first request is
  about to go and covers every later hop, its lookup and paced wait included. It is checked
  before each hop and again before each request (past it: retryable `fetch_timeout`, with no
  request sent), and each request's `timeout` is the time left (connect at most 10 s). In the
  crawl, a run whose robots.txt read took it past `RUN_DEADLINE` ends `unreachable` with
  `fetch_timeout` before the homepage.
- **Content a browser never renders.** `iframe` content, `noembed`, `noframes`, `datalist`, SVG
  `desc` and `metadata`, and a `dialog` without `open` are removed (iframe and script embeds
  are collected first, as before).
- **CSS escapes.** Inline styles are unescaped after their comments are removed: a hex escape
  (up to six digits and one optional whitespace; 0, a surrogate or a value past U+10FFFF is
  U+FFFD), else the escaped character. So `display:n\one` and `display:\6e one` hide;
  `visibility:hidd\en` does not, because `\e` is U+000E in CSS and a browser doesn't hide it
  either (`hidd\65 n` does).
- **`html` and `body`** are never removed, whatever hides them (an anti-flicker snippet);
  their descendants are still checked.
- **Reparenting links.** After a merge moves the links, a moved link whose page stayed with the
  merged Lead (because the survivor holds that page's URL) points at the survivor's page with
  that URL.
- **Canonicalizing a merged Lead.** `canonicalize_lead` acts only when the survivor's
  `web_domain` is the run's `site_domain` (or already the final domain: `kept`); otherwise
  `nothing`, so a survivor's own other website is never replaced.
- **Own addresses.** `default_own_addresses` raises `AdapterError` `fetch_failed` when they
  can't be read, so the fetch fails closed before its first hop.
- **Default ports.** `check_url` leaves a scheme's default port out of the canonical URL, so
  `https://x:443/team` and `https://x/team` are one page.
- **Tests.** The fetch-policy contract no longer pins the built packages to `{"crawler"}` and
  now also proves that a first URL resolving to a private address, and a redirect to one, are
  refused before any request to them. New tests cover kind order against document order (and
  `crawl_max_pages` 2 keeping `team`), the holder-already-merged `kept` branch,
  `crawl_max_pages` and `ats_hosts` in the settings schema, and pruning the pacing table.
- **Source hygiene.** Invisible characters (format characters, separators, a BOM) that had
  landed literally in test strings and one regex are written as `\u` escapes.

**Implementation verification** (targeted; the full suite is the orchestrator's gate): the new and touched test files with `tests/contracts`, 2661 passed and 1 skipped (the opt-in `ai_eval` check). `lint-imports`: 29 kept, 0 broken.

**Final verification** (on the final tree, after the review fixes): the full suite through `suite_lock.py` (track-D). The first run had one failure, the known runner flake `tests/unit/test_job_runner.py::test_start_runs_one_realtime_one_interactive_the_bulk_threads_and_the_keeper` (this tree predates main's fix). The single permitted re-run gave 7631 passed and 3 skipped (the two `ai_eval` tests and the symlink test). `lint-imports`: 29 kept, 0 broken. Before that run, the orchestrator removed the domain-name codec's name from `adapters/crawler/parsing.py`, because SC-8's `test_no_other_module_normalises_identities` allows that word only in `domain/identity.py` (see Charsets above). No real website has been fetched.

## Spec Change Log

## Review Triage Log

**Pass 1.** The three layers reported 38 raw findings:
- Blind Hunter (B): 16;
- Edge Case Hunter (E): 16;
- Verification Gap (V): 5 gaps plus 1 other finding.

Merging duplicates leaves 29: B1+E1, B2+E5+E16+V-o1, B3+E4, B6+E10+E11, B11+E14 and B15+E12.
- **Routing:** 20 patch, 0 defer and 9 reject.
- **Loopbacks:** there are no `intent_gap` or `bad_spec` entries, so nothing loops back.
- **Logged in `deferred-work.md`:** the residual risk under #4 (parking and marketplace domains).

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | B1+E1: a header or `<meta>` charset naming a non-text codec (`base64`, `hex`, `rot13`, `undefined`, `idna`) makes `decode` raise `LookupError` or `UnicodeError`. It escapes `fetch_page` as a non-`AdapterError`. | medium | Real. `_known` accepts any `codecs.lookup` name, and `decode` runs outside the adapter's `try` (`crawler/__init__.py`). `_home` and `_page` catch only `AdapterError`, so the job dies and raises an alert. | patch (accept only text encodings; fall back to UTF-8 when decoding fails) |
| 2 | E2: a status outside 100–599 (h11 takes any 3 digits) makes `Robots` or `FetchedPage` raise `ValueError`. | medium | Real. It breaks the adapter's "only `AdapterError` gets out" rule, and the job dies. | patch (`_read`: a status outside 100–599 is `fetch_failed`) |
| 3 | B2+E5+E16+V-o1: when the homepage lands on another registrable domain, that domain's robots.txt is never read; its pages are judged by the old site's rules and Crawl-delay. | medium | Real (`_crawl` keeps `origin.allows`). It breaks the intent's "every fetch must obey robots.txt" and FR-10/NFR-6. The spec says which robots.txt applies only to the run's own origin. | patch (read the final origin's robots.txt once, before any page there, with the same 2xx/4xx/other rules; extends Decision 4, see Implementation Notes) |
| 4 | B3+E4: canonicalisation trusts any landing domain. A firm whose site forwards to `facebook.com`, a site builder or a directory becomes that domain, and the next such firm is merged into it, irreversibly. | high | Real. `_home` sets `final_domain` for any landing domain, and `_canonicalize` moves or merges on it. Story 2.3 already defines `is_shared_platform` for exactly this risk. | patch (port 2.3's `is_shared_platform` verbatim; a home landing on a shared platform ends the run `unreachable` with `shared_platform`, stored nothing and enqueues nothing; `_canonicalize` also returns `nothing` for one). Parking and marketplace domains, which aren't on the list, are logged in `deferred-work.md`. |
| 5 | B4: a robots.txt that failed (5xx, timeout, too large, refused address) shows as "website blocked by robots.txt". | low | Real. The label is `OUTCOME_LABELS[robots_disallowed]`. The outcome itself is what the spec prescribes. | patch (the label says the crawl stopped because robots.txt disallows it or couldn't be read) |
| 6 | B5: a robots.txt 429 counts as "4xx: allow everything", and a 429 or 503 on a page doesn't slow the run. | medium | Real. `_robots` treats every 4xx alike, and `_page` only counts the error. NFR-6 politeness: a site asking us to slow down is crawled anyway. | patch (a robots 429 disallows everything like a 5xx; a page answering 429 or 503 ends the page loop) |
| 7 | B6+E10+E11: the 30 s deadline is per hop, and each read gets a fresh 30 s, so slow sites can push a run past the job's `max_runtime`. A server dripping header bytes can hold a hop open longer. | medium | Real for slow, honest servers: 6 hops × (connect + read) per fetch, plus robots on two origins and the homepage, before the run deadline is first checked. The header drip needs an adversarial server; the lease recovers it. | patch (one deadline per fetch, across all hops, with each request's timeouts capped at the time left; the run deadline is also checked before the homepage). The header drip is rejected: adversarial only, and a watchdog thread isn't a direct fix. |
| 8 | B7: content a browser never renders reaches the stored text: `<iframe>` fallback text, `<noembed>`, `<noframes>`, a closed `<dialog>`, `<datalist>`, and SVG `<desc>`/`<metadata>`. | medium | Real (verified by probe). These are AD-18 hiding places for injected instructions. | patch (remove them, with a `dialog` lacking `open` counted as hidden; extend the hidden fixture) |
| 9 | B8: CSS escapes in an inline style (`display:n\one`, `\6e one`) slip past the inline-hidden check. | medium | Real. `_style_hides` compares raw text, while browsers unescape first. It is an injection vector. | patch (unescape CSS escapes before comparing) |
| 10 | B9: one kind can use up the page budget. Every lawyer bio is `team`, so about, careers and contact may never be fetched. | medium | Real, but the frozen spec orders candidates by `PageKind`, then document order. Taking one of each kind first changes the approved order. | reject: would change the approved spec (reported to Mac) |
| 11 | B10: after a merge, `latest_crawl_run` may show the merged Lead's own website's outcome. | low | Real, but it lasts only until the survivor's next crawl; most merges happen at import, before any crawl. The fix adds a domain filter. | reject |
| 12 | B11+E14: a moved `ats_links` row keeps a `page_id` whose page stayed on the merged Lead; pages left on the merged Lead keep `run_id`s now owned by the survivor. | medium | Real (`crawl_repo.reparent`). Story 2.11 joining a link to its page would read another Lead's page. The `run_id` part is cosmetic. | patch (re-point a moved link's `page_id` to the survivor's page with the same URL); the `run_id` part is rejected |
| 13 | B12: the fetch-policy contract hard-codes `built_fetching_packages() == {"crawler"}`, so Story 2.6's driver still fails it, and the contract checks only the denylist, not the AD-9 address rules. | low | Real (`test_fetch_policy_contracts.py`). | patch (drop the hard-coded set; each driver also proves a private first address and a redirect to a private address are refused) |
| 14 | B13: `canonicalize_lead` doesn't check that the run belongs to the Lead. | false | The payload is written in the same apply transaction from the same run; no other code enqueues the job. The reachable case is #15. | reject |
| 15 | E3: a crawled Lead merged into a survivor with another `web_domain` before `canonicalize_lead` runs. The survivor's own domain is then replaced by the merged Lead's redirect target, and released. | medium | Real (`_canonicalize` works on the survivor without comparing its domain with the run's `site_domain`). | patch (act only when the survivor's `web_domain` is the run's `site_domain` or already the final domain; otherwise `nothing`) |
| 16 | B14: a Lead that gains a website later, from a CSV row matching an existing Lead, is never crawled. | medium | Real, but Decision 2 (frozen) enqueues only for a created Lead. | reject: would change the approved spec (reported) |
| 17 | B15+E12: `default_own_addresses` returns an empty set on `OSError` (fails open). It also never knows the router's public address. | low | The `OSError` part is real, and a PC's global IPv6 address is refused only by this check. Finding the router's public address needs an outside service, which AD-9 doesn't ask for. | patch (an `OSError` raises `AdapterError(False, fetch_failed)`); the public-address part is rejected |
| 18 | B16: the default `ats_hosts` miss the EU boards (`job-boards.eu.greenhouse.io`, `jobs.eu.lever.co`). | low | Real, but the frozen spec lists the four defaults, and Mac can add hosts in Settings. | reject: would change the approved spec (noted for Story 2.11) |
| 19 | E6: a robots.txt served with no `Content-Type` or as `application/octet-stream` is `fetch_unsupported_type`, so the whole site is disallowed. | low | Real, but the spec takes only `text/*` for robots.txt, and the failure is closed (nothing is crawled). | reject: spec, fails closed (reported) |
| 20 | E7: only the first resolved address is tried, so a broken IPv6 path makes a working site unreachable. | medium | Real, but the spec says the GET goes "to the first address". | reject: would change the approved spec (reported) |
| 21 | E8: an http-only site, or one with a bad certificate, is unreachable. | low | Decision 4 (frozen): only `https` is tried. | reject: spec |
| 22 | E9: `<html>` or `<body>` that is hidden, or styled `display:none` / `visibility:hidden` (anti-flicker snippets), makes the parse fail or empties the page, so the homepage is unreachable. | medium | Real (verified: `SelectolaxError` → `fetch_failed`). | patch (the root and `body` are never treated as hidden) |
| 23 | E13: `min_interval_s=inf` overflows `sleep`. | false | No caller can pass it: `crawl_interval` caps at 10 s, and the service's other value is `MIN_INTERVAL_S`. | reject |
| 24 | E15: `https://x:443/team` and `https://x/team` are stored as two pages. | low | Real; dropping a default port from the canonical URL is a direct correction. | patch |
| 25 | V1: ordering candidates by kind is never tested; every fixture lists its links in kind order. | gap (pre-verified) | Filed with evidence. | patch (a test with out-of-order links, under a page cap) |
| 26 | V2: moving only the ATS links the survivor lacks is never exercised; dropping the filter would break the unique index on merge. | gap (pre-verified) | Filed with evidence. | patch (both Leads hold the same board link) |
| 27 | V3: `canonicalize_lead`'s "holder already merged into the survivor" branch never runs. | gap (pre-verified) | Filed with evidence. | patch (a second `canonicalize` returns `kept`) |
| 28 | V4: the `crawl_max_pages` range and the `ats_hosts` rule are untested. | gap (pre-verified) | Filed with evidence. | patch (tests mirroring `bulk_threads`) |
| 29 | V5: pruning the pacing table is untested. | gap (pre-verified) | Filed with evidence. | patch (monkeypatched `PACE_PRUNE_SIZE`) |

## Verification

**Commands** (from `e:/Nextron-track4/nextron-engine`):
- `uv run pytest -q tests/unit/test_fetch_policy.py tests/unit/test_crawl_domain.py tests/unit/test_crawler_adapter.py tests/app/test_website_crawl.py tests/contracts` -- all pass.
- `uv run lint-imports` -- every contract kept.
