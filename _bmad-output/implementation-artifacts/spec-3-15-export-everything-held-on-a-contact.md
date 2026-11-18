---
title: 'Story 3.15: Export everything held on a Contact'
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: '581e81e9682457228631ff05e3e88f78b3e73b19'
review_loop_iteration: 0
story_key: '3-15-export-everything-held-on-a-contact'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Mac has no way to answer a UK GDPR subject access request (FR-52, AD-17): the Contact's data is spread over `contacts`, `provenance_records`, `evidence`, Signals, crawled pages, the Suppression List and the event log, and nothing collects it. Nothing stops a future personal-data column from being missed either.

**Approach:**
- Each owner gets a registry-driven exporter, `queries.<owner>.export_pii(...)`.
- `Compliance.export_contact(contact_id)` assembles them into one JSON document.
- A declaration in `nextron.domain.contact_export` (`COVERAGE`) says, for every table holding a personal-data column, which owner exports it, or why it holds nothing about a Contact. A contract test derives the list of tables that must be covered from `pii_registry.PII` and `owners.TABLES`, and also checks the columns.
- On the Lead page, each Contact gets an "Export data (JSON)" form. Its POST, with CSRF, opens a request of kind `export`, completes it, and builds the file. The browser downloads it through a one-time in-memory ticket. Nothing is written to disk.

The acceptance criteria are Story 3.15 in `_bmad-output/planning-artifacts/epics.md`, word for word, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**
- **The `data_subject_requests` table** (migration `0028_3_15_subject_requests`, owner `compliance`) follows Story 3.13's AC exactly:
  - columns `id`, `contact_id` (FK `contacts.id`, indexed), `kind` ∈ {`erase`, `export`}, `status` ∈ {`open`, `completed`}, `request_ref` (≤200 characters, PII `redact`), `opened_at`, `completed_at` (NULL exactly while `open`), and `receipt_id` (a nullable ULID, with no FK until `erasure_receipts` exists);
  - named CHECKs, and no cascade;
  - every other column is PII `none`.
- **`nextron/domain/subject_request.py`** (pure):
  - `RequestKind`, `RequestStatus`, `RequestEvent.COMPLETE`, `RequestCause` (`opened`, `completed`), `CREATION` and `transition` (`open` → `completed`; anything else raises `IllegalTransition`);
  - `checked_request_ref(text)` returns the stripped text: 1–200 characters with no `Cc`/`Cf` character. Otherwise it raises `RequestRefused(DomainError)` with `.message`: "Enter the request reference" (blank) or "A request reference is at most 200 characters, with no control characters".
- **`Compliance`:**
  - **`@command open_dsr(contact_id, kind, request_ref) -> str`** checks the arguments (`TypeError` / `ValueError` / `RequestRefused`). An unknown Contact, or a non-ULID id, raises `LookupError` with no id in its message. It inserts an `open` row and one `events` row (entity `data_subject_requests`, `None` → `open`, cause `opened`, actor `operator`).
  - **`@command complete_dsr(request_id)`** applies `transition`, sets `completed_at` to the clock's now, and appends an `events` row (cause `completed`).
  - **`export_contact(contact_id) -> ContactExport(filename, body)`** only reads, so it works in a read-only unit too. It reads the exporters and `eventlog.history("contacts", contact_id)`, then `contact_export.assemble` and `render`.
  - **`@use_case export_on_request(contact_id, request_ref) -> ContactExport`** runs `checked_request_ref` before the transaction. Then, in one transaction: `open_dsr(export)`, `complete_dsr`, `export_contact`. Any failure rolls all three back.
