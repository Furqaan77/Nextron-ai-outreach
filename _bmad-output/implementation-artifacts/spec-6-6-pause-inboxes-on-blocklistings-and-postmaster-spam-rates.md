---
title: 'Story 6.6: Pause inboxes on blocklistings and Postmaster spam rates'
type: 'feature'
created: '2026-11-19'
status: 'done'
route: 'dispatch'
baseline_commit: 'c1a45a2d3c62e6c6a107d77a3df7038765a48d4b'
review_loop_iteration: 0
story_key: '6-6-pause-inboxes-on-blocklistings-and-postmaster-spam-rates'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-6-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-6-5-check-sending-domain-authentication-on-connect-and-daily.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A blocklisted Sending Domain or a high Gmail spam rate burns the domain if sending goes on (FR-38, FR-58, AD-9). Nothing reads a DNSBL or Google Postmaster Tools yet, and no guardrail pauses an inbox.

**Approach:** Extend Story 6.5's `check_domains` so each check also queries the configured DNSBL zones (a new `DnsPort.a`) and reads yesterday's Postmaster spam rate (a new `PostmasterPort` under the `postmaster` grant). It records both on the same `domain_checks` row. In that row's transaction it pauses the domain's `active` inboxes, raises deduplicated alerts through `notify`, and adds health issues.

## Boundaries & Constraints

**Always:**
- **Migration** `0047_6_6_reputation` (down `0046_6_11_inbox_pacing`) adds three nullable columns to `domain_checks`. Rows from before it keep NULL; every new row sets them.
  - `dnsbl_result` TEXT: the canonical JSON `{zone: outcome}`, encoded and decoded by the domain module.
  - `spam_rate` INTEGER: the ratio in parts per million. It is NULL when there is no data, and never 0 for no data.
  - `spam_rate_status` TEXT, with a CHECK of `ok`, `alert`, `pause` or `no_data`.
- **Setting** `dnsbl_zones` (LIST, Sending section), with default `("dbl.spamhaus.org",)`. It needs at least one entry, and each entry must be a zone in `domain.reputation.KNOWN_ZONES`, so no setting can switch the guard off.
- **Domain rules** live in the new stdlib module `nextron/domain/reputation.py`.
  - Each zone's outcome from its A answers:
    - no answer: `clean`;
    - any answer in `127.255.255.0/24`: `query_refused`;
    - any answer in the zone's own listing codes: `listed`;
    - anything else: `unrecognised`.
  - Listing codes:
    - `dbl.spamhaus.org`: `127.0.1.2`–`127.0.1.106`;
    - `multi.uribl.com`: `127.0.0.2`–`127.0.0.14` even;
    - `multi.surbl.org`: `127.0.0.2`–`127.0.0.254`, excluding `.1`.
  - The overall result takes the worst outcome: `listed` > `query_refused` > `unrecognised` > `clean`.
  - Spam status from ppm: `None` is `no_data`; under 1000 is `ok`; 1000–2999 is `alert`; 3000 or more is `pause`. The ppm is `round(Decimal(str(ratio)) * 1_000_000)`.
- **Ports.**
  - `DnsPort.a(name) -> tuple[str, ...]` returns sorted dotted IPv4 answers; NXDOMAIN and NODATA give `()`. It has the same errors and metering as `txt` (operation `a`), in the adapter and in `FakeDns`.
  - `PostmasterPort.spam_rate(domain, day) -> PostmasterReading(ppm | None)` reads v1 `GET https://gmailpostmastertools.googleapis.com/v1/domains/{domain}/trafficStats/{YYYYMMDD}` with a Bearer token.
    - An absent `userReportedSpamRatio`, a 404 and a 403 all give `ppm=None`.
    - The token comes from an injected `token: Callable[[], str]` (`GoogleAccess.access_token("postmaster")`), read at call time.
    - The adapter is `nextron/adapters/postmaster/` over `httpx2`, guarded by `default_http_client`, and each request is metered once as provider `postmaster`, operation `traffic_stats`.
    - Errors: `postmaster_unavailable` (retryable: network, timeout, 429, 5xx); `postmaster_rejected` (other 4xx); `postmaster_bad_response`.
    - Classify it in `live_guard`, c5 and `PORT_FAKES` (`FakePostmaster`).
