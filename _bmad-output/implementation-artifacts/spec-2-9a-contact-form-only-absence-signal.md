---
title: 'Story 2.9a: Produce the "contact form only" Absence Signal from detected contact methods'
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: '65d6b7d0e54f44b25510216390c0941f76d215dc'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-9-detect-signals-with-evidence-from-the-crawled-pages.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-2-6-render-near-empty-pages-and-detect-chat-and-booking-widgets.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Story 2.9's AC stores the Law score-only Attribute "contact form only" (`law_contact_form_only`) with Absence Evidence when a Lead's pages show a contact form and no chat, booking or after-hours line, with `extracted_by=rule`. 2.9 deferred it to 2.6, and 2.6 built the contact methods but deferred the Signal back to 2.9 (both `deferred-work.md` entries). Nothing produces it. This completes Story 2.9; its sprint key stays `review`, and no new sprint key is added.

**Approach:**
- **Tag managers.** The crawl and the render also record which tag managers each page loads, in a new column `crawled_pages.tag_managers` (migration `0028_2_9_tag_managers`).
- **The rule.** A pure rule decides whether the Absence holds, from the Lead's `ContactMethods` and its checked pages.
- **Where it runs.** It runs and reconciles in the same transaction that sets `contact_methods`, so it doesn't depend on the AI job's budget, key or outcome.

## Boundaries & Constraints

**Always:**
- **`domain/widgets.py`** gains:
  - `TagManager(key, hosts, markers)`, with the same host and marker matching as `Signature`;
  - `TAG_MANAGERS`, in this order:
    - `google_tag_manager`: hosts `googletagmanager.com/gtm.js` and `googletagmanager.com/ns.html`; markers the same two, plus `gtm.start`;
    - `segment`: hosts and markers `cdn.segment.com` and `cdn.segment.io`;
    - `tealium`: host `tags.tiqcdn.com`; markers `tags.tiqcdn.com` and `utag.js`;
    - `adobe_launch`: host and marker `assets.adobedtm.com`;
  - `tag_managers(html, embeds) -> tuple[str, ...]`: the keys found from `script` and `iframe` URLs and from raw-HTML markers, in `TAG_MANAGERS` order. It never raises.
- **The new pure module `domain/absence.py`.** It imports only the stdlib and `nextron.domain`.
  - **Constants:** `CONTACT_FORM_ONLY = "law_contact_form_only"` and `CONTACT_FORM_ONLY_SOUGHT = "a chat widget, an online booking link or an after-hours line"`.
  - **`AbsenceReason`.** Its values, in check order:
    1. `not_in_segment`: the Lead's Segment has no type with this key whose `evidence` includes `absence`;
    2. `no_pages`;
    3. `chat_found`, `booking_found`, `after_hours_found`;
    4. `no_form`;
    5. `page_needs_render`: a checked page's text is `None` or `needs_render`;
    6. `tag_manager_unknown`: a checked page's `tag_managers` is `None`;
    7. `tag_manager`;
    8. `holds`.
  - **`CheckedPage(url, text, tag_managers)`.** Its text never shows in the `repr`.
  - **`contact_form_only(segment, methods, pages) -> AbsenceReason`.** It returns the first reason that applies. `pages` must cover exactly `methods.pages_checked`, else `ValueError`.
  - **`reconcile_absence(live, holds, pages_checked) -> AbsencePlan(confirm, insert, supersede, retract)`.** `live` is ordered oldest first, by `created_at` then id:
    - when the Absence doesn't hold, every live row is retracted;
    - when it holds:
      - the oldest row with the same `sought` and `pages_checked` is confirmed;
      - failing that, the oldest row is superseded by a new row, or a new row is inserted when there is none;
      - every other live row is retracted.
- **Migration `0028_2_9_tag_managers`** (down revision `0027_2_6_contact_methods`):
  - `op.add_column("crawled_pages", tag_managers Text NULL)`;
  - the value is a canonical JSON array of keys: `[]` means none were found, and NULL means the page was never checked;
  - owner `research`, PII `none`;
  - the model, `owners.py` and `pii_registry.py` match.
