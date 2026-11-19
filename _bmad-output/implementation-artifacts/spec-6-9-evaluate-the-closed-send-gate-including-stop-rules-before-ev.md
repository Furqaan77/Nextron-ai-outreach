---
title: 'Story 6.9: Evaluate the closed send gate, including stop rules, before every send'
type: 'feature'
created: '2026-11-19'
status: 'done'
baseline_commit: '954f10f4c073813ad90d27f7d6193cff4142abd0'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-6-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing yet checks an approved OutboundMessage (OM) immediately before it leaves. A stopped Sequence, a reply, a suppression, a stale footer or an exhausted cap could let a send through once Story 6.10 sends.

**Approach:** Build `domain.send_gate` as a pure evaluator over one closed list of 13 predicates. Build `compliance.assert_sendable(om, now)` to read each fact through owners' `app.queries` and return every failing predicate. Inputs that later stories own (OM rows from 5.2, approvals from 5.4, `mail_messages` from 6.8, sent counts from 6.10) come through named fail-closed seams. Add Stop Sequence to the Lead page.

## Boundaries & Constraints

**Always:**
- `PREDICATES` is exactly:
  - for all kinds: `contactable`, `approval_live`, `hash_matches`, `inbox_ready`, `footer_complete`, `market_eligible`;
  - for cold kinds only: `enrollment_active`, `thread_quiet`, `campaign_active`, `in_send_window`, `under_daily_cap`, `warmup_ok`, `under_total_ceiling`.
- Cold kinds are `step` and `recontact`; responsive kinds are `reply` and `rebook`. Responsive kinds skip every cold predicate. Any other kind raises `ValueError` when the candidate is built.
- **Fail closed.** Any missing, unknown or unreadable fact (`None`, a missing row, an unset setting, an unbuilt seam) fails the predicate it feeds.
- The result lists every failing predicate in `PREDICATES` order.
- `assert_sendable` reads only through `nextron.app.queries.*` and `assert_contactable` (AD-16). It writes nothing.
- Only the `SendGate.check` command writes, and it writes one thing: the deduplicated ops alert `send_hash_mismatch` (subject: the OM id, text: IDs only) when `hash_matches` fails.
- The gate never cancels an OM and never writes another owner's rows.
- Time comes from `now`. Day counts use calendar days in `sender_timezone`.
- No setting or control disables or weakens a predicate (NFR-7).

**Never:**
- Send email or call `MailPort`.
- Add a migration.
- Add a 14th predicate. Brand-domain and recovery-mode defence goes inside `inbox_ready`.
- Build the "Approved, not yet sent" list or OM tables (Epic 5). Story 6.9 provides `send_gate.LABELS` and the read-only `assert_sendable` that the list will call.
- Import `app.sequencing`, `app.approval`, `app.drafting` or `app.sending` from compliance (c6).

## I/O & Edge-Case Matrix

The table uses a fixed `now`, with each predicate failing on its own:

