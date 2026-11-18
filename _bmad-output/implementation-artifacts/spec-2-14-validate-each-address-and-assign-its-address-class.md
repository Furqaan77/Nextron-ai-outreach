---
title: 'Story 2.14: Validate each address and assign its Address Class'
type: 'feature'
created: '2026-11-18'
status: 'done'
baseline_commit: '43f9c1b2afe7291f31cc49648341ba4ccf0259a3'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-13-find-the-primary-contact-s-email-address-in-the-cheapest-order.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Story 2.13 finds addresses and CSV import brings them in, but nothing checks them. A `guessed` address can never become `verified` or `invalid`, a dead domain looks as good as a live one, and nothing promotes a guessed address that a later crawl finds on the Lead's own site. Eligibility (Epic 3) depends on the Address Class.

Requirements: FR-15, FR-59, FR-57 · AD-4, AD-9, AD-16, INT-6, INT-11.

**Approach:** A new `verify_contact_email` job runs free offline checks (syntax, disposable list, MX) on every new address, then calls Hunter Email Verifier only for the Primary Contact's `guessed` address, once per address. Results are stored on the Contact and shown on the Lead page. A crawl promotes `guessed` addresses it finds on the site to `published`. The acceptance criteria are Story 2.14 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where the decisions below say otherwise.

## Boundaries & Constraints

**Always:**
- **The job** `verify_contact_email(contact_id)`, lanes bulk (default) and interactive, `JobGroup.ENRICHMENT`, retry 3, payload `{version, lead_id, contact_id}`. It runs, in order, and stops at the first outcome:
  1. **Skip** (job ok, nothing written): Contact missing, erased, no email, or class `invalid`.
  2. **Gate:** `compliance_api.assert_contactable(ContactSubject(contact_id), Channel.EMAIL, Purpose.DISCOVERY)`. `Blocked` records result `suppressed` with its date, leaves the class unchanged, makes no DNS or Hunter call.
  3. **Syntax** through the existing `EmailSyntaxPort` (`check_deliverability=False`). Fail → `invalid`, reason `syntax`.
  4. **Disposable:** the address's domain or its registrable domain is in `nextron/domain/data/disposable_domains.txt` → `invalid`, reason `disposable`.
  5. **MX** through `DnsPort.mx(domain)` (IDNA/ASCII form; a non-DNS name is `syntax`). No records → `invalid`, reason `no_mx`; only a null MX → `invalid`, reason `null_mx`. A retryable `AdapterError` (`dns_timeout`, `dns_unavailable`) is re-raised so the job retries; any other DNS error fails the job.
  6. Class `published` or `verified`, or a `guessed` address on a non-Primary Contact, or a `guessed` Primary already verified by Hunter (`email_verifier_at` set) → result `checks_passed`, no Hunter call.
  7. **Hunter** for the Primary's `guessed` address: first `budget.allow(BLOCKABLE, …, quota="hunter")` in its own transaction; refused → result `quota_reached`, job returns `DEFERRED`. Then `verify_email(email)` with no transaction open. `valid` → `verified` (`AddressEvent.VERIFY`); `invalid` → `invalid`, reason `verifier_invalid` (`FAIL_CHECK`); any other status → stays `guessed`. Every case records the status (normalised to `[a-z_]{1,32}`, else `other`), the date and `email_verifier_at`.
