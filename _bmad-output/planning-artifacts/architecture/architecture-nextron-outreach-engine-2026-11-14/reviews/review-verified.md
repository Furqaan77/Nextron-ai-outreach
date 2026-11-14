---
review_of: ARCHITECTURE-SPINE.md (Nextron Outreach Engine, draft 2026-11-14)
lens: verified (were committed decisions web-researched or reality-checked, not asserted from training data?)
reviewed: 2026-11-14
method: live PyPI/npm JSON registry queries; vendor docs fetched today; `uv pip compile` of the pinned stack for CPython 3.14 / win_amd64; local interpreter probes (CPython 3.14.8 via uv); an import-linter 2.16 spike on a scratch copy of the `nextron/` layout
---

# Review: verification lens

## Verdict

**Conditional pass.** Every version pin in the Stack table is still the newest release on PyPI or npm today. The whole pinned set, plus the packages added below, resolves for CPython 3.14 on win_amd64. The memlog's version claims hold up.

The gaps are in **behaviour the spine assumes but never tested**:

- **2 High findings.**
  - Server-side `fallbacks` are rejected inside Message Batches, and AD-8 sends both to Opus routes.
  - The Sent-folder reconciliation key in AD-5 depends on Gmail search behaviour that no doc confirms.
- **6 Medium findings:** batch latency, refusal and schema handling, KV free-tier list and delete quotas, Task Scheduler recipe, `tzdata`, and import-linter contract shape.
- **10 Low or Info findings.**

Fix the two High findings before Slice 1 builds the sending and drafting paths.

## What the decision log already verified, re-checked today

| Item (spine / memlog claim) | Today's evidence | Status |
| --- | --- | --- |
| FastAPI 0.162.7, Uvicorn 0.54.0, Jinja2 3.1.6, SQLAlchemy 2.1.8, Alembic 1.20.1, APScheduler 3.11.3, anthropic 2.8.0, google-api-python-client 2.202.0, google-auth-oauthlib 1.5.0, httpx2 2.16.0, selectolax 1.0.2, trafilatura 2.3.1, protego 0.7.0, email-validator 2.3.0, dnspython 2.9.2, keyring 25.7.0 | PyPI JSON `info.version` for each = the pinned version | Verified, all still latest |
| uv 0.13 | PyPI: 0.13.8 (2026-11-12). Local install: 0.13.0 | Verified (see L3) |
| HTMX 2.0.11 vendored; 4.x not yet `latest` | npm dist-tags: `latest` 2.0.11, `next` 4.0.1 | Verified |
| Python 3.14.8 bundles SQLite 3.53.1, at or above 3.51.3 | Local probe: `3.14.8`, `sqlite3.sqlite_version` = `3.53.1` | Verified |
| SQLite 3.51.3 contains the WAL-reset corruption fix | sqlite.org news; release 2026-03-13; bug range 3.7.0 to 3.51.2 | Verified |
| 3.13 is security-only | endoflife.date: 3.13 bugfix support ended 2026-10-01 | Verified |
| APScheduler 4 is still alpha | PyPI: last 4.x is `4.0.0a6` (2025-04-27); 3.11.3 is 2026-06-28 | Verified |
| No uvloop on Windows | uvicorn 0.54.0 metadata: `uvloop ... sys_platform != "win32"` | Verified |
| Model IDs `claude-haiku-5-5`, `claude-opus-5-5` (from the skill cache dated 2026-11-03) | Both listed in `supportedModels` of the live Batch processing and Structured outputs docs | Verified live |
| Workers free plan: 100k requests/day; KV 1k writes/day | Cloudflare Workers limits and KV pricing pages (updated 2026-10-30) | Verified, but see M3 (list and delete quotas missed) |
| Gmail `gmail.modify` allows `q` searches | users.messages.list: "`q` ... cannot be used ... with the gmail.metadata scope"; modify is allowed | Verified |
| Stack co-installability on Windows / 3.14 | `uv pip compile --python-version 3.14 --python-platform x86_64-pc-windows-msvc` succeeds (82 packages). Compiled dependencies ship `cp314-win_amd64` wheels: pydantic-core 2.50.4, selectolax, sqlalchemy, lxml, greenlet, httptools | Verified |

