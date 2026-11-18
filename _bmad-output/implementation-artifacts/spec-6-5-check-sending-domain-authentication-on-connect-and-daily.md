---
title: 'Story 6.5: Check Sending Domain authentication on connect and daily'
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: '581e81e9682457228631ff05e3e88f78b3e73b19'
review_loop_iteration: 0
story_key: '6-5-check-sending-domain-authentication-on-connect-and-daily'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-6-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** No cold email may leave from a Sending Domain that Gmail, Yahoo or Microsoft would reject or distrust (FR-64, NFR-4, NFR-20). But nothing checks a Sending Domain's DNS yet, there is no `DnsPort`, and the send gate (6.9) has no authentication fact to read.

**Approach:**
- Add `DnsPort` (TXT and MX, built for reuse by 2.14 and 6.6), with a dnspython adapter and a fake.
- Add a stdlib domain module that judges SPF, DKIM, DMARC and relaxed alignment from TXT answers.
- Add a `domain_checks` table and a `DomainChecks` service. Its `check_domains` job checks every Sending Domain daily and on the Operator's Recheck.
- Add reads that tell the inbox panel, the health panel and the future send gate whether cold sends are blocked.

## Boundaries & Constraints

**Always:**
- **Port** (`nextron/ports/dns.py`):
  - `MxRecord(preference: int, exchange: str)`. `exchange` is lower-case with no final dot. A null MX (RFC 7505, `.`) has `exchange == ""`.
  - `@runtime_checkable DnsPort`:
    - `txt(name) -> tuple[str, ...]`: each TXT record's strings joined with nothing (RFC 7208 §3.3), decoded as UTF-8 with replacement, sorted;
    - `mx(name) -> tuple[MxRecord, ...]`: sorted by `(preference, exchange)`.
  - NXDOMAIN and NODATA both answer `()`. A non-`str` name raises `TypeError`.
  - Every other failure is an `AdapterError`, with a code constant in the port module:

    | Code | Retryable | When |
    |---|---|---|
    | `dns_timeout` | yes | the query's lifetime ran out |
    | `dns_unavailable` | yes | no nameserver answered usably: SERVFAIL, REFUSED or a network error (dnspython `NoNameservers`) |
    | `dns_invalid_name` | no | the name isn't a non-empty ASCII DNS name; checked before any query |
    | `dns_failed` | no | any other dnspython error |

- **Adapter** (`nextron/adapters/dns/__init__.py`, the only module that imports `dns`):
  - `default_resolver()` returns `dns.resolver.Resolver()` built from the system configuration. It's the live constructor.
  - `DnspythonDns(*, meter, resolver=None, lifetime=5.0)` builds nothing and reads no configuration until its first query. Then it builds the resolver once, under a lock, through `default_resolver()`.
  - Each query works in this order:
    1. check the name;
    2. make it absolute (`dns.name.from_text`);
    3. inside `Metered(meter, "dns", "txt" | "mx")`, call `resolver.resolve(name, type, lifetime=lifetime, search=False)` exactly once.

    So the adapter records one `ApiCall` per query, and none for a refused name.
- **Fake and guards:**
  - `tests/fakes/dns.py`: `FakeDns(txt=None, mx=None, errors=None)`.
    - It answers from its scripts, keyed by name in lower case with the final dot stripped, and answers `()` for any name not scripted.
    - It raises the scripted `AdapterError` for a name in `errors`.
    - It records `queries` as `(type, name)` pairs.
    - It goes in `PORT_FAKES`.
  - `tests/live_guard.py`: add `"dns": Guarded(("default_resolver",))` and the matching `LIVE_CONSTRUCTORS` entry.
  - Contract c5 lists `nextron.adapters.dns`.