- **After any move to `invalid`** on the Primary Contact, when an FR-14 source remains untried for it (Hunter or Prospeo not in `email_search_tried`, or the domain's Domain Search not run), `find_contact_email` is enqueued once through `finding.enqueue_search`, in the same transaction.
- **Each class change** (verify, fail_check, find_on_site) goes through `domain.address_class.transition`, appends an `events` row (IDs only) and an `address` Provenance Record holding the new class: source `finder`/"Hunter Email Verifier" for Hunter results, `website`/page URL for find-on-site, and a copy of the address's latest record's source and detail for a failed offline check.
- **Columns on `contacts`** (migration `0043_2_14_email_checks`), all codes or times, `PiiClass.NONE`, research-owned, added to the tombstone's fixed columns (22) with `trg_contacts_tombstone` recreated: `email_check_result`, `email_checked_at`, `email_invalid_reason`, `email_verifier_at`. Writing a new address (`set_address`) clears all four.
- **Enqueues of `verify_contact_email`:** 2.13's `EmailFinding._write` (same transaction); the CSV import for each Contact it creates with an address; `PrimaryContacts` selection and "Make primary" for a Primary whose address is `guessed` with `email_verifier_at` unset.
- **Find on site:** after `WebsiteCrawls` stores pages (crawl and render apply paths), each `guessed` Contact address on the Lead that appears literally (casefolded) in a stored page's text or `mailto` list moves to `published`, with Quote Evidence (the line or ≤300-char window holding it, or `mailto:<address>`) inserted or confirmed through research.
- **Hunter verifier:** `EmailVerifierPort.verify_email(email) -> VerifierAnswer(status)` in `ports/email_finder.py`, implemented by `HunterFinder` (`GET /v2/email-verifier`, key header-only, read at call time). 200 → `data.status`; 222 → `unknown`; 202 (still running) → retryable `hunter_unavailable`, not metered; other mappings as 2.13. Metered `Metered(meter, "hunter", "verify")` count 1 on 200/222.
- **Lead page:** the Email cell gets a `<p class="email-check-note">`: "verified <date>", "invalid: <reason text> (<date>)", "checks passed <date>", "verification: <status> (<date>)", "verification pending: quota reached", "On the Suppression List". The table keeps 8 columns.

**Never:** SMTP probing or any connection to a mail server; a Hunter call for a `published`, `verified`, `invalid`, non-Primary or already-verified address; a second Hunter verification of the same address; a test reaching DNS, SMTP or Hunter; re-selecting the Primary Contact after an invalidation (stays deferred); changing `score_lead`'s position in the chain.

### Decisions made overnight (pending user review)
1. **No MX means invalid.** The RFC 5321 fallback to an A record is not used; a domain without MX is treated as unable to receive mail. Conservative: never send to it.
2. **Hunter verifies once per address.** `email_verifier_at` marks it; an `accept_all` or `unknown` answer is not retried, so credits are not spent twice. A new address clears the mark.
3. **The gate runs first**, before even the free checks (deferred-work 169): a suppressed address gets result `suppressed`, keeps its class, and no lookup is made.
4. **Any invalidation of the Primary's address** re-enqueues the finding when sources remain, not only a Hunter `invalid`; a dead domain also warrants trying the next source.
5. **Hunter's own `disposable`/`webmail` statuses** leave the address `guessed`, as the AC says; our own list check catches disposable domains first.
6. **The disposable list** is vendored from the `disposable-email-domains` project (CC0), pinned at commit `f4cad16eb9fa2cd9ceb352ff5a8c213a25a10083`, with that source and its SHA-256 recorded in a header and a test.
7. **DNS has no quota** (none exists in `QUOTAS`); MX lookups are metered by the existing adapter only, with no budget check before them.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected |
|---|---|---|
| Published, ok | `jane@roe.law`, MX present | stays `published`; `checks_passed`; no Hunter call |
| No MX | `jroe@dead.example`, MX `()` | `invalid`, reason `no_mx`; Provenance + event |
| Disposable | `x@mailinator.com` | `invalid`, `disposable`; no DNS query |
| DNS timeout | MX raises `dns_timeout` | job retries; nothing written |
| Primary guessed, valid | Hunter `valid` | `verified`; 1 unit metered |
| Primary guessed, invalid | Hunter `invalid`, Prospeo untried | `invalid`; `find_contact_email` enqueued once |
| Accept-all | Hunter `accept_all` | stays `guessed`; result + date recorded; a re-run makes no call |
| Non-Primary guessed | CSV address | `checks_passed`; no Hunter call |
| Quota | Hunter units ≥ limit | stays `guessed`; `quota_reached`; `DEFERRED`; page note |
| Suppressed | address on the Suppression List | `suppressed`; class unchanged; no DNS/Hunter call |
| Crawl finds it | guessed `jane@roe.law` in a new page's text | `published` with Quote Evidence and `website` Provenance |

</frozen-after-approval>

## Code Map

Paths under `nextron-engine/`.
- **Patterns:** `nextron/app/research/finding.py` (`EmailFinding`: `_allowed` :461 quota check, `_gate` :216, `_write` :621, `_quote_evidence` :660, `enqueue_search` :187, `_run` :318 for `tried_of`); `jobs/handlers.py:321` (find handler, `DEFERRED`); `main.py:1377` `build_job_types(..., finding=)` and :1641 registration; `main.py:1179` existing `DnspythonDns` instance (reuse).
- **Domain:** `domain/address_class.py` (`transition`, `AddressEvent`); `domain/email_finding.py` (`addresses_in`, `PageAddresses`); `domain/identity.py` (`registrable_domain`, `normalize_email`); `domain/budget.py` (`UNIT_WEIGHTS[("hunter","verify")]` already 1; hunter scale 2); `domain/contactability.py:144` `ContactSubject`. New pure module `domain/email_checks.py` (result/reason codes, `is_disposable`, `normalise_status`, note text keys) and `domain/data/disposable_domains.txt` (packaging as `domain/psl/` is packaged).
- **Ports/adapters:** `ports/email_finder.py` (add `EmailVerifierPort`, `VerifierAnswer`); `adapters/hunter/__init__.py` (`HunterFinder` :163, `_get` :218); `ports/dns.py` (`mx`, `MxRecord`, `RETRYABLE_CODES`); `ports/email_syntax.py`; fakes `tests/fakes/email_finder.py` (add `ScriptedVerifier`), `tests/fakes/dns.py` `FakeDns(mx=, errors=)`; `PORT_FAKES` in `tests/fakes/__init__.py`.
- **Schema:** copy `db/alembic/versions/0040_2_13_email_domains.py` (`CONTACT_FIXED`, batch add_column, drop and recreate trigger); `db/models/research.py:157` `ContactRecord`; `db/repos/research/contacts.py` (`StoredContact`, `_COLUMNS`, `set_address` :296; add `set_email_check`, `set_address_class`); `owners.py:220`; `pii_registry.py:235`.
- **Pins to bump:** `tests/app/test_erasure.py:1261` (18 → 22), `tests/app/test_contact_export.py:1055` column set; HEAD in `tests/contracts/test_persistence_contracts.py`, `tests/unit/test_migrations.py`, `tests/app/test_serve_process.py`, `tests/unit/test_serve.py` probes; `tests/contracts/test_source_tree.py` `FILES`.
- **Enqueue sites:** `finding._write`; `app/discovery/api.py:485-514` (CSV import); `app/research/people.py` `PrimaryContacts._search` :725 and `make_primary`.
- **Crawl:** `app/research/api.py` `WebsiteCrawls._apply` :1749 and `_apply_renders` :1979.
- **Job lists:** `tests/contracts/test_job_contracts.py` (:13, :581, :692, :728, handler tests), `tests/unit/test_runner_lifespan.py:239`, `tests/app/test_engine_health.py:344`, `tests/unit/test_health_page.py:290`.
- **AD-16:** `tests/contracts/test_ad16_callers.py` manifest :193 (`\w*verify\w*email\w*` in `app/`; keep the method off `EmailFinding`, whose regex :188 would claim it), `Scenario` :983, `_find` case :1274, `SCENARIOS` :1321, probe :1507.
- **Web:** `web/leads_page.py` `ContactRow` :777, `contact_row` :1088, `SEARCH_NOTES` :280; `templates/lead_contacts.html:40`; `create_app` unchanged.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/email_checks.py`, `nextron/domain/data/disposable_domains.txt` -- pure codes and rules, vendored list with header; `tests/unit/test_email_checks_domain.py` (list hash and source pin, subdomain match, status normalising).
- [x] `ports/email_finder.py`, `adapters/hunter/__init__.py`, `tests/fakes/email_finder.py`, `tests/fakes/__init__.py` -- the verifier; `tests/unit/test_hunter_adapter.py` (mock transport: every status, 202, 222, metering, no secret in errors).
- [x] `db/alembic/versions/0043_2_14_email_checks.py`, model, repo, `owners.py`, `pii_registry.py` and every pin above.
- [x] `nextron/app/research/verification.py` -- `EmailVerification.verify_contact_email`, `enqueue_verify`, the find-on-site command used by `WebsiteCrawls`.
- [x] Enqueue sites (finding, CSV import, `PrimaryContacts`), `jobs/handlers.py`, `main.py` (`build_job_types(..., verification=None)`, `build_app` injectable `verifier=`), job-name lists.
- [x] `web/leads_page.py`, `templates/lead_contacts.html` -- the note; `tests/unit/test_leads_page.py` (escaped, every note).
- [x] `tests/app/test_email_verification.py` -- every matrix row through the real handler with `FakeDns`, `ScriptedVerifier`, the enqueues, Provenance/events rows, the re-enqueue once, find-on-site through a real `WebsiteCrawls` run.
- [x] `tests/contracts/test_ad16_callers.py` -- the `verify_contact_email` scenario (Blocked: no DNS or Hunter call, no class write).
- [x] `nextron-engine/README.md` ("Validating addresses" section after "Finding email addresses", migrations row, jobs list) and `deferred-work.md` (mark 169 and 246 handled for 2.14).

**Acceptance Criteria:**
- Given the full suite, when `pytest` and `lint-imports` run, then everything passes and every contract is kept.
- Given `grep` over `nextron/`, when it looks for `contacts.address_class` writes, then only `db/repos/research/contacts.py` holds them, called only from research's add/finding/verification paths.

## Implementation Notes

Built as specified, except where noted. Where the code had to choose:

- **Domain (`nextron/domain/email_checks.py`).** `VERIFY_CONTACT_EMAIL_JOB`, `BUDGET_CLASS`,
  `HUNTER_QUOTA`, `VERIFIER_DETAIL`, `CheckResult` (`checks_passed`, `invalid`, `suppressed`,
  `quota_reached`), `InvalidReason` (`syntax`, `disposable`, `no_mx`, `null_mx`,
  `verifier_invalid`) with `REASON_TEXT` (the Lead page's reason text), `normalise_status`
  (stripped and lower-cased; `[a-z_]{1,32}` else `other`; the result codes `checks_passed`,
  `suppressed` and `quota_reached` also give `other`, so a status can't pass for a result),
  `disposable_domains()` / `is_disposable(domain)` and `mx_reason(records)`.
  `email_check_result` holds a `CheckResult` or, after Hunter answered, the normalised status
  (`valid`, `invalid`, `accept_all`, ...), so a Hunter `invalid` and a failed offline check both
  store `invalid`, told apart by the reason. `nextron/domain/email_finding.py` gains
  `address_quote(address, text, mailto)` (find-on-site's quote: the address's first occurrence in
  the text by 2.13's quote rule, else `mailto:<address>`); an occurrence is an
  `addresses_in`-style match, so `xjane@roe.law` never publishes `jane@roe.law`.
- **The disposable list** is the upstream `disposable_email_blocklist.conf` at the pinned commit,
  byte for byte under an 8-line `#` header (source, commit, CC0, the body's SHA-256
  `d0ee73b4...`). `.gitattributes` marks `nextron/domain/data/**` `-text`.
