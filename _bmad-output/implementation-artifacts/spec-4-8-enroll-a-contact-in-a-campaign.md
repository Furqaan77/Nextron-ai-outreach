---
title: 'Story 4.8: Enroll a Contact in a Campaign'
type: 'feature'
created: '2026-11-18'
status: 'done'
baseline_commit: '505c05b625f21a6e4f467d6e4a273c889207cf4f'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-nextron-outreach-engine-2026-11-14/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing can start a Sequence: there is no `enrollments` table, so AD-16's `sequencing.enroll` caller, FR-63's re-contact rule (Eligibility `history`), 2.2's merge refusal and 3.3's "closing or suppressing stops Enrollments" all wait on this story.

**Approach:** Add `enrollments` and `variant_counters` (owner `sequencing`), the Enrollment machine and `domain.sending_policy.capacity_forecast`/`pick_inbox`. One `@command Sequencing.enroll(contact_id, campaign_id, origin, steps)` runs in the caller's transaction: it gates (AD-16), re-checks Eligibility live with the Contact's Enrollment history, assigns the next active Variant round-robin, binds one inbox, records the eligibility basis and publishes `Enrolled`, whose subscriber `Pipeline.advance_to_at_least(queued)` moves a `new` Lead to `queued`. Enrollments stop as soon as they exist: on suppression, Lead close and erasure.

## Boundaries & Constraints

**Always:**
- `enroll` checks in this order and returns `EnrollRefusal(code, reasons=(), blocks=())` with nothing written: (1) `compliance_api.assert_contactable(ContactSubject(contact_id), "email", "cold")` → `blocked` (its `BlockReason`s); (2) `queries.setup.campaign_active` false → `campaign_not_active`; (3) Contact `tz` or `market_id` missing → `missing_tz_or_market`; (4) `Compliance(clock).evaluate_eligibility(contact_id)` not eligible → `not_eligible` (its `Reason`s; the Decision is never stored); (5) Lead's `segment_id` or Contact's `market_id` isn't the Campaign's → `campaign_mismatch`; (6) Lead stage not `new`, `queued` or `contacted` → `lead_already_replied`; (7) a live (`active`/`paused`) Enrollment on the Lead → `lead_already_enrolled`; (8) no active Variant → `no_active_variant`; (9) `queries.sending.inboxes_for_binding()` empty → `no_sending_inbox`. Wrong argument types, an unknown Contact or Campaign, a missing Sequence or bad `steps` raise (`TypeError`/`LookupError`/`ValueError`) before any write. An `InvalidCampaigns` read raises (fail closed).
- On success, in order: upsert the Campaign's `variant_counters` row; insert the Enrollment (`active`) with one `events` row (`None`→`active`, cause `enrolled`, `cause_ref` the Campaign); `research.record_eligibility_basis(contact_id, decision, campaign.id, lia_ref, lia_date, now)` with the LIA values only when `needs_lia(market country)`, else `None, None`; publish `Enrolled(enrollment_id, lead_id)`. Return `NewEnrollment(enrollment_id, lead_id, contact_id, campaign_id, variant_id, sending_inbox_id)`. Enqueue no job.
- An Enrollment binds, at enroll time, its `variant_id`, `sending_inbox_id`, `sequence_id`, `sequence_version` and `offer_id`; none of these (nor `contact_id`, `campaign_id`, `origin`, `created_at`) ever changes: the repository has no setter and a trigger refuses the update.
- Every status change appends one `events` row (entity `enrollments`). Every subscriber is idempotent. `ended_at` is `max(now, last_step_sent_at)` so a stop is never rolled back by the `ended_at >= last_step_sent_at` CHECK.
- Under `app/sequencing/`, only `enroll` may have "enroll" in a def name (the AD-16 manifest matches `\w*enroll\w*`), and the `enrollments` insert is called lexically inside `enroll`.

**Never:** Drafts, `drafting.request_steps`, `pause`/`resume`/`delay_until`/`on_step_sent`, `approval.cancel_pending`, routes, pages or jobs (4.9, 4.10). An `InboxRemoved` subscriber (Epic 6 publishes it). A referral inheriting its referrer's inbox (Epic 7). Writing another owner's tables, storing the Decision, an `ERASURE` entry for these all-`none` tables (the 3.13 contract refuses one). A new dependency.

