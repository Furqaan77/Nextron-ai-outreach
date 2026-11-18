# Epic 8 Context: Automated Lead supply

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Slice 2 turns the engine from hand-picked supply into self-feeding supply. The engine discovers candidate Leads itself through Google Places (within the Maps storage terms), Companies House (UK) and an optional job aggregator, and every paid or quota-bound call stops before any paid use or budget overrun. Each morning it enrolls the best Eligible Leads by Tier and Event freshness. It re-checks Leads for new Events using immutable, superseding Evidence, re-contacts Not Now Leads on their chosen date, and restarts a finished Sequence only on a genuinely new Event. It also deletes Contacts' personal data after the retention period, while their suppression entries stay. The result is a queue that holds fresh, grounded reasons to write without Mac picking Leads by hand, with cost, platform terms and UK GDPR still respected.

## Stories

- Story 8.1: Keep supply jobs inside quotas and the AI budget
- Story 8.2: Places search adapter that keeps only what the Maps terms allow
- Story 8.3: Turn Places candidates into deduplicated, contactable Leads
- Story 8.4: Discover active UK companies through Companies House
- Story 8.5: Spike: choose a job-aggregator API and confirm its terms (OQ-7)
- Story 8.6: Discover hiring organizations through the chosen job aggregator, off by default
- Story 8.7: Re-check Leads on a schedule with immutable, superseding Evidence
- Story 8.8: Re-contact Not Now Leads on their chosen date
- Story 8.9: Select the day's new Sequences automatically by Tier and Event freshness
- Story 8.10: Top up discovery each weekday within quotas
- Story 8.11: Restart a completed Sequence only on a new Event at least 90 days later
- Story 8.12: Delete Contacts' personal data after the retention period

## Requirements & Constraints

- **Discovery sources, never scraping.** Only public APIs are used: Places, Companies House and at most one job aggregator. The FR-49 denylist (LinkedIn, lnkd.in, Instagram, Google Maps hosts and `/maps` paths, Indeed, Glassdoor, ZipRecruiter) is never fetched; a denylisted URL may be stored as text only.
- **Places terms.** Long term, only `place_id` is stored. Coordinates are kept for at most 30 days and purged daily. Places name, address, phone and reviews never reach the DB, `events`, job payloads, logs or prompts. The Lead's name and domain come from its own website (final URL → registrable domain).
- **Registry.** Inactive and dormant companies are discarded. Officers become Contacts only when they are active natural-person directors or LLP designated members, with a Provenance Record and no email address.
- **Aggregator.** Off by default. It can be enabled only after the OQ-7 decision record selects an API and the Operator records a terms-confirmed date. If no API is selected, ship only a disabled setting and an explanatory message.
- **Deduplication.** A match on web domain, company number or place ID never creates a second Lead. That holds for Disqualified, Lost, merged and suppressed Leads too.
- **Quotas and budget.** Hitting a quota or the AI budget stops only discovery, enrichment and re-check work, checked before each paid call. It never blocks sending, reply handling, unsubscribe polling, `daily_selection` or `recontact_due`. A quota of 0 is a hard stop, and only an Operator edit raises it. Paid Places use never starts automatically. Pro and Enterprise SKUs are metered separately. Total running cost stays under $100 a month, with an AI budget of $20.
- **Daily selection.** Tier A goes first, newest live Event first. Tier B follows, by Lead Score. Every Lead needs a score of at least `min_lead_score` (default 50), and Tier C is never selected. Selection fills the capacity left after follow-ups, capped at total capacity ÷ Steps. Weekends and away days enroll no one. Unused capacity shows as "signal shortfall" and is never filled with weaker Leads.
- **Re-check.** Eligible Leads outside an active Sequence get a full re-check every 30 days and a job-board re-check every 7 days. AI extraction runs only on changed pages. A new Event re-scores the Lead and may change its Tier.
- **Re-contact.** A Not Now Lead gets exactly one re-contact Draft on its chosen date, after Eligibility and suppression are re-checked live. A Sequence that ended without a Reply may restart only 90 or more days after its last Step, on an Event first seen after it ended, and the new Step 1 cites that Event. Not Interested and Unsubscribe are permanent on every channel.
- **Retention.** A Contact who was never emailed is erased 6 months (`P6M`) after their earliest Provenance retrieval. An emailed Contact with no engagement is erased 12 months (`P12M`) after their last Step. A Contact with a live Enrollment is never erased. Retention writes no new suppression entry and keeps existing ones. This must ship before the first deletion falls due, 6 months after the first discovery.
- **Compliance.** Every outbound email still needs human approval. Never send from nextronai.com, and never automate LinkedIn or Instagram.