- **The check** (`DomainChecks.check_domain`). Outside any transaction it queries `<domain>.<zone>` for each zone, then Postmaster for the UTC date before `checked_at`.
  - A token error coded `google_not_granted`, `google_needs_reconsent` or `google_client_not_imported` is `no_data`.
  - Any other `AdapterError` propagates and nothing is recorded, as in 6.5.
- **In the insert transaction:**
  - **DNSBL `listed`:** every `active` inbox on the domain moves to `paused` through `transition(..., pause_blocklist)`. The update is fenced on `status='active'`, and each move appends an `events` row (cause `blocklist`, `cause_ref` the check id, actor `system:sending`). Then alert `domain_blocklisted`.
  - **Spam `pause`:** the same with `pause_spam_rate`, then alert `spam_rate_pause`. If the blocklist already paused the inboxes, none are paused again.
  - **Spam `alert`:** alert `spam_rate_high`.
  - Alerts use `notify.alert(kind, "<domain id lower-case>:<UTC date>", text, (telegram, dashboard))`. The text carries IDs and counts only.
- **Health** (`queries.sending.health_issues`), one issue per listed domain, from its newest check:
  - `domain_blocklisted`, naming the domain;
  - `dnsbl_query_refused`, for `query_refused` or `unrecognised`, as a warning that pauses nothing;
  - `spam_rate_high` or `spam_rate_pause`;
  - one `postmaster_grant_missing` ("Postmaster grant missing") while a domain is listed and `GoogleAccess.check_stored_grant("postmaster")` raises.
- **Panel.** The authentication section gains DNSBL and Spam rate columns, for example "Clean", "Listed (dbl.spamhaus.org)", "Query refused", "0.12%" or "No data".
- Pausing makes `dispatch_state` and `inbox_ready` fail closed, with no change to either.

**Never:**
- Call real Google or DNS in tests.
- Pause an inbox for `query_refused`, `unrecognised` or `no_data`.
- Treat no data as 0%.
- Add `pause_cause` to `sending_inboxes`; that column is Story 6.3's.
- Pause `connecting` inboxes.
- Add a resume command.
- Send email.

### Decisions made overnight (pending user review)
1. **The pause cause lives in the `events` row** (cause `blocklist` or `spam_rate`, `cause_ref` the check id). Story 6.3 adds the `pause_cause` column, and its AC names that migration, so it can backfill from the latest pause event.
2. **Postmaster v1 `trafficStats` for the UTC day before the check.** Spike 6.1 must confirm three things: the real Workspace setup, whether v1 or v2 is current, the data lag, and whether 403 means "domain not verified". Until then, 403 and 404 read as `no_data`.
3. **Known zones only, defaulting to Spamhaus DBL.** Gmail sends from Google IPs, so only domain lists apply. Spamhaus refuses public resolvers with `127.255.255.254`, which shows as a health warning. Unrecognised answers, such as `127.0.0.1` or a hijacked NXDOMAIN, are warnings, never pauses (AD-9: no false-positive pauses).
4. **Alert dedup: one per kind, domain and UTC day.** A Recheck or an Operator resume on the same day re-pauses without a repeat alert. A condition that persists alerts again once a day, which avoids the "never again" behaviour of a permanent `(kind, subject)` key.
5. **A Postmaster outage fails the check** and nothing is recorded (retried, then a dead job with its alert), the same rule as 6.5's DNS timeout. The earlier authentication pass stands for its 26 hours.
6. **Recheck reads DNSBL and Postmaster too,** so after a delisting the Operator can resume and recheck.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Listed | DBL answers `127.0.1.2`; two `active` inboxes and one `connecting` | `dnsbl_result` `{"dbl.spamhaus.org":"listed"}`; the two inboxes are `paused` with cause `blocklist` events; one `domain_blocklisted` alert per channel; a health issue | N/A |
| Refused | `127.255.255.254` | `query_refused`; a health warning; no pause, no alert | N/A |
| Spam 0.09 / 0.10 / 0.29 / 0.30 % | ratio `0.0009`, `0.001`, `0.0029`, `0.003` | `ok`; `alert` (alert); `alert` (alert); `pause` (inboxes paused, cause `spam_rate`, alert) | N/A |
| No data | 404, 403, or no ratio field | `spam_rate` NULL, `no_data`; no alert, no pause; the panel shows "No data" | N/A |
| Grant missing | the token raises `google_not_granted` or `google_needs_reconsent` | `no_data` recorded; `postmaster_grant_missing` health issue | N/A |
| Resume while listed | an inbox set back to `active` while DBL still lists it | the next check pauses it again; no second alert that day | N/A |
| Outage | Postmaster 503, or a DNSBL `dns_timeout` | nothing recorded | job retried |

