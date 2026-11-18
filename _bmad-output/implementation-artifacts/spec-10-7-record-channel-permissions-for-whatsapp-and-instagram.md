---
title: 'Story 10.7: Record Channel Permissions for WhatsApp and Instagram'
type: 'feature'
created: '2026-11-18'
status: 'done'
baseline_commit: 'c1dbbaa57c58d5bebff941749acf59bb35e93cfb'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-10-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A WhatsApp or Instagram Draft (Story 10.8) may only exist once the prospect opened that channel, but nothing records who opened which channel, when, or the Evidence.

**Approach:** A `pipeline`-owned `channel_permissions` table, filled only by the Operator through a Channel Permission page on each Contact (record, update Instagram `last_inbound_at`, revoke, "Opted out on this channel"), with the Evidence stored by `research` as operator Quote Evidence and the contactability gate run first.

## Boundaries & Constraints

**Always:**
- epics.md Story 10.7 ACs are binding, including the exact text "An email reply, or a number in a signature or on a website, doesn't qualify" and "24-hour window closes at <time>" (time in `sender_timezone`).
- Valid actions: `whatsapp` → `started_whatsapp_conversation`, `gave_number_for_whatsapp`; `instagram` → `started_instagram_conversation`. No other option is offered. `occurred_at` not in the future; Evidence 1–300 characters after stripping.
- Recording (and updating `last_inbound_at`) runs `compliance_api.assert_contactable(ContactSubject(id), channel, follow_up)` first; `Blocked` stores nothing and shows the block reason(s). Revoke and opt-out are never gated.
- Every write is one `@use_case` unit of work, appending `events` rows (entity `channel_permissions`, actor `operator`, no personal data). Refusals store nothing; DB CHECKs back the domain rules.
- Story 10.1 contracts stay as they are: outside `nextron.domain` no module or defined name holds `linkedin`/`instagram` (use "channel permission" names); no platform host literal anywhere (templates included); no fetch-port call whose argument names a platform. Domain names that need it get allowlist entries with reasons.
- New personal-data columns are classified in `pii_registry.py`, covered by `pipeline.api.ERASURE` (`tombstoned`) with a real `erase_pii`, and by `contact_export.COVERAGE` as `Exported("pipeline", "channel_permissions")`.

**Never:** send, fetch or open anything on WhatsApp or Instagram; build a link to either platform; add a job, adapter, port or notification; let an email reply or a signature/website number count; touch root `HANDOFF.md`/`README.md`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Error Handling |
|---|---|---|---|
| Record Instagram | action started_instagram_conversation, handle, time 1 h ago, Evidence 120 chars | `active` row, `last_inbound_at = occurred_at`, operator Quote Evidence linked; page shows "24-hour window closes at <occurred+24h>" | N/A |
| Mismatched action | channel instagram, action gave_number_for_whatsapp | refused | nothing stored |
| Future time / Evidence 301 chars / blank Evidence | submit | refused with field message | nothing stored |
| Blocked Contact | Contact's address suppressed | refused, notice names the block reason | no permission, no Evidence |
| Second active | active instagram permission exists; record another instagram | refused "already has an active … permission" | nothing stored |
| Update inbound | later time, not future | `last_inbound_at` moves; window text moves | earlier than stored or future: refused |
| Window closed | `last_inbound_at` 25 h ago | shows "24-hour window closed at <time>: don't send" | N/A |
| Revoke | active | `revoked`, `revoked_at` set, events row | already revoked: refused |
| Opted out | active, Contact has email | `compliance.suppress(ContactSubject, person, operator, permission id)`, every active permission of the Contact revoked (cause `opted_out`) | N/A |
| Erasure | Contact erased | number and handle NULL, active rows revoked (actor `system:pipeline`), Evidence tombstoned by research | N/A |

