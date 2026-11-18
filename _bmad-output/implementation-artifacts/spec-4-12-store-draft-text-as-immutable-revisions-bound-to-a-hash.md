---
title: "Story 4.12: Store Draft text as immutable revisions bound to a hash"
type: 'feature'
created: '2026-11-18'
status: 'done'
baseline_commit: '923ee90fa5547525a2b02b433d97b7019fd5b2c3'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-nextron-outreach-engine-2026-11-14/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Draft slots (4.9) hold no text, so there is nothing to ground (4.13/4.14), approve against a hash (Epic 5) or send (Epic 6); a footer setting change has no subscriber; the Lead page shows no Drafts.

**Approach:** Add immutable `draft_revisions` (owner `drafting`) with a composite FK from `drafts`; add `domain.render.compose` and `domain.render.text_hash` (the single definitions); `drafting.revise` (operator edit) and `drafting.recompose_ready` (`FooterChanged` subscriber) append revisions through one private writer that snapshots the footer, composes and hashes; the Lead page gets a Drafts section.

## Boundaries & Constraints

**Always:**
- **Table** `draft_revisions` (story AC columns): PK (`draft_id`, `rev`), FK `draft_id`→drafts, `footer_snapshot_id`→footer_snapshots, `cited_signal_id`→signals (nullable), `route_tuple_id`→ai_route_tuples (nullable); `evidence_ids` and `fact_versions` JSON text (a list of ULIDs; a list of `[fact_id, version]` pairs; `[]` allowed); `playbook_sha256`, `playbook_version`, `prompt_version`, `instruction` nullable; CHECKs: origin in the four values, `rev >= 1`, `(rev = 1) = (origin = 'generated')` (revision 1 is always the AI's text), `text_hash` 64 lowercase hex, `instruction` NULL unless origin `regenerated` and ≤ 200 chars. `drafts` gains FK (`id`, `current_rev`)→draft_revisions(`draft_id`, `rev`) (table rebuild via batch).
- **Immutability:** triggers abort DELETE and any UPDATE except `subject`, `body`, `instruction` changing from non-NULL to `[erased]` (3.13's `_redaction_only` pattern). The repo has `insert`, reads, and one `tombstone(draft_ids)` (the erasure); no other update/delete.
- **`compose(revision, footer_snapshot, recipient) -> Composed(to_address, subject, body)`**: `body` = revision body + `"\n\n"` + footer text (an empty body still ends with the full footer); plain text only. **`text_hash(to_address, inbox_id, subject, body)`** = SHA-256 hex of UTF-8 per the AC with `canon` (NFC, CRLF/CR→LF, rstrip each line, drop trailing blank lines). Both pure, in `domain/render.py`, defined nowhere else.
- **Private writer** `_append(draft, subject, body, origin, traceability, at)`: the Contact (Enrollment's `contact_id`) must have an email; footer = `render.footer(queries.setup.footer_settings(), Contact's Market, provenance source)` where the source is the Contact's earliest Provenance record with subject `contact`; `footer_snapshots.ensure`; compose; hash with the Enrollment's `sending_inbox_id`; insert rev = current + 1 (1 when none); `drafts.current_rev` moves to it in the same unit.
- **`@command revise(draft_id, subject, body) -> int`**: Draft `ready` with a current revision, else `ValueError` (unknown `LookupError`); `subject` a `str` 1–200 chars after strip, `body` a `str` ≤ 10,000 chars (may be empty); origin `operator_edit`; traceability (evidence, facts, signal, tuple, Playbook, prompt) copied from the previous revision, `instruction` NULL; grounding stays pending (no `grounding_results` exist yet). Returns the new rev.
- **`@command recompose_ready(n: FooterChanged)`** (registered in `main.build_subscribers`): for each `ready` Draft with a current revision whose Contact's `market_id` is `n.market_id`, append origin `footer_change` (same subject, body, traceability) only when the new hash differs from the current one (idempotent); a Draft whose footer can't be composed is logged and skipped. `approved`/`sent`/other Drafts untouched.
- **Erasure:** `subject`, `body`, `instruction` class `redact`; `text_hash` `keep_hash` (kind `None`); other columns `none`. `drafting.ERASURE["draft_revisions"]` is `TOMBSTONED`; `erase_pii` tombstones every revision of the erased Contact's Drafts.
- **Seed sync** (`setup._record_market`) publishes `FooterChanged(market_id)` when a stored Market's decoded `footer_template` or `privacy_line_required` changes (deferred-work 4.11 item).
- **Lead page Drafts section** (`lead_drafts.html`, included in `lead.html`): per Draft of the Lead's Enrollments: kind, Step, status, due time or "after Step N−1 is sent", the composed message (autoescaped, inside `hx-disable`), "Sending blocked: footer incomplete (…)" when `footer_complete` fails (`render.incomplete_notice`), the Playbook version and the revision history (rev, origin, time, short hash). "No text yet" for a Draft without a revision. Page keeps `Cache-Control: no-store`.

**Never:** grounding rows or rules (4.13/4.14), AI calls, generation of revision 1 (4.15), regenerate (4.20), approval/edit UI (Epic 5/11), HTML, tracking or links in compose, any setting changing compose, a second hash/canon definition, a new dependency.

### Decisions made overnight (pending user review)
1. Grounding parts of revise/recompose (pending result, re-run rules, copy AI result) are deferred to 4.13/4.14: no table exists.
2. Footer provenance = the Contact's earliest `contact`-subject Provenance record; a UK source with no phrase fails closed (revise raises; recompose skips and logs).
3. Operator edits and footer changes copy the previous revision's traceability.
4. Erasure tombstones revision text (`[erased]`), keeping rows and hashes (3.13 pattern); the repo's one non-insert write is that tombstone.
5. Seed sync now publishes `FooterChanged` on a template change (compliance-safe).
6. `revise` limits: subject 1–200 chars, body ≤ 10,000 chars; only `ready` Drafts.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Error handling |
|---|---|---|---|
| Edit | ready Draft at rev 1 | rev 2 `operator_edit`, current_rev 2, new hash | N/A |
| Edit refused | approved / no revision / blank subject | nothing written | `ValueError` |
| Empty body | body `""` | composed body is `"\n\n"` + full footer | N/A |
| Hash goldens | CRLF vs LF; trailing spaces; NFD vs NFC | same hash | N/A |
| Hash differs | other footer or other inbox | different hash | N/A |
| Footer change | ready + approved Drafts in Market | ready gets `footer_change` rev; approved untouched; repeat no-op | N/A |
| Immutability | UPDATE/DELETE on a revision | aborted (except tombstone) | trigger |
| Erasure | Contact erased | its revisions' text `[erased]`, rows kept | N/A |

</frozen-after-approval>

## Code Map

- `nextron/domain/render.py` -- add `Composed`, `compose`, `canon`, `text_hash`; update docstring.
- `nextron/db/alembic/versions/0035_4_12_draft_revisions.py` (new) -- template `0034_4_9_drafts.py`; triggers like `0032_3_13_erase_contacts.py` (`_redaction_only`, `REDACTED`); rebuild `drafts` with `op.batch_alter_table` to add `fk_drafts_current_rev` (keep its CHECKs and indexes).
- `nextron/db/models/drafting.py` -- `DraftRevisionRecord`; FK on `DraftRecord`. `db/owners.py`, `db/pii_registry.py` (+`HASH_KINDS` for `text_hash`: `None`).
- `nextron/db/repos/drafting/revisions.py` (new) -- `insert`, `get`, `of_draft`, `of_drafts`, `tombstone`. `db/repos/drafting/drafts.py` -- `set_current_rev`, ready Drafts with a revision (status read-only).
- `nextron/app/drafting/api.py` -- `Drafting.revise`, `Drafting.recompose_ready`, `_append`; `ERASURE` + `erase_pii` (module-level, currently a no-op) tombstone revisions; docstring. AD-16 manifest in `tests/contracts/test_ad16_callers.py` scans def names and insert sites: update its pins if it flags the new repo `insert`.
- `nextron/app/queries/drafting.py` -- revision views; `drafts_of_lead(lead_id)` for the page. Need reads: `queries.research.contact`, `.provenance`; a Market by id from `queries.setup` (add `market_by_id` if missing); enrollments by Lead / Contact from `queries.sequencing`.
- `nextron/app/setup/api.py:1011` `_record_market` -- publish `FooterChanged`.
- `nextron/main.py:1636` `build_subscribers` -- `FooterChanged` → `Drafting(chosen).recompose_ready`; pins in `tests/unit/test_serve.py`.
- `nextron/web/app.py:1465` `lead_page`, `nextron/web/leads_page.py`, `templates/lead.html`, new `templates/lead_drafts.html`.
- Tests to bump: HEAD in `tests/contracts/test_persistence_contracts.py`, `tests/unit/test_migrations.py`, `tests/app/test_serve_process.py`; chain probes; `tests/contracts/test_source_tree.py` `FILES`; erasure contracts.

## Tasks & Acceptance

**Execution:**
- [x] migration + model + owners + PII -- table, triggers, drafts FK.
- [x] `domain/render.py` -- compose, canon, text_hash; `tests/unit/test_render.py` goldens.
- [x] repos + queries + `app/drafting` -- writer, revise, recompose_ready, erasure.
- [x] `app/setup/api.py`, `main.py` -- FooterChanged publish and subscriber.
- [x] web -- Drafts section.
- [x] Tests -- `tests/app/test_draft_revisions.py` (matrix, trigger aborts, erasure, footer change), seed-sync publish test, Lead page test, contract bumps.
- [x] `nextron-engine/README.md` -- Story 4.12 section.

**Acceptance Criteria:**
- Given the migration at head, then `draft_revisions` exists, every column mapped and classified, and `drafts.current_rev` can only name an existing revision.
- Given the revisions repo, then it exposes no update or delete function other than `tombstone`.
- Given `compose` output, then it holds no HTML tag, tracking pixel or URL other than the footer's.

## Implementation Notes

- **Migration** `0035_4_12_draft_revisions.py` (revision `0035_4_12_draft_revisions`, down `0034_4_9_drafts`). It rebuilds `drafts` first (`batch_alter_table(recreate="always", copy_from=0034's exact table)`) to add `fk_drafts_id_current_rev_draft_revisions` (`id`, `current_rev`) → `draft_revisions` (`draft_id`, `rev`). It then creates `draft_revisions` and three triggers: `trg_draft_revisions_no_delete`, `trg_draft_revisions_insert_new` (refuses an `INSERT` whose `id` or (`draft_id`, `rev`) exists, so `INSERT OR REPLACE` can't replace a row) and `trg_draft_revisions_immutable` (only `subject`/`body`/`instruction` non-NULL → `[erased]`).
- **Key (deviation from the frozen text, forced by the spine).** The spine's IDs rule ("ULID strings for every primary key") and the `metadata_problems` contract require one ULID `id` PK. So `draft_revisions` has `id` as PK and (`draft_id`, `rev`) as `uq_draft_revisions_draft_id_rev`, and the drafts FK references that unique pair. The FK name follows the naming convention (`fk_drafts_id_current_rev_draft_revisions`), not `fk_drafts_current_rev`. In the model the FK has `use_alter=True`, which breaks the metadata sort cycle.
- **CHECKs beyond the frozen list (conservative):**
  - `playbook_sha256` is NULL or 64 lowercase hex.
  - `evidence_ids` and `fact_versions` are JSON arrays.
- `RevisionOrigin` lives in `nextron/domain/draft.py`.
- **`domain/render.py`** adds `Composed`, `compose` (Protocols `RevisionText` and `FooterText`; the recipient is the to-address `str`), `canon`, `text_hash` and `BODY_FOOTER_SEPARATOR`.
- **Repos:**
  - `db/repos/drafting/revisions.py`: `insert`, `get`, `of_draft`, `of_drafts`, `tombstone`.
  - `drafts.py`: `set_current_rev`, `ready_with_text`.
  - `sequencing/enrollments.py`: `for_lead`, `for_contact`.
  - `setup/markets.py`: `by_id`.
- **Queries:**
  - `queries.drafting`: `RevisionView`, `revisions`, `revision`, `current_revision`, `ready_with_text`, `footer_snapshot`.
  - `queries.sequencing`: `enrollments_of_lead`, `enrollments_of_contact`.
  - `queries.setup`: `market_by_id`.
- **`app/drafting/api.py`:**
  - `_prepare` does every check and computation before any write: Enrollment → Contact (email, not erased) → Market → footer (earliest `contact` Provenance `source_kind`) → compose → hash.
  - `Drafting._append` writes: `footer_snapshots.ensure`, then insert rev = current + 1, then `set_current_rev`.
  - `revise` and `recompose_ready` as the spec says. `recompose_ready` calls `_prepare` first, skips when the hash is unchanged, and logs `recompose_skipped` (`draft_id`, `error_type`) on `ValueError`/`LookupError`.
  - `erase_pii` tombstones the revisions of every Draft of the Contact's Enrollments.
  - `ERASURE["draft_revisions"]` is `TOMBSTONED`.
- **`setup._record_market(..., footer_changed=)`** publishes `FooterChanged` when a stored Market's `footer_template` or `privacy_line_required` changed in `_sync_markets`.
- **`main.build_subscribers`** registers `FooterChanged` → `Drafting(clock).recompose_ready`.
- **Web:**
  - `leads_page`: `DraftSource`, `DraftRow`, `RevisionRow`, `draft_row`, `due_text`, the label maps, and `lead_context(drafts=...)`.
  - `app.py`: `lead_drafts(lead_id)`.
  - New `templates/lead_drafts.html` (section `hx-disable`; body in `<pre>`), included in `lead.html`.
- **Further implementer decisions (pending review):**
  - (a) `revise` refuses a subject that contains a line break (`ValueError`). A subject becomes one header line, so this guards against header injection.
  - (b) `_prepare` treats an erased Contact as having no email (`ValueError`).
  - (c) The Lead page shows the footer-blocked notice only on `ready`/`approved` Drafts. An erased Contact's Draft shows "To: " with no address.
  - (d) Revisions write no `events` rows, because nothing changes status.
- **Tests:**
  - New: `tests/unit/test_render.py` (goldens) and `tests/app/test_draft_revisions.py` (matrix, triggers, CHECKs, FK, erasure, footer change, seed sync, Lead page).
  - Pins bumped: HEAD ×3, chain probes 0036/0037, `test_source_tree` `FILES` (migration, repo, template), the erasure contract `COVERAGE`, the `test_erasure.py` not-planted set (the `draft_revisions` columns) the `test_serve.py` subscriber pins, the erasure contract probes and `HASH_KINDS` pin, the `test_web_contracts.py` template list, the `test_leads_page.py` section headings and the `test_enroll.py` repository-function pin.

- **Review patches (pass 1):**
  - `recompose_ready` also skips and logs `InvalidMarkets`, so an unreadable Market no longer rolls back a settings save or a seed sync.
  - The text rules now live in `_check_text`, which `_prepare` runs for every origin (`revise` also runs them before its lookup). The rules: a subject of 1–200 characters after strip, no line break and no `Cc` control character; a body of at most 10,000 characters.
  - The `main.py` docstring now says `drafting`'s erasure hook tombstones `draft_revisions`.
  - The `leads_page` import order is fixed.
  - New tests:
    - erasure redacts a non-NULL `instruction`, and a second erasure changes nothing;
    - `cited_signal_id` and `route_tuple_id` are real ids and are copied by `operator_edit` and `footer_change`;
    - UK: the earliest `contact` source is named; a source with no phrase makes `revise` raise and makes recompose skip with `ValueError` while the save still commits;
    - `InvalidMarkets` is skipped;
    - a seed sync that turns the privacy line on recomposes;
    - the immutability cases (`instruction`, `footer_snapshot_id`, `origin`, `draft_id`, a duplicate `INSERT`);
    - control-character subjects are refused;
    - `test_0035_keeps_every_draft_and_its_partial_index`.
  - Market validation requires `{privacy_line}` exactly when `privacy_line_required`, so a seed sync can't flip the flag alone; the test changes both.

- **Final gate:** full suite through the lock on the final tree: 14679 passed, 11 skipped; lint-imports 29 kept. An earlier run during a machine crash failed only on resource exhaustion (WinError 1450, timeouts) in unrelated tests.

## Spec Change Log

## Review Triage Log

Pass 1 (blind hunter BH, edge-case hunter EC, verification gap VG): 24 raw, 19 after dedup; 11 patched, 1 deferred, 7 rejected.

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | EC1: `market_by_id` can raise `InvalidMarkets` (a `DomainError`, not `ValueError`) inside `recompose_ready` | medium | `_stored` decodes the row; the `except` catches only `ValueError`/`LookupError`, so one bad Market row rolls back the whole footer settings save or seed sync | patch: catch `InvalidMarkets` too |
| 2 | BH1, EC2, EC4: the one-line subject rule lives only in `revise`; other control characters pass | medium | `_prepare`/`_append` accept any subject, so 4.15's `generated` path could store a multi-line subject (header injection, ambiguous hash boundary) | patch: move the subject/body checks into `_prepare` for every origin and refuse any control character in the subject |
| 3 | VG1, BH9: erasure never shown to redact a non-NULL `instruction`; repeat erasure untested | medium | no test erases a revision that has an instruction | patch: test |
| 4 | VG2: `cited_signal_id`/`route_tuple_id` copy untested | medium | `REVISION_COLUMNS` omits them and `TRACE` sets both `None` | patch: test with a real Signal and tuple |
| 5 | VG3, BH10: UK footer path, earliest-Provenance choice and the `ValueError` skip untested | medium | every test Draft is US; the skip test reaches only `LookupError` | patch: UK tests (phrase present; no phrase → `revise` raises, `save` still commits and logs) |
| 6 | VG4: seed sync's `privacy_line_required` trigger untested | low | only the template change is tested | patch: test |
| 7 | VG5: the `drafts` rebuild in 0035 has no data-preservation test | medium | earlier rebuilds (0026, 0030) have one each | patch: `test_0035_keeps_every_draft_and_its_partial_index` |
| 8 | BH11: immutability test misses `instruction`, `footer_snapshot_id`, `origin`, `draft_id` updates and a plain duplicate INSERT | low | the trigger exists for these; the tests are one parametrize away | patch: extend the parametrize |
| 9 | VG-other: `main.py` docstring still calls drafting's erase hook a no-op | low | direct correction | patch |
| 10 | BH13: import out of order in `leads_page.py` | low | direct correction | patch |
| 11 | BH2: a Contact whose Market changes keeps its old footer and hash | medium | research can rewrite `contacts.market_id`; nothing recomposes; needs a new notification or a send-time check | defer to Epic 5/6.9 (approval and the send gate must check the footer against the current Market) |
| 12 | EC3, BH3: erasure misses Drafts with no Enrollment | low | unreachable: `_prepare` refuses a Draft with no Enrollment, so such Drafts hold no revision; the stories adding those kinds extend `erase_pii` | reject |
| 13 | EC5: compose output may hold a URL or tag from the body | false | the story AC is that compose and settings *add* none; claims and URLs in the body are 4.13's deterministic rules | reject |
| 14 | BH4: `text_hash` kept after erasure is an unkeyed hash over the address | low | 3.13 keeps address hashes the same way (suppression `key_hash`, `keep_hash` columns); it is the proof of what was approved and holds no plaintext | reject |
| 15 | BH5: `revise` with unchanged text appends a revision | low | the AC says revise adds a revision; Epic 5 decides re-approval behaviour | reject (spec behaviour) |
| 16 | BH6: an erased Contact's Drafts stay `ready` with `[erased]` text | false | `Sequencing.erase_pii` stops the Enrollment and `cancel_pending` cancels its `ready` Drafts in the same transaction (Story 4.9) | reject |
| 17 | BH7: `recompose_ready` scans every ready Draft per Market | low | ready Drafts number in the dozens (daily capacity); a join adds code for no observed cost | reject |
| 18 | BH8: Lead page doesn't recheck the hash; erased Contact shows a bare "To:" | low | erased Contacts' Drafts are cancelled; hash checking is Epic 5's approval gate | reject |
| 19 | BH12: `text_hash` doesn't canonicalise the to-address | low | the AC defines the hash input exactly (raw `to_address`); callers pass the stored address | reject |

## Verification

**Commands:**
- `cd e:/Nextron-track3/nextron-engine && uv run pytest -q tests/unit/test_render.py tests/app/test_draft_revisions.py tests/app/test_draft_slots.py tests/unit/test_serve.py tests/unit/test_migrations.py` -- all pass
- `uv run pytest -q tests/contracts` -- all pass
- `uv run lint-imports` -- all contracts kept
