# Epic 2 Context: Researched Leads with evidence and a reachable decision-maker

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Turn CSV imports and pasted signals into one deduplicated Lead per organization. Each Lead is researched from its own website and ATS boards. Every Signal has Evidence, and every Contact and address has a Provenance Record. The engine confirms the UK legal form, picks the decision-maker, finds and validates their address, and computes scores and the Lead Tier, all shown on a Lead page. The epic also builds the AI gateway that later epics reuse. It is Slice 1 work, and it feeds Eligibility (Epic 3) and drafting.

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

- **Segments:** Segments come from seed YAML, so a new one needs no code change. A seed sync never re-scores Leads.
- **Deduplication:** Leads match on registrable domain, Companies House number or place ID, at any stage, including Disqualified and suppressed. The engine never creates a second Lead.
- **Evidence:** a Signal always has Evidence. A quote is at most 300 characters and must be an exact substring of the cited stored text (NFC- and whitespace-normalised). The pages listed in absence or count Evidence come from the crawler, not the model.
- **Contacts:** only people in decision-maker roles are stored. CSV, pattern and finder addresses start `guessed`, and only `guessed` addresses use verification credits.
- **Never fetched:** LinkedIn, Instagram, Google Maps and job sites. Such URLs are stored as a source only.
- **Polite crawling:** robots.txt, at least 1 s per domain, at most 15 pages per Lead. Stored page text is purged after 30 days, keeping its hash.
- **Budget:** discovery, enrichment and their AI calls check `domain.budget.allow` first and return `deferred`. A paid step over its monthly quota is skipped without a call.
- **Signal Inbox:** an item is processed on submission, and its Step 1 Draft should be in the Approval Queue within 15 minutes.

## Technical Decisions

- **Owners:**
  - `discovery`: the `leads` identity group and `signal_inbox_items`.
  - `research`: the profile group, Contacts, Provenance, Evidence, Signals, crawl data, job postings and `email_domains`. It is the sole writer of Evidence, Signals and Provenance.
  - `scoring`: the score group. `playbook`: prompt snapshots and activations. `setup`: `segments`.
  - New columns go in `owners.py` and `pii_registry.py`. Owners of Lead-scoped rows register `reparent_lead`.
- **Identity:** only `domain.identity` normalises (Public Suffix List vendored).
- **Evidence is immutable:**
  - `type` is `quote`, `absence` or `count`. `extracted_by` is `ai:<route>@<version>`, `rule` or `operator`.
  - A re-observation updates `last_confirmed_at`. A change inserts a new row and sets `superseded_by` on the old one. A vanished source sets `retracted_at`.
  - Research publishes `EvidenceChanged` and `EventClosed`.
  - Tier is computed only by `domain.scoring.tier`, and age only by `domain.signals.age_days`.
- **State machines:** Pipeline Stage, Address Class (`invalid` is terminal) and SignalInboxItem live in `domain/<machine>.py`. Each transition appends an `events` row with IDs only.
- **AI gateway:**
  - Only `adapters.ai` imports `anthropic`.
  - Calls return `ok`, `refused`, `truncated`, `invalid` or `failed(retryable)`.
  - The extraction routes run on `claude-haiku-5-5` with no fallbacks.
  - The schema goes in `output_config.format`.
  - Retryable errors raise `AdapterError` with SDK retries off.
  - The gateway never checks the budget, and it meters every call.
  - No prompt or response text is stored or logged.
- **Untrusted text:** goes only in user-turn `<untrusted>` blocks, one Lead per call. The crawler strips hidden content before storing. Model output is a proposal: code validates it or discards it and records an event.
- **Prompts:** immutable once used and hash-locked. Calls use `queries.playbook.active_tuple(route)` and the snapshot text.
- **Adapters:** crawler, renderer, ATS, Companies House, Hunter and Prospeo, DNS and LLM. Each joins import-linter contract 5, has a fake and meters every call.
- **Egress filter:** fetches go only to public IPs on ports 80 and 443. Every redirect is re-checked, including against the FR-49 denylist.
- **Job chain:** each stage enqueues the next in its own transaction: `enrich_lead` (bulk lane, key `domain:<registrable domain>`) → `render_pages` (only when needed) → `extract_lead` → `check_job_boards` → `check_legal_form` → `select_primary_contact` → `find_contact_email` → `verify_contact_email` → `score_lead`. Signal Inbox chains stay on the interactive lane with urgency `soon`.

## UX & Interaction Patterns

- **Lead page:** Tier and scores with factors linked to Evidence; then Signals, Evidence, Contacts with Provenance and job postings.
  - It sends `no-store`, escapes untrusted text and redirects a merged Lead's URL to the survivor.
- **CSV import:** requires the list's origin and summarises imported, merged and rejected rows.
- **Signal Inbox:** flagged items show what is missing and let Mac resolve them.

## Cross-Story Dependencies

- **Built on Epic 1:** jobs, triggers, metering and budget, settings, secrets, CSRF and the event log.
- **Order:** 2.1 and 2.2 come first. 2.5–2.6 and 2.7–2.8 precede 2.9. 2.9's Signals feed 2.10, 2.11 and 2.15. 2.11 and 2.12 insert stages before `select_primary_contact`.
- **Handed to Epic 3:** Contact `tz` and Market stay null until Epic 3 sets them. Eligibility uses `legal_form_status` and Address Class.
