---
title: 'Story 3.13: Erase a Contact on request'
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: '65d6b7d0e54f44b25510216390c0941f76d215dc'
review_loop_iteration: 0
story_key: '3-13-erase-a-contact-on-request'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A person can ask Nextron to erase their data (UK GDPR Art. 17, FR-52), and nothing can do it yet. AD-17 says erasure tombstones every personal-data column the PII registry marks, across every table that holds the Contact's data, while AD-16 keeps the person's hashed keys on the Suppression List so they are never contacted again. It must be idempotent, auditable without personal data, and fail closed.

Requirements: FR-52, FR-50, AD-3, AD-4, AD-16, AD-17, NFR-3, UX-DR3, UX-DR17.

**Approach:**
- Migration `0028_3_13_erase_contacts` adds `data_subject_requests`, `contacts.erased_at`, tombstone triggers and the amended Evidence trigger.
- `nextron/app/compliance/erasure.py` gets `Erasures(clock, compliance)` with:
  - `open_dsr`;
  - `erase_contact` (suppress, then publish `ContactErased`, then complete the request);
  - compliance's own `erase_pii`;
  - the `erase_on_request` use case.
- Each owner holding a PII table registers an `erase_pii` subscriber with an `ERASURE` declaration. Research's hook tombstones by registry class.
- The Lead page gets an Erase action, a confirmation step that names the Contact, and the tombstone line. Contract tests force every PII table to have a hook and forbid cascades.

The acceptance criteria are Story 3.13 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**
- **Pure domain (stdlib and `nextron.domain` only).**
  - **`nextron/domain/erasure.py`:**
    - `REDACTED = "[erased]"`;
    - `ErasureCause` (`dsr`, `retention`);
    - `Coverage` (`tombstoned`, `kept`, `not_held`) and `TableErasure(coverage, reason)`;
    - `tombstone_hash(kind, value)`: `contactability.key_hash(kind, value)`, or, when normalisation fails, the SHA-256 hex of `kind + ":" + value`; it never raises on a `str`;
    - `tombstone(pii_class, value, *, hash_kind)`, by class code:
      - `redact` gives `REDACTED` (NULL stays NULL);
      - `null` gives NULL;
      - `keep_hash` gives `tombstone_hash`, or the value unchanged when `hash_kind` is `None` (already a hash);
      - `none` leaves the value.
  - **`nextron/domain/data_subject_request.py`**, the AD-4 machine:
    - `RequestKind` (`erase`, `export`) and `RequestStatus` (`open`, `completed`; terminal `completed`);
    - `CREATION = Transition(open, opened)`; `TRANSITIONS = {(open, complete): Transition(completed, completed)}`; anything else raises `IllegalTransition`;
    - `check_request_ref(ref, *, name)` raises `ValueError` unless `ref` is a `str` of 1–200 characters, equal to its strip, with no control character and no `@`, and, when `name` is set, without every `normalize_name` word of `name` as a whole `normalize_name` word of `ref`. The message never echoes a value.
  - **`domain/contactability.py`:**
    - `BlockReason.CONTACT_ERASED`, last;
    - `ContactFacts.erased: bool = False`: an erased Contact gives `contact_erased`, and its tombstone values are never keys;
    - `erased_person_keys(email_hash)`: the address key straight from a stored `is_hash` value, else `()`.
- **Migration `0028_3_13_erase_contacts`** (down `0027_2_6_contact_methods`; `downgrade()` raises).
  - **`data_subject_requests`** (owner `compliance`):
    - columns `id`, `contact_id` (FK `contacts`, indexed), `kind` (8), `status` (16), `request_ref` (200), `opened_at`, `completed_at` (nullable) and `receipt_id` (ULID, nullable, no FK until 3.14);
    - CHECKs on kind, status, ref length and `(status='completed') = (completed_at IS NOT NULL)`;
    - triggers:
      - `no_delete`;
      - `insert_open`;
      - `immutable`: `id`, `contact_id`, `kind`, `request_ref` and `opened_at` are fixed; status only `open → completed`; `completed_at` changes only with it; `receipt_id` is set at most once.
  - **`contacts.erased_at`**, a nullable UtcDateTime, added in place (`add_column`, no rebuild).
  - **`trg_contacts_tombstone`** aborts:
    - an UPDATE setting `erased_at` unless the new row is a tombstone: `name` and `role` NULL or `[erased]`; `linkedin_url`, `office_country` and `office_region` NULL; `email` NULL or 64 lowercase hex;
    - any UPDATE of an erased row that changes a column other than `lead_id`, `is_primary` and `updated_at`.
  - **`trg_provenance_records_erased_update` and `_insert`** abort an UPDATE or INSERT of a record whose Contact has `erased_at` set.
  - **`trg_evidence_immutable`** is dropped and recreated. It is the same, except that `quote` and `value` may each change only from non-NULL to `[erased]`, on a row whose `person_contact_id` is set. `person_contact_id` stays fixed, so every other mutation is still refused.
  - **Model, owners and registry.** The models mirror the columns, checks and index. `owners.py` maps the columns (`erased_at` to research). `pii_registry.py`:
    - `request_ref` is `redact`, and every other new column `none`;
    - `crawled_pages.text` becomes `null` (decision 4);
    - new `HASH_KINDS = {("contacts","email"): "address", ("provenance_records","address"): "address", ("suppression_entries","value_hash"): None}`, with a contract that every `keep_hash` column is listed.
