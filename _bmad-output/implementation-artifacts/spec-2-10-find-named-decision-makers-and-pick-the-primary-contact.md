---
title: 'Story 2.10: Find named decision-makers and pick the Primary Contact'
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: '8000957cb38d6390f58dd485600105d096b7dcba'
review_loop_iteration: 0
story_key: '2-10-find-named-decision-makers-and-pick-the-primary-contact'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-9-detect-signals-with-evidence-from-the-crawled-pages.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-3-2-resolve-each-contact-s-market-and-timezone.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Story 2.9 turns crawled pages into Signals and Evidence, but no Lead gets a named decision-maker. Nothing calls `extract_people`, nothing filters people by the Segment's decision-maker roles, and nothing picks the Primary Contact that Stories 2.13 (finding the address), 2.15 (scoring) and 4.8 (enrolment) need. A Lead with nobody to write to doesn't say so.

Requirements: FR-10, FR-13, FR-51 · AD-3, AD-7, AD-10, AD-17, AD-18.

**Approach:**
- **People.** `extract_lead` gains a second Claude call: `extract_people`, sent the Lead's team, about and contact pages.
- **Validation and storage.** Code validates each person. Only people in the Segment's decision-maker roles become Contacts, through `research.add_contact(source=website)`, each with Quote Evidence (`person_contact_id`). A Law Lead also gets Count Evidence: the number of lawyers listed.
- **Selection.** `extract_lead` then enqueues a new `select_primary_contact` job, which picks the Primary Contact, or marks the Lead `no_decision_maker`.
- **The Lead page** shows the Primary Contact, or "no decision-maker found", and a "Make primary" button on each Contact.
- **Migration** `0023_2_10_primary_contact`.

The acceptance criteria are Story 2.10 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**

- **Pure domain** (standard library and `nextron.domain` only).
  - **`nextron/domain/contacts.py`:**
    - `PrimarySetBy` (`system`, `operator`);
    - `RoleMatch(title, rank)`; `role_match(role, segment) -> RoleMatch | None`; `title_matches(role, title) -> bool`; `is_lawyer_role(role) -> bool` (Decision 5);
    - `PrimaryCandidate(contact_id, role, created_at, address_class, departed, is_primary, set_by)`; `choose_primary(candidates, segment, preferred_roles) -> PrimaryChoice(contact_id | None, set_by | None)` (Decision 9);
    - `PrimaryRefused(reason)`, a `DomainError` with code `primary_refused` and reasons `unknown_lead`, `merged_lead`, `not_on_lead` and `address_invalid`.
  - **`nextron/domain/signals.py`:**
    - `EventSignal(signal_id, signal_type, tier, weight, event_date, first_seen_at)`;
    - `strongest_event(events, now) -> EventSignal | None`: the live Event with the highest `weight × weight_factor`;
    - `preferred_roles(signal_type, quotes) -> tuple[str, ...]` (Decision 10).
  - **New `nextron/domain/people.py`:**
    - the constants `ROUTE = "extract_people"`, `BUDGET_CLASS = BudgetClass.BLOCKABLE`, `PEOPLE_PAGE_KINDS = (team, about, contact)`, `LAWYERS_LISTED = "lawyers listed"` and `PERSON_DISCARDED = "person_discarded"`;
    - `PersonDiscardCause`: `unknown_block`, `quote_empty`, `quote_too_long`, `quote_not_in_page`, `name_invalid`, `role_invalid`, `name_not_in_quote`, `role_not_in_quote`;
    - `validate_people(parsed, pages) -> PeopleValidation(people, discards)` (Decision 3);
    - `office_from_quote(quote, lead_country, markets) -> Location | None` (Decision 4);
    - `lawyer_count(people) -> int`;
    - `reconcile_people(live, proposed, texts)`, which keeps `extraction.reconcile`'s four rules, grouped per (page URL, Contact id).
- **Migration `0023_2_10_primary_contact`.** Its down revision is `0022_2_9_signals_evidence`, and `downgrade()` raises. Everything is owned by `research`, with PII class `none`.
  - `contacts.primary_set_by` is a nullable `String(8)`.
  - `leads.no_decision_maker` is a nullable `Boolean`: NULL until selection first runs.
  - Both columns are added with `add_column` only: no rebuild and no CHECK.
  - Partial unique index `uq_contacts_one_primary` on `contacts(lead_id) WHERE is_primary = 1`.
  - Code keeps `is_primary` ⇔ `primary_set_by IS NOT NULL`.