- **Judgement** (`nextron/domain/domain_auth.py`, standard library plus `domain.identity` and `domain.backup.latest_slot`):
  - **Enums and constants:**
    - `AuthItem`: `spf`, `dkim`, `dmarc`, `alignment`, in that order.
    - `AuthReason`: `ok`, `spf_missing`, `spf_multiple`, `spf_no_google`, `dkim_missing`, `dkim_empty_key`, `dmarc_missing`, `dmarc_multiple`, `unauthenticated`, `from_domain_mismatch`.
    - `SPF_INCLUDE = "_spf.google.com"`, `DKIM_SELECTOR = "google"`, `CHECK_WINDOW = 26 h`, `DOMAIN_CHECK_TIME = 07:00`.
    - `dkim_name(d)` is `google._domainkey.<d>`; `dmarc_name(d)` is `_dmarc.<d>`.
  - **SPF** (TXT at the domain):
    - An SPF record is one whose first term is `v=spf1`, case-insensitive.
    - No SPF record is `spf_missing`; more than one is `spf_multiple`.
    - Otherwise it's `ok` only when one term, case-insensitive, is `include:_spf.google.com` or `+include:_spf.google.com`; else `spf_no_google`.
  - **DKIM** (TXT at `dkim_name`):
    - No record is `dkim_missing`.
    - It's `ok` when some record's `;`-separated tag list has a `p` tag whose value, with whitespace removed, isn't empty; else `dkim_empty_key`.
  - **DMARC** (TXT at `dmarc_name`):
    - A DMARC record is one whose first tag is `v` with the exact value `DMARC1`; whitespace is allowed around `=` and `;`.
    - None is `dmarc_missing`, two or more is `dmarc_multiple`, and exactly one is `ok`.
  - **Alignment** (relaxed):
    - The authenticated domain is the Sending Domain, where SPF and DKIM were judged.
    - The From domains are the domain parts of the Sending Domain's non-removed inbox addresses, or the Sending Domain itself when it has none.
    - If neither SPF nor DKIM is `ok`, the result is `unauthenticated`.
    - Otherwise it's `ok` only when every From domain's `identity.registrable_domain` equals the Sending Domain; else `from_domain_mismatch`.
  - **Functions:**
    - `judge(domain, spf_txt, dkim_txt, dmarc_txt, from_domains) -> AuthJudgement`. It holds one reason per item, `.passed` (every item `ok`) and `.failing` (the failed items, in `AuthItem` order).
    - `auth_verdict(latest, now) -> AuthVerdict(blocked, failing, text)`. `latest` is `None` or the newest check: `checked_at`, `passed`, failing items. The verdict is blocked unless `latest` passed and `0 <= now - checked_at < CHECK_WINDOW`.
  - **Texts:**
    - Item labels: `SPF`, `DKIM`, `DMARC`, `Alignment`. There is a plain-English `REASON_TEXTS` entry for each reason.
    - When allowed: `Cold sends allowed`.
    - When blocked, the text is one of:
      - `Cold sends blocked: not checked yet`;
      - `Cold sends blocked: <labels joined by ", "> failing` (the latest check failed);
      - `Cold sends blocked: no check passed in the last 26 hours` (stale or future-dated).
  - **Storage helpers:**
    - `encode_details`/`decode_details`: compact JSON with sorted keys, one key per item, whose value is that item's reason. Anything else raises `ValueError`.
    - `check_missed(newest_times, now, at, timezone)`: whether any time is `None` or earlier than `latest_slot(now, at, timezone)`.
- **Schema** (migration `0028_6_5_domain_checks`, down revision `0027_2_6_contact_methods`, owner `sending`):
  - `domain_checks` columns:
    - `id` (ULID);
    - `sending_domain_id` (FK `fk_domain_checks_sending_domain_id_sending_domains`, no cascade);
    - `checked_at`;
    - `spf_ok`, `dkim_ok`, `dmarc_ok`, `aligned_ok` and `passed` (Boolean);
    - `details` (String(256)).
  - Every column is NOT NULL and PII class `none`.
  - `ck_domain_checks_passed`: `passed = (spf_ok AND dkim_ok AND dmarc_ok AND aligned_ok)`.
  - Index `ix_domain_checks_sending_domain_id_checked_at`.
  - Append-only: the repository only inserts and reads.
- **Service** (`DomainChecks(clock, dns, queue)`, `nextron/app/sending/checks.py`):
  - `@use_case check_domain(sending_domain_id) -> DomainCheckResult | None`:
    1. A transaction reads the domain and the From domains. An unknown id logs `domain_check_skipped` and returns `None`.
    2. `checked_at` is `clock.now()`.
    3. Outside any transaction, it makes three `dns.txt` queries, for the domain, `dkim_name` and `dmarc_name`. An `AdapterError` propagates, and nothing is recorded.
    4. It calls `judge`, inserts the row in one transaction, and logs `domain_check_recorded` (`sending_domain_id`, `passed`, `failing`).
  - `@command queue_check(sending_domain_id) -> bool` runs in the caller's transaction (6.2 calls it on connect). It enqueues `check_domains` with:
    - `scope=<id>` and `discriminator="tick"`;
    - `payload={"v": 1, "sending_domain_id": <id>}`;
    - the `interactive` lane.
  - `@use_case recheck(sending_domain_id) -> bool` is `False` (nothing queued) unless the domain has a non-removed inbox; otherwise it calls `queue_check`.
  - `@use_case queue_daily() -> int` queues one check per Sending Domain with a non-removed inbox, all in one transaction.
  - `schedule() -> DomainCheckSchedule(at, timezone, missed)` reads `DOMAIN_CHECK_TIME`, `sender_timezone` (UTC when unset) and `check_missed` over the listed domains.
- **Reads** (`queries.sending`):
  - `domain_auths(now)`: one `DomainAuthView(sending_domain_id, domain, latest, verdict)` per Sending Domain with a non-removed inbox, sorted by domain. `latest` is a `DomainCheckView`, with `reasons` decoded.
  - `domain_auth(sending_domain_id, now)`: the same view for one domain, or `None`. This is the send gate's read (6.9).
  - `health_issues(now)`: one `HealthIssue("domain_auth_failing", <id>, "<domain>: <verdict text>", detail=<recheck hint>)` per blocked domain.