- **`nextron/db/erasure.py`:** `tombstoned(table, row) -> dict` applies `domain.erasure.tombstone` to each non-`none` column in `PII[table]`, with `HASH_KINDS`. Repositories use it. The app never imports the registry (c1).
- **Repositories (writers only where named).**
  - research:
    - `contacts.tombstone(contact_id, at)`;
    - `provenance.tombstone_for_contact(contact_id)`;
    - `signals.tombstone_person_evidence(contact_id)`;
    - `crawl.tombstone_pages(lead_ids)`: page text and `page_contact_methods.matched` by class; `content_hash` kept.
  - compliance:
    - `data_subject_requests.py`: `insert_open`, `complete`, `open_for(contact_id, kind, request_ref)`, `get` and `completed_erasures(contact_ids)`;
    - `suppression_entries.voided_for(kind, value_hash)` and `redact_void_reason(entry_id)`.
  - discovery: `leads.merged_from(lead_id)`, the ids whose merge chain ends at it, through `queries.discovery.merged_lead_ids`.
  - research's `unresolved_ids` and `has_unresolved` skip erased Contacts, and `StoredContact` gains `erased_at` (last, default `None`).
- **`Erasures(clock, compliance)` in `nextron/app/compliance/erasure.py`.** The `api.py` docstring points to it.
  - **`@command open_dsr(contact_id, kind, request_ref) -> str`.**
    1. It checks its arguments: a bad type is `TypeError`, a bad value `ValueError`, an unknown Contact `LookupError`. `check_request_ref` gets the Contact's name.
    2. An `open` request with the same (`contact_id`, `kind`, `request_ref`) returns its id.
    3. Otherwise it inserts the row and one `events` row: `data_subject_requests`, `None → open`, cause `opened`, `cause_ref` the Contact, actor `operator`.
  - **`@command erase_contact(contact_id, request_ref, cause) -> ErasureResult(contact_id, request_id, erased)`.**
    1. `cause` goes through `ErasureCause`. `dsr` needs a valid `request_ref`; `retention` needs `None`.
    2. An unknown Contact raises `LookupError`.
    3. For `dsr`, the open `erase` request for (Contact, ref) is found, or `open_dsr` opens one.
    4. If the Contact is already erased, nothing is suppressed, published or tombstoned. The request, if any, is completed, and the result is `erased=False`.
    5. Otherwise:
       - for `dsr`, `compliance.suppress(ContactSubject, PERSON, DSR, cause_ref=request_id)`;
       - `publish(ContactErased(contact_id, cause))`;
       - it re-reads the Contact and raises `RuntimeError("erasure incomplete")` unless `erased_at` is set (fail closed);
       - it completes the request: status, `completed_at`, and `events` `open → completed`, cause `completed`, `cause_ref` the Contact.
  - **`@command erase_pii(n)`**, compliance's hook, registered first. It reads the still-plaintext Contact and its Lead. It redacts the `void_reason` of every `voided` entry on the person's keys (`person_keys`). `data_subject_requests` is `kept`.
  - **`@use_case erase_on_request(contact_id, request_ref) -> ErasureResult`.** The first transaction opens the request and commits it. The second runs `erase_contact` with `dsr`; an exception rolls it back, the request stays `open`, and the exception propagates. After a commit it logs `contact_erased` (`contact_id`, `request_id`, `erased`).
- **`Research(clock).erase_pii(n)`**, registered second. An unknown Contact raises `LookupError`; an erased one returns.
  1. It tombstones the Contact's Provenance Records.
  2. It tombstones its Evidence by `person_contact_id`.
  3. It tombstones the pages of the Lead and of every Lead merged into it.
  4. Last, it tombstones the Contact (`erased_at` and `updated_at` set to now).
  5. It appends one `events` row: `contacts`, `None → erased`, cause `n.cause`, actor `system:research`.

  Research's other paths skip an erased Contact: `_match`, and `_resolve` (a no-op, never `LookupError`).
- **No-op hooks.** `setup`, `drafting`, `jobqueue`, `ops` and `notify` each get a module-level `@command erase_pii(n)` that writes nothing. Each owner's `api.py` (compliance's `erasure.py`) declares `ERASURE: Mapping[table, TableErasure]` for exactly its tables with a non-`none` column. `main.build_subscribers` registers compliance's hook, then research's, then the rest. Every `ERASURE` is listed in the I/O matrix's coverage row.
- **`suppress` and the gate on an erased Contact.**
  - `suppress` uses `erased_person_keys(contact.email)`, plus the web domain key for firm scope (decision 7).
  - `assert_contactable(ContactSubject)` passes `erased=True` with no values.