- **`extract_lead` (the job is unchanged).** `SignalExtractions(clock, meter, llm, research, people)`.
  - After the Signals phases (whatever their outcome, except a budget refusal, an unknown or merged Lead, or a raise), it runs `PeopleExtractions.extract(lead_id, run_id) -> bool`. This lives in `nextron/app/research/people.py`, isn't a use case, and runs its own transactions:
    1. **Prepare.**
       - `allow(BUDGET_CLASS, ...)` refuses: return `False`, so the whole job is deferred (Decision 2).
       - Unknown or merged Lead: skip.
       - No `active_tuple(ROUTE)`: a `no_activation` run.
       - Pages: when any page of `PEOPLE_PAGE_KINDS` with text has a `changed_at` later than the `started_at` of the last `ok` run **of this route**, every such page with text is sent. They go in `PageKind` order, then by URL, cut by `allot(..., PAGE_TEXT_BUDGET)`, one `website` block each (block id: the page id). Otherwise it records a `no_pages` run.
    2. **Call.** `llm.run(tuple_, blocks, Urgency.BULK)`, handling failures exactly as 2.9 does.
    3. **Apply**, after `ok`, in one transaction:
       - A merged Lead records `lead_merged`.
       - Each refusal appends one `events` row: entity `leads`, `to_status` `person_discarded`, the cause, `cause_ref` the page id when it was sent, actor `tuple_.actor`.
       - Each kept person whose `role_match` isn't `None` goes through `research.add_contact(lead_id, ContactCandidate(name, role, office=office_from_quote(...)), source=website, detail=<page URL>, retrieved_at=<page fetched_at>)`. People in other roles are never stored.
       - Person Evidence is reconciled (`reconcile_people`, scope: live `quote` rows with a `person_contact_id`, an `ai:extract_people@` actor and a `source_url` among the pages sent). New rows have `type` `quote`, `source` `website`, `source_url` and `pages_checked` `[url]`, `retrieved_at` and `last_confirmed_at` the page's `fetched_at`, `extracted_by` `tuple_.actor` and `person_contact_id`.
       - A Law Lead's lawyer count is stored per Decision 6.
       - It publishes `EvidenceChanged` for each superseded or retracted row.
       - It records the `ok` run (`stored`: Evidence rows inserted; `discarded`: refusals).
    4. **Whenever it recorded a run** (any outcome but a raise), the same transaction enqueues `select_primary_contact`:
       - scope: the Lead; discriminator `tick`; payload `{v, lead_id}`;
       - concurrency key `domain:<web_domain>` when the Lead has one.

    `signals_repo.last_ok_run` takes `route`, and 2.9 passes its own `ROUTE`.
  - **Wiring and results.** It is `PeopleExtractions(clock, meter, llm, research, queue)`, and its runs are `extraction_runs` rows with `route` `extract_people`.
    - When `extract` returns `False`, `extract_lead` returns `False`.
    - An unknown or merged Lead logs `extraction_skipped`, enqueues nothing and returns `True`.
    - After its commit, an `ok` run logs `people_extracted` with IDs and counts only: `lead_id`, `run_id`, `pages_sent`, `pages_truncated`, `people`, `contacts_created`, `not_decision_makers`, `stored`, `confirmed`, `superseded`, `retracted`, `discarded` and `lawyers`. Every other run logs `extraction_finished`, as in 2.9.
- **Selection: `Research` (`app/research/api.py`).**
  - **`@command select_primary(lead_id) -> PrimaryChoice`.**
    - It reads the Segment, the Lead's Contacts and its current Signals.
    - The strongest Event's `preferred_roles` come from the quotes of its live linked Evidence.
    - It builds `PrimaryCandidate`s. `departed` means the Contact has `extract_people` Evidence and none of it is live.
    - It calls `choose_primary`, then writes only what changed:
      - clear the old flag, then set the new one (`primary_set_by`);
      - `no_decision_maker = (choice is None)`;
      - one `events` row per change (Decision 11).
  - **`@command make_primary(lead_id, contact_id)`.**
    - It raises `PrimaryRefused` before any write.
    - Otherwise the Contact becomes Primary with `operator`, any other Primary is cleared, and `no_decision_maker` is false.
  - **`reparent_lead`.** `move_to_lead(clear_primary=True)` clears `primary_set_by` too, and the survivor's selection runs again after the move.
- **`PrimaryContacts(clock, research)`** (`app/research/people.py`).
  - `@use_case select_primary_contact(lead_id)`: an unknown or merged Lead logs `primary_selection_skipped`.
  - `@use_case make_primary(lead_id, contact_id)`.
  - **Job type `select_primary_contact`:**
    - lanes: bulk by default, or interactive;
    - priority 45, `max_runtime` 5 min, retry limit 3;
    - group `enrichment`;
    - `handlers.select_primary_contact(primary)` checks the `lead_id` ULID.
- **The Lead page.** The Contacts section moves into `lead_contacts.html` (`id="lead-contacts"`).
  - **The Primary line:** "Primary Contact: <name>" with "(chosen by you)" or "(chosen by the engine)"; "Primary Contact: no decision-maker found" when `no_decision_maker` is true; otherwise no line.
  - **Make primary.** Each row except the operator's Primary has a "Make primary" form posting to `POST /leads/{lead_id}/primary` (`contact_id`), with `@personal_data(True)`, `hx-post`, `hx-target="#lead-contacts"`, `hx-swap="outerHTML"` and `csrf_field()`.
  - **The POST** answers 200 with the panel and a notice. An unknown Lead answers 404. The notices:
    - "Primary Contact set.";
    - `merged_lead`: "This Lead was merged into another Lead.";
    - `not_on_lead`: "That Contact isn't on this Lead.";
    - `address_invalid`: "That Contact's address is Invalid."
  - **Untrusted text** sits in `hx-disable` cells, never around the forms.
  - **Evidence.** A person Evidence row's Supports reads "Contact: <name>".

**Never:**
- No real Claude request in tests (`ScriptedLlm`).
- No edit to the prompts, schemas, `SHIPPED_CANDIDATES` or the gateway.
- No `contacts` write outside `research`, and no Contact created except through `add_contact` (Story 3.4 gates it).
- No email finding (2.13), job postings (2.11) or scoring (2.15).
- No Contact folding after a merge (Decision 13).
- No name, role, quote, URL or place in a log line, `events` row, error or job payload.

### Decisions made overnight (pending user review)

1. **Pages.** When any team, about or contact page changed, all of them are sent, so the lawyer count covers every listing page.
2. **A second budget check** comes before the people call. A refusal defers the whole job. On the re-run, the Signals step refuses first, or finds no changed page and makes no call.
3. **Validation, in order:**
   1. `block_id` is a page sent;
   2. the quote, normalised, is 1 to 300 characters and passes `quote_in` against the page's full text;
   3. the name and the role, each `quotes.normalise`d, pass `check_name` and `check_role`;
   4. each is a case-sensitive substring of the normalised quote.

   A repeat of the same page and `normalize_name` key is dropped silently.
