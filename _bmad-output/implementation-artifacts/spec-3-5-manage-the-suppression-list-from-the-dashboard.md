---
title: 'Story 3.5: Manage the Suppression List from the dashboard'
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: '5c992c64d5c94d963d937176fc56671be3a8e8d8'
review_loop_iteration: 0
story_key: '3-5-manage-the-suppression-list-from-the-dashboard'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-3-3-record-suppression-entries-through-one-writer.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The Suppression List (Story 3.3) can only be written from code, for a stored Contact. Mac has no way to honour a do-not-contact instruction that arrives outside the engine (an address, a whole firm, a list), to check whether a value is on the list, or to correct a reply rule that misread a "no". FR-50, FR-41, AD-13, AD-16, NFR-7, NFR-36, UX-DR3, UX-DR25.

**Approach:**
- `Compliance.suppress` gains two subjects, an address and a domain.
- `Compliance.reverse_optout` voids a `reply_rule` entry through the SuppressionEntry machine.
- `Compliance.import_values` suppresses a parsed list.
- A pure `domain.suppression_list` holds the panel's input rules and messages.
- `queries.compliance` reads the list.
- A Suppression List page at `/suppression`, linked from Settings, adds, imports, searches, filters and reverses. Every action is a CSRF-checked POST.

The acceptance criteria are Story 3.5 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**
- **`domain.contactability`** (stays pure; still under the no-expiry contract):
  - **`AddressSubject(address)`.** `address` must be a `str` equal to `normalize_email(address)`. Otherwise it raises `TypeError` or `ValueError`, and the message holds no value. The field uses `repr=False`.
  - **`DomainSubject(domain)`.** `domain` must equal `registrable_domain(domain)` and must not be free webmail (`is_free_webmail`). Otherwise it raises `ValueError`. So no caller can suppress a whole webmail domain.
  - **`SuppressionSubject`** = `ContactSubject | AddressSubject | DomainSubject`.
- **New pure `nextron/domain/suppression_list.py`** (stdlib + `nextron.domain`):
  - `SuppressionRefused(DomainError)` with `.message`, and the exact messages below.
  - `entry_key(text)`: the stripped text with an `@` is an address key (`normalize_email`); otherwise a domain key (`registrable_domain`, which takes a website or URL too). Any `IdentityError`, or empty text, raises `NOT_VALID`.
  - `entry_value(text) -> EntryValue(subject, key)`: `entry_key`, then a domain that is free webmail (raw host or registrable domain) raises `FREE_WEBMAIL`.
  - `parse_import(data) -> ImportPlan(values: ((line_no, EntryValue), ...), invalid_lines)`:
    - decoding is `utf-8-sig`, strict; a NUL is refused;
    - lines split on `\r\n`, `\r` and `\n`; a blank line is skipped;
    - the value is the stripped first CSV column (`csv.reader` on the line);
    - a refused value is invalid by line number only;
    - limits are 5 MiB and 10,000 non-blank lines.
  - `checked_reason(text)`: the reason, stripped, 1–500 characters with no `Cc`/`Cf` character.
- **`Compliance(clock)`** in `app/compliance/api.py`:
  - **`suppress`.**
    - `AddressSubject` takes scope `person` only, and `DomainSubject` scope `firm` only. Any other pairing raises `ValueError`.
    - **Address.** Its keys are the address key first, then `person_keys` of every Contact whose stored `email` equals it, through a new `queries.research.contacts_with_email`, each with its Lead's `web_domain`. Keys are deduplicated as before. It publishes one `SubjectSuppressed` per matched Contact (`suppression_entry_id` = the address key's entry). With no match it publishes once, with no `contact_id` or `lead_id`.
    - **Domain.** It writes the one domain key, and publishes once with `lead_id` = the Lead whose `web_domain` is the domain (a new `queries.discovery.lead_by_web_domain`), or `None`.
  - **`@command reverse_optout(entry_id, reason)`.** It checks in this order:
    1. a non-`str` raises `TypeError`;
    2. no entry gives `UNKNOWN_ENTRY`;
    3. a source other than `reply_rule` gives `ONLY_RULE_MATCHED`;
    4. a `voided` entry gives `ALREADY_REVERSED`;
    5. `checked_reason`.

    It then applies `transition(active, void)`, calls the new repository writer `void(entry_id, voided_at, void_reason)`, and appends `events` (`active → voided`, cause `operator_reversal`, actor `operator`, no `cause_ref`, no reason text). It publishes nothing and leaves the Pipeline Stage unchanged. It returns the count of other `active` `reply_rule` entries with the same non-null `cause_ref`.
  - **`@command import_values(values) -> ImportCounts(added, already)`.** For each value, it reads the value's own key (`active_for`): `already` if active, else `added`. Then it calls `suppress(subject, person|firm, import, None)` either way, so a known Contact's missing keys are still written.
