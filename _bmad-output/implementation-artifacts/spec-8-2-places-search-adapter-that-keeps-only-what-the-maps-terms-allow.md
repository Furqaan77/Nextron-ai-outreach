---
title: "Story 8.2: Places search adapter that keeps only what the Maps terms allow"
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: '2bc36d85b44b2a32081567988717ab1d32cf63c6'
review_loop_iteration: 0
story_key: '8-2-places-search-adapter-that-keeps-only-what-the-maps-terms-al'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-8-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-12-confirm-a-uk-lead-s-legal-form-through-companies-house.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Discovery needs Google Places (Text Search, New) to find candidate organizations, but the Maps Platform terms forbid storing Places names, addresses, phone numbers or reviews, every call is billed per SKU, and paid use must never start on its own.

**Approach:** Add `PlacesPort` with transient `PlaceCandidate` values, a metered httpx2 adapter whose field mask asks only for id, website, display name and location, a per-call budget/quota seam in `app.discovery`, three coordinate columns on `leads` purged daily after 30 days, environment wiring that uses an offline Places outside `prod` unless a key is stored, and a contract test that keeps Places content out of every store.

## Boundaries & Constraints

**Always:**
- Field mask exactly `places.id,places.websiteUri,places.displayName,places.location,nextPageToken`; SKU from the mask: `enterprise` when `places.websiteUri` is in it, else `pro`. Meter provider `places`, operation `text_search`, that `sku`; exactly one `ApiCall` per attempted request (`Metered`).
- API key read with `read_credential(secrets, CredentialKey.GOOGLE_PLACES)` on every call, sent only in the `X-Goog-Api-Key` header; never in a URL, log, error, DB, job payload or `repr`.
- Every `AdapterError` is this module's own text, raised `from None`; never a response body.
- Before every request, `PlaceSearches.search_page` passes `domain.budget.allow(BLOCKABLE, meter.snapshot(now), setup_queries.budget_limits(), quota=<quota of the mask's SKU>)` read in a transaction; the request itself runs outside any transaction.
- `display_name` and `website_uri` are `repr=False` on `PlaceCandidate`.
- Purge: NULL `place_lat`, `place_lng`, `place_coords_at` where `place_coords_at < now - 30 days` (strict); `place_id` never touched.

**Never:**
- No Lead creation, ingest, discovery page, `discovery_runs`, paging loop or `discover_places` job (Story 8.3); no `budget_class` on job registration (Story 8.1).
- No column besides `leads.place_id`, `place_lat`, `place_lng`, `place_coords_at` fed from a `PlaceCandidate`; no `display_name`/address/phone/review in DB, `events`, job payloads, logs or AI prompts.
- No test reaches Google; no new dependency; no DB CHECK on the new columns (add_column only, no table rebuild).

