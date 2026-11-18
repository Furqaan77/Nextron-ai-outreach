---
title: 'Story 3.14: Erase personal data outside the database and issue a receipt'
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: '360768d69d669c458defdde61835c1f0e9ffe54a'
review_loop_iteration: 0
story_key: '3-14-erase-personal-data-outside-the-database-and-issue-a-receipt'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-3-13-erase-a-contact-on-request.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Story 3.13 tombstones a Contact inside the database, but AD-17 says an erasure must reach every store that may hold the Contact's data (Telegram, backups, the dev/test sink files, logs, the restore work copies, the ledger, Anthropic, Worker KV, the nextronai.com inbox, the Sending Inbox mailboxes) and must produce a receipt that tells the requester what was erased and what remains. Nothing records that today, and a failed erasure leaves no trace on the health panel.

Requirements: FR-52 · AD-10, AD-15, AD-17 · NFR-3, NFR-17.

**Approach:** Migration `0029_3_14_erasure_receipts` adds `erasure_receipts` and its per-store rows. Inside `erase_contact`'s transaction, after `ContactErased`, compliance writes one receipt (the database items worked out in the transaction, the external stores `pending`), links it to the request, and enqueues one `erase_external` job per external store. Each job runs its store's handler and settles its row. The Lead page and the erase page show the receipt; `queries.compliance.health_issues()` lists failed stores and erase requests left open.

The acceptance criteria are Story 3.14 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**
- **Pure domain: `nextron/domain/erasure_receipt.py`** (stdlib and `nextron.domain` only).
  - `ERASE_EXTERNAL_JOB = "erase_external"`.
  - **`ErasureStore`** (StrEnum, display order):
    - in-database items: `database`, `suppression`, `evidence_quotes`, `page_addresses`, `other_contacts`;
    - the AD-17 stores: `mailboxes`, `crawled_pages`, `telegram`, `anthropic`, `worker_kv`, `backups`, `ai_test_sets`, `nextronai_inbox`;
    - the engine's own files: `logs`, `sink_files`, `restore_work`, `erasure_ledger`.

    Each store has a `label` and an `action` (fixed text; see Design Notes).
    - `AD17_STORES` is the eight AD-17 stores.
    - `EXTERNAL_STORES` is the twelve AD-17 and file stores, each settled by a job.
    - `STORE_NUMBERS` is a fixed `{store: 1..12}` for the external stores, in that order. It is pinned by a test, and a number is never reused.
  - **`StoreStatus`:** `pending`, `done`, `residual`, `none_held` and `failed`, with `pending` the only non-terminal status. `TRANSITIONS`: `pending` can go to each terminal status (cause `settled`); anything else raises `IllegalTransition`.
  - **`ReceiptOutcome`:** `erased` or `already_erased`.
  - **`receipt_state(statuses)`:**
    - `incomplete` when any status is `failed` (label "Incomplete: N failed");
    - else `in_progress` when any is `pending` ("In progress");
    - else `complete` ("Complete").
  - **`StoreResult(status, detail)`:** `detail` is a stripped text of 1–1000 characters with no `@` and no control character, else `ValueError`. Its messages never echo the value.
  - **`mentions_name(name, text)`:** every word of the folded name (NFKC, casefold, non-word runs to spaces, as `normalize_name` does but with no length limit) appears as a whole word of the folded text.
  - **`url_mentions_name(name, url)`:** the same test on the URL's percent-decoded path words, or the name's words joined with nothing appear as one path word. An empty name never matches.
  - **Detail builders** give every status and text in the I/O matrix and Design Notes. Every detail holds counts, dates (`YYYY-MM-DD`), ULIDs and codes only.
- **Migration `0029_3_14_erasure_receipts`** (down `0028_3_13_erase_contacts`; `downgrade()` raises).
  - **`erasure_receipts`** (owner `compliance`):
    - columns: `id`; `contact_id` (FK `contacts`, indexed); `request_ref` (200, nullable); `cause` (`dsr`/`retention`); `outcome`; `created_at`;
    - CHECK `(cause='dsr') = (request_ref IS NOT NULL)`, plus the length and enum CHECKs;
    - triggers: `no_delete`; `immutable` (every UPDATE aborts).
  - **`erasure_receipt_stores`** (owner `compliance`):
    - columns: `id`; `receipt_id` (FK, indexed); `store`; `action` (300); `status`; `detail` (Text); `at`;
    - UNIQUE `(receipt_id, store)`;
    - CHECKs: store in the 17, status in the five, `length(detail)` 1–1000, `instr(detail,'@')=0`;
    - triggers:
      - `no_delete`;
      - `insert`: aborts an INSERT whose `id` or (`receipt_id`, `store`) exists;
      - `settle`: an UPDATE aborts unless `OLD.status='pending'` and `NEW.status<>'pending'`, with `id`, `receipt_id`, `store` and `action` unchanged.
  - **`trg_data_subject_requests_receipt`** aborts setting `receipt_id` to a receipt that doesn't exist or belongs to another Contact. It is a trigger, not a foreign key, so there is no table rebuild.
  - **Models, `owners.py` and `pii_registry.py`:**
    - `erasure_receipts.request_ref` is `redact`;
    - every other new column is `none`. The details are code-built and checked to hold no `@`.
- **Repositories, `db/repos/compliance/erasure_receipts.py`:**
  - writers: `insert_receipt(...)`, which inserts the receipt and its 17 rows; `settle(contact_id, store, status, detail, at) -> int`, which updates only that Contact's `pending` rows of that store;
  - readers: `pending_count(contact_id, store)`, `receipts_for(contact_ids)` (each with its rows), `failed_rows()` and `open_erase_requests()`;
  - plus `data_subject_requests.set_receipt(request_id, receipt_id)`.