- **Web.**
  - **The Lead page.** Each Contact row that isn't erased links to `GET /leads/{lead_id}/contacts/{contact_id}/erase` ("Erase (data subject request)"). An erased row shows only "[erased], erased <YYYY-MM-DD> (request <ref>)". A retention erasure leaves out the request part. The row has no other value and no Provenance Records, and the Lead and the other Contacts render unchanged.
  - **The confirmation step.** `GET` (`contact_erase_page`) and `POST` (`contact_erase`), both `@personal_data(True)`.
    - A Contact not on that Lead's survivor is a 404 (`lead_missing.html`).
    - The GET names the Contact (name, role, email and LinkedIn), says what erasure does and that it can't be undone, and holds a form: `method="post"`, `hx-post`, `{{ csrf_field() }}`, one `request_ref` input (hint: "a ticket or letter reference, never the person's name or address") and the button "Erase permanently".
    - An erased Contact's GET shows only "This Contact was erased on <date>."
    - The POST takes exactly `csrf_token` and one `request_ref`; anything else is `MalformedForm` (400).
      - A `ValueError` from the ref check re-renders the panel with the message.
      - Success shows "Erased. Request <ref> completed."
      - `erased=False` shows "This Contact was already erased; nothing changed."
      - Any other exception logs `contact_erasure_failed` (`contact_id` and the error type) and shows "Erasure failed and was rolled back: nothing was erased, and the request stays open (<ErrorType>)."
      - Each answer is 200. No answer shows erased data.
  - `create_app(..., erasures=...)`, wired in `main`.
- **Tests.**
  - The `events` rows and logs hold no personal data.
  - No `DELETE` runs, and the row count of every table is unchanged.
  - An end-to-end planted-value scan covers every non-`none` column of every table.

**Never:**
- No stores outside the DB, receipts or `erase_external` jobs (3.14), no export (3.15) and no ledger (3.16).
- No new dependency, setting, job type, adapter or port. No change to `ContactErased`'s fields.
- No deleted row, and no `ON DELETE CASCADE`.
- No plaintext kept in a tombstoned column, and no tombstone value (the `[erased]` name or the email hash) shown on any page.
- No edits outside `nextron-engine/` except this spec, `sprint-status.yaml` and `deferred-work.md`.

### Decisions made overnight (pending user review)

1. **Two transactions.** `erase_on_request` commits the open request first, so a failed erasure leaves it `open`, as the AC says. A retry with the same reference reuses it.
2. **`contacts.erased_at` marks a tombstone.** A retention erasure has no request, and every reader needs one cheap test. The DB triggers make the tombstone final: only a merge (`lead_id`, `is_primary`, `updated_at`) may touch an erased Contact, and its Provenance can't change.
3. **The address hash equals the suppression key's hash** (`key_hash("address", …)`, deferred item H). An address that can't be normalised still hashes, so an erasure never fails on bad data.
4. **`crawled_pages.text` is reclassified `null`**, because AD-17 and the AC both say "nulled; content hash kept", which matches the purge job. The pages of Leads merged into the Contact's Lead are nulled too.
5. **`page_contact_methods.matched` on those pages becomes `[erased]`.** It is page-level, but it may hold the person's direct phone number. Nothing reads it, and the next crawl rewrites it.
6. **The Evidence trigger allows only the redaction** (`→ [erased]`, rows with `person_contact_id`). No current writer sets `person_contact_id`, so today no Evidence row is touched; Stories 2.10 and 7.x set it.
7. **`suppress` of an erased Contact** (deferred item H) uses the stored address hash as the address key. It writes no person or profile key, since tombstones aren't keys, and firm scope adds the web domain. So a later opt-out still lands, and no junk `erased|<domain>` key is written.
8. **The gate blocks every erased Contact** with the new `contact_erased` reason, even after a retention erasure, which writes no suppression entry: fail closed.
9. **The hook declarations are per owner.** Seven tables never hold a Contact's data and are declared `not_held` with a reason: `settings`, `footer_snapshots`, `jobs`, `backups`, `restore_tests`, `alerts` and `notifications`. Any new PII table fails the contract until it is declared. `data_subject_requests` is `kept`: it is the proof of erasure, and the page shows its reference.
10. **The request reference may not hold an address or the person's name**, because the record is kept and shown after erasure.
11. **A Contact with no known key is still erased.** The DSR suppress writes nothing, and it can't be re-blocked; Story 3.14's receipt should list that.
12. **Retention passes no reference** and opens no request. Its `events` actor is `system:research`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| DSR, full Contact | name, role, email, LinkedIn, office; 2 Provenance records; person Evidence; pages; voided `reply_rule` entry | address, person and profile entries (`dsr`, `cause_ref` request); every PII column tombstoned by class; page text NULL, hash kept; `void_reason` `[erased]`; request `completed`; rows unchanged in count | N/A |
| Coverage | ERASURE declarations | contacts, provenance_records, evidence, crawled_pages, page_contact_methods, suppression_entries: `tombstoned`; data_subject_requests: `kept`; the seven others: `not_held` | contract |
| Re-add | Same address, or same name at the Lead, by CSV or crawl | `add_contact` blocked (`address_suppressed` / `person_suppressed`); no Contact | N/A |
| Repeat | Erase an erased Contact | `erased=False`; nothing tombstoned or suppressed; its open request completed | no error |
| Hook fails | A hook raises | erase transaction rolled back; request `open`; page shows the error | rollback |
| Retention | `cause="retention"` | tombstoned, no entry, no request | `request_ref` set → `ValueError` |
| Gate / suppress | Erased Contact | `Blocked((contact_erased,…))`; `suppress` writes only the address-hash key (+ domain for firm) | N/A |
| Bad ref | `@`, the Contact's name, empty, 201 chars | refused, nothing written | `ValueError` |
| Raw SQL | refill an erased Contact; edit its Provenance; Evidence quote → other text, or on a row with no person; any DSR delete or illegal status | aborted | `IntegrityError` |