</frozen-after-approval>

## Code Map

- **Extend:**
  - `nextron/app/sending/checks.py` `DomainChecks`. Add the `postmaster`, `notify` (an `Alerts` protocol, as `app/compliance/send_gate.py:302` does) and `zones` reads through `queries.setup.get(Key.DNSBL_ZONES)`. Update the module docstring, which currently says "nothing here pauses".
  - `nextron/domain/sending_inbox.py`: use `transition` with `PAUSE_EVENTS`.
  - `db/repos/sending/inboxes.py`: add `active_ids_of(domain_id)` and a fenced `set_status`.
  - `db/repos/sending/checks.py`: `insert` and `StoredCheck` gain the three fields.
  - `db/eventlog.py` `append`.
- **Ports and adapters:**
  - `nextron/ports/dns.py` and `adapters/dns/__init__.py`: mirror `txt`.
  - `tests/fakes/dns.py`.
  - New `nextron/ports/postmaster.py`, `adapters/postmaster/__init__.py` (pattern: `adapters/telegram/__init__.py`, with `httpx2`, `Metered` and its error mapping), and `tests/fakes/postmaster.py`.
  - `tests/fakes/__init__.py` `PORT_FAKES`; `tests/live_guard.py` `LIVE_CONSTRUCTORS` and `ADAPTER_PACKAGES`; `pyproject.toml` c5 `source_modules`; `tests/contracts/test_test_isolation.py`.
- **Settings:** `nextron/domain/settings_schema.py`. Mirror `ATS_HOSTS` and its rule. A non-empty LIST default needs the "Settings form lists every setting" tests updated.
- **Reads and health:**
  - `app/queries/sending.py`: `DomainCheckView` gains `dnsbl` and `spam_rate`/`spam_status`; `health_issues`.
  - `nextron/main.py` `build_health_issues` gains `postmaster_grant_missing: Callable[[], bool] | None = None`, passed to the `sending` source. `build_app` builds `GooglePostmaster(token=lambda: google.access_token("postmaster"), meter=meter)` unless `postmaster=` is injected, and passes `notify` to `DomainChecks`.
  - Update the module docstring's Domain checks paragraph.
- **Web:** `web/inboxes_page.py` `auth_context`; `templates/inbox_auth_panel.html`; `tests/dashboard.py` (`FakePostmaster`, and Notify for `DomainChecks`).
- **Migration bookkeeping** (track rules §3): `HEAD` in `test_persistence_contracts`, `test_migrations` and `test_serve_process`; the chain probes (0048/0049); `test_source_tree.FILES`; `db/pii_registry.py` (class `none`); `db/models/sending.py`. Pattern: `versions/0046_6_11_inbox_pacing.py`.
- **Do not change:** `domain/send_gate.py`, `sending_policy.pick_dispatch`, `Sending.dispatch_state`, or 6.5's auth verdict rules.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/reputation.py` -- zones, outcomes, codec, spam status, alert subjects and texts -- one stdlib home for the guardrail rules.
- [x] `ports/dns.py`, `adapters/dns`, `tests/fakes/dns.py` -- `a()`.
- [x] `ports/postmaster.py`, `adapters/postmaster/`, `tests/fakes/postmaster.py`, the guards, c5 -- `PostmasterPort`.
- [x] `settings_schema.py` -- `dnsbl_zones`.
- [x] `db/models/sending.py`, `versions/0047_6_6_reputation.py`, `pii_registry.py`, `repos/sending/{checks,inboxes}.py` -- the columns and the pause write.
- [x] `app/sending/checks.py`, `app/queries/sending.py`, `main.py` -- the check, pauses, alerts, health and wiring.
- [x] `web/inboxes_page.py`, `inbox_auth_panel.html`, `tests/dashboard.py` -- the panel columns.
- [x] Tests:
  - `tests/unit/test_reputation.py`: every outcome and each boundary, including 999/1000/2999/3000 ppm and ratios 0.0009/0.001/0.0029/0.003.
  - `tests/unit/test_postmaster_adapter.py`: offline with `httpx2.MockTransport`; each status code; metering; the token read per call; the guard.
  - DNS `a()` tests.
  - `tests/app/test_domain_reputation.py`: every I/O row on a migrated temporary DB. Cover the re-pause, alert dedup, `dispatch_state` closed after a pause, and the event rows.
  - Panel and health tests.
