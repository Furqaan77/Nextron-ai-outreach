---
title: 'Story 1.9: Grant Google OAuth access through a loopback flow'
type: 'feature'
created: '2026-11-17'
status: 'done'
route: 'dispatch'
baseline_commit: 'b31e084846829c5e260da08cbfad72e1b299318b'
review_loop_iteration: 0
story_key: '1-9-grant-google-oauth-access-through-a-loopback-flow'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The engine can't hold a Google OAuth grant. Mac has no way to import the Google Desktop client, or to grant `internal_notify` (`gmail.send`) and Postmaster Tools (`postmaster.readonly`) access, and later stories (internal email, spam-rate reads, Sending Inboxes) have no flow to reuse (FR-56, AD-12, AD-14, INT-2, INT-3, NFR-18, UX-DR27).

**Approach:**
- **Import.** A Google section on the Credentials page takes the path of the downloaded `client_secret.json`. The file is parsed as an installed-app client, the client id and secret go to keyring as `google/client_config`, and the file is deleted once that write succeeds.
- **Grant.** A POST starts an installed-app loopback flow: a one-shot listener on `127.0.0.1:<ephemeral>`, PKCE S256, a random `state`, and the consent page opened in the default browser. The row shows "waiting for consent" for at most 5 minutes.
- **Store and check.** On a matching redirect, the code is exchanged. The account comes from the ID token, then the domain and dev/test allowlist rules are checked. The refresh token goes to `google/<grant>` and the account and time go to `meta/google/<grant>`. Access tokens stay in memory.
- **Re-grant, remove, refresh.** Re-granting or removing revokes the old token. A later `invalid_grant` on refresh marks the grant "needs re-consent".
- **Keyring race.** It also closes the deferred cross-process keyring race: each secret gets its own Credential Manager target.