- **Port and adapter.** `VerifierAnswer(status)` (non-blank `str`) and `EmailVerifierPort` in
  `ports/email_finder.py`; `HunterFinder.verify_email` (`GET email-verifier?email=`): 200 gives
  `data.status` (else `hunter_bad_answer`, counted), 222 gives `unknown` (counted), 202 is a
  retryable `hunter_unavailable` (count 0); 403, 429, 5xx and transport errors are retryable and
  other statuses refused (as 2.13). `ScriptedVerifier` answers `unknown` once its script is empty.
- **Schema.** Migration `0043_2_14_email_checks` (down `0042_4_9_drafts`): four nullable
  `add_column`s (`email_check_result` and `email_invalid_reason` `String(32)`, the two times
  `UtcDateTime`), and `trg_contacts_tombstone` recreated with 22 fixed columns. The repo gains
  `set_address_class(contact_id, *, address_class, at)` and `set_email_check(contact_id, *,
  result, reason, at, verifier_at=None)` (`updated_at` untouched; `verifier_at` is only ever set
  here, never cleared); `set_address` clears the four columns.
- **`EmailVerification(clock, meter, queue, *, dns, email_syntax, verifier)`** in
  `nextron/app/research/verification.py`. `verify_contact_email` is a `@use_case` (as 2.13's)
  that opens one transaction for the read, the gate and the syntax and disposable checks, makes
  the MX lookup with none open, re-reads in a second (it skips, writing nothing, when the address
  or class changed, the Contact was erased or left the Lead), checks the budget in a third, calls
  Hunter with none open and applies the answer in a fourth (re-reading again). It returns the
  stored result (or `None` when skipped); the handler defers on `quota_reached`. The skip list
  adds "not on the Lead" (a Contact whose `lead_id` isn't the payload's) to the spec's four. The
  MX domain is the domain of the `normalize_email` form (IDNA/ASCII), and it must pass
  `ports.dns.is_dns_name`, else `syntax`.
- **Provenance of a failed offline check** copies the latest `address` record for that exact
  address (by `retrieved_at`, then id), at the clock's now. When the address has no record (only
  possible for rows written outside `add_contact`), the class still changes, with its `events`
  row, and no Provenance Record is written.
