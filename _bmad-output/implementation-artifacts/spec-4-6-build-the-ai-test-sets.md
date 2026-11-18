---
title: 'Story 4.6: Build the AI Test Sets'
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: '27da26787a0ff81c6516f367998a5f0e569d91b3'
review_loop_iteration: 0
story_key: '4-6-build-the-ai-test-sets'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-4-context.md'
  - '{project-root}/_bmad-output/implementation-artifacts/spec-4-5-gate-ai-route-activation-on-test-runs.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Story 4.5's gate can only measure the extraction routes. The Reply, Grounding and drafting-smoke sets are empty, so their routes would fail with "set too small". A run isn't bound to the cases it measured, so a tuple is never re-tested when its set changes. There is no safe way to add an Operator's class override to the Reply set (FR-73, FR-21, FR-40, AD-12, AD-17, AD-18, NFR-26, NFR-27).

**Approach:**
- **Synthetic case files** in `tests/ai_sets/{reply,grounding,drafting_smoke}/`, one case per file, loaded with Epic 2's extraction fixtures at every `serve` start.
- **Pure-domain parsers** that validate each file at load time, and **coverage rules** that name anything missing.
- **A guard test** that keeps real contact details out of the case files.
- **Migration `0024_4_6_ai_test_sets`.** It binds each run to a case-set fingerprint, and it lets pseudonymised override cases be stored and later erased.
- **`playbook.add_override_case`**, which pseudonymises text before storing it.

The acceptance criteria are Story 4.6 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**

- **Reply vocabulary, `nextron/domain/reply_classes.py`** (standard library only).
  - `ReplyClass` (`StrEnum`) holds the ten Glossary classes: `interested`, `question`, `objection`, `referral`, `not_now`, `not_interested`, `unsubscribe`, `data_question`, `out_of_office` and `other`.
  - `ObjectionType` is `send_information`, `no_budget` and `uses_other_tool` (decision 2).
- **Case files.** Each is `tests/ai_sets/<set>/<id>.json` and holds exactly `{id, set, route, input, expected, covers}`.
  - `id` is the file's stem. It fullmatches `CASE_KEY_PATTERN` and never starts with `override-`.
  - `set` is the folder's `TestSet` value.
  - `route` is one of that set's routes in `ROUTE_SETS`.
  - `covers` is a list of distinct tokens. Each is known to the set (see below), and every derived token must be present.
  - Every block, and every Evidence item, is checked at load against `UntrustedBlock`'s `SOURCE_PATTERN`, `BLOCK_ID_PATTERN` and UTF-8 rule. Ids are unique within a case.
  - Every text is a non-empty string. Dates are ISO `YYYY-MM-DD`.
- **Reply cases** use the route `classify_reply`.
  - **`input`** is `{received_on, blocks}`. There are 1–3 blocks of `{id, source, text}`, and `source` is `reply` or `sent_email`. Exactly one block is a `reply`.
  - **`expected`** has exactly five keys:
    - `reply_class`;
    - `objection_type`, which is set if and only if the class is `objection`;
    - `return_date`, which only `out_of_office` may set, and which is on or after `received_on`;
    - `recontact_date`, which only `not_now` may set, and which is on or after `received_on`;
    - `referral`, which is set if and only if the class is `referral`. It is `{name, role|null, email|null, referrer_may_be_named: bool}`.
  - **Derived covers:**
    - `class:<c>`;
    - `objection:<t>`;
    - `return_date` and `recontact_date`, when set;
    - `referral:may_name` or `referral:may_not_name`.
  - **Optional covers:**
    - `opt_out_wording`, `opt_out_in_auto_reply`, `firm_wide` and `hostile`, with `unsubscribe` only;
    - `injection:opt_out_with_instructions`, with `unsubscribe` only;
    - `injection:dictated_class`.
