---
title: 'Story 1.17: Encrypt each backup and copy it off the PC'
type: 'feature'
created: '2026-11-17'
status: 'done'
route: 'dispatch'
baseline_commit: '4668d306d762a6a3489bd174bfe9fca1443d40cc'
review_loop_iteration: 0
story_key: '1-17-encrypt-each-backup-and-copy-it-off-the-pc'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-1-16-back-up-the-database-daily-and-before-every-migration.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Every backup stays on the PC, as plaintext. A lost, stolen or failed PC loses the Suppression List and the Provenance Records together with every copy of them, and nothing says the off-PC destination (OQ-A3) is missing.

Requirements: AD-9, AD-17, AD-20, OP-6, INT-13, NFR-16, NFR-17.

**Approach:** After each verified daily or manual backup, `ops` sends the local copy (and the erasure ledger, when it exists) through a new `BackupTargetPort`. Its folder adapter encrypts each file (scrypt key from the `backup/passphrase` keyring entry, AES-256-GCM in 64 KiB chunks) and writes it to the `backup_offpc_dir` setting's folder. The `backups` row records the off-PC outcome. A failed copy raises the `backup_failed` alert and a panel error, old off-PC copies rotate after 35 days, and a new doctor check warns while off-PC backup isn't configured.

The acceptance criteria are Story 1.17 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**

- **Crypto** (`nextron/adapters/crypto/__init__.py`, `cryptography` 50.0.2, already pinned by the spine and locked: no new dependency). Pure computation over the binary streams it is given; it opens no file and logs nothing.
  - **Format v1.** A 40-byte header, then chunks:
    - header: `MAGIC = b"NXBK"` (4) | `FORMAT_VERSION = 1` (1) | `log2_n` (1) | `r` (1) | `p` (1) | salt (16, `os.urandom`, fresh per file) | key check (16);
    - `scrypt(NFC(passphrase).encode("utf-8"), salt, n=2**log2_n, r, p, length=64)` gives `enc_key = [:32]` and `check_key = [32:]`. The key check is `HMAC-SHA256(check_key, header[:24])[:16]`;
    - chunk `i` (from 0) is `AESGCM(enc_key).encrypt(nonce=i.to_bytes(12, "big"), plaintext ≤ 64 KiB, aad=header || i.to_bytes(8, "big") || bytes([final]))`. Every chunk but the last holds exactly 65,536 plaintext bytes; the last has `final = 1` and may be shorter (empty only for an empty plaintext). Each ciphertext chunk is its plaintext + a 16-byte tag.
  - **`ScryptParams(log2_n, r, p)`** is frozen. `DEFAULT_SCRYPT = ScryptParams(17, 8, 1)` (128 MiB, about 0.3 s). Readers accept `10 ≤ log2_n ≤ 18`, `1 ≤ r ≤ 8`, `1 ≤ p ≤ 4` only.
  - **`seal(source, sink, passphrase, *, params=DEFAULT_SCRYPT) -> Sealed(plain_size, plain_sha256, sealed_size)`** streams with a one-chunk read-ahead.
  - **`unseal(source, sink, passphrase) -> Opened(plain_size, plain_sha256)`.** Each failure is a non-retryable `AdapterError` with its own code:
    - `backup_bad_format`: shorter than the header, wrong magic, another version, or parameters out of range;
    - `backup_wrong_passphrase`: the key check differs (a wrong passphrase, or a changed header byte);
    - `backup_tampered`: a chunk fails authentication (a changed byte, a reordered, extra or removed chunk, or a cut inside a chunk);
    - `backup_truncated`: every chunk authenticates but the last one read isn't marked final (a cut at a chunk boundary, or no chunk at all).
  - No message, `repr` or traceback ever holds the passphrase or a key.
- **Port** (`nextron/ports/backup_target.py`).
  - `BackupTargetPort`:
    - `put(source: Path, name: str) -> StoredCopy(name, sealed_size, plain_size, plain_sha256)`;
    - `list() -> tuple[str, ...]`: the names of the files directly in the target, sorted;
    - `get(name, target: Path) -> FetchedCopy(plain_size, plain_sha256)`: decrypts into the new file `target`;
    - `delete(name)`: removes it; a missing name does nothing.
  - `BackupTargets = Callable[[Path], BackupTargetPort]`: a target for a folder.
  - The code constants: the four crypto codes above, `backup_name_invalid`, `backup_target_exists`, `backup_target_unavailable` (retryable), `backup_source_unreadable` and `backup_copy_missing`.
  - Each call raises `AdapterError` only.
