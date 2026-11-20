---
title: 'Story 6.2: Connect a Sending Inbox through Gmail OAuth with an identity check'
type: 'feature'
created: '2026-11-19'
status: 'done'
baseline_commit: '59433fcb666de956f50c8897e1fc2a2951f83572'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-nextron-outreach-engine-2026-11-14/ARCHITECTURE-SPINE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-6-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Sending Inboxes can be registered (Story 4.7) but never connected: there is no Gmail grant, no proof that the mailbox is Furqaan's personal inbox on a lookalike Sending Domain, and nothing moves an inbox `connecting → active` or handles `invalid_grant`. Requirements: FR-32, FR-56 · AD-12, AD-9, AD-14, AD-1, AD-4, AD-20 · NFR-18, NFR-20 · UX-DR26, UX-DR29, UX-DR3 (epics.md Story 6.2).

**Approach:** A `GmailPort` (`get_profile`) with a fake and a real `nextron.adapters.gmail` adapter (never exercised by tests), and an `InboxConnections` service in `app.sending` that reuses Epic 1's loopback consent pieces with the `gmail.modify` scope only. After consent, `getProfile` must return the requested address, which must not be on nextronai.com, must not be a role address and (outside `prod`) must be allowlisted; otherwise the grant is revoked and nothing is stored. On success, the refresh token goes to keyring `gmail/<address>`, and the inbox is created or moved to `active` with an `events` row, a domain check queued and the domain's reputation re-applied. `invalid_grant` moves an inbox to `auth_failed` with an IDs-only ops alert; a doctor check and health issues report grants and statuses. Spike 6.1 is not done, so the whole connect path is gated on a setting that records the OQ-A2 decision.

## Boundaries & Constraints

**Always:**
- Connect is started only by a CSRF-checked POST; no GET route starts a flow. PKCE S256, random `state` compared in constant time, ephemeral `127.0.0.1` listener, `CONSENT_TIMEOUT` (5 min). The consent URL's `scope` is exactly `https://www.googleapis.com/auth/gmail.modify` (no `openid email`). The address comes from `users.getProfile`, not an ID token.
- Identity check, in order, after the token exchange: the `gmail.modify` scope granted and a refresh token present; then the profile address equals the requested address (ASCII-lowercased, `same_account`) else "Signed in as a different account"; on `nextronai.com` or a subdomain → "Sending Inboxes can't be on nextronai.com"; a role local part (`ROLE_LOCAL_PARTS`, `+tag` ignored) → "Role addresses can't send"; outside `prod`, not in `sink_allowlist` → refused (AD-14). Every refusal or failure revokes the new grant and stores nothing (no keyring entry, no DB row, no status change). The same address, label, brand, role and allowlist checks also run before the flow starts.
- Success: keyring `gmail/<address>` holds the refresh token (≤ 1,280 characters, as `SecretsPort` asserts; longer is a refusal with revoke); access tokens stay in memory only. Then one transaction: find or create the Sending Domain (`registrable_domain(address)`); create the inbox (`connecting`, cause `registered`) if it isn't registered, then `connect` → `active` (cause `connected`), or `reconsent` → `active` for an `auth_failed` inbox; `from_name` = the Sender's name setting; status timestamps; `DomainChecks.queue_check(sending_domain_id)`; and if the domain's newest check pauses (DNSBL listed or spam rate `pause`), the inbox is paused at once with that cause (Story 6.6 hand-off). If that transaction fails, the keyring entry is deleted and the grant revoked.
- `invalid_grant` on refreshing an inbox's token moves it `active`/`paused → auth_failed` (cause `invalid_grant`) with an `events` row and one `notify` ops alert (Telegram and dashboard) holding IDs only; reconnecting moves it back to `active` (`reconsent`).
- No token, code, verifier, address or label in any log, error message, `repr`, DB column, job payload or events row. Google libraries are imported only in `nextron.adapters.gmail` (plus the existing adapters); a Gmail service object is created per thread.
- Tests reach no real service (Google, Telegram, DNS) and use the test keyring only.

