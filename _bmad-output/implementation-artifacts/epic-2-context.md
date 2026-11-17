# Epic 2 Context: Researched Leads with evidence and a reachable decision-maker

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Turn hand-supplied candidates (CSV imports and pasted Signal Inbox items) into exactly one Lead per organization, researched from its own website, careers pages and ATS boards. Every Signal is backed by verifiable Evidence, and every Contact and address by a Provenance Record. The engine confirms a UK Lead's legal form, picks the decision-maker as Primary Contact, finds and validates their address with an Address Class, and computes Fit, Signal and Lead Scores and the Lead Tier, all shown on a Lead page. The epic also builds the core of the single Claude gateway: Haiku extraction routes, typed results and metered cost. Later epics extend it for drafting, grounding and reply classification. This is Slice 1 work, because a reason to write and a person to write to must exist before any Draft.

## Stories

- Story 2.1: Seed the Segments and their Signal lists with `nextron seed sync`
- Story 2.2: Keep exactly one Lead per organization
- Story 2.3: Import Leads from CSV with their origin recorded
- Story 2.4: Browse Leads and open a Lead page
- Story 2.5: Crawl a Lead's website politely inside the egress filter
- Story 2.6: Render near-empty pages and detect chat and booking widgets
- Story 2.7: Call Claude through one gateway with typed results and metered usage
- Story 2.8: Version extraction prompts and pin the active route tuple
- Story 2.9: Detect Signals with Evidence from the crawled pages
- Story 2.10: Find named decision-makers and pick the Primary Contact
- Story 2.11: Collect hiring Signals from careers pages and ATS boards
- Story 2.12: Confirm a UK Lead's legal form through Companies House
- Story 2.13: Find the Primary Contact's email address in the cheapest order
- Story 2.14: Validate each address and assign its Address Class
- Story 2.15: Score and tier every enriched Lead
- Story 2.16: Turn pasted signals into researched Leads through the Signal Inbox

## Requirements & Constraints

- **Deduplication.** There is one Lead per organization, matched by registrable web domain, Companies House number or place ID. The match applies at any stage, including Disqualified and suppressed Leads. When two Leads merge, the older one survives.
- **Provenance.** Every Contact and address records its source (page URL, API name, CSV origin or Signal Inbox), the retrieval date and the Address Class. A CSV import requires an origin of 3–200 characters and accepts at most 5 MB or 5,000 rows. CSV emails start as `guessed`.
- **Evidence.**
  - No Signal is ever stored without Evidence: a quote of 300 characters or fewer, an absence or a count.
  - Event age counts from the event date, or else from first seen.
  - Tier 1 Events keep full weight for 30 days and fall to zero at 90. Tier 2 Events keep full weight for 90 days, then expire. Attributes don't decay. A job-posting Event stays live only while its posting is open.
  - Seed weights: Tier 1 Event 60, Tier 2 Event 35, Tier 2 Attribute 30, Tier 3 Attribute 15.
- **Platform terms.**
  - The engine never fetches LinkedIn, `lnkd.in`, Instagram, Google Maps or job sites (Indeed, Glassdoor, ZipRecruiter). A denylisted URL pasted into the Signal Inbox is stored as a source only.
  - ATS boards are queried only by tokens found on the Lead's own site, and only on configured hosts (default Greenhouse, Lever and Ashby).
- **Polite crawling.** The crawler obeys robots.txt, waits at least 1 s between requests per domain and fetches at most 15 pages. Only pages with under 500 characters of visible text are rendered, with at most 3 renders per Lead and a 20 s timeout.
- **Email finding.**
  - The order is: published address, then a pattern, then Hunter Domain Search (once per domain, ever), then Hunter Email Finder, then Prospeo. It stays within the monthly credits, with no SMTP probing.
  - Verification credits are spent only on the Primary Contact's `guessed` address. A result of `accept_all` or `unknown` leaves the address `guessed`.
  - When a quota is reached, the step is skipped, the job is deferred and the Lead page says why.
