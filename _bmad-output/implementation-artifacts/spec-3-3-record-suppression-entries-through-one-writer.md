---
title: 'Story 3.3: Record suppression entries through one writer'
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: 'dcbcf88761b82c25e5bff3ef47f325b5ca6a166b'
review_loop_iteration: 0
story_key: '3-3-record-suppression-entries-through-one-writer'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The engine has no Suppression List. Every later outreach path needs one: the gate (3.4), the panel (3.5), unsubscribe tokens and polling (3.8, 3.11), erasure (3.13), the ledger (3.16) and reply opt-outs (Epic 7). AD-16 says it must be permanent and hashed, with exactly one writer, so a person who said no is matched however they reappear and no plain copy of their address is kept.

Requirements: FR-50, FR-63, AD-3, AD-4, AD-16, AD-17, NFR-1, NFR-36.

**Approach:**
- A pure `domain.contactability` holds the suppression keys: `key_hash(kind, value)` and the key sets for a Contact.
- A pure `domain.suppression_entry` holds the SuppressionEntry machine.
- Migration `0019_3_3_suppression_entries` creates `suppression_entries` (owner `compliance`). DB triggers make it permanent.
- `Compliance(clock).suppress(subject, scope, source, cause_ref)` (`@command`) is the only writer. It writes one `active` row and one `events` row per new key, then publishes `SubjectSuppressed`.
- `main.build_subscribers` registers `Pipeline.close_suppressed`, which closes the Lead `lost` for opt-out sources, using the 2.2 machine's `close_lost`.
- A contract test proves the single writer and that nothing deletes or expires an entry.

The acceptance criteria are Story 3.3 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**
- **`nextron/domain/contactability.py`** (stdlib and `nextron.domain` only; frozen, slotted dataclasses):
  - `KeyKind` (`StrEnum`): `address`, `domain`, `person`, `profile`.
  - `SuppressionScope` (`StrEnum`): `person`, `firm`.
  - `ContactSubject(contact_id)`: a non-ULID `contact_id` raises `ValueError`.
  - `normalised_key(kind, value) -> str` uses only `domain.identity`:
    - address: `normalize_email(value)`;
    - domain: `registrable_domain(value)`;
    - person: `value` is a `(full_name, web_domain)` pair, giving `normalize_name(full_name) + "|" + registrable_domain(web_domain)`;
    - profile: `normalize_linkedin_url(value)`.
  - **Argument errors:** a wrong value type (a `str` for person, a pair for any other kind) raises `TypeError`, and an unknown kind raises `ValueError`. An `IdentityError` propagates.
  - `key_hash(kind, value) -> str` is the lowercase 64-hex SHA-256 of the UTF-8 bytes of `kind + ":" + normalised_key(kind, value)`. Story 3.8's `addr_hash` is `bytes.fromhex(key_hash("address", a))`.
  - `SuppressionKey(kind, value_hash, domain_plain)`: `domain_plain` is set exactly for the domain kind, as the normalised domain.
  - `person_keys(*, email, name, linkedin_url, web_domain) -> tuple[SuppressionKey, ...]`, in this order:
    - the address key, when `email` is set;
    - the person key, when both `name` and `web_domain` are set;
    - the profile key, when `linkedin_url` is set.
  - `firm_domain_keys(*, email, web_domain)` gives domain keys, in this order:
    - the registrable domain of the address's domain part;
    - then the web domain, when it differs.

    A domain where `domain.markets.is_free_webmail` is true is never a key. A value that fails normalisation (`IdentityError`) gives no key ("not known"); it never raises out of these two functions.
- **`nextron/domain/suppression_entry.py`** (AD-4 machine):
  - `EntryStatus` (`active`, `voided`); `TERMINAL_STATUSES = (voided,)`.
  - `SuppressionSource`, the AC's 7 sources; `OPT_OUT_SOURCES` = `reply_rule`, `reply_class`, `one_click`, `mailto`; `VOIDABLE_SOURCES` = `reply_rule`.
  - `EntryEvent` (`void`); `EntryCause` = the 7 sources plus `operator_reversal`.
  - `CREATION[source] = Transition(active, EntryCause(source))`; `TRANSITIONS` = {(active, void): Transition(voided, operator_reversal)}.
  - `transition()` raises `IllegalTransition` for every other pair, including anything from `voided`.