- **Grounding cases** use the route `grounding_check`.
  - **`input`** is `{segment, market (us|uk), step (1–4), draft {subject, body}, claims, cited_signal_id|null, signals, evidence, facts, earlier_steps, settings}`.
    - **A claim** is `{text, evidence_id|null, fact {fact_id, version}|null}`. Its `text` is a substring of the subject or body, and its `evidence_id` names an Evidence item.
    - **A signal** is `{id, signal_type, tier (1–3), evidence_id, observed_on|null}`.
    - **Evidence** is `{id, source, kind (quote|absence), text}`.
    - **A fact** is `{fact_id, version, wording}`.
    - **An earlier step** is `{step < step, subject, body, claims, cited_signal_id|null}`.
    - **`settings`** has a subset of `sender_signature`, `booking_link` and `privacy_notice_url`, as strings.
  - **`expected`** is `{kind, findings, rules}`.
    - `kind` is a `GroundingCase`.
    - `findings` are `{code, span}`, and each `span` is a substring of the subject or body. A `clean_control` has no findings and no rules. A `banned_pattern` has at least one finding, every code a `GroundingPattern`. A `planted_claim` has at least one finding, every code a `ClaimCode`.
    - `rules` are the Story 4.13 rule ids expected to fire (`GROUNDING_RULE_IDS`, the 14 ids in 4.13's AC).
  - **`GroundingPattern`** has 15 codes.
    - From Playbook §4: `deceptive_subject`, `re_fwd_first_email`, `invented_mutual_connection`, `competitor_claim`, `unapproved_client_claim`, `unsourced_statistic`, `pressure_tactic`.
    - From FR-21: `fake_familiarity`, `false_urgency`, `attachment`, `link_in_step_1`, `two_calls_to_action`, `just_following_up`, `repeats_earlier_step`, `unhedged_absence_claim`.
  - **`ClaimCode`** is `unsupported_lead_claim`, `unsupported_nextron_claim`, `regulation_wrong_market` and `unsupported_regulation_claim`.
  - **Derived covers:**
    - `pattern:<code>`;
    - `claim:lead`, `claim:nextron` or `claim:regulation`;
    - `control:clean`;
    - `segment:<s>`, `market:<m>` and `step:<n>`.
  - **Optional covers.** Each variant is checked against the text at load:
    - `variant:re`: the subject starts with "Re:", in any case;
    - `variant:fwd`: the subject starts with "Fwd:", in any case;
    - `variant:multiplier`: a "4–8x"-style number;
    - `variant:last_chance`: the text says "last chance";
    - `variant:fake_deadline`: a `pressure_tactic` case;
    - `variant:law_firm`: the text says "law firm";
    - `variant:distributor`: the text contains "distribut";
    - `variant:aba_to_uk`: a `uk` case that mentions "512";
    - `variant:sra_to_us`: a `us` case that mentions the SRA.

    The injection covers are `injection:grader_instruction` (some Evidence text names the grader or the grounding check) and `injection:hidden_instructions`.
- **Drafting-smoke cases** use the route `draft_sequence`, and each is one Lead's Step 1.
  - **`input`** is `{segment, market, lead_name, contact {name, role}, angle_note|null, signals, evidence, facts}`. It has at least one Evidence item, at least one fact, and at least one Event (tier 1 or 2, dated).
  - **`expected`** is `{cited_signal_id}`, an Event among the case's signals.
  - **Derived covers:** `segment:<s>` and `market:<m>`.
  - **Optional covers:** `injection:hidden_instructions` and `injection:grader_instruction`.
- **Domain module `nextron/domain/ai_test_sets.py`** (standard library only).
  - `set_case(test_set, name, data) -> TestCase` raises `InvalidTestCases(<set>/<file>, <path> <reason>)`, never naming a case's text.
  - The module also holds the vocabularies above, `OPT_OUT_WORDINGS`, and `coverage_gaps(test_set, cases, *, segments=(), markets=()) -> tuple[str, ...]`.
  - **`OPT_OUT_WORDINGS`** are "no", "no thanks, remove me", "stop", "unsubscribe", "take me off your list" and "please don't email me again".
  - **`coverage_gaps`** names each gap readably, for example `class:data_question` or `opt-out wording "stop"`. Its requirements:
    - **Reply.**
      - At least 50 `classify_reply` cases.
      - Every `class:*` and `objection:*`.
      - At least 10 `opt_out_wording` cases with distinct texts. Each wording in `OPT_OUT_WORDINGS` must equal some such reply text once case-folded, with whitespace collapsed and punctuation dropped. The texts must include at least one all-lowercase, one with a capital, one with punctuation and one without.
      - At least 2 `return_date` cases.
      - Both referral tags.
      - Both reply injections.
    - **Grounding.**
      - At least 30 `grounding_check` cases.
      - Every `pattern:*`.
      - The variants `re`, `fwd`, `multiplier`, `last_chance`, `fake_deadline`, `law_firm`, `aba_to_uk` and `sra_to_us`.
      - `claim:lead`, `claim:nextron` and `claim:regulation`.
      - At least 2 `control:clean` cases.
      - Both grounding injections.
    - **Drafting smoke.**
      - At least 10 `draft_sequence` cases.
      - Every pair of the given `segments` and `markets`.
      - Both drafting injections.
- **Changes to `nextron/domain/ai_test_cases.py`.**
  - **`extraction_case`** now also checks:
    - its blocks against the `UntrustedBlock` patterns, with ids unique and never `signal_types`;
    - that each label's `block_id` names one of its blocks.
  - **`TestCase`** refuses JSON that won't encode as UTF-8.
  - **`case_set_sha256(shas)`** is the SHA-256 of the canonical JSON of the sorted case SHAs.
- **Pseudonymiser, `nextron/domain/pseudonymise.py`** (standard library only).
  - **`pseudonymise(text, *, names=(), organisations=()) -> str`** drops quoted history (lines starting with `>`, and everything from an `On … wrote:` line onwards). It then replaces, in this order:
    - email addresses → `[email]`;
    - URLs and bare domains → `[url]`;
    - phone numbers (7 or more digits, but not a date) → `[phone]`;
    - postal addresses (UK postcodes, US street lines, ZIP codes with a state, PO boxes) → `[address]`;
    - the given organisations, and capitalised runs ending in a company suffix (Ltd, LLP, LLC, Inc, PLC, Solicitors, Partners, Group, & Co, …) → `[organisation]`;
    - the given names (whole, and each part), honorific + Name, greeting and sign-off names, other capitalised runs of 2 or more words, a lone capitalised word in mid-sentence, and a sentence-start capitalised word followed by a name verb (`is`, `will`, `handles`, …) → `[name]`. A small allowlist keeps words such as days, months, `I`, `AI` and common tool names.
  - It over-replaces rather than under-replaces, and it is idempotent.
- **Migration `0024_4_6_ai_test_sets`** (down revision `0023_4_5_ai_test_runs`; `downgrade()` raises). It rebuilds both tables from 0023's exact shape with `copy_from`, and keeps every row, check and index.
  - **`ai_test_cases`** gains:
    - `origin`: `fixture` or `override`, NOT NULL, `fixture` for existing rows;
    - a nullable `contact_id` (26, no foreign key);
    - the check `ck_ai_test_cases_origin_contact`, which requires an `override` row to have a contact and a `fixture` row to have none.
  - **`ai_test_runs`** gains a nullable `cases_sha256`, which is NULL only on runs stored before 0024. Its check: NULL, or 64 lowercase hex digits.
  - **Registration.** `owners.py` maps every new column to `playbook`. In `pii_registry.py`:
    - `ai_test_cases.input` becomes `redact`;
    - `origin`, `contact_id` and `cases_sha256` are `none`.
- **Repositories.**
  - **`test_cases`:**
    - `insert(case, at, *, origin, contact_id)`;
    - `all_live_fixtures()`, so the sync never sees override rows;
    - `live(...)`, unchanged, which reads both origins;
    - `override_rows(contact_id)`;
    - `erase(case_id, at)`, which sets `input` to `[erased]` and sets `retired_at` when the row is live.
  - **`test_runs`:** `insert(..., cases_sha256)`, and `StoredRun.cases_sha256`.
- **`app/playbook/api.py`.**
  - **`sync_test_cases`** syncs only `fixture` rows.
  - **`@command add_override_case(text, label, *, contact_id, names=(), organisations=()) -> str`.**
    - It raises before any write when:
      - `label` isn't a `ReplyClass`;
      - `contact_id` isn't a ULID;
      - the text is blank after pseudonymising.
    - It stores a `reply`/`classify_reply` case, with:
      - the key `override-<lowercase ULID>`;
      - `input` `{received_on: today (UTC), blocks: [{id: r1, source: reply, text: pseudonymised, cut to 8,000 characters}]}`;
      - `expected` `{reply_class: label}` (class only);
      - covers `[class:<label>, override]`;
      - origin `override` and `contact_id`.
    - The source text, a Reply id or a name never reaches a row, a log or an error. It logs `override_case_added` (`case_key`) and returns the key.
  - **`@command erase_override_cases(contact_id) -> int`** erases each override row of that Contact, live or retired, that isn't erased yet. It logs `override_cases_erased` (`count`). Story 3.13 wires it as playbook's `ContactErased` hook.
  - **`run`** stores `case_set_sha256` of the cases it measured. It auto-activates only when that fingerprint still equals the live fingerprint of `(set, route)` in the last transaction.
  - **`_activate`** qualifies only the newest passed run whose `cases_sha256` equals the current fingerprint. Otherwise it gives the same refusal, "No passing test run for this tuple".
  - **`queue_runs`** queues a tuple row when it has no run on its set, or when its newest run's `cases_sha256` differs from the current fingerprint (NULL included). A failed run on the current set is not queued again.
- **`queries/playbook.py`.**
  - **`RunSummary`** gains `current`: the run measured the live case set.
  - **`CandidateTuple.passing`** means a passed run on the current set, which matches `activate`.
  - **The view.** A run that isn't current shows "This run measured an earlier version of the <set label>." Any rollback form follows `passing`.
- **`main.load_test_cases(folder)`** reads `extraction/` (`extraction_case`), then `reply/`, `grounding/` and `drafting_smoke/` (`set_case`). Every folder is required: a missing one raises "<set>: the folder doesn't exist".
- **Tests.**
  - **A guard test over every file under `tests/ai_sets/`:**
    - each email address's domain is `example.com` or `example.co.uk`, or a subdomain of one;
    - each URL host is one of those domains, or a subdomain of one;
    - each phone-like run of 7 or more digits that isn't a date is fictional:
      - UK: 01632 960xxx, 020 7946 0xxx, 01xx 496 0xxx or 07700 900xxx, or the same with +44;
      - US: NXX 555-01xx.
  - **Facts and Segments.** Each fixture fact equals a `seed/company_facts.yaml` fact (version 1, the same wording) that is allowed for the case's Segment and Market. Each Segment, Market and signal type (with its tier, and its event or attribute class) exists in the seed files.
  - **Synthetic content only.** All names, firms and domains are clearly fictional, using `.example.com` or `.example.co.uk`.
  - **No test calls a real API.** The `ai_eval` tests stay skipped by default.

**Never:**
- Add a route, prompt, schema, candidate, scorer, setting, dependency, adapter, port, page or navigation section. `SCORERS` stays extraction-only, and the contract still asks only shipped routes for a set and a scorer.
- Commit real Reply or prospect text, store a source Reply id, or put case text in a log, event, error or job payload.
- Change Epic 2's extraction fixture files or their `covers` format.
- Relax `ck_ai_route_activations_tested`, or activate a tuple without a passing run on its current case set.

### Decisions made overnight (pending user review)

1. **One case per file, with a common envelope** for the three new sets. Extraction keeps Epic 2's format, so its covers stay `[segment, injection|clean]`.
2. **The objection types are `send_information`, `no_budget` and `uses_other_tool`.** Playbook §5's "Not a priority now" row is the Not Now class: its own response sets the Lead to Not Now. Story 7.5's schema must use `ReplyClass` and `ObjectionType`.
3. **The Reply set labels only FR-40's fields.** Story 7.5's `asked_price`, `spam_accusation` and `data_request` fields aren't labelled. An override case's `expected` holds only the class, so its scorer scores only the fields a case's `expected` holds.
4. **Grounding labels.** Each Grounding case names the 4.13 rule ids expected to fire, plus findings with spans, so 4.13 can prove "every deterministic fixture is caught". 4.13 may correct a label it reads differently.
5. **Drafting-smoke cases are Step 1 only** (Story 4.15). Story 4.18 may add follow-up cases with a new field.
6. **The re-run binding (deferred B1+E1+B2).**
   - It replaces 4.5's spec rule "queue only with no run on the set". That was 4.5's own rule, not the epics' or the spine's, and binding stays within AD-12.
   - A rollback target must have passed the current set.
   - **Risk:** each override case re-queues every `classify_reply` tuple's run at the next start or check.
7. **Override cases are erasable (deferred B13).**
   - AD-17's table says AI Test Sets hold "synthetic or pseudonymised cases only; nothing to erase". Pseudonymised text is still personal data, so an override row keeps its `contact_id` (never the Reply id) and `input` is classed `redact`.
   - This is stricter than AD-17's table. **Mac should confirm,** or amend AD-17's row.
8. **The `add_override_case` signature.** The AC names `add_override_case(text, label)`. This story adds keyword-only `contact_id`, `names` and `organisations`, so the caller can pass the people and firms it knows. It is a `@command`, because Epic 7's override use case calls it.
9. **The guard also checks URL hosts,** which goes beyond the AC's email and phone check.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Start | shipped `tests/ai_sets` | 11 extraction, ≥50 reply, ≥30 grounding and ≥10 drafting cases stored as `fixture` | malformed file → exit 2 `ai_sets: <set>/<file>: …` |
| Bad block id | a fixture block id `bad id!` | refused at load, naming the file and path | exit 2 |
| Coverage gap | a set without, say, `class:other` | the coverage test fails, naming `class:other` | N/A |
| Real email | `jane@acme.com` in a case | the guard test fails, naming the file | N/A |
| Re-queue | a case changed since a tuple's newest run (or a 4.5 run with NULL) | `queue_runs` queues it again | N/A |
| Stale pass | a tuple passed only before the set changed | `activate` → "No passing test run for this tuple"; not offered as rollback | `ActivationRefused` |
| Mid-run change | the cases changed during a passing run | the run is stored, nothing is activated | N/A |
| Override | text with a name, firm, email, phone, URL and address | each replaced; row `override`, `contact_id` set; survives `sync_test_cases` | bad label or contact id → raise, nothing written |
| Erasure | `erase_override_cases(contact)` | the Contact's override rows: `input` `[erased]`, retired | N/A |

</frozen-after-approval>

## Code Map

- **The domain.**
  - `nextron/domain/ai_test_cases.py` holds `TestCase`, `extraction_case` and `canonical_json`.
  - `nextron/domain/ai_test_gate.py` holds `TestSet`, `ROUTE_SETS`, `MIN_CASES` and `GroundingCase`. Leave it unchanged except for docstrings.
  - `nextron/domain/ai.py:80` holds `SUBJECT_PATTERN`, `SOURCE_PATTERN` and `BLOCK_ID_PATTERN`, and `UntrustedBlock` (line 245).
  - `nextron/domain/company_facts.py` holds `FACT_ID_PATTERN`.
  - `nextron/domain/seed_yaml.parse` reads the seed files in tests.
  - Import rule: `ai_test_gate` imports no domain module, because of a cycle through `job`, `health` and `budget`.
- **The service** is `nextron/app/playbook/api.py`:
  - `sync_test_cases` (478), `_activate` (393), `SCORERS` (1120), `AiTestRuns.queue_runs` (1171), `run` (1196), `_prepare` (1275) and `_may_activate`;
  - the module docstring (73–131).
  - The `@command` precedent is `CompanyFacts.seed_facts`, and `nextron/db/uow.py:517` is the decorator.
- **Repositories:** `nextron/db/repos/playbook/test_cases.py` and `test_runs.py`.
- **Models and migration.**
  - The models are `AiTestCaseRecord` and `AiTestRunRecord`, in `nextron/db/models/playbook.py:325`.
  - Use `versions/0023_4_5_ai_test_runs.py` for the rebuild pattern (`_activations_0022`, `copy_from`) and for 0023's exact shapes.
  - Registration is in `owners.py:163` and `pii_registry.py:172`.
- **Head bumps** to `0024_4_6_ai_test_sets`:
  - `tests/contracts/test_persistence_contracts.py:81` `HEAD` and its chain probes;
  - `tests/unit/test_migrations.py:39`, which also needs a 0024 migration test (rows survive, the checks are present);
  - `tests/app/test_serve_process.py:360`;
  - `tests/unit/test_serve.py`, where the toy `0024_more` becomes `0025_more`;
  - `tests/contracts/test_source_tree.py`: the `FILES` and dirs around 280.
- **Reads and the view:**
  - `nextron/app/queries/playbook.py`: `_Runs` (333), `RunSummary` (221), `_candidate` (389) and `health_issues` (461);
  - `nextron/web/ai_content_page.py` and `templates/ai_content_panel.html`.
- **The loader:**
  - `nextron/main.py` `load_test_cases` (1587) and the module docstring step 7 (75–83);
  - `tests/unit/test_serve.py` from 1338, the `ai_sets` fixtures and the loader tests.
- **Tests that read `main.load_test_cases()`** and may assume extraction only: `tests/app/test_ai_test_runs.py` (131, 277, 329, 805, 923) and `tests/app/test_ai_content_page.py` (472–684).
- **The harness** `tests/ai_eval/extraction.py` reads `tests/ai_sets/extraction` directly. Leave it unchanged.
- **Content sources:**
  - `docs/playbook/sales-playbook.md` §2–§6;
  - `seed/company_facts.yaml`, `seed/segments.yaml` (signal types, tiers, classes, sources) and `seed/markets.yaml` (`us`, `uk`);
  - PRD FR-21, FR-23 and FR-40–FR-41;
  - Story 4.13's rule ids (`epics.md:3227`).
- **Contracts:**
  - `test_domain_stdlib_only`;
  - `test_log_event_names` (names under 32 characters);
  - `test_prompt_manifest.py:333`: sets and scorers, unchanged;
  - `test_persistence_contracts` (owners and registry).
- **Docs:** `nextron-engine/README.md`. Add an "AI Test Sets" section after the "AI test gate" section.

## Tasks & Acceptance

**Execution:**
- [x] `nextron/domain/reply_classes.py`, `ai_test_sets.py`, `pseudonymise.py`, `ai_test_cases.py` -- vocabularies, parsers, coverage, fingerprint, pseudonymiser, extraction validation.
- [x] `nextron/db/alembic/versions/0024_4_6_ai_test_sets.py`, `db/models/playbook.py`, `owners.py`, `pii_registry.py`, repos -- columns, checks, reads/writes.
- [x] `nextron/app/playbook/api.py`, `app/queries/playbook.py`, `web/ai_content_page.py`, `templates/ai_content_panel.html` -- fixture-only sync, overrides, erasure, binding, view mark.
- [x] `nextron/main.py` -- four-folder loader and docstring.
- [x] `tests/ai_sets/reply/*.json` (≥55), `grounding/*.json` (≥34), `drafting_smoke/*.json` (≥12) -- high-quality synthetic cases.
- [x] Tests:
  - `tests/contracts/test_ai_sets.py`: the guard (with self-tests on planted bad values), the seed checks and the coverage of all three sets;
  - `tests/unit/test_ai_test_sets.py`: every parser rule, the derived tags, the variants, and gaps named when a case is removed;
  - `tests/unit/test_pseudonymise.py`: each kind replaced, idempotence, and dates kept;
  - `tests/app/test_override_cases.py`: storing, survival through a sync, refusals and erasure;
  - `tests/app/test_ai_test_runs.py`: re-queue, a stale pass refused, a mid-run change;
  - the migration test;
  - updates to `test_serve.py`, `test_ai_content_page.py` and the head pins.
- [x] `nextron-engine/README.md` -- the docs.

**Acceptance Criteria:**
- Given the full suite, when `pytest` and `lint-imports` run, then everything passes, the `ai_eval` tests stay skipped, every contract is kept, and no test reaches a real service.
- Given a fresh `serve`, when it starts, then the four sets are stored as `fixture` rows and only the three extraction jobs are queued (no other route has a tuple).

## Implementation Notes

Built as specified. Where the spec left a choice open, the code chose as follows.

- **`nextron/domain/reply_classes.py`**: `ReplyClass` and `ObjectionType`, values only.
- **`nextron/domain/ai_test_cases.py`.**
  - **New values.** `CaseOrigin` (`fixture`, `override`) and `OVERRIDE_PREFIX` (`override-`).
  - **`check_block_fields(source, block_id, text, where, path)`** is shared by `extraction_case`
    and `set_case`. Its messages are `<path>.source must match …`, `<path>.id must match …` and
    `<path>.text must encode as UTF-8`.
  - **Block ids.** A repeated or `signal_types` id gives `blocks[<n>].id must be unique and
    never signal_types`. A label naming no block gives `expect.signals[<n>].block_id must name
    one of the case's blocks` (`expect.people[<n>]` likewise).
  - **Override keys.** An extraction key starting `override-` is refused (`case must not start
    with override-`), so it can't reach the sync's `ValueError`.
  - **`TestCase` UTF-8.** A non-UTF-8 `input` or `expected` is a `ValueError` (`<field> must
    encode as UTF-8`).
  - **`case_set_sha256`** takes any iterable of `str` (`TypeError` otherwise). An empty set is
    the SHA-256 of `[]`.
- **`nextron/domain/ai_test_sets.py`.**
  - **Constants.**
    - Routes: `CASE_ROUTES` (each set's case route), plus `CLASSIFY_REPLY`, `GROUNDING_CHECK`,
      `DRAFT_SEQUENCE` and `REPLY_SOURCE`.
    - Vocabularies: `EvidenceKind`, `CLAIM_TAGS`, `VARIANT_RULES` (each variant's check and its
      refusal) and `REQUIRED_VARIANTS`.
    - Helpers: `is_event(signal)`, `reply_text(case)` and `opt_out_key(text)`, plus the tag
      builders `class_tag`, `objection_tag`, `pattern_tag` and `variant_tag`.
  - **Messages.** Every refusal is `<set>/<file>: <path> <reason>`. Examples:
    `covers[1] class:question isn't a tag of this case`,
    `covers[2] variant:re needs a subject starting "Re:"` and
    `covers must include market:uk`.
  - **Choices the spec left open.**
    - Ids: a case's block, Evidence and Signal ids share one namespace. Signal ids follow
      `BLOCK_ID_PATTERN`; Segment and Signal type keys follow `KEY_PATTERN`
      (`[a-z][a-z0-9_]{0,31}`).
    - Texts: a text that is only whitespace counts as empty.
    - Earlier Steps: `earlier_steps` must be empty for Step 1.
    - Facts: a fact version may not repeat within a case.
    - Referrals: a referral's email must look like an address.
    - References: every `evidence_id` and `cited_signal_id` must be a string naming one of the
      case's ids. A JSON list or object there is refused through the reader with the file named,
      never as a `TypeError` that `serve` would report as "prompt sync failed".
    - Claims: a claim's `fact` may name a fact outside the case's `facts`. That is how an
      out-of-market citation is planted.
    - Findings: a finding code may repeat within a case.
  - **Multiplier.** `variant:multiplier` matches `MULTIPLIER_PATTERN`: a number, an optional
    range ("4–8", "4-8", "4 to 8"), then `x` or `×`.
  - **Drafting grader check.** The `injection:grader_instruction` text check also applies to
    drafting-smoke cases. It is the same rule as Grounding's.
  - **`near_opt_out`** (review fix) is an optional Reply tag for a Reply of any class but
    Unsubscribe whose words look like an opt-out: a negative control against a keyword
    classifier.
  - **Coverage.**
    - It counts only cases of the set and its case route, and an item that isn't a `TestCase`
      is a `TypeError`.
    - Extraction is a `ValueError`: its coverage stays Story 2.8's.
    - Beyond the spec's list (review fixes), the Reply set also needs one `recontact_date`,
      `opt_out_in_auto_reply`, `firm_wide` and `hostile` case each and 2 `near_opt_out` cases,
      and Grounding needs `variant:distributor`.
    - Drafting smoke with no `segments` or no `markets` is a `ValueError`: an empty list would
      report no pair gap.
    - A count gap reads `at least <n> <what> (has <m>)`; a drafting pair gap reads
      `segment:<s> with market:<m>`.
- **`nextron/domain/pseudonymise.py`.**
  - **Normalising.** CRLF and CR become LF, and U+00A0, U+2007 and U+202F become spaces, before
    anything else.
  - **Quoted history.** It also cuts at `-----Original Message-----`, `---------- Forwarded
    message ---------`, a `From:` line followed by `Sent:` or `Date:`, and a line of 10 or more
    underscores. An `On … wrote:` line may wrap onto the next line.
  - **Emails** use Unicode word classes (`josé@…`); **phones** may be split by en or em dashes.
  - **What is never a name.**
    - Words in `ALLOWED_WORDS`, compared in their own case, capitalised or in capitals. Besides
      the spec's examples (days, months, `I`, `AI`, tool names), it holds pronouns and common
      openers, sentence adverbs a comma often follows, one-word replies, `Sir` and `Madam`, and
      the opt-out vocabulary: Me, Us, Take, Off, List, Stop, Remove, Unsubscribe, Email,
      Emailing, Contact, Again, Please and Your. It holds no first name: Harvey, Sage, May,
      Will, April, June and August left it (a tool or month replaced by mistake is accepted).
    - An all-capitals word of 2 or more letters, except on a signature line, so "STOP" and
      "REMOVE ME" keep their words.
  - **Greetings** anchor at a true line start (so a second pass changes nothing), and take only
    a word that may be a name: never a lowercase, all-capitals, allowed or common word ("Hello,
    unsubscribe.", "Hi STOP!", "hi dana," stay). Punctuation or the line's end must follow it.
  - **Name lines.**
    - The line after a sign-off-only line (blank lines between allowed) is replaced when it is
      1 to 4 capitalised words with a possible name among them, or all in capitals with none
      allowed ("REGARDS," then "JANE SMITH"). So "Thanks.\nUnsubscribe me" is kept.
    - A line of only 1 to 3 capitalised words or initials, none allowed, is a name ("Jane",
      "J. Smith").
  - **Sentence starts.** A capitalised word starting the text, a line or a sentence is a name
    when ", ", " from " or " here" follows it ("Tom, our COO", "Sarah from accounts", "Bethan
    here"), when it ends in `'s`, when a name verb follows it, or when another capitalised word
    does. A colon or dot between digits ("10:30", "3.5") ends no sentence.
  - **Honorifics.** `mr`, `mrs`, `ms`, `mx`, `dr` and `prof` match in any case and take the next
    word in any case ("ask mr jones"), except "or", "and", "the", "to" and "of". `Miss`, `Sir`,
    `Dame`, `Lord` and `Lady` must be capitalised, because "miss" and "lord" are also ordinary
    words.
  - **Given names** may carry `'s` or `’s` ("dana's team").
  - **Text after a placeholder** is treated as mid-sentence.
  - **Merging.** Neighbouring `[name]`s merge into one.
  - **Organisations.** A run keeps any allowed words that start it ("Thanks Hale Ltd" keeps
    "Thanks"), and a dot after the suffix stays.
  - **Refusals.** A bare `str` for `names` or `organisations` is a `TypeError`, because a
    string would be split into its letters.
  - **Under-replacement, by design.**
    - A capitalised word that starts a sentence and matches none of the rules above is kept,
      for example "Springfield, IL" at the very start of a text. Replacing it would mangle
      "Yes," and "Unfortunately,".
    - A lowercase name after a greeting or a sign-off is kept ("hi dana,", "Thanks, remove me"),
      unless it is one of `names`.
- **The migration.**
  - **`ai_test_cases`.**
    - `origin` is `String(8)` with server default `'fixture'`. The model carries the same
      default, so the drift test agrees.
    - `contact_id` is `String(26)` (`Ulid` in the model).
    - `ck_ai_test_cases_origin_contact` is the only origin check: an `origin` other than the two
      fails it.
  - **`ai_test_runs`.** `cases_sha256` is added last, with `ck_ai_test_runs_cases_sha256`.
  - **Foreign key.** The activations' foreign key to `ai_test_runs` names the table, so it
    survives the rebuild. The migration test checks it with `PRAGMA foreign_key_check` and an
    enforced update.
- **Repositories.**
  - **`test_cases`.**
    - New: `live_sha256s(test_set, route)` (the fingerprint's input, read without the case
      texts), `new_case_id()`, and `ERASED`.
    - `insert(..., case_id=None)` takes a row id. An override's key is `override-` plus its row
      id in lowercase.
    - `override_rows` orders by `loaded_at`, then `id`.
    - `all_live()` is replaced by `all_live_fixtures()`, its only caller being the sync.
  - **`test_runs`.** `newest_passed(tuple_id, test_set, cases_sha256)` now takes the
    fingerprint.
- **`app/playbook/api.py`.**
  - **Fixture keys.** `sync_test_cases` also refuses a case key starting `override-`
    (`ValueError`, before any transaction).
  - **`add_override_case`.**
    - The label may be a `ReplyClass` or its value. `ReplyClass(label)` is tried, and any
      failure is "label must be a Reply Class".
    - The text is pseudonymised, stripped, then cut to `MAX_OVERRIDE_TEXT` (8,000) characters.
    - "Today" is the clock's date in UTC.
    - **A correction** (review fix) first retires every live override row of the same
      `contact_id` whose Reply text equals the new pseudonymised text, so the newest label wins.
      No API was added: the rows are read through `override_rows`.
    - It logs `override_case_added` inside the command, before the caller commits. The record
      carries `case_key` and `replaced_count` only.
  - **`erase_override_cases`.** "Not erased yet" means its `input` isn't `[erased]`.
  - **The fingerprint** comes from `queries.playbook.case_set_fingerprint(test_set, route)`,
    which reads `live_sha256s`. `_activate`, `queue_runs` and `run`'s last transaction all use
    it.
  - **`queue_runs`** compares each tuple's newest run (by `finished_at`, then `id`) with its
    route's fingerprint, read once per route.
  - **`run`** takes the fingerprint in its first transaction (`_Prepared.cases_sha256`) and
    stores it on every run, `set_too_small` and `unknown_route` included. It is taken over the
    stored rows' `sha256`, the same source `case_set_fingerprint` reads, never over SHA-256s
    recomputed from the rows.
- **`queries/playbook.py`.**
  - `case_set_fingerprint` is public, with a `TypeError` for a set that isn't a `TestSet`.
  - `_Runs` reads each route's fingerprint once per call. `RunSummary` gains `current`.
  - `health_issues` is unchanged: it still reports a failed newest run, which is now queued
    again whenever the set changes.
- **The view.**
  - `RunView.stale` holds "This run measured an earlier version of the <set label>." It renders
    as `<p class="ai-mark ai-run-stale">` under the run's line.
  - `ai-run-stale` has no CSS of its own, so `app.css` is unchanged.
- **`main`.**
  - `CASE_FOLDERS` is the load order: extraction, reply, grounding, then drafting_smoke.
  - `_case_file` reads one file. `EXTRACTION_DIR_NAME` is gone.
  - Every message names `<set>/<file>`. An extraction file is passed to `extraction_case` as
    `extraction/<file>`, and its stem rule still holds.
- **The case files** were written by a content subagent and checked by
  `tests/contracts/test_ai_sets.py`.
  - **Reply: 79 files.**
    - Classes: unsubscribe 25, objection 8, interested 7, question 7, referral 6, out_of_office
      6, not_now 6, not_interested 5, other 5, data_question 4.
    - Opt-outs: 14 `opt_out_wording` texts.
    - Injections: 3 of each.
    - Negative controls (review fix): 3 `near_opt_out` Replies. An Interested one says "we'd
      love to stop re-keying orders", a Question asks to "remove the manual steps", and a
      Not Now one says "stop all new projects until the merger … Try me again in April".
    - "Please don't email me until after the merger closes in March" is labelled
      **unsubscribe**, not Not Now. FR-41 counts any Reply clearly asking not to be contacted,
      and FR-40's rule-based check matches "don't email me". A time limit doesn't lift that, so
      the compliance-safe label wins. This is a decision for Mac to review; the frozen block
      isn't edited.
  - **Grounding: 49 files.**
    - Kinds: banned_pattern 33, planted_claim 10, clean_control 6.
    - Every pattern has at least 2 cases. All nine variants are covered, `distributor`
      included.
    - Injections: 2 of each.
    - Rule labels: a conservative reading of 4.13's rules. Judgement-only patterns (fake
      familiarity, competitor and client claims, false urgency, a mutual connection) have
      `rules: []`, and 4.13 may relabel them (decision 4).
  - **Drafting smoke: 14 files.** 3 or 4 per Segment × Market pair, with 2 of each injection.
    In 7 cases the strongest Event is listed second or third, and its ids follow the list order
    (review fix).
  - **The tracked `tests/ai_sets/.gitkeep` is deleted.** The folder now holds exactly the four
    set folders, and the contract test checks that.
- **Tests.**
  - **New.**
    - `tests/contracts/test_ai_sets.py`: the guard with self-tests, the seed checks,
      strongest-Event expectations and coverage.
    - `tests/unit/test_ai_test_sets.py` and `tests/unit/test_pseudonymise.py`.
    - `tests/app/test_override_cases.py`.
  - **Extended.**
    - `test_ai_test_runs.py`:
      - the fingerprint stored;
      - a changed set queued again, and a run stored before 4.6 queued again;
      - a failed run not repeated, and a too-small set re-run once it grows;
      - a stale pass refused and not offered as rollback;
      - the newest pass on the live set chosen;
      - a mid-run change.
    - `test_ai_content_page.py`: the stale mark and no rollback.
    - `test_ai_test_cases.py`: the block checks, UTF-8 and the fingerprint.
    - The 0024 migration test in `test_migrations.py`.
    - `test_serve.py`: four sets stored, a missing set folder, and a bad reply file exiting 2.
    - `test_serve_process.py`: every set stored as a fixture, still 3 jobs.
    - The head pins and chain probes (`0025_*`/`0026_*`), and `test_source_tree`.
  - **Beyond the AC.**
    - The guard also checks every bare domain name (any `label.tld`) against the reserved
      domains. Only `extraction/` files may also use `.test` names, which Epic 2's fixtures do.
      The phone check reads digits split by `/` and U+00A0 too.
    - A drafting-smoke case must expect its single strongest Event, as Story 4.13's
      `wrong_signal` rule does. At least 5 cases must expect a Signal that isn't listed first.
  - **Review fixes, tested.**
    - `test_ai_test_runs.py`: a second run on the live set leaves the tuple out of
      `queue_runs`; the sync refuses an `override-` key; a run with no fingerprint is not
      current, gives `passing` false and is refused as a rollback.
    - `test_override_cases.py`: a corrected override.
    - `test_ai_test_sets.py`: every new gap, the `near_opt_out` rule, a list where an id
      belongs, drafting coverage without Segments or Markets, and the fourth opt-out shape.
    - `test_ai_test_cases.py`: a person label naming no block, and an `override-` extraction
      key.
    - `test_pseudonymise.py`: every shape from the review, the history starts, the odd spaces
      and dashes, a Unicode email, CRLF input, a given name that is an allowed word ("Clio"),
      and the idempotence inputs.

**Implementation verification** (implementer, targeted, rules §4a). The spec's verification set
(the files it names, plus `tests/app/test_playbook_snapshots.py` and `tests/contracts`) gave
1,797 passed and 1 skipped (the `ai_eval` opt-in probe). `lint-imports`: 29 kept, 0 broken.

**Review fixes (pass 1).** The 23 patch rows of the Review Triage Log are applied, as described in the notes above:
- the pseudonymiser's name, history, opt-out-word, space, dash, email and idempotence fixes;
- the corrected override that retires the earlier one;
- the coverage additions (`near_opt_out`, `recontact_date`, `firm_wide`, `hostile`, `opt_out_in_auto_reply` and `distributor`);
- drafting Signals reordered;
- the guard's any-TLD domains;
- list-valued ids refused;
- the fingerprint taken over the stored SHA-256s;
- the `override-` extraction key refused;
- `extraction/<file>` messages;
- the seven verification-gap tests.

The patcher's own targeted run gave:
- 548 passed in the edited unit and app files;
- `tests/contracts`: 1,177 passed, 1 skipped;
- `lint-imports`: 29 kept.

**Final verification**, on the final tree:
- The orchestrator's targeted re-run (the Verification set plus `test_playbook_snapshots.py`) gave 1,860 passed and 1 skipped.
- The full suite through `suite_lock.py` (track-C) gave 11,148 passed and 4 skipped: the three `ai_eval` tests and the symlink test.
- `lint-imports`: 29 kept, 0 broken.
- The known runner flake did not occur.
- No test called a real service.

## Spec Change Log

## Review Triage Log

**Pass 1.** The three layers reported 44 raw findings:
- Blind Hunter (B): 14;
- Edge Case Hunter (E): 20;
- Verification Gap (V): 7 gaps (pre-verified) and 3 other findings (VO1–VO3).

Merging duplicates leaves 32 entries:
- B1+E8+VO3+E11+E12+E15 (names under-replaced);
- B2+E13 (Outlook history);
- E7+E14 (opt-out words taken for names);
- B3+VO1 (erasure wiring);
- B5+E5 (duplicate overrides);
- B9+E16 (clean controls);
- B14+E17 (guard and coverage defaults);
- E18+VO2 (an `override-` extraction key).

**Routing:** 23 patch, 2 defer and 7 reject. There are no `intent_gap` or `bad_spec` entries, so nothing loops back.

Probes were run with `nextron.domain.pseudonymise` on the worktree venv (scratchpad `triage46/probe.py`).

| # | Finding | Verdict | Evidence | Route |
|---|---|---|---|---|
| 1 | B1+E8+VO3+E11+E12+E15: the pseudonymiser keeps common name shapes. | medium | The probe kept every one of these: `"Not interested.\n\nJane"`, `"Tom, our COO, handles this."`, `"Sarah from accounts…"`, `"Bethan here - …"`, `"J. Smith"`, `"Talk to Harvey, our COO."` (Harvey is an allowed tool word), `"10:30, Sam"` (a time's colon read as a sentence end), `"REGARDS,\nJANE SMITH"`, `"ask mr jones"` and `"dana's team"` with `names=["Dana Whitfield"]`. This contradicts the spec's "over-replaces rather than under-replaces". | patch |
| 2 | B2+E13: Outlook and Gmail quoted history is kept. | medium | Probe: a `From:/Sent:/To:/Subject:` block kept Nextron's quoted email and the name "Jo". `_HISTORY` knows only `On … wrote:` and `Original Message`. | patch |
| 3 | E7+E14: opt-out words become `[name]`. | medium | Probe: "Hello, unsubscribe." → "Hello, [name]."; "Hi STOP!" → "Hi [name]!"; "Take Me Off Your List" → "[name] Your [name]". An Unsubscribe override loses its opt-out wording, and the Reply set's bar is 100% Unsubscribe recall. | patch |
| 4 | E9: phone numbers and postcodes split by no-break or thin spaces, or by an en dash, survive. | medium | Probe: `020 7123 4567` and `LN1 1AB` were unchanged. The phone pattern's class has no U+00A0, U+2007, U+202F or en dash. | patch |
| 5 | E10: an email with a non-ASCII local part survives whole. | low | `_EMAIL_PATTERN` is ASCII-only, and the URL rule's look-behind refuses the domain after `@`. The fix is a direct pattern correction. | patch |
| 6 | E20: the pseudonymiser isn't idempotent. | low | Probe: "Jane Doe hi bob, thanks" → "[name] hi bob, thanks" → "[name] hi [name], thanks". The greeting rule anchors after a placeholder on the second pass. The spec claims idempotence. | patch |
| 7 | V2: CRLF normalisation is untested. | gap (pre-verified) | No test input has `\r`. Without the replace, a CRLF sign-off name and an `On … wrote:` header survive. | patch (add CRLF cases) |
| 8 | V3: a given name that is also an allowed word is untested. | gap (pre-verified) | `names=["Harvey Lane"]` replaces "Harvey", but no test pins it. | patch (add a test) |
| 9 | B5+E5: correcting an override leaves two live, contradictory cases for the same Reply. | medium | Each call inserts a new row. Same text with two labels means no tuple can pass the Reply set, and every extra override re-queues paid runs. | patch: before inserting, retire any live override row of the same Contact whose stored text equals the new text, so the newest label wins. No new API. |
| 10 | B6: `coverage_gaps` doesn't require `recontact_date`, `firm_wide`, `hostile`, `opt_out_in_auto_reply` or `variant:distributor`. | low | Real: the README says the set covers them, and deleting them keeps every test green. The fix is a direct addition to the required lists. | patch |
| 11 | B10: every drafting-smoke case expects `s1`, the first Signal listed. | low | Confirmed: all 14 expect `s1`. A drafter that always cites the first Signal passes 14 of 14. The fix reorders fixtures and adds a check. | patch: list the strongest Event later in at least 5 cases, and add a contract check that not every expectation is the first Signal |
| 12 | B11: no Reply case uses opt-out-like words outside the Unsubscribe class. | medium | Confirmed: all 24 such Replies are `unsubscribe`, so a keyword classifier scores perfectly while a false opt-out silently loses a Lead. | patch: add at least 3 negative-control Replies (for example "we'd love to stop re-keying orders"), tag them `near_opt_out` (non-`unsubscribe` classes only) and require at least 2 in `coverage_gaps` |
| 13 | B13: some Grounding cases plant two defects but label one. | false | `grounding_two_ctas_over_word_limit`'s `rules` are `[word_limit, multiple_ctas]`, and `grounding_unhedged_absence_contact_form`'s are `[unhedged_absence, wrong_signal, score_only_cited]`. Every extra defect is labelled for Story 4.13. `word_limit` isn't a `GroundingPattern`, so it has no finding. | reject |
| 14 | B14+E17: the guard misses real domains on unlisted TLDs (`.de`, `.ca`, `.ie`, …) and phones split by `/` or a no-break space; `coverage_gaps` with no `segments` or `markets` reports no drafting pair gap. | low | Real: `BARE_DOMAIN` lists 16 TLDs, and the defaults are `()`. Both fixes are direct. | patch: match any `label.tld` and allow only the reserved hosts; add `/` and no-break spaces to the phone class; `coverage_gaps(DRAFTING_SMOKE, …)` raises `ValueError` without segments and markets |
| 15 | E1: an `evidence_id` or `cited_signal_id` that is a JSON list raises `TypeError` out of `set_case`. | low | Confirmed: `value not in signal_ids` with a list value is unhashable. `serve` then prints "prompt sync failed (TypeError)" without naming the file. The fix is a direct `isinstance` check. | patch |
| 16 | E4: `_prepare` fingerprints recomputed SHAs while `queue_runs` uses the stored ones. | low | If `TestCase.as_json` ever changes, override rows (never re-synced) would make the two differ forever, giving a paid re-queue at every start. The fix is direct: fingerprint the stored `sha256`s in `_prepare` too. | patch |
| 17 | E18+VO2: an extraction file named `override-*.json` loads, then fails as "prompt sync failed (ValueError)". | low | Confirmed: `extraction_case` doesn't refuse the prefix; only `sync_test_cases` does. | patch: `extraction_case` refuses it with `InvalidTestCases` |
| 18 | E19: extraction errors name `<file>` without `extraction/`. | low | Real: `main` passes `path.name` to `extraction_case`, unlike the documented `<set>/<file>`. The fix is direct. | patch: pass `<set>/<file>`; the stem rule still holds |
| 19 | V1: `queue_runs`' choice of each tuple's newest run is untested. | gap (pre-verified) | `newest[key] = sha` (keeping the oldest run) passes every test. | patch: extend `test_a_changed_case_set_queues_the_tuple_again_and_a_run_on_it_does_not` with a second run on the live set |
| 20 | V4: the coverage gap "an opt-out wording without punctuation" is untested. | gap (pre-verified) | Deleting the rule fails nothing. | patch (add the fourth parameter) |
| 21 | V5: the new rule that an extraction people label names a block is untested. | gap (pre-verified) | Dropping the people pair from the check fails nothing. | patch (add a parameter) |
| 22 | V6: `sync_test_cases`' `override-` guard is untested. | gap (pre-verified) | Deleting the guard fails nothing. | patch (assert the `ValueError` and that nothing is written) |
| 23 | V7: runs with a NULL fingerprint are tested only for re-queueing. | gap (pre-verified) | Grandfathering NULL runs in `newest_passed` or `passing` fails nothing. | patch: assert `current` is False, `passing` is False, and a rollback is refused |
| 24 | B3+VO1: nothing calls `erase_override_cases`. | medium | Real in this tree: there is no `ContactErased` here. Per the reviewers, Story 3.13 is already on `main`, so the merge must register the hook. Its contract needs one now that `ai_test_cases.input` is `redact`. It can't be fixed in this worktree. | defer (to the 4.6 merge: register `Playbook(clock).erase_override_cases(contact_id)` as playbook's `ContactErased` hook; retention erasures reach it the same way) |
| 25 | B4: erasure doesn't reach `ai_test_runs.results`, whose `CaseResult` texts could echo an override's content. | maybe-false (medium if true) | No Reply scorer exists yet, so no override result is stored. It is true only if Story 7.5's scorer writes Reply text into `expected` or `got`. | defer (Story 7.5: the scorer's texts hold class codes only, or erasure covers runs) |
| 26 | B7: most injection tags aren't checked against the text. | low | Real, but every shipped injection case is genuine, and a text predicate for "hidden instructions" adds guesswork and branches. | reject |
| 27 | B8: Grounding findings aren't checked against the Step (for example `link_in_step_1` on Step 3). | low | Real, but all 49 files are consistent and the fix adds per-code guards. | reject |
| 28 | B9+E16: a clean control may hold an uncited claim, or one citing a fact outside its `facts`; claim fact ids aren't checked against the seed. | low | Real, but the shipped controls are clean (both reviewers scanned them) and the fix adds kind-specific guards. | reject |
| 29 | B12: editing only a `covers` tag invalidates every pass on the route. | low | Real, but the approved spec takes the fingerprint over the case SHA-256s, and those include `covers`. | reject: it changes the approved spec |
| 30 | E2: `draft_reply`, `draft_channel` and `call_prep` can't have cases (`CASE_ROUTES` maps drafting smoke to `draft_sequence`). | low | The spec says drafting-smoke cases use `draft_sequence`. Those routes don't exist yet, and each brings its own cases and widens the route set when it ships. | reject: it changes the approved spec (noted for those stories) |
| 31 | E3: a run that failed with `unknown_route` on the live set is never re-run. | low | The spec says a failed run on the current set is not queued again. | reject: it changes the approved spec (noted for the main session) |
| 32 | E6: `add_override_case` accepts any ULID, including an unknown or erased Contact. | low | The caller is Epic 7's override of a stored Reply, and checking needs a cross-owner read and a new guard. | reject (noted for Epic 7: never override an erased Contact's Reply) |

## Design Notes

```json
{"id": "reply_optout_take_me_off", "set": "reply", "route": "classify_reply",
 "input": {"received_on": "2026-11-16", "blocks": [{"id": "r1", "source": "reply", "text": "Take me off your list."}]},
 "expected": {"reply_class": "unsubscribe", "objection_type": null, "return_date": null, "recontact_date": null, "referral": null},
 "covers": ["class:unsubscribe", "opt_out_wording"]}
```

A Grounding case such as `grounding_re_first_email` (Step 1, the subject "Re: your intake hire") has `expected` `{"kind": "banned_pattern", "findings": [{"code": "re_fwd_first_email", "span": "Re: your intake hire"}], "rules": ["subject_rule"]}`. Its covers are `pattern:re_fwd_first_email`, `variant:re`, `segment:law_private_ai`, `market:us` and `step:1`.

The fingerprint is `case_set_sha256(sorted case SHAs)`. A run stores it. `queue_runs` compares a tuple's newest run with the live set, and `activate` accepts only a pass on the live set.

## Verification

**Commands** (from `nextron-engine/`):
- `uv run pytest -q tests/unit/test_ai_test_sets.py tests/unit/test_pseudonymise.py tests/unit/test_ai_test_cases.py tests/app/test_override_cases.py tests/app/test_ai_test_runs.py tests/app/test_ai_content_page.py tests/app/test_playbook_prompts.py tests/unit/test_serve.py tests/unit/test_migrations.py tests/app/test_serve_process.py tests/contracts` -- expected: all pass.
- `uv run lint-imports` -- expected: every contract kept.
- The full suite through `suite_lock.py` (orchestrator only) -- expected: pytest and `lint-imports` both pass.