**Never:**
- Send email, call `messages.send`, or add sync, send or reconciliation logic (Stories 6.7–6.12). Change `domain/send_gate.py`, `sending_policy.pick_dispatch` or `Sending.dispatch_state`.
- Pause, resume or remove commands (Story 6.3), or a `pause_cause` column (6.3 adds it; the cause is read from the newest pause `events` row).
- Allow connection from nextronai.com, or any LinkedIn/Instagram automation.
- Touch Mac's real `%LOCALAPPDATA%\Nextron`, Credential Manager outside the test keyring, Task Scheduler, the registry or services.

### Decisions made overnight (pending user review)
1. **OQ-A2 gate.** No `docs/spikes/oq-a1-a2-gmail.md` exists, so the Internal-app decision is pending Spike 6.1. A new boolean setting `gmail_internal_app_confirmed` (Sending section, default `false`) records it. While it is `false`, Connect refuses with "Connecting inboxes waits for the Gmail spike (Story 6.1) to confirm the Internal OAuth app" and starts no flow; the doctor check warns. Mac sets it after 6.1. Tests set it `true`.
2. **Assumptions Spike 6.1 must confirm:** (a) Workspace allows an Internal app for the Desktop client, and each Sending Domain is in the Cloud project's Workspace org; (b) `gmail.modify` alone is enough for `users.getProfile` and returns the primary address (an alias connect is refused as "different account"); (c) Google returns a refresh token under `prompt=select_account consent` with `access_type=offline` for an Internal app; (d) a revoked or expired grant shows as `invalid_grant` at the token endpoint (not a Gmail 401); (e) the Gmail service built with `google-api-python-client` 2.202.0 and `static_discovery=True` works offline from its bundled discovery document.
3. **The migration** (`0048_6_2_inbox_connect`) adds only what 4.7 lacks: `sending_inboxes.status_changed_at` (NOT NULL, backfilled from `created_at`) and `connected_at` (nullable). The label, the status and the Sending Domain's registrable `domain` already exist (migration 0018).
4. **One consent flow at a time.** A new Connect replaces a waiting flow (its listener is closed, no outcome). The outcome is kept in memory per address and shown on the panel until the next Connect.
5. **Two ways to connect.** The registration form gains a "Connect inbox" button (`POST /inboxes/connect`, fields `address`, `label`), which needs no prior registration; and each `connecting` or `auth_failed` inbox row gets "Connect" or "Reconnect" (`POST /inboxes/reconnect`, field `inbox_id`). "Add inbox" (4.7) stays, so Sequences can still bind before connection.
6. **`auth_failed` happens only on `google_invalid_grant`** from the token refresh. A Gmail 401/403 drops the cached access token and raises a retryable `AdapterError`; it never changes status.
7. **The domain check is queued on every successful connect,** not only when the domain is new (6.5 decision 1: "calls `queue_check` inside its connect transaction").
8. **Free-mail Sending Domains** (deferred-work, from 4.7) stay undecided: not refused here. Mac decides before FR-64 goes live.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Happy, new address | gate on; `furqaan@nextron-mail.com` allowlisted; profile returns the same | keyring `gmail/furqaan@nextron-mail.com`; domain created; inbox `active` with `registered` and `connected` events; `check_domains` queued; panel "Connected" | N/A |
| Registered inbox | an inbox `connecting` | moves `connecting → active`; `from_name` refreshed | N/A |
| Reconnect | an inbox `auth_failed` | `auth_failed → active` (`reconsented`); the old refresh token is replaced and revoked | N/A |
| Different account | profile returns `other@nextron-mail.com` | grant revoked; nothing stored; "Signed in as a different account" | N/A |
| Brand / role | profile on `mail.nextronai.com`, or `sales@…` | revoked; nothing stored; "Sending Inboxes can't be on nextronai.com" / "Role addresses can't send" | N/A |
| Not allowlisted | env `dev`, address not in `sink_allowlist` | refused before the flow; if reached after consent, revoked; nothing stored | N/A |
| State mismatch / timeout | wrong `state`, or no callback in 5 minutes | nothing stored; "consent failed" | N/A |
| Gate off | `gmail_internal_app_confirmed` false | refused, no listener, no browser | N/A |
| Listed domain | newest check has DBL `listed` | inbox becomes `active` then `paused` (cause `blocklist`) in the same transaction | N/A |
| invalid_grant | refresh raises `google_invalid_grant` on an `active` inbox | `auth_failed`, one event, one IDs-only alert; the health panel lists it | N/A |
| Crowded domain | 4 inboxes on one domain | "More than 3 inboxes on <domain>" shown; connect not blocked | N/A |
| Store fails | keyring write or the DB transaction fails | the grant revoked, keyring entry deleted; "consent failed" | logged by code only |

