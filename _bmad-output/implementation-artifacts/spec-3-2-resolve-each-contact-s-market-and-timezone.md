---
title: "Story 3.2: Resolve each Contact's Market and timezone"
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: 'f1abece09875451cec947eb66db0b0e3d2d7b384'
review_loop_iteration: 0
story_key: '3-2-resolve-each-contact-s-market-and-timezone'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Every Contact's `market_id` and `tz` are NULL (Story 2.3 created them empty). Without a per-person Market, UK and blocked-country rules can only apply to the company, and AD-11 forbids enrolling a Contact with no timezone. The CSV import also accepts rows from countries with no enabled Market (CA, DE).

Requirements: FR-2, FR-51, FR-59 · AD-3, AD-7, AD-10, AD-11, AD-17 · NFR-5 · UX-DR20.

**Approach:**
- A stdlib domain resolver (`nextron/domain/contact_market.py`) decides a Contact's Market, timezone, both bases and the Provenance notes, from:
  - its address;
  - its office location (new, stored on the Contact);
  - its Lead's country;
  - the Lead's headquarters timezone (always `None` until Story 2.9);
  - the stored Markets.
- A vendored US state table (`nextron/domain/geodata/us_state_timezones.csv`, read by `nextron/domain/geo.py`) maps each state to its IANA zone.
- **`research` is the only writer** and resolves in the same transaction as every Contact write that exists today: `add_contact` (the CSV import) and `reparent_lead` (a Lead merge). It also exposes one command, `resolve_market(contact_id)`, for the later paths.
- **Migration `0016_3_2_contact_markets`** adds `market_basis`, `tz_basis`, `office_country` and `office_region` to `contacts`.
- **The CSV import** blocks rows whose country has no enabled Market, and reports them.
- **A one-off `resolve_contact_markets` bulk job,** queued at engine start, resolves the Contacts that existed before this story.

The acceptance criteria are Story 3.2 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**

- **Geography** (new `nextron/domain/geo.py`, stdlib and `nextron.domain` only).
  - **The vendored table.** `geodata/us_state_timezones.csv` is read once (`functools.cache`, through `importlib.resources`, as `identity.py` reads the PSL). Its format:
    - lines starting `#` and blank lines are skipped;
    - the first other line is exactly `code,timezone,name`;
    - then exactly the 51 rows of Design Notes: `code` fullmatches `[A-Z]{2}` and is unique, `timezone` is in `settings_schema.timezone_names()`, and `name` is non-blank.

    A malformed file raises `ValueError` on first use.
  - **`US_STATE_ZONES`** (via `us_state_zones() -> Mapping[str, str]`, read-only) maps code to zone. `COUNTRY_ZONES = {"GB": "Europe/London"}` (read-only).
  - **`location_zone(country_code, region) -> str | None`:**
    - `GB` gives `Europe/London`;
    - `US` with a region gives that state's zone;
    - anything else gives `None`, `US` without a region included.
  - **`zone_country(zone) -> str | None`:** `GB` for `Europe/London`, `US` for any zone in the US table, else `None`.
  - **`Location(country_code, region=None)`** is frozen and slotted, checked in `__post_init__`. A problem raises `InvalidLocation(field, reason)`: a `DomainError` with code `invalid_location`, whose message names the field and never the value. The rules:
    - `country_code` is a `str` equal to `normalize_country_code(country_code)`;
    - `region` is `None`, or (only when the country is `US`) a key of the US table.
- **Contacts domain** (`nextron/domain/contacts.py`). `ContactCandidate` gains a last field, `office: Location | None = None`. Anything else raises `InvalidContact("office", "must be a Location or None")`. Every 2.3 rule stays.
- **The resolver** (new `nextron/domain/contact_market.py`, stdlib and `nextron.domain` only).
  - **Types.**
    - `MarketBasis` (`StrEnum`): `office_location`, `lead_country`, `conflict_stricter`, `cctld_outside_markets`, `location_outside_markets`, `location_unknown`.
    - `TzBasis` (`StrEnum`): `office_location`, `lead_hq`, `market_default`, `us_unknown_fallback`, `no_market`.
    - `ContactMarket(market: MarketDef | None, market_basis, tz: str | None, tz_basis, notes: tuple[str, ...])`, frozen. The invariant: `market is None` ⇔ `tz is None` ⇔ `tz_basis is no_market`.
    - `RESOLVE_CONTACT_MARKETS_JOB = "resolve_contact_markets"`.
  - **`resolve_contact_market(*, email, office, lead_country, lead_hq_tz, markets) -> ContactMarket`.** It is pure. "Enabled" means `market_for_country(code, markets)` isn't `None`.

    **The Market:**
    1. **The place.** The place is `office.country_code` (`place_basis = office_location`) when there is an office; otherwise it is `lead_country` (`lead_country`).
    2. **ccTLD.** `domain_country = cctld_country(email)` when there is an email. If it is set and not enabled, the result is no Market, `cctld_outside_markets`. This step comes first, so it applies whatever the place.
    3. **No place.** No place gives no Market, `location_unknown`.
    4. **Place outside.** A place that isn't enabled gives no Market, `location_outside_markets`.
    5. **Conflict.** A `domain_country` whose Market differs from the place's Market gives `conflict_stricter`.
       - The stricter Market applies: the one with more of these four true: not `cold_email_allowed`, `free_webmail_blocked`, `privacy_line_required`, `eligible_legal_forms is not None`.
       - A tie gives no Market.
    6. **Otherwise** the place's Market applies, with `place_basis`.

    **The timezone** is resolved only when a Market applies; otherwise `tz` is `None` and `tz_basis` is `no_market`. It uses the **place's** Market, which in a conflict is the place's own Market, not the stricter one:
    1. **Office.** With an office and `location_zone(office)` set: that zone, `office_location`.
    2. **Headquarters.** With `lead_hq_tz` set and `zone_country(lead_hq_tz)` equal to the place: that zone, `lead_hq`.
    3. **Unknown-location window.** When the place's Market has an `unknown_send_window`: its `unknown_timezone`, `us_unknown_fallback`.
    4. **Otherwise** its `default_timezone`, `market_default`.
  - **Notes.** `notes` holds, in order, the Market note, then the timezone note, each only where Design Notes gives one. The note texts are fixed and hold codes, zone names and Market names only, never an address or a person.