- **The re-enqueue** after an invalidation happens only for the Primary, on a Lead that isn't
  merged and has a `web_domain` (the paid finders need one). **Import cycle:** `research.api`
  (`WebsiteCrawls`) imports `verification` for find-on-site, and `finding` imports
  `research.api`, so `verification` imports `finding.enqueue_search` inside `_after_invalid`
  (the one function-level import, commented).
- **Find-on-site** is `verification.promote_found_on_site(lead_id, at)`, called by
  `WebsiteCrawls._apply` after the pages and contact methods are stored (never for a merged
  Lead) and by `_apply_renders` after the render counts. It scans every stored page of the Lead
  in page order (kind, then URL), makes no gate call (a class change only) and writes no
  `email_check_*` column. Its Quote Evidence is inserted or confirmed as 2.13's.
- **Enqueues.** `EmailFinding._write` (after `resolve_market`, same transaction);
  `LeadImports(clock, discovery, research, email_syntax, queue)` (a new required `queue`) for a
  row whose `add_contact` created the Contact with an email; `PrimaryContacts._search` enqueues
  `verify_contact_email` (on the lane the search would use) when the Primary has a usable
  address that `needs_verifier` (`guessed`, `email_verifier_at` NULL). `enqueue_verify`: scope
  the Contact, discriminator `tick`, no concurrency key.