**Not verified anywhere in the log, checked here:**

- import-linter
- pydantic-settings
- the ULID library
- `tzdata`
- wrangler
- searching Gmail by header
- Message Batches semantics: fallbacks, latency, caching
- Task Scheduler cmdlets
- KV list and delete quotas
- APScheduler and Uvicorn event-loop behaviour on 3.14 / Windows

## Findings

### H1. AD-8 contradicts itself: Opus routes send server-side `fallbacks`, but batch requests reject `fallbacks`

- **Severity:** High
- **Spine location:** AD-8. The rule bullets "Batchable routes run through the Message Batches API" and "Opus routes enable server-side refusal fallbacks" together. The memlog decision to batch "nightly enrichment extraction and Step drafting" makes `draft_sequence` (an Opus route) batchable.
- **Evidence:**
  - https://platform.claude.com/docs/en/build-with-claude/batch-processing. Section "What can be batched": `stream`, `speed`, `fallbacks` and `max_tokens: 0` "are **not** supported in batch requests. Including any of these returns a validation error."
  - https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback#refusals-in-message-batches: "a batch request that includes `fallbacks` produces a per-item errored result ... batch refusals don't mint fallback credits."
- **Impact:** if the gateway attaches `fallbacks` per model as written, every batched `draft_sequence` item errors. No Step drafts get produced.
- **Fix:**
  - Make `fallbacks` a property of the **sync call path** in the gateway, not of the route or model.
  - For batch results, treat `result.type == "succeeded"` with `stop_reason == "refusal"` as a refusal. Collect those items and resubmit them on the fallback model as direct calls or a new batch. The fallback model comes from `allowed_fallback_models` on the model's `/v1/models` entry; the skill notes say to expect `claude-opus-5`.
  - Record both attempts in `ai_usage`.
  - Add a gateway test that a batch request body never contains `fallbacks`.

### H2. AD-5 reconciles the Sent folder "by header", and no doc confirms that works; the suggested `rfc822msgid:` fallback is also unreliable

- **Severity:** High. A false negative sends the row back to `queued`, it is sent again, and FR-35 exactly-once is broken.
- **Spine location:** AD-5, last bullet ("reconciled against the inbox's Sent folder by header"). Also the IDs row of Consistency Conventions (`X-Nextron-Message-Id`).
- **Evidence:**
  - https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list says `q` "supports the same query format as the Gmail search box". The API docs document no header-search operator.
  - https://support.google.com/mail/answer/7190 does list `header:` ("Search for emails with a specific custom header or header value"). Its only example is a Google-set header, `header:X-Google-Calendar-Notification:rsvpWithNote`. Nothing shows that it matches a sender-set `X-` header on the Sent copy of a message sent through the API.
  - The same page lists `rfc822msgid:` ("Find emails with a specific message-id header").
  - https://docs.connectonion.com/blog/gmail-kept-the-body-and-changed-the-id (2026-09-08; anecdotal) reports:
    - Gmail **replaced** a client-supplied Message-ID on send, so "the first lookup by the reviewed Message-ID found nothing".
    - A custom marker header **survived**.
    - Recovery works by scanning recent sent-message metadata for the exact marker.
  - Other community reports say the original value is kept in `X-Google-Original-Message-ID`.
- **Answer to the question asked:**
  - **`header:` search:** it exists in Gmail search, but it is unproven for this use.
  - **`rfc822msgid:` on a Message-ID we set:** not a safe substitute, because Gmail may rewrite that Message-ID.
  - **Safe key:** the `X-Nextron-Message-Id` header value, read back from the message itself, not found through search.
- **Fix:** rewrite the AD-5 reconciliation step as follows.
  1. Right after `send` returns, store the Gmail `id` and `threadId`. Also store the *actual* `Message-ID`, read back with `users.messages.get(format=METADATA, metadataHeaders=[Message-ID])`. You need it anyway for `In-Reply-To` threading.
  2. To reconcile a `sending` row, call `users.messages.list(labelIds=["SENT"], q="to:<recipient> after:<epoch(sending_started_at) - 120>")`. Then fetch each candidate with `format=METADATA`, `metadataHeaders=["X-Nextron-Message-Id","Message-ID","X-Google-Original-Message-ID"]`, and match the ULID exactly. This does not depend on header-search operators.
  3. Run reconciliation for any `sending` row older than a grace period, not only at startup. UJ-5 sleep can leave in-flight rows too.
  4. If the row is still unresolved after the grace period, move it to a `needs_review` state that surfaces on the health panel (FR-58), instead of `queued`.
  5. Add a Slice-1 spike against the real Workspace tenant, sending to an allowlisted address. Record:
     - whether `X-Nextron-Message-Id` survives;
     - whether Message-ID is rewritten;
     - whether `header:` and `rfc822msgid:` find the message, and after what delay.

  Promote either search operator to a fast path only if the spike proves it works.