4. **Office from a quote.** The office is read with `geo.place_in` on the person's own quote, and kept only when:
   - its country is the Lead's country, or
   - both have enabled Markets and the place's is strictly stricter (`contact_market.strictness`).

   A misread can therefore never loosen a Contact's rules.

   **3.2's item E:** `place_in` never gives a US office without a state, so no refinement is built, and a stored office is never changed.
5. **`role_match`.**
   - **Normalisation.** Casefold; `&` becomes "and"; split on non-alphanumerics.
   - **Canonical forms:**
     - `chief operating/executive/financial (or finance) officer` → `coo`/`ceo`/`cfo`;
     - `vice president` and `v p` → `vp`; `svp` → `senior vp`; `evp` → `executive vp`; `avp` → `assistant vp`;
     - `ops` and `operation` → `operations`;
     - `financial` → `finance`.
   - **Dropped words:** `of`, `the`, `and`, `for`, `at`, `a`, `an`.
   - **Variants.** A role also matches in its form swapped around its first `of` or comma (`Director of Finance` → `finance director`).
   - **A title matches** when its canonical tokens appear contiguously in a variant.
   - **Never a match:** a role holding `assistant`, `deputy`, `former`, `retired`, `emeritus`, `intern`, `secretary`, `pa`, `ea`, `ex` or `to`.
   - **Ranks.** The best rank wins. Each `decision_maker_roles` rank is its index; a title found only in `preferred_roles` ranks `len(ranks)`.
   - **Lawyer roles:** `attorney`, `lawyer`, `partner`, `associate`, `counsel`, `solicitor` and `barrister` (singular or plural), never with `assistant`, `secretary` or `paralegal`.
6. **The lawyer count.**
   - It is stored only for a Segment whose `size_range.unit` is `lawyers`, only when a `team` page was sent and only when the count is at least 1. A zero would falsely disqualify the Lead.
   - It counts distinct `normalize_name` keys among every kept person, whatever their role.
   - The row: `type` `count`, `sought` `LAWYERS_LISTED`, `number`, `pages_checked` the URLs sent, `source` `website`, `source_url` NULL, `retrieved_at` the latest `fetched_at` sent, `extracted_by` `rule`, `fact` NULL.
   - A live row with the same number and pages is confirmed; otherwise the new row supersedes it. No count leaves it alone.
7. **Columns without a CHECK.** `primary_set_by` and `no_decision_maker` are added in place to avoid a `contacts` rebuild while other tracks change it. A later rebuild may add the CHECKs.
8. **Candidates** need a `role_match`. They must not be departed (gone from a re-crawled page), and their `address_class` must not be `invalid` (terminal).
9. **`choose_primary`, in order:**
   1. An operator's Primary whose address isn't `invalid` stays.
   2. If the preferred roles are non-empty, the eligible Contact matching the earliest-listed preferred role wins.
   3. Otherwise the best rank wins.

   Ties go to the earliest found (`created_at`, then `id`). With no candidate: no Primary.
10. **`strongest_event`.** Only live Events count. Ties go to the lower Tier, then the younger, then the earlier `first_seen_at`, then the id. An Event has preferred roles only when one of its `when_terms` appears as a whole phrase (casefolded) in a live quote of that Event.
11. **One `events` row per Primary change:**
    - entity `leads`, the Lead's id;
    - `to_status` `primary_contact_set` (`cause_ref`: the Contact) or `primary_contact_cleared`;
    - cause `selection` or `operator`;
    - actor `system:research` or `operator`.
12. **The operator** may pick any Contact on the Lead except one with an `invalid` address. Picking the engine's Primary pins it.
13. **Merges.** The survivor's selection re-runs. Folding duplicate Contacts isn't in this story's ACs and goes to `deferred-work.md`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected |
|---|---|---|
| People | Law Lead: team page names "Jane Roe, Managing Partner, Austin, TX", an associate and a paralegal | Jane becomes a Contact (office US/TX), with Quote Evidence and Provenance; a count of 2 lawyers; `select_primary_contact` queued |
| Refused | a quote not in the page; a name absent from its quote | nothing stored; one `person_discarded` row each |
| Re-crawl | Jane is gone from the changed team page | her Evidence is retracted; she is departed; selection picks another Contact or none |
| AP/AR | Ops Lead: a COO and a Controller; the strongest Event quote "Accounts Payable Clerk" | Controller is Primary |
| None | no Contact matches | no Primary; `no_decision_maker` true; the page says "no decision-maker found" |
| Operator | Mac makes the CEO Primary; a COO appears later | the CEO stays; the COO wins only once the CEO's address is `invalid` |
| Refusals | Make primary with another Lead's Contact; an Invalid address | 200 with the refusal; nothing written |
| Budget | the people check refuses | `DEFERRED`; no people run |

</frozen-after-approval>

## Code Map