- **`queries.compliance`:**
  - `entry_list(*, kind, source, status, page)`: 50 per page, newest first (`created_at` then `id`, descending), with `total`, `page` and `pages`, the page clamped as in 2.4;
  - `entries(ids)`;
  - `active_entry(kind, value_hash)`.
- **Contract.** `WRITERS` = `insert`, `void`. Every new repository function is classified. `domain/suppression_list.py` joins `GUARDED`. Web code never names the table.
- **Page** `nextron/web/suppression_page.py` with `suppression.html` and `suppression_panel.html` (`#suppression-panel`):
  - **Routes.** `GET /suppression`, and `POST /suppression/add`, `/import` (multipart), `/search` and `/reverse` (`entry_id` is a form field). All are `@personal_data(True)`, with `method="post"`, `hx-post` and `{{ csrf_field() }}`. Each POST answers 200 with the panel re-rendered at page 1, unfiltered, and a notice.
  - **Filters.** A GET form for kind, source and status, following 2.4's pattern: a bad parameter answers 400.
  - **Rows.** Kind, source, date (`YYYY-MM-DD`), status, the first 12 hex characters of the hash, and `domain_plain` for domain entries. Never an address, a name, a URL or a void reason.
  - **Reverse.** A form with a required reason, on `active` `reply_rule` rows only. There is no delete control.