### M1. The batch latency guarantee is absent from the spine: no SLA, a 24-hour expiry, and no expiry handling

- **Severity:** Medium
- **Spine location:** AD-8 ("Batchable routes run through the Message Batches API"); AD-10 (job types); memlog Batch-API decision.
- **Evidence:** https://platform.claude.com/docs/en/build-with-claude/batch-processing:
  - "most batches completing within 1 hour".
  - "You can access batch results when all messages have completed or after 24 hours, whichever comes first. Batches expire if processing does not complete within 24 hours."
  - Expired requests are not billed.
  - Results are kept for 29 days.
  - Under demand, "you might see more requests expiring after 24 hours."
- **Impact:**
  - Nightly Step drafting can miss the next morning's approval session and Send Window.
  - Chaining `draft_sequence` and then a batched `grounding_check` can take up to about 48 hours.
  - Nothing in the spine owns polling, expiry or partial results.
- **Fix:**
  - Add a durable `poll_ai_batch` job type (AD-10) that owns the batch id, polls `processing_status`, and ingests results by `custom_id`, never by position.
  - Treat `expired` and `errored` items as retryable: resubmit them, or switch to the sync API once a per-route `deadline` passes.
  - Add `max_wait` / `deadline` to the route record in `routes.py`.
  - Don't batch `grounding_check` when it gates a Draft that is already late.

### M2. AD-8 has no defined handling for refusals, truncated output or enum casing under structured outputs, and Haiku 5.5 has no server-side fallback

- **Severity:** Medium
- **Spine location:** AD-8 ("Structured outputs are used for every extraction and classification"; "Opus routes enable server-side refusal fallbacks"); AD-7 ("schema-validated proposal").
- **Evidence:**
  - https://platform.claude.com/docs/en/build-with-claude/structured-outputs, section "Invalid outputs":
    - On `stop_reason: "refusal"` the output "might not match your schema". The same goes for `max_tokens`.
    - "Structured outputs don't guarantee the capitalization of string `enum` and `const` values."
    - A schema property that asks for the model's thinking or step-by-step reasoning "might lead to a `reasoning_extraction` refusal".
    - The same page confirms structured outputs are GA and "Works with: Batch processing". Changing `output_config.format` invalidates the prompt cache.
  - https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback: "Claude Haiku 5.5 has neither server-side fallback nor fallback credit, so use the SDK middleware or a manual retry."
- **Impact:**
  - Pydantic validation fails on refusals or casing drift.
  - `classify_reply` must reach 100% recall on unsubscribes, yet an enum casing slip or a truncated JSON response becomes a hard failure.
  - Haiku routes have no fallback path at all.
- **Fix:**
  - In the gateway, branch on `stop_reason` *before* schema validation:
    - `refusal`: retry on the fallback model. Haiku routes use a manual retry on another model, or discard the proposal and record an event.
    - `max_tokens`: retry with a higher cap. Thinking is always on for Opus 5.5 and on by default for Haiku 5.5, and it counts toward `max_tokens`.
  - Use lowercase `snake_case` enum values with no spaces, and normalise case before validation.
  - Ban schema fields named like `reasoning`/`chain_of_thought`; use a short `explanation` field instead.
  - Emit a `refusal` metric separately from errors, because a refusal returns HTTP 200.

### M3. The Unsubscribe Endpoint polling design can exceed the KV free-tier **list** and **delete** quotas, which the log didn't check

