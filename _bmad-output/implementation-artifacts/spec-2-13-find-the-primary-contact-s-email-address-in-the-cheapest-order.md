---
title: "Story 2.13: Find the Primary Contact's email address in the cheapest order"
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: '98d0250856afbbbec85eacec7f1897ba97314ab5'
review_loop_iteration: 0
story_key: '2-13-find-the-primary-contact-s-email-address-in-the-cheapest-ord'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-10-find-named-decision-makers-and-pick-the-primary-contact.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-3-4-gate-every-outreach-path-with-assert-contactable.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Story 2.10 picks a Primary Contact, but nothing finds that person's email address. The crawler drops `mailto:` links, nothing learns a firm's address pattern, and there is no Hunter or Prospeo adapter. Without an address, no Lead can reach Eligibility (Story 2.14 onward).

Requirements: FR-14, FR-51, FR-57 · AD-7, AD-9, AD-10, AD-16, INT-6 · NFR-12.

**Approach:**
- **The job.** A new `find_contact_email` job tries four sources in order and stops at the first address it can assign:
  1. an address published on the crawled pages;
  2. a learned pattern;
  3. Hunter Domain Search;
  4. Hunter Email Finder, then Prospeo.
- **Triggers.** It is enqueued by `select_primary_contact`, by "Make primary" and by a new "Find email" button.
- **Storage.** It adds the `email_domains` table, a per-page `mailto` list, and per-Contact search state.
- **Calls.** Every paid call is metered and quota-checked. Every assignment goes through `assert_contactable` and writes Provenance.

The acceptance criteria are Story 2.13 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**

