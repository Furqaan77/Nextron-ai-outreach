---
title: 'Story 8.11: Restart a completed Sequence only on a new Event at least 90 days later'
type: 'feature'
created: '2026-11-19'
status: 'in-review'
baseline_commit: 'e530b3856def5a5034f17ebdb61df802354a7475'
route: 'dispatch'
review_loop_iteration: 2
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-8-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-nextron-outreach-engine-2026-11-14/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A Lead whose Sequence completed without a Reply can never be written to again today: Eligibility's `recontact_not_allowed` is never given Event times, and nothing can find, enroll or show a restart. FR-63 allows one restart only 90+ days after the last Step, on a live Tier 1/2 Event first seen after the Sequence ended, with Step 1 citing that Event.

**Approach:** A pure domain rule (`domain/restart.py`) plus a query that lists restart candidates with their restart Event (the callable 8.9's `daily_selection` will use), `enroll(..., origin=restart, restart_event_id=...)` that re-checks the rule in the same unit of work and stores the Event on a new immutable `enrollments.restart_event_id` column, a minimal `Selection.restart(lead_id)` use case, Event times fed to Eligibility, a pure "restart Event cited" check for the grounding rule, and a restart banner on the Lead page's Drafts section.

## Boundaries & Constraints

**Always:**
- Restart candidate ⇔ all of: the Lead's latest Enrollment (by `created_at`, then id) is `completed` (never `stopped`, any cause) with `stop_cause` NULL; it sent a Step; `now - last_step_sent_at >= 90 days` (`eligibility.RECONTACT_WAIT`); the Lead's stage is in `ENROLLABLE_STAGES` (so `not_now`, `lost`, `disqualified`, `replied`+ are out); no live Enrollment; and the Lead has a live (`signals.is_live`), unretracted Event (`signal_class=event`, tier 1 or 2, not `score_only`) with `first_seen_at > ended_at` and `<= now` and at least one live Evidence id. The restart Event is the newest `first_seen_at` such Event (tie: smallest id).
- Restart enrolls the same Contact and Campaign as the completed Enrollment. `assert_contactable` stays the first check (Blocked → nothing written: no Enrollment, Draft, Task or channel Draft). Then `enroll` re-validates the restart rule for `origin=restart` inside its unit of work and refuses with new `RefusalCode.RESTART_NOT_ALLOWED` on any mismatch (wrong/foreign/dead/old Event, too early, latest not completed). `origin=restart` requires `restart_event_id`; any other origin forbids it (`ValueError`).
- `evaluate_eligibility` passes `events_first_seen` = `first_seen_at` of the Lead's live Tier 1/2 Events (same filter as above), completing Epic 3's FR-63 wiring for every origin.
- Variant round-robin and a fresh inbox pick reuse `enroll`'s existing code; Step 1 gets a new Draft slot (there is no Thread table yet).
- Migration `0055_8_11_sequence_restart` adds nullable `enrollments.restart_event_id` (FK `signals.id`), a CHECK that it is set iff `origin='restart'`, recreates `trg_enrollments_bound` so the column is immutable; owner `sequencing`, `PiiClass.NONE`. `downgrade()` raises.
- Restart candidate ordering: each candidate converts to `domain.scoring.ReadyCandidate` with `newest_event_age` = the restart Event's `age_days`, so `ready_order` orders it with other candidates.

**Never:** send or queue any email; build `daily_selection`, its trigger, capacity or shortfall (Story 8.9); build Step 1 Claude drafting (4.15), the review queue (Epic 5), away-date rules (5.11), Threads/sends/replies tables; edit `domain/grounding_rules.py` (not in this tree, Story 4.13).

## I/O & Edge-Case Matrix

| Scenario | State (frozen `now`) | Expected |
|---|---|---|
| Too early | completed 89 days after last Step, new Event | not a candidate; `enroll(restart)` refused |
| Old Event only | 90 days, only live Event first seen before `ended_at` | not a candidate |
| Happy | 90 days, live Tier 1/2 Event first seen after `ended_at` | candidate; `Selection.restart` enrolls, origin `restart`, `restart_event_id` stored, Step 1 Draft slot |
| Stopped | latest Enrollment `stopped` (any cause) | never a candidate |
| Not Now / Lost | Lead `not_now`, or `lost` after Not Interested/Unsubscribe | never a candidate |
| Suppressed | Contact key suppressed / Not Interested | `enroll(restart)` → `Blocked` from gate, no rows written |
| Tier 3 / retracted / dead Event | only such Events after `ended_at` | not a candidate |

</frozen-after-approval>

## Code Map

- `nextron/app/sequencing/api.py` -- `Sequencing.enroll` (l.313): add keyword `restart_event_id: str | None = None`; restart check after Eligibility, before writes; pass to `enrollments_repo.insert`.
- `nextron/domain/enrollment.py` -- `EnrollmentOrigin.RESTART` exists; add `RefusalCode.RESTART_NOT_ALLOWED`.
- `nextron/domain/eligibility.py` -- `recontact_allowed`, `RECONTACT_WAIT`, `EndedSequence`; reuse, don't change.
- `nextron/domain/signals.py` -- `is_live`, `age_days`; `nextron/domain/scoring.py` -- `ReadyCandidate`, `ready_order`.
- `nextron/app/compliance/api.py` -- `evaluate_eligibility` (l.901): add `events_first_seen` to `EligibilityLead`.
- `nextron/app/queries/research.py` -- `signals(lead_id)`, `signal_evidence_ids(lead_id)`; `nextron/app/queries/sequencing.py` -- `enrollments_of_lead`, `ended_sequences`; add restart queries here (or a new `queries/restart.py` if layering needs it).
- `nextron/app/scoring/selection.py` -- `Selection(clock, sequencing, compliance)`; add `restart_candidates()` and `@use_case restart(lead_id)`.
- `nextron/app/queries/drafting.py` -- `drafts_of` (status `sent`, `status_at`) for earlier Steps sent.
- `nextron/db/models/sequencing.py` (l.75, `BOUND_COLUMNS` l.44), `nextron/db/repos/sequencing/enrollments.py`, `nextron/db/owners.py` (l.413), `nextron/db/pii_registry.py` (l.443).
- `nextron/web/leads_page.py` (`DraftSource`, `DraftRow`, `draft_row`), `nextron/web/templates/lead_drafts.html` -- restart banner.
- Tests: `tests/contracts/test_ad16_callers.py` (`_enroll` l.1251, `"sequencing.enroll"` l.1702), HEAD constants in `tests/contracts/test_persistence_contracts.py`, `tests/unit/test_migrations.py`, `tests/app/test_serve_process.py`, `FILES` in `tests/contracts/test_source_tree.py`.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/db/alembic/versions/0055_8_11_sequence_restart.py` + model/repo/owners/pii -- column, CHECK, recreated bound trigger.
- [x] `nextron/domain/restart.py` -- pure `RestartFacts`/`restart_event(facts, now) -> str | None` over the latest Enrollment, stage, live-enrollment flag and Event facts; `restart_event_cited(cited_evidence_ids, restart_evidence_ids) -> bool` (true iff the claims cite at least one Evidence of the restart Event) for 4.13's `restart_event_not_cited`.
- [x] queries -- `restart_facts(lead_id, now)`, `restart_candidates(now) -> tuple[RestartCandidate(lead_id, contact_id, campaign_id, enrollment_id, ended_at, event_id, ...)]`, `restart_context(enrollment_id) -> (previous ended_at, earlier sent Steps with dates, event_id) | None`.
- [x] `nextron/app/sequencing/api.py`, `nextron/domain/enrollment.py` -- enroll restart path.
- [x] `nextron/app/compliance/api.py` -- `events_first_seen`.
- [x] `nextron/app/scoring/selection.py` -- `restart_candidates()` (as `ReadyCandidate`s via `ready_order`) and `restart(lead_id)`, one Lead per call.
- [x] `nextron/web/leads_page.py`, `lead_drafts.html` -- for a restart Enrollment's Drafts: "Restart: previous Sequence ended <date> without a Reply" and the earlier sent Steps with dates.
- [x] Tests -- unit (domain matrix incl. 89/90-day boundary, tie-breaks, stopped/not_now/lost, tier 3/retracted/score_only), app (`Selection.restart`, `enroll` refusals, Blocked writes nothing, Eligibility now passes after a new Event), migration (CHECK, immutability), web (banner), AD-16 restart case, contracts HEAD bumps.
- [x] `nextron-engine/README.md` -- Story 8.11 section.

**Acceptance Criteria:**
- Given the frozen-`now` matrix, when restart candidates are listed, then exactly the Happy rows appear, each with its restart Event, ordered by `ready_order` using that Event's age.
- Given a restart enrolls, then the Enrollment has origin `restart` and `restart_event_id`, a round-robin Variant, a freshly picked inbox and a Step 1 Draft slot; updating `restart_event_id` later raises.
- Given a restart Enrollment's Drafts on the Lead page, then the restart line and earlier sent Steps with dates show.

## Implementation Notes

- **Migration** `0055_8_11_sequence_restart` (revision id = file stem, down `0054_2_16_signal_inbox`) adds nullable `restart_event_id` in place, with no table rebuild (loop 1, G4): one `op.execute("ALTER TABLE enrollments ADD COLUMN restart_event_id VARCHAR(26) CONSTRAINT fk_enrollments_restart_event_id_signals REFERENCES signals (id) CONSTRAINT ck_enrollments_restart_event CHECK ((origin = 'restart') = (restart_event_id IS NOT NULL)))")`, in the style of `0009_1_17_offpc`; then `DROP TRIGGER trg_enrollments_bound` and recreates it with `restart_event_id` added to 0038's bound columns. `trg_enrollments_erased_insert`, the partial unique index and every row are untouched. Merge touchpoint (in the docstring): re-chained after main's `0056_11_7_sequence_editing`, the recreated trigger must also list `sequence_version_id`. Model, `BOUND_COLUMNS`, `owners.py` (sequencing) and `pii_registry.py` (NONE) updated; HEAD constants, chain probes (0056/0057) and `test_source_tree.FILES` bumped.
- **Domain** `nextron/domain/restart.py`: `EventFacts`, `LatestEnrollment` (with `origin`, loop 1 G1), `RestartFacts` (with `restarted: bool = False`, loop 2 H2), `qualifying_event` (event class, Tier 1/2, not score_only, unretracted, first seen <= now, >=1 live Evidence id, `is_live` at `age_days`), `events_first_seen` (checks `now` is aware first, even with no Events: loop 3 I2), `restart_event` (`None` when `restarted` or the latest Enrollment's origin is `restart`: a Lead restarts once; otherwise the newest qualifying Event first seen after `ended_at`; tie smallest id), `restart_event_cited` (`TypeError` for a `str`/`bytes` or non-iterable argument, loop 2 H3), and (loop 2 H1) `RECONTACT_REASON` = `Reason(RECONTACT_NOT_ALLOWED, RECONTACT_TEXT)` with `without_restart(decision, history) -> Decision`: while `history` holds an unreplied `EndedSequence`, a Decision without `recontact_not_allowed` comes back `Decision(False, None, reasons + RECONTACT_REASON)` in `ReasonKind` order; otherwise unchanged. It defines its own `ENROLLABLE_STAGES` (a unit test asserts it equals `sequencing.api.ENROLLABLE_STAGES`).
- **Queries**: `queries.research.event_facts(lead_id)` (its `retracted` is always False, since `signals()` already leaves retracted Signals out; documented, loop 3 I6); `queries.sequencing.restart_facts(lead_id)` (fills `restarted` = any of the Lead's Enrollments has origin `restart`), `restart_candidate(lead_id, now)`, `restart_candidates(now)` (over the new repo read `enrollments.leads_with_completed()`), `restart_context(enrollment_id)` (`RestartContext`/`SentStep`; previous = the Lead's Enrollment created just before the restart). `_latest` fills `LatestEnrollment.origin`. `queries.sequencing` now also imports `queries.pipeline` and `queries.research` (no cycle; lint-imports keeps all contracts). `StoredEnrollment` gained `restart_event_id` (default `None`).
- **enroll**: keyword-only `restart_event_id`; `_check_restart_event` (TypeError / ValueError) runs with the argument checks. For origin `restart`, the restart check (`_restart_allowed`: `restart_candidate` must exist with the same Event, Contact and Campaign) runs straight after the `blocked` gate (loop 1, G3), before `campaign_not_active` and every other refusal, so every restart mismatch (too early, old Event only, wrong/foreign/Tier 3 Event, stopped or restart latest Enrollment, a Lead that ever restarted, another Contact, another Campaign) refuses `restart_not_allowed`; `RefusalCode.RESTART_NOT_ALLOWED` sits second, after `BLOCKED` (ready_page label added). The Eligibility step calls `evaluate_eligibility(contact.id, restart=chosen_origin is EnrollmentOrigin.RESTART)` (loop 2 H1); `enroll`'s own `_unanswered_history` and `RECONTACT_REASON` are gone, so a non-restart origin's `not_eligible` carries `recontact_not_allowed` next to every other failing reason.
- **Eligibility**: `evaluate_eligibility(contact_id, *, restart=False)` (a non-`bool` `restart`: `TypeError`) reads `now` once, passes `events_first_seen = restart.events_first_seen(research_queries.event_facts(lead.id), now)` for every call, reads `history = ended_sequences(contact.id)` once, and returns the Decision as is when `restart` is true or (loop 3, I1) the Lead's live Enrollment (`queries.sequencing.live_enrollment(lead.id)`, helper `_live_restart_of`) has origin `restart` and belongs to this Contact, otherwise `without_restart(decision, history)` (loop 2 H1: AD-21, only a restart re-contacts an unanswered Contact). So during a live restart the Lead page Contacts panel and any default caller (6.9's send gate: deferred-work) see the Contact eligible; once the restart ends, or for another Contact of the Lead, the rule applies again. Display callers (`Selection.ready_list`, the Lead page) keep the default. The one test monkeypatch of it (`test_enroll.py` UK basis) takes `restart=`.
- **Selection**: `restart_candidates() -> tuple[RestartRow(ready, restart), ...]` in `ready_order`; Leads with no stored Tier/Lead Score are left out. `@use_case restart(lead_id) -> PickOutcome` (reasons `restart_not_allowed`, `no_campaign`, or enroll's code); every Step of the Campaign's Sequence; no `daily_capacity` write; logs `lead_restarted` with `enrolled` (0/1) and `reason` (the outcome's code or `None`, loop 2 H7).
- **Docs (loop 3 I3)**: the `domain/eligibility.py` module docstring names `evaluate_eligibility(contact_id, *, restart=False)`, the Story 8.11 Event times and AD-21 rule with the live-restart exemption; the README's 8.11 Eligibility bullet makes `not_eligible` conditional on a Sequence that ended without a Reply. `deferred-work.md` gains the 6.9 send-gate touchpoint (I1) and D1 (FR-63 history per Contact, not per Lead).
- **Web**: `DraftSource.restart` / `DraftRow.restart` (`RestartBanner`), filled by `app.py lead_drafts` from `restart_context`; `lead_drafts.html` renders `div.draft-restart` on each Draft of a restart Enrollment.
- **Tests**: `tests/unit/test_restart.py` (incl. a completed `restart` Enrollment never restarting, other origins restarting), `tests/app/test_sequence_restart.py` (matrix with a live Tier 2 "old" Event first seen before the end via `completed(..., ended_days=)`; too early / old Event only refuse `restart_not_allowed`; another Contact of the Lead and another active matching Campaign (`ops_automation_us` after moving the Lead's Segment) refuse `restart_not_allowed`; an `operator_pick` is `not_eligible`/`recontact_not_allowed` while `Selection.restart` succeeds; a completed restart never restarts; the bound trigger alone refuses a change between two valid Events; `restart_context` is `None` for a first restart Enrollment and an unended previous one; a suppressed Contact's `Selection.restart` reason is exactly `blocked`; loop 2: `evaluate_eligibility(restart=True)` eligible while the default carries `recontact_not_allowed`; `operator_pick`/`selection` refusals keep `recontact_not_allowed` next to `provenance_missing`; `Selection.ready_list` shows a completed, unanswered, scored Lead with a new Event as not eligible; restart completes, another Contact's `operator_pick` completes, a newer Event 100 days on is no candidate; a Not Interested suppression (`reply_class`, person scope: this tree has no Reply path, so the test writes the entry a Not Interested Reply would) closes the Lead `lost`, so `Selection.restart` is `restart_not_allowed`, `enroll(restart)` `blocked`, nothing written; a non-restart Enrollment's rendered Drafts have no `draft-restart`; loop 3 I1: after a restart the default `evaluate_eligibility` is eligible and the Lead page has no `recontact_not_allowed` item, and once the restart ends it carries `recontact_not_allowed` again; a live restart of another Contact of the Lead leaves this Contact's `recontact_not_allowed`), `tests/unit/test_migrations.py::test_0055_adds_the_restart_event_in_place_and_keeps_every_enrollment_and_draft` (populated 0054 upgrade, plus the positive case: a `restart` row naming a real Signal is accepted with `foreign_key_check` empty; loop 3 I5: the recreated `trg_enrollments_bound` names every `BOUND_COLUMNS` column), `tests/unit/test_restart.py` (loop 2: `restarted`, `restart_event_cited` type checks, `without_restart`; loop 3 I2: `events_first_seen((), naive)` raises `TypeError`), AD-16 caller `scoring.restart` (`Selection\.restart`) with a `_restart` scenario (loop 3 I4: `_restarted` checks the single restart row is the allowed Lead's, with that Lead's Signal as `restart_event_id`), `test_enroll.py` (bound trigger param, CHECK param, repo function set), `test_enrollment.py` refusal order.

## Spec Change Log

- **Loop 1 (review of the first implementation).**
  - **Triggering findings:** G1, G2 and G3 in the Review Triage Log.
  - **Amended (non-frozen sections only):**
    - G2: "an operator pick may also re-enroll" is replaced by AD-21's rule that a restart after an unanswered Sequence is `origin=restart` only. `enroll` with any origin other than `restart` refuses `not_eligible` (reason `recontact_not_allowed`) whenever the Contact's FR-63 history (`queries.sequencing.ended_sequences(contact)`) is non-empty. This keeps every non-restart origin exactly as it behaved before Event times were wired.
    - G3: for origin `restart`, the restart check runs straight after the `blocked` gate and before the other refusals. "Too early", "old Event only" and the other mismatches then refuse `restart_not_allowed`, as the frozen Boundaries say.
    - G1: a completed Enrollment whose own origin is `restart` never restarts again. `LatestEnrollment` gains `origin`.
  - **Known-bad state avoided:**
    - an `operator_pick` or `selection` enroll re-contacting an unanswered Contact outside the restart rules, with no `restart_event_id` and no Event citation;
    - "too early" reported as `not_eligible`, contradicting the frozen text;
    - unbounded repeat restarts.
  - **KEEP:** everything else in the first implementation: the domain module, queries, `Selection.restart`/`restart_candidates`, the web banner, the AD-16 scenario and the tests. The code was not reverted; it is amended in place, because no part of it outside these rules was found wrong.

- **Loop 2 (review of the loop-1 tree).**
  - **Triggering finding:** H1 in the Review Triage Log.
  - **Amended (non-frozen sections only):** AD-21's "only a restart re-contacts an unanswered Contact" moves from `enroll` into the Decision itself. `Compliance.evaluate_eligibility(contact_id, *, restart=False)` still passes `events_first_seen` for every origin, which keeps the frozen wiring. When `restart` is false and the Contact's FR-63 history holds a Sequence that ended without a Reply, the Decision is not eligible and carries `recontact_not_allowed`, added once next to any other reasons. `enroll` calls it with `restart=(origin is restart)` and drops its own `_unanswered_history` guard and `RECONTACT_REASON`. Display callers (the Ready list, the Lead page) keep the default, so they show what a pick would get.
  - **Known-bad state avoided:**
    - the Ready list and the Lead page show "Eligible" while a pick is refused;
    - the refusal drops `recontact_not_allowed` when another reason also fails.
  - **KEEP:** all of loop 1 (G1–G11), the domain, queries, migration, `Selection` and the web code.

## Review Triage Log

Raw findings: 26 (blind hunter BH1-BH15, verification gap VG1-VG2 plus VGo1-VGo4, edge-case hunter EC1-EC5). Deduplicated: 21.

| Id | Members | Verdict | Route | Evidence / action |
|---|---|---|---|---|
| G1 | BH1, EC1 | medium | bad_spec | `restart_event` never reads the completed Enrollment's origin, so a restart Enrollment that completes can restart again and again. The frozen Problem reads "allows one restart"; the conservative reading is one restart per Lead's chain. Amended: refuse when the latest Enrollment's origin is `restart`. |
| G2 | BH2, EC2 | high | bad_spec | Eligibility now gets Event times for every origin, so once FR-63 holds, `operator_pick` or `selection` can re-enroll an unanswered Lead with no restart Event. AD-21 says "A restart after a Sequence ended without a Reply (FR-63) is `origin=restart`, by selection only." Overnight decision 6 contradicted that; amended. |
| G3 | BH3, EC3 | medium | bad_spec | The frozen Boundaries say `enroll(restart)` refuses `RESTART_NOT_ALLOWED` on any mismatch "(... too early ...)", but the code returned `not_eligible`, because Eligibility ran first. Amended: the restart check runs right after the gate. |
| G4 | BH9 | high | patch | Main's `0056_11_7_sequence_editing` adds `enrollments.sequence_version_id` (ALTER TABLE) and recreates `trg_enrollments_bound`. Re-chained after it, 0055's `batch_alter_table(recreate="always", copy_from=<0038 table>)` would drop that column and its data. Fix: use `ALTER TABLE enrollments ADD COLUMN restart_event_id ... REFERENCES signals(id)` with a named CHECK, drop and recreate only `trg_enrollments_bound`, and do no table rebuild. |
| G5 | VG1 | medium | patch | No test upgrades a populated 0054 database to 0055. Add one like the 0039 and 0045 tests: rows unchanged, `restart_event_id` NULL, `foreign_key_check` empty, partial unique index and both triggers present, CHECK enforced. |
| G6 | BH4, VGo2 | low | patch | `test_a_suppressed_contact_is_blocked_and_nothing_is_written` accepts `blocked` or `restart_not_allowed`. Suppression doesn't change `restart_candidate`, so the reason must be exactly `blocked`. |
| G7 | VG2 | medium | patch | `_restart_allowed`'s same-Contact and same-Campaign comparisons are never exercised on their own: the "another Contact" case fails on the Event id. Add a second Contact of the same Lead, and another valid Campaign, both using the correct Event. |
| G8 | BH10 | low | patch | The bound-trigger parameter for `restart_event_id` also breaks the CHECK. Add a test changing one valid restart Event to another on a restart row, so the trigger alone refuses it. |
| G9 | BH15a, VGo1 | low | patch | The app matrix "old" row and the Eligibility test use Tier 1 Events older than 90 days, so liveness rejects them, not "first seen before it ended". Use Tier 2 and fix the docstring's "dead" claim. |
| G10 | VGo4 | low | patch | `restart_context`'s `None` branches are untested: a first Enrollment, and a previous Enrollment that hasn't ended. Add a direct test. |
| G11 | BH14 | low | patch | `nextron.domain.restart` imports are out of alphabetical order in `compliance/api.py` and `queries/research.py`. |
| R1 | BH5 | low | reject | `restart_candidates` does N+1 reads over every Lead with a completed Enrollment. This is a single-operator volume, and the fix adds query complexity. Noted for 8.9. |
| R2 | BH6 | low | reject | `Selection.restart` computes the candidate twice. With no candidate it answers before the gate, but nothing is written, and the AD-16 `scoring.restart` scenario passes. |
| R3 | BH7, EC4 | low | reject | After a Lead merge, the restart context's previous Enrollment may be another Contact's. Merges are rare, and the fix adds a branch. |
| R4 | BH8 | false | reject | "Nothing reaches the feature": the spec's Never list keeps `daily_selection` and its trigger in 8.9, and `Selection.restart` is the minimal trigger. |
| R5 | BH11 | low | reject | UTC banner dates are a recorded spec decision; the fix edits the spec. |
| R6 | BH12 | low | reject | The banner repeats on every Draft by recorded spec decision. |
| R7 | BH13 | low | reject | The banner doesn't name the restart Event. No AC requires it, and the epic's card text names only the date and Steps. |
| R8 | BH15b | low | reject | The generic `restart_not_allowed` reason text is cosmetic. |
| R9 | VGo3 | low | reject | `_restart_one`'s `InvalidCampaigns` branch is untested; testing it needs a corrupt stored Campaign. Fail-closed code. |
| R10 | EC5 | low | reject | An erased Contact's Lead may be listed as a candidate, but `enroll` refuses it (gate and erased-insert trigger). Nothing is written. |

Loop 2 review (on the loop-1 tree). Raw findings: 21 (blind hunter BH2-1 to BH2-10 with sub-items, verification gap VG2-1 plus VG2o-1, edge-case hunter EC2-1 to EC2-6). Deduplicated: 15.

| Id | Members | Verdict | Route | Evidence / action |
|---|---|---|---|---|
| H1 | VG2-1, BH2-1, EC2-1, EC2-2, EC2-5 | medium | bad_spec | Event times now reach every Decision, but the AD-21 rule lived only in `enroll`. `Selection.ready_list` (selection.py l.367) and the Lead page Contacts panel (web/app.py l.2234/2468/2501) showed "Eligible" for an unanswered Contact that a pick refuses, and the refusal lost `recontact_not_allowed` when another reason also failed. Amended (loop 2): the rule goes into `evaluate_eligibility(contact_id, *, restart=False)`. |
| H2 | BH2-2, EC2-4 | medium | patch | "A Lead restarts at most once" checked only the latest Enrollment's origin. Path: restart R completes, then another Contact of the Lead (no history of its own) is enrolled and completes; the Lead would restart again. Fix: `RestartFacts.restarted` is true when any of the Lead's Enrollments has origin `restart`, and `restart_event` refuses then. Unit and app test. |
| H3 | BH2-5 | low | patch | `restart_event_cited` doesn't type-check, contrary to its module docstring; a bare `str` is iterated by character. Fix: raise `TypeError` for a `str` argument (or a non-iterable). Unit test. |
| H4 | BH2-6 | low | patch | The 0055 migration test has no positive case. Add one: after upgrade, a `restart` row naming a real Signal is accepted. |
| H5 | BH2-7 | medium | patch | The merge touchpoints were recorded only in this spec. Append them to `deferred-work.md`: the 0056 re-chain (`trg_enrollments_bound` must list `sequence_version_id` and `restart_event_id`), 5.11 `operator_away`, 4.13 `restart_event_not_cited`, 4.15 and Epic 5 `restart_context`, and 8.9's notes (R1, H9, H10). |
| H6 | BH2-8 | medium | patch | The matrix row "Suppressed: … / Not Interested" was tested with an address suppression only. Add a case where the Contact is marked Not Interested through this tree's existing compliance path, so `Selection.restart` gives `blocked` (or the Lead isn't a candidate) and nothing is written. If no Not Interested path exists in this tree, use the suppression cause it would write. |
| H7 | BH2-9 | low | patch | The `lead_restarted` log line has no refusal reason. Add `reason` (the PII-free code or `None`). |
| H8 | BH2-10a,b,d | low | patch | Test hygiene in `tests/app/test_sequence_restart.py`: import order, a misleading comment in the `restart_context` None test, and a banner test that never renders. Render a non-restart Draft and assert there is no `draft-restart`. |
| R11 | BH2-10c | low | reject | The `restart_context` None test drops `trg_enrollments_bound` to reorder `created_at`. That is test-only, and restructuring adds complexity. |
| R12 | BH2-3 | low | reject | `restart_candidates` doesn't flag `below_minimum`. 8.9 owns selection policy; noted in deferred-work (H5). |
| R13 | BH2-4 | low | reject | Candidates whose Campaign is inactive, or whose Contact lacks tz/Market, are listed, but `enroll` refuses them and writes nothing. Noted for 8.9 (H5). |
| R14 | EC2-3, EC2-6 | low | reject | The 0055 CHECK would fail on an existing `origin='restart'` row. No code path created one before this story: `Selection.pick` uses `operator_pick`, and no `restart` caller existed. |
| R15 | BH2-11 | false | reject | "Stale bookkeeping": the loop-2 triage and change log are being written now, and the sprint status is set at finish. |
| R16 | VG2o-1 | low | reject | The `restart_not_allowed` label on the Ready page isn't reachable from `pick`. It is harmless, and `Selection.restart` outcomes use the same labels. |

Loop 3 review (on the loop-2 tree). Raw findings: 13 (blind hunter BH3-1 to BH3-11 with sub-items, verification gap: none, edge-case hunter EC3-1). Deduplicated: 13. No intent_gap or bad_spec, so there is no further loop.

| Id | Members | Verdict | Route | Evidence / action |
|---|---|---|---|---|
| I1 | BH3-1 | medium | patch | With a restart Enrollment live, the Contact's history still holds the unanswered Sequence. Any `evaluate_eligibility(contact)` default caller then adds `recontact_not_allowed`: the Lead page panel shows "not eligible" during a live restart, and a send gate calling with the default (6.9, per the compliance docstring) would refuse every restart Step. Fix: `evaluate_eligibility` treats the call as a restart when the Contact's live Enrollment has origin `restart`. Test it; record the 6.9 touchpoint in `deferred-work.md`. |
| I2 | BH3-2 | low | patch | `events_first_seen((), naive_now)` returns `()` without raising, contrary to the module docstring. Fix: call `_aware(now)` first. Unit test. |
| I3 | BH3-5 | low | patch | The `domain/eligibility.py` docstring still describes `evaluate_eligibility(contact_id)` and Event times as "Story 2.9". The README's "every other origin … get `not_eligible`" sentence reads as unconditional. Correct both. |
| I4 | BH3-7 | low | patch | The AD-16 `_restarted` checker counts restart rows only. Also check the Lead and the `restart_event_id`. |
| I5 | BH3-11b | low | patch | The 0055 migration test asserts that the trigger mentions `restart_event_id`, but not every 0038 bound column. Assert all of `BOUND_COLUMNS`. |
| I6 | BH3-11a | low | patch | `event_facts`' `retracted` field is always False on the real path, because `signals()` already excludes retracted Signals. Say so in its docstring. |
| D1 | EC3-1 | medium (unverified scope) | defer | FR-63 history is per Contact (Epic 3's `ended_sequences(contact_id)`, pre-existing). Another Contact of a Lead whose Sequence ended unanswered on Contact A can still be picked at once. This predates the story. Recorded in deferred-work for a decision on per-Lead history. |
| R17 | BH3-3 | false | reject | `Decision.__post_init__` (eligibility.py l.332-334) refuses reasons out of `ReasonKind` order, so a wrong sort in `without_restart` raises in the existing test. |
| R18 | BH3-4 | low | reject | The `restart_not_allowed` entry's position in `REASON_TEXTS` is a dict lookup order only; cosmetic. |
| R19 | BH3-6 | false | reject | The tracking files are set at finish (spec done, sprint key review). |
| R20 | BH3-8 | low | reject | Unsubscribe closes a Lead through the same suppression and `close_suppressed` path as the tested Not Interested case. |
| R21 | BH3-9 | low | reject | The `>` and `<=` boundaries and the tie-breaks are unit-tested in the pure domain function. The app path passes stored `first_seen_at` through unchanged. |
| R22 | BH3-10 | low | reject | carried R3: the restart context is found by position. Merges are rare; fixing it needs a new bound column. |

## Design Notes

Boundaries left to later stories (merge touchpoints): 8.9 calls `Selection.restart_candidates()` inside `daily_selection` and enrolls via `Selection.restart` / `enroll(origin=restart)` per Lead, counting capacity; 5.11 (merging separately) refuses origin `restart` with `operator_away` inside `enroll` — the merge must keep both checks; 4.13's `domain.grounding_rules` gains `restart_event_not_cited` wrapping `restart.restart_event_cited`, and 4.15's Step 1 drafting reads the Event from `restart_context`; Epic 5's review card reuses `restart_context`.

### Decisions made overnight (pending user review)
- Eligibility's `events_first_seen` uses the same filter as the restart Event, including "at least one live Evidence id" (conservative: an Evidence-less Event never clears FR-63).
- `enroll(origin=restart)` requires the given Event to be exactly the computed restart Event (newest qualifying one), not just any qualifying Event.
- `Selection.restart_candidates` leaves out Leads with no stored Tier or Lead Score (they can't be ordered by `ready_order`).
- The restart banner shows on every Draft of the restart Enrollment, with UTC dates.
- No `daily_selection` job or trigger is built; the minimal trigger is `Selection.restart(lead_id)`, a use case 8.9 will call (8.9 owns capacity, weekends and shortfall).
- (Amended in loops 1 and 2, G2/H1.) Eligibility receives live Tier 1/2 Event first-seen times for every origin, but `evaluate_eligibility(..., restart=False)` (every caller except a restart `enroll`) adds `recontact_not_allowed` while the Contact has an unanswered FR-63 Sequence. So only a restart re-contacts an unanswered Contact (AD-21: restarts are selection-only, `origin=restart`).
- (Loops 1 and 2, G1/H2.) A Lead restarts at most once: once any of its Enrollments has origin `restart`, it never restarts again (conservative reading of "one restart").
- (Loop 1, G3.) For origin `restart`, the restart check runs right after the AD-16 gate, so every restart mismatch, too early included, refuses `restart_not_allowed`.
- (Loop 1, G4.) Migration 0055 adds the column with ALTER TABLE ADD COLUMN instead of a table rebuild, so it can be re-chained after main's 0056 (which adds `enrollments.sequence_version_id`) without dropping it. Merge touchpoint: `trg_enrollments_bound` must list both `sequence_version_id` and `restart_event_id`.
- A restart Event must have at least one live Evidence id, so Step 1 has something to cite.
- Restart reuses the completed Enrollment's Contact and Campaign; an inactive Campaign or blocked Contact refuses it.
- "No Reply on any Thread" is read as `status=completed` with `stop_cause` NULL (Reply stops a Sequence as `reply`/`manual_reply`; there are no Thread tables yet).

## Verification

**Commands:**
- `cd e:/Nextron-epic1/nextron-engine && uv run pytest -q -n 2 <touched test files>` -- pass
- `uv run pytest -q -n 2 tests/contracts` -- pass
- `uv run lint-imports` -- all contracts kept