- `nextron/app/research/extraction.py`: `SignalExtractions.extract_lead`, `_prepare`, `_record` and `_finished` are the pattern to mirror, and the module docstring. `_in_scope` filters by the `extract_signals` actor, so person rows stay out of its scope.
- `nextron/domain/extraction.py`: `allot`, `SentPage`, `reconcile` (copy its rules), `PAGE_TEXT_BUDGET`, `WEBSITE_BLOCK` and `ExtractionOutcome`.
- `nextron/domain/quotes.py` (`normalise`, `quote_in`), `domain/geo.py` (`place_in`), `domain/contact_market.py` (`strictness`), `domain/markets.py` (`market_for_country`), and `domain/contacts.py` (`check_name`, `check_role`, `ContactCandidate`). `normalize_name` comes from `domain/identity.py`.
- `nextron/domain/segments.py`: `SegmentDef.decision_maker_roles`, `titles()`, `SignalType.preferred_roles`, `SizeUnit.LAWYERS` and `size_range`. The seed is `seed/segments.yaml`: Law has `[Managing Partner]`, `[Owner]`; Ops has `[COO, VP Operations]`, `[Owner, CEO]` and the preferred `[Controller, CFO, Finance Director]`.
- `nextron/app/research/api.py`:
  - `Research.add_contact` (unchanged);
  - `_resolve`;
  - `reparent_lead` (~480);
  - `WebsiteCrawls._apply` (the enqueue pattern with `lead_job_keys`).
- Repositories:
  - `db/repos/research/contacts.py`: keep `StoredContact` and `_COLUMNS` in step, adding `primary_set_by` after `is_primary`; add `set_primary`/`clear_primary`; update `move_to_lead`.
  - `db/repos/research/signals.py`: `last_ok_run` gains `route`; `insert_evidence` already takes `person_contact_id`.
  - `db/repos/research/profile.py`: `no_decision_maker` and `set_no_decision_maker`.
  - `app/queries/research.py`: `last_ok_extraction` passes the Signals route; add `no_decision_maker(lead_id)`.
- Models:
  - `db/models/research.py` (`ContactRecord`, plus the `Index(..., unique=True, sqlite_where=text("is_primary = 1"))`) and `db/models/leads.py`;
  - `owners.py` and `pii_registry.py`;
  - the 0022 `add_column` pattern.
- `schema_differences` (`tests/contracts/test_job_contracts.py`) compares indexes and CHECKs, so the model's index must compile exactly as the migration creates it.
- **Head bumps** to `0023_2_10_primary_contact`:
  - `tests/contracts/test_persistence_contracts.py` (`HEAD`, the `0023_*` probes → `0024_*`);
  - `tests/unit/test_migrations.py`;
  - `tests/app/test_serve_process.py:296`;
  - `tests/unit/test_serve.py` (`0023_more` → `0024_more`);
  - `test_source_tree.FILES`.
- **Jobs:**
  - `jobs/handlers.py` (copy `extract_lead`);
  - `main.build_job_types(..., primary=)` and every caller (grep `build_job_types(`);
  - `test_job_contracts` names, specs and shapes;
  - `test_health_page.py:285` and `test_engine_health.py:329` (the Enrichment group);
  - `test_health_contracts`;
  - `main.build_app` (~848: build `PeopleExtractions` and `PrimaryContacts`; pass them to `SignalExtractions` and `create_app`).
- **Tests to adapt:**
  - `tests/app/test_signal_extraction.py`: three `SignalExtractions(` constructions. `ScriptedLlm` raises when its script is exhausted, so 2.9 tests whose pages include team, about or contact kinds need an extra `LlmOk` people answer.
  - `tests/dashboard.py`, the only `create_app` caller.
  - `tests/contracts/test_extraction_contracts.py`: add the `extract_people` budget class.
- **Web:**
  - `web/app.py` `lead_page` (~993) and the new POST;
  - `web/leads_page.py` (~435, `primary: YES/NO` at ~577; `lead_context`);
  - `templates/lead.html` and the new `lead_contacts.html`;
  - the Lead page tests in `tests/unit/test_leads_page.py`.
