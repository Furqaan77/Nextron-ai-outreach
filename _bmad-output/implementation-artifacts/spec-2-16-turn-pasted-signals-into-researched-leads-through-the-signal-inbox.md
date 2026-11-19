---
title: 'Story 2.16: Turn pasted Signals into researched Leads through the Signal Inbox'
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: '43f9c1b2afe7291f31cc49648341ba4ccf0259a3'
review_loop_iteration: 0
story_key: '2-16-turn-pasted-signals-into-researched-leads-through-the-signal'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A signal Mac finds by hand (a post or job ad) has no way into the engine. The `signal_inbox_parse` route, prompt and schema exist, but nothing stores pasted items, parses them, turns them into a Lead with Evidence, or runs the research chain on them quickly.

Requirements: FR-8, FR-9, FR-11, FR-49 · AD-4, AD-7, AD-8, AD-10, AD-18 · NFR-6, NFR-30, NFR-45 · UX-DR19.

**Approach:**
- `discovery` owns a new `signal_inbox_items` table (migration `0043_2_16_signal_inbox`) and the SignalInboxItem machine (`nextron/domain/signal_inbox_item.py`).
- `Discovery.submit_signal_items` (the spine's name) stores the items and enqueues `process_signal_item` (interactive lane) in one transaction.
- A new `SignalInbox` service runs the job: budget check, one `signal_inbox_parse` call at urgency `soon`, validation, then `Discovery.upsert_lead` + `Research.ingest_signal_item` + `enrich_lead` (interactive) in one apply transaction, or a flag.
- The interactive lane is threaded through every chain stage (`job.lane`), and the chain's AI calls use urgency `soon` on that lane.
- A Signal Inbox page (`/signals`, the nav's existing "Signal Inbox" entry) has the submit form, the item list and a reprocess form for flagged items.

The acceptance criteria are Story 2.16 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where the decisions below say otherwise.

### Decisions made overnight (pending user review)

1. **"Urgency `soon`" means the AI call urgency.** The job queue has no urgency. Spine AD-8 sets urgency per AI call. So the chain's jobs run on the interactive lane, and every AI call they make (`signal_inbox_parse`, `extract_signals`, `extract_people`) uses `Urgency.SOON`. The bulk lane keeps `Urgency.BULK`.
2. **Lane threading.** Each chain stage enqueues its next stage on the lane its own job ran on (`job.lane`, passed by the handler as `lane=`). `render_pages` stays bulk-only; its payload carries an optional `chain_lane` (`interactive`), so the `extract_lead` it enqueues goes back to interactive. A bulk-lane job already live under the same idempotency key absorbs the interactive enqueue; that is accepted here (see deferred I in 2.15).
3. **Form.** The form shows 5 item fieldsets (URL, text, author, company). A fieldset with every field blank is ignored, and at least one item is required. URL: required, absolute `http`/`https`, at most 2,048 characters. Text: required, at most 10,000 characters. Author and company: optional, at most 200 characters each. Any invalid item refuses the whole submission (`MalformedForm`-style field errors) and stores nothing.
4. **What the parse may give.** Besides the AC's rules:
   - The Signal type must list `signal_inbox` in its `sources`.
   - The website must not be FR-49-denied and must not be a shared platform (`is_shared_platform`). It "appears" when its registrable domain occurs, case-insensitively, in the item text or the company field.
   - The author's name and role are accepted only when each appears (NFC/whitespace-normalised, case-insensitive) in the item text or the author field.
   - The event date must be stated in the item text (`stated_date`) and be on or before the submission date.
   - A rejected field is dropped (null) and the rest still apply. A website that is dropped gives `organization_unknown`; a Segment that is dropped gives `segment_unknown`.
5. **The model sees.** One `signal_types` block (every Segment), then one `signal_inbox` block (subject `signal_item:<item id>`, id the item id): the item text, then, when given, lines `Author: …` and `Company: …`. The quote is checked against the item text only.
6. **The stored parse.** The item keeps its accepted fields (Segment, Signal type, website domain, author name and role, event date, quote, `extracted_by`), so a reprocess needs no second AI call.
7. **Existing Lead.** `upsert_lead` with the parsed Segment returns an existing Lead with its own Segment unchanged. The Quote Evidence is always stored. The Signal is stored only when its type belongs to that Lead's Segment. The Lead's name for a new Lead is the company field when it isn't a URL or domain, else the registrable domain.
8. **Suppressed organization.** A new Lead blocked by the contact gate flags the item with a fourth reason, `organization_suppressed` ("Organization is on the Suppression List"), and creates nothing else.
9. **Reprocess.** `POST /signals/{item_id}/reprocess` works only on a `flagged` item. It takes either an existing Lead (its id or Lead page link) or a website plus a Segment. It applies synchronously, in one transaction, with the stored parse fields and no AI call. A merged Lead resolves to its survivor. An unknown Lead, a denied or shared-platform website, an unknown Segment or a suppressed organization answers with an error, and the item stays flagged and unchanged. On success the item becomes `processed` (actor `operator`) and `enrich_lead` is enqueued on the interactive lane.
10. **The author Contact** is added through `Research.add_contact` with role match by `domain.contacts.role_match` against the Lead's Segment, no email, no LinkedIn URL, Provenance source `signal_inbox`, detail the item URL, retrieved at the submission time. A pasted URL is data only and is never fetched by any job.
11. **The `process_signal_item` job.** Lanes interactive only, priority 60, 5 minutes, retry limit 3, group Discovery. Scope the item, discriminator `tick`, payload `{v, item_id}`. A budget refusal returns `DEFERRED` and the item stays `pending`. An item no longer `pending` is skipped.

## Boundaries & Constraints

**Always:**
- Only `app.research` writes Evidence, Signals and Provenance (`ingest_signal_item`). Only `discovery` writes `signal_inbox_items`.
- The gateway is the only path to Claude. The budget is checked before the call. No prompt or response text is stored or logged; logs carry IDs and counts only.
- Every transition appends an `events` row with IDs only (entity `signal_inbox_item`).
- Pasted text renders escaped (`|plain`) inside an `hx-disable` container.
- `ingest_signal_item` joins `tests/contracts/test_ad16_callers.py` with a scenario.

**Never:**
- Never fetch the item URL, LinkedIn or Instagram. Never set a Contact's LinkedIn URL from an item.
- Never store a quote that isn't an exact substring of the item text.
- No new notification (AD-3's list is closed). No drafting.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| COO re-keying post (Ops), site in text | parse ok | one new Lead, Quote Evidence + Tier 1 `ops_rekeying_post` Event (first seen = submission time), COO Contact; item `processed`; chain interactive to Tier A | N/A |
| Item URL on linkedin.com | submit | stored; list shows "stored as a source only; never fetched" | never fetched |
| Parse refused / invalid / truncated | job | item `flagged`, `parse_failed` | stays in the inbox |
| No acceptable website | parse ok | `flagged`, `organization_unknown` | reprocess |
| Segment key not seeded | parse ok | `flagged`, `segment_unknown` | reprocess |
| Quote not in text | parse ok | quote dropped; no Evidence, no Signal; rest applies | N/A |
| Website already a Lead (any stage) | parse ok | same Lead returned; Evidence added | N/A |
| New Lead on a suppressed domain | parse ok | `flagged`, `organization_suppressed`, nothing else written | N/A |
| Budget exhausted | job | `DEFERRED`, item `pending` | retried later |
| Reprocess with a website + Segment | flagged item | Lead upserted, item `processed` | errors leave it flagged |

</frozen-after-approval>

## Code Map

Paths are under `nextron-engine/`.

- `nextron/adapters/ai/{routes,schemas}.py`, `prompts/signal_inbox_parse/v1.md` -- exist; don't change (the prompt is hash-locked). `SignalInboxParseV1` gives `segment`, `signal_type`, `website`, `author_name`, `author_role`, `event_date`, `quote`.
- `nextron/app/research/extraction.py:240-360` -- the three-phase pattern to copy:
  - phase 1 (transaction): `allow(BUDGET_CLASS, meter.snapshot(now), setup_queries.budget_limits())`, then `playbook_queries.active_tuple(route)`;
  - phase 2 (no transaction): `llm.run(tuple, blocks, urgency)`, `AdapterError` re-raised, then `outcome_of(result)`;
  - phase 3: apply. `extracted_by` format and `UntrustedBlock` usage are here too. `Urgency.BULK` is hard-coded at :280; `people.py` does the same. Both take `urgency` from the lane (Decision 1).
- `nextron/domain/ai_test_cases.py:372-386` -- the inbox block convention (subject `signal_item:…`, catalogue of every Segment via `segments.signal_catalogue`).
- `nextron/domain/quotes.py` -- `normalise`, `quote_in`. `nextron/domain/extraction.py:442` -- `stated_date`. `EvidenceSource.SIGNAL_INBOX` (:156).
- `nextron/domain/fetch_policy.py` -- `is_denied(url)`, `url_refusal`. `nextron/domain/leads.py:226` -- `is_shared_platform`. `nextron/domain/identity.py` -- `registrable_domain`, `url_host` (raise `IdentityError`).
- `nextron/app/discovery/api.py:207-300` -- `UpsertedLead(lead_id, created, merged, blocked)`, `upsert_lead(identity, source)` (`LeadSource.SIGNAL_INBOX` already maps to `created_from_signal_inbox`), `_enqueue_crawl` (:300; CSV only today; add a `lane=` path for the inbox). `Discovery(clock, pipeline, queue)`.
- `nextron/app/discovery/legal_form.py:345` -- `_select`, which enqueues `select_primary_contact`; thread `lane`.
- `nextron/app/research/api.py` -- add `@command ingest_signal_item(lead_id, item: SignalItemFacts) -> IngestedSignal`. Use `signals_repo.insert_evidence` / `insert_signal` / `link` (`db/repos/research/signals.py:195,279,313`) as `extraction.py:_link` (:598) keys Events by (type, event_date). `add_contact` (:787). Crawl apply enqueues `render_pages` (:1796) or `_enqueue_extraction` (:1862); render apply enqueues `extract_lead` (:2016).
- `nextron/app/research/people.py:433` (`check_job_boards`), `:687-688` (`find_contact_email` via `finding.enqueue_search(lane=)`, and `score_lead`); `job_boards.py:312` (`check_legal_form`); `app/scoring/lead_scores.py:63` `enqueue_score_lead(lane=)` -- thread `lane` through every one.
- `nextron/domain/contacts.py` -- `ContactCandidate` (:216), `ProvenanceSource.SIGNAL_INBOX` (:125), `role_match(role, segment_def)` (:463).
- `nextron/domain/pipeline_stage.py`, `domain/machines.py` -- the machine pattern (`MACHINE`, statuses, causes, `TRANSITIONS`, `CREATION`, `transition`). `app/pipeline/api.py:106` -- `eventlog.append(...)`.
- `nextron/jobs/handlers.py` -- Protocol + factory pattern; `Job.lane` is available to every handler. `nextron/main.py:1377-1690` -- `build_job_types`, wiring; `render_pages` is bulk-only (~:1515).
- `nextron/app/jobqueue/api.py:277` -- `enqueue(job_type, *, scope, discriminator, payload, run_after=None, lane=None, concurrency_key=None)`.
- `nextron/web/app.py` -- `NAV_SECTIONS` already lists `signal_inbox_page`. Copy the CSV import pattern (`web/app.py:1214-1245`, `web/lead_import_page.py`, `lead_import*.html`). `create_app` gains `signal_inbox=`. `web/leads_page.py:374` `SOURCE_LABELS` has "Signal Inbox (manual)" for Provenance; the Lead page's Evidence table (`templates/lead.html:71`, `EvidenceRow` :817) has no source column yet.
- `tests/fakes/llm.py` -- `ScriptedLlm`. `tests/app/test_leads.py` -- `queue_on(clock)`.
- Contracts: `tests/contracts/test_ad16_callers.py:194` (the `signal_inbox` entry, `SCENARIOS` :1321, expected set :1427); `test_source_tree.py` `FILES`; `db/owners.py`, `db/pii_registry.py` (`text`, `author`, `company`, `parsed_author_*`, `parsed_quote` are `redact`; `url` follows `evidence.source_url`); `lead_id` FK means `discovery` registers `reparent_lead` for the table; `test_job_contracts.py` (docstring, names, shapes); `tests/unit/test_health_page.py:280` `NEVER_RUN`; HEAD in `test_persistence_contracts.py:82`, `tests/unit/test_migrations.py:39`, `tests/app/test_serve_process.py:368`.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/signal_inbox_item.py` -- statuses `pending`, `processed`, `flagged`; `FlagReason` (`organization_unknown`, `segment_unknown`, `parse_failed`, `organization_suppressed`); events and causes; `TRANSITIONS` (pending→processed, pending→flagged, flagged→processed by operator); `CREATION`; `transition`. Add the pure validation: `accept_parse(parsed, item, segments, submitted_at) -> AcceptedParse` (Decisions 4 and 7), plus form-item validation (Decision 3). Unit tests.
- [x] `nextron/db/alembic/versions/0043_2_16_signal_inbox.py` + `db/models/` -- `signal_inbox_items`: `id`, `url`, `text`, `author`, `company`, `status` (CHECK), `flag_reason` (CHECK, nullable), `submitted_at`, `processed_at`, `lead_id` (FK `leads.id`), `parsed_segment`, `parsed_signal_type`, `parsed_website`, `parsed_author_name`, `parsed_author_role`, `parsed_event_date`, `parsed_quote`, `extracted_by`. Index `(status, submitted_at)`. Owners, PII, reparent, HEAD pins, chain probes, `FILES`.
- [x] `nextron/db/repos/discovery/signal_inbox.py`, `nextron/app/queries/discovery.py` -- insert, status writes, `item(id)`, `items(limit)` (newest first).
- [x] `nextron/app/discovery/api.py` -- `@command submit_signal_items(items) -> tuple[str, ...]`; inbox enqueue of `enrich_lead` on a given lane.
- [x] `nextron/app/discovery/signal_inbox.py` -- `SignalInbox(clock, meter, llm, discovery, research, queue)`: `@use_case process_signal_item(item_id) -> bool` and `@use_case reprocess(item_id, choice) -> ReprocessOutcome`. Logs with IDs only.
- [x] `nextron/app/research/api.py` -- `ingest_signal_item` (Evidence `quote`, `source=signal_inbox`, `source_url` = item URL, `retrieved_at` = submission time; Event dated by stated date else submission date, first seen at submission time; author Contact per Decision 10).
- [x] Lane threading (Decision 2) through `enrich_lead`, crawl apply, `render_pages` (`chain_lane`), `extract_lead`, people step, `check_job_boards`, `check_legal_form`, `select_primary_contact`, `find_contact_email`, `score_lead`; urgency from the lane in `extraction.py` and `people.py`. Existing bulk behaviour stays unchanged.
- [x] `nextron/jobs/handlers.py`, `nextron/main.py` -- `process_signal_item` job type (Decision 11) and wiring.
- [x] Web: `GET /signals` (`signal_inbox_page`), `POST /signals` (submit), `POST /signals/{item_id}/reprocess`; `web/signal_inbox_page.py`, `templates/signal_inbox*.html`. The list shows status, submission time, the pasted text, the URL (with the denylist note), the Lead link, and for flagged items the reason as "what is missing" plus the reprocess form. The Lead page's Evidence rows show their source label, `signal_inbox` as "Signal Inbox (manual)".
- [x] Tests: `tests/unit/test_signal_inbox_item.py`; `tests/app/test_signal_inbox.py` (the AC's COO chain end to end with `ScriptedLlm`, port fakes, a frozen clock and every job asserted interactive except renders; each flag reason; existing Lead; suppressed; budget deferral; reprocess paths; no fetch of the item URL); web tests; contract pins.
- [x] `nextron-engine/README.md` -- a Story 2.16 section.

**Acceptance Criteria:**
- Given a bulk-lane chain (CSV import), when it runs, then every stage stays on the bulk lane at urgency `bulk`, as before.
- Given any submitted item, when every job has run, then no fetch was made to the item's URL.

## Implementation Notes

- **Domain** (`nextron/domain/signal_inbox_item.py`): the machine (`ItemStatus`, `FlagReason` + `FLAG_LABELS`, `ItemEvent`, `ItemCause`, `TRANSITIONS`, `CREATION`, `transition`), the form rules (`check_items` -> `SubmittedItem`s or `ItemsRefused(errors)` keyed `<field>_<n>`, `items` when nothing was filled in), `url_never_fetched` (`fetch_policy.is_denied`), `accept_parse` -> `AcceptedParse` (`.flag`), `website_domain`, `lead_name`, and the job/route constants. A malformed answer (wrong field type) raises `ValueError` -> `parse_failed`.
- **Events entity** is `signal_inbox_items` (the table name), not `signal_inbox_item`: `nextron.db.eventlog` only accepts a table of `owners.TABLES`. Causes: `submitted` (actor `operator`), `parsed` / `flagged` (actor `system:discovery`; the flag reason itself is in `flag_reason`), `reprocessed_by_operator` (actor `operator`); `cause_ref` is the Lead id on a processed transition.
- **Table** `signal_inbox_items` (model in `db/models/leads.py`, migration `0043_2_16_signal_inbox`, down revision `0042_4_9_drafts`): the spec's columns plus CHECKs `status`, `flag_reason` (set exactly when flagged) and `processed` (`processed_at` and `lead_id` set exactly when processed); indexes `(status, submitted_at)` and `lead_id`. Repo `db/repos/discovery/signal_inbox.py`; queries `signal_item`, `signal_items`, `export_pii` in `app/queries/discovery.py`.
- **Discovery** (`app/discovery/api.py`): `submit_signal_items`, `enqueue_enrich(lead_id, *, lane)`, `_enqueue_crawl` takes a lane; `SignalInboxRows(clock).reparent_lead` (LeadsMerged, after sequencing) and the module-level `erase_pii` (ContactErased, second: after compliance's, before research's; see Review fixes C) with `ERASURE` (`signal_inbox_items` tombstoned, fail-safe: every item of the Contact's Lead and Leads merged into it, and unlinked items that may name the Contact).
- **Service** `app/discovery/signal_inbox.py`: `SignalInbox.submit` (use case around `submit_signal_items`), `process_signal_item`, `reprocess` (`ReprocessChoice`, `ReprocessOutcome`, `lead_id_of`). Phase 3 calls `upsert_lead` (the contactability gate) **before** writing the parse to the item, so the gate precedes the job's first write (AD-16 order check); a flagged parse is stored with the flag. No active `signal_inbox_parse` tuple flags `parse_failed`; a non-retryable `AdapterError` or an `LlmFailed` also flags `parse_failed`; a retryable one is re-raised (item stays `pending`) except on the job's last attempt, which flags `parse_failed`.
- **Research** `ingest_signal_item(lead_id, SignalItemFacts) -> IngestedSignal` plus helpers `_inbox_signal` / `_inbox_author`; the quote is re-checked (`ValueError` if not an exact substring). Reprocess drops a stored quote that is no longer in the (erased) text.
- **Lane threading**: `enrich_lead(..., lane=)`, `render_pages(..., chain_lane=)` (payload `chain_lane` added only for the interactive lane), `extract_lead(..., lane=)` -> `people.extract(..., lane=)`, `check_job_boards(..., lane=)`, `check_legal_form(..., lane=)`, `select_primary_contact(..., lane=)` (its `find_contact_email` and `score_lead` on that lane). `canonicalize_lead` is also enqueued on the crawl's lane. Urgency comes from `nextron.domain.ai.chain_urgency(lane)`. Handlers pass `lane=job.lane`; a bulk job passes `Lane.BULK`, which is each job type's default, so bulk behaviour (lanes, payloads, keys) is unchanged.
- **Job** `process_signal_item` registered last in `build_job_types` (optional `signal_inbox=` keyword; missing -> the handler raises `RuntimeError`, like `finding`).
- **Web**: `GET/POST /signals`, `POST /signals/{item_id}/reprocess` (`web/signal_inbox_page.py`, `signal_inbox.html`, `signal_inbox_panel.html`); `create_app(..., signal_inbox=...)` is required. The Lead page's Evidence Source cell now shows the source label (`EVIDENCE_SOURCE_LABELS`) before the link (or alone without one).
- **Export** (Story 3.15 contract): `contact_export.COVERAGE["signal_inbox_items"] = Exported("discovery", "signal_inbox_items", shown_as text -> text_excerpts)`; compliance's export adds a `discovery` part (`queries.discovery.export_pii(lead_id, name, emails)`: items of the Lead, or with no Lead, that mention the Contact).
- **Tests**: `tests/unit/test_signal_inbox_item.py`, `tests/app/test_signal_inbox.py` (the AC's COO chain through every real handler: only `render_pages` on bulk, every AI call `soon`, Tier A, COO Primary, the post URL never fetched; flags, suppressed, budget, reprocess paths, existing Lead, and the CSV chain all bulk), `tests/unit/test_signal_inbox_page.py`; pins updated in contracts (ad16 scenario `signal_inbox`, job, web, persistence, erasure, export, source tree), health page, engine health, serve, runner lifespan, dashboard nav, leads page, erasure and contact export tests.
- **Review fixes (pass 1):**
  - A: `process_signal_item(item_id, *, last_attempt=False)`; the handler passes `last_attempt=job.attempts > handlers.PROCESS_SIGNAL_ITEM_RETRIES` (3, now the job type's `retry_limit`), and on the last attempt a retryable `AdapterError` flags the item `parse_failed` instead of being raised, so no item stays `pending`.
  - B: `check_items` refuses a control or format character (`leads.REFUSED_NAME_CATEGORIES`, now also the URL check's set) in the author (`AUTHOR_INVALID`) and company (`COMPANY_INVALID`).
  - C: `queries.discovery.contact_items(lead_id, name, emails)` = the items of the Contact's Lead and merged Leads, plus each item with no Lead (`pending`/`flagged`; repo `unlinked()`) that mentions the Contact (`item_mentions`: name or any address, in text, author, company, parsed author or quote) or whose `parsed_website` is one of those Leads' web domains. `erase_pii` tombstones all of them (repo `tombstone(item_ids)` replaces `tombstone_for_leads`); `export_pii` exports those that mention the Contact. Because the match needs the Contact's plaintext name and addresses (current email plus Provenance addresses), discovery's `erase_pii` is now registered **second**, after compliance's and before research's (which tombstones the Contact); `test_erasure_contracts.FIRST` is `("compliance", "discovery", "research")` and `test_serve` pins the order.
  - D: `domain_occurs(domain, *within)` replaces the substring test in `website_domain`: whole labels, not preceded by a letter, digit or `-`, not followed by a letter, digit, `-` or a further label. A preceding `.` is allowed (so `www.acme.com` matches, as the fix asked) and a following `.label` is refused instead (so `acme.com.au` doesn't match `acme.com`; a sentence's final `.` still does).
  - E: `signal_inbox_items.url` is `PiiClass.REDACT`; erasure tombstones it (`ERASURE` text updated). Evidence `source_url` and Provenance stay as they were (deferred).
  - F: the reprocess notice is "Reprocessed: the item is linked to its Lead."
  - G: import order fixed in `db/models/leads.py` and `db/repos/discovery/signal_inbox.py`.
  - H: `chain_urgency` compares with `nextron.domain.job.Lane.INTERACTIVE`; `INTERACTIVE_LANE` dropped.
  - Tests: last-attempt flag (app and job contract), control characters, label-boundary domains, unlinked-item erasure (name, address only, Lead's and merged Lead's website; an unrelated item untouched) and export (address-only mention, unlinked flagged item), the planted item URL in the erasure scan, J (a site needing no render keeps every job interactive and every AI call `soon`), K (lane parametrised: legal form on GB, non-GB and a non-retryable failure; the people step's no-activation, no-pages and refused paths; `World.extract` takes `lane=`), L (a merge moves a processed item to the survivor), M (exact Lead name, with and without a company), O (`test_the_item_date_is_a_date` deleted).
- **Gate fix:** the first full-suite run caught three pins: `tests/unit/test_contact_export.py` (the `discovery` export section in `SECTIONS`, the assembled document and its key order) and `tests/unit/test_credentials_page.py` (the nav now starts with Signal Inbox).

## Spec Change Log

## Review Triage Log

Pass 1: 31 raw findings (blind hunter BH1–BH14, edge-case hunter EC1–EC11, verification gap VG1–VG6). They group into 22 entries: 15 patch, 2 defer and 5 reject.

| ID | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| A | BH2, EC1, BH9 (tail) | An item stays `pending` forever when a retryable AI error outlives the retry limit or the apply raises. Reprocess takes only `flagged` items. | medium | `process_signal_item` re-raises a retryable `AdapterError`; nothing flags the item when the job dies. | patch: on the job's last attempt, flag `parse_failed` instead of re-raising |
| B | BH9, EC2 | A control or format character in the company (or author) field reaches `lead_name`, so `LeadIdentity.check_name` raises inside the apply transaction. The job fails and the item is stuck. | medium | `check_items` checks refused characters only in the URL; `leads.check_name` refuses `REFUSED_NAME_CATEGORIES`. | patch: `check_items` refuses those characters in author and company too |
| C | BH1, EC4, EC5 | Erasure and export select items by `lead_id`. A `pending` or `flagged` item (`lead_id` NULL) that names the Contact is never tombstoned or exported. | medium | CHECK `processed` keeps `lead_id` NULL unless processed; `tombstone_for_leads` and `of_leads` filter on `lead_id IN`. | patch: also cover unlinked items that mention the Contact (`mention_terms`) or whose `parsed_website` is the Lead's domain |
| D | BH4, EC6 | The website "appears" check is a plain substring test, so `acme.co` matches `acme.com` and `me.com` matches `acme.com`. | medium | `_occurs` is a casefolded `in`. | patch: match the domain with label boundaries |
| E | BH5, EC11 | `signal_inbox_items.url` is `none`, but a pasted post URL often carries the person's name (`linkedin.com/posts/<slug>`). | medium | The PII registry classifies it `none`, so erasure keeps it. | patch: classify the item's `url` `redact`; defer Evidence `source_url` and the Provenance detail copies (pre-existing class) |
| F | EC7 | Reprocessing a `parse_failed` item says "the item is now a Lead's Evidence" when no quote was stored. | low | `PROCESSED` notice text in `signal_inbox_page.py:79`. | patch: neutral wording |
| G | BH10 | Import order is broken in `db/models/leads.py` (a second `from datetime import date`) and in `db/repos/discovery/signal_inbox.py`. | low | Verified; ruff isn't installed, so no check failed. | patch: merge or reorder the imports |
| H | BH13 | `chain_urgency` compares against a copied `"interactive"` string instead of `Lane.INTERACTIVE`. | low | Domain modules may import each other (`ai.py` imports `budget` and `playbook`). | patch: compare with `Lane.INTERACTIVE` |
| I | BH11 | `sprint-status.yaml`'s `last_updated` lost its format and time. The status remarks are false (mid-workflow). | low | Verified the format change. | patch (orchestrator) |
| J | VG1 | The crawl's no-render branch (the usual site) is never exercised on the interactive lane. | gap | Pre-verified. | patch: a COO variant with long pages, no `render_pages`, all interactive and `soon` |
| K | VG2 | Lane threading on the other branches is untested (legal form on GB and on failure, the people step's no-activation, no-pages and refused paths). | gap | Pre-verified. | patch: parametrised lane assertions |
| L | VG3 | Nothing tests that a merge moves an item's `lead_id` to the survivor. | gap | Pre-verified. | patch: a test that merges after processing |
| M | VG4 | The end-to-end test accepts either Lead name. | gap | Pre-verified. | patch: assert the exact name, with and without a company |
| N | VG5 | Export matching by the Contact's email is untested. | gap | Pre-verified. | patch: an email-only planted item |
| O | VG6 | `test_the_item_date_is_a_date` tests no production code. | low | Verified. | patch: delete it |
| P | BH7, EC9 | The list shows only the newest 100 items, so an older flagged item loses its reprocess form. | medium | `signal_items(limit=SIGNAL_ITEMS_SHOWN)`. | defer (needs paging or a flagged filter) |
| Q | BH6, EC10 | Pasting the same post twice stores duplicate Evidence and pays for a second parse. | low | No dedupe on submission; it takes Mac's own repeat action, and dedupe adds new rules. | reject |
| R | BH3 | A `parse_failed` item can never be parsed again; a reprocess applies only the stored parse. | low | Decision 9 says a reprocess makes no AI call. | reject (the spec) |
| S | BH12 | The author's name and role are accepted separately, so one person's role can attach to another's name. | medium | Decision 4 accepts each when it appears in the text. Every email still needs Mac's approval. | reject (the spec) |
| T | BH8, EC8 | `enqueue_enrich` queues a crawl for a closed Lead, reports `True` when a live bulk job absorbs it, and silently queues nothing for a Lead with no website. | low | The AC enqueues `enrich_lead` for the Lead at any stage; absorption is Decision 2; a Lead with no website can't be crawled. | reject |
| U | BH14 | Reprocess UX: no prefill of the parsed website or Segment, notices not tied to the item, no polling. | low | Polish; adds surface. | reject |
| V | EC3 | A registrable domain over 200 characters breaks `lead_name`. | low | Unreachable in practice, and the fix adds a guard. | reject |

## Verification

**Commands:**
- `cd e:/Nextron-epic1/nextron-engine && uv run pytest -q -n 2 tests/unit/test_signal_inbox_item.py tests/app/test_signal_inbox.py tests/contracts` -- expected: pass.
- `uv run lint-imports` -- expected: every contract kept.
