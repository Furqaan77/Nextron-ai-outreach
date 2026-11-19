# Handoff: Nextron Outreach Engine

**Read this first if you are a new Claude session or a new developer.** It records where the project stands, every decision made so far, and exactly how to continue. Last updated 2026-11-17.

---

## 1. What this is

**Nextron AI** (www.nextronai.com) is a 17-person custom software and AI agency based in San Jose. Furqaan Ahmed is the CEO and Bushra Ahmed the co-founder. It builds custom AI (private LLMs, chatbots, voice agents), CRM/ERP systems, automation and dashboards. **It currently has no live clients and has never done outbound sales.**

This repository holds the **Nextron Outreach Engine**: an in-house system that finds companies showing public signs of a problem Nextron solves, writes short evidence-based emails in Furqaan's name, has a human approve **every** email, sends them safely, and hands interested replies to Furqaan. **The only success metric is paying clients.**

The project owner and operator is **Mac** (the user). Mac prefers fast paths, few checkpoint pauses, and plain-English explanations.

## 2. Where everything is

| What | Path |
|---|---|
| Product brief (final) | `_bmad-output/planning-artifacts/briefs/brief-nextron-outreach-engine-2026-11-14/brief.md` (+ `addendum.md`: setup checklist, legal rules, budget, rejected options) |
| PRD (final, FR-1..FR-73) | `_bmad-output/planning-artifacts/prds/prd-nextron-outreach-engine-2026-11-14/prd.md` (+ `addendum.md`, `changes-v2.md`, review files) |
| Architecture spine (final, AD-1..AD-22) | `_bmad-output/planning-artifacts/architecture/architecture-nextron-outreach-engine-2026-11-14/ARCHITECTURE-SPINE.md` (+ `reviews/`) |
| Epics and stories (final, 11 epics, 146 stories) | `_bmad-output/planning-artifacts/epics.md` (+ `epics-validation.md`) |
| Sprint tracking | `_bmad-output/implementation-artifacts/sprint-status.yaml` |
| Story specs and build artifacts | `_bmad-output/implementation-artifacts/` (`spec-1-1-…md`, `epic-1-context.md`, `deferred-work.md`) |
| **Decision logs** (the "why" behind everything) | `.memlog.md` in each planning folder (brief, prd, architecture) and `_bmad-output/party-mode/memories/installed/.memlog.md` |
| Research | `docs/research/` (company profile, outreach landscape and laws, ICP segments) |
| Sales Playbook (runtime AI rules, v0.2.0) | `docs/playbook/sales-playbook.md`; sources pinned in `docs/playbook/sources/agency-agents/` (MIT, commit 8ae6164) |
| Plain-English overview page | `docs/overview/engine-overview.html` (open in a browser) |
| **The code** | `nextron-engine/` (Python 3.14, uv project) |
| BMAD method (installed, v6.12.4) | `_bmad/` plus skills in `.claude/skills/bmad-*` |

**Before re-deciding anything, read the relevant `.memlog.md`.** Mac has already answered most questions.

## 3. Current status (2026-11-17)

| Phase | Status |
|---|---|
| Product brief | ✅ final |
| PRD | ✅ final (FR-28 amended 2026-11-15) |
| Architecture spine | ✅ final (amended 2026-11-15: any rejected Step stops the Sequence; owner-map additions) |
| Epics and stories | ✅ final, 146 stories |
| Sprint planning | ✅ done; readiness gate PASS |
| **Build** | **Stories 1.1–1.15 complete** (commits `c2e1b78`, `5adc1aa`, `b1a7384`, `8a92a32`, `ec55442`, `542bc92`, `0966ef1`, `81478a5`, `5352826`, `c50fff3`, `dfc2cdb`, `6e34a17`, `712357d`, `970454c`, `6ec7164`, `b0eb26e`; sprint status `review`), plus **Story 1.17** (merge `a442dd9`), **Story 2.1** (merge `cb63e02`), **Story 2.7** (merge `d4026d2`), **Story 3.1** (merge `93fd888`), **Story 2.2** (merge `8b32024`), **Story 1.18** (merge `5572b62`), **Story 4.1** (merge `8a32d7a`), **Story 2.8** (merge `2acd2c0`), **Story 2.3** (merge `24bbda4`), **Story 1.19** (merge `ac54f23`), **Story 4.3** (merge `84c0f80`), **Story 4.7** (merge `fb662d6`), **Story 2.4** (merge `04a9a64`), **Story 3.2** (merge `b6cf191`), **Story 2.5** (merge `9b91549`), **Story 1.20** (merge `ba3b062`), **Story 4.2** (merge `c511e23`), **Story 3.3** (merge `75f39d5`), **Story 4.11** (merge `8cd08e5`), **Story 1.21** (merge `932cd0a`), **Story 3.8** (merge `f29d1f6`), **Story 2.9** (merge `c5781f1`), **Story 4.4** (merge `f437364`), **Story 2.6** (merge `1e53e10`), **Story 3.4** (merge `6826855`), **Story 3.5** (merge `0236df2`), **Story 3.6** (merge `f7a1dbb`), **Story 2.10** (merge `da145d0`) the 2.9 follow-up **2.9a** (merge `8f190c3`), **Story 4.5** (merge `d121f0a`), **Story 3.7** (merge `e767cee`), **Story 3.13** (merge `4d46a06`), **Story 6.5** (merge `1857f69`), **Story 3.15** (merge `52c47bc`), **Story 4.6** (merge `97a216b`), **Story 2.11** (merge `9b5f7bb`), **Story 2.12** (merge `0df50b1`), **Story 3.14** (merge `424c070`), **Story 4.8** (merge `9c3764a`), **Story 3.10** (merge `e391ab4`), **Story 3.16** (merge `818a8ff`), **Story 6.4** (merge `42df580`), **Story 2.13** (merge `777aae2`), **Story 2.15** (merge `5a415d7`), **Story 4.9** (merge `09b6bf1`), **Story 10.1** (merge `e5d70b3`), **Story 9.7** (merge `5d502de`), **Story 3.11** (merge `a257c96`), **Story 8.2** (merge `68472bb`), **Story 4.12** (merge `63c5797`), **Story 3.12** (merge `6f654ec`), **Story 8.3** (merge `c8166d5`), **Story 10.2** (merge `c7744a6`), **Story 9.1** (merge `13963a5`), **Story 2.14** (merge `a39174b`), **Story 10.7** (merge `863ef3e`), **Story 4.10** (merge `f15e2b0`), **Story 9.4** (merge `46bc888`) **Story 2.16** (merge `f512511`), **Story 5.11** (merge `5eaacc6`), **Story 11.5** (merge `917902e`), **Story 11.7** (merge `b6d0b48`), **Story 11.8** (merge `1af4568`), **Story 9.6** (merge `3cd459b`), **Story 9.8** (merge `52f3c3f`), **Story 4.13** (merge `f8276b0`), **Story 4.14** (merge `f8beb44`), **Story 8.4** (merge `510f672`) and **Story 8.1** (merge `f597413`). **Epics 1 and 2 are complete.** The build runs in parallel tracks (§4). See `sprint-status.yaml` for what is in progress. Built unattended overnight on 2026-11-17 per Mac's instruction; overnight decisions are listed under "Decisions made overnight (pending user review)" in each spec. |