- **Wiring.** `build_job_types(..., verification=None)` registers `verify_contact_email` right
  after `find_contact_email` (bulk or interactive, priority 45, 5 minutes, retry limit 3,
  enrichment); left out, its handler raises `RuntimeError`. `build_app(..., verifier=None)`: the
  verifier defaults to the same `HunterFinder`, and the MX lookups use the same `dns`
  (`DnspythonDns`) as `DomainChecks`, with `EmailValidatorSyntax()`. A `build_app` test reaches
  the injected DNS and verifier through the job.
- **The Lead page.** `leads_page.check_note(contact)` builds the note (first match wins):
  `suppressed` gives "On the Suppression List"; class `invalid` with a known reason gives
  "invalid: <reason text> (<checked date>)"; class `verified` gives "verified <verifier date>";
  `quota_reached` gives "verification pending: quota reached"; `checks_passed` gives "checks
  passed <date>"; another result with `email_verifier_at` gives "verification: <status>
  (<verifier date>)". It renders as `<p class="email-check-note">` in the Email cell, after
  2.13's search note; the table keeps 8 columns.
- **AD-16.** The `verify_contact_email` scenario runs on the Primary's `guessed` CSV address
  with a scripted MX and Hunter `valid`. Allowed: one `ContactSubject` gate call before the
  first write. Blocked: only `UPDATE contacts`, no row gained, no DNS or Hunter call, class
  `guessed`, result `suppressed`. The "built caller without a scenario" probe now uses
  `signal_inbox`. The update scan isn't extended to `set_address_class` (its callers are
  research's own verification and find-on-site paths).