- **Docs:** `nextron-engine/README.md`: a "Decision-makers and the Primary Contact" section after "Signals and Evidence", the migrations row and the jobs list.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/contacts.py`, `domain/signals.py`, `domain/people.py` -- the pure rules above -- with `tests/unit/test_role_match.py` covering every AC title and each Segment, `test_people_domain.py`, `test_primary_choice.py` and `test_signals_domain.py` (`strongest_event`, `preferred_roles`).
- [x] `nextron/db/alembic/versions/0023_2_10_primary_contact.py`, models, `owners.py`, `pii_registry.py`, and the repositories and queries above -- the columns and the index.
- [x] `nextron/app/research/people.py` (`PeopleExtractions`, `PrimaryContacts`), `extraction.py` and `api.py` -- the people step, selection, `make_primary` and the reparent.
- [x] `nextron/jobs/handlers.py`, `nextron/main.py` -- the job type and the wiring.
- [x] `nextron/web/app.py`, `web/leads_page.py`, `templates/lead.html`, `templates/lead_contacts.html` -- the panel, the POST and the person-Evidence label.
- [x] Tests:
  - `tests/app/test_people_extraction.py`: every matrix row through `ScriptedLlm` on pages a real crawl stored, the blocks sent, the second budget check, the Contacts' Market from an office, the select enqueue and the 2.9 route split of `last_ok_run`;
  - `tests/app/test_primary_contact.py`: selection, `make_primary`, the unique index raising `IntegrityError`, the events rows and the reparent;
  - the Lead page and POST tests (escaped, CSRF);
  - the contract and head updates.
- [x] `nextron-engine/README.md`, `_bmad-output/implementation-artifacts/deferred-work.md` (Decision 13's fold) -- docs.

**Acceptance Criteria:**
- Given the full suite, when `pytest` and `lint-imports` run, then everything passes (`ai_eval` skipped) and every contract is kept.
- Given `grep` over `nextron/`, when it looks for `contacts` writes, then only `db/repos/research` holds them, and only `app/research` calls them.

## Spec Change Log

- **Review pass 1, 2026-11-18: overnight refinements to Decisions 3, 5 and 6, pending user review.**
  - **Triggers:** entries A–E of the Review Triage Log.
    - Junior titles match: "Credit Controller" and "Product Owner".
    - Common variants are missed: "Chief Operations Officer".
    - The model can return the role "Managing Partner" from "Assistant to the Managing Partner".
    - A name passes as a substring of another name: "Ann Lee" inside "Joann Lee".
    - The lawyer count includes retired partners and marketing associates, and is taken from cut pages.
  - **Amended (code, not the frozen text):**
    - Decision 5: the single-word titles `controller` and `owner` don't match when a listed qualifier is the word just before them. `chief operations officer` becomes `coo` and `chief executive` becomes `ceo`.
    - Decision 3: the name and role must sit on word boundaries in the quote.
    - A person whose role, as written in the quote, is qualified by a "never" word just before it is no decision-maker and no lawyer.
    - Decision 5's lawyer veto gains `retired`, `former`, `emeritus`, `ex`, `summer`, `intern`, `marketing`, `business`, `development`, `relations` and `client`.
    - Decision 6: no count when any page sent was cut by the page budget.
  - **Avoids:** junior or non-practising people becoming Contacts or the Primary Contact, and a count that falsely fits or disqualifies a firm.
  - **KEEP:** the rest of Decisions 3, 5 and 6 as built: the ranks, the swap variants, the existing word lists, and the count only for a `lawyers` Segment with a `team` page and at least 1 lawyer.

## Review Triage Log

**Pass 1 (2026-11-18).**
- **Sources:** BH = blind hunter (14 findings), EC = edge-case hunter (16), VG = verification gap (2 gaps and 2 other findings).
- **Counts:** 34 raw findings, which make 22 entries after deduplication.
- **Outcomes:**
  - 10 entries patched: A–H, plus two test gaps (P and Q);
  - 2 deferred: K and O;
  - 10 rejected: I, J, L, M, N, R, S, T, U and V.
- **No loopback.** A–E refine overnight Decisions 3, 5 and 6 in the conservative direction (fewer false decision-makers and lawyers). They are logged in the Spec Change Log for Mac instead of re-deriving the code.
- **Rejected only because the fix would change the approved spec (listed for Mac):**
  - J: selection is enqueued only by `extract_lead` and merges, so Leads without a crawled website, or whose job died, are never selected;
  - L: there is no "let the engine choose" for a pin, and a pinned departed Contact isn't flagged;
  - N: a stale stored role (CSV "Partner") is never replaced by the website's "Managing Partner";
  - R: a merge sets `no_decision_maker` before research;
  - U: a matched Contact gets no website Provenance Record;
  - V: preferred-only finance titles can be Primary without an AP/AR Event.

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| A | BH2, EC2 | "Credit/Document/Stock/Quality Controller" match the Ops preferred title Controller; "Product/Process/Account Owner" match Owner in both Segments. | medium | Verified: `_contains` matches the one-word title anywhere in a variant. With an AP/AR Event the preferred-role loop makes a Credit Controller Primary over the COO. | patch (qualifier denylist for `controller` and `owner`) |
| B | BH3, EC3 | "Chief Operations Officer" and "Chief Executive" aren't read as COO/CEO; UK "Managing Director", "Operations Director" and "President" aren't decision-makers. | medium | Verified: `PHRASE_FORMS` lacks both forms. The UK titles carry no fixed meaning ("Managing Director, Sales"); they belong in the seed (`seed/segments.yaml`), which Mac edits without code. | patch (the two forms); the UK titles go to `deferred-work.md` |
| C | EC1 | The role "Managing Partner" passes for the quote "Jane Roe, Assistant to the Managing Partner"; "Ann Lee" passes inside "Joann Lee". | medium | Verified: `_cause` checks only `role in quote`, and `NEVER_WORDS` checks the model's role string only. AD-18: model output is a proposal. | patch (word boundaries, and a never-word just before the role in the quote) |
| D | BH5, EC4 | `is_lawyer_role` counts "Retired Partner", "Partner Emeritus", "Marketing Associate" and "Partner Relations Manager". | medium | Verified: the veto set is only assistant/secretary/paralegal. An overcount skews 2.15's size disqualifiers. | patch (veto words) |
| E | BH4, EC8 | A lawyer count is stored from a team page cut by `allot`. | medium | Verified: `_lawyers` ignores `pages_truncated`. An undercount is stored with every page "checked". The zero-count rule shows the same intent. | patch |
| F | BH12, EC7 | After a merge the survivor can hold two live `lawyers listed` counts; `_lawyers` supersedes only `live[0]`. | low | Verified: `signals_repo.reparent` moves both rows. Direct fix. | patch |
| G | BH9, EC13, EC14 | A merge clears the merged Lead's operator Primary whenever the survivor has any Primary (even the engine's). The merge's clears write no `events` row. | medium | Verified: `move_to_lead(clear_primary=survivor_has_primary)`. The AC says an operator's choice is overridden only for an Invalid address or a removed Contact. | patch |
| H | BH13a | "Make primary" shows on a Contact with an Invalid address, and is always refused. | low | Verified: `can_make_primary` excludes only the operator's Primary. Direct fix. | patch |
| I | BH13b | After a `merged_lead` refusal the panel re-renders empty, with no link to the survivor. | low | True, but only for a page left open across a merge. The notice says why. An `HX-Redirect` is new behaviour. | reject |
| J | BH6, EC11, EC12 | Leads with only CSV Contacts, an unreachable crawl or no website, or whose `extract_lead` died (a non-retryable people error included), are never selected. | medium | True. The AC enqueues selection from `extract_lead`, and the spec's "any outcome but a raise" excludes the raise. Stories 2.13 and 2.16 add Contact paths. | reject (spec-mandated) + `deferred-work.md` |
| K | BH7 | Nothing re-runs selection when an address turns Invalid, the strongest Event ages out or a Contact gains a role outside a crawl. | medium | True. Those writers are Stories 2.13–2.15. | defer |
| L | BH8 | A pin can't be handed back to the engine, and a pinned Contact gone from the website isn't flagged. | low | True. Decision 9.1 keeps an operator's Primary unless its address is Invalid; an un-pin action is new surface. | reject (spec-mandated) + `deferred-work.md` |
| M | BH10, EC10 | When the people step defers or raises after a Signals run that wasn't `ok`, the re-run repeats the Signals call. After an `ok` one, it adds a `no_pages` row. | low | True (Decision 2's text is optimistic for the non-`ok` case). Rare: it needs both a non-`ok` Signals answer and a deferred or failed people call, and it costs one Haiku call per retry. The fix adds a per-crawl-run guard. | reject + `deferred-work.md` |
| N | BH11, EC6 | Selection reads the stored role, which `add_contact` never overwrites, so a CSV "Partner" stays non-decision-maker though the site says "Managing Partner". | medium | True. Stories 2.3 and 3.2 fix "filled once, never overwritten"; changing it changes approved specs. | reject (spec-mandated) + `deferred-work.md` |
| O | BH1 | Once merged with Story 3.4 on main, `add_contact` may return `contact_id=None` (blocked). `_contacts` would then store person Evidence with no Contact, and `reconcile_people`'s sort could compare `None` with a `str`. | maybe-false (medium if true) | This tree has no Story 3.4; `AddedContact.contact_id` is a `str` here. Settled at merge by reading 3.4's `AddedContact`: the people step must then skip a blocked person (no Evidence, counted apart). | defer (merge instruction for the main session) |
| P | VG1, VG other 1 | The app never tests a person's quote changing on a re-crawl (the supersede write, `EvidenceChanged`, "departed" staying false), and the "departure" test only invalidates an address. | medium | Pre-verified gap. | patch (test) |
| Q | VG2 | No test puts a non-live quote on the strongest Event, so dropping `row.live` in `_preferred_roles` passes. | medium | Pre-verified gap. | patch (test) |
| R | VG other 2 | A merge re-runs selection, so a never-extracted survivor can read "no decision-maker found". | low | True by Decision 13. The text is accurate for its stored Contacts. | reject (spec-mandated) |
| S | EC5 | "University of Texas" or "Admitted in Texas" in a person's quote gives a Texas office. | low | True, but only within the Lead's country (Decision 4 drops a looser foreign place), so only the timezone shifts. An office-cue heuristic is new complexity. | reject |
| T | EC9 | A big team page can truncate the `extract_people` answer on every run. | low | The gateway retries once at double `max_tokens`. Firms that large are disqualified (more than 50 lawyers). Story 2.9's deferred entry on truncation covers splitting. | reject |
| U | EC15 | A Contact already on the Lead (CSV) and confirmed by the website gets no website Provenance Record. | low | True: Story 2.3's rule (nothing filled, nothing written; its rejected item). The person Evidence carries the page URL and retrieval time. | reject (spec-mandated) |
| V | EC16 | A preferred-only finance title (Controller, CFO, Finance Director) can be Primary with no AP/AR Event when no ranked title exists. | low | True by Decision 5 (rank `len(ranks)`) and Story 2.1's note that `titles()` keeps them as Contacts. | reject (spec-mandated) |

## Implementation Notes

Built as specified. What the spec left open, and where the code had to choose:

- **`nextron/domain/contacts.py`.**
  - Besides the spec's names it holds `PrimaryRefusal` (the four reasons), `NO_PRIMARY`
    (`PrimaryChoice(None, None)`) and `INVALID_ADDRESS` (`"invalid"`, restating
    `AddressClass.INVALID`: `domain.address_class` imports this module, so importing it back
    would be a cycle; a unit test pins the two equal).
  - Role normalisation: casefold, `&` to ` and `, then the alphanumeric runs (and commas, for
    the swap). Single-word forms first (`financial`, `ops`, `operation`, `svp`, `evp`, `avp`),
    then the phrases, so "Chief Financial Officer" reads `cfo`. The "never" words are checked
    on the canonical words, so an `AVP` is an assistant. The swap needs words on both sides of
    the first `of` or comma. A title is normalised the same way (no variants).
  - `role_match` walks the ranked titles in rank order, then `titles()`' preferred-only titles
    (rank `len(ranks)`); the first match is the best. `role_match` raises `TypeError` for a
    non-`SegmentDef`, and a non-`str` role raises it too.
  - `is_lawyer_role` matches whole words (singular or plural); `assistant`, `secretary` and
    `paralegal` (singular or plural) veto.
  - `choose_primary` checks its arguments (`TypeError` for a non-candidate, or a bare `str` as
    `preferred_roles`). The operator's Primary stays whatever its role or departure (Decision
    9.1 names only the Invalid address); a stored `primary_set_by` the domain doesn't know reads
    as `None`, so the engine may replace that Primary.
  - `PrimaryRefused`'s message is `the Primary Contact wasn't set: <reason>`.
