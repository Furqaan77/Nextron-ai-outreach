# Epic 1 Context: Safe, self-running engine on the PC

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Build the platform every later epic stands on: a single-user engine on Mac's Windows 11 PC. It installs with `uv sync --frozen` and is checked with `nextron doctor`. It starts at logon, restarts itself after crashes or hangs, and serves a locked-down localhost dashboard. Credentials live only in Windows Credential Manager. Every external call is metered against quotas and the AI budget, and a health panel and Telegram ops alerts show what needs attention. Verified, encrypted off-PC backups, a monthly restore test and a recovery mode protect the Suppression List and Provenance Records. Dev and test can never reach real prospects. This epic creates only infrastructure tables, no domain tables. It is Slice 1 work, needed before the first send, which can happen once the Sending Inboxes finish 14 days of warm-up.

## Stories

- Story 1.1: Scaffold the engine project with layer contracts and a localhost health page
- Story 1.2: Log structured, redacted JSON lines and raise typed errors
- Story 1.3: Persist through one configured SQLite engine, units of work and an append-only event log
- Story 1.4: Enter the dashboard through a one-time code with a hardened shell
- Story 1.5: Require CSRF-checked POSTs and render untrusted text safely
- Story 1.6: Declare typed settings once and edit them on a Settings page
- Story 1.7: Store external credentials in Windows Credential Manager from a write-only panel
- Story 1.8: Keep dev and test away from real people with environment isolation and sink adapters
- Story 1.9: Grant Google OAuth access through a loopback flow
- Story 1.10: Run durable jobs on three lanes with leases and retries
- Story 1.11: Schedule periodic triggers and write the runner heartbeat
- Story 1.12: Send ops alerts by Telegram and show dashboard notifications
- Story 1.13: Meter every external call and stop non-essential work at quotas and the AI budget
- Story 1.14: Check install readiness with `nextron doctor`
- Story 1.15: See engine health on one panel
- Story 1.16: Back up the database in-process daily and before every migration
- Story 1.17: Encrypt each backup and copy it off the PC
- Story 1.18: Prove backups restore with a monthly restore test
- Story 1.19: Restore a backup and hold sending in recovery mode
- Story 1.20: Spike: register the logon task and prove crash and stale-heartbeat restarts (OQ-A9)
- Story 1.21: Start the engine at logon and supervise it with `run.ps1`

## Requirements & Constraints

- **Platform:** one PC, one Operator role with no sign-in, embedded SQLite, and a dashboard on localhost only.
- **Credentials:** every external secret is entered in a write-only field and holds 1,280 characters or fewer. Secrets never go into files, the DB, logs, job payloads, backups or responses.
- **Quotas and AI budget:** usage is tracked per provider and operation. Hunter charges 1 credit per find and 0.5 per verification, and Places Pro and Enterprise are counted separately. AI spend has a $20 monthly budget with an alert at 80%. At 100% of a quota or the budget, only discovery, enrichment and re-checks stop. Sending, reply handling and unsubscribe polling are never blocked. Paid Places usage never starts automatically.
- **Health panel:** shows the worker status (heartbeat under 2 minutes old), each job type's last success, the last backup and restore test, the doctor readiness list and the errors needing attention.
- **Reliability:** the engine restarts at logon and after a crash or hang. After sleep, a missed trigger runs once.
- **Backups:** a daily local backup plus a mandatory encrypted off-PC copy. Copies rotate after 35 days, and a monthly test restore checks them.
- **Privacy and safety:**
  - No personal data goes into logs, `events`, job payloads or the usage tables.
  - No setting can disable Suppression, Eligibility, the footer, opt-out handling or Market rules.
  - Outside `prod`, mail and alerts reach only an allowlist.

## Technical Decisions

- **Layers:**
  - Code is split into `domain` (stdlib only, pure, takes `now`), `ports` (Protocols), `db`, `app/<owner>/api.py` plus `app/queries/<owner>.py`, then `web`, `jobs` and `adapters`, with `main.py` as the composition root.
  - import-linter contracts run as a test. SDKs may appear only in adapters, and `anthropic` only in `adapters.ai`. The per-owner repository bans are generated from `owners.py`.
  - Each new adapter joins the contract list and gets a fake.
- **Process:** one process per environment runs Uvicorn (`workers=1`, on 127.0.0.1) with the runner threads and APScheduler in the lifespan, under an exclusive `nextron.lock`. Exit code 2 means a startup check failed and 3 means the lock is held. The supervisor relies on both.
- **Persistence:**
  - Only `db/engine.py` configures SQLite (WAL, `BEGIN IMMEDIATE`).
  - Each entry point opens one unit of work. Owner commands join it and never commit. No transaction spans a network call: prepare, then the port call, then apply.
  - Each table has one owner and is written only through that owner's repository. Every column must appear in `owners.py` and `pii_registry.py`.
  - Effects that go up the DAG use only the closed list of notifications, with subscribers registered in `main.py`.
  - Alembic runs as one linear chain with revision ids `NNNN_<story-key>`, and each story adds its own migration.
  - Statuses are closed enums with transition tables in `domain/<machine>.py`. Each transition appends a row to the append-only, personal-data-free `events` log.
