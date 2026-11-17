---
title: 'Story 3.8: Issue and decode unsubscribe tokens'
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: '278a49a236bc0bb497bff707e9ba8a54bbf67017'
review_loop_iteration: 0
story_key: '3-8-issue-and-decode-unsubscribe-tokens'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Every Step's `List-Unsubscribe` link (RFC 8058, CAN-SPAM, PECR) needs an opaque token that identifies only a hashed address, so a click opts the person out without the address ever leaving the PC (AD-15). Nothing issues, records or decodes such a token yet, and no key material exists for it.

Requirements: FR-70, AD-1, AD-9, AD-12, AD-15, AD-17, NFR-17, NFR-35.

**Approach:**
- A pure `domain.unsub_token` fixes the byte format.
- `TokenCodecPort` gets a `cryptography` adapter (AES-256-GCM-SIV plus a truncated HMAC-SHA256 tag) in `nextron/adapters/crypto/`, and an in-memory fake.
- `Compliance.issue_unsub_token(om)` records `unsub_tokens` (migration `0020_3_8_unsub_tokens`).
- Per-`kid` keys `k_tok` and `k_edge` go in Credential Manager through the credentials registry, with write-only rows on the Credentials page. A new `unsub_active_kid` setting picks the active key id, and a new doctor check, `unsub_token_keys`, tests that kid's keys.
- A committed vector fixture binds every other implementation (the Worker, Story 3.10).

The acceptance criteria are Story 3.8 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**
- **Token format** (`nextron/domain/unsub_token.py`, stdlib only):
  - `raw` = `kid` (1 byte) ‖ `nonce` (12 bytes) ‖ `ct` ‖ `tag`.
  - `ct` = AES-256-GCM-SIV under `k_tok[kid]`, with that nonce and no associated data, of `addr_hash` (32 bytes) ‖ `issued_at` (8 bytes, unsigned big-endian Unix seconds). That is 40 bytes of plaintext and 56 of ciphertext.
  - `tag` = the first 12 bytes of HMAC-SHA256 under `k_edge[kid]` over `raw[:69]`.
  - `raw` is 81 bytes; the token is base64url of it without padding: exactly 108 characters of `[A-Za-z0-9_-]`.
  - `kid` is 1–255; 0 is reserved and is never a known kid.
  - `addr_hash = bytes.fromhex(key_hash("address", address))` (`domain.contactability`).
  - `token_hash(token)` is the lowercase hex SHA-256 of the token's ASCII bytes. 3.11 reuses it.
- **The domain module also holds:**
  - `TokenInvalidReason`: `malformed`, `unknown_kid`, `bad_tag`, `bad_ciphertext`.
  - `TokenInvalid(reason)` and `DecodedToken(kid, addr_hash, issued_at: int)`, both frozen.
  - `parse_key(text) -> bytes | None`: the 32 bytes of a canonical base64url key (43 characters, or 44 with one trailing `=`); anything else is `None`.
  - `encode_key(raw)`: the canonical unpadded text.
  - `KEY_MIN_LIFE` (731 days) and `key_in_use(newest_issued_at, now)`.
  - `UnsubKeyInUse(DomainError)`, code `unsub_key_in_use`, message `Key <kid> still has tokens under 2 years old`.
- **The port, `nextron/ports/token_codec.py`.** `TokenCodecPort` has two methods:
  - `encode(kid, addr_hash, issued_at) -> str`. A bad argument raises `TypeError` or `ValueError`. A missing or malformed key raises `AdapterError(False, "unsub_key_missing", ...)`, whose message names the keyring entry, never a value.
  - `decode(token) -> DecodedToken | TokenInvalid`. It never raises for any token value, a non-`str` included (`malformed`).
  - Either method lets a Credential Manager failure (`AdapterError`) propagate.
- **The adapter, `nextron/adapters/crypto/unsub_token.py`:** `TokenCodec(secrets, *, nonce=os.urandom)`. It reads each key at call time through a new `ports.secrets.read_unsub_key(secrets, key, kid) -> bytes | None` and never caches one. Decode runs in this order:
  1. the shape (otherwise `malformed`);
  2. the kid (0, or no valid `k_edge`: `unknown_kid`);
  3. the tag, compared with `hmac.compare_digest` before any decryption (`bad_tag`);
  4. `k_tok` (missing or malformed: `unknown_kid`);
  5. the decryption (`InvalidTag`: `bad_ciphertext`).

  It touches no database.