- **Crawl repo:**
  - `set_tag_managers(page_id, keys)`, which replaces the value;
  - `add_tag_managers(page_id, keys)`, which takes the union;
  - `tag_managers_of(page_ids) -> dict[str, tuple[str, ...] | None]`.
- **`WebsiteCrawls`:**
  - `_Page` and `_Rendered` carry `tag_managers`;
  - `_apply` sets them beside `replace_contact_methods`, and `_apply_renders` adds them beside `add_contact_methods`.
- **`app/research/absence.py`**, `apply_contact_form_only(lead_id, segment, methods, pages, now)`, runs in the caller's transaction and returns its counts. `_set_contact_methods` calls it, so it runs on both paths and only when `contact_methods` is written.
  - **Live rows in scope:** live Evidence with `type` `absence` and `extracted_by` `rule`, linked to any Signal of type `law_contact_form_only`.
  - **A confirmed row** gets `last_confirmed_at = now`.
  - **A new row** is written with:
    - `type` `absence`, `sought` `CONTACT_FORM_ONLY_SOUGHT`, `pages_checked` `methods.pages_checked`;
    - `source` `website`, `source_url` NULL, `retrieved_at = now`;
    - `extracted_by` `rule` (`EXTRACTED_BY_RULE`).

    It is linked to the Lead's current Signal of the type, or creates one (Attribute; Tier and `score_only` from the Segment; `first_seen_at = now`).
  - **Then** `retract_unsupported(lead_id, now)` runs, and `EvidenceChanged` is published for each row superseded or retracted.
- **Logging.** After the transaction commits, `enrich_lead` and `render_pages` log `absence_checked` (`lead_id`, `run_id`, `reason`, `confirmed`, `inserted`, `superseded`, `retracted`, `signals_created`, `signals_retracted`) whenever the rule ran.

**Never:**
- No AI call, and no change to `extract_lead`, the prompts or the schemas.
- No `events` row. No URL, text, HTML or match in a log line.
- No UPDATE or DELETE of Evidence beyond the repository's confirm, supersede and retract.
- No real browser and no network in tests (use `ScriptedRenderer` and the fake crawler).

### Decisions made overnight (pending user review)

1. **It runs when `contact_methods` is set, not in `extract_lead`.** That job may be deferred by the AI budget, die on a missing key, refuse, or send no changed page (for example a widget added with the same visible text). The rule needs none of that. The Absence is in place before the run's extraction starts, so the AC's "when extraction finishes" still holds.
2. **"No after-hours line" counts, a phone number doesn't,** as the Segment's name says: "(no chat, booking, or after-hours line)".
3. **When the Absence can't be verified** (a page needs rendering, or a tag manager is present or unknown), any live Absence is retracted. Claiming one from an older crawl would assert what the latest crawl couldn't confirm. A run that stored nothing, or whose render never applied, leaves the last value, as with `contact_methods`.
4. **What counts as a tag manager.** Tag managers count, because they can inject a chat widget: Google Tag Manager containers (`gtm.js`, `ns.html`, the `gtm.start` snippet), Segment, Tealium and Adobe Launch. The Google tag (`googletagmanager.com/gtag/js`, for Analytics and Ads) doesn't count: it loads only Google products and can't inject a chat. Pages checked before migration 0028 are "unknown", so no claim is made until a re-crawl.
5. **No `events` row** for a refusal: the `events` pattern records discarded model proposals and state changes. A log line with the reason code is used instead.
6. **The type key is a domain constant.** A new absence rule needs code anyway, and a Segment without the type is `not_in_segment`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Notes |
|---|---|---|---|
| Holds | Law Lead, 3 pages ≥ 500 chars, a `form_tag`, no chat, booking, after-hours or tag manager | 1 Absence (sought, all 3 pages, `rule`, `website`, no URL), a score-only Tier 3 Attribute Signal; `absence_checked` reason `holds`, inserted 1 | |
| Re-crawl, same pages | holds again | `last_confirmed_at` moves; nothing else | no `EvidenceChanged` |
| Re-crawl, other pages | holds, 4 pages | new row supersedes old, same Signal | `EvidenceChanged(old)` |
| Chat added | an Intercom script on re-crawl | Evidence retracted, Signal retracted | reason `chat_found` |
| Unverifiable | a page under 500 chars after its render failed; a GTM snippet; NULL `tag_managers` | nothing claimed; a live one retracted | `page_needs_render` / `tag_manager` / `tag_manager_unknown` |
| No form / Ops Lead | no form; an Ops-Automation Lead | nothing | `no_form` / `not_in_segment` |
| Render path | shell rendered to 1,200 chars with a form | Absence written by `render_pages`' apply | |
| gtag only | `googletagmanager.com/gtag/js` script | not a tag manager | |
| Merged / stale render | Lead merged; newer run | rule not run | no log |