- **UK legal form.** Only companies and LLPs count as corporate subscribers. The legal form is confirmed only when Evidence links the Lead to the registry record: the company number on its site, or a matching name and postcode. An unconfirmed Lead stays visible and researchable.
- **Decision-makers.** Only people in the Segment's decision-maker roles become Contacts. The Primary Contact prefers the role tied to the strongest live Event. If no one qualifies, the Lead shows "no decision-maker found". An Operator's choice of Primary Contact sticks.
- **Scoring.**
  - Signal Score = min(100, the sum of live Event weight × decay).
  - Fit Score = min(100, size fit + live Attribute weights). Size fit is 50 inside the Segment's range, 25 when size is unknown and 0 outside.
  - Lead Score = the weighted Fit and Signal Scores, 0.5 each by default.
  - Tier A needs a live Tier 1 Event, and Tier B a live Tier 2 Event. Everything else is Tier C.
  - Disqualifiers run first, and a Lead of unknown size is never disqualified. A Segment change applies only on re-score.
- **AI budget.**
  - The budget is $20 a month, with an alert at 80%.
  - At 100% of the budget or a provider quota, only discovery, enrichment, re-checks and their AI routes stop. Every Epic 2 job checks first and returns `deferred`.
  - Sending, replies and unsubscribe polling are never blocked, and paid usage never starts automatically.
- **Data residency and privacy.**
  - All data stays on the PC except calls to the named APIs.
  - Prompt and response text is never stored or logged. Prompts carry no email address or phone number unless the route's output needs one.
  - No personal data goes into `ai_usage`, `events`, job payloads or logs.
- **Extensibility.** New Segments can be added through seed files without code changes.

## Technical Decisions

### AI gateway (Story 2.7, extended by 2.8)

- **Isolation.** Only `nextron.adapters.ai` imports `anthropic` (2.8.0, built on httpx2), and import-linter enforces it.
- **Port.** `LlmPort.run(route_tuple, inputs, urgency) -> LlmResult`.
  - The full port later adds `submit_batch`, `fetch_batch` and `delete_batch` (Epic 4). No port call ever waits on a batch.
  - The result is exactly one of `ok(parsed)`, `refused`, `truncated`, `invalid` or `failed(retryable)`. Each carries the model that served the call and its usage, including `iterations`.
- **Routes are data.** `routes.py` declares candidate versions `{name, model, effort, prompt_version, output_schema, batchable, max_tokens, max_tokens_cap, budget_class}`.
  - Epic 2 routes:
    - `extract_signals` and `extract_people`: `claude-haiku-5-5`, batchable, blockable for enrichment.
    - `signal_inbox_parse`: `claude-haiku-5-5`, not batchable, blockable for discovery.
  - Effort is explicit per route; the model default is `medium`.
  - Later routes: `grounding_check` (Haiku, with a tested Opus fallback) and the drafting, `classify_reply` and `call_prep` routes (Opus). All of them are `essential`, meaning never blocked.
  - Services pass the active tuple and never hard-code a model.
- **Result handling**, by `stop_reason`, before schema validation:
  - `max_tokens`: one retry with double `max_tokens`, up to the route cap, then `truncated`.
  - `refusal`: `refused`. Haiku has no server-side fallback, so Epic 2 never sends `fallbacks`.
  - Output that fails the schema: `invalid`.
  - HTTP 429, 5xx and connection errors raise `AdapterError(retryable=True)` with SDK retries off, so the job runner owns retries. Other 4xx errors return `failed(retryable=False)`.
  - The gateway never checks the budget. The calling service calls `domain.budget.allow(blockable, …)` first.
- **Structured output.**
  - The JSON schema goes in `output_config.format`, never in the cached system prompt.
  - Enum values are lowercase `snake_case`, and responses are case-normalised before validation.
  - No property may be named like `reasoning`, `chain_of_thought` or `thought`. An `explanation` property needs a limit of 200 characters or fewer.