- **The credentials registry, `nextron/domain/credentials.py`.** A per-kid family sits beside `REGISTRY`: `UnsubKey` (`k_tok`, `k_edge`), and `UnsubKeyCredential(key, label, secret_prefix, required=True)` with `secret_name(kid)` (`unsub/k_tok/<kid>`, `unsub/k_edge/<kid>`) and `meta_name(kid)`. Lookups go through `unsub_key(key)` and `declared_unsub_keys()`. `REGISTRY`, `declared()` and the `required_credentials` check are unchanged.
- **The `unsub_tokens` table:**
  - Columns: `id` ULID primary key; `token_hash` String(64), unique, 64 lowercase hex; `om_id` ULID, unique, with no foreign key; `kid` Integer from 1 to 255; `issued_at` UtcDateTime, whole seconds. All are NOT NULL.
  - Triggers: `no_delete`, `immutable` (any `UPDATE`) and `no_reuse` (an `INSERT` whose `id`, `token_hash` or `om_id` exists). The message is `unsubscribe token records are permanent`.
  - Owner `compliance`; every column is `none` in `pii_registry.py`. The repository is `db/repos/compliance/unsub_tokens.py`.
- **`Compliance(clock, *, codec=None).issue_unsub_token(om) -> str`** (`@command`). `om` has `id` (a ULID, otherwise `ValueError`) and `to_address` (a `str`, otherwise `TypeError`). It runs these steps:
  1. A row for `om.id` raises `TokenAlreadyIssued(DomainError)` ("token already issued for this message") before any keyring read.
  2. It reads the kid from `queries.setup.get(Key.UNSUB_ACTIVE_KID)`.
  3. It takes the clock's now, at whole seconds.
  4. It encodes, inserts the row and returns the token.

  Any failure propagates and dooms the caller's transaction. With no codec it raises `TypeError`. It logs nothing and writes no `events` row.
- **`queries.compliance`:** `newest_unsub_token(kid) -> datetime | None` and `newest_unsub_tokens() -> Mapping[int, datetime]`.
- **The `unsub_active_kid` setting:** integer, section Sending, label "Unsubscribe token key id", default 1, rules min 1 and max 255.
- **`Credentials` (`app/setup/api.py`)** gains three methods:
  - `unsub_keys()`. It reads the setting and `newest_unsub_tokens()` in the caller's unit of work, then the keyring. It lists the active kid, `active+1` (`next`, while it is 255 or less) and every kid with tokens, ascending. Each kid has a role (`active`, `next`, `in_use` or `retired`) and the statuses of `k_tok` and `k_edge`.
  - `save_unsub_key(kid, key, text)`. The text must pass `parse_key`, or `InvalidCredentialValue`; the canonical form is stored, then the meta entry. Replacing a value that is already set raises `UnsubKeyInUse` while `key_in_use` holds.
  - `remove_unsub_key(kid, key)`. It raises `UnsubKeyInUse` while `key_in_use` holds; otherwise it deletes both entries.

  Both write methods read `newest_unsub_token(kid)` in the caller's unit of work before any keyring write.
- **Web:**
  - `POST /credentials/unsub/save` (`unsub_key_save`) takes exactly `kid`, `key` and `value`. `POST /credentials/unsub/remove` (`unsub_key_remove`) takes `kid`, `key` and an optional `confirm=yes` (two steps, as today). `kid` must match `[1-9][0-9]{0,2}` and be 255 or less, and `key` must be `k_tok` or `k_edge`; anything else is `MalformedForm`.
  - Both are `@personal_data(False)` and run the service call inside `units.open(read_only=True)`.
  - Each answers with the swapped row `unsub_key_row.html` (`id="unsub-key-<kid>-<key>"`). A refusal is a 200 with `role="alert"`.
  - `credentials.html` gains an "Unsubscribe token keys" section, which says to escrow `k_tok` and to put `k_edge` in the Worker as `K_EDGE_<kid>`.
  - No value is ever shown or echoed.
