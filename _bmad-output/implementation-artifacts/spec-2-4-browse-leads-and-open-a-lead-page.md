---
title: 'Story 2.4: Browse Leads and open a Lead page'
type: 'feature'
created: '2026-11-17'
status: 'done'
baseline_commit: 'ccc7c1b794b5472639cc7985539bbbc439b6325f'
route: 'dispatch'
review_loop_iteration: 0
story_key: '2-4-browse-leads-and-open-a-lead-page'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Mac can import Leads (Story 2.3) but can't see them. There is no Leads list, no Lead page, and no place where a Contact's Provenance Records show (FR-51), so the nav's Leads/Pipeline section is still missing (UX-DR18's Slice 1 list, UX-DR17's minimal Lead page).

Requirements: FR-51, FR-13 · AD-3, AD-13 · UX-DR17, UX-DR18.

**Approach:**
- A read-only Leads list at `GET /leads` (route `pipeline_page`, so the nav shows "Leads/Pipeline") reads through new `queries.discovery.lead_list`, 50 per page, filterable by stage and Segment and searchable by name or domain.
- A Lead page at `GET /leads/{lead_id}` (route `lead_page`) reads `queries.discovery.survivor_of`, `queries.pipeline.stage`, `queries.setup.segments`, `queries.research.contacts` and new `queries.research.lead_provenance`, and redirects a merged Lead to its survivor.
- No migration, no write path, no new dependency.

The acceptance criteria are Story 2.4 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**

- **List query** (`db/repos/discovery/leads.py` + `app/queries/discovery.py`).
  - Repo: `ListedLead(id, name, web_domain, segment_id, country_code, pipeline_stage, created_at)`, `listed(*, stage, segment_id, search, limit, offset)` and `count_listed(*, stage, segment_id, search)`. Both keep only `merged_into IS NULL`; add `pipeline_stage == stage`, `segment_id == segment_id`, and `name.contains(search, autoescape=True) OR web_domain.contains(search, autoescape=True)` when each is not `None`. Order: `created_at DESC, id DESC`. The docstring says it reads the pipeline group's `pipeline_stage` column read-only.
  - Query: `LEADS_PER_PAGE = 50`; `LeadListPage(leads, total, page, pages)`; `lead_list(*, stage: PipelineStage | None = None, segment_id: str | None = None, search: str | None = None, page: int = 1)`. `pages = max(1, ceil(total / 50))`, and `page` is clamped to `pages`. Errors: a `page` that is not an `int` (a `bool` included) raises `TypeError`, and `page < 1` raises `ValueError`. A blank `search` means none.
- **`survivor_of(lead_id) -> StoredLead | None`** (`queries.discovery`) follows `merged_into` to the Lead that isn't merged, through at most `MAX_MERGE_HOPS = 32` hops. It returns `None` for a non-ULID or an unknown id, and for a chain longer than the bound. This is deferred item 2.2/BH6's query; Story 2.5's jobs reuse it.
- **Provenance per Lead.** `provenance.for_lead(lead_id)` (research repo) runs one statement joining `contacts`. `queries.research.lead_provenance(lead_id) -> dict[str, tuple[StoredProvenance, ...]]` maps each Contact id that has records to them, by `retrieved_at`, then `id`. It returns `{}` for a non-ULID or an unknown Lead.
- **Filters** (`nextron/web/leads_page.py`, `list_filters(query_items)`).
  - Parameters: `stage`, `segment` (a Segment key), `q` and `page`, each at most once.
  - Blank means unset. `q` is stripped, with inner whitespace collapsed, and is at most 200 characters. `page` is 1–7 ASCII digits with a value ≥ 1.
  - An unknown or repeated parameter, an unknown stage, an unknown Segment key or a bad `q` or `page` raises `MalformedForm`, so the response is 400.