| Scenario | Input / State | Expected |
|---|---|---|
| all pass, cold | every fact good | `failing == ()` |
| approval expiry | `now = approved_at + 72h` | `approval_live` only (at `+71:59` it passes) |
| approval not current | the Draft isn't `approved`, or the OM's `approval_id` isn't the current approval | `approval_live` |
| hash | the approved hash ≠ the current revision's `text_hash`, or ≠ a fresh recompute (revision + stored footer snapshot + the Contact's current email + the OM's inbox) | `hash_matches`; `SendGate.check` raises one `send_hash_mismatch` alert |
| inbox | `paused`/`auth_failed`/missing; no passing check within 26 h; the address on `nextronai.com` or a subdomain; recovery mode on | `inbox_ready` |
| footer | the legal name, postal address or (UK) privacy URL is blank; or the revision's footer snapshot is for another Market, or its text ≠ `render.footer(current settings, Contact's current Market, source)` | `footer_complete` |
| eligibility | the Market is disabled or missing; for cold kinds, live `eligibility.evaluate` fails | `market_eligible` |
| suppressed after approval | `assert_contactable` returns `Blocked` | `contactable` |
| enrollment | `paused`/`stopped`/`completed`/missing | `enrollment_active` (cold) |
| thread | the seam reports activity after `approved_at`, or returns `None` (unknown) | `thread_quiet` (cold) |
| campaign | `queries.setup.campaign_active` is false | `campaign_active` (cold) |
| window | UK on Monday 07:59 London, or a Saturday; US with an unknown tz at 10:59 New York | `in_send_window` (cold) |
| caps | cold sent today ≥ the inbox's `cold_daily_caps` entry, or the count is unknown | `under_daily_cap` |
| warm-up | under 14 recorded days | `warmup_ok` |
| ceiling | all SENT today (including the warm-up tool figure) ≥ `ceiling()`, or the count is unknown | `under_total_ceiling` |
| responsive OM | a `reply` while every cold fact fails | only the all-kind failures |
| bad kind | `channel` or an unknown kind | `ValueError` when the candidate is built |

</frozen-after-approval>

## Code Map

- `nextron/domain/sending_policy.py`:
  - add `SEND_WINDOW` (08:00–17:00 weekdays);
  - add `in_send_window(contact_tz: str|None, market: MarketDef, now) -> bool`. `None` means unknown: use `market.unknown_timezone` and `market.unknown_send_window`, or the default window. A non-aware `now` raises;
  - add `APPROVAL_TTL = 72h` and `approval_expired(approved_at, now)`, which is expired at ≥ 72 h;
  - reuse `local_date`, `warmup_days` and `MIN_WARMUP_DAYS`.
- New `nextron/domain/send_gate.py` (stdlib and domain only):
  - `Predicate` (StrEnum);
  - `PREDICATES`: a tuple of `GatePredicate(name, cold_only, label)`;
  - `LABELS`, for example "Outside Send Window";
  - `OmKind` and `COLD_KINDS`/`RESPONSIVE_KINDS`;
  - `SendCandidate(om_id, kind, draft_id, approval_id, sending_inbox_id, contact_id, enrollment_id|None, thread_id|None)`, the seam that 6.10 builds from the OM row;
  - `GateFacts`: optional raw facts, with `None` failing;
  - `evaluate(kind, facts, now) -> GateResult(failing)`, with `.passed`.
- New `nextron/app/compliance/send_gate.py`, re-exported from `compliance/api.py`:
  - `assert_sendable(om: SendCandidate, now) -> GateResult` gathers the facts. Mirror `Compliance.evaluate_eligibility` (api.py:874); factor its body into a module function so the gate reuses it with `now`.
  - The contactable purpose comes from the kind: `step` 1 is `cold`, a later step is `follow_up`, then `recontact`, `reply` and `rebook`.
  - `class SendGate(clock, notify)` has an `@command check(om)` for 6.10's claim unit and raises the alert. Notify pattern: `compliance/unsub_poll.py` `TOKENS_INVALID`.
- Fail-closed seams (each docstring names the story that fills it):
  - `queries.approval.current_approval(draft_id) -> ApprovalView(id, draft_id, rev, approved_at, approved_hash)|None` returns `None` until 5.4.
  - `queries.replies.thread_quiet(thread_id, since) -> bool|None` returns `None` until 6.8.
  - `queries.sending.cold_sent_on(inbox_id, local_date) -> int|None` and `sent_total_on(local_date) -> int|None` return `None` until 6.10/6.8.
- Existing reads:
  - `queries.sending`: `inboxes()`, `domain_auth(id, now)` (`None` means blocked), `cold_daily_caps`, `ceiling()`;
  - `queries.ops.recovery_mode()`;
  - `queries.setup`: `footer_settings()`, `market_by_id`, `campaign_active`;
  - `queries.drafting`: `draft`, `current_revision`, `footer_snapshot`;
  - `queries.research`: `contact` (`.tz`, `tz_basis`; `US_UNKNOWN_FALLBACK` or `NO_MARKET` means unknown tz) and `provenance`;
  - `queries.sequencing.enrollment`;
  - `domain.sending_inbox.BRAND_DOMAIN` / `check_address`;
  - the `drafting/api.py:204` `_prepare` recompute pattern.
- `tests/contracts/test_ad16_callers.py`:
  - add the `compliance.assert_sendable` scenario;
  - make the manifest entry read-only (it inserts nothing; let a read-only entry skip the "not vacuous" `Write` check at :1418);
  - update `test_the_built_callers_are_the_epic_2_paths` and the docstring.
- Web:
  - add `POST /leads/{lead_id}/stop-sequence` in `nextron/web/app.py` with `@personal_data`, registered with the other lead POSTs. It calls `Sequencing.stop(cause=StopCause.OPERATOR_STOP, lead_id=...)`, which today cancels `ready` Drafts; 5.2/6.10 add `sending.cancel` for `queued` OMs.
  - Add the button (with a confirmation) to `lead.html`, shown only while the Lead has a live Enrollment.
  - Update `tests/dashboard.py`/`create_app` wiring only if a new dependency is needed.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/sending_policy.py`: add the window, the TTL and `approval_expired`. The policy is the single home of these (AD-11).
