---
title: 'Story 9.8: Show each Lead''s complete history'
type: 'feature'
created: '2026-11-19'
status: 'done'
baseline_commit: '6fb4ec9189ba21fab47d58b87d2ee4d486a15335'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-9-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-9-7-view-the-pipeline-by-stage-with-filters.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The Lead page shows only current state. Mac cannot see, in one place and in order, everything that happened with a Lead (stage changes, calls, Evidence and Signals, Contacts, suppression, and later messages and Drafts) before he acts (FR-46, FR-51, UX-DR17).

**Approach:** A read-only History tab at `GET /leads/{lead_id}/history`: one newest-first timeline, filterable by type, 100 entries per page. It is built from two sources. (1) Every `events` row about the Lead or about any row linked to it, found **generically** through foreign keys, so entity types merged later (drafts, enrollments, outbound messages, replies, call preps) appear without code changes. (2) Row-derived entries for history that has no `events` rows: Contacts added (with Provenance source), Evidence added/superseded/retracted, and Signals added/expired/retracted. It is composed by `app.analytics` (read-only) through `queries.*`.

### Decisions made overnight (pending user review)
1. **Tab = a separate page.** The Lead page gets a tab bar ("Overview", "History") linking `/leads/<id>` and `/leads/<id>/history`; History is its own page, not an in-page panel.
2. **Linked rows** = the Lead, every Lead merged into it (`queries.discovery.merged_lead_ids`, which reads `leads.merged_into`), and any row of any table whose foreign key points (directly or through a chain of foreign keys) at one of those Leads (e.g. `contacts.lead_id`, `call_outcomes.lead_id`; on main `drafts.enrollment_id → enrollments.lead_id`). Suppression entries have no foreign key: an entry (any status) is linked when its `(kind, value_hash)` is a key of one of the Lead's Contacts or of the Lead's web domain, derived exactly as `compliance.suppress` derives keys (`person_keys`/`erased_person_keys` + `firm_domain_keys`).
3. **Merged Leads need no new event.** Reading `merged_lead_ids` covers merged history, so 9.6's deferred `leads_merged` gap is left as is (still open for 9.6's Calls report). No write path changes.
4. **Filters** (`type` query param): `all` (default), `messages`, `drafts`, `stages`, `evidence` ("Evidence and Signals"), `calls`. Category by entity table: messages = `outbound_messages`, `threads`, `mail_messages`, `replies`, `manual_replies`, `bounces`, `touches`, `linkedin_tasks`; drafts = `drafts`, `draft_revisions`, `approvals`; stages = `leads` rows whose `to_status` is a `PipelineStage`; evidence = `evidence`, `signals`, `job_postings`; calls = `call_outcomes`, `call_preps`. Everything else (Contacts, suppression, data subject requests, enrollments, `leads` non-stage rows, unknown tables) shows only under `all`.
5. **Generic rendering.** An event entry shows when, type (entity label; unknown tables humanised from the table name), from → to (stage labels for stages, humanised codes otherwise), cause (humanised code), and actor (`operator` → "Operator", `sender` → "Sender", `seed` → "Seed", `system:<owner>` → "Engine (<owner>)", `ai:<route>@<v>` → "AI (<route>)"). Richer per-type detail the AC lists for types not in this tree (sent message text from the OM copy with inbox label and Step, Reply class and class source, Bounce class, Draft rejection reason, Call Prep Sheet revisions, links to a Draft or Thread) is deferred to deferred-work until those tables reach this code; their status changes already show generically.
6. **Detail links:** Evidence → its `source_url` (only `http`/`https`; otherwise plain text); Contact entries → the Lead page's Contacts section; call entries → the Lead page's Pipeline panel; others none. Links always point at the survivor Lead.
7. **Signal expired** = the moment the domain's liveness rule first makes the Signal not live (by its age anchor), shown only when that moment is at or before now; a Signal retracted earlier shows only "retracted". **Evidence superseded** is dated by the superseding row's `created_at`.

## Boundaries & Constraints

**Always:** `@personal_data(True)` and `Cache-Control: no-store`; read-only unit of work; web reads only `queries.*` and `nextron.app.analytics.history`; stored (tombstoned) values only, so an erased Contact shows "[erased]" for name and text while ids, statuses and times remain; all free text (Evidence quotes, notes, names) autoescaped through `|plain` inside an `hx-disable` container; newest first by `at` desc then a stable id desc; 100 per page; a page past the last shows the last.

