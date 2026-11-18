---
title: 'Story 3.16: Keep an erasure ledger and reapply it after a restore'
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: '505c05b625f21a6e4f467d6e4a273c889207cf4f'
review_loop_iteration: 0
story_key: '3-16-keep-an-erasure-ledger-and-reapply-it-after-a-restore'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Restoring a backup forgets every opt-out, void and erasure made after the copy was taken, so a restore can bring back an erased person or an opted-out address (UK GDPR, AD-16, AD-17, FR-50, FR-52). Recovery mode (Story 1.19) holds sending, but its `reapply_erasure_ledger` step has no handler and no ledger exists.

Requirements: FR-50, FR-52, AD-16, AD-17, AD-20, NFR-16, NFR-36.

**Approach:** compliance mirrors every committed suppression entry, void and erasure to an append-only, fsynced JSONL file `<backup dir>\erasure-ledger.jsonl` (hashes and ids only), re-syncs it from the DB at startup, and registers a startup recovery handler that reapplies it after a restore before the runner starts. A missing ledger keeps recovery mode on, with a health issue. `nextron restore` decrypts the newest off-PC ledger copy when the backup dir has none (a new PC). The acceptance criteria are Story 3.16 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**
- **Pure domain `nextron/domain/erasure_ledger.py`** (stdlib + `nextron.domain`): `LEDGER_VERSION = 1`; `REAPPLIED = "reapplied after restore"`; frozen lines `Suppressed(entry_id, kind, value_hash, source, at, domain=None)`, `Voided(entry_id, at)`, `Erased(contact_id, cause, at)`, each validated (ULIDs, `KeyKind`, 64-hex hash, `SuppressionSource`, `ErasureCause`, aware `at`; `domain` set exactly for kind `domain`). `encode(line) -> str`: one compact JSON object in the AC's key order (`v`, `type`, then the fields; `domain` last, only for a domain key), `at` as UTC ISO-8601 with `Z`, ending `"\n"`. `parse(data: bytes) -> ParsedLedger(lines, malformed, torn)`: an unterminated last segment is `torn` and ignored; a complete line that isn't a valid v1 line counts in `malformed` and is skipped; duplicates are kept in order. `identity(line)` is `("suppressed"|"voided", entry_id)` or `("erased", contact_id)`. `plan_reapply(lines) -> ReapplyPlan(voids, inserts, erasures)`: `voids` every voided entry id; `inserts` the first `Suppressed` line per entry id whose id is not voided; `erasures` the first `Erased` line per Contact; each in ledger order.
- **The file, `nextron/app/compliance/ledger.py`** (compliance-owned; file I/O is never inside a transaction):
  - `LedgerFile(path)`: `exists()`, `read() -> ParsedLedger | None` (`None` when missing), `ensure()` (creates an empty file only when absent, `open(..., "ab")`), `append(lines)`: under one process-wide `threading.Lock`, `open(path, "ab")`; when the file is non-empty and its last byte isn't `\n`, a `\n` goes first (a torn tail becomes its own malformed line); then all encoded lines in one `write`, `flush`, `os.fsync`. No code anywhere opens the ledger with a mode other than `"ab"`/`"rb"`, truncates, renames over or deletes it (contract test over the source).
  - `LedgerAppender(file, alerts)`: `record(lines)` is called by a command inside a transaction and registers `current().after_commit(...)`; after the commit it appends. A failed append logs `erasure_ledger_append_failed` (ERROR: `count`, `code`) and, in a new transaction of the still-running use case, raises the ops alert `erasure_ledger_failed` (subject the first line's id; text `"Erasure ledger append failed: <n> line(s) not written (<ErrorType>); the next engine start re-appends them"`, `ALL_CHANNELS`). Nothing is rolled back; a failure raising the alert is logged `erasure_ledger_alert_failed` and swallowed.
- **`UnitOfWork.after_commit(callback)`** (`nextron/db/uow.py`): only inside an open write transaction (else `UnitOfWorkError`); callbacks run in order right after that transaction commits, outside it, while the use case still runs; a rollback drops them; an exception from one is logged `after_commit_failed` (ERROR, type only) and the rest still run. Documented in the module docstring.
- **Writers record lines** (only when the `Compliance` has an appender): `Compliance(clock, *, codec=None, ledger=None)`; `_write` records one `Suppressed` per inserted entry; `reverse_optout` one `Voided`; `Erasures.erase_contact` one `Erased(contact, cause, after.erased_at)` when it erased (`erased=True`), through `compliance.ledger`.
- **Reapply writers in `app/compliance/api.py`** (the single-writer contract stays): `@command reinstate(line: Suppressed) -> str | None` inserts an `active` entry (source from the line, `cause_ref` the line's entry id, `domain_plain` the line's domain) and one `events` row (`None → active`, cause `CREATION[source].cause`, actor `system:compliance`) unless an `active` entry exists for (kind, hash) (`None`); it publishes nothing. `@command void_reapplied(entry_id) -> bool` voids an `active` entry (reason `REAPPLIED`, `events` `active → voided`, the machine's cause, actor `system:compliance`); an unknown or already voided entry is `False`.
- **`ErasureLedger(clock, file, compliance, erasures)`** in `ledger.py`:
  - `@use_case reapply() -> bool`: a missing file logs `erasure_ledger_missing` (WARNING) and returns `False`. Else it parses outside any transaction (logging `erasure_ledger_damaged_lines`, WARNING: `malformed`, `torn`, when either is set), then one transaction applies the plan in order: voids (`void_reapplied`), inserts (`reinstate`), erasures (an unknown or tombstoned Contact is skipped; otherwise `erase_contact(cid, REAPPLIED, "dsr")` for a `dsr` line, falling back to `retention` only when `check_request_ref` refuses `REAPPLIED` for that name; `erase_contact(cid, None, "retention")` for a retention line). Any exception rolls everything back and propagates (the step stays `pending`). After the commit it logs `erasure_ledger_reapplied` (INFO: counts) and returns `True`.
  - `@use_case sync() -> int`: one transaction reads every entry, every voided entry and every erased Contact with its cause (`dsr` when it has a completed `erase` request, else `retention`); after it, when the file is missing it is created only if no `on` recovery row has `reapply_erasure_ledger` `pending` (else nothing is written and it returns 0); then it appends, in one `append`, every line whose `identity` the file lacks (entries by `created_at`, voids by `voided_at`, erasures by `erased_at`), and returns the count, logging `erasure_ledger_synced` (INFO: `appended`).
- **Wiring (`nextron/main.py`).** `build_app` builds one `LedgerFile(config.backup_dir / ERASURE_LEDGER_FILE)`, a `LedgerAppender(file, notify)`, `Compliance(clock, ledger=appender)` (also behind `Erasures`) and `ErasureLedger`. `build_recovery_steps(reapply=None)` registers `RecoveryHandler(REAPPLY_ERASURE_LEDGER, "compliance", reapply, startup=True)` when given; `build_app` passes `ledger.reapply`. The first startup step is `sync_erasure_ledger` (its own unit of work in a fresh context, like `queue_contact_markets`), after the recovery pass. `build_health_issues` registers source `compliance`: `functools.partial(queries.compliance.health_issues, ledger_path)`.
- **`queries.compliance.health_issues(ledger: Path)`**: when the `on` recovery row has `reapply_erasure_ledger` `pending`: a missing file gives `HealthIssue("erasure_ledger_missing", <recovery id>, "Erasure ledger not found; sending stays blocked", detail="Put the ledger at <path> and restart the engine")`, a present one `HealthIssue("erasure_ledger_not_reapplied", <recovery id>, "Erasure ledger not yet reapplied; sending stays blocked", detail="Restart the engine to retry; the log has recovery_step_failed")`. Otherwise `()`.
- **Doctor check `erasure_ledger`** (`nextron/startup_checks.py`, registered after `offpc_backup`): `ok` when the file exists and `os.access(W_OK)`; `fail` when missing (database exists) or not writable; `warn` when missing and there's no database yet ("created at the first engine start"). It never opens the file.
- **`nextron restore`**: after the checks and before the swap, when `<backup dir>\erasure-ledger.jsonl` doesn't exist and the source is `.db.enc`, it decrypts the newest `nextron-<env>-<stamp>.ledger.jsonl.enc` beside it (`targets(file.parent).get`) into `<backup dir>\erasure-ledger.jsonl.part`, then renames it (never over an existing file), printing and logging `erasure_ledger_recovered` (`file`). None found, or a failure, prints `no erasure ledger found: recovery mode holds sending until <path> exists` (log `erasure_ledger_not_recovered`, WARNING, `code`) and the restore continues.
- **Tests**: every matrix row; the redaction detector over a ledger written by an end-to-end DSR erasure (hashes masked first, then `redact(text) == text`, and no planted name, address, LinkedIn URL or void reason in the bytes); after_commit unit tests; a contract that only `ledger.py` touches the file and never with a truncating mode; the restore-test count check runs on a real `suppression_entries` count.

**Never:**
- No plaintext personal data in the ledger (a firm domain on a domain line only). No rewrite, truncation or deletion of the ledger.
- No migration, new table, setting, job type, adapter, port or dependency. No change to `ContactErased` or `SubjectSuppressed`.
- No I/O inside a transaction. No `events` read that drives behaviour.
- No receipts (Story 3.14) or retention (8.x). No edits outside `nextron-engine/` except this spec, `sprint-status.yaml` and `deferred-work.md`.

### Decisions made overnight (pending user review)

1. **Appends run after the commit** through a new `UnitOfWork.after_commit`, so a rolled-back change never reaches the ledger, and every current and future writer (unsubscribe polling, reply rules, retention) is covered by the three compliance writers.
2. **Domain lines carry the domain** (`"domain"`, last): a domain entry needs `domain_plain` to be re-inserted. It is a firm's web domain, stored in plain already (`domain_plain` is class `none`).
3. **Reapply order is voids, then inserts, then erasures**, from the whole ledger: a suppressed-then-voided entry isn't re-inserted, and a key re-suppressed after a void stays suppressed. Re-inserted entries get new ids (`cause_ref` the original), which the after-commit hook appends; reapply publishes no `SubjectSuppressed`, so Pipeline Stages aren't changed (the gate reads the list).
4. **A reapplied DSR erasure uses the request reference "reapplied after restore"**, so its request (and, after 3.14 merges, its receipt) is marked; the events actor stays `operator` because `open_dsr` fixes it.
5. **The erasure cause at sync** is `dsr` when the Contact has a completed erase request, else `retention` (no behaviour reads `events`). Over-suppressing is the safe side.
6. **Torn and malformed lines are skipped with a WARNING.** A torn line only follows a crash or a failed write after a commit; the startup sync and the alert cover it while the DB still holds the row.
7. **The startup sync never creates the ledger while a reapply is pending**, so a restored database can't make a missing ledger look present.
8. **A missing ledger on a new PC** is recovered by `nextron restore` from the newest off-PC ledger copy in the source folder; otherwise recovery mode holds sending until the file is put there.
9. **Doctor `erasure_ledger`** only warns before the first engine start.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Append | suppress, void, DSR erasure, retention erasure commit | one line each, fsynced, the AC's fields | N/A |
| Rollback | the transaction fails after a writer ran | no line | N/A |
| Append fails | file unwritable | change committed; `erasure_ledger_failed` alert; next `sync` appends it | logged |
| Startup sync | rows from before 3.16 / crash before append | missing lines appended once; second run appends 0 | N/A |
| Restore T0, T1 changes | suppress + erase + void at T1, restore T0, start | before any job: gate blocks the address, Contact tombstoned, voided entry voided; step `done` | N/A |
| Reapply twice | step run again | no new entry, void or erasure | N/A |
| Ledger missing | restore, no ledger, no off-PC copy | step `pending`, recovery on, health "Erasure ledger not found; sending stays blocked"; sync creates no file | N/A |
| New PC | `.db.enc` with `.ledger.jsonl.enc` beside it, backup dir empty | newest ledger decrypted to the backup dir; reapply runs | decrypt fails: warning, restore continues |
| Torn tail | last line unterminated | ignored; next append starts on a new line | WARNING |
| Reapply fails | an erase hook raises | all rolled back; step `pending` | `recovery_step_failed` |

</frozen-after-approval>

## Code Map

- `nextron/db/uow.py`: `UnitOfWork.transaction()` (319–338), `_end`, `_Transaction` (165): add `after_commit`. Its contracts: `tests/contracts/test_persistence_contracts.py`, `tests/unit/test_uow*.py`.
- `nextron/app/compliance/api.py`: `Compliance.__init__` (354), `_write` (358), `reverse_optout` (499); docstring. Single-writer contract: `tests/contracts/test_suppression_contracts.py` (classify `reinstate`/`void_reapplied` call sites in `api.py`).
- `nextron/app/compliance/erasure.py`: `erase_contact` (after `after.erased_at` is checked).
- `nextron/db/repos/compliance/suppression_entries.py` (`insert`, `void`, `active_for`, `get`): add readers `all_for_ledger()` and `voided_for_ledger()`; `data_subject_requests.py`: `completed_erase_contact_ids()`; `db/repos/research/contacts.py` + `queries.research.erased_contacts()` -> `(id, erased_at)` pairs.
- `nextron/domain/suppression_entry.py` (`CREATION`, `transition`, `EntryEvent.VOID`), `domain/contactability.py` (`KeyKind`, `is_hash`), `domain/erasure.py` (`ErasureCause`), `domain/data_subject_request.py` (`check_request_ref`), `domain/backup.py` (`ERASURE_LEDGER_FILE`, `offpc_names`), `domain/recovery.py`, `domain/redaction.py`, `domain/health.py` (`HealthIssue`).
- `nextron/app/queries/compliance.py`, `queries/ops.py` (`recovery_state`, `RecoveryRow.reapply_erasure_ledger`).
- `nextron/jobs/recovery.py` (how a handler runs: own unit, one use case; `startup=True` runs before the runner).
- `nextron/main.py`: `build_app` (899–1079), `run_startup_steps`/`queue_contact_markets` (1081–1114), `build_health_issues` (1312), `build_recovery_steps` (1417), `build_doctor_checks` (2112), the restore class `_restore` (2726) and docstrings (295–360, 380–400). `tests/contracts/test_recovery_contracts.py::test_the_registry_ships_frozen_and_empty` changes: empty with no argument, one step with `reapply`.
- `nextron/startup_checks.py` (`check_offpc_backup` 676 pattern; docstring list).
- `nextron/app/ops/api.py` `OffpcBackups.send` (1044) copies the ledger live; nothing to change (decision 6 covers a torn copy).
- Tests to copy: `tests/app/test_erasure.py`, `tests/app/test_suppression.py` (World helpers, `transact`), `tests/app/test_recovery*.py` and `tests/app/test_restore*.py` (restore CLI fakes, `FolderBackupTarget` with a test keyring), `tests/unit/test_doctor*.py`, `tests/unit/test_serve.py` (startup steps, health sources, doctor ids pins), `tests/contracts/test_health_contracts.py`, `tests/contracts/test_doctor_contracts.py`.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/erasure_ledger.py` + `tests/unit/test_erasure_ledger_domain.py`.
- [x] `nextron/db/uow.py` `after_commit` + unit tests.
- [x] Repos and queries readers; `queries.compliance.health_issues`.
- [x] `nextron/app/compliance/ledger.py`; `api.py` (`ledger=`, recording, `reinstate`, `void_reapplied`); `erasure.py` recording.
- [x] `startup_checks.check_erasure_ledger`; `main.py` wiring, startup step, recovery handler, health source, doctor check, restore ledger recovery; docstrings.
- [x] `tests/app/test_erasure_ledger.py` (matrix rows on real units of work, redaction scan, end-to-end T0/T1 restore through `RecoveryRunner.startup`), restore CLI tests for the new-PC rows, `tests/contracts/test_erasure_ledger_contracts.py` (file access and modes).
- [x] Test pin updates (serve, doctor, health, recovery contracts); `nextron-engine/README.md` Story 3.16 section, replacing 3.13's "until Story 3.16" warning.

**Acceptance Criteria:**
- Given `tests/contracts` and `lint-imports`, when they run, then every contract holds and each new probe fails as intended.
- Given a full engine start after a restore, when the lifespan runs, then the reapply handler runs before `runner.start` and `check_recovery` can end recovery mode.

## Implementation Notes

- **Domain** `nextron/domain/erasure_ledger.py`: as specified. `at` is written as UTC with
  microseconds (`%Y-%m-%dT%H:%M:%S.%fZ`), the same precision the database stores, so a line
  round-trips exactly. `parse` counts a blank complete line as malformed. A `Suppressed` line
  refuses an empty `domain`.
- **`UnitOfWork.after_commit`** (`nextron/db/uow.py`): callbacks live on the `_Transaction` and
  run from `transaction()` after `_end(commit=True)` returns, so a rollback or a doomed
  transaction drops them; a read-only unit refuses it; a non-callable is a `TypeError`. A failing
  callback logs `after_commit_failed` with `code` (the exception type).
- **Readers**: `suppression_entries.all_for_ledger()` / `voided_for_ledger()` (classified as
  readers in `test_suppression_contracts.py`), `data_subject_requests.completed_erase_contact_ids()`,
  `contacts.erased()` with `queries.research.erased_contacts()`.
  `queries.compliance.pending_reapply()` (the `on` row's id while its ledger step is pending) is
  shared by `health_issues` and `ErasureLedger.sync`; `queries.compliance` now also imports
  `queries.ops` (import rule in its docstring updated).
- **`Compliance`**: `LedgerRecorder` Protocol in `api.py` (so `api.py` doesn't import
  `ledger.py`); `compliance.ledger` property; `_write` records all its inserted entries in one
  `record` call. `reinstate` records a `Suppressed` line for the new entry (decision 3);
  `void_reapplied` records nothing (the `voided` line is already in the ledger).
  `void_reapplied` also returns `False` for an active entry whose source can't be voided (only
  `reply_rule`; the table's trigger would refuse it), so it stays suppressed: the safe side.
- **`ledger.py`**: `LedgerFile.append` writes nothing for no lines; `ensure()` creates the
  parent folder (`append` never creates anything: see the review patches). The alert's channels are `(Channel.TELEGRAM, Channel.DASHBOARD)`
  (`ops.api.ALL_CHANNELS`, which compliance can't import under the app DAG). `reapply` takes an
  optional `RecoveryContext` so it is the handler's `run` directly; its log carries `voided`,
  `reinstated` and `erased`.
- **Wiring**: `build_app` builds the recovery runner after the ledger (the job registry reaches it
  through its lambda). `build_health_issues(*, budget, ledger=None)` registers `compliance` only
  when given a path (`build_app` always gives it), so the existing test callers keep their four
  sources. `build_recovery_steps(reapply=None)`.
- **Restore** (`main._Restore._recover_ledger`, step 12a, after `_prepare` and before the swap):
  lists the source folder through the target, picks the newest `.ledger.jsonl.enc` by stamp,
  makes the backup dir (a new PC has none) and clears a stale `.part` (`_fresh_part`), decrypts with `get`, refuses when a ledger appeared
  meanwhile, then `os.rename` (which never replaces on Windows). Any failure is reported with
  `error_code(exc)`; a plain `.db` source, or no ledger copy, reports `not_found`. It prints
  only when the backup dir has no ledger.
- **Contract** `tests/contracts/test_erasure_ledger_contracts.py`: the ledger's name appears only
  in `domain/backup.py`, `app/ops/api.py` and `main.py`; `ledger.py` opens only `ab`/`rb` and
  calls no truncating/renaming/removing call; every function in those modules that names the
  ledger obeys the same, except `main.py`'s `_recover_ledger` `rename`.
- **Tests**: `tests/unit/test_erasure_ledger_domain.py`, `tests/unit/test_uow_after_commit.py`,
  `tests/app/test_erasure_ledger.py`, `tests/unit/test_erasure_ledger_check.py`, four new rows in
  `tests/unit/test_restore_command.py`; pins updated in the doctor, health, recovery,
  suppression and source-tree contracts, `tests/unit/test_doctor.py` (its database fixture now
  also creates the ledger, as the engine's first start does) and
  `tests/unit/test_runner_lifespan.py` (the backup dir now holds the startup sync's empty ledger,
  the off-PC folder its ledger copy, and the recovery-mode test registers the ledger step, which
  is `done`), `tests/app/test_environment_isolation.py` (each engine's `backups/<env>` holds its
  ledger) and `tests/unit/test_suppression_page.py` (its `Compliance` spy takes `ledger=`).
- **Review patches (pass 1).**
  - `LedgerFile.append` now opens the existing file with `os.open(O_WRONLY | O_APPEND |
    O_BINARY)` (no `O_CREAT`, no `mkdir`) and writes through `os.fdopen(fd, "ab")`; a missing
    ledger raises `FileNotFoundError` into the failed-append path (log, alert; sync re-appends
    later), so a writer after a restore can never create a stand-in that lets reapply end
    recovery mode. `ensure()` is the only creator. The contract now also checks `os.open`
    flags (`O_APPEND`, none of `O_CREAT`/`O_TRUNC`/`O_RDWR`) and `os.fdopen` modes, with a probe.
  - `_erase` logs `erasure_ledger_dsr_reapplied_as_retention` (WARNING, `contact_id`) when it
    falls back to a retention erasure.
  - `after_commit_failed` now carries `callback` (its qualified name) and the traceback.
  - `parse` drops a leading UTF-8 BOM and counts a `RecursionError` (nesting too deep) as
    malformed.
  - New tests: a writer with a pending restore and no ledger (no file created, alert raised,
    reapply still `False`); the `_erase` branches (unknown and tombstoned Contacts skipped, a
    retention line re-erased with no request, the DSR-to-retention fallback for a Contact named
    "Restore"); domain lines through the T0/T1 reapply and the startup sync; `build_app`'s
    `Compliance` carries a `LedgerAppender` on `<backup dir>\erasure-ledger.jsonl` and its last
    health source is `compliance` bound to that path; BOM and deep nesting in `parse`; the
    after-commit log's `callback` and `exc_info`. `tests/app/test_erasure_ledger.py`'s world now
    starts with an empty ledger, as an engine does after its first start.

- **Final verification (orchestrator).** The first full suite run found one failure:
  `tests/app/test_serve_process.py::test_guarded_health_page_and_second_instance` asserted
  an absent backup dir, which now holds the startup sync's empty `erasure-ledger.jsonl`. The
  test now asserts exactly that file, empty. The file passes alone (5 passed), and the full
  suite then passed through `suite_lock.py` (track-E): 14060 passed, 13 skipped; lint-imports 29 kept.

## Spec Change Log

## Review Triage Log

**Pass 1:** 24 raw findings from three reviewers: blind hunter (BH, 11), edge-case hunter (EC, 9) and verification gap (VG, 4 gaps). Deduplicated to 22 entries (BH8 split in two): 9 patch, 0 defer, 13 reject. There was no intent_gap or bad_spec, so no loopback.

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| 1 | BH1 | `LedgerFile.append` opens with `"ab"`, which creates a missing file. After a restore with no ledger, a panel suppress (or any writer) creates a one-line ledger, the next start's reapply finds it and returns `True`, and recovery mode ends with every post-backup change lost. | high | True: `append` mkdirs and opens with `"ab"`; the dashboard and the writers keep running while sending is held. Decision 7's guard is only in `sync`. | patch: `append` opens without creating (`os.open` with `O_APPEND`, no `O_CREAT`), so a missing file is a failed append (logged, alerted, re-synced later); only `ensure()` creates it. Test it on a pending restore. |
| 2 | VG1 | No test checks that `build_app`'s `Compliance` has the `LedgerAppender` on the backup dir's ledger. | medium | True: `test_build_app_builds_compliance_on_its_clock` asserts only the clock, and reverting the line breaks no test. | patch: assert the appender and its path |
| 3 | VG2 | No test checks that `build_app` registers the `compliance` health source with the ledger path. | medium | True: only direct calls to `build_health_issues(ledger=...)` are tested. | patch: assert the built app's issue sources |
| 4 | VG3 | A domain line isn't tested in reapply (`reinstate` with `domain_plain`) or in sync. | medium | True: the T0/T1 and sync fixtures hold no domain entry; dropping `domain_plain` would roll every reapply back. | patch: a domain entry in both tests |
| 5 | BH4, VG4 | `_erase`'s branches are untested: an unknown Contact, a tombstoned one, a retention line, and the fallback when `check_request_ref` refuses `REAPPLIED`. | medium | True: only Jane's DSR re-erasure is tested. | patch: tests for each branch |
| 6 | BH5 | The DSR-to-retention fallback is silent. | low | True, and the fix is one log line. | patch: WARNING `erasure_ledger_dsr_reapplied_as_retention` (`contact_id`) |
| 7 | BH10 | `after_commit_failed` logs only the exception type: no callback and no traceback. | low | True; every other ERROR path logs a (redacted) traceback. A direct correction. | patch: `exc_info` and the callback's `__qualname__` |
| 8 | EC3 | A deeply nested JSON line raises `RecursionError` in `parse`, which isn't caught, so reapply and sync raise at every start. | low | True for crafted content. Adding it to the caught types is a direct correction. | patch |
| 9 | EC4 | A UTF-8 BOM (a ledger put back by hand) makes the first line malformed. | low | True, and `removeprefix(BOM)` is a direct correction. Decision 8 tells Mac to put the file there by hand. | patch |
| 10 | BH2, EC1 | A void of a reinstated entry (new id B, `cause_ref` A) is lost at a second restore of a backup older than the first restore: the plan re-inserts A. | low | True. It needs two restores of an old backup with a Reverse in between, and the result is over-suppression (the safe side). The fix is a key-based plan, more than a correction. | reject: noted in the report |
| 11 | BH3, EC2 | `reinstate` sets `cause_ref` to the original entry id, not the original cause, so `reverse_optout`'s sibling cascade misses reinstated entries. | low | True, but the AC says "`cause_ref` the line's entry id". | reject: it would change the approved AC |
| 12 | BH6 | The interaction with Story 3.14 (receipts, `erase_external` jobs on every reapply) isn't designed. | maybe-false | The AC wants a receipt per reapplied erasure. 3.14's jobs are keyed `erase_external:<contact>:<n>`, so a reapply can't duplicate them. | reject: listed for the merge in the report |
| 13 | BH7 | The append-only contract misses `shutil` copies, helper functions that get the path as an argument, and allows any `rename` in `_recover_ledger`. | low | True, but it is a guard over code that does none of these; widening it is new design. | reject |
| 14 | BH8a | Ledger recovery only looks beside a `.db.enc` source, not in the off-PC folder when a plain local copy is restored. | low | True, but it fails closed with a printed path, and Mac can restore from the `.db.enc` copy. | reject |
| 15 | BH8b | The decrypted ledger is renamed without an fsync or a parse check, so a damaged copy could reapply nothing. | false | The copy is AES-GCM authenticated: a damaged copy fails `get`, so nothing is renamed. | reject |
| 16 | BH9 | On Windows, `os.access(W_OK)` ignores NTFS ACLs. | low | True; the check is advisory, and a real append failure alerts. | reject |
| 17 | BH11 | The spec is still in progress, and the sprint key isn't `review`. | false | The workflow sets both at step 5. | reject |
| 18 | EC5 | Reapply returns `True` with malformed lines and ends recovery mode. | low | Decision 6: a malformed line is a torn write that the next start's sync re-appends while the DB holds the row. | reject: it would change the approved spec |
| 19 | EC6 | A DB row that fails line validation (an empty `domain_plain`) makes sync raise at every start. | false | Only `suppression_key` writes `domain_plain`, always with a normalised domain. No writer writes `''`. | reject |
| 20 | EC7 | A Contact retention-erased and then DSR'd syncs as `dsr`. | false | Decision 5 chooses this. After 3.13's patch, that DSR did suppress the person's key, so `dsr` is the closer cause. | reject |
| 21 | EC8 | A directory at the ledger path is reported as "not found" and "not reapplied". | low | Unlikely; it fails closed either way. | reject |
| 22 | EC9 | The "hashes and ids only" claim is broken by domain lines. | low | Decision 2 and Never state the firm domain. | reject: listed for 3.14's receipt text |

## Verification

**Commands:**
- `cd nextron-engine && uv run pytest -q tests/app/test_erasure_ledger.py tests/unit/test_erasure_ledger_domain.py tests/contracts` -- expected: pass
- `cd nextron-engine && uv run lint-imports` -- expected: all contracts kept