- **`Erasures(clock, compliance, *, queue=None)`.** `erase_contact` raises `RuntimeError` before reading anything when `queue` is `None` (fail closed). The hook-only instance in `build_subscribers` passes none.
  - **Before `publish`** (a new Contact), `erase_contact` works out the in-database items from the plaintext:
    - `suppression` from `person_keys` and the Lead's web domain;
    - `evidence_quotes`: Evidence on the Lead and on every Lead merged into it, with `person_contact_id` not this Contact, whose `quote` or `value` `mentions_name`;
    - `page_addresses`: the distinct `url`, `final_url` and Evidence `source_url` values of those Leads that `url_mentions_name`;
    - `other_contacts`: the ids, sorted, of the other Contacts that aren't erased, on any Lead, whose address key or profile key equals this one's.
  - **After the completion**, it does three things in this transaction:
    1. writes the receipt (outcome `erased`; the database item `done`; the external stores `pending`; one `events` row: entity `erasure_receipts`, `None → written`, cause the outcome, `cause_ref` the Contact, actor `operator` for `dsr` and `system:compliance` for `retention`);
    2. sets the request's `receipt_id`;
    3. enqueues `erase_external` per external store: scope the Contact, discriminator `str(STORE_NUMBERS[store])`, payload `{"v":1,"contact_id":<ULID>,"store":<code>}`, which gives the key `erase_external:<contact_id>:<n>`.
  - **Already erased** with a request: the same steps with outcome `already_erased`, and the in-database items as Design Notes says. A retention call on an erased Contact writes nothing.
  - `ErasureResult` gains `receipt_id: str | None = None`, as its last field. `erase_on_request` logs it too.
- **`ErasureStores(clock, notify, sink)` in `nextron/app/compliance/erasure_stores.py`.**
  - **`@use_case run(contact_id, store, *, last_attempt)`:**
    1. In one transaction, it returns if `pending_count` is 0 (idempotent). Otherwise it reads the Contact and the handler's `prepare(contact, now)`.
    2. Outside any transaction, it calls `act(plan) -> StoreResult`.
    3. In a last transaction, it calls `settle` and appends one `events` row per settled row: entity `erasure_receipt_stores`, `pending → <status>`, cause `settled`, `cause_ref` the receipt, actor `system:compliance`.

    A retryable `AdapterError` propagates, so the job retries, unless `last_attempt`. Any other exception, or any error on the last attempt, settles `failed` with "Failed: <code>" (`jobqueue.error_code`) and logs `erasure_store_failed` (`contact_id`, `store`, `code`).
  - **`handlers`** is a read-only `{store: handler}` for exactly `EXTERNAL_STORES`. `MAILBOX_PLACEHOLDER` is the `mailboxes` handler (`none_held`).
- **The Telegram path.**
  - `queries.notify.alerts_about(subject)`: the `telegram` alerts whose `subject` is the Contact's ULID.
  - `AlertPort.delete(recipient, message_id) -> bool`: `True` when deleted, `False` when Telegram refuses (400).
    - `TelegramAlert.delete` POSTs `deleteMessage` and is metered as one `ApiCall("telegram","delete_message")`.
    - A 429, a 5xx or a transport error is a retryable `telegram_unavailable`; any other status is `telegram_refused`.
  - `AlertSink.delete` passes to `wrapped`. The `InMemoryAlert` fake records deletes.
  - `Notify.delete_message(chat_id, message_id) -> bool` is a plain port call.
  - **The handler.**
    - A `sent` alert with a message id and an `updated_at` under 48 h old is deleted (`alert_chat_id` is read in `prepare`; an unusable setting settles `failed`).
    - Any other `sent` alert counts as "older than 48 hours". An `unknown` or `pending` alert counts as "may have been delivered without an id".
    - A `failed` alert never reached Telegram and is ignored.
    - The store is `none_held` with no alerts, `done` when every alert was deleted, and `residual` otherwise. The detail gives each count. A retry that repeats a delete counts it as refused (the conservative direction).
- **The sink path.** `nextron/ports/sink.py`: `SinkFilesPort.erase_recipient(address_hash) -> SinkErasure(files, lines)`.
  - `SinkRecorder` implements it under `_WRITE_LOCK`. In each `*.jsonl`, a `mail` line whose `tombstone_hash("address", recipient)` equals the hash gets `recipient`, `subject` and `text` set to `[erased]`; `time` and `port` are kept.
  - A changed file is written to `<name>.erasing`, fsynced, then `os.replace`d. A leftover `.erasing` is deleted first. An `OSError` raises a retryable `AdapterError("sink_erase_failed")`.
  - A fake goes in `PORT_FAKES`. `main` passes `None` in `prod`.
- **The `erase_external` job type:** lane `bulk`; priority 60; `max_runtime` 5 min; retry limit 5; group `OTHER`.
  - Its handler, `handlers.erase_external(stores)`, checks that `contact_id` is a ULID and `store` is in `EXTERNAL_STORES` (else `InvalidJob`), then calls `stores().run(contact_id, store, last_attempt=job.attempts > ERASE_EXTERNAL_RETRIES)`.
  - `build_job_types(..., erasure_stores=None)`: when it is unwired, the handler raises a non-retryable `RuntimeError`.