- **Severity:** Medium. Pending opt-outs could go unseen until 00:00 UTC, which hurts FR-50 and FR-70 timeliness.
- **Spine location:** AD-15 (Worker stores `{token, received_at}` in KV; engine polls `GET /pending` and acknowledges); AD-14 (separate KV namespace for non-prod); memlog Cloudflare decision (checked only the 1k writes/day).
- **Evidence:**
  - https://developers.cloudflare.com/kv/platform/pricing/ (updated 2026-10-30), free plan: reads 100,000/day; writes 1,000/day; **deletes 1,000/day; list requests 1,000/day**. "If you exceed any one of these limits, further operations of that type will fail with an error." Limits reset at 00:00 UTC.
  - https://developers.cloudflare.com/kv/concepts/how-kv-works/: KV is eventually consistent. Other locations "may take up to 60 seconds or more" to see changes, and KV "is not ideal ... where you need support for atomic operations."
  - https://developers.cloudflare.com/d1/platform/pricing/: D1 free plan allows 5M rows read/day and 100k rows written/day.
- **Impact:**
  - If `GET /pending` uses `list()`, polling every minute makes 1,440 list calls a day, which is over the limit.
  - Dev and test namespaces count against the same account-level daily quota.
  - Forged POSTs carrying random tokens can burn the 1,000 writes.
  - Acknowledged (deleted) tokens may still be listed for up to about 60 seconds, so the engine can see duplicates.
- **Fix:** pick one option.
  - (a) Poll at most every 5 minutes, about 288 lists a day, as an **account-wide** budget across environments, and cap acknowledgements per poll.
  - (b) Move the pending queue to a D1 table on the same free plan. It is SQL-consistent and has far higher quotas.

  In either case:
  - Have the Worker reject tokens that are malformed. Optionally, have it check an HMAC tag (`token.mac` keyed by a shared secret) before writing, so forged POSTs can't spend the quota.
  - Make the engine's suppression insert idempotent.

### M4. The Task Scheduler recipe isn't in the spine; the cmdlet defaults would break it, and "restart on failure" likely doesn't cover crashes

- **Severity:** Medium. This affects uptime under SM-5 and SM-8, and the non-admin install.
- **Spine location:**
  - Stack row "Windows Task Scheduler: logon task (current user)".
  - Structural Seed `scripts/install_logon_task.ps1`.
  - The recipe exists only in the memlog: "run only when logged on, no highest privileges, execution limit PT0S, allow on battery, restart on failure".
- **Evidence:**
  - https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasktrigger: `-AtLogOn` with no `-User` creates a trigger that "starts when a user logs on", meaning any user. Community and Microsoft Q&A reports say any-user logon triggers and `-RunLevel Highest` need elevation.
  - https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasksettingsset: "Without the ExecutionTimeLimit setting defined, the time limit [is] set to it's default of three days."
  - Restart semantics:
    - https://learn.microsoft.com/en-us/previous-versions/windows/it-pro/windows-server-2008-r2-and-2008/cc722178(v=ws.11) says restart applies "if the task fails to run".
    - Community reports, for example https://serverfault.com/questions/936459, say a process that started and then exited non-zero counts as a successful run, so no restart happens.
- **Fix:** put the exact recipe into the spine (AD-2 or the Structural Seed):

  ```powershell
  $me = "$env:USERDOMAIN\$env:USERNAME"
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User $me
  $principal = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited
  $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
  ```

  - Handle crash restarts outside "restart on failure". Use either a supervising loop in the launched PowerShell wrapper, with backoff, or a second repeating trigger every 5 minutes combined with `IgnoreNew`.
  - Add a Slice-1 check: run the script from a **non-elevated** shell, then confirm `Get-ScheduledTask | Get-ScheduledTaskInfo` and a crash/restart cycle. I could not find authoritative confirmation of non-admin registration with these exact parameters.

### M5. `tzdata` is an undeclared runtime dependency of AD-11 on Windows

- **Severity:** Medium
- **Spine location:** AD-11 (IANA timezone per Contact; `domain.sending_policy`); AD-1 (domain imports "stdlib only"); Stack table.
- **Evidence:**
  - https://docs.python.org/3.14/library/zoneinfo.html: Windows has no system IANA database, and cross-platform projects should declare a dependency on `tzdata`.
  - Local probe on the target interpreter (uv CPython 3.14.8, `-I`): `ZoneInfo('Europe/London')` raised `ZoneInfoNotFoundError`, and `zoneinfo.TZPATH == ()`.
  - In the resolved set, `tzdata` 2026.6 arrives only transitively, via APScheduler → `tzlocal` (`tzdata; platform_system == "Windows"`).