- **Pure domain: new `nextron/domain/email_finding.py`** (stdlib and `nextron.domain` only).
  - **Constants:**
    - `FIND_CONTACT_EMAIL_JOB = "find_contact_email"`;
    - `BUDGET_CLASS = BudgetClass.BLOCKABLE`;
    - `HUNTER_QUOTA = "hunter"` and `PROSPEO_QUOTA = "prospeo"` (the `QUOTAS` names).
  - **`ROLE_LOCAL_PARTS`:** the AC's six (info, contact, office, admin, team, sales), plus hello, enquiries, enquiry, inquiries, support, help, mail, email, reception, accounts, billing, careers, jobs, hr, marketing, press, media, privacy, legal, webmaster, noreply, no-reply, bookings, service and admissions.
  - **`is_role_address(email)`:** the whole local part, casefolded and with any `+tag` dropped, is in `ROLE_LOCAL_PARTS`.
  - **`NameTokens(first, last)`:** `name_tokens(name) -> NameTokens | None`.
    1. Fold: NFKD, combining marks dropped, casefold. Apostrophes and hyphens are removed inside words. Split on everything else that isn't a letter.
    2. Drop honorifics (mr, mrs, ms, miss, dr, prof, sir, dame) and suffixes (jr, sr, ii, iii, iv, esq, qc, kc, phd, cpa).
    3. Drop one-letter middle tokens.
    4. `first` is the first token and `last` the last (`None` when only one token is left). The result is `None` with no token.
  - **`Pattern`** (StrEnum, the AC's seven: `first.last`, `flast`, `first`, `firstlast`, `first_last`, `last.first`, `f.last`).
    - `local_part(pattern, tokens) -> str | None` is `None` when the pattern needs a `last` that isn't there.
    - `fitting(local, tokens) -> frozenset[Pattern]`.
    - `HUNTER_PATTERNS`: Hunter's `{first}.{last}`, `{f}{last}`, `{first}`, `{first}{last}`, `{first}_{last}`, `{last}.{first}`, `{f}.{last}` mapped to `Pattern`. Anything else maps to `None`.
  - **Published-address rules** (Decision 4): `addresses_in(text)` and `published_match(tokens, name, pages, *, exclude) -> PublishedMatch | None`.
    - `pages` are `PageAddresses(page_id, url, fetched_at, text, mailto)`, in `page_order`.
    - The result carries the address, page, rule and quote.
  - **`learn_pattern(examples) -> Pattern | None`** (Decision 7).
  - **`EmailSearchOutcome`:** `found`, `not_found`, `quota_reached`, `suppressed`, `lead_closed`.
  - **`FinderStep`:** `published`, `pattern`, `hunter_domain_search`, `hunter`, `prospeo`.
  - **`provenance_for(step, ...)`:** the Provenance source and detail per Decision 2.
- **Port: new `nextron/ports/email_finder.py`** (Decision 1).
  - Frozen values with personal fields `repr=False`:
    - `FinderQuery(domain, first_name, last_name)`;
    - `FinderAnswer(email: str | None, claimed: bool = False)`;
    - `DomainPerson(email, first_name, last_name, personal: bool)`;
    - `DomainSearchAnswer(pattern: str | None, people: tuple[DomainPerson, ...])`.
  - Protocols:
    - `EmailFinderPort.find_email(query) -> FinderAnswer`;
    - `DomainSearchPort.domain_search(domain) -> DomainSearchAnswer`.
- **Adapters** (each modelled on `adapters/telegram`).
  - Each has `default_http_client()` (`httpx2`, timeout 15 s with connect 10 s, `follow_redirects=False`) and `__init__(secrets, *, meter, http=None)`.
  - The key is read with `read_credential` on every call and sent only in a header. It never goes in a URL, error or log.
  - One `Metered` record per request: `count` 1 for a 2xx answer, 0 otherwise.
  - Errors are `AdapterError` with the module's own text, raised `from None`:
    - transport errors, 429 and 5xx are retryable (`*_unavailable`);
    - 401, 403 and other 4xx are not (`*_refused`), unless a rule below says otherwise;
    - an answer that isn't the expected JSON shape is not retryable (`*_bad_answer`).
  - **`adapters/hunter`: `HunterFinder`**, which implements both Protocols.
    - Base `https://api.hunter.io/v2/`, header `X-API-KEY`, provider `hunter`.
    - **Domain Search:** `GET domain-search?domain=&limit=10`, operation `domain_search`.
      - It reads `data.pattern` and `data.emails[]` (`value`, `type == "personal"`, `first_name`, `last_name`).
    - **Email Finder:** `GET email-finder?domain=&first_name=&last_name=`, operation `find`.
      - It reads `data.email`; null means none.
      - A 404 means none.
      - A 451 (`claimed_email`) gives `FinderAnswer(None, claimed=True)`.
      - Hunter's 403 is its rate limit, so a 403 is retryable here.
  - **`adapters/prospeo`: `ProspeoFinder`**, which implements `EmailFinderPort`.
    - `POST https://api.prospeo.io/enrich-person`, header `X-KEY`, provider `prospeo`, operation `find`.
    - Body: `{"only_verified_email": true, "data": {"first_name", "last_name", "company_website": domain}}`.
    - Responses:
      - a 200 with `error` false gives `person.email.email` (a `str`, else none);
      - a 400 with `error_code` `NO_MATCH` gives none;
      - `INSUFFICIENT_CREDITS` is retryable;
      - `INVALID_API_KEY` is refused.
  - Hunter's doubled units are already in `UNIT_WEIGHTS`: find 2, Domain Search 2. Prospeo's find is 1.
- **Migration `0030_2_13_email_domains`** (down `0029_2_9_tag_managers`; `downgrade()` raises).
  - **New table `email_domains`**, owner `research`, PII `none`:
    - columns: `id` (ULID), `domain` (unique, the registrable mail domain), `pattern` (nullable, a `Pattern` value), `pattern_source` (nullable: `published` or `hunter_domain_search`), `pattern_evidence_ids` (nullable JSON list of Evidence ids), `pattern_learned_at`, `domain_search_at` (nullable), `created_at` and `updated_at`;
    - CHECKs: `pattern` and `pattern_source` values; `pattern`, `pattern_source` and `pattern_learned_at` are all null or all set; `pattern_evidence_ids` is set only for `published`.
  - **`contacts` gains three columns**, `add_column` only, no CHECK (as 2.10's Decision 7), owner `research`, PII `none`:
    - `email_search_tried`: comma-joined `hunter`/`prospeo`, in the order tried;
    - `email_search_outcome`: an `EmailSearchOutcome`;
    - `email_search_at`.
  - **`crawled_pages.mailto`**: nullable JSON text, owner `research`, PII `redact`, as `text` is.
- **The crawler keeps `mailto:` addresses** (Decision 3).
  - `parse_html` gives `ParsedHtml.mailto`: the addresses of visible `a[href^=mailto:]` links, read after hidden content is stripped.
    - The address is the href after the scheme and before any `?`, percent-decoded, and split on commas.
    - Each is kept only when `identity.normalize_email` accepts it.
    - They are distinct, in document order, at most 20.
  - `FetchedPage` and `RenderedPage` gain `mailto: tuple[str, ...] = ()` (`repr=False`, checked). Both adapters pass it on.
  - `WebsiteCrawls` stores it on insert, update and render (`insert_page`, `update_page`, `set_page_text`), as a JSON list or NULL when empty.
  - `purge_text` nulls `mailto` with `text`.
  - Links, the text, `content_hash` and `changed_at` are unchanged.
- **`EmailFinding(clock, meter, research, queue, *, domain_search, hunter, prospeo)`** in new `nextron/app/research/finding.py`.
  - It is not a use case. It runs its own `uow.transaction()`s, and every port call happens outside them.
  - **`find_contact_email(lead_id, contact_id) -> EmailSearchOutcome | None`.** It returns `None` (and logs `email_search_skipped`) for:
    - an unknown or merged Lead;
    - a Contact unknown or not on the Lead;
    - a Contact that already has a usable address (an email whose class isn't `invalid`).
  - **The steps, in order.** It stops at the first assignment.
    1. **Gate first.** `compliance_api.assert_contactable(ContactSubject(contact_id), "email", "discovery")`.
       - On `Blocked`, it sets the outcome to `lead_closed` when every reason is `lead_lost` or `lead_disqualified`, else `suppressed`.
       - It appends one `events` row: entity `contacts`, `to_status` `email_search_blocked`, cause the first reason, actor `system:research`.
       - It stops, with no further step or call.
    2. **Published.** `published_match` over the Lead's pages.
    3. **Pattern.** It uses the stored domain pattern, or learns one (Decision 7).
    4. **Hunter Domain Search**, only when the Lead has a web domain, no pattern is stored, `domain_search_at` is NULL and `tokens.last` is set.
       - After a 2xx it sets `domain_search_at`.
       - It stores the mapped pattern (`hunter_domain_search`) if none is stored.
       - It then assigns either the `personal` person whose `name_tokens` equal the Contact's, or else the pattern's address.
       - Nothing else from the answer is stored.
    5. **Hunter Email Finder**, then **Prospeo**, each only when not yet in `email_search_tried` and `tokens.last` is set.
       - A 2xx adds the provider to `email_search_tried`.
       - A `claimed` answer stops the search with `not_found`.
       - Prospeo's `credential_not_set` skips Prospeo without recording it.
    6. **Otherwise** the outcome is `not_found`.
  - **Before each paid step**, a fresh transaction runs `allow(BUDGET_CLASS, meter.snapshot(now), setup_queries.budget_limits(), quota=...)`. A refusal sets `quota_reached`, makes no call, and returns `QUOTA_REACHED` (Decision 9).
  - **Assigning an address** happens in one transaction, which re-reads the Contact and the Lead and returns `None` if either changed as above.
    - **Candidate checks.** A candidate is dropped and the next one is tried when it is:
      - not accepted by `normalize_email`;
      - a role address;
      - already held by another Contact on the Lead;
      - in this Contact's `address` Provenance history.
    - **The gate.** `assert_contactable(NewContactSubject(lead_id, email=address, name=contact.name, linkedin_url=contact.linkedin_url), "email", "discovery")`.
      - `Blocked` is handled as in step 1 and stops the search.
    - **The writes:**
      - `contacts_repo.set_address(contact_id, email=, address_class=assign(source).to, at=)`;
      - an `address` Provenance record with the class (Decision 2);
      - one `events` row as `Research._address` writes (entity `contacts`, from `None`, cause `assign(source).cause`);
      - for `published`, Quote Evidence: `quote`, `website`, `source_url` the page, `pages_checked` `[url]`, `retrieved_at` the page's `fetched_at`, `extracted_by` `rule`, `person_contact_id`. A live row with the same quote, URL, Contact and actor is confirmed instead.
      - `research.resolve_market(contact_id)`;
      - the outcome `found`.
  - **After each commit**, the module logs `email_search_finished` (`lead_id`, `contact_id`, `outcome`, `step`, `calls`). No address, name or domain is logged.
  - **`@use_case queue_search(lead_id, contact_id) -> SearchQueued`** (for "Find email"). Its results are `queued`, `unknown_lead`, `merged_lead`, `not_on_lead` and `has_address`. When queued, it enqueues the job on the interactive lane.
- **The job `find_contact_email`.**
  - Default lane bulk; lanes bulk and interactive; priority 45; `max_runtime` 5 min; retry limit 3; group `enrichment`.
  - Payload `{v, lead_id, contact_id}`, scope the Contact, discriminator `tick`, concurrency key `domain:<web_domain>` when the Lead has one.
  - `handlers.find_contact_email(finding)` checks both ULIDs and returns `DEFERRED` for `QUOTA_REACHED`.
  - **Enqueued from:**
    - `PrimaryContacts.select_primary_contact`, in its transaction, when the chosen Primary has no usable address (default lane);
    - `PrimaryContacts.make_primary`, likewise, on the interactive lane;
    - `queue_search`.

    `PrimaryContacts` gains `queue`.
- **The Lead page.**
  - **The note.** A Contact without a usable address shows a note under the Email cell, by outcome:
    - `quota_reached`: "email not found yet: quota reached";
    - `not_found`: "no email address found";
    - `suppressed`: "not searched: on the Suppression List";
    - `lead_closed`: "not searched: the Lead is Lost or Disqualified".
  - **"Find email."** The same Contact gets a "Find email" form, modelled on "Make primary": `POST /leads/{lead_id}/find-email` (`contact_id`), with `@personal_data(True)`, `hx-post`, `hx-target="#lead-contacts"`, `hx-swap="outerHTML"` and `csrf_field()`.
  - **The POST** answers 200 with the panel and a notice. An unknown Lead answers 404. The notices:
    - "Looking for an email address. Reload the page in a minute.";
    - "This Lead was merged into another Lead.";
    - "That Contact isn't on this Lead.";
    - "That Contact already has an address."
- **AD-16 contract (`tests/contracts/test_ad16_callers.py`).**
  - The `find_contact_email` entry's qualname becomes `ANY + r"(?:\w*find\w*email\w*|EmailFinding\.\w+)"`, and its scenario is added:
    - with `Allowed`, the gate calls are `ContactSubject`, then `NewContactSubject(lead_id, email=<found>, name=<name>)`, before the first write;
    - with `Blocked`, the blocked rows are `{"events": 1}`, and the only blocked writes are `INSERT events` and `UPDATE contacts`.
  - **The update scan** closes 3.4's deferred item. Any reference to `contacts_repo.set_address`, or call of `fill` with an `email` keyword or a `**` splat, outside `db/repos/research/contacts.py` and outside a manifest def fails. Probes prove it.
  - The tests that list built callers or probe a missing scenario are updated.

**Never:**
- No real Hunter or Prospeo request in tests, and no new dependency or setting.
- No email verification, MX or DNS check, `verify_contact_email` job or Address Class change after assignment. That is Story 2.14.
- No `invalid` written. Nothing replaces a usable address.
- No `ProvenanceSource` or `EvidenceSource` change, and no `provenance_records` rebuild.
- No storage of Domain Search people, or of non-Contact names or addresses.
- No paid call for a Contact without `tokens.last`, or for a Lead without a web domain.
- No address, name, quote, domain or key in a log line, `events` row, error or job payload.

### Decisions made overnight (pending user review)

1. **Two Protocols in one port module.** `EmailFinderPort` (`find_email`) is implemented by Hunter and Prospeo, and `DomainSearchPort` by Hunter only. AD-9's single `EmailFinderPort` names its operations (find, verify, Domain Search), and Prospeo serves only find, so a Protocol method Prospeo can't honour would fail at run time. Story 2.14 adds verification to the same module.
2. **Provenance sources** keep the existing values. A published address gets `website` (detail: the page URL, retrieved at the page's `fetched_at`). A pattern address gets `pattern` (detail "`<pattern>` pattern learned from published addresses" or "`<pattern>` pattern from Hunter Domain Search"). Steps 3 and 4 get `finder`, with the API name as detail ("Hunter Domain Search", "Hunter Email Finder", "Prospeo"). This avoids a CHECK rebuild of `provenance_records`, and matches 4.11's `finder` key and 3.7's "an API name such as Hunter Domain Search".
3. **`mailto:` addresses** are stored per page, because the job runs after the crawl, and purged with the text after 30 days. A mailto-only address's Quote is the literal `mailto:<address>`, cited against the page's stored mailto list. Obfuscated addresses ("jane [at] firm") aren't read.
4. **A published match** never uses an initial alone, because a published address is sent without verification. A match is either:
   - **Rule A:** the local part's `.`, `_` and `-` parts include both `first` and `last` as whole parts, or the joined local part equals `first+last` or `last+first`;
   - **Rule B:** the Contact's stored name (normalised, whole words) and exactly one non-role address sit on the same text line, and both fit one quote of at most 300 characters.

   Matches are ordered by rule A first, then the Lead's mail domain, page order and position, and the first is taken. Its Quote is the line, or the 300-character window around the name and address.
5. **The mail domain** is the Lead's `web_domain`, because `leads.mail_domains` doesn't exist yet.
6. **Role addresses** use the extended list above. The AC's six are a minimum.
7. **Pattern learning** happens only when no pattern is stored.
   - **Examples** are the Lead's *other* named Contacts whose published match (rule A or B) is at the mail domain, isn't a role address and passes the gate with `NewContactSubject`.
   - **The pattern** fitting the most examples wins, if at least 2 examples fit it and it fits strictly more than any other. Each example gets its Quote Evidence (inserted or confirmed), whose ids are stored. The examples' Contacts aren't changed.
   - **A stored pattern** is never overwritten (Story 7.4 disables patterns).
   - **Only Contacts are examples.** Only decision-makers are stored (2.10), so non-Contacts' addresses never become Evidence.
8. **Paid steps run once.** A Domain Search runs once per domain ever. Hunter Email Finder and Prospeo run once per Contact ever (`email_search_tried`), whoever asks: "Find email" re-runs the free steps and any paid step not yet tried. Story 2.14's re-enqueue after an `invalid` therefore reaches the next untried source.
9. **A quota refusal** stops the search at that step and defers it. The job never skips ahead to Prospeo while Hunter's quota is reached, which keeps the AC's order. When Hunter's month is used up, finding waits for the next month.
10. **The gate.** A `Blocked` answer, up front or for a found address, ends the whole search for that Contact. Hunting for another address of someone who opted out would undo the opt-out. Hunter's `claimed_email` (451) is treated the same way, without Prospeo.
11. **Metering.** Each 2xx answer counts in full (Hunter 2 units, Prospeo 1), even when nothing is found, so the quota errs low. Hunter's `X-Credits-Used` header is ignored.
12. **Address uniqueness** per Lead is checked in code, with no unique index. Merges can bring two Contacts with one address together (2.10's deferred fold), and an index would make the merge fail.
13. **Triggers.** "Make primary" enqueues the search, since Mac picked that person to write to. "Find email" works on any Contact without a usable address. A merge's re-selection enqueues nothing.
14. **Prospeo** uses Enrich Person with `only_verified_email: true`, because its old Email Finder endpoint is no longer documented. Its key is optional, so an unset key skips Prospeo. Hunter's key is required, so `credential_not_set` fails the job and a `job_dead` alert follows. Neither adapter has been tried with a live key.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected |
|---|---|---|
| Published (text) | team page line "Jane Roe, Managing Partner – jane.roe@roe.law" | `jane.roe@roe.law` is `published`, with Quote Evidence, `website` Provenance and `found`; no port call |
| Published (mailto) | an "Email" link to `mailto:jane.roe@roe.law` | assigned `published`; the quote is `mailto:jane.roe@roe.law` |
| Initial only | the address `jroe@roe.law` on a different line from "Jane Roe" | not published |
| Pattern learned | 2 other Contacts' published `first.last` addresses | `email_domains` stores `first.last` with 2 Evidence ids; `jane.roe@roe.law` is `guessed`, source `pattern` |
| Role | only `info@roe.law` on the pages | never assigned or learned |
| Domain Search | no pattern; Hunter answers `{f}{last}` | `domain_search_at` and the pattern are stored; `jroe@roe.law` is `guessed`, `finder`/"Hunter Domain Search"; 2 units metered |
| Domain Search again | another Contact at the same domain | no second Domain Search; the stored pattern is used |
| Finders | Hunter answers none, Prospeo answers an address | `hunter,prospeo` tried; `guessed`, `finder`/"Prospeo" |
| Quota | Hunter units ≥ the limit | no call; `quota_reached`; `DEFERRED`; the page note |
| Suppressed | the found address is suppressed | nothing assigned; `suppressed`; no further call |
| Duplicate | the address belongs to another Contact on the Lead | skipped; the next source is tried |
| Has address | a CSV `guessed` address | skipped; no call |

</frozen-after-approval>

## Code Map

- **Patterns to copy:**
  - `nextron/app/research/people.py`: `PeopleExtractions.extract` for the budget check and transactions, `_select` for the enqueue, and `PrimaryContacts` (L632).
  - `nextron/app/research/extraction.py`: `page_order`.
  - `nextron/app/research/api.py`:
    - `_address` (L705) and `_record` (L686) for the Provenance and events writes;
    - `resolve_market` (L640);
    - `add_contact`'s gate (L573-583) and `_blocked`;
    - `WebsiteCrawls._stored` (~L1301) and the render path (~L1540), which store pages.
- **Domain:**
  - `domain/address_class.py`: `assign(source)` and `AddressCause`, with `ASSIGNED_FROM_PATTERN/FINDER/WEBSITE`.
  - `domain/contacts.py`: `ProvenanceSource.PATTERN/FINDER/WEBSITE` and `ProvenanceSubject.ADDRESS`.
  - `domain/identity.py`: `normalize_email`, `registrable_domain` and `normalize_name`.
  - `domain/quotes.py`: `normalise` and `quote_in`.
  - `domain/contactability.py`: `ContactSubject`, `NewContactSubject` and `BlockReason`.
  - `domain/budget.py`: `allow(..., quota=)` (L396), `QUOTAS` and `UNIT_WEIGHTS`.
  - `domain/job.py`: `DEFERRED`, `TICK` and `VERSION_FIELD`.
  - `domain/crawl.py`: `PAYLOAD_VERSION`, `LEAD_ID_FIELD` and `DOMAIN_KEY_PREFIX`.
- **Repositories** (keep `StoredContact`/`_COLUMNS` and `StoredPage` in step):
  - `db/repos/research/contacts.py`: add `set_address`, `set_email_search` and the three fields.
  - New `db/repos/research/email_domains.py`.
  - `db/repos/research/crawl.py`: the `mailto` field, `insert_page`, `update_page`, `set_page_text` and `purge_text`.
  - `db/repos/research/signals.py`: `insert_evidence` and `confirm_evidence`.
  - `db/repos/research/provenance.py`: `insert` and `for_contact`.
  - `app/queries/research.py`: `crawled_pages` and `contacts`.
  - `app/queries/setup.py`: `budget_limits`.
- **Models and registries:**
  - `db/models/research.py` (`ContactRecord`, plus a new `EmailDomainRecord`), `db/models/crawl.py` and `db/models/__init__.py`;
  - `owners.py` and `pii_registry.py`;
  - the 0023 and 0029 `add_column` migrations as patterns.
  - `schema_differences` compares CHECKs and indexes against the migration.
- **Crawler:** `adapters/crawler/parsing.py` (`parse_html`, `_strip_hidden`, `ParsedHtml`), `adapters/crawler/__init__.py:367`, `adapters/renderer/__init__.py:488`, `ports/crawler.py` (`FetchedPage`) and `ports/renderer.py` (`RenderedPage`).
- **Adapters:**
  - `adapters/telegram/__init__.py` is the template (lazy client under a lock, `Metered`, the error mapping).
  - `ports/secrets.py` provides `read_credential`; the keys are `CredentialKey.HUNTER` and `PROSPEO`.
  - `ports/usage_meter.py` provides `Metered`.
  - Also update `adapters/__init__.py`'s docstring list.
- **Tests and contracts:**
  - `tests/live_guard.py`: `LIVE_CONSTRUCTORS` and `ADAPTER_PACKAGES` (`Guarded(("default_http_client",))`).
  - `tests/fakes/__init__.py`: `PORT_FAKES`, with a new `tests/fakes/email_finder.py` holding `ScriptedFinder` and `ScriptedDomainSearch`. They record calls, build with no arguments and return none by default.
  - `pyproject.toml` contract c5: the source modules plus the comment.
  - `tests/contracts/test_adapter_boundary.py`: `DRIVERS` and the `PLANTED` keys.
  - `tests/contracts/test_source_tree.py`: `FILES`.
- **Jobs:**
  - `jobs/handlers.py`: copy `extract_lead` (L256) and its docstring bullet.
  - `main.py`: `build_job_types(..., finding=)` (L1038) and every caller; grep `build_job_types(` (about 32 sites, including `tests/dashboard.py`, `test_job_contracts.registry()` and `queue_on` in `test_ad16_callers`/`test_leads`).
  - `main.build_app` (~L908-938): build `HunterFinder(secrets, meter=meter)` and `ProspeoFinder`, injectable like `crawler=`/`llm=`, then `EmailFinding`, and pass `queue` to `PrimaryContacts`.
- **Job-name lists:** `test_job_contracts` (names, shapes, handler tests), `tests/unit/test_runner_lifespan.py` (L213), `tests/unit/test_health_page.py` (L280) and `tests/app/test_engine_health.py` (L326).
- **Head bumps to `0030_2_13_email_domains`:**
  - `test_persistence_contracts` (`HEAD`, probes `0030_*` → `0031_*`);
  - `tests/unit/test_migrations.py` (`HEAD`; pin the 0029 test at L517-549);
  - `tests/app/test_serve_process.py:324`;
  - `tests/unit/test_serve.py` (`0030_more` → `0031_more`).
- **AD-16:** `test_ad16_callers.py` L151 (the manifest), `World`/`world_at` (L777), `Scenario` (L868), the add_contact scenario (L1041) as the model, and `test_the_built_callers_are_the_epic_2_paths` (L1111) and `test_a_built_caller_without_a_scenario_fails` (L1152), which should probe `verify_contact_email` instead. Also update the docstring (L13) and README L1384.
- **Web:**
  - `web/app.py`: `lead_make_primary` (L1331) is the POST model; `create_app(..., email_finding=)`.
  - `web/leads_page.py`: `ContactRow`, `contact_row` and `contacts_context`.
  - `web/templates/lead_contacts.html`.
  - `test_web_contracts` route lists.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/email_finding.py`, with `tests/unit/test_email_finding_domain.py` -- the pure rules: name tokens, the seven patterns, the Hunter map, role addresses, `addresses_in`, rules A and B with quotes, `learn_pattern`'s ties, and Provenance details.
- [x] `nextron/ports/email_finder.py`, `adapters/hunter/__init__.py` and `adapters/prospeo/__init__.py`, with `tests/unit/test_hunter_adapter.py` and `test_prospeo_adapter.py` (an `httpx2` mock transport) -- every status mapping, the header-only key, metering units and counts, a bad shape, and no secret in errors.
- [x] `adapters/crawler/parsing.py`, `ports/crawler.py`, `ports/renderer.py`, both adapters, the crawl repo and `WebsiteCrawls` -- `mailto`, with parser tests (hidden, `?subject`, multiple recipients, invalid, the cap) and a store-and-purge test.
- [x] `nextron/db/alembic/versions/0030_2_13_email_domains.py`, models, `owners.py`, `pii_registry.py`, repositories and queries -- the schema above.
- [x] `nextron/app/research/finding.py`, `app/research/people.py` (`PrimaryContacts.queue`) -- `EmailFinding` and the enqueues.
- [x] `nextron/jobs/handlers.py`, `nextron/main.py`, `tests/live_guard.py`, `tests/fakes/`, `pyproject.toml` c5, `test_adapter_boundary`, `test_source_tree` -- the job type, the wiring and the guards.
- [x] `nextron/web/app.py`, `web/leads_page.py`, `templates/lead_contacts.html` -- the note, "Find email" and the POST, with tests in `tests/unit/test_leads_page.py` (escaped, CSRF, the notices).
- [x] `tests/app/test_email_finding.py` -- every matrix row on pages stored by a real `WebsiteCrawls` run with `ScriptedCrawler`, covering:
  - the order of the steps;
  - once per domain, and once per Contact;
  - uniqueness and the Provenance history;
  - the gate in both places;
  - the events, Evidence and Provenance rows;
  - `resolve_market`;
  - the select and Make primary enqueues;
  - `queue_search`;
  - `DEFERRED` through the handler.
- [x] `tests/contracts/test_ad16_callers.py` -- the scenario, the regex, the update scan and its probes, plus the contract and head updates.
- [x] `nextron-engine/README.md` and `deferred-work.md`:
  - README: a "Finding email addresses" section after "Decision-makers and the Primary Contact", plus the migrations row, the jobs list and the adapters;
  - `deferred-work.md`: obfuscated addresses; a Prospeo fallback while Hunter's quota is reached; re-selection after a merge enqueueing the search.

**Acceptance Criteria:**
- Given the full suite, when `pytest` and `lint-imports` run, then everything passes (`ai_eval` skipped) and every contract is kept.
- Given `grep` over `nextron/`, when it looks for `contacts.email` writes, then only `db/repos/research/contacts.py` holds them, and only `Research.add_contact` and `EmailFinding` call them.

## Implementation Notes

Built as specified, except where noted. Where the code had to choose:

- **`EmailFinding.find_contact_email` is a `@use_case`.** The spec says "not a use case", but the
  unit of work lets only a running use case open `uow.transaction()`, and the runner calls the
  handler with no use case around it, so every job would have raised `UnitOfWorkError`. It
  still opens one transaction per phase and makes every port call with none open.
- **Domain (`nextron/domain/email_finding.py`).** The spec's API plus `CONTACT_ID_FIELD`
  (`contact_id`, the job payload's), `PatternSource`, `PublishedRule` (`name_in_address` = rule
  A, `same_line` = rule B), `published_matches` (every match, ordered; `published_match` is its
  first not in `exclude`), `address_for`, `hunter_pattern`, `FINDER_PROVIDERS`, `tried_of` /
  `tried_text`. Names fold casefold, NFKD, marks dropped; apostrophes and hyphens are removed
  only between two letters; honorifics and suffixes are dropped anywhere; a one-letter first or
  last token is kept. Rule B counts distinct non-role addresses on the line. Quotes are raw
  substrings of the stored text (checked with `quote_in`), at most 300 characters, windowed on
  long lines; a mailto-only address's quote is `mailto:<address>`.
- **Port and adapters.** `nextron/ports/email_finder.py` as specified (blank strings refused).
  `HunterFinder` maps 403, 429, 5xx and transport errors to retryable `hunter_unavailable`
  (both endpoints), 401 and other 4xx to `hunter_refused`, a bad 2xx to `hunter_bad_answer`;
  Email Finder's 404 is none and 451 `claimed`. `ProspeoFinder` checks `INSUFFICIENT_CREDITS`
  (retryable) and `INVALID_API_KEY` (refused) at any status, `NO_MATCH` only on a 400. A
  `Metered` record counts 1 only for a 2xx. Fakes `ScriptedFinder` / `ScriptedDomainSearch`
  answer none once their script is empty. `tests/contracts/test_test_isolation.py` and
  `nextron/ports/__init__.py` list the new port and packages.
- **Crawler.** `parse_html` reads `a[href]` `mailto:` links after hidden content is stripped,
  as specified; escapes that aren't UTF-8 give no address. `StoredPage.mailto` is a tuple (`()`
  for NULL); the repo stores compact JSON or NULL; `purge_text` nulls `mailto` with `text`.
- **Schema.** `ck_email_domains_pattern_evidence` is a biconditional (evidence ids set exactly
  for `published`). `email_domains_repo.set_pattern` writes only where no pattern is stored.
  `contacts_repo.set_email_search(tried, outcome, at)` with `outcome=None` records only
  `email_search_tried` (a finder answered mid-search).
- **The search.** A finder answer that isn't `claimed` (a 2xx, or Hunter's 404) adds the
  provider to `email_search_tried` at once, in its own transaction. A `claimed` answer ends the
  search `not_found` without recording Hunter, so a re-run asks Hunter again (free) rather than
  Prospeo. Domain Search candidates are the `personal` people whose `name_tokens` equal the
  Contact's, then the mapped pattern's address; in that run the Provenance is
  `finder`/"Hunter Domain Search", and later runs using the stored pattern get `pattern`/"`<p>`
  pattern from Hunter Domain Search". Learning stores Evidence for the examples that fit the
  learned pattern. `email_search_finished` is logged once per run, after the transaction that
  stores the outcome (`step`: the step that ended it, `calls`: paid calls made). Every gate
  answer other than `Allowed`/`Blocked` raises `TypeError`.
- **Wiring.** `build_job_types(..., finding=None)`: left out, the handler raises
  `RuntimeError` when a job runs, so the ~30 test callers that never run one are unchanged.
  `create_app(..., email_finding=None)`: without one, "Find email" answers 503.
  `build_app(..., domain_search=, hunter=, prospeo=)` are injectable; one `HunterFinder` serves
  both Hunter ports. `PrimaryContacts(clock, research, queue)` enqueues through
  `finding.enqueue_search` (bulk after a selection, interactive after "Make primary").
- **`Research._resolve`** now calls `contacts_repo.fill` with named keywords (no splat), so the
  AD-16 update scan holds.
- **The Lead page.** The note is a `<p class="email-search-note">` inside the Email cell (which
  keeps `hx-disable`); "Find email" shows on every Contact without a usable address (an
  `invalid` one included). The POST reads exactly `contact_id`.
- **AD-16 contract.** The scenario stores a crawled page through the crawl repo. The update
  scan also flags a reference to `fill` that isn't a call (an alias would hide its keywords).

**Implementation verification** (targeted): every touched test file, `tests/contracts` and
neighbours (`test_job_runner`, `test_lead_import`, `test_leads`, `test_signal_extraction`):
2,816 passed, 9 skipped (`ai_eval` and the unbuilt AD-16 callers); `lint-imports` 29 kept, 0 broken. No real Hunter or Prospeo request was made.

**Review fixes (pass 1).**
- `_domain_search_step` and `_finder_step` record the paid call first in their apply
  transaction (the domain row, `domain_search_at` and Hunter's pattern; the provider in
  `email_search_tried`, which needs only the Contact to exist), then re-read the Lead and
  Contact through `_skipped`, which returns the `_Skip` instead of raising it, so the writes
  commit and the search ends skipped after the transaction. No credit is paid twice.
- The Domain Search's pattern candidate uses the stored pattern when one is set, else Hunter's
  (`_domain_candidates`).
- `_learn` keeps one example per address (the first Contact matching it).
- `_free_steps` reads `PatternSource(stored.pattern_source)` with no fallback.
- `_finder_step` restores the previous `search.step` when Prospeo's key isn't set.
- `PrimaryContacts.make_primary` drops the dead unknown-Lead check.
- `crawled_pages.mailto` is PII class `null` (an erasure's "[erased]" isn't JSON).
- The domain docstring names `nextron.app.research.finding`; `main.py`'s import is in order.
- Tests: the claimed answer leaves `email_search_tried` NULL and a re-run asks Hunter again,
  never Prospeo; two Contacts on one address are one example; a paid answer is kept when the
  Contact gets an address during the call; `build_app` gives its `EmailFinding` (on injected
  finders) to the `find_contact_email` handler and to `create_app`
  (`tests/unit/test_runner_lifespan.py`).

**Final verification** (on the final tree, after the review fixes): the full suite through
`suite_lock.py` (track-D) passed on its first run: 13,894 passed and 11 skipped. `lint-imports`:
29 kept, 0 broken. No real Hunter, Prospeo or Claude request was made.

## Spec Change Log

## Review Triage Log

**Pass 1 (2026-11-18).**
- **Sources:** BH = blind hunter (17 findings), EC = edge-case hunter (8), VG = verification gap (2 gaps and 1 other finding).
- **Counts:** 28 raw findings, which make 23 entries after deduplication.
- **Outcomes:** 11 entries patched (A, F2, G, H, I, K, L, M, S, U, V); 0 deferred; 12 rejected (B, C, D, E, F1, J, N, O, P, Q, R, T).
- **No loopback.** No finding needs the intent or the spec changed. G's root (optional `finding=`/`email_finding=`) is the implementer's logged departure, and a `build_app` test closes its harm.
- **Rejected only because the fix would change the approved spec (listed for Mac):**
  - B: a provider's own "out of credits" answer (Hunter 429, Prospeo `INSUFFICIENT_CREDITS`) is a retryable error, per AD-8, not `quota_reached`;
  - C: a missing or revoked Hunter key fails the job (Decision 14);
  - E: the assignment's `events` row has `from_status` `None` even when an Invalid address is replaced;
  - J: hyphens and apostrophes are folded out of surnames, so patterns and finder queries use `smithjones`;
  - N: "Find email" also shows after a `suppressed` or `lead_closed` outcome;
  - R: a quota of 0 (a provider switched off) defers the search hourly, forever (Decision 9). This goes to `deferred-work.md`.

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| A | BH1, EC2, EC3 | After a paid call, the apply transaction starts with `_read`, whose `_Skip` rolls back `domain_search_at`, the Hunter pattern and `email_search_tried`. The credit is spent and unrecorded, so it is paid again. | medium | Verified in `_domain_search_step` and `_finder_step`: `self._read(search)` comes first in the transaction. | patch (record the call, then re-check without rolling back) |
| B | BH2 | A provider's own out-of-credits answer is retryable and dies after 3 tries, instead of `quota_reached`. | low | True. The spec and AD-8 make 429 and `INSUFFICIENT_CREDITS` retryable, and a dead job alerts Mac. | reject (spec-mandated) |
| C | BH3 | A missing or revoked Hunter key fails every search, with no outcome on the Contact. | low | True by Decision 14; `job_dead` alerts. | reject (spec-mandated) |
| D | BH4 | Finder answers aren't checked against the Lead's mail domain; Hunter's score is dropped. | low | Hunter and Prospeo answer for the queried domain. A published address may be at any domain (Decision 4). Every finder address is `guessed`, so it is verified (2.14) before any send. | reject |
| E | BH5 | `from_status` is `None` when an Invalid address is replaced. | low | True; the spec says "from `None`" (a new address starts its own lifecycle). | reject (spec-mandated) |
| F1 | BH6 | A Hunter claim isn't stored, so each re-run asks Hunter again. | low | True, but by design (Implementation Notes 3): a 451 isn't billed (count 0), and Prospeo is never asked. A durable marker is new state. | reject |
| F2 | VG gap 2 | No test shows that a claim leaves `email_search_tried` empty and that a re-run never reaches Prospeo. | medium | Pre-verified gap. | patch (test) |
| G | BH7, VG gap 1 | `finding=` and `email_finding=` are optional, so dropping them from `build_app` fails no test, and every job would die in production. | medium | Pre-verified gap. The implementer made them optional to spare about 30 callers. | patch (a `build_app` test that reaches the scripted finder through the job and the POST) |
| H | BH8 | `domain/email_finding.py`'s docstring names `nextron.app.research.email_finding`. | low | Verified. | patch |
| I | BH9 | `main.py` imports `nextron.domain.email_finding` out of order. | low | Verified. | patch |
| J | BH10 | Hyphenated surnames are folded (`smithjones`) for patterns and finder queries. | low | True; the spec's `name_tokens` rule. | reject (spec-mandated) |
| K | BH11a, EC7 | `PatternSource(stored.pattern_source or "")` has an unreachable fallback. | low | The CHECK forbids a pattern without a source; it is a direct correction. | patch |
| L | BH11b | `PrimaryContacts.make_primary`'s `if lead is not None` is unreachable. | low | `Research.make_primary` raises for an unknown Lead first. Direct deletion. | patch |
| M | BH11c | An unset Prospeo key leaves `step=prospeo` in the log. | low | Verified; a direct correction. | patch |
| N | BH12a | "Find email" shows after `suppressed` or `lead_closed`. | low | True; the spec shows it for every Contact without a usable address. | reject (spec-mandated) |
| O | BH12b | Nothing shows that a search is queued. | low | New surface. | reject |
| P | BH12c | `contact_row` repeats `has_usable_address` inline. | low | It is one line, and no caller diverges. | reject |
| Q | BH12d | `SEARCH_NOTICES` is keyed by strings. | low | `SearchQueued` is a `StrEnum`, so the keys are equal; a new value fails in tests. | reject |
| R | EC1 | A quota of 0 defers the search hourly, forever. | medium | True by Decision 9 and 1.13 (a quota of 0 is a hard stop). | reject (spec-mandated) + `deferred-work.md` |
| S | EC4 | The Domain Search step builds the candidate from Hunter's pattern even when a stored pattern exists. | low | Reachable only through a race that the domain concurrency key prevents; a direct correction. | patch |
| T | EC5 | An empty `Blocked.reasons` raises `IndexError`. | false | `Blocked.__post_init__` refuses empty reasons (3.4). | reject |
| U | EC6 | Two Contacts matching one address count as two examples, so a pattern is learned from one address. | medium | Verified: `_learn` doesn't dedupe by address; this breaks Decision 7's "at least 2". | patch |
| V | EC8, VG other | `crawled_pages.mailto` is `redact`, so erasure would write `"[erased]"`, which `json.loads` can't read. Every page read would then crash. | medium | Verified: the registry's REDACT value with `crawl_repo._page`'s decode. | patch (classify `null`) |

## Verification

**Commands:**
- `cd e:/Nextron-track4/nextron-engine && uv run pytest -q <touched test files> tests/contracts` -- expected: all pass.
- `cd e:/Nextron-track4/nextron-engine && uv run lint-imports` -- expected: all contracts kept.