## Technical Decisions

- **Budget classes.** Each job registration declares `budget_class` ∈ {`blockable`, `essential`}, default `essential`. A blockable job re-evaluates `domain.budget.allow` against a fresh `UsageMeterPort.snapshot` before each provider call. A refusal returns `deferred`, which re-queues the job an hour later without using a retry attempt and never makes it `dead`. Notifications go out once per threshold. Adapters never check the budget themselves; they surface a 429 as `AdapterError(retryable=True)` with a sanitized message.
- **Ports and adapters.** Use one port per external role, each with an in-memory fake in `tests/fakes/`. Each adapter is listed in import-linter contract 5, classified in `tests/live_guard.py` and registered in `PORT_FAKES`. Adapters use httpx2 with header auth and read credentials from keyring at call time. Every call is recorded through `UsageMeterPort`. In dev and test, the live API is never called.
- **Ownership.**
  - `discovery` owns the `leads` identity group (name, `web_domain`, `ch_number`, `place_id`, coordinates, Segment, Market, `merged_into`), `discovery_runs` and `ingest_candidate`.
  - `research` owns Evidence, Signals, Provenance, contacts, `job_postings`, `crawl_runs` and `refresh`.
  - `scoring` owns Tier, scores, `daily_capacity` and `select_daily`.
  - `sequencing` owns `enrollments`, and `enroll(contact, campaign, origin, steps)` is the only creator. Its `origin` ∈ {`selection`, `operator_pick`, `referral`, `recontact`, `restart`}.
  - `pipeline` owns `recontact_on`.
  - `compliance` owns `erase_contact`.
  - Cross-owner writes go only through `<owner>.api`.
- **Contactability gate.** `compliance.assert_contactable` runs before every Lead insert (`purpose=discovery`), every Contact creation and every enroll. Each new path gets a scenario in the AD-16 callers contract test.
- **Evidence is immutable.**
  - A changed observation inserts a new row and sets `superseded_by` on the old one.
  - A vanished quote sets `retracted_at`.
  - An unchanged page updates only `last_confirmed_at`.
  - Quote text, type and source are never updated.
  - `EvidenceChanged` flags `ready` Drafts `stale` through `drafting.flag_stale`. Only `EventClosed` pulls back an approved Draft.
  - Tier comes only from `domain.scoring.tier(events, now)`, and Event age only from `domain.signals.age_days`.
- **Jobs.** Keys follow `<type>:<scope>:<discriminator>`, for example `discover_places:<campaign>:<location hash|date>`, `recheck_lead:<lead>:<date>` and `daily_selection:global:<date>`. Payloads hold ULIDs and scalars only; Places candidate data never goes in a payload. Chained stages are enqueued in the same transaction. Daily triggers come only from the closed AD-10 list: `recheck_leads`, `recontact_due`, `apply_retention`, `purge_place_coords`, `purge_page_text` and `daily_selection` (first run after 05:00 Operator time). Supply work runs on the bulk lane.
- **Selection.** `enroll` is called per Lead, each in its own transaction block inside the one use case, so a refusal skips that Lead without rolling back the others. Capacity comes only from `domain.sending_policy.capacity_forecast`. The ordering function is shared with Epic 4's Ready-to-draft list.
- **Retention.** `domain.retention.due(facts, settings, now)` is pure and reads send and Reply history only through `queries.sending` and `queries.replies`. Each Contact is erased in its own unit of work with `erase_contact(cause="retention")` and `request_ref` `retention:<date>`. Settings are ISO 8601 durations declared in `settings_schema`.
- **Migrations** (one per story that needs one):
  - 8-2 adds `place_lat`, `place_lng` and `place_coords_at` to `leads`.
  - 8-3 adds `discovery_runs`.
  - 8-4 adds `website_lookup_at`.
  - 8-6 adds the aggregator source and `job_postings`.
  - 8-9 adds selection columns to `daily_capacity`.
  - 8-11 adds `enrollments.restart_event_id`.

  Every new column is mapped in `owners.py` and classified in `pii_registry.py`.
