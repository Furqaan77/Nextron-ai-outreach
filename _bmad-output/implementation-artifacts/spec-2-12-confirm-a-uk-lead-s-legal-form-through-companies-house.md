---
title: "Story 2.12: Confirm a UK Lead's legal form through Companies House"
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: '98d0250856afbbbec85eacec7f1897ba97314ab5'
review_loop_iteration: 0
story_key: '2-12-confirm-a-uk-lead-s-legal-form-through-companies-house'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-3-6-evaluate-eligibility-live-and-show-the-reasons.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-10-find-named-decision-makers-and-pick-the-primary-contact.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** UK cold email may go only to corporate subscribers (PECR, NFR-3). Story 3.6's Eligibility therefore fails every UK Contact with `uk_legal_form_unconfirmed`, because nothing records a Lead's legal form. There is no Companies House adapter, no `check_legal_form` stage and no legal-form fact on a Lead.

Requirements: FR-12, FR-9 · AD-3, AD-7, AD-9, INT-5 · NFR-3.

**Approach:**
- **Adapter.** A `RegistryPort` and its `adapters/companies_house` adapter. It searches companies by name and looks up a company by number. It authenticates with the keyring key, read on every call, limits itself to 600 requests per 5 minutes, and meters every call as provider `companies_house`.
- **The stage.** A new `check_legal_form` job (owner `discovery`) runs after the people step of `extract_lead` and before `select_primary_contact`. For a GB Lead it links the Lead to a Companies House record in one of two ways:
  - by a company number quoted on its stored pages;
  - by an exact registered-name match plus a registered-office postcode quoted on the site.
- **Recording.** `discovery` sets `ch_number`, merging per FR-9. `research.record_legal_form` stores `legal_form`, `company_status` and `legal_form_status`, with Quote Evidence from the site and from the registry record.
- **Eligibility** passes the result to `EligibilityLead`. The Lead page shows it.
- **Migration** `0030_2_12_legal_form`.

The acceptance criteria are Story 2.12 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**
- **Fail closed.** `legal_form_status` is `confirmed` only when all of these hold:
  - Evidence links the Lead to exactly one record;
  - that record's `company_status` is `active`;
  - its type is one of the 8 corporate-subscriber codes.

  Anything else, any doubt included, is `not_corporate` (linked, active, other or unknown type) or `unconfirmed`. A Lead that is never checked has status NULL, which Eligibility treats as unconfirmed.