- **`nextron/domain/signals.py`.** `strongest_event`'s key is `(-score, tier, age, first_seen_at,
  signal_id)`. `preferred_roles` normalises (`quotes.normalise`) and casefolds both the quotes
  and the terms; a whole phrase has no letter or digit (`str.isalnum`) just before or after it,
  so `AP/AR` matches in "an AP/AR specialist" but not in "xAP/AR"; roles keep the entries' order,
  each once (case-insensitive).
- **`nextron/domain/people.py`.** Also `SELECT_PRIMARY_CONTACT_JOB`, and the values `Person`,
  `PersonDiscard`, `PeopleValidation`, `LivePersonEvidence`, `PersonQuote`,
  `PersonSuperseding` and `PeoplePlan` (names, roles, quotes and text never in a `repr`).
  `validate_people` normalises the name, role and quote before every check; a `block_id` that
  isn't a page sent is checked first, with `page_id` `None`. An answer without the schema's
  shape raises `ValueError`. `office_from_quote` keeps a place in the Lead's country whatever
  the Markets; with no Lead country it keeps nothing. `reconcile_people` groups by (URL,
  Contact id) and walks the groups sorted by URL, then Contact id.
- **Migration `0023_2_10_primary_contact`** (down `0022_2_9_signals_evidence`). `primary_set_by`
  is `String(8)` and `no_decision_maker` a `Boolean` with no CHECK (SQLAlchemy 2 creates none
  for a Boolean), both through `batch_alter_table(...).add_column`, so SQLite adds them in place
  (at the end of each table; the models list them last too). The index is created after the
  columns with `sqlite_where=sa.text("is_primary = 1")`, and the model's
  `Index(..., unique=True, sqlite_where=text(ONE_PRIMARY_WHERE))` compiles to the same SQL.
- **Repositories.** `contacts.set_primary(contact_id, set_by, updated_at)` and
  `clear_primary(contact_id, updated_at)` write `is_primary` and `primary_set_by` together and
  touch `updated_at`; `move_to_lead(clear_primary=True)` nulls `primary_set_by` too.
  `profile.no_decision_maker(lead_id)` / `set_no_decision_maker(lead_id, value)`.
  `signals.last_ok_run(lead_id, route)`.
- **`nextron/app/research/extraction.py`.** `_Recorded`, `_outcome_of` and `_page_order` became
  the public `RecordedRun`, `outcome_of` and `page_order` (the people step reuses them). The
  people step is typed by a `PeopleStep` protocol, so this module doesn't import
  `app/research/people.py` (which imports it). `extract_lead` returns
  `people.extract(lead_id, run_id)` after phase 1's recorded run, after a non-`ok` result and
  after a stored `ok` run; it returns `True` without the people step after phase 3's
  `lead_merged` run ("a merged Lead" in the spec's exceptions), and after an unknown or merged
  Lead's skip.
- **`PeopleExtractions`** (`nextron/app/research/people.py`).
  - `crawl_run_id`, the run fields and the log lines follow Story 2.9; every log line is written
    after its transaction commits. A failed run followed by a raise (`AdapterError`) enqueues no
    selection (the spec's "any outcome but a raise"); every other recorded run does, a
    `lead_merged` one included (the job then logs `primary_selection_skipped`).
  - `people` in `people_extracted` counts the kept people; `lawyers` is the count stored or
    confirmed in this run, else 0.
  - A confirmed person row's `last_confirmed_at` is `max(old, page fetched_at)`, as in 2.9. A
    live count with the same number and the same set of pages is confirmed at `max(old, latest
    fetched_at)`; otherwise the new count supersedes the first live `rule` count of
    `LAWYERS_LISTED`, or is inserted. `EvidenceChanged` goes out after the writes: the
    superseded person rows, then the superseded count, then the retracted rows.
- **`Research`** (`app/research/api.py`).
  - `select_primary` raises `LookupError` for an unknown Lead and `ValueError` for a merged one
    (`PrimaryContacts` checks both first). Event Signals whose type the Segment no longer lists
    don't count for the strongest Event; the strongest Event's quotes are those of its live
    linked Evidence.
  - One `events` row per Primary change: a new Primary (or a new `primary_set_by` on the same
    Contact, as when the operator pins the engine's choice) is one `primary_contact_set` row
    with `cause_ref` the new Primary; a Primary cleared with no replacement is one
    `primary_contact_cleared` row with `cause_ref` the Contact cleared (the spec gives
    `cause_ref` only for the set; an id is the useful audit, and no text is stored).
    `from_status` is `None`.
  - `make_primary` on the operator's own Primary writes nothing; `no_decision_maker` is only
    written when it differs.
  - `reparent_lead` re-runs the survivor's selection after all its moves (Contacts, crawl rows,
    Evidence and Signals), as `select_primary` does; the merged Lead's `no_decision_maker` stays.
- **`PrimaryContacts`.** `select_primary_contact` returns the `PrimaryChoice` (or `None` when
  skipped) and logs `primary_contact_selected` (`lead_id`, `contact_id`, `set_by`);
  `make_primary` logs `primary_contact_made` (`lead_id`, `contact_id`) after its commit.
- **Jobs and wiring.** `handlers.select_primary_contact(primary)` with a `SelectingPrimary`
  protocol; `build_job_types(..., primary=)` and every caller (the test files,
  `tests/dashboard.py`, `test_backup_contracts`' and `test_job_contracts`' service maps).
  `build_app` builds `PeopleExtractions(clock, meter, llm, research, queue)`, passes it to
  `SignalExtractions`, and builds `PrimaryContacts(clock, research)` for the job and
  `create_app(..., primary_contacts=...)`.
- **The Lead page.** `lead_contacts.html` is the panel (`<section class="lead-contacts"
  id="lead-contacts">`), included by `lead.html`. The Contacts table gains an "Action" column;
  every untrusted cell (name, role, email, Address Class, LinkedIn, the Provenance row) carries
  `hx-disable`, the Primary and Action cells don't. The form is `method="post"`, `action` and
  `hx-post` `/leads/{lead_id}/primary`, `hx-target="#lead-contacts"`, `hx-swap="outerHTML"`,
  `hx-sync="this:drop"`, `csrf_field()`, a hidden `contact_id` and a "Make primary" button. The
  Primary line's name is the Contact's name, else its email, else "—"; a Primary with no
  `primary_set_by` reads "(chosen by the engine)". The POST reads exactly `contact_id`, once
  (`MalformedForm` otherwise: 400), and answers an unknown Lead with a plain-text 404 "That Lead
  doesn't exist." (HTMX shows a 4xx body as text, and it never echoes the id). A person
  Evidence row's Supports is "Contact: <name>" (else the email, else "—").
- **Tests.** `tests/unit/test_role_match.py`, `test_primary_choice.py`, `test_people_domain.py`,
  `test_signals_domain.py` (`strongest_event`, `preferred_roles`); `tests/app/test_people_extraction.py`
  and `test_primary_contact.py` (they reuse Story 2.9's `World` from
  `tests/app/test_signal_extraction.py`, whose `extract` now runs the people step on its own
  `ScriptedLlm`, answering no people by default, with `runs`/`calls` kept to the Signals route);
  the Lead page tests in `tests/unit/test_leads_page.py`; `test_extraction_contracts` (the
  `extract_people` budget class and the `Person` fields); `test_web_contracts` (the route lists);
  the head bumps, `FILES` and the job-type lists.

**Implementation verification** (targeted; the full suite is the orchestrator's gate): every
touched test file with `tests/contracts`, 2,348 passed and 1 skipped (the opt-in `ai_eval`
check) before two fixes (`lead_contacts.html` added to `test_web_contracts`' template list;
`test_lead_import`'s merge test now sets operator Primaries, since a merge re-runs the
selection), then those two files, 226 passed; 12 neighbouring files (the app builders, merges,
geography and extraction domain), 1,603 passed. `lint-imports`: 29 kept, 0 broken. No real
Claude request was made.

**Review fixes (pass 1):**

- **A. One-word titles.** `contacts.TITLE_QUALIFIERS` (read-only) maps `controller` and `owner`
  to their junior qualifiers; a title whose canonical words are just that word doesn't match
  where the variant's word just before it is one of them ("Credit", "Document", "Stock",
  "Quality" Controller; "Product", "Process", "Account" Owner, ...). "Financial Controller",
  "Founding Partner & Owner", "Co-Owner" and "Business Owner" still match.
- **B. Canonical forms.** `chief operations officer` → `coo` (so "Chief Ops Officer" too) and
  `chief executive` → `ceo`, after `chief executive officer`. "Deputy Chief Executive" never
  matches.
- **C. Whole words, and a role qualified away.** `validate_people` finds the name, then the role,
  in the normalised quote only with no letter or digit just before or after it (same causes).
  `people.qualified_away(person)` is true when one of the up to three words before the role's
  first whole occurrence (read with the new `contacts.role_words`: the canonical forms, commas
  left out, the dropped words kept) is in `contacts.NEVER_WORDS`;
  `people.decision_maker(person, segment)` is `None` then, else `role_match`. `_contacts` uses
  `decision_maker`, and `lawyer_count` skips a person qualified away.
- **D. The lawyer veto** (`NOT_LAWYER_WORDS`) adds retired, former, emeritus, ex, summer, intern,
  marketing, business, development, relations and client.
- **E. Cut pages.** `_lawyers` stores, confirms and supersedes no count when
  `call.pages_truncated > 0`.
- **F. Every live count.** `_lawyers` returns the ids it superseded and retracted: a new count
  supersedes every live `rule` count of `LAWYERS_LISTED`; a confirmed one retracts every other.
  Each gets `EvidenceChanged`, and the log line's `superseded` and `retracted` include them.
- **G. Merges.** `reparent_lead` reads both Primaries first. When the merged Lead's was chosen by
  the operator and the survivor's wasn't, the survivor's is cleared (before the move, for the
  unique index) and the moved one kept; otherwise, when the survivor has a Primary, the moved
  ones are cleared as before. Each Primary cleared appends one `primary_contact_cleared` row
  (the survivor's id, `cause_ref` the Contact, cause `selection`, actor `system:research`);
  `_select` then runs as before. `contacts_repo.has_primary` is no longer called.
- **H. The Lead page.** `can_make_primary` is also false for a Contact whose `address_class` is
  `invalid`.
- **Tests.** `test_role_match` (A, B, D), `test_people_domain` (C: the whole-word cases,
  `qualified_away`, `decision_maker`, the lawyer count), `test_people_extraction` (P: a changed
  line supersedes Jane's Evidence and she stays Primary; C: the assistant isn't stored; E; F:
  both ways), `test_primary_contact` (G: the three merge cases with their rows, replacing the
  old merge test; Q: a superseded AP/AR quote prefers nobody), `test_leads_page` (H).

**Review fixes verification** (targeted): the edited test files and their neighbours
(`test_people_extraction`, `test_primary_contact`, `test_leads_page`, `test_role_match`,
`test_primary_choice`, `test_people_domain`, `test_lead_import`, `test_leads`,
`test_contact_markets`, `test_signal_extraction`, `test_signals_domain`, `test_contact_market`,
`test_contacts_domain`) all pass; `lint-imports`: 29 kept, 0 broken.

**Final verification** (on the final tree, after the review fixes): the full suite through
`suite_lock.py` (track-E) passed on its first run: 10,993 passed and 3 skipped (the two
`ai_eval` tests and the symlink test). `lint-imports`: 29 kept, 0 broken. No real Claude request
was made.

## Verification

**Commands:**
- `cd e:/Nextron-track5/nextron-engine && uv run pytest -q <touched test files> tests/contracts` -- expected: all pass.
- `cd e:/Nextron-track5/nextron-engine && uv run lint-imports` -- expected: all contracts kept.