### Decisions made overnight (pending user review)
1. **Stops wired now.** `deferred-work.md` (3.3) and `Pipeline.close`'s docstring assign them to 4.8; the epics list them under 4.9. Compliance-safe choice: stop live Enrollments as soon as they can exist. 4.9 adds `approval.cancel_pending` inside `stop`.
2. **Refusal order** puts the gate first (AD-16 before anything), which makes `blocked` reachable (otherwise Eligibility's `address_or_domain_suppressed` would always answer first).
3. **Extra refusals** `campaign_mismatch`, `lead_already_replied` (3.6 triage #11, 3.7 triage #2: enroll must filter by Segment and stage) and `no_active_variant`, which the recorder would otherwise turn into a rolled-back `ValueError`.
4. **Extra columns** `sequence_id`, `sequence_version`, `offer_id` (4.2 triage R9: a changed Offer or Sequence applies to new Enrollments only).
5. **History** holds only ended Enrollments (`stopped`/`completed`) with a sent Step: a Contact never emailed hasn't been contacted. `replied` ⇔ stop cause `reply` or `manual_reply`.
6. **Daily Caps are 0** until Epic 6 (placeholder `queries.sending.cold_daily_caps`), and no follow-up is due until 4.9, so `pick_inbox` today spreads Enrollments by fewest live ones, then the earliest registered. Capacity doesn't refuse an enroll; 4.10's selection limits picks.
7. **Suppression sweep.** On `SubjectSuppressed`, stop the named Contact's live Enrollments and every live Enrollment whose Contact the gate now blocks (catches domain and shared-key suppressions), cause `suppressed`, `cause_ref` the entry.
8. **Actor** is `operator` for origin `operator_pick` and stop causes `operator_stop`/`operator_rejected`, otherwise `system:sequencing`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Happy path | eligible US Contact, active Campaign, Variants A+B, 2 inboxes | `NewEnrollment`; Variant A then B; inbox with fewest live; basis row; `Enrolled`; Lead `new`→`queued` | N/A |
| 100 enrollments | A and B active | 50 each | N/A |
| Inactive Variant | B inactive | every Enrollment gets A | N/A |
| FR-63 | Contact's Sequence completed without Reply 30 days ago | `not_eligible` with `recontact_not_allowed` | nothing written |
| Gate blocks | suppressed address / erased Contact | `blocked` with reasons | no Enrollment, counter unchanged, no job |
| Second enroll | Lead has a live Enrollment | `lead_already_enrolled` | index also refuses a raw duplicate |
| Merge | both Leads have a live Enrollment | `MergeRefused` | one or none live: merge proceeds, loser's Enrollments move to the survivor |
| Suppression | Contact suppressed (any source) | live Enrollment `stopped`/`suppressed`; repeat is a no-op | then Lead closed `lost` for opt-outs |
| Lead close | `Pipeline.close(lost/disqualified)` | live Enrollment `stopped`/`lead_closed` (also when already closed) | N/A |
| Erasure | `ContactErased` with a live Enrollment | `stopped`/`contact_erased` | N/A |
| Advance | Lead at `queued`+ , closed or parked | `advance_to_at_least(queued)` no-op, no event | target without an automatic move raises `ValueError` |

</frozen-after-approval>

## Code Map

- `nextron/db/uow.py` -- `@command`/`@use_case`, `current().publish` (synchronous, same transaction, `NOTIFICATIONS` only). Tests wrap `enroll` in a use case + `transaction()`.
- `nextron/db/eventlog.py:76` -- `append(entity, entity_id, from_status, to_status, cause, cause_ref, actor, at)`.
- `nextron/domain/sending_inbox.py`, `campaign.py`, `machines.py` -- machine template (`MACHINE`, StrEnums, `CREATION`, `_rows`, `TRANSITIONS` MappingProxyType, `transition()`); copy for `enrollment.py`. All-pairs test pattern: `tests/unit/test_pipeline_stage.py`.
- `nextron/domain/pipeline_stage.py` -- `ADVANCEMENT`, `CLOSED_STAGES`, `PARKED_STAGES`, events `ENROLL`/`SEND`/`REPLY`/`HOT_REPLY`; add `advance(current, target)`.
- `nextron/app/pipeline/api.py` -- `Pipeline.close` (docstring: "Story 4.8 adds stopping the Lead's Enrollments"), `close_suppressed`; pipeline may import `app.sequencing` (c6 DAG: pipeline > sequencing; sequencing must not import pipeline or discovery).
- `nextron/app/compliance/api.py:604` -- `evaluate_eligibility` builds `EligibilityContact` without `history`; add `history=sequencing_queries.ended_sequences(contact_id)`. Keep contract (e) and the no-`timedelta` rule.
- `nextron/domain/eligibility.py:362` -- `EndedSequence(last_step_at, ended_at, replied)`; `Decision`, `Reason`, `basis_country`.
- `nextron/app/research/api.py:795` -- `record_eligibility_basis` (checks Market, basis country, LIA; trusts the Decision).
- `nextron/app/queries/setup.py` -- `campaign`, `campaign_active`, `SeededCampaign`/`SeededVariant` (ordered by name), `markets()`; add `sequence_by_id(id) -> SeededSequence | None` over a new `db/repos/setup/sequences.py` `by_id`. `nextron/domain/campaign.py`: `needs_lia`, `VariantStatus`.
- `nextron/app/setup/api.py:1436` -- `_today` (date in `sender_timezone`, UTC fallback): copy for the local date.
- `nextron/app/queries/sending.py:61` -- `inboxes_for_binding()`; add placeholder `cold_daily_caps(local_date) -> Mapping[str, int]` (0 for each bindable inbox).
- `nextron/app/queries/research.py` `contact`, `queries/discovery.py` `lead`, `queries/pipeline.py` `stage`.
- `nextron/app/discovery/api.py:326` -- `merge`: add the live-Enrollment refusal before `leads_repo.mark_merged`.
- `nextron/app/sequencing/api.py`, `app/queries/sequencing.py`, `db/repos/sequencing/__init__.py` -- empty stubs; rewrite docstrings.
- `nextron/domain/notifications.py:110` -- `Enrolled(enrollment_id, lead_id)` already declared.
- `nextron/main.py:1607` -- `build_subscribers`; pinned by `tests/unit/test_serve.py:774,805`.
- `nextron/db/alembic/versions/0018_4_7_sending_inboxes.py` (table template), `0022_3_3_suppression_entries.py:89` (partial unique index), `0032_3_13_erase_contacts.py:184` (erased-insert trigger to copy).
- Tests to update: `tests/contracts/test_ad16_callers.py` (scenario per `_add_new` :924/:1050; `test_the_built_callers_are_the_epic_2_paths` :1119; insert-scan models :1234), `test_persistence_contracts.py` (HEAD :81, chain probes :801-819 shift by one, lead-scoped columns :537, reparenting owners), `tests/unit/test_migrations.py:39`, `tests/app/test_serve_process.py:360`, `tests/contracts/test_source_tree.py` `FILES`, `tests/contracts/test_erasure_contracts.py` (subscriber count). Eligible-Contact fixture: `tests/app/test_eligibility_basis.py:149`; inbox registration: `tests/app/test_sending_inboxes.py`; Campaign activation: `tests/app/test_campaigns_panel.py`.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/db/alembic/versions/0033_4_8_enrollments.py` -- revision `0033_4_8_enrollments`, down `0032_3_13_erase_contacts`; `enrollments` (FKs to leads, contacts, campaigns, variants, sending_inboxes, sequences, offers, no cascade; CHECKs on origin/status/causes, `(status='paused') = (pause_cause IS NOT NULL)`, `(status='stopped') = (stop_cause IS NOT NULL)`, `(status IN ('stopped','completed')) = (ended_at IS NOT NULL)`, `ended_at >= last_step_sent_at`, `sequence_version >= 1`); partial unique `uq_enrollments_one_live_per_lead` on `lead_id` WHERE status IN ('active','paused'); indexes on `contact_id`, `sending_inbox_id`; trigger refusing a change to any bound column; trigger refusing an insert for an erased Contact; `variant_counters(campaign_id PK→campaigns, last_variant_id→variants, updated_at)`. `downgrade()` raises.
- [x] `nextron/db/models/sequencing.py` + `models/__init__.py`, `db/owners.py`, `db/pii_registry.py` (all `none`) -- mirror the migration.
- [x] `nextron/domain/enrollment.py` -- statuses, origins, pause/stop causes, events (`pause_<cause>`, `resume`, `stop_<cause>`, `complete` from active or paused), `CREATION`, `LIVE_STATUSES`, `transition()`, `RefusalCode` + `EnrollRefusal`, `NewEnrollment`, `next_variant(variants, last_variant_id)`.
- [x] `nextron/domain/sending_policy.py` -- `capacity_forecast(cold_daily_cap, follow_ups_due) -> int` (≥0, reserves follow-ups first), `InboxLoad`, `pick_inbox(loads) -> str | None` (most capacity, fewest live, earliest registered, id).
- [x] `nextron/domain/pipeline_stage.py` + `nextron/app/pipeline/api.py` -- `advance()`; `@command advance_to_at_least(lead_id, stage) -> bool`; `@command on_enrolled(n: Enrolled)`; `close` calls `Sequencing(clock).stop(lead_id=…, cause=lead_closed, cause_ref=…)` before its early return.
- [x] `nextron/db/repos/sequencing/{enrollments,variant_counters}.py`, `nextron/app/queries/sequencing.py` -- repos (no setter for bound columns); queries `enrollment(id)`, `live_enrollment(lead_id)`, `ended_sequences(contact_id) -> tuple[EndedSequence, ...]`, `live_counts_by_inbox()`.
- [x] `nextron/app/sequencing/api.py` -- `Sequencing(clock)`: `enroll`, `stop(*, cause, enrollment_id|lead_id|contact_id, cause_ref=None) -> tuple[str, ...]`, `stop_suppressed(n)`, `erase_pii(n: ContactErased)` (stops with `contact_erased`), `reparent_lead(n: LeadsMerged)`.
- [x] `nextron/app/compliance/api.py`, `queries/setup.py`, `db/repos/setup/sequences.py`, `queries/sending.py`, `app/discovery/api.py` -- history, `sequence_by_id`, `cold_daily_caps`, merge refusal.
- [x] `nextron/main.py` -- register `Sequencing.stop_suppressed` before `Pipeline.close_suppressed`; `Sequencing.reparent_lead` after Research's; `Sequencing.erase_pii` after the existing hooks; `Pipeline.on_enrolled` for `Enrolled`. Update its docstring.
- [ ] Tests -- `tests/unit/test_enrollment.py` (all pairs, `next_variant`), `tests/unit/test_sending_policy.py` (fixed date), `tests/unit/test_pipeline_stage.py` (`advance`), `tests/app/test_enroll.py` (matrix rows, refusals change nothing, 100→50/50, FR-63, basis row incl. GB LIA via a stubbed eligible Decision if no UK Contact can be eligible, `Enrolled` subscriber, trigger and no-setter checks), `tests/app/test_enrollment_stops.py` (suppression, close, erasure, merge), AD-16 scenario, contract and HEAD updates.
- [x] `nextron-engine/README.md` -- Story 4.8 section.

**Acceptance Criteria:**
- Given the migration at head, when the schema is inspected, then both tables, the partial unique index and the triggers exist and every column is mapped and classified.
- Given the AD-16 contract, when it runs, then `sequencing.enroll` has a scenario: `Allowed` gates before the first write; `Blocked` adds no row to any table, writes nothing and enqueues no job.
- Given a successful enroll, when it commits, then exactly one Enrollment, one `enrollments` event, one `eligibility_basis` Provenance Record and (from `new`) one `leads` event exist.
- Given any refusal, when it returns, then every table's row count is unchanged.

## Implementation Notes

- **Migration** `0033_4_8_enrollments` (down `0032_3_13_erase_contacts`): `enrollments` with every listed column, FK and CHECK (`ck_enrollments_origin/_status/_pause_cause/_stop_cause/_paused/_stopped/_ended/_ended_after_last_step/_sequence_version`), `uq_enrollments_one_live_per_lead`, indexes on `contact_id` and `sending_inbox_id`; triggers `trg_enrollments_bound` (refuses a change to `id` or any bound column) and `trg_enrollments_erased_insert`; `variant_counters`. Model `db/models/sequencing.py`; owners and PII (all `none`) mapped.
- **Domain:** `domain/enrollment.py` (machine: `stop_<cause>` from active/paused, `pause_<cause>` from active, `resume`, `complete` from active or paused; `RefusalCode`, `EnrollRefusal`, `NewEnrollment`, `next_variant`); `domain/sending_policy.py` (`capacity_forecast(cold_daily_cap, follow_ups_due)`, `InboxLoad`, `pick_inbox`); `pipeline_stage.advance` + `AUTOMATIC_EVENTS` (queued, contacted, replied, interested).
- **App:** `Sequencing.enroll/stop/stop_suppressed/erase_pii/reparent_lead`; `queries.sequencing.enrollment/live_enrollment/ended_sequences/live_counts_by_inbox`; `queries.setup.sequence_by_id`; `queries.sending.cold_daily_caps` (0 per bindable inbox; Epic 6 replaces it); `Compliance.evaluate_eligibility` passes `history`; `Discovery.merge` refuses when both Leads are live; `Pipeline.advance_to_at_least(lead_id, stage, cause_ref=None)`, `on_enrolled`, and `close` stops the Lead's Enrollment (`lead_closed`) before its already-closed return. `main.build_subscribers`: `Sequencing.reparent_lead` after Research's, `stop_suppressed` before `Pipeline.close_suppressed`, `erase_pii` last for `ContactErased`, `Pipeline.on_enrolled` for `Enrolled`.
- **Deviations:** `variant_counters` has a ULID `id` PK plus a unique `campaign_id` (the persistence contract requires a ULID `id` PK). `advance_to_at_least` takes an optional `cause_ref` (the enrollment id). `stop` with an unknown `enrollment_id` raises `LookupError`; an unknown Lead or Contact stops nothing. `advance` never moves `not_now` (only the recontact event does). `no_active_variant` is unreachable from stored data (a Campaign with no active Variant fails to decode with `InvalidCampaigns`); it is kept as a defence and tested with a stub. `steps` is validated (ascending, unique, within the Sequence), not stored.
- **Contracts touched:** AD-16 scenario for `sequencing.enroll` (with `Allowed`, two gate calls: `enroll`'s and `evaluate_eligibility`'s; `Blocked` writes nothing); built-callers set and insert-scan models; lead-scoped columns gain `enrollments.lead_id`, reparenting owners gain `sequencing`; the erasure contract's subscriber count only additionally allows `sequencing` (no other check changed); HEAD and chain probes shifted; `FILES`.
- **Review patches (pass 1):** `reparent_lead` stops the moved live Enrollment with `lead_closed` when the survivor is closed; `ended_sequences` raises `RuntimeError` instead of an `assert`; tests for the merge refusal through `upsert_lead` and for each `ck_enrollments_*` CHECK; a fake sender identity in the AD-16 fixture.
- **Export (3.15):** not in this tree. At merge, `enrollments` (it references `contacts.id`) needs a 3.15 `COVERAGE` entry and an exporter.

## Spec Change Log

## Review Triage Log

Pass 1 (blind hunter BH, edge-case hunter EC, verification gap VG): 26 raw, 20 after dedup; 5 patched, 0 deferred, 15 rejected.

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | BH1, EC1: a merge moves the loser's live Enrollment onto a survivor that is closed | medium | `reparent_lead` moves rows unconditionally; `Pipeline.reparent_lead` keeps a closed survivor closed, so a live Enrollment sits on a `lost`/`disqualified` Lead | patch: stop the moved live Enrollments with `lead_closed` when the survivor is closed |
| 2 | BH7: `ended_sequences` relies on a bare `assert` | low | removed under `python -O`; a direct correction | patch: explicit raise |
| 3 | VG1: the both-live merge refusal is untested through `upsert_lead`/`canonicalize_lead` | medium | only a direct `merge` test exists | patch: test that an upsert matching two enrolled Leads raises `MergeRefused` and writes nothing |
| 4 | VG2: no test refuses a row violating the `ck_enrollments_*` CHECKs | medium | no `ck_enrollments` in tests; drift check ignores CHECKs | patch: parametrized CHECK test |
| 5 | BH13: AD-16 fixture uses a real-looking sender identity | low | `Furqaan Ahmed` / `furqaan@nextron-ai.com`; a direct rename | patch: obviously fake values |
| 6 | BH3, EC2: the sweep labels any gate block `suppressed` | low | other block reasons (lead closed, erased) already stop their Enrollments, so only inconsistent state reaches it; fix adds a guard | reject |
| 7 | BH4, EC3, VG-other: the sweep gates every live Enrollment per suppression | low | decision 7 accepts it at current scale (tens to hundreds of live rows) | reject |
| 8 | BH5: the named Contact is stopped without asking the gate | false | the notification means that person was suppressed; stopping is the conservative intent (decision 7) | reject |
| 9 | BH2, EC4: `steps` validated, not stored | false | spec: 4.9 hands the argument to `drafting.request_steps` in the same call; nothing needs storing | reject |
| 10 | BH6: `stop` with an unknown `lead_id`/`contact_id` returns `()` | low | every in-tree caller passes a stored id; `Pipeline.close` raises `LookupError` for an unknown Lead anyway | reject |
| 11 | BH8, EC6: a missing Market makes the LIA `None` | false | `record_eligibility_basis` re-reads the Campaign's Market and refuses a GB basis without the LIA, and Eligibility needs the Market first | reject |
| 12 | EC7: `campaign_active` and the LIA choice read the country differently | false | both read the live Market row in the same transaction | reject |
| 13 | EC8: a concurrent enroll gets an `IntegrityError` | false | `BEGIN IMMEDIATE` serializes writers; the index is the spec's backstop | reject |
| 14 | EC5: a Lead with no stage row reads as `lead_already_replied` | low | every Lead gets its stage at creation (`record_created`); unreachable | reject |
| 15 | EC9: `variant_counters` has a ULID `id` PK, not `campaign_id` | low | the persistence contract requires a ULID `id` PK; a unique `campaign_id` keeps one row per Campaign (Implementation Notes) | reject |
| 16 | EC10: model `BOUND_COLUMNS` omits `id` that the trigger guards | low | documentation only; no contract compares them | reject |
| 17 | BH9: a bad `sender_timezone` silently falls back to UTC | low | same fallback as `Setup._today` (pre-existing pattern); caps are 0 until Epic 6 | reject |
| 18 | BH10: the DB doesn't check that the Variant/Offer belongs to the Campaign | low | `enroll` is the only writer and takes both from the Campaign read; a composite FK adds complexity | reject |
| 19 | BH11: Pipeline calls Sequencing directly instead of a notification | false | the spine says `close` also stops Enrollments and the c6 DAG allows pipeline → sequencing; AD-3's notification list has no "Lead closed" | reject |
| 20 | BH12: assorted untested paths (timezone fallback, `sequence_by_id` bad ids, operator stop actors, lead-only notification) | low | each is a small read or mapping exercised indirectly; adding tests is not a defect fix | reject |

## Design Notes

`next_variant` walks the Campaign's Variants in name order, starting after the counter's `last_variant_id` and wrapping, and returns the first `active` one (no counter row: the first active). `ended_sequences` builds `EndedSequence(last_step_sent_at, ended_at, stop_cause in {reply, manual_reply})` for ended rows with a sent Step, ordered by `ended_at`. The local date for `pick_inbox` is now in `sender_timezone` (UTC fallback); follow-ups due are `{}` until 4.9 (a private helper in `app/sequencing/api.py` that 4.9 fills from `queries.drafting`).

## Verification

**Commands:**
- `cd e:/Nextron-track3/nextron-engine && uv run pytest -q tests/unit/test_enrollment.py tests/unit/test_sending_policy.py tests/unit/test_pipeline_stage.py tests/app/test_enroll.py tests/app/test_enrollment_stops.py tests/app/test_pipeline_close.py tests/app/test_eligibility.py tests/unit/test_serve.py tests/unit/test_migrations.py` -- all pass
- `uv run pytest -q tests/contracts` -- all pass
- `uv run lint-imports` -- all contracts kept