- **Messages:**
  - `NOT_VALID` "Not a valid email address or domain";
  - `FREE_WEBMAIL` "Free-webmail domains can't be suppressed as a whole. Add the address instead.";
  - `ONLY_RULE_MATCHED` "Only rule-matched opt-outs can be reversed";
  - `ALREADY_REVERSED` "This opt-out has already been reversed";
  - `UNKNOWN_ENTRY` "That entry isn't on the list";
  - `REASON_REQUIRED` "Enter a reason for reversing this opt-out";
  - `REASON_INVALID` "The reason must be at most 500 characters of plain text";
  - `FILE_EMPTY` "The file has no addresses or domains";
  - `FILE_NOT_UTF8` "The file isn't UTF-8 text";
  - `FILE_TOO_LARGE` "The file is larger than 5 MB";
  - `FILE_TOO_MANY` "The file has more than 10,000 addresses and domains. Split it and import each part.";
  - search: "On the list (active since <date>, source <source>)" / "Not on the list";
  - add: "Added to the Suppression List" (with the written entries' rows) / "Already on the list (active since <date>, source <source>)".

**Never:**
- No migration, setting, job type, adapter, port, dependency or notification field.
- No delete, expiry or un-void path. No change to `SubjectSuppressed`, `Pipeline`, the triggers or the `0022` migration.
- No `assert_contactable` (Story 3.4), unsubscribe tokens (3.8) or erasure (3.13).
- No plain address, name, URL or reason in `events`, logs, URLs or entry rows.
- No file is written, and the upload's file name is never kept.
- No editing outside `nextron-engine/`, except this spec, `sprint-status.yaml` and `deferred-work.md`.

### Decisions made overnight (pending user review)

1. **What counts as a valid address.** `normalize_email` accepting it is enough; no email-validator check. Being permissive never under-suppresses, and a junk key matches nothing.
2. **How a value is read.** Text with an `@` is an address. Anything else is a domain, website or URL, normalised to its registrable domain.
3. **Subjects and scopes.** The address subject is person-scope and the domain subject is firm-scope. Nothing calls an address with firm scope yet, so that pairing is refused.
4. **An address belonging to a known Contact.** An address add, or an import line, writes every key of every Contact that has the address. The AC says this for the add; the import does the same, which is the protective reading.
5. **`SubjectSuppressed` for the new subjects** goes out once per matched Contact, otherwise once with no Contact. For a domain, it carries the Lead holding that web domain. This way 4.8's cascades see these entries.
6. **Manual adds and imports don't close Leads.** `operator` and `import` aren't opt-out sources, and the AC asks for no close.
7. **Import rules:**
   - blank lines are skipped;
   - a header line counts as invalid;
   - a free-webmail domain line counts as invalid;
   - a repeated line counts as "already suppressed";
   - "added" and "already" go by the line's own key;
   - a file over a limit is refused whole, and nothing is written;
   - values are applied 500 per unit of work, so the write lock is never held near the 5 s `busy_timeout`. A crash part-way leaves earlier chunks, which is safe: a re-import gives "already".
   - Invalid line numbers are shown up to 100, then "and N more".
8. **Search is a POST,** so the value never reaches a URL, the history or the access log. It matches the exact key only. A voided entry, or a free-webmail domain, is "Not on the list".
9. **Typed values are never echoed.** Every input renders empty after a POST.
10. **Reverse voids one entry.** The notice counts the other active `reply_rule` entries from the same opt-out (same `cause_ref`), so Mac can reverse those too. The reason is stored only in `void_reason` and is never shown.
11. **Where the page is linked.** The nav is fixed by UX-DR1, so the link goes on Settings, beside Facts and Inboxes, as "Suppression List page".
12. **No index on `contacts.email`.** The lookup scans the table, which is acceptable at Release 1 volumes. 7.x may add an index.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Add address, unknown | `Jane@Firm.co.uk` | 1 `address` row, `operator`, `events` row; panel shows it by hash prefix | N/A |
| Add address, known Contact | email of a Contact with name, Lead web domain, LinkedIn | address, person and profile rows; Lead stage unchanged | N/A |
| Add again | same address | no row; "Already on the list (…)" | N/A |
| Add domain | `https://www.Firm.co.uk/x` | 1 `domain` row, `domain_plain` `firm.co.uk` | N/A |
| Free webmail | `gmail.com`, `mail.yahoo.co.uk` | `FREE_WEBMAIL`; nothing written | refused |
| Not valid | `jane@`, `not a domain`, `` | `NOT_VALID`; nothing written | refused |
| Import | lines: valid address, dup, domain, `gmail.com`, junk, blank, CSV `"a@b.com",x` | added/already/invalid counts; invalid by line no. | N/A |
| Import too big | 10,001 values; >5 MB; not UTF-8; NUL; empty | file message; nothing written | refused |
| Reverse | active `reply_rule`, reason `misread` | `voided`, `voided_at`, `void_reason`; `operator_reversal` event; stage stays `lost` | N/A |
| Reverse refused | other source / voided / unknown id / blank or 501-char reason | message; nothing changes | refused |
| Search | active / voided / absent value | "On the list (active since …, source …)" / "Not on the list" | N/A |
| Bad filter | `?kind=x`, repeated or unknown parameter | 400 malformed form | refused |
| CSRF/Origin/Sec-Fetch-Site | any POST without them | 403; nothing written | refused |

</frozen-after-approval>

## Code Map

- `nextron/app/compliance/api.py`: `Compliance.suppress`, `_check_arguments`, `_unique`, `ACTORS`, `ENTITY`. Extend; keep 3.3 behaviour for `ContactSubject` byte-for-byte.
- `nextron/domain/contactability.py`: `ContactSubject`, `suppression_key`, `person_keys`, `_firm_domain` (the webmail test to mirror). `nextron/domain/suppression_entry.py`: `transition`, `EntryEvent.VOID`, `VOIDABLE_SOURCES`, `OPT_OUT_SOURCES`. Reuse.
- `nextron/domain/identity.py` `normalize_email`, `registrable_domain`, `IdentityError`; `nextron/domain/markets.py:332` `is_free_webmail`; `nextron/domain/errors.py` `DomainError`.
- `nextron/db/repos/compliance/suppression_entries.py`: add readers `listed`, `count_listed`, `by_ids`, `count_active_for_cause` and the writer `void` (`UPDATE … WHERE id AND status='active'`). Update its docstring and `db/repos/compliance/__init__.py`.
- An `entry_id` that isn't a ULID is an unknown entry (`domain.ids.is_ulid` before any read; the `Ulid` column's bind would raise), as 3.3's `Pipeline.close` does.
- `nextron/app/queries/compliance.py`: an empty stub. Fill it (import rule: domain + `db.repos.compliance`). Mirror `queries/discovery.py:48-110` (`LEADS_PER_PAGE`, `LeadListPage`, `lead_list` clamping).
- `nextron/app/queries/research.py` + `db/repos/research/contacts.py`: add `with_email(email)` → `contacts_with_email`. `queries/discovery.py`: add `lead_by_web_domain` over `leads_repo.by_web_domain`.
- **Web**, following the patterns the Story 2.4 and 4.7 pages use:
  - `nextron/web/app.py`:
    - add `compliance: Compliance` to `create_app` (signature ~362, docstring 3-123);
    - routes go inline, after `/inboxes` (~949), following the `inbox_register` refusal flow (954-976);
    - the upload follows `lead_import` (910-934, `Depends(upload_form)`, `form.too_large`);
    - the GET follows `pipeline_page` (978-990).
  - `nextron/web/leads_page.py`: `_single_values`, `list_filters`, `day`, `pager`, `page_href`; `inboxes_page.py`: `submitted_inbox`, `Notice`.
  - `nextron/web/forms.py`: `UploadForm`, `MalformedForm`; `csrf.py` `CSRF_FIELD`.
  - Templates: `inboxes_panel.html`, `leads.html` (the GET form and pager), `lead_import_form.html` (the multipart form), `settings.html:6-9` (the links).
