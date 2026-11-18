---
title: 'Story 3.7: Show Provenance and the Eligibility basis on the Lead page'
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: 'b16d0f91906ac96d5eaf1fcc3a768901bf24e80b'
review_loop_iteration: 0
story_key: '3-7-show-provenance-and-the-eligibility-basis-on-the-lead-page'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** FR-51 needs every Contact and email address to answer "where did you get my details?" and, once enrolled, "on what basis?" (the UK legitimate-interests basis included). The Lead page (2.4) lists raw Provenance Records but not the Market, timezone, their bases or fallback notes. Nothing records an Eligibility basis yet, `provenance_records.basis_rule` (64) is too short for a UK basis (71–108), and a Contact with no source record still counts as Eligible.

Requirements: FR-51 · AD-7, AD-13, AD-16 · NFR-3 · UX-DR4, UX-DR17.

**Approach:**
- `Research.record_eligibility_basis` inserts an `eligibility_basis` Provenance Record. It is the recorder Story 4.8's `enroll` calls. A migration widens `basis_rule`.
- A pure `domain.contacts.provenance_recorded` check feeds a new `provenance_missing` Eligibility reason, and the Lead page shows it in red.
- The Lead page shows each Contact's Market, timezone, their bases and notes, linked sources, each address's history, and the recorded bases (or "Basis not yet recorded (recorded at enrollment)") next to the live Decision.

The acceptance criteria are Story 3.7 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**
- **`provenance_recorded(email, records) -> bool`** (`nextron/domain/contacts.py`, stdlib only). `records` are any objects with `subject`, `source_kind` and `address`, typed with a `Protocol`. It is true when both hold:
  - some record is a `contact` record whose kind isn't `eligibility_basis`;
  - `email` is `None`, or some `address` record (kind not `eligibility_basis`) has `address == email`.
- **Eligibility** (`nextron/domain/eligibility.py`):
  - `ReasonKind` gains `provenance_missing`, right after `address_not_sendable`.
  - `EligibilityContact` gains a **required** `provenance_recorded: bool`, after `contactable` (a non-bool raises `TypeError`). `evaluate` adds `provenance_missing` when it is false.
  - Its text is `PROVENANCE_MISSING_TEXT` = "Provenance missing: no Provenance Record shows where this Contact or its email address came from".
  - `MAX_BASIS_RULE_LENGTH` = 128. `Decision` refuses a longer `basis_rule` with a `ValueError` that names no value.
  - `basis_country(basis_rule) -> str | None` returns `US` for `US_BASIS`, `GB` for the `UK_BASIS` shape (any non-blank label), and `None` otherwise.
  - Update the module docstring.