The acceptance criteria are Story 1.9 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**
- **Domain** (`nextron/domain/google_oauth.py`, stdlib only, pure, takes `now`).
  - **Endpoints and limits.**
    - `AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"`, `TOKEN_URI = "https://oauth2.googleapis.com/token"`, `REVOKE_URI = "https://oauth2.googleapis.com/revoke"`, and `ID_TOKEN_ISSUERS = {"https://accounts.google.com", "accounts.google.com"}`.
    - `CONSENT_TIMEOUT = 5 min`, `MAX_CLIENT_FILE_BYTES = 65_536`, `INTERNAL_DOMAIN = "nextronai.com"`, and `IDENTITY_SCOPES = ("openid", "email")`.
  - **Keyring names.** `CLIENT_SECRET_NAME = "google/client_config"` and `CLIENT_META_NAME = "meta/google/client_config"`. The meta entry holds `encode_updated_at`.
  - **Grants.** `GoogleGrant` (StrEnum: `internal_notify`, `postmaster`). `GrantSpec(grant, label, scope)` has `secret_name` = `google/<grant>` and `meta_name` = `meta/google/<grant>`. `GRANTS` holds them in order, and `grant_spec(value)` raises `UnknownGrant` (`unknown_grant`). The two grants are:
    - "Internal notifications (internal_notify)": `https://www.googleapis.com/auth/gmail.send`;
    - "Postmaster Tools (postmaster)": `https://www.googleapis.com/auth/postmaster.readonly`.
  - **`ClientConfig(client_id, client_secret)`.** Frozen, with the secret left out of its `repr`.
  - **`InvalidClientFile(reason)`** (`invalid_client_file`). Its reason never quotes the file or the path.
  - **`client_path(text)`.**
    1. Strip spaces, one pair of surrounding `"` (File Explorer's "Copy as path"), then spaces again.
    2. Refuse when the result is empty, longer than 1,024, or contains a control character.
    3. Refuse unless it is an absolute `PureWindowsPath` whose drive is a single letter: UNC, `\\?\` and relative paths are refused.
    4. Refuse unless it ends in `.json`, in any case.
  - **`parse_client_file(raw: bytes)`.**
    1. Decode UTF-8 (a BOM is allowed) and parse JSON.
    2. A top-level object must have an `"installed"` object. A `"web"` client gets its own reason: "create a Desktop app client".
    3. `client_id` must fullmatch `[0-9]{1,30}-[0-9a-z]{1,64}\.apps\.googleusercontent\.com`.
    4. `client_secret` must fullmatch `[\x21-\x7e]{1,200}`.
  - **`encode_client_config`.** Compact JSON `{"client_id","client_secret"}`. `decode_client_config(text)` returns `ClientConfig | None` under the same field rules.
  - **`code_challenge(verifier)`.** The base64url SHA-256 of the verifier, with no padding (it matches the RFC 7636 Appendix B vector).
  - **`authorization_url(client_id, spec, redirect_uri, state, challenge)`.** `AUTH_URI?` followed, in order, by: `response_type=code`, `client_id`, `redirect_uri`, `scope` (`<spec.scope> openid email`), `state`, `code_challenge`, `code_challenge_method=S256`, `access_type=offline`, `prompt=select_account consent`. There is no `include_granted_scopes`.
  - **`id_token_account(id_token, client_id, now)`.** Returns the `email` claim, or `None`. The payload is base64url JSON; there is no signature check (decision 1). The claims must be:
    - `iss` in `ID_TOKEN_ISSUERS`;
    - `aud` a `str` equal to `client_id`;
    - `exp` greater than `now`;
    - `email_verified is True`;
    - `email` a valid email per `domain.sink.is_sink_recipient`, containing `@`.
  - **`scope_granted(scope_text, spec)`.** Whether `spec.scope` is among the space-separated scopes.
  - **`ConsentFailure`** (StrEnum): `timed_out`, `state_mismatch`, `declined`, `exchange_failed`, `no_account`, `scope_not_granted`, `no_refresh_token`, `not_internal_domain`, `not_allowlisted`, `store_failed`, `browser_failed`, `listener_failed`, `crashed`.
  - **`account_problem(spec, account, env, allowlist)`.** For `internal_notify`, the part after the last `@`, ASCII-lowercased, must equal `nextronai.com`; otherwise `not_internal_domain` (`INTERNAL_NOTIFY_DOMAIN = "internal_notify must be a nextronai.com account"`). When `sink_applies(env)` and not `allowlisted(account, allowlist)`, the result is `not_allowlisted`.
  - **`GrantMeta(account: str | None, granted_at, needs_reconsent_at=None)`.** Encoded as compact JSON with `…Z` times. `decode_grant_meta` returns `None` for anything else, and `same_account(a, b)` compares after ASCII lowercasing.
- **Ports** (`nextron/ports/google_oauth.py`, all `@runtime_checkable`):
  - **`TokenSet(access_token, expires_in: int, scope: str, refresh_token=None, id_token=None)`.** Frozen, with every token left out of its `repr`.
  - **`GoogleOAuthPort`.**
    - Methods: `exchange(client, code, verifier, redirect_uri) -> TokenSet`, `refresh(client, refresh_token) -> TokenSet` and `revoke(token) -> None`.
    - Codes: `google_invalid_grant` (False), `google_rejected` (other 4xx, False), `google_unavailable` (network, timeout, 429, 5xx, True) and `google_bad_response` (False).
    - `revoke` treats 200, and a 400 whose `error` is `invalid_token`, as done.
  - **`ConsentListener`.**
    - `redirect_uri() -> str`;
    - `wait(timeout: float) -> Mapping[str, tuple[str, ...]] | None`: the query of the first request to `/`, or `None` on timeout or close;
    - `close() -> None`, which is idempotent.
  - **`ClientFilePort`.** `read(path, limit) -> bytes` and `delete(path) -> None`. The codes are `client_file_missing`, `client_file_not_regular` (a folder, link or reparse point), `client_file_too_large`, `client_file_unreadable` and `client_file_not_deleted`. A missing file on delete is a no-op.
- **Adapter** (`nextron/adapters/google_oauth/__init__.py`: `nextron.ports`, `nextron.domain`, `httpx2` and the stdlib). It raises only `AdapterError`, never with response or exception text, and it logs nothing that holds a token, a code or a URL.
  - **`default_http_client()`** is the live constructor: an `httpx2.Client` with a 15 s timeout and no redirects.
  - **`GoogleOAuthClient(http=None)`.** It builds the default client lazily, on the first call. Its POSTs are form-encoded:
    - **exchange:** `code`, `client_id`, `client_secret`, `redirect_uri`, `grant_type=authorization_code`, `code_verifier`;
    - **refresh:** `refresh_token`, `client_id`, `client_secret`, `grant_type=refresh_token`;
    - **revoke:** `token`.

    A 2xx needs a JSON object with a non-empty `access_token` and an integer `expires_in > 0`.
  - **`LoopbackListener()`.**
    - **Binding.** It binds `127.0.0.1:0` (an `OSError` becomes `listener_failed`) and serves on a daemon thread. `redirect_uri()` is `http://127.0.0.1:<port>/`.
    - **What it accepts.** Only a GET whose `Host` is exactly `127.0.0.1:<port>` and whose path is exactly `/` counts; any other path gets 404 and a wrong `Host` gets 400. The first counting request records its `parse_qs` (blank values kept, at most 20 fields) and gets a fixed `no-store` page with a strict CSP: "Nextron received Google's answer. You can close this tab and return to the dashboard." Later requests get 404.
    - **No request logging.** `log_message` is silenced, because the request line carries the code.
  - **`LocalClientFiles`.** Uses `os.lstat`: a regular file only, at most `limit` bytes.
- **Service** (`GoogleAccess` in `nextron/app/setup/api.py`; it touches no database).
  - **Constructor.** `GoogleAccess(secrets, clock, *, oauth, listen: Callable[[], ConsentListener], files, browser: Callable[[str], bool], env, allowlist: Callable[[], Iterable[str]], start=<daemon thread>)`.
  - **Two locks.** An in-memory-state lock covers flows, outcomes and cached access tokens, and is never held over I/O. A store lock serialises keyring changes to grants, and may be held over a revoke. When both are taken, the store lock comes first.
  - **`client_status()`** returns `CredentialState` and the time.
  - **`import_client(path_text)`.**
    1. `client_path`, then `files.read(path, MAX_CLIENT_FILE_BYTES)`. Each file code becomes an `InvalidClientFile` reason.
    2. `parse_client_file`, then encode. A result over 1,280 UTF-16 units is refused with `SECRET_TOO_LONG`.
    3. Write the secret, then the meta. An `AdapterError` raises `CredentialStoreFailed("google_client")`, and the file stays.
    4. Drop the cached access tokens.
    5. `files.delete`; if it fails, `file_deleted=False`.
    6. Return `ClientImport(status, file_deleted)`.
  - **`start_grant(grant)`.**
    1. Read the client. If it is absent or can't be decoded, raise `ClientNotImported`.
    2. Cancel any waiting flow of this grant: mark it and close its listener.
    3. Start the listener with `listen()`.
    4. Make `state = token_urlsafe(32)` and `verifier = token_urlsafe(64)`.
    5. Register the flow, waiting until `now + CONSENT_TIMEOUT`.
    6. Call `browser(url)`. If it returns false or raises, the outcome is `browser_failed`.
    7. `start(run)`. The run calls `wait(300.0)`, then closes the listener, then completes.

    `listen` failing gives the outcome `listener_failed`.
  - **Completing a flow** never raises; an unexpected error is `crashed`. The steps, in order:
    1. No redirect: `timed_out`.
    2. `state` isn't given exactly once and equal to the flow's (`hmac.compare_digest`): `state_mismatch`.
    3. An `error`, or not exactly one `code`: `declined`.
    4. `exchange`; any `AdapterError` is `exchange_failed`.
    5. No account from the ID token: `no_account`.
    6. The scope wasn't granted: `scope_not_granted`.
    7. No refresh token: `no_refresh_token`.
    8. `account_problem`. An allowlist provider that raises counts as an empty allowlist and logs `google_allowlist_unreadable`.
    9. If the flow was superseded, stop with no outcome.
    10. Store the refresh token, then the meta. On a failure: `store_failed`.
    11. Revoke the replaced token (see Revoking).
    12. Cache the access token, with its expiry, in memory.

    Every refusal or failure from step 5 on revokes the new refresh token (or the access token, when there's no refresh token) and stores nothing.
  - **Revoking.** A token is revoked only when no stored grant holds the same account (decision 2). Otherwise the outcome's `Revocation` is `shared`. A revoke error gives `failed`, and a revoke that succeeds gives `revoked`.
  - **`remove_grant(grant)`.** It cancels a waiting flow, revokes (decision 3), deletes the token and then the meta, drops the cached access token, and returns `GrantRemoval(status, revocation)`. A keyring failure raises `CredentialStoreFailed`.
  - **`access_token(grant)`.** A cached token is used while `expires_at - 60 s > now`. Otherwise:
    - a missing client is `google_client_not_imported`, and a missing token is `google_not_granted`;
    - a meta that already says "needs re-consent" is `google_needs_reconsent`, with no call to Google;
    - `refresh` → `google_invalid_grant` writes `needs_reconsent_at` to the meta (only if the stored token is still the one that failed), logs `google_grant_needs_reconsent`, and raises `google_needs_reconsent`.

    Every error is an `AdapterError`, `False`.
  - **Status.** `grant_statuses()` and `grant_status(grant)` return `GrantStatus(spec, state, account, granted_at, needs_reconsent_at, waiting_until, outcome)`. `GrantState` is `not granted`, `granted`, `needs re-consent` or `unknown`; the last means the keyring can't be read.
  - **Logs.** These events carry only `grant`, `reason` and `code`: `google_client_imported`, `google_client_file_kept`, `google_store_failed`, `google_consent_started`, `google_consent_failed`, `google_grant_stored`, `google_grant_removed`, `google_token_revoked`, `google_revoke_failed`, `google_revoke_skipped`, `google_grant_needs_reconsent` and `google_consent_crashed`.
- **Page** (`nextron/web/google_page.py` view model; `google_client_row.html`; `google_grant_row.html`; a Google section in `credentials.html`).
  - **`GET /credentials`** becomes `@personal_data(True)`, because it shows the account.
  - **`POST /credentials/google/client`** (`google_client_import`, `False`). The fields are exactly `path`, given once. The answers are:
    - "Imported. The file was deleted." (`status`);
    - "Imported, but the file could not be deleted: delete it yourself." (`alert`);
    - "Nothing was imported: <reason>." (`alert`);
    - `STORE_FAILED`.
  - **`POST /credentials/google/grant`** (`google_grant_start`, `True`). The field is `grant`. The answer is the row, or "Import the Google Desktop client first." (`alert`).
  - **`GET /credentials/google/grant/{grant}`** (`google_grant_row`, `True`) returns the row alone. An unknown grant gets 404.
  - **`POST /credentials/google/remove`** (`google_grant_remove`, `True`). It takes `grant` and an optional `confirm=yes`, in two steps like credentials. It answers "Removed." plus the revocation note.
  - **Row texts.** The state is one of:
    - "Not granted";
    - "Granted for <account>, YYYY-MM-DD HH:MM UTC";
    - "Needs re-consent: Google refused the stored grant on … UTC. Grant it again.";
    - the unreadable text.

    While waiting, the row says "Waiting for consent in your browser until HH:MM:SS UTC." and carries `hx-get` with `hx-trigger="every 2s"` and `hx-swap="outerHTML"`.

    Every failure reads "Consent failed: <reason text>." with `role="alert"`. The `not_internal_domain` text contains the AC sentence verbatim. A revocation note follows:
    - "Google access was revoked.";
    - "Revoking it at Google failed: remove Nextron at myaccount.google.com/permissions.";
    - "It was not revoked, because another grant uses the same Google account."
  - **The client row** has a text input `path` (`autocomplete="off"`) with a "Copy as path" hint. Grant and Remove appear only when the client is imported and the grant isn't waiting.
- **Composition** (`main.py`).
  - `open_in_browser(url)` calls `webbrowser.open` at call time.
  - `build_app` gains `google_oauth=None` and `browser=None`, and builds `GoogleAccess` with:
    - `GoogleOAuthClient()` (lazy);
    - `LoopbackListener`;
    - `LocalClientFiles()`;
    - `config.env`;
    - `functools.partial(read_sink_allowlist, units)`.
  - `open_dashboard`'s `browser` defaults to `None`, meaning `open_in_browser`.
  - Add a docstring paragraph.
- **Keyring per-secret targets** (`nextron/adapters/secrets`, decision 9).
  - **Where a secret lives.** `service` stays `nextron-engine/<env>`, and each secret lives at the service `<service>/<name>`, with `<name>` as the user name.
  - **`write`** sets that entry. It then deletes WinVault's copy of the previous value (`delete_password(f"{name}@{service}/{name}", name)`) and any legacy entry `(service, name)`. A missing entry is ignored.
  - **`read`** tries the new entry first. If there is none, a legacy value is moved to the new entry, the legacy entry is deleted, and the value is returned.
  - **`delete`** removes both layouts.
  - **One lock.** Everything stays under `_BACKEND_LOCK`.
- **Tests.**
  - **The live guard.**
    - `LIVE_CONSTRUCTORS` and `ADAPTER_PACKAGES` gain `google_oauth: Guarded(("default_http_client",))`, and `c5` gains `nextron.adapters.google_oauth`.
    - The guard refuses `webbrowser.open`, `open_new` and `open_new_tab` and never restores them.
  - **Fakes.** `PORT_FAKES` gains `FakeGoogleOAuth`, `FakeConsentListener` and `FakeClientFiles` (in `tests/fakes/google_oauth.py`, with an unsigned-ID-token builder).
  - **No real network call** to Google is made in any test.

**Never:**
- Don't add a migration, a dependency (`httpx2` is pinned) or `include_router`. Don't use google-auth-oauthlib's `run_local_server`.
- Never start a flow, or write a grant, from a GET route. Never serve the redirect from the dashboard.
- Never put a token, the client secret, the code, the verifier, the state, the auth URL or the path in a response, log, exception message, DB or file. The account address appears only in the keyring meta and on the page.
- Never open a browser, call Google or touch the real Credential Manager in a test.
- **Out of scope:**
  - Sending Inbox grants (`gmail/<address>`, Epic 6);
  - doctor checks (1.14);
  - any consumer of `access_token`.
- Don't edit the vendored HTMX or `htmx-config`, or anything outside `nextron-engine/` except this spec, `sprint-status.yaml` and `deferred-work.md`.

### Decisions made overnight (pending user review)

1. **The account comes from the ID token.** The flow asks for `openid email` as well, because `gmail.send` and `postmaster.readonly` can't call `users.getProfile`. The ID token arrives straight from Google's token endpoint over TLS (OIDC Core §3.1.3.7), so its signature isn't checked; `iss`, `aud`, `exp` and `email_verified` are.
2. **A token is revoked only when no other stored grant holds the same account.** Google's revoke ends the client's whole grant for that account, so revoking an old token could kill a new grant on the same account or the other grant. When that would happen, the token is only discarded, and the panel says so.
3. **Removing deletes the grant even when Google can't be reached to revoke it.** The panel says how to revoke it by hand.
4. **`internal_notify` needs an address exactly `@nextronai.com`,** with no subdomains, and a verified email.
5. **An unticked scope is revoked and refused.** Under granular consent, Mac can untick the scope; the grant is then refused.
6. **A wrong or missing `state` ends the flow** (fail closed), and so does the 5-minute timeout. A new Grant click replaces a waiting flow for the same grant.
7. **The import path rules.** The path must be a full drive-letter `.json` path to a regular file: no links, UNC or network paths. Only the client id and secret are stored, and the file is deleted only after both keyring writes.
8. **`prompt=select_account consent` with `access_type=offline`,** so every grant returns a refresh token and Mac picks the account.
9. **Each secret gets its own Credential Manager target,** under the env namespace AD-12 names. This closes the deferred race before this story's background keyring writer (the consent thread). Entries move over lazily, on first read.
10. **Token calls use `httpx2`, not google-auth-oauthlib's `Flow`,** so the adapter controls timeouts, error text and logging.
11. **"Needs re-consent" is kept in the meta entry.** Only `invalid_grant` sets it, and a fresh grant clears it.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Import | `"C:\…\client_secret_1.json"` (quoted), valid installed config | `google/client_config` = `{"client_id":…,"client_secret":…}`, meta time; file deleted; "Imported. The file was deleted." | N/A |
| Import refused | web client / not JSON / bad id / relative / `\\srv\x.json` / `.txt` / missing / folder | "Nothing was imported: <reason>."; nothing stored; file left | 200 |
| Import store fails | keyring write fails | `STORE_FAILED`; file left | 200 |
| Delete fails | stored, delete raises | "Imported, but the file could not be deleted…" | 200 |
| Grant start | client stored; `internal_notify` | listener opened; browser gets the URL (all 9 params; `S256` of the verifier); row waiting and polling; nothing stored | no client: "Import the Google Desktop client first."; no listener or browser |
| Timeout | `wait` → `None` | no exchange; "Consent failed: no answer from Google within 5 minutes." | N/A |
| Bad state | `state` wrong, missing or repeated | no exchange; nothing stored; "Consent failed: …" | N/A |
| Declined | `error=access_denied` | "Consent failed: …" | N/A |
| Granted | `dev`, allowlist has `mac@nextronai.com`, ID token for it | `google/internal_notify` = refresh token; meta `{"account","granted_at"}`; access token in memory only; listener closed; "Granted for mac@nextronai.com, …" | N/A |
| Wrong domain | `internal_notify`, `x@gmail.com` (allowlisted) | revoked; nothing stored; "Consent failed: internal_notify must be a nextronai.com account. Google access was revoked." | N/A |
| Not allowlisted | `dev`/`test`, account not listed; same in `prod` | refused and revoked; `prod`: granted | provider raises: refused |
| Bad tokens | scope missing / no refresh token / ID token wrong `aud` or expired | revoked; refused | N/A |
| Re-grant | old A, new B ≠ A; then new A = old A | B stored, A revoked; A stored, old not revoked ("another grant uses the same Google account") | revoke error: stored; "Revoking it at Google failed…" |
| Remove | confirm=yes | revoked, then entries deleted; "Removed." | revoke fails: still deleted, with note; postmaster on same account: not revoked |
| Refresh | valid cache; expired; `invalid_grant` | cached token, no call; refresh and cache; meta marked, "Needs re-consent…", `google_needs_reconsent`, no further Google calls | stored token changed meanwhile: not marked |
| Keyring | legacy shared-target entries; a write of `a` | read moves each to `nextron-engine/<env>/<name>`; the write touches only `a`'s targets | N/A |

</frozen-after-approval>

## Code Map

- **`nextron-engine/nextron/app/setup/api.py`** -- holds `Credentials`, `CredentialState`, `CredentialStoreFailed`, `_store_failed` and `ENTER_A_VALUE`. Add `GoogleAccess` here; don't create a second app module (deferred: "callers use only api").
- **`nextron-engine/nextron/app/ops/api.py`** -- uses `secrets.token_urlsafe` in the app layer; that precedent covers the state and verifier.
- **`nextron-engine/nextron/domain/credentials.py`** -- `encode_updated_at`, `decode_updated_at` and the registry style. **`nextron/domain/sink.py`** -- `sink_applies`, `allowlisted`, `is_sink_recipient`.
- **`nextron-engine/nextron/ports/secrets.py`** -- `MAX_SECRET_LENGTH`, `SECRET_TOO_LONG`, `secret_length` and `is_secret_name`. `google/client_config` and `meta/google/<grant>` already match the pattern.
- **`nextron-engine/nextron/adapters/secrets/__init__.py`** -- `KeyringSecrets`, `_BACKEND_LOCK` and the race in the docstring. **`keyring/backends/Windows.py`** -- the `set_password` copy and the `delete_password` rule that per-secret targets rely on.
- **`nextron-engine/nextron/main.py`** -- `build_app`, `read_sink_allowlist`, `open_dashboard` (the `browser` parameter) and the docstring.
- **`nextron-engine/nextron/web/app.py`** -- `create_app` (gains `google: GoogleAccess`), `page()` and the credential route patterns (`row_answer`, `store_failed_row`). **`web/credentials_page.py`** -- `Notice`, `Role`, `STORE_FAILED`, `UNREADABLE`, `_single` and the `MalformedForm` rules to reuse.
- **`nextron-engine/nextron/web/templates/credential_row.html`, `credentials.html`, `static/app.css`** -- the row markup and classes to reuse; there is no inline style or script.
- **`nextron-engine/tests/live_guard.py`, `tests/conftest.py` (docstring)** -- the guards. **`tests/fakes/__init__.py`** -- `PORT_FAKES`. **`tests/dashboard.py`** -- `Dashboard` must build `GoogleAccess` with fakes:
  - deferred `start` jobs that a test runs;
  - a recording browser;
  - an allowlist field.
- **`nextron-engine/tests/contracts/test_test_isolation.py`** -- the expected protocol set (line 130) and package set (line 371). **`test_web_contracts.py`** -- the route list (line 143), the route lists in the declare and route-rule tests, and the template list (line 521). **`test_source_tree.py`** -- `FILES` and the adapter folders. **`test_credential_contracts.py`** -- `RESERVED_PREFIXES` gains `google/` and `gmail/`.
- **`nextron-engine/tests/unit/test_secrets.py`, `tests/app/test_environment_isolation.py:300-314`** -- assertions on `(service, name)` that change with per-secret targets. The latter must still prove every call stays in the app's own `nextron-engine/<env>` namespace.
- **`nextron-engine/tests/app/test_credential_secrecy.py`** -- the planted-value pattern for the Google secrecy test.
- **`nextron-engine/pyproject.toml`** -- `c5` sources.
- **`httpx2`** -- `Client(transport=..., timeout=...)` and `MockTransport(handler)` for adapter tests.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/domain/google_oauth.py` -- everything under Domain.
- [x] `nextron-engine/nextron/ports/google_oauth.py` -- `TokenSet`, the three ports and the codes.
- [x] `nextron-engine/nextron/adapters/google_oauth/__init__.py`, `nextron/adapters/__init__.py` docstring, `pyproject.toml` (`c5`) -- the adapter.
- [x] `nextron-engine/nextron/adapters/secrets/__init__.py` -- per-secret targets and the lazy move; rewrite the docstring (the race is closed).
- [x] `nextron-engine/nextron/app/setup/api.py` -- `GoogleAccess` and its types; docstring.
- [x] `nextron-engine/nextron/web/google_page.py`, the templates, `web/app.py`, `static/app.css` -- the routes and rows.
- [x] `nextron-engine/nextron/main.py` -- wiring, `open_in_browser`, docstring.
- [x] `nextron-engine/tests/live_guard.py`, `tests/conftest.py`, `tests/fakes/google_oauth.py`, `tests/fakes/__init__.py`, `tests/dashboard.py` -- guards, fakes and the helper.
- [x] `nextron-engine/tests/unit/test_google_oauth_domain.py` -- every Domain rule, including the RFC 7636 vector and the URL's exact parameters.
- [x] `nextron-engine/tests/unit/test_google_oauth_adapter.py` -- the client and files:
  - `GoogleOAuthClient` on `MockTransport`: the exact form fields, every status mapping, no response text in any error, and a lazy default that raises `LiveAdapterInTests`;
  - `LocalClientFiles` on `tmp_path`.
- [x] `nextron-engine/tests/unit/test_loopback_listener.py` -- `LoopbackListener` on real loopback sockets:
  - the first `/` recorded, other paths 404, a wrong `Host` 400;
  - a timeout gives `None`, and `close` is idempotent and stops the port;
  - nothing goes to stderr or the logs.
- [x] `nextron-engine/tests/unit/test_google_access.py` -- every matrix row on the service, with fakes. This includes the waiting state before the job runs, a superseded flow, the lock order, and the access token never reaching the keyring.
- [x] `nextron-engine/tests/unit/test_google_page.py` -- the four routes through `Dashboard`:
  - the matrix rows as HTML;
  - polling markup only while waiting;
  - CSRF and `MalformedForm` refusals;
  - two-step remove.
- [x] `nextron-engine/tests/contracts/test_google_contracts.py` -- the flow is started only by a POST and never by a GET:
  - every GET route of the app, the row GET included, called with a session, never calls `listen` or the browser;
  - an AST check that only `google_grant_start` calls `start_grant`;
  - the Google keyring names are valid and collide with no credential.
- [x] `nextron-engine/tests/app/test_google_secrecy.py` -- through the dashboard with fakes: import, grant, refresh, re-grant and remove. Planted client secret, refresh token, access token, code and path values reach no response, log record (before redaction) or DB column.
- [x] `nextron-engine/tests/unit/test_secrets.py` -- per-secret targets on a WinVault-like fake backend:
  - the lazy move, delete of both layouts, and dropping the stale copy;
  - writing `a` touches only `a`'s targets.
- [x] Existing tests -- route, template, protocol and package lists; `test_environment_isolation` namespace assertions; the `credentials_page` declaration.
- [x] `nextron-engine/tests/contracts/test_source_tree.py` -- the new files and the adapter folder.
- [x] `nextron-engine/README.md` -- Secrets (per-secret targets and the move), a Credentials > Google subsection (import, grant, allowlist outside `prod`, needs re-consent), and Test (the browser guard and the new fakes).
- [x] `_bmad-output/implementation-artifacts/deferred-work.md` -- remove the `KeyringSecrets` race entry.

**Acceptance Criteria:**
- Given the full suite, when `uv run pytest -q` and `uv run lint-imports` run, then everything passes, every contract is kept, and no test opens a browser or reaches Google.
- Given the app built by `main.build_app` with injected secrets, when it is created, then nothing live is constructed: no HTTP client, no listener, no browser.

## Implementation Notes

- **Additions the spec allows but doesn't name** (all small, all tested):
  - **Superseded flows.** A superseded flow (a newer Grant click, or a removal) stores nothing, records no outcome and revokes nothing: revoking could end the newer flow's grant on the same account, so its token is only discarded.
  - **Unknown accounts.** An unknown account counts as held by no grant, so its token is revoked. That happens with an invalid ID token or a garbled old meta.
  - **Keyring failures in the sharing check.** A Credential Manager failure while checking whether another grant holds the account gives `Revocation.FAILED`.
  - **`store_failed`.** It puts back the old token and meta (best effort), then revokes the new token under the sharing rule.
  - **Re-grant notice.** A successful re-grant's row says "The previous grant was replaced." before the revocation note.
  - **Import notice.** A client import's row adds "Reload this page to grant Google access.", because the swapped-in client row doesn't refresh the grant rows.
  - **Listener hardening.**
    - `LoopbackListener` reads the raw request target, because `http.server` turns `//` into `/`.
    - It sets Windows `SO_EXCLUSIVEADDRUSE`.
    - It uses `ThreadingHTTPServer`, so a browser's idle pre-opened connection can't block the real redirect.
    - Its `handle_error` prints nothing.
  - **File reads.** `LocalClientFiles` also refuses a file swapped between `lstat` and `open`, compared by device and inode.
  - **Port codes.** `ClientNotImported` lives in the domain module. `revoke` takes any 2xx as done, and port errors from `access_token` keep their own retryable flag (`google_unavailable` stays retryable).
- **Existing tests changed:**
  - the route, template, protocol and adapter-package lists;
  - the namespace assertions in `test_environment_isolation` (every backend call stays in the app's own `nextron-engine/<env>` namespace);
  - `RESERVED_PREFIXES` gains `google/` and `gmail/`;
  - two credentials-page tests count only the credential-row forms;
  - `tests/dashboard.py` builds `GoogleAccess` with fakes: deferred jobs that a test runs with `run_jobs()`, a recording browser and an allowlist field.
- **End to end.** One test runs a whole flow on the real `LoopbackListener` and the real daemon-thread `start`: a stand-in browser sends the redirect over loopback, and the flow completes and stores the grant.
- **Skipped.** The symlink case of `LocalClientFiles` skips on this PC, because the account may not create symbolic links; the reparse-point test covers the same branch.
- **Deferred work.**
  - Removed: the `KeyringSecrets` cross-process race entry, closed by the per-secret targets.
  - Added by review pass 1: the AD-12 wording amendment for per-secret targets (BH7).
- **Verification before review.**
  - `uv run pytest -q`: 2581 passed, 2 skipped (`ai_eval`, the symlink test).
  - `uv run lint-imports`: 29 kept, 0 broken.

Review fixes (pass 1, P1–P12; see the Review Triage Log):
- **P1.** `start_grant` keeps its early `_cancel`. At registration, under the state lock, it also takes out any flow a concurrent call registered meanwhile and marks it cancelled; that flow's listener is closed after the lock is released.
- **P2.** The `SHARED` note reads "It was not revoked, because a stored grant uses the same Google account, and revoking it would end that grant too." README step 4 says the old token is revoked unless a stored grant, the new one included, uses the same account. The remove-confirmation question keeps "another grant", because a removal excludes the grant's own token.
- **P3.** README step 5 no longer claims `invalid_grant` covers a changed client. README step 1 and the client-row hint say "Importing a different client ends the existing grants: grant them again."
- **P4.**
  - The adapter refuses an `expires_in` outside 1 to 86,400 (`MAX_EXPIRES_IN`) as `google_bad_response`.
  - `id_token_account` returns `None` for an `exp` too large for a float.
- **P5.** `test_google_secrecy.py` also plants three account addresses and their raw ID tokens. The accounts appear in no log record (checked before redaction), error, DB column or file. The ID tokens appear nowhere, the keyring and responses included.
- **P6.** The Google rows' headings are `<h3>` under the section's `<h2>`; `app.css` styles `.credential h2, .credential h3`.
- **P7.** `credentials_page._single` is now the public `single_value`, used by both pages.
- **P8.** `client_path` refuses a colon after the drive (`PATH_COLON`, "the path may not contain a colon after the drive letter"), so NTFS stream paths such as `C:\x\file.txt:s.json` are refused.
- **P9.** `test_build_apps_own_flow_runs_on_its_thread_and_reads_the_allowlist` runs two flows through `main.build_app`'s own wiring on the real daemon thread and the real database:
  - an allowlisted `internal_notify` account is granted;
  - a non-allowlisted `postmaster` account is refused with the allowlist text, and its token is revoked.
- **P10–P12.** New tests:
  - a refreshed token isn't cached once its refresh token is gone;
  - a first-ever grant whose meta write fails leaves no token (`store_failed`, revoked);
  - a Grant POST with an unreadable keyring answers 200 with `STORE_FAILED` and starts nothing.

**Final verification (after pass 1).**
- `uv run pytest -q`: 2595 passed, 2 skipped (`ai_eval`, the symlink test).
- `uv run lint-imports`: 29 kept, 0 broken.
- `NEXTRON_ENV=dev uv run nextron doctor`: all 5 checks PASS.
- **Not verified here:** no real call to Google was made, and the lazy keyring move hasn't run against the real Credential Manager. Both need Mac's first real import and grant.

## Spec Change Log

## Review Triage Log

**Pass 1 (2026-11-17).**
- **Sources:** BH = blind hunter (14 findings), EC = edge-case hunter (14), VG = verification gap (4 gaps and 1 other finding).
- **Counts:** 33 raw findings, 27 after deduplication.
- **Outcomes:**
  - 12 groups patched (P1–P12): 8 code or doc fixes and 4 test gaps;
  - 1 deferred: BH7;
  - 14 rejected.
- **No loopback.** No finding is an intent gap or a bad spec. Three findings (BH6, BH9 with EC8, EC9) would change the frozen spec and are rejected for that reason; Mac can revisit them when he reviews the overnight decisions.

| # | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| A | BH2, EC3, VG5 | `start_grant` calls `_cancel`, then registers the new flow unconditionally. A concurrent `start_grant` for the same grant that registers in between is overwritten without being cancelled: its listener stays bound for up to 5 minutes, and a consent finished there is silently dropped. | low | Verified at `api.py` (`_cancel`, then `self._flows[spec.grant] = flow`). HTMX queues a second click on the same element (its default strategy is `last`), so a double-click is sequential. The race needs two tabs posting at once. The fix is a direct correction: the registration also takes out and closes any flow it finds. | P1 |
| B | BH3 | After a same-account re-grant, the row says "It was not revoked, because another grant uses the same Google account", but the holder is the grant's own new token. README step 4 says granting again "revokes the old one". | low | Verified: `_store` calls `_revoke(..., exclude=None)`, which finds the grant's own new meta and returns `SHARED`. Not revoking is decision 2; only the wording is wrong. The fix is a direct text correction in the note and the README. | P2 |
| C | BH4 | README step 5 says `invalid_grant` covers "the client changed", and nothing warns that importing a different client strands the existing grants. | low | A refresh with another client's credentials may come back as `unauthorized_client` or `invalid_client` (`google_rejected`, not marked), so the claim is unverified. Re-importing a different client is rare. The fix is a direct doc and hint correction; marking the grants on a client change would be new behaviour. | P3 |
| D | EC2, EC6, EC7 | Numbers from Google aren't bounded. A huge `expires_in` makes `timedelta(seconds=...)` raise `OverflowError` after the grant was stored (the outcome reads `crashed`, and `access_token` raises a non-`AdapterError`). A huge integer `exp` makes `math.isfinite` raise inside `id_token_account`. Either way the new token isn't revoked (EC2). | low | Verified: `timedelta(seconds=10**20)` and `math.isfinite(10**400)` both raise `OverflowError`. Google never sends such values, but each fix is a direct correction: one bound in an existing check, and `None` instead of a raise. With both fixed, EC2 has no known trigger. | P4 |
| E | BH8 | The secrecy test plants neither the account address nor the raw ID token, so either leaking into a log, error or DB column would pass. | low | Verified: `PLANTED` in `test_google_secrecy.py` lists only the secrets, the code and the path. The fix is test-only. | P5 |
| F | BH13 | The Google rows use `<h2>`, the same level as the section heading `Google`, so heading navigation loses the grouping. | low | Verified in `credentials.html`, `google_client_row.html` and `google_grant_row.html`. The fix is a direct correction: `<h3>`, plus a CSS selector. | P6 |
| G | BH14 | `google_page.py` imports the private `_single` from `credentials_page`; `_grant_value` and `is_grant` rebuild the same set on each call. | low | The private import couples two modules through a name their owner may rename. The fix is a direct rename that makes it public. The repeated set does no named harm, so that half is dropped. | P7 |
| H | EC11 | `client_path` accepts an NTFS alternate-data-stream path such as `C:\x\file.txt:s.json`: the stream is read, then deleted, though decision 7 says the path must be a regular file. | low | Verified: `PureWindowsPath(r"C:\x\file.txt:s.json")` is absolute on `C:` with a name ending in `.json`. A colon is never valid in a Windows file name, so refusing it is a direct correction that makes the documented path rule hold. | P8 |
| I | VG1 (gap) | No consent flow runs through `main.build_app`'s own `GoogleAccess`, so its `env` and `read_sink_allowlist(units)` wiring, read from the flow's daemon thread, is unverified. | medium | Pre-verified by VG: every flow test injects its own allowlist. A broken provider would make every dev or test grant fail as "not allowlisted", and a wrong env would skip the AD-14 check, with no test failing. The fix is test-only. | P9 |
| J | VG2 (gap) | The success path of "cache a refreshed token only while its refresh token is still stored" is untested. | low | Pre-verified: only the `invalid_grant` path swaps the token mid-refresh. Without the check, a removed grant's access token could keep being used until it expires. The fix is test-only. | P10 |
| K | VG3 (gap) | A first-ever grant whose meta write fails is untested, so `_restore`'s delete branch is unpinned. | low | Pre-verified: the only store-failure test seeds an earlier grant. Without the branch, a revoked token would stay stored and show as granted. The fix is test-only. | P11 |
| L | VG4 (gap) | The Grant route's `CredentialStoreFailed` answer is untested. | low | Pre-verified: dropping the `except` clause would turn an unreadable keyring into a 500, and no test would fail. The fix is test-only. | P12 |
| M | BH7 | The per-secret keyring targets (`nextron-engine/<env>/<name>`) change what AD-12's "keyring service `nextron-engine/<env>`" literally says. The spine isn't amended and no `.memlog.md` decision exists. | low | True. The decision is recorded as overnight decision 9, pending Mac's review. Code reaches the keyring only through `SecretsPort`, and the README and adapter docstrings describe the new layout. Amending the binding spine needs Mac, so it is recorded in `deferred-work.md`. | defer |
| — | BH1, EC1 | A flow superseded after its code exchange drops a refresh token that is neither stored nor revoked; `_Flow.cancelled` is never read. | low | Verified only for the narrow case. BH1's scenario ("Grant twice, then finish consent in the first tab") doesn't occur: the first listener is closed by then, so the redirect is refused. The real window is a supersede during the sub-second exchange. Revoking there could end the newer flow's grant (decision 2). The fix adds a guard. | reject |
| — | BH5 | `internal_notify` ignores the ID token's `hd` claim, so a personal Google account on an `@nextronai.com` address would pass. | low | The project's consent screen is Internal, so only Workspace accounts can consent (architecture memlog). A consumer account can't be newly created on a Workspace-verified domain, and it would have no Gmail mailbox to send from. The fix adds a claim and a check; decision 4 defines the rule. | reject |
| — | BH6 | A waiting flow can't be cancelled or restarted from the page: Grant and Remove are hidden for up to 5 minutes. | low | True as specified: the frozen Boundaries say Grant and Remove appear only while no flow waits. The fix would edit the spec. | reject |
| — | BH9, EC8 | Any local GET to `/` with the right `Host` consumes the one-shot listener, so the flow ends as `state_mismatch`. | low | Real, but it needs a local process or a page that has found the ephemeral port within the 5 minutes, and the result fails closed (nothing is stored). Decision 6 and the frozen port contract ("the query of the first request to `/`") define this behaviour. The fix would edit the spec. | reject |
| — | BH10 | The waiting row keeps polling after a 401 (session gone, or the engine restarted) and shows a stale "waiting". | low | HTMX doesn't swap non-2xx answers, so the stale row stays until a reload. It is harmless (one GET every 2 s), and stopping it needs a 286 answer from the guard or a new trigger rule. | reject |
| — | BH11 | Partial keyring writes are reported as plain failures. A remove whose delete fails after the revoke still shows "Granted"; an import whose meta write fails leaves the new client stored with the old time shown. | low | It needs Credential Manager to fail between two writes. The row says what happened ("The row shows what is stored now"), and a retry finishes the job. `Credentials.save` has the same secret-then-meta order. The fix adds branches. | reject |
| — | BH12 | The legacy-keyring fallback never ends (an extra lookup on every read of a missing secret), and the lazy move is a read-then-write across processes. | low | The extra lookup costs a few CredRead calls. The cross-process window is a one-time move of one name during a concurrent `nextron open`, and its worst case is a one-time code that has to be issued again. The fix adds a marker or a conditional write. | reject |
| — | EC4 | Re-importing the client while a flow waits lets that flow store a token from the old client. | low | It needs Mac to import a different client during his own 5-minute consent wait. The fix adds a cancel on import. | reject |
| — | EC5 | A rotated `refresh_token` in a refresh answer is discarded. | low | Google doesn't rotate installed-app refresh tokens, and the worst case is "needs re-consent". The fix adds a branch and a keyring write. | reject |
| — | EC9 | A grant whose client entry is missing (deleted by hand) can't be removed from the page. | low | The client can only be deleted in Credential Manager itself. The frozen Boundaries tie Remove to an imported client, so the fix would edit the spec. | reject |
| — | EC10 | A read may return WinVault's stale `<name>@<target>` copy when the target is missing. | low | It needs two failures in a row: a crash between `set` and the copy drop, then a delete interrupted between its two targets. The legacy layout had the same fallback. A fix would bypass keyring's public resolution. | reject |
| — | EC12 | The file could be replaced between `read` and `delete`, so a different file is deleted. | low | It needs another process to swap the file within milliseconds on Mac's own PC. The fix adds a second identity check. | reject |
| — | EC13 | Re-granting or removing doesn't always revoke the old token, against the Intent's "revokes the old token". | false | Frozen decision 2 settles it: a token is revoked only when no stored grant holds the same account. | reject |
| — | EC14 | The row keeps showing "waiting" past its displayed deadline while the exchange and revoke run. | low | It applies only when the redirect arrives near the deadline, and adds at most the calls' 15 s timeouts. A timeout itself ends at once. Cosmetic; the fix adds a state. | reject |

## Design Notes

```python
# consent thread (start_grant → start(run))
params = flow.listener.wait(CONSENT_TIMEOUT.total_seconds()); flow.listener.close()
outcome = self._complete(flow, params)   # never raises; stores only after every check
```

**Choices the user won't notice:**
- **The redirect is served by a separate one-shot listener, never by a dashboard route.** That settles AD-13's no-side-effecting-GET rule (reconcile L4).
- **The keyring move is lazy** (on read), so startup never touches Credential Manager. Test subprocesses stay keyring-free (deferred item).

## Verification

**Commands:**
- `cd nextron-engine; uv run pytest -q` -- expected: all pass, with `ai_eval` skipped.
- `uv run lint-imports` -- expected: every contract (29) KEPT.
