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
| **Build** | **Stories 1.1–1.15 complete** (commits `c2e1b78`, `5adc1aa`, `b1a7384`, `8a92a32`, `ec55442`, `542bc92`, `0966ef1`, `81478a5`, `5352826`, `c50fff3`, `dfc2cdb`, `6e34a17`, `712357d`, `970454c`, `6ec7164`, `b0eb26e`; sprint status `review`), plus **Story 1.17** (merge `a442dd9`) and **Story 2.1** (merge `cb63e02`). The build runs in parallel tracks (§4). See `sprint-status.yaml` for what is in progress. Built unattended overnight on 2026-11-17 per Mac's instruction; overnight decisions are listed under "Decisions made overnight (pending user review)" in each spec. |

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

**Faster tests (chore `aee9466`):** `uv run pytest -q -n 6 --dist loadgroup` runs the suite in about 75 s instead of about 250 s, with pytest-xdist 3.8.0 as a dev dependency. The spine's tool table doesn't list pytest-xdist yet.

**Story counts:** 146 total. Release 1 (Epics 1–7, first approved sends) is 109 stories, of which 18 are done. Release 2 (Epics 8–9) is 20 stories, and Release 3 (Epics 10–11) is 17.

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