- **Folder adapter** (`nextron/adapters/backup_target/__init__.py`): `FolderBackupTarget(folder: Path, *, secrets: SecretsPort, params: ScryptParams = DEFAULT_SCRYPT)`.
  - A name must be a plain file name: no `\`, `/` or `:`, not `.` or `..`, at most 255 characters. Otherwise `backup_name_invalid`, before any I/O.
  - `put` and `get` read the passphrase with `read_credential(secrets, CredentialKey.BACKUP_PASSPHRASE)` first, on every call, and never keep it.
  - **`put`.** In order:
    1. `folder.mkdir(exist_ok=True)`: the last level only, never its parents;
    2. open `source` (`rb`) and `<folder>\<name>.part` (`xb`);
    3. `seal`, then flush and `os.fsync`;
    4. rename the part to `<name>`. Windows refuses an existing name, which is `backup_target_exists`.

    Any failure deletes the part (best effort) and raises. It streams: no `shutil`, no `.copy(…)`.
  - **`get`.** Opens `<folder>\<name>` (missing: `backup_copy_missing`) and the new `target` (`xb`), then `unseal`s. A failure deletes `target` and raises.
  - **`list`.** A missing folder gives `()`.
  - An `OSError` on the folder or the part is `backup_target_unavailable`, and one on the source is `backup_source_unreadable`.
  - No error carries a path, the passphrase or an exception's text. It never re-raises bare: the error contract applies.
- **Domain** (`nextron/domain/backup.py`, stdlib only).
  - **Constants.**
    - `ERASURE_LEDGER_FILE = "erasure-ledger.jsonl"`: the ledger in the backup dir, which Story 4.x writes;
    - `OFFPC_SUFFIX = ".enc"`;
    - `OFFPC_ROTATION_DAYS = 35`, equal to `MAX_ROTATION_DAYS`.
  - **`OffpcStatus(StrEnum)`:** `copied`, `failed` and `not_configured`. `.label` is "copied off the PC", "off-PC copy failed" or "off-PC not configured".
  - **`LedgerStatus(StrEnum)`:** `copied` and `absent`.
  - **Names.**
    - `offpc_names(env, at) -> (db_name, ledger_name)`: `copy_name(env, at) + ".enc"` and `nextron-<env>-<stamp>.ledger.jsonl.enc`.
    - `offpc_stamp(env, name) -> datetime | None`: the UTC stamp of either shape, with or without a trailing `.part`; `None` for any other name.
  - **`offpc_rotation(env, names, now, days) -> tuple[str, ...]`:** the names to delete, which are those with a stamp older than `now - days`. It never selects the newest complete `.db.enc` copy, nor the ledger with the same stamp.
  - **`offpc_problem(folder: str, data_dir, backup_dir) -> str | None`:**
    - `folder_not_set` for a blank folder;
    - `folder_in_data_dir` when it is the data dir or inside it;
    - `folder_in_backup_dir` when it is the backup dir or inside it;
    - else `None`.

    It compares casefolded `PureWindowsPath` parts, with no I/O.
  - **`same_volume(folder, data_dir) -> bool`:** the casefolded drives are equal (a drive letter or `\\server\share`).
  - **`is_offpc_folder(text) -> bool`:** blank, or an absolute Windows path. That is a drive path or a UNC share, with no `..` part and not a `\\?\` or `\\.\` device path.
  - **`offpc_failed_text(kind, backup_id)`** is `"Off-PC backup copy failed: <kind> <ULID>"`.
  - **`OffpcCopyMismatch(DomainError)`**, code `offpc_copy_mismatch`.
- **Setting** **`backup_offpc_dir`** (STRING), in Section Engine after `backup_rotation_days`.
  - Its default is `""` and its rule is `is_offpc_folder`.
  - Its label is "Off-PC backup folder". Its unit text gives an example drive and an example share, and says:
    - each daily backup is encrypted with the Backup passphrase and copied there;
    - copies older than 35 days are deleted;
    - blank keeps backups on this PC only.
- **Persistence.**
  - **Migration `0008_1_17_offpc`** (`downgrade()` raises) adds nullable columns to `backups` with SQLite `ALTER TABLE … ADD COLUMN` (raw `op.execute`). This was verified with SQLite 3.53.1: a named column CHECK can reference other columns, and it is enforced.
    - `offpc_status` (String 16);
    - `offpc_name` (Text);
    - `offpc_ledger` (String 16);
    - `offpc_at` and `offpc_deleted_at` (UtcDateTime text);
    - `offpc_error` (String 500).

    It adds three constraints:
    - `ck_backups_offpc_status`: `offpc_status IS NULL OR IN ('copied','failed','not_configured')`;
    - `ck_backups_offpc_ledger`: `offpc_ledger IS NULL OR IN ('copied','absent')`;
    - `ck_backups_offpc`:
      - `NULL`: every off-PC column is `NULL`;
      - `copied`: the row is `verified`, with a name, a ledger status and a time, and no error;
      - `failed`: the row is `verified`, with an error and a time, and no name, ledger status or `offpc_deleted_at`;
      - `not_configured`: the row is `verified`, with a time only.

    Add these to the model, `owners.py` and `pii_registry.py` (`offpc_error` is `redact`; the others are `none`).
  - **`db/repos/ops/backups.py`.** `BackupRow` gains the six columns. It adds:
    - `set_offpc(backup_id, *, status, name, ledger, at, error) -> int`, only while `offpc_status IS NULL`;
    - `newest_offpc(statuses)`;
    - `offpc_failed_after(since)`, oldest first;
    - `mark_offpc_deleted(names, at)`, while `NULL`.
- **ops** (`app/ops/api.py`).
  - **`OffpcBackups(clock, *, env, data_dir, backup_dir, targets: BackupTargets)`.**
    - **`destination()`** runs inside a transaction. It reads `backup_offpc_dir`. `InvalidSettingValue` propagates. It returns the folder `Path`, or the `offpc_problem` reason.
    - **`send(taken, folder) -> OffpcOutcome`** makes no DB access and never raises an `Exception`:
      1. `put(taken.path, db_name)`;
      2. compare the returned `plain_sha256` with `taken.snapshot.sha256`. A mismatch deletes the copy (best effort) and fails with `OffpcCopyMismatch`;
      3. when `<backup dir>\erasure-ledger.jsonl` is a file, `put(ledger, ledger_name)` gives `copied`, else `absent`;
      4. `credential_not_set` gives `not_configured` (reason `passphrase_not_set`), and any other error gives `failed` (`error_text`, `error_code`).
    - **`@command record(backup_id, outcome)`**.
    - **`rotate(folder, backup_id) -> int`.** Outside any transaction it lists the folder, then deletes each name `offpc_rotation(env, names, now, OFFPC_ROTATION_DAYS)` selects. Then, in one transaction, it `mark_offpc_deleted`s the deleted `.db.enc` names. It logs `offpc_backups_rotated` (`count`) when the count is above 0.
  - **`Backups(clock, local, *, notify, queue, offpc: OffpcBackups)`**, with `offpc` required.
    - After a verified row commits, and before local rotation, `back_up` runs the off-PC step:
      1. a transaction that reads `destination()`; a problem records `not_configured` in the same transaction;
      2. `send`, outside any transaction;
      3. a transaction that `record`s the outcome. For `failed` it also calls `notify.alert(BACKUP_FAILED, id, offpc_failed_text(kind, id), ALL_CHANNELS)`;
      4. after the commit, it logs `offpc_backup_copied` (INFO: `backup_id`, `file`, `ledger`), `offpc_backup_failed` (ERROR: `backup_id`, `code`) or `offpc_backup_skipped` (INFO: `backup_id`, `reason`);
      5. a `copied` outcome then runs the guarded `rotate`. Its failure logs `offpc_rotation_failed` (ERROR, `backup_id`, `code`).
    - An `InvalidSettingValue` gives `failed`.
    - Any exception in steps 1 or 3 logs `offpc_record_failed` (ERROR, `backup_id`, `code`). The step never fails the backup: `back_up` still returns its id and the job ends `done`.
  - **`record_pre_migrate`** stays local only.
- **Queries.** `queries.ops.health_issues()` lists the local failures, as before. It then lists each `offpc_status = failed` row newer than the newest `copied` or `not_configured` one, oldest first, as `HealthIssue(BACKUP_FAILED, id, offpc_failed_text(kind, id), detail="Error: " + redact(offpc_error))`. The registry is unchanged.
- **Doctor.** **`check_offpc_backup(read, offpc_dir, data_dir, backup_dir)`** in `startup_checks.py`, id `offpc_backup`, registered after `internal_notify_grant` and before `required_settings`.
  - It reads the passphrase state first (`_credential_state` on `backup/passphrase`, through Credential Manager), then `offpc_dir()` (the database).
  - It warns `off-PC backup not configured (OQ-A3): backups are local only; <every reason, with its fix>` for an `offpc_problem`, or a passphrase that is not set, too long or unreadable. A folder that can't be read (any error, by type name) gives the same warning.
  - It warns `off-PC backup folder <folder> is on the same drive as the data dir (<drive>); …` when `same_volume` holds.
  - Otherwise it is `ok`.
  - It makes no I/O on the folder.
  - `ReadinessDatabase` gains `offpc_dir() -> str`. `DoctorDatabase` gives `""` with no file, else reads in a read-only unit; `EngineDatabase` reads in the caller's unit.
  - The doctor now has twelve ids.
- **Composition** (`main.py`).
  - `build_app(..., backup_targets: BackupTargets | None = None)` defaults to `functools.partial(FolderBackupTarget, secrets=secrets)`.
  - `build_offpc_backups(config, clock, targets)`.
  - The `Backups` wiring passes `offpc=`.
- **Web.** The panel's Backups line appends `, <OffpcStatus.label>` when `offpc_status` is set. A failed off-PC copy also gets `health-backup-failed`.
- **Tests** run as `test`, in temporary folders only. The scrypt parameters are `ScryptParams(10, 8, 1)` unless a test is about the default.
  - `adapters.crypto` and `adapters.backup_target` are `NoExternalSystem` in `tests/live_guard.py` and are added to contract c5.
  - `tests/fakes/backup_target.InMemoryBackupTarget` (it stores plaintext and can be scripted to fail any call) is in `PORT_FAKES`.

**Never:**
- **No secret anywhere.** The passphrase, a key or a planted secret never appears in a file, row, log, error or response.
- **Nothing is sent unencrypted off the PC.** No plaintext is written to the off-PC folder, not even a `.part` file.
- **No network or folder I/O in a GET.** That includes the doctor check.
- **The copy contract stands.** No narrowing of `test_backup_contracts.py`.
- **Out of scope:**
  - the restore test and its `get`-based checks (1.18);
  - `nextron restore` (1.19);
  - writing the erasure ledger (Story 4.x);
  - a passphrase-strength rule;
  - any change to the Credentials page.
- **What not to edit.** Don't change the vendored HTMX, the spine or the planning docs, or anything outside `nextron-engine/` except this spec, `sprint-status.yaml` and `deferred-work.md`.

### Decisions made overnight (pending user review)

1. **No new dependency.** The spine's Stack table already pins `cryptography 50.0.2`, which is in `pyproject.toml` and `uv.lock`. Contract 4 already confines it to adapters.
2. **Only daily and manual backups go off the PC.** A `pre_migrate` copy stays local: it guards an upgrade, and a network call before the runner starts could hold startup on a slow share. Its row's off-PC columns stay `NULL`.
3. **"Not configured" covers several cases:** a blank folder, the data or backup dir or a folder inside either, or no passphrase. The row records `not_configured`, with no alert, and doctor warns. A stored folder that no longer parses, or any other error, is `failed`, with an alert. A folder on the data dir's drive still copies, and doctor warns.
4. **Off-PC rotation is fixed at 35 days.** It doesn't follow `backup_rotation_days`: the off-PC copy is the disaster copy.
   - It lists the folder and deletes only this environment's backup names: `.db.enc`, `.ledger.jsonl.enc` and stale `.part` files. It never deletes the newest complete copy or its ledger, so it also catches copies that have no row.
   - It runs after a successful copy only.
5. **The file format** adds a 16-byte key check to the AC's header (from a second scrypt-derived key, never the encryption key), so a wrong passphrase gets its own error. The chunk index is the GCM nonce, safe because every file has a fresh salt and therefore its own key. The index and the final flag are also in each chunk's AAD.
6. **The scrypt parameters** are n = 2^17, r = 8 and p = 1 (128 MiB, about 0.3 s per file on this PC). They are stored per file, so they can rise later. The passphrase is NFC-normalised UTF-8.
7. **Each off-PC copy is checked.** The plaintext SHA-256 computed while encrypting must equal the row's, or the copy is deleted and fails.
8. **The adapter creates only the folder's last level**, so a wrong or unplugged drive fails instead of growing a new tree. It writes `<name>.part` and then renames it, so a half-written file never has a final name.
9. **A failed off-PC copy isn't retried automatically**, as with Story 1.16's decision 3. If the ledger copy fails after the database copy, the outcome is `failed`, and the database copy stays until rotation.
10. **The panel lists each off-PC failure** until a later backup is `copied` or `not_configured`.
11. **"Same volume" compares drive names only** (no I/O, because the panel runs the check inside a GET). A folder on a mounted volume under `C:\` isn't detected.
12. **The crypto adapter has no port.** It is the folder adapter's internal helper and is tested directly. The fake the AC asks for is the `BackupTargetPort` fake.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Copied | daily backup; folder set; passphrase set; no ledger | `<folder>\nextron-test-<stamp>.db.enc` decrypts to the row's SHA-256; row `copied`, `offpc_ledger=absent`, name and time; `offpc_backup_copied`; no `.part` left | N/A |
| Ledger | `<backup dir>\erasure-ledger.jsonl` exists | `…ledger.jsonl.enc` beside it, decrypting to the ledger's bytes; `offpc_ledger=copied` | N/A |
| Not configured | folder blank, the data or backup dir, or inside one; or passphrase missing | local backup verified; row `not_configured`; no alert; `offpc_backup_skipped` with reason; nothing written off-PC | N/A |
| Copy fails | folder unreachable, refused, or a hash mismatch | row `failed` with error; alert `backup_failed` (telegram and dashboard) "Off-PC backup copy failed: daily <ULID>"; a panel issue; the job is `done` | no part left; logged |
| Decrypt | a changed byte; truncated mid-chunk or at a boundary; wrong passphrase; bad magic | `backup_tampered`; `backup_tampered`/`backup_truncated`; `backup_wrong_passphrase`; `backup_bad_format`; the target file is deleted | typed `AdapterError` |
| Rotation | copies 40, 36 and 34 days old plus a new one, an old ledger and an old `.part`, another env's old copy, an unrelated file | the 40- and 36-day copies, their ledgers and the old part deleted; their rows marked; the rest kept; the newest kept even when 40 days old | a delete error: logged, the backup is still fine |
| Doctor | unset; is the backup dir; no passphrase; same drive; separate drive and passphrase | warn (not configured); warn; warn; warn (same drive); ok | an unreadable folder: warn |
| Panel | newest row copied, failed or not configured | "…, verified (daily), copied off the PC" / "…, off-PC copy failed" (failed class) / "…, off-PC not configured" | N/A |
| Planted secret | the passphrase and a credential planted in keyring | neither appears in the local copy, the `.enc` files, the rows or the log | N/A |

</frozen-after-approval>

## Code Map

- **ops** (`nextron-engine/nextron/app/ops/api.py`).
  - `Backups.back_up` (643-682): the off-PC step goes after `_backup_completed`, before `_rotate_after`.
  - `LocalBackups` and `_rotate_after` are the patterns for the guarded steps. `ALL_CHANNELS`, `error_text` and `error_code` are already imported.
  - The module docstring is binding documentation: update it.
- **Domain** (`nextron-engine/nextron/domain/backup.py`): `copy_name`, `_STAMP`, `is_copy_name`, `backup_failed_text` and `is_env_name` are the patterns. `startup_checks._is_within` and `_parts` are the casefolded-parts pattern.
- **Persistence.**
  - `nextron-engine/nextron/db/alembic/versions/0007_1_16_backups.py`: the docstring and `downgrade` text.
  - `db/models/ops.py` `BackupRecord`, `db/owners.py` `TABLES` and `db/pii_registry.py` `PII`.
  - `db/repos/ops/backups.py`: `_COLUMNS` derives from `BackupRow`'s fields, so keep the field order equal to the select.
- **Queries** (`nextron-engine/nextron/app/queries/ops.py`): `health_issues`.
- **Ports and adapters.**
  - The patterns are `nextron/ports/alert.py` and `nextron/adapters/telegram/__init__.py` (`read_credential` at call time, `raise … from None`, no logging).
  - Update the docstrings of `nextron/adapters/__init__.py` and `nextron/ports/__init__.py` if they list packages.
  - `tests/contracts/test_error_contracts.py` forbids a bare `raise` and exception text in adapters: use `try`/`finally` with a success flag for cleanup.
- **The credential** is already declared: `CredentialKey.BACKUP_PASSPHRASE`, keyring `backup/passphrase`, not required (`nextron/domain/credentials.py`).
- **Doctor** (`nextron-engine/nextron/startup_checks.py`): `_credential_state` (463) and `check_backup_dir_separate` (348) are the patterns. Update the docstring's list.
- **`nextron-engine/nextron/main.py`.**
  - `build_doctor_checks` (1047), `ReadinessDatabase` (943), `DoctorDatabase` (956), `EngineDatabase` (1004), `build_app` (408-490) and `build_local_backups` (569).
  - Update the docstring's Backups and Doctor paragraphs: "eleven" becomes "twelve".
- **Settings** (`nextron-engine/nextron/domain/settings_schema.py`):
  - `Key` (133) and `SCHEMA` (829); the new setting goes after `BACKUP_ROTATION_DAYS` (881-897).
  - A default must pass the rules.
  - The settings contract checks only the key and the label for secret words.
- **Web** (`nextron-engine/nextron/web/health_page.py`): `backup_text` (119) and `panel_context` (156-158).
- **Pins to update.**
  - **Migration head.**
    - `HEAD` in `tests/contracts/test_persistence_contracts.py:72`, `tests/unit/test_migrations.py:38` and `tests/app/test_serve_process.py:263`;
    - the chain probes at `test_persistence_contracts.py:670-682` (0008 → 0009, 0009 → 0010, "revision 9");
    - `tests/unit/test_serve.py:732,746` ("0008_more" → "0009_more");
    - `tests/contracts/test_source_tree.py` `FILES`, which also lists the new modules.
  - **Doctor ids** (twelve; `offpc_backup` before `required_settings`):
    - `tests/contracts/test_doctor_contracts.py`;
    - `DOCTOR_IDS` in `tests/unit/test_runner_lifespan.py:371`;
    - `READINESS` in `tests/dashboard.py:76`;
    - `tests/unit/test_doctor.py:59,182`, `tests/unit/test_serve.py:523` and `tests/unit/test_startup_checks.py:808`;
    - `tests/unit/test_health_page.py`;
    - README 1230 and 1339.

    Doctor's "all ok" tests now need a passphrase and a separate-drive folder.
  - **`Backups(...)` callers:** `tests/app/test_backups.py:92,1007` and `tests/dashboard.py:212`.
  - **Settings field order:** `tests/unit/test_settings_schema.py` and `test_settings_page.py`.
  - **`BackupRow(...)` built positionally** anywhere in the tests.
- **Test infrastructure.** `tests/live_guard.py` `ADAPTER_PACKAGES`, `pyproject.toml` contract c5's sources, `tests/fakes/__init__.py` `PORT_FAKES` and `tests/fakes/secrets.py` `InMemorySecrets`.
- **README** (`nextron-engine/README.md`):
  - "Backups" (843): off-PC copies, the format, escrow, a changed passphrase keeping the old one for older copies, rotation and what isn't done yet;
  - "Settings" (290);
  - the migrations table;
  - the doctor list;
  - "Engine health" (1196).

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/domain/backup.py`, `domain/settings_schema.py`: the off-PC names, statuses, rotation, problem and volume rules, the folder rule, the text, `OffpcCopyMismatch` and the `backup_offpc_dir` setting.
- [x] `nextron-engine/nextron/ports/backup_target.py`, `adapters/crypto/__init__.py`, `adapters/backup_target/__init__.py`: the port, the format v1 sealing and the folder adapter.
- [x] `nextron-engine/nextron/db/alembic/versions/0008_1_17_offpc.py`, `db/models/ops.py`, `db/owners.py`, `db/pii_registry.py`, `db/repos/ops/backups.py`: the columns, the constraints and the repository functions.
- [x] `nextron-engine/nextron/app/ops/api.py`, `app/queries/ops.py`: `OffpcBackups`, the off-PC step in `back_up`, and the issues.
- [x] `nextron-engine/nextron/startup_checks.py`, `main.py`: `check_offpc_backup`, the `ReadinessDatabase.offpc_dir`s, the registration, `build_offpc_backups` and the `build_app` wiring.
- [x] `nextron-engine/nextron/web/health_page.py`: the off-PC label on the Backups line.
- [x] `nextron-engine/pyproject.toml` (c5), `tests/live_guard.py`, `tests/fakes/backup_target.py`, `tests/fakes/__init__.py`: the adapter registration and the fake.
- [x] `nextron-engine/tests/unit/test_backup_crypto.py`:
  - round trips of 0 bytes, 1 byte, 65,536, 65,537 and 3 chunks, each giving the plaintext's size and SHA-256;
  - the header fields;
  - a fresh salt per file;
  - each decrypt error in the matrix (a changed byte in the header, in a tag and in a body; a cut at a chunk boundary and inside a chunk; an extra chunk; swapped chunks; params out of range);
  - NFC equivalence;
  - one test that the default params derive a key.