- `nextron/main.py` `build_app` (~768, `create_app(` ~883) and `tests/dashboard.py` (fields ~261-280, `__post_init__`): pass `Compliance(clock)`.
- `tests/contracts/test_suppression_contracts.py`:
  - `READERS`/`WRITERS` (56-57), `GUARDED` (72), the module docstring;
  - the permanence check bans comparisons with `created_at`/`voided_at` and date strings in guarded modules, but ordering is fine.
- `tests/contracts/test_web_contracts.py`: the ordered route list (~147-182), the GET/POST lists (~287-320, ~478-510), the template list (~597-622). `tests/contracts/test_source_tree.py` `FILES`.
- Tests to copy:
  - `tests/unit/test_inboxes_page.py` (`dashboard`, `session`, `get`, `post`, the `Panel` parser, the CSRF 403 test, the settings-link and not-in-nav tests);
  - `tests/unit/test_lead_import_page.py:143` (upload `post`, `MAX_UPLOAD_BODY`);
  - `tests/unit/test_leads_page.py:520` (bad query);
  - `tests/app/test_suppression.py:195` (units with `main.build_subscribers`) and `seeded` (~725);
  - `tests/unit/test_dashboard.py` nav pins.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/contactability.py`: add `AddressSubject`, `DomainSubject` and `SuppressionSubject`; update the docstring.
- [x] `nextron/domain/suppression_list.py`: the new pure module (rules, limits, messages, `SuppressionRefused`).
- [x] `nextron/db/repos/research/contacts.py`, `app/queries/research.py`, `app/queries/discovery.py`: the two new reads.
- [x] `nextron/db/repos/compliance/suppression_entries.py`, `app/queries/compliance.py`: the readers, `void` and the queries.
- [x] `nextron/app/compliance/api.py`: the subjects in `suppress`, plus `reverse_optout`, `import_values` and `ImportCounts`; update the docstrings.
- [x] `nextron/web/suppression_page.py`, `templates/suppression.html`, `templates/suppression_panel.html`, `web/app.py`, `templates/settings.html`, `main.py`, `tests/dashboard.py`: the page.
- [x] `tests/unit/test_suppression_list.py`: every parse, limit and message.
- [x] `tests/unit/test_contactability.py`: the subjects' checks.
- [x] `tests/app/test_suppression.py` (or a new `test_suppression_panel_commands.py`): every matrix row on real units:
  - the Contact keys;
  - publishes;
  - `reverse_optout` refusals change nothing, including the `events` count;
  - the stage is unchanged;
  - no personal data in `events`.
- [x] `tests/unit/test_suppression_page.py`: the routes, rows, notices and no echo:
  - no address or reason in any response;
  - no delete control;
  - CSRF, Origin and Sec-Fetch-Site 403s with nothing written;
  - `no-store` on every response;
  - the Settings link, not in the nav;
  - a bad filter answers 400;
  - an oversize upload.
- [x] Contract and pin updates: `test_suppression_contracts.py` (new probes for `void` outside the API), `test_web_contracts.py`, `test_source_tree.py`.
- [x] `nextron-engine/README.md`: a Story 3.5 section.

**Acceptance Criteria:**
- Given the code base, when `tests/contracts` and `lint-imports` run, then every contract holds, and a probe using `void` outside `app/compliance/api.py` fails the writer check.
- Given the engine running `serve`, when Mac opens Settings, then a link leads to `/suppression`, and the nav still shows exactly the six UX-DR1 sections.

## Implementation Notes

**Files.**
- Domain:
  - `nextron/domain/contactability.py`: `AddressSubject(address)` (field `repr=False`), `DomainSubject(domain)` and `SuppressionSubject` (a `ContactSubject | AddressSubject | DomainSubject` union, usable with `isinstance`). A non-`str` raises `TypeError` for both subjects; anything else they refuse raises `ValueError` with a fixed message. `DomainSubject` also refuses a shared platform (`leads.is_shared_platform`, review patch H).
  - New `nextron/domain/suppression_list.py`: `SuppressionRefused` (code `suppression_refused`), `EntryValue`, `ImportPlan`, `entry_key`, `entry_value`, `search_key`, `parse_import`, `checked_reason`, the limits (`MAX_FILE_BYTES`, `MAX_VALUES`, `MAX_REASON_LENGTH`, `INVALID_LINES_SHOWN`), `LOCAL_PART_SPECIALS`, every message constant (`SHARED_PLATFORM` included), and the message functions `on_the_list`, `already_on_the_list`, `reversed_message`, `import_message` and `invalid_lines_message`.
- Repository `nextron/db/repos/compliance/suppression_entries.py`:
  - `ListedEntry` (no `cause_ref`, `voided_at` or `void_reason`);
  - readers `listed`, `count_listed`, `by_ids` and `active_ids_for_cause(source=, cause_ref=) -> tuple[str, ...]` (the `active` ids, by `created_at`, then `id`; review patch D);
  - writer `void(entry_id, voided_at, void_reason) -> bool`, which runs `UPDATE ... WHERE id = ? AND status = 'active'`.
- Reads:
  - `contacts_repo.with_email` and `queries.research.contacts_with_email`;
  - `queries.discovery.lead_by_web_domain`, over `leads_repo.by_web_domain`;
  - `queries.compliance`: `ENTRIES_PER_PAGE`, `EntryListPage`, `entry_list`, `entries(ids)` and `active_entry(kind, value_hash)`.
- `nextron/app/compliance/api.py`:
  - `suppress` takes the three subjects and checks the subject/scope pairing.
  - A private `_write` holds the 3.3 write loop and also returns every key's entry id in key order.
  - New: `reverse_optout`, `import_values`, `ImportCounts`, `AddedValue`, `ImportOutcome`, `IMPORT_BATCH` (500) and three use cases (below).
- Web:
  - `nextron/web/suppression_page.py`, with `templates/suppression.html` and `templates/suppression_panel.html`.
  - `web/app.py`: the five routes after `/inboxes`, the helpers `suppression_answer` and `suppression_refused`, and `create_app(..., compliance=...)`.
  - `templates/settings.html`: the "Suppression List page" link.
  - `static/app.css`: a `.suppression-*` block appended. The file's CRLF first 293 lines are untouched.
- Wiring: `main.build_app` passes `Compliance(clock)`, and `tests/dashboard.py` builds `compliance`.

**Choices within the spec.**
- **Three `@use_case` entry points on `Compliance`.** The web layer can't open a transaction or call a command, so the panel calls:
  - `add_value(text) -> AddedValue(written, since, source)`: `entry_value`, then one transaction running `suppress(..., operator, None)`. The same transaction reads the typed value's own `active` entry for the "Already on the list (...)" answer.
  - `import_list(data) -> ImportOutcome(added, already, invalid_lines)`: `parse_import`, then one transaction per 500 values running `import_values`. This is decision 7's "500 per unit of work".
  - `reverse_entry(entry_id, reason) -> tuple[str, ...]`: one transaction running `reverse_optout`, returning its ids.

  Refusals raise `SuppressionRefused` before or inside the transaction, so nothing is written.
- **The add's notice.** It is "Added to the Suppression List" whenever `written` is non-empty, with those rows read through `queries.compliance.entries`. Otherwise it is "Already on the list (...)" from the own key's entry. So when an address is already listed but a Contact holding it now has new keys, the answer is "Added" and shows the new person and profile rows.
- **The address subject.** The address key always comes first, so the published `suppression_entry_id` is `ids[0]` from `_write`. A Contact whose Lead can't be read raises `LookupError`, as in 3.3. The `ContactSubject` path keeps 3.3's `(written + existing)[0]` and its early empty return.
- **`reverse_optout`:**
  - It reads the entry only when `entry_id` is a ULID.
  - It compares `status` with `EntryStatus.VOIDED`.
  - If `void` changes no row it raises `ALREADY_REVERSED`. This can't happen inside the same `BEGIN IMMEDIATE` transaction; it is defence in depth.
  - It returns the ids of the remaining `active` `reply_rule` entries with the same `cause_ref`, read after the void, so the entry itself is never among them. An entry with no `cause_ref` returns `()`. The panel's notice is `reversed_message(len(ids))`.
- **`import_values`** checks every value is an `EntryValue` (`TypeError`) before writing anything.
- **`parse_import`:**
  - A NUL gives `FILE_NOT_UTF8`, the spec's file message for "not text".
  - An empty file, or one with only blank lines, gives `FILE_EMPTY`.
  - The size limit is checked before decoding, and the 10,000 limit counts non-blank lines before the values are parsed; an invalid first line (a header) allows one more (review patch E).
  - A line `csv` can't read (`csv.Error`, such as a field over csv's 131,072-character limit) is invalid.
- **`checked_reason`** also refuses `Cs` (a lone surrogate), besides `Cc` and `Cf`. SQLite can't store a lone surrogate as UTF-8, and a strict UTF-8 form can't carry one anyway. Review patch B adds `Co`, `Cn`, `Zl` and `Zp`.
- **`search_key(text)`** is a small extra pure helper. It returns `entry_key(text)`, or `None` for a free-webmail domain or (review patch H) a shared platform, so search answers "Not on the list" for one without matching on a message text (decision 8).
- **No logging** in the new commands and use cases, following 3.3's decision 11.
- **The page's display:**
  - Rows show the raw codes for kind, source and status, matching the "source <source>" messages.
  - Every POST re-reads the list at page 1 with no filter.
  - Each Reverse form's reason input is `required maxlength="500"`, and its id is `reason-<place>-<entry id>` (`featured` or `list`), so an entry shown in both tables never repeats an id.
- **Security tests.** A multipart body presents no CSRF token (`require_csrf` reads only the header for it), so the import has no "wrong body token" case.

**Tests.**
- New:
  - `tests/unit/test_suppression_list.py` (101 after the review patches): every message word for word, typed values, webmail, every parse rule and limit, and the reason.
  - `tests/app/test_suppression_panel_commands.py` (59 after the review patches): every matrix row on real units with `main.build_subscribers`, plus publishes, stage unchanged, no personal data or reason in `events`, refused reversals changing nothing (the `events` count included), import batches with a failure part-way, the reads, the `void` writer and the trigger. It imports `World`, `jane` and the constants from `tests/app/test_suppression.py`, and defines its own `world` and `published` fixtures.
  - `tests/unit/test_suppression_page.py` (99 after the review patches): the routes, rows, notices and no echo; no address, file name or reason in any answer; no delete control; CSRF, Origin, `null` Origin and Sec-Fetch-Site 403s with nothing written; `no-store`; the Settings link and not in the nav; bad filters (400); malformed forms (400); the oversize upload; and `build_app` wiring.
- Changed:
  - `tests/unit/test_contactability.py`: the subjects' checks.
  - `tests/contracts/test_suppression_contracts.py`: `READERS` and `WRITERS`, `GUARDED` gains `domain/suppression_list.py`, three `void` writer probes outside the API, the classification probes, two new permanence probes, and the docstring.
  - `test_web_contracts.py`: the route list, the GET and POST lists, and the template list.
  - `test_source_tree.py`: `FILES`.

**Verification (implementer).**
- Targeted: 1,092 passed. That covers the spec's Verification files plus `test_suppression_panel_commands.py`, `test_settings_page.py`, `test_campaigns_panel.py`, `test_environment_isolation.py`, `test_telegram_secrecy.py`, `test_credentials_page.py`, `test_doctor.py`, `test_google_access.py`, `test_health.py`, `test_heartbeat_writer.py`, `test_notifications_area.py`, `test_quotas_page.py`, `test_lead_import_page.py`, `test_pipeline_close.py` and `test_serve.py`.
- `uv run pytest -q tests/contracts`: 1,249 passed, 1 skipped (`ai_eval`).
- `uv run lint-imports`: 29 kept, 0 broken.
- The full suite was not run (§4a).

**Review patches (pass 1).**
- **A.** `_normalised` refuses with `NOT_VALID` an address whose normalised local part holds an RFC 5322 special legal only in a quoted string (`LOCAL_PART_SPECIALS`: `( ) < > [ ] : ; , \ "`). So `mailto:jane@firm.co.uk`, `email:jane@firm.co.uk`, `https://medium.com/@jane.doe` and `<jane@firm.co.uk>` are refused through `entry_value` and `search_key`, and an import line holding one is invalid. The test CSV-quotes the line, so the first column is exactly the value.
- **B.** `REFUSED_CATEGORIES` gains `Co`, `Cn`, `Zl` and `Zp`. U+2028, U+2029, U+E000 and U+FFFE give `REASON_INVALID`.
- **C.** `_suppress_domain` publishes `queries.discovery.survivor_of(lead.id)`'s id, or the found Lead's when that returns `None`. Tests cover a merged loser holding the domain (the survivor is published) and a broken chain (`survivor_of` patched to `None`: the loser is published).
- **D.**
  - The reader `count_active_for_cause` became `active_ids_for_cause(*, source, cause_ref) -> tuple[str, ...]`, reclassified in `READERS` and its probes. `reverse_optout` and `reverse_entry` return those ids (`()` with no `cause_ref`).
  - The panel's one table above the forms (`suppression-featured`, the old "Entries added" table) now takes a caption and a Reverse column, through the macros `reverse_cell` and `headings`. `panel_context(..., featured=, caption=)` and `suppression_answer(..., featured=, caption=)` replace `added=`.
  - An add shows `ADDED_CAPTION` "Entries added"; a search that finds an `active` entry shows its row under `MATCHING_CAPTION` "Matching entry", with Reverse when reversible; a reversal keeps `reversed_message(len(ids))` and shows those entries under `SAME_OPT_OUT_CAPTION` "Still on the list from the same opt-out", each with its Reverse form.
  - Rows still never show an address, a URL or a reason.
