---
title: "Story 5.3: Show the Evidence, facts and grounding result behind every card"
type: 'feature'
created: '2026-11-19'
status: 'done'
baseline_commit: 'fb8d52bc21f5404097c2d9b2cf5d0c113f9a0a72'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-nextron-outreach-engine-2026-11-14/ARCHITECTURE-SPINE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-5-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** 5.1's queue card shows the message but not why it can be trusted: no Evidence, no Approved Company Facts, no grounding result, no earlier Steps, no `rev`/`shown_hash` binding that 5.4's approve needs, and its `?card=k` target shifts when the queue changes.

**Approach:** Each `QueueCard` gains its bound inbox, `shown_hash`, `generic`/`stale` flags and the current revision's grounding status; a new read-only `queries.approval.card_details(card)` (called for the focused card only) returns its Evidence rows, fact versions, Playbook version, earlier sent Steps and grounding flags. `queue.html` renders them, colours a `fail` card red with highlighted spans, disables approval for `fail`/`pending`, and `?draft=<id>` becomes the stable card target.

## Boundaries & Constraints

**Always:**
- **Evidence:** for each id in the current revision's `evidence_ids` (in order), from `queries.research.evidence_history(lead_id)` (superseded/retracted rows still shown, labelled "superseded"/"retracted"): type label Quote / Absence / Count; Quote → `quote` (plus `fact: value` when set); Absence → `sought` and the pages checked; Count → `number` and `sought`; source URL via the existing `safe_link` filter (link only for `http`/`https`, `rel="noopener noreferrer"`, else plain text; none → "—"); `retrieved_at`; label "AI-extracted" when `extracted_by` starts with `ai:`. An id not found → a row "Evidence not found" with the id.
- **Facts:** each `(fact_id, version)` of `fact_versions` from `queries.playbook.fact(fact_id)` → that version's wording, type, source link (`safe_link`); "retracted" label when that version is retracted; missing → "Fact not found" row. Plus the revision's `playbook_version` ("—" when `None`).
- **Earlier Steps** (Step Drafts with `step_no > 1` and an Enrollment): the Enrollment's OMs with status `sent` and `step_no` < this Draft's, by `step_no`: subject, send date (`sent_at` in `sender_timezone`), body inside a `<details>` element (expandable from the keyboard with one key on its focused summary). None → "No earlier Step sent yet".
- **Grounding:** `queries.drafting.grounding_result(draft_id, rev)` for the card's `rev`; no row → treated as `pending` (4.13 deferral: not grounded). `fail` → card element has class `grounding-fail` (red, `app.css`), each flag listed with its rule code and explanation, and the first occurrence of each non-empty span in the shown subject/body wrapped in `<mark>` (segments built in Python, rendered via `|plain`; overlaps skip the later span), notice "Edit this Draft to remove the failing claims before approving". `pending` → "Grounding check pending". `pass` → a `pass` badge. Reuse `leads_page.grounding_of` for status/flag rows.
- **Approve control:** when grounding is `fail` or `pending`, or the card has no `shown_hash`, render `<button type="button" class="queue-approve" disabled>Approve</button>` with the notice; otherwise render no approve control (5.4 adds the enabled form).
- **Badges:** one badge row holding the grounding badge, then `generic` and `stale` badges when those Draft columns are true; later stories append theirs to the same row.
- **Binding:** every card renders `<input type="hidden" name="rev">` and `<input type="hidden" name="shown_hash">`; `shown_hash = render.text_hash(message.to_address, enrollment.sending_inbox_id, message.subject, message.body)` of exactly the composed message shown (same inputs as `Sending.enqueue`). No composed message or no Enrollment → `shown_hash` empty and approval disabled ("This Draft can't be approved yet").
- **Stable target:** `?draft=<id>` selects that Draft when it is in the flattened cards, else falls back to `?card=k`; previous/next links use `?draft=<id>`.
- GET stays a read-only unit; `@personal_data(True)`; no-store; card content inside `hx-disable`; everything `|plain`/`safe_link`, no `|safe`/`Markup` outside `templating.py`.

**Never:** an enabled approve form or any POST (5.4), keyboard JS or the `V` toggle script (5.7), badges for skipped/event-expired/reapproval/stalled (their stories), a migration, a new dependency, sending anything, reading another service's repo from `queries.approval`.

