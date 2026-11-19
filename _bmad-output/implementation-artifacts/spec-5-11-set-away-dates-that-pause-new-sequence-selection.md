---
title: 'Story 5.11: Set away dates that pause new Sequence selection'
type: 'feature'
created: '2026-11-19'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
baseline_commit: 'e71c66ef650ae2b2e6da6fa6fbe6297382913810'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-5-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** When Mac is away, the engine would keep starting new Sequences, so he returns to a queue full of fresh Step 1s instead of only follow-ups and replies (FR-31).

**Approach:** An `away_periods` setting (declared in `settings_schema`, owned by `setup`) holds date ranges read in `sender_timezone`. `domain.sending_policy.is_away(local_date, periods)` is the single test; `sequencing.enroll` refuses origins `selection` and `restart` with `operator_away` on an away day, and a banner says "Away until <end date>: no new Sequences are selected".

## Boundaries & Constraints

**Always:**
- Setting `away_periods` (`Key.AWAY_PERIODS`), type `list`, section Sending, default empty. One period per line: `YYYY-MM-DD to YYYY-MM-DD` (start, end, both inclusive; a single `YYYY-MM-DD` is not accepted). Rule `away_period` (shape and real calendar dates) runs before rule `away_order`, whose message is exactly `End date must be on or after start date`. Overlapping periods are allowed.
- Dates are calendar days in `sender_timezone` (UTC while unset or unreadable, as `sequencing._local_date` already does).
- The away check in `enroll` runs right after the AD-16 gate (the gate stays first) and before every other refusal; `RefusalCode.OPERATOR_AWAY = "operator_away"`. Refused: nothing written, no Draft requested. `operator_pick`, `referral`, `recontact` are never refused for being away.
- The banner's end date is the last day of the contiguous away run containing today (overlapping or back-to-back periods merge), shown as ISO `YYYY-MM-DD`. It never reorders anything.
- GET pages read settings only through `nextron.app.queries.*` in a read-only unit.

**Never:** no migration (settings rows already exist); no change to follow-up or Reply Draft ordering; no sending; no pause of existing Enrollments; no new dependency.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| first/last day | period 2026-12-21 to 2027-01-04; local 2026-12-21 or 2027-01-04 | `is_away` true | N/A |
| day before/after | local 2026-12-20 or 2027-01-05 | false | N/A |
| one-day period | 2026-12-25 to 2026-12-25 | true only on 2026-12-25 | N/A |
| end before start | `2027-01-04 to 2026-12-21` saved | refused, field error `End date must be on or after start date` | nothing stored |
| bad line | `next week`, `2026-02-30 to 2026-03-01` | refused, rule `away_period` | nothing stored |
| tz boundary | now 2026-12-21T03:00Z, tz America/Los_Angeles (still 12-20) | not away | N/A |
| enroll away | origin selection/restart, today away | `EnrollRefusal(OPERATOR_AWAY)`, no rows, no Drafts | N/A |
| enroll pick away | origin operator_pick/referral/recontact | enrolls normally | N/A |

</frozen-after-approval>

## Code Map