- **E.** `parse_import` allows `MAX_VALUES + 1` non-blank lines when the first non-blank line is invalid (a header), else `MAX_VALUES`. Tests, directly on `parse_import`: a header plus 10,000 values is accepted (1 invalid); a header plus 10,001 is refused; no header plus 10,001 is refused; an invalid line that isn't first earns nothing. The old "10,001 lines" test had a junk first line, so it now uses valid values.
- **F.** Each Reverse form's reason input has `aria-label="Reason for reversing the <kind> entry <hash prefix>"` and its button `aria-label="Reverse the <kind> entry <hash prefix>"`. A page test checks both and that every `id` in the panel is unique.
- **G.** A page test posts a multipart import with no session cookie: 401, nothing written.
- **H.** `entry_value` refuses a registrable domain that `leads.is_shared_platform` matches with `SHARED_PLATFORM` ("Shared platforms such as facebook.com or sites.google.com can't be suppressed as a whole. Add the firm's own domain or the address instead."). `search_key` returns `None` for one, an import line holding one is invalid, and `DomainSubject` raises `ValueError` ("DomainSubject.domain must not be a shared platform"). The add form's hint names shared platforms. Tests run through the domain, the subject, the command (`SHARED_PLATFORM`, nothing written) and the page (the alert, nothing written; a search answers "Not on the list").
- **I.** The reversal test also opts out a second Contact with another ULID `cause_ref`, and adds an active `one_click` entry with the same `CAUSE_REF` on another key. The returned ids stay exactly the first opt-out's remaining entries (sorted by id: they share a `created_at`), and the others stay `active`.
- **Line lengths.** Every flagged line is wrapped. Three invisible characters in the reason tests (U+200B, U+202E, U+00AD), written literally by mistake, are now `\u` escapes.
- **Docstrings and README** follow A to I: `suppression_list`, `contactability`, the compliance API, the repository, `suppression_page`, `web/app.py`, the panel template's comment, the contract test's docstring and the README's Suppression List section.
- **Re-verified** (only the files covering the edits): `test_suppression_list.py`, `test_contactability.py`, `test_suppression_panel_commands.py`, `test_suppression_page.py`, `tests/app/test_suppression.py`, `test_suppression_contracts.py` and `test_web_contracts.py`: 702 passed. `lint-imports` keeps 29 contracts. No line in the touched files passes 100 characters.

