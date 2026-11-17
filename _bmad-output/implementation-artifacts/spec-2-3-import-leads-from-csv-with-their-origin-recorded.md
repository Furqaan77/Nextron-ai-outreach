---
title: 'Story 2.3: Import Leads from CSV with their origin recorded'
type: 'feature'
created: '2026-11-17'
status: 'done'
baseline_commit: '8b32024844cd2c5d0b34acac9d5920dcfe02567a'
route: 'dispatch'
review_loop_iteration: 0
story_key: '2-3-import-leads-from-csv-with-their-origin-recorded'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Mac can't bring hand-built lists into the engine. FR-59 needs a CSV import that turns each row into one deduplicated Lead (FR-9) and each listed person into a Contact, with every Contact and address carrying a Provenance Record that names the list's origin (FR-51) and every imported address starting `guessed` (FR-15). No `contacts`, `provenance_records`, Address Class machine, FR-49 denylist or upload form exists yet.

Requirements: FR-59, FR-9, FR-15, FR-51, FR-49 · AD-3, AD-4, AD-7, AD-13, AD-17, AD-18 · UX-DR20.

**Approach:**
- An upload page (`/leads/import`) takes the origin, a default Segment and a UTF-8 CSV.
- A stdlib domain planner (`domain/lead_import.py`) validates the file and each row; the `discovery` use case `LeadImports.run` then calls `discovery.upsert_lead` and `research.add_contact(source=csv)` per row, in batched transactions.
- Migration `0012_2_3_contacts_provenance` creates `contacts` and `provenance_records` (owner `research`), with the Address Class machine in `domain/address_class.py` and `research.reparent_lead` registered for `LeadsMerged`.

The acceptance criteria are Story 2.3 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**

- **Identity additions** (`nextron/domain/identity.py`, still the only normaliser).
  - `normalize_country_code`: after the existing strip, ASCII check and uppercase, `COUNTRY_ALIASES = {"UK": "GB"}` maps the alias, then the code must be in `COUNTRY_CODES`. Every other rule stays.
  - `url_host(text) -> str` is steps 1–4 of `registrable_domain` (strip and refusals, `http(s)` scheme rule, hostname, IP refusal, one trailing separator dropped, IDNA labels, label and 253-character rules), returning the ASCII lowercase host **without** stripping `www`. `registrable_domain` is refactored to call it; its behaviour is unchanged.
- **FR-49 denylist** (new `nextron/domain/fetch_policy.py`, stdlib and `nextron.domain` only). `is_denied(url: str) -> bool` is true when:
  - `url_host(url)` raises `IdentityError` (fail closed);
  - the host is, or is a subdomain of, one of `DENIED_DOMAINS`: `linkedin.com`, `lnkd.in`, `instagram.com`, `instagr.am`, `ziprecruiter.com`, `maps.app.goo.gl`;
  - the host's registrable domain (`registrable_domain(host)`; an `IdentityError` there means no match) has a first label in `DENIED_BRANDS` (`indeed`, `glassdoor`), so `indeed.com`, `uk.indeed.com` and `glassdoor.co.uk` match;
  - Google Maps: the host's first label is `maps` under a registrable domain whose first label is `google` (`maps.google.com`, `maps.google.co.uk`), or the registrable domain's first label is `google` or it is `goo.gl`, and the URL's lowercased path is `/maps` or starts with `/maps/`.

  Its docstring says Story 2.5 adds the fetching adapters and their contract test.
- **Shared platforms** (`nextron/domain/leads.py`). `is_shared_platform(web_domain) -> bool` is true when the domain is in `SHARED_PLATFORM_DOMAINS` or its first label is in `SHARED_PLATFORM_BRANDS` (`google`, `facebook`). The domain list is in Design Notes; a test asserts `registrable_domain("x." + d) == d` for each entry.
- **Contacts domain** (new `nextron/domain/contacts.py`, stdlib and `nextron.domain` only).
  - `ProvenanceSource(StrEnum)`: `csv`, `website`, `signal_inbox`, `referral`, `replier`, `operator`, `pattern`, `finder`, `eligibility_basis`. `ProvenanceSubject(StrEnum)`: `contact`, `address`.
  - **`ContactCandidate(name=None, role=None, email=None, linkedin_url=None)`** is frozen and slotted, checked in `__post_init__`:
    - `name` and `role`: `None` or a `str` of 1–200 characters, equal to its own strip, with no `Cc`, `Cf`, `Zl` or `Zp` character; `name` must also give a `normalize_name` key;
    - `email`: `None` or `normalize_email(x) == x`; `linkedin_url`: `None` or `normalize_linkedin_url(x) == x`;
    - at least one of `name` and `email` is set.

    A problem raises `InvalidContact(field, reason)`: a `DomainError`, code `invalid_contact`, message `"<field>: <reason>"`, never the value. `name_key` is `normalize_name(name)` or `None`.