### Decisions made overnight (pending user review)
1. **8.1 boundary.** 8.1 is not built. This story adds only `PlaceSearches.search_page` (the per-call check plus one port call, returning `None` when refused). `budget_class`, `deferred` handling, notifications and health issues stay in 8.1; the paging caller is 8.3.
2. **"The fake" at runtime.** Runtime code can't import `tests/fakes`, so outside `prod` the engine uses `OfflinePlaces` (in `nextron/adapters/places/`): it makes no request, meters nothing and returns an empty page. `build_places(env, secrets, meter)` decides once at startup: `prod` → `GooglePlaces`; `dev`/`test` → `GooglePlaces` only when the `google_places` keyring entry exists (presence checked, value discarded), else `OfflinePlaces`; a keyring read failure → `OfflinePlaces`.
3. **Location.** `location` is free text (8.3 passes e.g. "Columbus, OH"); the request's `textQuery` is `"<query> in <location>"`, `pageSize` 20, `pageToken` when given. No `locationBias` (it needs coordinates we don't hold).
4. **Purge job group.** `purge_place_coords` registers in `JobGroup.OTHER` like `purge_page_text` (bulk only, priority 20, 10 min, retry 2), with `Every(1 day, at_startup=True)`.
5. **PII class** of the three new columns is `none` (an organization's location).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Error Handling |
|---|---|---|---|
| Happy page | 200 with places + `nextPageToken` | `PlaceSearchPage(candidates, token)`; 1 ok `ApiCall` sku `enterprise` | — |
| Last page | 200, no token / no `places` | token `None`; empty candidates when none | — |
| Bad item | place without valid `id`, or bad location | item skipped; location out of range → `lat`/`lng` `None` | — |
| 429 / 5xx / transport | | `places_rate_limited` / `places_unavailable`, retryable; 1 failed `ApiCall` | message names only the status |
| 401/403 | | `places_unauthorized`, not retryable | |
| Other non-2xx (incl. 400, 3xx) | | `places_refused`, not retryable | |
| Body >2 MB, not JSON object, `places` not a list | | `places_bad_response` | |
| Blank query or location | | `places_refused`, no request, nothing metered | |
| Key missing | | `credential_not_set`, no request | |
| Quota | enterprise quota 1000, 999 used | first `search_page` makes 1 request; second returns `None` with no request | |
| Pro quota 0 | a mask without `websiteUri` | `search_page` refuses (pro quota 0) | |
| Purge | coords 30 d + 1 s old / exactly 30 d old | first NULLed / second kept; `place_id` kept | |

</frozen-after-approval>

## Code Map

All paths under `nextron-engine/`. Follow Story 2.12's Companies House work as the pattern (see its spec Code Map).

- **Port (new)** `nextron/ports/places.py` (model `ports/registry.py`): `PlaceCandidate(place_id, website_uri, display_name, lat, lng)` frozen slotted, validated (`place_id` 1–`PLACE_ID_LENGTH` chars; strings or `None`; `lat` −90..90 / `lng` −180..180 floats or `None`, both or neither); `PlaceSearchPage(candidates: tuple[PlaceCandidate, ...], next_page_token: str | None)`; `@runtime_checkable PlacesPort.text_search(query, location, page_token=None) -> PlaceSearchPage`; error codes `PLACES_*`. Docstring paragraph in `ports/__init__.py`.
- **Domain** `nextron/domain/places.py` (new): `FIELD_MASK` tuple, `places_sku(fields) -> "enterprise" | "pro"`, `quota_for_sku(sku)` (`places_enterprise`/`places_pro` from `budget.QUOTAS`), `PURGE_PLACE_COORDS_JOB`, `PLACE_COORDS_TTL = timedelta(days=30)`.
- **Adapter (new)** `nextron/adapters/places/__init__.py`: copy `adapters/companies_house/__init__.py` (`default_http_client`, lazy `_client` under lock, `read_credential` each call, `Metered` around the request only, `_answer`-style status table). `GooglePlaces(secrets, *, meter, http=None)`: POST `https://places.googleapis.com/v1/places:searchText`, headers `X-Goog-Api-Key`, `X-Goog-FieldMask`, `Content-Type: application/json`; parse `places[].id`, `.websiteUri`, `.displayName.text`, `.location.latitude/longitude`. Also `OfflinePlaces()` (Decision 2).
- **Seam (new)** `nextron/app/discovery/places.py`: `PlaceSearches(clock, meter, places)` with `@use_case search_page(query, location, page_token=None) -> PlaceSearchPage | None`; mirror `app/discovery/legal_form.py:204-216` for the budget check. Log `places_search_refused` (the quota name) / `places_page_fetched` (candidate count and `has_next` only; never names, URIs, query, location or token).
- **Data.** `db/models/leads.py:85` add `place_lat`/`place_lng` (`Float`), `place_coords_at` (`UtcDateTime`); migration `db/alembic/versions/0037_8_2_place_coords.py` (revision `0037_8_2_place_coords`, down `0036_2_12_legal_form`) copying `0036`'s `batch_alter_table` add_column; `downgrade` raises. `db/owners.py:~116` (with `place_id`, owner discovery) and `db/pii_registry.py:~119`. `db/repos/discovery/leads.py`: `purge_place_coords(cutoff) -> int`.
- **Service.** `app/discovery/api.py` `Discovery`: `@use_case purge_place_coords() -> int` (model `app/research/api.py:1292`), logs `place_coords_purged` count.
- **Jobs.** `jobs/handlers.py:242-253` pattern → `purge_place_coords(discovery)`; `main.build_job_types` register after `PURGE_PAGE_TEXT_JOB` (:1366); `main.build_triggers` (:1524) add `Trigger.PURGE_PLACE_COORDS`; `PURGE_PLACE_COORDS_PERIOD`.
- **Wiring.** `main.build_places(env, secrets, meter) -> PlacesPort`; `build_app(..., places: PlacesPort | None = None)` (:962) resolves it, builds `PlaceSearches` and keeps it on `app.state.place_searches`; module docstring paragraph.
- **Tests and contracts.**
  - `tests/fakes/places.py` `ScriptedPlaces(pages=...)` keyed by `(query, location, page_token)`, results/`AdapterError`/callables, `calls`, unscripted → `AssertionError`; add to `tests/fakes/__init__.py` `PORT_FAKES`.
  - `tests/live_guard.py`: `("nextron.adapters.places", "default_http_client")` + `Guarded`. `pyproject.toml` c5: add `nextron.adapters.places`.
  - `tests/contracts/test_test_isolation.py` (port `PlacesPort`, package `places`); `tests/contracts/test_adapter_boundary.py` (`PLANTED`/`DRIVERS` for `GooglePlaces`); `tests/contracts/test_source_tree.py` `FILES` + layers.
  - Head bump to `0037_8_2_place_coords`: `tests/contracts/test_persistence_contracts.py`, `tests/unit/test_migrations.py` (+ probe), `tests/app/test_serve_process.py` (trigger count 5→6, startup tick).
  - Job lists (grep `purge_page_text`): `tests/contracts/test_job_contracts.py`, `tests/unit/test_runner_lifespan.py`, `tests/unit/test_health_page.py`, `tests/app/test_engine_health.py`, `tests/dashboard.py`, `tests/unit/test_triggers.py` if needed.
  - New `tests/contracts/test_places_storage_terms.py` (see Tasks).
- **Docs.** `nextron-engine/README.md`: "Google Places search" section, migrations row, jobs list, adapters note.

## Tasks & Acceptance

**Execution:**
- [x] port, domain, adapter, offline adapter -- as Code Map.
- [x] model, migration, owners, PII, repo, `Discovery.purge_place_coords`, handler, job type, trigger -- as Code Map.
- [x] `app/discovery/places.py` seam and `main` wiring -- as Code Map.
- [x] `tests/unit/test_places_adapter.py` -- every I/O-matrix row via `httpx2.MockTransport`; request method, URL, headers (key only in `X-Goog-Api-Key`), JSON body; field mask equals `FIELD_MASK` exactly and a test fails it on any of `formattedAddress`, `nationalPhoneNumber`, `internationalPhoneNumber`, `reviews`, `rating` or any extra field; `places_sku` cases; `OfflinePlaces` makes no request.
- [x] `tests/app/test_place_searches.py` -- 999/1000 enterprise case; pro quota 0 refusal; AI budget reached refusal; the request runs outside a transaction; `build_places` choices per env/key presence/keyring failure.
- [x] `tests/app/test_place_coords_purge.py` -- frozen `now`, boundary rows, `place_id` kept, handler via the registry, health panel lists `purge_place_coords`.
- [x] `tests/contracts/test_places_storage_terms.py` -- (a) model columns: the only `leads` columns named `place_*` are the four, and no table has a column named like a Places field (`display_name`, `website_uri`, `formatted_address`, `phone`, `rating`, `review`); (b) AST: `PlaceCandidate` and `nextron.ports.places` imported only from an allowlist (`ports`, `adapters/places`, `app/discovery/places.py`, `main.py`), and no module under the AI/prompt packages imports them; (c) planted run: `ScriptedPlaces` returns a candidate with unique planted `display_name`/`website_uri`, `search_page` runs on a real migrated DB with log capture, then every text cell of every table (incl. `events`, `jobs.payload`) and every log record lacks the planted strings.

**Acceptance Criteria:**
- Given the full suite under `NEXTRON_ENV=test`, when it runs, then no request reaches `places.googleapis.com` and `lint-imports` keeps every contract.
- Given the engine starts in `dev` with no Places key, then `app.state.place_searches` uses `OfflinePlaces`.

## Implementation Notes

Built as specified. Where the spec left a choice open, the code chose as follows.

- **Port** (`nextron/ports/places.py`). Also exports the five `PLACES_*` codes, `PLACES_CODES` and `PLACE_ID_LENGTH` (512, the `leads.place_id` width). `PlaceCandidate` requires `lat`/`lng` as `float` (an `int` is a `TypeError`; the adapter converts) and finite. `PlaceSearchPage` validates its tuple and refuses an empty-string token; its token is `repr=False` too. Docstring paragraph added to `ports/__init__.py`.
- **Domain** (`nextron/domain/places.py`). Also `PROVIDER` (`places`), `TEXT_SEARCH`, `WEBSITE_FIELD`, `SKU_ENTERPRISE`/`SKU_PRO` and `coords_cutoff(now)` (`now - 30 days`). `places_sku` takes any iterable of fields. `quota_for_sku` looks the name up through `budget.quota_spec` and raises `ValueError` for any other SKU.
- **Adapter** (`nextron/adapters/places`).
  - **Call order:** argument checks (`places_refused`, no request, nothing metered: a query or location that isn't a str with text, or a `page_token` that isn't `None` or a non-empty str), then `read_credential` (its errors propagate, no request), then the lazy client, then `Metered(..., sku=places_sku(FIELD_MASK))` around the POST *and* the body's parsing, so a bad body is metered `places_bad_response`.
  - Query and location are whitespace-collapsed; the body is sent with `json=` plus the explicit `Content-Type` header. Both classes have a fixed `repr` (`GooglePlaces()` / `OfflinePlaces()`).
  - **Parsing.** A non-object item or one whose `id` isn't a 1–512-char str is skipped; a non-str `websiteUri` or display name reads `None`; a location needs both numbers (not `bool`), finite and in range, else both `None`. `nextPageToken` that isn't a non-empty str is `None`. Body cap is 2,000,000 bytes; any exception from the POST is `places_unavailable` (retryable).
  - `OfflinePlaces.text_search` doesn't validate its arguments; it always answers `PlaceSearchPage((), None)`.
- **Seam** (`PlaceSearches`). Constructor keyword `fields=FIELD_MASK` (the mask the port uses) fixes the quota once; a `places` property exposes the port. A refusal logs `places_search_refused` with `quota` and `reason` (`ai_budget` when the plain AI-budget check also refuses, else `quota`). The snapshot and limits are read inside one `uow.transaction()`; the port call runs after it closes.
- **Data.** Migration `0037_8_2_place_coords` (down `0036_2_12_legal_form`): `sa.Float()` ×2 and `sa.String(27)` (the `UtcDateTime` width) via `batch_alter_table(...).add_column`. Model: `Float` and `UtcDateTime`. Owner `discovery`, PII `none`. `leads_repo.purge_place_coords(cutoff)` is one `UPDATE ... WHERE place_coords_at IS NOT NULL AND place_coords_at < cutoff` (UTC text compares in order).
- **Service and jobs.** `Discovery.purge_place_coords()` (`@use_case`, one transaction, logs `place_coords_purged` `count`). `handlers.PurgingPlaceCoords`, `handlers.DiscoveryJobs` (the union of `Canonicalizing` and `PurgingPlaceCoords`, now the type of `build_job_types(discovery=)`), `handlers.purge_place_coords(discovery)`. Registered right after `purge_page_text` with its spec, so no `build_job_types(` caller changed. `Trigger.PURGE_PLACE_COORDS` (already in the AD-10 enum) registered after `PURGE_PAGE_TEXT` with `Every(PURGE_PLACE_COORDS_PERIOD = 1 day, at_startup=True)`.
- **Wiring.** `main.build_places(env, secrets, meter)`: `prod` → `GooglePlaces`; otherwise `read_credential(secrets, GOOGLE_PLACES)` and any exception (not set, keyring failure) → `OfflinePlaces`, else `GooglePlaces`. `build_app(..., places=None)` builds `PlaceSearches` after `LegalFormChecks` and sets `app.state.place_searches` on the app `create_app` returns. Docstrings in `main` (module paragraph, `build_job_types`/`build_triggers` paragraphs, `build_app`) updated.
- **Tests.**
  - New: `tests/unit/test_places_adapter.py` (71: the mask check `mask_problems` with its proofs for each forbidden field, an extra field and a missing one; SKU/quota table; every matrix row; key rotation; `OfflinePlaces`), `tests/app/test_place_searches.py` (20: 999→1000 Enterprise through a real `GooglePlaces` on a mock transport, Pro usage not counted against Enterprise, Pro mask refused at default 0 then allowed after the setting is raised, Enterprise 0, AI budget, outside-transaction via `uow.in_transaction()`, error propagation, `build_places` 9 cases, `build_app` in `dev` without a key → `OfflinePlaces`, injected port), `tests/app/test_place_coords_purge.py` (2), `tests/contracts/test_places_storage_terms.py` (20: columns with `ALLOWED_COLUMNS` empty and proofs; AST import allowlist with `AI_PACKAGES` checked first and proofs on a copied tree; planted run through both `GooglePlaces`+real `UsageMeter` and `ScriptedPlaces`, scanning every text cell and every log record).
  - `tests/fakes/places.py` (`ScriptedPlaces`) in `PORT_FAKES`; `live_guard` (`places` Guarded, `default_http_client`); `pyproject.toml` c5; isolation (port, package, probe lists); adapter boundary (`PLACES_KEY` planted, `places_calls` driver); source tree (`FILES`, layers).
  - Head bump to `0037_8_2_place_coords` in `test_persistence_contracts` (chain probes now `0038_*`/`0039_*`), `test_migrations` (new 0037 probe; the 0036 probe now locates its columns by index instead of "last five"), `test_serve_process` (6 triggers, the `purge_place_coords` startup tick and log), `test_serve` (`0038_more`).
  - Job lists: `test_job_contracts` (names, specs, handler call, triggers), `test_runner_lifespan` (names, 6 triggers, scheduler jobs), `test_health_page` and `test_engine_health` (group Other), `tests/dashboard.py` message.
  - **Building the app now reads one secret.** Decision 2's startup presence check means `build_app` outside `prod` makes exactly one `secrets.read("google_places/api_key")` (value discarded). Two tests that asserted building the app reads no secret now expect exactly that one read: `test_runner_lifespan.py::test_building_the_app_runs_no_readiness_check` and `test_serve.py::test_reload_is_allowed_in_dev`. In `test` with the real (guarded) keyring the read raises and the engine stays offline.
- **Verification (implementer, targeted).** `tests/contracts` plus every touched/new test file (`-n 6`): 2093 passed, 10 skipped, then the 2 failures above fixed and re-run green; 7 further files that build the app and inspect secrets: 420 passed. `uv run lint-imports`: 29 kept, 0 broken. No request reached Google.
- **Docs.** README "Google Places search" section (before "Engine health"), migrations row, jobs list, triggers count (six), metering adapters note.
- **Review patches (pass 1).**
  - Adapter: a coordinate that overflows `float()` reads as `None` (no `OverflowError` escapes); a whitespace-only `page_token` is `places_refused` with no request. Tests extended.
  - `leads_repo.purge_place_coords` also nulls coordinates held with a NULL `place_coords_at` (unknown age); the purge test covers lat-only and lng-only rows. Docstrings and README updated.
  - `main.build_places` logs `places_port_chosen` (`live`: bool, never the key); asserted in the `build_places` test.
  - Storage-terms contract: relative imports resolved (as `test_sink_contracts.py` does), `PlaceSearchPage` added to `PLACES_NAMES`, three new probes (two relative imports, one `PlaceSearchPage`), and a test that `ports.places.PLACE_ID_LENGTH == models.leads.PLACE_ID_LENGTH`.
  - `app/discovery/places.py` docstring: a limit of 0 (Pro's default) always refuses; the Enterprise quota's maximum is Google's free allowance.
  - `test_migrations` 0037 probe anchors on `columns.index("place_lat")`.
  - README: the 8.3 sentence reworded, a "**Not yet:**" line (paging, Lead creation, location bias: 8.3; `budget_class`/deferred: 8.1), the trigger-list line re-wrapped.
  - Targeted re-run of the touched test files: 122 passed; `lint-imports` 29 kept, 0 broken.
- **Final gate.** Full suite through `suite_lock.py` on the patched tree: 15,258 passed, 13 skipped; `lint-imports` 29 kept, 0 broken. A first run had 2 engine-startup timeouts under load (`test_environment_isolation`, `test_serve_process`); both files passed alone (8 passed) and the re-run was clean.

## Spec Change Log

## Review Triage Log

Pass 1 (blind hunter 13 + 3 doc notes, edge-case hunter 8, verification gap 1 + 2 other; 27 raw, 20 after dedup).

| # | Source | Finding | Verdict | Route / evidence |
|---|---|---|---|---|
| 1 | edge | `_number`: a JSON integer of 309+ digits makes `float()` raise `OverflowError`, escaping as a non-`AdapterError` | low | patch: direct correction, catch `OverflowError` -> `None` |
| 2 | edge, blind | the 2 MB cap is checked after `response.content` read the whole body | low | reject: same pattern as the Companies House adapter; Google bodies are small; streaming adds complexity |
| 3 | edge | a whitespace-only `page_token` passes and spends a paid request | low | patch: refuse a token with no text, like query/location |
| 4 | edge, blind | `purge_place_coords` skips rows with lat/lng set and `place_coords_at` NULL; no CHECK prevents that, so such coordinates would outlive 30 days | medium | patch: also null coordinates whose `place_coords_at` is NULL; add a test |
| 5 | edge | a daily purge with a strict 30-day cutoff lets coordinates live up to ~31 days | low | reject: the frozen AC fixes the cutoff (exactly 30 days untouched) and Decision 4 the daily period; changing either edits the approved spec |
| 6 | edge, blind | check and request aren't atomic: concurrent searches at 999/1000 can both pass | medium | defer: the per-call check pattern is shared with every blockable job (e.g. `check_legal_form`); Story 8.1 owns quota enforcement and 8.3 the concurrent caller |
| 7 | edge | `PlaceSearches(fields=)` can disagree with the mask `GooglePlaces` sends | low | reject: the default is the adapter's own `FIELD_MASK`; only tests pass another mask |
| 8 | edge | a bare `str` `fields` splits into characters | low | reject: as 7 |
| 9 | verif | the import contract skips relative imports (`node.level == 0`) | medium | patch: resolve relative imports against the module's package, as `test_sink_contracts.py:407` does; add relative-import probes |
| 10 | verif, blind | `PlaceSearchPage` (which carries candidates) is not in `PLACES_NAMES` | low | patch: add it; one-line correction |
| 11 | blind | dynamic imports (`importlib`) aren't scanned | low | reject: none exist; the planted run is the backstop |
| 12 | blind | seam docstring says the default limit 0 stops paid use, but the Enterprise default is 1,000 | low | patch: reword (the Enterprise quota's maximum is Google's free allowance, so paid use never starts; Pro defaults to 0) |
| 13 | blind | `place_coords_at` in the future outlives 30 days | low | reject: no writer yet; 8.3 writes `now` |
| 14 | blind | 429 ignores `Retry-After`; no self rate limit | low | reject: retryable error, the job queue backs off |
| 15 | blind | an expired page token is indistinguishable from a bad request (400 -> `places_refused`) | low | reject: 8.3's paging concern; not reachable yet |
| 16 | blind | failed requests are metered and count against the quota | false | the project-wide `Metered` contract records every attempted request (AD-9); conservative by design |
| 17 | blind | `build_places` falls back to offline silently; nothing says which port runs | low | patch: log `places_port_chosen` with `live` true/false (no key) once at build |
| 18 | blind | the 0037 migration test uses `columns[-3:]`, brittle like the old 0036 test | low | patch: anchor by `columns.index("place_lat")` |
| 19 | blind | `ports.places.PLACE_ID_LENGTH` duplicates the model's with no test tying them | low | patch: assert equality in the storage-terms contract |
| 20 | blind | column-name check is narrow; the planted run makes no job/event rows; README garbled sentence, no "Not yet" line, over-long line; no index on `place_coords_at` | low | patch only the README text (garbled sentence, "Not yet" line, wrap); reject the rest (8.3 extends the planted run; small table) |

## Verification

**Commands:**
- `cd nextron-engine && uv run pytest -q tests/unit/test_places_adapter.py tests/app/test_place_searches.py tests/app/test_place_coords_purge.py tests/contracts` -- all pass
- `cd nextron-engine && uv run lint-imports` -- all contracts kept