### Decisions made overnight (pending user review)
1. Details are computed only for the focused card (`card_details`), so a page load doesn't read Evidence/facts/OMs for all N cards.
2. A revision with no grounding row shows as "Grounding check pending" and can't be approved.
3. For a passing card no approve control renders until 5.4; a disabled button renders only when blocked.
4. "One-key expandable" uses native `<details>`/`<summary>`; 5.7's keymap may add a binding.
5. Responsive Drafts (no Enrollment, inbox from Threads in Epic 7) get an empty `shown_hash` and disabled approval.
6. `?draft=<id>` was added here (5.1 deferral) so later decisions target the Draft shown.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Error handling |
|---|---|---|---|
| Evidence | Quote (ai), Absence (rule), Count | type, text/pages/number, link, time, "AI-extracted" only on the ai row | N/A |
| Bad URL | `javascript:` / `ftp:` source URL | plain text, no `<a>` | N/A |
| Facts | rev cites fact v1 after v2 exists | v1 wording shown | missing → "Fact not found" |
| Follow-up | Step 3, Step 1 OM `sent`, Step 2 OM `queued` | only Step 1 listed, body in `<details>` | N/A |
| Fail | grounding `fail` with spans | class `grounding-fail`, `<mark>` spans, notice, disabled button | N/A |
| Pending / none | `pending` or no row | "Grounding check pending", disabled button | N/A |
| Pass | `pass` | `pass` badge, no approve control | N/A |
| Flags | `generic`, `stale` true | both badges in the badge row | N/A |
| Hash | Step 1, follow-up, UK card | hidden `shown_hash` = `text_hash` of shown To/Subject/body + inbox | N/A |
| Target | `?draft=<id>` present / unknown | that card / falls back to `?card` | N/A |
| Escaping | `<b>` in quote, wording, subject, span | escaped | N/A |

</frozen-after-approval>

## Code Map

