---
title: 'Story 5.2: Hold each approved Draft as exactly one queued OutboundMessage'
type: 'feature'
created: '2026-11-19'
status: 'done'
baseline_commit: 'e9ab78130308a54584620b598690cd32494264e5'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-nextron-outreach-engine-2026-11-14/ARCHITECTURE-SPINE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-5-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** An approval (5.4) needs somewhere safe to put the approved message: one row holding its own copy of the exact approved text, which delivery (6.9/6.10) reads instead of the Draft, and which can never exist twice for one Draft.

**Approach:** Migration `0048_5_2_outbound_messages` adds `outbound_messages` (owner `sending`); `domain/outbound_message.py` is the AD-4 OM machine; `Sending.enqueue(draft, approval)` copies the composed approved revision into one `queued` OM with its unsubscribe token and AD-4 snapshot; `Sending.cancel(om_id)` cancels a `queued` OM; `sending.erase_pii` tombstones an erased Contact's copies. Nothing sends.

## Boundaries & Constraints

**Always:**
- **Table** (ULID `id`, later the `X-Nextron-Message-Id`): `draft_id` (FK drafts), `approval_id` (ULID, no FK until 5.4's `approvals`), `kind` (CHECK `step|recontact|reply|rebook`), `status` (CHECK the five statuses), `to_address`, `inbox_id` (FK sending_inboxes), `contact_id`, `lead_id`, `enrollment_id` (FKs; `enrollment_id` nullable), `subject`, `body`, `approved_hash`, `unsub_token`, snapshot `tier`, `lead_score`, `variant_id`, `segment_id`, `market_id`, `step_no` (nullable), `created_at`, `cancelled_at`, `sent_at` (NULL). CHECK `cancelled_at IS NOT NULL` iff `status = 'cancelled'`. Partial unique index `uq_outbound_messages_live_draft` on `draft_id` WHERE `status <> 'cancelled'`. Triggers abort DELETE and any UPDATE of a copy or snapshot column, except `to_address`, `subject`, `body`, `unsub_token` changing to `[erased]` (the 4.12 redaction-only pattern); `status`, `cancelled_at`, `sent_at` stay updatable.
- **Machine** (`OmStatus`, `OmEvent`, `OmCause`, `TRANSITIONS`, `transition()` raising `IllegalTransition`; `CREATION = Transition(queued, enqueued)`): `claim` queued→sending (`gate_passed`); `confirm_sent` sending|unknown→sent (`sent`); `mark_unknown` sending→unknown (`not_found`); `restore` queued|sending→unknown (`restored`); `requeue` unknown→queued (`operator_requeued`); `operator_cancel` unknown→cancelled (`operator_cancelled`); `cancel` queued→cancelled (`unapproved`). Terminal: `sent`, `cancelled`.
- **`enqueue(draft, approval)`** (`@command`, joins the caller's UoW): re-reads the Draft by id; then, before any write, raises — rolling the whole transaction back — when: kind isn't `step`/`recontact` or there is no Enrollment (`UnsupportedKind`); the Draft isn't `approved`, or `approval.draft_id`/`approval.rev` aren't this Draft and its `current_rev` (`NotApproved`); the Draft already has a non-cancelled OM (`AlreadyQueued`); `assert_contactable(ContactSubject, "email", purpose)` is `Blocked` (`ContactBlocked`; purpose `cold` Step 1, `follow_up` Step >1, `recontact`); `render.text_hash(copy)` ≠ `approval.text_hash` (`HashMismatch`). The copy = `render.compose(current revision, its footer snapshot, Contact's current email)` with the Enrollment's `sending_inbox_id`. Then: issue the token with `compliance.issue_unsub_token(copy)` (new OM id), insert the OM (`approved_hash = approval.text_hash`, snapshot: scoring's stored `tier`/`lead` score or NULL, Enrollment `variant_id`, Lead `segment_id`, Contact `market_id`, Draft `step_no`), and append `events` (`outbound_messages`, None→`queued`, cause `enqueued`, `cause_ref` the approval id, actor `system:sending`). Returns the OM id.
- **`cancel(om_id)`**: `queued` → `cancelled` with `cancelled_at` and an `events` row (`cancel`/`unapproved`), returns `True`; already `cancelled` → `False`, no write; `sending`, `unknown` or `sent` → `AlreadySending` ("already sending"), unchanged; unknown id → `LookupError`. The status update is conditional on `status = 'queued'`.
- **Erasure**: module-level `@command erase_pii(n: ContactErased)` registered in `main.build_subscribers` tombstones `to_address`, `subject`, `body`, `unsub_token` of every OM of the Contact to `[erased]`, keeping ids, statuses, timestamps, `approved_hash`, the snapshot. `ERASURE["outbound_messages"]` TOMBSTONED. PII classes: those four `redact`, the rest `none`. Export `COVERAGE["outbound_messages"] = Exported("sending", "outbound_messages", withheld={"unsub_token": …})` via `queries.sending.export_pii`.
- `Sending(clock, *, env, compliance=None)`; `enqueue` raises `TypeError` without one; `main` passes its codec-bearing `Compliance`. Reads: `queries.sending.outbound_message(id)`, `live_outbound_of_draft(draft_id)`.

**Never:** call `MailPort` or send anything; an `approvals` table, approve command or UI (5.4); cancelling approved Drafts in `cancel_pending` (5.4 AC); threads, `thread_id`, `blocked_by`, `send_started_at`, Gmail ids (6.7/6.10); a health-panel OM count; a new dependency.

### Decisions made overnight (pending user review)
1. `approvals` arrives in 5.4, so `enqueue` takes any `approval` with `approval_id`, `draft_id`, `rev`, `text_hash` (a Protocol); `approval_id` has no FK.
2. Fail closed: `enqueue` requires the Draft already `approved` and the approval bound to its current revision.
3. Reply/rebook/channel Drafts (inbox from a Thread, 6.7/Epic 7) and any Draft without an Enrollment raise `UnsupportedKind`.
4. `enqueue` calls `assert_contactable` itself: AD-16's insert scan guards `outbound_messages`, and a manifest entry `sending.enqueue` is added.
5. `cancel` of a `sent` OM also raises `AlreadySending`; a repeat cancel is a no-op.
6. `unsub_token` decodes to the person's address hash, so it is `redact` (tombstoned) and withheld from the export.
7. Erasure keeps OM statuses (AC); the gate blocks an erased Contact's `queued` OM, and 5.4's cascade cancels it.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Error handling |
|---|---|---|---|
| Happy | approved Step 1 Draft, matching approval | one `queued` OM, copy = compose, token row with `om_id`, `events` row | N/A |
| Hash mismatch | Contact email changed after approval | nothing written (no OM, no token) | `HashMismatch` |
| Twice | second `enqueue` for the same Draft | nothing written | `AlreadyQueued` |
| Direct insert | raw INSERT of a second live OM | refused | `IntegrityError` |
| After cancel | OM cancelled, re-enqueue | new `queued` OM allowed | N/A |
| Reply Draft | kind `reply` / no Enrollment | nothing written | `UnsupportedKind` |
| Not approved | Draft `ready`, or approval for another rev | nothing written | `NotApproved` |
| Suppressed | Contact suppressed | nothing written | `ContactBlocked` |
| Cancel | `queued` / `cancelled` / `sending` / `unknown` | cancelled / no-op / unchanged | — / — / `AlreadySending` |
| Erasure | ContactErased | 4 columns `[erased]`, rest kept | N/A |
| Tamper | UPDATE `body` to other text; DELETE | aborted | trigger |

</frozen-after-approval>

## Code Map

All under `nextron-engine/`.
- `nextron/db/alembic/versions/0048_5_2_outbound_messages.py` (new; down `0047_4_14_ai_grounding`); redaction-only trigger pattern in `0045_4_12_draft_revisions.py` / `0032_3_13_erase_contacts.py`. Model in new `nextron/db/models/sending.py` section or existing sending models module. `db/owners.py` (sending owns the table), `db/pii_registry.py`.
- New `nextron/db/repos/sending/outbound_messages.py`: `insert`, `get`, `live_of_draft`, `set_status(id, from_status, to_status, at)` (conditional), `tombstone_contact(contact_id)`, `for_export(contact_id)`.
- New `nextron/domain/outbound_message.py` (pattern: `nextron/domain/draft.py`; `IllegalTransition`, `Transition` from `domain/machines.py`); errors as `DomainError` subclasses in the sending API (`domain/errors.py:30`).
- `nextron/app/sending/api.py` -- `Sending.enqueue`, `Sending.cancel`, module `ERASURE` + `erase_pii`, docstring. Reads via `queries.drafting.draft/current_revision/footer_snapshot`, `queries.sequencing.enrollment` (`sending_inbox_id`, `variant_id`, `contact_id`, `lead_id`), `queries.research.contact` (`email`, `market_id`, `erased_at`), `queries.discovery.lead` (`segment_id`), `queries.scoring.stored` (`tier`, `lead`). Compose exactly as `app/drafting/api.py:381-384`. `Compliance.issue_unsub_token` (`app/compliance/api.py:748`, takes `.id`, `.to_address`); `compliance_api.assert_contactable` (`:959`), `ContactSubject`, `Purpose` (`domain/contactability.py:365`). c6 allows sending → compliance/queries only.
- `nextron/app/queries/sending.py` -- `outbound_message`, `live_outbound_of_draft`, `export_pii`; `nextron/app/compliance/api.py:~830` export assembly adds `"sending"`; `nextron/domain/contact_export.py` `COVERAGE` (+ docstring table).
- `nextron/main.py` -- `build_subscribers` registers `sending_api.erase_pii` for `ContactErased` (after `Sequencing.erase_pii`); `Sending(clock, env=..., compliance=compliance)` at :1383; docstring.
- Tests to bump: HEAD in `tests/contracts/test_persistence_contracts.py`, `tests/unit/test_migrations.py`, `tests/app/test_serve_process.py`; chain probes; `tests/contracts/test_source_tree.py` FILES; `tests/contracts/test_erasure_contracts.py` probes; `tests/contracts/test_ad16_callers.py` (new `Caller("sending.enqueue", ("app/sending/",), ANY + "enqueue", inserts={"outbound_messages"})` + scenario); subscriber pins in `tests/unit/test_serve.py`; export tests.
- Test helpers: `tests/app/test_draft_revisions.py` `world`/`drafted`; `tests/app/test_due_queue.py`; a stub `TokenCodecPort` fake from `PORT_FAKES`/`tests/app/test_unsub_*`.

## Tasks & Acceptance

**Execution:**
- [ ] migration + model + owners + pii registry -- table, index, CHECKs, triggers.
- [ ] `nextron/domain/outbound_message.py` + `tests/unit/test_outbound_message.py` -- table and every illegal pair.
- [ ] repo, `app/sending/api.py`, `app/queries/sending.py` -- enqueue, cancel, erase, export.
- [ ] `main.py`, export coverage, compliance export assembly.
- [ ] `tests/app/test_outbound_messages.py` -- the I/O matrix on a migrated temp DB with a frozen clock (rollback proven by row counts in `outbound_messages`, `unsub_tokens`, `events`).
- [ ] pins and `nextron-engine/README.md` Story 5.2 section.

**Acceptance Criteria:**
- Given the migration at head, then the single-head test passes and every new column is mapped and classified.
- Given any module under `nextron/`, then only `app/sending` imports the OM repo, and nothing in this story calls `MailPort`.

## Implementation Notes

- **Migration** `0048_5_2_outbound_messages` (down `0047_4_14_ai_grounding`). Columns as the spec; `approval_id`, `variant_id`, `segment_id`, `market_id` have no FK (snapshot values / 5.4's table); `segment_id` NOT NULL, `variant_id`/`market_id`/`tier`/`lead_score`/`step_no`/`enrollment_id` nullable. CHECKs: `kind`, `status`, `cancelled` (`(cancelled_at IS NOT NULL) = (status = 'cancelled')`), `approved_hash` (64 lowercase hex), `tier` (NULL/A/B/C), `step_no` (NULL or ≥1), `step_slot` (step/recontact need `enrollment_id` and `step_no`). Indexes `uq_outbound_messages_live_draft` (partial, `status <> 'cancelled'`) and `ix_outbound_messages_contact_id`. Triggers: `trg_outbound_messages_no_delete`, `trg_outbound_messages_insert_new` (refuses a reused id or a second live OM per Draft, so `INSERT OR REPLACE` can't delete the first via the partial index), `trg_outbound_messages_immutable` (every column fixed except `status`, `cancelled_at`, `sent_at`, `lead_id`, and the four redact columns only to `[erased]`).
- **Model** `OutboundMessageRecord` in `nextron/db/models/sending.py`; owners (`sending`) and PII (`to_address`, `subject`, `body`, `unsub_token` redact; rest none).
- **Domain** `nextron/domain/outbound_message.py`: `OmStatus`, `OmKind`, `OmEvent`, `OmCause`, `TRANSITIONS`, `transition()`, `CREATION`, `TERMINAL`, `LIVE`.
- **Repo** `nextron/db/repos/sending/outbound_messages.py`: `new_om_id`, `insert`, `get`, `live_of_draft`, `set_status` (conditional; sets `cancelled_at`/`sent_at`), `move_lead`, `tombstone_contact`, `for_export` (drops `unsub_token`).
- **App** `nextron/app/sending/api.py`: `Sending(clock, *, env, compliance=None)`; `@command enqueue(draft | draft_id, approval)` in the spec's check order (UnsupportedKind → NotApproved → AlreadyQueued → gate → HashMismatch), then token, insert, event; `@command cancel(om_id)`; `@command reparent_lead(LeadsMerged)`; module `ERASURE` + `@command erase_pii`. Errors are `DomainError` subclasses under abstract `OmRefused` with codes `om_unsupported_kind`, `om_not_approved`, `om_already_queued`, `om_contact_blocked` (`.reasons`), `om_hash_mismatch`, `om_already_sending`. Unknown Draft/revision/footer snapshot/Contact/Lead → `LookupError`; non-ULID `approval_id` → `ValueError`; a Contact with no email → `HashMismatch`.
- **Queries** `queries.sending.outbound_message`, `live_outbound_of_draft`, `export_pii`; `Compliance.export_contact` adds the `sending` section; `contact_export.COVERAGE["outbound_messages"]` withholds `unsub_token`.
- **main**: `Sending(clock, env=config.env, compliance=compliance)` for the dashboard; `build_subscribers` registers `sending_api.erase_pii` for `ContactErased` (after drafting's, in the no-op loop) and the module-level `sending_api.reparent_lead` for `LeadsMerged` (after sequencing's; no `Sending` instance, so no fake `prod` env).
- **Tests**: `tests/unit/test_outbound_message.py`, `tests/app/test_outbound_messages.py` (I/O matrix, rollback via counts of `outbound_messages`/`unsub_tokens`/`events`, including a codec failure and a failure after the token is issued), AD-16 manifest entry + scenario `sending.enqueue`, insert-scan pins, HEAD/chain probes, source-tree FILES, erasure/export/reparent/subscriber pins, contact export seed + assertions.
- **Review patches (pass 1)**:
  - Migration 0048 (edited in place, unreleased) adds `trg_outbound_messages_final`: an `UPDATE` that changes the `status` of a `sent` or `cancelled` OM aborts ("a sent or cancelled OutboundMessage is final"), so a cancelled OM can't be revived.
  - `cancel`'s `events` row now has `cause_ref` = the OM's `approval_id`.
  - `reparent_lead` is a module-level `@command reparent_lead(n: LeadsMerged)` in `nextron/app/sending/api.py` (like `erase_pii`); `main.build_subscribers` registers it. The persistence contract's `reparenting_owners` also accepts a module function of `nextron.app.<owner>.api`; `test_serve` and `test_persistence_contracts` pins updated.
  - The OM repository imports `REDACTED` from `nextron.domain.erasure` and `OmStatus.CANCELLED`/`SENT` from `nextron.domain.outbound_message` instead of local copies.
  - New tests in `tests/app/test_outbound_messages.py`: a Contact with no email (`HashMismatch`), a non-ULID `approval_id` (`ValueError`), an unknown Draft (`LookupError`), an unknown Enrollment (`UnsupportedKind`), each writing nothing; `kind`/`approval_id`/`contact_id`/`step_no` fixed; `[erased]` never restored; `lead_id` updatable; a cancelled OM never revived and a sent one never moves; `INSERT OR REPLACE` reusing a cancelled OM's id refused, row unchanged; `cancel`'s lost race (`set_status` False) raises `AlreadySending` with no `events` row.
  - Full-suite gate fixes: `tests/unit/test_inboxes_page.py::test_build_app_builds_sending_on_its_clock_and_environment` now asserts `Sending` gets `env` and a `Compliance` holding a codec (closes VG3); `tests/app/test_erasure.py`'s `not_planted` lists the four `outbound_messages` redact columns (erased in `tests/app/test_outbound_messages.py`).
  - `tests/contracts/test_ad16_callers.py`'s `sending.enqueue` scenario catches `ContactBlocked` (the Blocked case raised out of the action); `test_serve`'s clock pin skips sending's module-level subscriber.

### Additional decisions made while implementing (pending user review)
8. `outbound_messages.lead_id` has an FK to `leads.id`, so the persistence contract requires a `LeadsMerged` handler: `sending_api.reparent_lead` moves the merged Lead's OMs to the survivor, and the immutability trigger leaves `lead_id` updatable (it is a reference, not part of the copy or snapshot).
9. A second-live-OM insert is refused by a trigger as well as the partial unique index, so `INSERT OR REPLACE` can't silently delete the live OM.
10. `enqueue` also accepts the Draft id as a plain string, not only an object with `.id`.

## Spec Change Log

## Review Triage Log

Pass 1 (blind hunter BH, edge-case hunter EC, verification gap VG): 23 raw, 20 after dedup; 9 patched, 1 deferred, 10 rejected.

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | EC1+BH3: `enqueue` doesn't refuse a non-`active` Enrollment or an unusable inbox | low | at approval time a ready Draft of a stopped Enrollment is already cancelled (4.9); 6.9's `enrollment_active` and `inbox_ready` block the send; a new refusal adds surface | reject |
| 2 | EC3: a step Draft with `step_no` None reaches the CHECK as a raw IntegrityError | false | `ck_drafts_step_slot` (`db/models/drafting.py:103`) requires `step_no` for step kinds | reject |
| 3 | EC4: a malformed `approval.text_hash` | false | it must equal `render.text_hash`'s hex digest or `HashMismatch` is raised first | reject |
| 4 | EC5+BH4: an erased or suppressed Contact's `queued` OM stays `queued` until 5.4's cascade | medium | real until 5.4 (decision 7); nothing sends before 6.10 and the gate's `contactable` blocks it | defer (5.4 must cancel it) |
| 5 | EC6: AC says only `app/sending` imports the OM repo; `app/queries/sending` does too | low | `queries.sending` is sending's own read module (c7 allows it); fix would edit the spec wording | reject |
| 6 | BH1: no CHECK ties `sent_at` to `status = 'sent'` | low | nothing writes `sent` before 6.10, which defines the send apply | reject |
| 7 | BH2: the DB lets a status leave `sent`/`cancelled` (a cancelled OM revived) | low | owner code uses a conditional update, but a raw or buggy write could re-queue a cancelled copy; one trigger condition closes it | patch |
| 8 | BH5: deferred item (unsubscribe through `unsub_tokens.om_id`) not done | low | pre-existing deferred entry, still listed | reject |
| 9 | BH6: refusal paths untested (no email, non-ULID approval id, unknown Draft/Enrollment) | low | grep finds no test for them | patch |
| 10 | BH7: trigger edge cases untested (`[erased]` back to text, `lead_id` free, other fixed columns) | low | `test_the_copy_and_the_snapshot_are_fixed` covers only some columns | patch |
| 11 | BH8: no-email refusal reads as a hash mismatch | false | its message is "the Contact has no email address"; the code fits (no address, no matching hash) | reject |
| 12 | BH9: subscriber `Sending(chosen, env="prod")` claims prod | low | the instance only reparents, but a later reuse would skip AD-14's allowlist; module-level `reparent_lead` like `erase_pii` removes it | patch |
| 13 | BH10: `cancel` writes `cause_ref = None` | low | the OM holds `approval_id`; direct correction | patch |
| 14 | BH11: `enqueue` reads the repo, `cancel` the query | low | style only, same data | reject |
| 15 | BH12: repo copies `REDACTED`/`CANCELLED`/`SENT` | low | drift risk; direct correction to import the domain values | patch |
| 16 | BH13: `OutboundMessageView` exposes `unsub_token` | false | 6.10's message builder needs it; no route renders the view | reject |
| 17 | BH14: a cancelled OM's token stays valid | low | the token was never sent, so nobody holds it | reject |
| 18 | VG1: the insert trigger's same-id clause (INSERT OR REPLACE of a cancelled OM) untested | medium | removing `id = NEW.id` passes every test | patch |
| 19 | VG2: `cancel`'s lost-race branch untested | medium | dropping the `set_status` check passes every test | patch |
| 20 | VG3: `build_app` passing the codec Compliance to `Sending` isn't pinned | medium | no test asserts the wiring; the full-suite gate found `tests/unit/test_inboxes_page.py`'s `build_app` pin stale, and updating it pins the codec-bearing `Compliance` | patch |

## Verification

**Commands:** (from `nextron-engine/`, `-n 2` max, never two pytest runs at once)
- `uv run pytest -q tests/unit/test_outbound_message.py tests/app/test_outbound_messages.py` -- pass
- `uv run pytest -q tests/contracts` -- pass
- `uv run lint-imports` -- all contracts kept