</frozen-after-approval>

## Code Map

- **Reuse (Epic 1, Story 1.9):** `nextron/domain/google_oauth.py`: `code_challenge`, `authorization_url` (add a scope-only variant, such as `authorization_url_for_scope(client_id, scope, redirect_uri, state, challenge)`, sharing the parameter list; leave `authorization_url`'s output unchanged), `CONSENT_TIMEOUT`, `decode_client_config`, `CLIENT_SECRET_NAME`, `same_account`, `ConsentFailure`. `nextron/ports/google_oauth.py`: `GoogleOAuthPort` (`exchange`, `refresh`, `revoke`), `ConsentListener`. `nextron/app/setup/api.py` `GoogleAccess` (lines ~2140–2470) is the pattern for the flow, the `_Flow` record, supersede, `_complete` (state check with `hmac.compare_digest`) and revoke; don't change it. `main.py` builds `LoopbackListener`, `open_in_browser` and the `GoogleOAuthPort` for it; reuse the same objects.
- **Domain:** `nextron/domain/sending_inbox.py` already has the statuses, causes and the exact AD-4 table (`transition`, `PAUSE_EVENTS`), `check_address`, `check_label`, `allowlist_refusal`, `crowded_domains`, `ROLE_LOCAL_PARTS`, `BRAND_DOMAIN`. Add the connect refusals and texts (`ConnectFailure` or extended `Refusal`) and an `identity_problem(requested, profile, env, allowlist)` pure function; the unit tests prove the transition table is exactly AD-4's.
- **Port and adapter:** new `nextron/ports/gmail.py` `GmailPort.get_profile(access_token) -> str` (the `emailAddress`); errors `AdapterError` codes `gmail_unauthorized` (401/403, not retryable), `gmail_unavailable` (retryable), `gmail_bad_response`. New `nextron/adapters/gmail/__init__.py` (googleapiclient `build("gmail", "v1", credentials=google.oauth2.credentials.Credentials(token), static_discovery=True, cache_discovery=False)` per call/thread via `threading.local`; metered through `Metered`/`UsageMeterPort` like `adapters/postmaster`). New `tests/fakes/gmail.py` `FakeGmail(profiles=...)`; `tests/fakes/__init__.py` `PORT_FAKES`; `tests/live_guard.py` `LIVE_CONSTRUCTORS`/`ADAPTER_PACKAGES`; `pyproject.toml` c5 `source_modules` and the non-AI adapter list; `tests/contracts/test_test_isolation.py`; `test_adapter_boundary` as fits (no live call).
- **App:** new `nextron/app/sending/connect.py` `InboxConnections(secrets, clock, *, oauth, listen, browser, gmail, env, sending, domain_checks, notify, open_unit, start=start_daemon)`: `start_connect(address, label)`, `start_reconnect(inbox_id)`, `outcome()`, `access_token(inbox_id)` (in-memory cache; on `google_invalid_grant` → `Sending.mark_auth_failed`), `check_inbox(inbox_id, *, network)` for doctor. The consent thread must open its own unit of work (`nextron/db/uow.py`: units are thread-bound; `UnitOfWorkFactory.open()`). `nextron/app/sending/api.py` `Sending`: add `@use_case connect_inbox(address, label|None, *, now)` (create-if-needed + transitions + `queue_check` + reputation re-pause) and `mark_auth_failed(inbox_id)`; update the docstring. `nextron/app/sending/checks.py` `DomainChecks.queue_check` (existing; call it, don't change it).
- **Repos and model:** `nextron/db/repos/sending/inboxes.py` (`set_status` is fenced; add timestamps and `from_name` update); `nextron/db/models/sending.py` (columns, docstring); `nextron/db/owners.py`; `nextron/db/pii_registry.py` (class `none`); new `nextron/db/alembic/versions/0048_6_2_inbox_connect.py` (down `0047_6_6_reputation`; pattern `0046_6_11_inbox_pacing.py`).
- **Settings:** `nextron/domain/settings_schema.py`: `gmail_internal_app_confirmed` (BOOLEAN, default false, Sending section, last entry); update the "every setting" lists in settings tests.
- **Reads and health:** `nextron/app/queries/sending.py`: `SendingInboxView` gains `status_changed_at`, `connected_at`, `pause_cause` (newest `pause_*` event's cause, `None` otherwise); `health_issues` adds `inbox_paused` (with cause) and `inbox_auth_failed` issues before the domain issues. `nextron/main.py`: build `InboxConnections`, pass it to `create_app`; `build_doctor_checks` registers `sending_inbox_grants` (doctor: refresh + `getProfile` match per non-removed, non-`connecting` inbox; in-engine: keyring presence only, no network; warn while the gate setting is off); update the module docstring.
- **Web:** `nextron/web/inboxes_page.py`, `templates/inboxes_panel.html` (Connect inbox button with `hx-post="/inboxes/connect"`, per-row Connect/Reconnect forms, the flow notice, polling every 2 s while a flow waits, status with pause cause, connected time); `nextron/web/app.py` routes `POST /inboxes/connect`, `POST /inboxes/reconnect`, `@personal_data(True)`; health panel inbox status list (`web/health_page.py` and its template). `tests/dashboard.py` wiring (FakeGmail).
- **Migration bookkeeping (track rules §3):** `HEAD` in `tests/contracts/test_persistence_contracts.py`, `tests/unit/test_migrations.py`, `tests/app/test_serve_process.py`; chain probes; `tests/contracts/test_source_tree.py` `FILES`.
- **Do not change:** `domain/send_gate.py`, `sending_policy.pick_dispatch`, `Sending.dispatch_state`, `GoogleAccess`'s behaviour, 6.5/6.6 check rules.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/sending_inbox.py`, `nextron/domain/google_oauth.py` -- connect refusals, `identity_problem`, scope-only consent URL -- pure rules.
- [x] `nextron/ports/gmail.py`, `nextron/adapters/gmail/`, `tests/fakes/gmail.py`, guards, c5 -- `GmailPort`.
- [x] `settings_schema.py` -- `gmail_internal_app_confirmed`.
- [x] `db/models/sending.py`, `versions/0048_6_2_inbox_connect.py`, `owners.py`, `pii_registry.py`, `repos/sending/inboxes.py` -- columns and writes.
- [x] `app/sending/api.py`, `app/sending/connect.py` -- connect, reconsent, auth-failed, alert, reputation re-pause, token cache.
- [x] `app/queries/sending.py`, `main.py` -- views, health issues, doctor check, wiring.
- [x] `web/inboxes_page.py`, templates, `web/app.py`, health panel, `tests/dashboard.py` -- the panel.
- [x] Tests: `tests/unit/test_sending_inbox.py` (exact AD-4 table, `identity_problem` each branch incl. `+tag` role, subdomain brand, case), `tests/unit/test_google_oauth.py` (scope-only URL), `tests/unit/test_gmail_adapter.py` (guard raises in test env, error mapping as a pure function, no network), `tests/app/test_inbox_connect.py` (every I/O row on a migrated temp DB with `FakeGoogleOAuth`, a fake listener, the test keyring, `FakeGmail`; revoke called on each refusal; no keyring entry or row after a refusal; events; queued `check_domains`; the re-pause), panel/route tests (POST only, CSRF, no GET starts a flow), health and doctor tests, a log-redaction test (planted token and address never in logs).
- [x] Bookkeeping updates and `nextron-engine/README.md`: a "Connecting a Sending Inbox" section, the adapter, the setting and the migration row.

**Acceptance Criteria:**
- Given the full suite through the suite lock and `uv run lint-imports`, then everything passes and every contract is kept, with `nextron.adapters.gmail` in c5 and the non-AI adapter list.
- Given `grep -rn "googleapiclient\|google.oauth2" nextron/`, then only `nextron/adapters/` matches.
- Given the persistence contracts, then the new columns are owned by `sending`, classified, and the migration matches the model.
- Given any refusal path, then the fake OAuth port recorded a `revoke` of the new token and the test keyring has no `gmail/<address>` entry.

## Implementation Notes

**Built (implementer, 2026-11-19).**

- **Domain.** `sending_inbox.py`: `GMAIL_SCOPE`, `gmail_secret_name`, `ConnectFailure` (24 values) + `CONNECT_MESSAGES` (spec texts exact; generic ones start "Consent failed"), `ConnectRefused` (code `connect_refused`), `connect_failure(Refusal)`, `identity_problem(requested, profile, env, allowlist)` (different account → `check_address` brand/role/invalid → allowlist), `INBOX_AUTH_FAILED`/`INBOX_PAUSED` kinds, hints, `auth_failed_text`. The AD-4 table is unchanged. `google_oauth.py`: `authorization_url_for_scope` (shares `_consent_url` with `authorization_url`, whose output is unchanged) and `has_scope`.
- **Port/adapter.** `ports/gmail.py` `GmailPort.get_profile`; `adapters/gmail` `GmailProfiles(meter, service=None)`, live constructor `default_service` (`build("gmail","v1", Credentials(token), static_discovery=True, cache_discovery=False)`), one service per thread via `threading.local` (rebuilt when the token changes), metered `gmail/get_profile`; pure `error_for_status`. Fake `tests/fakes/gmail.py`; `PORT_FAKES`, `live_guard` (`Guarded(("default_service",))`), c5 list, `test_test_isolation`, `test_no_linkedin_adapter` (approved extra port `GmailPort`, since AD-9 names only Mail/MailboxPort) updated. Not added to `test_adapter_boundary` (its drivers are httpx-based; the adapter's error mapping and metering are unit-tested instead).
- **Setting.** `gmail_internal_app_confirmed` (BOOLEAN, default false, Sending, last).
- **DB.** Migration `0048_6_2_inbox_connect` (down `0047_6_6_reputation`): adds `status_changed_at` + `connected_at` in place, backfills, rebuilds from 0046's exact table to make `status_changed_at` NOT NULL. owners/pii (`none`). Repo: `insert` sets `status_changed_at`; `set_status(..., at=)` (Story 6.6's `_pause` now passes `now`); `by_id`, `mark_connected`, `pause_causes` (newest `events` row into `paused`, read-only).
- **App.** `Sending(clock, *, env, gap_source, domain_checks=None, notify=None)`: `@use_case connect_inbox(address, label|None, *, now)` and `@use_case mark_auth_failed(inbox_id)` (see module docstring). `app/sending/connect.py` `InboxConnections(secrets, clock, *, oauth, listen, browser, gmail, env, sending, open_unit, start)` — note `domain_checks`/`notify` are injected into `Sending` rather than `InboxConnections` (the connect transaction is Sending's use case). It opens its own units, each in a fresh `contextvars.Context`. Outcome kept in memory (`status()`).
- **Reads/health/doctor.** `SendingInboxView` + `status_changed_at`, `connected_at`, `pause_cause`; `address_registered`; `inbox_health_issues()` prepended to `health_issues` (inbox ids only). Doctor check `sending_inbox_grants` registered last (`startup_checks.check_sending_inbox_grants`): warn while the gate is off; doctor form refreshes + `getProfile`, in-engine form keyring only. `ReadinessDatabase` gains `gmail_app_confirmed()` and `connected_inboxes()`; `DoctorContext.inbox_connections`.
- **Web.** `POST /inboxes/connect` (fields `address`, `label`), `POST /inboxes/reconnect` (`inbox_id`), `GET /inboxes/connect` (status partial `inbox_connect_status.html`, polled every 2 s only while waiting; starts nothing), all `@personal_data(True)`. Panel: "Connect inbox" button (`hx-post` on the button), Connected and Gmail columns, per-row Connect/Reconnect form, status with pause cause. Health panel: "Sending Inboxes" table (label, status+cause, last connected).
- **main.py.** One `GoogleOAuthPort`/browser shared by `GoogleAccess` and `InboxConnections`; `build_app(..., gmail=None)`; `doctor(..., gmail=None)` builds its own `InboxConnections` (no unit of work, no consent).

**Decisions made while implementing (conservative, pending review).**
1. `access_token(inbox_id)` must be called outside any transaction (raises `RuntimeError`) and runs `mark_auth_failed` in its own unit (fresh context), because it is called from inside other use cases (6.8+). On `invalid_grant` it raises non-retryable `gmail_auth_failed`.
2. Connecting an `active` or `paused` inbox is refused (`already_connected`); removed addresses are refused (`inbox_removed`).
3. A flow superseded after Google answered revokes its new grant (GoogleAccess silently drops it); no outcome is recorded.
4. On a failed connect transaction during a reconnect, the keyring entry is deleted (per spec), so the old (already invalid) token is not restored.
5. An address whose `gmail/<address>` isn't a valid keyring name (e.g. contains `'`) is refused before the flow as `invalid_address`.
6. Alert subject for `inbox_auth_failed` is the `events` row id, so each auth failure alerts once.
7. Doctor's `sending_inbox_grants` warns (not fails) while the gate is off, so a fresh `doctor` shows 14 ok + 1 warn.

**Verification.** `tests/contracts` (one full run: 1,892 passed, 7 failed on stale lists; those 7 fixed and the affected contract files re-run green), `uv run lint-imports` (29 kept), and targeted runs: `test_sending_inbox`, `test_google_oauth_domain`, `test_gmail_adapter`, `test_inbox_grants_check`, `test_inbox_connect` (every I/O row), `test_sending_inboxes`, `test_domain_checks`, `test_domain_reputation`, `test_inboxes_page`, `test_settings_schema`, `test_settings_page`, `test_doctor`, `test_runner_lifespan`, `test_health_page`, `test_migrations`, `test_startup_checks`, `test_telegram_secrecy`, `test_google_secrecy`, `test_engine_health`, `test_enroll`, `test_inbox_pacing`, `test_send_gate` (unit/app), `test_serve` — all pass. Full suite not run (orchestrator's gate).

**Review patches (P1–P13, 2026-11-20).** P1: `_complete` wraps `_verify` (steps 2–4): any exception logs `inbox_connect_crashed`, revokes the new grant and is `crashed` with its revocation. P2: `GmailProfiles.get_profile` builds the service inside the `try` and `Metered`; a build failure is `gmail_unavailable` (retryable, metered, `from None`; in tests the live guard's `LiveAdapterInTests` is that error's context). P3: a replaced refresh token is overwritten, not revoked (same account and client; Google's revoke could end the new grant); success outcomes carry no `revocation`; module docstring step 8 and README updated. P4: reconnecting an `auth_failed` inbox whose newest `auth_fail` event came from `paused` re-pauses it at once with its newest pause event's cause (any cause; actor `system:sending`, no `cause_ref`), after `_repause`'s blocklist/spam check (which wins when it pauses); new read `repos.sending.inboxes.newest_event_into(inbox_id, to_status)`; the cause is `ConnectedInbox.paused`. P5: a failing `mark_auth_failed` inside `access_token` logs `inbox_mark_auth_failed_failed` (inbox id only) and still raises `gmail_auth_failed`. P6: `main._gmail_app_confirmed` treats unset/invalid as `False`. P7: `_Flow`, `_Plan`, `ConnectOutcome`, `ConnectStatus` keep address/label out of `repr`. P8: `RESUME_HINT` = "It sends nothing while paused. Fix the cause; a Resume button comes with Story 6.3." P9: `test_0048_backfills_status_changed_at_and_keeps_every_inbox` (the rebuild keeps every row and its order but renumbers rowids; nothing reads an inbox rowid). P10–P13: tests for the crowded domain, the panel's Reconnect/paused-cause rows and notices, the health inbox table, doctor and in-engine health with the gate on, `status_changed_at` after later transitions, a failed revoke, the newest pause cause, and `repr`. P11: import order in `main.py` and `health_page.py`.

## Spec Change Log

## Review Triage Log

Pass 1: 3 layers (BH blind hunter 14, ECH edge-case hunter 10, VG verification gap 6 + 1 other) = 31 raw; 27 after dedup (BH1=ECH1=ECH10, BH8=VG6, BH10=matrix audit). Patched P1–P13; rejected the rest; none deferred.

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| 1 | ECH1, BH1, ECH10 | A non-`AdapterError` after the exchange (`_verify`'s unit of work, `identity_problem`, `secret_length`) reaches `_run`'s catch-all as `crashed` without revoking the new grant | medium | `_complete` catches only `AdapterError`; the Boundaries say every failure revokes | patch P1 |
| 2 | ECH2, BH1 | `GmailGoogle.get_profile` builds the service outside its `try`, so a build error escapes unmapped and unmetered | low | direct move inside the try | patch P2 |
| 3 | ECH3 | A pre-existing `gmail/<address>` entry is overwritten then deleted (not revoked) when `connect_inbox` fails | low | only reachable for an `auth_failed` reconnect (old grant already dead) or a crash orphan; decision 7 of the implementation notes | reject |
| 4 | ECH4 | A flow superseded during `_store` connects the inbox but its outcome is dropped | low | the inbox row shows `active`; only the notice is missing; narrow race | reject |
| 5 | ECH5 | On success the replaced refresh token is revoked; it is the same account and client, and Google's revoke can drop the whole grant, killing the new token | medium | Epic 1 skips revoking a token another grant shares for this reason (`_held_by_a_grant`); the identity check guarantees the same account | patch P3 |
| 6 | ECH6 | An inbox `paused` (operator, bounce) that hits `invalid_grant` comes back `active` on reconnect; `_repause` re-applies only blocklist/spam | medium | AD-4 maps `reconsent` to `active`; nothing restores the earlier pause | patch P4 |
| 7 | ECH7 | A failure inside `mark_auth_failed` surfaces as a raw exception from `access_token` | low | direct guard: log and still raise `gmail_auth_failed` | patch P5 |
| 8 | ECH8 | A cached access token outlives a removal | low | removal is Story 6.3; `dispatch_state` refuses non-`active` inboxes | reject (noted for 6.3) |
| 9 | ECH9 | Doctor reads `gmail_internal_app_confirmed` without catching `InvalidSettingValue`, so it fails where the gate treats it as false | low | `main._gmail_app_confirmed` | patch P6 |
| 10 | BH2 | `_Flow`, `_Plan`, `ConnectOutcome`, `ConnectStatus` carry the address/label in `repr` | low | Boundaries forbid an address in any `repr`; `field(repr=False)` | patch P7 |
| 11 | BH3 | "Connect inbox" on an already-registered `connecting` address ignores a different label | low | the label is validated but unused; harmless | reject |
| 12 | BH4 | `RESUME_HINT` points to a Resume button that arrives with Story 6.3 | low | direct text correction | patch P8 |
| 13 | BH5 | A crash between the keyring write and the DB commit leaves an orphan grant | low | crash window of milliseconds; reconnecting replaces it | reject |
| 14 | BH6 | `access_token` refreshes again for an `auth_failed` inbox; no single-flight refresh | low | callers arrive with 6.8; `dispatch_state` gates non-active inboxes | reject |
| 15 | BH7 | Doctor's `invalid_grant` doesn't mark the inbox `auth_failed` | low | doctor reads the database read-only and takes no lock by design (Story 1.14) | reject |
| 16 | BH8, VG6 | No migration test for 0048's backfill and rebuild | medium | `test_migrations` has one per earlier sending migration | patch P9 |
| 17 | BH9 | New NOT NULL `status_changed_at` without a server default breaks raw `INSERT INTO sending_inboxes` fixtures on other tracks at merge | medium | three fixtures here needed it | reject (no code fix: listed as a merge touchpoint in the report) |
| 18 | BH10 | Crowded-domain matrix row has no connect test | medium | matrix audit | patch P10 |
| 19 | BH11 | Paused inboxes add per-inbox health issues on top of the domain issue | false | the AC asks the health panel to list every paused inbox | reject |
| 20 | BH12 | Spec bookkeeping (sprint status, test file name, signature) | low | sprint status moves at the end; fix is a spec edit | reject |
| 21 | BH13 | Two imports out of order (`health_page.py`, `main.py`) | low | direct correction | patch P11 |
| 22 | BH14 | Health inbox table identifies inboxes by label only | low | labels are the Operator's own names | reject |
| 23 | VG1 | No test for the `auth_failed` Reconnect row, the paused-cause status, or the health inbox table | gap | as filed | patch P12 |
| 24 | VG2 | Doctor/health wiring only tested with the gate off | gap | as filed | patch P12 |
| 25 | VG3 | No test pins `status_changed_at` after a transition at a later time | gap | as filed | patch P12 |
| 26 | VG4 | No test for a failed revoke (`REVOKE_HINT`) or the paused-at-once notice | gap | as filed | patch P12 |
| 27 | VG5 | No test that the newest pause event's cause wins | gap | as filed | patch P13 |
| 28 | VG other | `test_runner_lifespan` finds readiness rows by `len(row) == 3`, which the inbox table also matches | low | no failure today (no inbox seeded there) | reject |

## Design Notes

Merge touchpoints (not in this tree): Story 5.2 `OutboundMessage` (`fb8d52b`), 4.15, 4.17, 5.1, 8.x and 11.x are on or heading to `main`; main's migration head is ~0062+, so `0048_6_2_inbox_connect` is renumbered at merge. Story 6.3 adds `pause_cause` and pause/resume/remove (removal must revoke and delete `gmail/<address>`). Story 6.8+ uses `InboxConnections.access_token(inbox_id)` and must create the Gmail service per thread. `sending_inboxes.status_changed_at` is NOT NULL with no server default: any raw `INSERT INTO sending_inboxes` fixture on `main` (5.x, other tracks) must add it at merge. The 0048 rebuild keeps rows and their order but renumbers `sending_inboxes` rowids (nothing reads them).

## Verification

**Commands:**
- `cd e:\Nextron-epic2\nextron-engine; uv run pytest -q -n 2 --basetemp="e:\Nextron AI outreach\_handoff\scratch\track-B\pt" <touched test files>` -- pass
- `uv run pytest -q -n 2 --basetemp=... tests/contracts` -- pass
- `uv run lint-imports` -- every contract kept
