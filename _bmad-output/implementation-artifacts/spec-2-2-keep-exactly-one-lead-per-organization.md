---
title: 'Story 2.2: Keep exactly one Lead per organization'
type: 'feature'
created: '2026-11-17'
status: 'done'
baseline_commit: '198f2be198a1844526d9dd5bc043e7b8ee98685e'
route: 'dispatch'
review_loop_iteration: 0
story_key: '2-2-keep-exactly-one-lead-per-organization'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The engine has no Leads, no identity normalisation and no Pipeline Stage machine. Every later Epic 2 story (CSV import, Signal Inbox, crawling, scoring) and Epics 3, 7, 8 and 9 need one Lead per organization, matched by website domain, Companies House number or place ID at any stage, including Disqualified and suppressed ones (FR-9). AD-3 makes `discovery.merge` publish `LeadsMerged`, and SC-8 says only `domain.identity` normalises.

Requirements: FR-9, AD-3, AD-4, SC-8.

**Approach:**
- `nextron/domain/identity.py` holds the identity normalisers on a vendored Public Suffix List.
- `nextron/domain/leads.py` holds `LeadIdentity` and `LeadSource`, and `nextron/domain/pipeline_stage.py` holds the Lead Pipeline Stage machine.
- Migration `0008_2_2_leads` creates `leads`, with an identity group owned by `discovery` and a pipeline group owned by `pipeline`.
- `Discovery.upsert_lead` and `Discovery.merge` are `@command`s. `Pipeline` records the creation `events` row, and its `reparent_lead` is the first `LeadsMerged` subscriber.

The acceptance criteria are Story 2.2 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**