</frozen-after-approval>

## Code Map

- `nextron/domain/widgets.py`:
  - reuse `_pairs`, `_host_and_path`, `_host_matches` and `_first_marker` for `tag_managers`;
  - `ContactMethods.found[ContactMethod.*]` and `pages_checked`. Don't change v1 `encode`/`decode`.
- `nextron/domain/crawl.py` -- `needs_render`.
- `nextron/domain/segments.py` -- `SegmentDef.signal_types`, `EvidenceType.ABSENCE`.
- `nextron/domain/extraction.py` -- `EXTRACTED_BY_RULE`, `EvidenceSource.WEBSITE`.
- `nextron/app/research/api.py`:
  - `_Page` (~712), `_Rendered` (~734), `_stored` (~1009, `detect(...)` there), `_render` (~1222);
  - `_apply` (~1024; `lead` is already read, so pass `lead.segment_id`);
  - `_set_contact_methods` (~1146) and `_apply_renders` (~1238), which today returns `bool` and can return the counts or `None`;
  - the `crawl_finished` and `render_finished` logs;
  - the module docstring (§ the Lead's `contact_methods`).
- `nextron/app/research/extraction.py`:
  - copy `_in_scope` (link-based claim), `_carry_out` and `_link` patterns;
  - `retract_unsupported`;
  - `current().publish(EvidenceChanged(...))`;
  - segment lookup via `setup_queries.segment_by_id(...).definition`.
- `nextron/db/repos/research/signals.py` -- `insert_evidence`, `confirm_evidence`, `supersede_evidence`, `retract_evidence`, `evidence(live_only=True)`, `signals`, `links`, `link`, `insert_signal`, `retract_unsupported`.
- `nextron/db/repos/research/crawl.py` (`replace_contact_methods` style), `db/models/crawl.py` `CrawledPageRecord`, `owners.py` (~283), `pii_registry.py` (~299).
- **Migration precedent:** `versions/0027_2_6_contact_methods.py` (`add_column`).
- **Head bumps to `0028_2_9_tag_managers`:**
  - `tests/contracts/test_persistence_contracts.py:81`, with its chain probes at ~804–817 (`0028_*` → `0029_*`, `0029_b` → `0030_b`);
  - `tests/unit/test_migrations.py:39`;
  - `tests/app/test_serve_process.py:324`;
  - `tests/unit/test_serve.py:833,847` (`0028_more` → `0029_more`);
  - `tests/contracts/test_source_tree.py` `FILES` (the migration, `domain/absence.py` and `app/research/absence.py`).
- **Tests:**
  - `tests/app/test_website_crawl.py` and `test_website_render.py`: the world; the Law Segment is first by key;
  - `tests/fakes/renderer.py` `rendered_page`;
  - existing Evidence or Signal count assertions in `test_signal_extraction.py` may need pages without a form.
- **Docs:** `nextron-engine/README.md`: "Signals and Evidence" (~3503), "Rendering and contact methods" (~3596) and the migration table (~1934).

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/widgets.py`, `nextron/domain/absence.py` -- tag managers, the rule, `reconcile_absence` -- pure, unit-tested (`tests/unit/test_widgets.py`, new `tests/unit/test_absence_domain.py`: every reason and its order, the gtag exclusion, every plan branch).
- [x] `nextron/db/alembic/versions/0028_2_9_tag_managers.py`, `db/models/crawl.py`, `owners.py`, `pii_registry.py`, `db/repos/research/crawl.py` -- the column and its repo functions.
- [x] `nextron/app/research/absence.py`, `nextron/app/research/api.py` -- the apply step, the plumbing and the log.
- [x] Tests -- new `tests/app/test_contact_form_only.py` covering every matrix row through `WebsiteCrawls` (crawl and render paths), the `EvidenceChanged` notices, the log keys, and idempotence; plus the head bumps and `FILES`.
- [x] `nextron-engine/README.md`, `_bmad-output/implementation-artifacts/deferred-work.md`:
  - **README:** the rule and the migration row;
  - **2.9 entry:** gets `status: resolved`;
  - **2.6 entry:** the Absence part is resolved, and the Chromium doctor check is still open;
  - **2.9's spec:** a Change Log line.

**Acceptance Criteria:**
- Given the full suite, when `pytest` and `lint-imports` run, then everything passes and every contract is kept.
- Given `serve` on a fresh data dir, when it starts, then the migration reaches `0028_2_9_tag_managers`.

## Implementation Notes

Built as specified; no deviation from the frozen text. What the spec left open, and where the
code had to choose:

- **`nextron/domain/widgets.py`.**
  - `TagManager` checks its values as `Signature` does (`ValueError`): a key of lowercase
    letters, digits and underscores; non-empty tuples of non-empty, stripped `str` hosts and
    markers; hosts lowercase, without a scheme.
  - `tag_managers` reuses `_pairs`, `_host_and_path`, `_host_matches` and `_first_marker`. It
    reads only `script` and `iframe` embeds (an `a` link loads nothing) and reads a non-`str`
    HTML as `""`. A container host's path must be `/gtm.js` or `/ns.html` or lie under it, so
    `gtag/js` and `gtm.jsx` match nothing. The module docstring documents the list and the
    Google tag exclusion.
- **`nextron/domain/absence.py`.**
  - The argument checks come before any reason, so a caller's mistake never hides behind
    `not_in_segment`:
    - `TypeError` for a `segment` that isn't a `SegmentDef`, `methods` that isn't a
      `ContactMethods`, or a page that isn't a `CheckedPage`;
    - `ValueError` when the pages aren't exactly `methods.pages_checked`, or a URL is listed
      twice.
  - `CheckedPage` type-checks its fields (`TypeError`), and its `url` is non-empty.
  - The values:
    - `LiveAbsence(id, sought, pages_checked, created_at)`;
    - `AbsencePlan(confirm: str | None, insert: bool, supersede: str | None, retract: tuple)`.
  - `reconcile_absence` sorts `live` itself, by (`created_at`, `id`).
  - "The same `pages_checked`" is tuple equality. The rule always writes the sorted tuple that
    `ContactMethods` holds.
  - A holding Absence with an empty `pages_checked` is `ValueError`, since the rule never holds
    with no page. Wrong argument types are `TypeError`.
- **Persistence** (`0028_2_9_tag_managers`, down `0027_2_6_contact_methods`).
  - `set_tag_managers` writes `json.dumps(sorted(set(keys)), separators=(",", ":"))`, so the
    value is canonical whatever order it is given in. `tag_managers_of` therefore returns keys
    sorted, not in `TAG_MANAGERS` order.
  - `add_tag_managers` on a NULL value writes just the keys given.
  - `StoredPage` and `_PAGE_COLUMNS` are unchanged. The column is read only through
    `tag_managers_of`.
  - The owner is `research`, and the PII class is `none`.
- **`nextron/app/research/absence.py`.**
  - `AbsenceApplied(reason, confirmed, inserted, superseded, retracted, signals_created,
    signals_retracted)`:
    - `inserted` counts a new row that supersedes nothing;
    - `superseded` counts a row that a new row replaced;
    - so the new rows are `inserted + superseded`, and each count is 0 or 1 except `retracted`.
  - **Write order:** confirm, then the new row (and its `superseded_by` and link), then the
    retractions, then `retract_unsupported`. `EvidenceChanged` follows: the superseded row,
    then each retracted row in plan order.
  - **Linking.** The new row is linked to the Lead's first current Signal of the type, by
    `first_seen_at` then id. A created Signal has class `attribute`, the Segment type's Tier and
    `score_only`, no event date, and `first_seen_at = now`.
  - **`retract_unsupported`** retracts any unsupported Signal of the Lead, as the extraction's
    does.
  - **Scope.** The live rows in scope also include those linked to a retracted Signal of the
    type. A live row's Signals are always current, so this changes nothing in practice.
- **`nextron/app/research/api.py`.**
  - `_set_contact_methods(lead_id, segment_id, run_id, now) -> AbsenceApplied`. A Lead whose
    Segment has no row raises `RuntimeError`, as `extract_lead` does.
  - `_Run.absence` carries the result to the log.
  - `_apply_renders` returns `AbsenceApplied | None`, and `render_finished.applied` is
    `absence is not None`.
  - `absence_checked` is logged after `crawl_finished` or `render_finished`. Its `run_id` is
    the crawl run's id.
  - The module docstring documents the step, the log and when the rule doesn't run.
- **Merges** (a consequence, not separately tested). The merged Lead's Absence rows and Signal
  move to the survivor with `reparent_lead`. The survivor's next complete detection then does
  one of two things:
  - when the Absence holds, it confirms the oldest matching row and retracts the rest; the
    duplicate Signal, left unsupported, is retracted;
  - otherwise it retracts them all.
- **Tests.**
  - `tests/unit/test_widgets.py`, Story 2.9 part: the list; each host from a script and an
    iframe and never from a link; each marker in any case; the container snippet and its
    `noscript` frame; the Google tag; the order and repeats; garbage; a broken iterable; a bad
    `TagManager`.
  - `tests/unit/test_absence_domain.py`:
    - every reason, with a parametrized first-reason-wins table for the order;
    - a phone number not refuting the Absence;
    - the page coverage and type checks, and the `repr`;
    - every `reconcile_absence` branch: insert, nothing, retract all, confirm and retract the
      rest, a different `sought`, supersede the oldest and retract the rest, the id tie-break,
      tuple equality, no pages;
    - the plan invariant.
  - `tests/app/test_contact_form_only.py`:
    - every matrix row, on the crawl world with an `EvidenceChanged` recorder;
    - the render path, the stale and merged renders, and a run that stored nothing;
    - idempotence over three crawls;
    - the log keys, with no page words or URLs;
    - an AI extraction that retracts its own quote and leaves the Absence and its Signal live,
      on `tests/app/test_signal_extraction.py`'s `build_world`.
  - The head bumps (`test_persistence_contracts`, `test_migrations`, `test_serve_process`,
    `test_serve`) and `FILES` (the migration and the two `absence.py` modules).
- **Docs.**
  - `nextron-engine/README.md`: the migration row, a "Contact form only" part in "Signals and
    Evidence" (replacing its "Not yet" line), and the tag managers in "Rendering and contact
    methods".
  - `deferred-work.md`: the 2.9 entry is `resolved`; in the 2.6 entry the Absence part is
    resolved and the Chromium check stays open.
  - Story 2.9's spec gets a Change Log line.

**Implementation verification** (targeted; the full suite is the orchestrator's gate):
- **Tests.** The spec's command, plus the other touched files `tests/unit/test_serve.py` and
  `tests/app/test_serve_process.py`, run with `-n 4`: 2066 passed and 10 skipped. The skips are
  the opt-in `ai_eval` check and nine AD-16 "not built yet" probes.
- **`lint-imports`:** 29 kept, 0 broken.
- **Isolation.** No browser was launched, nothing reached the network, and no Claude request
  was made.

**Review fixes (pass 1)** (tests only; no code changed):
- `tests/app/test_contact_form_only.py`:
  - `test_a_render_records_the_tag_managers_its_html_loads`: a shell rendered with the GTM
    snippet stores `["google_tag_manager"]`, gives reason `tag_manager`, and claims no Absence.
  - `test_a_render_adds_to_the_tag_managers_the_crawl_stored_and_never_replaces_them`: a shell
    whose crawled HTML loads GTM, rendered without it, keeps the key and gives `tag_manager`.
  - `test_an_absence_after_a_retraction_gets_a_new_signal_and_tag_managers_are_replaced` walks
    four crawls:
    1. the Absence holds;
    2. a chat retracts it and its Signal;
    3. GTM is stored, with reason `tag_manager`;
    4. a plain crawl stores `[]` and gives `holds` with `inserted=1` and `signals_created=1`.

    The fourth crawl leaves two Signal rows, the old one retracted and the new one current and
    linked to the new row, and publishes no `EvidenceChanged`.
  - `test_a_page_that_now_needs_rendering_retracts_a_live_absence` now also checks the state
    between the crawl that queues `render_pages` and the render. The Absence and its Signal are
    untouched, and no `absence_checked` line is logged.
- `tests/unit/test_migrations.py`, `test_0028_leaves_every_existing_page_unchecked`: a
  `crawled_pages` row stored at `0027_2_6_contact_methods`, with its Segment, Lead and crawl
  run, reads `tag_managers` NULL after the upgrade to `0028_2_9_tag_managers`.
- `tests/unit/test_absence_domain.py`: a new `ORDER_CASES` row, an Ops Segment with no page
  checked, gives `not_in_segment` over `no_pages`.

**Final verification** (on the final tree, after the review fixes): the full suite through
`suite_lock.py` (track-D), run once: 12591 passed and 12 skipped (the two `ai_eval` tests, the
symlink test and nine AD-16 "not built yet" probes). `lint-imports`: 29 kept, 0 broken. No
browser was launched, nothing reached the network, and no Claude request was made.

## Spec Change Log

## Review Triage Log

Pass 1 (blind hunter BH, edge-case hunter EC, verification gap VG): 22 raw findings (BH 14, EC 4, VG 4), deduplicated to 17 entries (BH1+EC4, BH4+EC1, BH5+VG2, BH7+EC2, BH10+VG4). 7 patched (tests and one doc line), 0 deferred, 10 rejected. Three of the rejections are rejected only because the fix would change the approved spec (#1, #3, #4); each gets a `deferred-work.md` entry and is reported to Mac, following the track's standing practice for frozen-spec changes. No `intent_gap` or `bad_spec` loopback.

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | BH1 + EC4: script loaders that can inject a chat but aren't in `TAG_MANAGERS`: HubSpot's tracking code (`js.hs-scripts.com`, which loads HubSpot chat when it is on), first-party server-side GTM without the snippet, Cloudflare Zaraz, Piwik PRO, Ensighten, Commanders Act, Matomo Tag Manager, RudderStack. Such a site with a form reads `holds`. | medium | True: `tag_managers` returns `()` for them, and no chat signature matches the loader. The harm is a score-only Tier 3 Attribute (FR-11: drafts never cite score-only Attributes), so it only nudges the score. The list is fixed in the frozen Boundaries ("`TAG_MANAGERS`, in this order"). | reject: would change the approved spec (reported to Mac) + deferred-work entry |
| 2 | BH2: markers are searched across the whole raw HTML, so a link to `cdn.segment.com/docs` or prose "utag.js" counts as a tag manager, although the docstring says "an `a` link loads nothing". | low | True but on the safe side (the rule then claims nothing). The docstring is accurate: an `a` URL is never host-matched; markers are raw-HTML substrings, as the frozen spec and `Signature` define them. | reject (low; spec-defined marker semantics, errs safe) |
| 3 | BH3: a page that rendered successfully but is genuinely under 500 characters (a short contact page) blocks the Absence forever, because nothing records that a render succeeded. | medium | True. The frozen rule (and the caller's condition "no checked page still needs rendering (text under 500 characters, `needs_render`)") is text length alone. Fixing it needs a per-page render outcome. | reject: spec-mandated (reported) + deferred-work entry |
| 4 | BH4 + EC1: the rule ignores a partial run: a page fetch that errored, a 429 throttle, a robots-skipped page or the deadline. A booking page that timed out still lets the Absence hold on the pages stored. | medium | True: `_apply` runs the rule whenever `run.pages` is non-empty. The Evidence lists exactly the pages checked, so the claim is accurate for them; the 15-page cap makes every crawl partial anyway. Not running the rule, or a new `incomplete` reason, changes the frozen reason list and the "runs whenever `contact_methods` is set" rule. | reject: would change the approved spec (reported) + deferred-work entry |
| 5 | BH5 + VG2: no test shows that a render records the tag managers its HTML loads, or that `add_tag_managers` adds to (never replaces) what the crawl stored; the repository functions have no direct test. | gap (pre-verified) | VG2 filed with evidence: dropping `tag_managers=` in `_render`, or calling `set_tag_managers` in `_apply_renders`, passes every test. That would let a JavaScript-built page that loads a tag manager get a false Absence. | patch (tests) |
| 6 | BH6: `tag_managers_of` reads the column with a bare `json.loads`, so a stored `{"x":1}` reads as a key and `null` raises. | false | `set_tag_managers` is the column's only writer (grep), and it always writes a canonical sorted array; no path stores anything else. | reject |
| 7 | BH7 + EC2: an exception in the rule (a Segment row missing, a stored Segment that no longer validates) now rolls back the whole crawl or render apply. | low | True in principle, but no reachable path was shown: `leads.segment_id` is a foreign key, and a stored Segment that stops validating would already kill every `extract_lead`. It fails loudly (job retry, then a dead-job alert). | reject |
| 8 | BH8: merges are untested; after `reparent_lead` the survivor shows two Absence rows and two Signals until its next detection, and `_link` may link the new row to a Signal other than the superseded row's. | low | True. The next detection converges to one live row on one current Signal (the rest retracted, the unsupported Signal retracted). The duplicate Signals after a merge are the existing 2.9 merge-fold deferred entry. | reject (low; covered by the 2.9 merge-fold entry) |
| 9 | BH9: `absence_checked.signals_retracted` counts every unsupported Signal of the Lead, not only this type. | low | True, but the extraction always retracts its own unsupported Signals, so in practice only this type is ever unsupported here. The Implementation Notes say so. | reject (low, cosmetic) |
| 10 | BH10 + VG4: no migration test shows that pages stored before 0028 read NULL ("never checked"). | gap (pre-verified) | VG4 filed with evidence: a `server_default` of `'[]'` would pass every test and turn unchecked pages into "checked, none". `test_0026_keeps_every_activation_and_its_partial_index` is the pattern. | patch (test) |
| 11 | BH11: the 2.6 deferred entry doesn't say that, until Chromium is installed, every render fails, so any Lead with a near-empty page never gets (and loses) the Absence. | low | True, and a direct correction of one doc line. | patch (doc) |
| 12 | BH12: the diff leaves out the spec it references; code says "Story 2.9" while docs say "2.9a". | false | The review diff excludes the spec by design (it is the claims file); it is committed with the change. "Story 2.9" in code is intended: this completes 2.9 (the migration name follows `<epic>_<story>`); 2.9a names the spec. | reject |
| 13 | BH13: `ORDER_CASES` has no row showing that `not_in_segment` comes before `no_pages`. | low | True; a one-row addition to the table. | patch (test) |
| 14 | BH14: no test shows that a crawl which queues a render leaves a live Absence and its Signal untouched and logs no `absence_checked`. | low | True; a few assertions in the existing render test. | patch (test) |
| 15 | EC3: `_link` hard-codes `attribute`, and `_offers_absence` doesn't require `website` among the type's sources. | false | `_check_signal_rules` refuses any Event whose evidence isn't exactly `[quote]`, so a type with `absence` Evidence is always an Attribute. The frozen `not_in_segment` definition names only the key and `absence` Evidence. | reject |
| 16 | VG1: no test shows that an Absence which holds again after a retraction creates a new current Signal (and the retracted one stays retracted). | gap (pre-verified) | Filed with evidence: linking to a retracted Signal (`current_only=False`) passes every test and loses the Attribute. | patch (test) |
| 17 | VG3: no test shows that a re-crawl replaces a page's tag managers, so a removed tag manager stops blocking. | gap (pre-verified) | Filed with evidence: `add_tag_managers` in `_apply` passes every test. Cheap to cover in #16's test. | patch (test) |

## Verification

**Commands** (from `e:/Nextron-track4/nextron-engine`):
- `uv run pytest -q tests/unit/test_widgets.py tests/unit/test_absence_domain.py tests/app/test_contact_form_only.py tests/app/test_website_crawl.py tests/app/test_website_render.py tests/app/test_signal_extraction.py tests/unit/test_migrations.py tests/contracts` -- all pass.
- `uv run lint-imports` -- every contract kept.
