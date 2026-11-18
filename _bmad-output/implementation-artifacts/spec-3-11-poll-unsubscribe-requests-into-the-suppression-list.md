---
title: 'Story 3.11: Poll unsubscribe requests into the Suppression List'
type: 'feature'
created: '2026-11-18'
status: 'done'
baseline_commit: '2bc36d85b44b2a32081567988717ab1d32cf63c6'
route: 'dispatch'
review_loop_iteration: 0
story_key: '3-11-poll-unsubscribe-requests-into-the-suppression-list'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-3-8-issue-and-decode-unsubscribe-tokens.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A prospect's one-click unsubscribe lands in the Cloudflare Worker's KV (Story 3.10), but nothing brings it back to the PC, so the opt-out never reaches the Suppression List. Requests made while the PC was off must still land, and nothing may be applied twice.

Requirements: FR-70, FR-41, FR-50 · AD-10, AD-15, AD-16, AD-20 · NFR-1, NFR-11, NFR-15, NFR-32.

**Approach:** An httpx2 `UnsubscribePort` adapter fetches `GET https://<host>/pending` with the bearer secret. A `poll_unsubscribes` job on the realtime lane applies every new entry in one transaction through a new `Compliance.unsubscribe_by_token` command. That command decodes the token, suppresses the address key and the keys of every Contact holding that address, and records the request in a new `unsub_requests` table, deduplicated by `token_hash`. The acceptance criteria are Story 3.11 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**
- **Domain (`nextron/domain/unsub_requests.py`, stdlib only):**
  - `PENDING_FORMAT = "nextron-unsub-pending/1"`.
  - `POLL_PERIOD_PROD` (5 min) and `POLL_PERIOD_OTHER` (30 min); `poll_period(env)`.
  - `is_worker_host(text)`: blank, or a lowercase ASCII DNS name with at least 3 LDH labels, the first exactly `u`, at most 253 characters, with no port, path or scheme, and not under `workers.dev`.
  - `UnsubOutcome`: `applied`, `duplicate` or `invalid`.
- **The port (`nextron/ports/unsubscribe.py`):** `UnsubscribePort.fetch_pending(host) -> PendingFetch(entries, malformed)`. `PendingUnsub(token: str, received_at: aware UTC datetime)` is frozen, and `token` has `repr=False`.
- **The adapter, `nextron/adapters/unsubscribe/__init__.py`:** `WorkerUnsubscribe(secrets, *, meter, http=None)`, modelled on `CompaniesHouseRegistry` and built by a guarded `default_http_client()`. Each call, in order:
  1. refuses a host that fails `is_worker_host` or is blank (`unsub_refused`, non-retryable, no request);
  2. reads `read_credential(secrets, "unsubscribe_bearer")` every time; a value shorter than 43 characters is `unsub_bearer_invalid` (non-retryable, no request);
  3. sends one GET to `https://<host>/pending` with `Authorization: Bearer <value>` and `Accept: application/json`, never following redirects, inside `Metered(meter, "unsub_worker", "kv_list")`.

  The answers map as follows:
  - 401 or 403: `unsub_unauthorized` (non-retryable).
  - 429, 5xx (a KV failure included) or a transport failure: `unsub_unavailable` (retryable).
  - Any other non-2xx status: `unsub_refused`.
  - A body over 10 MB, one that isn't a JSON object, a `format` other than `PENDING_FORMAT`, or `entries` that isn't a list: `unsub_bad_response`.

  An entry that isn't an object, or lacks a `str` token or an ISO 8601 `received_at` with an offset, is counted in `malformed` and skipped; `expiration` is ignored. Errors are raised `from None` with the adapter's own text. No message holds the bearer, a token, the host or the body, and nothing in the adapter logs.
