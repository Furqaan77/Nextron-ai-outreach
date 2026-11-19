# Epic 11 Context: Funnel insight and self-service settings

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Give Mac (the Operator) the funnel from discovery to won deals, with honest rates per contacted Contact, breakdowns, supporting measures, decision checkpoints and approval metrics. Also let him edit Segments, Markets, Sequences/Step briefs and Campaigns/Variants in the dashboard instead of seed YAML, while the compliance rules stay locked in code. This is Slice 3 work. It builds on facts recorded since Slice 1 and on the seed-loaded configuration from Epics 2–4. Once an editing screen ships, the DB alone owns that configuration.

## Stories

- Story 11.1: See funnel counts and rates per contacted Contact
- Story 11.2: Break the funnel down and guard Variant comparisons
- Story 11.3: Show the supporting measures, counter-metrics and checkpoints
- Story 11.4: Show approval metrics
- Story 11.5: Edit Segments in the dashboard
- Story 11.6: Edit Markets with the compliance rules locked
- Story 11.7: Edit Sequences and Step briefs
- Story 11.8: Create and edit Campaigns and Variants

## Requirements & Constraints

**Segment editing (FR-1).**
- The Operator can create and edit Segments, covering:
  - industry keywords;
  - size range and unit;
  - ordered decision-maker roles, plus the roles preferred for named Events;
  - disqualifiers, chosen from the code-defined rule kinds with their parameters;
  - weighted Signal types: key, name, Event or Attribute, Signal Tier, Evidence type, allowed sources, score-only flag, weight and match terms;
  - discovery queries: Places query texts, Companies House keywords and SIC codes, aggregator job titles.
- A Segment change applies only to Leads discovered afterwards and to Operator-triggered re-scoring. Existing Leads are never re-scored silently.
- Validation is the Epic 2 seed validator, reused unchanged. It refuses a save and names the failing field when:
  - a Tier 1 weight is not above every Tier 2 weight, or a Tier 2 weight is not above every Tier 3 weight;
  - an Event allows Evidence other than Quote;
  - a non-Attribute is score-only;
  - a key repeats;
  - a Signal type represents ABA Formal Opinion 512 or the SRA notice.
- Editing adds one more refusal: "A Segment needs a decision-maker role and at least one Tier 1 or 2 Event".
- Disqualifiers run before scoring. A Lead with unknown size is never disqualified.

**Extensibility (NFR-40).**
- New Segments and simple Markets need no code change.
- A new Segment is inert until a Campaign uses it.

**Compliance lock (NFR-7).**
- Suppression, Eligibility, footer and opt-out handling, and Market rules can't be turned off from the dashboard. No setting or POST route may enable, disable or edit them; existing tests already assert this.
- On Markets, `cold_email_allowed`, `eligible_legal_forms`, `free_webmail_blocked` and `privacy_line_required` are read-only ("Set in code (compliance)"), and a forged POST is refused with no change.
- A Market outside the US and UK can't enable cold email (NFR-5).
- A footer template must keep the required placeholders (NFR-2).

**Analytics (FR-54, FR-55, NFR-41, NFR-46..50).**
- Rates are per contacted Contact, never per email. A Contact counts at most once per rate.
- Opt-outs are excluded from the reply rate. The raw rate appears only under "Don't optimize".
- A Variant comparison shows "not enough data (n of 150)" until each Variant has 150 or more contacted Contacts.
- Checkpoints are reportable (NFR-49).
- An unfiltered 12-month funnel renders in under 2 s.
- CSV exports carry aggregates only and are formula-injection-escaped.

## Technical Decisions

**Ownership and units of work (AD-3).**
- `app.setup` owns `segments`, `segment_versions`, `markets`, `offers`, `campaigns`, `variants`, `sequences`, `sequence_versions`, `step_briefs`, `fallback_templates` and `settings`. Only `setup` writes them, through `db/repos/setup/`.
- Other services read through `nextron.app.queries.setup`.
- `scoring` owns the Lead score group, including the Segment version used. `sequencing` owns `enrollments`, and 11.7 adds `sequence_version_id` there.
- Each web request opens one unit of work and calls one use case.
- Notifications form a closed list. `FooterChanged` (publisher `setup`, subscriber `drafting.recompose_ready`) is the only setup notification.
- Every new column is mapped in `owners.py` and classified in `pii_registry.py`.

