---
title: 'Story 2.15: Score and tier every enriched Lead'
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: '35e55f16b5bfc828f6a8c18ddc5f3fa50323da1a'
review_loop_iteration: 0
story_key: '2-15-score-and-tier-every-enriched-lead'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-2-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Leads get Signals, Evidence and a Primary Contact, but nothing turns them into a Fit Score, Signal Score, Lead Score and Lead Tier. Nothing applies the Segment's disqualifiers, and nothing re-tiers a Lead when its Events age out. Mac can't see which Leads have a live reason to write, or why.

Requirements: FR-17, FR-62, FR-1 · AD-3, AD-4, AD-7, AD-10.

**Approach:**
- A pure `nextron/domain/scoring.py` holds the formulas, `tier(events, now)`, the disqualifier check and the size reading.
- The `scoring` service owns a new score group on `leads` (migration `0031_2_15_scoring`). It writes it through `scoring.rescore` and the daily `recompute_tiers`.
- A new `score_lead` job ends the research chain. A matching disqualifier closes the Lead through `pipeline.close`.
- The Lead page gains a Score panel with a "Re-score" POST. The Leads list gains Tier and Lead Score columns and a Tier sort.

The acceptance criteria are Story 2.15 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where the decisions below say otherwise.

### Decisions made overnight (pending user review)

1. **Weights are whole percents.** `lead_score_fit_weight` and `lead_score_signal_weight` are `integer` settings (Engine section), 0 to 100, default 50 (meaning 0.5). `Setup.save` and `Setup.set` refuse a change that leaves the two effective values not summing to 100, with rule `weights_sum` on both keys. Lead Score = `(fw × Fit + sw × Signal + 50) // 100`, which rounds half up.
2. **Rounding.** Each factor keeps its exact points, written to 2 decimals. Fit and Signal Scores are `min(100, floor(sum + 0.5))` of the exact sums.
3. **Count a Signal identity once.** A Signal type contributes once.
   - For an Event type, that is its live, open Event with the highest decay factor. Ties go to the younger, then the earlier `first_seen_at`, then the smaller id.
   - For an Attribute type, it is the earliest current Signal.
   - Duplicates from merges and from several postings of one type count once. `tier` still looks at every live Event.
   - A Signal whose type isn't in the Lead's Segment counts for nothing, and neither does a stored class or Tier the domain refuses.
4. **Live Events.** An Event is live when `signals.is_live` holds and its `weight_factor` is above 0. A job-posting Event (one with a `job_postings` row) also needs that posting `live`. Retracted Signals are never read. `tier` gives A for any live Tier 1 Event, B for a live Tier 2 Event with no Tier 1, and C otherwise; a Tier 3 Event gives C.
5. **Size readings.**
   - A clue's reading is the live size-fact Evidence row (`fact` = the clue, `number` set) with the latest `retrieved_at`, then the largest id.
   - For `lawyers_firm_wide` only, when it has no fact row, the reading is the live `count` row with `sought` `lawyers listed` (same order).
   - Size fit unit mapping: `lawyers` reads `lawyers_firm_wide`, `staff` reads `staff`, and `usd_revenue` is always unknown (25 points), because no revenue source exists (R-18).
   - Inside means `minimum ≤ n ≤ maximum`.
6. **Disqualifiers.**
   - They run in Segment order and the first match wins: `fewer_than` is `n < threshold`, `more_than` is `n > threshold`. A clue with no reading never matches.
   - A match calls `pipeline.close(lead, disqualified, cause_ref=<the reading's Evidence id>, cause=StageCause.SEGMENT_DISQUALIFIER, actor="system:scoring")`. It stores `disqualified_rule` and `disqualified_evidence`, sets the four scores, `tier` and the factors to NULL, and sets `score_segment_version` and `tier_computed_at`.
7. **Which Leads are scored.** `score_lead` skips, logging `score_skipped` with a reason, a Lead that is:
   - unknown (`unknown_lead`);
   - merged (`merged_lead`);
   - closed, meaning `lost` or `disqualified` (`lead_closed`);
   - without an `ok` signal extraction (`not_enriched`);
   - in a Segment that is missing (`unknown_segment`).

   `recompute_tiers` re-tiers every Lead that has a stored `tier`, isn't merged and isn't `lost` or `disqualified`.
