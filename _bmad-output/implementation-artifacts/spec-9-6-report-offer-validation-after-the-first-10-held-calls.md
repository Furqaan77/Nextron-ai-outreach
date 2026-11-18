---
title: 'Story 9.6: Report Offer validation after the first 10 held calls'
type: 'feature'
created: '2026-11-19'
status: 'done'
baseline_commit: 'a4b8b9c5aa289f4a5f68ee1a3c2a943ff2fae23c'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-9-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-9-4-prompt-for-and-record-each-call-outcome.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Story 9.4 records each Call Outcome, but nothing tells Mac whether the audit's price and scope work: there is no calls report, no Offer validation after 10 held calls and no NFR-49 checkpoint (FR-69, FR-54, NFR-49, NFR-50).

**Approach:** A read-only Calls report page (`GET /reports/calls`) computed by `app.analytics` from `call_outcomes` and the Pipeline Stage `events` rows only, counting engine-sourced Leads. Below 10 held calls it shows booked/held/no-shows and the "n of 10" line; from 10 it adds the Offer validation figures and, with fewer than 2 audits accepted, the Offer review checkpoint.

### Decisions made overnight (pending user review)
1. **Engine-sourced** = the booking's Lead has a creation `events` row (entity `leads`, no `from`) whose cause is `created_from_<s>` for an engine `LeadSource` (domain constant `ENGINE_SOURCES`, today every `LeadSource`: discovery's places/companies_house/job_aggregator, csv, signal_inbox; referral joins when it exists). A Lead without one is not counted. Merges already reparent bookings to the survivor (9.4), so no `leads` column is read.
2. **Counted rows:** booked = unsuperseded `call_outcomes` rows; held/no-show = those with that recorded outcome. Audits proposed = held calls with `audit_proposed`; share = of held calls. Audits accepted = distinct Leads with a held call that have any `events` row (entity `leads`) `to_status = audit_won` (literal; a Lead that skipped to `build_proposed`/`won` doesn't count). Price range/median over proposed held calls' `audit_price`; an even count's median is the mean of the middle two. Price reactions: held calls per value, all three values shown (zeros too). Qualified share = qualified held / held. Top 3 objection types: per held call, by count desc then `ObjectionType` order, zero counts omitted. Percentages round half up to whole percent ("5 of 12 (42%)"). Money in whole dollars, with cents only when the median isn't whole.
3. **The AC fixture's "not a priority ×2"** isn't a recordable objection: 9.4 records `reply_classes.ObjectionType` codes only, and "Not a priority" is the Not Now class (Story 4.6). The fixture uses `send_information ×2` in its place; nothing is added to the objection codes.
4. **Placement:** the page is not a nav section (Epic 11's Funnel will host it as a panel); the Pipeline board (`/leads/pipeline`) links to it ("Calls report"). The report body is its own partial template for Epic 11 to include. `@personal_data(False)`: it shows counts, codes and prices only, never `reason`/`next_step` or names, so erased data can't appear.

## Boundaries & Constraints

**Always:** Read-only unit of work; `app.analytics` reads through `nextron.app.queries.pipeline` (AD-3; no `repos.analytics` table). The query reads only `call_outcomes` and `events` columns. Pure computation lives in `nextron/domain/`. The page carries the label "engine-sourced". `no-store` headers as every page.

**Never:** Write anything (no table, column, migration, event, job, notification, alert or email); show free text, Lead or Contact names; add an objection code; weaken a contract.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Early | 8 booked: 6 held, 1 no-show, 1 pending | booked/held/no-shows and "Offer validation starts at 10 held calls: 6 of 10 recorded"; no validation figures, no checkpoint | N/A |
| Empty | no bookings | zeros and "0 of 10 recorded" | N/A |
| Fixture | 12 held, 5 proposed, 1 Lead reached `audit_won`, objections no_budget×4, uses_other_tool×3, send_information×2 | "5 of 12 (42%)", accepted 1, the 3 objection rows in that order, checkpoint shown | N/A |
| Passed | 10+ held, 2+ Leads reached `audit_won` | figures, no checkpoint | N/A |
| Not engine-sourced | booking whose Lead has no engine creation row | excluded from every count | N/A |
| Superseded / pending | rebooked row; unrecorded booking | superseded excluded; pending counts as booked only | N/A |
| No proposals | 10+ held, none proposed | range and median "—" | N/A |

</frozen-after-approval>

## Code Map

- `nextron-engine/nextron/domain/offer_validation.py` (new) -- `HELD_CALLS_NEEDED = 10`, `AUDITS_NEEDED = 2`, `ENGINE_SOURCES`/`engine_creation_causes()` (from `pipeline_stage.CREATION`), `CallFact` (lead_id, outcome, qualified, audit_proposed, audit_price, objections tuple, price_reaction), `OfferReport` value, `build_report(facts, accepted_lead_ids) -> OfferReport`, `percent(n, d)`, median. Reuse `call_outcomes.CallResult`/`PriceReaction`, `reply_classes.ObjectionType`.
- `nextron-engine/nextron/db/repos/pipeline/bookings.py` -- `report_rows(creation_causes)`: unsuperseded rows joined (EXISTS) to an `events` creation row of the row's Lead; `stages.py` -- `leads_reached(lead_ids, stage)` from `events` (entity `leads`).
- `nextron-engine/nextron/app/queries/pipeline.py` -- `call_report_facts() -> (tuple[CallFact, ...], frozenset[str])`.
- `nextron-engine/nextron/app/analytics/offer.py` (new) -- `offer_validation() -> OfferReport` (reads only; docstring states FR-69/NFR-50 rules).
- `nextron-engine/nextron/web/calls_report_page.py` (new) + `templates/calls_report.html` (page) including `templates/calls_report_panel.html` -- labels, formatting (reuse `lead_pipeline.dollars`, `PRICE_REACTION_LABELS`, `OBJECTION_LABELS`).
- `nextron-engine/nextron/web/app.py` -- `GET /reports/calls` (`calls_report_page`, `@personal_data(False)`), handler shaped like `pipeline_board`; link in `templates/pipeline.html`.
- Contracts: `tests/contracts/test_source_tree.py` `FILES` (new modules/templates), `tests/contracts/test_web_contracts.py` (route + templates lists); import contracts unchanged (check `uv run lint-imports`).

## Tasks & Acceptance

**Execution:**
- [x] domain module -- unit tests: every decision-2 rule, rounding, median even/odd, objection ordering/ties, checkpoint threshold.
- [x] repos + query + `analytics/offer.py` -- app tests for each matrix row against a real temp DB (Leads created via `Discovery.upsert_lead`, outcomes via `Pipeline`), including the fixture's exact numbers, not-engine-sourced exclusion and a no-write check (DB unchanged after).
- [x] web page + route + link -- web tests: early, fixture, checkpoint on/off, "engine-sourced" label, no-store, an erased Contact's `[erased]` texts and Lead name absent.
- [x] `nextron-engine/README.md` -- document the report; `deferred-work.md` -- anything deferred.

**Acceptance Criteria:**
- Given any render, then no database row changes and the report shows no free text or names.
- Given the fixture of the matrix, then the page shows exactly its numbers and the checkpoint "Review the Offer's price and scope before the next Variant".

## Implementation Notes

- **Domain.** New `nextron/domain/offer_validation.py`: `HELD_CALLS_NEEDED`, `AUDITS_NEEDED`, `TOP_OBJECTIONS` (3), `CHECKPOINT`, `ENGINE_SOURCES` (= `frozenset(LeadSource)`), `engine_creation_causes()` (from `pipeline_stage.CREATION`), `CallFact`, `OfferReport` (booked, held, no_shows, validating, proposed, proposed_percent, accepted, price_low/high/median in micro-dollars, price_reactions, qualified, qualified_percent, top_objections, checkpoint), `percent` (half up, 0 for a zero denominator), `median`, `build_report`. Every figure is computed always; `validating` (held >= 10) decides what the page shows; `checkpoint = validating and accepted < 2`. An objection counts once per held call.
- **Repos.** `bookings.report_rows(creation_causes)` (unsuperseded rows with an `EXISTS` on an `events` creation row: entity `leads`, `from_status IS NULL`, cause in the set) → `ReportRow`; `stages.leads_reached(lead_ids, stage)` → `frozenset` of Lead ids with any `events` row (entity `leads`) into `stage`.
- **Query / analytics.** `queries.pipeline.call_report_facts()` → `(facts, accepted)` (accepted read only for Leads with a held call, stage `audit_won`); `app/analytics/offer.py` `offer_validation()` (module function, reads through the current unit of work).
- **Web.** `nextron/web/calls_report_page.py` (`report_context`, `money` with cents only when not whole, `price_range`, `share`), `calls_report.html` including `calls_report_panel.html`. `GET /reports/calls` (`calls_report_page`, `@personal_data(False)`; `page()` still sends `no-store`), registered after `pipeline_board`. Below 10 held: counts + "Offer validation starts at 10 held calls: n of 10 recorded"; from 10: figures (`dl.offer-figures`), price reactions table, top objections table (or "No objections recorded"), checkpoint `p.notice.offer-checkpoint role=status` when due. The Pipeline board links "Calls report". No CSS added.
- **Contracts.** `test_source_tree` FILES (domain, analytics, web module, two templates), `test_web_contracts` (route, templates). Import contracts unchanged.
- **Tests.** `tests/unit/test_offer_validation.py`, `tests/app/test_offer_report.py` (a non-engine-sourced Lead is built by copying a `leads` row and a booking with SQL, since `events` is append-only), `tests/unit/test_calls_report_page.py`.
- **Review patches (triage 1, 7, 9, 11, 13).** (1) New `stages.merged_refs(lead_ids)` (events only): for each Lead, the Leads merged into it, followed transitively through its `events` rows (entity `leads`, cause `leads_merged`, `cause_ref` not null), cycle-safe through a visited set; `call_report_facts` now counts a held Lead as accepted when it or any Lead merged into it has an `events` row into `audit_won`; both module docstrings say so. **Residual:** `Pipeline.reparent_lead` writes a `leads_merged` row only when the survivor's stage changes, so when the survivor is already at or past the merged Lead's stage (or closed) no row links them and an `audit_won` under the merged Lead still isn't counted. (7) Test: a Lead that reached `audit_won` and moved on to `build_proposed` or `won` still counts. (9) The checkpoint is `<p class="notice offer-checkpoint" role="status">` (no `notice-refused`, no `alert`); the web test pins it. (11) `bookings.py` imports `LEADS_ENTITY` from `stages` (stages imports nothing from bookings). (13) Test: a Lead whose creation row has `from_status` NULL but cause `created_by_hand`, and one with an engine cause but `from_status` `new`, are both left out of booked, held and accepted. App tests added in `tests/app/test_offer_report.py`, including a real `Discovery.merge` of an `audit_won` → `build_proposed` Lead into a held survivor.

## Spec Change Log

## Review Triage Log

Pass 1 (blind hunter 12, edge-case hunter 3, verification gap 1: 16 raw, 13 after dedup).

| # | Source | Finding | Verdict | Route |
|---|--------|---------|---------|-------|
| 1 | blind, edge | After a merge, the merged Lead's `audit_won` events row stays under its own id while its held bookings move to the survivor (`reparent_lead`), so `accepted` is undercounted and the checkpoint can show wrongly. | medium: verified in `Pipeline.reparent_lead`, which only writes a survivor row to the more advanced stage (e.g. `build_proposed`), never `audit_won`. | patch: also look for `audit_won` under the Leads merged into each held Lead, followed through the `leads_merged` events rows' `cause_ref` (events only, as the spec requires). |
| 2 | blind, edge | An unknown stored code (`CallResult`/`PriceReaction`/`ObjectionType`) makes the page 500. | low: codes are only written through those enums (9.4's `Outcome`), and `StoredBooking.result` already raises the same way; tolerance needs new branches. | reject |
| 3 | edge | `leads_reached` `IN (...)` could exceed SQLite's bound-variable limit. | false: this SQLite allows 32766 variables, far beyond any held-call count. | reject |
| 4 | blind | The web layer imports `app.analytics.offer` rather than a `queries` module. | false: the spec's Code Map places the read in `app.analytics` (AD-3 table: analytics owns read-only reports), and web routes already import app services. | reject |
| 5 | blind | Accepted counts Leads while other figures count calls; no two-held-calls test. | false: spec decision 2 defines the units; `test_accepted_counts_distinct_leads_with_a_held_call` covers it. | reject |
| 6 | blind | Price reactions should count only proposed calls. | low: changes approved decision 2. | reject (would change the approved spec) |
| 7 | blind | No test that a Lead that reached `audit_won` and then moved on still counts. | low: real gap, direct test addition. | patch |
| 8 | blind | Panel headings: unused `id="offer-validation-title"`, fixed levels. | low: cosmetic; Epic 11 decides embedding. | reject |
| 9 | blind | The checkpoint is a standing state rendered `role="alert"` with `notice-refused`. | low: direct correction. | patch: `role="status"`, drop `notice-refused`. |
| 10 | blind | Hard-coded `/leads/pipeline` and `/reports/calls` links. | low: the codebase uses literal paths in templates throughout (`/leads`). | reject |
| 11 | blind | `LEADS_ENTITY` defined a third time in `bookings.py`. | low: direct correction. | patch: import it from `stages`. |
| 12 | blind | The no-free-text test fakes erasure with SQL on `reason` only. | low: the report's query never selects `reason`/`next_step`; the planted value proves absence regardless of how it got there. | reject |
| 13 | blind, verification | Spec/code naming drift (`REACTION_LABELS`); the engine-cause and `from_status` filters in `report_rows` are never exercised by a test (only a Lead with no events). | low (naming: spec-only, reject); verification gap: real. | patch: add a test with a non-engine `from IS NULL` creation row and an engine cause with a non-NULL `from_status`, both excluded. |


## Verification

**Commands:**
- `uv run pytest -q -n 2 <touched test files>` -- pass
- `uv run pytest -q tests/contracts` and `uv run lint-imports` -- pass