- **List page** (`leads.html`):
  - `<h1>Leads</h1>` and a link "Import Leads from a CSV file" (`/leads/import`).
  - The filter form: `method="get" action="/leads"`, with no `hx-post` and no `csrf_field()`. It has a stage select ("All stages", then each `PipelineStage` in machine order by its label), a Segment select ("All Segments", then each by name, with the key as its value), a `q` text input (maxlength 200, "Name or website") and a "Filter" button. A "Clear filters" link to `/leads` shows while any filter is set.
  - The count line: "Showing <a>–<b> of <total> Leads", "No Leads yet." or "No Leads match these filters.".
  - The table columns:
    - Name, linked to `/leads/<id>`;
    - Website, the domain or "—";
    - Segment, its name or "Unknown Segment";
    - Country, the code or "—";
    - Stage, its label;
    - Created, `YYYY-MM-DD` UTC.
  - When `pages > 1`, a pager reads "Page <n> of <pages>", with Previous and Next links that keep the filters (only set parameters, URL-encoded).
- **Lead page** (`lead.html`, title "Nextron: Lead").
  - **Header:**
    - the name as `<h1>`;
    - Website: `safe_link("https://" + web_domain + "/", web_domain)`, or "No website on record";
    - Segment;
    - Country;
    - Pipeline Stage (label);
    - Created;
    - a link "All Leads" (`/leads`).
  - **Contacts section** (`<h2>Contacts</h2>`): "No Contacts yet." when there are none. Otherwise a table, oldest first, with the columns Name, Role, Email, Address Class, LinkedIn (`safe_link`), Primary (`Yes`/`No`), where a blank shows "—".
  - **Provenance.** Each Contact row is followed by a row listing its Provenance Records, with the columns Record ("Contact", or "Address" and its address), Source (label), Detail, Retrieved (`YYYY-MM-DD`) and Address Class ("—" if none). Without records it reads "No Provenance Record.".
- **Merged and unknown Leads.** A merged Lead's URL answers 303 with `Location: /leads/<survivor id>`. A non-ULID or unknown id, or a broken chain, answers 404 with `lead_missing.html`: "Lead not found" and a link to `/leads`. The id is never echoed.
- **Escaping.** Every Lead, Contact and Provenance text renders through `|plain` or `safe_link`, inside an element carrying `hx-disable` (`untrusted_block` or a container). There is no `|safe`.
- **Route rules.** Both routes are GETs declaring `@personal_data(True)`, so they send `no-store`. `/leads/{lead_id}` is registered after `/leads/import`. `nextron/web/leads_page.py` imports from `nextron.app` only `nextron.app.queries.*`.
- **Forms contract** (`tests/contracts/test_web_contracts.py`). A `<form>` may also be `method="get"` when it has no `hx-post`, no `csrf_field()` (a token must never reach a URL) and an `action` that is a local path starting with `/`. Probes cover each violation, and the docstring sentence "a GET filter form must amend this contract" is replaced by the rule.

**Never:** a write, migration or new dependency; Market, Tier, scores, Signals, Evidence, timeline, stage actions, Primary change, "no decision-maker found", DSR actions, "Provenance missing" (see decision 9); `|safe`/`Markup`; reading through `nextron.app.<owner>.api` or `nextron.db`; echoing a raw query value except the escaped `q`.

### Decisions made overnight (pending user review)

1. **The Leads list is the nav's Leads/Pipeline section** at `/leads` (route `pipeline_page`). Story 9.7's grouped Pipeline view replaces it later.
2. **The list query lives in `queries.discovery`.** It reads the pipeline group's `pipeline_stage` column of the same rows, read-only, so the stage filter and paging stay in one SQL statement. Writes still follow AD-3's single-owner rule.
3. **Search** is a substring match on the name or the website domain, ignoring case for ASCII letters only (SQLite `LIKE`). `%` and `_` are literal. The URL as typed isn't stored, so `www.` and `https://` in a search don't match.
4. **Order and paging.** Newest first, by created date and then id, 50 per page. A page past the end shows the last page. Merged Leads are excluded; every stage, Disqualified included, is listed.
5. **A bad query parameter is refused with 400** rather than silently ignored, so a hand-edited URL never shows an unfiltered list that looks filtered.
6. **The website link is `https://<domain>/`,** because only the registrable domain is stored. A Lead without a domain shows "No website on record".
7. **A merged Lead redirects with a 303 straight to the end of its merge chain** (at most 32 hops). A broken chain is a 404.
8. **Both pages declare personal data** (a Lead's name can be a sole trader's name), so both send `no-store`. The GET filter form never carries the CSRF token.
9. **Deferred to their stories:**
   - Market: 3.2 and 3.7;
   - Signals and Evidence: 2.9;
   - "No decision-maker found" and changing the Primary: 2.10;
   - Tier and scores: 2.15;
   - Provenance's Market rule, its basis and "Provenance missing": 3.7;
   - stage actions and the timeline: Epics 5 and 9;
   - DSR erase and export: 3.13 onwards.
