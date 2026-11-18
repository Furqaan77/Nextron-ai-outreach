---
title: 'Story 3.12: Monitor unsubscribe polling and re-poll the Worker after a restore'
type: 'feature'
baseline_commit: 'd07cfe2cbd7735919517d58376f5f18ca641eac6'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
story_key: '3-12-monitor-unsubscribe-polling-and-re-poll-the-worker-after-a-r'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-3-11-poll-unsubscribe-requests-into-the-suppression-list.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Story 3.11 polls the unsubscribe Worker, but nothing records whether polling works. A broken bearer or a down Worker today kills a `poll_unsubscribes` job every 5 minutes, and each dead job raises its own all-channel `job_dead` alert (about 288 a day). Nothing shows the last good poll, nothing checks the Worker in `nextron doctor`, nothing counts the 1,000-a-day KV list limit, and a restore can roll back applied opt-outs with no step that makes the engine fetch them again.

Requirements: FR-70, FR-58 · AD-10, AD-15, AD-20 · NFR-32 · UX-DR28, UX-DR29.

**Approach:** Record each poll's outcome in a one-row `unsub_poll_state` table, and stop the poll raising for adapter failures. On top of that state:
- a health issue and one alert per failure episode after 24 hours;
- a status line on the health panel;
- a `unsub_worker` doctor check;
- a `repoll_unsubscribes` recovery step;
- a daily KV-list line on the quotas panel, with one 80% alert per day.

The acceptance criteria are Story 3.12 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**
- **Domain (`nextron/domain/unsub_requests.py`, stdlib only):**
  - `FAILING_AFTER` (24 h), `KV_LIST_DAILY_LIMIT` (1000), `KV_LIST_ALERT_PCT` (80) and `DOCTOR_DEADLINE_S` (10.0).
  - `polling_failing(last_success_at, failing_since, now) -> bool`: true when `failing_since` is set and `now - (last_success_at or failing_since) > FAILING_AFTER`.
  - `PollFailure` closed codes: the five `UNSUB_*` port codes, plus `unsub_unreachable` and `internal_error`.
  - Text helpers, all holding only times, codes and counts:
    - `failing_text(since)`: "Unsubscribe polling failing since <YYYY-MM-DD HH:MM UTC>".
    - `kv_list_text(count)`.
- **Table `unsub_poll_state`** (migration `0038_3_12_unsub_poll_state`, down revision `0037_3_11_unsub_requests`):
  - Exactly one row: `id` INTEGER primary key with `CHECK (id = 1)`.
  - Nullable `last_success_at`, `failing_since` and `last_failure_at`, all `UtcDateTime`.
  - Nullable `last_failure_code`, with a CHECK on the closed codes.
  - Owner `compliance`. Every column is `none` in `pii_registry.py`. Repository: `db/repos/compliance/unsub_poll_state.py`.