**Story 1.1 result:** `nextron-engine/` scaffold with exact version pins, the spine's package layout (16 owner services), 28 import-linter contracts in 7 AD-1 groups, startup safety checks, a per-environment instance lock, `nextron serve`, `nextron doctor`, and `GET /health` on 127.0.0.1. 190 tests pass. The three-layer review found 37 findings: 15 fixed (including a high one, Uvicorn's exit code 3 colliding with "lock held"), 2 deferred, 20 rejected with reasons.

**Story 1.2 result:** redacted JSON-line logging (rotating `nextron.log`, redaction of secrets, tokens and emails on every path, including uncaught exceptions) and typed `DomainError`/`AdapterError`. 380 tests.

**Story 1.3 result:** one configured SQLite engine (WAL, `BEGIN IMMEDIATE`), thread-bound units of work (`@use_case`/`@command`), an append-only `events` table, the 10 AD-3 notification payloads, forward-only Alembic migrations run at `serve` start, and a PII registry for every column. 787 tests, 29 contracts.

**Story 1.4 result:** `nextron open` (one-time 256-bit code, 5-minute expiry), a web guard (Host check, session, security headers, no CORS), secrets in Windows Credential Manager via `SecretsPort`, a base layout with self-hosted HTMX, and every route declaring whether it shows personal data. 1172 tests, 29 contracts.

**Story 1.5 result:** CSRF-checked POSTs (`Origin` + token; every form uses `method="post"`, `hx-post` and `{{ csrf_field() }}`), `/enter` accepts only browser-started navigations, autoescaped rendering of untrusted text with a `|safe` allowlist, and read-only units of work for GET handlers. 1422 tests, 29 contracts.

**Story 1.6 result:** one typed settings schema (`nextron/domain/settings_schema.py`, 9 types, checked at import), a `settings` table (migration `0002_1_6_settings`), `Setup.set`/`Setup.save`, and a CSRF-checked Settings page built from the schema. Contract tests stop any setting from switching off compliance features or naming a secret. 1825 tests, 29 contracts.

**Story 1.7 result:** a credential registry (`nextron/domain/credentials.py`, 9 credentials with keyring names such as `anthropic/api_key`), a `Credentials` service, `read_credential()` for adapters (read at call time, never cached), and a write-only `/credentials` page linked from Settings, where removing takes two steps. A planted-secret test proves values reach no response, log, DB column or file. 1960 tests, 29 contracts.

**Story 1.8 result:** `AlertPort` and `MailPort`, and sink adapters (`nextron/adapters/sink`) that wrap every mail and alert adapter outside `prod`. Only `sink_allowlist` recipients are delivered; everyone else goes to `<data dir>\sink\<date>.jsonl`. The allowlist is empty by default. Tests run as `NEXTRON_ENV=test`, and live adapters, the real keyring and non-loopback network all raise there. `doctor` warns if `prod` is set persistently. 2190 tests, 29 contracts.

**Story 1.9 result:** Google Desktop client import, and a loopback OAuth consent flow for the `internal_notify` and Postmaster grants, using `httpx2` and no new dependencies. Grants are stored per account in Credential Manager. Each secret now has its own Credential Manager target, `nextron-engine/<env>/<name>`, which closes the cross-process race; old entries move over on first read, as verified live on 2026-11-17. 2595 tests, 29 contracts.

**Story 1.10 result:** durable jobs (`jobs` table, migration `0003_1_10_jobs`), a Job state machine, the `JobQueue` service, and `nextron/jobs/runner.py`. The runner has three lanes (realtime, standard, bulk), leases renewed while a handler runs, retries, a keeper thread that reaps expired leases and purges old jobs, and a 15-second graceful shutdown. Handlers must be idempotent. 2985 tests, 29 contracts.

**Story 1.11 result:** the closed list of 19 AD-10 triggers (registered only after their job type exists, so it ships empty), an APScheduler scheduler that catches up once after sleep, and a heartbeat (`runner_heartbeat` table, migration `0004_1_11_heartbeat`, plus `<data dir>\heartbeat` touched every 30 s). The heartbeat stops when a lane dies, a job overruns `max_runtime`, or the scheduler or event loop stops responding. 3134 tests, 29 contracts.

**Story 1.12 result:** ops alerts by Telegram (adapter behind `AlertPort`, sink-wrapped outside prod) and in the dashboard. It adds the `alerts` and `notifications` tables (migration `0005_1_12_notify`), the first job type `send_alert`, a `job_dead` alert from an `ops` sweep, a new required setting `alert_chat_id` (numeric Telegram chat id), and a notification area with Dismiss. Nothing has been sent to a real Telegram chat yet. 3416 tests, 29 contracts.

**Story 1.13 result:** metering of every external call (`UsageMeterPort`; `api_usage` and `ai_usage` tables, migration `0006_1_13_metering`), budget rules (`nextron/domain/budget.py`, `allow()`), seven Budget-and-limits settings, a `DEFERRED` job result (re-queued in 1 h with the attempt given back), once-a-month dashboard notices at thresholds, and a Quotas and cost page at `/quotas`. Sending and replies are never blocked. 3641 tests, 29 contracts.

**Story 1.14 result:** `nextron doctor` runs 11 readiness checks (`ok`/`warn`/`fail`, table or `--json`, `--env`), reading the database read-only and never taking the lock. With a stored Google grant it makes one real token refresh, metered in memory only. 3815 tests, 29 contracts.