10. **Contacts.** Every Contact shows, oldest first, with no paging. One Provenance query covers the whole Lead.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| List | Leads A (oldest), B, C; B merged into A | rows C, A with name link, domain, Segment name, country, stage label, created date; "Showing 1–2 of 2 Leads" | N/A |
| Filters | `stage=contacted`, `segment=law_private_ai`, `q=Smith_` | only matching Leads; `_` literal; the selects and `q` keep their values | N/A |
| Paging | 51 Leads; `page=2`; `page=9` | 50 rows and a Next link with the filters; 1 row; the last page | N/A |
| Bad params | `stage=bogus`, `segment=nope`, `page=0`, `page=abc`, `q` × 2, `foo=1`, a 201-character `q` | 400 "Request refused: malformed form" | `MalformedForm` |
| Lead page | a CSV-imported Lead with one Contact | header facts; Contact row; Contact and Address records with the origin, date and `guessed` | N/A |
| Merged | GET loser; chain C→B→A | 303 to `/leads/<A>` | N/A |
| Missing | unknown ULID, lowercase ULID, `abc`, `<script>` | 404 "Lead not found", the id not echoed | N/A |
| Hostile text | name `<script>x</script>`, role `<img src=x onerror=y>`, origin `<b hx-get="/x">` | escaped, each inside `hx-disable` | N/A |
| Empty | no Contacts; a Contact without records | "No Contacts yet."; "No Provenance Record." | N/A |

</frozen-after-approval>

## Code Map

- **Queries.**
  - `nextron/app/queries/discovery.py`: today only `lead()`; add `lead_list`, `LeadListPage`, `LEADS_PER_PAGE`, `survivor_of` and `MAX_MERGE_HOPS`; extend the docstring.
  - `nextron/db/repos/discovery/leads.py`: the `StoredLead` and `_COLUMNS` style; add `ListedLead`, `listed` and `count_listed`.
  - `nextron/app/queries/research.py` and `nextron/db/repos/research/provenance.py` (`for_contact` at line 89 is the style), plus `contacts.py`.
  - `queries.pipeline.stage` and `queries.setup.segments()` (`SeededSegment.id/.key/.definition.name`) already exist.
  - Contract group 7 lets a queries module import only its own repos.
- **Domain.**
  - `domain/pipeline_stage.py`: `PipelineStage`, in machine order.
  - `domain/contacts.py`: `ProvenanceSource` and `ProvenanceSubject`.
  - `domain/address_class.py`.
  - `domain/ids.is_ulid`: canonical uppercase only.
- **Web.**
  - `nextron/web/app.py`: the `NAV_SECTIONS` at 181 already holds `pipeline_page`; `page()` at 333, then set `status_code` for the 404; the `lead_import_page` routes near line 610. Register the new routes after them, and extend the module docstring's route list and `RedirectResponse`.
  - `web/lead_import_page.py`: the view-module style (constants, frozen dataclasses, `segment_options`).
  - `web/forms.py`: `MalformedForm`, `WRONG_FIELDS`, `REPEATED_FIELD`, `malformed_form`.
  - `web/templating.py`: `plain`, `safe_link`; `templates/macros.html`: `untrusted_block`.
  - `lead_import_form.html`: the table idiom.
  - `home.html`: keep its import link.
  - `static/app.css`: reuse `.import-table`, `.untrusted` and `.field`; add only what's needed.