- [x] `nextron/domain/send_gate.py`: add the closed list and the pure evaluator.
- [x] `nextron/app/queries/{approval,replies,sending}.py`: add the seams, each returning `None` now.
- [x] `nextron/app/compliance/send_gate.py` and `api.py`: add `assert_sendable` and `SendGate.check`.
- [x] `nextron/web/app.py` and `templates/lead.html`: add Stop Sequence, then update the route docstring and `test_web_contracts`.
- [x] Tests:
  - `tests/unit/test_send_gate.py`: `PREDICATES` exactness and the matrix rows on `evaluate`.
  - `tests/unit/test_sending_policy.py`: window additions, including the UK/US weekday hours, weekends, the US unknown 11–17 Eastern window and the 2027-03-14..28 DST gap (UK 08:00 London = 04:00 New York after Mar 14 but before Mar 28); also `approval_expired`.
  - `tests/app/test_send_gate.py`: on a migrated temp DB with a `FrozenClock`, each matrix row through `assert_sendable` with the seams monkeypatched. Cover the seams' real `None` failing closed, the alert once per OM, and no rows written by `assert_sendable`.
  - The Stop Sequence route test: CSRF 403, the cancelled `ready` Drafts, and the stopped Enrollment.
  - The NFR-7 test: no `settings_schema.declared()` key or label names a predicate or contains `disable`/`skip`/`bypass`/`override`, and no template form field names a predicate.
- [x] `nextron-engine/README.md`: add a "Send gate (Story 6.9)" section.

**Acceptance Criteria:**
- Given the full suite through the suite lock and `lint-imports`, then everything passes.
- Given `tests/contracts/test_ad16_callers.py`, then `compliance.assert_sendable` is built, has a scenario, and calls `assert_contactable`.
- Given the AST of `nextron/app/compliance/send_gate.py`, then it imports no `nextron.db.repos` module and no higher owner.

## Design Notes

Gathering a fact catches only `LookupError` and `ValueError` (a missing or invalid row) and turns it into `None`. Database errors propagate, and 6.10's claim then fails with the OM still `queued`.

`hash_matches` and `footer_complete` together settle the deferred 4.12 item. An approved Draft whose Contact moved Market, or whose footer settings changed after approval, is blocked until it is re-approved.

## Verification

**Commands** (in `nextron-engine/`):
- `uv run pytest -q -n 2 tests/unit/test_send_gate.py tests/unit/test_sending_policy.py tests/app/test_send_gate.py tests/contracts`: expect them to pass.
- `uv run lint-imports`: expect every contract kept.

## Implementation Notes