- **The doctor check `unsub_token_keys`** (`startup_checks.check_unsub_token_keys(read, active_kid)`):
  - It is `fail` when the active kid's `k_tok` or `k_edge` is missing, isn't 32 bytes of base64url, or can't be read, when the two are equal, or when the setting can't be read. The message names the missing key's keyring entry; otherwise it is `ok`.
  - It is registered last, after `database_revision`.
  - `ReadinessDatabase` gains `unsub_active_kid()`: `DoctorDatabase` gives the schema default when there is no database file, and `EngineDatabase` reads in the current unit of work.
- **Vectors.** `tests/fixtures/unsub_token_vectors.json` holds keys for kids 1 and 2, valid cases (kid, nonce, address, `addr_hash`, `issued_at`, token) and tampered tokens. Each tampered token carries its `reason` and an `edge` verdict (`accept` only where the tag verifies, i.e. `bad_ciphertext`). A test re-derives every token independently, with raw `AESGCMSIV` and stdlib `hmac`.
- **Wiring.** `PORT_FAKES` maps `TokenCodecPort` to `tests/fakes/token_codec.FakeTokenCodec`. The `crypto` live-guard reason and the c5 comment name the codec, and contract c5 already lists `nextron.adapters.crypto`.

**Never:**
- No Worker code (3.10), polling, `unsub_requests` or `unsubscribe_by_token` (3.11), and no `suppression_entries` write.
- No address, token, key or `addr_hash` in logs, `events`, job payloads or any column. Only `token_hash` is stored.
- No new dependency or job type. No editing outside `nextron-engine/`, except this spec, `sprint-status.yaml` and `deferred-work.md`.

### Decisions made overnight (pending user review)

1. **Format details the AC leaves open.** `issued_at` is unsigned big-endian. There is no AES associated data, because the tag already covers `kid`. A token is exactly 108 characters. Kid 0 is reserved, so a zeroed buffer always reads as `unknown_kid`.
2. **A fourth reason, `malformed`,** covers a wrong length or alphabet, padding, a non-`str` and an empty token, whose tag can't even be located.
3. **The per-kid keys are a family next to `REGISTRY`, not entries in it.** Their names depend on a kid. `required=True` means "required for the active kid", and `unsub_token_keys` enforces it (`fail`), not `required_credentials`.
4. **Replacing a key that is already set is refused like a removal** while the kid has tokens under 2 years old: a new value breaks those tokens just the same. A key that isn't set (one restored from escrow) can always be entered. "2 years" is 731 days.
5. **The key's keyring writes run inside a read-only unit after the token read.** Its `BEGIN IMMEDIATE` serialises them with issuance, so no token can be issued between the check and the write.
6. **`issue_unsub_token` encodes inside the caller's transaction** (two local Credential Manager reads), as the spine's single call at enqueue requires. A missing key rolls the enqueue back: fail closed.
7. **`unsub_token_keys` reads Credential Manager after the database.** The entry name depends on a setting, so it can't follow the order rule's "keyring first". It runs last, so only its two reads happen under the read lock, and the order-rule docstring says so.
8. **On decode, a Credential Manager failure propagates as `AdapterError`,** so 3.11 retries; it is never called an invalid token. A missing or malformed key gives `unknown_kid`.
9. **`unsub_tokens` has a ULID `id` and permanence triggers,** so the 2-year check can't be defeated by deleting rows.
10. **`k_tok == k_edge` fails the doctor check,** because Cloudflare would then hold the decryption key.
11. **The Credentials page lists a `next` kid** (the active kid + 1), so its keys can be entered and deployed to the Worker before `unsub_active_kid` switches to it.
12. **"Already set" means a valid key is stored** (added during implementation). While the kid has tokens under 2 years old, a stored 32-byte key can't be replaced, not even by the same value. A missing or malformed stored value can always be replaced: it decodes nothing, and a garbled entry must not get stuck behind the removal refusal.
13. **A narrow exception in the settings contract** (added during implementation). The label "Unsubscribe token key id" holds the secret words "token" and "key", which `tests/contracts/test_settings_contracts.py` refuses. `NAMED_NOT_SECRET` exempts exactly that key, that label and the `integer` type, which can't hold a key; probes show any other type, label or key still fails.
14. **Equal keys are also refused when saving and when encoding** (review patch A), beyond decision 10's doctor check. The Credentials page refuses a `k_tok` equal to the same kid's `k_edge`, or the reverse, with "k_tok and k_edge of a key id must differ". `encode` raises `unsub_key_missing`, so no token is issued under equal keys.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Issue | stand-in OM, kid 1 keys set | 108-char token; one `unsub_tokens` row; it decodes to `key_hash` of the address and the clock's second | N/A |
| Repeat | same `om.id` again | no keyring read, no row | `TokenAlreadyIssued` |
| Active kid 2 | setting 2, keys for 2 | token's first byte is 2 | N/A |
| Key missing | active kid has no `k_edge` | nothing recorded | `AdapterError unsub_key_missing` |
| Tampered | any vector | its `reason` | returned, never raised |
| Garbage | `None`, `""`, 107/109/500 chars, `+/=`, non-ASCII | `malformed` | never raises |
| Save key | 43-char base64url | stored canonical; row `Saved.` | N/A |
| Bad key | 31 bytes, std base64, spaces inside | nothing stored | refused, 200 alert |
| Remove in use | token issued 1 year ago under kid | nothing deleted | `Key 1 still has tokens under 2 years old` |
| Remove retired | newest token 732 days old | deleted | N/A |
| Doctor | active kid key missing or malformed | `unsub_token_keys` fail naming `unsub/k_tok/1` | N/A |

