---
title: 'Story 4.4: Snapshot the Sales Playbook and pin it in route tuples'
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: '689874306572671ac41950cdc25624632505a078'
review_loop_iteration: 0
story_key: '4-4-snapshot-the-sales-playbook-and-pin-it-in-route-tuples'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-4-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-8-version-extraction-prompts-and-pin-the-active-route-tuple.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Story 2.8 pins each AI route's prompt, but the Sales Playbook (`docs/playbook/sales-playbook.md`, front-matter `version: 0.2.0`) is stored nowhere.
- No route tuple can name the Playbook it ran under.
- Nothing records a candidate tuple for Story 4.5's test gate.
- The Operator can't see which tuple each route runs.

So a change in reply rates can't be traced to the exact Playbook, prompt and model (FR-71, AD-8, AD-12, NFR-9, NFR-25, UX-DR30).

**Approach:**
- **Migration `0022_4_4_playbook_snapshots`** adds:
  - `playbook_snapshots`;
  - a nullable `playbook_sha256` on `ai_route_activations`;
  - `ai_route_tuples`, which holds every candidate tuple;
  - `playbook_checks`, which holds each Playbook read.
- **One sync.** `Playbook.sync_prompts` also takes the Playbook read from the bootstrap path, at `serve` start and on the AI content view's "Check for changes" POST. In one transaction it stores new snapshots, creates candidate tuples and records the check.
- **Reads and the gateway.**
  - `queries.playbook.active_tuple` hands the gateway the pinned Playbook snapshot's text.
  - The gateway frames that text with the order of authority.
- **The view.** A new AI content view (`/ai`) shows each route's active tuple and its candidates.

The acceptance criteria are Story 4.4 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**

- **Domain** (`nextron/domain/playbook.py`, standard library only).
  - **Constants.** `VERSION_PATTERN` is `[0-9A-Za-z][0-9A-Za-z.+-]{0,31}`, and `MAX_PLAYBOOK_CHARS` is 100,000.
  - **`PlaybookProblem` (`StrEnum`):** `unreadable`, `too_large`, `not_utf8`, `no_version`, `bad_version`. `problem_text(problem)` gives the Operator's sentence for each. Every sentence ends "…, so no snapshot was stored." and `no_version` adds how to fix it.
  - **`InvalidPlaybook(DomainError)`** has code `invalid_playbook` and carries `.problem`.
  - **`PlaybookSnapshot(version, content)`** is frozen, with `content` hidden from `repr`. `content` is a non-blank `str` of at most `MAX_PLAYBOOK_CHARS` characters. `sha256` is the hex SHA-256 of its UTF-8.
  - **`read_playbook(data: bytes) -> PlaybookSnapshot`:**
    1. Over 4 × `MAX_PLAYBOOK_CHARS` bytes is `too_large`.
    2. Decode as strict UTF-8, else `not_utf8`. Drop one leading U+FEFF. Convert to universal newlines, so a CRLF checkout hashes the same.
    3. Over `MAX_PLAYBOOK_CHARS` characters is `too_large`.
    4. The front matter runs from a first line `---` to the next line `---`. A missing front matter, or no top-level `version:` line in it, is `no_version`.
    5. A repeated `version:`, or a value that fails `VERSION_PATTERN`, is `bad_version`. Before the check, the value is stripped, a ` #` comment is cut off, and one pair of matching quotes is removed.

    The content is the whole normalised text, front matter included.
  - **The frame.**
    - `AUTHORITY_ORDER` = `"Order of authority: law and platform rules come first, then the Step brief, then the Sales Playbook. Where they disagree, follow the earlier one."`
    - `playbook_section(snapshot)` = `AUTHORITY_ORDER`, a blank line, `Sales Playbook, version <version>:`, a blank line, then the content.
    - A contract test pins the frame's SHA-256: the frame never changes.
- **`nextron/domain/ai.py`.**
  - **`RouteTuple`** gains the last field `playbook: PlaybookSnapshot | None = None` (any other type raises `TypeError`), and `playbook_sha256` (`None` without one).
  - **`key`** appends `:p<first 12 hex of the Playbook SHA-256>` only when there is a Playbook. Extraction keys are unchanged.
  - **`RouteSpec`** gains the last field `uses_playbook: bool = False` (it must be a `bool`).
- **Routes** (`adapters/ai/routes.py`).
  - **`Candidate`** gains the last field `uses_playbook: bool = False`. The shipped three stay `False`.
  - **`default_specs()`** carries `uses_playbook`.
  - **`find(route_tuple)`** also requires `(route_tuple.playbook is not None) == candidate.uses_playbook`, so a mismatch is `llm_unknown_route`.
- **Prompting** (`adapters/ai/prompting.py`).
  - **Without a Playbook,** `system` is unchanged: one block with `cache_control`.
  - **With one,** `system` is two text blocks: the system prompt, then `playbook_section(route_tuple.playbook)`. Only the last block carries `cache_control` `ephemeral`, so the cached prefix holds both.
  - Nothing else in the request changes.