- **One source for the list.** `nextron/domain/legal_form.py` owns `CORPORATE_SUBSCRIBER_FORMS` (3.1's 8 codes, moved there from `markets._GB_CORPORATE_FORMS`), `LEGAL_FORM_PATTERN` and `LEGAL_FORM_LABELS` (moved from `eligibility`).
  - `markets` and `eligibility` import them. `markets._GB_CORPORATE_FORMS` stays as an alias of the same object.
  - It is pure: the standard library and `nextron.domain` only.
- **No I/O in a transaction.** The registry calls run outside any transaction: prepare, then the calls, then apply, as in 2.9. The apply transaction does all of the following, or none of them:
  - sets `ch_number`, with its merge;
  - records the legal form;
  - enqueues `select_primary_contact`.
- **Writers.** Only `research` writes Evidence and the new profile columns. Only `discovery` writes `ch_number`, through a new `Discovery.set_ch_number` `@command` that mirrors `_canonicalize`'s merge.
- **Nothing sensitive in logs.** No log line, `events` row, error or job payload holds the key, a name, a quote, a URL or a postcode: IDs, codes and counts only.
- **No real calls in tests.** No test reaches Companies House. The adapter is classified in `tests/live_guard.py`, is in contract c5, and has a fake in `PORT_FAKES`.

**Never:**
- Confirm without a linked record.
- Treat a CSV or pre-existing `ch_number` as Evidence.
- Pick between ambiguous records.
- Overwrite a different `ch_number` that a Lead already holds.
- Add CHECK constraints or a new Evidence `fact` kind; either one would rebuild `leads` or `evidence`.
- Build the SIC/keyword discovery search (S2), the officers, the accounts, or the FR-61 re-checks.
- Build Story 2.11's `check_job_boards`.

### Decisions made overnight (pending user review)

1. **Chain position.** Story 2.11 isn't in this tree.
   - `PeopleExtractions._select` becomes `_enqueue_next_stage`: the single, documented insertion point. It enqueues `check_legal_form` with `select_primary_contact`'s old key shape: scope the Lead, `tick`, `{v, lead_id}`, and the concurrency key `domain:<web_domain>`.
   - `check_legal_form` enqueues `select_primary_contact`.
   - **At the 2.11 merge,** `_enqueue_next_stage` enqueues `check_job_boards`, and `check_job_boards`'s finish enqueues `check_legal_form`.
2. **Number phrases.** The search runs on `quotes.normalise`d text and ignores case. It looks for:
   - `company no` / `company no.` / `company number` / `company registration number` / `company reg no`;
   - `registered no` / `registered number`;
   - `registered in england` / `wales` / `scotland` / `northern ireland`.

   **The number.** It is the first token within 40 characters after the phrase that matches `[A-Za-z]{2}\d{6}` or `\d{6,8}`, with no letter or digit on either side.
   - The gap before it may hold no digit and, apart from a `no.`, none of `.`, `!` or `?`.
   - The number is normalised by `identity.normalize_company_number`. `00000000` is dropped.

   **The quote** runs from the phrase's start to the number's end.

   Bare "registration number" is excluded: it is used for FCA, VAT and SRA numbers.
3. **The number path needs a name check.** A looked-up record links the Lead only when its normalised registered name either:
   - equals the Lead's normalised name; or
   - occurs, as a whole-token run, in the citing page's text normalised the same way.

   This keeps an unrelated number on the page from confirming the Lead.
4. **Ambiguity means no link.** This covers two or more distinct records passing the number path, and two or more distinct name-and-postcode candidates. Up to 3 distinct site numbers are looked up, in page order (pages by URL, then text order). If the number path gives no single link, the name path runs.
5. **The registry quote** is a canonical rendering of the record, cut to 300 characters at the name's end: `Companies House record <number>: company type <type|unknown>, company status <status|unknown>, registered office postcode <postcode|none>, registered name <name>`.
   - Its `source_url` is `https://find-and-update.company-information.service.gov.uk/company/<number>`.
   - There is no stored registry text to `quote_in` against.
   - The site quote is re-checked with `quote_in` against the page's stored text.
6. **`ch_number`** is set for any linked record, active or not.
   - When the Lead's survivor already holds a different number, nothing about the legal form is recorded and the status becomes `unconfirmed`. A log line `legal_form_conflict` records it.
7. **Adapter errors.**
   - **A retryable `AdapterError`** is raised again, so the runner retries it, as in 2.10.
   - **A non-retryable one** (`credential_not_set`, `registry_unauthorized`, `registry_refused`, `registry_bad_response`) keeps the prior legal-form state. It logs the code, and still enqueues `select_primary_contact`, so research isn't blocked.
8. **Self rate limit.** A sliding window holds the last 600 request times (an injected `monotonic`).
   - A request that would be the 601st inside 300 s raises `AdapterError(True, "registry_rate_limited")`, with no request and nothing metered.
   - It never sleeps. HTTP 429 gives the same code.
9. **Eligibility mapping** (`legal_form.eligibility_facts(legal_form, status)`):
   - `confirmed` with a corporate code gives `(code, True)`;
   - `not_corporate` with a non-corporate code gives `(code, True)`, so the reason is `uk_legal_form_ineligible`, not a false "no Evidence links";
   - anything else gives `(code if it matches the pattern else None, False)`.
10. **Re-runs.** A new run's finding replaces the old one.
    - A site or registry quote equal to the live row the pointer names (type, quote, source, `source_url`) is confirmed through `last_confirmed_at`.
    - A changed quote inserts a new row and supersedes the old one.
    - A slot the new finding lacks retracts the old row. So a Lead that loses its link becomes `unconfirmed`.
11. **Merges.** A loser's legal-form columns stay on the loser, as `hq_*` do. The survivor's columns change only on its own check. Its Evidence moves through `reparent_lead`.
12. **Budget.** `allow(BudgetClass.BLOCKABLE, meter.snapshot(now), budget_limits())` comes first. A refusal returns `DEFERRED`.
13. **404.** A company lookup that returns 404 gives `None`. It is metered `ok` and is not an error.
14. **Columns.** Profile columns on `leads` (owner `research`, `PiiClass.NONE`, all nullable, no CHECK, as in 0028):
    - `legal_form` (`String(64)`);
    - `company_status` (`String(32)`);
    - `legal_form_status` (`String(16)`);
    - `legal_form_site_evidence_id` and `legal_form_registry_evidence_id` (`String(26)`, no FK, like `hq_evidence_id`).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected |
|---|---|---|
| Number | GB Lead "Acme Accounting"; footer "Acme Accounting Services Limited is registered in England and Wales, company number 01234567"; record `ltd`, `active` | `ch_number` 01234567; `ltd`, `active`, `confirmed`; one website Evidence (the footer phrase) and one registry Evidence; `select_primary_contact` queued |
| Short number | "Company No. 1234567" | looks up `01234567` |
| Unrelated number | "Registered number 07654321" and the record "OTHER CO LTD" isn't the Lead's name or on that page | not linked; the name path runs |
| Name path | no number; Lead "Smith & Jones LLP"; one search hit "SMITH AND JONES LLP" with postcode EC1A 1BB; the site says "London EC1A 1BB"; profile `llp`, `active` | `confirmed`; site Evidence is the postcode quote |
| No postcode | the site has no postcode | no search call; `unconfirmed`; no Evidence |
| Ambiguous | two name hits share the name and a site postcode | `unconfirmed`; no `ch_number` |
| Not active | linked record `dissolved` | `ch_number` set; `ltd`, `dissolved`, `unconfirmed`; Evidence stored |
| Not corporate | linked active `limited-partnership` | `not_corporate`; Eligibility gives `uk_legal_form_ineligible` only |
| FR-9 | an older Lead already holds 01234567 | this Lead merges into it; the legal form is recorded on the survivor; select is queued for the survivor |
| Conflict | the Lead holds 09999999 and the site says 01234567 | `unconfirmed`; `ch_number` unchanged; `legal_form_conflict` logged |
| Non-GB | US Lead | no call; select queued |
| Errors | 503 / 429 / the window full | the job raises a retryable error; nothing recorded; nothing queued |
| Errors | key not set / 401 | prior state kept; select queued |
| Budget | `allow` refuses | `DEFERRED`; no call |
| Re-run | same quotes | the same Evidence rows; `last_confirmed_at` updated |
| Re-run | the number is gone and the name path fails | `unconfirmed`; both pointed rows retracted; pointers NULL |
| Eligibility | a UK Contact with a published address on a `confirmed` `ltd` Lead | no `uk_legal_form_*` reason |

</frozen-after-approval>

## Code Map

All paths are under `nextron-engine/`.

- **Domain (new)** `nextron/domain/legal_form.py`:
  - `CHECK_LEGAL_FORM_JOB = "check_legal_form"`;
  - `CORPORATE_SUBSCRIBER_FORMS`, `LEGAL_FORM_PATTERN`, `LEGAL_FORM_LABELS`;
  - `COMPANY_STATUS_PATTERN = [a-z][a-z0-9-]{0,31}`;
  - `LegalFormStatus` (`confirmed`, `not_corporate`, `unconfirmed`);
  - `is_corporate_subscriber(code)` and `status_for(company_type, company_status)` (for a linked record);
  - `normalise_company_name(name)`: NFKC and casefold; drop `.` and apostrophes; `&` becomes `and`; any other non-alphanumeric becomes a space; collapse whitespace; drop trailing `ltd`/`limited`/`llp`/`plc` tokens repeatedly. An empty result never matches;
  - `name_occurs(name_key, text)`;
  - `find_company_numbers(text) -> tuple[NumberQuote(number, quote), ...]` (Decision 2);
  - `find_postcodes(text) -> tuple[PostcodeQuote(key, quote), ...]`. The regex is `(?<![A-Za-z0-9])[A-Za-z]{1,2}\d[A-Za-z\d]?\s?\d[A-Za-z]{2}(?![A-Za-z0-9])`. The key is uppercase with no space. The quote is up to 80 characters before the postcode, starting after a space, through its end;
  - `postcode_key(text)`;
  - `registry_quote(...)` and `company_page_url(number)` (Decision 5);
  - `SiteQuote(page_url, quote, fetched_at)`;
  - `LegalFormFinding(status, number, legal_form, company_status, site, registry_quote, retrieved_at)`. Its `__post_init__` enforces: no number means no site, no registry quote and `unconfirmed`;
  - `eligibility_facts` (Decision 9).
- **Existing domain.**
  - `domain/markets.py:108,128` (the pattern and `_GB_CORPORATE_FORMS`) and `domain/eligibility.py:93,192-207` (imports and `LEGAL_FORM_LABELS`) import from `legal_form`. `tests/unit/test_eligibility.py:869` keeps passing.
  - `domain/identity.py:414` `normalize_company_number`.
  - `domain/quotes.py` `normalise` and `quote_in`.
  - `domain/extraction.py:135,141` `EXTRACTED_BY_RULE` and `EvidenceSource.WEBSITE`/`REGISTRY`.
  - `domain/budget.py:396` `allow`.
- **Port (new)** `nextron/ports/registry.py`, following `ports/alert.py`/`ports/crawler.py` (a `@runtime_checkable` Protocol and frozen slotted values):
  - `RegistryCompany(number, name, company_type, company_status, office_postcode)`. The number is `[A-Z0-9]{8}`; the name is 1–200 characters; the type and status are `None` or match their patterns; the postcode is `None` or up to 16 characters;
  - `RegistryPort.search_companies(name) -> tuple[RegistryCompany, ...]` and `company(number) -> RegistryCompany | None`;
  - add a paragraph to the `ports/__init__.py` docstring.
- **Adapter (new)** `nextron/adapters/companies_house/__init__.py`. Copy `adapters/telegram/__init__.py`: `default_http_client` (:92), the lazy `_client` under a lock, `read_credential(self._secrets, CredentialKey.COMPANIES_HOUSE)` on every call before reserving a slot, and `with Metered(meter, "companies_house", op)` around the request only. Errors are raised `from None`.
  - **Constructor:** `CompaniesHouseRegistry(secrets, *, meter, http=None, monotonic=time.monotonic)`.
  - **Calls:** `GET https://api.company-information.service.gov.uk/search/companies?q=<name>&items_per_page=20` (operation `search_companies`) and `/company/<number>` (operation `get_company`).
  - **Headers:** `Authorization: Basic base64("<key>:")` and `Accept: application/json`.
  - **Responses:**
    - search items use `company_number`, `title`, `company_type`, `company_status` and `address.postal_code`. Invalid items are skipped;
    - a profile uses `company_number`, `company_name`, `type`, `company_status` and `registered_office_address.postal_code`;
    - an unknown or invalid type or status becomes `None`.
  - **Error codes:**
    - 429 and the window full: `registry_rate_limited`, retryable;
    - 5xx and transport errors: `registry_unavailable`, retryable;
    - 401 and 403: `registry_unauthorized`, not retryable;
    - any other non-2xx, except a profile 404: `registry_refused`, not retryable;
    - a body that isn't a JSON object, has the wrong shape or exceeds 1 MB: `registry_bad_response`, not retryable.
- **Data.**
  - `db/models/leads.py:85-88` gets the 5 columns.
  - New migration `db/alembic/versions/0030_2_12_legal_form.py` uses the `0028` `batch_alter_table("leads")` `add_column` pattern; `downgrade` raises.
  - Register the columns in `db/owners.py:110-125` (`leads.research`) and `db/pii_registry.py:106-122`.
  - `db/repos/research/profile.py` gets `legal_form(lead_id) -> StoredLegalForm | None` and `set_legal_form(...)`.
  - `app/queries/research.py` gets `legal_form(lead_id)`, mirroring `no_decision_maker` (:238).
  - Evidence is written through `db/repos/research/signals.py:188-258` (`insert_evidence`, `confirm_evidence`, `supersede_evidence`, `retract_evidence` and `evidence`). Rows use type `quote`, `extracted_by` `rule`, `fact` NULL and `person_contact_id` NULL.
- **Research** `app/research/api.py` `Research` (:543) gets `@command record_legal_form(lead_id, finding) -> None`:
  - it re-checks the site quote with `quote_in` against `crawl_repo.pages(lead_id)` text at `site.page_url`, and raises `ValueError` when the check fails;
  - it reconciles the two pointer slots (Decision 10), ignoring a pointer whose row isn't this Lead's;
  - it sets the columns. On `reparent_lead` (:728), the columns stay put.
- **Chain** `app/research/people.py:81-86,341-410`: rename `_select` to `_enqueue_next_stage` (Decision 1), and update the docstring.
- **Discovery** `app/discovery/api.py`:
  - add `@command set_ch_number(lead_id, number) -> str | None`. It returns the survivor id, or `None` on a conflict. It is modelled on `_canonicalize` (:302-324), with `by_ch_number` and `set_keys(id, ch_number=)` (`db/repos/discovery/leads.py:89,126`), `_survivor`, `_age` and `merge` (:327);
  - add a docstring paragraph.
- **New** `app/discovery/legal_form.py`: `LegalFormChecks(clock, meter, registry, discovery, research, queue)` with `@use_case check_legal_form(lead_id) -> bool`.
  - Unknown or merged Lead: log `legal_form_skipped` and return `True`.
  - Pages come from `queries.research.crawled_pages`, skipping `text is None`.
  - **The flow.**
    1. **Prepare** (a transaction): the budget check, then read the Lead. A non-GB Lead enqueues select and returns.
    2. **The calls** (no transaction):
       - **The number path:** `company(n)` for each of up to 3 site numbers, with Decision 3's check. Exactly one linking record ends the search.
       - **The name path:** skipped when the Lead has no site postcode or its normalised name is empty. Otherwise it makes one `search_companies(lead.name)` call. The candidates are hits whose normalised title equals the Lead's and whose postcode key is a site postcode. Exactly one distinct number leads to `company(number)`, which must pass the same name and postcode checks on the profile.
    3. **Apply** (a transaction): re-read the Lead. If it is merged or unknown now, log a skip. Otherwise:
       - `set_ch_number`, when a record is linked; `None` means a conflict, and the finding becomes no-link `unconfirmed`;
       - `research.record_legal_form(survivor, finding)`;
       - enqueue select for the survivor.
  - Selection uses `_enqueue_next_stage` with the survivor's `web_domain`.
  - Log `legal_form_checked` (`lead_id`, `status`, `path` (`number`, `name` or `none`), `error_code`).
- **Jobs.**
  - `jobs/handlers.py`: add a `CheckingLegalForm` Protocol and `check_legal_form(legal_forms)`, giving `DEFERRED` on `False` (copy `extract_lead`, :256).
  - `main.build_job_types(..., legal_forms=)` (:1038): register it just before `SELECT_PRIMARY_CONTACT_JOB` (:1191) with that job's spec (priority 45, BULK default, lanes BULK and INTERACTIVE, 5 minutes, retry 3, `JobGroup.ENRICHMENT`).
  - Update every `build_job_types(` caller (about 30 test files; grep for them).
  - `main.build_app` (:844):
    - add the `registry: RegistryPort | None = None` kwarg, defaulting to `CompaniesHouseRegistry(secrets, meter=meter)`;
    - build `LegalFormChecks` after `research` (:930-938);
    - update the module docstring.
- **Eligibility** `app/compliance/api.py:583-609`: read `research_queries.legal_form(lead.id)`, then `eligibility_facts`, then `EligibilityLead(..., legal_form=, legal_form_confirmed=)`. Update the docstring at :136-160.
- **Web.**
  - `web/leads_page.py` `lead_context` (:745) takes `legal_form=None` (a keyword default). `_supports` (:597) returns "Legal form" for the pointed rows.
  - `web/app.py:1306` `lead_page` passes it.
  - New `templates/lead_legal_form.html` is included after the header (`lead.html:26`) only for `country_code == "GB"`: a `<section class="lead-legal-form" hx-disable>` with no HTMX action. It shows:
    - the status text: "Confirmed: corporate subscriber", "Not a corporate subscriber", "Unconfirmed" or "Not checked yet";
    - for anything not confirmed, the line "This Lead stays researchable; UK cold email waits for a confirmed corporate legal form.";
    - the legal form as "<label> (<code>)" or "—";
    - the company status or "—";
    - the company number as `safe_link(company_page_url(ch_number), ch_number)` or "—";
    - the two Evidence quotes with their source links.
- **Tests and contracts.**
  - `tests/live_guard.py:6-24,90-99,138-161`: add `("nextron.adapters.companies_house", "default_http_client")` and `Guarded`.
  - `pyproject.toml` c5 (:284-318): add `nextron.adapters.companies_house` after `clock`.
  - `tests/fakes/registry.py` `ScriptedRegistry(*, companies=None, searches=None)`, like `tests/fakes/crawler.py`: values are a result, an `AdapterError` or a callable; it keeps `calls`; an unscripted key raises `AssertionError`. Add it to `tests/fakes/__init__.py:50-67`.
  - `tests/contracts/test_test_isolation.py:131-149,408-423`: add the port name `RegistryPort` and the package `companies_house`.
  - `tests/contracts/test_adapter_boundary.py:64,166-170,218-223`: add `PLANTED` and a `DRIVERS` entry.
  - `tests/contracts/test_source_tree.py`: `FILES` gets the new files and the migration; add `adapters/companies_house` to the layers list (:357-373).
  - Head bumps to `0030_2_12_legal_form`:
    - `tests/contracts/test_persistence_contracts.py:81`;
    - `tests/unit/test_migrations.py:39`, plus a probe like :517-560;
    - `tests/app/test_serve_process.py:324`.
  - Job lists:
    - `tests/contracts/test_job_contracts.py:367-405,446-458,544-596,662-680`;
    - `tests/unit/test_runner_lifespan.py:213-225`;
    - `tests/unit/test_health_page.py:280-287`;
    - `tests/app/test_engine_health.py:328-334`;
    - `tests/dashboard.py:166-174,336`.
  - `tests/app/test_people_extraction.py`: select becomes `check_legal_form`.
- **Docs.** `nextron-engine/README.md` gets a "Companies House legal form" section after "Decision-makers and the Primary Contact", plus the migrations row, the jobs list and an adapters note.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/legal_form.py`, `domain/markets.py`, `domain/eligibility.py` -- the pure rules and the single list -- `tests/unit/test_legal_form.py`. It covers:
  - every code classified, plus `limited-partnership`, `scottish-partnership`, `oversea-company`, unknown and `None`;
  - `status_for`;
  - name normalisation cases;
  - each phrase, zero-padding, `SC123456`, the sentence break, "VAT/FCA registration number" not matched, 9 digits not matched, the window limit and dedupe;
  - postcodes;
  - a registry quote of at most 300 characters with a 160-character name;
  - the `eligibility_facts` table;
  - `markets._GB_CORPORATE_FORMS is CORPORATE_SUBSCRIBER_FORMS`.
- [x] `nextron/ports/registry.py`, `nextron/adapters/companies_house/__init__.py` -- the port and adapter -- `tests/unit/test_companies_house_adapter.py`, through an injected `httpx2` client with a mock transport (copy the Telegram adapter tests). It covers:
  - the Basic header, and a key rotated between calls;
  - a missing key raising `credential_not_set` with no request;
  - the search params and parsing;
  - a 404 giving `None`;
  - the status-to-code mapping and a bad body;
  - metering (provider, operation, `ok`);
  - the 601st call in 300 s refused with no request and no meter row, then allowed once the window slides;
  - the key never in an error or `repr`.
- [x] Migration, models, `owners.py`, `pii_registry.py`, `db/repos/research/profile.py`, `app/queries/research.py` -- the columns and readers.
- [x] `app/research/api.py` (`record_legal_form`), `app/research/people.py` (`_enqueue_next_stage`), `app/discovery/api.py` (`set_ch_number`), `app/discovery/legal_form.py` -- the stage.
- [x] `jobs/handlers.py`, `main.py` and every `build_job_types(` caller -- the job and its wiring.
- [x] `app/compliance/api.py` -- the Eligibility wiring.
- [x] `web/leads_page.py`, `web/app.py`, `templates/lead.html`, `templates/lead_legal_form.html` -- the section and the Evidence label.
- [x] Tests:
  - `tests/app/test_legal_form_check.py`: every matrix row through `ScriptedRegistry`, on pages a real crawl stored (as in `tests/app/test_people_extraction.py`), checking the Evidence rows, the merge, the logs (no names) and the queued jobs;
  - an Eligibility app test of the UK cases;
  - Lead page tests: escaped quotes, the Companies House link, no section on a non-GB Lead, the "Not checked yet" state;
  - the contract, fake, live-guard and head updates.
- [x] `nextron-engine/README.md`, `deferred-work.md` -- the docs. Append the deferrals for Decisions 1, 3 and 11.

**Acceptance Criteria:**
- Given the full suite, when `pytest` and `lint-imports` run, then everything passes (`ai_eval` skipped) and every contract is kept.
- Given `grep` over `nextron/`, when it looks for writes to the new columns or to `ch_number`, then only `db/repos/research/profile.py` writes the columns, only `db/repos/discovery/leads.py` writes `ch_number`, and only `app/research` and `app/discovery` call them respectively.
- Given an unconfirmed GB Lead, when the chain runs, then `select_primary_contact` still runs and the Lead stays on the Leads list.

## Implementation Notes

Built as specified. Where the spec left a choice open, the code chose as follows.

- **`nextron/domain/legal_form.py`.**
  - It also holds `ACTIVE`, `MAX_QUOTE` (300), `NUMBER_WINDOW` (40), `POSTCODE_LEAD_IN` (80), `COMPANY_NUMBER_PATTERN` (`[A-Z0-9]{8}`), `COMPANY_PAGE_BASE` and `UNLINKED` (the no-link `unconfirmed` finding). `LegalFormFinding.linked` is true when it has a number.
  - `status_for` gives `unconfirmed` for any status but `active`, an unknown (`None`) status included.
  - `eligibility_facts` requires the code to match `LEGAL_FORM_PATTERN` for either `True` case. So `not_corporate` with no known type is `(None, False)`, and `confirmed` or `not_corporate` with an inconsistent code is unconfirmed.
  - **Names.** The apostrophes dropped are `'`, `‘`, `’`, `ʼ` and `` ` ``. `&` becomes ` and `. "Letter or digit" means `str.isalnum`.
  - **Phrases.**
    - Each phrase needs no letter or digit just before it. After a word-final phrase there may be none either; a `no` may end with a `.`, which may touch the number (`Company No.SC123456`).
    - **The window** is the gap between the phrase's end and the number's start: at most 40 characters. Only the first number-shaped token after the phrase is considered.
    - **The gap** is checked after removing each `no.` that starts a word. The numbers use ASCII `[0-9]`; the "no digit in the gap" check uses `str.isdigit`.
  - **Postcodes.**
    - The spec's regex runs with `re.ASCII`, and the key must then fullmatch `[A-Z]{1,2}[0-9][A-Z0-9]?[0-9][A-Z]{2}`.
    - The quote starts at the text's start when the postcode is within 80 characters of it. Otherwise it starts after the first space in the 80 characters before the postcode, or at the postcode itself when there is none.
    - `postcode_key` returns `None` for anything that isn't a postcode.
  - `registry_quote` is keyword-only. It normalises (`quotes.normalise`) the head and the name, cuts the name so the whole is at most 300 characters, and normalises again. `company_page_url` raises `ValueError` for a value that isn't a number.
  - **Validation.** `SiteQuote` and `LegalFormFinding` validate fully (a `TypeError` or `ValueError`, never with a value). A linked finding needs `site`, `registry_quote` and `retrieved_at`, and its `status` must equal `status_for(legal_form, company_status)`. An unlinked one must also have no legal form or company status. Quotes never show in a `repr`.
- **`markets` and `eligibility`.**
  - `LEGAL_FORM_PATTERN` and `_GB_CORPORATE_FORMS` in `markets` are now the `legal_form` objects; `markets` no longer defines its own pattern.
  - `eligibility` imports `LEGAL_FORM_LABELS` and `LEGAL_FORM_PATTERN` from `legal_form`, and its own `LEGAL_FORM_LABELS` is gone. `test_eligibility.py:869` passes unchanged.
- **Port.**
  - `nextron/ports/registry.py` also exports the five `REGISTRY_*` codes, `MAX_NAME` (200) and `MAX_POSTCODE` (16).
  - `RegistryCompany` hides the name and postcode from its `repr`, and requires a stripped name.
- **Adapter** (`nextron/adapters/companies_house`).
  - **Call order:** the key (`read_credential`) and the Basic header, then the lazy client, then the rate-limit slot, then `Metered` around the GET *and* the body's parsing. So a bad body is metered `registry_bad_response`, as the adapter-boundary contract requires.
  - **The rate limit.** A `deque` of `monotonic()` times under a lock. A time at or before `now - 300` leaves the window, so a call exactly 300 s after the first is allowed.
  - **Inputs.** A blank name or a number that isn't `[A-Z0-9]{8}` raises `registry_refused` with no request: the error contract allows only `AdapterError` out of an adapter. Item and profile numbers are read stripped and uppercased, names with their whitespace collapsed. A postcode is read with its whitespace collapsed and kept only when it is 1 to 16 characters.
  - **Answers.** A search body with no `items` has no hits. A non-list `items`, a non-object body, unparseable JSON, a body over 1,000,000 bytes, or a profile without a valid number and name is `registry_bad_response`. Any exception from the GET is `registry_unavailable` (retryable). A search answered 404 is `registry_refused`.
- **Data.**
  - Migration `0030_2_12_legal_form` (down `0029_2_9_tag_managers`) adds the five columns with `batch_alter_table(...).add_column` (SQLite adds them in place, at the end). The Evidence ids are `String(26)` in the migration and `Ulid` in the model, like `hq_evidence_id`.
  - `profile_repo.StoredLegalForm(legal_form, company_status, legal_form_status, site_evidence_id, registry_evidence_id)`, `legal_form(lead_id)` and `set_legal_form(lead_id, value)` (all five columns at once).
  - `queries.research.legal_form` re-exports `StoredLegalForm`.
- **`Research.record_legal_form`.**
  - A `TypeError` for a finding of another type. A `LookupError` for an unknown Lead (a non-`str` id included), a `ValueError` for a merged one. A `ValueError` when the site quote isn't on the stored page at `page_url`: no such page, purged text, or `quote_in` false. All of these come before any write.
  - Each slot reads the Lead's live Evidence. A pointer to a row that isn't one of them counts as none: a new row is inserted and the old one is left alone.
  - A confirmed row's `last_confirmed_at` is the later of its own and the new time (the page's `fetched_at`, or the finding's `retrieved_at`).
  - `EvidenceChanged` goes out for each superseded or retracted row.
  - `reparent_lead` is unchanged in code; its docstring now says the legal-form columns stay on the merged Lead.
- **`PeopleExtractions._enqueue_next_stage`** enqueues `CHECK_LEGAL_FORM_JOB` with `select_primary_contact`'s old keys. `nextron.domain.people.SELECT_PRIMARY_CONTACT_JOB` stays: `LegalFormChecks` and `main` use it.
- **`Discovery.set_ch_number`.**
  - **Arguments:** a `TypeError` for non-`str` arguments, a `ValueError` for a number that isn't `[A-Z0-9]{8}`, a `LookupError` for an unknown Lead.
  - **Held already:** a holder merged into the survivor returns the survivor. The unique index means the number can't be copied there.
  - **A holder in another chain:** after the `merge`, the older survivor is returned only when it then holds the number. Otherwise the merge stands and the answer is `None`. This happens when the number had stayed on a Lead merged into a survivor holding another number.
- **`LegalFormChecks`** (`nextron/app/discovery/legal_form.py`).
  - **Non-GB Leads.** A Lead whose country isn't `GB` (`None` included) logs `legal_form_skipped` with reason `not_gb`.
  - **The number path.** It also requires the looked-up record's number to equal the quoted number.
  - **The name path.** The search is sent the Lead's stored name. A candidate's postcode key must be among the keys of the site's postcodes, and the profile must pass the same name and postcode checks. Its site quote is the first quote of the profile's postcode in page order. `retrieved_at` is the clock's now right after the `company()` call that read the linked record.
  - **Errors.** A retryable error logs `legal_form_checked` (status `None`, the path that failed, the code) and is raised again. A non-retryable one enqueues the selection when the Lead is still live, logs the same way and returns `True`.
  - **A conflict.** The `UNLINKED` finding is recorded on `queries.discovery.survivor_of(lead)`, so a merge `set_ch_number` made still lands on the survivor. It logs `legal_form_conflict` (warning, `lead_id` only) and `legal_form_checked` with path `none`.
  - Every log line holds IDs and codes only.
- **Jobs.** `handlers.check_legal_form(legal_forms)` uses a `CheckingLegalForm` protocol. `build_job_types(..., legal_forms=)` registers `check_legal_form` just before `select_primary_contact` with that job's spec. Every caller passes `legal_forms=`: about 30 files, plus the service maps in `test_job_contracts` (`RecordingLegalForms`) and `test_backup_contracts`. `build_app(..., registry=None)` builds `CompaniesHouseRegistry(secrets, meter=meter)` and `LegalFormChecks` after `primary_contacts`.
- **Eligibility.** `Compliance.evaluate_eligibility` reads `research_queries.legal_form(lead.id)` and passes `eligibility_facts(legal_form, legal_form_status)` to `EligibilityLead`. Its docstring is updated.
- **Web.**
  - **`leads_page`** gains `LegalFormEvidence`, `LegalFormView` and `legal_form_view(lead, legal_form, evidence)` (`None` outside GB). `lead_context` takes a `legal_form=None` parameter, and the context gains `legal_form`.
  - **Evidence labels.** `evidence_row` takes `legal_form_ids` and `_supports` returns "Legal form" for those rows. That check comes after a Signal's names and before a person's.
  - **Shown values.** A stored status this module doesn't know reads "Unconfirmed". A code without a label shows alone. The number is linked only when it matches `[A-Z0-9]{8}`, else shown as text.
  - **The section** (`lead_legal_form.html`) carries `class="lead-legal-form"`, `aria-labelledby` and `hx-disable`, with heading "UK legal form". Inside it:
    - `p.legal-form-status` and `p.legal-form-note`;
    - a `dl.lead-facts` with "Legal form", "Company status" and "Company number";
    - `ul.legal-form-evidence`, each item a `span.legal-form-source` ("Website" or "Companies House"), the `<q>` quote and the source `safe_link`.

    It is included right after the header. `web/app.py` passes `legal_form=research_queries.legal_form(found.id)`. No CSS was added.
- **Tests.**
  - New: `tests/unit/test_legal_form.py` (131), `tests/unit/test_companies_house_adapter.py` (42) and `tests/app/test_legal_form_check.py` (39). The last covers every matrix row plus: three numbers looked up at most, two links ambiguous, a record answering another number, the profile re-check, a CSV number never looked up, a merge during the calls, the job handler, a pointer to a retracted row, the chain to `select_primary_contact` with the Lead still listed, and `record_legal_form`'s quote re-check.
  - `tests/app/test_eligibility.py` (+11: the UK legal-form table, read live, a US Lead ignoring it).
  - `tests/unit/test_leads_page.py` (+10, and the h2 list of a GB Lead now starts with "UK legal form").
  - `tests/unit/test_migrations.py` (the 0030 probe), and `test_people_extraction` and `test_primary_contact` (the next stage is `check_legal_form`).
  - **Contracts:** isolation (port, package and the probe classification lists), the adapter boundary (`COMPANIES_HOUSE_KEY` planted, driver `companies_house_calls`), source tree (`FILES`, layers), web template list, and the job lists and specs. Head bumps in `test_persistence_contracts` (chain probes now `0031_*`/`0032_*`), `test_migrations`, `test_serve_process` and `test_serve` (`0031_more`).
- **Docs.** README: a "Companies House legal form" section after "Decision-makers and the Primary Contact", the migrations row, the jobs list, the metering adapters note, the Eligibility section and the Lead page note. `deferred-work.md`: three entries (decisions 1, 3 and 11).
- **Verification (implementer, targeted; the full suite is the orchestrator's gate).**
  - `uv run pytest -q -n 6 tests/contracts`: 1426 passed, 10 skipped.
  - `uv run lint-imports`: 29 kept, 0 broken.
  - Every touched test file together (the new files, `test_leads_page`, both `test_eligibility`, `test_people_extraction`, `test_primary_contact`, `test_migrations`, `test_serve`, `test_runner_lifespan`, `test_health_page`, `test_engine_health`, `test_serve_process`, `test_dashboard` and every `build_job_types` caller): 2052 passed.
  - No request reached Companies House.

- **Review pass 1 patches (C, I, Q, S).**
  - **C:** `LegalFormChecks._apply` records nothing about the legal form when `set_ch_number` merged the checked Lead into another survivor and returned `None` (a conflict): that survivor's own legal form and Evidence stay. A conflict on the checked Lead itself still records the unlinked finding. New test `test_a_holder_merged_into_a_lead_with_another_number_is_a_conflict`.
  - **I:** the `max_runtime` comment says five registry calls at most (3 number lookups, a search and a profile).
  - **Q:** import order in `app/discovery/api.py`, `main.py` and `app/queries/research.py`; one docstring line in `app/research/api.py` rewrapped to 100 characters.
  - **S:** README notes that the name path needs the registered office postcode on the site.
  - `deferred-work.md` gains five entries from the triage (A/E, B, L, N/O, S).

- **Final gate (orchestrator, after the patches):** targeted tests plus `tests/contracts` 1943 passed, 10 skipped; full suite through `suite_lock.py` 13655 passed, 12 skipped; `lint-imports` 29 kept, 0 broken.

## Spec Change Log

## Review Triage Log

Review pass 1 (2026-11-18). Layers: blind hunter (BH, 14), edge-case hunter (EC, 10), verification gap (VG, 2 gaps + 3 other). 29 raw findings; deduplicated into entries A–S below.

| Id | Finding(s) | Verdict | Evidence | Route |
|---|---|---|---|---|
| A | EC1, EC10, BH2, VG-o3: a `check_legal_form` job that exhausts its retries on a retryable error never queues `select_primary_contact` | medium | True: `_failed` re-raises, the runner dead-letters after 3 attempts, nothing else queues the selection. Decision 7 and the matrix row "Errors \| 503 / 429 / window full … nothing queued" mandate it (2.10's precedent, its entry J). | reject (would change the approved spec); deferred-work entry |
| B | EC2, BH1: the self rate limit's retryable `registry_rate_limited` spends retries faster than the 300 s window frees (30+60+120 s backoff) | medium | True for a burst of more than 600 requests in 5 minutes (about 120+ GB Leads at once). Decision 8 mandates a retryable error and no sleeping; converting it to `DEFERRED` changes Decision 7/8. | reject (spec); deferred-work entry |
| C | EC4, VG-o1, VG gap 1: when `set_ch_number` merges into an older survivor that holds another number and returns `None`, `_apply` records the unlinked finding on that survivor, retracting its own valid legal-form Evidence; the branch is untested | medium | True: `_apply` sets `target = survivor_of(lead.id)` and calls `record_legal_form(target, UNLINKED)`. Decision 6 says "nothing about the legal form is recorded"; the survivor's record came from its own check. | patch: skip recording when the conflict target isn't the checked Lead; add the merge-then-conflict test |
| D | EC3: a Lead holding number X whose site now shows Y is downgraded to `unconfirmed` and its X Evidence retracted | false | Decision 6 ("the status becomes `unconfirmed`") and the fail-closed rule require exactly this: the site now contradicts the held number. | reject |
| E | EC5: a Lead merged before or during the check is skipped, so the survivor isn't re-checked | low | True, but the survivor keeps its own state (fail-closed, NULL = unconfirmed) and is checked on its own next chain run; the Code Map mandates the skip, as 2.9/2.10 do for merged Leads. | reject (spec); deferred-work entry with A |
| F | EC6, BH6: `record_legal_form` raises `ValueError` if the page text was purged or the page stayed on a merged loser between phases | low | The check runs right after a crawl (text kept 30 days), and two Leads sharing a page URL would share `web_domain` (unique). The failure is fail-closed (rollback). The fix adds branches. | reject |
| G | EC7: a non-retryable error on the 2nd/3rd number lookup discards an earlier link and skips the name path | low | True, fail-closed and per Decision 7 (a non-retryable error keeps the prior state). | reject |
| H | EC8: a search answer with `"items": null` raises `registry_bad_response` | low | Companies House returns `items: []` or omits the key (both handled, `document.get("items", [])`); a null is undocumented, and the outcome is fail-closed. | reject |
| I | EC9, BH10: the `max_runtime` comment says "at most four registry calls"; the worst case is five | low | True (3 number lookups + search + profile). Direct correction. | patch |
| J | BH3: a missing or rejected key fails silently per Lead | low | Partly false: the key is a required credential, so `nextron doctor` and the Health panel's readiness list show it missing. A rejected key (401) is only logged. An alert is new machinery. | reject |
| K | BH4: a confirmation never expires | false | Re-checks are FR-61 (Epic 8), excluded by the spec's Never list and planned there; nothing in this story's intent sets a maximum age. | reject (out of scope by intent) |
| L | BH5: a subsidiary footer quoting the Lead's and the parent's numbers makes the number path ambiguous | low | True, and fail-closed (the name path runs). Preferring an exact-name record changes Decisions 3–4. | reject (spec); deferred-work entry |
| M | BH7: `status_for(None, "active")` is `not_corporate` but Eligibility reports `uk_legal_form_unconfirmed` | low | True and fail-closed both ways; the spec mandates `not_corporate` for an unknown type and Decision 9's `(None, False)` for it. | reject (spec) |
| N | BH8: "Not a corporate subscriber" is inaccurate for bodies corporate outside the 8 approved types (CIO, royal charter…) | low | The wording is the spec's; excluding them is compliance-safe. | reject (spec); noted in the report |
| O | BH9: the page shows a CSV or conflicting `ch_number` as a Companies House link without saying whether Evidence backs it; a conflict is only logged | low | True; the spec's Code Map mandates the link and the status line says "Not checked yet"/"Unconfirmed" beside it. | reject (spec); deferred-work entry |
| P | BH11: the 1 MB body cap reads the body before checking | low | True, but the host is the fixed Companies House API; streaming is more than a direct correction. | reject |
| Q | BH12: import order and one over-long docstring line | low | True (no ruff in this environment, so nothing enforces it). Direct correction. | patch |
| R | BH13, VG gap 2: tests missing for `set_ch_number` directly, a merge after a confirmed check, and the conflict after a prior confirmation | low | `set_ch_number`'s merge branch is covered by C's new test; a merge after confirm moving the loser's Evidence unlabelled to the survivor is Decision 11's accepted state. | patch (C's test); the rest reject (spec) |
| S | BH14: the name path rarely links firms registered at an accountant's address; undocumented | low | True; a README note and a deferred-work entry are a direct doc correction. | patch (docs) |

## Verification

**Commands** (from `nextron-engine/`, `NEXTRON_ENV=test`):
- `uv run pytest -q tests/unit/test_legal_form.py tests/unit/test_companies_house_adapter.py tests/app/test_legal_form_check.py tests/unit/test_leads_page.py tests/unit/test_eligibility.py tests/app/test_people_extraction.py` -- expected: all pass.
- `uv run pytest -q tests/contracts` and `uv run lint-imports` -- expected: all pass, every contract kept.
- The full suite runs only through `suite_lock.py`, by the orchestrator.