</frozen-after-approval>

## Code Map

- `nextron/domain/contactability.py`: `key_hash`. Reuse; don't change.
- `nextron/domain/credentials.py`: `REGISTRY`, `Credential`, `UnknownCredential`, `InvalidCredentialValue`. Add the family; keep `REGISTRY`'s nine entries as they are.
- `nextron/ports/secrets.py`: `read_credential` (line ~99) is the pattern for `read_unsub_key`. `ports/email_syntax.py` and `tests/fakes/email_syntax.py` show the port and fake style; `tests/fakes/__init__.py` holds `PORT_FAKES`.
- `nextron/adapters/crypto/__init__.py`: the backup format, the docstring style and `from None` errors. Update its "no port of its own" docstring and `nextron/adapters/__init__.py`'s adapter list.
- `tests/live_guard.py:134` (the `crypto` reason); `pyproject.toml` c5 comment (~line 268).
- `nextron/app/compliance/api.py`: `Compliance`. Add `issue_unsub_token`, keeping `suppress` unchanged. `app/queries/compliance.py` and `db/repos/compliance/__init__.py` docstrings.
- **The permanence contract** (`tests/contracts/test_suppression_contracts.py`). `GUARDED` covers `app/compliance/`, `db/repos/compliance/`, `app/queries/compliance.py` and two domain modules. There, no name may have a word starting `delet`, `remov`, `purg`, `expir`, `ttl`, `prun`, `evict`, `truncat` or `drop`, and nothing may use `timedelta`, an SQL date modifier, or a comparison with `created_at`/`voided_at`. Keep the 2-year arithmetic in `domain/unsub_token.py`.
- `nextron/db/models/compliance.py`, `db/alembic/versions/0019_3_3_suppression_entries.py`: the model, trigger and docstring pattern; `0019` is the down revision. Also `db/models/__init__.py`, `db/owners.py` (~line 210) and `db/pii_registry.py`.
- `nextron/domain/settings_schema.py`: `Key` (~line 135) and the `BULK_THREADS` declaration (~line 910) as the integer pattern.
- `nextron/app/setup/api.py:833`: `Credentials`; its docstring is at ~line 112.
- `nextron/web/credentials_page.py`, `web/app.py:528-590` (credential routes), and `templates/credentials.html` and `credential_row.html`.
- `nextron/startup_checks.py`: `_credential_state` (~482), `check_required_credentials` and `check_offpc_backup` (pattern), and the id constants (~144).
- `nextron/main.py`: `ReadinessDatabase`, `DoctorDatabase` and `EngineDatabase` (~1440-1540); `build_doctor_checks` (~1558) and its order-rule docstring; the module docstring (~285-330).
- **Pinned doctor ids:** `tests/unit/test_doctor.py`, `test_startup_checks.py`, `test_health_page.py`, `test_serve.py`, `test_runner_lifespan.py`, `tests/app/test_engine_health.py`, `tests/contracts/test_doctor_contracts.py` and `tests/dashboard.py`. Fake `ReadinessDatabase`s need `unsub_active_kid`, and "all ok" fixtures need kid 1's keys.
- `nextron/domain/redaction.py`: rule 9 already masks 32+ `[A-Za-z0-9_-]` runs. Only add a test.
- **HEAD bumps:** `tests/contracts/test_persistence_contracts.py:81` and its chain probes (`0020_next` becomes `0021_next`); `tests/unit/test_migrations.py:39`; `tests/migration_chains.py`; `tests/contracts/test_source_tree.py` `FILES`. Watch the backup, restore and snapshot tests that enumerate tables.
- `tests/contracts/test_credential_contracts.py` and `test_settings_contracts.py`, and the settings-page tests that post every key.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/unsub_token.py`, the `credentials.py` family, `ports/token_codec.py`, `ports/secrets.py` `read_unsub_key`: format, values, port.
- [x] `nextron/adapters/crypto/unsub_token.py` and `tests/fakes/token_codec.py`, plus the `PORT_FAKES`, live-guard, c5-comment and docstring updates.
- [x] The model, the migration `0020_3_8_unsub_tokens`, `owners.py`, `pii_registry.py`, `repos/compliance/unsub_tokens.py` and the `queries.compliance` readers.
- [x] `Compliance.issue_unsub_token` and `TokenAlreadyIssued`.
- [x] The setting, the `Credentials` methods, the page section, the routes and `unsub_key_row.html`.
- [x] `check_unsub_token_keys`, the `ReadinessDatabase` method, its registration, and the docstrings.
- [x] `tests/fixtures/unsub_token_vectors.json` (generated once, with the generator logic kept inside the test as the independent re-derivation).
- [x] Tests: `tests/unit/test_unsub_token_domain.py`, `tests/unit/test_token_codec.py` (vectors, tampering, garbage fuzz, tag-before-decrypt, keyring failure), `tests/app/test_unsub_tokens.py` (every matrix row, triggers, no address anywhere), Credentials service and page tests, doctor check tests, and the redaction case.
- [x] The HEAD bumps and pinned-list fixes in the Code Map.
- [x] `nextron-engine/README.md`: a Story 3.8 section with the exact token format and the vector file, written for Story 3.10, plus the key-rotation procedure.

**Acceptance Criteria:**
- Given the code base, when `tests/contracts` and `lint-imports` run, then every contract holds (the permanence contract included), and `TokenCodecPort` has a fake that satisfies it.
- Given the migration at head, when the schema is read, then `unsub_tokens` has its columns, checks, unique indexes and three triggers, and `owners.py` and `pii_registry.py` cover every column.

## Implementation Notes

**Where things are.** Format and values: `nextron/domain/unsub_token.py` (constants, `check_kid`/`check_addr_hash`/`check_issued_at`/`check_nonce`, `addr_hash_of`, `plaintext`/`split_plaintext`, `edge_tag`, `assemble`, `parse_token` -> `RawToken`, `tag_matches`, `token_hash`, `parse_key`/`encode_key`, `KEY_MIN_LIFE`/`key_in_use`, `UnsubKeyInUse`, `TokenAlreadyIssued`). Key family: `UnsubKey`, `UnsubKeyCredential`, `UNSUB_KEYS`, `unsub_key`, `declared_unsub_keys` in `nextron/domain/credentials.py` (labels "Token encryption key (k_tok)" and "Worker tag key (k_edge)"). Port: `nextron/ports/token_codec.py` (`TokenCodecPort`, `UNSUB_KEY_MISSING`, `checked_encode`); `read_unsub_key` in `ports/secrets.py`. Adapter: `nextron/adapters/crypto/unsub_token.py` (`TokenCodec`). Fake: `tests/fakes/token_codec.py` (`FakeTokenCodec`, kid 1 by default). Table: `UnsubTokenRecord` in `db/models/compliance.py`, migration `0020_3_8_unsub_tokens` (down revision `0019_3_3_suppression_entries`), repository `db/repos/compliance/unsub_tokens.py` (`for_om`, `newest_issued_at`, `newest_by_kid`, `insert`). Command: `Compliance(clock, *, codec=None).issue_unsub_token(om)`. Setting `unsub_active_kid`. `Credentials.unsub_keys`, `unsub_key_status`, `save_unsub_key`, `remove_unsub_key` with `UnsubKeyRole`, `UnsubKeyStatus`, `UnsubKidKeys`. Web: `credentials_page.py` (`UnsubKeyRow`, `UnsubGroup`, `unsub_groups`, `unsub_key_row`, `submitted_unsub_save`/`submitted_unsub_remove`), routes `unsub_key_save`/`unsub_key_remove` in `web/app.py`, `templates/unsub_key_row.html`, section in `credentials.html`. Doctor: `startup_checks.check_unsub_token_keys` (id `UNSUB_TOKEN_KEYS`), `ReadinessDatabase.unsub_active_kid`, registered last in `main.build_doctor_checks`.

**Choices the frozen spec left open (implementer):**
1. `save_unsub_key` strips spaces at either end before `parse_key` (as the credential rows do); a padded 44-character key is stored as its canonical 43 characters. Blank is `ENTER_A_VALUE`, anything else refused is `NOT_A_KEY`.
2. "A value that is already set" is read as "a valid key is stored": while `key_in_use` holds, replacing a stored valid key is refused (even with the same value); a missing or malformed stored value can always be replaced, since it decodes nothing, so a garbled entry can't deadlock with the removal refusal.
3. A fourth `Credentials` method, `unsub_key_status(kid, key)` (keyring only), renders the swapped row after a refusal, a store failure or the first Remove click.
4. Beyond the listed schema: `ck_unsub_tokens_issued_at` (`issued_at LIKE '%.000000Z'`, whole seconds) and the non-unique `ix_unsub_tokens_kid_issued_at` for the newest-token reads.
5. `TokenAlreadyIssued.code` is `unsub_token_already_issued`.
6. The setting's label "Unsubscribe token key id" holds the secret words `token` and `key`, which `tests/contracts/test_settings_contracts.py` refuses. The contract gained one narrow exception, `NAMED_NOT_SECRET` (exactly that key, that label and the `integer` type), with probes proving a string type, another label or another key still fails.
7. `settings_schema` can't import `domain.unsub_token` (circular through `contactability` -> `markets` -> `settings_schema`), so it declares `UNSUB_MIN_KID`/`UNSUB_MAX_KID` (1, 255); `test_unsub_token_domain.py` asserts they equal the format's bounds.
8. The adapter raises only `AdapterError` (error contract), so the port's `TypeError`/`ValueError` for bad arguments come from `ports.token_codec.checked_encode` (the domain checks), as `email_syntax.checked_address` does.
9. `GET /credentials` now reads the database (in the GET's read-only unit), so `test_credentials_page.py`, `test_google_page.py` and `test_environment_isolation.py` build their dashboards on real units of work. The unsubscribe rows use `class="credential unsub-key"` (the existing `.credential` styling; `app.css` untouched).
10. In the engine's health panel the two `unsub_token_keys` keyring reads run inside the read transaction (decision 7); `test_runner_lifespan.py` now asserts those two are the only reads inside it, and the last.
11. `check_unsub_token_keys` treats a kid the setting returns outside 1-255, or not an `int`, as "could not be read (<TypeName>)".
12. Nothing in `main.py` builds a `Compliance` with a `TokenCodec` yet: there is no caller until Story 5.2.

**Review patches (pass 1).** `save_unsub_key` also reads the kid's other key (a failed read is `CredentialStoreFailed`) and refuses an equal value with `InvalidCredentialValue` `SAME_KEY` ("k_tok and k_edge of a key id must differ") before any write; `TokenCodec.encode` refuses equal keys (constant-time compare) with `unsub_key_missing` naming both entries. `GET /credentials` reads the credential statuses and both Google reads before `unsub_keys()`, so only the unsubscribe keys' reads run inside the read transaction. `_unsub_role` is active, then `in_use`, then `next`, then `retired`. `FakeTokenCodec` uses a process-wide counter. The `unsub_key_row.html` hint says "(or 44 ending in =)". README, the `unsub_token` docstring and the vector description now give `addr_hash` as `key_hash("address", ...)` over `normalize_email` (stripped, NFC, lowercased, IDNA domain), computed only by the engine; the vectors' `strip().lower()` shortcut holds only for their ASCII addresses (fixture regenerated: description only). New tests: equal keys on save and encode, two fakes never share a token, the lowered-active-kid `in_use` role, `EngineDatabase.unsub_active_kid` with kid 2 and the check naming `unsub/k_tok/2`, a failed read in the save's in-use check, `in_transaction()` true at every keyring write and delete, the page's read order, a failed confirmed remove and unreadable unsubscribe rows, and a redaction line holding a token and an address.

**Orchestrator, after pass 1.**
- Overnight decisions 12–14 record implementer choices 2 and 6 and review patch A.
- `deferred-work.md` has three new Story 3.8 entries:
  - for 3.11, a token whose tag verifies but whose `k_tok` is missing (finding 2);
  - for 3.12/3.16, key protection after a restore (finding 12);
  - for Mac, checking a key re-entered from escrow (finding 3).

**Final verification:**
- Targeted run (the Verification files, the Google, environment-isolation, runner-lifespan and suppression tests, and `tests/contracts`): 2326 passed, 1 skipped (`ai_eval`).
- Full suite through `suite_lock.py` (`track-B`): 9501 passed, 3 skipped (`ai_eval` ×2, and a symlink test this account can't run).
- `lint-imports`: 29 kept, 0 broken.

## Spec Change Log

## Review Triage Log

Pass 1 (review loop 0). Three layers: blind hunter (BH, 13 findings), edge-case hunter (EC, 8) and verification gap (VG, 3 gap findings and 2 others). That is **26 raw** findings, **19 after grouping**: 11 patched, 0 deferred, 8 rejected.

| # | Layer | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| 1 | BH | `k_tok` equal to `k_edge` is accepted by `save_unsub_key` and used by `encode`; only doctor reports it. | medium | Nothing in `save_unsub_key` reads the sibling key, and `TokenCodec.encode` uses whatever it reads. Equal keys hand Cloudflare (`K_EDGE_<kid>`) the decryption key, against AD-15's separation. | patch (A) |
| 2 | BH | A token with a valid tag but a missing `k_tok` decodes as `unknown_kid`, which looks like a forgery. | low | Real, but it is the frozen spec's decode order (step 4). 3.11 records only processed tokens, so the next poll retries it while KV keeps it (30 days). The fix changes the spec. Noted for 3.11. | reject (spec change) |
| 3 | BH | A key re-entered from escrow isn't checked against the original, so a wrong copy silently breaks that kid's live links. | medium | Real: only `token_hash` and a time are stored, so nothing can check a key. The frozen spec says a key that isn't set "can always be entered", and the fix needs a new stored key-check value. | reject (spec change; flag for Mac) |
| 4 | BH | Nothing guards a change of `unsub_active_kid` to a kid without keys, and the README says step 3 "can't silently break new links". | low | Issuance fails closed (`unsub_key_missing` rolls the enqueue back) and doctor and the health panel fail, so the break is loud, as the README says. A cross-owner check on settings save adds a guard for an Operator error that is already reported. | reject |
| 5 | BH | `GET /credentials` reads the Google client and grants from Credential Manager while the read-only unit's `BEGIN IMMEDIATE` is held. | medium | VG's probe recorded `google/client_config`, `google/internal_notify` and `google/postmaster` inside the transaction: `unsub_keys()` (a DB read) runs before the Google calls in the handler. That breaks uow's "no port call inside a transaction" beyond the spec's exception. | patch (B) |
| 6 | BH | A database failure now takes down the whole Credentials page (500). | low | True: `unsub_keys()` is unguarded. A settings read failing on a GET isn't everyday use; the rest of the dashboard depends on the database too, and the fix adds a guard and a degraded rendering. | reject |
| 7 | BH | Keys stored for a kid that is neither active, next nor issued vanish from the page; and `_unsub_role` checks `next` before `in_use`. | low | Both are real. The orphan part (EC6) is harmless unused entries, still visible in Windows Credential Manager, and listing them needs a scan of 255 kids: rejected. The role order is a direct correction. | patch (C), orphan part rejected |
| 8 | BH | Rows of an in-use kid still offer Save and Remove, which fail; the row hint omits "44 ending in =". | low | Remove and replace answer with a clear refusal; hiding them adds role-dependent branches: rejected. The hint text is a direct correction. | patch (K), rest rejected |
| 9 | BH | `FakeTokenCodec` numbers tokens per instance, so two fakes give the same first token. | low | `counter = len(self._tokens)` in `tests/fakes/token_codec.py`. A test with two fakes on one database would hit `uq_unsub_tokens_token_hash`. A direct correction. | patch (H) |
| 10 | BH | The setting's hint renders "working.; default: 1". | false | `settings_page.py:95` appends `; default: …` to every unit, and every unit ends with a period (`… when the engine restarts.`). The same rendering applies to every setting, so this one is consistent. | reject |
| 11 | BH | The `addr_hash` recipe omits `normalize_email`'s IDNA step, and the vector recipe's ASCII shortcut isn't said to be ASCII-only. | low | README and docstring say "trimmed, NFC'd and lowercased". The Worker never computes `addr_hash` (it checks only the tag), so a doc correction suffices. | patch (I) |
| 12 | BH | The 2-year rule rests on `unsub_tokens` rows, which a restore to an older backup can lose. | low | It needs a restore, a kid activated after that backup, and then a manual key removal. Recovery mode (1.19) already holds sending, and the fix adds guards. Noted for 3.12 and 3.16. | reject |
| 13 | BH | No test shows the keyring write runs while the read-only unit holds `BEGIN IMMEDIATE`. | low | `test_the_newest_token_is_read_before_any_keyring_write` checks only call order. Decision 5 rests on this. Test-only. | patch (J) |
| 14 | EC | Google keyring reads run under the lock on `GET /credentials`. | medium | Same as finding 5. | patch (B) |
| 15 | EC | A database failure takes the whole page to 500. | low | Same as finding 6. | reject |
| 16 | EC | Saving the same value as `k_tok` and `k_edge` succeeds. | medium | Same as finding 1. | patch (A) |
| 17 | EC | `encode` doesn't refuse identical keys. | medium | Same as finding 1. | patch (A) |
| 18 | EC | `_unsub_role` labels an in-use active+1 kid `next`. | low | Same as finding 7 (role order). | patch (C) |
| 19 | EC | Orphaned keys become invisible on the page. | low | Same as finding 7 (orphan part). | reject |
| 20 | EC | Removing the active kid's key, while it has no tokens, makes every enqueue fail. | low | The frozen spec refuses removal only while the kid is in use. It fails closed and doctor and the panel fail; same root as finding 4. | reject |
| 21 | EC | The AC's redaction test (a log line holding a token and an address) isn't there: the new test has no address. | medium | `test_unsubscribe_tokens_and_keys_are_masked_whole` redacts tokens and keys only. The AC asks for both in one line. | patch (D) |
| 22 | VG | The in-engine `EngineDatabase.unsub_active_kid` is only tested with the default kid. | medium | Filed with evidence: no engine-path test stores a non-default kid, so a hard-coded 1 would pass. | patch (E) |
| 23 | VG | A failed keyring read in `save_unsub_key`'s in-use check is untested. | medium | Filed with evidence: only `write` failures are tested. Treating an unreadable entry as unset would bypass decision 4 unseen. | patch (F) |
| 24 | VG | The page's failed remove and an unreadable key row are untested. | low | Filed with evidence: the equivalent tests cover only the credential rows. | patch (G) |
| 25 | VG | (other) Google reads in the transaction. | medium | Same as finding 5. | patch (B) |
| 26 | VG | (other) `next` before `in_use`. | low | Same as finding 7 (role order). | patch (C) |

## Verification

**Commands** (from `nextron-engine/`):
- `uv run pytest -q tests/unit/test_unsub_token_domain.py tests/unit/test_token_codec.py tests/app/test_unsub_tokens.py tests/unit/test_doctor.py tests/unit/test_startup_checks.py tests/unit/test_health_page.py tests/app/test_engine_health.py tests/unit/test_settings_schema.py tests/unit/test_settings_page.py tests/unit/test_migrations.py` plus the credentials tests -- expected: all pass.
- `uv run pytest -q tests/contracts` and `uv run lint-imports` -- expected: all pass, every contract kept.
- The full suite through `suite_lock.py` (`track-B`), by the orchestrator only -- expected: exit 0.
