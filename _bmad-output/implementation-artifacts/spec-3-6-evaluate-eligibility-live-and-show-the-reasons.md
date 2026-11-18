---
title: 'Story 3.6: Evaluate Eligibility live and show the reasons'
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: '4b78f0c5f34402424923788ced332b5fa0f64ac4'
review_loop_iteration: 0
story_key: '3-6-evaluate-eligibility-live-and-show-the-reasons'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing decides yet whether a Contact may lawfully receive cold email. Story 3.2 stores each Contact's Market, but never re-checks it after a Market edit. Story 3.4's gate answers only "suppressed or closed". FR-18 needs one live Decision that combines the Market rules, the UK legal form, the Address Class, the Suppression List, the re-contact rules and the Lead's status, shows every failing reason, and is never stored. Enrollment (4.8), the queue and the send gate (6.9) will call it.

Requirements: FR-18, FR-2, FR-63 · AD-4, AD-16 · NFR-3, NFR-5, NFR-7 · UX-DR17.

**Approach:**
- A pure core, `nextron/domain/eligibility.py`: `evaluate(contact, lead, market_rules, now) -> Decision`, with a closed reason enum, Operator-facing texts and the FR-63 predicate.
- `Compliance.evaluate_eligibility(contact_id)` gathers the facts through the owners' read APIs and `assert_contactable`, then calls `evaluate`. Nothing is cached or written.
- The Lead page shows each Contact's live Decision.
- Contract tests show that no setting or dashboard route can disable Eligibility, that no Eligibility result is stored, and that only `compliance` calls `evaluate`.

