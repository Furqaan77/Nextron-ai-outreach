---
title: 'Story 3.10: Deploy the unsubscribe Worker'
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: '505c05b625f21a6e4f467d6e4a273c889207cf4f'
review_loop_iteration: 0
story_key: '3-10-deploy-the-unsubscribe-worker'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-3-8-issue-and-decode-unsubscribe-tokens.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Every Step's `List-Unsubscribe` URL (RFC 8058, FR-70) must reach an endpoint that records the opt-out at once, even while Nextron's PC is off. Story 3.8 fixed the token format and the vectors; nothing receives a click yet.

Requirements: FR-70, AD-14, AD-15, NFR-4, NFR-17, NFR-35.

**Approach:** A Cloudflare Worker in `nextron-engine/workers/unsubscribe/` (`wrangler.jsonc`, `package.json` with wrangler `4.162.0` exact and its committed lockfile, `src/index.js`). It checks only the token's tag, records each valid token once in KV, and serves `GET /pending` behind a bearer secret for Story 3.11. Its tests run locally with Node's built-in test runner, against the handler directly and against `wrangler dev --local`. The acceptance criteria are Story 3.10 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**
- **Token check (edge).** Valid means: exactly 108 chars of `[A-Za-z0-9_-]`; base64url-decodes to 81 bytes; `kid = raw[0]` is 1–255; the secret `K_EDGE_<kid>` exists and is canonical base64url of 32 bytes (43 chars, or 44 ending in one `=`); and `raw[69:81]` equals the first 12 bytes of HMAC-SHA256 (Web Crypto) under that key over `raw[0:69]`, compared in constant time. Anything else is invalid, and the check never throws.
- **Routes** (the only ones; anything else, HEAD included, is 404 with an empty body):
  - `POST /u/<token>`: invalid → 400, empty, no KV call. Valid → `get("p:<token>")`; only if it is absent, `put("p:<token>", "", {metadata: {received_at}, expirationTtl: 2592000})`. Then 200, empty. `received_at` is the UTC time at whole seconds, `YYYY-MM-DDTHH:MM:SSZ`. The request body is never read.
  - `GET /u/<token>`: a self-contained HTML page (inline CSS and one inline script, no external asset), never a KV call. Valid: a button whose script POSTs to the same URL and then shows "You're unsubscribed. You won't get further emails from this sender."; without script the form still POSTs. Invalid: status 400 and "This link isn't valid", with no button.
  - `GET /pending`: `Authorization: Bearer <x>` must equal `PENDING_BEARER` or `PENDING_BEARER_NEXT`, comparing SHA-256 digests in constant time. A configured secret shorter than 43 chars counts as unset. Otherwise 401, empty. 200 returns `application/json` `{"format": "nextron-unsub-pending/1", "entries": [{"token", "received_at", "expiration"}]}`, following every `list({prefix: "p:", cursor})` page until `list_complete`. `received_at` is from the metadata (`null` if missing); `expiration` is KV's Unix seconds (`null` if missing).
- **Every response** has `Cache-Control: no-store`, `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`. The page adds a CSP with `default-src 'none'`, the script allowed by its `sha256-` hash, `form-action 'self'`, `connect-src 'self'`, `frame-ancestors 'none'` and `base-uri 'none'`.
- **The source** never calls KV `delete`, `console`, `request.cf`, or any body reader. The only header it reads is `authorization`.
- **`wrangler.jsonc`:**
  - It has `workers_dev: false`, `preview_urls: false`, observability (Workers Logs) disabled and `logpush: false`, at top level and in each env.
  - It has envs `dev`, `test` and `prod`. Each has its own name, its own KV binding `UNSUB` with a distinct id, and its own custom-domain routes: `u.<domain>` for `prod`, `u-dev.<domain>` and `u-test.<domain>` for the others.
  - Until Story 3.9 names the domains, hosts use `sending-domain-1.invalid` and KV ids are placeholders.
- **Scripts:** `test` (`node --test`), `deploy:<env>` and `secret:<env>`. Both deploy and secret first run `scripts/check-config.mjs <env>`. It refuses an unknown env, any `.invalid` host, a placeholder or non-32-hex KV id, a `*.workers.dev` route, and logs, `workers_dev` or preview URLs that aren't off. `secret:<env>` accepts only `K_EDGE_<1-255>`, `PENDING_BEARER` or `PENDING_BEARER_NEXT`, and then runs `wrangler secret put <name> --env <env>`.
- **Tests read the engine fixture in place** (`../../tests/fixtures/unsub_token_vectors.json`), never a copy. Every `valid` token must be accepted, and every `tampered` token must get its `edge` verdict.
- **The integration test** binds `127.0.0.1` on free ports (server and inspector). It persists to a temp dir, gets secrets via `--var`, and sets telemetry and the `cf.json` fetch off. It kills the whole process tree afterwards.