**Never:** Write anything (no migration, column, event, job); show a suppression entry's `domain_plain`, a hash or any plain value (kind and source only); show contact emails, LinkedIn URLs or addresses; use `|safe`; hard-code a per-entity code path for events (the event reader is generic; only labels and categories are tables).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Full timeline | Lead created, a Contact added (source `csv`), Evidence added then superseded, a Signal added, stage moved by Operator, call booked and recorded, a suppression of its Contact | every one as an entry, newest first, with actor and cause | N/A |
| Filter | `?type=calls` | only `call_outcomes`/`call_preps` entries; tabs/filter links keep `type` | N/A |
| Paging | 230 entries, `?page=3` | entries 201–230, pager; `?page=9` shows page 3 | N/A |
| Merged | Lead B merged into A; `GET /leads/B/history` | 303 to `/leads/A/history`; A's history includes B's entries | N/A |
| Erased Contact | Contact erased via DSR | its entries show "[erased]" for name/text; times, statuses, ids remain | N/A |
| Unknown entity | an `events` row about a row of a later table linked by FK to the Lead | shows under `all` with humanised labels | N/A |
| Empty | new Lead with only its creation | the creation entry; filters with nothing show "No history entries of this type." | N/A |
| Malformed | unknown param, repeated param, unknown `type`, page not a positive integer | 400 "Request refused: malformed form" | `MalformedForm` |
| Unknown Lead | id unknown or not a ULID | the missing-Lead page, never echoing the id | N/A |

</frozen-after-approval>

## Code Map

- `nextron-engine/nextron/domain/lead_history.py` (new) -- `HistoryType` (StrEnum, decision 4), `ENTITY_TYPES` table→type map, `ENTITY_LABELS`, `PAGE_SIZE = 100`, `HistoryEntry` value (at, sort key, type, kind code, entity, ids, from/to, cause, actor, title, contact name, text, link), `history_type(entity, to_status)`, `actor_label`, `code_label`, `page_of(entries, type, page) -> HistoryPage` (sort, filter, clamp). Reuse `pipeline_stage.PipelineStage` labels.
- `nextron-engine/nextron/db/repos/analytics/lineage.py` (new, read-only) -- from `nextron.db.models.Base.metadata`: the tables linked to `leads` by foreign-key chains (cycle-safe, `leads` itself the root, `events` excluded); `lead_events(lead_ids, extra: Mapping[str, Collection[str]])` returns every `events` row with entity `leads` and id in `lead_ids`, or entity a linked table and `entity_id` in that table's nested `IN` subquery, or entity/ids in `extra`. Pattern: `repos/pipeline/board.py` reads other owners' tables read-only.
- `nextron-engine/nextron/app/queries/analytics.py` -- `lead_events(...)` over the repo.
- `nextron-engine/nextron/db/repos/compliance/suppression_entries.py` + `app/queries/compliance.py` -- `matching(keys)`: entries of any status for those `(kind, value_hash)` pairs (`ListedEntry`-like, no plain value used by the page).
- `nextron-engine/nextron/app/queries/research.py` -- reuse `contacts`, `lead_provenance`; add all-rows Evidence/Signals reads if `evidence`/`signals` filter to live rows (repo already has `live_only`/`current_only` flags). Signal liveness: `domain` `age_days`/`is_live` as in `_status`.
- `nextron-engine/nextron/app/analytics/history.py` (new) -- `lead_history(lead_id, *, type, page, now) -> LeadHistoryView | None`: survivor check is the route's; gathers merged ids, Contacts, keys (as `compliance/api.py:454-472`), suppression entries, events, row-derived entries; returns `HistoryPage` plus a contact-name map. Pattern: `analytics/offer.py`.
- `nextron-engine/nextron/web/history_page.py` (new) + `templates/lead_history.html` (new) + `templates/lead_tabs.html` (new, included in `lead.html` under the `h1`) -- param parsing (reuse `leads_page.MalformedForm`, pager style of `pipeline_page.py`), view rows, time formatting as the Lead page does.
- `nextron-engine/nextron/web/app.py` -- `GET /leads/{lead_id}/history` (`lead_history_page`, `@personal_data(True)`), shaped like `lead_page` (`survivor_of`, `missing_lead`, 303 to the survivor).
- Contracts: `tests/contracts/test_source_tree.py` `FILES`; `tests/contracts/test_web_contracts.py` route + template lists; import contracts must hold unchanged (`uv run lint-imports`).