</frozen-after-approval>

## Code Map

- `nextron/app/compliance/api.py`:
  - `Compliance.suppress` (lines 183–248): add the erased branch (decision 7);
  - `_facts` (lines 291–309): `erased=True`;
  - the module docstring: point to `erasure.py`.

  Keep edits small and additive: Story 3.5 is merging changes into this file.
- `nextron/domain/contactability.py`: `BlockReason` (302), `SUPPRESSION_REASONS` (`contact_erased` isn't one), `ContactFacts` (385), `_checked_keys`/`check` (477–540), `is_hash` (155), `person_keys`, `key_hash`.
- `nextron/domain/notifications.py`: `ContactErased(contact_id, cause)`. Don't change it. `nextron/domain/machines.py`, `suppression_entry.py`: the machine pattern.
- `nextron/app/research/api.py`: `_match` (386), `add_contact` (442), `_resolve` (536), `reparent_lead` (619), `ACTOR`, `ENTITY`. Docstring steps.
- Repositories:
  - `db/repos/research/contacts.py` (`_COLUMNS`, `StoredContact`, `unresolved_ids`, `has_unresolved`);
  - `provenance.py`;
  - `signals.py` (Evidence columns);
  - `crawl.py` (`pages`, `contact_methods_of`; `page_contact_methods` has no `lead_id`, so join through `crawled_pages`);
  - `db/repos/compliance/suppression_entries.py`;
  - `db/repos/discovery/leads.py`.
- `nextron/db/alembic/versions/`:
  - `0025_2_9_signals_evidence.py` (lines 160–178: `FIXED_COLUMNS`, `IMMUTABLE`, the trigger SQL to recreate: copy the column list, don't import it);
  - `0022_3_3_suppression_entries.py` (the trigger style);
  - `0027_2_6_contact_methods.py` (down revision; `add_column` in place).
- `nextron/db/models/research.py`, `crawl.py`, `compliance.py` (+ `models/__init__.py`), `db/owners.py`, `db/pii_registry.py`.
- `nextron/main.py` `build_subscribers` (1461) and the docstring. Build `Erasures(clock, Compliance(clock))` for the hook and for `create_app`.
- `nextron/web/app.py`:
  - `lead_page` (1121);
  - `create_app` kwargs (386);
  - the docstring route list;
  - the facts POST pattern (649–700: `form_fields`, `units.open()`, a panel answer).
- `nextron/web/leads_page.py` (`lead_context`, `ContactRow`), `templates/lead.html`. A new `nextron/web/erase_page.py` and `templates/contact_erase.html` and `contact_erase_panel.html`.
- `nextron/app/setup|drafting|jobqueue|ops|notify/api.py`: add `ERASURE` and `erase_pii`.
- Tests to copy:
  - `tests/app/test_suppression.py` (the World helpers, raw SQL voiding of a `reply_rule` entry, `transact`);
  - `tests/app/test_contactable.py`;
  - `tests/contracts/test_suppression_contracts.py` (the AST and probe patterns; update its writer classification for the two new compliance repo functions, allowed in `app/compliance/erasure.py`);
  - `tests/unit/test_leads_page.py` and `tests/dashboard.py` (the `create_app` helper).
- HEAD bumps:
  - `tests/contracts/test_persistence_contracts.py:81` plus its chain probes;
  - `tests/unit/test_migrations.py:39`;
  - `tests/app/test_serve_process.py:324`;
  - `tests/migration_chains.py`;
  - `tests/contracts/test_source_tree.py` `FILES`.

  Also update `tests/unit/test_serve.py` (the subscriber pins), any test pinning `BlockReason`'s members or `crawled_pages.text`'s class, and the 2.9 Evidence trigger tests.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/erasure.py`, `nextron/domain/data_subject_request.py`, `domain/contactability.py`: the pure parts, per Always.
- [x] Migration `0028_3_13_erase_contacts.py`, the models, `owners.py`, `pii_registry.py` (`HASH_KINDS`, reclassify), `nextron/db/erasure.py`.
- [x] The repositories listed in Always.
- [x] `nextron/app/compliance/erasure.py`; `api.py`: `suppress` and the gate's erased branch; `queries.compliance`: `open_request`, `completed_erasures`; `queries.discovery.merged_lead_ids`.
- [x] `nextron/app/research/api.py`: `erase_pii`, `ERASURE`, and skipping erased Contacts in `_match` and `_resolve`.
- [x] The setup, drafting, jobqueue, ops and notify `ERASURE` declarations and `erase_pii`; `main.py`: the subscribers, `Erasures` and `create_app` wiring.
- [x] Web: the Lead page row and link, `erase_page.py`, both templates and both routes.
- [x] Unit tests:
  - `tests/unit/test_erasure_domain.py`: `tombstone`, `tombstone_hash`, `check_request_ref` and the machine;
  - additions to `test_contactability_check.py`.
- [x] `tests/app/test_erasure.py`: every matrix row on real units of work. Also:
  - a raw-SQL trigger test for each abort, plus "every other Evidence mutation still refused" (each fixed column, a quote to other text, a quote on a row with no person, `[erased]` back, a NULL quote);
  - the planted-value scan;
  - no `DELETE` (a `before_cursor_execute` log);
  - row counts unchanged;
  - `events` and logs free of personal data.
- [x] `tests/contracts/test_erasure_contracts.py`, each check with probes:
  - every table with a non-`none` column is declared once in its owner's `ERASURE`, and that owner's `erase_pii` is registered for `ContactErased` (compliance first, research second);
  - no stale or extra declaration;
  - every `keep_hash` column is in `HASH_KINDS`;
  - no foreign key in the metadata, or at migrated head (`PRAGMA foreign_key_list`), has `ON DELETE CASCADE`.
- [x] Web tests: the GET names the Contact; CSRF is required; each POST answer; the Lead page tombstone line holds no name, role, address, hash or LinkedIn URL; other Contacts unchanged; 404s.
- [x] The HEAD bumps and test updates in the Code Map. `nextron-engine/README.md`: a Story 3.13 section.

**Acceptance Criteria:**
- Given the migration at head, when the schema is read, then the table, column, checks, index and triggers exist, and `owners.py` and `pii_registry.py` cover every column.
- Given `tests/contracts` and `lint-imports`, when they run, then every contract holds and each new probe fails as intended.

## Implementation Notes

- **Domain.** `nextron/domain/erasure.py`: `REDACTED`, `ErasureCause`, `Coverage`,
  `TableErasure` (a non-`Coverage` raises `TypeError`; a reason that isn't a stripped text of
  1–300 characters raises `ValueError`), `PII_CLASSES` (the registry's codes, pinned by a unit
  test), `HASHABLE_KINDS` (address, domain, profile), `tombstone_hash` and `tombstone`.
  `tombstone_hash` falls back to SHA-256 of `kind:value` on `IdentityError` or `UnicodeError`,
  encoding with `surrogatepass`, so no `str` makes it raise; a person or unknown kind raises
  `ValueError`, a non-`str` value `TypeError`. `nextron/domain/data_subject_request.py`: the
  machine (`RequestKind`, `RequestStatus`, `RequestEvent.COMPLETE`, `RequestCause`, `CREATION`,
  `TRANSITIONS`, `transition`) and `check_request_ref(ref, *, name)` (a non-`str` `name` raises
  `TypeError`; the messages say what is wrong, never the value). `contactability`:
  `BlockReason.CONTACT_ERASED` last, `ContactFacts.erased` (a non-`bool` raises `TypeError`),
  `_checked_keys` drops the Contact's own keys when `erased` (only the Lead's domains are looked
  up), `check` adds `contact_erased`, and `erased_person_keys(email_hash)`.
- **Migration `0028_3_13_erase_contacts`** (down `0027_2_6_contact_methods`). The trigger SQL is
  NULL-safe (`IS` comparisons): a NULL never slips through a `WHEN`.
  `trg_data_subject_requests_insert_open` also refuses an `INSERT` whose `id` exists, so
  `INSERT OR REPLACE` can't replace a request (still three triggers). `trg_contacts_tombstone`'s
  fixed list is every `contacts` column at this revision but `lead_id`, `is_primary` and
  `updated_at`; a test iterates `PRAGMA table_info(contacts)` so a new column fails until the
  trigger is recreated. The Evidence trigger copies 0025's column list minus `quote` and `value`.
- **Registry.** `HASH_KINDS` is a read-only mapping in `pii_registry.py`; `crawled_pages.text` is
  `null`; `contacts.erased_at` and every `data_subject_requests` column but `request_ref`
  (`redact`) are `none`. `nextron/db/erasure.py`'s `tombstoned` raises `KeyError` for a
  `keep_hash` column missing from `HASH_KINDS` (fail closed).
- **Repositories** read the whole row (`select(Model.__table__)`) and update with
  `tombstoned(...)`. `crawl.tombstone_pages` writes only rows whose values change and returns the
  page count. `data_subject_requests.completed_erasures` returns each Contact's **first**
  completed `erase` request (the one that erased it, or, after a retention erasure, the first
  request completed afterwards), so the Lead page's tombstone line names that one. `StoredRequest.request_ref` is hidden from the
  `repr`. `leads.merged_from` walks `merged_into` breadth-first with a visited set (a loop is
  followed once), sorted.
- **`Erasures`** (`nextron/app/compliance/erasure.py`, with `ErasureResult`). `open_dsr` checks
  types, then the kind, the ULID and the reference's shape, then reads the Contact
  (`LookupError`), then checks the reference against its name: `None` for an erased Contact (a
  tombstone is no name). `erase_contact` calls `open_dsr` itself for `dsr` (it finds the open
  request `erase_on_request` committed). Completion goes through `transition(...)`; both
  `events` rows use actor `operator`. Compliance's `erase_pii` validates `n.cause`, returns for
  an erased Contact, raises `LookupError` for an unknown Contact or Lead, and skips an entry
  already `[erased]`. `ERASURE_INCOMPLETE` is the fail-closed message.
- **Research.** `ERASURE` and `ERASED = "erased"` are module constants; `erase_pii` validates
  `n.cause` through `ErasureCause`. `_match` filters erased Contacts first; `_resolve` returns
  for one, so `reparent_lead` moves an erased Contact (the trigger allows the merge's columns)
  without touching its tombstone.
- **No-op hooks.** `setup`, `drafting` (its `api.py` was only a docstring), `jobqueue`, `ops` and
  `notify` each declare `ERASURE` (`not_held`, with a reason) and a module-level `@command
  erase_pii`; `main.build_subscribers` registers them in that order after compliance's and
  research's. `build_app` passes `Erasures(clock, Compliance(clock))` to `create_app`.
- **Web.** The Lead page's Contacts table gains an "Erasure" column (the link); an erased
  Contact's row is one cell spanning the seven columns, with no Provenance row. The erase routes
  are registered after `/leads` and before `/leads/{lead_id}`. A Contact on a merged Lead's
  survivor is found through the merged Lead's URL too (no redirect). The POST checks the
  reference itself first (`check_request_ref` with the Contact's name, `None` once erased), so
  only that check's `ValueError` re-renders as a refusal; any exception from the use case,
  `ValueError` included, is the logged failure answer. The page names the Lead (through
  `|plain` in `hx-disable`); the panel's notice and the Contact's values sit in `hx-disable`
  elements, the form outside them.
- **Tests.** `tests/unit/test_erasure_domain.py`, additions to
  `tests/unit/test_contactability_check.py`, `tests/app/test_erasure.py` (matrix rows, triggers,
  planted-value scan over every column of every table, `before_cursor_execute` log, row counts,
  `events` and `nextron.*` log records), `tests/contracts/test_erasure_contracts.py` and
  `tests/unit/test_erase_page.py`. Updated: the HEAD constants and chain probes (0029/0030),
  `test_source_tree` `FILES`, `test_serve` subscriber pins, `test_web_contracts` routes and
  templates, `test_suppression_contracts` (each writer has one home: `insert` in
  `app/compliance/api.py`, `redact_void_reason` in `app/compliance/erasure.py`; `voided_for` is
  a reader; two new probes), `test_leads_page` (the new column), `tests/dashboard.py`
  (`erasures`), and `test_google_contracts.get_paths`, which now fills every combination of a
  route's path parameters (the erase GET is the first route with two).

- **Review patches (pass 1).**
  - `erase_on_request`'s first transaction now also runs `compliance.suppress(ContactSubject,
    PERSON, DSR, <request id>)` right after `open_dsr`, so a failed erasure leaves the person
    suppressed (the request `open`, the `dsr` entries committed); the second transaction's
    `suppress` finds them `active`.
  - `erase_contact` (`dsr`) suppresses before the already-erased check, so a DSR on a Contact
    retention erased writes its stored address-hash key (`erased_person_keys`) and a re-import
    of the address is blocked.
  - `contacts.tombstone` also clears `is_primary`: a tombstone is nobody's Primary, so
    `has_primary` and `reparent_lead` ignore it.
  - Compliance's `erase_pii` also scans `firm_domain_keys(email, web_domain)` for voided entries.
  - `jobqueue`'s `ERASURE["jobs"]` reason is reworded (a redacted, truncated message holding
    codes by convention); the `completed_erasures` docstrings and README say "the one that erased
    it, or, after a retention erasure, the first request completed afterwards".
  - README: a restore of a pre-erasure backup brings the Contact back until Story 3.16 (erase it
    again); the same-name re-add block needs the Lead's web domain.
  - Tests: the failing-hook tests assert the `dsr` entries survive the rollback (and the gate
    blocks); a retention-then-DSR test; an erased-Primary/merge test; a candidate named
    "Erased"; re-erasing with "Erased per letter 12" (app and web); a voided `reply_rule` domain
    entry planted and redacted.

- **Final verification (orchestrator).**
  - Targeted run of the erasure, erase-page, contract, contactability, leads-page, serve and suppression-contract files: 493 passed.
  - Full suite through `suite_lock.py` (`track-B`): 12689 passed, 12 skipped (`ai_eval` ×2, a symlink test this account can't run, and the 9 "not built yet" AD-16 callers).
  - `lint-imports`: 29 kept, 0 broken.
  - `deferred-work.md` gained five Story 3.13 entries: other Contacts of the same person; Evidence without `person_contact_id`; page URLs that name a person; open erase requests on the health panel; and "no person key" without a web domain.

## Spec Change Log

## Review Triage Log

**Pass 1:** 30 raw findings from three reviewers: blind hunter (BH, 17), edge-case hunter (EC, 9) and verification gap (VG, 2 gaps and 2 merge notes). Deduplicated to 26 entries: 10 patch, 4 defer, 12 reject. No intent_gap or bad_spec, so no loopback.

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | BH1: a failed erasure leaves the person contactable, because `suppress(dsr)` runs only in the second transaction, which rolls back. | medium | `erase_on_request` commits only `open_dsr` first. The gate never reads an open request, so outreach is allowed until a retry. | patch: in the first transaction, `suppress(ContactSubject, PERSON, DSR, request_id)` too, so the person is suppressed once the request is recorded |
| 2 | EC1: a DSR on a Contact already erased by retention skips `suppress`. With no `dsr` entry, a re-import of the same address re-creates the person. | medium | `erase_contact` returns on `erased_at` before the `dsr` branch. Retention wrote no entry. | patch: for `dsr`, call `suppress` before the already-erased check (its erased branch writes the stored address hash key) |
| 3 | BH3 + EC4: an erased Primary Contact stays its Lead's Primary. `has_primary` counts the tombstone, so a merge clears live Contacts' Primary flags. | medium | `contacts.tombstone` writes only the registry columns, and `is_primary` is `none`. `has_primary` has no `erased_at` filter, and `reparent_lead` passes `clear_primary=has_primary(survivor)`. | patch: `tombstone` also sets `is_primary` false (a tombstone is nobody's Primary) |
| 4 | EC5: the void reason of a voided `reply_rule` entry on the firm's domain keys (a "remove us") may name the person, and the compliance hook scans person keys only. | low | `Erasures.erase_pii` builds `person_keys` only. The fix is a one-line direct addition. | patch: also scan `firm_domain_keys(email, web_domain)` |
| 5 | VG1: `test_a_new_candidate_never_matches_an_erased_contact` passes without `_match`'s filter. | low | "Ann Lee" never matches the `[erased]` name key. A candidate named "Erased" would. | patch: add a candidate named "Erased" |
| 6 | VG2: the rule that a tombstone is no name (`name=None` for an erased Contact, at both call sites) is never exercised. | low | No test re-erases with a reference containing "erased". | patch: add a reference containing "Erased" to the web and app re-erase tests |
| 7 | BH9: `jobqueue.ERASURE` says `last_error` is "never a Contact's data", while `pii_registry` says it "could still name a person". | low | The two texts contradict each other. A direct text correction. | patch: make the reason honest (a redacted, truncated exception message holding codes by convention, AD-17 and Story 1.2), so a reader isn't misled |
| 8 | EC2: `completed_erasures`' docstring claims "the one that erased it". After a retention erasure, the first completed request is a later DSR that erased nothing. | low | That DSR found the Contact already erased. After #2 it does suppress, so showing its reference is right, but the claim is wrong. | patch: correct the docstrings (repo, query, Lead page) |
| 9 | BH15: the README doesn't warn that restoring a backup taken before an erasure brings the Contact back until Story 3.16's ledger. | low | True: there is no reapply until 3.16. A one-sentence addition. | patch: add the warning to the README's "Not yet" paragraph |
| 10 | EC7: "the same name at the same Lead is blocked" fails on a Lead with no web domain, because a person key needs one (3.3's binding key scheme). | medium | `person_keys` skips the person key without `web_domain`, so a name-only re-add passes. The key scheme is binding. | patch the README claim ("when the Lead has a web domain"), plus a deferred-work entry for 3.14's receipt residual |
| 11 | BH2: the same person may also be a Contact on another Lead. Only the chosen Contact is erased, nothing lists the others, and their plaintext stays. | medium | Contacts deduplicate per Lead only. The intent erases "a Contact" (the AC), and the DSR keys already block the others. Listing or erasing them is a new feature. | defer |
| 12 | BH4 + EC3: Evidence quoting the person without `person_contact_id` survives, because extraction always inserts `person_contact_id = NULL`. | medium | Pre-existing: AD-17 says "Evidence naming a person carries `person_contact_id`", and 2.9 never sets it. The AC scopes erasure to rows that carry it. | defer (the story that links Evidence to people, 2.10 or later; residual on 3.14's receipt) |
| 13 | BH5: page URLs that name a person (`/our-people/jane-doe`) are PII class `none` in `crawled_pages.url`/`final_url`, `evidence.source_url`/`pages_checked` and `leads.contact_methods`. | medium | Pre-existing classification (Stories 2.5, 2.6 and 2.9). Reclassifying means changes across tables and consumers. | defer |
| 14 | BH14: an `open` erase request left by a failure isn't surfaced (no alert or health issue). | medium | True. Story 3.14 adds `queries.compliance.health_issues()` for failed erasure handlers. After #1 the person is suppressed meanwhile. | defer (3.14) |
| 15 | BH6: the fail-closed check covers only `erased_at`, and a skipped Provenance tombstone would be frozen. | false | Research's hook tombstones Provenance before the Contact, and the planted-value scan fails if any Provenance plaintext survives. No path skips it. | reject |
| 16 | BH7: nothing guards an `INSERT` on `contacts` (with `erased_at` set, or `INSERT OR REPLACE` over a tombstone). | low | No code path does either (the repository uses a plain `insert`). The fix adds a guard trigger for an unshown state. | reject |
| 17 | BH8: a redacted `void_reason` could be changed back on a voided entry (0022's trigger allows it). | low | No code writes `void_reason` except the redaction. The fix amends 0022's trigger. | reject |
| 18 | BH11 + EC8: the failure notice says "the request stays open" even when the first transaction (`open_dsr`) failed and no request exists. | low | Rare (a locked DB or a vanished Contact after the pre-check), and the text is the spec's mandated wording. | reject (spec-mandated) |
| 19 | BH12: `check_request_ref` refuses only the whole name, not a surname, an address local part or a phone number. | low | That is decision 10's rule in the frozen spec. | reject (would change the approved spec) |
| 20 | BH13: "The Lead and its other Contacts don't change" sits beside nulling the Lead's page text, and a re-crawl stores the text again. | low | The same panel paragraph says the page text is tombstoned. AD-17 allows page text for 30 days. Noted for 3.14's receipt. | reject |
| 21 | BH16 + EC9: no DB rule enforces one open request per (Contact, kind, reference), and concurrent same-reference posts give duplicate completed requests. | low | `BEGIN IMMEDIATE` serialises the transactions, the form has `hx-sync="this:drop"`, and the outcome is a harmless second completed record. | reject |
| 22 | BH17: hooks after research see only tombstones. | false | Owner hooks find their rows by `contact_id` (AD-17: ids, not plaintext), and research's hook reads the row itself. | reject |
| 23 | EC6: a Lead whose web domain changed leaves old-domain person-key void reasons unredacted. | low | Rare. The fix needs an entry-to-Contact link (`cause_ref`/`events` lookups). | reject |
| 24 | BH10: an already-erased repeat request completes with the same event as a real erasure, so the outcome isn't stored. | low | True. Storing an outcome is 3.14's receipt work (noted in the report). | reject |
| 25 | VG note a: main's `domain/eligibility.py` `BLOCK_REASON_KINDS` lacks `contact_erased` (`KeyError` at merge; `test_eligibility` catches it). | low | Not in this tree (3.6 is on main only). | reject here; reported to the main session for the merge |
| 26 | VG note b: main's `0028_2_10_primary_contact` adds `contacts.primary_set_by`. `trg_contacts_tombstone`'s fixed list and `test_every_column_..._is_fixed` must be recreated with it at merge; 2.10's `select_primary` may write `is_primary`/`primary_set_by` on an erased Contact. | low | Not in this tree. | reject here; reported to the main session for the merge |

## Verification

**Commands** (from `nextron-engine/`):
- `uv run pytest -q tests/unit/test_erasure_domain.py tests/app/test_erasure.py tests/contracts/test_erasure_contracts.py <touched test files>`: all pass.
- `uv run pytest -q tests/contracts`: all pass (`ai_eval` and the "not built yet" items skipped).
- `uv run lint-imports`: every contract kept.