- **Tests to update.**
  - `tests/unit/test_dashboard.py:726` (`test_the_nav_lists_only_the_sections_that_exist`) and 746: add `("/leads", "Leads/Pipeline")` in nav order.
  - `tests/contracts/test_web_contracts.py:730+`: `form_problems` and the docstring at line 23.
  - `tests/contracts/test_source_tree.py` `FILES` (around 153–164): add the new module and templates.
- **Tests to reuse.**
  - `tests/unit/test_lead_import_page.py`: the `real_units` fixture (real subscribers, shipped Segments synced), `Dashboard(units=..., clock=...)`, the `HTMLParser` helpers and `upload(...)` for a CSV import;
  - `tests/app/test_lead_import.py`: the app-level world and `Discovery.merge`;
  - `tests/fakes/clock.py`: `FrozenClock`.
- **README:** `nextron-engine/README.md` "CSV import, Contacts and Provenance" (line 648); add a "Leads list and Lead page" section after it.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/db/repos/discovery/leads.py`, `nextron/app/queries/discovery.py` -- `ListedLead`, `listed`, `count_listed`; `lead_list`, `LeadListPage`, `survivor_of` -- list and merge-chain reads.
- [x] `nextron-engine/nextron/db/repos/research/provenance.py`, `nextron/app/queries/research.py` -- `for_lead`; `lead_provenance` -- one Provenance read per Lead.
- [x] `nextron-engine/nextron/web/leads_page.py` -- `STAGE_LABELS` (every stage), `SOURCE_LABELS` (every source), `ListFilters`, `list_filters`, the page-link builder and context builders -- thin views.
- [x] `nextron-engine/nextron/web/app.py`, `web/templates/{leads,lead,lead_missing}.html`, `web/static/app.css` -- the routes `pipeline_page` and `lead_page`, the templates and the styles.
- [x] Tests:
  - `tests/app/test_lead_queries.py`: real units on a temp DB, covering the filters, the `LIKE` escape, the order, the clamp, the count, merged exclusion, `survivor_of` (chain, unknown, non-ULID, over-long via raw rows), `lead_provenance` and the argument errors;
  - `tests/unit/test_leads_page.py`: every page row of the matrix, `no-store`, the lock without a session, the route order (`/leads/import` intact), the nav link, the label completeness, the `hx-disable` ancestry of hostile text, and the queries-only import rule.
- [x] Contract updates (forms, source tree), the nav tests and the README section.

**Acceptance Criteria:**
- Given the full suite, when it runs through the suite lock, then everything passes and `lint-imports` keeps every contract.
- Given a session on a dashboard with imported Leads, when Mac opens Leads in the nav, filters and pages, and opens a Lead, then the list and the Lead page show what the matrix lists, and every imported text is escaped.

## Implementation Notes

- **Reads.** `leads_repo.listed`/`count_listed` share one `_listed_where` (`merged_into IS NULL`,
  then `pipeline_stage ==`, `segment_id ==`, and `name.contains(search, autoescape=True) OR
  web_domain.contains(search, autoescape=True)`); `listed` orders `created_at DESC, id DESC`
  with `LIMIT`/`OFFSET`. `lead_list` counts first, computes `pages = max(1, ceil(total / 50))`,
  clamps `page`, then reads the page, all in the caller's read-only unit. Besides the `page`
  errors, it raises `TypeError` for a `stage` that isn't a `PipelineStage` and a `segment_id` or
  `search` that isn't a `str`. `survivor_of` takes at most 32 hops and returns `None` for a loop,
  a dangling `merged_into` or a longer chain. `provenance.for_lead` joins `contacts` on
  `contact_id` in one statement; `lead_provenance` groups it by Contact id, keeping record order.
- **Views** (`nextron/web/leads_page.py`). `list_filters(query_items, segments)` takes the stored
  Segments as a second argument to resolve the `segment` key to its id (the spec named only
  `query_items`); `ListFilters` holds `stage`, `segment` (key), `segment_id`, `search` and
  `page`, and `filtered` is true for a stage, Segment or search. A blank parameter is empty or
  whitespace only. A "bad `q`" is one over 200 characters after stripping and collapsing its
  whitespace (tabs, line breaks and the like count as spaces), or one still holding a control
  character (`Cc`); every refusal is `MalformedForm("wrong_fields")`, a repeat
  `MalformedForm("repeated_field")`.
  `page_href` always carries `page` after the set filters (`urlencode`). The count line uses the
  singular for one Lead ("Showing 1–1 of 1 Lead"). Unknown stored stage or source values render
  as their own text (`stage_label`, `source_label`).
- **Templates.** `hx-disable` sits on containers: `header.lead-header` and the `table.lead-table`
  of the list and of the Contacts (the nested `provenance-table`s inherit it). The 404 page
  says "No Lead has this address. Open the Lead from the Leads list." with an "All Leads" link.
  The redirect also passes `NO_STORE` (the guard sets it anyway).
- **Routes.** `pipeline_page` and `lead_page` are added after the import routes; the 404 sets
  `status_code` on `page()`'s response.
- **Forms contract.** A `method="get"` form is checked separately: no `hx-post` or
  `data-hx-post` on its tag, no `csrf` text before its `</form>` (stricter than "no
  `csrf_field()`": a hand-written `csrf_token` is caught too), and a local `action` (`/` not
  followed by `/` or a backslash, quoted or not). A form tag with more than one `method` fails
  (a browser takes the first). The old probe `method="get" hx-post` became `method="got"`
  (still "without method=post"); new probes cover each GET violation, the two-method case and
  three allowed GET forms.
- **Other test updates.** `test_web_contracts` route and template lists;
  `test_google_contracts.PARAMETER_VALUES` gained `lead_id` (its every-GET probe fills each
  path parameter); `test_source_tree.FILES`; the two nav tests.
- **README:** "Leads list and Lead page" after "CSV import, Contacts and Provenance", and the
  form-contract sentences in "Open the dashboard".
- **Review fixes (pass 1).**
  - P1: `test_the_pager_links_encode_the_search_and_round_trip` searches `A&B+C 50% Firm` over
    51 matching Leads, follows the rendered Next href, then the Previous href back.
  - P2: the past-end page asserts its count line and that its Previous href carries `page=1`.
  - P3: `test_segment_options_are_by_name_whatever_the_key_order` probes `segment_options` with
    Segments whose name and key orders disagree, and the `selected` flag.
  - P4: the filter-form test collects every named element in the form and asserts the session's
    token value (and any `csrf` text) is absent from the form's HTML.
  - P5: the README and the `leads_page` docstring say whitespace characters count as spaces in
    `q`, any other control character is refused, and a blank parameter is empty or whitespace
    only.
  - P6: the README notes an internationalised domain is stored and shown, so searched, in its
    `xn--` form.
  - P7: `assert ids` became a check of page 1's row order and links against `ids`.
  - P8: the README parameters paragraph and the `leads_page` docstring are rewrapped.
- **Suite gate fix.** The first full run caught `tests/unit/test_credentials_page.py`
  (`test_the_page_extends_the_layout_and_links_back_to_settings`) pinning the nav without
  Leads/Pipeline; its expected links and nav text now include `("/leads", "Leads/Pipeline")`.

## Spec Change Log

## Review Triage Log

**Pass 1** (blind hunter BH, edge-case hunter EC, verification gap VG): 28 raw findings, 20 after grouping. 8 entries are patched, none deferred and 12 rejected. Nothing routed to intent_gap or bad_spec.

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| VG1 | Pager hrefs' URL-encoding never observed; the test reads `q=Firm 0` only and never follows the link | medium | Pre-verified gap. A plain `k=v` join passes `test_paging_keeps_the_filters`, while `q=A&B` would 400 on page 2. | patch (P1) |
| BH5 | Pager encoding untested for `&`, `#`, `+`, `%` and non-ASCII | medium | Same defect as VG1. | patch (P1) |
| VG2 | Count line and Previous href on a page past the end not asserted | low | Pre-verified gap. Using `filters.page` would render "Showing 401–401" unnoticed. | patch (P2) |
| VG3 | Segment select's by-name order not observable with the shipped Segments | low | Pre-verified gap. The two shipped Segments' key and name orders agree. | patch (P3) |
| VG4 | Filter form name check skips `button`, and no test submits the form as rendered | medium | Pre-verified gap. A `name` on the button would 400 every real submission. | patch (P4) |
| VG-O2 | `q` control characters that `str.split()` treats as whitespace are collapsed, not refused, though the README says refused | low | Confirmed: `_search` collapses before the `Cc` check, so `a\x1fb` becomes `a b`. The behaviour is harmless; the README is wrong. | patch (P5) |
| EC1 | Same as VG-O2 | low | Same root cause. | patch (P5) |
| BH3 | Same as VG-O2; the README says blank is "only spaces" but any whitespace counts | low | Confirmed. Same root cause (inaccurate docs). | patch (P5) |
| BH7 | IDN domains are stored and shown as `xn--…`, so a Unicode search misses them; undocumented | low | `registrable_domain` stores the IDNA form. Showing punycode is the homograph-safe choice; only the README lacks the note. | patch (P6) |
| BH11 | `assert ids` at the end of `test_paging_keeps_the_filters` is vacuous | low | Confirmed: it checks only that the list is non-empty. | patch (P7) |
| BH12 | README line over 160 characters; `leads_page` docstring broken mid-sentence | low | Confirmed at README "Leads list and Lead page" and the `leads_page.py` docstring ("``page`` is 1 to 7" / "ASCII digits"). | patch (P8) |
| VG-O1 | `survivor_of` stops at 32 hops while `Discovery._survivor` accepts chains up to `MAX_MERGE_CHAIN` 64 | low | Real (`discovery/api.py:106`). A chain of 33 or more successive merges is very unlikely. Decision 7 and the frozen Boundaries fix 32, so aligning the bounds changes the approved spec. | reject (spec) |
| VG-O3 | Forms contract passes a GET form with two `action` attributes, the first off-site | low | Real in the regex, but CSP `form-action 'self'` (AD-13) blocks any off-site submission. Unlikely, and the fix adds a guard. | reject |
| EC3 | Same as VG-O3 | low | As VG-O3. | reject |
| EC4 | `action="/&#47;evil"` or `action="/{{ next }}"` passes the local-action regex | low | CSP `form-action 'self'` blocks the off-site case. No template builds an action from a variable. Unlikely; a guard. | reject |
| BH1 | Unspaced Jinja `action="/{{next}}"` passes | low | As EC4. | reject |
| EC5 | `formaction`/`formmethod` on a submitter, or a token via `{% include %}`/`form=`, escapes the GET-form check | low | `formaction` off-site is blocked by CSP. A native `formmethod="post"` sends `Origin: null`, which the guard refuses (403). An included token is a contrived template. Unlikely; guards. | reject |
| BH2 | Same family as EC5, plus `hx-put`/`hx-patch`/`hx-delete` | low | As EC5 and EC6. The rendered-page token check is strengthened anyway under P4. | reject |
| EC6 | A GET form with `hx-put`/`hx-patch`/`hx-delete` could change state | false | `dashboard_route_class` refuses any route that isn't exactly GET or POST, so such a request gets 405 and changes nothing. | reject |
| EC2 | `queries.pipeline.stage` raises `ValueError` for a stored stage outside `PipelineStage`, so the Lead page gives 500 | false | `ck_leads_pipeline_stage` (built from the enum) keeps every stored value a member. Retiring a stage needs a migration that remaps rows. | reject |
| EC7 | `lead_list` with a search over SQLite's 50,000-byte LIKE limit raises `OperationalError` | false | The only caller caps `q` at 200 characters. The failure is loud and on an input no caller produces. | reject |
| BH4 | A refused query string gives a bare plain-text 400 page with no nav | low | Real for a native GET. Only hand-edited URLs reach it: the selects and `maxlength` produce valid values, and Segments can't be removed. Decision 5 chose 400. An HTML 400 page adds a branch and a template. | reject |
| BH6 | No test renders a survivor's page after a merge where the loser had Contacts | low | Reparenting is Story 2.3's, proven in `tests/app/test_lead_import.py` (Merge row) and by the reparent contract. The page reads Contacts by the survivor's id, covered by `test_a_csv_imported_lead_shows_its_contact_and_provenance`. | reject |
| BH8 | New CSS copies `.import-table`'s cell rules and `.import-counts` | low | Cosmetic. Scoped selectors, no user harm. | reject |
| BH9 | `segment_options` repeats the import page's `(name.casefold(), key)` ordering | low | Different return types (`Option` with `selected`). The only harm is a possible ordering drift between two selects. | reject |
| BH10 | `World` fixtures duplicated across the two new test files | low | Test-only duplication. No named harm beyond style. | reject |
| BH13 | `list_filters`/`list_context` take `Iterable` but the route iterates one object twice | false | `queries.setup.segments()` returns a `tuple` (`setup.py:120-122`), so both iterate the same tuple. | reject |
| BH14 | The Lead page's "All Leads" link drops the list's filters and page | low | Real. Boundaries specify "a link All Leads (`/leads`)". Carrying filters needs a new Lead-page parameter, which changes the approved spec. Browser Back keeps them. | reject (spec) |