## Tasks & Acceptance

**Execution:**
- [x] domain module -- unit tests: categories, labels, ordering ties, filter, page clamp.
- [x] lineage repo + analytics query -- app tests on a real temp DB: Lead + Contact + call_outcomes events found; a throwaway test-only table with a FK chain (e.g. via metadata in the test, or an existing FK-linked table) proves the generic link; unrelated Lead's events excluded.
- [x] compliance `matching` + research reads + `analytics/history.py` -- app tests for every matrix row except web ones, using real services (`Discovery.upsert_lead`, `Research.add_contact`, `Pipeline`, `Compliance.suppress`, erasure) and a no-write check.
- [x] web module, templates, route, tab bar -- web tests: filters, paging, merged redirect, erased "[erased]", no-store, `hx-disable`, autoescape of a `<script>` quote, malformed 400, unknown Lead, no `domain_plain`/hash/email in the body.
- [x] `nextron-engine/README.md` -- document the History tab; `deferred-work.md` -- decision 5's richer detail per future type.

**Acceptance Criteria:**
- Given the History tab of a Lead with activity, then one newest-first timeline shows every linked `events` row and every row-derived entry, each with time, type, change, cause and actor, linking to its detail where decision 6 gives one.
- Given any render, then no database row changes and `uv run lint-imports` keeps every contract.

## Implementation Notes