**Final verification (orchestrator).**
- Targeted run before review: the spec's Verification files plus `test_suppression_panel_commands.py` and `tests/contracts`: 1,870 passed, 1 skipped.
- Full suite through `suite_lock.py` (`track-B`) on the final tree, after the review patches: 11,292 passed, 3 skipped (`ai_eval` ×2, and a symlink test this account can't run).
- `lint-imports`: 29 kept, 0 broken.
- No migration, setting, job type, adapter, port or dependency was added.

## Spec Change Log

## Review Triage Log

Pass 1 (review loop 0). Three layers: blind hunter (BH, 13 findings), edge-case hunter (EC, 6), verification gap (VG, 1). That is **20 raw** findings, **16 after grouping**: 9 patched, 0 deferred, 7 rejected.

| # | Layer | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| 1 | BH | `mailto:jane@firm.co.uk`, `email:jane@…` or `https://medium.com/@jane.doe` is accepted as an address, so the panel says "Added" while the real address stays unsuppressed. | medium | `normalize_email` checks only whitespace, one `@`, lengths and the domain labels, so `entry_value("mailto:jane@firm.co.uk")` gives that literal `AddressSubject`. Decision 1's premise ("permissive never under-suppresses") fails here. No Contact can hold such an address: email-validator, without quoted local parts, refuses every RFC 5322 special. | patch (A) |
| 2 | BH | `checked_reason` lets through U+2028/U+2029 and private-use or noncharacters, despite "no line break". | low | `REFUSED_CATEGORIES` is `Cc`, `Cf`, `Cs`; `Zl`/`Zp` are line and paragraph separators. A direct correction of a documented rule. | patch (B) |
| 3 | BH | A domain suppression can publish a merged (loser) Lead. | low | `mark_merged` clears only the clashing keys, so a loser keeps its own `web_domain`, and `lead_by_web_domain` returns it without following `merged_into`. No subscriber acts on an `operator`/`import` domain today, but `suppress` accepts a `DomainSubject` with an opt-out source, and 4.8's cascades will read `lead_id`. One-line fix through `survivor_of`. | patch (C) |
| 4 | BH | Import batches scan `contacts` once per address inside the write transaction, so a big table could hold the lock for seconds. | low | Real only at large contact counts: decision 12 accepted the scan at Release 1 volumes, and 7.x may add the index. The fix is an index (a migration, which the spec forbids) or a per-batch prefetch that restructures `suppress`. | reject (decision 12; flag for 7.x) |
| 5 | BH | A failed import shows nothing, though earlier batches were committed. | false | `base.html`'s `htmx-config` `responseHandling` swaps every 4xx/5xx body as text into `#request-error`, so Mac sees the error. Decision 7 accepts committed batches: a re-import counts them "already". | reject |
| 6 | BH | The Reverse notice counts "other entries from the same opt-out" that Mac can't identify, since rows show only hash prefixes, so he could reverse another person's genuine opt-out. | medium | True: no row shows its opt-out. Searching an address answers only a sentence, with no row and no Reverse, so even the first entry can't be found from its address. Same root cause: the panel never ties a typed value or an opt-out to its rows. The fix replaces a reader and reuses the existing row table. | patch (D) |
| 7 | BH | Search says "Not on the list" for an address whose domain, person or profile key is active. | low | Decision 8 fixes exact-key matching, and the AC fixes the wording. The gate (3.4) still blocks. The fix would change the approved spec. | reject (spec change; flag for Mac) |
| 8 | BH | A header line counts toward the 10,000 limit, so header + 10,000 addresses is refused. | low | The AC accepts "up to 10,000 addresses and domains", and a header is neither. The fix allows one extra line when the first non-blank line is invalid. | patch (E) |
| 9 | BH | A domain suppression publishes only for the Lead whose website matches, not for Contacts mailing from that domain on other Leads. | low | Decision 5 fixes the publish set. The send and approval gates block the domain anyway. The fix would change the approved spec. | reject (spec change) |
| 10 | BH | Every Reverse form's label and button read the same to assistive technology. | low | Each row says "Reason" / "Reverse" with nothing naming the row. A direct correction: `aria-label`s naming the kind and hash prefix. | patch (F) |
| 11 | BH | After a POST the address bar keeps the old filters while the panel shows page 1 unfiltered. | low | Cosmetic. A refresh shows a valid filtered view. The fix adds an `HX-Replace-Url` path no other page uses. | reject |
| 12 | BH | No test covers an import POST without a session. | low | `test_a_post_needs_a_session` loops over add, search and reverse only. A test-only addition. | patch (G) |
| 13 | BH | `test_suppression_panel_commands.py` imports another test module's helpers. | low | A refactor there fails loudly at collection, not silently. Moving the helpers is a refactor for no named defect. | reject |
| 14 | EC | Same as 1 (`mailto:` and URL prefixes). | medium | Same as 1. | patch (A) |
| 15 | EC | A firm's page on a shared platform (`facebook.com/firm`, `sites.google.com/view/firm`, a Law Society listing) permanently suppresses the whole platform's domain (`facebook.com`, `google.com`, `lawsociety.org.uk`), never the firm's own. | medium | `registrable_domain` reduces it to the platform's domain, and `entry_value` checks only free webmail. An `operator`/`import` entry can never be voided, so one paste blocks every Contact at that domain forever. `domain.leads.is_shared_platform` already lists these (2.3 refuses them as Lead websites). A whole multi-tenant domain is the same hazard the AC refuses for free webmail. | patch (H) |
| 16 | EC | Same as 2 (U+2028/U+2029). | low | Same as 2. | patch (B) |
| 17 | EC | Same as 3 (merged Lead). | low | Same as 3. | patch (C) |
| 18 | EC | Same as 4 (scan under the lock). | low | Same as 4. | reject |
| 19 | EC | The Intent says every action is a CSRF-checked POST, but filtering and paging are GETs. | false | Filtering and paging change nothing. UX-DR3 and the AC require POSTs for state-changing actions only, and 2.4's GET filter form is the pattern. | reject |
| 20 | VG | No test pins `count_active_for_cause`'s `cause_ref` and `source` filters: mutants dropping either pass. | low | The reviewer ran both mutants: 38 passed. Both tests hold only the one opt-out's entries. A test-only fix. | patch (I) |

## Verification

**Commands** (from `nextron-engine/`):
- `uv run pytest -q tests/unit/test_suppression_list.py tests/unit/test_contactability.py tests/app/test_suppression.py tests/unit/test_suppression_page.py tests/unit/test_dashboard.py tests/unit/test_inboxes_page.py tests/unit/test_leads_page.py tests/app/test_facts_page.py` -- expected: all pass.
- `uv run pytest -q tests/contracts` and `uv run lint-imports` -- expected: all pass, every contract kept.
- The full suite through `suite_lock.py` (`track-B`), by the orchestrator only -- expected: exit 0.