**Patches dispatched:**
- **P1.** A paging test whose search holds `&`, `+` and `%` follows the rendered Next href.
- **P2.** On the past-end page, assert the count line and the Previous href (`page=1`).
- **P3.** A `segment_options` probe whose name order and key order disagree.
- **P4.** Collect every named control in the filter form, buttons included, and assert the session's token value is absent from the form.
- **P5.** The README says whitespace-class control characters count as spaces, other control characters are refused, and a blank parameter is empty or whitespace only.
- **P6.** The README notes IDN domains are stored and shown in their `xn--` form, and searched in it.
- **P7.** Replace `assert ids` with a real assertion.
- **P8.** Rewrap the README line and the docstring.

## Design Notes

**Stage labels:**
- `new` New
- `queued` Queued
- `contacted` Contacted
- `replied` Replied
- `interested` Interested
- `call_booked` Call booked
- `call_held` Call held
- `audit_proposed` Audit proposed
- `audit_won` Audit won
- `build_proposed` Build proposed
- `won` Won
- `not_now` Not now
- `lost` Lost
- `disqualified` Disqualified

**Source labels:**
- `csv` CSV import
- `website` Website
- `signal_inbox` Signal Inbox (manual)
- `referral` Referral
- `replier` Replier
- `operator` Entered by Mac
- `pattern` Email pattern
- `finder` Email finder
- `eligibility_basis` Eligibility basis

An unknown stored value renders as its raw text through `|plain`.

**The view flow:**
```python
@app.get("/leads/{lead_id}", name="lead_page")
@personal_data(True)
def lead_page(request: Request, lead_id: str) -> Response:
    found = discovery_queries.survivor_of(lead_id)
    if found is None:
        return missing(request)                      # 404, lead_missing.html
    if found.id != lead_id:
        return RedirectResponse(f"/leads/{found.id}", status_code=303)
    context = lead_rows.lead_context(found, pipeline_queries.stage(found.id), ...)
    return page(request, lead_rows.LEAD_TEMPLATE, **context)
```

## Verification

**Commands:**
- `cd nextron-engine; uv run pytest -q tests/app/test_lead_queries.py tests/unit/test_leads_page.py tests/unit/test_dashboard.py tests/contracts` -- expected: all pass.
- `uv run lint-imports` -- expected: every contract KEPT.
- Full suite through `suite_lock.py` (orchestrator only).