- `nextron/domain/lead_history.py`: `HistoryType` (+ `TYPE_LABELS`), `ENTITY_TYPES` and `ENTITY_LABELS` (decision 4/5 tables; unknown tables humanised by `code_label`), `ROW_KINDS` (`contact_added`, `evidence_added/superseded/retracted`, `signal_added/expired/retracted`) and `EVENT`, `HistoryEntry` (at, sort_id, type, kind, entity, entity_id, from/to, cause, raw actor, title, contact_id, text, link), `HistoryPage`, `history_type`, `actor_label` (also `rule` -> "Engine (rule)" for Evidence `extracted_by`), `code_label`, `entity_label`, `http_link` (http/https with a host only), `signal_expired_at` (first age `signals.is_live` refuses, from the age anchor at midnight UTC, never before `first_seen_at`, horizon `EVENT_LIFE_DAYS + 1`; a refused class/Tier counts as not live from age 0, as on the Lead page), `page_of` (at desc then sort_id desc, filter, 100 per page, clamp; type/page checks). Stage labels stay in `web/leads_page.STAGE_LABELS` (the domain has none), so the web turns a `leads` stage code into its label.
- `nextron/db/repos/analytics/lineage.py`: `linked_tables(metadata=None)` (fixed point over `Base.metadata` foreign keys from root `leads`; `events` excluded; `leads.merged_into` not followed) and `lead_events(lead_ids, extra, *, metadata=None)` -> `StoredEvent`s newest first: `leads` rows by id, each linked table with an `id` column through a nested `IN` subquery per foreign key (OR over FKs, `correlate(None)`, a table visited once per chain so self-references and cycles are skipped), and `extra` `{entity: ids}`. The optional `metadata` exists for the throwaway-table test.
- `nextron/app/queries/analytics.py`: `lead_events(lead_ids, extra)` (non-ULID ids dropped).
- Compliance: `repos/compliance/suppression_entries.matching(pairs)` (any status; `value_hash IN` then kind filtered in Python) and `queries.compliance.matching(keys)` (SuppressionKey items only, else `TypeError`). Research: `queries.research.all_evidence` / `all_signals` (the repo's existing `live_only=False` / `current_only=False`).
- `nextron/app/analytics/history.py`: `lead_history(lead_id, *, type, page, now) -> LeadHistoryView | None` (page, `contact_names` by id with "[erased]" for erased Contacts, survivor `lead_id`) and `LeadHistory(clock).view(lead_id, type=, page=)`. Reads the Lead + `merged_lead_ids`, their Contacts and Provenance, the suppression keys (as `Compliance.suppress`: `person_keys`/`erased_person_keys` + `firm_domain_keys` per Contact, plus `firm_domain_keys` of each Lead's web domain), `matching` entries (passed to `lead_events` as `extra`; each entry's event text is "<Kind> key, source: <Source>"), all Evidence and Signals. Row-derived entries: Contact added (cause = first non-`eligibility_basis` Provenance source; actor `operator` for an `operator` or `csv` source, else `system:research`), Evidence added (actor `extracted_by`, cause `source`, text quote or sought, link `source_url` if http/https)/superseded (superseding row's `created_at`, falling back to `last_confirmed_at` if that row isn't found)/retracted, Signal added (text = Signal type name, key as fallback, cause = class)/expired (decision 7)/retracted (from `expired` when the expiry was shown first). An event's contact is its `entity_id` for `contacts`, else its `cause_ref` when that is one of the Lead's Contacts (data subject requests, Primary events). The suppression entity name comes from `nextron.app.compliance.api.ENTITY`.
- Web: `nextron/web/history_page.py` (`history_query` -> `MalformedForm` for unknown/repeated params, unknown type, bad page; blank = default; `lead_tabs`, `history_href` (omits `type=all` and page 1), `entry_row`, `history_context`, `history_pager`), `templates/lead_history.html` (filters nav, `section.lead-history[hx-disable]` with the table or "No history entries of this type.", pager keeping `type`; Evidence source through `safe_link`), `templates/lead_tabs.html` (included right after the `h1`'s `header` of `lead.html` and `lead_history.html`, outside it, so the header's own links and facts are unchanged). `app.py`: `GET /leads/{lead_id}/history` (`lead_history_page`, `@personal_data(True)`) registered right after `lead_page`; query parsed first, then `survivor_of` (404 missing page; merged -> 303 to the survivor's history keeping the query); `lead_page` passes `tabs=`.
- `create_app` gains a required `lead_history: LeadHistory` (the web has no clock and Signal expiry needs now); wired in `nextron/main.py` and `tests/dashboard.py`. Other tracks' `create_app` callers need this kwarg at merge.
- Contracts: `test_source_tree.FILES` (6 new files), `test_web_contracts` route + template lists, `test_suppression_contracts` (`matching` and `for_cause_refs` readers; `domain/lead_history.py` an allowed home of the table name, as a label registry). No migration, no new dependency; `lint-imports` 29 kept.
- Tests: `tests/unit/test_lead_history_domain.py`, `tests/app/test_lead_history.py` (full timeline via real services, no-write check over every table, DSR chain, unrelated Lead excluded, throwaway FK chain via test metadata + raw tables, filters, 230-entry paging, merged, erased, Signal expiry both ways, empty/unknown Lead, argument checks, `matching`), `tests/unit/test_lead_history_page.py` (tabs, rows, filters, paging, merged 303, "[erased]", autoescape + `hx-disable`, `javascript:` source dropped, malformed 400s, unknown Lead 404 without echo, no hash/domain_plain/email, route rules, view-module imports).
- Docs: README "A Lead's History tab"; one `deferred-work.md` entry (decision 5).
- **Review patches.** Suppression: besides the derived keys, every entry (any status) whose `cause_ref` is the id of a data subject request of one of the Contacts is linked (`repos/compliance/data_subject_requests.ids_for_contacts`, `repos/compliance/suppression_entries.for_cause_refs`, `queries.compliance.caused_by_requests(contact_ids)`; read-only), so an erasure's own person/profile entries stay on the timeline; entries are merged by id. A Contact whose Lead has no web domain (a merged Lead's is cleared by `mark_merged`) derives its person and firm keys with the survivor's web domain. Evidence: a retraction goes from `superseded` when the supersede date (superseding row's `created_at`, else `last_confirmed_at`) is at or before `retracted_at`, else from `live` (the `evidence` table's CHECK refuses a row both superseded and retracted, so this is defensive and tested through `_evidence_entries`). Signals show their type's name from the Leads' Segments (`setup_queries.segment_by_id(...).definition.signal_types`, the survivor's winning), the key as fallback, as the Lead page does. Contact added: `csv` and `operator` sources are credited to `operator`. Web: filter links use `aria-current="true"` (tabs keep `"page"`); `history_query` strips `type`/`page` before parsing (`type=calls%20`, `page=%202` accepted); `app.css` gains rules for `.lead-tabs`, `.history-filters` (current link bold, ink, underlined), `.history-count`, `.history-table`, `.history-empty`. Tests added: DSR-sourced entries after a real erasure, a merged Contact's keys via the survivor's domain, a domain entry matched only by the Lead's own web domain, operator/CSV actors, Evidence retracted from live, superseded-then-retracted, the `last_confirmed_at` fallback, a merged Lead's Evidence and Signals on the survivor, a Signal type's name, an `ai:<route>@<v>` actor rendering "AI (<route>)", padded query values.