- **Prompt layout.**
  - The system prompt holds the instructions and is the stable, cached prefix.
  - Untrusted text goes only in the user turn, inside `<untrusted source="…" id="…">` blocks, with delimiter look-alikes escaped.
  - Each call holds exactly one Lead, and the builder raises if blocks belong to more than one.
- **API constraints.**
  - The SDK targets API version 2026-10-21, where sampling parameters return 400.
  - Opus 5.5 rejects a forced `tool_choice` and prefill, and its always-on thinking is billed as output. Use structured outputs.
- **Metering.** Every call, including retries and failures, writes one `ai_usage` row through `UsageMeterPort`. The row holds:
  - route, tuple and served model;
  - input, output, cache-read and cache-write tokens;
  - `iterations`;
  - the batch flag (false in Epic 2);
  - cost in integer micro-dollars;
  - `refused`, recorded separately from errors.
- **Pricing.**
  - Cost comes from `adapters/ai/pricing.py`, with per-model input, output, cache-read and cache-write rates, plus Haiku's higher rate for prompts over 100K tokens.
  - Rates the planning docs record: Opus 5.5 is $4 input and $20 output per MTok. Haiku 5.5 is $0.10 input and $0.50 output per MTok for prompts up to 100K tokens. Batches are half price.
  - The planning docs don't give the Haiku rate above 100K tokens or the cache multipliers. Take them from the current Claude API pricing reference; don't guess them.
  - Haiku 5.5's tokenizer counts about 30% more tokens than Haiku 4.5's.
- **Anthropic retention.** Request and response bodies are never stored locally. The provider's retention window is a residual on erasure receipts.
- **Versioned prompts (2.8).**
  - Prompts live at `prompts/<route>/v1.md` and are immutable once used. `manifest.lock` records each file's SHA-256.
  - `prompt_snapshots` and `ai_route_activations` are owned by `playbook`, with one active tuple per route. The first tuple is a bootstrap activation with actor `seed`.
  - The gateway reads prompt text from the snapshot the tuple names, never from the live file.
  - Results record `ai:<route>@<prompt_version>`.
  - The extraction pass bar is 100% of quotes passing the substring check and at least 80% Signal recall. The opt-in `ai_eval` suite activates nothing.

### Research pipeline

- **Owners.**
  - `setup`: `segments`.
  - `discovery`: the `leads` identity group and `signal_inbox_items`, plus `merge` and `canonicalize_lead`.
  - `research`: the `leads` profile group, `contacts`, `provenance_records`, `evidence`, `signals`, `signal_evidence`, `crawled_pages`, `crawl_runs`, `job_postings` and `email_domains`.
  - `scoring`: the score group.
  - `pipeline`: `pipeline_stage`.
  - `LeadsMerged` triggers every owner's `reparent_lead`.
- **Chain.** Each stage enqueues the next in its apply transaction:
  1. `enrich_lead` (bulk lane, concurrency key `domain:<registrable domain>`)
  2. `render_pages` (only when needed)
  3. `extract_lead`
  4. `check_job_boards`
  5. `check_legal_form`
  6. `select_primary_contact`
  7. `find_contact_email`
  8. `verify_contact_email`
  9. `score_lead`

  The Signal Inbox chain runs entirely on the interactive lane with urgency `soon`. No transaction spans a network call: prepare, then the port call, then apply.
- **AI output is only a proposal**, and code validates it:
  - a quote is at most 300 characters and an exact substring of the cited page's stored text after NFC and whitespace normalisation;
  - the Signal type belongs to the Lead's Segment;
  - a size clue's number appears in its quote;
  - a person's name and role appear in their quote;
  - absence page lists come from the crawler, and counts are computed by rule.

  A rejected proposal is discarded, and an `events` row records it with IDs only.
- **Evidence is immutable.**
  - An identical re-observation updates only `last_confirmed_at`.
  - A changed observation inserts a new row and sets `superseded_by` on the old one.
  - A vanished quote sets `retracted_at`.
  - Changes publish `EvidenceChanged`; a closed posting publishes `EventClosed`.
