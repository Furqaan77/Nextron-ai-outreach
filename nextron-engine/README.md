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

## Check readiness

```powershell
uv run nextron doctor                 # the environment NEXTRON_ENV names
uv run nextron doctor --env prod      # another environment, for this run only
uv run nextron doctor --json          # the same results as JSON
```

`nextron doctor` lists everything an install still lacks before the engine can run safely, so a
first install or an upgrade can be confirmed green before real work starts (FR-56). Each check
has an id, a status (`ok`, `warn` or `fail`) and a one-line message:

| Check | `ok` | `warn` | `fail` |
|---|---|---|---|
| `python_version` | Python 3.14.8 or later | | older, or unreadable |
| `sqlite_version` | SQLite 3.51.3 or later | | older, or unreadable |
| `tzdata` | `ZoneInfo("Europe/London")` loads | | it doesn't: run `uv sync --frozen` |
| `prod_env_not_persistent` | no persistent `NEXTRON_ENV=prod` (see below); the message names the environment and where it came from (`--env` or `NEXTRON_ENV`) | | a persistent `prod`, or an unreadable registry |
| `data_dir_outside_onedrive` | the data dir is outside OneDrive | | it is inside OneDrive |
| `backup_dir_separate` | the backup dir is separate from the data dir and outside OneDrive | | it is the data dir (in any case), inside it, contains it, or is inside OneDrive (the data dir's rules: under `%OneDrive%`, `%OneDriveCommercial%` or `%OneDriveConsumer%`, or a path segment starting with "OneDrive") |
| `port_free` | the dashboard port is free, or it is busy and `<data dir>\heartbeat` is under 2 minutes old: this environment's running engine holds it | | busy with no fresh heartbeat (a hung engine shows the same), or the probe fails |
| `required_credentials` | the 5 required credentials (see [Credentials](#credentials)) are set, each 1,280 characters or fewer | | lists the labels that are not set, too long, or unreadable in Credential Manager |
| `internal_notify_grant` | the Google `internal_notify` grant is stored and Google refreshed it | Google can't be reached: run doctor again later | the Desktop client is not imported, the grant is missing or needs re-consent, Credential Manager fails, or another error (by its code) |
| `offpc_backup` | "Off-PC backup folder" (`backup_offpc_dir`) is set, on another drive than the data dir, and the Backup passphrase is set (see [Backups](#backups)) | `off-PC backup not configured (OQ-A3): backups are local only; ...`, listing every reason with its fix: the folder is blank, is the data dir or the backup dir (or inside either), or can't be read; the passphrase is not set, too long or unreadable. Or the folder is on the data dir's drive (a lost disk would take both) | |
| `required_settings` | every required setting (`sender_timezone`, `alert_chat_id`) is set and still valid | | lists `<label> (<key>)` for each one that isn't, or the settings can't be read |
| `database_revision` | the database is at the code's head revision | no database file, an empty one, or one behind head: "will migrate on next start" | a revision this code doesn't know ("newer than this code"), or not this engine's database |

The table looks like this (some rows left out):

```text
status  check                      message
ok      python_version             Python 3.14.8 (needs 3.14.8 or later)
...
warn    database_revision          no database file yet; it will be created and migrated on next start
11 ok, 1 warn, 0 fail
```

`--json` prints the same list as `[{"id", "status", "message"}, ...]` (ASCII-only), and
nothing else. No message ever holds a secret value, an account address or an error's text: only
an error's type name or code.

| Exit code | Meaning |
|---|---|
| 0 | nothing is `fail` (warnings allowed) |
| 1 | at least one check is `fail` |
| 2 | `NEXTRON_ENV` (or another variable above) is unset or invalid; `--env` takes only `dev`, `test` or `prod` |

**What doctor never does.** It takes no lock (it runs while the engine runs), configures no
logging (it never opens `nextron.log`), and builds no app, runner, scheduler or heartbeat
writer. It opens `<data dir>\nextron.db` read-only (`mode=ro`) and never creates, migrates or
writes it; a missing data dir or database stays missing. Like the engine, it begins each read
with `BEGIN IMMEDIATE`; on a read-only file that opens a read transaction, so a read doesn't
wait for the engine's writes. Reading may leave empty `nextron.db-wal` and `nextron.db-shm`
files; the engine reuses them, and SQLite deletes them when the engine's last connection closes
at a clean stop.

**The off-PC check.** `offpc_backup` reads the Backup passphrase from Credential Manager first
(the value is dropped at once), then the `backup_offpc_dir` setting from the database (blank when
there is no database file). It never opens, lists or creates the folder, so an unplugged drive
or a slow share can't hold doctor or the health panel; "same drive" compares drive letters and
`\\server\share` names only, so a volume mounted inside a folder of `C:\` isn't detected. A
share or drive letter that points back at this PC isn't off the PC either, and doctor can't tell:
`\\localhost\...`, `\\127.0.0.1\...`, `\\<this PC's name>\...`, an admin share such as `C$` on
this PC, or a `subst` or mapped letter. Choose a folder that is really on another disk or
another machine.

**The Google refresh.** When the Desktop client and the `internal_notify` grant are stored,
doctor asks Google for one fresh access token through the same code the engine uses, so
"refreshable" is proven, not guessed. With no client or grant, or a grant already marked
"needs re-consent", nothing is sent. The access token is dropped at once. That one call is kept
in doctor's memory only: it writes no `api_usage` row and doesn't appear on the Quotas page
(`google_oauth` has no quota). If Google answers `invalid_grant`, the grant is marked "needs
re-consent" in Credential Manager, as the engine would mark it. Reading credentials also moves
any entry still in the pre-Story 1.9 layout to its own target (see [Secrets](#secrets)).

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
| 2 | `NEXTRON_ENV` unset or invalid, another invalid setting, a failed startup check (the message names it, `database_revision` and `pre_migrate_backup` included), a data dir that can't be created, a failed database migration, or a server that failed to start |
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
refused, and the page's error notice says `Request refused: security check failed` (see
[Alerts and notifications](#alerts-and-notifications)). Reload the tab.

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
    the handler never runs. HTMX shows that text in the page's error notice (see
    [Alerts and notifications](#alerts-and-notifications)). It is logged as `request_refused`
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

The backup settings (`backup_time`, `backup_rotation_days` and `backup_offpc_dir`, all in the
Engine section) are described under [Backups](#backups).

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

**The US solicitation label** (`us_solicitation_label`, "US solicitation label" in Footer and
booking; Story 3.1, NFR-2, OQ-8) is a string of at most 200 characters, blank by default. The US
footer template ends with a `{us_solicitation_label}` line: blank, the line is dropped; set
(say, to `Advertisement`), every US email's footer shows that text on its own line, with no code
change. Whether US emails carry a label is decided with counsel before the first US send. It is
declared once; Story 4.11's footer reads it with the other footer settings.

## Seeds

The Segments (FR-1) come from `seed/segments.yaml` until the Slice 3 editing screens ship
(AD-12): Law-PrivateAI and Ops-Automation, each with its industry keywords, size range, ranked
decision-maker roles, disqualifiers, weighted Signal types (the PRD's FR-1 table, row for row)
and discovery queries. The Markets (FR-2) come from `seed/markets.yaml`: the US and UK
cold-email rules (see [Markets](#markets)). Both files are required. Edit them, then load them:

```powershell
uv run nextron seed sync                 # the environment NEXTRON_ENV names
uv run nextron seed sync --env prod      # another environment, for this run only
```

```text
segments: 2 changed, 0 unchanged
  law_private_ai: created (version 1)
  ops_automation: created (version 1)
markets: 2 changed, 0 unchanged
  us: created (version 1)
  uk: created (version 1)
```

Each block's first line counts the Segments (or Markets) created or changed, then the unchanged
ones; a line follows for each one created, changed (`changed (version 1 -> 2)`) or stored but no
longer in the file (`not in seed/segments.yaml, left as it is (version 1)`, or `not in
seed/markets.yaml, ...`: a Segment or Market is never deleted, since Leads and snapshots point
to it). Running it again on the same files prints `segments: 0 changed, 2 unchanged` and
`markets: 0 changed, 2 unchanged` and writes nothing.

**What a sync does.** It upserts each Segment by key into the `segments` table and then each
Market by code into the `markets` table (both owner `setup`): a new one is stored at version 1,
a changed definition gets the next version, and each of those appends one `events` row (entity
`segments` or `markets`, `v1` or `v<n>` to `v<n+1>`, cause `seed_sync`, actor `seed`). Both
files are read and validated before the database is touched, and both tables are written in one
transaction, so a failure anywhere changes nothing in either. Signal-type and disqualifier keys
stay unique across every stored Segment: if a Segment that is no longer in the file (one whose
key you renamed, say) still uses a key the file uses, the sync is refused before anything is
written, naming the file's field (`seed/segments.yaml:
segments[law_private_ai].signal_types[law_hiring_legal_ops].key: is also a key of the stored
Segment old_segment, which is no longer in the file: rename one of them`). **A sync never
re-scores a Lead**: it enqueues no job, publishes no notification and touches no other table. A
changed Segment applies to Leads discovered afterwards and to re-scoring Mac starts.

**The database must exist and be at head.** `seed sync` never creates or migrates the
database: only `serve` does, after its backup. Start the engine once (`nextron serve`) first;
otherwise it says so and exits 1, changing nothing. Like `doctor`, it reads the revision
read-only, takes no lock (it runs while the engine runs), configures no logging and reads no
credential.

**The file is a strict subset of YAML** (`nextron/domain/seed_yaml.py`), read without a YAML
library: mappings and lists indented with spaces, one-line `[a, b]` lists, `'single'` (`''` is
a quote) or `"double"` (only `\\` and `\"` escapes) quoted text, plain text, whole numbers and
`true`/`false`, with `#` comments. Everything else is refused with its line number: tabs,
anchors and tags, `{...}`, `|` and `>` blocks, multi-line text, a list under a key at the key's
own column, and any plain value another YAML reader would type differently (`yes`, `no`, `on`,
`null`, `~`, `True`, `1.5`, `007`, `1_000`, `.inf`, dates and times). So the file is never
misread, and a YAML library could replace the reader later without changing it. Write such a
value in quotes (`'1.5'`) when it must stay text.

**Validation** (`nextron/domain/segments.py`) then checks every field and names the first
problem by its path, such as
`seed/segments.yaml: segments[ops_automation].signal_types[ops_dynamics_gp].weight: must be
from 1 to 100`. Besides the field rules (keys `[a-z][a-z0-9_]` of at most 31 characters,
unique; clean texts; 1 to 50 distinct items per list, though `disqualifiers`, `match_terms` and
`preferred_roles` may be empty; `tier` 1 to 3, `weight` 1 to 100), it refuses: within a
Segment, a weight of a lower Tier number that isn't above every weight of a higher Tier number
(so Tier 1 is above Tier 3 even with no Tier 2); an Event whose evidence isn't exactly
`[quote]`, or that is `score_only`; a hiring Signal type (source `careers_page` or `ats_board`) without
`match_terms`; preferred roles whose `when_terms` aren't among the match terms; a title listed
twice in a Segment's ranks; and any Signal type that represents ABA Formal Opinion 512 or the
SRA notice, which are Approved Company Facts (FR-72), not Signals. A syntax or validation
problem exits 1 before the database is opened.

| Exit code | Meaning |
|---|---|
| 0 | synced (or nothing to change) |
| 1 | a file is missing, unreadable, over 1 MiB, not UTF-8 or invalid (the message starts with `seed/segments.yaml:` or `seed/markets.yaml:`); the database is missing, behind, ahead or not this engine's; or the sync failed (`seed sync failed (<type>); nothing was changed`) |
| 2 | `NEXTRON_ENV` (or another bootstrap variable) is unset or invalid; `--env` takes only `dev`, `test` or `prod` |

Other code reads the Segments through `queries.setup.segments()` (each `SeededSegment` with its
`id`, `key`, `version` and decoded `SegmentDef`) and `queries.setup.segment(key)`.

### Markets

A Market (FR-2, Story 3.1) holds one country's cold-email rules. `seed/markets.yaml` ships two,
`us` and `uk`; a new simple Market is a new entry in the file, with no code change (NFR-40). Each
entry has exactly these fields (`unknown_send_window` may be left out):

| Field | US | UK | Rule |
|---|---|---|---|
| `code` | `us` | `uk` | `[a-z][a-z0-9_]`, at most 16 characters, unique: the natural key |
| `name` | United States | United Kingdom | a clean text of at most 100 characters |
| `enabled` | `true` | `true` | a disabled Market is no Market: `market_for_country` returns `None` |
| `country_code` | `US` | `GB` | two capitals, unique |
| `cctlds` | `[us]` | `[uk]` | 1 to 10 two-letter TLDs of that country, none shared |
| `cold_email_allowed` | `true` | `true` | |
| `eligible_legal_forms` | `not_checked` | 8 Companies House types | `not_checked`, or 1 to 50 `[a-z][a-z0-9-]` forms (an empty list is refused) |
| `free_webmail_blocked` | `false` | `true` | |
| `default_timezone` | `America/New_York` | `Europe/London` | an IANA region name, as for a `timezone` setting |
| `unknown_timezone` | `America/New_York` | `Europe/London` | the fallback for an unknown location |
| `unknown_send_window` | `'11:00-17:00'` | (none) | `'HH:MM-HH:MM'`, start before end: the narrowed Send Window there |
| `english_variant` | `en_us` | `en_gb` | `en_us` or `en_gb` |
| `footer_template` | 5 lines | 5 lines | one item per footer line (below) |
| `privacy_line_required` | `false` | `true` | |

The UK's legal forms are `ltd`, `plc`, `private-limited-guarant-nsc`,
`private-limited-guarant-nsc-limited-exemption`, `private-limited-shares-section-30-exemption`,
`private-unlimited`, `private-unlimited-nsc` and `llp` (private and public limited companies,
unlimited companies and LLPs; every other company type, such as `old-public-company` or a
partnership, is not eligible).

**The compliance floor.** A seed edit can't loosen these; each is refused naming its field: cold
email is allowed only in a `US` or `GB` Market (NFR-5), and a `GB` Market always has
`free_webmail_blocked: true`, `privacy_line_required: true` and a list of legal forms drawn only
from the eight company types above (PECR). It may list fewer; any other type is refused naming
its index (`markets[uk].eligible_legal_forms[8]: isn't a corporate-subscriber company type a GB
Market may list (PECR)`).

**The footer template** is a list of lines, each a clean text of at most 200 characters. A line
may hold literal text and the placeholders `{sender_signature}`, `{legal_name}`,
`{postal_address}`, `{optout_line}`, `{privacy_line}` and `{us_solicitation_label}`; any other
`{` or `}` is refused, and each placeholder appears at most once. Every template holds the first
four (FR-23); `{privacy_line}` appears if and only if `privacy_line_required` is true, and
`{us_solicitation_label}` if and only if the Market is `US` (NFR-2). Those two stand alone on
their line, because a line left blank when the footer renders is dropped: a blank
`us_solicitation_label` setting drops the US label line. Story 4.11 fills the values in.

**Validation** (`nextron/domain/markets.py`) names the first problem by its path, as for the
Segments: `seed/markets.yaml: markets[uk].free_webmail_blocked: must be true for a GB Market
(PECR: corporate subscribers only)`. A Market that is no longer in the file keeps its country
code and ccTLDs: a file Market that reuses them is refused before anything is written
(`seed/markets.yaml: markets[gb].country_code: is also the country code of the stored Market uk,
which is no longer in the file: keep that Market's code`).

**Reading the Markets.** Other code reads them through `queries.setup.markets()` (each
`SeededMarket` with its `id`, `code`, `version` and decoded `MarketDef`) and
`queries.setup.market(code)`; a stored row that no longer validates raises `InvalidMarkets`.
Other tables point to a Market by its ULID `id` (`market_id`). A Lead's Market is never stored:
it is derived from its country code with `nextron.domain.markets.market_for_country(code,
markets)`. The same module answers `cctld_country(domain)` (`firm.co.uk` is `GB`, `firm.de` is
`DE`, `firm.com` is `None`: any two-letter last label counts as a country, so `.eu` and `.io`
too) and `is_free_webmail(domain)` (the vendored `FREE_WEBMAIL_DOMAINS`, subdomains included),
and renders a template with `render_footer_template(template, values)`. No setting or
dashboard route changes a Market rule (NFR-7): `tests/contracts/test_market_contracts.py` fails
on a POST route that names a Market, a `nextron/web` module that names the sync, or a `leads`
column that names a market.

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
   encrypted off-PC backup reads (see [Backups](#backups)). The backup passphrase is optional
   here; doctor's `offpc_backup` check warns while it isn't set.
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
everywhere except `prod`. It holds one entry so far: `telegram` (`TelegramAlert`, the `alert`
role; see [Alerts and notifications](#alerts-and-notifications)).

```python
context = AdapterContext(config, secrets, clock, meter)   # meter: the UsageMeterPort
alert = build_role_adapter("telegram", context, units=units)
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
(`COMMIT`, `ROLLBACK`, `SAVEPOINT` and so on) raises. Nothing copies the file: a backup reads it
only through SQLite's backup API (see [Backups](#backups)).

**Startup migration.** After the port check, `serve` creates the file if it is missing and runs
`alembic upgrade head` in one transaction when the database is behind the code, logging
`database_migrated` with `from_revision` and `to_revision`. Before upgrading a database that
already has data, it takes a `pre_migrate` backup inside that transaction (see
[Backups](#backups)); a new file or a database at head takes none. `engine_started` carries
`db_revision`. `serve` refuses, with exit 2 and `startup_check_failed`
(`check=database_revision`), and changes nothing, when:

- the database's revision is unknown to this code: "database revision <rev> is newer than this
  code" (run the newer code, or restore a backup);
- it has several revisions, or tables but no revision, or it is a non-empty file not in WAL
  mode (it isn't this engine's database; the engine switches only an empty file to WAL).

A file that can't be opened (not SQLite, locked too long), or a migration that fails or leaves
foreign-key violations (rolled back), ends with exit 2 and `database_migration_failed` with the
traceback in `nextron.log`. When a `pre_migrate` copy was taken first, the message names the
copy's full path and `nextron restore "<path>"` (quoted; Story 1.19 adds that command), and
the log line carries the copy's `file` name.

**The migrations so far**, in order (`nextron/db/alembic/versions/`):

| Revision | Creates | Owner |
|---|---|---|
| `0001_1_3_persistence_foundation` | `events`, the append-only audit log, and its triggers | `db.eventlog` |
| `0002_1_6_settings` | `settings` (see [Settings](#settings)) | `setup` |
| `0003_1_10_jobs` | `jobs` (see [Jobs](#jobs)), its checks and its indexes, two of them partial | `jobqueue` |
| `0004_1_11_heartbeat` | `runner_heartbeat`, one row and its two checks (see [Triggers and the heartbeat](#triggers-and-the-heartbeat)) | `ops` |
| `0005_1_12_notify` | `alerts` (unique on kind, subject and channel, a status check) and `notifications` (unique on kind and subject, `ix_notifications_open`) (see [Alerts and notifications](#alerts-and-notifications)) | `notify` |
| `0006_1_13_metering` | `api_usage` (three checks, `ix_api_usage_at`) and `ai_usage` (two checks, `ix_ai_usage_at`) (see [Metering, quotas and the AI budget](#metering-quotas-and-the-ai-budget)) | `db.metering` |
| `0007_1_16_backups` | `backups`: one row per backup, its five checks and `ix_backups_started_at` (see [Backups](#backups)) | `ops` |
| `0008_2_1_segments` | `segments` (unique `key`, `ck_segments_version`) (see [Seeds](#seeds)) | `setup` |
| `0009_1_17_offpc` | six nullable off-PC columns on `backups` (`offpc_status`, `offpc_name`, `offpc_ledger`, `offpc_at`, `offpc_deleted_at`, `offpc_error`), added with `ALTER TABLE ... ADD COLUMN`, and their three checks (`ck_backups_offpc_status`, `ck_backups_offpc_ledger`, `ck_backups_offpc`) | `ops` |
| `0010_3_1_markets` | `markets` (unique `code` and `country_code`, `ck_markets_version`) (see [Markets](#markets)) | `setup` |

**Migrations are forward-only.** Every `downgrade()` raises `NotImplementedError`; a downgrade
could drop the append-only `events` audit trail. Recover from a bad upgrade by restoring the
`pre_migrate` backup `serve` took before it (see [Backups](#backups); `nextron restore`
arrives with Story 1.19).

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
in `nextron/app/jobqueue/api.py` (`JobQueue`) and the runner in `nextron/jobs/runner.py`. The
job types so far: `send_alert` (see [Alerts and notifications](#alerts-and-notifications)) and
`backup_db` (see [Backups](#backups)); the stories that add work add theirs, with a thin handler
in `nextron/jobs/handlers.py`.

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
        handler=enrich_lead,                    # thin: one use case; None or DEFERRED
        group=JobGroup.ENRICHMENT,              # its group on the health panel
    )
)
```

`group` is the health panel's group for the type: `discovery`, `enrichment`, `recheck`
("Re-check"), `sending`, `reply_check` ("Reply check"), `unsubscribe_poll` ("Unsubscribe
Endpoint poll"), `backup` or `other`. It defaults to `other`, but every registration names it
(`tests/contracts/test_health_contracts.py`). `send_alert` is `other`: the panel's groups have
none for alerts.

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
`JobQueue.fail`. A handler returns `None`, or `DEFERRED` (see [Deferring a
job](#deferring-a-job)); any other value, the plain string `"deferred"` included, fails the job.

**Retries and backoff.** `attempts` counts claims, except deferred ones (see [Deferring a
job](#deferring-a-job)). Only an `AdapterError` with `retryable=True` is retried: the job goes
back to `queued` after 30 s, then 60 s, 120 s and so on, doubling up to 30 minutes, until it
has run `retry_limit + 1` times; then it is `dead` (`retries_exhausted`). Any other exception
makes it `dead` at once (`failed`), so a type with retry limit 0 never runs twice, except that
it runs again after each `DEFERRED`. `last_error` holds `"<code>: <message>"` (or
`"<exception type>: <text>"`), redacted and cut to 500 characters. `dead` jobs stay until the
Operator dismisses them on the health panel (see [Engine health](#engine-health)).

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
`job_claim_failed`, `job_finish_failed`, `job_keeper_failed`, `job_deferred`, `job_dismissed`
and `jobs_purged` (with `count`).

### Deferring a job

A handler whose use case the budget refused (see [Metering, quotas and the AI
budget](#metering-quotas-and-the-ai-budget)) returns `DEFERRED` from `nextron.domain.job`:

```python
from nextron.domain.job import DEFERRED

def enrich_lead(job: Job) -> HandlerResult | None:
    if not enrichment.enrich(job.payload["lead_id"]):   # False: refused by domain.budget.allow
        return DEFERRED
    return None
```

The runner then finishes the job with `JobQueue.defer`: back to `queued` with `run_after` one
hour later (`DEFER_DELAY`), the lease cleared and the attempt given back (`attempts - 1`), with
a `deferred` event and a `job_deferred` log record (INFO). So `attempts` doesn't count a
deferred claim, and a deferral never uses a retry: a job deferred every hour for a month, even
one with retry limit 0, runs again after each `DEFERRED` and never becomes `dead` for it; it
does its work once the month's usage or a raised limit allows.

**Return `DEFERRED` only before the use case has done any work.** A deferred job runs again
from the start, so the budget check (`domain.budget.allow`) comes first, and a use case that has
already written or called a provider must finish, not defer. Like every finish, the deferral is
fenced on the claim's `attempts`: a lost claim changes nothing and logs `job_claim_lost`.

## Backups

The engine copies its database every day, on demand and before every migration, so a bad
upgrade, a corrupted file or a damaged disk can't lose the Suppression List and the Provenance
Records (FR-58, AD-2, AD-20, OP-5, OP-6, NFR-16). The `ops` service does it
(`nextron/app/ops/api.py`: `LocalBackups` and `Backups`); the rules are in
`nextron/domain/backup.py`.

**How a copy is made.** Only SQLite's backup API reads the live file
(`nextron.db.engine.copy_database`): a read-only connection to `<data dir>\nextron.db` is
copied into a new, empty file, so the copy holds exactly what was committed, even while the
engine writes. `tests/contracts/test_backup_contracts.py` refuses, anywhere under `nextron/`, an
import of `shutil`, a `.copy(...)` call with arguments (`Path.copy`), `.copy_into(`,
`hardlink_to(`, `symlink_to(`, `os.link(`, `os.symlink(` and `VACUUM INTO`, and `.backup(`
outside `nextron/db/engine.py`. Then the copy is verified through a read-only,
`immutable` connection that never changes it: `PRAGMA integrity_check` must answer exactly
`ok`, the copy must have an Alembic revision, and the rows of the Suppression List
(`suppression_entries`) and the Provenance Records (`provenance_records`) are counted while
those tables exist. Last come its size and SHA-256. A copy that fails any of this is deleted.
The copy is a single file whose header keeps WAL mode, so the engine can open it unchanged.

**Where copies go.** `<backup dir>\local\nextron-<env>-<YYYYMMDDTHHMMSS.mmm>Z.db`, for example
`%LOCALAPPDATA%\Nextron\backups\prod\local\nextron-prod-20261117T100000.123Z.db`, named by the
UTC start time with milliseconds (Windows forbids `:` in a name). An existing file is never
overwritten: that backup fails instead.

**When.**

- **Daily**, from the `backup_db` trigger, at "Daily backup time" (`backup_time`, Settings,
  Engine; 10:00 by default) in the sender's timezone (`sender_timezone`; UTC until it is set).
  Both are read when the engine starts, so a change takes effect at the next restart; if they
  can't be read, the trigger uses 10:00 UTC and logs `trigger_schedule_failed`.
- **A missed daily time is made up once**, right after the next start, when the newest daily
  backup started before the latest daily time (the PC was shut down or rebooted across it).
  A sleep across it needs no catch-up: the trigger's 24-hour grace runs it on wake. A new
  database (no daily backup yet) waits for its first time, so a new install never backs up at
  start.
- **On demand**, from "Back up now" on the health panel (see [Engine health](#engine-health)).
  It queues a `backup_db` job with the key `backup_db:global:<UTC date>` and the payload
  `{"v": 1, "kind": "manual"}`; a click while that job is queued or running is absorbed, and
  the panel says so.
- **Before every migration**: `serve` takes a `pre_migrate` copy of a database that already
  has data and is behind the code, inside the migration's write transaction, so nothing changes
  between the copy and the upgrade. If the copy fails, `serve` exits 2 without migrating
  (`startup check failed: pre_migrate_backup: <error>; the database was not migrated`, logged as
  `pre_migrate_backup_failed`). If the migration then fails, the message names the copy and
  `nextron restore "<path>"`, and the copy stays. After a successful upgrade the copy's row is
  recorded; if that fails, `backup_record_failed` is logged and the engine starts anyway.

**The job.** `backup_db` runs on the bulk lane only (priority 80, `max_runtime` 30 minutes,
retry limit 2, group Backup). One backup copies at a time. Each backup writes one `backups` row
(owner `ops`, migration `0007_1_16_backups`), once: `verified` with the path, size, SHA-256,
revision and counts (a count is empty when its table doesn't exist yet), or `failed` with the
redacted error. There is no state machine and no `events` row.

**A failed backup** (the copy, its check or the disk failed) is recorded `failed`, raises the
`backup_failed` ops alert ("Backup failed: <kind> <ULID>", Telegram and the dashboard) and shows
on the health panel's error list until a later backup verifies. It is not retried
automatically: the next daily time or "Back up now" tries again. Only a failure to record the
row raises from the job: the copy is deleted (so there is never a copy without a row),
`backup_record_failed` is logged, and a retryable `AdapterError` (`backup_not_recorded`) makes
jobqueue retry the job (retry limit 2; each retry takes a new copy), then `dead` with `job_dead`.

**Rotation.** After each verified backup (a `pre_migrate` one included), local copies older than
"Keep local backups for (days)" (`backup_rotation_days`, Settings, Engine; 7 to 35, default 35)
are deleted, and their rows marked (`local_deleted_at`). The newest verified copy is always kept,
however old. 35 days is the most because erased data must age out of backups within the 35-day
rotation (AD-17). Rotation deletes only files with a `verified` row, directly in
`<backup dir>\local` and named for this environment; anything else is skipped and logged
(`backup_rotation_skipped`). A file with no row (a crash mid-copy, or a `pre_migrate` copy whose
migration failed) is never deleted. A copy that can't be deleted (locked) is tried again next
time (`backup_copy_not_deleted`). A rotation setting that can't be read skips rotation. A
rotation that fails after the row is recorded (a locked database, say) is logged
(`backup_rotation_failed`, ERROR) and the backup still succeeds; the next backup rotates again.

### Off the PC

A lost, stolen or failed PC must not take every copy with it (AD-17, OP-6, NFR-17). After each
verified daily or manual backup, `ops` (`OffpcBackups` in `nextron/app/ops/api.py`) encrypts the
copy and writes it to **"Off-PC backup folder"** (`backup_offpc_dir`, Settings, Engine): a
folder on another drive or a network share, such as `E:\NextronBackups` or
`\\nas\backups\nextron`. It is blank by default: backups then stay on this PC only, and
`nextron doctor` warns (`offpc_backup`, OQ-A3). A `pre_migrate` copy always stays local: it
guards an upgrade, and a network call before the engine starts could hold startup on a slow
share.

**Set it up.**

1. Choose a passphrase and save it as "Backup passphrase" on the Credentials page (keyring
   `backup/passphrase`). It is read on every copy and stored nowhere else.
2. **Keep the passphrase somewhere safe off this PC** (escrow): a password manager, or on paper
   in a safe place. Without it no off-PC copy can ever be read; there is no recovery and no
   back door.
3. Set "Off-PC backup folder" to a folder on another drive or a network share. It must be a full
   path (`E:\...` or `\\server\share\...`, no `..`, not a `\\?\` or `\\.\` device path),
   not the data dir or the backup dir or inside either. The engine creates only the folder's last
   level, so a wrong or unplugged drive fails instead of growing a new tree.
4. Run `nextron doctor`: `offpc_backup` is `ok` once both are set and the folder is on another
   drive than the data dir. "Back up now" on the health panel then shows "copied off the PC".

**Changing the passphrase** affects only the copies made after the change. Each older copy still
needs the passphrase it was made with, so keep the old one (with its date) until no copy made
before the change is left in the folder: at least 35 days, and longer while no new copy
succeeds, because the newest complete copy is always kept, however old.

**Changing the folder** leaves the old folder's copies where they are: the engine only ever
lists and rotates the folder that is set now. Delete them yourself 35 days after the change
(erased data must age out of backups), or move them into the new folder, where rotation then
treats them like any other copy.

**What goes there.** `<folder>\nextron-<env>-<YYYYMMDDTHHMMSS.mmm>Z.db.enc`: the local copy's
name plus `.enc`. When `<backup dir>\erasure-ledger.jsonl` exists (Story 4.x writes it), it
goes beside it as `nextron-<env>-<stamp>.ledger.jsonl.enc`. Each file is written as
`<name>.part` (encrypted from the first byte), flushed to the disk and then renamed, so a
half-written file never has a final name and nothing reaches the folder in plaintext. An
existing name is never overwritten. The SHA-256 of the plaintext read while encrypting must
equal the verified backup's, or the off-PC copy is deleted and the copy fails.

**The file format (v1).** A 40-byte header, then the chunks (`nextron/adapters/crypto`):

- the header is `NXBK`, the format version (1), the scrypt parameters (`log2_n`, `r`, `p`), a
  16-byte random salt (new for every file) and a 16-byte key check;
- scrypt (n = 2^17, r = 8, p = 1: 128 MiB and about 0.3 s per file) turns the passphrase
  (NFC-normalised UTF-8) and the salt into a 32-byte AES-256-GCM key and a 32-byte check key.
  The key check is the first 16 bytes of HMAC-SHA256(check key, the first 24 header bytes), so
  a wrong passphrase is told apart from a damaged file. The parameters are stored in each file,
  so later versions can raise them and still read older copies;
- each chunk holds 64 KiB of plaintext (the last one may be shorter) plus a 16-byte tag. Its
  nonce is its index (safe, because every file has its own key), and the header, the index and
  a "final" flag are authenticated with it. So a changed byte, a reordered, removed or added
  chunk, or a cut file is refused: `backup_tampered`, or `backup_truncated` for a file cut at a
  chunk boundary. A wrong passphrase is `backup_wrong_passphrase`, and anything that isn't a
  version-1 backup is `backup_bad_format`.

`nextron/adapters/backup_target` (`FolderBackupTarget`, the `BackupTargetPort`) streams each file
through it: `put`, `list`, `get` (decrypt into a new local file; Story 1.18's restore test uses
it) and `delete`. Its errors never carry a path, the passphrase or an exception's text.

**What each backup records.** The off-PC step runs after the backup's row is recorded and never
fails the backup: the job still ends `done`. Its outcome is written once on the `backups` row
(migration `0009_1_17_offpc`): `offpc_status`, `offpc_name` (the file name, never the folder),
`offpc_ledger` (`copied` or `absent`), `offpc_at`, `offpc_error` for a failure, and
`offpc_deleted_at` once rotation deletes it.

| Outcome | When | What you see |
|---|---|---|
| `copied` | the copy (and the ledger, if any) is in the folder | "…, copied off the PC" on the health panel |
| `not_configured` | the folder is blank, is the data dir or the backup dir (or inside either), or the passphrase isn't set | "…, off-PC not configured"; no alert (doctor warns instead); `offpc_backup_skipped` with the `reason` |
| `failed` | the folder can't be reached or written, the name exists, the hash differs, the ledger copy failed, or the stored folder setting no longer parses | the `backup_failed` ops alert "Off-PC backup copy failed: <kind> <ULID>" (Telegram and the dashboard), "…, off-PC copy failed" marked on the panel, and an error on the panel's list until a later backup is copied off the PC (or off-PC is no longer configured) |

A failed off-PC copy isn't retried automatically, as with a failed local copy: the next daily time
or "Back up now" tries again. If the ledger fails after the database copy, the outcome is
`failed` and the database copy stays until rotation. If recording the outcome itself fails (a
locked database), `offpc_record_failed` is logged and the backup still succeeds.

**Rotation off the PC.** After each copy that succeeded, the folder is listed and this
environment's copies, ledgers and stale `.part` files older than 35 days are deleted; their rows
get `offpc_deleted_at`. The 35 days are fixed: they don't follow "Keep local backups for
(days)", because the off-PC copy is the disaster copy and erased data must age out of backups
within 35 days (AD-17). The newest complete copy and its ledger are always kept, however old.
Rotation goes by the name, so a copy with no row (an interrupted run) goes too; other files and
other environments' copies are never touched. A delete that fails is logged
(`offpc_rotation_failed`), the others still go, and the next copy tries again.

**Logs** carry IDs, kinds, codes, counts and file names, never a full path (the off-PC folder
included), a passphrase or an error's text: `backup_requested` (`job_id`, `job_created`),
`backup_completed` (`backup_id`, `kind`, `size_bytes`, `file`), `backup_failed` (ERROR:
`backup_id`, `kind`, `code`), `backups_rotated` (`count`), `backup_rotation_skipped`,
`backup_rotation_failed` (ERROR: `backup_id`, `code`), `backup_copy_not_deleted`,
`pre_migrate_backup_failed`, `backup_record_failed`, and off the PC `offpc_backup_copied`
(`backup_id`, `file`, `ledger`), `offpc_backup_failed` (ERROR: `backup_id`, `code`),
`offpc_backup_skipped` (`backup_id`, `reason`), `offpc_backups_rotated` (`backup_id`, `count`),
`offpc_rotation_failed` (ERROR: `backup_id`, `code`) and `offpc_record_failed` (ERROR:
`backup_id`, `code`).

**Not done yet.** The monthly restore test arrives with Story 1.18, and `nextron restore` with
recovery mode with Story 1.19; Story 4.x writes the erasure ledger. There is no passphrase-strength
rule. A failed off-PC copy is never retried before the next backup.

## Triggers and the heartbeat

**Periodic triggers** (AD-10) start periodic work: sends, mailbox syncs, unsubscribe polls,
backups. The closed list is the `Trigger` enum in `nextron/jobs/triggers.py`, exactly AD-10's
table (`tests/unit/test_triggers.py` checks it against the spine). A trigger only enqueues: each
firing enqueues one job of the job type with the trigger's own name, with the key
`<type>:global:tick` and the payload `{"v": 1}` (`JobQueue.enqueue_tick`); while that job is
still `queued` or `running`, a firing adds nothing. The runner's lanes run it like any other
job. So far one trigger is registered: `backup_db`, daily (see [Backups](#backups)).

**Register a trigger** in `nextron.main.build_triggers`, the only code that builds the registry
(`tests/contracts/test_job_contracts.py` checks it), after its job type is registered in
`build_job_types`:

```python
triggers.register_trigger(Trigger.SEND_DUE_MESSAGES, Every(timedelta(minutes=1)))
triggers.register_trigger(Trigger.RECONCILE_SENDS, Every(timedelta(minutes=2), at_startup=True))
triggers.register_trigger(
    Trigger.BACKUP_DB, DailyAt(backup_schedule, fallback=DailyRun(time(10, 0), "UTC"))
)
```

- `Every(period, at_startup=False)`: `period` is whole seconds from 1 minute to 31 days;
  `at_startup` also fires once right after the engine starts.
- `DailyAt(when, *, fallback)`: every day at a time read once, when the scheduler starts.
  `when()` returns a `DailyRun(at, timezone, catch_up=False)`: `at` a time in whole minutes,
  `timezone` an IANA name, and `catch_up` fires once right after the start, to make up a run
  the engine missed while it was off. If `when()` fails or returns anything else, the
  scheduler logs `trigger_schedule_failed` (WARNING, `trigger`, `code`) and uses `fallback`.
  It logs `daily_trigger_scheduled` (INFO: `trigger`, `at`, `timezone`, `catch_up`). A change
  to the time takes effect when the engine restarts. Monthly and Operator-time schedules arrive
  with their first trigger.
- A trigger that isn't a `Trigger` member (a plain string included), whose job type isn't
  registered, or that is already registered is refused (`TypeError`, `UnknownJobType`,
  `ValueError`).
- Per-inbox triggers (`sync_mailbox`) and data-dependent ones (`check_recovery`) add their own
  enqueue use case in their stories.
- **An `Every` restarts from zero at each engine start**, because schedules live in memory. A
  period longer than the engine usually runs between restarts (a day, say, on a PC shut down
  every night) never fires unless `at_startup=True`. Daily work wants `at_startup` with a
  handler that skips what is already done, or the calendar schedule its story adds.
- **Retries slow a trigger down.** A periodic job waiting out a retry backoff is still
  `queued`, so the firings meanwhile are absorbed and it retries on its backoff (30 s doubling
  to 30 minutes), not every period. Choose a periodic job type's retry limit with that in mind.

**How they fire.** The FastAPI lifespan starts APScheduler's `AsyncIOScheduler(timezone=UTC)`
right after the job runner (`scheduler_started`, with the trigger count) and stops it right
before (`scheduler_stopped`). An `Every` is an interval job with `coalesce=True`,
`max_instances=1` and a misfire grace equal to its period; a `DailyAt` is a cron job at its
hour and minute in its timezone with `coalesce=True`, `max_instances=1` and a grace of 24
hours. A firing runs on a worker thread,
never on the event loop, and never runs a handler; one that fails logs `trigger_failed` (with
`trigger` and the error `code`) and later firings still run. A firing that succeeds logs
nothing: the job's creation event records it.

**After the PC sleeps** through several firings, a trigger fires once on wake, not once per
missed firing:

- On Windows the event loop's clock (QueryPerformanceCounter) counts the time asleep, and
  Uvicorn's server loop wakes the event loop every 0.1 s, so APScheduler finds the overdue
  firings within a moment of the wake.
- With `coalesce` it runs only the latest missed slot. The next firing keeps the original
  schedule: it is due at that slot plus one period, the first slot after the wake, between 0
  and one period later (not a full period after the catch-up).
- The grace equals the period, so the catch-up normally runs. When the wake lands just before a
  slot, though, the catch-up can be past its grace by the time it runs: APScheduler logs it as
  missed (a WARNING) and the slot that has just come due fires at once instead.
- This has not yet been observed on a real PC sleep; Story 1.20's spike on the PC checks it.

**The heartbeat** (AD-20, NFR-14) proves the engine is alive. The `nextron-job-heartbeat`
thread beats once at startup and then every 30 s: it writes the `runner_heartbeat` row (owner
`ops`, exactly one row: `pid`, `started_at`, the time of this process's first beat, and
`beat_at`), then touches `<data dir>\heartbeat`, for example
`%LOCALAPPDATA%\Nextron\dev\heartbeat`. The row is written first, so a database that keeps
failing also leaves the file stale. The beat is **withheld** while:

1. `runner_down`: any lane thread or the keeper has ended;
2. `job_overran`: a running job has passed its `max_runtime` (a hung handler keeps its thread
   alive, so this is what catches it);
3. `scheduler_down`: the scheduler stopped, or the event loop doesn't answer within 10 s.

The supervisor (`scripts/run.ps1`, Story 1.21) restarts the engine when the file is more than
5 minutes old. A dead thread or a stuck event loop therefore restarts the engine about 5
minutes later. A hung handler stops the beat only once its job passes its `max_runtime`, so the
engine restarts about `max_runtime` + 5 minutes after that job started. The restart interrupts
every lane, not just the hung one; the interrupted jobs run again (handlers are idempotent).

**After a PC sleep** the heartbeat file is as old as the sleep until the next beat, up to 30 s
after the wake. A job running across the sleep counts the sleep toward its `max_runtime`
(the runner's clock counts sleep too), so the beat may stay withheld until that job returns.
`run.ps1` must allow for a wake before it calls the file stale; that is left to Story 1.21.

Logs carry no personal data and are written only when the outcome changes:
`heartbeat_withheld` (WARNING, `reason`), `heartbeat_failed` (ERROR, `code`, once per streak of
failures) and `heartbeat_resumed` (INFO, the first beat after either). The heartbeat writes no
`events` rows. The health panel shows the worker as running while the row's `beat_at` is
under 2 minutes old (see [Engine health](#engine-health)).

```powershell
(Get-Item "$env:LOCALAPPDATA\Nextron\dev\heartbeat").LastWriteTime
```

## Alerts and notifications

The engine tells you on Telegram when a job dies, and shows notices in the dashboard's
notification area (AD-15, INT-8, UX-DR5). The `notify` service (`nextron/app/notify/api.py`) owns
the `alerts` and `notifications` tables; `nextron/domain/alert.py` holds the Alert machine
(`pending`, `sent`, `unknown`, `failed`) and the rules.

**Set it up.**

1. Create a bot with Telegram's @BotFather and save its token as "Telegram bot token" on the
   Credentials page (keyring name `telegram/bot_token`). It is read on every send and never
   stored anywhere else.
2. Start a chat with the bot (or add it to a group), send the bot a message in that chat, and
   find the chat's numeric id. A private chat's id is positive, a basic group's is a shorter
   negative number, and a supergroup's or channel's starts with `-100`. A group upgraded to a
   supergroup gets a new `-100` id: alerts then fail as `telegram_refused` until you change the
   setting. To read the id without putting the token in a browser's address bar or history,
   run this in PowerShell (never open that URL in a browser):

   ```powershell
   $token = Read-Host "Bot token"
   (Invoke-RestMethod "https://api.telegram.org/bot$token/getUpdates").result.message.chat |
       Select-Object id, type, title
   Remove-Variable token
   ```

   Enter the id as "Telegram alert chat id" (`alert_chat_id`, Settings, Alerts and recipients).
   Only a numeric id is accepted: a `@name` can be handed to someone else. It is read on every
   send, so a change applies to the next alert.
3. **Outside `prod`**, alerts go through the sink: add the same chat id to "Dev and test
   allowlist" (Settings, Engine), or every alert is written to `<data dir>\sink\<date>.jsonl`
   instead of being sent (see [Dev and test sink](#dev-and-test-sink)), and still counts as
   sent.

**Raising an alert.** A service calls `notify.alert(kind, subject, text, channels)` inside its
own transaction. `channels` holds `telegram`, `dashboard` or both. Telegram gets an `alerts` row
(`pending`, with its `requested` event) and a `send_alert` job (realtime lane, priority 90,
`max_runtime` 3 minutes, retry limit 5, key `send_alert:<alert id>:0`); the dashboard gets a
`notifications` row. Each is unique per kind and subject, so raising the same alert again
changes nothing. The kind is `[a-z][a-z0-9_]{0,31}`, the subject a ULID or a token, and the text
plain, at most 4,096 UTF-16 units, with no control or format character but a line feed;
anything else raises `InvalidAlert` and nothing is written.

**What each send's outcome does.** The `send_alert` job reads the chat id, then calls the
Telegram adapter (`nextron/adapters/telegram`) between two transactions, never inside one. The
adapter POSTs `{"chat_id", "text"}` to `sendMessage`, plain text with no `parse_mode`.

| Telegram's answer | The alert |
|---|---|
| 2xx with a `message_id` | `sent`, the message id stored (none for a sink delivery) |
| 429, 5xx, or a connection failure before the request went out | stays `pending`; the job retries after 30 s, 60 s, 120 s and so on, 6 runs in all, then dies (a dashboard-only `job_dead` notice) |
| any other 4xx (400, 401, 403, 404), a 3xx, a missing or malformed bot token, or an unset chat id | `failed`, with the error code |
| a timeout or a dropped connection after the request went out, or a 2xx without a message id | `unknown`, then re-sent once 60 s later; if the resend is unknown too, it stays `unknown` and is never tried a third time |

An alert that ends `failed`, or `unknown` after its resend, adds a dashboard notification
(kind `alert_not_delivered`): "Telegram alert not delivered: <kind> <alert ULID>, <code>", or
"Telegram alert may not have been delivered: ...". Delivery is at least once: a crash between
Telegram's answer and the commit sends the alert again. A database error in the send (`database
is locked`, say) is not retried: the `send_alert` job dies (a dashboard-only `job_dead` notice)
and the alert stays `pending`, even if Telegram already took it. Each status change appends an
`events` row (entity `alerts`, actor `system:notify`). Logs carry `alert_sent` (with `sink`),
`alert_unknown` and `alert_failed` (with the `code`), and only the alert id, kind and channel:
never the text, the chat id or the token. The `httpx2` logger stays at WARNING (its INFO line
names the request URL, which holds the token), and redaction masks `/bot<token>` anyway.

**`job_dead`.** When a job dies, Telegram and the dashboard get "Job dead: <type> <ULID>,
attempts: <n>", with nothing else (no payload, no error text, no environment name). The `ops`
sweep (`DeadJobAlerts`) raises it for every `dead` job not yet alerted, right after a run that
died and on every 30 s keeper tick, so a job that died anywhere (a failed run, a reaped lease,
an unrunnable claim, a release at shutdown) is alerted once, even across a crash. A dead
`send_alert` job alerts only the dashboard, so a broken Telegram can't feed itself. A sweep that
fails logs `dead_job_alerts_failed` and the runner goes on.

**The notification area** sits under the navigation on every page. It loads once per page view
(no polling). It starts with "Engine health: <n> error(s) need(s) attention", linked to the
health panel, when there are errors (see [Engine health](#engine-health)), then shows
"Notifications (<count>)", the newest 5 undismissed notices with their time in UTC, and "and
<n> more" when there are more. Dismiss removes one (a CSRF-checked POST to
`/notifications/dismiss`); it never comes back. With nothing undismissed and no error the area
is empty and takes no space.

**Error notices.** When the dashboard refuses an action (a 403 security check, a 400 malformed
form, a 500), HTMX shows the answer's text above the notifications, as plain text, until the
page reloads. A request after the engine restarted (no session) shows "The dashboard is locked:
run nextron open in a terminal to open it again." For an HTMX `GET` (the health panel's poll,
say) the answer also carries `HX-Refresh: true`, so the page reloads and shows the locked page,
leaving no stale data on screen; an HTMX `POST` keeps the page, so nothing typed is lost. A
request that gets no answer at all (the engine is down) shows nothing.

## Metering, quotas and the AI budget

Every external call is counted, and discovery, enrichment and re-checks stop at the month's
quotas and AI budget (FR-57, AD-8, NFR-10 to NFR-12). **Sending, reply handling and unsubscribe
polling are never blocked.**

**What is recorded.** `UsageMeterPort` (`nextron/ports/usage_meter.py`) is implemented by
`nextron/db/metering.py` (`UsageMeter`), the only writer of the two usage tables (migration
`0006_1_13_metering`):

- `api_usage`: one row per HTTP request an adapter attempted, whatever its outcome: `provider`,
  `operation`, `sku` (Places Pro and Enterprise are separate SKUs), integer `units`, `ok`,
  `error_code` and `at`.
- `ai_usage`: one row per HTTP request to Claude (written by the Claude gateway; see [The
  Claude gateway](#the-claude-gateway)): `route`, `route_tuple`, `served_model`, the input,
  output, cache-read and cache-write tokens, `iterations`, `batch`, `cost_micros`
  (micro-dollars), `refused`, `error_code` and `at`.

Neither table holds personal data: `nextron/domain/budget.py` refuses any field value with `@`
or a space (`ApiCall` and `AiCall` are checked when they are built), and the meter writes no
`events` row. **Units:** Hunter's credits are stored doubled, so a find or a Domain Search is 2
units and a verification 1; every other call is 1 per `count` (an adapter passes `count=0` for
a call the provider doesn't bill). Each record commits in its own unit of work, so the usage is
kept even when the caller's work rolls back. A record that can't be written logs
`usage_record_failed` (with the provider and operation, or the route, and a `code`) and never
fails the call: the provider has already done, and billed, the work. A record made inside the
caller's open transaction writes nothing and logs the code `inside_transaction` (a port call
never runs inside a transaction).

**Adapters.** Every adapter whose `__init__` takes an `http` client takes a required keyword
`meter` and wraps its one request in `Metered(meter, provider, operation)`, which records
exactly one `ApiCall`: `ok`, or the `AdapterError`'s code. A call refused before any request (no
token, a bad chat id) records nothing. So far: Telegram (`telegram`/`send_message`) and Google
OAuth (`google_oauth`/`code_exchange`, `token_refresh` and `token_revocation`; a revoke answered
`invalid_token` is `ok`). The Claude gateway records an `AiCall` per request instead.
`tests/contracts/test_adapter_boundary.py` checks every such adapter.

**Quotas and the budget** are settings (Settings, Budget and limits), each counted over the UTC
calendar month:

| Setting | Default | Range | At the limit, stops |
|---|---|---|---|
| `quota_hunter_credits` | 50 | 0 to 100,000 | enrichment |
| `quota_prospeo_credits` | 100 | 0 to 100,000 | enrichment |
| `quota_places_enterprise` | 1,000 | 0 to 1,000 | discovery |
| `quota_places_pro` | 0 | 0 to 5,000 | discovery |
| `quota_job_aggregator` | 0 | 0 to 100,000 | discovery |
| `ai_budget_monthly` | $20 | $0 to $100 | discovery, enrichment and re-checks |
| `ai_budget_alert_pct` | 80 | 1 to 100 | nothing: it only warns |

The Places maximums are Google's free monthly allowances (March 2025 Maps pricing; check them
against your billing account), so no setting can start paid Places use. **A limit of 0 means
off:** work on it is always refused, quietly (no notice, not listed as stopped).

**Asking the budget.** Work belongs to a `BudgetClass`: `blockable` (discovery, enrichment,
re-checks and their AI routes) or `essential` (everything else). A use case reads the month's
usage with `meter.snapshot(now)` and the limits with `queries.setup.budget_limits()`, then asks
`domain.budget.allow(budget_class, usage, limits, quota="hunter")`: `essential` is always
allowed; `blockable` is refused when the AI spend is at least the budget, or the named quota's
usage at least its limit. A refused use case does nothing, and its handler returns `DEFERRED`
(see [Deferring a job](#deferring-a-job)).

**Notices.** Right after each record, the `ops` service's `Budget.check()` adds one dashboard
notification per threshold and UTC month (no Telegram message): "AI spend at 80% of $20: $16
spent in 2026-11" when the spend reaches the alert percentage, "AI budget reached: discovery,
enrichment and re-checks stopped ($20.05 of $20 spent in 2026-11)" at the budget, and "Hunter
quota reached: enrichment stopped (50 of 50 credits used in 2026-11)" for each quota reached.
A jump past both 80% and 100% gives both; raising a limit mid-month doesn't repeat a notice;
the next month starts afresh. Each new notice logs `usage_alert_raised` (with `kind` and
`subject`); a failed check logs `usage_alerts_failed` and the usage row stays.

**The Quotas and cost page** (`/quotas`, linked from the Settings page) shows, for the current
UTC month: the note that sending, reply handling and unsubscribe polling are never blocked;
"Non-essential work stopped: ..." with the reasons, or "Non-essential work is running."; the AI
spend against the budget, with the warning, reached or off line; each quota's use against its
limit (Hunter in credits, with halves) and its state (`OK`, `Reached: <work> stopped` or
`Off (quota 0)`); and every provider, operation and SKU's calls, failed calls and units.

## The Claude gateway

Every call to Claude goes through one gateway, `ClaudeGateway` in `nextron/adapters/ai` (FR-57,
AD-8, AD-17, AD-18, INT-7). It returns a typed result instead of crashing on a refusal, a cut-off
answer or bad output, and it meters every request. Only this package imports the `anthropic`
SDK (import contract 5). Nothing calls it yet: Story 2.9 adds the first caller, so `serve`
builds no AI or HTTP client.

**The call.** `LlmPort.run(route_tuple, inputs, urgency)` (`nextron/ports/llm.py`) takes:

- a `RouteTuple(route, model, effort, prompt_version, schema_version, system_prompt)`
  (`nextron/domain/ai.py`). Its `key`, `<model>:<effort>:<prompt_version>:s<schema>:<first 12 hex
  of the prompt's SHA-256>`, is what `ai_usage.route_tuple` stores. The hash comes from the text,
  so the two can't disagree. No `repr` shows the prompt;
- the untrusted `inputs`, each an `UntrustedBlock(subject, source, block_id, text)`. A call holds
  the blocks of one subject: the Lead (`lead:<id>`), or a Signal Inbox item with no Lead yet
  (`signal_item:<id>`);
- an `Urgency` (`interactive`, `soon` or `bulk`). In Epic 2 it changes nothing: every call is one
  direct Messages request.

It returns exactly one of `LlmOk(parsed)`, `LlmRefused(category)`, `LlmTruncated()`,
`LlmInvalid()` or `LlmFailed(retryable, code, status)`. Each carries the served `model` and an
`LlmUsage` that sums every request the call made. Tests use `ScriptedLlm` (`tests/fakes/llm.py`).
It first makes the gateway's caller checks, in the gateway's order: an unknown tuple, the
urgency, then the blocks. Then it answers with the next scripted result or error. Unlike the
gateway, it meters nothing.

`RouteTuple` refuses a system prompt that is blank (empty or only whitespace), because the API
answers 400 to a blank text block.

**The routes** are data, in `nextron/adapters/ai/routes.py`. The first candidate of a route is its
default, and `find(route_tuple)` returns the candidate the tuple names:

| Route | Model | Effort | Prompt | Output schema | Batchable | `max_tokens` / cap | Budget class |
|---|---|---|---|---|---|---|---|
| `extract_signals` | `claude-haiku-5-5` | `medium` | `v1` | `ExtractSignalsV1` (1) | yes | 8,000 / 16,000 | blockable (enrichment) |
| `extract_people` | `claude-haiku-5-5` | `medium` | `v1` | `ExtractPeopleV1` (1) | yes | 4,000 / 16,000 | blockable (enrichment) |
| `signal_inbox_parse` | `claude-haiku-5-5` | `medium` | `v1` | `SignalInboxParseV1` (1) | no | 2,000 / 8,000 | blockable (discovery) |

A candidate is refused unless `0 < max_tokens <= cap <= 16,000`, which keeps every request under
the SDK's non-streaming limit. No candidate or request carries `fallbacks`, because Haiku has no
server-side fallback. **The gateway never checks the budget.** The calling service asks
`domain.budget.allow(candidate.budget_class, ...)` first, and returns `deferred` when refused.

**The request.**

- The system prompt is the cached, stable prefix (`cache_control: ephemeral`).
- The user turn holds only the untrusted blocks, each as `<untrusted source="..."
  id="...">\n<text>\n</untrusted>`, separated by blank lines.
- Inside the text, a delimiter look-alike has its bracket replaced by `&lt;`. A look-alike is `<`
  or a bracket that looks like it (`＜`, `﹤`, `‹`, `〈`, `⟨` or `〈`), then optional whitespace
  and `/`, then `untrusted` in any case, in ASCII or fullwidth letters. Nothing else in the text
  changes.
- Blocks of two subjects, no blocks, or a repeated `block_id` raise `ValueError` before any
  request.
- The output schema goes in `output_config.format` (from `anthropic.transform_schema`, with
  `additionalProperties: false` on every object), never in the system text. Effort goes in
  `output_config.effort`.
- The request never sets `thinking`, sampling parameters, tools, stop sequences, `fallbacks` or
  betas, and has no assistant prefill.

**The schemas** (`nextron/adapters/ai/schemas.py`) check shape only. Quote length, the substring
check and Segment keys belong to Stories 2.9, 2.10 and 2.16. Enum values are lowercase
`snake_case`. Before validation (pydantic, strict), every string whose schema has an enum is
stripped and lowercased, so `" Staff"` becomes `"staff"`. No property may be named like
`reasoning`, `chain_of_thought` or `thought`; `tests/unit/test_ai_schemas.py` checks this.

**Outcomes**, by the answer's `stop_reason`:

| Answer | Result | `ai_usage` row |
|---|---|---|
| `end_turn`, one text block that parses as a JSON object and validates | `LlmOk(parsed)` | no error code |
| any other stop reason, no text block, a `tool_use` block, text that isn't JSON, or a schema failure | `LlmInvalid` | `llm_invalid` |
| `refusal` | `LlmRefused(category)`, and no second request | `refused` |
| `max_tokens`, the first time, when doubling still raises `max_tokens` within the cap | one retry at `min(2 × max_tokens, cap)` | `llm_truncated` |
| `max_tokens` otherwise | `LlmTruncated` | `llm_truncated` |
| HTTP 429, any 5xx, or no connection (timeouts included) | raises `AdapterError(True, "llm_unavailable")` | `llm_unavailable` |
| any other 4xx (408 and 409 included) | `LlmFailed(False, "llm_rejected", status)` | `llm_rejected` |
| anything else the SDK raises (a 3xx included) | `LlmFailed(False, "llm_unexpected", None)` | `llm_unexpected` |

Two cases raise before any request and record nothing: a tuple no candidate matches
(`AdapterError(False, "llm_unknown_route")`, a deployment mismatch) and an unset key
(`credential_not_set`). The SDK runs with `max_retries=0`, so the job runner owns every retry.

**Metering and prices.**

- **One row per request.** Each HTTP request attempted writes one `ai_usage` row through
  `UsageMeterPort` before `run` returns or raises, so a `max_tokens` retry writes two rows. The
  row holds the four token counts, `iterations` (the API's own `usage.iterations` count, else 1)
  and the served model (the response's `model`, else the tuple's).
- **A request with no response** records zero tokens, zero iterations and cost 0.
- **Cost.** `nextron/adapters/ai/pricing.py` prices each request in micro-dollars, rounded up:

  | Model | Input | Output | Cache read | 5-minute write | 1-hour write |
  |---|---|---|---|---|---|
  | Haiku 5.5, prompt of 100K tokens or fewer | $0.10 | $0.50 | $0.01 | $0.125 | $0.20 |
  | Haiku 5.5, prompt over 100K tokens | $0.50 | $2.50 | $0.05 | $0.625 | $1.00 |
  | Opus 5.5 | $4 | $20 | $0.20 | $5 | $8 |

  The prices are per million tokens, and they come from the Claude API pricing reference
  (cached 2026-11-03). **Check them against Anthropic's pricing page.**
- **Unlisted models.** A model that isn't listed is priced at the dearest card, field by field,
  so the budget errs toward stopping early.
- **Cache writes.** They are split into 5-minute and 1-hour writes by `usage.cache_creation`
  when the response gives it. Otherwise every write is priced at the 5-minute rate.

**Secrets and text.**

- The API key (`anthropic/api_key`, "Claude API key" on the Credentials page) is read on every
  call and never kept. The SDK sends it as a bearer credential.
- Prompt, block and response text never reaches an error, an `ai_usage` row, a result's `repr`
  or a log record.
- The gateway itself never logs. It keeps the `anthropic` logger at WARNING, because at DEBUG
  that logger writes whole request bodies.

**What comes later.**

- **Story 2.8** versions the prompts (`prompts/<route>/v1.md`, `manifest.lock`,
  `prompt_snapshots`) and activates one tuple per route. The system prompt text then comes from
  the snapshot the activation names.
- **Epic 4** adds:
  - the Opus drafting, `classify_reply` and `call_prep` routes, with server-side `fallbacks`;
  - `grounding_check`;
  - Message Batches;
  - `submit_batch`, `fetch_batch` and `delete_batch` on the port;
  - the per-route effort and cost sizing.

## Engine health

The health panel (`/health/panel`, "Health" in the navigation; FR-58, UX-DR29) shows on one page
whether the engine is healthy, so a stalled worker, a failing job type or a missing credential
doesn't go unnoticed until work stops. `GET /health` keeps its JSON; the panel is its own route.

**What it shows**, in order:

1. "Checked at <YYYY-MM-DD HH:MM:SS> UTC; refreshes every 30 seconds."
2. **Worker**: "Running (last beat <time> UTC)" while the `runner_heartbeat` row's `beat_at` is
   under 2 minutes old (in either direction: a beat 2 minutes or more in the future, the clock
   was set back, isn't fresh either), "Not running: last beat <time> UTC" otherwise, or "Not
   running: no heartbeat yet".
3. **Backups**: "No backup yet." or "Last backup: <YYYY-MM-DD HH:MM:SS> UTC, <verified or
   failed> (<daily, manual or before a migration>)", from the newest backup's start, followed by
   ", copied off the PC", ", off-PC copy failed" or ", off-PC not configured" once its off-PC
   outcome is recorded (marked when the backup or its off-PC copy failed), and the **Back up
   now** button (see [Backups](#backups)).
4. **Errors needing attention (<n>)**: each error's text and detail, or "No errors need
   attention." The errors are:
   - each `dead` job, oldest first: "Job dead: <type> <ULID>, attempts: <n>", with "Last error:
     <redacted last_error>" and a **Dismiss** button;
   - AI spend at the warning percentage (`ai_budget_alert_pct`, 80% by default) or more, or the
     AI budget reached (then only that one), and each quota reached; a limit of 0 (off) is never
     an error. These are the same texts as the month's notices (see [Metering, quotas and the AI
     budget](#metering-quotas-and-the-ai-budget)), and they last while the condition does;
   - each failed backup newer than the newest verified one, oldest first: "Backup failed:
     <kind> <ULID>", with "Error: <redacted error>". The list clears when a later backup
     verifies; there is no Dismiss;
   - then each failed off-PC copy newer than the newest one copied or not configured, oldest
     first: "Off-PC backup copy failed: <kind> <ULID>", with "Error: <redacted error>". It clears
     when a later backup is copied off the PC, or off-PC is no longer configured;
   - "The <name> health check could not be read (<ExceptionType>)" when one source of errors
     fails to read (logged as `health_check_failed`, ERROR, with `source`, `code` and the
     traceback); the other sources still show.
5. **Last successful runs**: a table per group (Discovery, Enrichment, Re-check, Sending, Reply
   check, Unsubscribe Endpoint poll, Backup, Other, as those types exist) with each job type's
   latest `done` time, or "never". The purge keeps each type's newest `done` row for this.
6. **Readiness (nextron doctor)**: the doctor checks (see [Check readiness](#check-readiness)),
   the same twelve ids in the same order, with "<n> ok, <n> warn, <n> fail".

**The poll.** The panel re-fetches itself every 30 seconds through HTMX
(`GET /health/panel/live`, the panel alone) while the page is open. Every answer is
`Cache-Control: no-store` (a dead job's last error is free text). After an engine restart the
poll gets the locked answer with `HX-Refresh: true`, so the page reloads to the locked page.

**Back up now.** The button posts `POST /health/backup` (CSRF-checked, no other field; anything
else is 400 `Request refused: malformed form`). It queues a manual backup and shows the panel
again with "Backup queued; its result shows here when it finishes.", or "A backup is already
queued or running." when one is. The copy is taken by the job runner's bulk lane, never in the
request; the panel's next refresh shows the result.

**Dismiss.** Dismissing a dead job deletes its `jobs` row (AD-10: `dead` rows stay until the
Operator dismisses them), logs `job_dismissed`, and shows the panel again. Its history stays in
`events`, and no `events` row is written (a dismissal is not a Job-machine transition, like the
purge). The dead-job sweep reads only `dead` rows, so it never alerts the job again. The job's
`job_dead` notification in the notification area is separate and has its own Dismiss.
Dismissing a job that isn't `dead` (or is already gone) changes nothing.

**Readiness in the engine.** The panel runs the doctor registry inside the engine, read-only,
with two checks in a no-side-effect form, because a GET never changes anything:

- `port_free` probes nothing and is `ok`: the engine serving the page holds the port.
- `internal_notify_grant` checks only what Credential Manager stores (client imported, grant
  present, not marked for re-consent) and never calls Google: a refresh would be a Google call,
  an `api_usage` row and possibly a re-consent mark. It says "internal_notify is granted; the
  health panel doesn't refresh it (nextron doctor does)". Run `nextron doctor` to prove the
  grant still refreshes.

The checks that read Credential Manager or the registry run before the panel's first database
read, so they never hold the read transaction (AD-2: every read begins with `BEGIN IMMEDIATE`);
`offpc_backup` reads the Backup passphrase before its setting, and never touches the folder.
Building the app runs no check.

**The error count.** The notification area shows "Engine health: 1 error needs attention" (or
"<n> errors need attention"), linked to the panel, whenever the panel's error list isn't empty.
The worker state isn't counted (it is at the top of the panel). The count is read on each page
view; the area isn't polled.

**Adding a source of errors.** An owner adds `queries.<owner>.health_issues()`, returning
`HealthIssue(kind, subject, text, detail=None)` values (`nextron/domain/health.py`), and
registers it in `nextron.main.build_health_issues`, the only code that builds the registry
(`tests/contracts/test_health_contracts.py`). The sources so far, in order: `jobqueue` (dead
jobs), `budget` (AI spend and quotas) and `backups` (failed backups, then failed off-PC copies).

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
  `oauthlib`, `anthropic` and `apscheduler` stay at WARNING (APScheduler would log two INFO
  lines per trigger firing).
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
uv run pytest                            # the unit, app and contracts suites; ai_eval is skipped
uv run pytest -q -n 6 --dist loadgroup   # the same suite on 6 worker processes, about 3x faster
uv run pytest -m ai_eval                 # the opt-in AI evaluation suite
uv run lint-imports                      # the import contracts on their own
```

- `tests/unit`: pure functions, the persistence layer and the composition root, with fakes,
  temporary folders and temporary database files (the `db_engine` and `units` fixtures in
  `tests/conftest.py`; temporary Alembic chains in `tests/migration_chains.py`).
- `tests/app`: the engine as a real process against temporary folders, and whole pages
  rendered by a dashboard app (untrusted text on a fixture page).
- `tests/contracts`: import contracts, the source tree, pins, the lock file and vendored assets.
- `tests/fakes`: one in-memory fake per port, listed in `tests.fakes.PORT_FAKES` (`FrozenClock`
  for `Clock`, `InMemorySecrets` for `SecretsPort`, `InMemoryAlert` for `AlertPort`,
  `InMemoryUsageMeter` for `UsageMeterPort` (every HTTP adapter takes a `meter`),
  `ScriptedLlm` for `LlmPort` (it makes the gateway's caller checks, then answers each `run`
  with the next scripted result or error),
  `InMemoryMail` for `MailPort`, `InMemoryBackupTarget` for `BackupTargetPort` (it stores
  plaintext and can be scripted to fail any call), and in `tests/fakes/google_oauth.py`
  `FakeGoogleOAuth` for
  `GoogleOAuthPort`, `FakeConsentListener` for `ConsentListener` and `FakeClientFiles` for
  `ClientFilePort`, with `id_token(...)`, an unsigned ID token builder), plus `FakeUnits`, which
  stands in for the unit-of-work factory in dashboard tests (`tests/dashboard.py`) and records
  each unit opened. `tests/dashboard.py`'s `Dashboard` builds `GoogleAccess` on these fakes with
  a recording browser and an `allowlist` field, and keeps each consent flow's job in `jobs`
  until a test calls `run_jobs()`. Its `EngineHealth` (`health`) reads the real health-issue
  registry and a fixed `readiness` list (all twelve doctor checks `ok`) unless a test passes
  its own. Its `Backups` (`backups`) queues the panel's "Back up now"; its snapshot takes no
  copy unless a test passes one (`snapshot=`), and its off-PC copies go to an
  `InMemoryBackupTarget` (`offpc_target`).
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
  on a `GoogleOAuthClient`'s first call), `nextron.adapters.telegram.default_http_client` (the
  Telegram Bot API, built on a `TelegramAlert`'s first send),
  `nextron.adapters.ai.default_http_client` (the Claude API, built on a `ClaudeGateway`'s first
  `run`) and the `build` of every `ROLE_ADAPTERS` entry raise
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

**Running in parallel.** The dev dependency `pytest-xdist` spreads the suite over worker
processes. `-n` is deliberately not in `addopts`: a plain `uv run pytest`, or a run of one file
or one test, stays serial and works exactly as before. For the full suite use
`uv run pytest -q -n 6 --dist loadgroup`. On the 16-thread build PC it takes about 75 to 90 s
instead of about 250 s, with the same result. `-n 8` saves only a few more seconds, and the PC
is shared, so 6 is the default. Each worker is its own process with its own `tmp_path`, so the
temporary `%LOCALAPPDATA%`, data dirs and databases the fixtures make are never shared. Tests
that start the engine as a process ask the OS for a free port (`free_port()`), never the fixed
one.

**The fixed-port rule.** A test must not bind a fixed port: not the `test` default 8767 (a
`serve` with no `NEXTRON_PORT`), and no other hard-coded port. On two workers at once it would
collide with itself. Take a free port instead (bind port 0, as `free_port()` does). If a test
really must use the fixed port, mark it `@pytest.mark.xdist_group("fixed_port")`, or put
`pytestmark = pytest.mark.xdist_group("fixed_port")` at the top of the module. With
`--dist loadgroup`, every test in one group runs on the same worker, one after another. Always
pass `--dist loadgroup` with `-n`, or the groups are ignored. The marker is registered in
`pyproject.toml`, so `--strict-markers` accepts it. The same goes for anything else a test
could share across processes: use `tmp_path`, never a fixed file under the source tree or the
real `%LOCALAPPDATA%`.

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
contract tests fail until you do. An HTTP adapter also takes a required keyword `meter` and
records each request it attempts (see [Metering, quotas and the AI
budget](#metering-quotas-and-the-ai-budget)). A `mail` or `alert` adapter is also registered in
`nextron.main.ROLE_ADAPTERS` (see [Dev and test sink](#dev-and-test-sink)).