- **Migration `0022_4_4_playbook_snapshots`** (down revision `0021_4_2_campaigns`; `downgrade()` raises). Every column is NOT NULL unless marked otherwise.
  - **`playbook_snapshots`:**
    - columns `id` (ULID), `sha256` (64, unique, `uq_playbook_snapshots_sha256`), `version` (32), `content` (Text) and `captured_at`;
    - CHECKs `ck_playbook_snapshots_sha256` (64 lowercase hex) and `ck_playbook_snapshots_version` (the pattern's characters, length 1–32, and an alphanumeric first character).
  - **`ai_route_activations`** is rebuilt with `batch_alter_table(recreate="always", copy_from=<0014's exact table, index included>)`, the 0019 precedent.
    - It gains a nullable `playbook_sha256` (64) after `prompt_sha256`.
    - It gains FK `fk_ai_route_activations_playbook_sha256_playbook_snapshots` → `playbook_snapshots.sha256`, and `ck_ai_route_activations_playbook_sha256` (NULL or 64 lowercase hex).
    - Every 0014 check, the composite FK and `uq_ai_route_activations_active_route` (still partial, `WHERE deactivated_at IS NULL`) are kept, and existing rows survive. **`ck_ai_route_activations_tested` is unchanged.**
  - **`ai_route_tuples`:**
    - columns `id`, `route` (64), `model` (64), `effort` (8), `prompt_version` (5), `prompt_sha256` (64), `playbook_sha256` (64, **nullable**), `schema_version` and `created_at`;
    - the composite FK `(route, prompt_version, prompt_sha256)` → `prompt_snapshots`, and the FK `playbook_sha256` → `playbook_snapshots.sha256`;
    - CHECKs for `effort`, `schema_version` and `playbook_sha256`, as on the activations;
    - partial unique indexes `uq_ai_route_tuples_no_playbook` on `(route, model, effort, prompt_version, schema_version)` WHERE `playbook_sha256 IS NULL`, and `uq_ai_route_tuples_playbook` on the same columns plus `playbook_sha256` WHERE `playbook_sha256 IS NOT NULL`.
  - **`playbook_checks`:**
    - columns `id`, `checked_at`, `trigger` (16), `playbook_sha256` (64, **nullable**, FK → `playbook_snapshots.sha256`) and `problem` (16, **nullable**);
    - CHECKs: `trigger` is `startup` or `operator_check`; `problem` is NULL or one of the five; exactly one of `playbook_sha256` and `problem` is set.
  - **Registration.** Every column is mapped to `playbook` in `owners.py` and is `none` in `pii_registry.py`. Repositories have no update or delete for the three new tables.
- **`Playbook(clock).sync_prompts(files, defaults, playbook, *, trigger=SyncTrigger.STARTUP) -> PromptSync`.**
  - **Arguments.** `playbook` is a `PlaybookSnapshot` or a `PlaybookProblem` (else `TypeError`). `SyncTrigger` is `startup` (actor `system:playbook`, cause `startup_sync`) or `operator_check` (actor `operator`, cause `operator_check`).
  - **No input.** With no files and no defaults it touches no table.
  - **Otherwise, in the one 2.8 transaction, in order:**
    1. **The 2.8 prompt work,** unchanged.
    2. **The Playbook snapshot.** A read snapshot whose SHA-256 isn't stored is inserted (`captured_at` = now).
    3. **The candidate tuples.** Per default, in order: its tuple is the default's route, model, effort, prompt version and schema version, the stored prompt SHA-256, and the read Playbook's SHA-256 when `uses_playbook`.
       - A Playbook default with a problem read gets no tuple.
       - A tuple with no stored row is inserted, with one `events` row: entity `ai_route_tuples`, `None` → `candidate`, the trigger's cause and actor.
       - Then each active activation of a default's route whose tuple has no row gets one, the same way.
    4. **Bootstrap activation** happens as in 2.8, but **only for defaults that don't use the Playbook**, with `playbook_sha256` NULL.
    5. **One `playbook_checks` row** records the trigger and the SHA-256 or the problem.
  - **After the commit:**
    - `prompts_synced` gains `candidate_count`;
    - a problem logs `playbook_invalid` (WARNING, `problem` code only);
    - `PromptSync` gains `candidates` (each `(route, tuple id)` created) and `playbook` (the read's version and SHA-256 with `stored` true or false, or the problem).
  - **Never in a log, event or error:** Playbook text.
- **Queries** (`app/queries/playbook.py`).
  - **`active_tuple`** also builds `playbook` from the snapshot that `playbook_sha256` names. A missing snapshot, or content whose hash isn't that SHA-256, raises `ValueError` naming the route only.
  - **`route_tuples() -> tuple[RouteTuples, ...]`,** by route name, covers every route with a tuple row or an activation:
    - `active`: the activation's model, effort, prompt and schema versions, Playbook version and SHA-256 (or none), `activated_at`, `bootstrap` and `actor`, or `None`;
    - `candidates`: the route's tuple rows that aren't the active tuple, newest first, each with its id, fields, Playbook version and SHA-256 (or none), and `created_at`.
  - **`playbook_status()`** gives the newest check (`checked_at`, `trigger`, `problem`) and the snapshot of the newest successful check (version, SHA-256, `captured_at`), each possibly `None`.
  - **`health_issues()`:** when the newest check has a problem, `HealthIssue("playbook_invalid", "playbook", "Sales Playbook: " + problem_text(problem))`; otherwise none.
- **`main`.**
  - **`read_playbook_file(path) -> PlaybookSnapshot | PlaybookProblem`.** File I/O outside any unit of work. An `OSError` (a missing file included) is `unreadable`. An `InvalidPlaybook` gives its problem.
  - **`serve(read_playbook=read_playbook_file)`** reads `config.playbook_path` beside `load_prompts` and passes the result to the sync.
    - A problem never stops `serve`. It prints `warning: playbook: <problem text>` and continues.
    - `InvalidPrompts` and other failures behave as in 2.8.
  - **`build_health_issues`** registers `IssueSource("playbook", playbook_queries.health_issues)` after `backups`.
  - **`build_app`** passes `create_app` a `Playbook(clock)` and a loader returning `(files, defaults, read_playbook_file(config.playbook_path))`, run outside any unit of work.
- **The AI content view** (`web/ai_content_page.py`, `templates/ai_content.html` and `ai_content_panel.html`).
  - **Routes.** `GET /ai` (`ai_content_page`) and `POST /ai/check` (`ai_content_check`), both `@personal_data(False)`. The page is linked from Settings: "AI prompts, the Sales Playbook and route tuples are on the <a href="/ai">AI content page</a>.".
  - **The panel** shows:
    - the Sales Playbook block: the current version and 12-hex short SHA-256, when it was captured, and the last check with its time, trigger and problem;
    - the "Check for changes" form (`method="post"`, `hx-post`, `hx-target="#ai-content-panel"`, `hx-swap="outerHTML"`, `{{ csrf_field() }}`);
    - per route: the active tuple (model, effort, prompt version, Playbook version and short SHA, or "No Playbook"), "No active tuple" when there is none, and every candidate with the same fields and its created time.
  - **The POST.** Its only field is `csrf_token`; anything else is `MalformedForm` (400). It runs the loader, then `sync_prompts(..., trigger=OPERATOR_CHECK)` in its own unit of work, then renders the panel in a read-only one.
    - **Notices.** `role="status"`: "Checked for changes: <n> new candidate tuple(s)." It adds "Stored Sales Playbook version <v> (<sha12>)." when a snapshot was stored. `role="alert"`: the problem text when the read failed.
    - **`InvalidPrompts`:** 200, an alert "Nothing was stored: <message>".
- **Tests** run as `test`, with temporary data dirs and Playbook copies, never the real keyring or network. They never edit `docs/playbook/sales-playbook.md`.

**Never:**
- Relax `ck_ai_route_activations_tested`, write a non-bootstrap activation, or add `playbook.activate`, `ai_test_cases`, `ai_test_runs`, `run_ai_test_sets` or any job or trigger (Story 4.5).
- Add a drafting route, prompt file, output schema or candidate (Story 4.15), or change a shipped candidate, prompt file or `manifest.lock`.
- Read the live Playbook file anywhere but `main`, or send Playbook text the snapshot didn't hold.
- Add a dependency, adapter, port, setting or navigation section.

### Decisions made overnight (pending user review)

1. **Candidate tuples are rows in a new `ai_route_tuples` table** (owner `playbook`), one per distinct tuple the sync has seen, active ones included. Its ULID is the "tuple id" of 4.5's `run_ai_test_sets:<tuple id>:<set>` and 4.12's `route_tuple_id`. Activations can't hold candidates without relaxing `ck_ai_route_activations_tested`. This adds to the spine's owner map.
2. **Each Playbook read is a `playbook_checks` row** (owner `playbook`, append-only), so `queries.playbook.health_issues()` reads the problem from the database. This adds to the owner map.
3. **A candidate is each route's default candidate** (the first in `routes.py`) with its prompt snapshot and, for a Playbook route, the Playbook just read. Older candidates get no new tuples. A Playbook revert or an unchanged file creates nothing.
4. **A Playbook route is never bootstrap-activated:** it serves only after 4.5's gate. No shipped route uses the Playbook yet.
5. **The order of authority is in the frame** the gateway puts around every pinned Playbook snapshot. So every Playbook route's system prompt, drafting's included, states it, and a contract test pins the frame. Story 4.15 adds the drafting prompt, schema and candidate; this story ships none.
6. **The whole file is the snapshot,** front matter included, normalised as above. Its limit is 100,000 characters (today's is 7,965 bytes).
7. **A new hash is stored even when its front-matter version is already used.** The AC stores each new hash, and the view shows the version and a short SHA, so the two snapshots stay distinguishable.
8. **A Playbook problem never stops `serve`:** extraction doesn't use the Playbook. It's recorded, warned about and shown on the health panel. A missing file is `unreadable`.
9. **The AI content view is at `/ai`,** linked from Settings, because UX-DR1 fixes the navigation.
10. **A Playbook tuple's `key` gets a `:p<sha12>` suffix,** so `ai_usage` tells Playbook versions apart.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| First start | empty DB, real files | 3 prompt snapshots; 1 Playbook snapshot (`0.2.0`); 3 tuple rows (no Playbook) with 3 `candidate` events; 3 bootstrap activations (`playbook_sha256` NULL); 1 check (`startup`) | N/A |
| Restart | nothing changed | only 1 new check row; 0 candidates | N/A |
| Playbook edited | new hash, valid version; a probe Playbook default | 1 new snapshot; 1 new candidate with the new SHA; active unchanged, not activated | N/A |
| Playbook reverted | a stored hash | no snapshot, no candidate; check names it; health issue cleared | N/A |
| No version | front matter without `version` | no snapshot; check `no_version`; health issue; no Playbook candidate; extraction unaffected; `serve` starts | warning printed |
| Other problems | a bad version, a repeated version, not UTF-8, too large, a missing file | `bad_version` / `not_utf8` / `too_large` / `unreadable`; as above | as above |
| BOM and CRLF | the same text with a leading BOM and CRLF line ends | the same SHA-256 as the plain LF text | N/A |
| New default | probe default with a changed effort | 1 new candidate; active kept; `active_tuple_not_default` | N/A |
| Pinned Playbook | activation naming a snapshot | `active_tuple` carries the snapshot; the request's system is [prompt, frame + snapshot]; the live file edited and synced again → byte-identical request | N/A |
| Tampered | snapshot content ≠ its SHA | refused | `ValueError` |
| Mismatch | Playbook tuple vs a non-Playbook candidate | 0 requests | `llm_unknown_route` |
| Check POST | button | panel re-rendered with the notice; 1 check (`operator_check`) | `InvalidPrompts` → 200 alert, nothing stored |
| Probe POST | an extra field | nothing stored | 400 |

</frozen-after-approval>

## Code Map

- **The sync to extend:** `nextron/app/playbook/api.py`: `Playbook.sync_prompts`, `_prompt_inputs`, `_sync` and `_warn_if_not_default`, with the module docstring. Keep the 2.8 behaviour and its tests: `tests/app/test_playbook_prompts.py:78` is the only test caller, through a helper.
- **Repositories to copy:** `nextron/db/repos/playbook/{snapshots,activations}.py` (the frozen `Stored*` row and `current().session`). The new ones are `playbook_snapshots.py`, `route_tuples.py` and `playbook_checks.py`. `activations.py` gains `playbook_sha256` in `StoredActivation`, `_COLUMNS` and `insert`, plus `all_active()` for the view.
- **Models:** `nextron/db/models/playbook.py`. `AiRouteActivationRecord` is at line 114, and its CHECK constants are in the same module. Add the three records and register them in `models/__init__.py`, `owners.py` and `pii_registry.py`. An Alembic drift test compares the models with the migrations, so keep them identical (a partial index uses `sqlite_where`).
- **Migrations:** `versions/0014_2_8_ai_routes.py` is the exact `ai_route_activations` table for `copy_from`. `versions/0019_3_2_contact_markets.py` is the rebuild pattern.
  - Add a migration test: an activation row from before survives, and the index SQL in `sqlite_master` still has `WHERE deactivated_at IS NULL`.
  - Head bumps to `0022_4_4_playbook_snapshots`:
    - `tests/contracts/test_persistence_contracts.py:81`, and its probes at 795–807 (`0022_*` → `0023_*`, `0023_b` → `0024_b`, plus any "revision 22" probe);
    - `tests/unit/test_migrations.py:39`;
    - `tests/app/test_serve_process.py:296`;
    - `tests/unit/test_serve.py:762/776` (`0022_more` → `0023_more`).
- **Reads:** `nextron/app/queries/playbook.py` (`active_tuple`). Its import rule is `nextron.domain` and `nextron.db.repos.playbook` only, and `HealthIssue` comes from `nextron.domain.health`. The precedent for a source is `nextron/app/queries/jobqueue.py:50`.
- **Domain:** `nextron/domain/ai.py`: `RouteTuple` at 159, `key` at 184 and `RouteSpec` at 198. `nextron/domain/errors.py` has `DomainError`.
- **Gateway:** `nextron/adapters/ai/routes.py`: `Candidate`, `find` at 147 and `default_specs` at 162. `nextron/adapters/ai/prompting.py`: `build_request` at 94, with its docstring. `nextron/adapters/ai/__init__.py:339` calls `find`.
- **`main`:**
  - `PromptLoader` (728) and `load_prompts` (1419);
  - the `sync_prompts` step (1442) and its `serve` call (1550);
  - `build_health_issues` (1046), `build_app` (751; `create_app(...)` at 866) and the module docstring's step 7;
  - `config.playbook_path` comes from `nextron/bootstrap.py`, and resolves to the repo's `docs/playbook/sales-playbook.md` by default.
- **The web:**
  - **Page pattern:** `nextron/web/facts_page.py` with `templates/facts*.html`, and `web/app.py` 615–660 (`facts_answer`, the `units.open()` pattern and the docstring list).
  - **Settings link:** `templates/settings.html:8`.
  - **Route lists:** `tests/contracts/test_web_contracts.py:158` and `:295`.
  - **Tests to copy:** `tests/app/test_facts_page.py`.
- **Other contract updates:**
  - `tests/contracts/test_source_tree.py` `FILES` gets every new file;
  - `tests/contracts/test_health_contracts.py` gets the sources list, if it is pinned;
  - in `tests/contracts/test_prompt_manifest.py`, keep `SHIPPED_CANDIDATES` unchanged and add the frame pin;
  - `tests/unit/test_ai_routes.py` (the exact table), `test_ai_domain.py` and `test_ai_prompting.py`;
  - `tests/unit/test_serve.py`: `no_prompts` at 108 and the loaders.
- **Docs:** `nextron-engine/README.md`: extend "Prompts and the active route tuple" with the Playbook snapshot, tuples, the view and the health issue.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/playbook.py`, `nextron/domain/ai.py` -- the Playbook values, `read_playbook`, the frame, and `RouteTuple.playbook`, `key` and `RouteSpec.uses_playbook`.
- [x] `nextron/adapters/ai/routes.py`, `prompting.py` -- `uses_playbook`, `find`, and the two-block system.
- [x] `nextron/db/alembic/versions/0022_4_4_playbook_snapshots.py`, `db/models/playbook.py`, `models/__init__.py`, `owners.py`, `pii_registry.py` -- the tables and the rebuild.
- [x] `nextron/db/repos/playbook/{playbook_snapshots,route_tuples,playbook_checks,activations}.py` -- reads and inserts.
- [x] `nextron/app/playbook/api.py`, `nextron/app/queries/playbook.py` -- the extended sync, `active_tuple`, `route_tuples`, `playbook_status` and `health_issues`.
- [x] `nextron/main.py` -- `read_playbook_file`, `serve` wiring, the health source and `build_app`.
- [x] `nextron/web/ai_content_page.py`, `web/app.py`, `templates/ai_content.html`, `ai_content_panel.html`, `settings.html` -- the view, the POST and the link.
- [x] Tests:
  - `tests/unit/test_playbook_domain.py` covers every `read_playbook` and frame row;
  - `tests/app/test_playbook_snapshots.py` covers every sync and query row of the matrix, the live-file edit and `health_issues`;
  - `tests/app/test_ai_content_page.py` covers the GET, the POST outcomes, the 400 and the Settings link;
  - extend `test_ai_domain.py`, `test_ai_routes.py`, `test_ai_prompting.py`, `test_ai_gateway.py` (the mismatch), `test_playbook_prompts.py`, `test_serve.py` (a problem doesn't stop `serve`), `test_serve_process.py` (1 Playbook snapshot, 3 tuples, 1 check) and the migration test;
  - the contracts: the head bumps, `FILES`, web routes, health sources, the frame pin, and that `AUTHORITY_ORDER` precedes the Playbook text in a built Playbook request.
- [x] `nextron-engine/README.md` -- the docs.

**Acceptance Criteria:**
- Given the full suite, when `pytest` and `lint-imports` run, then everything passes, `ai_eval` stays skipped, and every contract is kept.
- Given the shipped routes, when `serve` starts on a fresh data dir, then no route is activated with a Playbook and no non-bootstrap activation exists.

## Implementation Notes

Built as specified. What the spec left open, and where the code had to choose:

- **`nextron/domain/playbook.py`.**
  - `SyncTrigger` lives here (standard library only), with `actor` and `cause` properties, so
    the model CHECKs, the queries and the web can all name it. `MAX_PLAYBOOK_BYTES` is
    4 x `MAX_PLAYBOOK_CHARS`; `SHORT_SHA_LENGTH` is 12; `PlaybookSnapshot.short` is the 12-hex
    prefix.
  - **Front matter.** The fences are lines that are `---` once trailing spaces and tabs are
    dropped. A top-level `version:` line starts at the line's first character, so an indented,
    commented (`# version:`) or differently cased one doesn't count. The value is stripped, cut
    at its first `#` that follows a space or a tab and stripped again, then one pair of
    matching `"` or `'` is removed.
  - **Problem texts** start lowercase, so they read after `Sales Playbook: ` and
    `warning: playbook: `: "the file couldn't be read", "the file is longer than 100,000
    characters", "the file isn't UTF-8 text", "its front matter has no version line" (then
    `Add a line such as "version: 0.3.0" between the two --- lines at the top of the file.`) and
    "its front matter repeats the version line, or the version isn't 1 to 32 letters, digits,
    dots, plus or minus signs starting with a letter or digit", each followed by ", so no
    snapshot was stored.". `InvalidPlaybook(problem)` takes the problem and uses its text as
    the message.
  - `PlaybookSnapshot` also refuses content that can't encode as UTF-8, like `RouteTuple`.
- **`nextron/domain/ai.py`** gains `check_uses_playbook(value)`: `Candidate` (an adapter) may
  raise only `AdapterError` itself (`test_error_contracts`), so both it and `RouteSpec` use the
  domain check. `PLAYBOOK_HASH_PREFIX` is 12. The longest Playbook key is 109 characters, inside
  `ai_usage.route_tuple`'s 128.
- **`prompting.build_request`** builds the blocks as a list and sets `cache_control` on the
  last one, so a non-Playbook request is byte-identical to 2.8's (same key order).
- **The migration.** `trigger` is a column name, so raw SQL quotes it (`"trigger"`). The checks
  are `ck_playbook_checks_trigger`, `_problem` and `_outcome` (`(playbook_sha256 IS NULL) <>
  (problem IS NULL)`). The foreign keys are named by the convention:
  `fk_ai_route_activations_playbook_sha256_playbook_snapshots`,
  `fk_ai_route_tuples_route_prompt_version_prompt_sha256_prompt_snapshots`,
  `fk_ai_route_tuples_playbook_sha256_playbook_snapshots` and
  `fk_playbook_checks_playbook_sha256_playbook_snapshots`. The version check is
  `length(version) BETWEEN 1 AND 32 AND version GLOB '[0-9A-Za-z]*' AND version NOT GLOB
  '*[^0-9A-Za-z.+-]*'`. No triggers: the repositories simply have no update or delete.
- **Repositories.** `playbook_snapshots`: `get(sha256)`, `insert(sha256, version, content, at)`
  (`StoredPlaybook`, content hidden from `repr`). `route_tuples`: `find(route, model, effort,
  prompt_version, schema_version, playbook_sha256)` (a `None` SHA matches only a row without a
  Playbook), `every()` (by route, then `created_at` and `id` descending) and `insert(...)`.
  `playbook_checks`: `newest()`, `newest_read()` (the newest with a SHA-256) and `insert(...)`.
  `activations.insert` takes `playbook_sha256` as a required keyword.
- **The sync.**
  - `PromptSync` gains `candidates` and `playbook`, without defaults. `playbook` is
    `PlaybookRead(version, sha256, stored)`, the `PlaybookProblem`, or `None` when there was no
    input.
  - **No input** (no files and no defaults) returns `PromptSync((), (), (), (), (), None)`: no
    table is read or written, no check is recorded and `playbook_invalid` isn't logged (a toy
    migration chain in `test_serve.py` has no playbook tables). `prompts_synced` is still
    logged, with zero counts, as in 2.8.
  - The `playbook` and `trigger` types are checked after 2.8's input checks, before any
    transaction.
  - `active_tuple_not_default` also fires when the kept activation's use of the Playbook
    (`playbook_sha256` set or not) differs from the default's `uses_playbook`.
  - A Playbook default with no active activation is neither activated nor kept.
- **Queries.** `route_tuples()` returns `RouteTuples(route, active, candidates)` with
  `ActiveTuple` and `CandidateTuple` (each with `effort` as an `Effort` and `playbook` as a
  `PlaybookRef(version, sha256)` with `short`). A candidate is hidden when its model, effort,
  prompt version, schema version and Playbook SHA-256 equal the active activation's. A tuple
  naming a missing Playbook snapshot raises `ValueError` naming the route. `playbook_status()`
  returns `PlaybookStatus(last_check: LastCheck | None, current: PlaybookVersion | None)`.
- **`main`.**
  - `read_playbook_file(path)` reads at most `MAX_PLAYBOOK_BYTES` + 1 bytes, so a huge file is
    `too_large` without being loaded. A folder is `unreadable`.
  - `PlaybookReader` is the reader's type. The step is `sync_prompts(units, load, read, err,
    log_file)`: `load()`, then `read()`, then the warning (before the sync), then the unit of
    work.
  - `load_ai_content(playbook_path)` is the view's loader; `build_app` binds it with
    `functools.partial`. `create_app` takes it as `ai_content` beside `playbook`.
- **The view.**
  - The notice says "tuple" for one and "tuples" otherwise. The alert of a failed read is
    `Sales Playbook: <problem text>`, as on the health panel.
  - The trigger shows as "at engine start" or "with Check for changes"; the last check reads
    "<time>, <trigger>: <problem>" or "read without a problem.".
  - Each tuple shows its schema version too, and the active one "Activated <time> (bootstrap,
    by <actor>)" or "(tested, by <actor>)".
  - `ContentLoader` (the loader's type) is in `nextron/web/ai_content_page.py`.
  - Empty states: "No snapshot is stored yet.", "Not checked yet.", "No route has a tuple
    yet." and "No candidates.".
- **Tests.**
  - `tests/dashboard.py`'s `Dashboard` gains `playbook` and `ai_content` (default
    `shipped_ai_content`: the shipped prompts and the synthetic `TEST_PLAYBOOK`).
  - `tests/unit/test_serve.py`'s `dev` fixture copies the real Playbook into the temporary
    folder and points `NEXTRON_PLAYBOOK_PATH` at the copy; `run_serve` takes `read_playbook`.
    `tests/app/test_serve_process.py` does the same and checks one snapshot, three tuples
    without a Playbook, one startup check and no tested or Playbook activation.
  - The 2.8 helper in `test_playbook_prompts.py` passes a synthetic snapshot, and its two
    `PromptSync` equality checks compare the four 2.8 fields.
  - `test_prompt_manifest.py` pins `FRAME_SHA256` (of `playbook_section(PlaybookSnapshot("1.0.0",
    "CONTENT"))`) and checks the order in a built Playbook request. `SHIPPED_CANDIDATES` is
    unchanged.
  - `test_health_contracts.SOURCES` gains `playbook`; the web contracts gain the two routes and
    two templates; `test_source_tree.FILES` gains the eight new files.

**Implementation verification** (implementer, targeted): the spec's verification set plus every
dashboard and health test file, 1,685 + 997 passed and 1 skipped (`ai_eval`); the serve-process
tests passed; `lint-imports`: 29 kept, 0 broken. The full suite has not been run (rules §4a).

**Review fixes (pass 1):**

- **The view's loader is tested.** `test_serve.py` checks that `main.load_ai_content(path)` is
  `(*load_prompts(), read_playbook_file(path))` and `unreadable` for a missing file, and that
  the served app's `create_app(ai_content=...)` reads the `NEXTRON_PLAYBOOK_PATH` copy.
- **The view renders a Playbook route.** A `test_ai_content_page.py` test syncs a probe
  Playbook route, pins a tested activation to the first snapshot, syncs an edited Playbook and
  checks the active tuple's "version <v> (<sha12>)" with "(tested, by operator)" and the
  candidate's new version.
- **Each candidate shows its "Tuple id"** (the active tuple shows none), tested.
- **`app.css`** gains `.ai-route` (a bordered section per route), and `.ai-playbook` and
  `.ai-tuple` (two-column definition lists; each tuple has a rule above it). It was appended
  byte-preservingly; the first 293 CRLF lines are untouched.
- **The intro is truthful:** "A candidate never runs until the AI test gate activates it; the
  three extraction routes were activated at first start without a test run."
- **A version comment after a tab is cut too:** the value is cut at its first `#` after a space
  or a tab (`COMMENT`). The domain docstring, the README and the domain test say so.