- **Persistence.**
  - **Migration `0016_3_2_contact_markets`** (down `0015_2_3_contacts_provenance`; `downgrade()` raises like 0015). It rebuilds `contacts` with `op.batch_alter_table("contacts", recreate="always", copy_from=<0015's exact contacts Table>)`, never relying on reflection. The new columns, all nullable, go after `market_id`:
    - `market_basis` `String(32)`;
    - `tz_basis` `String(32)`;
    - `office_country` `String(2)`;
    - `office_region` `String(2)`.

    It adds these CHECKs, with every 0015 CHECK, the FKs and `ix_contacts_lead_id` kept:
    - `ck_contacts_market_basis` and `ck_contacts_tz_basis`: NULL or one of the enum values, built from the enums;
    - `ck_contacts_resolved`: `(market_basis IS NULL) = (tz_basis IS NULL)`;
    - `ck_contacts_market_tz`: `(market_id IS NULL) = (tz IS NULL)`;
    - `ck_contacts_office_country`: NULL or `GLOB '[A-Z][A-Z]'`;
    - `ck_contacts_office_region`: NULL or (`office_country IS NOT NULL` and `GLOB '[A-Z][A-Z]'`).

    Existing rows keep every value, with the new columns NULL.
  - **The model.** `ContactRecord` matches the migration, column order included.
  - **`owners.py`:** every new column → `research`.
  - **`pii_registry.py`:** `office_country` and `office_region` are `null`; the bases are `none`.
  - **Repositories:**
    - `contacts`:
      - `StoredContact` gains `market_basis`, `tz_basis`, `office_country` and `office_region` after `market_id`;
      - `insert(...)` takes `office_country` and `office_region`;
      - `unresolved_ids(after, limit)` returns ids with `market_basis IS NULL`, `id > after`, by id;
      - `has_unresolved()`.
    - `provenance`:
      - `first_contact_record(contact_id)`: the first `contact`-subject record by `retrieved_at`, then `id`;
      - `set_notes(record_id, notes)`.
  - Bump `HEAD` to `0016_3_2_contact_markets` in `test_persistence_contracts.py` (chain probes `0016_*` → `0017_*`, "revision 16" → "revision 17"), in `test_migrations.py` and in the `test_serve_process.py` assert. Add every new file to `test_source_tree` `FILES`.