8. **Re-tiering keeps the Segment version.** The Segment table holds only the current version, so `recompute_tiers` doesn't re-read the Segment.
   - It re-evaluates the stored Event factors at `now`, using each factor's stored weight and Tier against the Signal's current row. An Event that is retracted, missing, closed or not live is dropped.
   - It recomputes the Signal Score, the Lead Score (current weights), `tier` and `tier_computed_at`. Fit and its factors stay as stored.
   - When it drops an Event factor, it also calls `research.select_primary(lead)` in the same transaction (2.10's deferred K).
   - A Lead not re-scored keeps its `score_segment_version`. Only `score_lead` (chain, Event changes, "Re-score") applies the current Segment.
9. **Enqueue points for `score_lead`** (scope the Lead, discriminator `tick`, payload `{v, lead_id}`):
   - `PrimaryContacts.select_primary_contact` (the chain's current end; 2.13 and 2.14 will move it later), when a selection ran;
   - `JobBoards.recheck_event`, when it closed or reopened a posting;
   - the Lead page's `POST /leads/{lead_id}/rescore`, on the interactive lane.

   Default lane bulk, lanes bulk and interactive, priority 40, 5 minutes, retry limit 3, group enrichment. `recompute_tiers` is bulk only, priority 20, 10 minutes, retry limit 2, `Every(1 day, at_startup=True)`.
10. **Legal form isn't used.** This AC doesn't use legal form, so no 2.12 seam is needed.

## Boundaries & Constraints

**Always:**
- Tier is computed only by `domain.scoring.tier`, Event age only by `domain.signals.age_days`, and decay only by `domain.signals.weight_factor` (AD-7).
- Only `nextron.db.repos.scoring` writes the score group. `scoring` reads other owners only through `nextron.app.queries.*` and calls `pipeline` and `research` only through their `api`.
- Every new column goes in `owners.py` (`scoring`) and in `pii_registry.py` (`none`).
- Domain functions take `now` and do no I/O.

**Never:**
- No Eligibility display copy, `daily_capacity` or selection (later stories).
- No new notification, because AD-3's list is closed.
- No score on a merged or closed Lead.
- Unknown size never disqualifies.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected |
|---|---|---|
| Law, 20 lawyers, live T1 hiring (age 10) + contact-form-only | weights 60, 15 | Fit 65, Signal 60, Lead 63, Tier A |
| T1 Event age 60 | weight 60 | factor 0.5, 30 points, Tier A |
| T1 Event age 90 | — | not live, Tier C, 0 points |
| Two postings of one hiring type | both live | counted once |
| Posting closed, Signal still current | — | Event not live |
| 3 lawyers listed, no fact | `law_fewer_than_5_lawyers` | Disqualified, `segment_disqualifier` event (actor `system:scoring`), no scores |
| Size unknown | Law | 25 size points, never disqualified |
| Ops Lead | `usd_revenue` | 25 size points |
| Weights saved as 60/30 | Settings | `weights_sum` refusal, nothing stored |
| Lead lost | `score_lead` | skipped `lead_closed` |

</frozen-after-approval>

## Code Map

- `nextron/domain/signals.py` -- reuse `age_days`, `weight_factor`, `is_live`; don't change.
- `nextron/domain/segments.py` -- `SegmentDef.size_range`, `disqualifiers` (`DisqualifierKind`, `SizeClue`), `signal_types` (`weight`, `tier`, `signal_class`); `SizeUnit`.
- `nextron/domain/extraction.py` -- `FactKind`, `SIZE_FACTS`. `nextron/domain/people.py` -- `LAWYERS_LISTED`.
- `nextron/domain/pipeline_stage.py` -- add `StageCause.SEGMENT_DISQUALIFIER = "segment_disqualifier"`.
- `nextron/app/pipeline/api.py` -- `Pipeline.close(lead_id, stage, cause_ref)`. Add the keyword-only `cause: StageCause | None = None` and `actor: str | None = None` (defaults: the machine's cause and `system:pipeline`); update the docstring.
- `nextron/app/queries/research.py` -- `signals`, `evidence`, `last_ok_extraction`, `job_postings` (`StoredPosting.signal_id`, `.live`). Add `signal_evidence_ids(lead_id) -> dict[str, tuple[str, ...]]`, built from `signals_repo.links` filtered to live Evidence, each tuple sorted.
- `nextron/app/queries/setup.py` -- `segment_by_id` (`SeededSegment.version`, `.definition`); `get(Key...)`.
- `nextron/app/queries/pipeline.py` -- `stage`. `nextron/app/queries/discovery.py` -- `lead`, `lead_list`.
- `nextron/app/scoring/api.py`, `nextron/db/repos/scoring/__init__.py`, `nextron/app/queries/scoring.py` -- empty today; fill them.
- `nextron/app/research/people.py` -- `PrimaryContacts(clock, research)` → `(clock, research, queue)`. The constructors are in `main.py` and `tests/dashboard.py`, plus three tests (`test_job_boards`, `test_people_extraction`, `test_primary_contact`).
- `nextron/app/research/job_boards.py` -- `recheck_event` apply: enqueue as in `_apply`'s `SELECT_PRIMARY_CONTACT_JOB` call.
- `nextron/app/research/api.py:795` -- `Research.select_primary(lead_id)` (`@command`).
- `nextron/app/setup/api.py:718-784` -- `set`/`save`, for the `weights_sum` check. `nextron/domain/settings_schema.py` -- declare the keys; `Section`.
- `nextron/jobs/handlers.py` -- thin handlers, like `select_primary_contact`. `nextron/jobs/triggers.py` -- `Trigger.RECOMPUTE_TIERS` already exists.
- `nextron/main.py` -- `build_job_types` (add `scores`), `build_triggers` (`PURGE_PAGE_TEXT` pattern), service wiring, `create_app(..., lead_scores=...)`.
- `nextron/db/models/leads.py` + `db/alembic/versions/0028_*` -- the pattern for adding columns in place.
- `nextron/web/leads_page.py`, `web/app.py:1291-1360`, `templates/lead.html`, `templates/leads.html` -- the views. `POST /leads/{lead_id}/primary` is the pattern for an HTMX POST.
- `nextron/db/repos/discovery/leads.py` -- `listed`/`count_listed`. Add `sort` and read `tier`/`lead_score` read-only, as `pipeline_stage` is read.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/scoring.py` -- add:
  - `Tier`, `FactorKind` (`size_in_range`, `size_unknown`, `size_outside`, `attribute`, `event`);
  - `ScoringSignal(signal_id, signal_type, signal_class, tier, event_date, first_seen_at, evidence_ids, open=True)`, `SizeEvidence(evidence_id, clue, number, retrieved_at, counted)` and `Factor(kind, key, points, weight, tier, signal_id, evidence_ids)`;
  - `Disqualified(rule_key, evidence_ids)` and `Score(fit, signal, lead, tier, fit_factors, signal_factors)`;
  - `size_readings`, `disqualifier`, `fit_score`, `signal_score`, `lead_score`, `tier`, `score`, `retier`, `factors_text`/`factors_from_text` (canonical JSON), and the weight-sum check.

  Write the module docstring as the contract.
- [x] `tests/unit/test_scoring.py` -- the matrix and decisions 2–6 and 8, with a fixed `now`, including the decay boundaries (30, 31, 89, 90) and the caps at 100.
- [x] `nextron/db/alembic/versions/0031_2_15_scoring.py` + `db/models/leads.py` -- add nullable `leads` columns: `fit_score`, `signal_score` and `lead_score` (Integer), `tier` (String(1)), `tier_computed_at`, `score_segment_version` (Integer), `score_factors` (Text), `disqualified_rule` (String(31)) and `disqualified_evidence` (Text). Add them in place; no CHECK. Bump `HEAD`, the chain probes and `FILES`; map them in `owners.py` and `pii_registry.py`.
- [x] `nextron/db/repos/scoring/scores.py`, `app/queries/scoring.py` -- `write_score`, `write_disqualified`, `stored(lead_id) -> StoredScore | None` (factors decoded) and `retier_candidates()`.
- [x] `nextron/app/scoring/api.py` -- `Scoring(clock, pipeline, research)`:
  - `@command rescore(lead_id) -> RescoreOutcome`;
  - `@command retier(lead_id) -> bool`.

  Add `nextron/app/scoring/lead_scores.py`, `LeadScores(clock, scoring, queue)`:
  - `@use_case score_lead(lead_id)`;
  - `@use_case recompute_tiers()`, with one transaction per Lead;
  - `@use_case request_rescore(lead_id) -> bool`.

  Log `lead_scored`, `lead_disqualified`, `score_skipped` and `tiers_recomputed` with IDs and counts only.
- [x] Pipeline, research, setup and settings edits, per the Code Map and decisions 1, 6 and 9.
- [x] `nextron/jobs/handlers.py`, `nextron/main.py` -- add the `score_lead` and `recompute_tiers` job types, the trigger, and the wiring.
- [x] Web:
  - The Score panel, `templates/lead_score.html`, goes below the header, outside `hx-disable` for its form only. It shows Tier, Fit, Signal and Lead Scores, the computed time and the Segment version. Each factor gets a row with its label, points and Evidence links `#evidence-<id>`; each Evidence row gets `id="evidence-<id>"`.
  - A Disqualified Lead shows its rule's description and Evidence links instead. A Lead with no score shows "Not scored yet."
  - Add the "Re-score" form (`method="post"`, `hx-post`, `csrf_field()`) and `POST /leads/{lead_id}/rescore` (`@personal_data(True)`). It enqueues and swaps in the panel with "Re-score queued." An unknown Lead answers 404.
  - On the list, add Tier and Lead Score columns ("—" when unset) and `sort=tier` (Tier A to C, unscored last, then Lead Score descending, then newest). Column links keep the filters; any other `sort` value is `MalformedForm`.
- [x] Tests:
  - `tests/app/test_lead_scoring.py`: chain enqueue, rescore, disqualify (events row cause and actor), skips, recompute aging with a Tier change and the selection re-run, and the Segment version kept until Re-score.
  - Settings `weights_sum`.
  - Web routes and the list sort.
  - Update the pins.
- [x] `nextron-engine/README.md` -- add a Story 2.15 section.

**Acceptance Criteria:**
- Given the seeded Segments, when `nextron seed sync` changes the Law Segment, then no stored score changes until `score_lead` runs for that Lead.
- Given a Lead whose only Tier 1 Event ages past day 89, when `recompute_tiers` runs, then its Tier drops without a crawl and its Primary selection re-runs.

## Implementation Notes

**Files.**
- Domain: `nextron/domain/scoring.py` (new; the module docstring is the contract). `StageCause.SEGMENT_DISQUALIFIER` in `domain/pipeline_stage.py`; `Key.LEAD_SCORE_FIT_WEIGHT` and `Key.LEAD_SCORE_SIGNAL_WEIGHT` (integer, Engine, 0 to 100, default 50) in `domain/settings_schema.py`.
- Persistence: migration `0031_2_15_scoring` (revision `0031_2_15_scoring`, down `0030_2_11_job_postings`), nine nullable `leads` columns added in place (`tier_computed_at` as `String(27)`, the engine's UTC text). `db/models/leads.py`, `owners.py` (`scoring`), `pii_registry.py` (`none`). `db/repos/scoring/scores.py`: `stored_row`, `write_score`, `write_disqualified`, `write_tier` (re-tier: keeps fit and Segment version) and `retier_candidates`.
- Queries: `queries/scoring.py` (`StoredScore`, `stored`, `retier_candidates`); `queries/research.signal_evidence_ids`; `queries/discovery.lead_list(sort=...)` with `SORT_TIER`, and `repos/discovery/leads.listed(sort=...)` reading `tier`/`lead_score` read-only.
- Service: `app/scoring/api.py` (`Scoring.rescore`, `Scoring.retier`, `RescoreOutcome`), `app/scoring/lead_scores.py` (`LeadScores.score_lead`, `recompute_tiers`, `request_rescore`, `enqueue_score_lead`).
- Edits: `Pipeline.close(..., *, cause=None, actor=None)`; `PrimaryContacts(clock, research, queue)` enqueues `score_lead` after a selection ran; `JobBoards.recheck_event` enqueues it when the apply closed or reopened a posting; `Setup.set`/`save` `weights_sum` check.
- Jobs: `handlers.score_lead`, `handlers.recompute_tiers` (`ScoringLeads` protocol); `build_job_types(..., scores=)`; `Trigger.RECOMPUTE_TIERS` registered `Every(1 day, at_startup=True)` after `purge_page_text`; wiring in `build_app` and `create_app(..., lead_scores=)`.
- Web: `templates/lead_score.html` (new), `lead.html` (panel include, `id="evidence-<id>"` on Evidence rows), `leads.html` (Tier and Lead Score columns, sort links); `leads_page.py` (`ScorePanel`, `FactorRow`, `score_context`, `submitted_rescore`, `rescore_queued`, `sort_href`, `ListFilters.sort`); `POST /leads/{lead_id}/rescore` (`lead_rescore`).

**Choices made while building (within the approved spec).**
- Exact arithmetic: decay factors are recovered as exact fractions (`Fraction(weight_factor(...)).limit_denominator(1000)`, exact for `(90 - age) / 60`), so the half-up rounding never trips on float error. `Factor.points` is the 2-decimal value stored and shown.
- A counted Signal must have its stored class equal to its Segment type's class; otherwise it counts for nothing (conservative reading of Decision 3).
- Size factors: `key` is the clue read (`lawyers_firm_wide`, `staff`) or, when unknown, the unit (`usd_revenue`, `lawyers`); `weight`, `tier` and `signal_id` are null.
- `retier` returns `True` when it re-tiered; it returns `False` (writing nothing) for a Lead whose stored factors no longer decode, so a corrupt row waits for a Re-score rather than being zeroed. `tiers_recomputed` logs `leads` (candidates) and `retiered`.
- `score_lead` carries no concurrency key (no network work). `request_rescore` refuses an unknown Lead (404) and a merged or closed one (a notice; see Review fixes).
- `recompute_tiers` is in job group Enrichment.
- The Leads list's Created heading is a link (back to newest first) only while the list is sorted by Tier; only the Tier heading links to `sort=tier` (see Review fixes).
- A factor's Evidence link targets the live Evidence rows on the page; a since-superseded row has no anchor.

**Verification (implementer).** `tests/unit/test_scoring.py` (44), `tests/app/test_lead_scoring.py` (10), new tests in `test_leads_page.py`, `test_setup_settings.py`, `test_pipeline_close.py`, `test_job_contracts.py`, `test_job_boards.py`; every touched test file, `tests/contracts` and `lint-imports` (29 kept).

**Review fixes (after the first review pass).**
- `request_rescore` returns `RescoreRequest` (`queued`, `unknown`, `merged`, `closed`); a merged or closed Lead queues nothing and the panel answers with an alert ("This Lead is closed; it can't be re-scored." / "This Lead was merged into another Lead; it can't be re-scored."); unknown stays 404.
- The Score panel links a factor's or a disqualifier's Evidence only when that Evidence is live (on the page); others read "Evidence <n> (no longer live)" (`EvidenceLink`, `score_panel(..., live)`).
- `lead_scores.py` docstring: `enqueue_score_lead` is this service's enqueue; research enqueues the same key and payload inline.
- README 2.15: the Lead Score formula shows the ÷ 100; a re-score from the chain or a posting re-check also applies the current Segment.
- Leads list: only the Tier heading links to `sort=tier`; Lead Score is a plain heading.
- `lead.html`: an Evidence row with no id omits the `id` attribute.
- Tests: the exact recovered decay (Tier 1 ages 0..120, Tier 2); a re-check that reopens a posting queues `score_lead`; `recompute_tiers` leaves a corrupt `score_factors` row unchanged and a scored-then-merged Lead untouched; the Lead page renders with corrupt factors; Re-score on closed and merged Leads; non-live Evidence not linked.
- Gate fix: `tests/unit/test_health_page.py`'s `NEVER_RUN` pin lists `score_lead` and `recompute_tiers` in the Enrichment group (the first full-suite run caught it).

## Spec Change Log

## Review Triage Log

Pass 1: 26 raw findings (blind hunter BH1–BH16, edge-case hunter EC1–EC7, verification gap VG1–VG3). They group into 18 entries: 8 patch, 2 defer and 8 reject.

| ID | Source | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|---|
| A | BH2, EC4, part of BH16 | "Re-score" on a closed or merged Lead answers "Re-score queued.", but the job always skips. | medium | `request_rescore` refuses only an unknown Lead; `rescore` skips `lead_closed` and `merged_lead`. | patch: refuse with a notice, no enqueue |
| B | BH6, EC5 | Factor and disqualifier Evidence links point at Evidence rows that are no longer live, so their anchors aren't on the page. | low | `lead.html` anchors only live Evidence; `retier` keeps the stored fit factors' ids. | patch: link only live ids, else plain text |
| C | BH4 | The `lead_scores` docstring calls `enqueue_score_lead` "the one enqueue", but `people.py` and `job_boards.py` inline their own. | low | Verified: three call shapes. | patch: correct the docstring |
| D | BH10, BH8 | The README's Lead Score formula has no ÷100, and it says a seed sync changes no score until a re-score, without naming the posting re-check's full re-score. | low | Verified in the README. | patch: correct the README |
| E | BH11 | The "Lead Score" heading links to the same `sort=tier` URL as "Tier". | low | Verified in `leads.html`. | patch: make Lead Score a plain heading; the Tier link sorts Tier, then score |
| F | BH12 | `EvidenceRow.evidence_id` defaults to `""`, which renders `id="evidence-"`. | low | Verified: the dataclass default. | patch: leave out the id when it is empty |
| G | BH15 | Nothing pins the exactness of the decay recovered by `limit_denominator`. | low | No test compares it with `Fraction(90 - age, 60)`. | patch: test ages 0 to 120 |
| H | VG1, VG2, VG3 | Missing tests: a re-check that reopens a posting queues `score_lead`; unreadable stored factors (in `recompute_tiers` and on the Lead page); a merged Lead in the recompute skip test. | gap | Pre-verified by the layer. | patch: add the tests |
| I | BH3 | An interactive Re-score is absorbed by a live bulk `score_lead` with the same `tick` key and waits behind the bulk backlog. | medium | The jobqueue absorbs on the idempotency key. Promoting a lane is new jobqueue surface. | defer |
| J | BH7, EC2 | A merge doesn't re-score the survivor. An operator reopen of a Disqualified Lead keeps the disqualified panel, and a later `score_lead` closes it again. | medium | No merge subscriber enqueues `score_lead`. The reopen UI doesn't exist yet (4.10). | defer |
| K | BH1 | `Setup.set` can't move a weight away from 50, because the other weight still holds its value. | low | That is Decision 1's rule; `save` moves both. | reject (the spec) |
| L | BH5, EC1, EC6 | One raising Lead aborts the day's `recompute_tiers`; stored weights that don't sum to 100 stop all scoring. | low | The weights are validated on every write. Corrupt factors are already caught (`retier` returns False). No reachable raise was shown. | reject (unlikely, and needs added guards) |
| M | BH9 | "Computed" shows the last re-tier, and every row is rewritten daily. | low | `tier_computed_at` is the AC's re-tier time. | reject |
| N | BH13 | Corrupt stored text is hidden without a log. | low | Unlikely (written only by the code), and the fix adds branches. | reject |
| O | BH14 | The "lawyers listed" count is a lower bound, yet it triggers `fewer_than`. | low | Decisions 5 and 6 choose it; the AC says Count Evidence. | reject (the spec) |
| P | BH16 (rest) | Missing tests: retier after a weight change, a disqualifying re-check. | low | These are covered by the domain tests and the rescore path. | reject |
| Q | EC3 | `retier` drops a type when its stored Event is retracted while another live Event of the type exists. | false | The stored Event is the type's youngest, so it ages out last. Every retraction path ends in `score_lead` (the chain, or a re-check). | reject |
| R | EC7 | The spec says "four scores". | low | The fix edits the spec. | reject |

## Verification

**Commands:**
- `cd e:/Nextron-epic1/nextron-engine && uv run pytest -q tests/unit/test_scoring.py tests/app/test_lead_scoring.py tests/contracts` -- expected: pass.
- `uv run lint-imports` -- expected: every contract kept.