**Implementation verification** (targeted, `-n 2`): `tests/app/test_email_verification.py`,
`tests/unit/test_email_checks_domain.py`, `test_hunter_adapter.py`, `test_email_finder_port.py`,
`test_leads_page.py`, `test_runner_lifespan.py`, `test_health_page.py`, `test_migrations.py`,
`tests/app/test_engine_health.py`, `test_erasure.py`, `test_contact_export.py`,
`test_email_finding.py`, `test_lead_import.py`, `tests/contracts` and neighbours (`test_contactable`,
`test_eligibility`, `test_primary_contact`, `test_website_crawl`, `test_website_render`,
`test_people_extraction`, `test_leads`, `test_signal_extraction` and others): all pass;
`lint-imports` 29 kept, 0 broken. `test_contactable`'s CSV-suppression test now expects the open
row's `verify_contact_email` job beside its crawl. No DNS, SMTP or Hunter request is made.

**Review fixes (pass 1):**
1. `promote_found_on_site` gates each `guessed` Contact (`_contactable`: `assert_contactable`,
   email, discovery); `Blocked` leaves it alone, any other non-`Allowed` answer raises
   `TypeError`. `test_ad16_callers` needed no manifest entry for it (its name patterns and
   update scan don't catch it: it writes the class, Evidence and Provenance, not
   `contacts.email`).
2. + 4. `check_note`: class `invalid` is handled first (a known reason's text, else
   "invalid (<date>)"); "verification pending: quota reached" and "verification: <status>"
   show only for a `guessed` address.
3. Step 6: a `guessed` address with `email_verifier_at` set (Primary or not) writes nothing and
   returns its stored result, so Hunter's status and dates survive a re-run (unless the stored
   result is `suppressed`, which falls through to `checks_passed`).
5. `_after_invalid`: the Domain Search clause mirrors `finding._domain_search_step` — the
   Contact's name has a last token and the domain row is missing or has neither a pattern nor
   `domain_search_at`.
6. CSV import enqueues the check whenever the row's Contact holds the row's address after
   `add_contact` (created, filled, or already its own) and never when `email_dropped`.
   `AddedContact` exposes no "filled" flag, so an unchanged re-imported address is re-checked
   too (harmless: a live job absorbs it, and a re-run keeps a verifier answer).
7. The budget transaction always `_reread`s and re-runs the gate before the Hunter call
   (`Blocked` records `suppressed`, no call); `test_ad16_callers`' verify scenario now expects
   two Contact gate calls.
8. New tests in `test_email_verification.py`: find-on-site through the render, a suppressed and
   an erased Contact left alone, an address/class changed mid-DNS/mid-verifier call skipped, a
   suppression written after the MX lookup (no Hunter call), an offline `no_mx` invalidation
   enqueuing one search, the Domain Search precondition matrix, the CSV fill/drop enqueue.
9. `test_hunter_adapter.py`: the error-status tests already ran over all three calls (`CALLS`);
   added a `verify` metering table (200, 222, 202, 403, 429, 500, 401, 404, transport, bad
   answer) and `verify` to the 404/451-refused test.
10. `_page_order`/`KIND_ORDER` removed; `promote_found_on_site` uses
    `extraction.page_order`, imported inside the function (a top-level import is a cycle:
    extraction → research.api → verification).
11. Hunter docstring reflowed to 100 columns; `main.py`'s import put in order.

**Final verification** (on the final tree, after the review fixes): the full suite through
`suite_lock.py` (track-D) passed on its first run: 17,045 passed and 9 skipped. `lint-imports`:
29 kept, 0 broken. No real DNS, SMTP or Hunter request was made.

## Spec Change Log

## Review Triage Log

**Pass 1** (blind hunter BH1-15, edge-case hunter EC1-13, verification gap VG1-3): 31 raw, 20 after dedup; 12 patched, 2 deferred, 6 rejected.

