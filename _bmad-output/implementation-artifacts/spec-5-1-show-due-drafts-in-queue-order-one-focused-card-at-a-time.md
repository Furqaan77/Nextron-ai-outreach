---
title: "Story 5.1: Show due Drafts in queue order, one focused card at a time"
type: 'feature'
created: '2026-11-19'
status: 'done'
baseline_commit: '31596dbf529771dfcf33823399bee7d70224e529'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-nextron-outreach-engine-2026-11-14/ARCHITECTURE-SPINE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-5-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Drafts now have text (4.12) and grounding (4.13/4.14), but nothing shows Mac which Drafts are due or in what order; the nav's "Queue" section has no page.

**Approach:** A read-only `app.queries.approval.due_queue(now, eligibility)` builds the FR-26 groups of reviewable `ready` Drafts (live Eligibility per Draft, failures under "Held back"); `GET /queue` (route name `queue_page`) shows group headers with counts, one focused card ("n of N"), the session's elapsed time, and the empty state.

## Boundaries & Constraints

**Always:**
- **Candidates:** Drafts with status `ready` and a current revision (`reviewable`). Cold kinds (`step`, `recontact`) also need `due_at` set, `due_at <= now + 24 h` (inclusive), and an `active` Enrollment. Responsive kinds (`reply`, `rebook`, `channel`) need neither; they are resolved to a Contact/Lead through their `enrollment_id`; one that can't be resolved goes to Held back with reason `no_contact_link`.
- **Groups, in order** (header with count each): 1 Replies — responsive kinds, oldest `created_at` first; 2 Tier A Step 1 — kind `step`, `step_no = 1`, Lead tier `A`, by the newest live Event's age ascending (`domain.signals.age_days` over the Lead's current Event-class Signals that `is_live`; no live Event sorts after all with one), ties by Lead Score descending; 3 Other Step 1 and recontact — any other `step_no = 1` Draft (non-A `step`, or `recontact`), by Lead Score descending; 4 Follow-ups — `step_no > 1` of either cold kind, by Lead Score descending. A missing score sorts last; final tie-break `due_at` then Draft id (deterministic).
- **Live Eligibility (AD-16):** `due_queue` takes `eligibility: Callable[[str], Decision]` (contact id → `domain.eligibility.Decision`); the web route passes `Compliance(clock).evaluate_eligibility` (which itself calls `assert_contactable`). A Draft whose Decision is not eligible, or whose call raises `LookupError`, goes to Held back with the Decision's reasons (or `contact_unknown`); it is not counted in N. Scoring's stored eligibility is never read. Called once per Contact per build.
- **`DueQueue` result:** `groups` (each: key, label, ordered cards), `held_back` (card + reasons), `total` N, `next_due_at` (earliest `due_at` among all `ready` Drafts with one, any Enrollment status, else `None`).
- **Card data:** Lead name, Segment name, Market code (the Contact's Market), Lead Tier and Lead Score; Contact name, role, Address Class; the exact `domain.render.compose` output of the current revision with its footer snapshot (subject, body incl. signature, footer and UK privacy line); the "Sending blocked: footer incomplete" notice when `render.incomplete_notice` applies, as on the Lead page.
- **Page:** `/queue?card=k` (1-based, default 1, clamped to 1..N; a non-integer → 1) shows card k of the flattened group order, "k of N", previous/next links (plain `<a href>` GETs), the group headers with counts, and the Held back list (name, reasons). Empty groups: "Queue clear" plus the next due time in `sender_timezone`, or "Nothing scheduled". Prospect, crawled and AI text is autoescaped inside an `hx-disable` container; `Cache-Control: no-store` via `page()`; `@personal_data(True)`.
- **Session elapsed time:** `SessionStore` records each session's start (the app clock at `create`); the queue shows `now − start` as `Hh MMm` (rendered server-side; no script).

**Never:** approve/reject/edit/skip/regenerate actions or forms (later Epic 5 stories), keyboard JS, any write (GET is a read-only unit), grounding display (5.3), the away banner (5.11, merge touchpoint), a migration, a new dependency, sending anything, reading scoring's stored eligibility.

### Decisions made overnight (pending user review)
1. `due_queue` takes the eligibility callable because the `queries` layer may not import `app.compliance` (c6); the AC's signature gains this one parameter.
2. Drafts without a revision (4.15 not yet built) are not reviewable and are left out entirely.
3. Grouping is by `step_no`: any `step_no > 1` is a follow-up; a `recontact` Step 1 is in group 3 even for a Tier A Lead.
4. A Tier A Step 1 with no live Event sorts after those with one (still group 2).
5. "Session" = the dashboard session since `nextron open`; elapsed time is shown at render (no live ticking).
6. Responsive Drafts with no resolvable Contact are held back (`no_contact_link`) rather than shown without context.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Error handling |
|---|---|---|---|
| Group order | reply, Tier A S1, B S1, recontact, S2 | groups 1–4 in that order | N/A |
| Tier A tie-break | two A Step 1s, same Event age | higher Lead Score first | N/A |
| 24 h boundary | `due_at = now+24h` / `+24h+1s` | shown / not shown | N/A |
| Excluded | follow-up no `due_at`; Enrollment paused/stopped/completed; no revision; status not `ready` | absent from groups and Held back | N/A |
| Held back | Contact suppressed or ineligible | in Held back with reasons, not in N | N/A |
| Empty | nothing due, a `ready` Draft due in 3 days | "Queue clear" + that time | N/A |
| Nothing at all | no `ready` Draft with `due_at` | "Queue clear", "Nothing scheduled" | N/A |
| Card param | `?card=99`, `?card=x` | last card / card 1 | clamped |
| UK card | GB Contact | body ends with footer incl. privacy line | N/A |

</frozen-after-approval>

## Code Map

All under `nextron-engine/`.
- `nextron/app/queries/approval.py` -- docstring only now; add `due_queue(now, eligibility)`, `DueQueue`, `QueueGroup`, `QueueCard`, `HeldBack`, group keys. May import `nextron.domain`, `nextron.db.repos.approval` and other `nextron.app.queries.*` (`drafting`, `sequencing`, `research`, `discovery`, `scoring`, `setup`); never `app.compliance` (c6-app-dag, `pyproject.toml:344`) nor other services' repos (c7-owner-approval, `:524`).
- `nextron/db/repos/approval/drafts.py` -- precedent `ready_of_ended_enrollments` joins `DraftRecord`+`EnrollmentRecord`; add a read returning `ready` reviewable Drafts with their Enrollment status/lead/contact (cold filter on `due_at <= end` and `active`), and `earliest_ready_due()`.
- `nextron/app/queries/drafting.py` -- `DraftView.reviewable`, `current_revision(view)`, `footer_snapshot(id)`, `grounding_result` (not used here).
- `nextron/domain/draft.py` -- `DraftKind`, `STEP_KINDS`; add `RESPONSIVE_KINDS = (REPLY, REBOOK, CHANNEL)`.
- `nextron/domain/signals.py` -- `age_days` (:97), `is_live` (:123); live Events from `queries.research.signals(lead_id)` (see `app/drafting/api.py:300-317` for class/tier use).
- Lead: `queries.discovery.lead(id)` (`name`, `segment_id`); `queries.scoring.stored(lead_id)` (`lead`, `tier`; `None` if unscored); `queries.setup.segment_by_id`; Market via Contact `market_id` → `queries.setup.market_by_id` (may raise `InvalidMarkets`; catch like the Lead page). Contact: `queries.research.contact(id)` (`name`, `role`, `address_class`, `email`, `market_id`).
- `nextron/app/compliance/api.py:874` `Compliance.evaluate_eligibility(contact_id) -> Decision` -- read-only, safe in a GET.
- `nextron/domain/render.py` `compose`, `incomplete_notice`; reuse the pattern in `web/leads_page.py:1608 draft_row` and `web/app.py:1580 lead_drafts`.
- `nextron/web/app.py` -- `NAV_SECTIONS` (:430) already lists `("queue_page","Queue")`; route `@app.get("/queue", name="queue_page")` + `@personal_data(True)`, sync def, `page(request, "queue.html", ...)` (:620). `SessionStore()` at :603.
- `nextron/web/sessions.py` -- add per-session start time (clock) and a lookup for it.
- New `nextron/web/queue_page.py` (view rows, elapsed/`n of N` formatting) and `nextron/web/templates/queue.html`. Template rules: no `<script>`, `on*`, `hx-on*`, `style=` (`tests/contracts/test_web_contracts.py` ~:583); add it to the list at ~:648.
- Pins: `tests/unit/test_dashboard.py:726-765` nav test gains Queue; `tests/contracts/test_source_tree.py` FILES (~:321/:349).
- Test helpers: `tests/app/test_enroll.py` `build_world`/`World`; `tests/app/test_draft_slots.py` `enrolled`, `gate_answers`; `tests/app/test_draft_revisions.py` `world` fixture, `drafted`, `move_to_uk`, `lead_page()` web pattern (:687, `Dashboard`, `Tree`); `tests/app/test_lead_scoring.py` `signal`, `score`.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/draft.py` -- `RESPONSIVE_KINDS`.
- [x] `nextron/db/repos/approval/drafts.py` -- queue candidate read + earliest ready due.
- [x] `nextron/app/queries/approval.py` -- `due_queue` and result types, grouping/ordering, Held back.
- [x] `nextron/web/sessions.py`, `nextron/web/app.py`, `nextron/web/queue_page.py`, `templates/queue.html` -- route, card, groups, Held back, empty state, elapsed time.
- [x] `tests/app/test_due_queue.py` -- the I/O matrix with a frozen clock and a temp DB (incl. reply group via a hand-inserted reply Draft); web tests for the page (headers, no-store, `hx-disable`, autoescape of a `<b>` Lead name, n of N, clamping, UK privacy line).
- [x] pins (nav, template list, FILES) and `nextron-engine/README.md` Story 5.1 section.

**Acceptance Criteria:**
- Given any due queue build, then nothing is written to the database.
- Given a Contact with several due Drafts, then `eligibility` is called once for it.

## Implementation Notes

- `domain/draft.py`: `RESPONSIVE_KINDS = (reply, rebook, channel)`.
- `db/repos/approval/drafts.py`: `QueueCandidate` + `queue_candidates(end)` (one Core select, outer join to `enrollments`; `ready` and `current_rev IS NOT NULL`; responsive kinds any Enrollment or none, Step kinds `due_at IS NOT NULL AND due_at <= end AND enrollments.status = 'active'`; by id) and `earliest_ready_due()` (`min(due_at)` over `ready`, any Enrollment status, revision or not). Reads only.
- `app/queries/approval.py`: `due_queue(now, eligibility)` with `DueQueue(groups, held_back, total, next_due_at)` (+ `cards` flattening property), `QueueGroup`, `QueueCard`, `HeldBack`, `HeldReason(code, text)`, `GROUP_*`/`GROUP_ORDER`/`GROUP_LABELS`. A per-build `_Reader` caches Contact, Lead, score, Segment name, Market and newest-live-Event age, and calls `eligibility` once per Contact (`LookupError` → `contact_unknown`; a non-eligible Decision with no reasons → `not_eligible`). Unlinked responsive Drafts → `no_contact_link` (eligibility not called). Unreadable Segment/Market (`InvalidSegments`/`InvalidMarkets`) read as missing (no Market → no footer check). The card's footer check (`render.footer_complete` + `incomplete_notice`) is computed here from `setup.footer_settings()`. Event age: `age_days` over `signal_class == event` current Signals that `is_live` (a bad tier counts as not live); `score_only` Events are not excluded.
- `web/sessions.py`: `SessionStore(now=None)` records `now()` at `create`; `started_at(token)`.
- `web/app.py`: `create_app` gains a **required** `now: Callable[[], datetime]` keyword (web may not import `nextron.ports`, so it is the clock's bound `now`, not a `Clock`); `main.build_app` passes `clock.now`, `tests/dashboard.py` passes `self.clock.now`. `SessionStore(now)`. `GET /queue` (`queue_page`, `@personal_data(True)`) builds `due_queue(now(), compliance.evaluate_eligibility)`, reads `sender_timezone` (any `DomainError` → UTC), finds the session start from the first live `nextron_session` cookie (as the guard does), and renders via `page()` (no-store).
- `web/queue_page.py`: `queue_context`, `card_index` (`int()` else 1, clamped to 1..max(N,1)), `elapsed_text` (`Hh MMm`, floor, never negative), `local_text`/`zone_of` (`YYYY-MM-DD HH:MM <zone>`, unknown zone → UTC), `CardRow`/`GroupRow`/`HeldRow`. Market code shown upper-case (`UK`, `US`).
- `templates/queue.html`: no script/style/forms; card and Held back are `hx-disable`; all text via `|plain`; card nav is plain `<a href>`.
- Pins: nav tests in `tests/unit/test_dashboard.py` (Queue now real; the POST-only case is its own test after review pass 1), template list and the route-path list in `tests/contracts/test_web_contracts.py` (`/queue` after `/`), source-tree FILES. README "The Approval Queue" section.
- Tests: `tests/app/test_due_queue.py` (46 cases at first). Suppressed-Contact cases write the suppression entry raw, because `SubjectSuppressed` would cancel the Drafts; reply Drafts are hand-inserted with a copied revision.
- Review patches (pass 1):
  - `app/queries/approval.py`: `_Reader.decision` also catches `ValueError` (e.g. `assert_contactable` on a Contact whose Lead was merged) and holds the Draft back, fail closed, with the new reason `not_checkable` "Eligibility couldn't be checked"; it caches the reason code instead of `None`. Before, the error escaped and `GET /queue` returned 500.
  - `web/queue_page.py`: `zone_of` also catches `OSError` (`ZoneInfo("America")` names a tzdata folder) and falls back to UTC.
  - `web/sessions.py`: new `session_cookie_values(header)` yields each `nextron_session` value of one `Cookie` header; `guard._session_csrf_token` and `app.session_started` both use it (behaviour unchanged).
  - Import order fixed in `web/app.py` (`domain.errors` after `domain.credentials`) and `tests/app/test_due_queue.py` (`db.models.base` after `app.setup.api`).
  - `templates/queue.html` header comment now says only the card and the Held back list (prospect, crawled and AI text) sit inside `hx-disable`; headers, position nav and empty state carry engine text only.
  - Tests: `tests/unit/test_csrf.py` gains `SessionStore` start-time cases (no clock → `None`; unknown or empty token → `None`; start is the clock's time at `create` and doesn't move) and `session_cookie_values` cases. `tests/unit/test_dashboard.py` restores the GET `/funnel` middle-section ordering case and moves the POST-only case to its own test. `tests/app/test_due_queue.py` (now 55 cases) adds: `ValueError` → `not_checkable` (Recorder) and a merged Lead through the real gate and the page; the `not_eligible` fallback (a stand-in Decision, since a real ineligible `Decision` must have reasons); replies on paused and stopped Enrollments stay in Replies; page text for `p.draft-blocked`, `p.draft-to`, `p.draft-subject`, each `span.held-back-reason` and "Nothing held back."; next due time in UTC without `sender_timezone`; "No text yet" through the page with a replaced `due_queue` (unreachable through the query: a reviewable Draft has a revision, whose footer snapshot is a required foreign key); `local_text(…, "America")` → UTC. Loose assertions tightened (second card checked on `card=2`; excluded Drafts checked in the database; the last card is the exact `(due_at, id)` maximum).

- Full-suite gate: the nav pins in `tests/unit/test_credentials_page.py` and `tests/unit/test_leads_page.py` gained the Queue link.

## Spec Change Log

## Review Triage Log

Pass 1 (blind hunter BH, edge-case hunter EC, verification gap VG): 22 raw, 18 after dedup; 9 patched, 2 deferred, 7 rejected.

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | BH1+EC2+VG-o2: `next_due_at` can name a past time or a Draft on a paused/ended Enrollment or held back | medium | `earliest_ready_due` takes min `due_at` over every `ready` Draft, as the frozen Always rule ("any Enrollment status") and the AC ("earliest `due_at` among `ready` Drafts") say | reject (would change the approved spec) |
| 2 | BH2: every candidate's message is composed though one card shows | low | real but cheap at today's volumes; a direct fix restructures the build | reject |
| 3 | BH3: `?card=k` is positional, so a changed queue shows another Draft at the same k | low | true; harmless while the page has no actions; the decision stories need a stable Draft target | defer |
| 4 | BH4: session-cookie parsing duplicated from `guard._session_csrf_token` | low | the loops are copies; drift would make the elapsed time disagree with the guard | patch |
| 5 | BH5: unsorted imports (`DomainError` in app.py; `new_id` in the test) | low | direct correction | patch |
| 6 | BH6: lines over the length limit | maybe-false | no ruff config or linter in the tree; nothing enforces it | reject |
| 7 | BH7: no unit test for `SessionStore(now)` / `started_at` | low | `tests/unit/test_csrf.py` builds it without a clock only | patch |
| 8 | BH8+VG1+VG2: untested branches -- page-level blocked notice, To/Subject, held-back reason text, "Nothing held back.", a responsive Draft on a paused/stopped Enrollment still in Replies, `not_eligible` fallback, timezone fallback through the page, "No text yet" | medium | grep finds no `/queue` assertion for these; a template or filter regression would pass | patch |
| 9 | BH9: loose assertions (`assert second`, `data-draft in (first, second)`, held-back reason unchecked) | low | they only prove non-empty ids | patch |
| 10 | BH10: `queue.html` comment says everything is inside `hx-disable`; Held back has no count | low | headers/nav/empty state are outside `hx-disable` (engine text only); the count isn't in the spec | patch (comment only; count rejected) |
| 11 | BH11: `score_only` Event Signals count for the Tier A order | low | recorded in Implementation Notes; ordering only, nothing cited | reject |
| 12 | EC1: `evaluate_eligibility` raises `ValueError` for a merged Lead (`assert_contactable`, compliance/api.py:929), so one Contact 500s the whole queue | medium | only `LookupError` is caught in `_Reader.decision` | patch |
| 13 | EC3: missing footer snapshot shows "No text yet" | false | `footer_snapshot_id` is a NOT NULL FK (`db/models/drafting.py:228`) | reject |
| 14 | EC4: a Contact without email renders "To: " | low | a revision can't be written without an email (4.12 `_prepare`); an erased Contact fails Eligibility | reject |
| 15 | EC5: a recontact Step 2+ goes to Follow-ups | false | frozen overnight decision 3 | reject |
| 16 | EC6: `ZoneInfo("America")` raises `OSError` subclasses, so the page 500s | low | `zone_of` catches only `ZoneInfoNotFoundError`/`ValueError`; direct correction | patch |
| 17 | EC7: the nav test lost the middle-section (Funnel) ordering case | low | the GET `/funnel` case was replaced by the POST-only one | patch |
| 18 | VG-o1: responsive Drafts are checked with cold-email Eligibility (Not Now, re-contact) | maybe-false (medium if true) | no reply Drafts exist before Epic 7; AC applies `domain.eligibility.evaluate` to every due Draft | defer |

## Verification

**Commands:** (from `nextron-engine/`, `-n 2` max)
- `uv run pytest -q tests/app/test_due_queue.py tests/unit/test_dashboard.py tests/app/test_draft_revisions.py` -- pass
- `uv run pytest -q tests/contracts` -- pass
- `uv run lint-imports` -- all contracts kept