- **Identity** (`nextron/domain/identity.py`, stdlib only). Every refusal raises `IdentityError(reason)`: a `DomainError` with code `invalid_identity` whose message is the reason. The message never contains the input, which may be an email address. A non-`str` argument raises `TypeError`. Any input longer than 2,048 characters is refused.
  - **`registrable_domain(text) -> str`** (decision 7):
    1. Strip the text. Refuse it if it is empty or holds whitespace or a `Cc` character.
    2. A text with a `<scheme>://` prefix must use `http` or `https` (in any case); any other scheme is refused. A text without one is read as `http://` + text.
    3. Take the hostname with `urllib.parse.urlsplit`. Refuse it if it is missing or is an IP address (`ipaddress`). Drop one trailing dot.
    4. IDNA-encode each label with the stdlib `encodings.idna.ToASCII` (IDNA 2003) and lowercase it. Each label must fullmatch `[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?`, and the whole name may be at most 253 characters.
    5. Strip one leading `www` label.
    6. Return eTLD+1 by the PSL algorithm, using both sections. The prevailing rule is the exception rule if one matches, otherwise the longest matching rule; wildcards count. Refuse a name that is itself a public suffix, a single label, or under a top-level label with no rule in the list (decision 7).
  - **`normalize_email(text) -> str`.** Strip, NFC and lowercase. Then:
    - refuse whitespace, a `Cc` character, or anything other than exactly one `@`;
    - the local part is 1–64 characters, kept as it is (no dot or plus stripping);
    - the domain is IDNA-encoded label by label (LDH, at least two labels);
    - the whole address is at most 254 characters.
  - **`normalize_name(text) -> str`.** NFKC, then `casefold`. Each run of characters that aren't `isalnum()` becomes one space, then strip. An empty result, or an input over 500 characters, is refused.
  - **`normalize_linkedin_url(text) -> str`.**
    - It accepts only `http(s)` (or no scheme), on the host `linkedin.com` or any `*.linkedin.com`, with a first path segment of `in` (in any case) followed by a slug.
    - The slug is percent-decoded (strict UTF-8), NFC'd and lowercased. It must be 1–100 characters with no whitespace or `Cc`. It is re-encoded with `urllib.parse.quote(slug, safe="-_.~")`.
    - The result is `https://www.linkedin.com/in/<slug>`. Further segments, the query and the fragment are dropped.
    - Every other URL is refused.
  - **`normalize_company_number(text) -> str`.** Strip and uppercase. 1–8 digits are zero-padded to 8; otherwise the text must fullmatch `[A-Z0-9]{8}` with at least one digit.
  - **`normalize_place_id(text) -> str`.** Strip, then it must fullmatch `[A-Za-z0-9_-]{1,512}`. Case is kept.
  - **`normalize_country_code(text) -> str`.** Strip and uppercase, then it must be in `COUNTRY_CODES`: a hard-coded `frozenset` of the 249 assigned ISO 3166-1 alpha-2 codes, the same set as `tzdata/zoneinfo/iso3166.tab`. There is no alias (`UK` is Story 2.3's).
  - **The Public Suffix List.** It is vendored byte-for-byte at `nextron/domain/psl/public_suffix_list.dat`, copied from `C:\Users\mac\AppData\Local\Temp\claude\aa4d8b4ed58855c9\85872522-d5e0-4288-b624-7096a7dc9d2a\scratchpad\psl-download\public_suffix_list.dat`:
    - version `2026-11-16_22-30-55_UTC`;
    - SHA-256 `76918ba7c5f7501785558708be861152377f962643844828a7a3cb7f3ae864c5`;
    - MPL-2.0, with its header kept.

    It is parsed once (`functools.cache`). Rule labels are IDNA-encoded the same way; all 10,455 rules encode. `psl_version() -> str` returns the header's `VERSION`. `nextron-engine/.gitattributes` gets `nextron/domain/psl/** -text`.
- **Leads domain** (`nextron/domain/leads.py`, stdlib only).
  - **`LeadSource(StrEnum)`:** `csv`, `signal_inbox`, `places`, `companies_house`, `job_aggregator` (decision 1).
  - **`LeadIdentity(name, segment_id, web_domain=None, ch_number=None, place_id=None, country_code=None)`** is a frozen, slotted dataclass, checked in `__post_init__`:
    - `name`: a `str` of 1–200 characters, equal to its own strip, with no `Cc`, `Cf`, `Zl` or `Zp` character;
    - `segment_id`: a ULID;
    - each key is `None` or already normalised: `registrable_domain(x) == x`, `normalize_company_number(x) == x`, `normalize_place_id(x) == x`;
    - `country_code` is `None` or in `COUNTRY_CODES`;
    - at least one of the three keys is set.

    A problem raises `InvalidLeadIdentity(field, reason)`: a `DomainError` with code `invalid_lead_identity` and message `"<field>: <reason>"`, never showing the value.
- **Pipeline Stage machine** (`nextron/domain/pipeline_stage.py`, the Job machine's pattern, `MACHINE = "pipeline_stage"`; decision 10).
  - **`PipelineStage`**, in AD-4 order: `new`, `queued`, `contacted`, `replied`, `interested`, `call_booked`, `call_held`, `audit_proposed`, `audit_won`, `build_proposed`, `won`, `not_now`, `lost`, `disqualified`.
    - `OPEN_STAGES` are the first 11;
    - `PARKED_STAGES` is `(not_now,)`;
    - `CLOSED_STAGES` (terminal; the Operator may reopen) are `(lost, disqualified)`.
  - **`StageEvent`, `StageCause` and the read-only `TRANSITIONS`** follow the Design Notes table. `transition(current, event)` raises `IllegalTransition` for any pair the table lacks.
  - **`CREATION`** maps each `LeadSource` to `Transition(new, created_from_<source>)`.
  - **`reopen(current, previous)`** is `Transition(previous, reopened)` when `current` is closed and `previous` is open or parked. Otherwise it raises `IllegalTransition(MACHINE, "reopen", current)`.
  - **`merged(survivor, loser) -> Transition | None`** (decision 2):
    - a closed survivor keeps its stage (`None`);
    - otherwise a closed loser wins, with `Transition(loser, leads_merged)`;
    - otherwise the later stage in `ADVANCEMENT` wins: `new`, `queued`, `contacted`, `replied`, `not_now`, `interested`, `call_booked`, `call_held`, `audit_proposed`, `audit_won`, `build_proposed`, `won`;
    - a tie keeps the survivor's stage.
- **Persistence.**
  - **Migration `0008_2_2_leads`** (down `0007_2_1_segments`; `downgrade()` raises `NotImplementedError`) creates `leads`, every column NOT NULL unless stated:
    - `id` ULID (`pk_leads`);
    - `name` `String(200)`;
    - `web_domain` `String(253)`, `ch_number` `String(8)` and `place_id` `String(512)`, each nullable, each with a unique index `uq_leads_<col>`;
    - `segment_id` ULID, FK `fk_leads_segment_id_segments`;
    - `country_code` `String(2)`, nullable;
    - `merged_into` ULID, nullable, FK `fk_leads_merged_into_leads`;
    - `created_at` `UtcDateTime`;
    - `pipeline_stage` `String(32)`, with server default `'new'`.

    No FK cascades. The CHECKs are:
    - `ck_leads_pipeline_stage`: in the 14 stages;
    - `ck_leads_country_code`: `country_code IS NULL OR country_code GLOB '[A-Z][A-Z]'`;
    - `ck_leads_identity_key`: `merged_into IS NOT NULL OR web_domain IS NOT NULL OR ch_number IS NOT NULL OR place_id IS NOT NULL`;
    - `ck_leads_not_self_merged`: `merged_into IS NULL OR merged_into <> id`.

    The model, `LeadRecord` in `db/models/leads.py`, matches the migration.
  - **`owners.py`:** `leads` has `discovery` writing `id`, `name`, `web_domain`, `ch_number`, `place_id`, `segment_id`, `country_code`, `merged_into` and `created_at`, and `pipeline` writing `pipeline_stage`. **`pii_registry.py`:** every column is `none` (decision 9).
  - **Repositories.** Each statement is explicit Core `insert`/`update`/`select` through `current().session`, so writes run in order. They check nothing.
    - `db/repos/discovery/leads.py`:
      - `StoredLead` (frozen: every discovery column);
      - `get(id)`;
      - `by_web_domain`, `by_ch_number` and `by_place_id`;
      - `insert(...) -> id`, which omits `pipeline_stage`;
      - `set_keys(id, **keys)`;
      - `mark_merged(loser_id, survivor_id, cleared_keys)`.
    - `db/repos/pipeline/stages.py`: `stage(lead_id) -> str | None` and `set_stage(lead_id, stage)`.
  - **Revision id.** Bump `HEAD` in `tests/contracts/test_persistence_contracts.py` (and its chain probes: `0008_*` becomes `0009_*`, `0009_*` becomes `0010_*`, "revision 8" becomes "revision 9") and in `tests/unit/test_migrations.py`, plus the `tests/app/test_serve_process.py` assert.
- **Pipeline** (`app/pipeline/api.py`, `Pipeline(clock)`, `actor="system:pipeline"`, entity `leads`).
  - **`@command record_created(lead_id, source)`:** the stored stage must be `new` (otherwise `IllegalTransition`). It appends one `events` row: `None` to `new`, cause `CREATION[source].cause`, no `cause_ref`.
  - **`@command reparent_lead(n: LeadsMerged)`:** it reads both stages and computes `merged(...)`. On a `Transition`, it sets the survivor's stage and appends `events` from the old stage to the new one, cause `leads_merged`, `cause_ref` the merged Lead's id. The merged Lead's stage is unchanged.
- **Discovery** (`app/discovery/api.py`, `Discovery(clock, pipeline)`; it imports `app.pipeline.api` and `app.queries.setup`, which the DAG allows).
  - **`@command upsert_lead(identity: LeadIdentity, source: LeadSource) -> UpsertedLead(lead_id, created, merged: tuple[str, ...])`:**
    1. A wrong type raises `TypeError`. A `segment_id` not among `queries.setup.segments()` raises `InvalidLeadIdentity("segment_id", ...)`.
    2. Look up each set key and follow `merged_into` to the survivor, refusing a chain longer than 64 links (`RuntimeError`). Leads match at any stage.
    3. **No match:** insert at `created_at` = now, then `pipeline.record_created`; `created=True`.
    4. **Two or more distinct survivors:** the oldest (by `created_at`, then `id`) survives. `merge` each of the others into it, in that order, and list them in `merged` (decision 3).
    5. Then attach each candidate key that no Lead holds and the survivor lacks. Never change an existing Lead's `name`, `segment_id` or `country_code` (decision 4).
  - **`@command merge(survivor_id, loser_id) -> None`.** It raises `MergeRefused(reason)` (code `merge_refused`) when:
    - the two ids are the same;
    - either Lead is unknown;
    - either Lead is already merged;
    - the survivor is newer than the loser (by `created_at`, then `id`).

    Otherwise, in order:
    1. `mark_merged`: set `merged_into` and clear each key the survivor lacks;
    2. `set_keys` on the survivor;
    3. `uow.publish(LeadsMerged(survivor_lead_id, merged_lead_id))`.

    A key the survivor holds with a different value stays on the loser (decision 5).
- **Queries.**
  - `queries.discovery.lead(lead_id) -> StoredLead | None`;
  - `queries.pipeline.stage(lead_id) -> PipelineStage | None`.
- **`main.build_subscribers(clock=None)`** registers `LeadsMerged` → `Pipeline(clock or SystemClock()).reparent_lead`, then freezes.
- **Tests** run as `test` with temporary DBs, with no network and no keyring.

**Never:**
- No adapter, port, route, page, job type, trigger, setting, credential or dependency. In particular, nothing outside the stdlib for IDNA or the PSL.
- No `market_id`, coordinates, profile, score or other column groups (their stories add them). No CSV import, Signal Inbox, `assert_contactable` or denylist check (Stories 2.3, 2.16 and 3.x). No `advance_to_at_least`, `park_not_now` or `close` commands (Stories 7.x and 9.x).
- No `events` row for the merge itself, apart from the survivor's stage change (decision 11). No status string outside `pipeline_stage.py`.
- Don't edit the vendored HTMX; `serve`, `doctor`, `open` or `seed sync` behaviour; or anything outside `nextron-engine/` except this spec, `sprint-status.yaml` and `deferred-work.md`.

### Decisions made overnight (pending user review)

1. **A Lead's source is recorded in its creation `events` row:** cause `created_from_<source>`, actor `system:pipeline`. Story 9.6 counts "engine-sourced" Leads from Pipeline Stage `events` rows alone, and the AC gives `upsert_lead` a `source` with no column. The five sources are the ones the epics name: CSV, Signal Inbox, Places, Companies House and job aggregator.
2. **On a merge, "more advanced" is:**
   - a closed stage (`lost` or `disqualified`) beats everything, so a merge never makes an organization contactable again;
   - otherwise, Not Now ranks just after `replied`, because it is a Reply outcome that `hot_reply` and `book_call` move on from;
   - two closed stages, or a tie, keep the survivor's stage.
3. **A candidate matching several Leads merges them** into the oldest, in the same transaction, because FR-9 says the engine "merges". Story 2.12 relies on this.
4. **`upsert_lead` never changes an existing Lead's name, Segment or country.** It only attaches unique keys that the Lead lacks and no Lead holds. A key that conflicts with the Lead's own value is dropped.
5. **Merging moves only the keys the survivor lacks.** One column can't hold two domains. A conflicting key stays on the loser, where `upsert_lead` still finds it and follows `merged_into`, so FR-9 holds.
6. **AD-3's Enrollment refusal is deferred.** `merge` is refused for the same Lead, an unknown Lead, an already-merged Lead, or a newer survivor. AD-3 also refuses a merge when both Leads have a live Enrollment, but no `enrollments` table exists yet; the deferral is logged in `deferred-work.md`.
7. **Identity details the spine leaves open:**
   - stdlib IDNA 2003, because the domain layer is stdlib-only;
   - only `http(s)`;
   - IP hosts, public suffixes and top-level labels not in the PSL are refused, so `example.local` is not an organization;
   - `www.` is stripped before eTLD+1, as the spine orders it, so `www.co.uk` is refused;
   - `normalize_name` folds case, compatibility forms and punctuation;
   - `normalize_linkedin_url` accepts only `/in/` profiles;
   - company numbers, place IDs and country codes get normalisers here too, because only `domain.identity` normalises.
8. **Schema choices:**
   - `name` and `segment_id` are required, so a caller with no name (Story 2.16) passes the domain;
   - `country_code` is nullable, because Signal Inbox items carry no country;
   - there is no `updated_at`, because two owners write the row;
   - a Lead needs at least one key, because a keyless Lead could never be deduplicated.
9. **Every `leads` column is PII class `none`.** These are organization data, and erasing a Contact must not tombstone the firm's Lead.
10. **The machine's table also carries the later moves.** It covers the moves that AD-4, FR-45 and Stories 7.6, 9.1 and 9.3 name, so those stories add commands, not statuses. Reopen and merge have targets the event doesn't fix, so they are the functions `reopen` and `merged`.
11. **A merge leaves no `events` row of its own.** `merged_into` is the record. AD-4 forbids status strings outside a machine, and `merged` isn't a Pipeline Stage.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Identity | `https://www.Smith-Law.co.uk/team`; `bücher.de`; `a.b.ck`, `foo.blogspot.com`; `linkedin.com/in/Jane-Doe/?trk=x`; `" Jane.Doe+x@Example.COM "` | `smith-law.co.uk`; `xn--bcher-kva.de`; `a.b.ck`, `foo.blogspot.com`; `https://www.linkedin.com/in/jane-doe`; `jane.doe+x@example.com` | N/A |
| Identity refused | `co.uk`, `www.co.uk`, `ftp://x.com`, `10.0.0.1`, `example.local`, `localhost`, `a@b@c.com`, `linkedin.com/company/x` | `IdentityError`; message excludes the input | raised |
| New | no Lead holds the keys | 1 row, stage `new`, `created=True`; 1 `events` row `None`→`new`, cause `created_from_csv` | N/A |
| Match any stage | domain held by a `disqualified` Lead (or one merged into another) | survivor returned, `created=False`, no row or event added | N/A |
| Attach | match by domain; candidate brings an unheld `ch_number` | survivor gains `ch_number` | N/A |
| Multi-match | domain → Lead A (older), place_id → Lead B | B merged into A, `merged=(B,)`; `LeadsMerged` once; A's stage per `merged()` | N/A |
| Merge | A `new` (older), B `contacted` with `place_id`, A without | B `merged_into=A`, B's `place_id` NULL, A has it; A → `contacted` with an `events` row (cause `leads_merged`, `cause_ref` B) | N/A |
| Merge refused | same id; unknown; already merged; survivor newer | `MergeRefused`; transaction rolled back | raised |
| Machine | each (stage, event) pair | the table's row or `IllegalTransition` | raised |

</frozen-after-approval>

## Code Map

- **The machine pattern.** `nextron/domain/job.py:179-260` (statuses, events, causes, `CREATION`, `TRANSITIONS` as `MappingProxyType`, `transition`) and `nextron/domain/machines.py` (`Transition`, `IllegalTransition`). `domain/alert.py` is a smaller example.
- **Errors.** `nextron/domain/errors.py` `DomainError`: each subclass declares a unique `code`. `tests/contracts/test_error_contracts.py` imports every domain module.
- **The notification.** `nextron/domain/notifications.py` `LeadsMerged(survivor_lead_id, merged_lead_id)` already exists.
- **Subscribers.**
  - `nextron/db/uow.py` `Subscribers.register_subscriber` takes only `@command`s.
  - `nextron/main.py:634` `build_subscribers()` is the only registrar (`test_persistence_contracts.py` REGISTRY). Its callers are `connect_database` (643) and doctor's `_read_only_units` (840); keep both working with the default clock.
  - `tests/unit/test_serve.py:669` asserts an empty registry: change it to assert exactly `LeadsMerged` → one `@command` named `reparent_lead`.
  - `main.py` imports `SystemClock` already (246); the main docstring at 35 gets the registration.
- **Services.** `nextron/app/notify/api.py:170` (`Notify(clock, ...)`, `@command alert`, `_record` → `eventlog.append`) is the class-with-clock and event pattern. `app/discovery/api.py`, `app/pipeline/api.py`, `app/queries/discovery.py`, `app/queries/pipeline.py` and `db/repos/{discovery,pipeline}/__init__.py` are stubs whose docstrings say "Empty until a story adds…"; rewrite those lines.
- **The DAG** (`pyproject.toml` c6): `discovery` sits above `pipeline`, and `queries` is lowest. c7 forbids `discovery` and `pipeline` other owners' repositories.
- **Persistence patterns.**
  - `db/models/setup.py` (`SegmentRecord`: `ulid_pk()`, `UtcDateTime`, `CheckConstraint(name=...)`);
  - `db/models/jobqueue.py:40-60` (a unique `Index`, the CHECK listing statuses);
  - `db/models/base.py` `NAMING_CONVENTION` (`fk_%(table)s_%(cols)s_%(referred)s`);
  - `db/models/__init__.py`, which must import the new module;
  - `db/alembic/versions/0007_2_1_segments.py` (header and `downgrade`);
  - `db/repos/setup/segments.py` (the repository style).

  Migrations run with foreign keys off (`db/engine.py` `fk_off_connection`), and the drift test (`compare_metadata`) must stay empty.
- **Owners and PII.** `db/owners.py` `TABLES`; `db/pii_registry.py`.
- **The event log.** `db/eventlog.py` `append(entity, entity_id, from_status, to_status, cause, cause_ref, actor, at)`. `system:pipeline` is a valid actor.
- **Reading Segments.** `nextron.app.queries.setup.segments()` → `SeededSegment(id, key, version, definition)`.
- **Tests and helpers.**
  - `tests/conftest.py:102-117` `db_engine` (migrated temp DB). Build units as `UnitOfWorkFactory(db_engine, main.build_subscribers(clock=FrozenClock(...)))`.
  - `tests/fakes/clock.py` `FrozenClock`.
  - `tests/app/test_notify.py` shows how a test drives a `@command` from a test `@use_case` and `transaction()`.
  - Seed a Segment with `Setup(clock).sync_segments(...)` on the shipped file, or with a raw insert.
- **Contract files to extend.**
  - `tests/contracts/test_vendored_assets.py`: add the PSL's SHA-256, plus a test that `psl_version()` is the recorded version.
  - `tests/contracts/test_source_tree.py` `FILES`: add the PSL file and the new modules, the migration and the repositories, as 2.1 did.
- **Docs.** `nextron-engine/README.md`: a new "Leads and identity" section after "Seeds" (381).

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/domain/psl/public_suffix_list.dat`, `nextron-engine/.gitattributes` -- copy the downloaded file byte-for-byte; mark `-text` -- the vendored PSL.
- [x] `nextron-engine/nextron/domain/identity.py` -- the normalisers, `IdentityError`, `COUNTRY_CODES` and `psl_version` -- the only normaliser (SC-8).
- [x] `nextron-engine/nextron/domain/leads.py`, `nextron/domain/pipeline_stage.py` -- `LeadSource`, `LeadIdentity`, `InvalidLeadIdentity`; the machine.
- [x] `nextron-engine/nextron/db/models/leads.py`, `db/models/__init__.py`, `db/alembic/versions/0008_2_2_leads.py`, `db/owners.py`, `db/pii_registry.py`, `db/repos/discovery/{__init__,leads}.py`, `db/repos/pipeline/{__init__,stages}.py` -- the table and its repositories.
- [x] `nextron-engine/nextron/app/pipeline/api.py`, `app/discovery/api.py`, `app/queries/discovery.py`, `app/queries/pipeline.py`, `nextron/main.py` -- the commands, queries and subscriber registration.
- [x] `nextron-engine/tests/unit/test_identity.py` -- the Identity rows of the I/O matrix:
  - each refusal, and that the message never contains the input;
  - PSL wildcard, exception and private rules, an unknown TLD, and the 253/63 limits;
  - `COUNTRY_CODES` equals the codes in tzdata's `iso3166.tab` (read through `importlib.resources`);
  - every normaliser is idempotent on its own output.
- [x] `nextron-engine/tests/unit/test_pipeline_stage.py`, `tests/unit/test_leads_domain.py`:
  - every (stage, event) pair is its table row or raises;
  - `CREATION`, `reopen` and `merged()` on every pair of stages, per decision 2;
  - each `LeadIdentity` refusal names its field.
- [x] `nextron-engine/tests/app/test_leads.py` -- real units on a temp DB with a frozen clock:
  - every app row of the I/O matrix, with `events` rows asserted;
  - `upsert_lead` refusing an unknown Segment and wrong types (transaction rolled back, nothing written);
  - the queries;
  - the CHECKs, unique indexes and FKs, raising `IntegrityError` on a raw write.
- [x] Migration and contract tests in the Code Map; `nextron-engine/README.md` -- the section.

**Acceptance Criteria:**
- Given the full suite, when `uv run pytest` and `uv run lint-imports` run through the suite lock, then everything passes and all 29 contracts are kept.
- Given `grep` over `nextron/`, when it looks for `registrable_domain`, `normalize_email`, `idna` or `public_suffix` outside `domain/identity.py`, then only callers and tests appear, never a second normaliser.
- Given the persistence contracts, when they run, then `leads` is fully mapped and classified, and the migration matches the model.

## Implementation Notes

Implemented as specified; the choices below fill gaps the spec leaves open, all on the conservative side.

- **Identity (`nextron/domain/identity.py`).**
  - Labels split on the four IDNA 2003 separators (`.`, U+3002, U+FF0E, U+FF61), as `encodings.idna` does, so `example。com` is `example.com`. "Drop one trailing dot" drops one trailing separator.
  - The IP check runs on the hostname before and after the trailing-dot drop.
  - **Idempotency refinement.** A registrable domain whose own first label is `www` (`a.www.co.uk` would give `www.co.uk`; `x.www.ck` would give `www.ck`) is refused. Otherwise `registrable_domain` would return a name it refuses on a second call (step 5 strips `www.`), which `LeadIdentity` would then reject. Decision 7 already treats `www.co.uk` as no organization.
  - `normalize_company_number` and `normalize_country_code` refuse non-ASCII text before uppercasing, so `sc1234ﬀ` can't become `SC1234FF` and `gı` can't become `GI`.
  - `normalize_linkedin_url` also requires every host label to be letters, digits and hyphens, so `a..linkedin.com` is refused.
  - The PSL parse checks that the file has a `VERSION` line and exactly the ICANN and private sections, in order. All 10,455 rules encode, giving 10,447 rules and 8 exception rules. A brute-force check over every code point, kept out of the suite, found no input for which `registrable_domain` or `normalize_linkedin_url` is not idempotent. It tried single characters only, so it missed the letter-plus-mark case that P3 fixes; after P3 and P4 it was re-run for `normalize_name` and `normalize_email` with every letter followed by twelve combining marks, and found none.
  - `nextron/domain/__init__.py`'s docstring now notes that `identity` reads its vendored PSL once, like `zoneinfo` reads `tzdata`.
- **Leads domain.**
  - `MergeRefused` lives in `nextron/domain/leads.py` beside `InvalidLeadIdentity`.
  - The "at least one key" refusal names the field `keys`.
  - `LeadIdentity.keys()` returns the keys that are set, in `KEY_FIELDS` order.
  - A key with a non-`str` value is refused as `InvalidLeadIdentity(field, "must be a str or None")`.
  - An `IdentityError` from a key's normaliser becomes `InvalidLeadIdentity(field, <its reason>)`; neither message ever holds the value.
- **Machine.**
  - `transition` takes the Job machine's shape.
  - `reopen` and `merged` turn stage text into `PipelineStage`. Anything that is no stage raises `IllegalTransition` (`reopen` or `merge`).
  - `CREATION` is built from `LeadSource`, so a new source fails at import unless `StageCause` has its `created_from_<source>` member.
- **Persistence.**
  - The model builds `ck_leads_pipeline_stage` from `PipelineStage`, and the migration hard-codes the same SQL; `test_job_contracts` compares them.
  - The unique indexes are explicit `Index(..., unique=True)`, so they appear in `sqlite_master` as `uq_leads_*`.
- **Services.**
  - `Pipeline.record_created` raises `IllegalTransition(pipeline_stage, created_from_<source>, <stored>)` for a stored stage other than `new`, an unknown Lead included (`None`).
  - `reparent_lead` raises `LookupError` if either Lead has no row; `merge` makes that impossible.
  - `Discovery.merge` treats a non-canonical id (a lowercase ULID, say) as an unknown Lead, so a case-variant can't slip past the same-id check. Non-`str` ids raise `TypeError`.
  - The merge-chain `RuntimeError` names the Lead the lookup started from.
- **Tests outside the new files.**
  - `test_persistence_contracts`' coverage probe used `leads` as its dummy table; it is now `probe`.
  - `test_eventlog`'s bad-entity probe was `leads` (now a real table); it is now `no_such_table`.
- **Logged in `deferred-work.md`:** decision 6's Enrollment refusal, for Story 4.8.

**Review fixes (pass 1)**

- **P1 Deviation characters.** `_ascii_label` refuses a label holding an IDNA 2008 deviation character (`DEVIATION_CHARACTERS`: U+00DF, U+1E9E, U+03C2, U+200C, U+200D) before `ToASCII`. This covers website domains and email domains alike: IDNA 2003 would map `straße.de` to `strasse.de`, which is another registered domain. Email local parts keep `ß`. The message never echoes the input. Covered by tests, the module docstring and the README table.
- **P2 Format characters.** `normalize_email` and the LinkedIn slug now refuse `Cf` characters (zero-width space, soft hyphen, word joiner, BOM, bidi controls) alongside whitespace and `Cc`. Covered by tests, the docstring and the README.
- **P3 NFC after lowercasing.** `normalize_email` and the LinkedIn slug now NFC, lowercase, then NFC again (`_nfc_lower`). `J` + U+030C now gives `ǰ` (U+01F0), which is NFC and idempotent; a test for each normaliser asserts this.
- **P4 Combining marks.** `normalize_name` treats category `M*` as a word character, like `isalnum()`, and applies NFKC again at the end. `J̌ane` becomes `ǰane`, not `j ane`, and Devanagari `प्रिया` stays one word. Tests cover both examples and idempotency; the docstring and README rule are updated.
- **P5 Survivor re-read.** New test `test_a_key_moved_by_a_merge_beats_the_candidates_value`: a key the merge moved to the survivor wins over the candidate's conflicting value. It fails when the re-read after merging is replaced by the stale row.
- **P6 Tie-break test.** `test_a_tie_on_created_at_keeps_the_smaller_id` now gives the larger-id Lead the domain (the key looked up first) and the smaller-id Lead the place ID, both at the same frozen time. It fails if the sort key drops `id`.
- **P7 Parser tests.** The parsing body moved into `_parse_suffix_list(text)`, which `_suffix_list()` calls on the vendored text. Small inline lists test each refusal: a rule outside the sections (before and between them), no `VERSION` line, and sections missing, out of order or repeated.

**Final verification (after pass 1).**
- **`uv run pytest -q` through the suite lock (track-B):** 5,566 passed and 2 skipped (the `ai_eval` test and the symlink test), in 131 s.
  - The first run after the patches had one failure, `tests/unit/test_job_runner.py::test_a_second_start_raises_and_stop_is_idempotent` (a realtime thread outlived the shutdown grace). That run took 322 s because another track's suite was loading the machine.
  - Run alone, the same file then failed only the known flaky `test_start_runs_one_realtime_one_interactive_the_bulk_threads_and_the_keeper`.
  - This story changes neither the runner nor its tests, so the suite was re-run once and passed.
- **`uv run lint-imports`:** 29 kept, 0 broken.
- **The spec's verify command** prints `smith-law.co.uk`.
- **Before the review fixes:** 5,527 passed, 2 skipped, and 29 kept.

Not verified here: a built wheel carrying `psl/public_suffix_list.dat`. The engine runs from its source tree.

## Spec Change Log

## Review Triage Log

**Pass 1 (2026-11-17).**
- **Sources:** BH = blind hunter (13 findings), EC = edge-case hunter (13 findings and 1 claim), VG = verification gap (3 gaps and 1 other finding).
- **Counts:** 31 raw findings. BH9, BH12 and EC9 each make two separate claims, so there are 34 claims, which make 26 entries after deduplication.
- **Outcomes:**
  - 7 entries patched (P1–P7): 4 code fixes and 3 test gaps;
  - 4 deferred (A, F, J, Y);
  - 15 rejected: 2 as false.
- **No loopback.** No finding is an intent gap or a spec defect that needs re-derivation.
- **Rejected only because the fix would change the approved spec:**
  - D (decision 11: no merge audit row or unmerge);
  - E (decisions 4 and 5: a key that conflicts with the survivor's is dropped, so a later candidate holding only that key creates a second Lead);
  - G (decision 2: a closed loser beats a `won` survivor);
  - U (decision 9: a sole trader's firm name is classified `none`).

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| A | BH1, EC1 | A website on a shared platform gives the platform's domain as the key: `sites.google.com/view/x` → `google.com`, `facebook.com/X` → `facebook.com`, `x.business.site` → `business.site`, `linktr.ee/x` → `linktr.ee`. Unrelated firms then share one Lead, and a candidate that also brings its own place ID merges them, which can't be undone. | medium | Verified against the vendored list. No caller exists yet. The epics give the "is this the organization's own website" check to the callers: Story 2.3 refuses denylisted websites, Story 2.16 accepts only non-denylisted domains, and Story 8.3 counts "no own website". None of those lists names these platforms yet. | defer |
| B | BH2, EC6 | IDNA 2003 maps the IDNA 2008 deviation characters: `straße.de` → `strasse.de`, `info@straße.de` → `info@strasse.de`. `ς` becomes `σ`, and ZWJ/ZWNJ are dropped. These are different registered domains, so the wrong Lead matches and a stored address points at someone else's domain. | medium | Verified: `registrable_domain('https://straße.de')` returns `strasse.de`. The fix is direct: refuse a label holding ß, ẞ, ς, U+200C or U+200D before `ToASCII`, in domains and email domains. | P1 |
| C | BH3 | Placeholder keys pass: `normalize_company_number("0")` → `00000000`, and `normalize_place_id("none")`. | low | True, but company numbers come only from Companies House (Stories 2.12 and 8.4) and place IDs only from the Places API (8.3). No CSV column carries either. Refusing them adds new rules. | reject |
| D | BH4 | Merges leave no `merged_at`, no record of the key or source that caused them, and no unmerge. | low | Decision 11 (frozen) makes `merged_into` the record. A column, a record table or an unmerge is new surface. | reject |
| E | BH5, EC2 | A candidate key that conflicts with the matched Lead's own value is dropped silently, and `UpsertedLead` doesn't report it. A later candidate holding only that key creates a second Lead. | medium | Verified (`test_an_existing_leads_name_segment_and_country_never_change` asserts the value is on no row). Decisions 4 and 5 (frozen) chose this, because one column can't hold two values. Storing alias keys, or returning dropped fields, changes the approved spec. Listed for Mac. | reject |
| F | BH6 | A merged Lead keeps its own stage and identity. `queries.pipeline.stage(loser)` and `queries.discovery.lead(loser)` read it as a live Lead, and there is no `survivor_of` query, so a job or page holding the loser's id would act on it. | low | True by design: the loser row stays (AD-3 review H-41). Nothing holds a Lead id yet. The first consumers must resolve `merged_into`: Story 2.4's page redirect (already in its AC) and Story 2.5's job chain. | defer |
| G | BH7 | `merged(won, lost)` gives `lost`, and `merged(disqualified, won)` keeps `disqualified`, so a paying client can be closed by an old duplicate. | low | Verified. Decision 2 (frozen) makes a closed stage win so a merge never re-opens contact. Giving `won` its own rule changes the approved spec. Listed for Mac. | reject |
| H | BH8 | The table raises on routine late events (`send` at `replied`), which contradicts AD-4's no-op `advance_to_at_least`; and the "every move" claim is wrong. | false | AD-4's no-op cases (at or past, closed, parked) are decided by the command before any transition, and are not moves. Every forward move that `advance_to_at_least` makes from an earlier open stage has a row: `enroll`, `send`, `reply`, `hot_reply`, and `recontact` from `not_now`. | reject |
| I-a | BH9 (part), EC9 (part) | `normalize_email` and the LinkedIn slug keep `Cf` characters (`jane​@x.com` passes), so one person gets two keys. | low | Verified. Addresses copied from web pages often carry a zero-width space or soft hyphen, so users would meet it. The fix is a direct extension of the existing whitespace/`Cc` refusal, matching `LeadIdentity.name`'s rule. | P2 |
| I-b | BH9 (part) | `normalize_email` accepts `,`, `;`, `<` and `"` in the local part. | low | A normaliser is not a syntax validator. Story 2.3 checks syntax ("a row whose email fails syntax"), using email-validator in the adapter layer. Adding RFC 5322 validation here duplicates it. | reject |
| J | BH10 | `upsert_lead` decodes every stored Segment on every call. | medium | Measured: about 4 ms per call, so a 5,000-row import in one transaction holds `BEGIN IMMEDIATE` for about 20 s, well past other writers' 5-s `busy_timeout`. No caller exists yet. Story 2.3, the first bulk caller (next on this track), should add an id-only `queries.setup.segment_ids()` (or batch its transactions). | defer |
| K | BH11, EC12 | `connect_database` and doctor's read-only units call `build_subscribers()` without the injected clock, so `reparent_lead` stamps `SystemClock` time. | low | In production every service uses the one `SystemClock`. Only a test that injects a clock into `Discovery` but builds units through `connect_database` would see the difference, and the merge tests build the registry with the clock. Threading a parameter adds surface. | reject |
| L | BH12 | Story 2.3's `UK` → `GB` alias conflicts with "no other module normalises", and `LeadIdentity` checks `COUNTRY_CODES` instead of calling `normalize_country_code`. | false | Story 2.3 adds the alias inside `identity.py`, which SC-8 allows. `LeadIdentity` requires the normalised form, and the normaliser's output is always in `COUNTRY_CODES`, so an alias changes nothing there. | reject |
| M | BH13 | No index on `merged_into`, `segment_id` or `pipeline_stage`. | low | No query reads by them yet, and Segments are never deleted. A later `CREATE INDEX` needs no table rebuild. | reject |
| N | EC3, EC4, EC14 | `normalize_email` and the LinkedIn slug lowercase after NFC, so `J̌ane@example.com` returns the non-NFC `ǰane@…`, and a second call returns `ǰane@…`. The output is not NFC and the normaliser is not idempotent. | low | Verified. It breaks the AC's "NFC-normalising" and the module's idempotency claim. The fix is direct: apply NFC after lowercasing. | P3 |
| O | EC5 | `http://evil.com\@example.com` keys as `example.com`, while browsers go to `evil.com`. | low | Verified. It needs crafted input, and the stored key is what the crawler later fetches, so the two stay consistent. Refusing a backslash is a new guard. | reject |
| P | EC7 | `normalize_name` turns combining marks (`Mn`/`Mc`) into spaces: `J̌ane` → `j ane`, and Devanagari vowel signs split the name, so distinct names can collapse. | low | Verified (`प्रिया` → `प र य`). The fix is direct: count combining marks as word characters, then NFKC again so the result stays idempotent. | P4 |
| Q | EC8 | A non-ASCII local part of 64 characters can exceed 64 octets. | low | Deliverability is a syntax check for the validator (I-b). The normaliser's limit is in characters by design. | reject |
| R | EC9 (part) | A LinkedIn slug of `.` or `..` is accepted. | low | It's never a real profile, and only an operator-typed or CSV URL could carry it. A new guard. | reject |
| S | EC10 | A lone surrogate passes `LeadIdentity.name`, then the insert raises `UnicodeEncodeError`. | low | Every source decodes strict UTF-8 (Story 2.3's AC: a UTF-8 CSV), so no caller can produce one. A new guard. | reject |
| T | EC11 | `record_created` with a non-ULID raises `StatementError`, not `IllegalTransition`. | low | Only `upsert_lead` calls it, always with a fresh `new_id()`. | reject |
| U | EC13 | A sole trader's firm name is the person's name, and erasure leaves it in `leads.name`. | low | Decision 9 (frozen) classifies Lead columns as firm data. Story 3.14 owns erasure scope. Listed for Mac. | reject |
| V | VG1 | The survivor re-read after the merges in `upsert_lead` is unverified: with `kept = keep`, a key moved by a merge is overwritten by the candidate's value, and no test fails. | low | Pre-verified by mutation. Add the test the gap names. | P5 |
| W | VG2 | `test_a_tie_on_created_at_keeps_the_smaller_id` can't see a regression in the upsert's tie-break, because the smaller id also holds the first-looked-up key. | low | Pre-verified by mutation. Swap the keys so the larger id holds `web_domain`. | P6 |
| X | VG3 | The parser's three malformed-list `ValueError`s (a rule outside a section, no `VERSION`, wrong sections) have no test. | low | Pre-verified: removing two of them passes every test. The fix is direct: parse through a private `_parse_suffix_list(text)` and test each refusal. | P7 |
| Y | VG other | Nothing makes a later owner of Lead-scoped rows register its `reparent_lead`. | low | True. Today a contract test would pass trivially. Story 2.3 adds the first Lead-scoped table (`contacts`), so the contract belongs there: every table with a foreign key to `leads.id` has its owner's `reparent_lead` registered for `LeadsMerged`. | defer |

## Design Notes

**The transition table** (`StageEvent` → `to`, `StageCause`; "open<X" means every open stage before X):

| Event | From | To | Cause | Mover (later story) |
|---|---|---|---|---|
| `enroll` | new | queued | `enrolled` | `Enrolled` subscriber |
| `send` | new, queued | contacted | `message_sent` | `MessageSent` |
| `reply` | new, queued, contacted | replied | `reply_received` | replies |
| `hot_reply` | open<interested, not_now | interested | `hot_reply` | replies, Manual Reply |
| `park` | open<won | not_now | `not_now_reply` | `park_not_now` |
| `recontact` | not_now | queued | `recontact_due` | `recontact_due` |
| `book_call` | replied, interested, not_now | call_booked | `operator_move` | Operator (9.1) |
| `hold_call` | call_booked | call_held | `call_outcome` | `record_call_outcome` |
| `no_show` | call_held | call_booked | `call_outcome` | `record_call_outcome` |
| `propose_audit` | call_held | audit_proposed | `operator_move` | Operator |
| `win_audit` | call_held, audit_proposed | audit_won | `operator_move` | Operator |
| `propose_build` | call_held, audit_proposed, audit_won | build_proposed | `operator_move` | Operator |
| `win` | call_held … build_proposed | won | `operator_move` | Operator |
| `close_lost` | every open stage, not_now | lost | `closed_lost` | `close(lost)` |
| `close_disqualified` | every open stage, not_now | disqualified | `closed_disqualified` | `close(disqualified)` |

The other causes are `created_from_<source>` (five), `reopened` and `leads_merged`.

```python
@command
def upsert_lead(self, identity, source):          # Discovery
    found = {survivor(row) for row in (by_web_domain(...), by_ch_number(...), by_place_id(...)) if row}
    if not found:
        lead_id = leads.insert(..., created_at=self._clock.now())
        self._pipeline.record_created(lead_id, source)
        return UpsertedLead(lead_id, True, ())
    keep, *others = sorted(found, key=lambda r: (r.created_at, r.id))
    for other in others:
        self.merge(keep.id, other.id)
    ...attach unheld keys keep lacks...
```

## Verification

**Commands:**
- `cd nextron-engine; uv run pytest -q` -- expected: all pass. Run it through the suite lock.
- `uv run lint-imports` -- expected: 29 contracts KEPT.
- `uv run python -c "from nextron.domain.identity import registrable_domain as r; print(r('https://www.Smith-Law.co.uk/team'))"` -- expected: `smith-law.co.uk`.