- **Health.** `queries.compliance.health_issues()`, registered last in `main.build_health_issues` as `IssueSource("erasure", ...)`, returns, oldest first:
  - one `erasure_open` issue per `open` erase request (subject the request; "Erasure request still open: Contact <id>; erase it again from the Lead page");
  - then one `erasure_failed` issue per `failed` row (subject the receipt; "Erasure failed: <label>, Contact <id>, receipt <id>"; detail the row's detail).
- **Web.**
  - **The Lead page.** An erased Contact's tombstone row is followed by its receipts, oldest first, through the partial `erasure_receipt.html`. Each receipt shows a heading "Receipt <YYYY-MM-DD>: <state label>" and one line per store: label, status label, detail and time.
  - **The erase page.** Its GET for an erased Contact adds the same receipts. The POST success panel and the "already erased" panel link to the Lead page's receipt.
- **Tests** cover every matrix row on real units of work, the triggers in raw SQL, and the contracts (Tasks). `events`, payloads and logs hold no personal data.

**Never:**
- No new dependency, setting or migration other than `0029`, and no change to `ContactErased` or to 3.13's tombstone behaviour.
- No retry or acknowledge action for a failed store, no ledger (3.16), no export (3.15) and no mailbox handler (6.7).
- No personal data in a receipt, payload, event or log. No deleted row.
- No edits outside `nextron-engine/` except this spec, `sprint-status.yaml` and `deferred-work.md`.

### Decisions made overnight (pending user review)

1. **The stores are child rows** (`erasure_receipt_stores`), not a JSON column, so DB triggers can make each one settle once.
2. **`pending` is a fifth status.** It applies before a store's job runs. The AC's four are the terminal ones.
3. **The key's discriminator is the store's fixed number.** AD-10's binding key grammar allows a revision, a date or `tick`, so `erase_external:<contact_id>:<n>`. The payload carries the store's code, a closed token. It holds no personal data and no other text.
4. **The jobs are per Contact and store,** not per receipt. A job settles every `pending` row of its store on that Contact's receipts, so a repeat request's job (or one absorbed by a live one) never strands a row.
5. **The engine's own files are stores too:** logs, sink files, restore work copies and the ledger. The 3.13 residuals are in-database items worked out at erase time.
6. **A repeat request** gets its own receipt (`already_erased`). It re-runs the external stores, so the receipt is true now, and its in-database items say "already erased on <date>".
7. **The backups text is true today:** "a restore brings the Contact back until the erasure ledger exists; erase it again after a restore". Story 3.16 switches it to the AC's "erasure re-applied after any restore".
8. **Crawled pages is `none_held`** when the Lead had no stored pages. **AI Test Sets is `residual`,** as the AC says.
9. **A failed store stays on the health panel.** Retrying or acknowledging one is deferred.
10. **`build_job_types(erasure_stores=None)` and `Erasures(queue=None)` are optional.** This keeps the ~27 test call sites unchanged and avoids merge conflicts with main's 4.5. Both fail closed when unwired.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| DSR erasure | Contact with name, email, LinkedIn; Lead with web domain | one receipt (`erased`, ref), 17 rows: database `done`, suppression `done` ("3 keys …"), 12 external `pending`; request `receipt_id` set; 12 `erase_external` jobs, payload `{v, contact_id, store}`, keys `erase_external:<id>:1..12` | N/A |
| Jobs run | fakes; no alerts; no sink match; one pre-erasure backup | mailboxes `none_held`; crawled_pages `done` (N pages); telegram `none_held`; anthropic, worker_kv, backups (count + age-out dates), ai_test_sets, nextronai_inbox `residual`; logs, restore_work, erasure_ledger `none_held`; sink `none_held`; receipt "Complete" | N/A |
| Telegram | alerts about the Contact: sent 1 h ago (id), sent 3 days ago, `failed` | one `delete` call; `residual` "1 deleted; 1 older than 48 hours remains (Telegram message older than 48 hours)"; all deleted → `done`; refused (False) → `residual` | retryable error → retry; last attempt → `failed` |
| Sink | dev; two lines to the address, one to another | 2 lines `[erased]`, other untouched; `done` "2 messages in 1 file"; prod → `none_held` | locked file → retry, then `failed` |
| Residuals | Evidence quote with the full name, no `person_contact_id`; page `/people/jane-doe`; same address on another Lead; Lead without web domain | evidence_quotes, page_addresses, other_contacts (ids listed) and suppression ("no person key …") `residual`; none found → `none_held` | N/A |
| Retention | `cause="retention"` | receipt without ref; suppression `none_held`; jobs as DSR | N/A |
| Repeat | erase an erased Contact with a new ref | new receipt `already_erased`; database item `none_held` "Already erased on <date>"; jobs re-run | N/A |
| Failure | handler raises non-retryable | row `failed`, receipt "Incomplete: 1 failed", `erasure_failed` health issue | logged `erasure_store_failed` |
| Open request | erasure rolled back | `erasure_open` health issue until completed | N/A |
| Raw SQL | delete/update a receipt; re-settle a row; status back to `pending`; detail with `@`; foreign `receipt_id` | aborted | `IntegrityError` |

</frozen-after-approval>

## Code Map

- `nextron/app/compliance/erasure.py`: `Erasures.__init__`, `erase_contact` (lines 209–240), `ErasureResult`, `erase_on_request` (log), `ERASURE` (add `erasure_receipts`: `kept`, "the proof of erasure; its reference holds no address or name"); module docstring.
- `nextron/app/compliance/api.py`: module docstring only.
- `nextron/domain/erasure.py` (`REDACTED`, `ErasureCause`, `tombstone_hash`), `data_subject_request.py`, `contactability.py` (`person_keys`, `erased_person_keys`, `SuppressionKey`, `KeyKind`), `identity.py` (`normalize_name` folding at line 377), `machines.py` (`Transition`, `IllegalTransition`).
- `nextron/domain/job.py`: `idempotency_key` rules (lines 363–380); payload token rule. `nextron/jobs/handlers.py`: the `enrich_lead` last-attempt pattern; module docstring.
- `nextron/main.py`:
  - `build_app` (line 838; `Erasures(...)` at 992). Build `ErasureStores(clock, notify, sink)`, with `sink = SinkRecorder(config.data_dir / SINK_DIR_NAME, clock)` unless `prod`. Pass `queue` to `Erasures`.
  - `build_job_types` (1028).
  - `build_health_issues` (1183).
  - `build_subscribers` (1478, which stays hook-only).
  - The module docstring.
- `nextron/app/jobqueue/api.py`: `enqueue` (277) and `error_code` (174).
- `nextron/app/notify/api.py` (`Notify._port`, `send`). `nextron/app/queries/notify.py` and `db/repos/notify/alerts.py`: add `alerts_about`. A `sent` alert's `updated_at` is its send time; `sent` is terminal.
- `nextron/ports/alert.py`; `nextron/adapters/telegram/__init__.py` (copy `_post`/`_delivery`'s error mapping); `nextron/adapters/sink/__init__.py` (`SinkRecorder`, `_WRITE_LOCK`); `tests/fakes/alert.py`; `tests/fakes/__init__.py` `PORT_FAKES`.
- Queries:
  - `nextron/app/queries/ops.py` + `db/repos/ops/backups.py`: add `copies_holding(since, until)`, the `verified` rows with `started_at` in [since, until] whose local copy isn't deleted (`local_deleted_at` NULL) or whose off-PC copy is `copied` and not deleted.
  - `domain/backup.py`: `OFFPC_ROTATION_DAYS`, `DEFAULT_ROTATION_DAYS`. `Key.BACKUP_ROTATION_DAYS`: an unreadable value counts as 35.
- `nextron/app/queries/research.py`: `evidence(lead_id)`, `crawled_pages(lead_id)` and `contact`. Add `other_contacts_with_keys(contact_id, keys)`, which scans the Contacts that aren't erased and have an email or LinkedIn URL. `queries.discovery.merged_lead_ids` and `lead`.
- `nextron/app/queries/compliance.py`: add `receipts(contact_ids)`, `health_issues()`. `db/repos/compliance/data_subject_requests.py`: `set_receipt` and `open_erase_requests`.
- Models and registry: `nextron/db/models/compliance.py` (+ `models/__init__.py`), `db/owners.py`, `db/pii_registry.py`. Copy the trigger style of `versions/0028_3_13_erase_contacts.py`.
- Web: `nextron/web/leads_page.py` (`lead_context`, `contact_row`, `tombstone_line` at 586), `templates/lead.html` (line 78), `nextron/web/erase_page.py`, `templates/contact_erase_panel.html`, and a new `templates/erasure_receipt.html`.
- Tests to copy: `tests/app/test_erasure.py` (the World and the raw SQL), `tests/contracts/test_erasure_contracts.py` (the probe style) and `tests/unit/test_erase_page.py`.
- Pins to update:
  - HEAD: `tests/contracts/test_persistence_contracts.py:81` plus its chain probes, `tests/unit/test_migrations.py:39`, `tests/app/test_serve_process.py:324`, `tests/migration_chains.py`;
  - `tests/contracts/test_source_tree.py` `FILES`;
  - `tests/contracts/test_health_contracts.py:123` `SOURCES`;
  - the job-type pins: `tests/contracts/test_job_contracts.py`, `tests/unit/test_runner_lifespan.py`, `tests/app/test_engine_health.py` (`LastRun` lists) and `tests/unit/test_health_page.py`;
  - `test_web_contracts` (templates);
  - `test_sink_contracts` (`AlertSink.delete`);
  - `tests/dashboard.py` (`Erasures` with a queue).

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/erasure_receipt.py` -- the stores, statuses, machine, numbers, state, `StoreResult`, matchers and detail builders -- the pure rules.
- [x] Migration `0029_3_14_erasure_receipts.py`, the models, `owners.py`, `pii_registry.py` -- the schema and triggers.
- [x] Repositories: `erasure_receipts.py` and the `data_subject_requests` additions; the queries in `ops`, `research`, `notify` and `compliance`.
- [x] `nextron/ports/sink.py`, `SinkRecorder.erase_recipient`, `AlertPort.delete`, `TelegramAlert.delete`, `AlertSink.delete` and `Notify.delete_message`; the fakes.
- [x] `nextron/app/compliance/erasure_stores.py` -- `ErasureStores` and the 12 handlers; `erasure.py` -- the receipt, the jobs and the in-database items.
- [x] `nextron/jobs/handlers.py` `erase_external`; `nextron/main.py` -- the job type, the health source, the wiring and the docstring.
- [x] Web: the partial, the Lead page and the erase page.
- [x] Tests:
  - `tests/unit/test_erasure_receipt_domain.py`: machine, numbers, state, matchers, builders;
  - `tests/app/test_erasure_receipts.py`: every matrix row on real units of work, the triggers, the job keys and payloads, the handlers on fakes, the health issues, and no personal data in receipts, `events`, payloads or logs;
  - `tests/app/test_sink_erasure.py` on a temp dir;
  - the Telegram `delete` adapter tests on an `httpx2.MockTransport`;
  - web tests for the receipt on both pages.
- [x] `tests/contracts/test_erasure_contracts.py`, each check with a failing probe:
  - `ErasureStores(...).handlers` keys equal `EXTERNAL_STORES` exactly, and `AD17_STORES` is a subset;
  - while `"threads"` is a table in `Base.metadata`, the `mailboxes` handler must not be `MAILBOX_PLACEHOLDER`, and `MailboxPort` must have `trash_thread`;
  - every external store has a unique `STORE_NUMBERS` entry.
- [x] The pin updates in the Code Map; a `nextron-engine/README.md` section for Story 3.14; and `deferred-work.md` entries: retry or acknowledge a failed store; alert kinds about a Contact must use its ULID as subject.

**Acceptance Criteria:**
- Given the migration at head, when the schema is read, then both tables, their checks, indexes and triggers exist, and `owners.py` and `pii_registry.py` cover every column.
- Given `tests/contracts` and `lint-imports`, when they run, then every contract holds and each new probe fails as intended.

## Design Notes

**Details (fixed texts; counts, dates and ids filled in):**

| Store | Status and detail |
|---|---|
| database | `done` "The Contact's name, role, address, LinkedIn URL and office, its Provenance Records, the Evidence linked to it and the void reasons on its keys were replaced by tombstones; no row was deleted" |
| suppression | `dsr`: `done` "N keys (address, person, profile) stay suppressed"; plus `residual` "…; no person key, because the Lead has no web domain: a later import of this person by name alone on this Lead isn't blocked" when the Contact has a name and no `person` key was formed (with a web domain, "because the Contact's name and the Lead's web domain don't form one"; review pass 1); no key at all → `residual`. `retention` → `none_held` "Retention writes no suppression entry" |
| evidence_quotes / page_addresses | `residual` "N … name the person and were kept (the firm's public pages)" / `none_held` "None contains the person's full name"; no name → `none_held` |
| other_contacts | `residual` "N other Contacts share this address or LinkedIn URL and were not erased: <ids>" / `none_held` |
| crawled_pages | `done` "Text of N pages nulled, content hash kept; a later crawl stores public page text again for at most 30 days" when every page's text is NULL; `residual` "N pages nulled, content hash kept; M pages crawled since hold public page text again, kept at most 30 days" when one holds text; no page → `none_held` (review pass 1) |
| backups | always `residual`: "N local and M off-PC copies taken before the erasure hold the Contact; they age out between <d1> and <d2>, and every copy taken before the erasure ages out by <erased_at + 35 days>, while daily backups keep running. Copies saved by `nextron restore` or left by a failed migration are kept until deleted by hand. A restore brings the Contact back until the erasure ledger exists: erase it again after a restore." Age-out is local `started_at` + rotation days, or off-PC `started_at` + 35. With no recorded copy between the Contact's `created_at` and `erased_at` (a backup still running isn't recorded yet): "No recorded backup copy holds the Contact; every copy taken before the erasure ages out by <erased_at + 35 days>, while daily backups keep running." and the same last two sentences (review pass 1) |
| anthropic / worker_kv / ai_test_sets / nextronai_inbox | the AD-17 text, e.g. "Worker KV: token and time only; expires within 30 days" |
| logs / restore_work / erasure_ledger | `none_held`: "Logs hold ids and codes only; email addresses are masked when written"; "Decrypted work copies are deleted when each restore test or restore ends, and a crash's leftover at the next daily check"; "The ledger holds ids and hashes only" |

For `already_erased`, the database item is `none_held`, "Already erased on <date>; see that erasure's receipt", and suppression for a DSR is worked out from `erased_person_keys`. Evidence quotes, page addresses and other Contacts copy the status of the same row on the Contact's first `erased` receipt, with "As at the erasure on <date>: <its detail>" (or "As at the erasure on <date>: <status label>; see that erasure's receipt" when that wouldn't fit 1,000 characters); with no earlier `erased` receipt they are `residual` "Erased on <date>, before receipts were kept: what remains here wasn't recorded" (review pass 1).

## Implementation Notes

- **Domain** (`nextron/domain/erasure_receipt.py`). `ErasureStore` (17, display order, `label`
  and `action` from `STORE_LABELS`/`STORE_ACTIONS`), `DATABASE_STORES`, `AD17_STORES`,
  `FILE_STORES`, `EXTERNAL_STORES`, the literal `STORE_NUMBERS` (1–12), `StoreStatus` (with
  `label`), `TERMINAL_STATUSES`, `StoreCause.SETTLED`, `TRANSITIONS` and `transition(current, to)`
  (the event is the target status), `ReceiptOutcome`, `ReceiptState` and `receipt_state(statuses)
  -> ReceiptReading(state, failed)` whose `label` is the heading text. `StoreResult` checks its
  status type and its detail (`check_detail`). `folded_words`, `mentions_name`,
  `url_mentions_name` (path only, percent-decoded with `errors="replace"`; an unsplittable URL is
  `False`). The time rules `is_recent_message(sent_at, now)` (under 48 h) and `age_out(started_at,
  days)` live here, not in the app (see the contract note below). Detail builders: `pending`,
  `database_done`, `already_erased`, `suppression`, `evidence_quotes`, `page_addresses`,
  `other_contacts` (at most 30 ids listed, then "and N more"), `mailboxes_none`, `crawled_pages`,
  `telegram`, `backups`, `sink_files`, `sink_in_prod`, `sink_no_address`, `FIXED_RESULTS` (the
  AD-17 and file texts) and `failed(code)` (a code that isn't 1–64 of `[A-Za-z0-9_.:-]` shows as
  `error`). Telegram's counts are deleted, "that Telegram would not delete", "older than 48 hours"
  (with the AC's parenthesis) and "that may have been delivered without an id"; the no-key
  Suppression text is `NO_KEY_DETAIL`; the backup none-held text keeps the last two sentences.
- **Migration `0029_3_14_erasure_receipts`** (down `0028_3_13_erase_contacts`). Check names:
  `ck_erasure_receipts_request_ref`, `_dsr_ref`, `_cause`, `_outcome`;
  `ck_erasure_receipt_stores_store`, `_status`, `_detail`, `_detail_address`
  (`instr(detail, '@') = 0`); unique `uq_erasure_receipt_stores_receipt_id_store`; both
  `contact_id`/`receipt_id` indexed. Triggers as specified; the settle trigger's message is "each
  store of an erasure receipt settles once" (no apostrophe), and
  `trg_data_subject_requests_receipt` fires only when `receipt_id` changes to a non-NULL value
  (3.13's trigger already refuses changing a set one). The models mirror it (the check SQL is
  identical, which `test_job_contracts` compares).
- **Repositories.** `erasure_receipts.py`: `NewStore`, `StoredReceipt` (`request_ref` hidden from
  the `repr`, `stores` in `ErasureStore` order), `StoredReceiptStore`, `FailedStore`;
  `insert_receipt`, `settle`, `pending_count`, `receipts_for`, `failed_rows`, plus
  `pending_rows(contact_id, store)` (so each settled row gets its `events` row).
  `data_subject_requests`: `set_receipt -> int` (only while `receipt_id` is NULL; the app raises
  `RuntimeError` unless one row changed) and `open_erase_requests()`. `ops/backups.copies_holding`,
  `notify/alerts.about(subject, channel)`, `research/contacts.with_address_or_profile()`.
- **Queries.** `queries.compliance.receipts`, `health_issues` (`erasure_open` has no detail);
  `queries.ops.copies_holding`; `queries.notify.alerts_about`; `queries.research.evidence_history`
  (every Evidence row, live or not: a superseded quote is still stored) and
  `other_contacts_with_keys` (address and profile keys only).
- **Ports and adapters.** `nextron/ports/sink.py` (`SinkFilesPort`, `SinkErasure`,
  `SINK_ERASE_FAILED`), `tests/fakes/sink.py` (`InMemorySinkFiles`) in `PORT_FAKES`.
  `AlertPort.delete`; `InMemoryAlert.deleted`/`delete_outcomes`. `TelegramAlert.delete`: token
  and chat checks shared with `send`; a message id that isn't 1–19 digits without a leading zero is
  `telegram_bad_message_id` (non-retryable, no request); 2xx with `{"ok": true, "result": true}`
  is `True`, any other 2xx or a 400 is `False`; 429/5xx and any exception from the request are
  retryable `telegram_unavailable` (a delete is idempotent); other statuses `telegram_refused`;
  one `ApiCall("telegram", "delete_message")` per attempted request. `AlertSink.delete` passes
  through with no allowlist read. `SinkRecorder.erase_recipient`: only `mail` lines; a line that
  isn't a JSON object is kept byte for byte; each line keeps its own ending; a folder that doesn't
  exist is `SinkErasure(0, 0)`; any `OSError` is the retryable `sink_erase_failed`.
  `Notify.delete_message` is a plain port call.
- **`Erasures`** (`queue=None` keyword). `erase_contact` checks the queue first; the in-database
  items are worked out (`_database_items`) before `publish`, from the plaintext Contact and its
  Lead (`LookupError` for an unknown Lead); the receipt (`_receipt`) is written after the request
  is completed, then `set_receipt`, then the 12 `enqueue`s. For an already-erased Contact with a
  request: `_already_erased_items` (Suppression from `erased_person_keys`). `ERASURE` adds
  `erasure_receipts` (`kept`). `erase_on_request` logs `receipt_id`.
- **`ErasureStores`** (`nextron/app/compliance/erasure_stores.py`). Handlers: `MAILBOX_PLACEHOLDER`
  (a module-level `_Fixed`), `_CrawledPages`, `_Telegram`, `_Backups` (local copies age out at
  `started_at` + `backup_rotation_days`, 35 when unreadable or not a positive int; off-PC at
  `started_at` + `OFFPC_ROTATION_DAYS`), `_SinkFiles` (an erased Contact's `email` is already its
  address hash; `None` sink is `prod`), and `_Fixed` for the seven fixed results. `run` returns
  the settled status (or `None` when nothing was pending), validates the store and the ULID before
  any read, and logs `erasure_store_failed` after the settle commits.
- **Job, wiring, health.** `handlers.erase_external`, `ERASE_EXTERNAL_RETRIES = 5`,
  `StoreErasing`; `build_job_types(..., erasure_stores=None)` registers `erase_external` last
  (unwired: `_no_erasure_stores` raises `RuntimeError`); `build_health_issues` registers
  `IssueSource("erasure", compliance_queries.health_issues)` last; `build_app` builds
  `SinkRecorder(<data dir>\sink, clock)` unless `prod` (`sink_applies`), `ErasureStores(clock,
  notify, sink)` and `Erasures(..., queue=queue)`. `build_subscribers` stays hook-only.
- **Web.** `erase_page.py`: `StoreLine`, `ReceiptView`, `receipt_view`, `receipt_anchor`,
  `receipt_href`; `Notice.receipt_href`/`receipt_label` ("See the receipt on the Lead page");
  `erased(ref, receipt)` and `already_erased(receipt)`; `panel_context(..., receipts=...)`. The
  erase answers (GET and POST) read the receipts of an erased Contact, so the POST success panel
  shows them too; its link uses the survivor's id. `leads_page`: `ContactRow.receipts`,
  `contact_row(..., receipts=...)`, `lead_context(..., receipts=...)`. `lead.html` adds a
  `receipt-row` after the tombstone row (its own class, so the Contact still has no
  `provenance-row`); `erasure_receipt.html` is a `section` (`id="receipt-<id>"`, `hx-disable`)
  with a `receipt-table`. `app.css` gains four rules, appended in the file's trailing LF style.
- **Contract change.** `tests/contracts/test_suppression_contracts.py`'s permanence check covers
  all of `app/compliance/`, so the Telegram store's `delete_message` call failed it. It now has
  `ALLOWED = {("app/compliance/erasure_stores.py", "calls", "delete_message")}` with a probe test
  that the same call elsewhere still fails; the date arithmetic moved to the domain instead.
- **Tests.** New: `tests/unit/test_erasure_receipt_domain.py`, `tests/app/test_erasure_receipts.py`
  (the jobs run through the registry's handler with `JobQueue.claim`/`complete`),
  `tests/app/test_sink_erasure.py`, the `delete` tests in `tests/unit/test_telegram_adapter.py`,
  `AlertSink.delete` in `tests/unit/test_sink.py` and `tests/contracts/test_sink_contracts.py`
  (every `AlertPort` member wrapped, with a probe), the receipt tests in
  `tests/unit/test_erase_page.py`, and the three store contracts in
  `tests/contracts/test_erasure_contracts.py`. Updated: the HEAD pins and chain probes (0030/0031),
  `test_serve`'s `0030_more`, `test_source_tree` `FILES`, `test_web_contracts` templates,
  `test_test_isolation` ports, `test_health_contracts` sources, the job-type pins
  (`test_job_contracts`, `test_runner_lifespan`, `test_engine_health`, `test_health_page` ×2),
  `tests/dashboard.py`, and 3.13's `test_erasure.py` (a queue, `receipt_id` in results, the grown
  tables, `erasure_receipts.request_ref` in the not-planted set, the receipt-id permanence probe
  now on a request without a receipt) and `test_erase_page.py` (answers with links, receipts on
  the erased step).
- **Review patches (pass 1).**
  - `build_app`'s wiring is pinned by `test_build_app_wires_the_erasure_queue_stores_and_sink`
    (`tests/unit/test_runner_lifespan.py`, `test` and `prod`): `Erasures` gets the app's
    `JobQueue`, `ErasureStores` the `SinkRecorder` of `<data dir>\sink` (none in `prod`), and the
    registry's `erase_external` reaches that `ErasureStores`.
  - `ErasureStores.run`: before the last attempt, any exception that isn't an `AdapterError`
    (prepare, act or the settle transaction) is raised as the retryable `AdapterError`
    `erasure_store_retry` (`STORE_RETRY`, from the error), rows still `pending`; a
    non-retryable `AdapterError` still settles `failed` at once; on the last attempt every error
    of prepare or act settles `failed` with the original code, and a settle error propagates.
  - Backups are always `residual`, with the bound "every copy taken before the erasure ages out
    by <erased_at + 35 days>, while daily backups keep running" (`receipt.backups(...,
    erased_at=)`; `BACKUP_WINDOW_DAYS`); with no recorded copy: "No recorded backup copy holds
    the Contact; …".
  - Crawled pages count pages whose text is NULL and pages holding text again
    (`receipt.crawled_pages(nulled, kept)`): `residual` when any holds text.
  - A repeat request's Evidence quotes, page addresses and other Contacts copy the first `erased`
    receipt's rows (`receipt.as_at_erasure`, `receipt.unrecorded`, `COPIED_STORES` in
    `erasure.py`, read through `receipts_for`).
  - The Suppression List's "no person key" tail follows the keys formed (no `person` kind among
    them, whatever the reason); `has_web_domain` now only picks the reason
    (`NO_KEYED_NAME_TAIL` when the Lead has one).
  - The repeat answer reads "This Contact was already erased; nothing more was erased, and this
    request has its own receipt." (`ALREADY_ERASED_TEXT`).
  - Each receipt shows the request it answers under its heading (`ReceiptView.request`:
    "Request <ref>", "Retention", then ", already erased"; `<p class="receipt-request">`
    through `|plain` inside the `hx-disable` section).
  - `trg_data_subject_requests_receipt` aborts with its own message, "a request's receipt must
    exist and be its own Contact's"; 3.13's set-once probe now changes a receipt to another real
    receipt of the same Contact, so only 3.13's trigger answers it.
  - New tests: a non-retryable Telegram error, the retry before the last attempt (handler and
    settle), the Evidence branches (linked, retracted, a fact's value, a merged Lead's row and
    page), a page crawled since, a name that forms no person key, the copied residuals and the
    receipt-less repeat, the trigger's message, and the receipt's request line on the Lead page.
- **Known risk (not fixed).** A repeat request enqueued while a store's job is past its settle
  transaction but not yet completed is absorbed by that still-`running` job (same key), so the
  repeat receipt's row of that store stays `pending` ("In progress") until another erasure of the
  Contact. The window is the few milliseconds between the settle commit and the runner's
  `complete`.

- **Final verification (orchestrator).**
  - Full suite through `suite_lock.py` (`track-B`), on the patched tree: 12885 passed, 12 skipped.
  - `lint-imports`: 29 kept, 0 broken.
  - `deferred-work.md` gained three Story 3.14 entries:
    - retry or acknowledge a failed store;
    - alert kinds about a Contact use its ULID as subject;
    - the latent alert and Telegram cases (review #13).

## Spec Change Log

## Review Triage Log

**Pass 1:** 36 raw findings from three reviewers: blind hunter (BH, 17), edge-case hunter (EC, 15, two of them claim checks) and verification gap (VG, 3 gaps and 1 other). Deduplicated to 25 entries: 12 patch, 1 defer, 12 reject. No intent_gap or bad_spec, so no loopback.

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | VG1: `build_app`'s wiring is untested. Dropping `queue=queue`, `erasure_stores=...` or the sink recorder leaves every test green. | medium | `test_runner_lifespan` asserts only `job_types.names()`; no test spies on `Erasures`, `ErasureStores` or `SinkRecorder` in `build_app`. Each edit breaks every real erasure. | patch: a wiring test that spies on `main.Erasures`, `main.ErasureStores` and `main.SinkRecorder` in `build_app` (`test`, and `prod` for the sink being `None`), and runs the registry's `erase_external` handler through to the spied stores |
| 2 | VG2: a non-retryable `AdapterError` before the last attempt is never exercised. | medium | Only `AdapterError(True, …)`, a `DomainError` and a `RuntimeError` are raised in tests. Dropping the `retryable` check in `run` passes every test, and in production the row would stay `pending`. | patch: a test where `delete` raises `AdapterError(False, "telegram_refused", …)` with `last_attempt=False`: `failed`, "Failed: telegram_refused", an `erasure_failed` issue |
| 3 | VG3: four branches of the `evidence_quotes`/`page_addresses` items are never run: linked Evidence skipped, every Evidence row read (retracted too), a fact's `value` matched, merged Leads walked. | medium | The `World.evidence` helper always writes `value=None`, unlinked and live rows, all on one Lead. Each branch can be broken with every test green. | patch: a test with a linked row that names the person (not counted), a retracted row, a fact whose `value` names them, and an Evidence row and a page on a merged-in Lead (all counted); assert the exact counts |
| 4 | BH13: a short-lived database error (SQLite "database is locked" past `busy_timeout`) in the prepare transaction settles the store `failed` on attempt 1, for good. | medium | `run`'s `except Exception` settles `failed` at once. Only a retryable `AdapterError` retries. There's no retry or acknowledge path (decision 9), so the receipt stays "Incomplete". Long writers exist (deferred-work 2.2: a bulk import holding `BEGIN IMMEDIATE`). | patch: before the last attempt, any exception that isn't an `AdapterError` (prepare, act or the settle transaction) is raised as a retryable `AdapterError("erasure_store_retry")` from the error, so the job retries; the last attempt settles `failed` as now; a non-retryable `AdapterError` still settles `failed` at once |
| 5 | EC15: the backups store is `none_held` when no recorded copy falls in the window, but the AC says backups are `residual`. | low | `_Backups.prepare` returns `receipt.backups(local=0, …)`, which is `none_held`. The AC lists backups among the `residual` stores, and no overnight decision covers it (the Design Notes row was a planning slip). | patch: backups is always `residual` (see #6 for the text) |
| 6 | EC6 + BH14: a backup that started before the erasure and is still running when the job runs (or whose off-PC copy isn't recorded yet) isn't counted, so the receipt under-reports or says no copy is kept. | low | A `backups` row is written once, when the backup ends, and `backup_db` shares the bulk lane, so the two can overlap. `copies_holding` sees only finished rows, and off-PC only when `copied`. | patch: the detail adds the bound that covers any copy, "every copy taken before the erasure ages out by <erased_at + 35 days>, while daily backups keep running", in both the counted and the no-copy texts |
| 7 | BH4 + EC12: "Crawled page text" says "Text of N pages nulled" for every page of the Lead at job time, including pages re-crawled since (always so on a repeat request, where no hook runs) and pages of a Lead merged in after the erasure. | medium | `_CrawledPages.prepare` counts all pages and reads no text. Pages re-crawled before the bulk job, or before a repeat request, hold public page text again. | patch: count pages whose text is NULL as nulled; pages that hold text make the store `residual` ("M pages crawled since hold public page text again, kept at most 30 days"); `done` only when every page's text is NULL; `none_held` with no pages |
| 8 | BH3: a repeat request's receipt marks `evidence_quotes`, `page_addresses` and `other_contacts` `none_held`, so it can read "Complete" while those residuals still exist. For a Contact erased before receipts existed, "see that erasure's receipt" points at nothing. | medium | `_already_erased_items` sets all five to `receipt.already_erased(...)` (`none_held`). The first receipt's residual rows still describe the data. | patch: for those three, copy the status of the Contact's first `erased` receipt's row, with detail "As at the erasure on <date>: <its detail>" (or "As at the erasure on <date>: <status label>; see that erasure's receipt" when that doesn't fit 1,000 characters); with no earlier receipt, `residual` "Erased on <date>, before receipts were kept: what remains here wasn't recorded" |
| 9 | EC7: when the Lead has a web domain but the person key can't be formed (a name that doesn't normalise), the Suppression List item is `done` with no "no person key" note. | low | `suppression(..., has_web_domain=...)` decides the tail from the web domain, not from whether `person_keys` returned a person key. Direct correction. | patch: the residual tail applies when the Contact has a name and no `person` key was formed, whatever the reason |
| 10 | BH10: the "already erased" answer still says "nothing changed", though a repeat request now writes a receipt and re-runs the external stores. | low | `erase_page.already_erased`: "This Contact was already erased; nothing changed." Direct text fix. | patch: "This Contact was already erased; nothing more was erased, and this request has its own receipt." (README and docstrings too) |
| 11 | BH11: a receipt doesn't show which request it answers; with several receipts the Operator can't tell them apart. A printable copy for the requester is missing too. | low | `receipt_view` and `erasure_receipt.html` render only the date and state; `request_ref`, `cause` and `outcome` are read but not shown. | patch: under the heading, show "Request <ref>" (or "Retention") and "already erased" for that outcome. The printable copy is rejected: not in the AC |
| 12 | BH17: `trg_data_subject_requests_receipt` aborts with "data subject requests are permanent" when the receipt belongs to another Contact, which misleads. | low | Migration 0029 reuses `REQUESTS_PERMANENT`. Direct correction. | patch: its own message, "a request's receipt must exist and be its own Contact's" |
| 13 | EC11: an alert about the Contact keeps its `alerts.text` (`redact`) in the database, because notify's `ERASURE` declares `alerts` `not_held`. | low | Pre-existing (Story 3.13 decision 9): no alert kind names a person yet, so no such row exists today. | defer: the story that adds the first alert kind about a Contact also settles the latent Telegram-store cases in #19–#23 |
| 14 | EC1 + EC13 + BH1 (a): a repeat request enqueued between a running job's settle commit and the runner's `complete` is absorbed, so its row stays `pending` and the receipt reads "In progress" for ever. | low | Real but needs a repeat request inside a window of a few milliseconds, and repeat requests can't be made from the UI (the erase link is hidden once erased). The receipt errs toward "not complete". The fix needs a new healing path (a startup re-drive or a pending-without-job health check). | reject; reported for Story 3.16's startup work |
| 15 | EC2 + EC14 + VG other + BH1 (b): a job cut off (lease or shutdown) on its 6th attempt, or whose settle transaction fails on the last attempt, dies with its rows `pending`, and only jobqueue's dead-job issue shows it. | low | Needs five failed attempts first. The dead job shows on the health panel and raises `job_dead` until dismissed. The receipt reads "In progress", never a false "Complete". Same healing fix as #14. | reject; reported for Story 3.16 |
| 16 | EC8 + BH2: a repeat request's Suppression List item is worked out from the stored address hash only, so it says "1 key (address)" after a DSR that left three keys, or "no key" for a Contact without an address. | low | `_already_erased_items` uses `erased_person_keys`. Repeat requests are rare, the item lists the kinds it found, and the first receipt has the full count. The fix needs a new lookup by `cause_ref`. | reject |
| 17 | EC10 + BH5: the sink erasure reaches only `mail` lines addressed to the Contact, not recorded alert lines or mails to others naming the person. | low | Alert text carries ids only (AD-15; notify's `ERASURE`), and outreach mail goes to the Contact. A name scan isn't possible after the tombstone. | reject |
| 18 | EC9 + BH8: `page_addresses` doesn't scan Evidence `pages_checked`. | false | `pages_checked` holds the cited page's own URL (`research/extraction.py:519`, `(page.url,)`), which is a `crawled_pages.url` already scanned. | reject |
| 19 | EC3: an alert about the Contact still `pending` at erasure is sent later and never deleted. | low | Latent: no alert kind about a Contact exists. It is counted "may have been delivered without an id", so it is listed as a residual. | reject (noted in #13's deferral) |
| 20 | EC4: an alert resent after an unknown first send (`resent_at` set) may leave an undeleted first copy, unlisted. | low | Latent, as #19. | reject (noted in #13's deferral) |
| 21 | EC5 + BH15: the delete uses today's `alert_chat_id`. If the setting changed, the same message id is deleted in the wrong chat, or refused. | low | Latent, as #19. `alerts` stores no recipient. The fix needs a schema change in `notify`. | reject (noted in #13's deferral) |
| 22 | BH6: a sink-recorded alert (dev, `sent` with no message id) counts as "older than 48 hours". | low | Latent, as #19, and dev and test only. | reject (noted in #13's deferral) |
| 23 | BH7: one non-retryable delete error aborts the loop, and the messages already deleted aren't recorded. | low | Latent, as #19. A 401/403 would refuse every delete anyway. | reject (noted in #13's deferral) |
| 24 | BH9: matching needs every word of the stored name, so "Dr Jane A. Doe" misses a quote saying "Jane Doe" and the item reads "None contains the person's full name". | medium | True. But `mentions_name`'s rule (every word of the folded name) is in the frozen Always block, and the detail says "full name" truthfully. | reject: it would change the approved spec (reported) |
| 25 | BH12 + BH16: (a) main's Contacts panel is `lead_contacts.html` with 8 columns, so the receipt row must move there at merge; (b) `other_contacts_with_keys` scans every Contact in the write transaction and ignores the person key. | low | (a) isn't in this tree. (b) A few thousand Contacts hash in milliseconds, and a person key on another Lead has another web domain. | reject; (a) reported to the main session for the merge |

## Verification

**Commands** (from `nextron-engine/`):
- `uv run pytest -q tests/unit/test_erasure_receipt_domain.py tests/app/test_erasure_receipts.py tests/app/test_sink_erasure.py tests/app/test_erasure.py tests/contracts/test_erasure_contracts.py <touched test files>` -- all pass.
- `uv run pytest -q tests/contracts` -- all pass.
- `uv run lint-imports` -- every contract kept.