## Spec Change Log

## Review Triage Log

Pass 1 (blind hunter 13, edge-case hunter 6, verification gap 4 = 23 raw; 21 after merging duplicates).

| # | Source | Finding | Verdict | Route |
|---|---|---|---|---|
| 1 | blind, edge | After erasure, `_keys` only derives the address key from the stored hash, so the DSR's own person/profile/email-domain suppression entries (and earlier ones) vanish from the timeline | medium: `erasure.py:243` suppresses with full keys, then the Contact is tombstoned | patch: also link entries whose `cause_ref` is one of the Lead's Contacts' data subject request ids (residual: an older opt-out entry on a now-erased name/profile key can't be matched, by design of erasure) |
| 2 | edge | Merged Lead's `web_domain` is cleared by `mark_merged`, so its Contacts' person keys and firm keys are derived with `None` | medium: `leads.py:147` clears identity keys | patch: a Contact of a merged Lead whose domain is `None` uses the survivor's web domain |
| 3 | blind, edge | Evidence superseded then retracted shows "Live -> Retracted" | low, direct fix | patch: from `superseded` when superseded at or before the retraction |
| 4 | blind | Signal entries show the raw type key | low, every Signal row | patch: show the Signal type's name as the Lead page does (setup type names), key as fallback |
| 5 | blind | Every non-operator Provenance source credited to `system:research`, incl. CSV imports Mac uploads | low | patch: `csv` and `operator` -> `operator` |
| 6 | blind | No CSS for tab bar / filters, no visible current state | low, seen on every view | patch: minimal rules in `app.css` (byte-preserving) |
| 7 | blind | Non-stage Lead events (extraction refusals) noisy under All | low | reject: spec decision 4 puts them under All; changing that changes the approved spec |
| 8 | blind | Lineage skips tables without `id`, only joins FKs targeting `leads.id` | false: every model uses `ulid_pk()` `id`; non-root FKs recurse through `_condition` |
| 9 | blind, edge | Whole timeline rebuilt per view; IN subquery per linked table incl. crawl tables; bound params grow ids x paths | maybe-false (medium if true): needs a timing run on a Lead with many events and merged Leads | defer |
| 10 | blind, verif | No test: Evidence retracted | real gap | patch (test) |
| 11 | blind | No test: superseded-then-retracted; `last_confirmed_at` fallback; merged Lead's Evidence/Signals on survivor; `ai:` actor end to end | real gaps | patch (tests) |
| 12 | blind, verif | No test isolating the Lead's own web-domain suppression key | real gap | patch (test) |
| 13 | verif | No test: erased Contact's suppression entries in history | real gap | patch (test, with #1) |
| 14 | verif | No test: Operator actor for Contact added | real gap | patch (test, with #5) |
| 15 | blind | Stale-on-arrival Signal: "expired" listed before "added" | false: newest first, and expired follows added in time, so listing it above is the correct order |
| 16 | blind | Two mis-indented `contact_id=person,` lines | low, direct fix | patch |
| 17 | blind | Spec status `in-review` vs sprint `in-progress`; empty logs | false: workflow state mid-review |
| 18 | blind | Filter links use `aria-current="page"` | low, direct fix | patch: `aria-current="true"` on filters |
| 19 | edge | `history_query` checks blank on stripped text, parses raw text | low, direct fix | patch: parse the stripped value |
| 20 | edge | `lead_history` validates type/page only after the Lead lookup | low, developer-only, guard adds code | reject |
| 21 | edge | (merged into #9) bound-parameter growth | see #9 | defer |

## Verification

**Commands:**
- `cd e:/Nextron-track5/nextron-engine && uv run pytest -q -n 2 <touched test files> tests/contracts` -- expected: pass
- `uv run lint-imports` -- expected: every contract kept

**Result:** full suite through `suite_lock.py`: 15574 passed, 13 skipped; lint-imports 29 kept, 0 broken. (A first run had one unrelated failure: `test_override_cases` found the digits it checks inside a log record's timestamp; the re-run passed.)