- **Table `suppression_entries`** follows the AC, with these columns:
  - `id` ULID; `kind` String(16); `value_hash` String(64); `domain_plain` String(253), nullable; `status` String(16); `source` String(16);
  - `cause_ref` (ULID, nullable); `created_at` UtcDateTime; `voided_at`, nullable; `void_reason` Text, nullable.

  Its checks:
  - `kind`, `status` and `source` each hold one of their enum's values;
  - `value_hash` is 64 lowercase hex characters;
  - `(kind='domain') = (domain_plain IS NOT NULL)`;
  - `(status='voided') = (voided_at IS NOT NULL)` and `(status='voided') = (void_reason IS NOT NULL)`.

  Its index and triggers:
  - The partial unique index `uq_suppression_entries_active_key` on (`kind`, `value_hash`) `WHERE status='active'`.
  - **Triggers**, following migration 0017:
    - `no_delete`;
    - `no_id_reuse`: an INSERT whose `id` exists, or an `active` INSERT whose `active` key exists (this stops `INSERT OR REPLACE`);
    - `insert_active`: an INSERT must be `active`;
    - `immutable`: an UPDATE may not change `id`, `kind`, `value_hash`, `domain_plain`, `source`, `cause_ref` or `created_at`, may not leave `voided`, and may not change a set `voided_at`. `void_reason` may change while the entry stays `voided`, for 3.13's redaction.

  The model mirrors the columns, checks and index; the migration adds the triggers. `owners.py` gives every column to `compliance`. `pii_registry.py` classifies `value_hash` as `keep_hash`, `void_reason` as `redact`, and the rest as `none`.
- **Repository `nextron/db/repos/compliance/suppression_entries.py`.** `StoredSuppressionEntry`; readers `get(entry_id)` and `active_for(kind, value_hash)`; one writer, `insert(...)`, which always inserts `active`. Nothing else.
- **`Compliance(clock).suppress(subject, scope, source, cause_ref) -> SuppressResult(written, existing)`** (entry-id tuples), an `@command`:
  1. **Argument checks.** It raises `TypeError` unless `subject` is a `ContactSubject`, `scope` a `SuppressionScope` and `source` a `SuppressionSource`. It raises `ValueError` unless `cause_ref` is `None` or a ULID.
  2. **Reads.** It reads the Contact through a new `queries.research.contact(contact_id)` (None → `LookupError`, a message with no id or value) and its Lead through `queries.discovery.lead`.
  3. **Keys.** `person_keys(...)`, plus `firm_domain_keys(...)` for scope `firm`, deduplicated by (`kind`, `value_hash`).
  4. **Writes.** A key with an `active` row adds that row's id to `existing`. Otherwise it inserts a row and appends `events` (entity `suppression_entries`, `None → active`, cause `CREATION[source].cause`, `cause_ref`, the actor from decision 5, at the clock's now).
  5. **Publish.** If at least one key was found, it publishes `SubjectSuppressed(suppression_entry_id=(written + existing)[0], source, contact_id, lead_id)`.
- **`Pipeline`:**
  - `close(lead_id, stage, cause_ref) -> bool` (`@command`) takes `stage` `lost` or `disqualified` (anything else raises `ValueError`). It applies `close_lost` or `close_disqualified`, sets the stage and appends `events` (`system:pipeline`, cause `closed_lost`/`closed_disqualified`, `cause_ref`). An already-closed Lead is a no-op that returns `False`; an unknown Lead raises `LookupError`.
  - `close_suppressed(n: SubjectSuppressed)` (`@command`) calls `close(n.lead_id, lost, n.suppression_entry_id)` only when `n.source` is in `OPT_OUT_SOURCES` and `n.lead_id` is set.
  - `main.build_subscribers` registers it for `SubjectSuppressed`. AD-3's earlier subscribers go before it when their stories add them.