All under `nextron-engine/`.
- `nextron/app/queries/approval.py` -- `QueueCard` (:92) gains `inbox_id`, `shown_hash`, `generic`, `stale`, `grounding_status`; built in `due_queue`'s `_Reader`. New `card_details(card) -> CardDetails` with `CardEvidence`, `CardFact`, `EarlierStep`, `playbook_version`, `grounding` (the `GroundingResultView` or `None`). May import `nextron.domain`, `nextron.db.repos.approval`, other `nextron.app.queries.*`; never `app.compliance` (c6) nor other services' repos (c7-owner-approval).
- `nextron/db/repos/sending/outbound_messages.py` -- add `sent_of_enrollment(enrollment_id)` (status `sent`, by `step_no`); `nextron/app/queries/sending.py` -- `sent_of_enrollment` returning `OutboundMessageView`s.
- `nextron/app/queries/drafting.py` -- `current_revision`, `revision`, `grounding_result` (:234), `RevisionView` (`evidence_ids`, `fact_versions`, `playbook_version`).
- `nextron/app/queries/research.py:326` `evidence_history`; `StoredEvidence` (`nextron/db/repos/research/signals.py:66`). Shapes: quote has `quote`; absence `sought`+`pages_checked`; count `number`+`sought`; `fact`/`value` optional. `EvidenceType` in `domain/segments.py:103`.
- `nextron/app/queries/playbook.py:605` `fact(fact_id)` → `FactHistory.versions` (`FactVersion.content`: `type`, `wording`, `source_url`; `retracted_at`).
- `nextron/app/queries/sequencing.py` `enrollment(id)` (`sending_inbox_id`).
- `nextron/domain/render.py:265` `text_hash`; compose usage in `app/sending/api.py:339-343` (hash inputs to mirror).
- `nextron/web/leads_page.py:1576` `grounding_of`, `GroundingFlagRow`, `PENDING_GROUNDING`/`FAIL_GROUNDING`; `lead_drafts.html:25-31` badge/flag markup to mirror.
- `nextron/web/templating.py` -- `safe_link` filter (:81) for every source URL.
- `nextron/web/queue_page.py` -- `CardRow`, `card_row`, `card_index`, `_href`, `queue_context` gain details, highlight segments, badges, `?draft=` handling; `nextron/web/app.py:681` route passes `draft` param and `card_details` of the focused card.
- `nextron/web/templates/queue.html` -- no `<script>`, `on*`, `hx-on*`, `style=` (web contracts ~:583).
- `nextron/web/static/app.css` -- red `.queue-card.grounding-fail`; edit byte-preserving (first 293 lines CRLF; Edit tool or Python `newline=""`).
- Tests: `tests/app/test_due_queue.py` (helpers, world, page pattern); `tests/app/test_outbound_messages.py` (making OMs; set status `sent` via repo `set_status` queued→sending→sent); `tests/app/test_ai_grounding.py`/`test_draft_revisions.py` for grounding rows and evidence seeding.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/db/repos/sending/outbound_messages.py`, `nextron/app/queries/sending.py` -- `sent_of_enrollment`.
- [x] `nextron/app/queries/approval.py` -- card fields, `card_details` and its types.
- [x] `nextron/web/queue_page.py`, `nextron/web/app.py`, `templates/queue.html`, `static/app.css` -- rendering, highlights, badges, binding fields, approve control, `?draft=`.
- [x] `tests/app/test_card_details.py` (new) -- the I/O matrix through the query and `GET /queue`, incl. the `shown_hash` match for Step 1, follow-up and UK fixtures (hash recomputed from the page's unescaped To/Subject/body and the Enrollment inbox, and equal to the hash `Sending.enqueue` checks).
- [x] pins (`test_source_tree` FILES) and `nextron-engine/README.md` Story 5.3 section.

**Acceptance Criteria:**
- Given a queue page load, then nothing is written and only the focused card's details are read.
- Given every rendered link, then it carries `rel="noopener noreferrer"`.

## Implementation Notes

- `repos/sending/outbound_messages.sent_of_enrollment` (status `sent`, by `step_no`, then `sent_at`, id) and `queries.sending.sent_of_enrollment` (non-ULID → `()`).
- `queries.approval.QueueCard` gains `enrollment_id`, `inbox_id` (the Enrollment's `sending_inbox_id`, read once per Enrollment per build), `shown_hash`, `generic`, `stale`, `grounding_status` (`GroundingStatus`; `pending` without a row for `current_rev`). Held-back cards get the same fields.
- `shown_hash` is `""` unless the kind is `step`/`recontact` (`BINDABLE_KINDS`, mirroring `Sending.ENQUEUE_KINDS`, which `queries` can't import), the message composed, the Enrollment known **and the To address non-empty** (enqueue raises for a Contact without an email, so such a card is never bindable). Otherwise `render.text_hash(to, inbox, subject, body)` of the composed message.
- `card_details(card) -> CardDetails(evidence, facts, playbook_version, earlier_steps, grounding, revision)`; `CardEvidence`/`CardFact`/`EarlierStep` are own dataclasses (no other owner's repo type is imported; c7 kept). Evidence state: `retracted` wins over `superseded`. A fact that raises `InvalidCompanyFacts` reads as not found. Earlier Steps only for kind `step`, `step_no > 1`, with an Enrollment. `quote`/`value`/OM `subject`/`body` are `repr=False`.
- `queue_page`: `queue_context(..., draft_param=, details_of=)` calls `details_of` for the focused card only (the route passes `lambda card: approval_queries.card_details(card)`, looked up at call time). `focused_index` honours `?draft=` then `card_index`. Prev/next → `/queue?draft=<id>`.
- `card_row` uses `leads_page.grounding_of(details.revision, details.grounding)`; without details (no revision) it falls back to `card.grounding_status` with no flags.
- Highlights (`highlight`): spans deduplicated in flag order, empty ones dropped; each is searched first in the subject, else in the body (its first occurrence in "the shown subject/body" read as one message, subject first); an occurrence overlapping an already-marked one is skipped (no later occurrence is tried). Segments render via `|plain` inside `<mark>`.
- Approve control: disabled `<button type="button" class="queue-approve" disabled>` with notices, in order: fail notice or "Grounding check pending", then "This Draft can't be approved yet" when `shown_hash` is empty, then the card's `blocked` footer notice when set. Nothing renders for pass + hash + complete footer.
- Template: badge row `p.queue-badges` (grounding badge classes `queue-badge grounding-badge grounding-<status>`, then `badge-generic`, `badge-stale`); hidden `rev`/`shown_hash` inputs (no form); Evidence/facts sections; earlier Steps in native `<details>`; pages checked and every source URL via `safe_link`. Times (retrieved, sent) in `sender_timezone` (UTC fallback).
- `app.css`: `.queue-card.grounding-fail` red left border, red `fail` badge, tinted `<mark>`; appended with CRLF, byte-preserving.
- `test_due_queue.py`: three assertions updated for `?draft=` links and the now-present disabled button (5.1 asserted no button at all).
- No migration, no dependency, no new source file (so no `test_source_tree` FILES change).
- Review patches (pass 1):
  - `queue_page._approval_block` takes `card.blocked`: a blocked card (incomplete footer) has `approve_disabled` and its "Sending blocked: footer incomplete (...)" text as the last notice.
  - `queries.approval.has_earlier_steps(card)` (kind `step`, `step_no > FIRST_STEP`, Enrollment set) is the one predicate; `_earlier_steps` and `queue_page.card_row`'s `has_earlier_steps` both use it.
  - `queries.approval._grounding_status`: the dead `result.rev != draft.current_rev` check removed.
  - `queue_page.Badge.key` renamed `classes` (template updated).
  - `queue.html`: the disabled Approve button has `aria-describedby="queue-approve-notices"`, the id of a `<div>` wrapping its notices.
  - `app.css`: the failing `<mark>` background is `var(--band)` instead of `#f6d3d0` (single CRLF line edited in place).
  - `queries.sending` module docstring lists `sent_of_enrollment`; `README.md` Story 5.3 says which reads cover the whole queue (grounding status, Enrollment inbox) and which only the focused card, and "a red left border".
  - Tests: `test_card_details.py` adds a pass card with an incomplete footer (disabled button, notice, `aria-describedby`), a revision 2 (`regenerated`) appended after a revision-1 `pass` (pending row or none: `pending`, disabled, rev 2's `rev`/subject/`shown_hash`), an undecodable fact (empty wording, raw SQL; "Fact not found: broken_fact v1"), and earlier Steps sent out of order (Step 2 before Step 1, Step 4 sent while Step 3 is the card: listed Step 1, Step 2). `test_due_queue.py` asserts exactly one disabled `queue-approve` button instead of a vacuous `all(...)`.

## Spec Change Log

## Review Triage Log

Pass 1 (blind hunter BH, edge-case hunter EC, verification gap VG): 22 raw, 17 after dedup; 10 patched, 1 deferred, 6 rejected.

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | BH1+EC1: a footer-blocked card (`card.blocked`) that passed grounding gets no disabled Approve and no notice | medium | `_approval_block(grounding, shown_hash)` ignores `card.blocked`; `_card` still builds `message`/`shown_hash`; 5.4 would offer approval of an unsendable message | patch |
| 2 | BH2: `queries/sending.py` module docstring doesn't list `sent_of_enrollment` though its docstring points there | low | direct correction | patch |
| 3 | BH3+EC2: Earlier Steps omit OMs at `unknown`/`sending` that may have reached the prospect | medium | real, but the frozen AC says "whose OMs are `sent`"; nothing sends before Epic 6 | defer |
| 4 | BH4: per-card grounding + Enrollment reads; README "stays fast" overstates | low | the extra reads are by design (card fields); README wording is the defect | patch (wording) |
| 5 | BH5: `_grounding_status`'s `result.rev != current_rev` branch is dead; no test that revision 2 after a passed revision 1 reads `pending` and stays unapprovable | medium | `grounding_result(draft_id, rev)` fetches by rev; grep finds no such test | patch |
| 6 | BH6: only `fail` badge styled; hardcoded `#f6d3d0`; README says "outlined" but CSS draws a left border | low | colour token and README wording are direct corrections; styling other badges is cosmetic | patch (colour var + wording) |
| 7 | BH7: disabled Approve not linked to its notices for screen readers | low | direct: `aria-describedby` to the notices' ids | patch |
| 8 | BH8+EC3: `has_earlier_steps` drops the `enrollment_id` guard and uses literal 1 | low | a Step >1 Draft without an Enrollment would say "No earlier Step sent yet"; direct correction | patch |
| 9 | BH9: spans not found in the message are silently unmarked | low | the flag list still shows every span and explanation; tolerant matching adds complexity | reject |
| 10 | BH10a+VG2: `InvalidCompanyFacts` fallback in `_fact_row` untested | low | grep finds no test outside the facts domain/sync tests | patch |
| 11 | BH10b: untested recontact binding, missing Enrollment, unknown Evidence type, held-back `?draft=` | low | Step kinds without an active Enrollment are never candidates; `type` has a CHECK; recontact hashes through the same code as step | reject |
| 12 | BH11+VG-other: `test_due_queue` assertion now passes with zero buttons | low | `all(...)` over an empty list is true | patch |
| 13 | BH12: `Badge.key` holds CSS classes; comment about a card without details | low | rename is a direct correction; a queued card always has a revision, so the no-details path isn't reached from the page | patch (rename only) |
| 14 | VG1: "retracted wins over superseded" untested | false | `ck_evidence_not_superseded_and_retracted` (`superseded_by IS NULL OR retracted_at IS NULL`) forbids a row with both | reject |
| 15 | VG3: earlier-Step ordering and `< step_no` filter not exercised | low | single sent row in the test; adding a second out-of-order sent Step is a direct test addition | patch |
| 16 | EC4: cited Evidence on another Lead after a merge shows "Evidence not found" | maybe-false | would need to check whether `LeadsMerged` reparents `evidence.lead_id` with the Enrollment's `lead_id`; low even if true | reject |
| 17 | EC5: AC "every rendered link" vs. internal Previous/Next/nav links without `rel` | low | the AC is about source URLs (`safe_link`); internal same-origin links need no `rel` | reject |

## Verification

**Commands:** (from `nextron-engine/`, `-n 2` max, `--basetemp="e:\Nextron AI outreach\_handoff\scratch\track-E\pt"`)
- `uv run pytest -q tests/app/test_card_details.py tests/app/test_due_queue.py tests/app/test_outbound_messages.py` -- pass
- `uv run pytest -q tests/contracts` -- pass
- `uv run lint-imports` -- all contracts kept
