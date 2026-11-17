---
title: 'Story 3.4: Gate every outreach path with assert_contactable'
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: 'ea098023b1cc57bf0469afe8c9bab760b9469253'
review_loop_iteration: 0
story_key: '3-4-gate-every-outreach-path-with-assert_contactable'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Story 3.3 records suppression entries, but nothing reads them. Today a CSV import can create a Lead on a domain that said "remove us", and a Contact whose address, name or LinkedIn profile opted out. AD-16 needs one oracle that every Lead, Contact, Enrollment, Draft, Task and send path calls before it writes, and a contract test that forces later stories to call it.

Requirements: FR-50, FR-15, FR-63, AD-1, AD-16, NFR-36.

**Approach:**
- A pure core in `domain.contactability`: `check(...)`, `Allowed`, `Blocked`, `BlockReason`, `Channel`, `Purpose`, the facts and subject types and `lookup_keys`.
- `compliance.assert_contactable(subject, channel, purpose)` reads the facts and the active keys, through a new `queries.compliance` reader, and returns the core's answer.
- `discovery.upsert_lead` (before a Lead insert) and `research.add_contact` (before any write) call it. The CSV import reports what was blocked.
- `tests/contracts/test_ad16_callers.py` has a manifest of every AD-16 caller, scenarios with a recording fake, a call-site check and an AST insert scan.