- **Tests.** No personal data in `events`, logs or the notification. Erased people stay matchable: only hashes are stored for address, person and profile keys.

**Never:**
- No `assert_contactable`/`check` (3.4), panel, route, `reverse_optout` or any void code (3.5), unsubscribe tokens (3.8), erasure (3.13) or ledger (3.16).
- No new dependency, setting, job type, adapter, port or notification field. No change to `SubjectSuppressed`.
- No delete or expiry path anywhere, and no plaintext address, name or profile URL in any new column.
- No editing outside `nextron-engine/` except this spec, `sprint-status.yaml` and `deferred-work.md`.

### Decisions made overnight (pending user review)

1. **`key_hash` for a person takes a `(full_name, web_domain)` pair.** The output is hex, because 3.8 needs the 32 bytes. A name holding `|` can't be split wrongly.
2. **A key whose stored value can't be normalised is "not known" and is skipped.** A Contact with no known key gives an empty no-op result: no row, no event, no publish. Raising instead would block a DSR (3.13) or roll back a reply ingest forever.
3. **`SubjectSuppressed` is published on every call that finds at least one key, even when every key was already `active`.** An opt-out that arrives after an `operator` entry still closes the Lead and re-runs later cascades. Every subscriber must be idempotent.
4. **Only `ContactSubject` exists.** 3.5 (address or domain typed by the Operator) and 3.11 (a token's address hash) add their subject types and scopes.
5. **The `events` actor depends on the source.** `operator`, `import` and `dsr` give `operator`; `reply_rule`, `reply_class`, `one_click` and `mailto` give `system:compliance`. The creation cause is the source itself.
6. **The void half of "any entry write or void" is deferred to 3.5 (`reverse_optout`).** The machine, `VOIDABLE_SOURCES`, the columns and the triggers are ready; no code voids an entry yet.
7. **DB triggers back the contract test.** No delete, no replace, insert only as `active`, fixed columns, and `voided` is terminal. Permanence holds even against raw SQL.
8. **`Pipeline.close` can't stop Enrollments yet.** AD-4 says `close` also stops them; Story 4.8 (`enrollments`) adds that. A closed Lead is a no-op, so suppression never fails on stage.
9. **Firm scope uses only the address's domain and the Lead's `web_domain`.** `leads.mail_domains` doesn't exist yet. A free-webmail web domain is never written either.
10. **Backup and restore tests.** `suppression_entries` now exists at head, so tests expecting `None` or `skipped` for it now see `0` or `pass`. The "absent" paths drop the table in the planted copy, as merge `24bbda4` did for `provenance_records`.
11. **No log line.** `suppress` logs nothing; the `events` row is the audit.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Person, all keys | Contact `Jane Doe`, `jane@firm.co.uk`, LinkedIn URL; Lead `firm.co.uk`; `reply_rule` | 3 rows (address, person, profile), 3 `events` rows; publish; Lead `lost` | N/A |
| Partial keys | No name, or no web domain; no LinkedIn | Only the known keys | N/A |
| Repeat | Same call twice | 2nd: `written=()`, `existing` = 3 ids, no new row or event; publish again; Lead stays `lost` (no-op) | no error |
| Firm, business mail | `scope=firm`, address `@firm.co.uk`, web `firm.co.uk` | + one domain row (`domain_plain='firm.co.uk'`) | N/A |
| Firm, differing | address `@firm-mail.com`, web `firm.co.uk` | + two domain rows | N/A |
| Firm, webmail | address `@gmail.com`, web `firm.co.uk` | + only `firm.co.uk` | N/A |
| Non-opt-out | `operator`, `import` or `dsr` | rows and publish; stage unchanged | N/A |
| Closed Lead | Lead `disqualified`, `one_click` | rows written; stage stays `disqualified` | no-op |
| No keys | Name only, no web domain or LinkedIn | empty result, nothing written or published | N/A |
| Failing subscriber | A subscriber raises | rows, events and stage rolled back | exception propagates |
| Bad args | Wrong types; non-ULID `cause_ref`; unknown Contact | nothing written | `TypeError` / `ValueError` / `LookupError` |
| Raw SQL | DELETE; INSERT OR REPLACE; insert `voided`; UPDATE `value_hash`; `voided → active` | aborted by trigger | `IntegrityError` |

</frozen-after-approval>

## Code Map

- `nextron/domain/identity.py`: the normalisers (`normalize_email`, `registrable_domain`, `normalize_name`, `normalize_linkedin_url`, `IdentityError`). Reuse; don't change.
- `nextron/domain/markets.py`: `is_free_webmail`. Reuse.
- `nextron/domain/machines.py` (`Transition`, `IllegalTransition`); `nextron/domain/sending_inbox.py` and `pipeline_stage.py`: the machine and docstring style to follow.
- `nextron/domain/pipeline_stage.py`: `StageEvent.CLOSE_LOST`/`CLOSE_DISQUALIFIED`, `CLOSED_STAGES`, `transition`. Reuse; add no stage.
- `nextron/domain/notifications.py`: `SubjectSuppressed` (fields fixed). Don't change.
- `nextron/app/pipeline/api.py`: `Pipeline(clock)`, with `record_created`/`reparent_lead` as the pattern; `nextron/db/repos/pipeline/stages.py` (`stage`, `set_stage`).
- `nextron/app/compliance/api.py`, `app/queries/compliance.py`, `db/repos/compliance/__init__.py`: empty stubs to fill.
- `nextron/app/queries/research.py`: add `contact(contact_id)`, using `contacts_repo.get` (non-ULID → `None`). `app/queries/discovery.py`: `lead()`.
- `nextron/db/alembic/versions/0017_4_3_company_facts.py`: the trigger pattern. `0018_4_7_sending_inboxes.py` is the down revision.
- `nextron/db/models/` (`base.py`: `Ulid`, `UtcDateTime`, `ulid_pk`; `__init__.py` registry), `db/owners.py`, `db/pii_registry.py`, `db/eventlog.py` (`append` checks; entity must be in `owners.TABLES`).
- `nextron/main.py` `build_subscribers` (line ~1172) and its module docstring (line ~72).
- `tests/contracts/test_company_fact_contracts.py`: the AST writer-scan and probe pattern to copy.
- **HEAD bumps:** `tests/contracts/test_persistence_contracts.py:81`, `tests/unit/test_migrations.py:39`, `tests/app/test_serve_process.py:285`, `tests/migration_chains.py`, and `tests/contracts/test_source_tree.py` `FILES`.
- **Count fixes:** `tests/app/test_backups.py` (~258, 279–291, 883), `tests/app/test_restore_tests.py` (~332, 396–406, 769, 1162), `tests/unit/test_snapshot.py`, plus any other test asserting the table absent. See merge `24bbda4`.
- `tests/app/test_leads.py:120` / `test_lead_import.py:175`: how app tests build units with `main.build_subscribers(clock=...)` and seed Leads and Contacts.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/contactability.py`, `nextron/domain/suppression_entry.py`: new pure modules, per Always.
- [x] `nextron/db/models/compliance.py` (+ `models/__init__.py`), `db/alembic/versions/0019_3_3_suppression_entries.py`, `db/owners.py`, `db/pii_registry.py`: the table.
- [x] `nextron/db/repos/compliance/suppression_entries.py`: the repository.
- [x] `nextron/app/queries/research.py`: `contact()`.
- [x] `nextron/app/compliance/api.py`: `Compliance`, `SuppressResult`.
- [x] `nextron/app/pipeline/api.py`: `close`, `close_suppressed`.
- [x] `nextron/main.py`: register the subscriber and update the docstring.
- [x] `tests/unit/test_contactability.py`: the AC's three hash cases, every kind, the key-set rules and their errors.
- [x] `tests/unit/test_suppression_entry_domain.py`: the machine table, `CREATION` and `IllegalTransition`.
- [x] `tests/app/test_suppression.py`: every matrix row on real units of work, plus `events` rows free of personal data.
- [x] `tests/app/` pipeline `close` tests.
- [x] `tests/contracts/test_suppression_contracts.py`, with probes for each check:
  - **One writer:** the repository's writer is used only in `app/compliance/api.py`, and every public repository function is classified as a reader or a writer;
  - `SuppressionEntryRecord` is named only in `db/models` and `db/repos/compliance`;
  - no string outside `alembic/versions` holds an `INSERT`, `UPDATE`, `DELETE` or `REPLACE` naming `suppression_entries`;
  - the compliance repository, API and queries hold no delete, remove, purge, expire, expiry, ttl, prune, evict, truncate or drop function or call, no SQL `DELETE`, and no `timedelta`.
- [x] The HEAD bumps and count fixes in the Code Map.
- [x] `nextron-engine/README.md`: a Story 3.3 section.

**Acceptance Criteria:**
- Given the migration at head, when the schema is read, then the columns, checks, partial unique index and four triggers exist, and `owners.py` and `pii_registry.py` cover every column.
- Given the code base, when `tests/contracts` and `lint-imports` run, then every contract holds, and each probe of the suppression contract fails as intended.

## Implementation Notes

**Files.**
- Domain: `nextron/domain/contactability.py` (`KeyKind`, `SuppressionScope`, `ContactSubject`, `SuppressionKey`, `normalised_key`, `key_hash`, plus helpers `suppression_key(kind, value)` and `is_hash(value)`, `person_keys`, `firm_domain_keys`); `nextron/domain/suppression_entry.py` (the AD-4 machine).
- Table: `nextron/db/models/compliance.py` (`SuppressionEntryRecord`, registered in `models/__init__.py`); migration `0019_3_3_suppression_entries` (revision id = file stem, down revision `0018_4_7_sending_inboxes`); `owners.py` maps all ten columns to `compliance`; `pii_registry.py` gives `value_hash` `keep_hash`, `void_reason` `redact`, the rest `none`.
- Repository: `nextron/db/repos/compliance/suppression_entries.py` (`StoredSuppressionEntry`; readers `get`, `active_for`; writer `insert`, always `active`).
- `queries.research.contact(contact_id)`: `contacts_repo.get`, `None` for a non-ULID.
- `nextron/app/compliance/api.py`: `Compliance(clock).suppress`, `SuppressResult`, and `ACTORS` (source -> `events` actor, decision 5).
- `nextron/app/pipeline/api.py`: `CLOSE_EVENTS`, `close`, `close_suppressed`; `main.build_subscribers` registers `SubjectSuppressed -> Pipeline(clock).close_suppressed` after the two `LeadsMerged` subscribers, and the module docstring says so.

**Choices within the spec.**
- `Compliance.suppress` raises `LookupError("suppress: the Contact's Lead is unknown")` when the Contact's Lead can't be read. It can't happen (`contacts.lead_id` is a foreign key); it keeps the publish from naming a missing Lead.
- The published `lead_id` is the Lead read through `queries.discovery.lead(contact.lead_id)`.
- `Pipeline.close` checks its arguments before any read: the stage (`ValueError`; the plain codes `"lost"`/`"disqualified"` are accepted, being equal to the enum), then `cause_ref` (`ValueError` unless `None` or a ULID). A `lead_id` that isn't a ULID is an unknown Lead (`LookupError`), rather than letting the ULID column's bind raise.
- `firm_domain_keys` tests `is_free_webmail` on both the address's host and its registrable domain, and on both the raw web domain and its registrable domain.
- Keys are deduplicated by (`kind`, `value_hash`) in first-seen order, so `written` and `existing` follow the key order: address, person, profile, then the domain keys.
- The trigger message is `suppression entries are permanent` for all four triggers.

**Tests.**
- New: `tests/unit/test_contactability.py`, `tests/unit/test_suppression_entry_domain.py`, `tests/app/test_suppression.py` (every matrix row, the readers, raw SQL against each trigger and check), `tests/app/test_pipeline_close.py`, `tests/contracts/test_suppression_contracts.py` (the schema at head, owners and PII coverage, then the four AST checks, each with probes).
- HEAD bumps: `test_persistence_contracts.py` (`HEAD` and the chain probes, now `0020`/`0021`), `test_migrations.py`, `test_serve_process.py`, and `test_source_tree.py` `FILES` (the two domain modules, the model, the migration and the repository).
- Count fixes (decision 10): `test_backups.py`, `test_restore_tests.py` and `test_snapshot.py` now see `suppression_count` 0 at head. The tests that planted a fake `suppression_entries` table insert valid `active` rows through a `suppression_row(n)` helper instead. The "recorded absent" restore test drops `suppression_entries` in the live database before the backup. The snapshot "one counted table absent" test covers both tables. The corrupted-copy test uses its own `probe_rows` table.
- Also touched: `tests/unit/test_serve.py`. `build_subscribers` now registers `close_suppressed` for `SubjectSuppressed`, and the clock test covers all three subscribers.

**Verification (implementer).** These pass: the spec's targeted files plus `test_pipeline_close.py`, `test_recovery.py`, `test_serve_process.py`, `test_serve.py`, `test_doctor.py`, `test_engine_health.py`, `test_health_page.py`, `test_uow.py`, `test_notifications.py`, `test_lead_import_page.py` and `test_sending_inboxes.py` (1125 tests). `uv run pytest -q tests/contracts` gives 1065 passed and 1 skipped (`ai_eval`). `uv run lint-imports` keeps all 29 contracts. The full suite was not run (§4a).

**Review patches (pass 1).**
- `trg_suppression_entries_immutable` also refuses voiding an entry whose source isn't `reply_rule` (AD-4 `VOIDABLE_SOURCES`). `0019` was edited in place, since it hasn't shipped. The migration and model docstrings and the README say so.
- In `tests/app/test_suppression.py`:
  - The `seeded` helper's voided entry is now `reply_rule`.
  - A new test refuses voiding an `active` entry of each of the other six sources, with the `PERMANENT` message.
  - `test_a_plain_duplicate_active_key_is_refused` now matches `PERMANENT`.
  - New tests show the partial unique index refusing a second `active` row once `trg_suppression_entries_no_id_reuse` is dropped in the temporary DB, and `ck_suppression_entries_status` refusing `status = 'expired'`.
  - `"jane"` left the personal-data checks (`PLAIN` and the stored-row check), because `JANE` is valid Crockford base32 and a random ULID could hold it.
- `FREE_WEBMAIL_DOMAINS` gains 20 common US ISP mailbox domains under `# US providers`: `comcast.net`, `att.net`, `rr.com` and the rest. The list comment and `tests/unit/test_markets_domain.py` (`US_ISP_WEBMAIL`) are updated. Firm-scope rows show `@comcast.net` and `@nc.rr.com` give no domain key: `tests/unit/test_contactability.py` and `tests/app/test_suppression.py`.
- `tests/contracts/test_suppression_contracts.py` changes:
  - **New table-name check.** No non-docstring string under `nextron/` may hold `suppression_entries` outside `db/models/compliance.py`, the migrations, `db/repos/compliance`, `app/compliance`, `app/queries/compliance.py`, `domain/backup.py`, `db/owners.py` and `db/pii_registry.py`. Probes cover the f-string, `sa.table(...)`, `metadata.tables[...]` and `DROP TABLE` forms, plus allowed homes and docstrings.
  - **Wider permanence check.** It now also covers `domain/contactability.py` and `domain/suppression_entry.py`. It also fails a comparison with `created_at` or `voided_at`, `relativedelta`, and a non-docstring string holding an SQL date modifier (`julianday(`, `strftime(`, `datetime(`, `date(`, `'now'`, `[+-]N days/months/years/hours/minutes`). Each has a probe.
- **Re-verified:** `test_suppression.py`, `test_contactability.py`, `test_markets_domain.py`, `test_market_sync.py` and `test_suppression_contracts.py`. `tests/contracts` gives 1087 passed and 1 skipped. `lint-imports` keeps all 29 contracts.

**Orchestrator, after pass 1.**
- The README's contract-tests paragraph now names the table-name check and the wider permanence check.
- `deferred-work.md` has four new Story 3.3 entries:
  - `Pipeline.close` and merged Leads (G);
  - an erased Contact in `suppress`, for 3.13 (H);
  - Lead mail domains in firm scope (I);
  - Enrollments and the earlier `SubjectSuppressed` subscribers, for 4.8 (I).

**Final verification:**
- Targeted run: the Verification files plus the markets, seed-sync, pipeline-close and serve tests, and `tests/contracts`. 2208 passed, 1 skipped.
- Full suite through `suite_lock.py` (`track-B`): 9262 passed, 3 skipped (`ai_eval` ×2, and a symlink test this account can't run).
- `lint-imports`: 29 kept, 0 broken.

## Spec Change Log

## Review Triage Log

Pass 1 (review loop 0). Three layers: blind hunter (BH, 12 findings), edge-case hunter (EC, 7), verification gap (VG, 2). That is **21 raw** findings, **15 after grouping**: 7 patched, 2 deferred, 6 rejected.

| # | Layer | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| 1 | VG | The one-writer contract can't see dynamic SQL (`f"INSERT INTO {TABLE}"`) or Core `table("suppression_entries")` writes. | medium | The reviewer ran the four checks on such a probe tree; all returned `[]`. The checks match only `repo.insert`, the model name and whole SQL string constants. | patch (A) |
| 2 | VG | `assert_no_personal_data` can flake: a random Crockford ULID can contain `JANE`, which is `jane` once lowercased. | low | `PLAIN` holds `"jane"`; J, A, N and E are all Crockford digits, about 2e-5 per ULID. The fix is a direct correction (drop the marker; `doe`, `firm` and `@` still cover the name and address). | patch (B) |
| 3 | BH | Firm scope can permanently suppress a whole US ISP mailbox domain (`comcast.net`, `att.net`, ...). | medium | `FREE_WEBMAIL_DOMAINS` (Story 3.1) lists UK ISPs but no US ISP. A "remove us" from `bob@comcast.net` writes an unvoidable domain entry that 3.4's gate will apply to every comcast.net Contact. | patch (C) |
| 4 | BH | A repeat opt-out writes no `events` row, and the Lead close cites the older entry. | low | Real, but it's decision 3 as approved, and `SubjectSuppressed` is frozen. The opt-out's own record lives in its source row (the Reply, or `unsub_requests.received_at` in 3.11). The fix would change the spec. | reject (spec change) |
| 5 | BH | An opt-out closes a `won` client `lost`. | medium | Real. AD-3 and the AC say `pipeline.close(lost)` for every opt-out source, and the 2.2 machine allows `close_lost` from `won`. The fix would change the AC. | reject (spec change; flag for Mac) |
| 6 | BH | The triggers let raw SQL void a non-`reply_rule` entry. | medium | `trg_suppression_entries_immutable` restricts only fixed columns and leaving `voided`, so `UPDATE ... SET status='voided'` on a `dsr` entry passes (the `seeded` test helper does exactly this to an `operator` entry). AD-4 allows a void only for `reply_rule`. | patch (D) |
| 7 | BH | The contract misses Core `table()`, `metadata.tables[...]` and interpolated SQL; also "suppress is the only code that writes" is stronger than what's checked. | medium | The first part is finding 1. The wording part is false: `insert` is called only inside `suppress` today, and the AC's rule is module-level. | patch (A) |
| 8 | BH | The permanence contract misses expiry done by filtering (`created_at > cutoff`, SQL date modifiers) and doesn't guard the domain modules where 3.4's `check` will live. | medium | `GUARDED` covers only the repository, API and queries, and bans only name stems and `timedelta`. The AC forbids "any expiry logic". | patch (E) |
| 9 | BH | Nothing compares the migration's CHECK bodies with the model's. | low | Real, but every earlier table has the same pattern. A mismatch fails loudly (`IntegrityError`) in the adding story's own tests, and the fix adds a new comparison mechanism. | reject |
| 10 | BH | Tests don't show that the partial unique index or `ck_suppression_entries_status` enforce anything: the triggers fire first. | low | `test_a_plain_duplicate_active_key_is_refused` has no `match`, and `no_id_reuse` fires before the index. No test updates `status` to an unknown value. Test-only fix. | patch (F) |
| 11 | BH | `Pipeline.close` ignores `merged_into`. | low | Real for a stale merged id, but `suppress` can't reach it (Contacts move to the survivor). The root cause is the deferred "first consumers of a Lead id resolve `merged_into`" item. | defer (G) |
| 12 | BH | `domain_plain` keeps a sole trader's own-name domain in plain text after erasure. | medium | The AC says literally "the rest as `none`". The fix would change the AC; it relates to OQ-A10. | reject (spec change; flag for Mac) |
| 13 | BH | `person_keys` turns the erasure tombstone `[erased]` into a junk `erased\|<domain>` key. | low | The tombstone doesn't exist until 3.13, whose `erase_contact` calls `suppress` before tombstoning. Only a later `suppress` of an already-erased Contact reaches it. | defer (H) |
| 14 | BH | `deferred-work.md` has no Story 3.3 follow-ups (mail domains for firm scope; `close` stopping Enrollments). | low | True; this file is where the other tracks look for continuity. A direct addition. | patch (I, orchestrator) |
| 15 | EC | `suppress` on an erased Contact writes a bogus person key and drops the address key (email is a hash). | low | Same root cause as 13. | defer (H) |
| 16 | EC | Firm scope on a shared-platform web domain or an unlisted ISP. | medium | The ISP part is finding 3. The shared-platform part isn't reachable: 2.3 refuses shared-platform websites before `upsert_lead`, and a deferred item covers later callers. | patch (C) |
| 17 | EC | A person key on a free-webmail or shared web domain suppresses everyone with that name on any such Lead. | false | `uq_leads_web_domain` makes a web domain unique to one Lead, so `name\|domain` matches only Contacts of that one Lead. | reject |
| 18 | EC | Raw `UPDATE` voids a `one_click`, `dsr` or `operator` entry. | medium | Same as finding 6. | patch (D) |
| 19 | EC | `close()` given a merged Lead's id. | low | Same as finding 11. | defer (G) |
| 20 | EC | The writer is reachable through `from nextron.db import repos` or a star import. | low | No module in `nextron/` uses either form, and import-linter c7 already forbids every other app owner from importing `nextron.db.repos.compliance`. The fix adds alias-tracking branches. | reject |
| 21 | EC | Core `sa.table`, `metadata.tables[...]` and `DROP TABLE suppression_entries` pass every contract. | medium | Same as finding 1. | patch (A) |

## Verification

**Commands** (from `nextron-engine/`):
- `uv run pytest -q tests/unit/test_contactability.py tests/unit/test_suppression_entry_domain.py tests/app/test_suppression.py tests/app/test_backups.py tests/app/test_restore_tests.py tests/unit/test_snapshot.py tests/unit/test_migrations.py tests/app/test_leads.py tests/app/test_lead_import.py` -- expected: all pass.
- `uv run pytest -q tests/contracts` and `uv run lint-imports` -- expected: all pass, every contract kept.
- The full suite through `suite_lock.py` (`track-B`), by the orchestrator only -- expected: exit 0.