- **Research** (`app/research/api.py`).
  - **The resolution, `_resolve`.** It is one internal routine; every caller below goes through it.
    1. It reads the Contact, its Lead (`queries.discovery.lead`), and the Markets (`queries.setup.markets()`).
    2. It calls `resolve_contact_market` with the stored email, the stored office (`Location` or `None`), the Lead's `country_code` and `lead_hq_tz=None` (Story 2.9 passes the Lead's HQ zone).
    3. It writes `market_id` (the chosen Market's row id), `market_basis`, `tz` and `tz_basis` through `contacts_repo.fill` (with `updated_at` = now) **only when one of them differs**.
    4. It sets the notes (`"\n".join(notes)`, or NULL when there are none) on the Contact's first `contact` record **only when they differ**.
  - **`add_contact`:**
    - a new Contact stores the candidate's office, then is resolved;
    - on a match, an office the Contact lacks is filled like `name`, and counts as filled text for its `contact` record;
    - after the writes, the Contact is resolved whenever it was created or its email or office was filled.

    Every 2.3 behaviour and `AddedContact` stay unchanged.
  - **`reparent_lead`** resolves each moved Contact after the move, because the survivor's country may differ.
  - **`@command resolve_market(contact_id)`.** For later paths that change an address or office location outside `add_contact`. An unknown Contact raises `LookupError`.
  - **`ContactMarkets(clock, research, queue)`** (same module):
    - **`@use_case queue_backfill() -> bool`.** In one transaction, when `has_unresolved()`, it calls `queue.enqueue("resolve_contact_markets", scope="global", discriminator="1", payload={"v": 1})` (the live key absorbs repeats). It returns whether a job is now queued. It logs `contact_markets_queued`.
    - **`@use_case backfill() -> int`.**
      - When no Market is stored, it returns 0 and writes nothing, so the next start queues it again.
      - Otherwise it walks `unresolved_ids(after, 200)` with an id cursor, one `uow.transaction()` per batch, resolving each Contact.
      - It returns the count and logs `contact_markets_resolved` (count only).
- **Jobs.**
  - **The handler.** `handlers.resolve_contact_markets(markets)` calls `markets().backfill()`. A `MarketBackfilling` Protocol types it.
  - **The job type.** `build_job_types` gains `contact_markets=`, and registers the type with these settings:
    - lanes: `BULK` only;
    - priority 40;
    - `max_runtime` 30 min;
    - `retry_limit` 3;
    - `group=JobGroup.OTHER`.
  - **No trigger.** It is a one-off, and AD-10's list is closed.
  - **Engine start.** `engine_lifespan(runner, scheduler, writer, *, startup=None)` runs `startup` through `asyncio.to_thread` **before** `runner.start`.
    - A `startup` exception is logged (`startup_step_failed`, ERROR, with the exception's type) and never stops the engine.
    - `build_app` passes a step that runs `ContactMarkets.queue_backfill()` in its own unit of work.
- **The CSV import.**
  - **The planner.** `plan_import(..., enabled_countries: frozenset[str])` is a new required keyword. Right after the country check (2.3's step 7), a country outside `enabled_countries` makes the row a `BlockedRow(number, label, country)` whose `reason` is `f"Country {country} isn't an enabled Market"`. Such a row isn't rejected and is never written. `ImportPlan` gains `blocked`.
  - **The use case.** `LeadImports.run` reads `queries.setup.markets()` in the same pre-plan transaction as the Segments, and passes the enabled Markets' country codes.
    - `ImportSummary` gains `blocked` (row order, every blocked row of the plan, as with `rejected`).
    - `lead_import_finished` logs `blocked` too.
  - **The page** (`lead_import_form.html`):
    - the counts line ends with "Blocked by Market: N.";
    - a "Rows blocked by Market" table lists the row number, the label (`untrusted_block`) and the reason.
- Tests run as `test` with temporary DBs, with no network and no keyring. Domain tests import only the stdlib, pytest and `nextron.domain`. The docs go in the README's "CSV import, Contacts and Provenance" section, plus a new "Contact Markets and timezones" subsection, the migrations row and the Jobs list.

**Never:**
- No Eligibility, no `assert_contactable`, no Send Window logic, and no `enroll` change (Stories 3.4, 3.6, 4.x and 6.x).
- No `hq_tz` or Evidence (Story 2.9), no CSV office-location columns, and no Lead-level Market column.
- No Contact path beyond today's (crawl 2.5/2.10, finder 2.13, Signal Inbox 2.16).
- Nothing new besides this story's own: no new dependency, trigger, setting, route or credential.
- No writing of `contacts` or `provenance_records` outside `research`.
- Never log an address, a name, a note or a location.
- No editing outside `nextron-engine/`, except this spec, `sprint-status.yaml` and `deferred-work.md`.

### Decisions made overnight (pending user review)

1. **The office location is stored on the Contact** (`office_country`, `office_region`), so that a re-resolution and the backfill see it. `ContactCandidate.office` is filled once, never overwritten, like a name. No path supplies it today: the CSV has no such column, so CSV Contacts resolve from the Lead's country (AC 2). Story 2.10 passes it from team pages.
2. **Two bases were added for completeness:**
   - `location_unknown`, when neither an office location nor a Lead country is known (blocked);
   - `no_market`, the timezone basis of a Contact without a Market (`tz` NULL). This way every Contact gets both bases, as the backfill AC asks.
3. **The Lead's country stands in for an unknown office location** in every rule: the conflict rule and the "both must be enabled" rule included. A blocking ccTLD wins over a blocking location.
4. **"Stricter" means more restrictive rules** (the four flags above), so UK beats US. A tie between two enabled Markets blocks the Contact, the compliance-safe side. With today's seed it can't happen.
5. **The timezone follows the place, not the stricter Market.** A US office with a `.co.uk` address is a UK-Market Contact whose time is American: `America/New_York` with `us_unknown_fallback` when the state is unknown. Sending (6.x) must take the narrowed window from the US Market.
6. **`us_unknown_fallback` applies to a Market that declares an `unknown_send_window`** (only the US), and `market_default` applies otherwise.
7. **Deferral (Story 2.9).** The Lead-headquarters step is built in the domain resolver (`lead_hq_tz`, used only when its zone is in the place's country) but is always `None` in `research`. Until Story 2.9 adds `hq_tz` and passes it, a Contact with no office zone falls back to the Market default. Story 2.9 must also call `resolve_market` for the Lead's Contacts when `hq_tz` changes.
8. **The notes live on the Contact's first `contact` Provenance Record.** They are replaced whenever the resolution's notes change, so they always describe the current Market and timezone. Blocking bases get a note too, for Story 3.7's display.
9. **The one-off job.**
   - It is queued at each engine start while any Contact is unresolved, and the live idempotency key absorbs repeats.
   - Its payload is `{"v": 1}`: no personal data and no ids.
   - It waits while no Market is stored.
   - Its group is Other.
10. **CSV.** The Market check comes right after the country check. "Enabled" is the Market's `enabled` flag. Blocked rows are counted apart from rejected rows. With no Market stored, every row is blocked, so `nextron seed sync` must run first.
11. **The US table** covers the 50 states and DC. A multi-zone state takes the zone where most of its people live. Territories have their own ISO codes, so they aren't US.
12. **Market edits after resolution don't re-resolve Contacts.** Enabling or disabling a Market through `seed sync` leaves stored Contacts as they are. Story 3.6 must check live that the Contact's Market is still enabled. This is logged in `deferred-work.md`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Office, generic TLD | office US/NY, `jane@firm.com`, Lead GB | US, `office_location`; `America/New_York`, `office_location`; no notes | N/A |
| Lead country | no office, `jane@firm.co.uk`, Lead GB | UK, `lead_country`; `Europe/London`, `market_default`; notes: the AC sentence, then the default note | N/A |
| US unknown state | no office, no email, Lead US | US, `lead_country`; `America/New_York`, `us_unknown_fallback`; two notes | N/A |
| ccTLD blocks | office GB, `a@firm.de`; then `a@firm.io` | no Market, `cctld_outside_markets`, `no_market` | N/A |
| Location blocks | office CA, `a@firm.com`; Lead CA with no office | no Market, `location_outside_markets` | N/A |
| Conflict | office US/TX, `a@firm.co.uk` | UK, `conflict_stricter`; `America/Chicago`, `office_location`; a conflict note | N/A |
| Unknown | no office, Lead country NULL | no Market, `location_unknown` | N/A |
| HQ (domain only) | no office, Lead US, `lead_hq_tz` `America/Denver`; then `Europe/London` | `lead_hq` Denver; then `us_unknown_fallback` (wrong country) | N/A |
| CSV blocked | rows with country `CA`, `de`, `GB` | CA and DE: `blocked` "Country CA isn't an enabled Market" (and DE); no Lead or Contact. GB is imported and resolved in its batch's transaction | N/A |
| Fill re-resolves | a CSV Contact with no email, then the same name with `a@firm.de` | the second write fills the email, and the Contact becomes blocked in the same transaction | N/A |
| Merge | Lead A (US, older) and B (GB) with a Contact; merge | the moved Contact becomes US, `lead_country`, with its notes replaced | N/A |
| Backfill | 3 Contacts from before this story (NULL bases); engine start | one `resolve_contact_markets` job (bulk, payload `{"v":1}`); after it, every Contact has both bases; a second start queues nothing | no Markets stored: nothing is resolved, and the next start re-queues |
| Bad input | `Location("CA", "ON")`, `Location("gb")`, `Location("US", "ZZ")`, `office="US"` | `InvalidLocation` / `InvalidContact("office", ...)` | raised |

</frozen-after-approval>

## Code Map

- **Patterns to reuse.**
  - `nextron/domain/identity.py`: `_suffix_list` (`resources.files(__package__).joinpath(...)`, `functools.cache`) and `normalize_country_code`.
  - `nextron/domain/markets.py`: `market_for_country`, `cctld_country`, `MarketDef`, `send_window_text` and `GB`/`US`.
  - `nextron/domain/settings_schema.py`: `timezone_names()`.
- **Research.**
  - `nextron/app/research/api.py`: `add_contact` (the match and fill logic, `TEXT_FIELDS`, `_record`) and `reparent_lead` (read the moved ids with `for_lead(merged)` before `move_to_lead`).
  - `nextron/db/repos/research/{contacts,provenance}.py`: `_COLUMNS` order feeds `StoredContact(*row)`, so keep the dataclass and `_COLUMNS` in step.
  - `nextron/app/queries/research.py`: re-export unchanged.
- **Queries.**
  - `nextron/app/queries/setup.py`: `markets()` gives `SeededMarket(id, code, version, definition)`; map the chosen `MarketDef.code` to `id`.
  - `nextron/app/queries/discovery.py`: `lead(id)` has `country_code`.
- **The migration.** `nextron/db/alembic/versions/0015_2_3_contacts_provenance.py` holds the exact `contacts` definition for `copy_from` (copy it into 0016; never import it). `env.py` runs with `foreign_keys` off, so the rebuild under `provenance_records`' FK is safe.
- **The model and its contracts.**
  - `db/models/research.py`: `ContactRecord`, with its CHECKs built from enums.
  - `tests/contracts/test_job_contracts.py`: `schema_differences` compares each `CONSTRAINT ck_… CHECK (...)` line of the model's and the database's `CREATE TABLE` SQL, which is why the rebuild is needed, not `ADD COLUMN`.
- **The CSV import.**
  - `nextron/domain/lead_import.py`: `_Planner.row` (the country check at about line 364), `ImportPlan`, `RejectedRow` and `plan_import`.
  - `nextron/app/discovery/api.py`: `LeadImports.run` (the pre-plan transaction, `ImportSummary`, the log).
  - `nextron/web/templates/lead_import_form.html`: the counts and the tables.
  - `nextron/web/lead_import_page.py`.
- **Jobs.**
  - `nextron/jobs/handlers.py` (the `restore_test` pattern).
  - `nextron/main.py`: `build_job_types`, `build_app` (where `queue` and the services are built), `engine_lifespan` and its docstring paragraph at about line 181.
  - `nextron/domain/job.py`: the key rules (discriminator `"1"` is a revision).
- **Tests to update (forced):**
  - **Every `build_job_types(...)` call** gains `contact_markets=`: `tests/app/test_{backups,budget,dead_job_alerts,engine_health,notify,restore_tests}.py` and `tests/contracts/test_{backup,job}_contracts.py`. Grep `build_job_types(` for any others. The "ships frozen" contract lists `resolve_contact_markets`, and `test_engine_health.py:293`'s Other group gains it.
  - **Fixtures that import CSVs seed Markets too**, through `Setup(clock).sync_seed(segments, markets)` instead of `sync_segments`: `tests/app/test_lead_import.py:178`, `tests/unit/test_lead_import_page.py:122` and `tests/app/test_leads.py` if a merge there needs it.
  - **`plan_import` callers** in `tests/unit/test_lead_import.py` pass `enabled_countries`.
  - **`tests/unit/test_runner_lifespan.py`:** the startup step runs before `runner.start`, and its failure is logged without stopping the start.
- **Vendored files.**
  - `tests/contracts/test_vendored_assets.py`: pin the CSV's SHA-256, with a docstring paragraph.
  - `nextron-engine/.gitattributes`: add `nextron/domain/geodata/** -text`.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/domain/geodata/us_state_timezones.csv`, `nextron/domain/geo.py`, `.gitattributes`, `tests/contracts/test_vendored_assets.py` -- the vendored table, `Location`, `InvalidLocation`, `location_zone` and `zone_country` -- the timezone source.
- [x] `nextron-engine/nextron/domain/contacts.py`, `nextron/domain/contact_market.py` -- `ContactCandidate.office`; the enums, `ContactMarket`, `resolve_contact_market`, the note texts and the job name -- the pure rules.
- [x] `nextron-engine/nextron/db/alembic/versions/0016_3_2_contact_markets.py`, `db/models/research.py`, `db/owners.py`, `db/pii_registry.py`, `db/repos/research/{contacts,provenance}.py` -- the columns, CHECKs and repository additions.
- [x] `nextron-engine/nextron/app/research/api.py` -- `_resolve`, the `add_contact`/`reparent_lead` calls, `resolve_market`, `ContactMarkets` and the docstring.
- [x] `nextron-engine/nextron/jobs/handlers.py`, `nextron/main.py` -- the handler, the job type, `engine_lifespan(startup=)`, the wiring in `build_app` and the docstrings.
- [x] `nextron-engine/nextron/domain/lead_import.py`, `app/discovery/api.py`, `web/templates/lead_import_form.html` (and `web/lead_import_page.py` if needed) -- the blocked rows.
- [x] Tests:
  - `tests/unit/test_geo.py`: the table (51 rows; NY, CA, TX, AZ, HI, DC), the malformed-file cases through the parser, `Location`'s rules, `location_zone` and `zone_country`;
  - `tests/unit/test_contact_market.py`: every matrix row at domain level, plus the tie (two hand-built enabled Markets of equal strictness), the exact note texts and the invariant over a parametrised grid;
  - `tests/app/test_contact_markets.py`: real units on a temp DB with the shipped seeds. It covers the app matrix rows (add_contact, fill, merge, `resolve_market`, backfill with and without Markets, `queue_backfill` once and absorbed), notes rewritten on change and untouched when unchanged, no write when nothing changed, the CHECKs as `IntegrityError` (each enum value accepted), and a 0015 → 0016 upgrade keeping rows with a clean `foreign_key_check`;
  - the import tests: the CSV blocked rows in `tests/unit/test_lead_import.py`, `tests/app/test_lead_import.py` and `tests/unit/test_lead_import_page.py` (the count and the table, escaped);
  - the job and lifespan tests listed in the Code Map.
- [x] Contract, migration and tree tests from the Code Map; `nextron-engine/README.md` (the docs named in Always).

**Acceptance Criteria:**
- Given the full suite, when it runs through the suite lock and `uv run lint-imports` runs, then everything passes and all 29 contracts are kept.
- Given the persistence contracts, when they run, then the new columns are mapped (`research`) and classified, the migration matches the model, and `schema_differences` is empty.
- Given `grep` over `nextron/`, when it looks for writes to `contacts` or `provenance_records`, then only `db/repos/research` holds them, and only `app/research` calls their writers.

## Implementation Notes

- **Geography.** `nextron/domain/geo.py` exposes `parse_us_state_zones(text)` (public, so tests reach the malformed cases), `us_state_zones()` (`functools.cache`, `MappingProxyType`), `COUNTRY_ZONES`, `location_zone`, `zone_country`, `Location` and `InvalidLocation`. `US_STATE_ZONES` is reached only through `us_state_zones()`, so importing the module reads no file. A parser error names the line number and never repeats the line. The table's header comment lists the 15 multi-zone states (NV included, for West Wendover). SHA-256 `33512378d16aed7fa1b4b1dfdff1163993ed790853f7787f5dd94ad502eee827`, pinned in `test_vendored_assets.py`.
- **Resolver.** `nextron/domain/contact_market.py` also exposes `strictness(market)` (the count of the four restrictive rules) and the note templates as `*_NOTE` constants. Notes are built only from codes, the email's last label, zone names, Market names and `send_window_text`. In a conflict the Markets are compared by `code`.
- **Migration `0016_3_2_contact_markets`** (down `0015_2_3_contacts_provenance`). It runs `batch_alter_table("contacts", recreate="always", copy_from=_contacts_0015())`: a hand-copied 0015 table in a plain `MetaData`, with `ix_contacts_lead_id` declared on it so the batch recreates it. It then runs four `add_column(..., insert_after=...)` calls and six `create_check_constraint(op.f(...))` calls. The CHECK texts are literal in the migration; the model builds them from the enums. `schema_differences` is empty.
- **Repositories.** `contacts.unresolved_ids(after, limit)` takes `after: str | None`, where `None` means from the first id. The `Ulid` column type refuses `""` as a bind value, so the backfill starts its cursor at `None`. `provenance.first_contact_record` filters on `ProvenanceSubject.CONTACT`.
- **Research.**
  - `_resolve` raises `LookupError` for an id that isn't a ULID too, since the `Ulid` type would otherwise raise `ValueError`.
  - `ContactMarkets.queue_backfill` logs `contact_markets_queued` with `new_job`. `created` is a reserved `LogRecord` attribute and raised `KeyError`.
  - `BACKFILL_BATCH = 200` is a module constant.
  - `build_app` now builds one `Research(clock)`, shared by `LeadImports` and `ContactMarkets`.
- **Engine start.** `main.queue_contact_markets(units, contact_markets)` is the startup step. It runs `queue_backfill()` in its own unit of work inside a fresh `contextvars.Context()`, like `check_usage`. `startup_step_failed` logs `error` (the type's name) with no `exc_info`.
- **CSV.**
  - `BlockedRow.reason` is a property over `REASON_BLOCKED`.
  - `plan_import` wraps `enabled_countries` in a `frozenset`.
  - The template shows the blocked table between the rejected and the noted tables.
- **Tests touched beyond the Code Map.**
  - `tests/contracts/test_health_contracts.py`: the group-proof now removes the first of two `group=JobGroup.OTHER,` lines.
  - `tests/unit/test_health_page.py`: the Other group lists `resolve_contact_markets`.
  - `tests/unit/test_serve.py`: the chain-extension probe is now `0017_more`.
  - `tests/dashboard.py`: builds `ContactMarkets` and passes `contact_markets=`.
  - The import fixtures seed Markets with `Setup.sync_markets` after `sync_segments`, since `sync_seed` also needs the Sequences.
- **`deferred-work.md`** gains decision 12 (Story 3.6 checks live that the Market is still enabled), decision 7 (Story 2.9 passes `hq_tz` and re-resolves) and review entry G (Epic 6's Send Window narrows `us_unknown_fallback` Contacts with the US Market's window).
- **Review fixes (pass 1).**
  - P1: when `_resolve` writes the first `contact` record's notes, it also clears the notes of the Contact's other `contact` records in one UPDATE (`provenance.clear_other_contact_notes`), so exactly the first holds them. The write still happens only when the first record's notes differ.
  - P2: on a match, `add_contact` resolves whenever anything was filled, a name, role or LinkedIn URL included.
  - P3: `ImportPlan.blocked` and `ImportSummary.blocked` are required, and every construction passes them.
  - P4: the `lead_import_page.py` docstring lists the tables in page order: rejected, blocked, noted.
  - P5: style fixes: a long `tests/dashboard.py` docstring line wrapped, the `test_runner_lifespan.py` imports sorted, its `raw_query` call one argument per line, and its docstring reflowed.
  - P6: the stopped-import test has a blocked row and asserts `summary.blocked`.
  - P7: the UK-office conflict test pins its exact note, which has no timezone note.
- **Final verification (after review pass 1, P1–P7).** The full suite through `suite_lock.py` (track-E), once: 8,644 passed, 3 skipped, 0 failed. `uv run lint-imports`: 29 kept, 0 broken.

## Spec Change Log

## Review Triage Log

**Pass 1 (2026-11-18).**
- **Sources:** BH = blind hunter (17 findings), EC = edge-case hunter (8, one a low-confidence claim), VG = verification gap (2 gaps and 1 other finding).
- **Counts:** 28 raw findings, which make 21 entries after deduplication.
- **Outcomes:**
  - 7 entries patched (P1–P7): 2 code fixes, 1 type fix, 1 text fix, 1 style fix and 2 test gaps;
  - 1 deferred (G, to Epic 6's Send Window);
  - 13 rejected, 3 of them as false.
- **No loopback.** No finding is an intent gap, and no spec defect needs re-derivation. P1 and P2 keep the frozen rules: the notes stay on the first `contact` record by `retrieved_at` and `id`, and a resolution writes only what changed.
- **Rejected only because the fix would change the approved spec:**
  - D: Contacts already resolved as blocked stay blocked when a Market is enabled later, and with every Market disabled the backfill resolves them all as blocked (decisions 9 and 12);
  - E: a region from a later source never refines a stored US office that has no state (decision 1: an office is filled once);
  - H: a tie is stored as `conflict_stricter` with no Market, not under a separate basis (the frozen enum);
  - L: before the first `nextron seed sync`, every CSV row reads "Country US isn't an enabled Market", with no hint to seed (decision 10).

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| A | EC4, BH1, VG other | The notes go on the "first" `contact` record by `retrieved_at`, then `id`. A later record can sort first in two ways: an earlier caller-supplied `retrieved_at`, or the same `retrieved_at` (two rows of one CSV) with a same-millisecond random ULID. Then the notes land on the new record and stale notes stay on the old one, or the first record shows none. | medium | Verified: the ordering is `(retrieved_at, id)` and `new_id()` isn't monotonic; `_resolve` only writes the first record. A direct fix inside the frozen rule: when `_resolve` writes the first record's notes, it clears the notes on the Contact's other `contact` records, so exactly the first record holds them. P2 makes every fill re-run `_resolve`. | P1 |
| B | EC8 (claim) | A match that fills only a name, role or LinkedIn URL doesn't resolve the Contact, so a pre-3.2 Contact written that way stays unresolved until the backfill runs. It is also why A's moved "first" record keeps no notes. | low | Verified (`add_contact` resolves only on a new email or office). The fix is a one-line condition: resolve whenever the match filled anything. `_resolve` writes only what changed. | P2 |
| C | EC1, BH5 | The DB checks allow office values that `Location` refuses (`GB`+`NY`, `ZZ`), so `_resolve` would raise `InvalidLocation` and doom a batch. | false | No writer can store such a row. `contacts` is written only through `db/repos/research`; the office columns are set only from a `ContactCandidate.office`, which is a validated `Location`; and the columns are new, so no older row holds a value. | reject |
| D | EC2, BH6 | The backfill guard checks for stored Markets, not enabled ones. With every Market disabled, legacy Contacts resolve as blocked for good, and re-enabling a Market never re-resolves them. `add_contact` has no guard. | low | True, and the compliance-safe side: a blocked Contact gets no email. Decision 9 says the job "waits while no Market is stored", and decision 12 says Market edits don't re-resolve stored Contacts. Changing either changes the approved spec. Without Markets, the CSV (today's only creation path) blocks every row. Listed for Mac. | reject |
| E | EC3 | When a stored US office has no state and a later source gives the state, the state is never stored, so the Contact keeps the New York fallback. | low | True (`new_office` only when `office_country` is NULL). Decision 1 says the office is filled once and never overwritten; no path supplies an office yet. Story 2.10 decides refinement. Listed for Mac. | reject |
| F | EC5, BH8 | `zone_country` knows only the table's zones, so a valid US `hq_tz` such as `America/Juneau` or `America/Kentucky/Louisville` would be ignored. | false | `lead_hq_tz` is always `None` today. Story 2.9's AC sets `hq_tz` "through `domain.geo`", whose zones are exactly the table's plus `Europe/London`, and `zone_country` maps all of them. | reject |
| G | EC6, BH2 | In a conflict (US place, unknown state, `.co.uk` address), the Contact is UK-Market with `us_unknown_fallback`, and its note promises an 11:00-17:00 window that the UK Market has no rule for. Decision 5's obligation on sending isn't in `deferred-work.md`. | low | True as designed (decision 5): the person's time is American. Sending must take the narrowed window from the US Market (the Market of the timezone's country) whenever `tz_basis` is `us_unknown_fallback`, not from the Contact's Market. The fix belongs to Epic 6's Send Window, so it is logged there. | defer |
| H | BH3 | A tie stores `conflict_stricter` with no Market, so the basis name suggests a Market was applied. | low | True, but unreachable with the shipped seed (UK 3, US 0). The frozen enum has no tie value, and `market_id` NULL already blocks. Listed for Mac. | reject |
| I | BH4 | Nothing ties the blocking bases to a NULL Market, in either `ContactMarket` or the DB checks. | low | Only `resolve_contact_market` builds a `ContactMarket`, and only `_resolve` writes the columns; no path produces the inconsistent state. A new check would guard a state the code can't reach. | reject |
| J | EC7, BH9 | An erasure nulls the office columns but keeps `tz` and the bases derived from them, so an erased state could be read from `tz`. | low | True, but after an erasure the Contact is a tombstone with no name or address, and a timezone alone identifies no one. `tz` was classed `none` by Story 2.3. Story 3.13 owns erasure, and if it ever nulls `tz` it must also null `market_id` (`ck_contacts_market_tz`). | reject |
| K | BH7 | Every resolution re-reads and re-validates every Market. | low | Measured at about 0.4 ms for both Markets' decode, so a 200-Contact batch adds well under a second to its transaction. Passing the Markets in adds parameters to the one resolver. | reject |
| L | BH10 | Before any `seed sync`, every row is blocked with "Country US isn't an enabled Market", with no hint to seed. | low | True, but decision 10 states it, and the README says to run `nextron seed sync` first. A new refusal or hint is new page surface. Listed for Mac. | reject |
| M | BH11 | `ContactMarkets` takes a `clock` it never uses. | low | True. The frozen spec names the signature `ContactMarkets(clock, research, queue)`, and it's harmless. | reject |
| N | BH12 | `ImportPlan.blocked` and `ImportSummary.blocked` default to `()`, so a construction that forgets them silently shows "Blocked by Market: 0". | low | Verified: the defaults exist only for the older test constructions. A direct fix: make both required and update the constructions. | P3 |
| O | BH13 | The import's "enabled" (the `enabled` flag) and the resolver's (`market_for_country`, exactly one Market per country) can drift. | false | `uq_markets_country_code` makes two Markets per country impossible in the database, so the two definitions agree on every stored state. | reject |
| P | BH14 | `lead_import_page.py`'s docstring lists the tables as rejected, noted, blocked; the page shows rejected, blocked, noted. | low | Verified. A direct text fix. | P4 |
| Q | BH15 | The handler ignores its payload, so a later `{"v": 2}` would run as v1. | low | Only `queue_backfill` enqueues this type, always with `{"v": 1}`, and `decode_payload` already checks the payload's shape. A version check guards a state nothing produces. | reject |
| R | BH16 | Test gaps: the lifespan test drives one start, the runner may run the job during it, and there is no app-level tie, no country-less merge, no re-queue after `done` and no out-of-order `retrieved_at`. | low | The repeat-absorbed and re-queue-after-done cases are app-tested (`test_queue_backfill_queues_one_bulk_job_and_a_repeat_is_absorbed`, `test_with_no_market_stored_..._requeues`). The tie and `location_unknown` are unit-tested. The lifespan test asserts no status, so a run during it can't flake it. P1 adds the out-of-order test. | reject |
| S | BH17 | Style drift: a docstring line over 100 columns in `tests/dashboard.py`, out-of-order imports, a hand-shaped call and a stub docstring line in `tests/unit/test_runner_lifespan.py`. | low | Verified. Direct, cosmetic fixes. | P5 |
| T | VG1 | A stopped import's summary can drop the blocked rows with no test noticing: the only real stop test has no blocked row. | low | Pre-verified by mutation (dropping `plan.blocked` in the stop path passes 91 of 91). Add a blocked row to `test_a_failing_batch_stops_the_import_and_keeps_earlier_batches` and assert `stopped.summary.blocked`. | P6 |
| U | VG2 | The conflict note's "stricter" Market is pinned only where the domain's Market wins; naming the domain Market always passes every test. | low | Pre-verified by mutation (443 of 443 pass). Assert the exact note for a UK office with a `.us` address. | P7 |

## Design Notes

**Note texts** (`{…}` are codes, zones, Market names or a window; never personal data):
- **The Market notes:**
  - `lead_country`: "Market taken from the Lead's country; office location unknown"
  - `conflict_stricter`: "The {place} and the email's country-code domain point to different Markets ({place Market} and {domain Market}); the stricter {chosen} Market applies"
  - `conflict_stricter`, tie: "The {place} and the email's country-code domain point to different Markets ({a} and {b}) with equally strict rules, so no Market applies"
  - `cctld_outside_markets`: "No Market: the email address is on the country-code domain .{last label}, outside the enabled Markets"
  - `location_outside_markets`: "No Market: the office location ({code}) isn't in an enabled Market", or "No Market: the Lead's country ({code}) isn't in an enabled Market; office location unknown"
  - `location_unknown`: "No Market: neither the office location nor the Lead's country is known"

  `{place}` is "office location", or "Lead's country (office location unknown)".
- **The timezone notes:**
  - `lead_hq`: "Timezone taken from the Lead's headquarters ({tz})"
  - `market_default`: "Timezone is the {name} Market's default ({tz}); neither the office location nor the Lead's headquarters gave one"
  - `us_unknown_fallback`: "Timezone falls back to {tz}: the US state is unknown, so the Send Window narrows to {window}" (`send_window_text`)

**The US table** (`code,timezone,name`; most-populous zone). The file's header comment says so and lists the multi-zone states.
- **`America/New_York`:** CT, DE, DC, FL, GA, KY, ME, MD, MA, NH, NJ, NY, NC, OH, PA, RI, SC, VT, VA, WV.
- **`America/Indiana/Indianapolis`:** IN.
- **`America/Detroit`:** MI.
- **`America/Chicago`:** AL, AR, IL, IA, KS, LA, MN, MS, MO, NE, ND, OK, SD, TN, TX, WI.
- **`America/Denver`:** CO, MT, NM, UT, WY.
- **`America/Boise`:** ID.
- **`America/Phoenix`:** AZ.
- **`America/Los_Angeles`:** CA, NV, OR, WA.
- **`America/Anchorage`:** AK.
- **`Pacific/Honolulu`:** HI.

**For later stories:**
- Every path that creates a Contact goes through `research.add_contact`, which resolves.
- Any other change to a Contact's email, office location or Lead calls `research.resolve_market(contact_id)` in the same transaction: Stories 2.10, 2.13, 2.14 and 2.16.

## Verification

**Commands:**
- `cd nextron-engine; uv run pytest -q tests/unit/test_geo.py tests/unit/test_contact_market.py tests/app/test_contact_markets.py tests/unit/test_lead_import.py tests/app/test_lead_import.py tests/unit/test_lead_import_page.py tests/unit/test_runner_lifespan.py tests/contracts` -- expected: all pass.
- `uv run lint-imports` -- expected: 29 contracts kept.
- The full suite, only through the suite lock (the orchestrator).