- **Job, trigger and wiring:**
  - **Job type** `check_domains`:
    - the `interactive` lane only, priority 60, `max_runtime` 2 minutes, retry limit 3, group `sending`;
    - handler `handlers.check_domains(checks)`. A payload with `sending_domain_id` (a ULID, else `InvalidJob`) calls `check_domain`; a tick payload calls `queue_daily`.
  - **Trigger:** `Trigger.CHECK_DOMAINS` is `DailyAt(domain_check_schedule, fallback=DailyRun(DOMAIN_CHECK_TIME, "UTC"))`, registered after `purge_page_text`. `read_domain_check_schedule(units, checks)` mirrors `read_backup_schedule`.
  - **Health:** `build_health_issues` registers `IssueSource("sending", lambda: sending_queries.health_issues(clock.now()))` last.
  - **`build_app`:** it takes `dns=None` and builds `DnspythonDns(meter=meter)` unless one is injected. It builds `DomainChecks(clock, dns, queue)` and passes `create_app(..., domain_checks=...)`.
- **Panel** (inside `inboxes.html`, after the inboxes panel):
  - **Section:** `inbox_auth_panel.html`, `<section id="domain-auth" hx-get="/inboxes/auth" hx-trigger="every 30s" hx-swap="outerHTML">`, headed "Sending Domain authentication".
  - **Table columns:** Sending Domain, Last checked, SPF, DKIM, DMARC, Alignment, Cold sends, and a Recheck form per row.
    - Each item cell shows `Pass`, `Fail: <reason text>` or `Not checked yet`.
    - The Cold sends cell shows the verdict text; when blocked, it has `class="auth-blocked"`.
    - With no domains, the panel shows "No Sending Domains to check yet."
  - **Routes:** `GET /inboxes/auth` (`inbox_auth_panel`) and `POST /inboxes/auth/recheck` (`inbox_auth_recheck`), both `@personal_data(False)`: they show domain names and check results only.
  - **The POST** takes exactly one `sending_domain_id`, which must be a ULID; anything else is `MalformedForm` (400). It runs `recheck` in its own unit, then renders the section with one of:
    - `Recheck queued for <domain>; the result appears here when it lands.` (`role="status"`);
    - `That Sending Domain has no inboxes to check.` (`role="alert"`).

**Never:**
- An inbox status change, a pause or `pause_cause` (authentication is a gate predicate, AD-4).
- An ops alert, DNSBL, Postmaster (6.6), the send gate (6.9) or the "on connect" enqueue (6.2).
- A DNS query inside a transaction or in any test.
- A recorded row for a check that couldn't run.
- Nested SPF includes or redirects, or DMARC `aspf`/`adkim`.
- A second domain normaliser, or `|safe`.

### Decisions made overnight (pending user review)
1. **"On connect" is deferred to Story 6.2.** It needs the Gmail OAuth connect, which is gated on the 6.1 spike. Story 6.2 calls `DomainChecks.queue_check(sending_domain_id)` inside its connect transaction.
2. **DNS library: dnspython 2.9.2.** The spine pins it, and it's already in `pyproject.toml` and the lockfile, so there's no new dependency.
3. **`DnsPort` covers TXT and MX now.** Story 6.6 adds an A query for DNSBL. NXDOMAIN and NODATA both answer `()`; Story 2.14 decides whether it needs the RFC 5321 A/AAAA fallback.
4. **Every DNS query is metered,** as `api_usage` provider `dns`, operation `txt` or `mx`, with no quota (AD-9: every adapter records every call).
5. **A check that couldn't run records nothing.** The job retries; when its retries run out it is dead and shows on the health panel. The previous result stands until its 26 hours run out. A non-retryable DNS error fails the job the same way.
6. **Which domains are checked.** Every Sending Domain with a non-removed inbox, whatever the inbox's status, so DNS problems show before connecting. A domain whose inboxes are all removed is neither checked nor reported.
7. **What each check accepts:**
   - SPF passes only on a direct `include:_spf.google.com` in the domain's one SPF record.
   - DKIM passes on any record at `google._domainkey` with a non-empty `p=`.
   - DMARC passes on exactly one `v=DMARC1` record.
   - Alignment is relaxed: the organizational domain comes from the vendored PSL, and it's compared between the Sending Domain and each inbox's From domain.