- [x] `nextron-engine/tests/unit/test_backup_target.py`:
  - the folder adapter: `put`/`get`/`list`/`delete`;
  - no `.part` left on any failure;
  - an existing name refused;
  - the name rules;
  - the passphrase read on every call (a change between calls is used) and `credential_not_set` when missing;
  - a missing source or folder parent;
  - `get` of a missing name;
  - no planted passphrase in any written file or in an error's `str`, `repr` or formatted traceback.
- [x] `nextron-engine/tests/unit/test_backup_domain.py`: the names and stamps, every `offpc_rotation` case in the matrix, `offpc_problem` (with case and slash variants), `same_volume` (drives and UNC), `is_offpc_folder` and the texts.
- [x] `nextron-engine/tests/app/test_backups.py`, with real units and the real folder adapter on a temporary folder (or the fake for scripted failures):
  - every matrix row for `back_up`;
  - `pre_migrate` writing nothing off-PC;
  - the off-PC record failing (logged, job `done`);
  - rotation (including its own failure path);
  - `health_issues`;
  - raw-SQL violations of the three new constraints, each refused by name, plus valid rows of each status. Also check that the 0007 constraints still refuse after 0008.
- [x] `nextron-engine/tests/unit/test_startup_checks.py`, `tests/unit/test_doctor.py`: each doctor matrix row, and that the passphrase is read before the folder (order spy).
- [x] `nextron-engine/tests/unit/test_health_page.py`: each off-PC label and the failed class.
- [x] `nextron-engine/tests/unit/test_runner_lifespan.py`, with the real `build_app`, `InMemorySecrets` holding a passphrase and `backup_offpc_dir` set to a temporary folder: a POST `/health/backup` leaves a `copied` row and a `.db.enc` that the real adapter decrypts to the row's SHA-256. The planted-secret check runs here too.
- [x] The pins in the Code Map, and `nextron-engine/README.md` as listed there.

