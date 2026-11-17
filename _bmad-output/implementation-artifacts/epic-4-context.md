# Epic 4 Context: Grounded AI drafts in Furqaan's voice

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

For Leads the Operator picks by hand within the day's inbox capacity, the engine enrolls the Primary Contact in a Segment × Market Campaign, binds one Sending Inbox for the whole Sequence, and writes a four-Step email Sequence in Furqaan's voice under the versioned Sales Playbook. Every claim cites stored Evidence or an Approved Company Fact. Every revision passes a fail-closed grounding check (deterministic rules plus an AI check). The AI gateway is cost-capped, batches bulk work, and serves only route tuples that passed their AI Test Set. This is Slice 1's brand-risk core: it produces the `ready` Drafts that Epic 5 approves and Epic 6 sends.

## Stories

- Story 4.1: Seed the default Sequence and Step briefs
- Story 4.2: Configure Campaigns, Offers and Variants with pause, resume and the UK LIA
- Story 4.3: Maintain Approved Company Facts with versions
- Story 4.4: Snapshot the Sales Playbook and pin it in route tuples
- Story 4.5: Gate AI route activation on test runs
- Story 4.6: Build the AI Test Sets
- Story 4.7: Register the Sending Inboxes that Sequences bind to
- Story 4.8: Enroll a Contact in a Campaign
- Story 4.9: Create a Sequence's Draft slots, due times and lifecycle commands
- Story 4.10: Show today's capacity and pick Leads to draft
- Story 4.11: Fill in Furqaan's signature and the compliance footer
- Story 4.12: Store Draft text as immutable revisions bound to a hash
- Story 4.13: Check every Draft against deterministic grounding rules
- Story 4.14: Run the AI grounding check and fail closed
- Story 4.15: Draft Step 1 with Opus from Evidence and facts
- Story 4.16: Draft Signal Inbox Leads within 15 minutes
- Story 4.17: Send bulk AI work through Message Batches
- Story 4.18: Draft follow-up Steps in the afternoon batch with a 07:00 deadline
- Story 4.19: Use Segment fallback copy for hand-picked Tier C Leads
- Story 4.20: Regenerate a Draft and flag stale ones
- Story 4.21: Spike: size AI cost and effort per route against the $20 budget (Slice 2)

## Requirements & Constraints

- **Precedence:** law and platform rules, then the PRD Step brief, then the Playbook. The PRD governs where it and the Playbook differ. For example, Step 1 asks only about relevance ("Worth a look?"), never for a meeting.
- **Default Sequence:** 4 Steps with delays of 0, 3, 7 and 14 days. A due time on a weekend moves to the next weekday in the recipient's timezone.
  - Step 1: under 80 words. It cites exactly one Signal (the strongest live one), has one call to action and no link outside the footer. Its subject is under 7 words, isn't misleading, and never starts with "Re:" or "Fwd:".
  - Steps 2 and 3: at most 70 words. Step 4: at most 50.
  - Each follow-up uses a new angle or fact. For Law-PrivateAI, Step 2 uses the Market's regulatory fact.
  - No Step names a price or proposes the audit.
- **Claims:**
  - Lead claims map to Evidence. Nextron, case-study, regulation and statistic claims map to an Approved Company Fact allowed for the Draft's Segment and Market.
  - ABA 512 is cited only in US Drafts, and the SRA notice only in UK Drafts.
  - The Legal AI work is never described as "for a law firm", and Wish Wheels is never called a distributor.
  - Claims based on Absence Evidence are hedged.
  - Every URL, email address and phone number equals a Settings value.
- **Footer:**
  - It holds the signature, legal name, postal address and opt-out line.
  - US emails add the solicitation label when it's set.
  - UK emails add a privacy line naming the Provenance source and linking to the privacy notice.
  - No edit or setting can remove it.
  - An incomplete footer blocks sending, not drafting.
  - Messages are plain text only, with no tracking.
- **Campaigns:**
  - Status is only ever `draft`, `active` or `paused`.
  - A UK Campaign needs an LIA reference and a date that isn't in the future before it can be activated.
  - At most 2 Variants, assigned round-robin so they get even counts.
  - A paused Campaign enrolls nothing and adds nothing to the queue.