- **`Compliance.evaluate_eligibility`** passes `provenance_recorded(contact.email, queries.research.provenance(contact.id))`.
- **`@command Research.record_eligibility_basis(contact_id, decision, campaign_id, lia_ref, lia_date, at) -> str`** (the new record's id). It checks everything first; any refusal writes nothing. The checks:
  - `TypeError` for a wrong type: `decision` not a `Decision`, `campaign_id` or `contact_id` not a `str`, `lia_ref` not a `str` or `None`, `lia_date` not a `date` or `None` (a `datetime` is refused), `at` not an aware `datetime`.
  - `ValueError` for a `decision` that isn't `eligible`.
  - `LookupError` for an unknown Contact or Campaign (`queries.setup.campaign`).
  - `ValueError` when the Contact's `market_id` isn't the Campaign's `market_id`, or `basis_country(decision.basis_rule)` isn't that Market's `country_code` (read from `queries.setup.markets()`).
  - `ValueError` on the LIA:
    - when `needs_lia(country)` is true, both LIA values must be set and must equal the Campaign's `lia` (`ref` and `date`);
    - otherwise both must be `None`.

  The record it inserts:
  - `subject` `contact`, `source_kind` `eligibility_basis`, `source_detail` = the basis rule;
  - `retrieved_at` = `basis_recorded_at` = `at`;
  - `basis_rule`, `basis_campaign_id`, `lia_ref` and `lia_date` from the arguments;
  - `address`, `address_class` and `notes` NULL.

  Each call inserts a new record. Nothing is updated or removed, and nothing is logged with a value.
- **`add_contact`** refuses `source=ProvenanceSource.ELIGIBILITY_BASIS` with a `ValueError` before any write. `provenance.first_contact_record` ignores `eligibility_basis` records, so Market notes never land on one.
- **Migration** `0024_3_7_basis_rule_width` (down `0023_4_11_footer_snapshots`).
  - It rebuilds `provenance_records` with `batch_alter_table(recreate="always", copy_from=<0015's exact table>)`, per 0019's pattern.
  - `basis_rule` becomes `String(128)`. Every row and every check, the foreign key and the index are kept.
  - Set the models' `BASIS_RULE_LENGTH = 128`. Update the `HEAD` constants and the chain probes (`0024`→`0025`, `0025`→`0026`), plus `test_source_tree` `FILES`. `owners.py` and `pii_registry.py` need no change.
- **The Lead page** (`leads_page.py`, `lead.html`, `app.py` `lead_page`).
  - `lead_context` gains a 7th argument `markets` (`SeededMarket`s). The route passes `setup_queries.markets()`, or `()` on `InvalidMarkets`.
  - **Under each Contact (`provenance-row`):**
    - a `<dl class="provenance-facts">`:
      - Market: "<name> (<basis label>)", "No Market (<basis label>)", "Not resolved yet", or "Unknown Market (<basis label>)" for an id it can't name;
      - Timezone: "<tz> (<basis label>)", or "None (<basis label>)" / "Not resolved yet";
      - Notes: each line of every record's `notes`, in record order.

      The labels are in Design Notes, and an unknown stored value renders as its own text.
    - "Provenance missing" (`<p class="provenance-missing">`, red) when `provenance_recorded` is false. It replaces "No Provenance Record.".
    - The records table: every record's Detail renders through `safe_link`, and an address record names its address.
  - **In the `eligibility-row`, after the reasons:**
    - with no `eligibility_basis` record: `<p class="eligibility-basis">Basis not yet recorded (recorded at enrollment)</p>`;
    - otherwise a `<ul class="eligibility-bases">`, oldest first, one item per record:
      - "Basis: <rule>, recorded <time>";
      - plus "Legitimate interests: LIA <ref> dated <YYYY-MM-DD>, recorded <time>" when `lia_ref` and `lia_date` are set.

      `<time>` is `YYYY-MM-DD HH:MM UTC` of `basis_recorded_at` (else `retrieved_at`), and a NULL rule shows "—".
  - The `operator` source's label becomes "Manual (entered by Mac)".
  - Everything stays inside the existing `hx-disable` table, through `|plain`/`safe_link`. `app.css` gains `.provenance-missing` (`var(--refused)`, bold) and a small `.provenance-facts` rule, edited byte-preserving.
- **Docs.** README: "CSV import, Contacts and Provenance" (the recorder and the refusal), "Eligibility" (the new reason) and "Leads list and Lead page"; the docstrings of every touched module.

**Never:**
- No new column, table, setting, route, job, adapter, notification or dependency.
- No Enrollment, `enroll` or caller of the recorder (that is 4.8's).
- No change to `assert_contactable`, `suppress` or 3.2's resolver beyond `first_contact_record`.
- No `eligib` table or column name (3.6 contract c).
- No HTMX action on the page.
- No edits outside `nextron-engine/`, except this spec, `sprint-status.yaml` and `deferred-work.md`.

### Decisions made overnight (pending user review)

1. **"Provenance missing"** means no `contact` source record, **or** an email without an `address` record for that exact address. This fails closed: we must be able to say where the address we would email came from.
2. **The fact is required,** with no default, so no caller of `EligibilityContact` can forget it. `provenance_missing` sits after `address_not_sendable` (data about the Contact).
3. **The recorder trusts the caller's Decision,** but refuses anything that would make a false audit record: an ineligible Decision, a Campaign in another Market, a basis for the wrong country, or an LIA that isn't exactly the Campaign's recorded one. 4.8 passes the Decision from `evaluate_eligibility` in the same unit.
4. **Address Class history** is the address's `address` Provenance Records in time order, each holding the class at the time. A later class change that writes no address record (2.14's verification) must add one. That is deferred to 2.14.
5. **The "AI-extracted (<route>@<version>)" label is deferred.** This tree has no Evidence (2.9 is on `main`), and nothing stores an AI-extracted Contact item until 2.10. That goes to `deferred-work.md` (2.10 and the merge).
6. **The page doesn't name the Campaign of a basis.** The record keeps `basis_campaign_id`, and 3.15's export shows it.
7. **"manual"** reads "Manual (entered by Mac)".

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| US basis | eligible US Contact, US Campaign, no LIA | one `eligibility_basis` record; the page shows "Basis: US: cold email allowed (CAN-SPAM), recorded …" | N/A |
| UK basis | GB Contact, UK Campaign with LIA (R1, 2026-10-01), an eligible UK Decision | the record holds the rule, the Campaign, R1, the date and `at`; the page shows "Legitimate interests: LIA R1 dated 2026-10-01, recorded …" | N/A |
| LIA changed later | `Setup.record_lia` to R2, then a second basis | the first record keeps R1, and both records show, oldest first | N/A |
| Never enrolled | no basis record | "Basis not yet recorded (recorded at enrollment)" | N/A |
| Refusals | ineligible Decision; unknown Contact or Campaign; other Market; US basis in a UK Campaign; missing or different LIA; LIA on a US Campaign; naive `at`; `datetime` as `lia_date` | nothing written | `ValueError` / `LookupError` / `TypeError` |
| Provenance missing | the Contact's records removed (raw SQL); an email with no address record | `provenance_missing` in the Decision; a red "Provenance missing" | N/A |
| Facts | resolved US; no Market (`cctld_outside_markets`); unresolved; unreadable Markets | "United States (from the office location)" style; "No Market (…)"; "Not resolved yet"; "Unknown Market (…)" | `InvalidMarkets` caught on the page |
| Hostile | detail `javascript:alert(1)`, `https://x.test/<b>` | plain text; a link with `rel="noopener noreferrer"`, escaped | N/A |
| Basis source | `add_contact(..., source=ELIGIBILITY_BASIS)` | nothing written | `ValueError` |
| Width | the longest label's basis (108); a 64-character code (113); 129 characters | stored; stored; `Decision` refuses | `ValueError` |

</frozen-after-approval>

## Code Map

- `nextron/domain/contacts.py`: `ProvenanceSource` (with `ELIGIBILITY_BASIS`), `ProvenanceSubject`. Add `provenance_recorded` and its `Protocol`.
- `nextron/domain/eligibility.py`: `ReasonKind` (~L103), the `*_TEXT` constants, `Decision.__post_init__` (~L300), `EligibilityContact` (~L361, mirror `_bool`), `evaluate` (~L593), `US_BASIS`/`UK_BASIS`.
- `nextron/domain/campaign.py`: `Lia(ref, date)` and `needs_lia`. `nextron/app/queries/setup.py`: `campaign(id)` (`SeededCampaign.market_id`, `.lia`) and `markets()` (`SeededMarket.id`, `.definition.country_code`).
- `nextron/app/compliance/api.py` `evaluate_eligibility` (~L254): add the fact and the docstring.
- `nextron/app/research/api.py`:
  - `_check_arguments` (~L294) gets the refusal; add `record_eligibility_basis` to `Research` with its docstring;
  - research already imports `queries.setup` and `compliance_api`;
  - mind 3.3's permanence contract style.
- `nextron/db/repos/research/provenance.py`: add `insert_basis(...)`, and make `first_contact_record` filter `source_kind != eligibility_basis`.
- `nextron/db/models/research.py`: `BASIS_RULE_LENGTH` and its docstring. `nextron/db/alembic/versions/0019_3_2_contact_markets.py` is the rebuild pattern, and `0015_2_3_contacts_provenance.py` has the exact `provenance_records` table to copy.
- `nextron/web/leads_page.py`:
  - `SOURCE_LABELS`, `ProvenanceRow`, `ContactRow`, `contact_row` and `lead_context`;
  - add `MARKET_BASIS_LABELS`, `TZ_BASIS_LABELS`, `MarketFacts`, `BasisRow` and their builders;
  - it still imports only `nextron.app.queries.*` from the app.
- `nextron/web/templates/lead.html`: `eligibility-row`, `provenance-row`. `nextron/web/app.py` `lead_page` (~L999). `nextron/web/static/app.css` (~L607–640): byte-preserving.
- **Tests:**
  - **To reuse:**
    - `tests/unit/test_eligibility.py`: the `contact(**fields)` helper (~L90) and `ONE_REASON`.
    - `tests/app/test_eligibility.py`: `World`.
    - `tests/unit/test_leads_page.py`: `Tree`, `cells`, `table_rows`, `World`, `world`, `import_csv` and `sync_markets`.
    - `tests/app/test_campaign_sync.py`: the seed setup and `record_lia`.
  - **Likely affected:**
    - `test_leads_page.py` ~L665 ("No Provenance Record.") and ~L1064 (the label);
    - `tests/contracts/test_persistence_contracts.py`;
    - `tests/unit/test_migrations.py`;
    - `tests/app/test_serve_process.py`.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/contacts.py`, `nextron/domain/eligibility.py` -- `provenance_recorded`; the reason, the fact, the length and `basis_country` -- FR-51/FR-18
- [x] `nextron/app/compliance/api.py` -- pass the fact -- the live reason
- [x] `nextron/db/models/research.py`, `nextron/db/alembic/versions/0024_3_7_basis_rule_width.py`, `nextron/db/repos/research/provenance.py` -- widen; `insert_basis`; `first_contact_record` -- storage
- [x] `nextron/app/research/api.py` -- `record_eligibility_basis`; the `add_contact` refusal -- the 4.8 recorder
- [x] `nextron/web/leads_page.py`, `lead.html`, `app.py`, `app.css` -- the facts, links, missing, bases -- UX-DR17
- [x] `tests/unit/test_eligibility.py` -- the new case in `ONE_REASON`, the fact's type check, the width boundary, `basis_country`, every label's basis ≤ 128
- [x] `tests/unit/test_contacts_domain.py` -- `provenance_recorded` table
- [x] `tests/app/test_eligibility_basis.py` (new) -- the recorder matrix rows through real units, nothing written on refusal, earlier records kept after `record_lia`, `first_contact_record` skipping a basis; `provenance_missing` through `evaluate_eligibility`
- [x] `tests/unit/test_lead_provenance_page.py` (new) -- the page matrix rows, escaping and links inside `hx-disable`, `no-store`; adjust `test_leads_page.py`
- [x] migration tests -- `HEAD`, probes, rows and checks survive the rebuild, drift empty
- [x] `README.md`, `test_source_tree.py`, `deferred-work.md` -- docs, `FILES`, and the deferrals (decisions 4 and 5)

**Acceptance Criteria:**
- Given `uv run pytest -q tests/contracts`, when it runs, then every contract passes.
- Given `uv run lint-imports`, when it runs, then every contract is kept.

## Implementation Notes

- **Domain.** `domain.contacts.provenance_recorded(email, records)` takes any iterable of objects
  matching the `ProvenanceFacts` Protocol (read-only `subject`, `source_kind`, `address`
  properties, so the frozen `StoredProvenance` fits). `domain.eligibility` gains
  `ReasonKind.PROVENANCE_MISSING` (after `address_not_sendable`), `PROVENANCE_MISSING_TEXT`, the
  required `EligibilityContact.provenance_recorded` (checked with `_bool`), `_provenance_reason`
  (run with the address check in `evaluate`), `MAX_BASIS_RULE_LENGTH = 128` (refused in
  `Decision.__post_init__`, message names only the limit) and `basis_country` (exact
  `US_BASIS`; for GB the text must start with `UK_BASIS`'s prefix, end with its suffix and hold a
  non-blank label; a non-`str` gives `None`).
- **Compliance.** `evaluate_eligibility` passes
  `provenance_recorded(contact.email, research_queries.provenance(contact.id))`; it still reads
  only `markets` from `queries.setup` (contract (e) of 3.6 holds).
- **Storage.** `0024_3_7_basis_rule_width` (down `0023_4_11_footer_snapshots`) rebuilds
  `provenance_records` with `batch_alter_table(recreate="always", copy_from=0015's table)` and
  `alter_column("basis_rule", String(64) -> String(128))`. `BASIS_RULE_LENGTH` in
  `db/models/research.py` is `MAX_BASIS_RULE_LENGTH` (128), imported from the domain. `provenance_repo.insert_basis(...)` writes the basis record;
  `first_contact_record` adds `source_kind != 'eligibility_basis'`. `clear_other_contact_notes`
  is unchanged (it only touches records holding notes, and a basis record's are always NULL).
- **Research.** `_check_arguments` refuses `ELIGIBILITY_BASIS` (`ValueError`) after the type
  checks, before the Lead read and the gate. `record_eligibility_basis` is a `@command`:
  `_check_basis_arguments` (the type checks in the spec's order, then the ineligible Decision;
  returns the basis rule), then the Contact (`contacts_repo.get`, `LookupError` for a non-ULID
  too), the Campaign (`queries.setup.campaign`), its Market from `queries.setup.markets()`
  (`LookupError` if absent, which the FK prevents; an undecodable Market raises its
  `InvalidMarkets`), then `_basis_problem` (Market match, `basis_country`, the LIA rule with
  `needs_lia`), then one `insert_basis`. Nothing is logged and no `events` row is written.
- **Lead page.** `leads_page` adds `MARKET_BASIS_LABELS`, `TZ_BASIS_LABELS`, `MarketFacts`,
  `BasisRow`, `market_facts`, `basis_row`/`basis_rows` (oldest first by
  `basis_recorded_at or retrieved_at`, then id), `moment` (`YYYY-MM-DD HH:MM UTC`) and
  `market_names`; `ContactRow` gains `bases`, `facts` and `provenance_missing` (from
  `provenance_recorded` on the records shown). `contact_row` takes the Market names;
  `lead_context` takes `markets` 7th. `app.lead_page` passes `setup_queries.markets()`, or `()`
  on `InvalidMarkets`. `lead.html`: the bases `<ul class="eligibility-bases">` (one `<li>` per
  record, the LIA line after a `<br>`) or `<p class="eligibility-basis">` after the reasons; the
  `<dl class="provenance-facts">` (Market, Timezone, then one `<dd>` per note line, or "—"); the
  red `<p class="provenance-missing">Provenance missing</p>` replaces "No Provenance Record."
  (shown above the records table when some records exist); each Detail through
  `safe_link(record.detail)`. The records table holds the source records only: a basis record
  shows in the bases list, never as a row. `SOURCE_LABELS[operator]` is "Manual (entered by Mac)". `app.css` gains
  `.eligibility-bases` (with `.eligibility-reasons`), `.provenance-facts` (+ `dt`/`dd`) and
  `.provenance-missing`, then `.eligibility-basis`, written byte-preserving (the first 293 CRLF
  lines kept).
- **Tests changed.** `tests/contracts/test_ad16_callers.py` iterates a new `CONTACT_SOURCES`
  (every `ProvenanceSource` but `eligibility_basis`) for the `research.add_contact[...]`
  manifest, scenarios and built-callers set, since `add_contact` now refuses that source before
  the gate. `tests/unit/test_leads_page.py`: "No Provenance Record." became the
  `provenance-missing` paragraph, the every-source test skips `eligibility_basis` (its label is
  checked in the new page tests), the operator label, and the hostile Market-name test now finds
  3 escaped occurrences (the reason, the Market fact and the timezone note). The chain probes
  moved to `0025`/`0026`; `tests/unit/test_serve.py`'s fixed `0024_more` extension revision was
  left as it is (alembic accepts it after the real `0024`, and it has never been bumped).
- **Not done (deferred, decisions 4 and 5):** the Address Class history beyond the existing
  `address` records (2.14) and the "AI-extracted (<route>@<version>)" label (2.10/2.9 merge); both
  are in `deferred-work.md`.
- **Review patches (pass 1).**
  - `leads_page.contact_row` builds the records table from the records whose `source_kind` isn't
    `eligibility_basis`; the bases, the notes and `provenance_missing` still use every record.
    The module docstring and README say so. `test_a_us_basis_shows_its_rule_and_time` asserts
    only the source rows (no "Eligibility basis" row), and the new
    `test_a_basis_only_contact_shows_provenance_missing_and_no_record_row` shows "Provenance
    missing" with no records table for a Contact holding only a basis record.
  - `db/models/research.py`: `BASIS_RULE_LENGTH: Final = MAX_BASIS_RULE_LENGTH`, imported from
    `nextron.domain.eligibility` (lint-imports stays green: db may import domain).
  - `record_eligibility_basis`: the docstring's refusal list and README's recorder paragraph say
    a stored Market that no longer decodes raises its `InvalidMarkets` and nothing is written;
    `test_unreadable_markets_raise_invalid_markets_and_write_nothing` breaks the `uk` row's
    `cctlds` by raw SQL and checks it.
  - `app.css`: `.eligibility-basis { margin: 0.25rem 0 0; }` after the eligibility list rule,
    byte-preserving.
  - Tests: `[key] = rows(...)` spacing; two blank lines before the Story 3.7 section of
    `tests/unit/test_eligibility.py`; `test_the_longest_bases_are_stored_whole` renamed
    `test_the_longest_bases_round_trip_through_the_recorder`, with a comment that the width
    itself is pinned by the 0024 `PRAGMA table_info` test and the drift contract.

**Final verification (orchestrator).**
- Before review: the Verification command gave 1871 passed and 10 skipped (`ai_eval` and the 9
  "not built yet" AD-16 callers), and `lint-imports` kept 29.
- After the pass-1 patches: the patched files gave 393 passed, and the full suite through
  `suite_lock.py` (`track-A`) gave **11481 passed, 12 skipped**. `lint-imports` kept 29
  contracts and broke none.

## Spec Change Log

## Review Triage Log

Pass 1 (review loop 0). Three layers ran:
- blind hunter (BH): 15 findings;
- edge-case hunter (EC): 3;
- verification gap (VG): no gap, plus 4 other findings.

That is **22 raw** findings, **16 after grouping**: 8 patched, 2 deferred, 6 rejected.

| # | Layer | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| 1 | BH, EC | `record_eligibility_basis` accepts any eligible `Decision`. It isn't bound to the Contact or the time, so a stale or other Contact's Decision (or a hand-built UK one) becomes the audit basis. Re-evaluate inside the recorder. | low | It's true that the Decision is trusted. But no caller exists until 4.8, whose spec makes it `evaluate_eligibility` in the same unit right before recording (Design Notes "For 4.8"). Re-evaluating would empty the AC's `decision` parameter, and it contradicts frozen decision 3 ("the recorder trusts the caller's Decision"). | reject (approved spec; flag for Mac and 4.8) |
| 2 | BH, EC | No check that the Lead's Segment is the Campaign's, nor of the Campaign's status. | low | Unreachable today: the only future caller, `enroll` (4.8), selects Leads by the Campaign's Segment and checks `campaign_active`. The fix adds guards for a state nobody reaches. | reject (note for 4.8) |
| 3 | BH | `at` isn't checked against now, the Contact's creation or the LIA date. | low | The caller passes its clock's now (4.8). `lia_from_fields` already refuses a future LIA date. The fix would add guards for a caller bug nobody showed. | reject |
| 4 | BH | The "AI-extracted" deferral names Story 2.10, which is already merged on `main` (`23ebbd5`, with 2.9's Evidence), so the AC clause would have no owner after the merge. | medium | Verified with `git log main` (2.10 at `review`). | patch (the deferral now names the 3.7 merge into `main`; flagged in the report) |
| 5 | BH | AC1's "the referring Contact" isn't built or deferred: a referral shows its `source_detail` as text. | low | No referral producer exists. FR-67 (Epic 7) decides what a referral record holds, and the page shows its detail. | defer (Epic 7) |
| 6 | BH | No deferral for paths that set a Contact's email outside `add_contact` (2.13, 2.16). Without an `address` record they become `provenance_missing`. | low | Verified: `provenance_recorded` needs an `address` record equal to the email. It fails closed, but those stories must write one. | defer (2.13, 2.16) |
| 7 | BH | Basis records also show in the Provenance records table as "Contact" rows, as if a basis were a source. A basis-only Contact shows "Provenance missing" above a "Contact" row. | low | Verified in `contact_row` (every record) and in `test_a_us_basis_shows_its_rule_and_time`. Every enrolled Contact would show it. The fix is a direct filter, and the bases already show next to the Decision. | patch |
| 8 | BH, VG | `sprint-status.yaml` `last_updated` goes back from 07:00 to 06:32. | low | Verified. | patch (orchestrator) |
| 9 | BH | The spec isn't in the diff. | false | It's excluded from the review diff on purpose (step 4 gives it to the edge-case layer only), and it's committed with the story. | reject |
| 10 | BH | The width 128 is two literals tied only by comments. | low | Verified: `BASIS_RULE_LENGTH = 128` and `MAX_BASIS_RULE_LENGTH = 128` aren't linked. The direct fix is to define the model's constant from the domain's. | patch |
| 11 | BH, EC, VG | The recorder can raise `InvalidMarkets` (from `queries.setup.markets()`), which isn't documented or tested. | low | Verified. It fails closed (nothing is written), so the fix is docs plus a test. | patch |
| 12 | BH | `test_each_call_adds_a_record_even_for_the_same_basis` proves nothing about order. | false | The test claims each call adds a record, not order. Order is tested by `test_a_later_lia_leaves_the_earlier_basis_as_recorded`, `test_a_later_lia_shows_both_bases_oldest_first` and `test_basis_rows_are_oldest_first_and_only_basis_records`. | reject |
| 13 | BH | `<p class="eligibility-basis">` has no CSS, unlike `.eligibility-bases`. | low | Verified. Most Contacts (never enrolled) show it with default `<p>` margins. The fix is a direct rule. | patch |
| 14 | BH, VG | Formatting: `[key] =rows(` in `test_eligibility_basis.py`, and one blank line before a section in `test_eligibility.py`. | low | Verified. | patch |
| 15 | BH | `test_lead_provenance_page.py` imports the `world` fixture from `test_leads_page.py`. | low | Coupling between test modules, and a change breaks loudly. Moving fixtures to a `conftest.py` is more than a direct fix. 3.6 reused test helpers the same way. | reject |
| 16 | VG | `test_the_longest_bases_are_stored_whole` can't fail on the width (SQLite doesn't enforce `VARCHAR(n)`). | low | Verified. The width is pinned by the 0024 `PRAGMA table_info` test and the drift contract. The fix is a direct rename. | patch |

## Design Notes

**Basis labels.**

| Basis | `MARKET_BASIS_LABELS` | `TZ_BASIS_LABELS` |
|---|---|---|
| `office_location` | from the office location | from the office location |
| `lead_country` | from the Lead's country; office location unknown | — |
| `conflict_stricter` | the stricter of two conflicting Markets | — |
| `cctld_outside_markets` | the email is on a country-code domain outside the enabled Markets | — |
| `location_outside_markets` | the location is outside the enabled Markets | — |
| `location_unknown` | the location is unknown | — |
| `lead_hq` | — | from the Lead's headquarters |
| `market_default` | — | the Market's default |
| `us_unknown_fallback` | — | US state unknown: Eastern fallback with a narrowed Send Window |
| `no_market` | — | no Market |

**For 4.8:**
1. `decision = compliance.evaluate_eligibility(contact_id)`; refuse when it isn't eligible.
2. `campaign = queries.setup.campaign(id)`.
3. `research.record_eligibility_basis(contact_id, decision, campaign.id, campaign.lia.ref if campaign.lia else None, campaign.lia.date if campaign.lia else None, now)`, all in one unit.

## Verification

**Commands** (from `nextron-engine/`):
- `uv run pytest -q tests/unit/test_eligibility.py tests/unit/test_contacts_domain.py tests/app/test_eligibility.py tests/app/test_eligibility_basis.py tests/unit/test_leads_page.py tests/unit/test_lead_provenance_page.py tests/unit/test_migrations.py tests/app/test_lead_import.py tests/app/test_contact_markets.py tests/contracts` -- expected: all pass
- `uv run lint-imports` -- expected: all contracts kept
