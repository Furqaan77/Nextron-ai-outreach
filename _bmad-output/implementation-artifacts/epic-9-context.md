# Epic 9 Context: Discovery calls and pipeline

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Turn engaged Leads into discovery calls and track them through the commercial pipeline. Mac (Operator) moves Leads through the later Pipeline Stages by hand, with a deal value and notes. Each Hot Lead gets a grounded Call Prep Sheet, which is emailed internally to Furqaan (Sender) when a call is booked. Mac records how each call went, and a no-show triggers an approved rebooking email. After the first 10 held calls, a report checks whether the Offer's audit price and scope work. In Slice 3 a full Pipeline view and a per-Lead history timeline complete the epic. Stories 9.1–9.6 are Slice 2, and FR-68/FR-69 must ship before the first booked call is likely, about 2 weeks after the first send. Stories 9.7–9.8 are Slice 3.

## Stories

- Story 9.1: Move Leads through the later Pipeline Stages by hand, with deal value and notes
- Story 9.2: Prepare a grounded Call Prep Sheet for each Hot Lead
- Story 9.3: Email the Call Prep Sheet to Furqaan when a call is booked
- Story 9.4: Prompt for and record each Call Outcome
- Story 9.5: Draft a rebooking email after a no-show
- Story 9.6: Report Offer validation after the first 10 held calls
- Story 9.7: View the pipeline by stage with filters
- Story 9.8: Show each Lead's complete history

## Requirements & Constraints

- **Pipeline Stages, in order:** `new → queued → contacted → replied → interested → call_booked → call_held → audit_proposed → audit_won → build_proposed → won`. There is also the parked stage `not_now` (with a re-contact date) and the closed stages `lost` and `disqualified`, which the Operator can reopen.
  - These stages move only automatically: `new`, `queued`, `contacted`, `replied`, `interested` and `not_now`.
  - `call_held` (or a return to `call_booked`) is set only by a Call Outcome.
  - All other moves are made by hand by the Operator.
- **Call Prep Sheet content:**
  - context: Lead summary, Signals cited, every message exchanged;
  - 3 current-state, 2 gap-and-impact and 2 constraint questions suited to the Segment and Event;
  - likely objections, each with its Playbook §5 approach;
  - Approved Company Facts to mention;
  - the next step: the audit, its price, and its credit toward a build;
  - the line "Don't pitch features before the problem is quantified."

  It uses only Evidence and Approved Company Facts, and it must pass grounding. It shows on the Lead page and is emailed to the Sender's main nextronai.com inbox when the Lead enters `call_booked`.
- **Call Outcome:**
  - The dashboard prompts for it once the call's scheduled time plus a delay (default 60 minutes) has passed.
  - It records held or no-show; qualified or not, with a reason; the next step and its date; whether the audit was proposed and at what price; the objections raised (Playbook §5 types); and the price reaction (`accepted`, `pushed_back`, `not_discussed`).
  - The stage updates from the record.
  - A no-show leads to a rebooking draft offering two new slots.
- **Offer validation:**
  - After 10 held calls, the report shows audits proposed and accepted, price reactions and the top objections.
  - Fewer than 2 audits accepted shows the checkpoint "Review the Offer's price and scope before the next Variant".
  - Only engine-sourced Leads are counted.
- **Money and text:**
  - Deal values and prices are entered in whole US dollars and stored as integer micro-dollars.
  - Notes are plain text, 5,000 characters at most.
  - Event rows never hold amounts, note text or field values.
- **Pipeline view:**
  - Leads are grouped by stage, with each group's count and total deal value.
  - Filters: Campaign, Segment, Market and the date the Lead entered its current stage. The filter state lives in the URL.
  - 50 Leads per group page.
  - Renders in under 1 s with 5,000 Leads.
  - Merged Leads are excluded.
- **Lead history:** shown newest first, with type filters and 100 entries per page. An erased Contact's text, names and addresses show as "[erased]".
- **Compliance:**
  - Reopening a Lead never voids a suppression entry.
  - Every outbound email still needs human approval.
  - The Call Prep email never goes through a Sending Inbox or Telegram.

## Technical Decisions

- **Ownership (single owner per table):**
  - `pipeline` owns the `leads` pipeline group (`pipeline_stage`, `recontact_on`, `deal_value`, `notes`) and `call_outcomes`. Its commands are `advance_to_at_least`, `park_not_now`, `close`, `cancel_tasks` and `record_call_outcome`.
  - `drafting` owns `call_preps`, with `request_call_prep` and `request_rebook`.
  - `notify` owns `alerts`.
  - Other services read through `nextron.app.queries.*` and write only through `<owner>.api`.
  - Each entry point runs one unit of work, and cross-owner effects happen in the same transaction.
