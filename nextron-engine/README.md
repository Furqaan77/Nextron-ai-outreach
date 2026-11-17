# Nextron engine

The Nextron Outreach Engine: a single-user engine that runs on one Windows 11 PC and serves a
dashboard on `127.0.0.1` only. The architecture lives in
`_bmad-output/planning-artifacts/architecture/architecture-nextron-outreach-engine-2026-11-14/ARCHITECTURE-SPINE.md`.

## Install

Needs [uv](https://docs.astral.sh/uv/) 0.13. uv installs Python 3.14 (3.14.8 or later, below
3.15) on its own.

```powershell
cd nextron-engine
uv sync --frozen
```

`--frozen` installs exactly what `uv.lock` records. Every runtime package is pinned in
`pyproject.toml`; change a pin there, run `uv lock`, and commit both files.

## Choose an environment

`NEXTRON_ENV` is required and has no default. Each environment has its own data dir, database,
port, lock and Credential Manager namespace (`nextron-engine/<env>`), so `dev`, `test` and `prod`
can run side by side without touching each other's files or credentials. Outside `prod`, email
and alerts reach only an allowlist (see [Dev and test sink](#dev-and-test-sink)).

**Only the logon task sets `prod`.** Never set `NEXTRON_ENV=prod` as a persistent user or
machine environment variable (System Properties > Environment Variables, or `setx`): every
process started on the PC, a test run included, would then run as `prod` and could email real
people. Set the variable in each terminal instead, as below; it lasts only for that terminal.
`nextron doctor` fails when it finds a persistent `prod`. Setting `NEXTRON_ENV=prod` in a
PowerShell `$PROFILE`, a terminal profile or an editor's terminal settings is just as dangerous,
and `doctor` can't see it: never put `prod` there.

| Variable | Default |
|---|---|
| `NEXTRON_ENV` | none: `dev`, `test` or `prod` |
| `NEXTRON_DATA_DIR` | `%LOCALAPPDATA%\Nextron\<env>` (never under OneDrive) |
| `NEXTRON_BACKUP_DIR` | `%LOCALAPPDATA%\Nextron\backups\<env>` |
| `NEXTRON_PORT` | 8765 `prod`, 8766 `dev`, 8767 `test` |
| `NEXTRON_PLAYBOOK_PATH` | `../docs/playbook/sales-playbook.md` |

Relative paths resolve against this folder. Every other setting lives in the database.

```powershell
$env:NEXTRON_ENV = "dev"
```

## Check the machine

```powershell
uv run nextron doctor
```

Prints each startup check as PASS or FAIL: the SQLite version (3.51.3 or later), timezone data
(`Europe/London`), a data dir outside OneDrive, no persistent `NEXTRON_ENV=prod`, and a free
port. Exits 0 when nothing fails, 1 when a check fails, and 2 when `NEXTRON_ENV` (or another
variable above) is unset or invalid. It takes no lock, creates nothing and starts no server.

`prod_env_not_persistent` reads `NEXTRON_ENV` from the persistent user environment
(`HKCU\Environment`) and machine environment
(`HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment`). It fails with
`NEXTRON_ENV=prod must only be set by the logon task`, naming the scope, when either holds
`prod` (in any case, spaces ignored), and also when the registry can't be read. Delete the
variable in System Properties > Environment Variables, then open a new terminal. Only `doctor`
runs this check: the logon task's own `prod` start inherits any persistent value, so refusing
one at `serve` would also stop the real engine, not just the stray processes it endangers.

## Run the engine

```powershell
uv run nextron serve            # or: uv run python -m nextron serve
uv run nextron serve --reload   # dev only: restart when the code changes
uv run nextron open             # in a second terminal: open the dashboard (see below)
```

`serve` validates `NEXTRON_ENV`, runs the startup checks, creates the data dir and its `logs`
folder, takes an exclusive lock on `<data dir>\nextron.lock`, checks the port, opens the
database (see [Database](#database)), then runs Uvicorn on `127.0.0.1` with one worker.
`GET /health` returns `{"env", "version", "uptime_s"}` and nothing else, but only to a browser
with a dashboard session: like every other route, it answers 401 without one.

```powershell
curl.exe -i http://127.0.0.1:8766/health                           # 401 and the locked page
curl.exe -i -H "Host: evil.example" http://127.0.0.1:8766/health   # 400
curl.exe -i -X POST -H "Origin: http://evil.example" http://127.0.0.1:8766/   # 401
```

(`curl.exe`: in PowerShell 5.1, `curl` is `Invoke-WebRequest`.) With a dashboard session cookie,
that cross-origin POST gets 403 `Request refused: security check failed` instead.

| Exit code | Meaning |
|---|---|
| 0 | stopped normally |
| 1 | unexpected error (a Python traceback) |
| 2 | `NEXTRON_ENV` unset or invalid, another invalid setting, a failed startup check (the message names it, `database_revision` included), a data dir that can't be created, a failed database migration, or a server that failed to start |
| 3 | another instance is running for this environment |

The supervisor (`scripts/run.ps1`, a later story) restarts the engine on any exit except 3.
It watches the heartbeat file, not `/health`.

## Open the dashboard

```powershell
$env:NEXTRON_ENV = "dev"
uv run nextron open
```

With the engine running, `nextron open` writes a one-time code to Windows Credential Manager
and opens the default browser at `http://127.0.0.1:<port>/enter?t=<code>`. The link works once,
within 5 minutes: the dashboard deletes the code, starts a session, sets the `nextron_session`
cookie (`HttpOnly; Path=/; SameSite=Strict`) and shows the dashboard home. Run `nextron open`
again for a new link. The code is never printed or logged.

| Exit code | Meaning |
|---|---|
| 0 | the browser was opened |
| 1 | `engine not running` (nothing was written), the code couldn't be stored in Credential Manager, or the browser couldn't be opened (the code is then withdrawn) |
| 2 | `NEXTRON_ENV` unset or invalid, or another invalid setting |

Sessions live in the engine's memory, so they end when the engine restarts (or when the browser
drops its cookie); there is no idle timeout. A request without a session, `GET` included, gets
401 with a page saying to run `nextron open`; so does a reused, expired or wrong code. A wrong
code leaves the pending one in place, so a junk `/enter` from another website can't cancel your
link. `/enter` also accepts only a navigation the browser started itself (no `Sec-Fetch-Site`
header, or `none`, which is what a browser sends when `nextron open` launches it). A link from
another website (`cross-site`), or from a server on another `127.0.0.1` port (`same-site`), gets
the locked page, is logged as `dashboard_entry_refused` with `reason=cross_site`, and leaves the
pending code untouched. Browsers don't scope cookies by port, so the browser sends the
`nextron_session` cookie to every web server on `127.0.0.1` it visits, not only to the engine,
and the environments share that one cookie: opening one environment's dashboard ends the
browser's session with the other. Run `nextron open` again to switch back.

After a second `nextron open`, a dashboard tab opened earlier still carries the old session's
CSRF token while the browser now sends the new session's cookie, so the old tab's actions are
refused (silently for now, until Story 1.12's notification). Reload the tab.

These protections keep out other websites and other Windows users' processes, not your own:
any process running as your Windows user can read Credential Manager, and with it the pending
code.

**What the dashboard enforces (AD-13):**

- It answers only `Host: 127.0.0.1:<port>` or `localhost:<port>`; anything else gets 400 before
  any handler runs, which stops DNS rebinding.
- Every response carries the strict `Content-Security-Policy`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer` and `X-Content-Type-Options: nosniff`, and no CORS header.
- Every route declares whether it shows personal data, with `@personal_data(True|False)` under
  the route decorator. Unless it declared `False`, its responses get `Cache-Control: no-store`,
  and `create_app` refuses to start with an undeclared route.
- Handlers are sync `def`. Pages load only the vendored HTMX and `app.css`, with no inline
  script or style. HTMX is configured through `<meta name="htmx-config">` (`allowEval:false`,
  `allowScriptTags:false`, `selfRequestsOnly:true`, `historyCacheSize:0`,
  `includeIndicatorStyles:false`).
- The navigation shows each section (Queue, Signal Inbox, Leads/Pipeline, Funnel, Settings,
  Health) once its page route exists.
- **Every state change is a checked POST.** A page on another port of `127.0.0.1` is same-site,
  so the browser sends it the `SameSite=Strict` cookie; these checks are what stop it posting to
  the dashboard:
  - Every session has its own CSRF token (256 bits, kept for the session's life). Pages carry it
    in `<body hx-headers='{"X-CSRF-Token": ...}'>`, so every HTMX request sends it, and every
    `<form>` carries it as the hidden `csrf_token` field (`{{ csrf_field() }}`). Pages that
    carry it are never cached (`page()` answers with `Cache-Control: no-store`).
  - **Every form posts through HTMX** (`<form method="post" hx-post="...">`). Under the
    dashboard's `Referrer-Policy: no-referrer`, a browser's native form submission sends
    `Origin: null`, which the `Origin` check refuses; HTMX's request keeps the real `Origin`. If
    the HTMX script ever fails to load, the native fallback is refused with 403: it fails
    closed.
  - For any method other than GET and HEAD, before routing, the request needs exactly one
    `Origin` equal to `http://` plus its `Host` (so `localhost:<port>` posts with the
    `localhost` origin), and either no `Sec-Fetch-Site` or exactly one `same-origin`. **Every
    POST must carry `Origin`**: all current browsers send it, so a POST without one is refused.
  - Every POST route runs `require_csrf` first: the request must present the session's token in
    an `X-CSRF-Token` header or a `csrf_token` field of an `application/x-www-form-urlencoded`
    body, and every token it presents must match (compared in constant time). **A plain
    multipart form can't carry the token, so it is refused** until a story needs one.
  - A refused request gets **403 `Request refused: security check failed` as plain text**, and
    the handler never runs. HTMX doesn't swap error responses, so an HTMX control shows nothing
    for now; the error notification arrives in Story 1.12. It is logged as `request_refused`
    with `check` = `origin`, `fetch_site` or `csrf_token`, the `method` and the `path`, never
    with the query string, a header value or a token.
- **No GET writes to the database.** Every GET handler runs inside a read-only unit of work
  (see [Database](#database)); any database write in it raises, and the request gets 500.
  `GET /enter` is the one GET that changes state, by design: it consumes the one-time code and
  creates a session (neither in the database). A route is exactly a GET or exactly a POST:
  `create_app` sets a route class that refuses anything else (`PUT`, `HEAD`, GET and POST
  together, an `async def` or generator GET, a callable object) when the route is added, and
  refuses to start when a route breaks these rules (a plain `APIRoute`, a POST whose first
  dependency isn't `require_csrf`, an included router). Write a POST handler like this:

  ```python
  @app.post("/settings", name="settings_save")     # gets Depends(require_csrf) from the route class
  @personal_data(True)
  def settings_save(
      request: Request, fields: Annotated[FormFields, Depends(form_fields)]
  ) -> HTMLResponse:
      texts = submitted_texts(fields)              # MalformedForm (400) for any other fields
      with units.open():                           # its own write unit of work (AD-3)
          stored = setup.save(texts)
  ```

  A handler reads its form through the `form_fields` dependency (`nextron/web/forms.py`), never
  FastAPI's `Form()`, which needs `python-multipart`. It needs exactly one `Content-Type`
  (`application/x-www-form-urlencoded`) and an ASCII body that `parse_qs` reads strictly
  (percent-escapes as strict UTF-8, at most 1,000 fields). Anything else, or fields other than
  the form's own, gets **400 `Request refused: malformed form`** as plain text, logged as
  `request_refused` with `check=form`.

  A GET handler renders its page inside its body (`page(request, "x.html")`): the read-only
  unit of work closes before FastAPI serialises what the handler returns.
- **Untrusted text renders inert.** Crawled pages, imported rows, Replies and AI output may hold
  `<script>`, `onerror=`, `hx-post=` or `javascript:` text. Besides autoescaping, templates use:
  - `{{ value|plain }}`: the escaped text of any value, even `Markup` (`None` renders empty);
  - `{{ safe_link(url, text) }}`: a link only for an `http`/`https` URL with a hostname, no
    user name or password (`https://nextronai.com@evil.example/` would look trusted), no
    control character, space or backslash, no Unicode control, format (bidi override,
    zero-width), space or line/paragraph separator character, and a port that parses; it
    renders `<a href="..." rel="noopener noreferrer">`. Anything else (`javascript:`, `data:`,
    `//host`, `http:\\host`) renders as plain text;
  - `untrusted_block(text)` from `macros.html`: a `<div class="untrusted" hx-disable>` around
    the escaped text (or around a `{% call %}` body), so HTMX ignores any `hx-*` attribute
    inside it; `app.css` keeps its line breaks and wraps long strings.
- **The safe allowlist.** The `safe` filter (`|safe`, even split across lines, or named as
  `'safe'` as in `map('safe')`), a `{% filter safe %}` block, any `{% autoescape %}` whose
  argument isn't literally `true`, `Markup` (under any name) and `__html__` are allowed in
  `nextron/web` only on lines listed in `tests/contracts/safe_allowlist.txt`
  (`<posix path>: <stripped source line>`, one entry per line, `#` comments). Today it lists
  only the three lines of `nextron/web/templating.py` that build the link and the hidden field.
  An unlisted use, or a listed line that no longer exists, fails the contract tests.

`tests/contracts/test_web_contracts.py` checks the handler, declaration, route, inline-code,
autoescaping, form and safe-allowlist rules. No form uses GET yet: every `<form>` must have
`method="post"`, `hx-post` and `{{ csrf_field() }}`, and a future GET filter form must amend that
contract.

## Settings

Every operator setting lives in the database and is declared once, in
`nextron/domain/settings_schema.py` (AD-12); the bootstrap variables above are the only
configuration outside it. Secrets never go there: they live in Credential Manager (see
[Secrets](#secrets)).

**Declare a key.** Add a member to `Key` and a `Setting` to `SCHEMA`, in the same change:

```python
class Key(StrEnum):
    SENDER_TIMEZONE = "sender_timezone"
    DAILY_CAP = "daily_cap"                     # new

SCHEMA = _declare(
    ...,
    Setting(key=Key.DAILY_CAP, type=SettingType.INTEGER, section=Section.SENDING,
            label="Daily send cap", unit="emails per inbox per day", default=30,
            rules=(minimum(1, "1"), maximum(50, "50"))),
)
```

- `type` is one of `string`, `integer`, `boolean`, `time` (`HH:MM`), `timezone` (an IANA name,
  exactly as listed), `duration` (ISO 8601, such as `PT30M` or `P2W`), `money` (US dollars,
  held as integer micro-dollars), `list` (one item per line) and `availability` (one
  `Mon 09:00-17:00` slot per line). The module docstring gives each one's exact text form.
- `section` is one of the six Settings page sections (UX-DR23): Footer and booking, Sender,
  Alerts and recipients, Sending, Budget and limits, Engine. Pick one; don't add another.
- `default` is a typed value (`30`, `time(7, 0)`, `timedelta(days=2)`, `20_000_000` for
  $20.00, a tuple for a list) or `REQUIRED`.
- A `time` or `availability` setting names, in `timezone_key`, the `timezone` setting its times
  are in.
- `rules` adds checks on the typed value: `minimum(bound, shown)`, `maximum(bound, shown)`,
  `max_length(n)`, or any `Rule(name, message, check)`.
- The key is lowercase snake_case of at most 31 characters.

The module checks the whole schema when it is imported and raises `TypeError` on a bad
declaration: a malformed key, a key missing from `Key` (or the other way round), an empty label
or unit, a default that is invalid or not in canonical form, or a missing `timezone_key`. The
contract tests (`tests/contracts/test_settings_contracts.py`) also refuse a key that could switch
off Suppression, Eligibility, the footer, opt-out handling or Market rules (a boolean, or a key
name or label such as `skip_eligibility` or "Disable footer"), and a key name or label that
names a secret (`token`, `password`, `api_key`, `private`, or the word `key` or `pass`, and so
on).

**Read a value** through `queries.setup.get` inside the entry point's unit of work:

```python
from nextron.app.queries import setup as setup_queries

zone = ZoneInfo(setup_queries.get(Key.SENDER_TIMEZONE))   # SettingNotSet until Mac sets it
```

`get` returns the typed value: the stored one, else the default. A required setting that nobody
has set raises `SettingNotSet`, and a stored text that no longer parses (say, after a rule was
tightened) raises `InvalidSettingValue`.

**Write a value** with `Setup.set(key, value)` (`nextron/app/setup/api.py`), a use case, or on
the Settings page. The `settings` table (owner `setup`) holds one row per stored key: the
canonical text and `updated_at`. Each change logs `settings_changed` with the changed key names,
never a value.

**The Settings page** (`/settings`, in the navigation) is generated from the schema: one
fieldset per section, each field with its unit and either "required" or its default as a hint,
showing the stored value, else the default, else blank. Saving checks every field on the server
and stores all or nothing: an invalid field shows its reason beside it, marked
`aria-invalid="true"`, and the page says nothing was saved. A stored value that no longer passes
its rules (one tightened since it was saved) is marked the same way when the page opens.

**How blank fields, missing values and formats work:**

1. **Blank fields.** A blank field with a default resets the setting to its default (its row is
   deleted). A blank required field that was never set stays unset and isn't an error, so you
   can save other settings before you have every value (the footer postal address is still
   pending). A required value that has been set can't be cleared.
2. **An unset required setting fails closed.** `get` raises `SettingNotSet` instead of
   returning `None`, so nothing can run on a missing value by accident.
3. **Durations have no years or months,** because those have no fixed length. A setting counted
   in months (retention, say) is an `integer` with the unit "months".
4. **Input formats.** Money is entered and shown in US dollars (up to 6 decimals) and held as
   integer micro-dollars. Lists take one item per line. Weekly availability takes one
   `Mon 09:00-17:00` slot per line, in the timezone its `timezone_key` names.
5. **Unchanged defaults stay unset.** The page shows an unset setting's default as its value;
   saving it back unchanged stores nothing, so a default changed in a later version still
   applies.
6. **A timezone is a region name,** such as `America/Los_Angeles` (or `UTC`). Fixed-offset and
   legacy names such as `EST`, `EST5EDT`, `Zulu` or `Etc/GMT+5` are refused, because they don't
   follow daylight saving. Region links such as `US/Pacific` work.
7. **Setting names have at most 31 characters,** because log redaction masks any longer run as
   `<key>`.

## Secrets

Secrets live only in Windows Credential Manager, through `SecretsPort`
(`nextron/adapters/secrets`), under the service `nextron-engine/<env>`, so `dev` never reads a
`prod` entry. A value holds at most 1,280 characters; a longer one is refused with "secret
exceeds 1,280 characters". The adapter always uses Credential Manager (`WinVaultKeyring`),
whatever `PYTHON_KEYRING_BACKEND` or a keyringrc file says, and keeps entries on this machine
(they don't roam with a roaming profile).

**Each secret has its own Credential Manager target**, `nextron-engine/<env>/<name>`, with the
secret's name as the user name: for example `nextron-engine/dev/session/open_code` or
`nextron-engine/prod/google/postmaster`. To inspect or remove an entry by hand, open Credential
Manager > Windows Credentials. Keyring moves a target's previous value to
`<name>@<target>` when it overwrites it; the adapter deletes that copy right after each write.
Because no two secrets ever share a target, two writers of different secrets (the engine and
`nextron open`, or the engine's own background threads) can't lose or delete each other's
entry. Within the engine every Credential Manager call still runs one at a time under one lock.

**Entries from before Story 1.9 move on their own.** Earlier versions kept every secret under
one shared target, `nextron-engine/<env>`, or `<name>@nextron-engine/<env>`. The first time the
engine reads a secret that isn't at its own target yet, it reads the old entry, writes it to the
new target, deletes the old one and goes on; each secret moves once, when it is first needed,
so starting the engine touches nothing. Deleting a secret removes it from both layouts. Tests
use the in-memory fake `tests/fakes/secrets.py` and never touch Credential Manager.

### Credentials

Every external API key, token and passphrase is declared once, in
`nextron/domain/credentials.py` (AD-12), with the keyring name that holds it:

| Key | Label | Keyring name | |
|---|---|---|---|
| `claude` | Claude API key | `anthropic/api_key` | required |
| `google_places` | Google Places API key | `google_places/api_key` | optional |
| `hunter` | Hunter API key | `hunter/api_key` | required |
| `prospeo` | Prospeo API key | `prospeo/api_key` | optional |
| `companies_house` | Companies House API key | `companies_house/api_key` | required |
| `telegram_bot` | Telegram bot token | `telegram/bot_token` | required |
| `unsubscribe_bearer` | Unsubscribe Endpoint bearer secret | `unsub/bearer` | required |
| `backup_passphrase` | Backup passphrase | `backup/passphrase` | optional |
| `job_aggregator` | Job aggregator API key | `job_aggregator/api_key` | optional |

Each credential has a companion entry, `meta/<keyring name>` (for example
`meta/hunter/api_key`), that holds only the value's last-updated time
(`YYYY-MM-DDTHH:MM:SS.ffffffZ`), never the value. `tests/contracts/test_credential_contracts.py`
checks that every name is a valid secret name, that no two collide, and that none starts with
`meta/` or `session/`.

**Enter them on the Credentials page** (`/credentials`, linked from the Settings page). Each
credential has its own row: its label, "required" or "optional", and its state: `Set, last
updated YYYY-MM-DD HH:MM UTC`, `Set, last update time unknown`, `Not set`, or `Unknown: Windows
Credential Manager could not be read`. The page names the environment whose Credential Manager
entries it writes (`dev`, `test` or `prod`). Type or paste a value into the row's password field
and press Save; the field clears and the row says "Saved." with the new time. The field is
marked `autocomplete="new-password"` so the browser doesn't fill in a saved password; if the
browser offers to save or fill a password on this page anyway, decline: credentials belong only
in Windows Credential Manager. A stored value is
never shown, and no response, log record, error message, database row, file or job payload ever
holds one. Each row saves or removes only its own credential, through a CSRF-checked POST
(`POST /credentials/save`, `POST /credentials/remove`); a malformed form, such as an undeclared
credential or a repeated field, gets 400 `Request refused: malformed form` and changes nothing.
Every save, removal and failure is logged by key (`credential_saved`, `credential_removed`,
`credential_store_failed` with the `operation` and the error `code`, and
`credential_read_failed` with the `entry`, `value` or `meta`, and the `code`), never with the
value.

**How the page behaves:**

1. **The panel is its own page,** `/credentials`, linked from the Settings page. The top
   navigation keeps its six sections.
2. **The keyring names are the ones in the table above.** `backup/passphrase` is the name the
   encrypted off-PC backup (Story 1.17) reads. The backup passphrase is optional here; the
   doctor check that Story 1.17 adds covers it.
3. **"Set" is read from the credential's own entry; only the time comes from `meta/<name>`.** A
   row can never say "set" for a credential that is gone. A missing, garbled or unreadable meta
   entry shows "last update time unknown". If Credential Manager can't be read at all, the row
   says "Unknown".
4. **Removing asks first.** The first Remove click only asks "Remove the <label> from Windows
   Credential Manager?"; nothing is deleted until you press "Yes, remove it" (a second POST with
   `confirm=yes`). "Cancel" reloads the page. Removing deletes the entry and its `meta/` entry.
5. **Spaces at either end of a value are dropped before it is stored.** A blank value is refused
   with "enter a value" and never deletes anything; removing is a separate action. A value over
   1,280 characters (UTF-16 units: a character outside the Basic Multilingual Plane, such as an
   emoji, counts 2) is refused with "secret exceeds 1,280 characters". Either way nothing is
   written. The field has no `maxlength`, so a pasted value is never cut short without a word.
6. **Times are shown in UTC,** because the page can't assume the sender's timezone is set.

If Credential Manager fails while saving or removing, the row says "Windows Credential Manager
failed. The row shows what is stored now." and shows what it can read. The value is written
before its time, so a failed time write can only understate when it was saved.

**Read a credential in an adapter** with `read_credential` (`nextron/ports/secrets.py`), at call
time, and never keep it:

```python
from nextron.domain.credentials import CredentialKey
from nextron.ports.secrets import read_credential

api_key = read_credential(self._secrets, CredentialKey.HUNTER)   # in the call, not __init__
```

It returns the value, or raises `AdapterError(False, "credential_not_set", "credential hunter is
not set")` when there is none. `tests/app/test_credential_secrecy.py` saves a planted value on
the page, has a fake adapter send it as a bearer header to a failing transport, sends refused
requests that carry it, removes it, and then finds it in no HTTP response, log record (checked
before redaction), database column or file.

#### Google

Below the credential rows, the **Google** section holds the Google Desktop client and two
grants: `internal_notify` (scope `gmail.send`, for internal notification email) and
`postmaster` (scope `postmaster.readonly`, for Postmaster Tools spam rates). Refresh tokens live
in Credential Manager; access tokens stay in the engine's memory and are never written anywhere.

1. **Import the Desktop client once.** In Google Cloud Console create an OAuth client of type
   "Desktop app" and download its JSON. In File Explorer select the file, press Ctrl+Shift+C
   ("Copy as path"), paste the path into the client row and press Import. The path must be a
   full path on a drive of this PC (`C:\...`, not a network share), to a regular file (not a
   shortcut or link) ending in `.json`, of at most 64 KiB. Only the client ID and secret are
   stored, as `google/client_config`, with the import time in `meta/google/client_config`; the
   file is then deleted. If it can't be deleted the row says so: delete it yourself. A file that
   isn't a Desktop app client (a Web application client, say) is refused, left where it is and
   not stored. Importing a different client ends the existing grants: grant them again.
2. **Grant.** Press Grant on a row. The engine opens a one-time listener on `127.0.0.1` on a
   free port and Google's consent page in your default browser (PKCE S256, a random `state`,
   `prompt=select_account consent`). Choose the account and allow access within 5 minutes; the
   row says "Waiting for consent in your browser until HH:MM:SS UTC." and refreshes itself every
   2 seconds. Google sends the browser back to the listener, never to the dashboard; the tab then
   says you can close it. Only that POST starts a flow; no GET ever does. The refresh token is
   stored as `google/<grant>` and the account and time as `meta/google/<grant>`; the row says
   "Granted for <account>, YYYY-MM-DD HH:MM UTC".
3. **What is refused.** A wrong or missing `state`, the 5-minute timeout, a declined consent, an
   unticked permission, a missing refresh token, or an ID token that isn't for this client store
   nothing, and the row reads "Consent failed: <why>." `internal_notify` must be an
   `@nextronai.com` account ("internal_notify must be a nextronai.com account"). **Outside
   `prod`**, the account must also be on the "Dev and test allowlist" setting (Settings, Engine),
   so add your own address there before granting in `dev` or `test`. A refused token is revoked
   at Google.
4. **Grant again, or remove.** Granting again replaces the stored token and revokes the old
   one, unless a stored grant (the new one included) uses the same Google account; removing
   (two steps, like a credential) revokes the token and deletes both entries, even when Google
   can't be reached. Google's revoke ends Nextron's whole grant for that Google account, so a
   token is not revoked while a stored grant uses the same account; the row then says "It was
   not revoked, because a stored grant uses the same Google account, and revoking it would end
   that grant too." If revoking fails, the row says to remove Nextron at
   myaccount.google.com/permissions by hand.
5. **Needs re-consent.** When Google refuses a stored grant on refresh (`invalid_grant`: the
   access was revoked), the row reads "Needs re-consent: Google refused
   the stored grant on ... UTC. Grant it again." and the engine stops calling Google for it
   until you grant it again.

Logs carry only the grant, a reason and an error code (`google_client_imported`,
`google_consent_started`, `google_consent_failed`, `google_grant_stored`,
`google_token_revoked`, `google_grant_needs_reconsent` and so on), never a token, the client
secret, the code, the state, a URL, the path or the account. `tests/app/test_google_secrecy.py`
plants each of those and finds it in no response, log record, error, database column or file.

## Dev and test sink

In `dev` and `test`, email and alerts reach only the people on the `sink_allowlist` setting
(AD-14). Every other message is written to a file instead of being sent.

- **The allowlist** is "Dev and test allowlist" in the Engine section of the Settings page: one
  email address or numeric Telegram chat id per line. A Telegram `@username` is refused, because
  a user name can be handed to someone else. It is empty by default, so nothing is delivered
  until you list your own test inboxes and chats. Addresses match regardless of ASCII case
  (`Mac@Example.com` matches `mac@example.com`); nothing else is normalised (no spaces are
  stripped, and `straße@x.com` never matches `strasse@x.com`).
- **It is read on every send**, so a change on the Settings page applies to the next message. If
  it can't be read (a locked database, or a stored list that no longer passes its rule), the
  message is recorded, never delivered, and the sink logs `sink_allowlist_unreadable` as a
  WARNING.
- **Everyone else is recorded** in `<data dir>\sink\<YYYY-MM-DD>.jsonl` (the UTC date), one
  ASCII JSON object per line: `time`, `port` (`mail` or `alert`), `recipient`, `subject` (mail
  only) and `text`. The send reports success with `sink=true`, and no external service is
  contacted. If the line can't be written, the send fails with `sink_write_failed` and nothing
  is sent.

  ```text
  {"time":"2026-11-17T23:59:59.000000Z","port":"alert","recipient":"-100123","text":"..."}
  ```

- **The sink files hold recipient addresses and message text.** They stay in the environment's
  own data dir (never under OneDrive). Delete them when you no longer need them; retention
  arrives with the retention story. Logs carry only `sink_passed`, `sink_recorded` and
  `sink_allowlist_unreadable` with the `port`, never a recipient, subject or text.

**How it is wired.** Every `mail` or `alert` adapter is registered once in
`nextron.main.ROLE_ADAPTERS` as `RoleAdapter(name, role, adapter_type, build)` and built only by
`build_role_adapter`, which wraps it in `MailSink` or `AlertSink` (`nextron/adapters/sink`)
everywhere except `prod`. The registry is empty until the first adapter (Telegram, Story 1.12).

```python
alert = build_role_adapter("telegram", AdapterContext(config, secrets, clock), units=units)
delivery = alert.send(AlertMessage(chat_id, text))   # dev/test: delivered or recorded
```

`tests/contracts/test_sink_contracts.py` fails when an adapter class that sends an
`AlertMessage` or a `MailMessage` isn't registered for its role, when an adapter's `send`
doesn't annotate its message parameter, when any module under `nextron/` refers to a sender
outside `main.py`'s `ROLE_ADAPTERS` assignment (an adapter package may use its own), when a
registered adapter isn't wrapped outside `prod`, or when either message gains a field the sink
doesn't check.

## Database

One SQLite file per environment: `<data dir>\nextron.db`, for example
`%LOCALAPPDATA%\Nextron\dev\nextron.db`. Only `nextron/db/engine.py` opens it: WAL,
`busy_timeout=5000`, `synchronous=NORMAL`, `foreign_keys=ON`, and `BEGIN IMMEDIATE` for every
transaction, reads included, so keep transactions short. Transaction SQL sent any other way
(`COMMIT`, `ROLLBACK`, `SAVEPOINT` and so on) raises. Nothing copies the file; backups arrive in
a later story.

**Startup migration.** After the port check, `serve` creates the file if it is missing and runs
`alembic upgrade head` in one transaction when the database is behind the code, logging
`database_migrated` with `from_revision` and `to_revision`. `engine_started` carries
`db_revision`. `serve` refuses, with exit 2 and `startup_check_failed`
(`check=database_revision`), and changes nothing, when:

- the database's revision is unknown to this code: "database revision <rev> is newer than this
  code" (run the newer code, or restore a backup);
- it has several revisions, or tables but no revision, or it is a non-empty file not in WAL
  mode (it isn't this engine's database; the engine switches only an empty file to WAL).

A file that can't be opened (not SQLite, locked too long), or a migration that fails or leaves
foreign-key violations (rolled back), ends with exit 2 and `database_migration_failed` with the
traceback in `nextron.log`.

**The migrations so far**, in order (`nextron/db/alembic/versions/`):

| Revision | Creates | Owner |
|---|---|---|
| `0001_1_3_persistence_foundation` | `events`, the append-only audit log, and its triggers | `db.eventlog` |
| `0002_1_6_settings` | `settings` (see [Settings](#settings)) | `setup` |
| `0003_1_10_jobs` | `jobs` (see [Jobs](#jobs)), its checks and its indexes, two of them partial | `jobqueue` |

**Migrations are forward-only.** Every `downgrade()` raises `NotImplementedError`; a downgrade
could drop the append-only `events` audit trail. Recover from a bad upgrade by restoring the
`pre_migrate` backup (a later story).

**Adding a migration.** Alembic is configured in code (`nextron/db/migrations.py`); there is no
`alembic.ini` and no `alembic` command. A story that changes the schema:

1. Changes the models in `nextron/db/models/` (a new module is imported in its `__init__.py`),
   using `id: Mapped[str] = ulid_pk()` for the key, `UtcDateTime` for times and unique columns for
   natural keys.
2. Maps every new column to its writer in `nextron/db/owners.py` (`TABLES`) and classifies it in
   `nextron/db/pii_registry.py` (`none`, `redact`, `null` or `keep_hash`).
3. Hand-writes `nextron/db/alembic/versions/NNNN_<epic>_<story>_<short slug>.py`, for example
   `0001_1_3_persistence_foundation.py`: `NNNN` is the next number, underscores only (Alembic
   rejects `-`), the id is at most 31 characters and equals the file name, `down_revision` is
   the previous head, and `downgrade()` raises `NotImplementedError`. `env.py` runs it in batch mode, so `op.alter_column` and friends
   rebuild SQLite tables; a rebuild of `events` must create its triggers again.
4. Repeats every index and check exactly. A partial index needs the same `sqlite_where` in the
   model and the migration, and a check's full name in a migration goes through `op.f(...)`
   (`name=op.f("ck_jobs_status")`): the naming convention would otherwise prefix it a second
   time (`ck_jobs_ck_jobs_status`).

`tests/contracts/test_persistence_contracts.py` checks all of it: one linear chain, the id rules,
forward-only downgrades, owner and PII coverage, the key and datetime rules, the `events`
triggers, and that the models match a database at head (the drift test). The drift test sees
neither a partial index's `WHERE` nor a check, so `tests/contracts/test_job_contracts.py` also
compares the SQL of every table's indexes and checks with the model.

**Units of work.** Each entry point (a web handler, a job handler or a CLI command) opens one
unit of work and calls one use case. Only the use case opens transactions; owner commands join
the open one and never begin, commit or roll back:

```python
from nextron.db import eventlog
from nextron.db.uow import command, current, use_case

with units.open():                        # the entry point; main.py builds `units`
    approval_api.approve(draft_id, rev, shown_hash)

@use_case                                 # nextron/app/approval/api.py
def approve(draft_id: str, rev: int, shown_hash: str) -> None:
    uow = current()
    with uow.transaction():               # BEGIN IMMEDIATE ... COMMIT; rolls back on error
        ...                               # own repositories use uow.session
        eventlog.append(entity="drafts", entity_id=draft_id, from_status="ready",
                        to_status="approved", cause="operator_approved", cause_ref=None,
                        actor="operator", at=now)
        sending_api.enqueue(draft_id)     # an @command: joins this transaction
        uow.publish(MessageSent(...))     # subscribers (registered in main.py) run here
```

No transaction spans a network call: prepare in one `uow.transaction()` block, make the port
call, then apply in a second block. An exception that leaves a command rolls the whole
transaction back, even when the use case catches it. A web entry point opens its unit of work in
the sync handler body, not in a `Depends` or a middleware: FastAPI may run a dependency's setup
and teardown on other threadpool threads than the endpoint, and a unit of work is bound to the
thread that opened it.

**Read-only units of work.** `units.open(read_only=True)` is the unit of work every dashboard GET
handler runs in. `uow.session` works anywhere in it: the first use begins one read transaction
(`BEGIN IMMEDIATE`, then `PRAGMA query_only=ON`) that lasts until the handler returns and is
then rolled back, and a GET that never reads never touches the database. Use cases,
`uow.transaction()`, commands and `publish` raise `UnitOfWorkError`; the session refuses `add`,
`delete`, `merge`, `flush` and the bulk methods, and any flush; a statement other than `SELECT`
or `WITH` raises `ReadOnlySqlError` (`PRAGMA query_only=OFF` included); and a handler that
leaves changed ORM objects behind raises when it returns. Only `nextron/db/engine.py`
(`read_only_connection`) sets `query_only`, and it turns it off again before the connection
goes back to the pool.

## Jobs

Background work (crawls, mailbox syncs, sends, AI calls) runs as durable jobs in the `jobs`
table (owner `jobqueue`, AD-10), so it survives a crash or a restart, never runs twice at once,
and retries only what may succeed later. The rules live in `nextron/domain/job.py`, the service
in `nextron/app/jobqueue/api.py` (`JobQueue`) and the runner in `nextron/jobs/runner.py`. No job
type is registered yet; the stories that add work add them.

**Declare a job type** in `nextron.main.build_job_types`, the only code that builds the registry
(`tests/contracts/test_job_contracts.py` checks it), before `freeze()`:

```python
job_types.register_job_type(
    JobType(
        name="enrich_lead",                    # verb_noun, at most 48 characters
        default_lane=Lane.BULK,
        lanes=frozenset({Lane.BULK}),           # the lanes it may run on
        priority=50,                            # 0 to 100; higher runs first
        max_runtime=timedelta(minutes=5),       # whole seconds, 1 s to 24 h
        retry_limit=3,                          # 0 to 20 retries after the first run
        handler=enrich_lead,                    # thin: calls one use case, returns None
    )
)
```

**Handlers must be idempotent.** A run cut off by a crash or a shutdown is run again from the
start unless it was the job's last attempt, and the cut-off run may already have committed its
work. Work that must never happen twice (sending an email, say) uses retry limit 0 plus its own
state guard, checked and set by the use case itself (AD-5).

**Enqueue** with `JobQueue.enqueue`, an `@command`, inside the transaction whose change makes the
work necessary, so the job exists exactly when that change commits:

```python
jobqueue.enqueue("enrich_lead", scope=lead_id, discriminator=str(rev),
                 payload={"v": 1, "lead_id": lead_id}, concurrency_key="domain:example.com")
```

It raises `UnknownJobType` for an unregistered type and `InvalidJob` for a lane the type doesn't
allow, a bad key, payload or concurrency key, or a naive `run_after`; either dooms the caller's
transaction. `run_after` defaults to now and `lane` to the type's default lane.

- **Keys.** The idempotency key is `<type>:<scope>:<discriminator>`. The scope is a ULID,
  `inbox:<ULID>` or `global`; the discriminator is `tick`, a `YYYY-MM-DD` date or a revision
  from `0` to `999999999` (text, no leading zeros). While a job with the key is `queued` or
  `running`, enqueueing it again returns that job (`Enqueued(job_id, created=False)`) and writes
  nothing; once it is `done` or `dead`, the key makes a new job.
- **Payloads** hold IDs and scalars only, never personal data, secrets or free text: a flat JSON
  object with a version field `v` (1 to 999), at most 32 fields with `[a-z][a-z0-9_]{0,31}`
  names and at most 4,096 bytes, each value a ULID, an integer (signed 64 bits), a boolean, a
  `YYYY-MM-DD` date, a UTC time (`YYYY-MM-DDTHH:MM:SS[.ffffff]Z`) or a token of
  `[a-z0-9_:.-]{1,64}`. No `null`, float, list or object; a string with `@` or a space is
  refused. A refusal names the field, never its value. A handler gets the payload back as a
  read-only mapping in `Job.payload`.
- **Concurrency keys** (`inbox:<ULID>` or `domain:<name>`): a job is not claimed while a
  `running` job holds the same key, on any lane.

**Lanes and threads.** The FastAPI lifespan starts the runner when the server starts and stops
it when the server stops, so handlers never run on the event loop. Each lane has its own
threads: `nextron-job-realtime-1`, `nextron-job-interactive-1`, and `nextron-job-bulk-1` (plus
`nextron-job-bulk-2` when `bulk_threads` is 2), and one `nextron-job-keeper`. Time-critical work
(mail, replies, alerts) goes on `realtime`, the Operator's own requests on `interactive`, and
crawls and batches on `bulk`, so a slow crawl never delays a send. An idle lane looks for work
every second; enqueueing doesn't wake it sooner. A claim takes the lane's due job (`run_after`
now or earlier) with the highest priority, then the oldest `run_after`, then the lowest id. It
runs the handler in its own unit of work, then marks the job `done`, or hands the error to
`JobQueue.fail`. A handler returns `None`; any other value fails the job.

**Retries and backoff.** `attempts` counts claims. Only an `AdapterError` with
`retryable=True` is retried: the job goes back to `queued` after 30 s, then 60 s, 120 s and so
on, doubling up to 30 minutes, until it has run `retry_limit + 1` times; then it is `dead`
(`retries_exhausted`). Any other exception makes it `dead` at once (`failed`), so a type with
retry limit 0 never runs twice. `last_error` holds `"<code>: <message>"` (or
`"<exception type>: <text>"`), redacted and cut to 500 characters. `dead` jobs stay until the
Operator dismisses them (Story 1.15).

**Leases.** A claim sets `lease_until` to now + `max_runtime` + 60 s. Every 30 s the keeper
renews the lease of every job this process is still running, then reaps the other `running`
jobs whose lease has ended: back to `queued` with the attempt counted, or `dead` at the last
attempt (`lease_expired`). The reaping happens only on the keeper's 30 s ticks, so a job
orphaned by a crash is claimable again at most `max_runtime` + 90 s after the crash, once the
engine is running again; a job still running here (after the PC slept, say) is never claimed
twice. Python can't stop a thread, so a handler past its `max_runtime` keeps its
lease and is only logged (`job_overran`); a hung handler is left to the heartbeat and the
supervisor. Every finish is fenced by `(id, attempts)`: a run whose claim was lost (reaped or
released meanwhile) changes nothing and logs `job_claim_lost`.

**Shutdown.** Stopping the server stops claiming at once and waits up to 15 s for running
handlers. Jobs still running then are released: back to `queued` with the attempt counted, or
`dead` at the last attempt (`released`). A killed process releases nothing; its leases expire.

**Maintenance.** The keeper purges `done` jobs last changed more than 30 days ago when the
runner starts and then 24 hours after each purge that succeeded (a failed purge is tried again
on the next tick), keeping the newest `done` row of each type (so
the health panel can show each type's last success). It never deletes a `dead` job.

**`bulk_threads`** ("Bulk worker threads", Settings, Engine section) is 1 or 2, default 1. It
is read once when the runner starts, so a change takes effect when the engine restarts; if it
can't be read, the runner uses 1 and logs `bulk_threads_unreadable`.

**Every status change** follows the Job machine (`queued`, `running`, `done`, `dead`; the table is
in `nextron/domain/job.py`) and appends an `events` row in the same transaction (entity `jobs`,
actor `system:jobqueue`). Read a job with `nextron.app.queries.jobqueue.job(job_id)`. Logs carry
only `job_id`, `job_type`, `lane`, `attempts` and an error `code`: `job_runner_started` (with
`bulk_threads`), `job_runner_stopped` (with `released`), `job_retry_scheduled`, `job_dead`,
`job_claim_lost`, `job_lease_expired`, `job_lease_lost`, `job_released`, `job_overran`,
`job_claim_failed`, `job_finish_failed`, `job_keeper_failed` and `jobs_purged` (with `count`).

## Logs

`serve` writes `<data dir>\logs\nextron.log`, for example
`%LOCALAPPDATA%\Nextron\dev\logs\nextron.log`. Logging starts only once `serve` holds the lock,
so a second instance and `doctor` never open the file. Records at WARNING and above also go to
stderr. Startup failures (`startup_check_failed`, `database_migration_failed`,
`server_start_failed`), crashes (`engine_crashed`, `thread_crashed`) and Python warnings are
logged too, and `engine_stopped` carries the exit code. A first start also logs Alembic's own
`Running upgrade ...` lines.

`serve --reload` (dev only) is the exception: Uvicorn serves from a worker process that does
not write `nextron.log`, so only the reloader's own records reach the file. The worker's
WARNING and above go to stderr, redacted.

- **Format:** one JSON object per line, UTF-8, with `ts` (UTC, ISO 8601 with milliseconds and
  `Z`), `level`, `logger`, `event`, any scalar ID fields passed as `extra`, `exc` (the
  traceback) when there is one, and `stack` with `stack_info=True`. ULIDs, UUIDs, enums, paths
  and dates in `extra` are written as text. Uvicorn's records, the access log included, are
  written the same way. Keep event names under 32 characters; a contract test checks literal
  ones.
- **Rotation:** at 10 MiB the file rolls over to `nextron.log.1`, and so on up to
  `nextron.log.9`: 10 files at most.
- **Level:** INFO. The loggers of `googleapiclient`, `google_auth_oauthlib`, `google.auth`,
  `google_auth_httplib2`, `httplib2`, `httpx2`, `httpcore2`, `urllib3`, `requests_oauthlib`,
  `oauthlib` and `anthropic` stay at WARNING.
- **Redaction:** logs hold IDs only. As a safety net, every message, argument, traceback and
  string field passes through `nextron.domain.redaction.redact`. It removes URL query strings
  and fragments and masks URL userinfo, `/bot<token>` paths, `Bearer` and `Basic` credentials,
  the values of credential fields (`api_key`, `x-api-key`, `token`, `secret`, `password` and
  similar), email addresses (also `%40`-encoded and Unicode ones), `sk-ant-` keys, base64 blobs
  and any run of 32 or more key-like characters. 26-character ULIDs survive. It does not mask
  phone numbers, names or other free text. Use `redact` for any error text you store, too.
  `AdapterError` redacts its message when it is raised.

```powershell
Get-Content "$env:LOCALAPPDATA\Nextron\dev\logs\nextron.log" -Tail 20 -Wait
```

## Test

```powershell
uv run pytest               # the unit, app and contracts suites; ai_eval is skipped
uv run pytest -m ai_eval    # the opt-in AI evaluation suite
uv run lint-imports         # the import contracts on their own
```

- `tests/unit`: pure functions, the persistence layer and the composition root, with fakes,
  temporary folders and temporary database files (the `db_engine` and `units` fixtures in
  `tests/conftest.py`; temporary Alembic chains in `tests/migration_chains.py`).
- `tests/app`: the engine as a real process against temporary folders, and whole pages
  rendered by a dashboard app (untrusted text on a fixture page).
- `tests/contracts`: import contracts, the source tree, pins, the lock file and vendored assets.
- `tests/fakes`: one in-memory fake per port, listed in `tests.fakes.PORT_FAKES` (`FrozenClock`
  for `Clock`, `InMemorySecrets` for `SecretsPort`, `InMemoryAlert` for `AlertPort`,
  `InMemoryMail` for `MailPort`, and in `tests/fakes/google_oauth.py` `FakeGoogleOAuth` for
  `GoogleOAuthPort`, `FakeConsentListener` for `ConsentListener` and `FakeClientFiles` for
  `ClientFilePort`, with `id_token(...)`, an unsigned ID token builder), plus `FakeUnits`, which
  stands in for the unit-of-work factory in dashboard tests (`tests/dashboard.py`) and records
  each unit opened. `tests/dashboard.py`'s `Dashboard` builds `GoogleAccess` on these fakes with
  a recording browser and an `allowlist` field, and keeps each consent flow's job in `jobs`
  until a test calls `run_jobs()`.
  `tests/contracts/test_test_isolation.py` fails when a port has no fake there, or a fake
  doesn't match its port.
- `tests/fixtures`: fixture files, such as the hostile-text page
  `tests/fixtures/templates/untrusted_page.html`.
- `tests/ai_sets`: synthetic AI test cases only.

**Every test is isolated** (`tests/conftest.py`, `tests/live_guard.py`; AD-14):

- It runs as `NEXTRON_ENV=test`, with no other `NEXTRON_*` variable and an empty temporary
  `%LOCALAPPDATA%`, so the data dir is `<temp>\Nextron\test`. A test of the unset case calls
  `monkeypatch.delenv("NEXTRON_ENV")`.
- **No live adapter can be built.** `nextron.adapters.secrets.default_backend` (Credential
  Manager), `nextron.adapters.google_oauth.default_http_client` (Google's OAuth endpoints, built
  on a `GoogleOAuthClient`'s first call) and the `build` of every `ROLE_ADAPTERS` entry raise
  `LiveAdapterInTests`, so `KeyringSecrets("test")`, or `main.build_app` without injected
  secrets, fails. Inject a fake
  (or patch `main.KeyringSecrets`). A test that has neutralised what a constructor reaches may
  restore it with `allow_live_constructor(monkeypatch, module, name)`.
- **Credential Manager can't be called.** `WinVaultKeyring`'s `get_password`, `set_password`,
  `delete_password` and `get_credential` raise `LiveAdapterInTests` too, so a `WinVaultKeyring`
  built directly, or `keyring.get_password(...)`, never reaches it. `allow_live_constructor`
  never restores these.
- **No test opens a browser.** `webbrowser.open`, `open_new` and `open_new_tab` raise
  `LiveAdapterInTests`, and `allow_live_constructor` never restores them; `main.open_in_browser`
  looks `webbrowser.open` up when it is called, so it is refused too. Inject a recording browser
  (`browser=` on `open_dashboard` and `build_app`).
- **No test reaches the network.** These raise `NetworkInTests` for anything but a loopback
  address or `localhost`: the lookups `socket.getaddrinfo`, `gethostbyname`,
  `gethostbyname_ex`, `gethostbyaddr` and `getnameinfo`; `socket.socket.connect`, `connect_ex`,
  `sendto` and (where the platform has it) `sendmsg`; and on Windows asyncio's
  `IocpProactor.connect` and `sendto`, which skip the others for an IP literal.
- `tests/live_guard.py` classifies every package under `nextron/adapters`: guarded (with its
  live constructors), no external system (with the reason; none of its modules may send mail or
  alerts), or not yet built (it holds only its `__init__.py` and defines nothing). A new live
  adapter adds its constructors there; the isolation contract fails until every package is
  classified.

## Layers and import contracts

`nextron/` is split into `domain` (stdlib only), `ports`, `db`, `app/<owner>` with
`app/queries/<owner>.py`, then `web`, `jobs` and `adapters`, with `main.py` as the composition
root. Each package docstring states what it may import. The `[tool.importlinter]` section of
`pyproject.toml` enforces the seven contract groups of AD-1, and `tests/contracts` runs them on
every test run.

Contract group 7 (each owner uses only its own repositories) is generated from
`nextron/db/owners.py`. After changing the owners, regenerate it:

```powershell
uv run python scripts/gen_owner_contracts.py          # rewrite pyproject.toml
uv run python scripts/gen_owner_contracts.py --check  # what the tests run
```

When you add an adapter under `nextron/adapters/`, add it to contract 5, give its port a fake
in `tests/fakes` (listed in `PORT_FAKES`), and classify the package in `tests/live_guard.py`; the
contract tests fail until you do. A `mail` or `alert` adapter is also registered in
`nextron.main.ROLE_ADAPTERS` (see [Dev and test sink](#dev-and-test-sink)).