**Acceptance Criteria:**
- Given the full suite, when `uv run pytest -q -n 6 --dist loadgroup` and `uv run lint-imports` run, then everything passes and all 29 contracts are kept.
- Given `tests/contracts/test_backup_contracts.py`, when it scans the new adapters, then it passes unchanged.
- Given the error, isolation and source-tree contracts, when they run, then each new adapter is classified, in c5 and faked, and raises only `AdapterError`.

## Implementation Notes

- **Where the code is.**
  - Domain: `nextron/domain/backup.py` (the off-PC names, stamps, rotation, problem and volume
    rules, `is_offpc_folder`, `offpc_failed_text`, `OffpcCopyMismatch`) and the
    `backup_offpc_dir` setting in `domain/settings_schema.py`.
  - Port: `nextron/ports/backup_target.py` (also `check_backup_name`/`is_backup_name`, shared
    by the adapter and the fake). Adapters: `nextron/adapters/crypto` (format v1) and
    `nextron/adapters/backup_target` (`FolderBackupTarget`).
  - Persistence: migration `0008_1_17_offpc`; `BackupRecord`'s six columns and three checks;
    `owners.py`, `pii_registry.py`; `set_offpc`, `newest_offpc`, `offpc_failed_after` and
    `mark_offpc_deleted` in `db/repos/ops/backups.py`.
  - Services: `OffpcOutcome`, `OffpcBackups` and `Backups._copy_off_pc`/`_destination` in
    `app/ops/api.py`; the off-PC issues in `app/queries/ops.py`.
  - Doctor: `check_offpc_backup` in `startup_checks.py`; `offpc_dir` on the three readiness
    databases, the registration and `build_offpc_backups` in `main.py`.
  - Panel: `backup_text` and `backup_failed` in `web/health_page.py`.