- **Impact:**
  - Send Window and approval-expiry maths depend on a package nobody pinned.
  - Removing or replacing APScheduler would break sending at runtime.
  - A stale `tzdata` gives wrong DST rules.
- **Fix:**
  - Add `tzdata` (2026.6) to the Stack and to `pyproject.toml`.
  - Add a startup check that `ZoneInfo("Europe/London")` loads (AD-2 startup checks).
  - Note in AD-1 that `zoneinfo` is stdlib but its data on Windows comes from `tzdata`. This is an allowed data dependency, not an import.

### M6. The AD-1 import-linter rule can't be expressed as a single `layers` contract, and the obvious `forbidden` contract fails on legitimate code

- **Severity:** Medium. As written, the CI rule either misses violations or flags correct code.
- **Spine location:** AD-1 (layer table, "an import-linter contract enforces them"); AD-8 ("Only `nextron/adapters/ai` imports the `anthropic` SDK").
- **Evidence:**
  - https://pypi.org/project/import-linter/: 2.16, released 2026-11-06, `Requires-Python >=3.11`, declares Python 3.14. Its dependency is `grimp>=3.19`; the resolved set has 3.20.
  - https://import-linter.readthedocs.io/en/stable/contract_types/layers/: higher layers may import lower ones. `|` marks independent siblings.
  - https://import-linter.readthedocs.io/en/stable/contract_types/forbidden/:
    - indirect imports are forbidden unless `allow_indirect_imports` is true;
    - external modules need `include_external_packages = True`;
    - external modules must be root-level names ("`django`, but not `django.db.models`").
  - Spike run (import-linter 2.16 on CPython 3.14, scratch `nextron/` tree):
    - A `layers` contract stays KEPT when `adapters` imports `app`, and when `jobs` imports `db`, because both import downward.
    - A `forbidden` contract "web/jobs must not import db" reports BROKEN on the legal path `web → app → db` until `allow_indirect_imports = true` is set.
    - With the set below, all contracts were KEPT on a clean tree. They caught the injected violations: `jobs → db`, `adapters → app`, and `import anthropic` in `app`.
- **Fix:** adopt the verified contract set, and assign every third-party package to a layer. Undecided today: `keyring`, `apscheduler`, `ulid`, `pydantic_settings`, `selectolax`, `trafilatura`, `protego`, `dns`, `email_validator`.

  ```toml
  [tool.importlinter]
  root_package = "nextron"
  include_external_packages = true
  exclude_type_checking_imports = true

  [[tool.importlinter.contracts]]
  name = "Layers (AD-1)"
  type = "layers"
  layers = ["nextron.main", "nextron.web | nextron.jobs | nextron.adapters", "nextron.app", "nextron.ports | nextron.db", "nextron.domain"]

  [[tool.importlinter.contracts]]
  name = "Delivery never imports adapters or db directly (AD-1)"
  type = "forbidden"
  source_modules = ["nextron.web", "nextron.jobs"]
  forbidden_modules = ["nextron.adapters", "nextron.db"]
  allow_indirect_imports = true

  [[tool.importlinter.contracts]]
  name = "Adapters never import app or db (AD-1)"
  type = "forbidden"
  source_modules = ["nextron.adapters"]
  forbidden_modules = ["nextron.app", "nextron.db"]

  [[tool.importlinter.contracts]]
  name = "Only adapters import I/O SDKs (AD-1)"
  type = "forbidden"
  source_modules = ["nextron.domain", "nextron.app", "nextron.ports", "nextron.db", "nextron.web", "nextron.jobs"]
  forbidden_modules = ["anthropic", "googleapiclient", "google_auth_oauthlib", "google", "httpx2"]

  [[tool.importlinter.contracts]]
  name = "Only adapters.ai imports anthropic (AD-8)"
  type = "forbidden"
  # List every non-AI adapter explicitly (this form was tested in the spike). Add each adapter as it is created.
  # The spike didn't test how import-linter treats modules listed here that don't exist yet.
  source_modules = ["nextron.domain", "nextron.app", "nextron.ports", "nextron.db", "nextron.web", "nextron.jobs", "nextron.adapters.gmail", "nextron.adapters.places", "nextron.adapters.companies_house", "nextron.adapters.hunter", "nextron.adapters.prospeo", "nextron.adapters.ats", "nextron.adapters.crawler", "nextron.adapters.dns", "nextron.adapters.telegram", "nextron.adapters.unsubscribe", "nextron.adapters.sink"]
  forbidden_modules = ["anthropic"]
  ```

  - `google-auth` lives under the `google` namespace, so `google` has to be forbidden as a root name.
  - `httpx2` imports as `httpx2`; I checked the wheel contents.