| # | Findings | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | BH1 | medium | `promote_found_on_site` never calls `assert_contactable`; a suppressed Contact's address gets new Provenance and Evidence rows. | patch: gate each Contact with `ContactSubject`, skip on `Blocked` |
| 2 | BH2, EC6 | medium | Promotion leaves `email_check_result` (`quota_reached`, a Hunter status) in place, so a `published` address shows "verification pending: quota reached". | patch: `check_note` shows the quota and verifier-status notes only for a `guessed` address |
| 3 | BH3 | medium | Step 6 records `checks_passed` for a Primary already answered by Hunter, overwriting `accept_all`/`unknown`; the AC wants the result and date recorded. | patch: keep the stored verifier result and date when `email_verifier_at` is set; update the re-run test |
| 4 | BH4, EC8 | medium | `suppressed` is stored and never re-checked when a suppression is reversed; needs a hook in compliance's reversal path (new surface). | defer |
| 5 | BH5, EC4, EC5 | medium | Hunter 202 or lasting DNS failures exhaust the 3 retries; the job dies (a `job_dead` alert follows) with no note and no automatic re-check. | defer |
| 6 | BH6, EC7 | low | `check_note` falls through to "checks passed" for an `invalid` class whose reason it doesn't know (a future hard bounce). | patch: an `invalid` class with no known reason gets a generic "invalid" note |
| 7 | BH7 | low | `_after_invalid` treats "no Domain Search yet" as untried even when a stored pattern means 2.13 will never run one, re-enqueueing a search that can only end `not_found`. | patch: mirror 2.13's real precondition (`domain_search_at` unset and no stored pattern) |
| 8 | BH8, EC9, EC10 | medium | The CSV import enqueues on the row's raw email (also when `email_dropped`) and only for created Contacts, so an address filled into an existing Contact is never checked. | patch: enqueue whenever the stored Contact gained an address (created or filled), never when dropped |
| 9 | BH9, EC2 | low | A second Hunter call needs the apply transaction to fail after Hunter answered; rare, and a fix adds a pre-call marker state. | reject |
| 10 | EC1 | medium | Between the MX transaction and the Hunter call the Contact can be erased or suppressed; the budget transaction re-reads only on refusal and never re-gates. | patch: always `_reread` and re-gate in the budget transaction before the call |
| 11 | EC3 | low | A 200 with a bad body is rare, fails the job non-retryably; a later Make primary may call again. | reject |
| 12 | EC11 | low | Stored pages are the same set 2.13's published match reads; stale-page handling belongs to the crawl purge, not this story. | reject |
| 13 | EC12 | false | Every stored address has an `address` record (2.3 `add_contact`, 2.13 `_write`), so the no-record branch is defensive only. | reject |
| 14 | EC13 | false | `find_contact_email` itself needs the Lead's web domain (`enqueue_search` takes it), so no search could run without one. | reject |
| 15 | BH10, VG1, VG2, VG3 | medium | No test covers find-on-site through `_apply_renders`, the `_reread` skip, or the re-enqueue after an offline invalidation (or the Domain Search clause). | patch: add those tests |
| 16 | BH11 | low | `verify_email` lacks the 403/429/5xx/4xx/transport/no-key/no-secret tests the other Hunter calls have. | patch: extend the parametrised tests |
| 17 | BH12 | low | `_page_order`/`KIND_ORDER` duplicate `extraction.page_order`, which 2.13 uses for the same scan. | patch: reuse it |
| 18 | BH13 | false | The vendored list was excluded from the review diff on purpose; its header holds the source, commit, licence and SHA-256, and a test checks the hash. | reject |
| 19 | BH14 | low | `sprint-status.yaml` `last_updated` lost its `MM-DD-YYYY HH:MM` format. | patch (orchestrator) |
| 20 | BH15 | low | Hunter docstring lines over 100 columns; `main.py` import out of order. | patch |

## Verification

**Commands:**
- `cd nextron-engine && uv run pytest -q -n 2 <touched test files> tests/contracts` -- expected: all pass
- `cd nextron-engine && uv run lint-imports` -- expected: all contracts kept