- **The migration's checks.** SQLite inserts an added column's text before the table
  constraints of the stored `CREATE TABLE`, on the same line as the previous column. So the
  three named CHECKs are all column constraints of the last column, `offpc_error`, each starting
  on its own line; the schema test (`test_job_contracts.schema_differences`) then reads them,
  and their text equals the model's character for character. The outcome check uses SQLite's
  `IS 'copied'` (NULL-safe), so a row with a `NULL` status and any other off-PC column set is
  refused instead of passing on a `NULL` comparison.
- **Small additions beyond the spec.**
  - `seal` refuses parameters a reader would refuse (`backup_bad_format`), and a passphrase that
    can't be encoded (a lone surrogate) is `backup_wrong_passphrase` in both directions.
  - Names also refuse control characters (a `NUL` would make `open` raise `ValueError`).
  - `get` into an existing `target` is `backup_target_exists`, and any other `OSError` on
    `get`'s target is `backup_target_unavailable`; `get` deletes `target` only when it created
    it. `put` never deletes a stale `.part` it didn't create (that is `backup_target_unavailable`).
  - `rotate` keeps deleting after one delete fails, marks the deleted copies, then raises the
    first failure (logged by the guard as `offpc_rotation_failed`); `offpc_backups_rotated` also
    carries `backup_id`.
  - `nextron.domain.backup.drive_of` (the drive the same-drive warning names).