8. **The 26-hour window is strict.** A check stamped in the future (the clock was set back) doesn't count as fresh.
9. **The daily slot is 07:00 in `sender_timezone`** (UTC when it's unset), with no new setting. If any listed domain has no check since the latest slot, the check runs once at engine start. A domain registered after start waits for the next slot or a Recheck.
10. **The interactive lane only,** so a Recheck lands in seconds rather than behind crawls. A tick fans out one job per domain, each retrying on its own. The key is `check_domains:<id>:tick`, so a Recheck while that domain's check is queued or running is absorbed by it.
11. **No ops alert.** The health issue, the panels and the notification area's health error count surface a failing domain.
12. **The health panel shows it in its error list** (`domain_auth_failing`). The per-inbox authentication column (UX-DR29) comes with the inbox section in Stories 6.2–6.4.
13. **The authentication section refreshes itself every 30 seconds,** so a Recheck's result appears without resetting the registration form.
14. **The texts say "Cold sends blocked",** as the AC and FR-64 do. The spine's `inbox_ready` applies to all kinds; Story 6.9 decides how it reads `domain_auth`.
15. **Two keyword parameters are optional,** so 25 unrelated test call sites stay untouched:
    - `build_job_types`'s `domain_checks` defaults to a provider that raises `RuntimeError` when called. `main.build_app` always passes the real service, and a wiring test proves it.
    - `build_health_issues`'s `clock=None` means `SystemClock()`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Passing | SPF `v=spf1 include:_spf.google.com ~all`; DKIM `v=DKIM1; k=rsa; p=MIIB…`; DMARC `v=DMARC1; p=none` | A row with all four `ok` and `passed`; "Cold sends allowed"; no health issue | N/A |
| SPF fails | No SPF record; two `v=spf1` records; `v=spf1 include:_spf.example.net -all` | `spf_missing`, `spf_multiple` or `spf_no_google`; "Cold sends blocked: SPF failing" | N/A |
| DKIM fails | No record; `v=DKIM1; p=` | `dkim_missing` or `dkim_empty_key` | N/A |
| DMARC fails | NXDOMAIN; only `v=DMARC2`; two `v=DMARC1` records | `dmarc_missing`, `dmarc_missing` or `dmarc_multiple` | N/A |
| Misaligned | SPF and DKIM both fail; or a From domain on another organizational domain | `unauthenticated` or `from_domain_mismatch` | N/A |
| DNS timeout | Any query raises `AdapterError(True, "dns_timeout")` | No row is written; `JobQueue.fail` re-queues the job with backoff; an earlier pass counts until its 26 h run out | Retried |
| Stale or never checked | The latest pass is 26 h old or more, or stamped in the future; no row at all | Blocked with the matching text; one health issue | N/A |
| Recheck | POST with a listed domain's id, twice | One queued interactive `check_domains` job; both answers show the queued notice | Bad or extra field: 400; unlisted domain: alert, nothing queued |
| Daily tick | Two listed domains and one whose inboxes are all removed | Two per-domain jobs | N/A |

</frozen-after-approval>

## Code Map

- **Patterns to mirror:**
  - `nextron/ports/crawler.py` and `nextron/adapters/crawler/__init__.py`: a guarded `default_*` constructor built lazily under a lock, `Metered`, and `AdapterError` codes.
  - `nextron/ports/usage_meter.py` `Metered`.
  - `nextron/ports/errors.py` `AdapterError(retryable, code, message)`.
- **Fakes and guards:**
  - `tests/fakes/__init__.py` `PORT_FAKES`, and its docstring.
  - `tests/live_guard.py`: `LIVE_CONSTRUCTORS`, `ADAPTER_PACKAGES`, and its docstring.
  - `tests/contracts/test_test_isolation.py`: around 408 (the package set) and 440 (the probe names and modules).
  - `pyproject.toml` c5 `source_modules` (around 290), and `tests/contracts/test_import_contracts.py` if it lists them.
  - `tests/contracts/test_metering_contracts.py` and `test_adapter_boundary.py`, for whatever they require of adapters.
- **Domain helpers:**
  - `nextron/domain/identity.py` `registrable_domain`.
  - `nextron/domain/backup.py:244` `latest_slot`, and `missed_daily` as a model for `check_missed`.
  - `nextron/domain/health.py` `HealthIssue`.
  - `nextron/domain/job.py`: `GLOBAL_SCOPE`, `TICK`, `Lane`, `JobGroup.SENDING`, `InvalidJob`.
- **Sending owner:**
  - `nextron/app/sending/api.py`, `app/queries/sending.py` (`LISTED_STATUSES`), `db/models/sending.py`, `db/repos/sending/{domains,inboxes}.py`, `domain/sending_inbox.py`.
  - The owner contract c7 (`c7-owner-sending`) already covers `nextron.app.sending`.
- **Service patterns:**
  - `nextron/app/ops/api.py`: `Backups.schedule` (1236), and the manual enqueue at 1224.
  - `nextron/app/research/api.py`: I/O outside the transaction, then a write unit, as `WebsiteCrawls` does.
  - `nextron/app/jobqueue/api.py` `enqueue` (269).
- **Jobs:**
  - `nextron/jobs/handlers.py`: the `backup_db` kind dispatch and `_ulid_field`. Document the new handler in the module docstring.
  - `nextron/jobs/triggers.py` `Trigger.CHECK_DOMAINS` already exists.
- **Composition root** (`nextron/main.py`):
  - `build_job_types` (1019), `build_health_issues` (1174), `build_triggers` (1187), `read_backup_schedule` (1303);
  - `build_app`'s `Sending(...)` (981);
  - the module docstring's Jobs, Triggers and Health paragraphs, plus a new "Domain checks (Story 6.5)" paragraph.
  - `tests/contracts/test_backup_contracts.py:65` calls `build_triggers`; update it.
  - `tests/contracts/test_job_contracts.py` (around 690) and `tests/contracts/test_health_contracts.py`: registrations.
- **Web:**
  - `nextron/web/app.py` `inboxes_answer` (1100) and the routes after it; update the docstring's route list.
  - `nextron/web/inboxes_page.py` (add the auth rows, notices and `submitted_recheck`).
  - `templates/inboxes.html` and `templates/inboxes_panel.html`.
  - `templates/health_panel.html`: the 30 s refresh pattern.
  - `tests/contracts/test_web_contracts.py`: routes and templates.
  - `nextron/web/static/app.css`: byte-preserving edits only (see the track rules).
- **Test wiring:** `tests/dashboard.py` (the `Sending` construction around 268, `create_app` at 389). Build `DomainChecks(self.clock, FakeDns(), self.queue)` there.
- **Migration bookkeeping** (rules §3):
  - set `HEAD` to `0028_6_5_domain_checks` in `tests/contracts/test_persistence_contracts.py`, `tests/unit/test_migrations.py` and `tests/app/test_serve_process.py`;
  - bump the chain probes;
  - add to `test_source_tree.FILES`, `db/owners.py`, `db/pii_registry.py` and `db/models/__init__.py`.
- **Migration pattern:** `db/alembic/versions/0027_2_6_contact_methods.py` (the header, hard-coded SQL and the raising `downgrade`).

## Tasks & Acceptance

**Execution:**
- [x] `nextron/ports/dns.py`, `nextron/adapters/dns/__init__.py`, `tests/fakes/dns.py`, `tests/fakes/__init__.py`, `tests/live_guard.py`, `pyproject.toml` (c5) -- the port, adapter, fake and guards -- reusable DNS access.
- [x] `nextron/domain/domain_auth.py` -- the judgement, verdict, texts, details codec and `check_missed` -- one stdlib home for FR-64 rules.
- [x] `nextron/db/models/sending.py`, `db/models/__init__.py`, `db/alembic/versions/0028_6_5_domain_checks.py`, `db/owners.py`, `db/pii_registry.py`, `db/repos/sending/checks.py` -- the table and its append-only repository (`insert`, `newest(domain_id)`, `newest_times(domain_ids)`).
- [x] `nextron/app/sending/checks.py`, `nextron/app/queries/sending.py` -- `DomainChecks` and the reads.
- [x] `nextron/jobs/handlers.py`, `nextron/main.py` -- the job type, trigger, schedule reader, health source and `build_app` wiring.
- [x] `nextron/web/app.py`, `nextron/web/inboxes_page.py`, `templates/inboxes.html`, `templates/inbox_auth_panel.html`, `static/app.css`, `tests/dashboard.py` -- the section, its two routes, `create_app(domain_checks=...)`.
- [x] Tests:
  - `tests/unit/test_domain_auth.py`: every reason, the alignment cases, the verdict edges (25:59, 26:00, a future stamp, none), the texts, the details round trip and refusal, and `check_missed`.
  - `tests/unit/test_dns_adapter.py`: offline, with a recording resolver returning `dns.rdata.from_text` rdatas and raising real dnspython exceptions. It covers the joined and sorted TXT, MX sorting and the null MX, each error code, one metered call per query and none for an invalid name, nothing built until the first query, and the guard refusing `default_resolver`.
  - `tests/app/test_domain_checks.py`: every I/O row on a migrated temporary DB with `FakeDns` and `FrozenClock`, the timeout re-queued through `JobQueue.fail`, `queue_daily`, `recheck`, `schedule`, `health_issues`, `domain_auth`, the CHECK and FK raising `IntegrityError`, and the handler dispatch with `InvalidJob`.
  - `tests/unit/test_inboxes_page.py` additions: the section, Recheck success, unlisted, 400s, the 403 without CSRF, escaping, and the partial showing a newly recorded result.
  - The wiring test: `build_app` builds `DomainChecks` with its `DnspythonDns`.
- [x] Contract and bookkeeping updates from the Code Map, and `nextron-engine/README.md` (a "Sending Domain authentication" section, the DNS adapter, the migrations row and the daily trigger).

**Acceptance Criteria:**
- Given the full suite run through the suite lock, and `uv run lint-imports`, then everything passes and every contract is kept.
- Given `grep -rn "import dns\|from dns" nextron/`, then only `nextron/adapters/dns/` matches.
- Given the persistence contracts, then `domain_checks` is mapped to `sending`, classified, and the migration matches the model.

## Implementation Notes

Built as specified. What the spec left open, and where the code had to choose:

- **The name rule lives in the port.** The error contract allows only `raise AdapterError` under
  `nextron/adapters`, so the `TypeError` for a non-`str` name comes from
  `nextron.ports.dns.is_dns_name(name)`, which both `DnspythonDns` and `FakeDns` call first. A DNS
  name is labels of 1 to 63 `[A-Za-z0-9_-]` characters separated by single dots, an optional
  final dot, at most 253 characters without it; anything else is `dns_invalid_name`, and so is a
  name `dns.name.from_text` refuses. `FakeDns` applies the same rule (an invalid name raises
  `dns_invalid_name` and isn't recorded in `queries`).
- **Adapter errors.** `dns.exception.Timeout` (so `LifetimeTimeout` too) is `dns_timeout`;
  `dns.resolver.NoNameservers` and an `OSError` (a network error dnspython didn't wrap) are
  `dns_unavailable`; any other `DNSException` is `dns_failed`. A resolver `default_resolver()`
  can't build (`NoResolverConfiguration` or an `OSError`) is the retryable `dns_unavailable`
  (review fix P4), raised before `Metered`, so it records nothing. Every error is raised
  `from None` with the adapter's own text.
  The adapter passes `dns.rdatatype.TXT`/`MX` to `resolve`.
- **`AuthJudgement`** has the fields `spf`, `dkim`, `dmarc`, `alignment` (each an `AuthReason`,
  checked against `ITEM_REASONS[item]`), plus `reasons` (a dict in `AuthItem` order), `passed`
  and `failing`. `judge` treats an empty `from_domains` as `(domain,)`, and a From domain with no
  registrable domain (`IdentityError`) as misaligned. DKIM's `p` tag is matched case-sensitively
  (RFC 6376 §3.2), so `P=` doesn't count.
- **`auth_verdict(latest, now)`** reads `latest.checked_at`, `latest.passed` and
  `latest.failing` (the `LatestCheck` protocol). A failed latest check names its failing items
  whatever its age.
- **`decode_details`** refuses anything `encode_details` wouldn't write byte for byte (key order
  and compact separators included), a reason of another item, and a non-`str`, all as
  `ValueError`. `REASON_TEXTS[ok]` is "Passes"; the panel shows `Pass` itself.
- **Constants** in `domain_auth`: `CHECK_DOMAINS_JOB`, `SENDING_DOMAIN_ID_FIELD`,
  `PAYLOAD_VERSION`, `DOMAIN_AUTH_FAILING`, `RECHECK_HINT` ("Fix the domain's DNS records with
  its DNS host, then press Recheck on the Sending Inboxes page.") and `DomainCheckSchedule`
  (beside `BackupSchedule`'s pattern).
- **Repositories.** `checks.py` has `insert`, `newest` (the last inserted row, by SQLite
  `rowid`) and `newest_times` (each domain's last inserted row's `checked_at`; review fix P1). `domains.py` gains `by_id` and
  `with_inboxes(statuses)` (an `EXISTS` subquery, by `domain`); `inboxes.py` gains
  `addresses_of(sending_domain_id, statuses)`.
- **`DomainChecks`.** `check_domain` returns `DomainCheckResult(check_id, sending_domain_id,
  domain, checked_at, judgement)` with `passed`/`failing`; the From domains are the stored
  addresses' parts after the last `@`. `domain_check_recorded` logs `failing` as the failed items
  joined with `,` (the JSON log drops non-scalar extras). `queue_check` returns whether a new
  job was created; `recheck` returns `True` whenever the domain is listed (a new job or the live
  one), so a second Recheck still shows the queued notice, and logs `domain_recheck_queued`
  (`sending_domain_id`, `job_created`); `queue_daily` logs `domain_checks_queued` (`count`).
  `schedule()` catches only `SettingNotSet`, as `Backups.schedule` does (an invalid stored zone
  makes `DailyAt` fall back to 07:00 UTC). The web has no clock, so `DomainChecks.auths()` is the
  page's read: `queries.sending.domain_auths(clock.now())`.
- **`queries.sending`.** `DomainCheckView(id, checked_at, spf_ok, dkim_ok, dmarc_ok, aligned_ok,
  passed, reasons)`; its `failing` comes from the flags. `domain_auth` returns `None` for a
  non-ULID, an unknown id, or a domain with no listed inbox.
- **`main`.** `build_job_types(..., domain_checks=None)` uses `no_domain_checks`, a provider that
  raises `RuntimeError("this job type registry was built without domain_checks")`.
  `build_health_issues(*, budget, clock=None)` reads `(clock or SystemClock()).now` when the
  source is read; `build_app` passes its clock, so it still builds one `SystemClock`.
  `build_triggers` takes a required `domain_check_schedule`; every caller was updated.
  `create_app` takes a required `domain_checks`.
- **Web.** `GET /inboxes` renders the section through `inbox_rows.auth_context(...)` merged into
  the page context; `POST /inboxes` still renders only `inboxes_panel.html` (the section picks a
  new domain up on its next 30 s refresh). The Recheck notice names the domain read back through
  `auths()` (the id itself if it vanished meanwhile). Last checked is
  `YYYY-MM-DD HH:MM UTC`. The table has `class="auth-table"`, the Recheck form
  `hx-sync="this:drop"` and the button `aria-label="Recheck <domain>"`. `app.css` gains
  `.auth-table` and `.auth-blocked`, appended after `.inbox-note` with LF endings, the file's
  first 293 CRLF lines untouched.
- **Tests touched for the new job type, trigger and source:** `test_job_contracts`,
  `test_backup_contracts`, `test_recovery_contracts`, `test_health_contracts` (five sources),
  `test_engine_health` and `test_health_page` (a Sending group with `check_domains`),
  `test_runner_lifespan` and `test_serve_process` (five triggers, a third
  `daily_trigger_scheduled` at 07:00 UTC, no catch-up), `test_inboxes_page`'s hostile-label
  test (two `hx-post` forms once a domain is listed), the head bumps (`test_persistence_contracts`
  with probes 0029/0030, `test_migrations`, `test_serve_process`, `test_serve`'s `0029_more`),
  `test_source_tree.FILES`, `test_test_isolation`, `test_web_contracts` and `tests/dashboard.py`
  (`dns` field, `domain_checks`, the health registry on the dashboard's clock).

### Review fixes (pass 1)

- **P1** `repos/sending/checks.py`: `newest` is the last inserted row (`rowid` descending) and
  `newest_times` each domain's last inserted row's `checked_at` (`rowid IN (SELECT MAX(rowid) ...
  GROUP BY sending_domain_id)`), so a future-stamped check (the clock set back) never hides a
  later one; the table is append-only and the engine never runs `VACUUM`.
  `domain_auth.check_missed` also counts a newest time later than `now` as missed. Tests: a
  future-stamped pass followed by a later failure; `check_missed` with a future time.
- **P2** `DomainChecks.check_domain`: a known domain with no listed inbox is skipped like an
  unknown id (`domain_check_skipped`, `None`, no DNS query, nothing recorded). Test added.
- **P3** `DnspythonDns`: after `dns_timeout` or `dns_unavailable` a built resolver is forgotten
  under the lock (`_forget`, only while it is still the current one), so the next query rebuilds
  it through `default_resolver()`; an injected resolver is kept. Tests: each retryable failure
  replaces a built resolver; a non-retryable one keeps it; an injected one is kept.
- **P4** `DnspythonDns._live_resolver`: a resolver that can't be built is the retryable
  `dns_unavailable` (still before `Metered`, nothing recorded or kept). The test now covers
  `NoResolverConfiguration` and `OSError` and the rebuild on the next query.
- **P5** `queries.sending.domain_auth` docstring and the README: `None` means cold sends are
  blocked; the send gate fails closed.
- **P6** `inbox_auth_panel.html` hint: 07:00 in the Sender's timezone (set on the Settings page; a
  change takes effect at the next engine start). No test asserted the hint.
- **P7** `test_build_app_builds_domain_checks_on_its_dnspython_dns` spies `main.build_job_types`
  and asserts `kwargs["domain_checks"]() is checks`.
- **P8** `test_the_schedule_reads_each_domains_newest_check`: two domains, each with a check
  before the latest slot; `missed` stays `True` until both have a newer one after it, then
  `False`.
- **P9** The recheck-twice test asserts `domain_recheck_queued` with `job_created` `True`, then
  `False`; the daily-tick test asserts `domain_checks_queued` with `count` 2.

**Verification after the fixes.** The full suite through the suite lock on the final tree: 12928 passed, 12 skipped. `uv run lint-imports`: 29 contracts kept, 0 broken. `grep -rn "import dns\|from dns" nextron/` matches only `nextron/adapters/dns/`.

## Spec Change Log

## Review Triage Log

**Pass 1** (blind hunter BH1–BH13, edge-case hunter EC1–EC12, verification gap VG1–VG4): 29 raw findings, 19 after grouping. 9 patched, 0 deferred, 10 rejected (6 of them only because the fix would change the approved spec).

| # | Findings | Verdict | Evidence | Route |
|---|---|---|---|---|
| P1 | BH1, VG4, EC4: a check stamped in the future hides every later check | medium | `checks_repo.newest` orders by `checked_at`, then a ULID that also comes from the wall clock. After the clock is set back, a later-inserted check sorts below the future row. Once real time passes that stamp, the verdict reads an old pass over a newer failure (a cold send is allowed). `newest_times` returns the future max, so `check_missed` is False and nothing catches up at start. | patch: the newest check is the last one inserted (SQLite `rowid`; the table is append-only and the engine never vacuums); `check_missed` also counts a newest time later than `now` as missed |
| P2 | BH2, EC5: a domain whose inboxes are all removed is still checked and recorded | low | `check_domain` skips only an unknown id. A queued or retrying job that outlives the last inbox spends three DNS queries and records a vacuous pass, against decision 6. The fix is a direct correction. | patch: no listed inbox is skipped like an unknown id (`domain_check_skipped`, `None`, no query) |
| P3 | BH5, EC7: the resolver is built once and never refreshed | medium | `_live_resolver` caches the first `dns.resolver.Resolver()`, which copies the system's nameservers. After a network or VPN change, every later query goes to stale servers until a restart, and Recheck can't help. | patch: after a retryable failure, a resolver that wasn't injected is forgotten, so the next query rebuilds it |
| P4 | BH6, EC8: a resolver that can't be built is the non-retryable `dns_failed` | medium | `NoResolverConfiguration` or an `OSError` at build time (an engine started at logon before the network is up, exactly when the start-up catch-up runs) kills the job at once. "No nameserver" is `dns_unavailable`'s condition. | patch: a build failure is the retryable `dns_unavailable`, still before `Metered` |
| P5 | BH11: the send gate's read doesn't say `None` means blocked | low | `domain_auth` returns `None` for an unlisted domain, and nothing tells Story 6.9 to fail closed. Fixed by a docstring and README sentence. | patch: document that `None` must be treated as blocked |
| P6 | BH13: the hint says "07:00" with no timezone, while Last checked is in UTC | low | `inbox_auth_panel.html`'s hint omits that the slot is 07:00 in the Sender's timezone, and that a change takes effect at the next engine start. A direct text correction. | patch: hint text |
| P7 | VG1: nothing checks that `build_app` wires `check_domains` to its `DomainChecks` | medium | `domain_checks` is optional. Dropping `domain_checks=lambda: domain_checks` from `build_app` falls back to `no_domain_checks`, every test still passes, and every production check dies. Pre-verified gap. | patch: spy `build_job_types` in the wiring test and assert the provider returns the built `DomainChecks` |
| P8 | VG2: `newest_times` is tested with one check per domain only | low | Switching `MAX` to `MIN` passes every test. P1 rewrites this read anyway. | patch: a schedule test with an old check before the slot and a newer one after it (`missed` False) |
| P9 | VG3: `domain_recheck_queued` and `domain_checks_queued` are never asserted | low | Searched `tests`: only `domain_check_recorded` and `domain_check_skipped` are asserted. Pre-verified gap. | patch: caplog assertions (`job_created` True then False; `count` 2) |
| R1 | BH3, EC3: subdomain inboxes are judged at the Sending Domain's names, and alignment can only fail on corrupt data | medium | Real for an inbox like `a@mail.example.com`, whose envelope SPF and Workspace DKIM live at the subdomain. But the AC and the frozen Boundaries name the checks at `<domain>` (the Sending Domain) with relaxed alignment (decision 7). | reject: the fix changes the approved spec (reported for Story 6.2) |
| R2 | BH4: a DNS outage at 07:00 exhausts the 3 retries in about 3.5 min, and the domain is blocked until the next 07:00 | medium | Retries wait 30, 60 and 120 s. The dead job raises a `job_dead` ops alert and shows on the health panel, and Recheck clears it. A longer schedule changes the frozen "retry limit 3". | reject: changes the approved spec (reported) |
| R3 | BH7, EC2: DMARC passes without a valid `p=` | medium | RFC 7489 discards such a record unless `rua` is present. The frozen rule is "exactly one `v=DMARC1` record", as in the AC. | reject: changes the approved spec (reported) |
| R4 | BH8, EC1: SPF passes `include:` after `all`, `+all`, and more than 10 lookups | medium | Receivers never reach terms after `all`. The frozen rule is "one term is `include:_spf.google.com`". | reject: changes the approved spec (reported) |
| R5 | BH9, EC6: a Recheck during a running or backing-off check is absorbed, and the notice still says queued | low | That is decision 10 ("a Recheck while that domain's check is queued or running is absorbed by it"), with the spec's exact notice text. | reject: changes the approved spec |
| R6 | BH10: registering an inbox doesn't queue a check | low | Decision 9: "A domain registered after start waits for the next slot or a Recheck"; on-connect is Story 6.2 (decision 1). | reject: changes the approved spec |
| R7 | BH12, EC9: an undecodable `details` row makes `/inboxes` fail | low | Only `encode_details` writes `details`, through the one repository. A bad row needs a raw database edit or a future story that changes `AuthItem` without migrating. Unlikely in use, and the fix adds guards. | reject |
| R8 | EC10: a newly registered domain appears in the authentication table only at the next 30 s refresh | low | Cosmetic and documented in Implementation Notes; the fix adds an out-of-band swap. | reject |
| R9 | EC11: a poll swap during a Recheck POST can drop the queued notice | low | Needs a click inside the poll's response window. The job is still queued, and the next poll shows the result. The fix adds request-sync configuration. | reject |
| R10 | EC12: a resolver that can't be built records no usage row | false | Consistent with `Metered`: "a call refused before any request records nothing". No request is made, so there is nothing to meter. | reject |

## Verification

**Commands:**
- `cd e:/Nextron-track4/nextron-engine && uv run pytest -q tests/unit/test_domain_auth.py tests/unit/test_dns_adapter.py tests/app/test_domain_checks.py tests/unit/test_inboxes_page.py tests/contracts` -- expected: all pass
- `uv run lint-imports` -- expected: every contract KEPT
- `grep -rn "import dns\|from dns" nextron/` -- expected: only `nextron/adapters/dns/`