### Decisions made overnight (pending user review)
1. **No real cold send until later stories.** Until 5.4 (approvals), 6.8 (`mail_messages`) and 6.10 (sent counts) fill the seams, `approval_live`, `thread_quiet`, `under_daily_cap` and `under_total_ceiling` always fail. This is fail-closed by design.
2. **Brand-domain defence.** The planning docs don't make "never from nextronai.com" a gate predicate (it is FR-32, enforced at connect and by DB CHECKs). As defence in depth, `inbox_ready` also fails for a `nextronai.com` address or subdomain.
3. **Recovery mode.** Recovery mode fails `inbox_ready`, in addition to 6.10's dispatch skip.
4. **Grounding.** Grounding (4.13/4.14) isn't in this tree. 5.4 must refuse approval of a revision with no grounding row, and `approval_live` must require that the approved revision is grounded once 4.14 merges. Until then, `approval_live` already fails closed.
5. **Away dates.** Away dates (5.11) aren't in this tree. If 5.11 pauses Enrollments, `enrollment_active` covers them. Anything else becomes a 5.11/6.10 follow-up.
6. **Footer.** The footer must match the Contact's current Market and the current settings, or `footer_complete` fails. This resolves deferred-work item 394.
7. **Wiring.** `SendGate` isn't wired into `main` until 6.10 calls it.
8. **Responsive kinds** still need an enabled Market for the Contact.
9. **The ceiling is per inbox.** Story 6.4 labels `total_daily_ceiling` "Total daily ceiling per inbox", so the seam is `sent_total_on(inbox_id, local_date)` (the Code Map's signature had no inbox). The gate adds the inbox's `tool_send_figure(plan)` to it.
10. **Unreadable stored data fails closed.** Gathering a fact also catches `DomainError` (`SettingNotSet` for an unset `sender_timezone`, `InvalidSettingValue`, `InvalidMarkets`), alongside `LookupError` and `ValueError`. Database errors still propagate.
11. **Stop Sequence confirms on the server.** The first click returns the panel asking "Stop this Lead's Sequence?"; only the second POST (`confirm=yes`) stops. This works without JavaScript.
12. **`inbox_ready` needs `active`.** A `connecting` inbox fails too. A non-`active` inbox also fails `under_daily_cap` for cold kinds, because `cold_daily_caps` gives it 0.
13. **Approved Drafts on Stop Sequence.** `Sequencing.stop` cancels only `ready` Drafts, so an `approved` Draft stays `approved`. The gate's `enrollment_active` blocks it until 5.2/6.10 cancel queued OMs.
14. **Import cycle.** `compliance/send_gate.py` imports `compliance.api` at the bottom of the module, and `api.py` re-exports `SendGate`/`assert_sendable` at its bottom. Either module can then load first, and the AD-16 fakes of `assert_contactable` and `live_eligibility` are looked up at call time.
15. **Review patches (T3, T6, T9, T15).** The inbox list, `warmup_days` and `tool_send_figure` are read through `_fact`, so a lookup, value or domain error fails `inbox_ready`/`warmup_ok`/`under_total_ceiling` instead of raising. `SendGate.check` alerts `send_hash_mismatch` only when `hash_matches` fails and `current_approval(om.draft_id)` is the OM's approval (same `id` and `draft_id`); with no current approval there is no alert (still one per OM otherwise). A first Stop Sequence click after the Enrollment ended returns the panel with the `no_live_sequence()` notice instead of an empty body. An `approved_at` later than `now` fails `approval_live` (fail closed). The Stop texts now say ready Drafts are cancelled and approved Drafts are blocked from sending while the Sequence is stopped.

## Spec Change Log

## Review Triage Log

### Pass 1 (blind hunter BH, edge-case hunter EC, verification gap VG): 21 raw, 17 after dedup; 8 patch, 4 defer, 5 reject

| # | Finding | Verdict | Route | Evidence |
|---|---|---|---|---|
| T1 | VG1 + BH10: no app test for an Enrollment of another Contact or inbox, an erased Contact, a brand inbox address through `assert_sendable`, a `no_market` tz basis, or a snapshot that matches the text under another Market id | medium | patch | Dropping the binding guard or these branches leaves every test green; `test_a_hash_that_no_longer_recomputes` only checks membership. |
| T2 | BH1: a `recontact` OM can never pass `market_eligible` (`live_eligibility` passes no `events_first_seen`) | medium | defer | Pre-existing Story 3.6 behaviour (api.py docstring item 7, "Stories 2.9 and Epic 7 pass them"); fails closed. |
| T3 | BH2: `inboxes()`, `warmup_days` and `tool_send_figure` are read outside `_fact` | low | patch | A decode error raises out of the gate instead of failing its predicate, which contradicts the module docstring. Wrapping them is a direct fix. |
| T4 | BH3: `thread_quiet(None, since)` semantics for an OM with no Thread are undefined | medium (unverified) | defer | The seam answers `None` today; 6.7/6.8 decide whether a first Step carries a Thread. |
| T5 | BH4: the `send_hash_mismatch` alert rolls back with a failed claim, and notify's (kind, subject) dedup never re-fires for a re-approved OM | medium (unverified) | defer | Depends on 6.10's claim commit policy and on 5.4's re-approval model. |
| T6 | BH5 + EC1: `SendGate.check` alerts on every `hash_matches` failure, including no approval at all (always, today), an erased Contact or a missing snapshot, with "Approve its Draft again" text | medium | patch | `approved_hash` is `None` whenever `current_approval` is `None`, so every OM would alert. Alert only when the OM's approval is the Draft's current approval (a real approved text no longer matching). |
| T7 | BH6: the AD-16 `assert_sendable` entry lost `inserts={"outbound_messages"}`, and nothing checks that 6.10's claim gates before its write | low | defer | `assert_sendable` truly inserts nothing; 6.10 must add a `Caller` for its claim path. |
| T8 | BH7 + EC6: the Stop Sequence texts say every unsent Draft is cancelled and "nothing more is sent" | low | patch | `test_stop_sequence_leaves_an_approved_draft_to_the_gate` shows approved Drafts stay `approved`. Correct the text. |
| T9 | BH8 + EC5: a first click that arrives after the Enrollment ended renders an empty body, and HTMX's outerHTML swap removes the panel silently | low | patch | `lead_sequence.html` outputs nothing for `live=False, notice=None`. Return the `no_live_sequence()` notice instead. |
| T10 | BH9: the route checks the Lead and stops it in separate units | low | reject | A Lead merged in between makes `stop` stop nothing and return `()`; that is harmless, rare, and the fix adds a guard. |
| T11 | BH11: the bottom-of-module mutual import is fragile | low | reject | It is tested in both import orders and protected by `noqa: E402`; no named caller breaks. A refactor isn't a direct fix. |
| T12 | BH12: a fact error is swallowed without a log | low | reject | The predicate still fails closed, so no harm reaches users; adding logging per fact is new surface. |
| T13 | BH13: deferred work wasn't recorded for the seams, the `queued` OM cancel on Stop, grounding, or the resolved item 394 | low | patch | Added `deferred-work.md` entries. |
| T14 | BH14: the domain and app docstrings cite different FR lists | low | patch | The story's list is FR-37, FR-18, FR-23, FR-33, FR-34, FR-50, FR-64; align both docstrings. |
| T15 | EC2: an `approved_at` later than `now` counts as live, and for more than 72 h | medium | patch | `approval_expired` only checks `now - approved_at >= 72h`. A future approval fails closed. |
| T16 | EC3: a Contact with tz `None` and a resolved basis gets the unknown-location window | false | reject | `ck_contacts_market_tz` sets `tz` and `market_id` together, and without a Market `in_send_window` fails anyway. |
| T17 | EC4: a display-name address `X <a@nextronai.com>` evades `brand_address` | false | reject | Inbox addresses are stored through `check_address` (a plain address) and the DB CHECKs `ck_sending_inboxes_not_brand`/`ck_sending_domains_not_brand` refuse the brand domain. |