- **App layer order:** `replies | discovery | scoring | ops | analytics` > `pipeline` > `sequencing` > `approval` > `drafting | sending | research` > `compliance | playbook | setup` > `notify` > `jobqueue` > `queries`. `pipeline` may call `drafting` and `notify`, but not the reverse.
- **State machine (AD-4):**
  - The machine and its transition table live in `nextron/domain/`.
  - Every status write appends an `events` row in the same transaction, with `entity`, `from`/`to`, `cause`, `cause_ref` (a ULID), `actor` and `at`. Events never hold personal data.
  - Automatic moves use `advance_to_at_least(stage)`, which is a no-op at or past the stage.
  - `close(lost|disqualified)` stops `active` and `paused` Enrollments with cause `lead_closed` and cancels pending Drafts.
- **Migrations:**
  - New tables: `call_outcomes` (9.1, extended in 9.4) and `call_preps` (9.2).
  - Every column is mapped in `owners.py` and classified in `pii_registry.py`. Call Prep content is `redact`, so erasure replaces it with "[erased]".
  - Earlier `call_preps` revisions are kept.
- **AI gateway:**
  - The `call_prep` route uses `claude-opus-5-5` with budget class `essential`. It is not batchable, is never budget-blocked and runs on the `interactive` lane.
  - Its prompt is at `nextron/adapters/ai/prompts/call_prep/v1.md`.
  - The route serves only after the test gate activates a tuple with a passing smoke run. The drafting smoke set needs at least 3 Call Prep fixture Leads.
  - Untrusted text (Evidence, Replies) goes in `<untrusted>` blocks.
  - Code validates the output schema and builds the context section and the fixed line itself. A timeout, refusal or schema error counts as a grounding `fail`.
- **Grounding:** Call Prep Sheets are grounded the same way as Drafts. `domain.grounding_rules` runs first, then the `grounding_check` route, against the sheet's own Evidence ids and fact versions. A price that differs from the Offer fact's wording fails.
- **Alerts (AD-15):**
  - The Call Prep email is sent as `notify.alert(kind=call_prep, subject=<call_outcomes ulid>, channel=internal_email)`, through the `internal_email` adapter under the `internal_notify` grant, and only to the `internal_recipients` setting.
  - `alerts` is unique on kind, subject and channel. Its statuses are `pending → sent | unknown | failed`. An `unknown` email is never resent automatically.
  - Outside prod, alerts go only to the sink adapter's allowlist (AD-14).
- **Contactability (AD-16):**
  - `drafting` calls `assert_contactable` before any Call Prep Sheet or rebook Draft, and `pipeline` calls it before delivering a call sheet.
  - A `Blocked` result creates nothing and shows the reason.
  - Each new path adds a scenario to the AD-16 caller contract.
- **Rebook Draft:**
  - It uses the `draft_reply` route with a `rebook` brief, and `domain.slots.propose` from Epic 7 supplies the slots.
  - It has kind `rebook` and is due at once.
  - It is sent as a responsive OM in the Contact's latest Thread (AD-22). It skips the cold gate rows but keeps `contactable`, `approval_live`, `hash_matches`, `inbox_ready`, `footer_complete` and `market_eligible`.
- **Erasure (AD-17):** the nextronai.com inbox is listed on erasure receipts as a residual store for Call Prep emails.
- **Analytics:** `app.analytics` only reads `call_outcomes`, stage `events` rows and the AD-4 fact columns, and writes nothing.

## UX & Interaction Patterns

- **Dashboard hardening (AD-13):** every state change is a POST with a CSRF token. Reply, crawled, note and AI text renders autoescaped in `hx-disable` containers, with no `|safe`. Pages with personal data send `Cache-Control: no-store`.
- **Stage control:** it offers only the legal Operator transitions. A forged POST for an automatic stage returns "This stage is set automatically" and changes nothing.
- **Call booking:** the form requires the Contact, date, time and timezone (defaulting to the Contact's `tz`). "Rebook" supersedes the pending booking.
- **Call Outcome:**
  - "Call Outcome due: <Lead name>" is worked out from the table at render time; no job or stored flag is used.
  - Form errors show next to the field, and nothing is stored on an error.
- **Lead page:**
  - It shows the latest Call Prep Sheet, its grounding result (a red banner listing failing claims), its creation time and a "Regenerate Call Prep" button.
  - It shows the deal value and notes.
  - Slice 3 adds a History tab.
- **Rebook card:** it appears in the Approval Queue's Reply Draft group and shows the missed call time.
- **Pipeline routes:** a new `GET /leads/<word>` route is registered before `/leads/{lead_id}`.

## Cross-Story Dependencies

- **9.1 comes first:** it creates `call_outcomes` and the manual-stage controls that 9.3–9.6 rely on.
- **9.2** needs Epic 4's test gate, grounding and Playbook/Facts; it is triggered by `interested` (Epic 7) and by 9.1's `call_booked`.
- **9.3** needs 9.1 (the booking) and 9.2 (a passing, current sheet), plus Epic 7's `notify` and `internal_email` alerts.
- **9.4** extends 9.1's table. **9.5** depends on 9.4's no-show plus Epic 7's slots, Reply Drafts and Manual Reply handling, and Epic 6's sending gate.
- **9.6** needs 9.4's data and feeds Epic 11's Supporting measures panel.
- **9.7–9.8** read everything above, along with Epic 3 (suppression and erasure) and Epic 2 (Evidence and Contacts).