- **Address Class machine** (new `nextron/domain/address_class.py`, the Job machine's pattern, `MACHINE = "address_class"`).
  - `AddressClass`: `published`, `guessed`, `verified`, `invalid`; `TERMINAL = (invalid,)`.
  - `AddressEvent` and the read-only `TRANSITIONS`:
    - `verify`: `guessed` → `verified`, cause `verification_valid`;
    - `find_on_site`: `guessed` → `published`, cause `found_on_site`;
    - `fail_check`: `published`, `guessed` or `verified` → `invalid`, cause `check_failed`;
    - `hard_bounce`: the same three → `invalid`, cause `hard_bounce`.

    `transition(current, event)` raises `IllegalTransition` for every other pair.
  - `ASSIGNMENT` maps the starting class by source: `csv`, `pattern` and `finder` → `Transition(guessed, assigned_from_<source>)`; `website` → `Transition(published, assigned_from_website)`. `assign(source)` raises `IllegalTransition(MACHINE, "assign", source)` for any other source. `AddressCause` holds exactly these eight causes.
- **Persistence.**
  - **Migration `0012_2_3_contacts_provenance`** (down `0011_2_2_leads`; `downgrade()` raises like 0011). Every column is NOT NULL unless stated, with no FK cascades.
    - **`contacts`:**
      - `id` ULID (`pk_contacts`);
      - `lead_id` ULID, FK `fk_contacts_lead_id_leads`, index `ix_contacts_lead_id`;
      - `name` and `role`, each `String(200)` and nullable;
      - `email` `String(254)`, nullable;
      - `address_class` `String(16)`, nullable;
      - `linkedin_url` `String(2048)`, nullable;
      - `is_primary` `Boolean`, server default false;
      - `tz` `String(64)`, nullable;
      - `market_id` ULID, nullable, FK `fk_contacts_market_id_markets`;
      - `created_at` and `updated_at`, `UtcDateTime`.

      The CHECKs are:
      - `ck_contacts_address_class`: NULL or one of the four classes;
      - `ck_contacts_email_class`: `(email IS NULL) = (address_class IS NULL)`;
      - `ck_contacts_identity`: `name IS NOT NULL OR email IS NOT NULL`.
    - **`provenance_records`:**
      - `id` (`pk_provenance_records`);
      - `contact_id`, FK `fk_provenance_records_contact_id_contacts`, index `ix_provenance_records_contact_id`;
      - `subject` `String(16)`;
      - `source_kind` `String(32)`;
      - `source_detail` `String(2048)`;
      - `address` `String(254)`, nullable;
      - `retrieved_at` `UtcDateTime`;
      - `address_class` `String(16)`, nullable;
      - `notes` `Text`, nullable;
      - the eligibility-basis fields, all nullable: `basis_rule` `String(64)`, `basis_campaign_id` ULID (no FK: no `campaigns` table yet), `lia_ref` `String(200)`, `lia_date` (a `Date` column if the persistence contracts allow it, else `String(10)` ISO date), and `basis_recorded_at` `UtcDateTime`.

      The CHECKs are:
      - `ck_provenance_records_subject`: one of the two subjects;
      - `ck_provenance_records_source_kind`: one of the nine sources;
      - `ck_provenance_records_address`: `(subject = 'address') = (address IS NOT NULL)`;
      - `ck_provenance_records_address_class`: NULL or one of the four classes.
  - Models `ContactRecord` and `ProvenanceRecord` live in `db/models/research.py` (CHECK text built from the enums), imported by `db/models/__init__.py`.
  - **`owners.py`:** both tables → `research`, every column.
  - **`pii_registry.py`** (decision 7):
    - `contacts`: `name` and `role` are `redact`, `email` is `keep_hash`, `linkedin_url` is `null`, and the rest `none`;
    - `provenance_records`: `source_detail` and `notes` are `redact`, `address` is `keep_hash`, and the rest `none`.
  - **Repositories** (explicit Core statements via `current().session`, as in 2.2):
    - `db/repos/research/contacts.py`: `StoredContact`, `get`, `for_lead(lead_id)` (by `created_at`, `id`), `insert(...) -> id`, `fill(contact_id, updated_at, **fields)`, `has_primary(lead_id)` and `move_to_lead(from_lead, to_lead, updated_at, clear_primary)`;
    - `db/repos/research/provenance.py`: `StoredProvenance`, `insert(...) -> id` and `for_contact(contact_id)` (by `retrieved_at`, `id`);
    - `db/repos/setup/segments.py`: add `segment_ids() -> frozenset[str]` (`SELECT id`).
  - Bump `HEAD` to `0012_2_3_contacts_provenance` in `test_persistence_contracts.py` (chain probes `0012_*` → `0013_*`, "revision 12" → "revision 13"), `test_migrations.py` and the `test_serve_process.py` assert. Add every new file to `test_source_tree` `FILES`.
- **Research** (`app/research/api.py`, `Research(clock)`, actor `system:research`).
  - **`@command add_contact(lead_id, candidate, *, source, detail, retrieved_at) -> AddedContact(contact_id, created, email_dropped)`**:
    1. **Argument checks.** It raises `TypeError` for a wrong type: `candidate` not a `ContactCandidate`, `source` not a `ProvenanceSource`, or `retrieved_at` not an aware `datetime`. `detail` must be a stripped `str` of 1–2,048 characters (`ValueError`). An unknown Lead raises `LookupError` and a merged Lead `ValueError`, both via `queries.discovery.lead`. With an email, `assign(source)` runs first, so its refusal writes nothing.
    2. **Matching.** Among `for_lead(lead_id)`, the match is the first Contact with an equal `email`, else (when the candidate has a name) the first with an equal `name_key`.
    3. **No match.** Insert the Contact: Address Class `assign(source).to` when it has an email, `is_primary` false, `tz` and `market_id` NULL, both times now. Then add a `contact` Provenance Record. With an email, also add an `address` record (`address`, `address_class`) and one `events` row: entity `contacts`, `None` → class, cause `assign(source).cause`, no `cause_ref`. Returns `created=True`.
    4. **Match.** Fill each of `name`, `role`, `linkedin_url` and `email` that the Contact lacks; never overwrite. A filled name, role or LinkedIn URL adds one `contact` record. A filled email sets the class and adds the `address` record and the `events` row, as in step 3. A candidate email that differs from the Contact's own gives `email_dropped=True`, and nothing is written for it. When nothing is filled, nothing is written. Returns `created=False`.
    5. **Provenance Records.** Every record has `source_kind` = `source`, `source_detail` = `detail` and `retrieved_at`. `address_class` is set only on `address` records. `notes` and the basis fields are NULL.
  - **`@command reparent_lead(n: LeadsMerged)`.** It moves every Contact of the merged Lead to the survivor (`updated_at` now), clearing the moved Contacts' Primary flags when the survivor already has a Primary. Provenance Records follow their Contact. It writes no event.
- **Discovery.**
  - `upsert_lead` checks `segment_id in queries.setup.segment_ids()` (deferred item J). Nothing else in 2.2's behaviour changes.
  - `main.build_subscribers` registers `LeadsMerged` → `Pipeline(clock).reparent_lead`, then `Research(clock).reparent_lead`.
- **Queries:**
  - `queries.setup.segment_ids()`;
  - `queries.research.contacts(lead_id) -> tuple[StoredContact, ...]`;
  - `queries.research.provenance(contact_id) -> tuple[StoredProvenance, ...]`.
- **Email syntax** (decision 1).
  - **The port.** `ports/email_syntax.py` defines `EmailSyntaxPort.is_valid(address: str) -> bool`, a syntax check only.
  - **The adapter.** `adapters/email_syntax` `EmailValidatorSyntax` calls `email_validator.validate_email(address, check_deliverability=False, allow_smtputf8=True, allow_quoted_local=False, allow_domain_literal=False, allow_display_name=False, allow_empty_local=False, test_environment=False, globally_deliverable=True)`. `EmailNotValidError` gives `False`, and a non-`str` raises `TypeError`.
  - **Registration.** The adapter is classified `NoExternalSystem` in `tests/live_guard.py` and added to contract c5's sources.
  - **The fake.** `tests/fakes/email_syntax.py` `FakeEmailSyntax(refused=())` refuses listed addresses and any address holding `,;<>()[]\"` or a space. It goes in `PORT_FAKES`.
- **The import planner** (new `nextron/domain/lead_import.py`, stdlib and `nextron.domain` only).
  - **Constants:** `MAX_FILE_BYTES = 5 * 1024 * 1024`, `MAX_ROWS = 5000`, origin 3–200 characters, `LABEL_LENGTH = 100`.
  - **`ImportRefused(field, reason)`:** a `DomainError`, code `lead_import_refused`; `field` is `origin`, `file` or `default_segment`.
  - **`check_origin(text) -> str`:** collapse whitespace runs to one space and strip. The result must be 3–200 characters with no `Cc`, `Cf`, `Zl` or `Zp` character.
  - **`plan_import(data, *, segments, default_segment_id, email_ok) -> ImportPlan(rows, rejected)`.** `segments` maps each casefolded Segment key and name to its id. Every problem with the file as a whole raises `ImportRefused("file", ...)`:
    - **The file.** It must not be empty or over `MAX_FILE_BYTES`. It is decoded as `utf-8-sig`, strictly, and must hold no NUL. It is read with `csv.reader(io.StringIO(text, newline=""), strict=True)`; a `csv.Error` gives "The file isn't valid CSV (row <n>: <message>)".
    - **The header** is the first record. Each cell is stripped and casefolded, and each run of whitespace, `_` or `-` becomes one space; it is then matched against `HEADERS` (Design Notes). Unknown headers are ignored. A field named twice, or a missing required field (named in the message), refuses the file.
    - **Rows.** A record with every cell blank is skipped and not counted. More than `MAX_ROWS` counted rows refuses the file. A row's number counts the header as row 1. Each cell is whitespace-collapsed (`" ".join(cell.split())`).
  - **Row checks,** in this order; the first failure rejects the row with its text (Design Notes):
    1. more cells than the header;
    2. missing organization name, website or country;
    3. an organization name that breaks `LeadIdentity`'s name rule;
    4. an unreadable website (`registrable_domain` refuses it);
    5. `is_denied(cell)`;
    6. `is_shared_platform(domain)`;
    7. an unknown country (`normalize_country_code` refuses it);
    8. a non-blank Segment cell that matches no key or name;
    9. a blank Segment cell with no default.
  - **Contact cells** of an accepted row (decision 4). A cell that fails is dropped, with a note:
    - email: `normalize_email`, then `email_ok`;
    - LinkedIn: `normalize_linkedin_url`;
    - name and role: `ContactCandidate`'s text rules.

    A Contact is built when a name or an email remains. If some Contact cell was non-blank but neither remains, the row gets a note and no Contact.
  - **The plan.** Each `ImportRow(number, label, identity, contact, notes)` has a `LeadIdentity` built only from normaliser output. Each `RejectedRow(number, label, reason)` keeps its label, which is the cleaned name cell cut to `LABEL_LENGTH`.
- **The use case** (`app/discovery/api.py`, `LeadImports(clock, discovery, research, email_syntax)`; decision 5).
  - **`@use_case run(request: ImportRequest(origin, default_segment, data)) -> ImportSummary`:**
    1. **Checks before any write.** `check_origin` runs, then the empty-file and size checks. One transaction reads `queries.setup.segments()`. A `default_segment` key that isn't stored raises `ImportRefused("default_segment", ...)`. Then `plan_import` runs, outside any transaction. Any `ImportRefused` means nothing was written.
    2. **Rows.** `imported_at = clock.now()`. Rows run in batches of `BATCH_ROWS = 200`, each in its own `uow.transaction()`. Each row runs `discovery.upsert_lead(identity, LeadSource.CSV)`: `created` counts as imported, otherwise merged. With a Contact, it then runs `research.add_contact(lead_id, contact, source=csv, detail=origin, retrieved_at=imported_at)`; `email_dropped` adds a note.
    3. **A failing batch** rolls back, and `ImportStopped(summary, at_row)` (a `RuntimeError`) is raised from the error. `summary` counts only the committed batches, and `at_row` is the failed batch's first row number.
  - **The result.** `ImportSummary(imported, merged, rejected, noted)` lists rejected and noted rows in row order.
  - **Logging.** It logs `lead_import_finished` (counts), `lead_import_refused` (`field`) and `lead_import_stopped` (`at_row`, error type), never any CSV text, the origin or the file name.
- **Web** (AD-13).
  - **`upload_form` in `web/forms.py`,** an async dependency with a strict stdlib multipart parser:
    - **The request.** It needs exactly one `Content-Type` of `multipart/form-data` with a `boundary` (1–70 RFC 2046 characters, quoted or not); otherwise it raises `MalformedForm("not_multipart")`.
    - **Size.** The body streams through `request.stream()`. Bytes past `MAX_UPLOAD_BODY` (5 MiB + 64 KiB) are drained and discarded, and the result is `UploadForm(too_large=True)`.
    - **Parsing.** The body is `--B\r\n` part (`\r\n--B\r\n` part)* `\r\n--B--`, with an optional final `\r\n`. Each part's headers end at the first `\r\n\r\n`. `Content-Disposition` must fullmatch `form-data; name="<n>"` with an optional `; filename="…"`, and `Content-Type` is the only other header allowed. A part with a file name is a file (`bytes`); any other part is text, decoded as strict UTF-8. Anything else, or a repeated name, raises `MalformedForm`.
    - **Fields.** The handler allows only `csrf_token`, `origin` and `default_segment`, plus the file `file`; anything else is `MalformedForm(WRONG_FIELDS)`. `require_csrf` already accepts the `X-CSRF-Token` header HTMX sends, so `csrf.py` doesn't change.
  - **Routes** (`nextron/web/lead_import_page.py` builds the views; app handlers stay thin):
    - **`GET /leads/import`** (`lead_import_page`, `@personal_data(False)`) renders `lead_import.html`. Its form `lead_import_form.html` has `method="post"`, `enctype="multipart/form-data"`, `hx-post`, `hx-encoding="multipart/form-data"`, `hx-disabled-elt` on its button and `{{ csrf_field() }}`. It holds:
      - an origin input (required, `minlength` 3, `maxlength` 200, with a hint giving an example);
      - a default Segment select ("No default" plus each stored Segment by name, value its key);
      - a file input (`accept=".csv,text/csv"`).
    - **`POST /leads/import`** (`lead_import`, `@personal_data(True)`) runs `lead_imports.run` in `units.open()`. It answers 200 with the form:
      - an `ImportRefused` (or `too_large`) shows its error next to its field, keeping the origin and default typed so far;
      - a success shows the summary above a fresh form: imported, merged and rejected counts, rejected rows with number, label and reason, and imported rows with notes;
      - `ImportStopped` shows its summary and the stop notice.

      Every CSV-derived text renders through `untrusted_block`/`|plain`.
  - `create_app(..., lead_imports=...)` takes the service. `home.html` links to the page. The `app.py` docstring lists both routes.
- **Contract test (deferred item Y),** in `tests/contracts/test_persistence_contracts.py`. For every table with a foreign key to `leads.id`, other than `leads` itself, `main.build_subscribers().subscribers_of(LeadsMerged)` holds a `reparent_lead` bound to a class from `nextron.app.<owner>.api`, where `<owner>` writes that column in `owners.TABLES`. A probe table proves the check fails when the registration is missing.
- Tests run as `test` with temporary DBs, with no network and no keyring. Documentation goes in a new README section, "CSV import, Contacts and Provenance", after "Leads and identity".

**Never:**
- **Out of scope:**
  - no `enrich_lead` enqueue (Story 2.5);
  - no Market check or "blocked by Market" count (3.2);
  - no `assert_contactable` (3.x);
  - no Leads list or Lead page (2.4);
  - no Primary selection or decision-maker role filter (2.10);
  - no `mark_address_invalid` or other class-changing command (2.14, 6.x);
  - no Lead-level Provenance Record (decision 12).
- **Nothing new beyond this story's own:** no new dependency, job type, trigger, setting or credential. Nothing in the engine fetches any URL.
- Don't change `upsert_lead`'s semantics apart from the Segment check, `csrf.py`, the vendored HTMX, `serve`, `doctor`, `open` or `seed sync`. Don't edit anything outside `nextron-engine/` except this spec, `sprint-status.yaml` and `deferred-work.md`.
- Never log, or put in `events`, any CSV cell, origin text, email address or file name.

### Decisions made overnight (pending user review)

1. **Email syntax uses email-validator behind a new offline `EmailSyntaxPort`.** The spine pins email-validator, contract c4 allows it only in adapters, and Story 2.2's triage (I-b) assigned the syntax check to it. The adapter makes no DNS call, and test-only and special-use domains (`.test`, `.local`) fail. The MX and disposable checks stay with Story 2.14.
2. **A row naming an unknown Segment is rejected even when a default is chosen** ("unknown Segment"). A blank Segment cell takes the default, and a blank cell with no default gets "no Segment, and no default Segment was chosen". A typo shouldn't silently land a firm in the wrong Segment's playbook. Segments match by key or name, ignoring case.
3. **Websites on shared platforms are refused** (deferred item A): "a page on a shared platform such as Google Sites or Facebook is not the organization's own website". Otherwise unrelated firms on one platform domain would merge irreversibly.
4. **Contacts:**
   - **Deduplication.** Contacts are deduplicated within their Lead. A match only fills empty fields. A different email on the matched Contact is kept, and the new one is dropped with a note.
   - **What makes a Contact.** A Contact needs a name or a valid email. An invalid email, LinkedIn URL, name or role is dropped with a note, and the rest of the row imports.
   - **No role filter.** Roles aren't filtered: the list is Mac's own choice.
   - **Re-imports.** Re-importing the same file writes nothing new.
5. **Batched commits.** Rows commit in batches of 200, so no write lock lasts long (deferred item J). If a batch fails, the import stops there, earlier batches stay, and the page says so. Importing the same file again is safe, because rows already imported are merged.
6. **File rules:**
   - header aliases (Design Notes), and other columns are ignored and never stored;
   - row numbers count the header as row 1, as a spreadsheet does, and all-blank rows are skipped;
   - 5 MB means 5 MiB;
   - the country is an ISO alpha-2 code, or `UK`;
   - comma-separated values only.
7. **Provenance details:**
   - nine source kinds: AD-7's Contact sources plus `replier`, `pattern`, `finder` and `eligibility_basis`, which later stories name;
   - a `contact` record has no Address Class;
   - an `address` record stores its address, so a later address change can't mislabel it;
   - all five eligibility-basis fields are created now, NULL;
   - PII classes as listed above.
8. **On a merge, Contacts move to the survivor, and duplicates there are kept for now.** Folding duplicate Contacts is logged for Story 2.10. A moved Contact loses its Primary flag when the survivor has a Primary.
9. **Address Class events** use actor `system:research` and the causes `assigned_from_<source>`.
10. **The FR-49 denylist is created here, minimal and fail-closed.** Story 2.5 extends it.
11. **The page.** It lives at `/leads/import` and is linked from the dashboard home. Story 2.4 adds a link from the Leads list.
12. **A row without a Contact records its origin only through the Lead's creation event** (cause `created_from_csv`, with no text). FR-51's Provenance Records are per Contact and address.
13. **An upload over the body limit can't keep what was typed.** The form shows "The file is over 5 MB" with the origin and default reset, because the body was discarded unread.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Happy path | BOM, headers `Organisation Name,WEBSITE,Country,Contact Name,Email`; rows `Smith Law,https://www.smith-law.co.uk/team,UK,Jane Doe,Jane@Smith-Law.co.uk` and `Smith Law LLP,smith-law.co.uk,GB,,` | Row 2 is imported: Lead `smith-law.co.uk`, `GB`, the default Segment. Its Contact has address `jane@smith-law.co.uk`, class `guessed`, one `contact` and one `address` record (`csv`, the origin, import time) and one class `events` row. Row 3 is merged. Summary: 1 imported, 1 merged, 0 rejected. | N/A |
| Form refused | an origin of `ab` or 201 characters; a 5 MiB + 1 byte file; 5,001 data rows; Latin-1 bytes; no `website` header; an unknown `default_segment` | the form error by its field; the `leads`, `contacts` and `provenance_records` counts are unchanged | `ImportRefused`, 200 |
| Rejected rows | a blank name; `not a url`; `https://www.linkedin.com/company/x`; `https://www.google.com/maps/place/x`; `uk.indeed.com/jobs`; `sites.google.com/view/x`; country `ZZ`; Segment `Nope`; a blank Segment with no default; an extra cell | each listed by row number with its fixed reason; no write for that row | N/A |
| Email fails syntax | `a,b@firm.com` | the Lead and the named Contact are imported without an email, and the row is listed with "the email fails the syntax check, so it was not imported" | N/A |
| Contact dedup | the same email twice; then the same name with no email; then the same name with another email | one Contact; the email is filled once; the third row is noted "the Contact already has a different email address, so this one was not stored" | N/A |
| Merge | Leads A (older) and B, each with Contacts; `merge(A, B)` | B's Contacts now have `lead_id` A, and their records are unchanged | N/A |
| Batch stop | an `add_contact` stub raising at row 250 | rows 2–201 are saved, and from row 202 on nothing is; `ImportStopped(at_row=202)`; the page shows the counts so far and the stop notice | rolled back |
| Machine | every (class, event) pair; `assign` for each source | its table row, or `IllegalTransition` | raised |

</frozen-after-approval>

## Code Map

- **Patterns from Story 2.2.**
  - `nextron/domain/leads.py`: `_check_name`, `REFUSED_NAME_CATEGORIES`, `InvalidLeadIdentity`; reuse the name rule.
  - `domain/pipeline_stage.py` and `domain/job.py`: the machine layout (`MappingProxyType`, `transition`, `CREATION`).
  - `domain/machines.py`: `Transition`, `IllegalTransition`.
  - `app/discovery/api.py`: `Discovery`, `UpsertedLead`, and the per-call Segment check at the `setup_queries.segments()` line.
  - `app/pipeline/api.py`: the `Pipeline(clock)` command and `eventlog.append` pattern.
  - `db/repos/discovery/leads.py`: the repository style.
  - `db/models/leads.py`: the CHECKs built from the enum.
  - `db/alembic/versions/0011_2_2_leads.py`: the migration header and `downgrade`.
- **Identity.** `nextron/domain/identity.py`: `registrable_domain` (steps 1–4 become `url_host`), `normalize_country_code`, `normalize_email`, `normalize_linkedin_url`, `normalize_name` and `IdentityError`. `tests/unit/test_identity.py:446` lists `"UK"` as refused: move it to an alias test.
- **Units of work.** `nextron/db/uow.py`: a use case may open several transactions in turn, and an exception from a command dooms the block, which then raises `UnitOfWorkError` at exit.
- **The event log.** `db/eventlog.py` `append`: the entity must be in `owners.TABLES`, and the actor `system:research` is valid.
- **Subscribers.**
  - `main.py` `build_subscribers` (about line 833) and its docstring (about line 55);
  - `tests/unit/test_serve.py:679`: assert `[Pipeline.reparent_lead, Research.reparent_lead]` in order, and the clock test.
- **Web.**
  - `web/app.py`: `create_app` signature and docstring, `page()`, the `settings_save` pattern (`with units.open():`);
  - `web/forms.py`: `MalformedForm` and its reason codes;
  - `web/csrf.py`: header tokens; non-urlencoded bodies present nothing;
  - `web/templating.py`: `plain`, `csrf_field`;
  - `web/templates/macros.html`: `untrusted_block`;
  - `settings_form.html`: the form idiom;
  - `home.html`.
  - `main.build_app` (about line 481): build `LeadImports(clock, Discovery(clock, Pipeline(clock)), Research(clock), EmailValidatorSyntax())` and pass it.
  - `tests/dashboard.py`: the shared test app builder; add `lead_imports`.
- **Adapters and ports.**
  - `ports/llm.py` and `adapters/crypto`: the port and offline-adapter style;
  - `tests/live_guard.py` `ADAPTER_PACKAGES`;
  - `pyproject.toml` contract c5 `source_modules`;
  - `tests/fakes/__init__.py` `PORT_FAKES`;
  - `tests/contracts/test_contract_coverage.py`, which keeps the contract lists complete.
- **Persistence contracts.**
  - `tests/contracts/test_persistence_contracts.py` (`HEAD` at 72, probes about 670);
  - `tests/unit/test_migrations.py:39`;
  - `tests/app/test_serve_process.py:263`;
  - `tests/contracts/test_source_tree.py` `FILES`;
  - `db/owners.py` and `db/pii_registry.py`;
  - `db/models/base.py`: `NAMING_CONVENTION`, `Ulid`, `UtcDateTime`, `ulid_pk`; check whether a `Date` column is allowed for `lia_date`.
- **Queries.** `app/queries/setup.py` (`segments()`); `app/queries/research.py` and `db/repos/research/__init__.py` are stubs reading "Empty until…", so rewrite those lines.
- **Tests to reuse.** `tests/app/test_leads.py` (the `World` fixture: `UnitOfWorkFactory(db_engine, main.build_subscribers(clock=...))`, seeding Segments); `tests/fakes/clock.py` `FrozenClock`; `tests/unit/test_settings_page.py` (a dashboard on a real DB).

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/domain/identity.py` -- `url_host`, the `UK` alias, docstring -- normaliser additions.
- [x] `nextron-engine/nextron/domain/fetch_policy.py`, `domain/leads.py` -- `is_denied`; `is_shared_platform` and its lists -- website refusals.
- [x] `nextron-engine/nextron/domain/contacts.py`, `domain/address_class.py` -- the sources, `ContactCandidate`, `InvalidContact` and the machine.
- [x] `nextron-engine/nextron/domain/lead_import.py` -- `check_origin`, `plan_import`, `ImportRefused`, the reason and note texts.
- [x] `nextron-engine/nextron/db/models/research.py`, `db/models/__init__.py`, `db/alembic/versions/0012_2_3_contacts_provenance.py`, `db/owners.py`, `db/pii_registry.py`, `db/repos/research/{__init__,contacts,provenance}.py`, `db/repos/setup/segments.py` -- the tables and repositories.
- [x] `nextron-engine/nextron/ports/email_syntax.py`, `adapters/email_syntax/__init__.py`, `pyproject.toml` (c5), `tests/live_guard.py`, `tests/fakes/{email_syntax,__init__}.py` -- the syntax port.
- [x] `nextron-engine/nextron/app/research/api.py`, `app/discovery/api.py`, `app/queries/{setup,research}.py`, `nextron/main.py` -- `Research`, `LeadImports`, the Segment-id check, the queries and the wiring.
- [x] `nextron-engine/nextron/web/forms.py`, `web/lead_import_page.py`, `web/app.py`, `web/templates/{lead_import,lead_import_form,home}.html`, `web/static/app.css` (only if a style is needed) -- the page.
- [x] Tests:
  - `tests/unit/test_identity.py`: the alias and `url_host`;
  - `tests/unit/test_fetch_policy.py`: the 2.5 AC hosts, `/maps` paths, near-misses such as `notlinkedin.com` and `indeedjobs.com`, and fail-closed;
  - `tests/unit/test_contacts_domain.py`, `tests/unit/test_address_class.py`;
  - `tests/unit/test_lead_import.py`: every planner row of the matrix, the header aliases, the limits and the row numbering;
  - `tests/unit/test_upload_form.py`: the parser, including the size drain, malformed bodies and repeated fields;
  - `tests/unit/test_email_syntax.py`: the real adapter on valid and invalid addresses;
  - `tests/app/test_lead_import.py`: real units on a temp DB, covering each app matrix row, the queries, the CHECKs, FKs and indexes as `IntegrityError`, and the merge;
  - `tests/unit/test_lead_import_page.py`: the GET, the POST success, refusal and stop, escaping (a cell `<script>` renders escaped), `no-store`, and CSRF required.
- [x] Contract and migration tests from the Code Map; the reparent contract (item Y); the README section.

**Acceptance Criteria:**
- Given the full suite, when `uv run pytest` and `uv run lint-imports` run through the suite lock, then everything passes and all 29 contracts are kept.
- Given `grep` over `nextron/`, when it looks for `email_validator` outside `adapters/email_syntax`, or for `idna` or `public_suffix` outside `domain/identity.py`, then nothing appears.
- Given the persistence contracts, when they run, then `contacts` and `provenance_records` are fully mapped and classified, the migration matches the models, and the reparent contract passes.
- Given the dashboard with a session, when Mac opens `/leads/import`, uploads a valid file with an origin and submits, then the summary shows imported, merged and rejected counts with every CSV text escaped.

## Implementation Notes

- **Identity.** `url_host` and `registrable_domain` share steps 1–4; `registrable_domain` reads the labels from `url_host`'s output, so its behaviour (and every 2.2 test) is unchanged. `COUNTRY_ALIASES` is a read-only `{"UK": "GB"}`. `url_host` does not validate a port, so `javascript:alert(1)//x.com` reads as host `javascript` (and `registrable_domain` refuses it, so the import rejects the row).
- **Denylist.** `is_denied` percent-decodes the path before the `/maps` check (`google.com/%6Daps/x` is denied): a superset of the spec's "lowercased path", fail-closed. A non-`str` raises `TypeError`.
- **Shared platforms.** `is_shared_platform` lives in `domain/leads.py` with the listed domains (38, then 59 after review pass 1); `leads.check_name` (formerly `_check_name`) is now public so the planner applies `LeadIdentity`'s name rule before building the identity.
- **Contacts.** `contacts.check_name` / `check_role` expose `ContactCandidate`'s text rules for the planner. A candidate with neither name nor email raises `InvalidContact("identity", ...)`.
- **Planner.** Each `RejectedRow`/`ImportRow` label is the collapsed name cell with `Cc`, `Cf`, `Zl` and `Zp` characters removed, cut to 100 (so a bidi override can't reorder the summary). File refusal texts are constants (`FILE_*`, `ORIGIN_*`, `DEFAULT_SEGMENT_UNKNOWN`). Python's `csv` limits a cell to 131,072 characters: a longer cell refuses the file as invalid CSV. A header cell is stripped again after separator collapsing (`_website_` matches).
- **Email syntax.** The error contract allows only `AdapterError` raises under `nextron/adapters`, so the non-`str` `TypeError` comes from the port helper `ports.email_syntax.checked_address`, which the adapter and the fake both call.
- **Research.** `add_contact` raises `ValueError` for any bad `detail`, a non-`str` included (the spec lists `detail` under `ValueError`, not `TypeError`). A match writes one `fill` (text fields, email and class together) with `updated_at` = now.
- **Import use case.** `ImportSummary.rejected` lists every rejected row of the plan, also after a stop (they import nothing either way); `imported`, `merged` and `noted` count only committed batches. `lead_import_refused` is logged at INFO, `lead_import_stopped` at WARNING with `at_row` and `error` (the exception's type name).
- **Web.** The import block is `<div id="lead-import">` (summary + form); the form targets it with `hx-swap="outerHTML"` so a second import replaces the previous summary. `hx-disabled-elt="find button"`. `submitted_import` needs `origin`, `default_segment` and the file `file` (`csrf_token` optional); a missing one is `wrong_fields`. `app.css` gains `.import-counts` and `.import-table`.
- **Tests.** `tests/dashboard.py` gains `email_syntax`, `lead_imports` and `upload(...)` (a browser-style multipart body). The reparent contract (`reparent_problems`) reads `Base.metadata` foreign keys to `leads.id` and the bound `reparent_lead` subscribers' `nextron.app.<owner>.api` classes.

**Review fixes (pass 1)**

- P1: `SHARED_PLATFORM_DOMAINS` gains 21 domains, each confirmed its own registrable domain by the existing test: legal directories (`lawsociety.org.uk`, `sra.org.uk`, `chambers.com`, `legal500.com`), review sites and registers (`trustpilot.com`, `checkatrade.com`, `yellowpages.com`, `bbb.org`, `company-information.service.gov.uk`, `opencorporates.com`) and URL shorteners (`bit.ly`, `goo.gl`, `g.co`, `share.google`, `tinyurl.com`, `t.co`, `ow.ly`, `rebrand.ly`, `cutt.ly`, `is.gd`, `buff.ly`); docstring, README and website tests updated.
- P2: `NOTE_BAD_NAME` now reads "the Contact name is over 200 characters, holds control characters or has no letters or digits, so it was not imported", with a `---` test.
- P3: `_records` streams the reader (a generator of non-blank records with their 1-based positions), and `plan_import` raises `FILE_TOO_MANY_ROWS` as soon as the 5,001st data row is read; the `csv.Error` row number is kept. Tests: a CSV error after row 5,001 is never reached, and 300,000 blank lines stay under 8 MB at peak.
- P4: a stopped import keeps the origin and default Segment in the form (`origin=submitted.origin`, `default_segment=submitted.default_segment or ""`); the stopped-page test asserts it.
- P5: the `/maps` check normalises the path: percent-decoded, lowercased, `;` parameters dropped per segment, repeated slashes collapsed and dot segments resolved (`posixpath.normpath`, leading `/` kept); tests for `//maps`, `/maps;x`, `goo.gl//maps`, `/./maps`, `/x/../maps` and `%2Fmaps`.
- P6: row check 1 rejects only a non-blank cell past the header's width, so a trailing comma or blank trailing cells import; tests for both, docstring and README updated.
- P7: the header is the first non-blank record, so blank lines before it work; row numbers stay record positions (test: header at row 4, rows 5 and 7).
- P8: `_segment_lookup` keys are `" ".join(text.split()).casefold()`, matching the whitespace-collapsed CSV cell.
- P9: an app import with Segment cell `LAW_PRIVATE_AI` lands in the Law Segment, and a unit test of `_segment_lookup` (a name that folds to another Segment's key) shows the key wins and inner whitespace collapses.
- P10: the batch-stop test notes row 3 (committed) and row 205 (the failed batch) and asserts the summary's `noted` holds only row 3.
- P11: raw writes against the migrated database accept every `AddressClass` on `contacts.address_class` and on an `address` record, every `ProvenanceSource` as `source_kind`, and every `ProvenanceSubject`.

## Spec Change Log

## Review Triage Log

**Pass 1 (2026-11-17).**
- **Sources:** BH = blind hunter (14 findings), EC = edge-case hunter (10 findings, one of them a low-confidence claim), VG = verification gap (3 gaps, no other findings).
- **Counts:** 27 raw findings. BH5 and EC1 each make two separate claims, so there are 29 claims, which make 25 entries after deduplication.
- **Outcomes:**
  - 11 entries patched (P1–P11): 8 code or text fixes and 3 test gaps;
  - 1 deferred (C, to Story 2.5);
  - 13 rejected, 2 of them as false.
- **No loopback.** No finding is an intent gap or a spec defect that needs re-derivation. Each fix stays inside the frozen rules, or refines one in its stated direction: P6 refuses more, and P7 and P8 apply the blank-record rule.
- **Rejected only because the fix would change the approved spec:**
  - D: on an email match, a different name, role or LinkedIn URL is dropped with no note. The frozen `AddedContact` has only `email_dropped`, and decision 4 says a match only fills empty fields.
  - V: a Contact re-listed by a second list, with nothing new, gets no Provenance Record naming that list. Decision 4 says a re-import writes nothing new.

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| A | BH1, EC1 (part) | The shared-platform list misses legal and review directories, Companies House and URL shorteners. `solicitors.lawsociety.org.uk/x` keys as `lawsociety.org.uk`, and the same happens for `sra.org.uk`, `chambers.com`, `company-information.service.gov.uk`, `bit.ly`, `goo.gl`, `g.co`, `share.google` and `tinyurl.com`. Unrelated firms then merge irreversibly, and their Contacts pool. | medium | Verified: each is neither denied nor shared. The list is in Design Notes, outside the frozen block. Adding entries is a direct extension of decision 3, and the existing test checks each entry's registrable domain. | P1 |
| B | BH2, EC1 (part) | No note warns when a row merges into a Lead with a different name. | low | True, but it is new summary surface, and it would flag legitimate merges: the matrix's own happy path merges `Smith Law LLP` into `Smith Law`. The root cause, a shared domain, is fixed by P1. | reject |
| C | BH3 | The FR-49 denylist misses UK job boards (`reed.co.uk`, `totaljobs.com`, `cv-library.co.uk`), and `g.page`, which leads to Maps, is caught only as a shared platform. | medium | Verified (`is_denied('reed.co.uk/jobs')` is False). FR-49 says "job sites such as Indeed". The frozen spec enumerates `DENIED_DOMAINS`, and decision 10 makes Story 2.5, which owns the denylist and its fetch contract, the one that extends it. At import, a `g.page` row is already refused (shared). | defer |
| D | BH4, EC9 | An email match silently drops a different name, role or LinkedIn URL. A shared `info@` inbox listed for two people keeps only the first. | medium | Verified (`research/api.py` fills only empty fields). Reporting it needs a new `AddedContact` field and note, which changes the frozen return type and decision 4. Listed for Mac. | reject |
| E | BH5 (part) | Placeholder names (`N/A`, `TBC`, `Unknown`) become Contacts. | low | True (`normalize_name('N/A')` is `n a`). A placeholder list is a new guard. Story 2.10's decision-maker matching and Mac's approval of every Draft stand between it and an email. | reject |
| F | BH5 (part), EC8 | A Contact name with no letters or digits (`-`, `?`) is dropped with the note "over 200 characters or holds control characters", which is the wrong reason. | low | Verified: the planner gives that note for `---`. A direct text correction. | P2 |
| G | BH6 | `lead_import_stopped` logs only the error's type, with no traceback. | low | True, but SQLAlchemy errors embed the statement's parameters (names, addresses), and the spec forbids logging any CSV cell or address (AD-17: logs hold IDs). | reject |
| H | BH7 | Ignored columns get no feedback; split first and last name columns and `domain`/`work email` headers aren't recognised; `title` may mean a salutation and `contact` a phone. | low | New import features beyond the approved header table (Design Notes). Each refusal or drop is visible in the summary. | reject |
| I | BH8 | A semicolon- or tab-separated file gets "The header has no organization name column." | low | True, but the message is accurate, and Excel's UK and US CSV exports use commas. Detecting other separators adds a branch. | reject |
| J | BH9 | Contacts are deduplicated on plain-text email, but erasure keeps a hash, so a re-import would recreate an erased person. | false | No erasure exists yet. Story 3.x gates `research.add_contact` on the suppression keys, and its AC (epics: "`research.add_contact` is blocked by the gate on the `dsr` keys and no Contact is re-created") is what stops a re-creation. | reject |
| K | BH10 | The multipart `csrf_token` field is never checked, so a wrong body token with the right header passes, and the non-HTMX fallback gets 403. | false | `csrf.py` defines a non-urlencoded body as presenting nothing, and the `X-CSRF-Token` header must match the session's token. Every dashboard form posts through HTMX by design, and a native post sends `Origin: null`, which the guard refuses. No forgery path exists. | reject |
| L | BH11 | The summary counts Leads, never Contacts. | low | The AC asks for imported, merged and rejected row counts. Contact counts are new surface. | reject |
| M | BH12 | The planner keeps every record, blank ones included, before checking the row limit. A 5 MiB file of blank lines peaks at hundreds of MB. | low | Verified by reading `_records`. The fix is a direct restructure: stream the records, skip blank ones, and stop at 5,001 counted rows. The row limit still refuses the file before any row is planned. | P3 |
| N | BH13 | No partial unique index enforces one Primary per Lead. | low | No writer sets a Primary until Story 2.10, which owns Primary selection and can add the index without a table rebuild. | reject |
| O | BH14 | A stopped import resets the origin and default Segment, though its notice says to import the same file again. | low | Verified (`web/app.py`'s `ImportStopped` branch). A direct fix: keep what was typed, as a refusal does. | P4 |
| P | EC2 | `is_denied` misses Maps paths written `//maps`, `/./maps` or `/maps;x`. | low | Verified for `google.com//maps/x`, `google.com/maps;x` and `goo.gl//maps/x`. Normalising the path (repeated slashes, `;` parameters, dot segments) is a direct, fail-closed correction. `www.maps.google.com` is left to Story 2.5 (C); at import, every `google` host is already refused as a shared platform. | P5 |
| Q | EC3 | A stored Segment that no longer validates makes `segments()` raise, so the import page answers 500. | low | This is the existing contract of `queries.setup.segments()` (Story 2.1): every Segment reader fails the same way, and `nextron seed sync` fixes the stored text. | reject |
| R | EC4 | A row with trailing blank cells (a trailing comma) is rejected as "more cells than the header row". | medium | Verified: `A,a.com,GB,` is rejected. The rule exists to catch misaligned data; an extra cell holding nothing carries none. A direct correction: reject only when an extra cell isn't blank. | P6 |
| S | EC5 | Blank lines before the header refuse the whole file with "no organization name column". | low | Verified. A blank record is skipped by rule; P3's streaming reader takes the first non-blank record as the header at no extra cost. | P7 |
| T | EC6 | A Segment name with inner double spaces never matches a whitespace-collapsed cell. | low | True (2.1's text rule allows inner runs). A one-line correction: collapse whitespace when building the lookup. | P8 |
| U | EC7 | Two stored Segment names that are equal after casefolding send rows silently to the first. | low | Names aren't unique (only keys are), but both shipped names differ and Mac controls the seed file. Dropping colliding names adds branches. | reject |
| V | EC10 (claim) | A Contact re-listed by a second list, with nothing new, gets no record naming that list's origin. | low | True by decision 4: a match that fills nothing writes nothing, so re-imports stay idempotent. Changing it changes the approved spec. Listed for Mac. | reject |
| W | VG1 | The CSV Segment lookup by key, and the rule that a key beats a name, are untested; replacing the key insertion with `pass` passes every test. | low | Pre-verified by mutation. Add an app test with `LAW_PRIVATE_AI` in the Segment cell, and a unit test of `_segment_lookup` where one name folds to another Segment's key. | P9 |
| X | VG2 | A stopped import's notes are tested only with no notes, so leaking a failed batch's notes passes. | low | Pre-verified by mutation. Give a row before the failed batch and a row inside it a bad email, and assert that only the earlier one is noted. | P10 |
| Y | VG3 | The migration's CHECK lists are tested for only some values, and the drift test can't see CHECKs. | low | Pre-verified by mutation. Parametrise raw writes over every `ProvenanceSource` and every `AddressClass`, on both tables. | P11 |

## Design Notes

**Headers** (after normalisation; required ones marked *):

| Field | Accepted headers |
|---|---|
| organization name* | `organization name`, `organisation name`, `organization`, `organisation`, `company`, `company name` |
| website* | `website`, `website url`, `web site`, `url` |
| country* | `country`, `country code` |
| Segment | `segment` |
| Contact name | `contact name`, `contact` |
| role | `role`, `contact role`, `title`, `job title` |
| email | `email`, `email address`, `contact email` |
| LinkedIn URL | `linkedin url`, `linkedin`, `contact linkedin url` |

**Reason texts** (fixed; never echo a cell):
- "the row has more cells than the header row";
- "missing organization name", "missing website", "missing country";
- "the organization name is over 200 characters or holds control characters";
- "the website can't be read as a web address";
- "a LinkedIn, Instagram, Maps or job-site URL is not a company website";
- "a page on a shared platform such as Google Sites or Facebook is not the organization's own website";
- "unknown country: use a two-letter code such as US or GB";
- "unknown Segment";
- "no Segment, and no default Segment was chosen".

**Note texts:**
- "the email fails the syntax check, so it was not imported";
- "the LinkedIn URL isn't a linkedin.com/in/ profile, so it was not imported";
- "the Contact name is over 200 characters or holds control characters, so it was not imported" (the same for role);
- "the Contact needs a name or a valid email, so no Contact was imported";
- "the Contact already has a different email address, so this one was not stored".

**`SHARED_PLATFORM_DOMAINS`:**
- **Site builders:** `business.site`, `company.site`, `godaddysites.com`, `squarespace.com`, `wordpress.com`, `weebly.com`, `jimdosite.com`, `wix.com`, `mailchimpsites.com`, `strikingly.com`, `mystrikingly.com`, `site123.me`, `ueniweb.com`, `webnode.com`, `hubspotpagebuilder.com`, `myportfolio.com`.
- **Profile and link pages:** `tumblr.com`, `substack.com`, `medium.com`, `linktr.ee`, `about.me`, `g.page`, `fb.com`, `x.com`, `twitter.com`, `youtube.com`, `tiktok.com`, `pinterest.com`, `threads.net`, `bsky.app`, `calendly.com`.
- **Directories:** `yelp.com`, `yell.com`, `avvo.com`, `justia.com`, `findlaw.com`, `lawyers.com`, `martindale.com`.

Sites under a public-suffix platform (such as `x.wixsite.com` or `x.blogspot.com`) already key per site and aren't listed.

**The import loop:**
```python
for start in range(0, len(plan.rows), BATCH_ROWS):
    batch = plan.rows[start:start + BATCH_ROWS]
    try:
        with uow.transaction():
            for row in batch:
                upserted = self._discovery.upsert_lead(row.identity, LeadSource.CSV)
                if row.contact is not None:
                    added = self._research.add_contact(upserted.lead_id, row.contact, ...)
    except Exception as exc:
        raise ImportStopped(summary_so_far, batch[0].number) from exc
    ...fold the batch's counts and notes into the summary only after its commit...
```

## Verification

**Commands:**
- `cd nextron-engine; uv run pytest -q` -- expected: all pass. Run it through the suite lock.
- `uv run lint-imports` -- expected: 29 contracts KEPT.
- `uv run python -c "from nextron.domain.identity import normalize_country_code as n; print(n('uk'))"` -- expected: `GB`.