- `nextron/domain/sending_policy.py` -- add `AwayPeriod(start, end)` (frozen, end >= start), `parse_away_period(line) -> AwayPeriod | None`, `is_away(local_date, periods)`, `away_until(local_date, periods) -> date | None`; extend the module docstring. Standard library only, no clock.
- `nextron/domain/settings_schema.py` -- `Key.AWAY_PERIODS`, two `Rule`s, the `Setting` in Sending; docstring paragraph. Imports from `sending_policy` already exist (no cycle).
- `nextron/domain/enrollment.py` -- `RefusalCode.OPERATOR_AWAY` placed after `BLOCKED` (enum order is check order).
- `nextron/app/sequencing/api.py` -- in `enroll` after the gate block: if origin in {SELECTION, RESTART} and away today (`_local_date(self._clock.now())`) return refusal. Update docstring refusal order. Reuse `_local_date`.
- `nextron/app/queries/setup.py` -- `away_until(now) -> date | None` (reads `AWAY_PERIODS` and `sender_timezone`, parses lines; an unparseable stored line is ignored).
- `nextron/web/app.py` `home` route + `nextron/web/templates/home.html` + new `nextron/web/templates/away_banner.html` -- banner on the Dashboard (the approval queue, Story 5.1, isn't built yet). Follow how other GETs open a read-only unit.
- Tests: `tests/unit/test_sending_policy*.py` (find the existing file), settings contracts/unit tests, `tests/app/` enroll tests (find the Story 4.8 enroll test file and reuse its world), dashboard home test in `tests/unit/test_dashboard.py`; `tests/contracts/test_source_tree.py` `FILES` for the new template; any test listing every setting or the whole Settings form.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/sending_policy.py` -- away-period domain functions -- single definition (AD-21).
- [x] `nextron/domain/settings_schema.py` -- declare `away_periods` with rules -- AD-12 settings.
- [x] `nextron/domain/enrollment.py`, `nextron/app/sequencing/api.py` -- `operator_away` refusal -- FR-31.
- [x] `nextron/app/queries/setup.py`, `nextron/web/app.py`, `home.html`, `away_banner.html` -- banner -- UX-DR6.
- [x] tests for every I/O row, the banner (present when away with merged end date; absent otherwise), contracts updated.
- [x] `nextron-engine/README.md` -- short Story 5.11 section, including what Stories 4.10, 5.1 and 8.9 must do at merge.

**Acceptance Criteria:**
- Given today is inside an away period, when the Dashboard renders, then it shows "Away until <end date>: no new Sequences are selected"; outside one it shows no banner.
- Given an away day, when `enroll` is called with origin `selection` or `restart`, then it returns `operator_away` and no `enrollments`, `events` or Draft rows are written.

## Design Notes

Merge notes for other tracks (put in README): Story 4.10's `Selection.pick` enrolls with origin `operator_pick`, so it is unaffected; it may include `away_banner.html` on `/leads/ready`. Story 5.1's queue must include `away_banner.html` above the groups. Story 8.9's `select_daily` should check `queries.setup.away_until(now)` (or `is_away`) first and select nothing that day; its `enroll(..., origin=selection|restart)` calls are refused anyway.

### Decisions made overnight (pending user review)
- No queue page exists yet, so the banner goes on the Dashboard now; Story 5.1 adds it to the queue.
- Period text format `YYYY-MM-DD to YYYY-MM-DD`, one per line, in the existing `list` setting type (no new setting type); the banner date is ISO.

## Verification

**Commands:**
- `uv run pytest -q -n 2 <touched test files> tests/contracts` -- all pass
- `uv run lint-imports` -- all contracts kept

## Implementation Notes

- `domain/sending_policy.py`: `AwayPeriod` (plain dates, `end >= start`, else `ValueError`), `away_period_dates(line)` (shape `YYYY-MM-DD to YYYY-MM-DD`, ASCII digits, spaces/tabs around `to`, real dates; returns the two dates in written order or `None`), `parse_away_period(line)` (`None` when malformed or reversed), `is_away`, `away_until` (sort by start, extend through overlapping and back-to-back periods). `away_period_dates` was added beyond the Code Map so rule `away_period` (shape) can be told apart from rule `away_order` (a reversed line has dates but no period).
- `domain/settings_schema.py`: `Key.AWAY_PERIODS`, `AWAY_PERIOD_RULE` then `AWAY_ORDER_RULE` (message exactly `End date must be on or after start date`), the `list` Setting last in Sending (label "Away dates", default `()`).
- `domain/enrollment.py`: `RefusalCode.OPERATOR_AWAY` right after `BLOCKED`.
- `app/sequencing/api.py`: `AWAY_PAUSED_ORIGINS = {selection, restart}`; after the gate, `is_away(queries.setup.sender_local_date(now), queries.setup.away_periods())` returns `EnrollRefusal(OPERATOR_AWAY)` before any other check or write; `now` is read once there and reused for the writes (review #2, #3: the banner and the refusal share one reading of the day).
- `app/queries/setup.py`: `away_periods()` (reads the stored text directly and parses line by line, so one stale line is ignored instead of `get` raising for the whole value), `sender_local_date(now)` (UTC while `sender_timezone` is unset or unreadable), `away_until(now)` (`None` without reading `sender_timezone` when no period is stored, so probe-schema tests need no `UnknownSetting` catch; review #5).
- Web: the app factory has no clock, so `Setup.away_until()` (undecorated read, like `DomainChecks.auths()`) reads `queries.setup.away_until(clock.now())` inside the GET's read-only unit; `home` passes it to `home.html`, which includes the new `away_banner.html` (`<p class="away-banner" role="status">`). `.away-banner` CSS appended to `app.css` (CRLF kept).
- `domain/sending_policy.away_until` stops extending at `date.max` (review #1). `tests/unit/test_settings_page.py` saves away dates through the Settings form and checks both refusals and that nothing is stored (review #8).
- Test harness: `tests/dashboard.py` gives `FakeUnits` dashboards a `NoDatabaseSetup` whose `away_until()` is `None`, because `GET /` now reads the database and many FakeUnits tests GET `/` for the CSRF token. Banner tests use real units.
- Tests: `tests/unit/test_sending_policy.py` (domain matrix), `tests/unit/test_settings_schema.py` (rules, order, declaration), `tests/app/test_away_dates.py` (enroll refusals with nothing written, the three origins that go ahead, tz boundary, gate first, queries), `tests/unit/test_dashboard.py` (banner present with merged end, absent otherwise, tz). Key lists updated in `test_settings_contracts`, `test_settings_schema`, `test_settings_page`, `test_telegram_secrecy`, `test_enrollment`; `away_banner.html` added to `test_source_tree` `FILES` and to `test_web_contracts`' template list. README: "Away dates" subsection under Enrollments, with the 4.10 / 5.1 / 8.9 merge notes.

## Spec Change Log

## Review Triage Log

Review pass 1 (blind hunter 11, edge-case hunter 4, verification gap 0 gaps + 1 other; 16 raw, 14 after dedupe).

| # | Source | Finding | Verdict | Evidence | Route |
|---|--------|---------|---------|----------|-------|
| 1 | edge | `away_until` overflows when a period ends 9999-12-31 and another follows in start order | medium | `end + timedelta(days=1)` raises `OverflowError` at `date.max`; GET / would 500 and enroll would raise | patch |
| 2 | edge | `enroll` reads the clock for the away check and again for the writes | low | Two `clock.now()` calls; a check at 23:59:59.999 and writes after midnight could straddle days. Direct fix: read `now` once | patch |
| 3 | edge, blind, vg-other | Two timezone readers (`sequencing._local_date` vs `queries.setup.sender_local_date`) decide "today" for the refusal and the banner | medium | They catch different exceptions and differ on naive datetimes; the away day must have one definition | patch (enroll uses `setup_queries.sender_local_date`) |
| 4 | edge | An exact duplicate line is refused although overlaps are allowed | low | The `list` codec refuses repeats with "repeats an earlier item"; a duplicate period is pointless and the message is clear | reject |
| 5 | blind | `queries.setup._sender_zone` catches `UnknownSetting` only for test probe schemas | medium | Masks a real schema mistake in production | patch (`away_until` returns `None` before reading the zone when no period is stored; drop the catch) |
| 6 | blind | `home` reads through `Setup.away_until()` instead of `queries.*` directly | false | It is a thin undecorated read of `queries.setup.away_until` in the GET's read-only unit, the same pattern as `DomainChecks.auths()`; lint-imports keeps every contract | reject |
| 7 | blind | `NoDatabaseSetup` hides the DB read on fake units; no test of a corrupt row on GET / | low | Banner tests use real units; a bad stored line is ignored (`test_a_stored_line_that_no_longer_parses_is_ignored`); the table always exists at head | reject |
| 8 | blind | Settings form over HTTP not tested for the order error and nothing stored | low | Rules are tested on parse/encode and `Setup`; the matrix row says "field error", so a page-level test is cheap | patch (one Settings page test) |
| 9 | blind | Refusal messages don't name the bad line | false | `settings_schema` convention: no message ever includes a setting's value; the shape message gives the format by example | reject |
| 10 | blind | No size limit on the setting | false | The `list` type caps 200 items and 1,000 characters per item | reject |
| 11 | blind | Past periods accepted silently and never pruned | low | Not in the intent; a past period is harmless | reject |
| 12 | blind | Timezone tests only cover a zone behind UTC | low | One direction proves the zone is applied; the conversion is `astimezone` | reject |
| 13 | blind | No log line on an `operator_away` refusal | low | No other `enroll` refusal logs; the caller (8.9) owns reporting | reject |
| 14 | blind | Banner not shown on the Settings page | low | The AC places it on the queue only; Dashboard is the stand-in | reject |