- **Capacity:**
  - New-Sequence capacity = min(total cold capacity − follow-ups due, floor(total ÷ Steps)), never negative.
  - Cold caps are 0 until warm-up is recorded.
  - Unused capacity is recorded as `signal_shortfall` and is never filled automatically.
  - A hand-picked Tier C Lead gets the Segment's fallback template, flagged `generic`.
- **Speed:** a Signal Inbox Lead reaches a grounded Step 1 within 15 minutes.
- **Budget:**
  - $20 a month.
  - `draft_sequence` and `grounding_check` are `essential` and never blocked. Only blockable routes call `domain.budget.allow`.
  - Every call writes an `ai_usage` row.
- **Traceability:**
  - Each revision stores its Evidence ids, `(fact_id, version)` pairs, cited Signal, route tuple, Playbook SHA and version, and prompt version.
  - Revision 1 keeps the AI's original text.
  - Regenerates are countable per Draft.
- **Inboxes:** never `nextronai.com` or a role address. Outside `prod`, only allowlisted addresses.
- **Test gate pass bars:**

  | Set | Minimum size | Pass bar |
  |---|---|---|
  | Grounding | 30 | 100% of banned patterns; at least 95% of planted claims |
  | Drafting smoke | 10 Leads | zero flags |
  | Extraction | — | 100% of quotes pass the substring check; at least 80% Signal recall |

  All cases are synthetic and include the AD-18 injection fixtures.

## Technical Decisions

- **Ownership (AD-3).** Other owners' data is read only through `queries.<owner>` and changed only through `app.<owner>.api`.

  | Owner | Tables |
  |---|---|
  | `setup` | sequences, `step_briefs`, offers, campaigns, variants, `fallback_templates`, settings; publishes `FooterChanged` |
  | `playbook` | `company_facts`, `playbook_snapshots`, `prompt_snapshots`, `ai_route_activations`, `ai_test_cases`, `ai_test_runs` |
  | `sending` | minimal `sending_domains` and `sending_inboxes` |
  | `sequencing` | `enrollments`, `variant_counters` |
  | `drafting` | the `drafts` content group, `draft_revisions`, `grounding_results`, `footer_snapshots`, its `ai_batches` rows |
  | `approval` | the `drafts` status group; this epic adds only `cancel_pending` |
  | `scoring` | `daily_capacity` |

- **Notifications,** registered in `main.py` in the AD-3 order:
  - `Enrolled` → `pipeline.advance_to_at_least(queued)`
  - `MessageSent` → `sequencing.on_step_sent`
  - `SubjectSuppressed` → `sequencing.stop`, then `approval.cancel_pending`
  - `EvidenceChanged` → `drafting.flag_stale`
  - `FooterChanged` → `drafting.recompose_ready` (`ready` Drafts only)
- **State machines.** Campaign, SendingInbox, Enrollment and Draft are closed machines in `nextron/domain/`. Each transition appends an `events` row.
- **Single definitions.** Never duplicate these:
  - `domain.sending_policy`: `step_due_at`, `capacity_forecast`, `pick_inbox`
  - `domain.render`: `footer`, `footer_complete`, `compose`, `text_hash`
  - `domain.grounding_rules.check`
  - `domain.grounding.is_stale`
  - `domain.scoring.tier`
  - `domain.signals`, which picks the strongest live Event

  `drafts.due_at` comes only from `step_due_at`. Step N > 1 gets a due time only once Step N−1 is actually sent.
- **Enrollment.** `sequencing.enroll` is the only creator.
  - It runs in one transaction and checks Eligibility and AD-16 live.
  - At most one `active` or `paused` Enrollment per Lead, enforced by a partial unique index.
  - The bound inbox never changes.
  - It records the eligibility basis (with the UK LIA) and calls `drafting.request_steps`.
- **Contactability before AI.** Drafting calls `assert_contactable(contact, "email", "cold")` before any AI call. The AD-16 contract test gains scenarios for `enroll` and drafting.
- **Gateway (AD-8).**
  - Only `nextron/adapters/ai` imports `anthropic`.
  - Services pass the active tuple (route, model, effort, prompt SHA, Playbook SHA, schema version) from `queries.playbook.active_tuple`.
  - `draft_sequence` runs on `claude-opus-5-5`. `grounding_check` runs on `claude-haiku-5-5`, and on a refusal, invalid output or truncation it re-runs once on a tested Opus fallback tuple.
  - Direct Opus calls carry server-side `fallbacks` (beta `server-side-fallback-2026-07-01`). Batch requests never do.
  - On `max_tokens`, retry once with double `max_tokens`, up to the route cap.
  - The schema goes in `output_config.format`. Enums are lowercase `snake_case`. No property is named like `reasoning`; use `explanation`, at most 200 characters.