- **Exporters** (each returns `{section: tuple[dict, ...]}`, reads only its own repos, and returns every row's columns from a generic `select(<table>)`, so a new column is exported without code):
  - **`queries.research.export_pii(contact_id)`** (`LookupError` when unknown) returns these sections:
    - `contact`: the one row;
    - `provenance_records`: every column, every eligibility basis included;
    - `addresses`: from `address_history`;
    - `evidence`: rows whose `person_contact_id` is the Contact; `pages_checked` is decoded to a list;
    - `signals`: Signals linked to that Evidence, each with `evidence_ids`;
    - `pages`: the Lead's `crawled_pages` whose stored text mentions the Contact. Every column except `text`, plus `mentions` and `text_excerpts`;
    - `contact_methods`: the Lead's `page_contact_methods` whose `matched` mentions the Contact, with `page_url` and `mentions`.
  - **`queries.compliance.export_pii(contact_id, value_hashes)`** returns:
    - `suppression`: every entry, active or voided, whose `value_hash` is in `value_hashes` or whose `cause_ref` is one of the Contact's requests. Each shows only `kind`, `status`, `source`, `created_at`, `voided_at` and `void_reason`, ordered by `created_at`, then `id`;
    - `requests`: every column of the Contact's `data_subject_requests`, by `opened_at`.
  - **`queries.notify.export_pii(contact_id)`** returns `alerts` and `notifications` whose `subject` is the Contact's id.
- **`nextron/domain/contact_export.py`** (pure):
  - `SCHEMA_VERSION = 1`;
  - `COVERAGE: table → Exported(owner, section, shown_as={}, withheld={}) | NotAboutAContact(owner, reason)`;
  - `EXTRA_SECTIONS` (`research.addresses`, `research.signals`, `event_log.contact_history`);
  - `TOMBSTONE = "[erased]"`;
  - `mention_terms`, `mentions`, `excerpts`, `address_history`, `export_hashes`, `assemble`, `render` and `filename`.
- **`assemble`** emits `schema_version`, `generated_at`, `contact_id` and one object per owner holding every declared section, as an array that is `[]` when empty and never omitted. An undeclared section raises `ValueError`.
- **`render`** writes UTF-8 JSON with an indent of 2. A datetime becomes `YYYY-MM-DDTHH:MM:SS.ffffffZ` in UTC, and a date ISO; any other type raises `TypeError`.
- **`filename`** is `contact-<ULID>-export.json`.
- **The web routes:**
  - **`POST /contacts/{contact_id}/export`** (`contact_export`, `@personal_data(True)`):
    - It takes one `request_ref` field; any other form is `MalformedForm`, 400.
    - `RequestRefused` gives 200 with the form fragment and its message. The typed value is never echoed.
    - `LookupError` gives a plain 404, "Contact not found".
    - Success gives 204 with `HX-Redirect: /exports/<ticket>`.
  - **`GET /exports/{ticket}`** (`contact_export_download`, `@personal_data(True)`) answers `application/json` with `Content-Disposition: attachment; filename="contact-<ULID>-export.json"` and `Cache-Control: no-store`.
  - **The ticket** is `secrets.token_urlsafe(32)`. It is held only in memory in `create_app`, under a lock, bound to the session's CSRF token (`compare_digest`), and valid for one use within 60 s; at most 16 are held, and the oldest is dropped first. A missing, expired, used or other-session ticket gets a plain 404, "Export expired: export again from the Lead page".
- **The Lead page.** A "Data subject requests" section follows the Contacts table: one form per Contact, outside any `hx-disable`. Each has `method="post"`, `hx-post`, `{{ csrf_field() }}`, a `request_ref` input (maxlength 200) and the button "Export data (JSON)". It names the Contact (its name, otherwise its email) in a `<span hx-disable>` through `|plain`.
- **Nothing in the export path is logged:** no value, filename or ticket, and no exported content.

**Never:**
- Another Contact's rows, a whole page text, a suppression `value_hash`, an `id`, `domain_plain` or `cause_ref`, or any secret or credential in the file.
- A file written to disk, or the export stored in the DB, a job payload or `events`.
- Erasure (3.13), receipts (3.14), the ledger (3.16), messages or replies (Epics 5–7).
- Changes to `suppress`, `assert_contactable`, the evidence triggers or the existing migrations.
- Custom JavaScript, `include_router`, or a GET that writes to the DB.
- Editing outside `nextron-engine/`, except this spec, `sprint-status.yaml` and `deferred-work.md`.

### Decisions made overnight (pending user review)

1. **The request table is built here.** It follows 3.13's AC schema exactly, because 3.13 (another track) isn't in this tree and the AC requires opening and completing a request. **At merge, keep one table, migration and `open_dsr`,** and point 3.15's calls at it.
2. **Download through a ticket.** The guard refuses native form POSTs (`Origin: null`), and HTMX can't save a file. So the CSRF POST builds the file and answers `HX-Redirect`, and a one-time, session-bound, 60-second in-memory ticket GET serves the attachment. Like `/enter`, that GET consumes in-memory state only. A ticket that is never fetched leaves the request `completed`, and Mac exports again.
3. **Page-level data counts as held on a Contact** only when its stored text mentions the Contact's stored name (at least 3 characters, whole words, case-insensitive, any whitespace) or email address (case-insensitive, whole address).
   - For a page, the export gives the lines that mention the Contact (each at most 300 characters around the match, at most 20 per page), never the whole page. The rest of the page is the firm's and other people's data.
   - For a contact method, it gives the `matched` text.
4. **Suppression facts** cover every key the gate checks for the Contact (`lookup_keys`), the address keys of every address in its Provenance, any stored value that is already a key hash (an erased Contact), and entries whose `cause_ref` is one of its requests (3.13's DSR keys).
   - Firm-domain entries are included, because they block the person.
   - No hash, id, domain or cause is shown.
5. **Tables not about a Contact** are listed with a reason: `settings` and `footer_snapshots` (Nextron's own data), `jobs` (ULID payloads and redacted engine errors), and `backups` and `restore_tests` (engine errors).
6. **`notify` exports by `subject` = the Contact's id.** No alert or notice has such a subject yet.
7. **Extra sections:** `addresses`, `signals` and `event_log.contact_history`, the Contact's `events` rows. These hold the Address Class history.
8. **Messages and Replies are deferred** to Epics 5–7. Their tables will hold PII columns, so the coverage contract will force their exporters.
9. **`queries.compliance.export_pii` also takes `value_hashes`,** because AD-3 forbids it reading `research`'s Contacts.
10. **The request is completed in the export's transaction,** so the file shows it as `completed` at `generated_at`.
11. **An erased Contact's export** reads whatever its columns hold. `[erased]` is never a mention term or person key, and a hashed email has no `@`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Full export | Contact with provenance (an LIA basis), 2 addresses, person Evidence → Signal, a page naming them, a method row, address and domain entries, an alert | every section filled; new request `completed`, 2 `events` rows; 204 + `HX-Redirect`; the GET gives the attachment | N/A |
| Bare Contact | only `contacts` and one provenance record | every declared section present, `[]` when empty | N/A |
| Other people | second Contact on the Lead with its own provenance, Evidence, entries and request; pages mentioning only them | none of their values, rows or entries appear | N/A |
| Erased (simulated) | name `[erased]`, email = its address key hash, provenance tombstoned, page text NULL | tombstones plus suppression facts found by hash or request `cause_ref`; no pages or methods | N/A |
| Bad reference | blank, whitespace, 201 characters, control character | 200 form with message; no row; not echoed | refused |
| Unknown Contact | non-ULID or absent id | 404 "Contact not found"; nothing written | refused |
| Ticket | reused, after 60 s, other session, unknown | 404 "Export expired…"; `no-store` | refused |
| CSRF / Origin / Sec-Fetch-Site | missing on the POST | 403; nothing written | refused |

</frozen-after-approval>

## Code Map

- **Compliance service.**
  - `nextron/app/compliance/api.py`: `Compliance(clock)`, `ENTITY`, `OPERATOR_ACTOR`, the `@command`/`@use_case` patterns (`reverse_optout` ~437, `add_value` ~509) and `_facts` (~562; `ContactFacts` from stored values).
  - Extend the module docstring. Keep everything that exists unchanged.
- **Domain.**
  - `nextron/domain/contactability.py`: `lookup_keys`, `ContactFacts`, `LeadFacts(web_domain, (), None)`, `person_keys`, `is_hash` and `HASH_LENGTH`. Reuse them.
  - `nextron/domain/machines.py` (`Transition`, `IllegalTransition`) and `nextron/domain/suppression_entry.py` (machine layout to mirror).
  - `nextron/domain/suppression_list.py`: `checked_reason`, the Cc/Cf rule to mirror.
- **Queries and repositories.**
  - `nextron/app/queries/{research,compliance,notify}.py`: add `export_pii`. The import rule is `nextron.domain` plus the owner's own repos only (contract group 7; `queries/__init__.py`).
  - **New `nextron/db/repos/research/export.py`:** generic `select(Model.__table__)` row dicts for `contacts`, `provenance_records`, person Evidence, signals through `signal_evidence`, the Lead's pages, and the methods joined to `crawled_pages.lead_id`. Models are in `db/models/research.py` (contacts, provenance), `signals.py` (evidence, signals, signal_evidence) and `crawl.py` (pages, methods; `MAX_MATCHED` is 200).
  - `nextron/db/repos/compliance/suppression_entries.py`: add `for_export(value_hashes, cause_refs)`.
  - **New `db/repos/compliance/subject_requests.py`:** `insert`, `get`, `complete` and `for_contact`.
  - `nextron/db/repos/notify/{alerts,notifications}.py`: add `about(subject)`.
  - Update each `__init__.py` docstring.
- **The event log.** `nextron/db/eventlog.py`: add `history(entity, entity_id)`, the rows in the order `at`, then `id`. The app may import `eventlog` (contract `c1-app-db-surface`); the queries convention may not.
- **Models, migration and registries.**
  - `nextron/db/models/compliance.py`: add the model. `nextron/db/models/research.py` shows the constraint and docstring style.
  - **New `db/alembic/versions/0028_3_15_subject_requests.py`:** mirror `0024_3_8_unsub_tokens.py` (down revision `0027_2_6_contact_methods`).
  - `db/owners.py` and `db/pii_registry.py`: add the new table.
- **Head pins.**
  - The `HEAD` constants: `tests/contracts/test_persistence_contracts.py:81`, `tests/unit/test_migrations.py:39` and the `tests/app/test_serve_process.py:324` assert.
  - The chain probes in `test_persistence_contracts.py` (~808-818): `0028_*` becomes `0029_*`, and `0029` becomes `0030`.
  - `tests/contracts/test_source_tree.py` `FILES` (~150).
- **Web.**
  - `nextron/web/app.py`: add the routes after `suppression_reverse` (~1257). Copy the refusal flow of `suppression_add` and `NO_STORE`, and read `request.scope[CSRF_SCOPE_KEY]` (`nextron/web/csrf.py`). The `/static` registry and `STATIC_TYPES` stay unchanged.
  - New `nextron/web/export_page.py`: `submitted_reference`, `form_context`, `ExportTickets` and `download_response`. Copy `_only` from `suppression_page.py:253`.
  - `nextron/web/leads_page.py`: give `ContactRow` and `contact_row` an `id`.
  - `nextron/web/templates/lead.html` (the Contacts table is `hx-disable`, line ~71), plus a new `contact_export_form.html`.
  - `nextron/web/static/app.css`: optional, CRLF-safe edit only.
- **Tests.**
  - Web helpers: `tests/dashboard.py` (session, post, get), `tests/unit/test_suppression_page.py` (the 403, no-store and no-echo patterns) and `tests/unit/test_leads_page.py` (the Lead page fixtures).
  - App tests use `tests/app/test_suppression_panel_commands.py` (units, `gate_bypassed()`) and `tests/app/test_leads.py` (`queue_on`). Seed Contacts through `Research.add_contact`, or write rows with repos.
  - `tests/contracts/test_web_contracts.py`: the route lists and template list.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/subject_request.py`, `nextron/domain/contact_export.py`: the pure modules, with `COVERAGE` covering all 14 tables with personal-data columns once the new table is added.
- [x] Migration, model, `owners.py`, `pii_registry.py`, the repos, `eventlog.history`, and the head pins and probes.
- [x] `queries.research`, `queries.compliance` and `queries.notify`: `export_pii`. `Compliance`: `open_dsr`, `complete_dsr`, `export_contact` and `export_on_request`.
- [x] `nextron/web/export_page.py`, `web/app.py`, `leads_page.py`, `lead.html` and `contact_export_form.html`: the form and the routes.
- [x] `tests/unit/test_subject_request.py` and `tests/unit/test_contact_export.py`: the machine, the reference rules, mentions and excerpts (boundaries, length, the 20 cap, tombstone), `address_history`, `export_hashes`, `assemble` (empty arrays, unknown section) and `render` types.
- [x] `tests/app/test_contact_export.py`: every matrix row on real units, including no other Contact's data, the simulated erased Contact, rollback of the request on failure, and no logged content (`caplog`).
- [x] `tests/unit/test_export_page.py`: the POST and GET routes, the ticket rules (reuse, expiry with an injected clock, other session, capacity), the headers, the 400, 403 and 404 answers, and the form on the Lead page.
- [x] `tests/contracts/test_export_contracts.py`:
  - (a) derive the tables with personal-data columns from `PII`, and fail when one has no `COVERAGE` entry, when its owner isn't the table's `TABLES` writer, or when an entry is stale;
  - (b) `shown_as` and `withheld` keys must be personal-data columns of that table;
  - (c) each exporting owner has `queries.<owner>.export_pii`;
  - (d) a seeded full export has rows in every `Exported` section, and each row carries every personal-data column (or its `shown_as` key) not `withheld`;
  - (e) withheld values never appear in the file;
  - each check gets a probe test that it catches the problem.
- [x] Contract pins: `test_web_contracts.py` and `test_source_tree.py`.
- [x] `nextron-engine/README.md`: a Story 3.15 section.

**Acceptance Criteria:**
- Given the code base, when `uv run pytest -q tests/contracts` and `uv run lint-imports` run, then every contract holds. A probe table with a `redact` column and no `COVERAGE` entry fails the export contract.
- Given the engine running, when Mac exports a Contact from the Lead page, then the browser saves `contact-<ULID>-export.json` and the page stays.

## Implementation Notes

- **Migration** `0028_3_15_subject_requests` (revision id = file stem, down revision `0027_2_6_contact_methods`): `data_subject_requests` with `fk_data_subject_requests_contact_id_contacts` (no cascade), `ix_data_subject_requests_contact_id` and the CHECKs `ck_data_subject_requests_kind`, `_status`, `_request_ref` (1 to 200 characters) and `_completed` (`(status = 'completed') = (completed_at IS NOT NULL)`). Model `DataSubjectRequestRecord` in `db/models/compliance.py`; `owners.py` (`compliance`) and `pii_registry.py` (`request_ref` `redact`, the rest `none`). Head pins bumped; chain probes now `0029`/`0030`.
- **Domain.** `subject_request.py`: the machine, `RequestRefused` (code `request_refused`), `checked_request_ref` (strip, 1–200, no `Cc`/`Cf`). `contact_export.py`: `COVERAGE` (9 `Exported`, 5 `NotAboutAContact`; `crawled_pages.text` is `shown_as` `text_excerpts`, `suppression_entries.value_hash` is `withheld`), `EXTRA_SECTIONS` as `(owner, section)` pairs, the derived `SECTIONS`, `ContactExport(filename, body)` (body out of the `repr`), `MentionTerm`, `mention_terms`, `mentions` (term labels `name`/`email`), `excerpts` (lines split at `
` or ``; one excerpt per line, the window around the line's first match), `address_history(records, email)` (`{address, current, history[{address_class, source_kind, retrieved_at}]}`), `export_hashes`, `assemble`, `render` (naive datetime and NaN raise `ValueError`, other types `TypeError`) and `filename`.
- **Repositories.** New `repos/research/export.py` and `repos/compliance/subject_requests.py`; `suppression_entries.for_export` (classified a reader in `test_suppression_contracts.py`); `alerts.about`/`notifications.about`; `eventlog.history`. All exporter reads are `select(<table>)` returning `dict(row._mapping)`.
- **Queries.** `research.export_pii` (pages drop `text`, add `mentions` and `text_excerpts`; methods add `mentions`; `pages_checked` JSON-decoded), `compliance.export_pii(contact_id, value_hashes)` (non-hash items ignored), `notify.export_pii`.
- **Compliance.** `open_dsr`, `complete_dsr` (`IllegalTransition` when already `completed`, also when the guarded `UPDATE` changes no row), `export_contact` (reads the Contact's typed row, its Lead's `web_domain` and its Provenance addresses for `export_hashes`), `export_on_request`.
- **Web.** `export_page.py`: `ExportForm`, `export_form`, `form_context` (`{"export_form": ...}`), `lead_forms`, `submitted_reference`, `ExportTickets(monotonic)` (`issue`, `take`, `OrderedDict` under a `threading.Lock`) and `download_response`. `create_app` builds the tickets on its injectable `monotonic`. `take` pops the ticket on any presentation, so another session's attempt burns it (conservative: one presentation). The `RequestRefused` answer re-reads the Contact in a read-only unit; an unknown one answers 404 even for a bad reference. The Lead page passes `export_forms` built from the stored Contacts; `ContactRow` gained `id`. The "Data subject requests" section is omitted for a Lead with no Contact.
- **Existing tests touched.** `test_leads_page.py` (the new `h2` and the name now also inside the form's `hx-disable` span), `test_google_contracts.py` (a `ticket` path value), `test_suppression_contracts.py` (`for_export` a reader; `domain/contact_export.py` may name the table), the head pins, `test_source_tree.py` and `test_web_contracts.py`.
- **Simulated erasure** (`tests/app/test_contact_export.py`): the test drops `trg_evidence_immutable` on its temporary database to tombstone a quote, as Story 3.13 will have to allow. An erased Contact's hashed `email` and Provenance `address` appear in the file as stored (decision 11), though they equal suppression `value_hash`es; the `suppression` section itself never shows one.
- **Review patch 1:**
  - The Lead page hint and the README now say the engine keeps no copy: the browser saves the file to this PC's downloads, so Mac deletes it once it has been sent.
  - The README says the file is the Art. 15(3) copy. The reply must also give the Art. 15(1) information (purposes, legal basis, recipients, retention period, source, the rights to rectify, erase, object and complain to the ICO, any automated processing), which the privacy notice (`privacy_notice_url`) covers, so the reply links it.
  - `queries.research.export_pii` adds one `email` term (`mention_terms(None, address)`) per distinct Provenance `address` other than the stored email, so a page or contact method naming only an earlier address is exported. `contact_export.mentions` gives each label once, in term order.
  - `_name_term` counts the name's non-whitespace characters (`len("".join(words))`), so "A B" is no term.
  - `excerpts` skips any match that starts before the end of the last line span given, so a match over a line break followed by one on its second line no longer exports the line twice.
  - The `COVERAGE` reasons of `jobs`, `backups` and `restore_tests` are one constant, `UNLINKED`: the rows are never linked to a Contact, so a name an error message carries by chance can't be attributed to anyone.
  - `queries.compliance.export_pii` exports a `domain` entry's `void_reason` as `None` (the key stays): the entry is in every Contact's file at the firm, and its reason is the Operator's note about whoever opted the firm out. A person's own entry keeps its reason.
  - `subject_request.REFUSED_CATEGORIES` adds `Zl` and `Zp` (U+2028, U+2029).
  - In `contact_export`, a `LookupError` answers 404 only when a read-only re-read (`research_queries.contact`) finds no Contact, a non-ULID id included; otherwise it is re-raised, so a `KeyError` or `IndexError` from a bug is a 500.
  - `test_export_contracts.py` also derives every table with a column whose foreign key targets `contacts.id` (`contact_tables(Base.metadata)`: `provenance_records`, `evidence`, `data_subject_requests`) and requires a `COVERAGE` entry for each; the stale check accepts such tables. A probe table with only a foreign key to `contacts.id` and no entry fails. The `COVERAGE` docstring says so.
  - New tests:
    - a page and a contact method naming only an earlier Provenance address;
    - a voided `reply_rule` entry with its `voided_at` and `void_reason`;
    - a voided firm domain entry whose reason is absent while the person's own voided entry keeps its reason;
    - superseded and retracted Evidence and a retracted Signal (`retract_unsupported`), with their `superseded_by` and `retracted_at`;
    - the deduplicated labels, the skipped overlapping match, `Zl`/`Zp` and the `KeyError`/`IndexError` 500.
- The file is built inside the write transaction (decision 10's atomicity); a Lead's page scan runs well under the 5 s `busy_timeout`, an accepted trade-off.

## Spec Change Log

## Review Triage Log

Pass 1 (review_loop_iteration 0). Layers: blind hunter (BH, 16 findings), edge-case hunter (EC, 9), verification gap (VG, 2). 27 raw findings, 24 entries after grouping: 12 patch, 1 defer, 11 reject. No intent_gap or bad_spec entry, so no loopback.

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| BH1 | The Lead page hint says "nothing is kept on this PC", but the browser saves the file to Downloads | medium | `lead.html` hint text. The engine keeps no copy, but the downloaded file is personal data on the PC. | patch: reword the hint (engine keeps no copy; delete the file once sent) and add the same to the README |
| BH2 | Pages and methods are scanned only for the current email, not the Contact's earlier addresses | medium | `queries/research.py` `export_pii` calls `mention_terms(name, email)`, while `address_history` lists every Provenance address. A page that names an old address is missed. | patch: add an email term per distinct Provenance address (labels deduplicated) |
| BH3, EC9 | Evidence that quotes the person without `person_contact_id` is never exported; the only Evidence writer in this tree stores `person_contact_id=None` | medium | `export.person_evidence` follows AD-17 ("Evidence naming a person carries `person_contact_id`"). The gap is in the writers (Story 2.9's extraction; Story 2.10 on another track adds person Evidence), and erasure (3.13) relies on the same link. | defer (pre-existing in the Evidence writers): `deferred-work.md` |
| BH4 | Alerts and notifications are exported by `subject == contact_id` only, a convention no kind uses yet | low | True, and decision 6 says so. A future kind keyed by a reply id would be missed, but none exists. Fixing it means designing a convention for kinds that don't exist yet. | reject: by design (decision 6); passed to later stories in the report |
| BH5 | The coverage contract can't see a future table that references a Contact but holds no PII-classed column | medium | `personal_columns()` derives tables from PII classes only. A table with a `contact_id` FK and only `none` columns (an enrolment, say) is data about the person but would never be forced into a decision. | patch: the contract also derives every table with an FK to `contacts.id` and requires a `COVERAGE` entry |
| BH6 | The "not about a Contact" reasons for `jobs`, `backups` and `restore_tests` contradict the registry's "could still name a person" | low | `pii_registry.py:433-486` comments. The exclusion holds, because nothing links those rows to a Contact, but the wording claims more than it can. | patch: reword the three reasons (never linked to a Contact; a name an exception carries by chance can't be attributed) |
| BH7 | A firm-domain entry's `void_reason` can disclose a colleague | medium | The Contact's file includes the firm's domain entries (decision 4). A voided firm-level `reply_rule` entry carries the Operator's free-text reason about whoever opted out. That state is reachable through `reverse_optout`. | patch: a `domain` entry's `void_reason` is exported as `null`, documented |
| BH8, EC1 | The name floor counts whitespace, so "A B" is a term; a one-word name ("Jane", "Team") matches other people's lines | medium | `_name_term`: `len(" ".join(words)) < 3` counts the space. The one-word case is real too, but decision 3 (frozen) explicitly allows any name of 3 or more characters. | patch (whitespace part): count the name's non-space characters. The one-word part is rejected: it would change decision 3 (reported) |
| BH9 | The 20-excerpt cap drops lines without saying so | low | A page would need more than 20 lines naming the Contact; rare. The fix adds a new key and logic. | reject: low, and the fix adds complexity |
| BH10 | No `received_at` for the statutory deadline, and no delivered fact | low | An export is answered at the click, so the deadline matters little here. The schema is Story 3.13's (decision 1). | reject: it would change the approved schema and spec (reported) |
| BH11 | The file holds no Art. 15(1) information (purposes, recipients, retention, rights) | medium | The README and the hint never tell Mac that the reply needs this beside the copy. | patch: a README paragraph listing what the reply must add, with the privacy notice covering it |
| BH12 | No way to find every Contact who is the same person | medium | The AC exports "a Contact on the Lead page", so cross-Contact lookup is outside the intent. | reject: out of scope by the AC (reported as a follow-up) |
| BH13 | The `events` rows of requests and suppression entries aren't exported | low | Those rows repeat the request and entry times already in the file, with no personal data. Adding sections adds declared surface. | reject: low, and the fix adds complexity |
| BH14, EC5 | No feedback after a successful export: the typed reference and a stale alert stay, and a second click opens another request | low | True. `HX-Redirect` swaps nothing. A reset needs inline or custom JS (forbidden) or a different download flow. A second click only adds another completed record. | reject: low, and the fix adds complexity |
| BH15 | The file is built inside the write transaction | low | Deliberate (decision 10, atomic request and file). A Lead's pages are scanned in well under the 5 s `busy_timeout`. | reject: by design; trade-off recorded in Implementation Notes |
| BH16 | Mentions don't normalise Unicode or typographic punctuation | low | True, but rare. Normalising while keeping the excerpt offsets adds real complexity. | reject: low, and the fix adds complexity |
| EC2 | A match over a line break, then a match on its second line, exports the same line twice | low | The reviewer reproduced it. `excerpts` dedupes exact line spans only. The fix is a direct correction. | patch: skip a match that starts inside the last emitted span |
| EC3 | U+2028 and U+2029 pass `checked_request_ref` despite "no line break" | low | `REFUSED_CATEGORIES` is `{Cc, Cf}`, and U+2028 is `Zl`. Direct correction. | patch: also refuse `Zl` and `Zp` |
| EC4 | A `KeyError` or `IndexError` raised inside the export shows as 404 "Contact not found" | medium | `web/app.py` `except LookupError`. Both are `LookupError` subclasses, so a bug is masked as a missing Contact. | patch: answer 404 only when the Contact is really absent (re-read it); otherwise re-raise |
| EC6 | A ticket never fetched keeps its body in memory until the next `issue` or `take` | low | In process memory only, on localhost. A timer sweep adds a thread. | reject: low, and the fix adds complexity |
| EC7 | `ExportTickets(capacity<=0 / lifetime<=0)` gives a `KeyError` | false | `create_app` builds it with the defaults (16, 60 s) only; no caller passes other values. | reject |
| EC8 | A multi-recipient `matched` text puts another person's address in the file | low | Decision 3 (frozen) exports a method's `matched` text. It is at most 200 characters and rare. | reject: it would change decision 3 (reported) |
| VG1 | Voided entries are never exported in a test | medium (gap) | Filed evidence: every seeded entry is `active`, so an `active`-only filter in `for_export` would pass. | patch: a test reverses a `reply_rule` entry and checks its `voided` row, `voided_at` and `void_reason` |
| VG2 | Superseded or retracted Evidence and retracted Signals are never seeded | medium (gap) | Filed evidence: a `live_only` or `current_only` filter would pass every test. | patch: a test supersedes and retracts Evidence, retracts a Signal, and checks the rows stay |


## Design Notes

The shape of the file (abridged):

```json
{"schema_version": 1, "generated_at": "2026-11-18T09:00:00.000000Z", "contact_id": "01J…",
 "research": {"contact": [{…}], "provenance_records": [], "addresses": [], "evidence": [],
              "signals": [], "pages": [], "contact_methods": []},
 "compliance": {"suppression": [{"kind": "address", "status": "active", "source": "operator",
                "created_at": "…", "voided_at": null, "void_reason": null}], "requests": [{…}]},
 "notify": {"alerts": [], "notifications": []},
 "event_log": {"contact_history": [{…}]}}
```

The generic repository select is what makes the exporter registry-driven: columns are never listed by hand, so the behavioural contract (d) only fails when a new table, or a renamed or withheld column, needs a decision.

## Verification

**Commands:**
- `cd e:/Nextron-track5/nextron-engine && uv run pytest -q tests/unit/test_contact_export.py tests/unit/test_subject_request.py tests/unit/test_export_page.py tests/app/test_contact_export.py tests/unit/test_leads_page.py tests/unit/test_migrations.py` -- expected: all pass.
- `uv run pytest -q tests/contracts` -- expected: all pass.
- `uv run lint-imports` -- expected: every contract kept.