- **`@command Compliance.unsubscribe_by_token(token, source, received_at) -> UnsubOutcome`.** `source` must be `one_click` or `mailto` (otherwise `ValueError`), and `received_at` an aware datetime (otherwise `TypeError`). It runs these steps:
  1. A token that isn't a `str` of 108 `[A-Za-z0-9_-]` characters is `invalid`, with no read.
  2. If `unsub_requests` already holds the `token_hash`, the result is `duplicate`.
  3. `codec.decode(token)`: a `TokenInvalid` is `invalid`, with nothing written. A keyring `AdapterError` propagates.
  4. A new request id (ULID) is the `cause_ref`. The address key `erased_person_keys(addr_hash.hex())` is written through the existing `_write`.
  5. For every Contact from `queries.research.contacts_with_address_hash(hex)`, `suppress(ContactSubject(id), person, source, request_id)`. When no Contact matches, it publishes one `SubjectSuppressed` with no Contact or Lead, as `_suppress_address` does.
  6. It inserts `unsub_requests` (`id`, `token_hash`, `received_at`, `processed_at` = the clock's now) and returns `applied`.
- **`queries.research.contacts_with_address_hash(hex)`:** the Contacts whose normalised stored `email` hashes (`key_hash("address", …)`) to `hex`, plus erased Contacts whose stored `email` equals `hex`, oldest first. A malformed hash returns `()`.
- **The `unsub_requests` table** (migration `0037_3_11_unsub_requests`, down revision `0036_2_12_legal_form`):
  - Columns: `id` ULID primary key; `token_hash` unique, 64 lowercase hex; `received_at` and `processed_at` as UtcDateTime. All are NOT NULL.
  - Triggers: `no_delete`, `immutable` and `no_reuse`, as on `unsub_tokens`.
  - Owner `compliance`; every column is `none` in `pii_registry.py`. The repository is `db/repos/compliance/unsub_requests.py`.
- **`UnsubscribePolls(clock, compliance, port, notify)`** (`nextron/app/compliance/unsub_poll.py`), `@use_case poll()`:
  1. One transaction reads the new setting `unsub_worker_host`. When it is blank, the poll logs `unsub_poll_skipped` and returns, with no request.
  2. `fetch_pending(host)` runs outside any transaction. An `AdapterError` propagates, so the job retries a retryable one.
  3. One transaction calls `unsubscribe_by_token(e.token, one_click, e.received_at)` for each entry, in order. When `invalid + malformed > 0`, it also calls `notify.alert("unsub_tokens_invalid", "unsub_invalid:<UTC date>", "<n> unsubscribe requests from the Worker could not be decoded and were not applied; check the unsubscribe token keys", (dashboard,))`.
  4. After the commit it logs `unsub_poll_applied` with `fetched`, `applied`, `duplicate`, `invalid` and `malformed`, plus a WARNING `unsub_tokens_invalid` (`count`) when the count isn't 0.
- **Setting `unsub_worker_host`:** string, section Sending, label "Unsub Worker host", default `""`, rules `max_length(253)` and `is_worker_host`. It is not required.
- **Job type `poll_unsubscribes`:** realtime lane only, priority 80, `max_runtime` 5 min, retry limit 3, group `UNSUBSCRIBE_POLL`. It is never budget-gated. `build_job_types` gains an optional `unsubscribes`, whose default raises, like `domain_checks`.
- **The trigger:** `Every(poll_period(config.env), at_startup=True)`. `build_triggers` gains `unsub_poll_period` (default `POLL_PERIOD_OTHER`).
- **Wiring:** `build_app` builds `Compliance(clock, codec=TokenCodec(secrets))` and `WorkerUnsubscribe(secrets, meter=meter)` unless an `unsubscribe=` port is injected.

  The new package is also registered in these places:
  - `tests/live_guard.py`: `LIVE_CONSTRUCTORS` and `Guarded(("default_http_client",))`;
  - contract c5 and its comment;
  - `PORT_FAKES` (`tests/fakes/unsubscribe.py` `ScriptedUnsubscribe`);
  - `test_adapter_boundary.py` `DRIVERS`;
  - the `nextron/adapters/__init__.py` docstring.

**Never:**
- Don't deploy anything, and don't touch `workers/` or wrangler. Don't parse mailto messages (Epics 6/7), build health or monitoring (3.12) or look up OutboundMessages (Epic 5).
- No token, address, `addr_hash`, bearer or host in logs, `events`, job payloads or columns. Only `token_hash` is stored.
- No retry or alert class apart from invalid tokens, and no new dependency. Edit nothing outside `nextron-engine/`, except this spec, `sprint-status.yaml` and `deferred-work.md`.

### Decisions made overnight (pending user review)

1. **The host is a setting, `unsub_worker_host`, not a URL.** One host per environment is enough: every Sending Domain's `u.` route serves the same Worker and the same per-environment KV. The engine fixes the scheme (`https`) and the path. Blank (the default) means polling is off, so the poll makes no request and records nothing. It isn't required, so existing installs keep passing doctor.
2. **`fetch_pending(host)` takes the host.** Adapters can't read settings, which departs from the AC's `fetch_pending()`.
3. **Contacts are found by address hash, scanning the stored emails.** No `outbound_messages` table exists yet, so the token's `om_id` can't reach a Contact. `deferred-work` gets an Epic 5 entry for this.
4. **Invalid tokens and malformed entries are not recorded.** They are retried every poll while KV keeps them (up to 30 days). A tag-valid token whose `k_tok` is missing (the 3.8 deferred item) is treated the same way, so it lands once the key is restored. They raise one dashboard notification per UTC day, carrying only the count, plus a WARNING log on each poll.
5. **The request's ULID `id` is the entries' `cause_ref`,** tying each suppression to its request.
6. **Metering:** provider `unsub_worker`, operation `kv_list`, one call per request (the Worker's own cursor pages aren't visible to the engine).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| New one-click | valid token; a Contact holds the address | address and person keys `active`, source `one_click`; the Lead is closed `lost`; one `unsub_requests` row | N/A |
| Unknown om / no Contact | valid token; no Contact matches | the address key alone; one request row | N/A |
| Repeat | the same token in two polls, or twice in one | `duplicate`; one set of entries | N/A |
| Crash before commit | fetch ok, apply raises | nothing kept; the next poll applies it once | the job retries |
| Bad tag, kid or ciphertext | a tampered vector | not applied, not recorded; count warning | N/A |
| Mailto | `unsubscribe_by_token(t, "mailto", at)` | same as one-click, source `mailto` | N/A |
| Host blank | default setting | no request; `unsub_poll_skipped` | N/A |
| 401 / 500 / garbage | Worker answers | nothing applied | `unauthorized` / retryable `unavailable` / `bad_response` |
| Budget exhausted | AI 100% and every quota reached | the poll still runs | N/A |

</frozen-after-approval>

## Code Map

- `nextron/app/compliance/api.py`: `Compliance` (`_write`, `suppress`, `_suppress_address`, `issue_unsub_token`) and the module docstring. Add `unsubscribe_by_token`. `import_values` shows that a nested `@command` call works.
- `nextron/domain/contactability.py`: `erased_person_keys`, `key_hash`, `is_hash`, `ContactSubject`. `domain/unsub_token.py`: `token_hash`, `TokenInvalid`, `DecodedToken`, the token length. `domain/suppression_entry.py`: `SuppressionSource`.
- `nextron/ports/token_codec.py`. Fakes: `tests/fakes/token_codec.FakeTokenCodec` and the vectors `tests/fixtures/unsub_token_vectors.json`, for the tampered cases.
- `nextron/app/queries/research.py` (`contacts_with_email`) and `db/repos/research/contacts.py` `with_email`: the pattern for the new query and repository function.
- `nextron/adapters/companies_house/__init__.py`: the adapter template. `ports/usage_meter.Metered`; `ports/secrets.read_credential`; `domain/credentials.CredentialKey.UNSUBSCRIBE_BEARER`.
- `nextron/db/models/compliance.py` (`UnsubTokenRecord`), and `db/alembic/versions/0024_3_8_unsub_tokens.py` for the triggers. `db/owners.py`, `db/pii_registry.py`, `db/models/__init__.py`.
- The permanence contract (`tests/contracts/test_suppression_contracts.py` `GUARDED`): no name may start with `delet`, `remov`, `purg`, `expir`, `ttl`, `prun`, `evict`, `truncat` or `drop`, and no `timedelta` is allowed in the guarded compliance files. The poll period lives in `domain/unsub_requests.py`. The adapter ignores `expiration`, so don't name a field `expir*` in any guarded file.
- `nextron/domain/settings_schema.py`: `Key`, the `UNSUB_ACTIVE_KID` declaration and `HTTPS_URL_RULE`'s `Rule`. The settings and footer contracts forbid `unsubscribe`, `token`, `bearer` and similar words in a key or label, so the label is "Unsub Worker host".
- `nextron/main.py`: `build_app` (around line 962), `build_job_types` (around 1227, the `domain_checks`/`no_domain_checks` pattern), `build_triggers` (around 1493), and the docstrings (around 170–290). `jobs/handlers.py`: the `check_domains` handler pattern. `jobs/triggers.py` `Every`. `domain/job.py` `JobGroup.UNSUBSCRIBE_POLL`, `Lane`.
- `nextron/app/notify/api.py`: `Notify.alert(kind, subject, text, channels)` and `domain.alert.Channel`.
- Pinned lists:
  - `tests/contracts/test_job_contracts.py` (the job type order and the triggers test);
  - `tests/live_guard.py`;
  - `tests/fakes/__init__.py`;
  - `tests/contracts/test_adapter_boundary.py`;
  - the `pyproject.toml` c5 list and comment;
  - `tests/contracts/test_contract_coverage.py`;
  - `test_log_event_names.py`;
  - the health panel's job groups.
- HEAD bumps: `tests/contracts/test_persistence_contracts.py` and its chain probes, `tests/unit/test_migrations.py`, `tests/app/test_serve_process.py`, `tests/migration_chains.py`, and `test_source_tree` `FILES`. Watch the tests that enumerate tables or settings.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/unsub_requests.py`, `ports/unsubscribe.py`: the constants, the host rule, the outcome and the port.
- [x] `nextron/adapters/unsubscribe/__init__.py` and `tests/fakes/unsubscribe.py`, plus the live-guard, c5, `PORT_FAKES`, boundary-driver and docstring updates.
- [x] The model, the migration, `owners.py`, `pii_registry.py`, `repos/compliance/unsub_requests.py`, and the research repository and query function.
- [x] `Compliance.unsubscribe_by_token`, and `app/compliance/unsub_poll.py` `UnsubscribePolls`.
- [x] The setting, the job type, the handler, the trigger and the `build_app` wiring.
- [x] Tests:
  - `tests/unit/test_unsub_requests_domain.py`;
  - `tests/unit/test_unsubscribe_adapter.py` (a mock transport; every answer row; metering; no secret in an error);
  - `tests/app/test_unsubscribe_by_token.py` (every matrix row; the Lead closed; no address in `events`; triggers);
  - `tests/app/test_unsub_poll.py` (the poll through the job handler, crash recovery, a 3-day backlog, the budget exhausted);
  - the wiring and contract updates.
- [x] The HEAD bumps and the pinned-list fixes.
- [x] `nextron-engine/README.md`: a Story 3.11 section covering the setting, the bearer credential and what the poll does.

**Acceptance Criteria:**
- Given the code base, when `tests/contracts` and `lint-imports` run, then every contract holds, the adapter is guarded and driven, and `UnsubscribePort` has a fake.
- Given the migration at head, when the schema is read, then `unsub_requests` has its columns, its unique index and three triggers, and every column is mapped and classified.
- Given a `prod` config, when the triggers are built, then `poll_unsubscribes` is `Every(5 min, at_startup=True)`; in `dev` and `test` it is every 30 min.

## Implementation Notes

**Where things are.** Domain: `nextron/domain/unsub_requests.py` (`PENDING_FORMAT`, `POLL_UNSUBSCRIBES_JOB`, `POLL_PERIOD_PROD`/`POLL_PERIOD_OTHER`, `poll_period`, `MAX_HOST_LENGTH`, `is_worker_host`, `UnsubOutcome`). Port: `nextron/ports/unsubscribe.py` (`UnsubscribePort`, `PendingUnsub`, `PendingFetch`, the five `UNSUB_*` codes). Adapter: `nextron/adapters/unsubscribe/__init__.py` (`WorkerUnsubscribe`, `default_http_client`). Fake: `tests/fakes/unsubscribe.py` (`ScriptedUnsubscribe`, `pending`). Table: `UnsubRequestRecord` in `db/models/compliance.py`, migration `0037_3_11_unsub_requests` (down revision `0036_2_12_legal_form`), repository `db/repos/compliance/unsub_requests.py` (`new_request_id`, `for_token_hash`, `insert`). Research: `queries.research.contacts_with_address_hash` over the new `repos.research.contacts.with_any_email`. Command: `Compliance.unsubscribe_by_token` (`app/compliance/api.py`). Use case: `UnsubscribePolls.poll` and `PollCounts` (`app/compliance/unsub_poll.py`). Setting `unsub_worker_host` with `WORKER_HOST_RULE` (`worker_host`). Job type and handler: `handlers.poll_unsubscribes` / `PollingUnsubscribes`, `main.no_unsubscribes`; trigger `Trigger.POLL_UNSUBSCRIBES`; `build_app(..., unsubscribe=None)`.

**Choices the frozen spec left open (implementer):**
1. The adapter also refuses a bearer holding anything but visible ASCII (`!`..`~`) as `unsub_bearer_invalid`, since it can't go in a header; otherwise httpx would fail it as a (retryable) transport error.
2. `PendingUnsub` converts an aware `received_at` to UTC in `__post_init__`; an offset that leaves the calendar on conversion counts as malformed.
3. The app layer may not import `nextron.db.models` (contract c1), so the request ULID comes from the repository's `new_request_id()`.
4. `PollCounts.fetched` is every item the Worker returned: well-formed entries plus malformed ones.
5. The `SubjectSuppressed` of a matched Contact is `suppress(ContactSubject)`'s own: its `suppression_entry_id` is that call's first written entry (the person key when the address was just written by step 4), not the address entry.
6. A token whose address is already fully suppressed (another message's link) is still `applied`: it gets its own request row, and the Contact's `suppress` republishes, so the Lead is closed `lost` again if it was reopened.
7. `build_app` now builds `Compliance(clock, codec=TokenCodec(secrets))` for the dashboard too (the same instance the poll uses); `TokenCodec` reads no key until it decodes.
8. The poll reads the setting in a write transaction (as `DomainChecks.check_domain` does), and the notification is raised inside the apply transaction, so it rolls back with it.
9. The settings label holds none of the forbidden words, so no contract exemption was needed.

**Review patches (pass 1).** (A) Docstrings re-flowed to 100 characters in `db/repos/compliance/unsub_requests.py` and `main.py` (Jobs, Triggers and Suppression List panel paragraphs), wording unchanged. (B) New test `test_a_stored_email_that_no_longer_normalises_is_skipped_not_raised` in `tests/app/test_unsubscribe_by_token.py`: a Contact whose stored `email` `key_hash` refuses is skipped and the token still lands. The orchestrator also normalised `nextron/jobs/handlers.py` back to LF (the edit had left CRLF and two `\r\r\n`). `deferred-work.md` gained two entries (3.12 dead-job alert volume; Epic 5 changed address).

**Verification (implementer):** targeted runs of `tests/unit/test_unsub_requests_domain.py`, `tests/unit/test_unsubscribe_adapter.py`, `tests/app/test_unsubscribe_by_token.py`, `tests/app/test_unsub_poll.py`, the settings tests, and `tests/contracts` with the HEAD bumps.

**Orchestrator, final verification.** The full suite exposed one more pinned test: `tests/unit/test_suppression_page.py::test_build_app_builds_compliance_on_its_clock` stubbed `Compliance(clock)`; it now takes the `codec` keyword and asserts `build_app` passes a `TokenCodec`. Targeted run after the patches (the new test files, `tests/contracts`, runner lifespan): 1830 passed, 10 skipped. Full suite through `suite_lock.py` (`track-D`): 15337 passed, 13 skipped; `lint-imports` 29 kept, 0 broken. Two earlier full runs failed only `test_environment_isolation.py::test_dev_and_test_run_side_by_side_without_touching_each_other` (a dev engine still importing the `anthropic` SDK after 60 s while the PC was under load; a faulthandler dump confirmed it, and it passes alone) and, once, `test_backup_target.py::test_no_planted_passphrase_reaches_a_file_or_an_error` (passes alone); neither touches this story's code.

## Spec Change Log

## Review Triage Log

Pass 1 (review loop 0). Three layers: blind hunter (BH, 13 findings), edge-case hunter (EC, 7) and verification gap (VG, 1 gap finding, no others). That is **21 raw** findings, **15 after grouping**: 2 patched, 2 deferred, 11 rejected.

| # | Layer | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| 1 | BH | One entry that fails every time rolls the whole batch back on every poll, so no opt-out in the list ever lands. | low | No persistent per-entry failure is reachable: `suppress`'s `LookupError` needs a Contact whose Lead is gone, and Leads are tombstoned, never deleted; a keyring failure is transient and retried. The frozen spec fixes "one transaction" for every entry, and a savepoint-and-skip path adds a new failure class. | reject |
| 2 | BH | A broken bearer or Worker kills a job every 5 minutes, and each dead job raises its own all-channel `job_dead` alert. | medium | Real: `DeadJobAlerts.alert_dead_jobs` alerts once per dead job id (`app/ops/api.py:600`), and each trigger firing is a new job. The fix is a new alert/monitoring path, which the frozen "Never" excludes ("no retry or alert class apart from invalid tokens", "monitoring (3.12)"). | defer (3.12) |
| 3 | BH | Nothing reports "polling on but bearer not set": no doctor check or health issue. | low | Real, but it is the health and monitoring work the frozen spec gives to Story 3.12. Folded into the row 2 deferral note. | reject (intent: 3.12) |
| 4 | BH | The port can't tell whether the pending list is complete (no cursor or truncation flag), and past 10 MB nothing lands. | false | Decision 6: the Worker pages KV itself and returns the whole list; the engine sees one answer. 10 MB holds about 60,000 entries (≈150 bytes each), far beyond 30 days of opt-outs. | reject |
| 5 | BH | The pending list is never acknowledged, so every poll re-downloads it. | false | By design: the Worker never deletes (epic context, AD-15), and `unsub_requests` dedup is the stated mechanism. | reject |
| 6 | BH | `contacts_with_address_hash` hashes every Contact per applied token inside the write lock. | low | Real cost, O(tokens × Contacts); at this engine's scale (a few thousand Contacts) a backlog costs seconds. A per-poll lookup adds parameters across the command boundary. The implementer already recorded the scan cost in `deferred-work.md` (Epic 5 `om_id` lookup). | reject |
| 7 | BH | The notification blames the keys for malformed entries, says "1 … requests", and keeps the day's first count. | low | The text, the summed count and the per-day subject are the frozen spec's verbatim wording. Changing them edits the spec. | reject (spec change) |
| 8 | BH | `unsubscribe_by_token` repeats `_suppress_address` instead of sharing a helper. | false | Frozen step 5 prescribes `suppress(ContactSubject)` per Contact and the no-Contact publish "as `_suppress_address` does"; no divergence is shown. | reject |
| 9 | BH | `received_at` from the Worker is trusted (future-dated or later than `processed_at`). | low | It is only recorded, never used to decide anything, and it comes from our own bearer-authenticated Worker. A clamp adds a guard for an unshown state. | reject |
| 10 | BH | Three docstring lines exceed the 100-character wrap (`unsub_requests.py:4`, `main.py:201`, `main.py:425`), and `build_triggers`' docstring has a stray short line. | low | Confirmed with `awk`: 113, 137 and 102 characters. A direct correction. | patch (A) |
| 11 | BH | The handler drops the returned `PollCounts`. | false | Frozen spec: handlers return `None`; the counts are logged by the use case. | reject |
| 12 | BH | `test_serve_process.py`'s skip check only asserts the list is non-empty. | low | Non-empty is the claim (a skipped poll was logged); the no-request property is asserted in `test_unsub_poll.py::test_a_blank_host_makes_no_request_and_logs_skipped`. | reject |
| 13 | BH | The 10 MB cap is checked after `response.content` loads the whole body. | low | Real, but the server is our own Worker; streaming with a byte cap adds a code path for an unshown state. | reject |
| 14 | EC | One entry raising a non-adapter error rolls back the batch every poll. | low | Same as row 1. | reject |
| 15 | EC | A large backlog holds the write lock long and may pass `max_runtime`. | low | Same root as row 6; 5 minutes of hashing needs millions of hash operations. | reject |
| 16 | EC | N tokens mean N full scans with SHA-256. | low | Same as row 6. | reject |
| 17 | EC | The whole body is loaded before the size cap. | low | Same as row 13. | reject |
| 18 | EC | A missing or rejected bearer gives a new dead job every 5 minutes, each alerting on every channel. | medium | Same as row 2. | defer (3.12) |
| 19 | EC | A Contact whose email changed after the token was issued keeps its other keys unsuppressed and its Lead open. | medium | Real: the lookup matches only the current stored address (decision 3). Reaching the Contact needs the token's `om_id` and `outbound_messages`, which Epic 5 builds. | defer (Epic 5) |
| 20 | EC | Later failures the same day keep the first notification's count. | low | Same as row 7. | reject (spec change) |
| 21 | VG | `_holds_address`'s `except` for a stored email that no longer normalises has no test; narrowing it would block every opt-out unseen. | medium | Filed with evidence (grep of `tests/` for `IdentityError`, `_holds_address`, `with_any_email`). Test only. | patch (B) |

## Verification

**Commands:**
- `cd nextron-engine && uv run pytest -q <touched test files> tests/contracts`: expected all pass.
- `cd nextron-engine && uv run lint-imports`: expected all contracts kept.