**Story 1.15 result:** an engine health panel at `/health/panel` (the nav's Health link), refreshed every 30 s:
- worker state from the heartbeat;
- each job type's last success, by a new `JobGroup`;
- an error list (dead jobs with Dismiss, AI spend at 80% or more, quotas reached);
- the `nextron doctor` readiness list, run in-process with no network call and no Google refresh.

The notification area shows "Engine health: N errors need attention". New error sources register in `main.build_health_issues`. 3968 tests, 29 contracts.

**Story 1.16 result:** in-process database backups (SQLite backup API, never a file copy). Each copy is checked for integrity, then its SHA-256, revision and the row counts of the protected tables are recorded in a new `backups` table (migration `0007_1_16_backups`).
- A `backup_db` job runs daily at `backup_time` (10:00 in `sender_timezone`) and from the health panel's "Back up now".
- `serve` takes a `pre_migrate` copy before any migration.
- A failure raises a `backup_failed` alert and appears on the health panel.
- Copies older than `backup_rotation_days` (35) are rotated out, but the newest verified copy is always kept.

4161 tests, 29 contracts.

**Story 2.1 result:** `nextron seed sync` loads the Law-PrivateAI and Ops-Automation Segments, with their Signal types, weights, disqualifiers and decision-maker roles, from `seed/segments.yaml` into a new `segments` table (migration `0008_2_1_segments`, renumbered at merge).
- The file is read by a strict stdlib YAML-subset reader.
- A second run reports "0 changed".
- An invalid file changes nothing and names the failing field.

4560 tests on its branch.

**Story 1.17 result:** each daily or manual backup is encrypted (scrypt key from the `backup/passphrase` Credential Manager entry, AES-256-GCM in 64 KiB chunks) and written to the `backup_offpc_dir` folder.
- Each copy is verified by its plaintext SHA-256.
- Off-PC copies older than 35 days are rotated out, but the newest is never deleted.
- A failed copy alerts and shows on the health panel.
- Doctor now has 12 checks: `offpc_backup` warns while the folder or the passphrase is missing.
- Migration `0009_1_17_offpc`.

Main after both merges: 5017 tests, 29 contracts.

**Story 2.7 result:** every Claude call goes through `LlmPort` / `ClaudeGateway` (`nextron/adapters/ai/`). It returns a typed result: `ok`, `refused`, `truncated`, `invalid` or `failed`.
- Untrusted text goes into `<untrusted>` blocks.
- A truncated reply gets one retry with double `max_tokens`, up to the cap.
- Every HTTP request records an `ai_usage` row priced from `adapters/ai/pricing.py`.
- No prompt or response text is stored or logged, and only `nextron.adapters.ai` imports `anthropic`.
- It isn't wired into `main` yet; Story 2.9 does that.

**Story 3.1 result:** `seed/markets.yaml` (US and UK Market rules) syncs with the Segments in one `nextron seed sync` transaction, into a new `markets` table (migration `0010_3_1_markets`).
- A compliance floor in code allows cold email only in US and GB Markets. A GB Market must block free webmail, require the privacy line and check legal forms (8 Companies House types).
- New setting: `us_solicitation_label`.

**Story 2.2 result:** exactly one Lead per organization. A new `leads` table (migration `0011_2_2_leads`) and `nextron/domain/identity.py` key Leads by registrable domain (from a vendored Public Suffix List), company number, place id and LinkedIn slug.
- Duplicates merge into the oldest Lead, with the stage rules in the spec.
- A Lead's Market is never stored; it is derived.

Main after these merges: **6789 tests, 29 contracts.**

**Story 1.18 result:** a monthly restore test, plus "Run restore test now" on the health panel.
- It decrypts the newest off-PC copy into `<backup dir>\restore_test\` and checks it read-only: SHA-256, integrity, revision at head, and protected-table counts.
- It records the result in `restore_tests` (migration `0012_1_18_restore_tests`) and deletes the work folder.
- A failure alerts `restore_test_failed` and shows on the panel.
- The live database is never touched.

**Story 4.1 result:** `seed/sequences.yaml` holds the default four-Step Sequence and its Step briefs (asks from PRD FR-4, word limits, link and subject rules). Law Step 2 has an ABA 512 angle for the US and an SRA angle for the UK.
- It syncs in the same `nextron seed sync` transaction, into `sequences` and `step_briefs` (migration `0013_4_1_sequences`).
- A compliance floor in code: no price, no audit offer, Step 1 on day 0 with footer-only links and a subject under 7 words.

**Fix:** the flaky job-runner shutdown tests were made deterministic (merge `496e2ba`; test-only change).

**Story 2.8 result:** the extraction prompts are versioned files (`adapters/ai/prompts/<route>/v1.md`), hash-locked in `manifest.lock`.
- `serve` stores each prompt version and bootstrap-activates each route's default tuple (`prompt_snapshots`, `ai_route_activations`; migration `0014_2_8_ai_routes`).
- A used prompt can never change: `serve` exits 2.
- `queries.playbook.active_tuple(route)` gives the pinned tuple.
- An opt-in `ai_eval` extraction suite with 11 synthetic cases.

**Story 2.3 result:** CSV import at `/leads/import`.
- It creates Leads (deduplicated), Contacts and Provenance Records (`contacts`, `provenance_records`; migration `0015_2_3_contacts_provenance`), with email syntax checked offline.
- The FR-49 never-crawl list (`domain/fetch_policy.py`) refuses shared-platform websites.
- Merged Leads move their Contacts to the survivor.

**Story 1.19 result:** `nextron restore <copy>` checks a backup copy, takes a `pre_restore` safety copy, asks you to type `yes`, and swaps the copy in.
- Recovery mode then turns on (`recovery_state`, migration `0016_1_19_recovery`). Every later send path must check `queries.ops.recovery_mode()`.
- Stop the engine first, because restore holds the engine lock.
- The erasure-ledger reapply step is deferred to Story 3.16.

**Story 4.3 result:** Approved Company Facts.
- `seed/company_facts.yaml` holds 8 facts: Playbook §6, the Offer with no price, ABA 512 for US only, and the SRA notice for UK only.
- The `company_facts` table is append-only through DB triggers (migration `0017_4_3_company_facts`), and the `/facts` page under Settings adds, edits and retracts facts.
- Drafting may cite only `allowed_facts(segment, market)`.
- Seed sync moved to `ops.Seeds.sync_seed`.

**Story 4.7 result:** register Sending Inboxes at `/inboxes`, under Settings (`sending_domains`, `sending_inboxes`, migration `0018_4_7_sending_inboxes`).
- Refused: nextronai.com (also a DB CHECK), role addresses, and outside prod anything not on the allowlist.
- More than 3 inboxes on one domain triggers a warning.
- New required setting `sender_name`.

**Story 2.4 result:** the Leads list at `/leads` (the nav's Leads/Pipeline), with stage and Segment filters, search and paging, and a Lead page at `/leads/<id>` showing Contacts and their Provenance. Merged Leads redirect to the survivor.

**Story 3.2 result:** each Contact gets a Market and an IANA timezone, with the reason for each (`market_basis`, `tz_basis`).
- Sources: office location, the email's ccTLD and the Lead's country. A conflict between them takes the stricter Market, and a non-US/UK ccTLD blocks.
- A vendored US state→timezone table.
- CSV rows from countries outside the enabled Markets aren't imported ("blocked by Market").
- A one-off job resolves existing Contacts.

**Story 2.5 result:** a polite website crawler (`enrich_lead`):
- it obeys robots.txt;
- the egress filter blocks private, loopback and the PC's own addresses on every hop;
- it fetches 15 pages at most, to depth 2;
- the FR-49 denylist and shared platforms are refused;
- only visible text is stored (`crawled_pages`, purged after 30 days).

The User-Agent is `NextronBot/1.0`. It has never fetched a real website; it is tested only on mocks.

**Story 1.20 result (spike, OQ-A9):** a conditional **go** for a logon task plus supervisor. See `docs/spikes/oq-a9-logon-task.md`.
- A contained proof showed restarts after a crash and after a stale heartbeat.
- `Stop-ScheduledTask` leaves the engine running; Story 1.21 handles that.
- No real logon task was registered. One on-demand test task was registered by accident and removed at once; verified clean.

**Story 4.2 result:** Campaigns, Offers and Variants, seeded and shown on Settings.
- There is one Campaign per Segment × Market, starting at `draft`, with 1–2 Variants.
- A UK Campaign can't be activated without a recorded LIA, enforced by DB triggers.
- Pause and resume.

**Story 3.3 result:** the Suppression List. It is the `suppression_entries` table (migration `0022_3_3_suppression_entries`), with hashed keys for address, domain, person and profile.
- **Permanent:** DB triggers forbid deletes and key changes. Only `reply_rule` entries can be voided, and no-expiry is enforced by contract.
- **One writer:** `Compliance.suppress` is the only code that can write it, and a contract fails on any other mention of the table.
- **Closes the Lead:** an opt-out closes the Lead's pipeline as `lost`.

**Story 4.11 result:** the compliance footer.
- **Five settings:** `sender_signature`, `legal_name`, `postal_address`, `optout_line` and `privacy_notice_url`. Four are required, so doctor fails until they are set.
- **Code:** `domain/render.py` builds and checks it. `footer_complete` lists what's missing, and sending will be blocked while anything is missing.
- **UK privacy line:** it states where the details came from.
- **Snapshots:** they go in `footer_snapshots`.
- **Contract:** NFR-7 keeps the footer keys in their own section, and no footer text may name a price.

**Story 1.21 result:** `scripts/run.ps1` supervises the engine.
- **Process control:** each start runs in a kill-on-close job object, so stopping the task ends the whole uv/python tree.
- **Restart backoff:** 5 s, doubling to 5 min.
- **Ending supervision:** exit 3 ends it, and so do 5 exit-2s in a row.
- **Stale-heartbeat detection:** a 900 s grace before the first beat.
- **Graceful stop:** `-Stop`/`-Restart` send CTRL_BREAK, and `serve` then exits 0. `-Status` reports state.
- **Logon task:** `scripts/install_logon_task.ps1` registers it. It dry-runs by default and refuses elevation.
- **Safety contract:** no code outside `tests/task_scheduler_guard.py` may name a state-changing Task Scheduler command.

**Story 3.8 result:** unsubscribe tokens.
- **Format:** 108-character AES-GCM-SIV tokens with an HMAC edge tag the Cloudflare Worker can check. The shared test vectors in `tests/fixtures/unsub_token_vectors.json` are binding for Story 3.10.
- **Storage:** only token hashes are stored, in permanent `unsub_tokens` rows.
- **Keys:** each kid has a `k_tok` and a `k_edge` in Credential Manager, set on the Credentials page. They can't be replaced while that kid has tokens under 2 years old.
- **Doctor:** a new check, `unsub_token_keys`, makes 13 checks.

**Story 2.9 result:** Claude reads a Lead's changed pages (`extract_lead`).
- **What is kept:** a proposed Signal is stored only if its quote is on the page, its type belongs to the Segment, and any date is stated in the quote.
- **Evidence:** it is append-only (DB triggers), and it is reconciled on each run.
- **Lead page:** it gets a headquarters timezone and new Signals and Evidence sections.
- **Gateway:** `ClaudeGateway` is wired.
- **Never live:** no real Claude call has been made.

**Story 4.4 result:** the Sales Playbook (`docs/playbook/`) is snapshotted and pinned in AI route tuples (`playbook_snapshots`, `ai_route_tuples`, `playbook_checks`). A view at `/ai`, under Settings, shows the routes, tuples and Playbook versions. A Playbook problem warns but never stops `serve`.

**Story 2.6 result:** pages with under 500 characters are rendered in a headless Chromium. Every browser request goes through the crawler's egress filter, and the browser itself can reach nothing. Chat, booking, form, phone and after-hours widgets are detected per page and per Lead (`contact_methods`). Signal extraction waits for rendering when a render is needed.

**Story 3.4 result:** `assert_contactable` is the one contactability gate.
- **Where it runs:** `upsert_lead` and every `add_contact` source call it before writing.
- **Blocked:** suppressed keys, and Lost or Disqualified Leads.
- **Contract:** `test_ad16_callers.py` lists every AD-16 caller in the spine. It fails any future caller (email finder, enroll, draft, approve, send) that doesn't wire the gate.

**Story 3.5 result:** a Suppression List page at `/suppression`, under Settings, with add, import, exact search and reverse.
- Add or import an address or a firm domain; free webmail and shared platforms are refused.
- Reverse works only on `reply_rule` entries, with a reason.
- Entries can't be deleted, and raw addresses are never shown.

**Story 3.6 result:** live Eligibility per Contact on the Lead page: "Eligible: <basis>" or "Not eligible" with reasons.
- It is recomputed on every view, never stored, and fails closed.
- **No UK Contact is eligible until Story 2.12 confirms legal form via Companies House.**

**Story 2.10 result:** `extract_people` finds named decision-makers on team, about and contact pages. A quote must contain the person's name and role.
- **Selection:** a `select_primary_contact` job picks the Primary Contact: the strongest Event's preferred roles first, then rank. A pick by you is kept.
- **No decision-maker:** the Lead page then says "no decision-maker found".
- **Gated:** suppressed people never become Contacts, and blocked Contacts are never Primary.

**Story 2.9a (follow-up):** the Law "contact form only" Signal comes from detected contact methods by rule, with no AI call. It is withdrawn whenever it can't be verified (a page that still needs rendering, or a tag manager present).

**Story 4.5 result:** a new AI prompt or Playbook tuple goes live only after it passes its test set, with zero injection violations among the fail-closed bars.
- Test runs are queued automatically, at most once per tuple and set.
- They are budget-checked and metered.
- `/ai` offers rollback to a tuple that passed.
- Engines started by tests are network-guarded.

**Story 3.7 result:** the Lead page shows each Contact's Market and timezone with their bases, a red "Provenance missing", and the recorded Eligibility basis.
- UK bases also show the LIA reference and date.
- `record_eligibility_basis` (used by 4.8 at enrollment) refuses false audit records.

**Story 3.13 result:** erase a Contact on request.
- The person is suppressed first, in a committed transaction. Their data then becomes `[erased]` or hashes, and DB triggers freeze the tombstone.
- The gate always blocks an erased Contact, and they are never eligible or Primary.
- Requests are recorded in `data_subject_requests`.
- **Until Story 3.16, restoring a backup brings erased Contacts back.**

**Story 6.5 result:** each Sending Domain's SPF, DKIM, DMARC and alignment is checked daily at 07:00 and on Recheck, using a new reusable `DnsPort`. A failure, or no pass in 26 h, shows "Cold sends blocked". The check on connect is deferred to 6.2.

**Story 3.15 result:** export everything held on a Contact.
- It is a one-time, 60-second, session-bound download, and nothing is written to disk.
- A coverage contract forces every future PII table to be exportable.
- It is unified with 3.13's request records.

**Story 4.6 result:** AI Test Sets, all synthetic: reply 79, grounding 49, drafting smoke 14, plus extraction.
- The sets are validated at start, and a guard blocks real-looking personal data.
- Operator override cases are pseudonymised and erased with the Contact.

**Story 2.11 result:** hiring postings come from careers pages and the Greenhouse, Lever and Ashby public boards. Each matching posting is a hiring Event, and the Event closes when the posting disappears.

**Story 2.12 result:** a UK Lead's legal form is confirmed through Companies House (company number on the site, or exact name plus postcode).
- **UK Contacts can now become eligible,** when their legal form is confirmed corporate.
- Research order: people → job boards → legal form → Primary selection.

**Story 3.14 result:** an erasure also clears personal data outside the database (logs, sinks, AI Test Sets) and issues an erasure receipt per Contact, shown on the Lead page.

**Story 4.8 result:** a Contact can be enrolled in a Campaign, only after both gate checks pass. Suppression or erasure stops the Enrollment.

**Story 3.10 result:** the Cloudflare unsubscribe Worker's code, tests and deploy guard are built. **It is not deployed:** that waits for Story 3.9 and Mac's Cloudflare account, and the guard refuses every deploy until 3.9 replaces the placeholder hosts and KV ids.

**Story 3.16 result:** every opt-out, void and erasure is appended to an erasure ledger next to the backups. After `nextron restore` the ledger is re-applied before anything runs; if it is missing, sending stays blocked.

**Story 6.4 result:** each inbox records its warm-up and ramps its cold Daily Cap (default +5 a week up to 30) under a total daily ceiling (default 50).

**Story 2.13 result:** the Primary Contact's email address is searched in the cheapest order (site, mailto, patterns, then Hunter), within quota.

**Story 2.15 result:** every enriched Lead gets a Fit Score, Signal Score, Lead Score and Tier. A Segment disqualifier closes the Lead. `score_lead` runs at the end of the research chain.

**Story 4.9 result:** each Enrollment gets its Draft slots with due times (weekends shifted), plus pause, resume, delay and stop commands.

**Story 10.1 result:** contracts fail the build on any LinkedIn/Instagram automation library, adapter, job type or host, and the renderer refuses those hosts first. A LinkedIn URL may only come from a human-provided source.

**Story 9.7 result:** `/leads/pipeline` shows every stage group with counts and filters (Segment, Market, date entered), sorted by Lead Score.

**Story 3.11 result:** the engine polls the Worker's `/pending` list every 5 minutes and suppresses each unsubscriber (closing their Lead). Polling is off until `unsub_worker_host` is set (needs Story 3.9).

**Story 8.2 result:** a Google Places search adapter that keeps only the place ID and short-lived coordinates (purged after 30 days), with a quota check before each paid call. Offline outside prod without a key.

**Story 4.12 result:** Draft text is stored as revisions that can never be changed, each bound to a hash; erasure tombstones the text.

**Story 3.12 result:** a failing unsubscribe poll raises one alert after 24 h (not ~288 a day); after a restore, recovery mode holds sending until the Worker is re-polled successfully. Doctor checks the Worker.

**Story 8.3 result:** Places search results become deduplicated Leads behind the contact gate, from `/leads/discovery`, within quota and page caps.

**Story 10.2 result:** the LinkedIn account, its owner's Telegram chat and daily/weekly caps are configured; tasks stay off until set up. Nothing is automated.

**Story 9.1 result:** Leads move through the later stages by hand, with deal value, notes and call bookings; the pipeline board shows deal totals.

**Story 2.14 result:** every address is checked (syntax, disposable list, MX, one Hunter verification) and given an Address Class. No SMTP probing.

**Story 10.7 result:** WhatsApp and Instagram permissions are recorded by hand per Contact, with revoke and opt-out.

**Story 4.10 result:** `/leads/ready` shows today's capacity and lets Mac pick Leads to enroll, best Tier and score first.

**Story 9.4 result:** after each booked call the engine asks for the outcome and records it, moving the stage.

**Story 2.16 result:** the Signal Inbox turns pasted posts into researched, scored Leads; unparseable items are flagged for Mac.

**Story 5.11 result:** away dates (Settings) stop new Sequences being picked automatically while Mac is away. The Dashboard shows "Away until ...". Leads picked by hand still enroll.

**Stories 11.5, 11.7 and 11.8 result:** Segments, Sequences with their Step briefs, and Campaigns with their Variants are edited in the dashboard.
- Every save is a new version that can't be changed later. Running Enrollments keep the version they started with.
- Once rows exist, `nextron seed sync` reports the seed files as ignored ("managed in the dashboard").

**Story 9.6 result:** `/reports/calls` validates the Offer after the first 10 held calls. It shows only counts and codes, never names or erased data.

**Story 9.8 result:** each Lead has a History tab listing everything that happened to it, with filters.

**Story 4.13 result:** every Draft revision is checked against 14 deterministic grounding rules before it is stored, and the result is saved.

**Story 4.14 result:** every revision gets an AI grounding check (Haiku, with Opus as fallback) that fails closed. The Lead page shows a pass/fail/pending badge with the flagged spans.
- **Until a grounding route passes the 4.5 test gate, every revision fails with "checker unavailable".** This is by design.

**Story 8.4 result:** active UK companies are discovered through Companies House keyword and SIC searches. An officer becomes a Contact only when their occupation matches a target role.

**Story 8.1 result:** every job has a budget class. Supply work (discovery, enrichment, re-checks) stops at a provider quota or the AI budget. Sending, replies and unsubscribe polling always keep running.

Main now: **19526 tests, 29 contracts.** Migration head `0060_8_4_registry_discovery`.

**Faster tests (chore `aee9466`):** `uv run pytest -q -n 6 --dist loadgroup` runs the suite in about 75 s instead of about 250 s, with pytest-xdist 3.8.0 as a dev dependency. The spine's tool table doesn't list pytest-xdist yet.

**Story counts:** 146 total. Release 1 (Epics 1–7, first approved sends) is 109 stories, of which 70 are done. Release 2 (Epics 8–9) is 20 stories, and Release 3 (Epics 10–11) is 17.

### For Mac to review (overnight build, 2026-11-17)
- **Overnight decisions.** From Story 1.3 onward, every spec has a section `### Decisions made overnight (pending user review)` with the conservative choices made while you were away. Read them and say if any should change.
- **Before your first real Google grant outside prod:** add your own address to the "Dev and test allowlist" setting (`sink_allowlist`), or the grant is refused. Afterwards, check Credential Manager > Windows Credentials for entries at `nextron-engine/<env>/<name>`.
- **Spine wording (AD-12).** AD-12 still says secrets live under one keyring service, and the code now uses one target per secret. This needs your OK to amend (logged in `deferred-work.md`).
- **Story 1.9 review items rejected only because they'd change the approved spec:**
  - no Cancel while a consent waits;
  - any local request to the listener ends the flow;
  - a grant can't be removed once the client entry is missing.
- **Story 1.12 items rejected only because they'd change the approved spec:**
  - an alert whose send job dies stays `pending`;
  - every Telegram refusal is one code, so a bad token looks the same as a bad chat id;
  - each dead job gives two dashboard notices until `alert_chat_id` is set, and there is no "send a test alert";
  - an error notice stays until the page reloads.
- **Story 1.15 items rejected only because they'd change the approved spec:**
  - all eleven readiness checks run on every 30-second poll of the health panel;
  - after a Dismiss, the notification area's error count lags until the next page view;
  - dead jobs show no time of death;
  - AI spend at 80% counts as an error for the rest of the month, with no acknowledge.
- **Story 1.16 items rejected only because they'd change the approved spec:**
  - a backup copy that has no row is never deleted (from a crash mid-copy, or the `pre_migrate` copy of a failed migration), which works against AD-17's 35-day age-out;
  - rotation runs only after a successful backup;
  - while backups keep failing, the newest verified copy can outlive 35 days;
  - nothing alerts when backups simply stop running;
  - there is no free-space check, and the default backup dir is on the same disk;
  - a failed `pre_migrate` copy prints and exits 2 but sends no Telegram alert, because the engine isn't running yet.
- **Off-PC backups (Story 1.17):** set the "Off-PC backup folder" setting, on another drive or a share, and store a strong passphrase in Credential Manager as `backup/passphrase`. **Escrow that passphrase somewhere safe off the PC:** without it the encrypted copies can't be read. Until both are set, backups stay local only and doctor warns.
- **Story 1.17 items rejected only because they'd change the approved spec:**
  - old off-PC copies are deleted only after a successful copy;
  - if the ledger copy fails, the database copy stays;
  - no alert fires when off-PC copying stops because it became "not configured".
- **Story 2.1:**
  - no YAML library was added (a strict stdlib subset reader instead); PyYAML would need your OK;
  - a Segment removed from the seed file is kept and can't be retired;
  - the ABA/SRA check misses forms like `aba512` and `S.R.A.`.
- **Check the Claude prices (Story 2.7)** in `nextron-engine/nextron/adapters/ai/pricing.py` against Anthropic's pricing page. They came from a cached reference, not the live page. The AI budget relies on them.
- **Story 2.7, recommended spec change:** widen the `<untrusted>` escaping to catch fullwidth or look-alike slashes and zero-width characters. The approved spec allows only an ASCII `/`, so this needs your OK.
- **Story 3.1 decisions:**
  - UK legal forms allowed: `ltd`, `plc`, the guarantee and section-30 variants, `private-unlimited`, `private-unlimited-nsc` and `llp`;
  - US legal form isn't checked;
  - any two-letter TLD counts as a country, so `.io` and `.eu` addresses are treated as non-US/UK.
- **Story 2.2 items rejected only because they'd change the approved spec:**
  - merges leave no audit record and can't be undone;
  - a conflicting key on a candidate is dropped;
  - a closed duplicate turns a `won` Lead `lost`;
  - a sole trader's firm name in `leads.name` isn't erased with their Contact.
- **Story 1.18:**
  - the daily not-due check shows as a fresh `restore_test` success on the panel;
  - one transient failure (e.g. the off-PC drive unplugged at the slot) uses up that month's test until you click "Run restore test now".
- **Story 4.1:**
  - check the Step brief wording in `seed/sequences.yaml` against the Sales Playbook;
  - the ABA 512 / SRA detectors miss some spellings ("Op. 512", "S.R.A.");
  - brief text isn't scanned for price wording.
- **Before the first real `serve`, run the AI extraction eval once:** `uv run pytest -m ai_eval` with `ANTHROPIC_API_KEY` entered via `Read-Host -AsSecureString`. It costs a few cents. The first `serve` permanently pins prompt v1 (Story 2.8).
- **Story 2.3 items rejected only because they'd change the approved spec:**
  - when an email matches an existing Contact, a different name or role is dropped silently (e.g. a shared `info@` for two people keeps the first);
  - a Contact re-listed by a second list gets no Provenance Record naming it.
- **Set the new required "Sender's name" setting** (Story 4.7). Doctor's `required_settings` fails until it's set.
- **Decide on free-mail Sending Domains** (Story 4.7). `gmail.com`-style addresses are currently accepted as Sending Domains. This is logged in `deferred-work.md`.
- **Check the company facts** in `seed/company_facts.yaml` before the first send (Story 4.3): the ABA 512 and SRA notice wording, and the Offer's URL.
- **Story 4.3:**
  - seed sync moved to a new `ops.Seeds` use case, because `setup` and `playbook` may not call each other; this adds to the spine's owner map;
  - an Edit form opened in a second tab before a retraction can silently restore the retracted fact.
- **Story 1.19:**
  - the environment guard on restore reads file names only, so a renamed prod copy would be accepted in dev;
  - `pre_restore` safety copies are never rotated.
- **Confirm or amend AD-17 (Story 4.6):** Operator override test cases are now erasable (`ai_test_cases.input` is `redact`). This is stricter than AD-17's "AI Test Sets: nothing to erase".
- **Story 6.5:**
  - inboxes on a subdomain are checked at the parent domain, but Gmail checks the subdomain;
  - registering an inbox doesn't queue a check;
  - DMARC passes without a valid `p=`.
- **Story 2.12:** a firm registered at its accountant's address that quotes no company number stays unconfirmed (and so not eligible).
- **Story 2.11:** EU Greenhouse and Lever boards aren't included; postings on careers pages no longer crawled never close.
- **Story 11.8:** there is no Offer editor, and Offers are frozen once any Campaign exists. A new Offer would need a small follow-up story.
- **Story 11.5:** once any Segment exists, seed sync never writes Segments again, so the dashboard is the only place they change.
- **Story 8.4:**
  - the website lookup trusts the first Places match;
  - each run searches the first pages only (Story 8.10 rotates queries).
- **Story 4.14:** no revision passes grounding until a grounding route passes its AI test set.
- **Story 3.12:** restoring a backup older than the Worker's 30-day window silently loses opt-outs from that gap; the unsubscribe key guard only holds during recovery mode.
- **Story 2.16:** the author's name and role are accepted separately, so one person's role could attach to another's name.
- **Story 9.4:** a recorded no-show no longer counts as a pending booking.
- **Story 3.11:** the Worker's address is a host-only setting (`unsub_worker_host`); Contacts are matched by address hash because no sent-message table exists yet.
- **Story 8.2:** outside prod without a Places key the engine uses an offline stand-in that makes no request.
- **Story 4.12:** a footer whose UK provenance phrase is missing fails closed (no revision).
- **Story 3.10:** deploy is deferred to Story 3.9; the Worker test runner is Node's built-in `node:test` (Node 24+); a visitor without JavaScript sees a blank page after confirming (a `<noscript>` line says the click counted).
- **Story 3.16:** a firm-wide suppression's domain is stored in plain text in the ledger (needed to re-insert it).
- **Story 6.4:** the ceiling counts per inbox; leaving the warm-up record blank sets the cap to 0; "Cap used" shows 0 until sending exists (6.10).
- **Story 2.15:** Lead Score weights are settings (50/50); `score_lead` runs after Primary selection, not after the email search.
- **Story 4.9:** a Step's delay is the gap between Step days; the weekend shift applies to Step 1 too.
- **Story 4.5:**
  - output schemas are pinned by a contract-test lock, not a DB column (an AD-8 amendment would be needed);
  - a tuple with one failed test run isn't re-tested automatically.
- **Story 3.7:** the eligibility-basis recorder trusts the Decision passed in, but refuses a wrong Market, country or LIA.
- **Story 3.13:**
  - a request reference is refused only if it holds `@` or the person's whole name;
  - erasing a Lead's Primary doesn't yet re-run Primary selection.
- **Story 3.6 decisions:**
  - a Market disabled or changed blocks at once;
  - Contacts blocked when no Market was enabled stay blocked after one is enabled;
  - Leads at `replied`/`interested`/`won` show "Eligible" (FR-18 has no stage reason for them).
- **Story 2.10:**
  - Primary selection runs only after website extraction, so Leads with only CSV Contacts never get a Primary until a crawl;
  - a stale CSV role is never replaced by the website's;
  - add UK titles ("Managing Director", "Operations Director") to `seed/segments.yaml` if you want them matched.
- **Story 2.9a:** some script loaders (e.g. HubSpot's chat loader) aren't recognised, so such sites can get a false "contact form only" (it only affects the score).
- **Install Chromium once for page rendering (Story 2.6):** run `uv run playwright install chromium` from `nextron-engine`. Until you do, near-empty pages aren't rendered, and nothing alerts.
- **Unsubscribe token keys (Story 3.8):** set them on the Credentials page before sending. A key re-entered from escrow isn't checked against the original (needs your OK to add a key-check value).
- **Footer contract exception:** the setting label "Unsubscribe token key id" trips the rule against footer words outside the footer section. The merge added a narrow typed exception instead of renaming the label. Say if you'd rather rename it.
- **Story 2.9 items rejected only because they'd change the approved spec:**
  - merged Leads can carry duplicate Signals;
  - undated Events never age;
  - a failed AI run has no retry;
  - a missing Claude key gives one dead-job alert per Lead.
- **Story 2.6:** widgets loaded by a tag manager are missed (a false "no chat"), and phone numbers matched on pages are never purged.
- **Install the logon task yourself (Story 1.21).** The steps are in `nextron-engine/README.md` under "Start at logon and supervision", with check rows in `docs/spikes/oq-a9-logon-task.md`. Use a separate clean checkout for prod and run from a normal, non-admin PowerShell. Nothing was registered during the build.
- **Story 1.21 items for you:** a start that hangs past the 15-min grace is retried without limit, and each retry leaves a `pre_migrate` copy. There is no alert if the supervisor itself dies, and the task priority stays 7. Decide whether you want more than AD-20.
- **Logon task (Story 1.20):** your at-the-PC steps (registration, start at logon, sleep/wake, unregister) are in `docs/spikes/oq-a9-logon-task.md`. Story 1.21 will finalise them.
- **Story 4.2 default Offer:** a ladder of a reply, then an audit ($2,500–$3,500, credited to a build), then a build ($10k–$30k). It is commercial configuration only: drafts never contain a price. Check the figures.
- **Story 3.2:**
  - Contacts blocked when no Market is enabled stay blocked after one is enabled;
  - before the first `nextron seed sync`, every CSV row is refused as "isn't an enabled Market". Run `seed sync` before importing.
- **Story 2.5 crawler limits that would change the approved spec:**
  - lawyer-bio pages can use up the 15-page budget;
  - EU Greenhouse and Lever boards aren't in `ats_hosts`;
  - http-only sites, and sites with bad certificates, are unreachable.
- **Fill in the footer settings before any send** (Story 4.11): signature, legal name, postal address and privacy-notice URL. The opt-out line has a default.
- **Story 3.3 items rejected only because they'd change the approved spec:**
  - an opt-out from a `won` client closes the Lead as `lost`;
  - a sole trader's own-name domain stays in plain text after erasure (OQ-A10);
  - a repeat opt-out writes no new audit row.
- **Story 4.11:**
  - a UK Contact whose source has no privacy phrase (`operator`, `pattern`) can't be drafted;
  - a future `booking_link` setting must relax the "exactly six footer keys" contract.
- **Spine tool table:** add pytest-xdist 3.8.0, the dev-only parallel test runner added for build speed on 2026-11-17.
- **Check the Google Places free allowances** in Story 1.13's quota caps (Enterprise 1,000 and Pro 5,000 per month, from March 2025 pricing) against your Google billing account.
- **`doctor` on a database that is behind head** (for example after an upgrade, before `serve` has migrated it) shows `required_settings: the settings could not be read (OperationalError)`. It clears after one `serve`. Review rejected a clearer message because it would change the spec.
- **Never exercised against the real service:** nothing has talked to real Google, Telegram, Hunter or Claude yet. Every adapter is proven against fakes.

## 4. How to continue building (exact procedure)

1. Open this folder in Claude Code. The BMAD skills in `.claude/skills/` load automatically.
2. Run `/bmad-build` and say: *"Implement Story 1.2 from `_bmad-output/planning-artifacts/epics.md`, tracked in sprint-status.yaml as `1-2-log-structured-redacted-json-lines-and-raise-typed-errors`. Mac prefers no checkpoint pauses."*
3. The workflow does the following:
   - reuses `epic-1-context.md` (it's cached; regenerate it if planning docs changed);
   - loads the previous done spec (`spec-1-1-…`) for continuity;
   - writes `spec-1-2-….md` and asks Mac to approve it;
   - implements via a subagent;
   - verifies against the git diff since the baseline commit;
   - runs three reviewers (blind hunter, edge-case hunter, verification gap);
   - triages every finding into the spec's Review Triage Log;
   - patches, re-verifies, marks the spec done and the story `review`;
   - commits locally.
4. Repeat story by story.

**Parallel tracks (Mac approved them for speed on 2026-11-17):**
- **Worktrees.** Each track builds in its own git worktree and branch:
  - track A, the rest of Epic 1: `e:\Nextron-epic1`, branch `epic-1`;
  - track B, Epic 2: `e:\Nextron-epic2`, branch `epic-2`;
  - track C, the Epic 3–4 stories that need only Epic 1 and the merged Epic 2 work: `e:\Nextron-track3`, branch `track-3`.
- **`main` is the integration branch.** `e:\Nextron AI outreach` is only for merging.
- **After each story:**
  1. merge the track's branch into `main`;
  2. if two tracks took the same migration number, renumber and re-chain the incoming migrations after `main`'s head (file names `NNNN_<story>` must match revision ids, per `test_persistence_contracts`);
  3. run the full suite and commit;
  4. fast-forward the track's branch to `main` before its next story.
- **One full suite at a time.** Every full suite runs through a lock, so only one full suite runs at a time on the PC.
- **Without the worktrees,** build one story at a time, in order: stories share files and one linear Alembic chain.
5. Optional: `/bmad-sprint-planning` with "show sprint status" at any time.

**Conventions established in Story 1.1 (keep them):**
- **Commits:** one local commit per story, in conventional style (`feat(engine): …`), ending with the Co-Authored-By trailer. The remote `origin` is https://github.com/Furqaan77/Nextron-ai-outreach.git (branch `main`, first pushed 2026-11-16). Push only when the user explicitly asks. The commits so far used a placeholder identity `Mac <mac@localhost>` because no git identity was set. Set your own with `git config user.name/user.email`.
- **Verification commands** (run from `nextron-engine/`):
  - `uv sync --frozen`
  - `uv run pytest` (expect all pass, `ai_eval` skipped)
  - `uv run lint-imports` (all contracts KEPT)
  - `NEXTRON_ENV=dev uv run nextron doctor` (all PASS)
  - `NEXTRON_ENV=dev uv run python -m nextron serve`, then `NEXTRON_ENV=dev uv run nextron open` (opens the dashboard with a one-time code). Since Story 1.4 every route needs a session, so `curl.exe -i http://127.0.0.1:8766/health` returns 401: that is expected.
- **Review diff:** exclude the generated `uv.lock` and the vendored `htmx-2.0.11.min.js`.
- **Ports:** 8765 prod, 8766 dev, 8767 test. **Data dirs:** `%LOCALAPPDATA%\Nextron\<env>\`.

**Model choice (discussed with Mac):**
- Opus 5.5 was used for all planning and for Story 1.1.
- Option under consideration: Sonnet 5.5 implements the routine stories while Opus reviews every story.
- Keep Opus for the roughly 25–30 safety-critical stories: 1.16–1.19, 3.3, 3.4, 3.8–3.11, 3.13–3.16, 4.12–4.14, 5.2, 5.4, 6.9, 6.10, 6.12, 7.1–7.3.
- Rough estimate, from one data point: 25–40 minutes per story on Opus, so about 45–70 hours for Release 1.
- Mac has not chosen yet. Ask.

## 5. Key decisions (all made by Mac unless noted)

**Scope and market**
- v1 Segments:
  1. Law firms with 5–50 lawyers (private AI).
  2. Mid-size distributors, logistics firms and light manufacturers (operations automation).
- Markets: **US + UK only**.
- UK cold email goes only to companies and LLPs. PECR treats sole traders and ordinary partnerships as individuals.
- Gulf, India, EU, Canada, CPA/RIA/clinics and real estate are later waves.
- Deal size: $10k–$30k first project. Offer ladder (Claude's choice, to validate on the first 10 calls): a reply-based ask → a $2,500–3,500 Private AI & Workflow Audit, credited toward the build → the build.

**Channels and approval**
- Cold email is the only automated channel.
- LinkedIn is human-sent: Tasks go to the account owner's Telegram (default Furqaan). LinkedIn bans automation and account sharing.
- WhatsApp and Instagram are for warm leads only, with opt-in.
- **Every email, follow-ups included, is individually approved.** Rejecting any Step stops that Sequence; "skip" doesn't.
- Hot replies: Furqaan replies from the Gmail app on his phone. Alerts reach him via Telegram, internal email and the dashboard, carrying a drafted reply, the booking link and two time slots.

**Platform and cost**
- Runs **locally on Mac's Windows 11 PC**, with a dashboard on localhost only and no remote access in v1. Mac confirmed the PC stays on during US and UK morning sending windows.
- The only off-PC piece is a Cloudflare Worker for RFC 8058 one-click unsubscribe.
- Moving to a cloud server was discussed on 2026-11-16 and is possible later as a bounded change. It is not chosen.
- Budget under $100/month: about $35–50 in month 1, about $70–85 from month 2 when Hunter Starter is added.
- Addresses published on a firm's own website count as trusted.
- Engine AI: `claude-haiku-5-5` reads (extraction, grounding check); `claude-opus-5-5` writes (drafts, Reply Drafts, reply classification, call prep). AI cap $20/month. Batch API for overnight work.

**Stack and engineering**
- Python 3.14 via uv, FastAPI + HTMX (vendored 2.0.11), SQLite (WAL), SQLAlchemy/Alembic, APScheduler 3.x, keyring.
- Exact pins are in the spine's Stack table and `nextron-engine/pyproject.toml`.
- `pytz` is allowed **only** as trafilatura's indirect dependency, and `nextron` code may never import it (Mac, 2026-11-15).
- **agency-agents:** the BMAD roundtable judged it safe and useful. Only its ideas were adapted into the Sales Playbook; the agents were **not** installed into `.claude/agents/`.

## 6. Open items

**Business setup (Mac and Furqaan; outside the software)**
- [ ] **Buy the sending domain and 3 Google Workspace inboxes, and start warm-up now.** The first send can happen 14 days after warm-up starts. Steps are in the brief's `addendum.md` §A.
- [ ] A Cal.com booking link on nextronai.com.
- [ ] A business postal address for the email footer.
- [ ] A Telegram bot and alert chat.
- [ ] A lawyer check of CAN-SPAM solicitation wording (PRD OQ-8).
- [ ] A UK privacy notice plus a legitimate-interests assessment.
- [ ] 1–2 case studies with client-confirmed numbers.
- [ ] Confirm the Workspace admin allows an Internal OAuth app. (Spike in Story 6.1.)
- [ ] Decide whose LinkedIn account is used (default Furqaan).

**Deferred engineering** (`_bmad-output/implementation-artifacts/deferred-work.md`)
- Enforcing "callers use only `app.<owner>.api`" needs an AD-1/AD-3 amendment.
- The clean-stop path (exit 0) on Windows belongs to Story 1.21.

**Architecture open questions:** see "Open Questions" in the spine (OQ-A*). They are slice-scoped spikes with owners.

## 7. Gotchas for a new machine or account

- `uv` installs Python 3.14 itself. The startup check requires SQLite 3.51.3 or later, which uv's 3.14.8 build has.
- `_bmad/render/` is gitignored, and BMAD regenerates it on first `/bmad-build`.
- The overview page was published as a private claude.ai artifact under the old account (`https://claude.ai/artifact/QGX5pvd9FqFyDnL7M4fkGb`). It may not be visible from the new account. The source is `docs/overview/engine-overview.html`; republish it if needed.
- Claude Code's auto-memory from the old account does not transfer. Its content is reproduced in section 5 above.
- Windows PowerShell: use `curl.exe`, not `curl`.