- **The health issue says how to clear it:** its `detail` is "Fix the file, then use Check for
  changes on the AI content page (/ai) or restart the engine." A test renders the health panel
  with the problem and its detail, and shows it gone after a good check.
- **The logs name the trigger:** `prompts_synced` and `playbook_invalid` carry `trigger` (the
  trigger's value). The docstring, the README and the tests say so.
- **Drift by `uses_playbook` alone is tested:** one `active_tuple_not_default`, for
  `extract_people` only.
- **The migration test** also checks that `ck_ai_route_activations_effort` and
  `ck_ai_route_activations_schema_version` survive the rebuild.
- **Story 2.8's restart test is renamed**
  (`test_a_restart_with_the_same_files_changes_no_prompt_or_activation`) and asserts the one new
  check row.
- **The README** gives Story 2.8's section the current `sync_prompts(files, defaults, playbook)`
  signature, pointing to the 4.4 section, and says a change to `routes.py` takes effect, with its
  candidate tuple, at the next start (Check for changes can't see it).

**Final verification** (on the final tree, after the review fixes): the full suite through
`suite_lock.py` (track-C) gave 10,561 passed and 3 skipped (the two `ai_eval` tests and the
symlink test). `lint-imports`: 29 kept and 0 broken. The known runner flake did not occur.

## Spec Change Log

## Review Triage Log

**Pass 1.** Three layers reported 26 raw findings:
- Blind Hunter (B): 16 bullets, with B8 split into B8a and B8b, so 17;
- Edge Case Hunter (E): 4;
- Verification Gap (V): 3 gaps (pre-verified) plus 2 other findings.

Merging duplicates leaves 22: V1+B9, V2+B8a, V-o1+B15 and B5+E1.
- **Routing:** 14 patch, 0 defer and 8 reject.
- **Loopbacks:** there are no `intent_gap` or `bad_spec` entries, so nothing loops back.

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | V1+B9: the real loader `main.load_ai_content` and `build_app`'s `functools.partial(load_ai_content, config.playbook_path)` are never run by a test. | gap (pre-verified) | No test names `load_ai_content`. Every `/ai/check` test injects `shipped_ai_content`. A wrong path binding would pass. | patch (test `load_ai_content` directly, and `build_app`'s `ai_content` reading the `NEXTRON_PLAYBOOK_PATH` copy) |
| 2 | V2+B8a: the view never renders a Playbook tuple or a tested activation. | gap (pre-verified) | `test_ai_content_page.py` asserts only "No Playbook" and "(bootstrap, by seed)". | patch (render a probe Playbook route with a pinned, tested activation and a candidate; assert the labels) |
| 3 | V3: the drift warning's new `uses_playbook` comparison is never exercised. | gap (pre-verified) | No test keeps an activation whose Playbook use differs from its default's. | patch (one warning for that route) |
| 4 | V-o2: README's Story 2.8 section still documents `sync_prompts(files, defaults)`. | low | Real, `README.md` about line 2632. It is a direct doc correction. | patch |
| 5 | B1: the "new candidate tuples" count and the `candidate` events include tuples that are, or at once become, the active tuple, which the panel hides. | low | Every new tuple row gets `None → candidate`, as the spec says (Boundaries, step 3). Via the POST the mismatch needs a tuple matching the active one with no row. Only the startup sync meets that: legacy activations, and new non-Playbook routes, which need a restart. | reject: spec-mandated event; mismatch unreachable from the button |
| 6 | B2: `SHIPPED_CANDIDATES` doesn't pin `uses_playbook`, so flipping it on a shipped candidate would orphan its activations unseen. | false (first half) | `test_a_shipped_candidate_is_still_found` builds a tuple without a Playbook, and `find` now requires `uses_playbook` to match, so a flipped flag fails that test. The second half (4.15 can't list a Playbook candidate there as is) is that story's contract extension. | reject (noted for Story 4.15) |
| 7 | B3: a Playbook problem never says how to clear it, and the health issue has no `detail`. | low | Real: the issue clears only at the next good read, which is a restart or Check for changes. Adding a `detail` sentence is a direct correction. | patch (a `detail` that says to fix the file, then use Check for changes on the AI content page or restart) |
| 8 | B4: Check for changes can't see a changed model or effort in `routes.py`, because `default_specs()` is in memory. | low | Real, but a `routes.py` change takes effect only at the next start anyway, which is when its candidate appears. A doc sentence fixes the gap. | patch (README: the button re-reads the prompt files and the Playbook; a `routes.py` change gets its candidate at the next start) |
| 9 | B5+E1: `POST /ai/check` catches only `InvalidPrompts`, so another error is a bare 500. | low | Real, but needs a rare OS error reading a prompt file. Other dashboard POSTs behave the same, and the fix adds a guard branch. | reject |
| 10 | B6: `TupleView.id` is set but never rendered. | low | Real (`ai_content_page.py`). The id is the "tuple id" Story 4.5 runs. Rendering it is a direct template addition. | patch (render "Tuple id" for candidates) |
| 11 | B7: `ai-tuple`, `ai-playbook` and `ai-route` have no CSS. | low | Real: unstyled `<dl>` blocks run together. Small rules in `app.css` are a direct addition. | patch (minimal rules, edited byte-preserving) |
| 12 | B8b: nothing renders the health panel with `playbook_invalid`. | low | Real: only `health_issues()` and the registry are tested. A render test is direct. | patch |
| 13 | B10: the `/ai` intro says "a candidate runs only once it has passed its AI Test Set", but the gate doesn't exist yet and the extraction routes run untested bootstrap tuples. | low | Real wording mismatch. A direct text correction. | patch |
| 14 | B11: `version: 0.3.0<TAB># note` is `bad_version`. | low | Real: only `" #"` is cut. A one-pattern correction (whitespace, then `#`). | patch |
| 15 | B12: `prompts_synced` and `playbook_invalid` don't say which trigger ran. | low | Real. Adding `trigger` to `extra` is direct. | patch |
| 16 | B13: `playbook_checks` has no retention. | low | One small row per start or click; pruning adds a job or branch. | reject |
| 17 | B14: the migration test doesn't check that `ck_ai_route_activations_effort` and `_schema_version` survived the rebuild. | low | Real gap; the drift compare ignores CHECKs. A direct test addition. | patch |
| 18 | V-o1+B15: `route_tuples()` raises for a tuple naming a missing Playbook snapshot, so `GET /ai` is a 500. | low | Unreachable without hand-editing the database with foreign keys off (the FKs on `playbook_sha256` refuse it); it fails loudly, as `active_tuple` does. | reject |
| 19 | B16: `test_a_restart_with_the_same_files_writes_nothing` is misnamed: every sync now writes a `playbook_checks` row. | low | Real. A rename, and an assertion of the one check row. | patch |
| 20 | E2: a stored snapshot hand-edited to be blank or too long raises `PlaybookSnapshot`'s `ValueError` without the route. | low | Needs a tampered database; it still fails loudly. | reject |
| 21 | E3: a file read mid-save could store a truncated Playbook as a snapshot. | low | Rare. The result is only an extra snapshot and candidate, which never serves without passing Story 4.5's gate. | reject |
| 22 | E4: a Playbook route whose reads always failed has no tuple row or activation, so it's missing from `/ai`. | low | No shipped route uses the Playbook. The problem shows as the view's alert and a health issue. Listing configured routes adds a new input. | reject (noted for Story 4.15) |

## Design Notes

```python
# main.serve, after record_pre_migrate (file I/O first, then one unit of work)
files, defaults = load_prompts()
read = read_playbook(config.playbook_path)       # PlaybookSnapshot | PlaybookProblem
with units.open():
    Playbook(SystemClock()).sync_prompts(files, defaults, read)

# gateway system for a pinned Playbook tuple (prefix cached up to the last block)
[{"type": "text", "text": prompt},
 {"type": "text", "text": playbook_section(snapshot), "cache_control": {"type": "ephemeral"}}]
```

## Verification

**Commands** (from `nextron-engine/`):
- `uv run pytest -q tests/unit/test_playbook_domain.py tests/app/test_playbook_snapshots.py tests/app/test_ai_content_page.py tests/app/test_playbook_prompts.py tests/unit/test_ai_*.py tests/unit/test_serve.py tests/unit/test_migrations.py tests/contracts` -- expected: all pass.
- `uv run lint-imports` -- expected: every contract kept.
- The full suite through `suite_lock.py` (orchestrator only) -- expected: pytest and `lint-imports` both pass.