**Never:**
- No `wrangler deploy`, `login` or `secret put`, no Cloudflare API call, and no DNS change. Deployment is Mac's documented step.
- No decryption and no `k_tok` in the Worker. Store no IP, User-Agent, header or body, and log nothing.
- No engine (Python) behaviour change, no migration, no `UnsubscribePort` or polling (3.11). No committed `node_modules`, `.wrangler`, `.dev.vars` or real secret.

### Decisions made overnight (pending user review)

1. **Deployment deferred.** Story 3.9 (OQ-A7) is open, so hosts and KV ids are placeholders that `check-config` refuses. Mac's deploy steps are in the engine README; the spine's First run step 6 is unchanged.
2. **Test runner: Node's built-in `node:test`.** The spine pins none, so no extra dependency is added. Node 24 is assumed (`engines`).
3. **dev and test hostnames** are `u-dev.<domain>` and `u-test.<domain>` (AD-14: their own hostnames), so the same zone and route type serve all three.
4. **`received_at` is at whole seconds**, matching `unsub_tokens.issued_at`.
5. **An invalid-token GET page returns 400**; a valid one returns 200.
6. **A bearer secret under 43 characters is treated as unset** (fail closed), since AD-15 requires 256 bits.
7. **The `/pending` JSON shape** is above, versioned by `format`, for 3.11.
8. **The Python gate runs the handler tests too.** A pytest test runs `node --test` on the handler-only test file, which needs no `node_modules` and no port; it skips when `node` isn't on PATH. Python contract tests pin the config, package files and forbidden source calls without Node.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| One-click | POST valid token, body `List-Unsubscribe=One-Click` | 200 empty; one `p:` key with `received_at` | N/A |
| Repeat | same POST later | 200; still one key, first `received_at` | N/A |
| Forged | any `tampered` vector with `edge: reject` | 400 empty; no KV call | N/A |
| Tag ok, bad ct | `edge: accept` vectors | 200; key written | engine rejects in 3.11 |
| Kid secret missing | kid 3, or `K_EDGE_1` malformed | 400 | N/A |
| Scanner GET | GET valid or invalid | page 200 or 400; no KV call | N/A |
| Poll | GET /pending, bearer = NEXT | 200 JSON over every list page | N/A |
| Bad auth | none, wrong, `Basic`, or secret unset | 401 empty | N/A |
| Other | `/`, `/u/`, `/u/<t>/x`, PUT, HEAD | 404 empty | N/A |

</frozen-after-approval>

## Code Map