- [x] Bookkeeping updates and `nextron-engine/README.md`: a "Blocklist and spam-rate guardrails" section, the adapter and the migration row.

**Acceptance Criteria:**
- Given the full suite run through the suite lock, and `uv run lint-imports`, then everything passes, every contract is kept, and `nextron.adapters.postmaster` is in c5.
- Given `grep -rn "gmailpostmastertools" nextron/`, then only `nextron/adapters/postmaster/` matches.
- Given the persistence contracts, then the new columns are classified and the migration matches the model.

## Implementation Notes

Built as specified. Where the spec left room, the code chose:

- **Migration `0047_6_6_reputation`** (down `0046_6_11_inbox_pacing`) rebuilds `domain_checks`
  in hand-written SQL rather than `ALTER TABLE ADD COLUMN`: the job contracts' schema check reads
  each `CONSTRAINT ck_... CHECK (...)` as a table-level line, and an added column's check is
  column-level. The copy is `INSERT ... (rowid, ...) SELECT rowid, ...`, so every row keeps its
  `rowid` (6.5's "newest check" is the highest `rowid`); a `batch_alter_table` rebuild would
  renumber them. `spam_rate_status` is `VARCHAR(16)` and `dnsbl_result` `TEXT`;
  `ck_domain_checks_spam_rate_status` is `spam_rate_status IN ('ok','alert','pause','no_data')`
  (NULL passes, for old rows). `tests/unit/test_migrations.py::test_0047_...` proves the rowids,
  columns, index, FK and both checks.
- **`nextron/domain/reputation.py`** holds the zones, `zone_outcome`, `overall`, the codec
  (`encode_dnsbl`/`decode_dnsbl`, canonical compact JSON with sorted keys, at least one known
  zone), `ratio_ppm` (refuses non-numbers, NaN and anything outside 0..1), `spam_status`,
  `postmaster_day`, the panel texts (`dnsbl_text`: "Clean", "Listed (<zones>)", "Query
  refused", "Unrecognised answer"; `spam_rate_text`: two decimals or "No data"), the alert and
  health kinds, `alert_subject` and the alert texts (domain id and counts only). Among several
  answers of one zone, a refused answer wins over a listing code, as the spec orders the rules.
- **`DnsPort.a`**, `DnspythonDns.a` (operation `a`) and `FakeDns(a=...)` mirror `txt`.
- **`PostmasterPort`/`GooglePostmaster`.** A domain that isn't a DNS name, or a `day` that isn't
  a `date` (a `datetime` included), is `postmaster_rejected` before the token is read; the
  domain is lower-cased without its final dot. A 2xx body over 1 MB or not a JSON object, or a
  ratio that isn't a number from 0 to 1 (a `bool` and `null` included), is
  `postmaster_bad_response`. `PostmasterReading.ppm` is `None` or an int 0..1,000,000. The
  adapter is in `test_adapter_boundary.DRIVERS` (planted token).
- **Setting `dnsbl_zones`** is the last `Key`/`SCHEMA` entry (Sending section) with rule
  `dnsbl_zones` (`reputation.is_zone_list`). A stored value that no longer parses reads as
  `DEFAULT_ZONES` in the check, so the guard never switches off.
- **`DomainChecks(clock, dns, queue, *, postmaster, notify)`** (both keyword-only and
  required). Query order: the three TXT, one A per zone (setting order), then Postmaster. The
  pause uses `repos.sending.inboxes.active_ids_of(domain_id)` and
  `set_status(inbox_id, from_status=, to_status=)` (fenced); the event's `at` is the clock's now
  in the insert transaction. The alert subject's date is `checked_at`'s UTC date. A spam pause
  alert is raised even when the blocklist already paused every inbox (its text says "0 inboxes
  paused"). `DomainCheckResult` gains `dnsbl`, `spam_rate`, `spam_status` and `paused`;
  `domain_check_recorded` logs `dnsbl`, `spam_rate_status` and `paused` too.
- **Reads.** `DomainCheckView` gains `dnsbl`, `spam_rate`, `spam_status` (all `None` for a
  pre-6.6 row) and `dnsbl_outcome`. `health_issues(now, postmaster_grant_missing=None)` adds the
  reputation issues after the `domain_auth_failing` ones; the grant callable is called only
  while a domain is listed. `domain_blocklisted` and `dnsbl_query_refused` are exclusive per
  domain (the worst outcome decides).
- **`main`.** `build_app(..., postmaster=None)`; `domain_checks` is now built after `notify`.
  `POSTMASTER_GRANT` is `str(GoogleGrant.POSTMASTER)`; `postmaster_grant_missing(google)` is
  `True` when `check_stored_grant("postmaster")` raises an `AdapterError`.
  `build_health_issues(..., postmaster_grant_missing=None)` passes it to the `sending` source.
- **Panel.** `AuthRow` gains `dnsbl`, `spam_rate`, `dnsbl_paused`, `spam_paused`; the table's
  DNSBL and Spam rate columns sit after Alignment; a pausing result has `class="auth-blocked"`;
  no check, or a pre-6.6 check, shows "Not checked yet".
- **Tests touched:** `test_domain_checks` (fixture, the DNSBL query, the `build_app` wiring
  asserting `GooglePostmaster` and `Notify`), `test_inboxes_page` (headings and rows),
  `test_health_contracts`, `test_settings_contracts`, `test_settings_schema`,
  `test_settings_page`, `test_telegram_secrecy` (the new setting), `test_adapter_boundary`,
  `test_test_isolation`, `test_source_tree`, the head bumps and probes (0048/0049).
- **Review patches.**
  - The `domain_blocklisted` and `spam_rate_pause` health texts end "; each check pauses its active inboxes", so they stay true when the check paused nothing.
  - `SPAM_HINT` and the README say the rate is the previous UTC day's, so a same-day resume or Recheck pauses again. A resume holds once a newer day's rate is below 0.30%.
  - New tests:
    - `main.postmaster_grant_missing` and the `build_app` token both ask for the `postmaster` grant;
    - an unparsable `dnsbl_zones` falls back to the DBL and still pauses;
    - `paused`, `auth_failed` and `removed` inboxes are left untouched;
    - a spam pause re-pauses a resumed inbox the same day without a second alert;
    - the rendered panel highlights the pausing cells.
  - `tests/app/test_send_gate.py` (Story 6.9): its `check` helper passes the three new `insert` fields. They record a clean DBL result and `no_data`.

## Spec Change Log

## Review Triage Log

Three layers (blind hunter, edge-case hunter, verification gap): 19 raw findings, 15 after deduplication. 6 patch entries, 0 defer, 9 rejected.

| # | Source | Finding | Verdict | Route | Evidence |
|---|---|---|---|---|---|
| 1 | VG1, BH9 | `main.postmaster_grant_missing` and the `build_app` token lambda's grant name are untested | medium | patch | Only stubs reach `build_health_issues`; the wiring test checks the type, not the `"postmaster"` grant. A wrong grant name would silently disable the spam guard (`google_not_granted` reads as `no_data`). |
| 2 | VG2, BH9 | The `_zones()` fallback to `DEFAULT_ZONES` on an unparsable stored `dnsbl_zones` is untested | medium | patch | It is the only path that keeps the "guard never switches off" promise; no test stores a bad value. |
| 3 | VG3 | The rendered panel's `auth-blocked` highlight on a pausing DNSBL or Spam rate cell is untested | low | patch | Only the `AuthRow` flags are asserted; dropping the `{% if %}` would pass. The fix is a test only. |
| 4 | ECH6, BH7 | The `domain_blocklisted` and `spam_rate_pause` health texts say "its active inboxes are paused" even when the check paused none (all `connecting`, or resumed since) | low | patch | `queries/sending.py:348,372`. A direct text correction. The "0 inboxes paused" second alert is accurate, so that part is rejected. |
| 5 | BH5 | A spam-rate pause re-applies on a same-day Recheck, because the check reads the same day's rate; nothing tells the Operator | low | patch | `postmaster_day` is the day before `checked_at`. A direct text correction to `SPAM_HINT` and the README. |
| 6 | BH9 | No test leaves `paused`, `auth_failed` or `removed` inboxes alone, and no test re-pauses after a resume on a spam `pause` with alert dedup | low | patch | `active_ids_of` filters on `active`, but only `connecting` is tested. Tests only. |
| 7 | ECH1, BH1 | A DNSBL `listed` result is discarded when Postmaster (or another zone) raises, including a non-retryable 401, 400 or bad response | medium | reject (spec) | The frozen intent says "Any other AdapterError propagates and nothing is recorded, as in 6.5", and decision 5. Changing it changes the approved spec. A dead job raises its own alert. |
| 8 | ECH2 | A 401 (revoked or expired token) is the non-retryable `postmaster_rejected` | medium | reject (spec) | The frozen error table maps every other 4xx to `postmaster_rejected`. |
| 9 | ECH3 | A zone that keeps timing out or SERVFAILing fails the whole check | medium | reject (spec) | The frozen intent propagates DNS errors, as 6.5 does. |
| 10 | ECH5, BH4 | An inbox that becomes `active` by connect or reconsent on a listed domain can send until the next check | medium | reject (spec) | The frozen intent says `dispatch_state` and `inbox_ready` don't change. The `send_gate`/`dispatch_state` are on the Code Map's do-not-change list. For 6.2 and 6.3. |
| 11 | ECH4 | `decode_dnsbl` raises on a stored value whose zone was later dropped from `KNOWN_ZONES`, which breaks the panel and health | low | reject | Only this code writes the column, and only from `KNOWN_ZONES`. The trigger needs a future code change; the fix adds a guard. |
| 12 | BH2, BH3, BH8 | Spamhaus refuses public resolvers; 403/404 read as `no_data`; URIBL/SURBL refusal codes read as `unrecognised` | medium | reject (spec) | These are decisions 2 and 3 and the frozen outcome rules. All of them fail safe as warnings. Spike 6.1 confirms them. |
| 13 | BH6 | The schema doesn't enforce `spam_rate IS NULL` exactly when `no_data` | low | reject | The domain module and the repo are the only writers and enforce it; extra CHECKs add complexity for a defect no one meets. |
| 14 | BH9, BH10 | A domain + zone name over 253 characters; spec typos ("three things" lists four; the Code Map names `sending_inbox.py`); empty logs; Verification file list | low/false | reject | Sending domains are short. The text fixes edit this spec. The logs are written now. |
| 15 | BH11, BH12 | Style (one docstring line of 117 characters; import order, which no linter enforces); `check_stored_grant` read on each health refresh | low | reject | The grant read happens only while a domain is listed; caching adds state. Cosmetic otherwise. |

## Verification

**Commands:**
- `cd e:/Nextron-epic2/nextron-engine && uv run pytest -q -n 2 tests/unit/test_reputation.py tests/unit/test_postmaster_adapter.py tests/unit/test_dns_adapter.py tests/app/test_domain_reputation.py tests/app/test_domain_checks.py tests/unit/test_inboxes_page.py tests/contracts` -- expected: all pass
- `uv run lint-imports` -- expected: every contract KEPT