- **Test parameters.** Every test uses `ScryptParams(10, 8, 1)` except the default-parameters
  crypto test and `test_runner_lifespan.py`'s real-app test, which uses `build_app`'s default
  `backup_targets` (the real adapter, `DEFAULT_SCRYPT`) to prove the wiring.
- **Line endings.** Files rewritten by scripts were put back to CRLF, as the worktree checks
  them out (`core.autocrlf=true`); the committed blobs are unchanged by it.

**Verification** (through the suite lock): `uv run pytest -q -n 6 --dist loadgroup` gave 4394
passed, 2 skipped, and `uv run lint-imports` 29 kept, 0 broken.

Review fixes (pass 1, P1–P11; see the Review Triage Log):
- **P1 (A).** `FolderBackupTarget.delete`, and `get` on a missing file, raise the retryable
  `backup_target_unavailable` when the folder itself isn't there (`is_dir()` is false): Windows
  reports an unplugged drive or an offline share as a missing file, so rotation no longer marks
  a copy deleted that it couldn't reach, and `get` no longer says `backup_copy_missing`. `list()`
  is unchanged. Test: `test_a_folder_that_is_not_there_is_unavailable_to_get_and_delete`.
- **P2 (C).** README: changing `backup_offpc_dir` leaves the old folder's copies, which the engine
  never rotates; delete them 35 days after the change, or move them into the new folder.
- **P3 (D).** The `backup_db` `max_runtime` comment names the encrypted off-PC copy too (still 30
  minutes).
- **P4 (G).** `is_offpc_folder` also refuses, in every name after the drive (the UNC server and
  share included), any of `<>"|?*`, a `:` (an NTFS stream), a reserved device name (`CON`, `PRN`,
  `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`, any case, with or without an extension) and a
  trailing `.` or space. Cases in `test_backup_domain.py` and the setting-refusal test.