### L1. Tools named in decisions have no pins in the Stack table

- **Severity:** Low
- **Spine location:** Stack table; Consistency Conventions (Config, IDs, Tests rows); Structural Seed `workers/unsubscribe/`.
- **Evidence:**
  - https://pypi.org/project/pydantic-settings/: 2.16.3, released **2026-11-13**, one day ago. `>=3.11`, declares 3.14.
  - https://pypi.org/project/import-linter/: 2.16.
  - https://pypi.org/project/python-ulid/: 4.1.0.
  - https://pypi.org/project/tzdata/: 2026.6.
  - https://www.npmjs.com/package/wrangler: 4.162.0 (2026-11-13).
- **Fix:**
  - Add rows: pydantic-settings `~=2.16` (the patch is a day old, so allow 2.16.x fixes), import-linter 2.16, python-ulid 4.1.0, tzdata 2026.6, pytest, and wrangler 4.162.0, pinned in `workers/unsubscribe/package.json`.
  - All of these resolve together with the existing pins; I checked with uv.

### L2. No ULID library is named; one of the obvious picks is dead, and two of them collide on import

- **Severity:** Low
- **Spine location:** Consistency Conventions, IDs row; AD-5 (`OutboundMessage` id = ULID).
- **Evidence:**
  - https://pypi.org/project/python-ulid/: 4.1.0, released 2026-10-27. Pure Python, `>=3.11`, declares 3.14.
  - https://python-ulid.readthedocs.io/en/stable/changelog.html:
    - 4.0.0 added pluggable monotonicity policies, with `StrictMonotonicPolicy` as the default, and removed `ValueProvider`;
    - 4.1.0 dropped Python 3.10 and fixed lowercase parsing;
    - Pydantic v2 type support has existed since 2.3.0.
  - https://pypi.org/project/ulid-py/: last release 1.1.0 in 2020. `py-ulid` (2019) and `ulid2` (2021) are also stale.
  - Wheel inspection: **both** `python-ulid` and `ulid-py` install a top-level `ulid` package.
