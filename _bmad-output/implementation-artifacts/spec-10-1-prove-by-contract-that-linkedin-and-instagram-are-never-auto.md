---
title: 'Story 10.1: Prove by contract that LinkedIn and Instagram are never automated'
type: 'feature'
created: '2026-11-18'
status: 'done'
baseline_commit: '2bc36d85b44b2a32081567988717ab1d32cf63c6'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-10-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** FR-49 (never sign in to, browse, fetch or post to LinkedIn or Instagram) is held today by scattered checks: an import-linter ban on four libraries, the fetch-policy denylist and per-adapter egress tests. Nothing stops a future change from adding a LinkedIn adapter, port, job type or library, and no test proves the stored `contacts.linkedin_url` is never fetched.

**Approach:** Add build-time contracts that fail on any such future code (module and top-level names, ports outside AD-9, job types, platform hosts in code and templates, banned libraries in imports and `uv.lock`), an explicit platform check in the renderer's request handler, and a write-path contract plus runtime guard on where a LinkedIn URL may come from.

## Boundaries & Constraints

**Always:**
- Story ACs (epics.md Story 10.1) are binding: `test_no_linkedin_adapter`; c4-banned-everywhere also covers `pyautogui`, `undetected_chromedriver`, `linkedin`, and a test asserts none of the banned packages is in `uv.lock`; renderer interception aborts any denylisted host (LinkedIn badge, Instagram embed), proven with the renderer fake; the crawler denylist tests cover `linkedin.com` + subdomains, `lnkd.in`, `instagram.com` for first URLs and redirects; `linkedin_url` sources are only Signal Inbox, website, CSV, referral, Operator, each recorded in Provenance, and no code path fetches it.
- Every allowlist is explicit, minimal, has a one-line reason, and is itself checked (each entry exists; name allowlists hold only `nextron.domain.*` modules).
- Each new contract has a negative probe proving it catches a planted violation (tmp copy or synthetic input, never editing real source).

**Never:**
- Weaken, remove or loosen any existing contract, test or denylist entry.
- Add a dependency, migration, route or job type.
- Touch the root `HANDOFF.md`/`README.md`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Error Handling |
|---|---|---|---|
| Planted module | `nextron/adapters/linkedin/__init__.py` or `.../instagram_feed.py` | `test_no_linkedin_adapter` fails, naming it | N/A |
| Planted port | new `class LinkedInPort(Protocol)` in `nextron/ports` | port-list contract fails | N/A |
| Planted import | `import pyautogui` / `import undetected_chromedriver` / `import linkedin` anywhere | lint-imports BROKEN | N/A |
| Render sub-resource | image/script/iframe/xhr on `platform.linkedin.com`, `static.licdn.com`, `www.instagram.com/embed.js`, `scontent.cdninstagram.com` | aborted `blockedbyclient`, no fetch, no lookup, fetch cap not consumed | N/A |
| Contact with LinkedIn URL from `finder`, `pattern` or `replier` | `add_contact(..., linkedin_url set)` | `ValueError`, nothing stored | raises before any write |