**Events and status (AD-4).**
- Every configuration save appends an `events` row with actor `operator`. Seed writes use actor `seed`.
- `events` is append-only, holds no personal data and never triggers behaviour.
- Campaign status ∈ {`draft`, `active`, `paused`}. A UK Campaign activates only with an LIA reference and date.
- Analytics (`app.analytics`) reads only `events` plus the AD-4 fact columns, through `queries.*`, and writes nothing. The fact columns are:
  - the OM enqueue snapshot: tier, lead_score, variant_id, segment_id, market_id, inbox_id, step_no and kind;
  - `replies.om_id`, `replies.step_no` and `replies.hot`;
  - `daily_capacity`, `crawl_runs` and `drafts.generic`;
  - `approvals.review_ms` and `approvals.edit_ratio`, plus `approval_sessions`;
  - `call_outcomes` and `ai_usage`.

**Segment versioning (11.5).**
- The migration `<NNNN>_11-5-segment-editing` creates `segment_versions` (owner `setup`). Each row holds one version's full definition and is immutable.
- The migration backfills `segment_versions` from the current `segments` definitions.
- A save inserts version n+1 and moves `segments.version` to it, in the same transaction as its `events` row.
- Earlier versions stay viewable, with what changed.
- `score_lead` re-scores an existing Lead with that Lead's stored Segment version, read from `segment_versions`. New Leads get the current version.
- "Re-score with the current definition" (per Lead, or for a whole Segment) is the only way to move a Lead to the current version. It enqueues `score_lead` for each Lead.
- Watch for a tension: Epic 2's plain "Re-score" applies the current version. Reconcile the two without silently re-scoring.

**Seed sync coexistence (AD-12).**
- `nextron seed sync` stays idempotent, upserting by natural key with actor `seed`, for whatever the DB doesn't yet own.
- Once an editing screen ships, sync stops writing that entity. It reports "Segments are managed in the dashboard; seed/segments.yaml ignored" and lists any differences between the YAML and the DB. 11.6, 11.7 and 11.8 report the same way for Markets, Sequences and Campaigns.
- A sync never re-scores Leads.
- Settings keys are declared only in `domain/settings_schema.py`. Don't invent configuration keys.

**Dashboard hardening (AD-13).**
- Every state change is a POST with a synchronizer CSRF token, plus the `Origin` and `Sec-Fetch-Site` checks. No GET has a side effect.
- Autoescaping stays on, with no `|safe` on untrusted text.
- Pages that show personal data send `Cache-Control: no-store`.
- Handlers are sync `def`. HTMX runs with its locked configuration.

**Other migrations.**
- 11.7: `<NNNN>_11-7-sequence-editing` creates `sequence_versions` (immutable, backfilled). Existing Enrollments keep their version.
- 11.6: a footer edit publishes `FooterChanged`. Eligibility is evaluated live (AD-16), and the Send Window comes only from `domain.sending_policy` (AD-11).

## UX & Interaction Patterns

- The editors are sections of the Settings panel (UX-DR23): typed, server-validated forms with unit hints. Refusals name the failing field and change nothing.
- Every control is a form POST or `hx-post` carrying the CSRF token (UX-DR3). A CSRF failure renders an error without changing state.
- The version history view shows each earlier version and its diff.
- Read-only compliance fields show "Set in code (compliance)".
- Funnel and approval views (UX-DR31, UX-DR32) group counter-metrics under "Don't optimize", with no targets or up arrows.

## Cross-Story Dependencies

- **11.5 depends on Epic 2:**
  - the `segments` table, the seed loader and its validator (Story 2.1);
  - `score_lead` / `scoring.rescore`, which stores the Segment version used (Story 2.15);
  - the Lead page's Re-score control.
- Epic 8 discovery (Places, Companies House, aggregator) consumes the discovery queries.
- Campaigns (11.8) make a Segment live. A new Segment does nothing until then.
- 11.6 depends on Epic 3 Market rules and Eligibility, and on Epic 4 `drafting.recompose_ready`.
- 11.7 depends on Epic 4 Sequences and capacity, on `domain.grounding_rules`, and on Epic 6 `sending_policy`.
- 11.8 depends on Epic 4 Campaigns and Variants, `variant_counters` and the UK LIA rule.
- 11.1–11.4 depend on the Slice 1 fact columns and on Epic 9 `call_outcomes`. 11.2 and 11.3 extend 11.1's page.