- **Fix:**
  - Use **python-ulid 4.1.0** (`from ulid import ULID`).
  - Forbid `ulid-py` in the lockfile review, because the two can't coexist.
  - If a dependency-free ID is ever preferred, `uuid.uuid7()` is in the stdlib since 3.14 (https://docs.python.org/3.14/library/uuid.html). It is time-ordered and monotonic within a millisecond, but it is a 36-character UUID, not a 26-character ULID, so the convention would have to change.

### L3. The Python decision doesn't mention 3.15, and the patch floor is two releases behind

- **Severity:** Low
- **Spine location:** Stack row "Python (via uv): 3.14 (3.14.8+)"; memlog version line.
- **Evidence:**
  - https://endoflife.date/python: 3.15.0 released 2026-10-09, latest 3.15.2. The 3.14 line's latest is **3.14.10** (2026-10-25).
  - Local `uv` 0.13.0 lists only 3.14.8; PyPI uv is 0.13.8.
  - Several pinned packages don't yet declare 3.15 in their classifiers: APScheduler, Uvicorn, anthropic, python-ulid.
- **Fix:**
  - Record the decision: "stay on 3.14 until the stack declares 3.15".
  - Run `uv self update` and move the floor to 3.14.10.
  - Keep the runtime SQLite assertion; it guards whatever the build bundles.

### L4. The APScheduler 3.x scheduler class and where it starts are unspecified, which matters on Python 3.14

- **Severity:** Low
- **Spine location:** AD-10 ("APScheduler (3.x) triggers only enqueue jobs"); AD-2 (one process).
- **Evidence:**
  - https://pypi.org/project/APScheduler/: 3.11.3 declares 3.14.
  - https://apscheduler.readthedocs.io/en/3.x/versionhistory.html:
    - 3.11.0 fixed `AsyncIOScheduler` "creating a defunct event loop at start";
    - 3.11.x added `ZoneInfo` support and DST fixes.
  - https://docs.python.org/3.14/library/asyncio-eventloop.html: in 3.14, `get_event_loop()` raises `RuntimeError` when no loop is current, and the policy system is deprecated.
- **Fix:**
  - Specify `AsyncIOScheduler(timezone=ZoneInfo("UTC"))`, created and started inside the FastAPI `lifespan` so a running loop exists, and shut down there too.
  - Or specify `BackgroundScheduler` and accept a second thread, which then needs its own SQLAlchemy session.
  - Enqueue callbacks must go through the unit of work (AD-2).

### L5. Uvicorn uses a different event loop under `--reload` on Windows

- **Severity:** Low
- **Spine location:** AD-2 (`uvicorn nextron.main:app`); `scripts/backup.ps1`.
- **Evidence:** https://github.com/Kludex/uvicorn/blob/0.54.0/uvicorn/loops/asyncio.py. On `win32`, Uvicorn uses `ProactorEventLoop` when there is no subprocess, and `SelectorEventLoop` with `--reload`/`--workers`. `SelectorEventLoop` can't run asyncio subprocesses on Windows.
- **Fix:**
  - Run external scripts with `subprocess.run` in a worker thread (`anyio.to_thread`), not with `asyncio.create_subprocess_exec`.
  - Run the startup checks and the prod-parity smoke test without `--reload`.

### L6. Batch inputs and outputs are kept at Anthropic for up to 29 days and are not ZDR-eligible

- **Severity:** Low (data-handling note for AD-15 and §5.3)
- **Spine location:** AD-8 (batchable routes); AD-15 ("Off-PC data is minimal").
- **Evidence:** https://platform.claude.com/docs/en/build-with-claude/batch-processing. Feature metadata says "zdr: not-eligible, 29-day retention". The data-handling section says batches can be deleted after processing with `DELETE /v1/messages/batches/{id}`.
- **Fix:**
  - After results are ingested, delete each batch from the `poll_ai_batch` job.
  - Note the processor and retention period in the compliance record alongside the sync API's.

### L7. Prompt-caching savings inside batches are best-effort; the cost estimate assumes they happen

- **Severity:** Low
- **Spine location:** memlog cost estimate ($12-20/mo) and the "prompt caching with a stable prefix (Playbook + Approved Company Facts + schema)" decision.
- **Evidence:**
  - https://platform.claude.com/docs/en/build-with-claude/batch-processing:
    - batch cache hits are "best-effort"; "typical ... 30 percent to 98 percent";
    - "consider using the 1-hour cache duration".
    - Haiku 5.5 has a second rate card for prompts over 100,000 tokens.
  - Structured-outputs doc: changing `output_config.format` invalidates the cache. Caches are also per model, so Haiku and Opus routes never share a prefix.
- **Fix:**
  - Put the schema in `output_config.format`, not in the cached prompt text.
  - Use a 1-hour TTL `cache_control` on the shared batch prefix.
  - Keep prompts under 100k tokens.
  - Re-run the cost estimate with a 30% hit-rate floor.

### L8. `wrangler.toml` is no longer the recommended format, and wrangler is unpinned

- **Severity:** Low
- **Spine location:** Structural Seed, `workers/unsubscribe/ (wrangler.toml, src/index.js)`.
- **Evidence:** https://developers.cloudflare.com/workers/wrangler/configuration/ says "Cloudflare recommends using `wrangler.jsonc` for new projects" and "some newer Wrangler features will only be available to projects using a JSON config file". wrangler `latest` is 4.162.0.
- **Fix:**
  - Use `wrangler.jsonc` and pin `wrangler@4.162.0` as a devDependency.
  - Set an explicit `compatibility_date`.
  - Declare the prod KV namespace and the non-prod one (or D1, per M3) as separate bindings or environments.

### L9. keyring's Credential Manager limit is in characters, not bytes

- **Severity:** Low
- **Spine location:** AD-12 ("Each value is under 2,560 bytes").
- **Evidence:**
  - keyring's Windows backend writes the secret as UTF-16 (https://sources.debian.org/src/python-keyring/25.7.0-1/keyring/backends/Windows.py), so the 2,560-byte blob limit means **1,280 characters**.
  - Users report `keyring.set_password(..., 'a'*1281)` failing with WinError 1783 ("The stub received bad data").
- **Fix:**
  - Restate the rule as "≤ 1,280 characters per value".
  - Add a length assertion in the secrets adapter. Google refresh tokens and API keys fit comfortably.

### L10. "httpx2 for all outbound HTTP" isn't true for the Gmail adapter

- **Severity:** Info
- **Spine location:** AD-1 ("Third-party SDKs (Anthropic, Google, httpx2)"); memlog HTTP decision ("httpx2 ... for all outbound HTTP").
- **Evidence:** in the resolved set, `google-api-python-client` brings `httplib2` 0.32.1, and `google-auth` brings `requests` 2.34.2. anthropic 2.8.0 does depend on `httpx2>=2,<3`, as the memlog says.
- **Fix:**
  - Change the claim to "httpx2 for all HTTP the engine issues itself; Google client libraries use their own transport".
  - AD-9 metering must wrap the `googleapiclient` request calls, not an httpx2 hook.

## Answers to the spot-check questions

| Question | Answer (evidence above) |
| --- | --- |
| import-linter: current version; works with this layout? | **2.16** (2026-11-06), Python ≥3.11, declares 3.14. It works with the layout, but only as a set of five contracts, `layers` plus four `forbidden` contracts (M6), which I ran on 3.14. |
| pydantic-settings current version | **2.16.3** (2026-11-13); resolves with pydantic 2.14.3 and pydantic-core 2.50.4 on 3.14/win. |
| ULID library | **python-ulid 4.1.0** (maintained, 2026-10-27); avoid ulid-py, py-ulid and ulid2 (L2). |
| Gmail: search Sent by custom header? | Gmail search lists a `header:` operator, but the API docs don't confirm it for API-sent `X-` headers on Sent copies. `rfc822msgid:` on our own Message-ID is unreliable because Gmail may rewrite the Message-ID. Reconcile by listing `SENT` in a time window and matching `X-Nextron-Message-Id` from `messages.get(format=METADATA)`. Prove it with a spike (H2). |
| Batches API with structured outputs and caching for Haiku 5.5 / Opus 5.5; latency guarantee | Structured outputs: **yes** (GA; "Works with: Batch processing"). Prompt caching: **yes, best-effort** (30-98%); use a 1-hour TTL. Both models are listed as supported. **Not supported:** `fallbacks` (H1). **No latency guarantee:** most finish within 1 h, then results at completion or 24 h, then expiry (M1). |
| Cloudflare KV free-tier limits; wrangler version | Free plan: 100k reads, **1k writes, 1k deletes, 1k lists** per day, 1 GB stored, reset 00:00 UTC. Workers: 100k requests/day, 10 ms CPU. wrangler **4.162.0** (M3, L8). |
| Register-ScheduledTask for a non-admin logon task | Possible for **your own** account. Pass `-User` to `-AtLogOn` and use `-LogonType Interactive -RunLevel Limited`. Set `-ExecutionTimeLimit` to zero, because the default is 3 days. Don't count on "restart on failure" for crashes. Test from a non-elevated shell (M4). |
| APScheduler 3.11 on Python 3.14 | 3.11.3 declares 3.14 and resolves. Start `AsyncIOScheduler` inside the running loop (lifespan), because `get_event_loop()` raises in 3.14 when no loop is current (L4). Its `tzlocal` dependency is the only thing bringing in `tzdata` today (M5). |
| FastAPI and Uvicorn on 3.14 / Windows | FastAPI 0.162.7 (Starlette 1.10.0, pydantic 2.14.3) and Uvicorn 0.54.0 both declare 3.14, and compiled dependencies ship cp314 win_amd64 wheels. Uvicorn uses Proactor single-process but Selector under `--reload` (L5); no uvloop on Windows. |