- **Jobs:**
  - `jobqueue.enqueue` runs inside the transaction that makes the work necessary. Idempotency keys follow `<type>:<scope>:<discriminator>`, and payloads are versioned and hold only ULIDs and scalars.
  - Jobs run on three lanes (`realtime`, `interactive` and `bulk`) with renewed leases and backoff until `dead`. A job deferred by the budget re-queues without using a retry.
  - Triggers come from a closed enum and only enqueue. Handlers are thin.
- **Settings:** bootstrap config holds only `NEXTRON_ENV`, the data dir, the backup dir, the port and the Playbook path. Every other key is declared once in `domain/settings_schema.py` and read through `queries.setup.get(Key.X)`.
- **Secrets and OAuth:** `SecretsPort` stores secrets through keyring under `nextron-engine/<env>`. Google grants use a loopback flow with PKCE that only a POST can start. Refresh tokens go in keyring and access tokens stay in memory.
- **Dashboard:**
  - The Host header is checked. `nextron open` issues a one-time code that creates an in-memory `SameSite=Strict` session, and every request needs it.
  - State changes are POSTs with CSRF, `Origin` and `Sec-Fetch-Site` checks.
  - Every response carries a strict CSP and `X-Frame-Options: DENY`, and pages with personal data add `no-store`.
  - Autoescaping stays on, and `|safe` is allowed only from an allowlist. Handlers are sync `def`, and HTMX is vendored and hardened.
- **Environments:** `dev`, `test` and `prod` each get their own data dir (never under OneDrive), DB, keyring namespace and port. Outside `prod`, sink adapters wrap `mail` and `alert`. Tests never build live adapters, and only the logon task sets `prod`.
- **Metering:** `UsageMeterPort` is the only writer of `api_usage` and `ai_usage`, and it commits on its own. Money is stored in integer micro-dollars and Hunter credits doubled. Budget checks use `domain.budget.allow`.
- **Alerts:** `AlertPort` is the only channel to Nextron people. Telegram messages are plain text, and ops alerts carry IDs and counts only.
- **Backups:** copies use `sqlite3.Connection.backup()` and never copy the live file, and each copy is verified. Off-PC copies are encrypted and sent with the erasure ledger. Restoring needs a stopped engine and turns on recovery mode, and senders must check `queries.ops.recovery_mode()`.
- **Conventions:** ULIDs from python-ulid, UTC ISO 8601 `…Z` times with `zoneinfo` and `tzdata`, sync SQLAlchemy on pysqlite, typed `DomainError` and `AdapterError(retryable, code, message)`, and redacted JSON-line logs. `ulid-py`, `aiosqlite` and `pytz` are forbidden.

## UX & Interaction Patterns

- Pages are server-rendered with Jinja2 and HTMX partials, using only self-hosted assets, with no inline script or style.
- The base layout has navigation (Queue, Signal Inbox, Leads/Pipeline, Funnel, Settings, Health) and a notification area for notices, the health error count and the recovery-mode banner.
- A request without a session gets a page saying to run `nextron open`, with no data.
- Every action is a POST carrying CSRF. No GET has a side effect.
- Untrusted text is escaped, and only http and https links render.
- Settings and Credentials forms come from their schema or registry and are validated on the server. Credential values are never displayed; each shows "set" or "not set" and its last-updated time.
- The Quotas panel states that sending, replies and unsubscribe polling are never blocked.
- The Health panel refreshes every 30 s.

## Cross-Story Dependencies

- **Foundations:**
  - 1.1, 1.2 and 1.3 come before everything else.
  - `SecretsPort` from 1.4 feeds 1.7, 1.9, 1.12 and 1.17.
  - Every POST panel needs 1.5.
  - The `settings` table from 1.6 feeds 1.8, 1.10, 1.12, 1.13 and 1.16.
- **Chains:**
  - Jobs and health: 1.10 → 1.11 → 1.12 → 1.15.
  - Backups: 1.16 → 1.17 → 1.18 → 1.19.
  - The 1.8 sink must wrap 1.12's Telegram adapter.
  - Spike 1.20 must finish before 1.21.
- **Open registries for later epics:** doctor checks, health issues, recovery steps, `owners.py`, notification subscribers and the adapter contract list.
- **Undecided off-PC destination:** until it is decided, doctor warns and backups stay local.