### Decisions made overnight (pending user review)
- Also ban `seleniumwire`, `playwright_stealth` and `linkedin_scraper` (platform-automation/anti-bot tools) in c4-banned-everywhere and their dists in the lock test; stricter only.
- Add `licdn.com` and `cdninstagram.com` (the platforms' own static CDNs, which serve LinkedIn badges and Instagram embeds) to `DENIED_DOMAINS`; stricter only.
- Job type names containing `linkedin` or `instagram` fail unless on an explicit allowlist that starts empty; Stories 10.4/10.5 add their job types there with a reason.
- `add_contact` refuses a LinkedIn URL from any source outside `LINKEDIN_URL_SOURCES` (runtime guard, not only a test).

</frozen-after-approval>

## Code Map

- `nextron-engine/pyproject.toml` -- `[[tool.importlinter.contracts]]` id `c4-banned-everywhere` (~line 183): add modules.
- `nextron-engine/tests/contracts/test_import_contracts.py` -- pins c4-banned set (line ~81) and probe params (line ~118): extend both.
- `nextron-engine/tests/contracts/test_lockfile.py` -- `_lock()` keyed by dist name; `test_banned_package_is_not_in_the_lock` parametrize: extend.
- `nextron-engine/nextron/ports/*.py` -- Protocol classes today: AD-9 ones plus `EmailSyntaxPort`, `GoogleOAuthPort`, `ConsentListener`, `ClientFilePort` (Stories 1.9/2.3, approved extras; allowlist them with reasons).
- `nextron-engine/nextron/domain/identity.py` -- `normalize_linkedin_url`, `LINKEDIN_*` constants; `nextron/domain/lead_import.py` `NOTE_BAD_LINKEDIN`: the only top-level linkedin-named definitions today.
- `nextron-engine/nextron/domain/fetch_policy.py` -- `DENIED_DOMAINS`, `is_denied`; host literals also in `identity.py`, `lead_import.py`.
- `nextron-engine/nextron/adapters/renderer/__init__.py` -- `_Render._serve`: add `is_denied(request.url)` refusal before the cap counters/fetch; update docstring.
- `nextron-engine/tests/unit/test_renderer_adapter.py` -- `World` fake browser; existing denylist sub-resource/redirect tests (~355-397) stay.
- `nextron-engine/tests/contracts/test_fetch_policy_contracts.py` -- per-adapter first-URL/redirect denylist drivers (~50-70, 229-300): extend URLs so linkedin subdomain, `lnkd.in`, `instagram.com` all appear for both first URL and redirect.
- `nextron-engine/nextron/domain/contacts.py` -- `ProvenanceSource`; add `LINKEDIN_URL_SOURCES`.
- `nextron-engine/nextron/app/research/api.py` -- `_check_arguments` (~615): add guard; callers `app/discovery/api.py:536` (csv), `app/research/people.py:507` (website, no URL); `ContactCandidate(linkedin_url=...)` built only in `domain/lead_import.py:451`.
- `nextron-engine/nextron/main.py` `build_job_types` -- see `tests/contracts/test_backup_contracts.py:181` for building it with no services.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/pyproject.toml` -- add `pyautogui`, `undetected_chromedriver`, `linkedin`, `seleniumwire`, `playwright_stealth`, `linkedin_scraper` to c4-banned-everywhere; update its comment.
- [x] `nextron-engine/tests/contracts/test_import_contracts.py` -- pin the new set; add probe params for each new module.
- [x] `nextron-engine/tests/contracts/test_lockfile.py` -- assert none of `linkedin-api`, `linkedin`, `linkedin-scraper`, `instaloader`, `instagrapi`, `selenium`, `selenium-wire`, `pyautogui`, `undetected-chromedriver`, `playwright-stealth` is in the lock.
- [x] `nextron-engine/tests/contracts/test_no_linkedin_adapter.py` (new) -- `test_no_linkedin_adapter`: walk `nextron/` (py modules and packages, case-insensitive `linkedin|instagram` in any segment) with a domain-only allowlist (empty now); top-level function/class/constant names with those words only in allowlisted domain modules (`identity`, `lead_import`); ports: every `Protocol` class in `nextron.ports` is in AD-9 ∪ approved extras; job types from `build_job_types` named with those words fail unless allowlisted (empty); platform host literals (`linkedin.com`, `lnkd.in`, `licdn.com`, `instagram.com`, `instagr.am`, `cdninstagram.com`) in non-docstring Python strings only in `domain.fetch_policy`, `domain.identity`, `domain.lead_import`, and in no template or static file (vendored htmx excluded); each check has a negative probe.
- [x] `nextron-engine/nextron/domain/fetch_policy.py` + its tests -- add `licdn.com`, `cdninstagram.com`.
- [x] `nextron-engine/nextron/adapters/renderer/__init__.py` + `tests/unit/test_renderer_adapter.py` -- explicit denylist refusal; parametrized test over the matrix's badge/embed URLs and types (image, script, document iframe, xhr, main-frame later navigation) proving abort, no lookup, no fetch, no metering, cap untouched.
- [x] `nextron-engine/tests/contracts/test_fetch_policy_contracts.py` -- extend first-URL and redirect cases per Code Map.
- [x] `nextron-engine/nextron/domain/contacts.py`, `nextron/app/research/api.py` -- `LINKEDIN_URL_SOURCES` = signal_inbox, website, csv, referral, operator; guard in `_check_arguments`.
- [x] `nextron-engine/tests/contracts/test_linkedin_url_write_path.py` (new) -- AST: every `ContactCandidate(... linkedin_url=...)` and every `add_contact` caller is in a known allowlist with its source; `LINKEDIN_URL_SOURCES` pinned; no call to `fetch_page`/`fetch_robots`/`fetch_resource`/`render`/`list_postings`-style fetch method takes an expression mentioning `linkedin_url`; every `normalize_linkedin_url` output is `is_denied`; guard behaviour (refused sources raise, allowed ones store with the Provenance Record).
- [x] `nextron-engine/README.md` -- short Story 10.1 section.

**Acceptance Criteria:**
- Given the tree as committed, when the contract suite and `lint-imports` run, then all pass; given each planted violation in the matrix, the matching contract fails.

## Implementation Notes

- **c4-banned-everywhere** (`pyproject.toml`) now also forbids `pyautogui`, `undetected_chromedriver`, `linkedin`, `seleniumwire`, `playwright_stealth`, `linkedin_scraper`; `test_import_contracts.py` pins the set and lint-probes each new module (8 new probe params, `linkedin` twice).
- **Lock** (`test_lockfile.py`): `PLATFORM_AUTOMATION_DISTS` (the ten dists in the task) parametrise `test_banned_package_is_not_in_the_lock`, now comparing PEP 503-normalised names; `test_a_planted_banned_package_is_caught` appends a synthetic `[[package]]` to the real lock text and proves it is caught.
- **Denylist**: `licdn.com`, `cdninstagram.com` appended to `DENIED_DOMAINS` (docstring and `test_fetch_policy.py` pinned list, denied and lookalike-allowed cases updated).
- **Renderer**: `_Render._serve` returns `False` on `is_denied(request.url)` as its first statement, before the method/deadline check, the cap counters and `fetch_resource`; docstring updated. Tests in `test_renderer_adapter.py`: 4 URLs × 5 types (image, script, child-frame document, xhr, fetch) abort `blockedbyclient` with no lookup, no request, only the main document metered; a later main-frame navigation to each aborts with `"aborted"` (the existing rule for any refused later navigation, so the page stays in place, rather than the matrix's `blockedbyclient`); 100 denied requests then 99 allowed scripts all fulfilled (cap untouched); the fetcher is never asked for a platform URL. The last two fail with the handler check removed (verified by monkeypatching `is_denied` to `False`).
- **Fetch-policy contracts**: first URLs and redirects now include bare and subdomain `linkedin.com`, `lnkd.in`, `licdn.com`, bare `instagram.com`, `instagr.am`, `cdninstagram.com` for every fetching adapter driver.
- **`test_no_linkedin_adapter.py`** (new): AST checks over `nextron/` (parsed once per path via a cached `parse`) for module names, top-level names, `Protocol` ports (AD-9's 18 + 4 approved extras with reasons), job types (from `build_job_types` called with a stub per required parameter, read from its signature so new services don't break it) and platform host literals (non-docstring strings in Python; any non-Python file under `nextron/`, vendored `htmx-*.min.js` excluded). Allowlists carry one-line reasons and are checked (entries exist, domain-only, still needed). Each check has negative probes on a synthetic `tmp_path` tree.
- **Name allowlist includes `nextron.domain.contacts`** besides `identity` and `lead_import`: this story's own `LINKEDIN_URL_SOURCES` lives there, so the Code Map's "only top-level linkedin-named definitions today" grew by one.
- **Write path**: `LINKEDIN_URL_SOURCES` (frozenset of the five sources) in `domain/contacts.py`; `research.api._check_arguments` raises `ValueError("add_contact: a LinkedIn URL never comes from <source>")` before any read or write. `test_linkedin_url_write_path.py` (new): pins the set; allowlists `ContactCandidate` with `linkedin_url` (keyword, 4th positional, or `replace(..., linkedin_url=)`) to `lead_import:_Planner.contact` (csv) and `add_contact` callers to `LeadImports.run` (csv) and `PeopleExtractions._contacts` (website), checking each call's `source=`; no call to a fetch method (crawler/renderer/job-board port methods read from `nextron.ports` + `fetch_resource`, `fetch_json`) has an argument naming linkedin/instagram; normalised profile URLs are `is_denied` and `url_refusal == denylisted`; DB-backed guard tests (reusing `tests.app.test_contactable`'s `world` fixture): finder/pattern/replier refused with no row in `contacts`, `provenance_records` or `events`; each allowed source stores the URL with a `contact` Provenance Record of that source; replier without a URL still stores. Negative probes for each AST check.
- **AD-16 callers contract** (`tests/contracts/test_ad16_callers.py` `_add_match`): its match scenario filled a LinkedIn URL for every Contact source, which the new guard now refuses for `finder`, `pattern` and `replier`; for those three it fills a role instead (same two gate calls, same blocked-path expectations). The scenario count and gate assertions are unchanged.
- **Review patches (iteration 1):** name allowlist is now keyed by `module.qualified_name` (7 entries, incl. `lead_import._Planner._linkedin`), host allowlist by `(module, exact literal)` (10 entries); defined names also read under module-level `if`/`try`/`with`/`for`/`while`, methods and nested classes, and import `as` aliases (function bodies and class fields still not read); `PLATFORM_HOST` has a right boundary (`lnkd.info`, `linkedin.company.example` no longer match); `_job_type_names` stubs every parameter, defaulted ones included. Write-path contract: `**`/`*` unpacking counts as possibly carrying `linkedin_url`; new `repo_write_problems` flags any call to the contacts repo's `insert`/`fill` with `linkedin_url` or `**` outside `research.api` (`Research.add_contact`, `Research._resolve` allowlisted); fetch tripwire now reads every public (sync or async) method of every `Protocol` in `nextron.ports` (no real call site failed), and the docstring/README call it a tripwire, the guarantee being the denylist every fetching adapter applies. c4 bans also `selenium_stealth`, `undetected_playwright`, `pyppeteer_stealth`, `instagram_private_api`; the lock list adds `selenium-stealth`, `undetected-playwright`, `pyppeteer-stealth`, `python-linkedin`, `python3-linkedin`, `instagram-private-api`, and the planted-package probe uses the real test's `banned_in_lock` helper and asserts the planted dist itself is reported. Renderer platform cases add stylesheet, font, media, ping (POST) and xhr POST.
- No dependency, migration, route or job type added. README: Story 10.1 section under "Layers and import contracts", plus the CDNs in "What is never fetched".

## Spec Change Log

## Review Triage Log

Pass 1 (blind hunter 12, edge-case hunter 9, verification gap 0 = 21 raw; 14 after grouping; 13 patch, 1 rejected, 0 deferred).

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| 1 | blind, edge | `ContactCandidate(**row)` / `replace(c, **changes)` / starred args bypass `candidate_problems` | medium | `_keyword` matches `kw.arg == "linkedin_url"` only; a `**` keyword has `arg=None` | patch: treat `**`/`*` as possibly carrying the URL; probe |
| 2 | blind, edge | Direct `contacts_repo.insert/fill(linkedin_url=...)` skips `add_contact`'s guard, uncaught | medium | contract scans only `ContactCandidate` and `add_contact` calls | patch: flag repo insert/fill calls passing `linkedin_url` or `**` outside `nextron.app.research.api`; probe |
| 3 | blind, edge (x2) | Fetch check is syntactic (`url = c.linkedin_url; fetch(url)` passes) while README claims proof | low | true of the AST check; the real guarantee is the denylist every fetching adapter applies | patch: README/docstring call it a tripwire backed by the denylist |
| 4 | blind, edge | `FETCH_METHODS` covers 3 ports only and skips `async def` methods | medium | `_fetch_methods` reads crawler/renderer/job_board and `ast.FunctionDef` only | patch: read every Protocol method in `nextron/ports` (sync and async) |
| 5 | blind, edge | Name check misses defs under module-level `if/try/with`, class methods, import aliases | medium | `_defined_names` walks `tree.body` only | patch: walk module-level compound bodies, methods in classes and `as` aliases; probes |
| 6 | blind, edge | Banned-library gaps (`selenium-stealth`, `undetected-playwright`, `pyppeteer-stealth`, `python-linkedin`, `python3-linkedin`, `instagram-private-api`) | low | not in c4 or the lock list; stricter-only addition | patch: add modules to c4 + dists to the lock test |
| 7 | blind | Write-path docstring claims every `add_contact` caller's source is in `LINKEDIN_URL_SOURCES`; untested and not true by design | low | test asserts only `CANDIDATE_WITH_LINKEDIN_URL` | patch: reword docstring |
| 8 | blind | Name/host allowlists exempt whole modules | medium | allowlist keys are module names | patch: key by `module.name` and by `module` + exact literal |
| 9 | blind | Lock probe passes whenever any banned dist is present, re-implements check | low | `any(...)` over the whole list | patch: share helper, assert the planted spelling is caught |
| 10 | blind | Renderer test docstring says crawler checks are out of the way, they aren't | low | wrapper still calls the real `fetch_resource` | patch: reword |
| 11 | blind | Platform tests skip stylesheet/font/media/ping and non-GET; WebSocket and service workers not intercepted | low | WebSocket and SW part false: `route_web_socket` closes every socket and the context uses `service_workers="block"`; type/method coverage gap real | patch: add those types and a POST case |
| 12 | blind | `PLATFORM_HOST` lacks a right boundary (`lnkd.info` flagged); README line overlong | low | regex has a left lookbehind only | patch: right boundary + probe; rewrap README |
| 13 | edge | `Protocol` imported under an alias escapes the port check | low | real but unlikely; fix needs alias resolution (added complexity) | reject |
| 14 | edge | `build_job_types` params with defaults (e.g. `domain_checks=None`) aren't stubbed, so conditionally registered types escape | medium | `_job_type_names` stubs only required params | patch: stub every parameter |

## Verification

**Commands:**
- `cd nextron-engine && uv run pytest -q tests/contracts tests/unit/test_renderer_adapter.py tests/unit/test_fetch_policy.py tests/app/test_leads.py` -- all pass
- `cd nextron-engine && uv run lint-imports` -- all contracts KEPT