- **Egress filter** for the crawler, renderer and ATS adapters:
  - `http` and `https` on ports 80 and 443 only, with DNS resolved by the adapter itself;
  - loopback, RFC 1918, link-local, CGNAT, multicast, IPv6 ULA and the PC's own addresses are refused;
  - the connection goes to the validated IP, and each redirect (5 at most) is re-validated;
  - responses are HTML, XHTML or JSON of 5 MB or less, with no cookies kept and a fixed User-Agent.

  Hidden content (`script`, `style`, `noscript`, `template`, comments and hidden elements) is stripped before page text is stored. Page text is purged after 30 days, and the content hash is kept.
- **Single sources of truth:**
  - `domain.identity`, which normalises identities against the vendored Public Suffix List;
  - `domain.signals` (`age_days`, decay, `strongest_event`, `match_hiring`);
  - `domain.scoring` (`tier` and the scores);
  - the closed state machines for Lead Pipeline Stage, Address Class and SignalInboxItem.
- **Metered providers:**
  - `web`, `ats` and `companies_house` (600 requests per 5 minutes, header authentication);
  - Hunter and Prospeo in doubled units: find 2, Domain Search 2, verification 1.

## UX & Interaction Patterns

- **Lead page.**
  - The header shows the name, the website (an `http`/`https` link with `rel="noopener noreferrer"`), Segment, country, Pipeline Stage, Tier, and the scores with each factor linked to its Evidence. A Disqualified Lead also shows the matching rule.
  - Sections: Contacts with their Provenance, Signals, Evidence (with an "AI-extracted" label), Job postings, legal form, and each address's verification status.
  - Plain-text states include "website unreachable", "no decision-maker found", "email not found yet: quota reached" and "verification pending: quota reached".
  - The page is sent with `no-store`, renders values through the untrusted-text helpers and reads only through `queries.*`. A merged Lead's URL redirects to the survivor.
- **Leads list.** 50 per page, filterable by stage and Segment, searchable by name or domain, and without merged Leads. The Tier and Lead Score columns are sortable.
- **CSV import.** The form takes an origin and a default Segment. The summary shows imported, merged and rejected counts, with an escaped reason for each rejected row.
- **Signal Inbox.**
  - A paste form takes items.
  - The list shows each item's status, submission time and escaped text in an `hx-disable` container, with a link to the resulting Lead.
  - A flagged item shows what is missing and lets Mac pick a Lead or enter a website and Segment.
  - A denylisted URL gets a "stored as a source only; never fetched" notice.
- **Actions.** "Make primary", "Find email", "Re-score" and "reprocess" are CSRF-protected POSTs.

## Cross-Story Dependencies

- **From Epic 1:** `UsageMeterPort` with the `ai_usage` and `api_usage` tables, `domain.budget.allow`, re-queuing of `deferred` jobs, job lanes, settings, secrets and the env isolation that keeps live adapters out of tests.
- **Within Epic 2:**
  - 2.1's Segments feed 2.3, 2.9, 2.10, 2.15 and 2.16.
  - 2.2 underlies every Lead creation.
  - 2.3's Contacts and Provenance feed 2.10, 2.13, 2.14 and 2.16.
  - Crawling and extraction: 2.5 → 2.6 → 2.9.
  - The gateway: 2.7 → 2.8 → 2.9, 2.10, 2.11 (postings come from `extract_signals`) and 2.16.
  - Email: 2.10 → 2.13 → 2.14.
  - 2.15 closes the chain, and 2.16 exercises the whole chain end to end.
- **Later epics:**
  - Epic 3's Eligibility needs the legal form, Address Class and Provenance from this epic.
  - Epic 4 builds on this epic's gateway:
    - it adds the Opus routes with server-side `fallbacks` (beta `server-side-fallback-2026-07-01`, direct calls only);
    - it adds `grounding_check`;
    - it adds Message Batches, which never carry `fallbacks`, use a 1-hour cache TTL and set `custom_id` to `<route>_<target ULID>_<rev>`;
    - it runs a per-route cost and effort sizing spike.

    The Epic 2 gateway must leave room for all of this without sending any of it yet.