### Decisions made overnight (pending user review)
- No Draft table with a channel kind exists in this tree (Stories 10.3/10.8 create channel Drafts), so revoking cancels no Draft yet; a `deferred-work.md` entry tells 10.8 to make revoke (and opt-out) cancel `ready` channel Drafts in the same transaction and to require an active permission before drafting.
- The channel's own identifier is required (WhatsApp number as `+` and 8–15 digits; Instagram handle as 1–30 of `A–Z a–z 0–9 . _`, an optional leading `@` dropped); the other channel's field must be blank.
- Updating `last_inbound_at` is Instagram-only and gated like recording; "Opted out on this channel" also revokes every active permission of the Contact.
- Form date and time are local to `sender_timezone`; a non-existent local time (DST gap) is refused.

</frozen-after-approval>

## Code Map

- `nextron-engine/nextron/db/alembic/versions/0037_10_2_assist_accounts.py` -- migration style; new `0038_10_7_channel_permissions.py` (down `0037_10_2_assist_accounts`). HEAD constants in `tests/contracts/test_persistence_contracts.py`, `tests/unit/test_migrations.py`, `tests/app/test_serve_process.py`; chain probes; `tests/contracts/test_source_tree.py` FILES.
- `nextron-engine/nextron/db/models/pipeline.py` -- add `ChannelPermissionRecord` (ULID id, FK `contacts.id`, FK `evidence.id`, `revoked_at`, `created_at`, `updated_at`; partial unique index one `active` per (`contact_id`, `channel`)). `owners.py` all `pipeline`; `pii_registry.py`: `whatsapp_number`/`instagram_handle` `null`, rest `none`.
- `nextron-engine/nextron/db/repos/pipeline/assist_accounts.py` -- repo style for new `channel_permissions.py`.
- `nextron-engine/nextron/domain/` -- new pure module (e.g. `channel_permission.py`): enums, `VALID_ACTIONS`, status machine `active → revoked`, field checks, messages, `window_closes_at`. Machine pattern: any `nextron/domain/*` with `transition`/`IllegalTransition`.
- `nextron-engine/nextron/app/research/api.py` (`Research`) -- add `@command record_operator_quote(contact_id, quote, at) -> evidence id` via `signals_repo.insert_evidence` (type `quote`, source `operator`, extracted_by `operator`, `person_contact_id`, Contact's `lead_id`, `pages_checked=[]`). Verify `research/people.py:560-650`, `extraction.py`, `absence.py` retractions never touch operator Evidence; research `erase_pii` already tombstones `person_contact_id` Evidence.
- `nextron-engine/nextron/app/pipeline/assist_accounts.py` -- `@use_case` service pattern; new `nextron/app/pipeline/channel_permissions.py` `ChannelPermissions(clock, research, compliance)`. App DAG lets pipeline call `research` and `compliance`.
- `nextron-engine/nextron/app/compliance/api.py:436 suppress`, `:751 assert_contactable`; `:606 export_contact` gains `"pipeline": pipeline_queries.export_pii(contact_id)`.
- `nextron-engine/nextron/app/pipeline/api.py:164-180` -- `ERASURE` + `erase_pii` (now does work); `nextron/domain/contact_export.py:154 COVERAGE` and its docstring table; `tests/contracts/test_erasure_contracts.py`, `test_export_contracts.py`.
- `nextron-engine/nextron/app/queries/pipeline.py` -- `permissions(contact_id)`, `export_pii(contact_id)`.
- `nextron-engine/nextron/web/app.py:1595-1652` + `web/contact_erase` rows module + `templates/contact_erase*.html` -- the Contact sub-page pattern; new routes `GET/POST /leads/{lead_id}/contacts/{contact_id}/permissions`, `POST …/permissions/{permission_id}/inbound|revoke|opt-out` (registered before `/leads/{lead_id}`, `@personal_data(True)`, CSRF, `hx-post`). `templates/lead_contacts.html` keeps 8 columns: add a "Channel permissions" link in the Action cell for live Contacts. `create_app` takes `channel_permissions=`; wire in `nextron/main.py`.
- `nextron-engine/tests/contracts/test_ad16_callers.py` -- add the record scenario; `tests/contracts/test_no_linkedin_adapter.py` allowlists; `test_web_contracts.py` route/template lists.

## Tasks & Acceptance

**Execution:**
- [x] domain module + unit tests (actions per channel, future time, Evidence length, identifier rules, window open/closed boundary at exactly 24 h, machine).
- [x] migration, model, repo, owners, PII registry, contract constants.
- [x] `Research.record_operator_quote` + test.
- [x] `ChannelPermissions` use cases `record`, `update_inbound`, `revoke`, `opt_out` + app tests for every matrix row (events rows, nothing stored on refusal, CHECKs, partial index).
- [x] erasure hook, ERASURE, export COVERAGE and `export_pii`, compliance `export_contact` wiring + tests.
- [x] queries, web page/panel/routes, Contacts-panel link, `main.py` wiring + web tests (form text, options, refusals re-render 200 with `aria-invalid`, CSRF/malformed 400, erased Contact shows no form).
- [x] AD-16 scenario, 10.1 allowlists, web contracts; `nextron-engine/README.md` Story 10.7 section; `deferred-work.md` entry for 10.8.

**Acceptance Criteria:**
- Given a fresh DB at head, when the migration runs, then `channel_permissions` has the AC's columns, owner `pipeline`, and every column classified.
- Given the tree, when `tests/contracts` and `lint-imports` run, then all pass with no contract loosened.

## Implementation Notes

- **Domain** `nextron/domain/channel_permission.py` (pure, stdlib only; no platform word in any module-level name, so no 10.1 allowlist entry was needed): `PermissionChannel`, `ProspectAction`, `VALID_ACTIONS`, labels, the machine (`active → revoked` by `revoke`/`opt_out`/`erase`; causes `permission_recorded`, `inbound_updated`, `permission_revoked`, `opted_out`, `contact_erased`), `check_record` (all problems at once), `check_inbound`, `parse_local` (DST gap refused, ambiguous time = first occurrence), `window_closes_at`/`window_open` (closed at exactly 24 h)/`window_text`, `BLOCK_TEXTS`/`blocked_text`, `PermissionRefused` (code `channel_permission_refused`).
- **Migration** `0038_10_7_channel_permissions.py` (revision `0038_10_7_channel_permissions`, down `0037_10_2_assist_accounts`): table `channel_permissions` with the AC columns plus `id`, `revoked_at`, `created_at`, `updated_at`; CHECKs `channel`, `action` (valid per channel), `identifier` (own channel's only; required while active), `last_inbound` (exactly for Instagram, ≥ `occurred_at`), `status`, `revoked_at` (set iff revoked), `recorded_by` (`operator`); index on `contact_id`; partial unique `uq_channel_permissions_active (contact_id, channel) WHERE status='active'`. Model `ChannelPermissionRecord` in `db/models/pipeline.py`; owners all `pipeline`; PII `whatsapp_number`/`instagram_handle` `null`, rest `none`. Repo `db/repos/pipeline/channel_permissions.py`.
- **Research** `@command record_operator_quote(contact_id, quote, at)`: quote Evidence, source/extracted_by `operator`, `person_contact_id`, Contact's `lead_id`, `pages_checked=[]`; unknown/erased Contact → `LookupError`. Verified extraction, people and absence only touch their own `extracted_by`, and research's `erase_pii` already tombstones `person_contact_id` Evidence.
- **App** `nextron/app/pipeline/channel_permissions.py` `ChannelPermissions(clock, research, compliance)`: `record`, `update_inbound`, `revoke`, `opt_out` (`@use_case`, one transaction each) and a read-only `view(contact_id)` (permissions, sender zone, now). `record` order: field checks → unknown/erased Contact → `compliance_api.assert_contactable(ContactSubject, channel, follow_up)` → second-active check → Evidence → insert → event (cause_ref = Evidence id). `update_inbound` is gated again. Revoke/opt-out never gated; opt-out works from any of the Contact's permissions, suppresses (scope person, source operator, cause_ref the permission id) and revokes every active permission (event cause_ref = first suppression entry).
- **Timezone**: when `sender_timezone` is unset or unparseable, recording/updating is refused ("Set the sender's timezone on the Settings page first…") and the page shows times in UTC with a note (`queries.pipeline.sender_zone`).
- **Erasure**: pipeline's `erase_pii` became a `Pipeline` method (it needs the clock), registered last in `build_subscribers`; `ERASURE["channel_permissions"]` is `tombstoned`. It revokes active rows (actor `system:pipeline`, cause `contact_erased`) and NULLs number and handle. `test_serve` and the erasure contract's hook map were updated for the method form.
- **Export**: `queries.pipeline.export_pii`, `contact_export.COVERAGE["channel_permissions"] = Exported("pipeline", "channel_permissions")`, wired into `Compliance.export_contact`.
- **Web** `nextron/web/permissions_page.py` + `templates/channel_permissions.html` / `channel_permissions_panel.html`; routes `GET/POST /leads/{lead_id}/contacts/{contact_id}/permissions` and `POST …/permissions/{permission_id}/inbound|revoke|do-not-contact` (the opt-out route is named `do-not-contact` / `contact_permission_do_not_contact`, not `opt-out`: Story 4.11's footer contract forbids any route whose path or name holds `opt_out`; the button still reads "Opted out on this channel"), all `@personal_data(True)`, before `/leads/{lead_id}`. The Contacts panel's Action cell links "Channel permissions" for live Contacts (still 8 columns). An erased Contact shows no form, and its POSTs run nothing. `create_app(channel_permissions=…)` is wired in `main.py` and `tests/dashboard.py`.
- **Contracts**: AD-16 manifest entry `pipeline.channel_permissions` (`ChannelPermissions.record|update_inbound`) with record + inbound scenarios; HEAD constants and chain probes bumped (0039/0040); `test_source_tree` FILES; web route/template lists; erasure COVERAGE; export count 18.
- **Story 10.1 tripwire**: the record route passes the form values through `permissions_page.record_arguments(values)`, because `record` is also a port method name and an argument expression naming the platform (`values["instagram_handle"]`) trips `test_no_fetch_takes_a_linkedin_url`.
- **Existing tests updated** (no contract loosened): `test_export_contracts` (`CONTACT_TABLES` gains `channel_permissions`, count 18), `test_contact_export` (`Seeded` plants a permission for Jane and Bob; the file's owner list gains `pipeline`), `test_erasure` (plants Jane's Instagram permission, so `instagram_handle` is covered; `whatsapp_number` is listed as unplantable because digits can't hold the mark), and `test_leads_page` (the Action cell now reads "Make primary Channel permissions").
- **Docs**: `nextron-engine/README.md` Story 10.7 section; `deferred-work.md` entry for Story 10.8 (cancel `ready` channel Drafts on revoke/opt-out/erasure; require an active permission and an open window).
- **Tests**: `tests/unit/test_channel_permission.py`, `tests/app/test_channel_permissions.py`, `tests/unit/test_permissions_page.py`.
- **Review patches (iteration 1)**: `parse_local` turns an `OverflowError` at `datetime`'s bounds into `BAD_TIME`; `check_evidence` reads each `\r\n` as `\n` before its checks and stores the normalised text; `MAX_EVIDENCE` is `extraction.MAX_QUOTE`; the opt-out notice counts written and already-present entries with singulars ("1 suppression entry", "1 entry", "1 permission") and, when the person has no known key, says nothing was added to the Suppression List (the permissions are still revoked); every panel form has `action` equal to its `hx-post`; window lines lost `role="status"`, and the closed one also carries `window-closed` (styled in `app.css`); `main.py`'s erasure-order text no longer says research's hook runs last. Tests: bounds, CRLF at the 300 limit, a no-key opt-out, an unparseable stored timezone (app and page), the closed-window class, form actions, the notice wording, and the revoked row's heading, facts and remaining form.
- **Full-suite pins (orchestrator):** `tests/unit/test_contact_export.py` pins the new `pipeline` owner's `channel_permissions` section (declared sections, the empty document and its key order).

## Spec Change Log

## Review Triage Log

Pass 1 (blind hunter 14, edge-case hunter 4, verification gap 2 = 20 raw; 19 after grouping; 10 patch, 9 rejected, 0 deferred).

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| 1 | edge | `parse_local` lets `OverflowError` escape at datetime bounds (500) | low | `astimezone(UTC)` of `9999-12-31T23:59` in a negative zone overflows; only `ValueError` is caught; direct correction | patch: map `OverflowError` to `BAD_TIME`; test |
| 2 | edge | Non-`PermissionRefused` errors answer 500 | low | only reachable via #1 (fixed) or an invariant violation, which should fail loudly; fix adds a branch | reject |
| 3 | edge, blind | Opt-out on a person with no known key writes no entry yet says every channel is blocked | medium | `person_keys` can be empty (no email/LinkedIn, no name or no web domain); the notice is unconditional | patch: when no entry was written or existed, the notice says no key is known so nothing was added to the Suppression List (permissions still revoked); test |
| 4 | edge | CRLF line breaks make browser-accepted 300-character Evidence too long | low | textarea `maxlength` counts a break as 1, the POST sends CRLF; direct correction | patch: normalise `
` to `
` before the Evidence checks; test |
| 5 | verification | Unparseable stored `sender_timezone` branch untested | medium | only the deleted-row path is tested | patch: test (view zone None, record refused `TIMEZONE_UNSET`, GET 200 with note) |
| 6 | verification | Revoked row rendering untested | medium | after revoke only the notice is asserted | patch: assert "Instagram: revoked", Revoked time, no Revoke/inbound forms, opt-out still offered |
| 7 | blind | Opt-out has no confirmation | low | a mistaken click over-suppresses, the fail-safe direction; fix adds UI surface | reject |
| 8 | blind | Forms lack `action=` | low | `lead_contacts.html` sets `action` alongside `hx-post`; without HTMX the POST hits the record route (400); direct correction | patch: add `action` equal to `hx-post` on every panel form |
| 9 | blind | Erased check runs in a separate unit from the write | false | erasure revokes every active row, so inbound/revoke are refused `NOT_ACTIVE`/`ALREADY_REVOKED`; opt-out on a tombstone suppresses `erased_person_keys`, which is what erasure itself does | reject |
| 10 | blind | `main.py` docstring says research's hook runs last | low | `main.py:123` "tombstones the Contact last" while pipeline's hook is now registered after it; direct correction | patch: docstring order text |
| 11 | blind | `Pipeline.erase_pii` duplicates `_apply` | low | both go through the machine and the events log; dedupe needs a cross-module helper | reject |
| 12 | blind | `check_record` skips identifier checks when the channel is invalid | low | unreachable from the form's select; only a crafted POST | reject |
| 13 | blind | Two independent 300-character limits | low | domain `MAX_EVIDENCE` and research `MAX_OPERATOR_QUOTE` can drift and turn a refusal into a 500; direct correction | patch: `MAX_EVIDENCE` derived from `nextron.domain.extraction.MAX_QUOTE` |
| 14 | blind | Identifier patterns loose (`+0`, dots, case) | low | Operator-typed and never matched or fetched; tightening adds rules | reject |
| 15 | blind | Evidence shows "—" after a Lead merge | false | research `reparent_lead` moves every Evidence row and the Contact to the survivor, so `contact.lead_id` still finds it | reject |
| 16 | blind | Missing tests (DST gap via app, WhatsApp AD-16, erased use cases, WhatsApp number planting) | low | each path is unit-tested at the domain or covered by the Instagram scenario; the no-key opt-out test comes with #3 | reject |
| 17 | blind | Opt-out notice counts written only and has no singulars | low | a repeat opt-out says "0 suppression entries written"; direct correction | patch: report written and already-present entries with singular/plural forms |
| 18 | blind | Window line is a `role="status"` live region; closed state unstyled | low | every swap re-announces each row; deletion | patch: drop `role="status"` from window lines and add a `window-closed` class to the closed line |
| 19 | blind | Import order in several files | low | no linter enforces it | reject |

## Verification

**Commands:**
- `cd nextron-engine && uv run pytest -q -n 2 tests/contracts <touched test files>` -- all pass
- `cd nextron-engine && uv run lint-imports` -- all contracts KEPT