- **P5 (H).** README: a share or letter that points back at this PC (`\\localhost`,
  `\\127.0.0.1`, this PC's name, an admin share such as `C$`, a `subst` or mapped letter) isn't
  off the PC, and doctor can't tell.
- **P6 (I).** `InMemoryBackupTarget.get` wraps `open(target, "xb")`: `FileExistsError` is
  `backup_target_exists`, any other `OSError` `backup_target_unavailable`. Both cases are in the
  fake's own test.
- **P7 (J).** `test_0008_keeps_every_0007_backup_row_with_no_off_pc_outcome`
  (`test_migrations.py`): a 0007 database with a verified daily, a failed manual and a verified
  `pre_migrate` row upgrades to head with every row unchanged and the six off-PC columns `NULL`;
  a `copied` outcome is then accepted on the verified row and refused on the failed one
  (`ck_backups_offpc`).
- **P8 (L).** `test_a_passphrase_that_cannot_be_read_fails_the_off_pc_copy_and_alerts`: a
  `keyring_failed` read through `back_up` with the real adapter is `failed`, with the alert.
- **P9 (N2).** README: keep an old passphrase until no copy made before the change is left in the
  folder (at least 35 days, longer while no new copy succeeds).
- **P10 (S).** An empty passphrase (a hand-made keyring entry) is `credential_not_set` in `put` and
  `get`, before any I/O (`FolderBackupTarget._passphrase`). Test:
  `test_an_empty_passphrase_is_credential_not_set_and_writes_nothing`.
- **P11 (U).** `test_runner_lifespan.py` asserts the engine panel's `offpc_backup` row: the
  exact "not configured" warning (no folder, no passphrase) in the in-engine doctor test, and the
  same-drive warning naming the configured folder in the real-adapter test.

**Final verification** (after pass 1, through the shared suite lock):
- `uv run pytest -q -n 6 --dist loadgroup`: 4425 passed, 2 skipped (`ai_eval` and the symlink
  test), in 88 s.
- `uv run lint-imports`: 29 kept, 0 broken.

Before the review fixes: 4394 passed, 2 skipped, and 29 kept.

Not verified here: a real network share or second drive (only temporary local folders), a
real passphrase in Credential Manager, and the encrypted copy of a production-size database.

## Spec Change Log

## Review Triage Log

**Pass 1 (2026-11-17).**
- **Sources:** BH = blind hunter (14 findings), EC = edge-case hunter (12), VG = verification gap (2 gaps, no other findings).
- **Counts:** 28 raw findings. Some make several claims (BH2 two, BH4 three, BH13 two, EC9 two), so there are 33 claims, which make 24 entries after deduplication.
- **Outcomes:**
  - 11 entries patched (P1–P11): 4 code fixes, 3 doc or comment fixes and 4 test gaps;
  - 1 deferred;
  - 12 rejected.
- **No loopback.** No finding is an intent gap or a bad spec.
- **Rejected only because the fix would change the approved spec:**
  - B (the AC's "deleted after a successful copy");
  - E3 (decision 9);
  - M (decision 3).

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| A | BH1, EC1, EC2 | An unreachable off-PC folder reads as a missing one. On an unplugged drive, `delete` returns quietly (so `rotate` marks rows deleted whose files are still there), and `get` raises the non-retryable `backup_copy_missing` instead of `backup_target_unavailable`. | medium | Verified on this PC: `Path(r"Q:\NextronBackups\a.enc").unlink(missing_ok=True)` returns quietly, and `os.scandir` raises `FileNotFoundError` (WinError 3). `get` maps every `FileNotFoundError` to `backup_copy_missing`, which breaks the port's code table (that code means "no file of that name") and misleads Story 1.18's restore test while a NAS is offline. The fix, in `delete` and in `get`'s `FileNotFoundError` branch: when `folder.is_dir()` is false, raise `backup_target_unavailable`. `list()` keeps the spec's "a missing folder gives ()": it deletes nothing. | P1 |
| B | BH2 (part), EC4, EC11 | Off-PC rotation runs only after a `copied` outcome. While copies fail, or come out `not_configured`, old copies outlive 35 days. | medium | True, and specified by the AC itself: "off-PC copies older than 35 days are deleted after a successful copy". Each failure meanwhile raises a Telegram alert and a panel issue, and `not_configured` shows on the doctor row. Precedent: Story 1.16 entry C. | reject |
| C | BH2 (part), EC5 | After `backup_offpc_dir` changes, the old folder's copies are never rotated, and nothing tells the Operator. Erased data then stays there past AD-17's 35 days. | medium | Verified: rows keep only `offpc_name`, and `rotate` lists only the folder configured now. Deleting files from a folder the Operator no longer names would surprise him, and remembering every past folder is new design. The smallest fix is a direct correction: say so in the README's off-PC section (delete the old folder's copies yourself, 35 days after the change, or move them to the new folder). | P2 |
| D | BH3 | The off-PC step has no time budget. A slow share could pass `backup_db`'s 30-minute `max_runtime`, and the `max_runtime` comment still says "a copy, its check and its hash". | low | The database is small (MBs), and encryption runs at about 4 GB/s here. An overrun is the designed hang path: the heartbeat is withheld and the supervisor restarts the engine. The stale comment is real, and fixing it is a direct correction: name the off-PC copy in it. | P3 |
| E1 | BH4 (part) | The ledger is read live, without a snapshot, so once Story 4.x appends to it, an off-PC ledger copy can end in a torn line. | maybe-false | No writer exists yet. Whether it matters depends on how Story 4.x appends (flushed whole lines) and whether Story 1.19's reapply tolerates a torn last line. If true, it would be medium. | defer |
| E2 | BH4 (part) | The ledger `put`'s `StoredCopy` (its size and SHA-256) is discarded, so nothing on the row lets a later check verify the ledger copy. | low | True. No AC checks the ledger copy (Story 1.18 checks the database copy), and recording it needs new columns. | reject |
| E3 | BH4 (part) | When the ledger `put` fails, the database copy already written keeps a complete name with no ledger beside it. | low | Specified by decision 9 ("the database copy stays until rotation"). The row is `failed`, so Story 1.18 (which picks the newest `copied` row) never uses it. | reject |
| F | BH5 | Nothing reads the encrypted file back after the rename, so a bad write stays `copied` until the restore test. | low | True by design. AD-20 places the end-to-end check in the monthly restore test (Story 1.18). The write is `fsync`ed before the rename, and a read-back would double the network I/O of every backup. | reject |
| G | BH6, EC9 (part) | `is_offpc_folder` accepts paths Windows refuses or reads differently: `<>"|?*`, an NTFS stream (`E:\Backups:ads`), `CON`, and a component ending in a dot or space. A trailing dot aliases the folder without it, so `<data dir>.` puts the copies in the data dir while they are recorded as copied off the PC. | medium | Verified: `offpc_problem` compares the written parts, and Windows strips a trailing dot. The others fail every backup, which at least alerts. The fix is a direct correction of "an absolute Windows path": refuse those characters, a `:` after the drive, a component ending in `.` or a space, and a reserved device name (CON, PRN, AUX, NUL, COM1–9, LPT1–9, with any extension). | P4 |
| H | BH7, EC9 (part) | A path that points back at this PC (`\\localhost\C$\…`, `\\127.0.0.1\…`, `\\<this PC>\…`, a `subst` or mapped letter) passes as off-PC. | low | True. Decision 11 accepts a name-only comparison, and only I/O could resolve this PC's own name or a `subst` drive. The README names only mounted volumes, so the direct correction is to name these too. | P5 |
| I | BH8 | `InMemoryBackupTarget.get` lets a raw `FileExistsError` or `FileNotFoundError` out, against the port's "AdapterError only" rule. | low | Verified at `tests/fakes/backup_target.py:71`. Story 1.18's tests will use this fake. The fix is a direct wrap: `backup_target_exists` and `backup_target_unavailable`, as the real adapter raises. | P6 |
| J | BH9 | No test takes a 0007 database that already holds backup rows through `0008`. | low | Verified: `test_migrations.py` only moves `HEAD`. Mac's databases already hold backup rows, and a failed upgrade stops startup. The fix is a direct test addition: rows of each kind and status at 0007 survive the upgrade with every off-PC column `NULL`, and a recorded outcome is accepted after it. | P7 |
| K | BH10 | `ck_backups_offpc` accepts an off-PC outcome on a `pre_migrate` row. | low | Decision 2 is enforced in code (`record_pre_migrate` never calls `OffpcBackups`) and tested (`test_a_pre_migrate_copy_stays_local`). Adding a `kind` clause changes the specified constraint. | reject |
| L | BH11, VG2 (gap) | No test runs a `keyring_failed` passphrase read through `back_up`. It must stay `failed` with an alert; only `credential_not_set` is `not_configured`. | medium | Pre-verified by VG: mapping `keyring_failed` to `not_configured` passed 61 tests. Add the case: the real `FolderBackupTarget`, `world.secrets.failing.add("read")`, then `assert_offpc_failed(..., "keyring_failed", ...)`. | P8 |
| M | BH12 | No alert fires when the off-PC copies stop (`copied` → `not_configured`), and the panel's off-PC failures clear. | medium | True, and specified by decision 3 ("records `not_configured`, with no alert, and doctor warns") and the AC ("doctor warns … and backups continue locally"). The doctor row on the panel shows it. | reject |
| N1 | BH13 (part) | Neither the copy nor its row records which passphrase sealed it. | low | True. After several passphrase changes, finding the right one is trial and error. The README tells Mac to keep each old passphrase with its date. A key id is new format surface. | reject |
| N2 | BH13 (part) | The README says to keep an old passphrase until its copies "have rotated out, 35 days later". The newest complete copy is kept however old it is. | low | Verified at README:955-956. The fix is a direct correction: keep it until no copy made before the change is left in the folder (at least 35 days, and longer while no new copy succeeds). | P9 |
| O | BH14, EC12 | `Scrypt(...).derive` can raise `MemoryError` (or an OpenSSL `ValueError`) unwrapped, against the port's "AdapterError only" rule. | low | True in principle: n = 2^17 needs 128 MiB, and an accepted header can ask for 256 MiB. That is unlikely on this PC. `send` catches every `Exception` and records `failed`. The fix needs a new error code (public surface), so it is not a direct correction. Story 1.18's `get` caller must catch it (noted for 1.18). | reject |
| P | EC3 | If `mark_offpc_deleted`'s transaction fails after the deletes, the rows never get `offpc_deleted_at`, and the files can't be listed again to retry. | low | True, but rare: the database is locked right after it committed. `offpc_deleted_at` is informational, and the newest `copied` row's file is never deleted. | reject |
| Q | EC6 | A SHA-256 mismatch whose `delete` also fails leaves a mismatched copy with a complete name. | low | It needs a double failure. The row is `failed`, and Story 1.18 picks by row, never by file name. | reject |
| R | EC7 | A passphrase removed between the database `put` and the ledger `put` is recorded as `not_configured`, though a copy was written. | low | A window of under a second during a backup. The fix adds a branch. | reject |
| S | EC8 | A keyring entry with an empty password reads as `""`. `read_credential` checks only `None`, so the copy is sealed under an empty passphrase (a publicly derivable key) and recorded `copied`, while doctor says "not set". | medium | Verified: `KeyringSecrets.read` returns the value as stored, and the Credentials page can't store `""`, but a hand-made entry can. The fix is a direct correction in `FolderBackupTarget.put` and `get`: an empty passphrase is `credential_not_set` before any I/O, which matches doctor. | P10 |
| T | EC10 | Doctor's same-drive warning stays hidden while another reason is shown. | low | The next run after a fix shows it, so discovery is sequential. The fix adds a branch. | reject |
| U | VG1 (gap) | The engine panel's `offpc_backup` row is never asserted, so a broken `EngineDatabase.offpc_dir` passes. | medium | Pre-verified by VG: a raising `offpc_dir` passed 6 tests. Assert the row in `test_the_built_health_panel_runs_doctor_in_the_engine_without_a_side_effect` (the "not configured" warning naming no folder and no passphrase), and in `test_back_up_now_copies_the_backup_off_the_pc_encrypted_with_the_real_adapter` (the same-drive warning naming the configured folder). | P11 |

## Design Notes

```python
# Backups.back_up, after the verified row committed (inside self._lock, outside any transaction)
_backup_completed(backup_id, taken)
self._copy_off_pc(backup_id, taken)        # never raises an Exception; logs its outcome
_rotate_after(self._local, backup_id)       # local rotation, as before
return backup_id
```

The adapter decides nothing about environments, names or age: `ops` and the domain name the files and choose what rotates. The adapter only encrypts, writes, lists, decrypts and deletes inside one folder. Story 1.18 calls `get(row.offpc_name, tmp)` and compares `FetchedCopy.plain_sha256` with the row's `sha256`.

## Verification

**Commands:**
- `cd nextron-engine; uv run pytest -q -n 6 --dist loadgroup` -- expected: all pass (`ai_eval` and the symlink test skipped). Run the full suite only through the suite lock.
- `uv run lint-imports` -- expected: 29 contracts KEPT.