- **Prompts.**
  - The cached prefix holds the instructions, the tuple's Playbook snapshot (never the live file) and the allowed facts.
  - The user turn holds one Lead only, in escaped `<untrusted source id>` blocks. An Operator instruction follows as an `<operator_instruction>` block and can't override the brief or the compliance rules.
  - Prompts contain no email addresses or phone numbers.
  - Prompt files are immutable once activated.
- **Fail closed.**
  - Code validates every proposal: schema, citations inside the input set, a live `cited_signal_id`, and non-empty text. Invalid proposals are discarded.
  - Rule breaks are flagged, not discarded.
  - The deterministic rules and the AI check both run. If either flags anything, or the checker fails, the result is `fail`.
- **Revisions and jobs.**
  - Revisions are immutable. Origins: `generated`, `regenerated`, `operator_edit`, `footer_change`.
  - The grounding job is enqueued in the revision's own transaction.
  - Urgency: `soon` for Step 1; `bulk` for Steps 2–4, as a Message Batch item; `interactive` for a regenerate or an edit recheck.
  - A batch result is applied only if its target still needs it, its fingerprint matches and the subject is contactable. Otherwise the item is `stale`.
  - Job payloads hold ULIDs and scalars only.
- **Config.**
  - Settings are declared only in `settings_schema`.
  - Seeds load through idempotent `nextron seed sync` (actor `seed`). An invalid file fails, naming the file and key, and loads nothing.

## UX & Interaction Patterns

- Pages are server-rendered with HTMX, and every control is a POST with CSRF.
- AI and crawled text is autoescaped in `hx-disable` containers. Pages with personal data send `Cache-Control: no-store`.
- Screens this epic touches:
  - Settings: Campaign controls and the LIA, footer fields
  - Approved Company Facts panel, with version history
  - Sending Inboxes panel
  - AI content view: active and candidate tuples, test-run metrics, failing cases, rollback, "ungated" marks
  - "Ready to draft" list, under "Remaining today: n of N"
  - Lead page Drafts section: composed message, revisions, grounding, generic and stale badges, Regenerate with an instruction of at most 200 characters
  - Notification area: signal shortfall, and Signal Inbox Leads that weren't drafted
  - Signal Inbox "Draft Step 1 when ready" checkbox, on by default

## Cross-Story Dependencies

- **Upstream:**
  - Epic 1: UoW, jobs and triggers, settings, budget classes, `ai_usage` metering, health panel, doctor.
  - Epic 2: Haiku-only gateway and `LlmPort.run`; `prompt_snapshots` and `ai_route_activations` with three bootstrap extraction tuples; Evidence, Signals and Tier; the Pipeline machine; the Signal Inbox chain; extraction fixtures.
  - Epic 3: `assert_contactable`, live Eligibility, `SubjectSuppressed`, the AD-16 contract test.
- **Within the epic,** in order:
  - 4.1 → 4.2
  - 4.3 + 4.4 → 4.5 → 4.6. Until 4.6 fills the sets, they are "too small".
  - 4.7 → 4.8 → 4.9 → 4.10
  - 4.11 → 4.12 → 4.13 → 4.14 → 4.15 → 4.16
  - 4.17 → 4.18
  - 4.19 and 4.20 extend drafting.
  - 4.21 waits for 2 weeks of `ai_usage` data or the first 80% budget alert. Its tuples go live only through the 4.5 gate.
- **Downstream:**
  - Epic 5 approves against `text_hash` and a grounding `pass`.
  - Epic 6 alters the inbox tables and replaces the placeholder `queries.sending.cold_daily_caps`.
  - Epic 7 uses `pause`, `resume`, `delay_until` and `stop`.
  - Epic 8 reuses the capacity formula and the "Ready to draft" ordering.
  - Epic 11 adds the editing screens.