- **`UnsubscribePolls.poll()` records the outcome:**
  - **Blank host:** skipped as today. It writes nothing and leaves the state unchanged.
  - **Success:** the apply transaction also sets `last_success_at = now` and clears `failing_since`, `last_failure_at` and `last_failure_code`. If it was failing, it also clears the episode's dashboard notification.
  - **An `AdapterError` from fetch or apply:** the apply transaction (if any) rolls back. A new unit of work then:
    - sets `failing_since` if it was null;
    - sets `last_failure_at` and `last_failure_code` (the error's code if it is closed, otherwise `internal_error`);
    - when `polling_failing(...)`, calls `notify.alert("unsub_poll_failing", "unsub_poll_failing:<failing_since epoch seconds>", text with the time and code only, ALL_CHANNELS)`. Notify's permanent dedup makes this one alert per episode.

    The poll then **returns normally**, so the job is `done` and no `job_dead` row is made.
  - **Any other exception:** it is recorded the same way as `internal_error`, and then re-raised.
  - **Every fetch attempt**, success or failure, ends with the KV check in the same recording transaction. If today's (UTC) `api_usage` count for provider `unsub_worker`, operation `kv_list`, is 80% of the limit or more, it calls `notify.alert("kv_list_quota", "kv_list:<YYYY-MM-DD>", kv_list_text(count), DASHBOARD_ONLY)`. Polling never stops.
- **A `Notify` clear:** `Notify.clear(kind, subject)` marks that notification dismissed, if it exists and is active. Nothing else changes in alert dedup.
- **Dead `poll_unsubscribes` jobs.** These come only from bugs now. `DeadJobAlerts` alerts them under the subject `poll_unsubscribes:<UTC date>` instead of the job id, so there is at most one `job_dead` alert a day for the type. Every other type is unchanged.
- **Health:**
  - `queries.compliance.health_issues(now)` yields one `HealthIssue("unsub_poll_failing", "unsub_poll", failing_text(...))` when `polling_failing`. It is registered in `main.build_health_issues` through a lambda on `clock.now`.
  - `queries.compliance.unsub_poll_status()` gives the host-on flag and the state.
  - `HealthReport` gains `unsub_poll`. `health_page.unsub_poll_text` always renders one of three lines:
    - "Last successful poll: <time>", or "never";
    - plus "failing since <time> (<code>)" when failing;
    - or "Off (no Worker host set)".
  - It has its own section in `health_panel.html`.
- **Doctor check `unsub_worker`, the fourteenth, placed last:**
  - Host blank: OK, "polling is off".
  - Host set but bearer not stored: FAIL, "polling on but bearer not set".
  - `in_engine`: OK, "not probed in the engine", with no network call.
  - Otherwise it calls the port's new `probe(host, deadline_s=DOCTOR_DEADLINE_S)`:
    - 200 within the deadline: OK, "reachable".
    - A retryable `AdapterError`, or `unsub_unreachable` (slower than the deadline): WARN.
    - Any other `AdapterError`: FAIL.
  - `probe` uses the same refusals, headers and metering as `fetch_pending`. Its httpx timeout is the deadline, and it measures elapsed time on a monotonic clock.
  - Doctor builds the adapter with `DoctorMeter`. `doctor()` takes an injectable `unsubscribe` port, and `ReadinessDatabase` gains `unsub_worker_host`.
- **Recovery step `RecoveryStep.REPOLL_UNSUBSCRIBES`:**
  - Owner `compliance`, non-startup. It is registered in `build_recovery_steps`.
  - `run(ctx)` returns True only when `last_success_at > ctx.restored_at`. The poll trigger already runs `at_startup`, and the Worker returns every entry it still holds, so the dedup by `token_hash` re-applies whatever the restore rolled back. `run` enqueues nothing.
- **Quotas panel:** a line "Unsubscribe Worker KV list calls today: N of 1,000", with a warning style at 80% or more.
- **Unsubscribe token keys after a restore (3.8 deferred item):** while `queries.ops.recovery_mode()` is on, `Setup.remove_unsub_key`, and `Setup.save_unsub_key` when it replaces an existing key, refuse with a `DomainError` that names recovery mode.

**Never:**
- No token, address, host, bearer, or Worker response body in the state table, alerts, health text, logs or events.
- Polling never stops or gets budget-gated. Don't touch `workers/`, deploy anything, or add a dependency.
- Don't change any other job type's dead-job alerting, or the 3.11 apply semantics.
- Edit nothing outside `nextron-engine/`, except this spec, `sprint-status.yaml` and `deferred-work.md`.

### Decisions made overnight (pending user review)

1. **Adapter failures no longer kill the job.** The next trigger firing is the retry (5 min in prod, 30 min elsewhere). That replaces about 288 `job_dead` alerts a day with one `unsub_poll_failing` alert per failure episode, sent after 24 h on both Telegram and the dashboard. Failures still show on the health panel at once, in the status line.
2. **A blank host is "off".** It is neither a success nor a failure, and it raises no issue. The recovery step stays pending while the host is blank, so recovery mode holds sending until a real poll succeeds. That is the compliance-safe choice.
3. **The 80% KV alert goes to the dashboard only,** once per UTC day, following Epic 1's budget-threshold rule. The count is the engine's own `kv_list` rows; the Worker's internal KV pages aren't visible (3.11 decision 6).
4. **The doctor result:** a retryable failure or a slow answer is WARN, and a refusal (401/403 or a bad bearer) is FAIL, matching the Google grant check.
5. **Token-key protection after a restore** is a refusal while recovery mode is on. The alternative, rebuilding `unsub_tokens`, isn't possible: the Worker holds no `kid` → message mapping.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Good poll | host set, 200 | `last_success_at` = now; failing cleared | N/A |
| Bearer missing | 5-min polls for 25 h | job `done` each time; one `unsub_poll_failing` alert; one health issue; no `job_dead` | recorded `unsub_bearer_invalid` |
| Recovery | failing for 30 h, then 200 | issue gone; notification cleared; "Last successful poll" updated | N/A |
| Failing under 24 h | last success 3 h ago | status line shows failing; no issue, no alert | N/A |
| Never succeeded | first failure 25 h ago | issue "failing since <first failure>" | N/A |
| Bug | apply raises `RuntimeError` | `internal_error` recorded; job retries and dies; at most one `job_dead` alert a day | re-raised |
| After restore | recovery on; first poll after restore commits | next `check_recovery` marks the step done | N/A |
| Host blank after restore | recovery on | step pending; mode stays on | N/A |
| KV at 800 today | the 800th row | one dashboard alert for the day; the poll proceeds | N/A |
| Doctor, slow Worker | 200 after 11 s | WARN, not reachable | N/A |
| Doctor in engine | health panel | no request | N/A |
| Key removal in recovery | recovery on | refused | `DomainError` |

</frozen-after-approval>

## Code Map

- `nextron/app/compliance/unsub_poll.py` (`UnsubscribePolls`, `TOKENS_INVALID`), `nextron/jobs/handlers.py` (`PollingUnsubscribes`; **keep LF line endings**), `nextron/adapters/unsubscribe/__init__.py` (`WorkerUnsubscribe`, `TIMEOUT_S`, `CONNECT_TIMEOUT_S`), `nextron/ports/unsubscribe.py`, `tests/fakes/unsubscribe.py` `ScriptedUnsubscribe` (add `probe`).
- The permanence contract (`tests/contracts/test_suppression_contracts.py` `GUARDED`: `db/repos/compliance/`, `app/compliance/`, `app/queries/compliance.py`) forbids names starting with `delet`, `remov`, `purg`, `expir`, `ttl`, `prun`, `evict`, `truncat` or `drop`, and forbids `timedelta` and SQL date modifiers there. Keep the 24-hour math in `domain/unsub_requests.py`. Write "unexpired" nowhere as an identifier.
- Health: `main.py:1530` `build_health_issues` (see the `sending` lambda) and the docstring at about 483–500; `domain/health.py` `HealthIssue`; `app/ops/api.py:717` `HealthReport`, `:789` `EngineHealth.report()`; `web/health_page.py` (`backup_text`, `panel_context`); `templates/health_panel.html`; `queries/ops.py:134` for the pattern; `tests/contracts/test_health_contracts.py`; `tests/unit/test_health_page.py`.
- Doctor: `main.py:2371` `build_doctor_checks` (the count is pinned in `tests/contracts/test_doctor_contracts.py`); `:2355` `DoctorContext`; `:2238` `ReadinessDatabase`, `DoctorDatabase`, `EngineDatabase`; `:2447` `_internal_notify_grant` (the `in_engine` pattern); `:2459` `doctor()`; `:~2224` `DoctorMeter`. `domain/readiness.py`; `startup_checks.py:588` `check_unsub_token_keys` and `:676` `check_offpc_backup` for the style. Tests: `tests/unit/test_doctor.py`. Watch the tests that list doctor ids.
- Recovery: `main.py:1651` `build_recovery_steps` (its docstring already names this step); `domain/recovery.py` `RecoveryHandler`, `RecoveryContext`, `RecoveryStep.REPOLL_UNSUBSCRIBES`; `jobs/recovery.py` `RecoveryRunner.check`; `tests/contracts/test_recovery_contracts.py`.
- Alerts: `app/notify/api.py:200` `alert`, plus `dismiss` (model `clear` on it); `notifications_repo.find`; `app/ops/api.py:478` `ALL_CHANNELS`/`DASHBOARD_ONLY`, `:600` `DeadJobAlerts.alert_dead_jobs`; `domain/alert.py` `JOB_DEAD`, `job_dead_text`.
- Quotas: `web/quotas_page.py`, `templates/quotas.html`, `GET /quotas` (`web/app.py:1102`), `db/metering.py:160` `DbUsageMeter.snapshot` (monthly; add a day count of `api_usage` through a read-only query).
- Keys: `app/setup/api.py` `save_unsub_key` (~1735), `remove_unsub_key` (~1799); `queries/ops.py:110` `recovery_mode`.
- Migration and HEAD bumps: the 3.11 list (`test_persistence_contracts.py` and its chain probes, `tests/unit/test_migrations.py`, `tests/app/test_serve_process.py`, `tests/migration_chains.py`, `test_source_tree` `FILES`), plus `owners.py`, `pii_registry.py` and `db/models/compliance.py`.

## Tasks & Acceptance

**Execution:**
- [x] `domain/unsub_requests.py`: the constants, `polling_failing`, the codes and the texts. Port `probe` and `UNSUB_UNREACHABLE`; the adapter `probe`; the fake.
- [x] The model, the migration, `owners.py`, `pii_registry.py` and the state repository.
- [x] `UnsubscribePolls` recording, the alerts and the KV check; `Notify.clear`; the `DeadJobAlerts` per-day subject for `poll_unsubscribes`.
- [x] `queries.compliance.health_issues` and `unsub_poll_status`; the registry; `HealthReport`; the page text and template.
- [x] The doctor check and its wiring; the recovery step; the quotas line; the key refusal in recovery mode.
- [x] Tests for every matrix row, plus the contract, pinned-list and HEAD updates.
- [x] `nextron-engine/README.md`: a Story 3.12 section.

**Acceptance Criteria:**
- Given the code base, when `tests/contracts` and `lint-imports` run, then every contract holds, including the health, doctor, recovery and permanence contracts.
- Given the migration at head, when the schema is read, then `unsub_poll_state` admits only `id = 1` and the closed failure codes, and every column is mapped and classified.

## Implementation Notes

**Where things are.** Domain: `nextron/domain/unsub_requests.py` (`FAILING_AFTER`, `KV_LIST_DAILY_LIMIT`, `KV_LIST_ALERT_PCT`, `DOCTOR_DEADLINE_S`, `KV_PROVIDER`, `KV_LIST_OPERATION`, `POLL_FAILING`, `KV_LIST_QUOTA`, `PollFailure`, `failure_code`, `polling_failing`, `kv_list_warning`, `failing_text`, `failing_alert_text`, `kv_list_text`, `failing_subject`, `kv_list_subject`); `domain/budget.py` `day_bounds`; `domain/unsub_token.py` `UnsubKeysHeldInRecovery`. Port: `UnsubscribePort.probe`, `UNSUB_UNREACHABLE`; `UsageMeterPort.day_calls`. Adapter: `WorkerUnsubscribe.probe` (shared `_headers`/`_get` with `fetch_pending`, injectable `monotonic`). Fakes: `ScriptedUnsubscribe.probe`/`probes`/`probe_answer`, `InMemoryUsageMeter.day_calls`; `main.DoctorMeter.day_calls` (0). Table: `UnsubPollStateRecord` (`db/models/compliance.py`), migration `0038_3_12_unsub_poll_state` (down `0037_3_11_unsub_requests`), repository `db/repos/compliance/unsub_poll_state.py` (`PollState`, `get`, `save` as an upsert). Metering: `db/metering.UsageMeter.day_calls`. Poll: `UnsubscribePolls(clock, compliance, port, notify, meter)` (`_fetch_and_apply`, `_record_success`, `_record_failure`, `_check_kv`, `@use_case repolled`). Notify: `@command Notify.clear(kind, subject)`. Dead jobs: `app/ops/api.dead_job_subject`. Health: `queries.compliance.unsub_poll_status`/`UnsubPollStatus`/`health_issues`, source `compliance` in `build_health_issues` (with `main.unsub_polling_on`), `HealthReport.unsub_poll`, `health_page.unsub_poll_text`/`unsub_poll_failing`, the `health-unsub` section and CSS. Doctor: `startup_checks.check_unsub_worker` (`UNSUB_WORKER`), `main._unsub_worker`, `DoctorContext.unsubscribe`, `ReadinessDatabase.unsub_worker_host`, `doctor(..., unsubscribe=None)`. Recovery: `build_recovery_steps(repoll=None)`; `build_app` passes `lambda context: unsubscribes.repolled(context)`. Quotas: `Budget.kv_list_calls`, `quotas_page.page_context(..., kv_list_calls=0)`, the "Unsubscribe Worker" section. Keys: `Credentials.save_unsub_key`/`remove_unsub_key` read `queries.ops.recovery_mode()`; the two web routes catch `UnsubKeysHeldInRecovery` like `UnsubKeyInUse`.

**Choices the frozen spec left open (implementer):**
1. **The row's id is a ULID, not an INTEGER.** The persistence contract requires every table's primary key to be one `Ulid` column named `id`, so `id` is the ULID-shaped text `00000000000000000000000001` with `ck_unsub_poll_state_singleton` (`id = '...01'`), following `runner_heartbeat`'s singleton. The effect is the spec's: exactly one row, "id 1".
2. A third CHECK, `ck_unsub_poll_state_failure`, keeps `failing_since`, `last_failure_at` and `last_failure_code` set or NULL together.
3. `failure_code` maps `credential_not_set` (no bearer stored, raised by `read_credential` before the adapter's own check) to `unsub_bearer_invalid`, as the matrix's "Bearer missing … recorded `unsub_bearer_invalid`" requires; any other non-closed code (`keyring_failed` included) is `internal_error`.
4. `queries.compliance` may read only `compliance`'s repositories, so it can't read the `unsub_worker_host` setting: `unsub_poll_status(polling_on)` and `health_issues(now, *, polling_on)` take the flag. `EngineHealth.report()` and the registered lambda (`main.unsub_polling_on()`) read the setting. A blank host gives no health issue even when stale failure state remains (decision 2).
5. The status line shows "; failing since <time> (<code>)" whenever `failing_since` is set (at once); the issue and alert wait for `polling_failing`.
6. The KV count goes through the meter (`UsageMeterPort.day_calls`, read in the caller's unit), so `UnsubscribePolls` and `Budget` take it from their meter; the day is `[UTC midnight, next midnight)` (`domain.budget.day_bounds`, kept out of the permanence-guarded compliance files).
7. If recording an unexpected exception itself fails, the poll logs `unsub_poll_record_failed` (ERROR, the class name) and re-raises the original exception. An adapter failure logs `unsub_poll_failed` (WARNING, `code`).
8. Doctor's `unsub_worker` treats an unreadable bearer as FAIL; messages carry codes only (no host or bearer). In the engine the check reads the setting and then the bearer's keyring entry (only when the host is set), after `unsub_token_keys`, so it is the second check that reads Credential Manager under the read transaction.
9. `dead_job_subject` dates a dead `poll_unsubscribes` job by its `updated_at` (when it died), so the sweep needs no clock; the sweep also tracks subjects added in the same pass.
10. `HealthReport.unsub_poll` defaults to `None` (rendered as "Off") so existing constructions keep working; `DoctorContext.unsubscribe` defaults to `None` (the engine passes none and never probes).
11. `save_unsub_key` refuses in recovery mode only when a *valid* key is stored (the same "replaces" test as the in-use rule): a missing or garbled key can still be entered from escrow.

**Review patches (pass 1).** (A) `test_health_page.py`: the real `compliance` source on the dashboard's injected clock lists "Unsubscribe polling failing since …" with the host set and nothing with it blank. (B) `test_runner_lifespan.py`: the built panel's `unsub_worker` row is ok "not probed in the engine" with a host and bearer set; no probe, no fetch, no `api_usage` row. (C) `test_credentials_page.py`: in recovery mode a replacing save and a confirmed remove show the recovery-mode notice and leave the keyring unchanged. (D) `WorkerUnsubscribe.probe` maps an httpx timeout to `unsub_unreachable` (`fetch_pending` keeps `unsub_unavailable`); the doctor WARN reads "was slower than 10 s" for `unsub_unreachable` and "is down, unreachable or rate-limited" for other retryable codes. (E) The `AdapterError` branch's recording is guarded (`_record_failure_logged`): a recording failure logs `unsub_poll_record_failed` and the poll still returns `None`. (F) README: the quotas KV count is the engine's own polls; doctor's probe and the Worker's internal pages aren't counted. (G) Test: an apply-side `AdapterError` (codec `keyring_failed`) rolls back, records `internal_error` and ends the job `done`; the module docstring says `unsub_unreachable` is doctor-only. (H) Test: the migration's `FAILURE_CODES` equal `PollFailure`. (I) `ports/unsubscribe.py` docstring re-wrapped; README `required_settings` row lists the seven required settings; README says a recovery dismisses only the dashboard notice. (J) `alert_dead_jobs` also skips a job whose id is already an alerted subject (alerted under its id before the upgrade); test in `test_dead_job_alerts.py`.

**Verification (implementer).** Targeted runs, all passing: `tests/app/test_unsub_poll.py` (30, every matrix row but the doctor ones), `tests/app/test_unsub_poll_state.py` (new: schema CHECKs, owners/PII, repository, `UsageMeter.day_calls`, `Notify.clear`), `tests/unit/test_unsubscribe_adapter.py` (the probe), `tests/unit/test_startup_checks.py -k unsub_worker`, `tests/unit/test_doctor.py`, `tests/unit/test_health_page.py`, `tests/unit/test_quotas_page.py`, `tests/unit/test_unsub_requests_domain.py`, `tests/app/test_unsub_tokens.py -k recovery`; `tests/contracts` 1597 passed, 10 skipped; `lint-imports` 29 kept, 0 broken.

**Orchestrator, final verification.** The implementer was restarted once after a process crash and continued the partial work in the tree. After the review patches, the full suite through `suite_lock.py` (`track-D`) gave 15439 passed, 13 skipped; `lint-imports` 29 kept, 0 broken. `nextron/jobs/handlers.py` is unchanged and LF; `app.css` keeps its 293 CRLF lines. Merge touchpoint with Story 3.16 (on main): `build_recovery_steps` (3.16 adds its `reapply` startup step; this story adds `repoll=` for the non-startup `repoll_unsubscribes`), `tests/contracts/test_recovery_contracts.py`, the health contract `SOURCES`, `tests/unit/test_runner_lifespan.py`'s recovery test, and the migration number (0038 here).

## Spec Change Log

## Review Triage Log

Pass 1 (review loop 0). Three layers: blind hunter (BH, 14 findings), edge-case hunter (EC, 7) and verification gap (VG, 3 gap findings and 1 other). That is **25 raw** findings, **20 after grouping**: 10 patched, 2 deferred, 8 rejected.

| # | Layer | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| 1 | VG | The real `unsub_polling_on()` wiring of the `compliance` health source is never exercised with a failing state. | medium | Filed with evidence: the contract test monkeypatches `unsub_polling_on`, and the panel test checks only the status line. Test only. | patch (A) |
| 2 | VG | The in-engine `unsub_worker` doctor check is never run with a host set (`main._unsub_worker`, `EngineDatabase.unsub_worker_host`). | medium | Filed with evidence: `test_runner_lifespan` never sets the host. Test only. | patch (B) |
| 3 | VG | The Credentials routes' `except (UnsubKeyInUse, UnsubKeysHeldInRecovery)` is untested. | medium | Filed with evidence: grep finds `UnsubKeysHeldInRecovery` only in a service-level test. Test only. | patch (C) |
| 4 | VG other | `tests/dashboard.py` builds the health issues on `SystemClock`, so a future assertion on the error count would depend on the wall clock. | low | No current assertion depends on it; a hypothetical future test. | reject |
| 5 | BH | The key refusal holds only while recovery mode is on. Once the step finishes, a kid first used after the backup looks unused again. | medium | Real: `key_in_use` reads the restored `unsub_tokens`. Decision 5 fixed the refusal-during-recovery form; a lasting guard (for example kid ≥ the restored newest kid for 2 years after `restored_at`) is a new rule for Mac. The 3.8 deferred entry's status is corrected to "partly resolved". | defer |
| 6 | BH | `repoll_unsubscribes` reports done even when the backup is older than the Worker's 30-day KV window, so opt-outs made between the backup and day −30 are lost silently. | medium | Real: `repolled` compares only `last_success_at` with `restored_at`; `RecoveryContext.backup_at` is unused. A warning or a hold needs a new health issue or alert class; it's Mac's call. | defer |
| 7 | BH | A blank host doesn't end a failure episode, so setting the host again brings back the old `failing_since` and alerts at once. | low | Frozen spec: "Blank host: … writes nothing and leaves the state unchanged." | reject (spec change) |
| 8 | EC | After a gap of more than 24 h (PC off), the first transient failure alerts at once, because `polling_failing` measures from `last_success_at`. | low | This is the frozen `polling_failing` definition, and the AC's "no poll has succeeded for more than 24 hours" is literally true. | reject (spec change) |
| 9 | BH, EC | `probe` maps an httpx timeout to `unsub_unavailable`, not `unsub_unreachable`, and the WARN text says "did not answer within 10 s" for every retryable code, a fast 503/429 and a DNS failure included. | low | Confirmed: `_get` turns every transport exception into `unsub_unavailable`, and `check_unsub_worker` uses one text. Direct fix: map an httpx timeout to `unsub_unreachable` in `probe`, and give the WARN text per code. | patch (D) |
| 10 | BH, EC | The `AdapterError` branch of `poll` calls `_record_failure` unguarded; a failed recording escapes as a database error, so the job can die and the dead-job path alerts. | medium | Confirmed at `unsub_poll.py` `poll`: the generic branch guards the recording, this one doesn't. | patch (E) |
| 11 | BH | An unparseable stored `unsub_worker_host` makes `EngineHealth.report()` raise. | low | The setting is validated on save, so only database corruption reaches this; the fix is a new guard. | reject |
| 12 | BH, EC | Doctor's probe is a real KV list call, metered only by the in-memory `DoctorMeter`, so the daily count leaves it out. | low | Real but small: doctor runs by hand. Direct fix: say so in the README (the count is the engine's own polls). | patch (F) |
| 13 | BH | A slow and malformed answer reports `unsub_bad_response`, not slowness. | low | A bad answer is the more useful failure, and FAIL outranks WARN; the precedence change adds nothing. | reject |
| 14 | BH | No test shows that an apply-side `AdapterError` (`keyring_failed` from the codec) is recorded as `internal_error` with the job `done`; `unsub_unreachable` is never stored by the poll, and that isn't documented. | medium | The apply-side path is real and untested. Test plus a one-line docstring note. | patch (G) |
| 15 | BH | Nothing asserts that the migration's `FAILURE_CODES` equals `PollFailure` at head. | low | A direct one-assertion test. | patch (H) |
| 16 | BH | The KV test inserts `api_usage` rows by hand, and the next-day rollover isn't tested. | low | The subject carries the date (unit-tested in the domain), and the meter writes its row before `_check_kv` reads it. Extra tests guard no shown defect. | reject |
| 17 | BH, EC | The spec is a pre-review snapshot, and the code departs from the frozen text (ULID `id` rather than INTEGER `id = 1`, `Credentials.*` rather than `Setup.*`, `UnsubKeysHeldInRecovery` rather than a bare `DomainError`). | false | The status and triage log are now current. The ULID id is required by the persistence contract (one Ulid `id` per table) and matches `runner_heartbeat`. The class is named `Credentials` in the code, and `UnsubKeysHeldInRecovery` is a `DomainError` naming recovery mode. All are recorded in the Implementation Notes. | reject |
| 18 | BH | The `ports/unsubscribe.py` docstring line is 111 characters; the README `required_settings` doctor row lists 3 of the 7 required settings; the README doesn't say `Notify.clear` dismisses only the dashboard notice (no Telegram "recovered"). | low | Confirmed; direct corrections. | patch (I) |
| 19 | EC | On upgrade, dead `poll_unsubscribes` jobs already alerted under their job id are alerted again under a date subject: one burst per past day. | low | Confirmed: `alert_dead_jobs` now checks only the date subject. Direct fix: also skip a job whose id is already alerted. | patch (J) |
| 20 | EC | A restore while `unsub_worker_host` is blank leaves recovery mode on with no way out in code. | low | Decision 2 chose exactly this (a compliance-safe hold until a real poll succeeds); setting the host and a successful poll end it. | reject (spec change) |

## Verification

**Commands:**
- `cd nextron-engine && uv run pytest -q <touched test files> tests/contracts`: expected all pass.
- `cd nextron-engine && uv run lint-imports`: expected all contracts kept.