The acceptance criteria are Story 3.4 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**
- **`nextron/domain/contactability.py`** additions (stdlib and `nextron.domain` only; frozen, slotted dataclasses). The module stays under 3.3's permanence contract: no comparison with `created_at` or `voided_at`, no `timedelta`, no SQL date text, and no name with a stem of delete, remove, purge, expire, ttl, prune, evict, truncate or drop.
  - **Enums:** `Channel` (`email`, `linkedin`, `whatsapp`, `instagram`, `phone`); `Purpose` (`cold`, `follow_up`, `reply`, `referral`, `rebook`, `recontact`, `task`, `call`, `discovery`); `BlockReason`, the AC's 7 reasons in the AC's order.
  - `channel_of(value)` and `purpose_of(value)` accept a member or its `str` value. A non-`str` raises `TypeError`; an unknown text raises `ValueError`.
  - **Facts:** `LeadFacts(web_domain, mail_domains=(), stage=None)`, where `stage` is a `PipelineStage` or `None` for a Lead not stored yet. `ContactFacts(lead, email=None, name=None, linkedin_url=None)`. A wrong type raises `TypeError`.
  - **Subjects** for the app gate:
    - `ContactSubject(contact_id)`, the existing type, for a stored Contact;
    - `NewContactSubject(lead_id, email=None, name=None, linkedin_url=None)`, for a Contact about to be stored on a stored Lead (a non-ULID `lead_id` raises `ValueError`);
    - `NewLeadSubject(web_domain, mail_domains=())`, for a Lead about to be created.

    Personal fields use `field(repr=False)`.
  - **Checked keys**, each with the reason it gives, in this order:
    - the address key → `address_suppressed`;
    - the address's registrable domain → `address_domain_suppressed`;
    - the Lead's web domain, then each mail domain → `lead_domain_suppressed`;
    - the person key (name with the Lead's web domain) → `person_suppressed`;
    - the profile key → `profile_suppressed`.

    A free-webmail domain is never a key, and a value that can't be normalised gives no key. Reuse 3.3's `_known` / `_firm_domain` logic.
  - `lookup_keys(facts)` gives the distinct keys to look up.
  - `check(facts, channel, purpose, active)` takes `active`, a collection of the active (`kind`, `value_hash`) pairs. It validates `channel` and `purpose` and returns `Blocked(reasons)` or `Allowed()`. Reasons are distinct and in enum order:
    - each key whose (`kind`, `value_hash`) is in `active` gives its reason;
    - a stage of `lost` gives `lead_lost`, and `disqualified` gives `lead_disqualified`.

    `channel` and `purpose` don't change the answer.
  - `Blocked.__post_init__` refuses empty, non-`BlockReason`, duplicate or out-of-order reasons (`ValueError`).
- **`nextron/app/queries/compliance.py`:**
  - `active_entry(kind, value_hash)` wraps `suppression_entries.active_for` and gives the entry or `None`;
  - `active_keys(keys) -> frozenset[tuple[str, str]]` gives the active pairs among `keys`.
- **`nextron/app/compliance/api.py`, the module-level function `assert_contactable(subject, channel, purpose) -> Allowed | Blocked`.** It is not a `@command`: it only reads, so it works inside a transaction and in a read-only unit.
  1. It checks `channel` and `purpose`, then the subject's type (anything else is a `TypeError`), before any read.
  2. It builds the facts:
     - a `ContactSubject`: the Contact (`queries.research.contact`) and its Lead;
     - a `NewContactSubject`: its own values with the Lead;
     - a `NewLeadSubject`: `LeadFacts(web_domain, mail_domains)`, stage `None`.

     For a Contact, its Lead gives the web domain and the stage (`queries.discovery.lead`, `queries.pipeline.stage`). An unknown Contact or Lead raises `LookupError`, and a merged Lead raises `ValueError`. Neither message holds an id or a value.
  3. It returns `check(facts, channel, purpose, queries.compliance.active_keys(lookup_keys(facts)))`. Nothing is written or logged.
- **`Discovery.upsert_lead`:**
  - When no Lead matches, before the insert, it calls `compliance_api.assert_contactable(NewLeadSubject(identity.web_domain), "email", "discovery")`, as a module attribute so the contract's fake can replace it.
  - `Blocked` returns `UpsertedLead(lead_id=None, created=False, merged=(), blocked=<the answer>)` with nothing written and nothing enqueued.
  - A matched Lead takes the old path, without the gate.
  - `UpsertedLead` gains `blocked: Blocked | None = None`.
- **`Research.add_contact`:**
  - After its argument checks, the Lead read and `assign`, and after `_match` (a read), but before any write, it calls `compliance_api.assert_contactable(NewContactSubject(lead.id, email, name, linkedin_url), "email", "discovery")` for every source. Each value is the candidate's, or, when the candidate lacks it, its match's.
  - On `Blocked`, nothing is written (no Contact, fill, Provenance or address) except one `events` row: entity `leads`, the Lead's id, `None` to `contact_blocked`, cause the first reason, `cause_ref` `None`, actor `system:research`, now. It returns `AddedContact(contact_id=None, created=False, email_dropped=False, blocked=<the answer>)`.
  - `AddedContact` gains `blocked: Blocked | None = None`.
- **`LeadImports.run`:**
  - A blocked `upsert_lead` adds `SuppressedRow(number, label)` (in `domain.lead_import`; `reason` is "Organization is on the Suppression List"). The row is neither imported nor merged, and its Contact isn't tried.
  - A blocked `add_contact` adds a note to the row:
    - `NOTE_CONTACT_SUPPRESSED` = "Contact is on the Suppression List; not imported" when any reason is a suppression reason;
    - otherwise `NOTE_CONTACT_LEAD_CLOSED` = "the Lead is Lost or Disqualified, so the Contact was not imported".
  - `merged` counts only matched rows.
  - `ImportSummary` gains `suppressed: tuple[SuppressedRow, ...] = ()`, in `ImportStopped` too.
  - `lead_import_finished` logs a `suppressed` count.
  - The page shows "Blocked: suppressed domain: N." in the counts and a "Rows blocked: suppressed domain" table (row, escaped label, reason) after the Market table.
- **`tests/contracts/test_ad16_callers.py`:**
  - **`MANIFEST`** of `Caller(name, where, qualname, calls=None, inserts=frozenset())`:
    - `where` holds path prefixes under `nextron/`;
    - `qualname` is a regex fullmatched against each def's dotted qualified name;
    - `calls`: the def must also call that attribute name.

    It is built when the AST finds a match. It lists:
    - `discovery.upsert_lead` (inserts `leads`);
    - the CSV import `LeadImports.run`;
    - `research.add_contact[<source>]` for every `ProvenanceSource` (inserts `contacts`);
    - `find_contact_email`, `verify_contact_email`, and the Signal Inbox chain (`process_signal_item` / `ingest_signal_item`);
    - replies Contact creation (`app/replies/`, `calls="add_contact"`);
    - `sequencing.enroll` (`enrollments`);
    - `drafting.request_*` with Call Prep (`drafts`, `call_preps`);
    - `approval.approve`;
    - pipeline Task and call-sheet creation, listing and delivery (any `pipeline` def naming `task` or `call_sheet`, `cancel_*` excepted; `linkedin_tasks`);
    - `compliance.assert_sendable` (`outbound_messages`).
  - **Scenarios.** A built entry without a scenario fails, and an unbuilt one skips with "not built yet: <name>".
    - Each scenario runs against a recording fake of `nextron.app.compliance.api.assert_contactable` and a `before_cursor_execute` log of write statements on the test engine.
    - **With `Allowed`:** the expected subject types, channel and purpose are recorded, and a gate call comes before the first write and before every guarded insert.
    - **With `Blocked`:** no table gains a row (except `add_contact`'s one `contact_blocked` row), so no job is added.
  - **Call sites.** Every call of `upsert_lead` or `add_contact` under `nextron/`, outside their defining defs, sits in a manifest def.
  - **Insert scan.** It finds each `db/repos` function that inserts into a guarded table (`leads`, `contacts`, `enrollments`, `drafts`, `linkedin_tasks`, `call_preps`, `outbound_messages`, found by the models' `__tablename__`). An insert is SQLAlchemy `insert(Model)`, `Model(...)` or `Model.__table__.insert`. Any reference to such a function outside its module and outside a manifest def listing that table fails. So does any non-docstring string under `nextron/` (outside the migrations) holding `INSERT INTO <guarded table>`.
  - **Probes** on `tmp_path` trees prove each check: a missing scenario, a stray caller, a stray insert, a constructed model, raw SQL, a write before the gate, and a `Blocked` ignored.
- Tests write no personal data to `events` and no log line naming a value.

**Never:**
- No migration, setting, job type, adapter, port, notification or dependency.
- No change to `suppress`, the table, its triggers or 3.3's contracts. No void (3.5), panel or Eligibility (3.6).
- Not built here: `find_contact_email`, `verify_contact_email` and the Signal Inbox chain. Their AC parts go to their stories through the manifest and `deferred-work.md`.
- Nothing is cached, and no `Blocked` raises out of a command (an exception dooms the transaction).
- No edits outside `nextron-engine/` except this spec, `sprint-status.yaml` and `deferred-work.md`.

### Decisions made overnight (pending user review)

1. **Gate subjects.** The gate has three subjects: `ContactSubject` (a stored Contact), `NewContactSubject` and `NewLeadSubject`. The last two carry the values of something not stored yet. The gate reads every Lead fact itself, so suppression is looked up in one place.
2. **Channel and purpose.** They are validated and recorded, but they don't change the answer: an opt-out blocks every channel.
3. **The gate is a module-level function** in `compliance.api`, not an injected service. It needs no clock, it serves read-only pages (3.6), and it changes no constructor. Callers reach it as `compliance_api.assert_contactable`, so the contract's fake can replace it.
4. **Contact creation uses `email`/`discovery`.** `add_contact` gates with `email`/`discovery` for every source. Replies (Epic 7) call the gate with their own purpose first, as the epics say.
5. **`upsert_lead` gates only before an insert.** A matched Lead is returned as before (FR-9), and merges and key attachment aren't gated.
6. **Blocked is a return value** (`lead_id`/`contact_id` `None` plus `blocked`). An exception would doom the transaction and stop the whole CSV batch.
7. **`add_contact` checks the Contact as it would be stored.** That is the candidate's values, with gaps filled from its match. A `Blocked` answer fills nothing.
8. **A Lost or Disqualified Lead blocks `add_contact` too.** The CSV note then says the Lead is closed, not that the Contact is suppressed.
9. **The `contact_blocked` row** is entity `leads` with the Lead's id, `None` to `contact_blocked`, and cause the first reason. No `cause_ref` and no value. A blocked Lead writes no `events` row: there is no Lead id, so the CSV summary is the record.
10. **Mail domains.** `leads.mail_domains` doesn't exist yet. The core supports mail domains, but every caller passes the web domain only.
11. **Free-webmail domains** are never looked up, because they are never keys.
12. **The manifest finds unbuilt callers by def name** (AST). A later story whose def appears without a scenario fails the contract. The call-site check forces every new `upsert_lead` or `add_contact` caller (Epic 8 discovery, replies) into the manifest.
13. **(Review pass 1) A matched Contact is gated on both value sets.** Decision 7 and the matrix's "Match blocked" row say the stored Contact must block a fill. The Always line checks the candidate's values first, so a stored value that differs from the candidate's went unchecked. When `add_contact` finds a match, it now also calls the gate with `ContactSubject(match.id)` (`email`/`discovery`), right after the `NewContactSubject` call and before any write. Either `Blocked` answer blocks; the reasons are combined in enum order. This is the conservative, compliance-safe reading.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Each reason | One active matching key, or a `lost` / `disqualified` stage | `Blocked((reason,))` | N/A |
| All at once | Every key active, Lead `lost` | All matching reasons, in enum order | N/A |
| Voided | The only match is a voided `reply_rule` entry | `Allowed()` | N/A |
| Webmail | `jane@gmail.com`, nothing else | `gmail.com` isn't looked up; `Allowed()` | N/A |
| New Lead, suppressed domain | CSV row on a suppressed domain, no Lead holds it | No Lead, no job; `suppressed` row; not counted imported or merged | N/A |
| Existing Lead | CSV row matches a Lead whose domain is suppressed | That Lead returned (merged count); its Contact blocked, with the note | N/A |
| Contact blocked | Address, person or profile key active | No Contact, Provenance or address; one `contact_blocked` event; CSV note | N/A |
| Match blocked | Candidate matches a Contact; the merged values are suppressed | Nothing filled; `contact_id=None` | N/A |
| Bad arguments | Unknown channel or purpose; wrong subject type | Nothing read | `ValueError` / `TypeError` |
| Unknown | Unknown Contact or Lead; merged Lead | Nothing read beyond it | `LookupError` / `ValueError` |

</frozen-after-approval>

## Code Map

- `nextron/domain/contactability.py`: 3.3's keys (`person_keys`, `firm_domain_keys`, `_known`, `_firm_domain`, `_address_domain`, `ContactSubject`). Add the gate core here; keep 3.3's API unchanged. `nextron/domain/pipeline_stage.py`: `PipelineStage`.
- `nextron/app/compliance/api.py`: `Compliance.suppress` (unchanged). Add `assert_contactable` at module level. It may import `queries.research`, `queries.discovery`, `queries.pipeline` (`stage`) and `queries.compliance`.
- `nextron/app/queries/compliance.py`: empty stub. Import only `nextron.domain` and `db.repos.compliance` (`suppression_entries.active_for`).
- `nextron/app/discovery/api.py`:
  - `Discovery.upsert_lead` (insert branch ~L214);
  - `UpsertedLead`;
  - `LeadImports.run` (~L398–479);
  - `ImportSummary`/`ImportStopped`/`NotedRow`;
  - the module docstring's steps.
- `nextron/app/research/api.py`: `Research.add_contact` (~L311–374), `AddedContact`, the docstring step list, and `eventlog.append` usage in `_address`.
- `nextron/domain/lead_import.py`: `BlockedRow` (~L242) and the `NOTE_*` texts (~L190): the pattern for `SuppressedRow` and the new notes.
- `nextron/web/templates/lead_import_form.html`, `nextron/web/lead_import_page.py` docstring: the summary.
- Import DAG (`pyproject.toml` c6): discovery > research > compliance > queries is legal. c7 forbids other owners' repositories.
- **Tests to copy:**
  - `tests/app/test_suppression.py`: the `World` helpers, `queue_on`, `transact`, the `sql` helper and the shipped-segments seeding;
  - `tests/app/test_lead_import.py`: CSV fixtures, with Markets seeded so rows aren't Market-blocked;
  - `tests/contracts/test_suppression_contracts.py`: the AST helpers (`_modules`, `_docstrings`, `_strings`, the import-binding resolution) and the probe pattern;
  - `tests/conftest.py` `db_engine`.
- **Tests likely to need updates:**
  - `tests/app/test_lead_import.py` and `tests/unit/test_lead_import_page.py` (the counts text);
  - any test that adds a Contact to a `lost` or `disqualified` Lead (`test_leads_page.py`, `test_lead_queries.py`, `tests/dashboard.py`; check).

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/contactability.py`: the gate core, per Always.
- [x] `nextron/app/queries/compliance.py`: `active_entry`, `active_keys`.
- [x] `nextron/app/compliance/api.py`: `assert_contactable`; update the docstring.
- [x] `nextron/app/discovery/api.py`: gate the insert branch, `UpsertedLead.blocked`, and the CSV import counting, notes and `suppressed`; update the docstring.
- [x] `nextron/app/research/api.py`: gate `add_contact`, `AddedContact.blocked`, the `contact_blocked` event; update the docstring.
- [x] `nextron/domain/lead_import.py`: `SuppressedRow`, `NOTE_CONTACT_SUPPRESSED`, `NOTE_CONTACT_LEAD_CLOSED`.
- [x] `nextron/web/templates/lead_import_form.html`, `nextron/web/lead_import_page.py`: the count and the table.
- [x] `tests/unit/test_contactability_check.py`: one table-driven case per reason, voided absent → `Allowed`, all reasons ordered, `Blocked` validation, the enums and parsers, `lookup_keys` (order, dedup, webmail, unnormalisable), the subject checks.
- [x] `tests/app/test_contactable.py`: every matrix row on real units with real entries. Voiding is raw SQL on a `reply_rule` entry. Also a read-only unit, `events` free of personal data, and nothing logged.
- [x] `tests/contracts/test_ad16_callers.py`: per Always, with probes.
- [x] Update affected existing tests; `nextron-engine/README.md`: a Story 3.4 section.

**Acceptance Criteria:**
- Given `tests/contracts`, when it runs, then `test_ad16_callers.py` passes. Every unbuilt manifest entry is reported skipped by name, and every probe fails as intended.
- Given `uv run lint-imports`, when it runs, then every contract is kept.

## Implementation Notes

- **Core** (`nextron/domain/contactability.py`): `Channel`, `Purpose`, `BlockReason`,
  `SUPPRESSION_REASONS` (the five key reasons), `channel_of`/`purpose_of`, `LeadFacts`,
  `ContactFacts`, `NewContactSubject`, `NewLeadSubject`, `Allowed`, `Blocked`, `lookup_keys`
  and `check`. The checked keys reuse 3.3's `_known`, `_firm_domain` and `_address_domain`; one
  internal list (`_checked_keys`) pairs each key with its reason, so `lookup_keys` and `check`
  can't disagree. One domain key can give two reasons (an address on the Lead's own domain:
  `address_domain_suppressed` and `lead_domain_suppressed`). Small additions beyond the spec's
  `ValueError` list: `Blocked` with a non-tuple `reasons`, `check` with a `str`/`bytes` or
  non-collection `active`, and `queries.compliance.active_keys` with a non-`SuppressionKey`
  item each raise `TypeError`. Channel and purpose messages list the allowed codes, never the
  value.
- **Gate** (`nextron/app/compliance/api.py`): module-level `assert_contactable`; facts come
  from `queries.research.contact`, `queries.discovery.lead` and `queries.pipeline.stage`, keys
  from `queries.compliance.active_keys` (new, with `active_entry`). Discovery and research call
  it as `compliance_api.assert_contactable` with `Channel.EMAIL`/`Purpose.DISCOVERY`.
- **Callers.** `Discovery.upsert_lead` gates only the insert branch. `Research.add_contact`
  gates after `_match` with `_new_contact` (candidate's value, else the match's) and, on
  `Blocked`, appends the `contact_blocked` event at the call's `now`. `LeadImports.run` counts a
  blocked Lead as a `SuppressedRow` (committed batches only after a stop), counts `merged` from
  matched rows only, notes blocked Contacts with `NOTE_CONTACT_SUPPRESSED` or
  `NOTE_CONTACT_LEAD_CLOSED` (`_contact_note`), and logs `suppressed`. `REASON_SUPPRESSED` holds
  the row reason text.
- **Page.** The counts line adds "Blocked: suppressed domain: N."; the "Rows blocked:
  suppressed domain" table follows the Market table, labels through `untrusted_block`.
- **Contract** (`tests/contracts/test_ad16_callers.py`): an AST `Index` of every def (dotted
  qualname with nested defs) and every `Name`/`Attribute` load with its chain of enclosing defs.
  A use counts as inside a manifest def when any enclosing def is one (so a nested helper of
  `LeadImports.run` is covered). The call-site check covers references as well as calls. The
  insert scan reads `__tablename__` from `db/models`, recognises `insert(Model)` (any
  `sqlalchemy*` `insert` import or any `.insert(Model)` attribute call), `Model(...)` and
  `Model.__table__.insert()` (model names, import aliases and `models.Model` attributes), and
  follows calls between a repository module's own top-level functions, so a wrapper of an
  inserting function inserts too. Module bindings follow 3.3's import-binding resolution
  (aliases, dotted imports, parent packages, relative imports, imported functions). The
  scenario harness logs `INSERT`/`UPDATE`/`DELETE`/`REPLACE` statements through
  `before_cursor_execute`; with `Allowed`, it requires a gate call before the first write and
  one gate call since the previous guarded insert before each guarded insert; with `Blocked`,
  it compares every table's row count. Each built scenario runs `Allowed` then `Blocked` on one
  fresh database (counts are relative). Runtime probes load small modules from `tmp_path` and
  run them against a temporary SQLite file through the same harness.
- **Deferred** (`deferred-work.md`): `find_contact_email`, `verify_contact_email`, the Signal
  Inbox chain, the later AD-16 callers' scenarios, and passing `mail_domains` once the column
  exists.

**Review patches (pass 1).**
- (A) `Research.add_contact`: with a match, a second gate call
  `ContactSubject(match.id)`/`email`/`discovery` right after the `NewContactSubject` call and
  before any write (decision 13). `_blocked` combines both answers' reasons in `BlockReason`
  order into one `Blocked`, so there is one `contact_blocked` event, caused by the first
  combined reason. The `_new_contact` and module docstrings say so. Tests cover conflicting
  stored and candidate values in both directions, for the email and the LinkedIn URL, and the
  combined reasons.
- (I) Both callers proceed only on `Allowed`. `Blocked` takes the blocked path, and any other
  answer raises `TypeError("assert_contactable answered a <type>")`. Tests cover a gate
  answering `None` (and `True` or a text, for `upsert_lead`), including a match's second
  answer: `TypeError`, nothing written.
- (B) Contract: `Scenario` gains `cases` (`add_contact` adds a stored Contact matched by name
  that a LinkedIn URL would fill) and `blocked_writes` (`{("INSERT", "events")}` for
  `add_contact`, empty otherwise). Under `Blocked`, `blocked_write_problems` fails any other
  logged write. A `gates_first` check (all gate calls before the first write; off for the CSV
  import) proves the match's two calls come before the `UPDATE`. New probes: an `UPDATE` and
  `DELETE` after a `Blocked` answer, and a write between two gate calls.
- (C) Each case's `prepare` returns the expected `GateCall`s, compared by value. For the CSV
  import, the expected subjects are computed after the run, once the Lead's id exists.
- (D) Looser manifest patterns: `*find*email*`, `*verify*email*`, `*enroll*`, `*approve*`.
  The probe adds `approve_draft`, `enroll_contact`, `find_email_for_contact` and
  `verify_address_email`, and the real tree's built set is unchanged.
- (F) The insert scan also catches `insert(Model.__table__)`, `sa.insert(models.Model.__table__)`,
  `insert(table("<guarded>"))`, `sa.table(...).insert()` and `bulk_insert_mappings` /
  `bulk_save_objects` with a guarded model first. Each form is in the constructed-model probe.
- (G) `SQL_INSERT` takes an optional plain, quoted or bracketed schema (`"main"."leads"`,
  `[main].[contacts]`), and both are probed.
- (H) README: the gate's coverage is stated exactly. A new Lead is gated before its insert (a
  matched Lead isn't), `add_contact` before any write and on both value sets, and later paths
  through the contract. Merges, key attachment, `reparent_lead` and `resolve_market` aren't
  gated.
- (J) App test: a new Lead's mail domain blocks (`lead_domain_suppressed`), and a webmail mail
  domain is never looked up.

**Final verification (orchestrator).**
- Targeted run: the Verification files, the other research and discovery tests, and `tests/contracts`. 2118 passed, 10 skipped (`ai_eval` and the 9 "not built yet" AD-16 callers).
- Full suite through `suite_lock.py` (`track-A`): 11111 passed, 12 skipped.
- `lint-imports`: 29 kept, 0 broken.
- `deferred-work.md` gained the update-scan entry (E) for Stories 2.13 and 2.14.

## Spec Change Log

## Review Triage Log

Pass 1 (review loop 0). Three layers: blind hunter (BH, 13 findings), edge-case hunter (EC, 8), verification gap (VG, 1 gap + 3 other). That is **25 raw** findings, **19 after grouping**: 9 patched, 1 deferred, 9 rejected.

| # | Layer | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| 1 | BH | `add_contact` gates the candidate's values first, so a matched Contact whose own stored address (or name) is suppressed still gets role, LinkedIn and Provenance filled. | medium | `_new_contact` takes `getattr(candidate, name)` first. A stored `jane@firm.co.uk` (active address entry) matched by name, with a candidate bringing another email, gets `Allowed` and a fill. This contradicts the frozen matrix row "Match blocked" and decision 7. The Always line caused it; resolved overnight as decision 13 (gate the match too). | patch (A) |
| 2 | BH | With `Blocked`, the contract counts rows only, so an `UPDATE`/`DELETE` after a block passes; the `add_contact` scenarios never hit the match/fill branch. | medium | `run_scenario` uses `gained_rows` alone, though the recorder logs every write. A future `approve` flipping `drafts.status` would pass. `_add` stores no Contact before the call. | patch (B) |
| 3 | BH | The contract checks the subject's type, not its values. | low | `run_scenario` compares `type(call.subject)`. A caller passing `NewLeadSubject(None)` passes. Test-only, a direct correction: compare the recorded subjects by value for the built paths. | patch (C) |
| 4 | BH | Future callers can escape the manifest by def name, and address assignment by `UPDATE` (`contacts_repo.fill(email=...)`) isn't scanned. | medium | The `find_contact_email`, `verify_contact_email`, `enroll` and `approve` patterns are exact final names, so `approve_draft` or `find_email_for_contact` skips forever. The insert scan sees inserts only. Broadening the patterns is a direct fix. An update scan is new machinery that Story 2.13 (address assignment) must own. | patch (D) + defer (E) |
| 5 | BH | The insert scan misses `insert(Model.__table__)`, `insert(table("leads"))`, `bulk_insert_mappings`/`bulk_save_objects`, repository class methods and f-string SQL. | low | Verified in `_model_of`/`_inserted_table`. The first three each take a line or two plus a probe. Repositories are module functions by convention (none has a class), and a dynamic table name can't be read statically, so those two parts are rejected. | patch (F) |
| 6 | BH | The pipeline pattern's `(?!cancel_)` exclusion is fragile: `_cancel_tasks`, `complete_task` or `_task_row` would need a scenario. | low | True, but over-inclusion is the compliance-safe direction: a false hit fails loudly and the Epic 10 story narrows the pattern on review. Narrowing now risks missing a real creator. | reject |
| 7 | BH | The CSV note says "Contact is on the Suppression List" when the cause is a domain reason. | low | Real, but the frozen Always picks `NOTE_CONTACT_SUPPRESSED` for "any suppression reason", the five including both domain reasons. The fix changes the approved spec. | reject (spec change; flag for Mac) |
| 8 | BH | A merged row on a suppressed or closed Lead with no Contact cells gets no note, and its keys are still attached. | low | Real, but decision 5 (frozen) returns a matched Lead "as before, without the gate". A note needs the gate on the match path. | reject (spec change; flag for Mac) |
| 9 | BH | A `contact_blocked` event is appended on every repeat import. | low | By design: the AC asks for an `events` row recording each blocked creation. One row per blocked attempt is bounded by the imports Mac runs. | reject |
| 10 | BH | The gate costs about 8 queries per row. | low | Local SQLite point reads on indexed keys (the partial unique index): microseconds each; 5,000 rows is well under a second. Premature. | reject |
| 11 | BH | `UpsertedLead`/`AddedContact` don't enforce "`id is None` exactly when `blocked`", and the two callers check different sides. | low | No code builds an inconsistent result. `LeadImports` checks `lead_id is None`, the safe side for passing the id on. The fix adds guards for a state not shown reachable. | reject |
| 12 | BH | `_lead_facts` raises `LookupError` ("unknown Lead") when the stage read returns `None`. | false | `leads.pipeline_stage` is NOT NULL with a server default and a CHECK, and the Lead row was read in the same transaction, so `stage()` can't return `None` there. | reject |
| 13 | BH | The README says every path that creates a Lead or Contact calls the gate before it writes. | low | The matched-Lead path (merge, key attachment) is deliberately ungated (decision 5), and `reparent_lead`/`resolve_market` aren't gated. A direct wording fix. | patch (H) |
| 14 | EC | Matched Contact: candidate's value hides the match's suppressed stored value. | medium | Same as finding 1. | patch (A) |
| 15 | EC | A gate answer that is neither `Allowed` nor `Blocked` is treated as allowed (fail-open). | low | Both callers test `isinstance(answer, Blocked)`, so a `None` from a broken fake or a regression proceeds to write. A direct fix: proceed only on `Allowed`, raise `TypeError` on anything else. | patch (I) |
| 16 | EC | `check` with an `active` collection of non-tuple items (lists) never matches, so it allows. | low | The only producer is `queries.compliance.active_keys`, which returns a frozenset of tuples. Unreachable; the fix adds per-item guards. | reject |
| 17 | EC | The `Blocked` check ignores `UPDATE`/`DELETE`; no match-fill scenario. | medium | Same as finding 2. | patch (B) |
| 18 | EC | `insert(Model.__table__)` isn't recognised. | low | Same as finding 5. | patch (F) |
| 19 | EC | The raw-SQL regex misses a quoted or bracketed schema prefix (`"main"."leads"`). | low | `(?:\w+\s*\.\s*)?` admits only a bare schema. A one-regex correction with a probe. | patch (G) |
| 20 | EC | Suffix variants (`approve_draft`, `enroll_contact`) aren't detected. | medium | Same as finding 4's naming part. | patch (D) |
| 21 | EC | A repository reached through a higher ancestor import (`from nextron.db import repos`) isn't resolved. | low | No module uses that form. It's the same case 3.3 rejected (its EC20): the fix adds alias-tracking branches. | reject |
| 22 | VG | `test_the_candidates_own_values_come_before_its_matchs` can't tell which value wins; no test has a candidate and match with different values. | medium | Verified: the test's match has no LinkedIn URL. Covered by the decision-13 fix, with conflicting-value tests in both directions. | patch (A) |
| 23 | VG | `_new_contact`'s docstring and the README say "the Contact as it would be stored", which isn't true when the candidate and match differ. | low | Same root cause as finding 1; the docstrings change with the fix. | patch (A) |
| 24 | VG | A name-matched Contact whose stored email or LinkedIn URL is suppressed can still be filled. | medium | Same as finding 1. | patch (A) |
| 25 | VG | Lead mail domains are never tested through the app gate. | low | Only the pure `check()` tests them. A direct test addition: `assert_contactable(NewLeadSubject(None, ("mail.co.uk",)), ...)` against an active domain entry. | patch (J) |

## Verification

**Commands** (from `nextron-engine/`):
- `uv run pytest -q tests/unit/test_contactability_check.py tests/unit/test_contactability.py tests/app/test_contactable.py tests/app/test_suppression.py tests/app/test_lead_import.py tests/app/test_leads.py tests/unit/test_lead_import_page.py tests/unit/test_leads_page.py tests/app/test_lead_queries.py`: all pass.
- `uv run pytest -q tests/contracts`: all pass. The unbuilt AD-16 callers are skipped by name.
- `uv run lint-imports`: all contracts kept.