- `nextron-engine/workers/unsubscribe/.gitkeep`: remove it. It is replaced by `wrangler.jsonc`, `package.json`, `package-lock.json`, `src/index.js`, `scripts/check-config.mjs`, `scripts/secret.mjs`, `test/handler.test.mjs` (fake KV, needs no deps) and `test/dev.test.mjs` (wrangler dev).
- `nextron-engine/tests/fixtures/unsub_token_vectors.json`: binding, read only. Keys are base64url without padding.
- `nextron-engine/nextron/domain/unsub_token.py`: the reference for the edge check (`parse_token`, `tag_matches`, `parse_key`). Don't change it.
- `nextron-engine/nextron/domain/credentials.py`: `unsubscribe_bearer` (`unsub/bearer`) is the engine's copy of the bearer. Unchanged.
- `nextron-engine/tests/contracts/test_source_tree.py`: `FILES` (~line 295). Swap the `.gitkeep` for the new files.
- `nextron-engine/tests/contracts/test_lockfile.py`: add the npm pin checks (package.json devDependencies are exactly `{"wrangler": "4.162.0"}`, and the lock's `node_modules/wrangler` version is 4.162.0).
- Root `.gitignore`: it already has `node_modules/` and `.wrangler/`; add `.dev.vars*`.
- `nextron-engine/README.md`: a new "Unsubscribe Worker" section after "Unsubscribe tokens". It covers the routes, the `/pending` contract for 3.11/3.12, local tests and Mac's deploy steps.

## Tasks & Acceptance

**Execution:**
- [x] `workers/unsubscribe/package.json` + lock: `npm install --save-dev --save-exact wrangler@4.162.0` in that folder. Add `engines`, `private` and `type: module`, and the scripts.
- [x] `workers/unsubscribe/wrangler.jsonc`: the config as specified. Pick a compatibility date the bundled workerd supports.
- [x] `workers/unsubscribe/src/index.js`: the Worker. It has a default export `{ fetch(request, env) }` only.
- [x] `workers/unsubscribe/scripts/*.mjs`: `check-config` (JSONC comment stripping is string-aware) and `secret`.
- [x] `workers/unsubscribe/test/handler.test.mjs`: the I/O matrix, every vector, cursor paging (more than one page), the rotation bearer, and a KV fake whose `delete` throws.
- [x] `workers/unsubscribe/test/dev.test.mjs`: the story's `npm test` AC against `wrangler dev --local`.
- [x] `tests/contracts/test_unsub_worker_contracts.py`: covers the config, the source bans, the fixture path, `.gitignore`, and the check-config refusal on the committed placeholders (via node, skipped without it). It also runs the handler test under node.
- [x] `test_source_tree.py`, `test_lockfile.py`, `.gitignore`, README: as in the Code Map.

**Acceptance Criteria:**
- Given `npm test` in `workers/unsubscribe/`, when it runs offline, then both test files pass and no process or port is left behind.
- Given `npm run deploy:prod` with the committed config, when it runs, then `check-config` refuses before wrangler starts.

## Implementation Notes

**Where things are.** `workers/unsubscribe/`: `wrangler.jsonc` (compatibility date `2026-11-01`, supported by the bundled workerd `1.20261113.1`; `send_metrics: false`; envs `dev`/`test`/`prod` named `nextron-unsubscribe-<env>`, placeholder KV ids `PLACEHOLDER-<ENV>-KV-NAMESPACE-ID`, hosts `u[-dev|-test].sending-domain-1.invalid`), `package.json` (wrangler `4.162.0` exact, `engines` `node >=24`) and `package-lock.json` (lockfileVersion 3), `src/index.js`, `scripts/config-lib.mjs` (`stripJsonc`, `parseJsonc`, `checkConfig` with a key allow-list `TOP_LEVEL_KEYS`/`ENV_KEYS`, `checkConfigFile`, `secretNameOk`, `secretPlan(argv, check)`), and the thin CLIs `scripts/check-config.mjs` and `scripts/secret.mjs` (always run their main; secret.mjs reports a missing wrangler; it runs `node node_modules/wrangler/bin/wrangler.js secret put <name> --env <env>` with no shell), `test/handler.test.mjs`, `test/dev.test.mjs`. Engine: `tests/contracts/test_unsub_worker_contracts.py`, `test_lockfile.py::test_the_unsubscribe_worker_pins_wrangler_exactly_and_locks_it`, `test_source_tree.py` `FILES`, root `.gitignore` (`.dev.vars*`), README "Unsubscribe Worker".

**Choices the frozen spec left open (implementer):**
1. The page CSP also has `style-src '<sha256>'` for its one inline style (`default-src 'none'` would block it). Both hashes are constants in the source, and the handler test recomputes them from the served page.
2. The bearer scheme is matched case-insensitively (`Bearer`/`bearer`, RFC 7235), one space, then a value of visible ASCII. Both configured secrets are always compared, so timing doesn't reveal which one matched.
3. A KV failure (or a `list` page that isn't `list_complete` but has no new cursor) is caught at the top and answers an empty 500 with the common headers, logging nothing, so a mail client or the poller retries.
4. The first `list` call passes `{prefix: "p:"}` with no `cursor` key; later calls pass `{prefix, cursor}`.
5. The page never echoes the token; the valid page's form has no `action`, so it posts to its own URL. The script's `fetch` uses `credentials: "omit"` and `cache: "no-store"`.
6. `check-config` also refuses: a non-custom-domain route or singular `route`, a host not starting with the env's prefix, a second KV binding or one not named `UNSUB`, a KV id (or `preview_id`) shared with another env, an env name equal to another env's or the top-level name, and any `observability.<x>.enabled` not `false` or `invocation_logs: true`.
7. `secret:<env>` runs `check-config` twice (the package script, then `secret.mjs` itself) and refuses any extra argument; the value is always typed at wrangler's prompt.
8. `dev.test.mjs` passes the child a minimal environment (PATH and OS dirs only) plus `CI`, `DO_NOT_TRACK`, `WRANGLER_SEND_METRICS=false`, `WRANGLER_SEND_ERROR_REPORTS=false`, `CLOUDFLARE_CF_FETCH_ENABLED=false`, `CLOUDFLARE_LOAD_DEV_VARS_FROM_DOT_ENV=false`, `CLOUDFLARE_INCLUDE_PROCESS_ENV=false`, and log, registry and cache dirs inside its temp dir; it uses `--show-interactive-dev-session=false --live-reload=false`. Teardown waits for the child's exit after `taskkill /T /F` (process-group `SIGKILL` elsewhere), checks both ports bind again, and retries the temp-dir removal for up to 15 s (workerd's files stay locked briefly on Windows). wrangler still writes its bundle under `workers/unsubscribe/.wrangler/` (ignored).
9. The Python contract bans, in the source with whole-line comments removed: `delete`, `console`, `.cf`, body readers (`.text(`, `.json(`, `.arrayBuffer(`, `.formData(`, `.blob(`, `.bytes(`, `.body`, `bodyUsed`, `getReader`), header enumeration, client-identity header names, `decrypt`/`aes`/`k_tok`, any `import`/`require`, and any `env.<X>.` storage other than `UNSUB`; it pins `headers.get("authorization")` as the only header read and `get`/`put`/`list` as the only KV calls. Probes prove each pattern fires.

**Review fixes (pass 1, implementer):** README notes on KV consistency, `--env`-only deploys, per-env secrets and post-deploy checks; `role="status"` and a `<noscript>` note on the page (script and style unchanged, so the CSP hashes are too); the key allow-list; the guard logic moved to `config-lib.mjs` with `secretPlan` tests; bracket-access and word-boundary bans; the pin assertion only in `test_lockfile.py`; a private HOME/USERPROFILE/APPDATA/LOCALAPPDATA/XDG_CONFIG_HOME for the wrangler child; a lowercase `bearer` case. `npm test`: 136 passed.

**Verification (implementer):** `npm test` in `workers/unsubscribe/`: 113 passed (106 handler, 7 against `wrangler dev`), about 7-10 s, no workerd process or temp dir left. `node scripts/check-config.mjs <dev|test|prod|staging>` exits 1 on the committed config. `npm run deploy:*` was deliberately not run (the auto-mode classifier refuses a deploy command); the contract test runs the same guard.

## Spec Change Log

## Review Triage Log

Pass 1 (review_loop_iteration 0): blind hunter (BH) 18, edge-case hunter (EC) 10, verification gap (VG) 5 = 33 raw; 28 after dedup (BH4=EC2 race; BH5=EC4=VG-other no-JS page; BH8=EC7 bracket access; EC1=EC10 main detection).

| # | Layer | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| 1 | BH | `package-lock.json` absent from the review diff | false | Excluded from the review diff on purpose, like `uv.lock`; the file is present, and `test_lockfile.py` pins its wrangler entry. | reject |
| 2 | BH | Spec and sprint status left mid-flight | false | Review is in progress; step 5 sets `done` and `review`. | reject |
| 3 | BH | The real deployment is tracked nowhere once 3.10 is done | medium | Decision 1 defers it, and only the README holds the steps. | defer |
| 4 | BH, EC | Get-then-put on eventually consistent KV: a near-simultaneous repeat can overwrite `received_at`, or hit the 1 write/s/key limit and answer 500 | low | Real for KV. 3.11 dedups by `token_hash`, so no opt-out is lost; at worst the time moves by seconds or the client sees a retryable 500. A README note is a direct fix. | patch (A) |
| 5 | BH, EC, VG | A no-JS visitor gets a blank page after the POST | low | POST is empty by the frozen spec. A `<noscript>` line saying the click is recorded even if the next page is blank is a direct, spec-compatible fix. | patch (B) |
| 6 | BH | `check-config` ignores `tail_consumers`, `logfwdr`, `vars`, `unsafe` and other bindings | medium | A tail consumer forwards request events with logs off, and a `vars` secret would sit in plaintext. Fix: an allow-list of top-level and env keys. | patch (C) |
| 7 | BH | Bare `wrangler deploy` with no `--env` is unguarded | low | It uploads `nextron-unsubscribe` with no route, `workers_dev` off and no KV, so it is unreachable. A README warning is a direct fix. | patch (A) |
| 8 | BH, EC | The storage ban misses bracket access (`env["X"].put`) | low | Confirmed: the pattern covers dotted access only. A one-regex fix. | patch (D) |
| 9 | BH | `dev.test.mjs` passes the real `HOME`/`USERPROFILE`/`APPDATA`/`LOCALAPPDATA` to wrangler | medium | A child could read a future `wrangler login` token. Point them at the temp dir. | patch (E) |
| 10 | BH | `wrangler dev` writes `.wrangler/` inside the repo | low | wrangler 4 always bundles into `<project>/.wrangler/tmp`, which is gitignored; `--persist-to` already moves the state. No direct fix. | reject |
| 11 | BH | The Python and JS JSONC strippers differ | low | The committed file has no strings with `,}` and no block comments; the JS one is the deploy guard. The fix is a new parser. | reject |
| 12 | BH | `/pending` is unbounded (50-subrequest cap) | false | KV operations have their own per-invocation limit (1,000), and each list returns up to 1,000 keys. 30-day volume is far below that. | reject |
| 13 | BH | `secret.mjs` fails silently when wrangler is missing | low | Confirmed: `result.error` is ignored and it exits 1 with no message. A direct fix. | patch (F) |
| 14 | BH | The README doesn't say dev and test use their own `K_EDGE` and bearer | low | AD-14 gives each env its own keyring namespace; one sentence fixes it. | patch (A) |
| 15 | BH | The status text has no live region | low | Add `role="status"` to `#m`, a direct fix. | patch (B) |
| 16 | BH | The README's post-deploy check is thin | low | Add checks for the 400 page, a 404, the headers and workers.dev. A doc-only direct fix. | patch (A) |
| 17 | BH | The `aes` ban has no word boundary; comment stripping is whole-line only | low | `aes` would match `caesar`. A direct regex fix (`aes`). | patch (D) |
| 18 | BH | The wrangler pin is duplicated in two tests | low | Two places to edit on a bump; deleting the duplicate in `test_unsub_worker_contracts.py` is a direct fix. | patch (D) |
| 19 | EC | check-config's main-module check (`import.meta.url === pathToFileURL(argv[1])`) can be false (drive-letter case, junction, subst) | high | A mismatch makes `main()` not run and exit 0, so `&& wrangler deploy` proceeds unguarded. Same in `secret.mjs`. Fix: move the logic to a library module, and make both CLIs run `main()` unconditionally. | patch (G) |
| 20 | EC | Removing `K_EDGE_<old kid>` loses clicks | false | The README rotation (3.8) already says to keep `K_EDGE_1` until kid 1 is retired. | reject |
| 21 | EC | A bearer with a space or non-ASCII can never match | low | Only a mis-typed secret; the engine generates `token_urlsafe(32)`. Fixing it adds a guard. | reject |
| 22 | EC | `preview_id` equal to its own id passes | low | `preview_id` is used only by remote `wrangler dev`, never deployed traffic. Adds a branch. | reject |
| 23 | EC | `process.exit` after stderr writes can truncate output on POSIX pipes | false | Node writes stderr synchronously to pipes on POSIX and files everywhere; the target is Windows, where pipes are synchronous too. | reject |
| 24 | EC | `after()` can hang if taskkill fails | low | Test-only, and the suite timeout bounds it. Adds a guard. | reject |
| 25 | VG | `dev.test.mjs` isn't in the pytest gate | medium | Pre-verified. Decision 8 scopes the gate to the handler file. | defer |
| 26 | VG | `secret.mjs` `main()`'s allow-list is never executed by a test | medium | Pre-verified. Covered by G's `secretPlan()` plus test cases. | patch (G) |
| 27 | VG | Five check-config refusals have no refusal test | low | Pre-verified. Add five refusal-table entries. | patch (C) |
| 28 | VG | Case-insensitive bearer scheme untested | low | Pre-verified. Add one case. | patch (H) |

### Patches dispatched (pass 1)
A README notes (4, 7, 14, 16); B page `<noscript>` and `role="status"` (5, 15); C check-config key allow-list and five refusal tests (6, 27); D contract regex fixes and dedup (8, 17, 18); E dev.test profile dirs (9); F secret.mjs error message (13); G library split plus testable `secretPlan` (19, 26); H lowercase bearer test (28).

## Verification

**Commands:**
- `cd nextron-engine/workers/unsubscribe && npm test`: expected all pass.
- `cd nextron-engine && uv run pytest -q tests/contracts tests/unit/test_token_codec.py && uv run lint-imports`: expected pass.