- **Grounding.** Add the `restart_event_not_cited` rule to `domain.grounding_rules`. A recontact Step brief is seeded in `seed/sequences.yaml`.

## UX & Interaction Patterns

- **Discovery page.** It offers per-source "Run … discovery" POST forms with CSRF. Places runs need an active Campaign and a location. Companies House runs are for UK Campaigns only. The page also shows:
  - recent runs with their source, Campaign, location, status and counts (seen, created, merged, suppressed, no own website, Market blocked, inactive);
  - "Pool sufficient: n Leads ready";
  - Leads created today against the target, and Places requests used against today's allowance;
  - "Job-aggregator discovery is off: …" when the aggregator is disabled.

  Pages that show personal data send `Cache-Control: no-store`.
- **Health panel.** It shows "Discovery stopped: <provider> quota reached" or "Discovery stopped: AI budget reached", plus the last successful run of each discovery, re-check, purge and retention job. The quota panel shows "non-essential work stopped".
- **Leads list.** A "website needed" filter lists Leads with no website, and the Operator can enter one by POST.
- **Lead page.** It shows superseded Evidence labeled "superseded" with a link to its replacement, the last re-check date, the re-contact date with the latest refusal reason, and "Erased by retention on <date>".
- **Queue.** Stale Drafts show a `stale` badge. A restart card shows "Restart: previous Sequence ended <date> without a Reply" with the earlier Steps and their dates. The Ready-to-draft page shows exclusion counts, and the notification area shows "signal shortfall: n".
- **Settings panel.** It shows the retention durations, the next date a deletion falls due, and how many Contacts fall due in the next 30 days.

## Cross-Story Dependencies

- **8.1 comes first.** It gates every blockable supply job in 8.2–8.4, 8.6, 8.7 and 8.10.
- **Places stories.** 8.2 (port and adapter) precedes 8.3 (ingest). 8.4 and 8.6 also use the gated Places lookup to find a website.
- **Aggregator stories.** 8.5's decision record decides whether 8.6 builds a port or ships only a disabled setting, and it may require an AD-7 `source=aggregator` amendment.
- **Selection and top-up.** 8.9 precedes 8.10 (the top-up chains off `daily_selection`) and 8.11 (restart candidates join the selection pool). 8.8 recontacts count against 8.9's `daily_capacity.picked`.
- **Re-check.** 8.7 reuses Epic 2's research chain (`enrich_lead`, `check_job_boards`, `score_lead`, `extract_signals`) and relies on Epic 4/5's `stale` refresh and `EventClosed` handling.
- **Earlier epics this epic builds on.**
  - Epic 1: the usage meter, quota settings, the health-issue registry and the job runner.
  - Epic 2: the `RegistryPort` legal-form lookup, hiring terms, scoring and tiers.
  - Epic 3: the Eligibility rule `recontact_not_allowed`, `assert_contactable`, and `erase_contact` with receipts (Story 3.14).
  - Epic 4: `enroll`, the capacity formula, the Ready-to-draft ordering and Step 1 drafting.
  - Epic 5: away-period rules for origins `recontact` and `restart`.
  - Epics 6 and 7: the send and Reply history and `park_not_now`, needed by 8.8, 8.11 and 8.12.