The acceptance criteria are Story 3.6 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**
- **The core** (`nextron/domain/eligibility.py`) uses only the stdlib and `nextron.domain`. Its dataclasses are frozen and slotted. A wrong type raises `TypeError`, a bad value `ValueError`, and neither message holds a value.
  - **`ReasonKind` (`StrEnum`)** is closed. Its members, in this order (the AC's, plus decision 6):

    `no_enabled_market`, `cold_email_not_allowed`, `uk_legal_form_ineligible`, `uk_legal_form_unconfirmed`, `uk_free_webmail`, `address_not_sendable`, `address_or_domain_suppressed`, `asked_not_to_be_contacted`, `recontact_not_allowed`, `not_now_until`, `not_now_undated`, `lead_closed`.
  - **`Reason(kind, text, until=None)`.**
    - `until` is a `date`, set for `not_now_until` only (it is required there and refused elsewhere).
    - `code` is `not_now_until_<YYYY-MM-DD>` for that kind and the kind's value for every other.
    - `text` is non-blank and holds Market names, Address Classes, legal-form labels and dates only. It never holds an address, a name or a domain.
  - **`Decision(eligible, basis_rule, reasons)`.**
    - `__post_init__` enforces `eligible` ⇔ `reasons == ()` ⇔ `basis_rule is not None`.
    - Reasons have distinct kinds, in enum order.
    - `kinds` gives the reasons' kinds.
  - **Facts.**
    - `EligibilityContact(email, address_class, market_id, market_basis, office_country, office_region, contactable, history=())`. The stored texts come as they are stored, `contactable` is an `Allowed` or `Blocked`, and `history` is a tuple of `EndedSequence(last_step_at, ended_at, replied)`.
    - `EligibilityLead(country_code, stage, recontact_on=None, legal_form=None, legal_form_confirmed=False, events_first_seen=())`. `stage` is a `PipelineStage`.
    - Every datetime, `now` included, must be timezone-aware (`ValueError` otherwise).
  - **`market_rules`:** `Mapping[str, MarketDef]` (the Market row id to its current definition), or `None` when the Markets can't be read.
- **`evaluate`** runs every check and collects every failing reason. The Contact is eligible only with no reason.
  1. **The Market** (decision 1). It is `no_enabled_market`, with one text per cause (Design Notes), when:
     - `market_rules` is `None`;
     - `market_id` is `None` (the text follows `market_basis`, and "not resolved yet" when that is `None`);
     - the id isn't in `market_rules`, or its Market isn't `enabled`;
     - the stored office isn't a valid `geo.Location`;
     - or `resolve_contact_market(email=, office=, lead_country=, lead_hq_tz=None, markets=market_rules.values())` gives no Market, or a Market whose `code` differs from the stored one.

     Otherwise the stored Market's **current** definition applies. With no Market, the Market checks below are skipped.
  2. **The Market checks** (decision 3):
     - `cold_email_not_allowed` unless `cold_email_allowed` and the country is `US` or `GB`.
     - **Legal forms** are checked when `eligible_legal_forms is not None` or the country is `GB`:
       - `uk_legal_form_unconfirmed` when `legal_form` is `None` or not `legal_form_confirmed`;
       - `uk_legal_form_ineligible` when `legal_form` is set and not listed.
     - `uk_free_webmail` when `free_webmail_blocked` (or the country is `GB`) and the email is free webmail (`markets.is_free_webmail`).
  3. **The address:** `address_not_sendable` unless there is an email whose Address Class is `published` or `verified`. The text names the class, or says there is no email or no known class.
  4. **Suppression:** a `Blocked` `contactable` maps each `BlockReason`:
     - `address_suppressed`, `address_domain_suppressed` and `lead_domain_suppressed` give `address_or_domain_suppressed`;
     - `person_suppressed` and `profile_suppressed` give `asked_not_to_be_contacted`;
     - `lead_lost` and `lead_disqualified` give `lead_closed`.

     The mapping covers every `BlockReason`.
  5. **FR-63:** `recontact_allowed(history, events_first_seen, now)` is true when every `EndedSequence` without a Reply has its last Step at least 90 days before `now` and some Event first seen in (`ended_at`, `now`]. A false result gives `recontact_not_allowed`.
  6. **Not Now:** a `not_now` stage gives:
     - `not_now_undated` with no `recontact_on`;
     - `not_now_until` when `recontact_on > now`'s UTC date;
     - nothing on or after the date.
  7. **Closed:** `lead_closed` for a `lost` or `disqualified` stage (the text names it).
  8. **The basis rule** when eligible:
     - `US: cold email allowed (CAN-SPAM)`;
     - `UK: corporate subscriber (<legal-form label>), legitimate interests`, with the label from `LEGAL_FORM_LABELS` (Design Notes) or, failing that, the code.
- **`Compliance.evaluate_eligibility(contact_id) -> Decision`** (`nextron/app/compliance/api.py`). It is a plain method (not a `@command`), uses the clock's now and works in a read-only unit.
  1. It reads the Contact (`queries.research.contact`; unknown: `LookupError`), its Lead (`queries.discovery.lead`) and its stage (`queries.pipeline.stage`).
  2. It calls `compliance_api.assert_contactable(ContactSubject(contact_id), "email", "cold")` through the module attribute, then `queries.setup.markets()` (on `InvalidMarkets`, `market_rules=None`).
  3. It builds the facts with `history=()`, `recontact_on=None`, `legal_form=None`, `legal_form_confirmed=False` and `events_first_seen=()` (decisions 4, 6 and 7), and returns `evaluate(...)`.

  It writes, logs and caches nothing.
- **The Lead page.**
  - `create_app` gains `compliance: Compliance`. `main.build_app` and `tests/dashboard.py` pass `Compliance(clock)`.
  - `lead_page` evaluates each Contact on render and passes the Decisions to `leads_page.lead_context`.
  - Under each Contact row, an `eligibility-row` cell shows "Eligible: <basis>" or "Not eligible", then a list of every reason's text, each item carrying `data-reason="<code>"`.
  - It sits inside the existing `hx-disable` table, autoescaped. `leads_page` still imports only `nextron.app.queries.*` from the app.
- **Contracts** (`tests/contracts/test_eligibility_contracts.py`), each with probes:
  - (a) No POST route has `eligib` in its path, name, or endpoint module or qualname.
  - (b) `"eligib"` is among `test_settings_contracts.PROTECTED_STEMS`, and `SCHEMA` passes that check.
  - (c) No table or column name in `Base.metadata` contains `eligib`, except `markets.eligible_legal_forms`.
  - (d) An AST scan, alias-aware: `nextron.domain.eligibility.evaluate` is named outside `domain/eligibility.py` only in `app/compliance/api.py`.
  - (e) `domain/eligibility.py` and `app/compliance/api.py` don't import `settings_schema`, name no `queries.setup` reader but `markets`, and use no `cache`, `lru_cache` or `cached_property`.
- **Docs and tracking.** Add a README section "Eligibility" (after "The contactability gate") and a note in "Leads list and Lead page". Add new files to `test_source_tree` `FILES`.

**Never:**
- No migration, column, setting, job, adapter, port, route, notification or dependency.
- No stored Decision or cache.
- No change to `assert_contactable`, `suppress`, 3.2's resolver or the Market seed.
- No AD-16 manifest entry: this isn't an outreach path.
- No legal-form, Evidence, Enrollment or `recontact_on` reads (Stories 2.12, 2.9, 4.8, Epic 7).
- No `timedelta`, and no name stemmed delete/remove/purge/expire/ttl/prune/evict/truncate/drop, in `app/compliance/api.py` (3.3's permanence contract).
- No edits outside `nextron-engine/`, except this spec, `sprint-status.yaml` and `deferred-work.md`.

### Decisions made overnight (pending user review)

1. **The live Market (3.2's deferral).** The stored resolution counts only when it is confirmed live: the Market is still stored and enabled, and resolving afresh against the current Markets gives the same Market. So:
   - a disabled or changed Market blocks at once;
   - a Contact resolved as blocked stays blocked when a Market is enabled later (3.2 rejected item D), until something re-resolves it.

   Re-resolving after Market edits goes to `deferred-work.md` (Story 11.6 / `seed sync`).
2. **Rules are read live.** Edits apply at once (11.6's AC).
3. **Defence in depth.** A GB Market always checks the legal form and free webmail, and cold email needs a US or GB country as well as the flag.
4. **UK legal form (Story 2.12 isn't built).** No legal-form fact exists, so every UK Contact fails with `uk_legal_form_unconfirmed`, and no UK Contact is eligible until 2.12 passes `legal_form` and `legal_form_confirmed`.
5. **Suppression reasons by key kind.** The gate returns no sources, so address and domain keys map to `address_or_domain_suppressed`, and person and profile keys to `asked_not_to_be_contacted`. An opt-out writes every known key for the person, so it shows both.
6. **`not_now_undated`** is added to the closed enum. A Lead parked `not_now` with no recorded date fails closed. Until Epic 7's `park_not_now` records `recontact_on`, every `not_now` Lead gets this reason. A date that has arrived passes, so `recontact_due` can enroll.
7. **FR-63 facts.** No `enrollments` table exists, so `history=()` is accurate today. Story 4.8 passes the Enrollment history and Story 2.9 the Lead's Event first-seen times. A Sequence that ended with a Reply is outside this predicate (other rules govern it).
8. **`evaluate_eligibility` is a `Compliance` method** because it needs `now`. The dashboard gets `compliance=`. The AD-16 call uses `email`/`cold`.
9. **Unreadable inputs fail closed.**
   - Markets that don't decode, or an office that isn't a valid Location, give `no_enabled_market`.
   - An unknown Address Class text gives `address_not_sendable`.
   - An unknown Contact raises `LookupError`, and a merged Lead the gate's `ValueError`: there is no Decision without a stored Contact.
10. **The basis rule's length.** The UK basis uses a readable legal-form label, so it can exceed 64 characters (`provenance_records.basis_rule` is `String(64)`). Story 3.7 must widen the column.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| US passes | US Market, `published`, `Allowed`, stage `new` | eligible, "US: cold email allowed (CAN-SPAM)" | N/A |
| UK passes (domain) | GB Market, `verified`, corporate email, `ltd` confirmed | eligible, "UK: corporate subscriber (private limited company), legitimate interests" | N/A |
| UK today (app) | a CSV UK Contact | not eligible: `uk_legal_form_unconfirmed` and `address_not_sendable` (Guessed) | N/A |
| No Market | `market_id` NULL, basis `cctld_outside_markets`; then NULL basis | `no_enabled_market` with the basis text; then "not resolved yet" | N/A |
| Market disabled / enabled later | `seed sync` disables US; enables a CA Market for a CA-blocked Contact | `no_enabled_market` for both | N/A |
| Stale resolution | stored US, but a fresh resolution gives UK | `no_enabled_market` (stale) | N/A |
| Unreadable | Markets raise `InvalidMarkets`; office `US`/`ZZ` | `no_enabled_market` ("can't be read") | caught, fail closed |
| Market rules | CA Market `cold_email_allowed` false; GB with `jane@gmail.com`; GB `llp` unlisted | `cold_email_not_allowed`; `uk_free_webmail`; `uk_legal_form_ineligible` | N/A |
| Address | `guessed`, `invalid`, no email, class `None` | `address_not_sendable`, text naming the class or its absence | N/A |
| Suppressed | an active address entry; a person entry; a domain entry | `address_or_domain_suppressed` and/or `asked_not_to_be_contacted` | N/A |
| FR-63 | empty; 91 d + an Event after; 91 d, no Event; 60 d | pass; pass; `recontact_not_allowed`; `recontact_not_allowed` | N/A |
| Not Now | until 2027-01-15 (future); until today; no date | `not_now_until_2027-01-15`; passes; `not_now_undated` | N/A |
| Closed | `lost`, `disqualified` | `lead_closed` (one reason, even with the gate's `lead_lost`) | N/A |
| Several | UK, Guessed, Lost, suppressed | every reason, in enum order | N/A |
| Live | evaluate, suppress, evaluate | the second Decision differs; nothing stored | N/A |
| Lead page | eligible and ineligible Contacts | "Eligible: <basis>" / "Not eligible" + each reason, recomputed per render | N/A |
| Bad input | naive `now`; `contactable=None`; unknown Contact | `ValueError`; `TypeError`; `LookupError` | raised |

</frozen-after-approval>

## Code Map

- `nextron/domain/contactability.py`: `Allowed`, `Blocked` (`.reasons`), `BlockReason`, `ContactSubject`. Unchanged.
- `nextron/domain/contact_market.py`: `resolve_contact_market` (keyword-only; returns `ContactMarket.market`), `MarketBasis`. `nextron/domain/geo.py`: `Location(country_code, region)` raises `InvalidLocation`.
- `nextron/domain/markets.py`: `MarketDef` (`code`, `name`, `enabled`, `country_code`, `cold_email_allowed`, `eligible_legal_forms`, `free_webmail_blocked`), `US`, `GB`, `is_free_webmail`, `_GB_CORPORATE_FORMS` (the eight codes `LEGAL_FORM_LABELS` must cover), `InvalidMarkets`.
- `nextron/domain/address_class.py`: `AddressClass`. `nextron/domain/pipeline_stage.py`: `PipelineStage`, `CLOSED_STAGES`.
- `nextron/app/compliance/api.py`: `Compliance(clock)` with `suppress` and the module-level `assert_contactable` (`_lead_facts`, `_facts`). Add `evaluate_eligibility` and update the module docstring.
  - Mind 3.3's permanence contract (`tests/contracts/test_suppression_contracts.py`): no `timedelta`, no `created_at` comparison and no delete/remove-style names here.
  - Import `queries.setup` (allowed by c6 and c7).
- `nextron/app/queries/{research,discovery,pipeline,setup}.py`: `contact`, `lead`, `stage`, `markets()` (`SeededMarket.id`/`.definition`). Unchanged.
- `nextron/web/app.py`: `create_app` keywords and docstring (~L362–400); `lead_page` (~L993); the module docstring's Lead page bullet (~L113).
- `nextron/web/leads_page.py`: `ContactRow`, `contact_row`, `lead_context`, the module docstring. `nextron/web/templates/lead.html`: the Contacts table.
- `nextron/main.py` `build_app` (~L868 `create_app(...)`); `tests/dashboard.py` (~L354).
- **Tests to copy:**
  - `tests/app/test_contactable.py`: `World`, its helpers `entry`, `set_stage`, `sql` and `counts`, with seeded Markets and Segments.
  - `tests/unit/test_leads_page.py`: `Tree`, `cells`, `table_rows`, `world`, `import_csv`; the Markets seeding at ~L575.
  - `tests/contracts/test_market_contracts.py`: `market_post_routes`, `with_post_route`, `endpoint`, `_names`.
  - `tests/contracts/test_settings_contracts.py`: `PROTECTED_STEMS`, `protected_feature_problems`.
- **Tests likely affected:** `tests/unit/test_leads_page.py` (a new row per Contact) and `tests/contracts/test_source_tree.py`.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/eligibility.py` -- the core per Always, the Design Notes texts and `LEGAL_FORM_LABELS` -- FR-18/FR-63
- [x] `nextron/app/compliance/api.py` -- `Compliance.evaluate_eligibility`; docstring -- the live Decision
- [x] `nextron/web/app.py`, `nextron/web/leads_page.py`, `nextron/web/templates/lead.html`, `nextron/main.py`, `tests/dashboard.py` -- wiring and the eligibility row -- UX-DR17
- [x] `tests/unit/test_eligibility.py` -- table-driven:
  - one case per `ReasonKind`, one passing US, one passing UK, several reasons together;
  - the FR-63 four cases plus the 90-day boundary;
  - the Not Now date boundary;
  - `Reason`/`Decision` validation, `code`, texts free of the address, every `BlockReason` mapped, every GB corporate form labelled.
- [x] `tests/app/test_eligibility.py` -- every matrix row reachable through real units:
  - a Market toggled with `Setup.sync_markets`, an invalid Market row by raw SQL;
  - a recording fake of `assert_contactable`;
  - a read-only unit, nothing written, nothing logged, re-evaluation reflecting changes.
- [x] `tests/unit/test_leads_page.py` -- the eligibility rows, recomputed per render, inside `hx-disable`.
- [x] `tests/contracts/test_eligibility_contracts.py` -- (a)–(e) with probes.
- [x] `nextron-engine/README.md`, `tests/contracts/test_source_tree.py`, `deferred-work.md` -- the docs, `FILES`, and the deferrals (2.12, 2.9, 4.8, Epic 7 `recontact_on`, 3.7 `provenance_missing` and the basis length, 11.6 re-resolution).

**Acceptance Criteria:**
- Given `uv run pytest -q tests/contracts`, when it runs, then every contract passes, including the new ones and their probes.
- Given `uv run lint-imports`, when it runs, then every contract is kept.

## Implementation Notes

- **The core** (`nextron/domain/eligibility.py`) follows the Always list and the Design Notes texts exactly; the texts, `LEGAL_FORM_LABELS`, `BLOCK_REASON_KINDS` (every `BlockReason`), `ADDRESS_CLASS_LABELS`, `CLOSED_STAGE_LABELS`, `US_BASIS`/`UK_BASIS` and `RECONTACT_WAIT` (90 days) are module constants. Reasons are collected per check, then sorted into `ReasonKind` order; `lead_closed` appears once, naming the stage (the Lead's closed stage, else the gate's `lead_lost`/`lead_disqualified`). An eligible Decision's basis comes from `_basis_rule`; a Contact with no reason and no basis (unreachable) would make `Decision` raise rather than pass.
- **Validation beyond the letter of the spec, all fail-closed or type-strict:** `Reason.until` and `EligibilityLead.recontact_on` refuse a `datetime` (a `date` only, `TypeError`); `EligibilityLead.legal_form` must match `markets.LEGAL_FORM_PATTERN` (`ValueError`), so a legal-form text can only ever be a Companies House code; a stored office region without a country counts as an unreadable office (`no_enabled_market`), matching the `contacts` check; a GB Market whose `eligible_legal_forms` is `None` lists nothing, so any legal form there is `uk_legal_form_ineligible`. `EligibilityContact.email` is `repr=False`.
- **`Compliance.evaluate_eligibility`** calls `assert_contactable(...)` as a plain module global (looked up at call time, so `monkeypatch.setattr(compliance_api, "assert_contactable", fake)` sees it, which `tests/app/test_eligibility.py` proves); it passes `ContactSubject(contact.id)` (the stored id). The Markets go to `evaluate` as a read-only `MappingProxyType` `{id: definition}` through `_market_rules()` (`InvalidMarkets` gives `None`). Module docstring updated.
- **Lead page:** `create_app(..., compliance=)`; `lead_page` builds `{contact.id: compliance.evaluate_eligibility(contact.id)}` on each render and passes it as `lead_context`'s new sixth argument `decisions` (a missing Decision raises `KeyError`). `leads_page` gains `ReasonRow`, `EligibilityRow`, `eligibility_row(decision)` and `ContactRow.eligibility`; `lead.html` renders `<tr class="eligibility-row"><td colspan="6"><p class="eligibility-summary">…</p><ul class="eligibility-reasons"><li data-reason="<code>">…</li></ul></td></tr>` right after each `contact-row` (the list only when there are reasons), inside the existing `hx-disable` table. `main.build_app`, `tests/dashboard.py` and their docstrings pass `Compliance(clock)`.
- **Contracts** (`tests/contracts/test_eligibility_contracts.py`) reuse `test_market_contracts`' `_dotted`, `_imported_from`, `_package_of`, `endpoint`, `probe_tree` and `with_post_route`, and `test_settings_contracts`' `PROTECTED_STEMS`, `protected_feature_problems`, `setting` and `probe`. (d) also catches a star import, `getattr(<alias>, "evaluate")` and the dotted string `"nextron.domain.eligibility.evaluate"`; (e) also catches `getattr` on a `queries.setup` alias and any name `settings_schema`.
- **Tests:** `tests/unit/test_eligibility.py` (152 cases), `tests/app/test_eligibility.py` (29), `tests/unit/test_leads_page.py` (+5: eligible and ineligible rows under their Contact row, a Contact without a Market, recomputed per render with no row count changing in any table, a hostile Market name escaped inside `hx-disable`, `eligibility_row` codes), the contracts (46). FR-63's `recontact_not_allowed` and `not_now_until` aren't reachable through the app yet (decisions 6 and 7); the app test checks the facts passed are absent instead.
- **Docs:** README section "Eligibility" (after "The contactability gate", before "Sending Inboxes"), a note in "Leads list and Lead page" (and its "Reads"), and the 3.2 "Not yet" paragraph now points to it. `test_source_tree` `FILES` gains `nextron/domain/eligibility.py`. `deferred-work.md` gains five entries (2.12 legal form; 2.9 Events and 4.8 Enrollments; Epic 7 `recontact_on`; 3.7 `provenance_missing` and `basis_rule` width, every UK basis being 71 to 108 characters; 11.6 re-resolution).
- **Verification (implementer):** `uv run pytest -q tests/unit/test_eligibility.py tests/app/test_eligibility.py tests/unit/test_leads_page.py tests/app/test_contactable.py tests/contracts` 1599 passed, 10 skipped (the AD-16 "not built yet" entries and `ai_eval`); `uv run lint-imports` 29 kept, 0 broken; also `test_dashboard`, `test_untrusted_rendering`, `test_environment_isolation`, `test_health`, `test_credentials_page`, `test_suppression`, `test_contact_markets` 273 passed. The full suite was not run (track rules 4a).
- **Review patches (pass 1).**
  - **A.** `tests/unit/test_leads_page.py` `test_each_contact_on_one_lead_shows_its_own_decision`: one US Lead with an eligible Contact (Published), a Guessed one and one with no email. The Contacts table runs contact-row, eligibility-row, provenance-row for each Contact in order, and each eligibility row shows its own Contact's summary, `data-reason` codes and summary class.
  - **B.** `tests/unit/test_eligibility.py` `test_every_closed_stage_has_a_label`: `set(CLOSED_STAGE_LABELS) == set(CLOSED_STAGES)` and `set(_BLOCK_STAGES.values()) <= set(CLOSED_STAGES)`.
  - **D.** Contract (d) also flags, outside `app/compliance/api.py`, `evaluate` imported from `nextron.app.compliance.api` (a relative import, an alias or `*` included), the attribute `evaluate` on any alias of that module, `getattr(<alias>, "evaluate")` and the dotted string `"nextron.app.compliance.api.evaluate"` (`EVALUATE_MODULES`). Five new probes, and an allowed probe for `compliance_api.Compliance(clock).evaluate_eligibility(...)`. The real tree passes.
  - **E.** `lead.html`'s summary `<p>` carries `eligibility-summary eligible` or `eligibility-summary not-eligible`. `app.css` (edited byte-preserving, LF region after `.provenance-table`) gains `.eligibility-summary.not-eligible` (`var(--refused)`, `font-weight: 700`) and `.eligibility-reasons` (`margin: 0.25rem 0 0; padding-left: 1.5rem`). The page tests pin the class.
  - **F.** `deferred-work.md`'s Epic 7 entry now requires recording the prospect's chosen date or, failing one, FR-63's 90-day default after the Reply, so every `not_now` Lead gets a `recontact_on`.
  - **G.** `EndedSequence.__post_init__` raises `ValueError` ("ended_at must not be before its last_step_at", no values) when `ended_at < last_step_at` (equal is allowed). Module docstring updated, and the case added to `test_bad_facts_are_refused_without_their_values`.
  - **H.** `tests/app/test_eligibility.py` `test_one_real_entry_among_distinct_keys_blocks_with_its_reason`: a US Contact whose address is on `acme-mail.com` (not the Lead's `acmelaw.com`) with a LinkedIn URL. A real entry on only the Lead's web domain gives the gate's `lead_domain_suppressed`, on only the address's domain gives `address_domain_suppressed`, and on the profile gives `profile_suppressed`; the Decisions are `address_or_domain_suppressed`, `address_or_domain_suppressed` and `asked_not_to_be_contacted`.
  - Tests after the patches: `test_eligibility` (unit) 154, (app) 32, `test_leads_page` 49, `test_eligibility_contracts` 52, all passing; `test_notifications_area`, `test_web_contracts` and `test_dashboard` 262 passing.
- **Deferred in review:** the UX-DR17 header Decision (the Primary Contact's, or "no decision-maker found") goes to Story 2.10 in `deferred-work.md` (triage #3).

**Final verification (orchestrator).**
- Before review: the Verification command gave 1599 passed and 10 skipped (`ai_eval` and the 9 "not built yet" AD-16 callers), and `lint-imports` kept 29.
- After the patches: the full suite through `suite_lock.py` (`track-A`) gave **11356 passed, 12 skipped**. `lint-imports` kept 29 contracts and broke none.

## Spec Change Log

## Review Triage Log

Pass 1 (review loop 0). Three layers ran: blind hunter (BH, 13 findings), edge-case hunter (EC, 8) and verification gap (VG, 2 gaps). That is **23 raw** findings, **20 after grouping**: 7 patched, 1 deferred, 12 rejected.

| # | Layer | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| 1 | BH | Contract (d) misses the core when it is reached through `nextron.app.compliance.api`, which re-exports `evaluate` by its `from nextron.domain.eligibility import ... evaluate`. | medium | Verified. `api.py` binds `evaluate` in its namespace, and the scan follows only `nextron.domain(.eligibility)` names. So `from nextron.app.compliance.api import evaluate` in `web/` or a later service passes (d) and lint-imports, and a consumer could build a Decision from its own facts. The AC says every consumer calls `evaluate_eligibility`. Direct fix: the scan also flags `evaluate` imported from, or taken as an attribute of, `nextron.app.compliance.api` (or its aliases) outside that module, with a probe. | patch (D) |
| 2 | BH | "Eligible" and "Not eligible" rows look the same: the template ignores `EligibilityRow.eligible`, and `app.css` has no rule for the row. | low | Verified. `lead.html` gives the summary `<p>` one class whatever the state; only the text differs. Mac reads this page every day, and the compliance verdict should stand out. Small fix: a state class on the summary (`eligible` / `not-eligible`), minimal byte-preserving CSS (`--refused` for not eligible) and a test pinning the class. | patch (E) |
| 3 | BH | UX-DR17 wants the header to show the live Decision, or "no decision-maker found"; the page shows Decisions only per Contact. | low | True, but not caused here. The 3.6 AC asks for the Decision per Contact on render, which is built. The header needs the Primary Contact and its decision-maker state (FR-13, Story 2.10), which don't exist. | defer (Story 2.10) |
| 4 | BH | The Epic 7 `recontact_on` deferral omits FR-63's default date (90 days after the Reply), so a Not Now Lead parked without a chosen date would stay `not_now_undated`. | low | Verified against FR-63 ("default 90 days after the Reply"). The entry is this story's own text. Direct fix: reword it. | patch (F) |
| 5 | BH | FR-63 history is per Contact, so a second Contact at the same Lead skips the 90-day wait. | low | Real reading, but by design. The frozen AC ("the Contact's earlier Enrollment history") and 4.8's AC ("the Contact's Enrollment history") both say per Contact. `EndedSequence` doesn't care whose history 4.8 passes. Changing the scope changes planning. | reject (planning; flag for Mac) |
| 6 | BH | `recontact_allowed` returns only a bool, not which Event allows the restart that Step 1 must cite. | low | Not a defect of this story. Citing the Event is drafting's and the restart story's job (FR-63 restart by selection, Epic 8). Exposing the Event would add public surface no caller needs yet. | reject |
| 7 | BH | `EndedSequence` doesn't require `last_step_at <= ended_at`; an inverted pair moves the "Event after it ended" window earlier. | low | Verified: nothing checks the order, and the inverted window can let an Event seen before the last Step count (fail-open). No producer exists until 4.8, but the type is new here and validates everything else, and Eligibility must fail closed on inconsistent input. Direct fix: one `ValueError` (no value) plus a probe case. | patch (G) |
| 8 | BH | No Lead page test has several Contacts with different Decisions, so a wrong join would pass. | medium | Same as finding 22. | patch (A) |
| 9 | BH | The app suppression test can't tell the domain keys apart (the email domain equals the web domain), and `profile_suppressed` is tested only through a fake. | low | Verified at `tests/app/test_eligibility.py` `test_an_active_entry_blocks_with_its_reason`. Direct test addition: a Contact whose email domain differs from the Lead's web domain with only the web domain suppressed, and a real profile entry. | patch (H) |
| 10 | BH | Each Contact's evaluation re-reads the Contact, Lead and stage and re-decodes every Market. | low | Local SQLite point reads plus a decode of two Markets: microseconds per Contact, and a Lead has a handful. Sharing reads across calls would be a cache, which the AC forbids. Premature. | reject |
| 11 | BH | Leads at `replied`, `interested`, `call_booked` or `won` show "Eligible" for cold email. | low | Real display, by design: FR-18's criteria and the AC's closed enum don't include those stages. Selection and `enroll` filter by stage, live Enrollment and earlier Enrollments (FR-19, Stories 4.8 and 8.9). Adding a stage reason changes the approved enum. | reject (spec change; flag for Mac) |
| 12 | BH | Contract (c) checks names only, so a Decision serialised into an `events` or `jobs` payload would pass. | low | `events` has no payload column (AD-4's fixed fields), and job payloads hold ULIDs only (AD-17, epic context). A serialisation scan is new machinery for an unbuilt consumer. | reject |
| 13 | BH | The stale-Market text blames "the current Market rules" even when the Contact's own facts or the Lead's country changed. | low | Real but cosmetic. Every cause gives the same Operator action (re-resolve), and the text is the spec's Design Notes text, so the fix edits this build's spec. | reject (spec change) |
| 14 | EC | Contract (d) misses `import nextron.domain` (or `from nextron import domain`) followed by `.eligibility.evaluate`. | low | True, but no module uses that form, and the fix adds alias-tracking branches. This is the same case 3.3 and 3.4 rejected (3.4's #21). | reject |
| 15 | EC | Contract (e) misses `from nextron.app import queries` followed by `queries.setup.get(...)`. | low | Same class as #14: no module imports `queries` that way, and the fix adds an alias branch. | reject |
| 16 | EC | A legal-form code missing from `LEGAL_FORM_LABELS` puts the raw code into the reason and basis texts. | false | That is the spec'd behaviour ("or, failing that, the code"). The code must fullmatch `LEGAL_FORM_PATTERN` (`[a-z][a-z0-9-]{0,63}`), so it can't carry personal data or markup, and it is autoescaped anyway. | reject |
| 17 | EC | A blank or `@`-less email with class `published` or `verified` would be eligible. | low | Unreachable. Every stored email went through `normalize_email` and the syntax check in `add_contact` (2.3), and a CHECK keeps the class NULL without an email. The fix guards a state not shown reachable. | reject |
| 18 | EC | A Market dropped from the seed file stays stored and enabled, so its Contacts stay eligible. | low | Pre-existing by design: 3.1's decision 7 (frozen) leaves a stored Market missing from the file as it is. Disabling is `enabled: false`, which 3.6 honours at once. | reject (3.1 decision) |
| 19 | EC | `compliance.api` re-exports `evaluate`, bypassing contract (d). | medium | Same as finding 1. | patch (D) |
| 20 | EC | A Decision serialised into a JSON payload passes contract (c). | low | Same as finding 12. | reject |
| 21 | EC | A US Market that lists legal forms blocks every US Contact with Companies House wording. | low | That is the spec's decision 3 and Always (forms are checked when listed), the fail-closed side. The shipped US seed is `not_checked`. | reject |
| 22 | VG | No test checks that each Contact gets its own Decision on a Lead page with several Contacts. | medium | Pre-verified gap: every eligibility page test has one Contact per Lead, and the multi-Contact tests ignore the rows. Fix: one page test with an eligible Contact and an ineligible one on the same Lead, each `eligibility-row` right after its own `contact-row`. | patch (A) |
| 23 | VG | Nothing ties `CLOSED_STAGE_LABELS` (and `_BLOCK_STAGES`) to `CLOSED_STAGES`; a new closed stage makes `_closed_reason` raise `KeyError` and the Lead page fail. | low | Pre-verified gap. Fix: a coverage test next to `test_every_block_reason_is_mapped`. | patch (B) |

## Design Notes

**Texts** (`{market}` is a Market `name`):
- **`no_enabled_market`:**
  - "No enabled Market: the Market rules can't be read"
  - "No enabled Market: the Contact's Market hasn't been resolved yet"
  - by basis:
    - `cctld_outside_markets`: "No enabled Market: the email address is on a country-code domain outside the enabled Markets"
    - `location_outside_markets`: "No enabled Market: the office location or the Lead's country isn't in an enabled Market"
    - `location_unknown`: "No enabled Market: neither the office location nor the Lead's country is known"
    - `conflict_stricter`: "No enabled Market: the location and the email's country-code domain point to equally strict Markets"
    - any other: "No enabled Market applies to this Contact"
  - "No enabled Market: the Contact's Market is no longer stored"
  - "No enabled Market: the {market} Market is disabled"
  - "No enabled Market: the Contact's office location can't be read"
  - "No enabled Market: the Contact's Market no longer matches the current Market rules"
- **`cold_email_not_allowed`:** "The {market} Market doesn't allow cold email"
- **`uk_legal_form_unconfirmed`:** "The Lead's legal form isn't confirmed: no Evidence links the Lead to its Companies House record"
- **`uk_legal_form_ineligible`:** "The Lead's legal form ({label}) isn't a corporate subscriber the {market} Market allows"
- **`uk_free_webmail`:** "The address is on a free-webmail domain; the {market} Market allows corporate addresses only"
- **`address_not_sendable`:**
  - "The address is {Guessed|Invalid}; only Published or Verified addresses can be emailed"
  - "The Contact has no email address"
  - "The address has no known Address Class"
- **`address_or_domain_suppressed`:** "The address or its domain is on the Suppression List"
- **`asked_not_to_be_contacted`:** "This person asked not to be contacted (Suppression List)"
- **`recontact_not_allowed`:** "An earlier Sequence ended without a Reply: re-contact needs 90 days since its last Step and an Event first seen after it ended"
- **Not Now:**
  - `not_now_until`: "Parked Not Now until {YYYY-MM-DD}"
  - `not_now_undated`: "Parked Not Now with no re-contact date recorded"
- **`lead_closed`:** "The Lead is closed ({Lost|Disqualified})"

**`LEGAL_FORM_LABELS`:**
- `ltd`: private limited company
- `plc`: public limited company
- `llp`: limited liability partnership
- `private-unlimited`: private unlimited company
- `private-unlimited-nsc`: private unlimited company without share capital
- `private-limited-guarant-nsc`: private company limited by guarantee
- `private-limited-guarant-nsc-limited-exemption`: private company limited by guarantee, exempt from 'Limited'
- `private-limited-shares-section-30-exemption`: private company limited by shares, exempt under section 30

**For later stories:**
- **3.7** adds `provenance_missing` to `ReasonKind` (and a fact for it) and widens `basis_rule`.
- **4.8** passes the history from `queries.sequencing` and refuses on `not decision.eligible`.
- **6.9's `market_eligible`** calls `evaluate_eligibility`.
- **8.x** reads `Decision.kinds`.

## Verification

**Commands** (from `nextron-engine/`):
- `uv run pytest -q tests/unit/test_eligibility.py tests/app/test_eligibility.py tests/unit/test_leads_page.py tests/app/test_contactable.py tests/contracts` -- expected: all pass
- `uv run lint-imports` -- expected: all contracts kept
