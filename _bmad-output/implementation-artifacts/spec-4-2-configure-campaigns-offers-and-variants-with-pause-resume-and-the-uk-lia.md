---
title: 'Story 4.2: Configure Campaigns, Offers and Variants with pause, resume and the UK LIA'
type: 'feature'
created: '2026-11-17'
status: 'done'
route: 'dispatch'
baseline_commit: '93198f5bb97a3b3c3e7a64cb66d7c1b44c6dd655'
review_loop_iteration: 0
story_key: '4-2-configure-campaigns-offers-and-variants-with-pause-resume-an'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-4-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The engine has Segments, Markets, the default Sequence and Approved Company Facts, but no Campaign, Offer or Variant (FR-3). Enrollment (4.8), the Approval Queue (Epic 5) and the send gate's `campaign_active` predicate (AD-5) all need one answer to "may this Campaign run?", and PECR/UK GDPR (NFR-3) forbid a UK Campaign running without a recorded legitimate-interests assessment (LIA).

Requirements: FR-3, AD-3, AD-4, AD-5 (`campaign_active`), AD-12, AD-13, NFR-3, NFR-7, UX-DR3, UX-DR23.

**Approach:**
- Two new seed files, `seed/offers.yaml` and `seed/campaigns.yaml`, load through `nextron seed sync`. They join `setup.check_seed` and the `Setup.seed_sync` command, so all six files still sync in one transaction through `ops.Seeds.sync_seed`.
- New stdlib-only domain modules: `nextron/domain/offers.py` and `nextron/domain/campaign.py` (the Campaign machine, Variants, the LIA rules and the file validation).
- New tables `offers`, `campaigns` and `variants` (owner `setup`, migration `0012_4_2_campaigns`).
- A `Campaigns(clock)` service in `app/setup/api.py` holds the Operator commands: activate, pause, resume, record the LIA, and add a Variant.
- `queries.setup.campaign_active(campaign_id)` is the single answer.
- A Campaigns section on the Settings page has CSRF-checked POST controls.

The acceptance criteria are Story 4.2 in `_bmad-output/planning-artifacts/epics.md`, verbatim, except where "Decisions made overnight" says otherwise.

## Boundaries & Constraints

**Always:**

- **`nextron/domain/offers.py`** (stdlib and `nextron.domain` only; frozen, slotted dataclasses; every list a tuple).
  - `OfferRungKind` (`StrEnum`): `reply`, `audit`, `build`.
  - `OfferRung(kind, name, price_usd_min, price_usd_max, terms)`. Both prices are `int | None`, and `terms` is `str | None`.
  - `OfferDef(key, name, ladder)`.
  - **The file shape.** The top level is exactly `offers: [<offer>, ...]`, with 1 to 20 items. An offer is exactly `key`, `name` and `ladder` (1 to 5 rungs). A rung has `kind` and `name`, plus optional `price_usd_min`, `price_usd_max` and `terms`. Unknown and missing keys are refused.
  - **`validate_offers(data) -> tuple[OfferDef, ...]`** raises `InvalidOffers(field, reason)` (code `invalid_offers`, message `"<field>: <reason>"`) for the first problem, with paths like `offers[default].ladder[1].price_usd_min`. An offer's brackets hold its key when valid and not repeated, else its 0-based index. Rungs use their 0-based index.
  - **Value rules:**
    - `key` fullmatches `[a-z][a-z0-9_]{0,30}` and is unique.
    - The texts follow 2.1's text rules: `name` at most 100 characters, and `terms` at most 200.
    - Prices are an `int`, never a `bool`, from 1 to 1,000,000.
  - **Rules:**
    1. Rung kinds are unique within a ladder.
    2. The first rung is `reply`: the first ask is only a reply (FR-4).
    3. A `reply` rung has no price.
    4. An `audit` or `build` rung has both prices, with `price_usd_min <= price_usd_max`.
  - `offer_form`/`offer_from_form` round-trip.
- **`nextron/domain/campaign.py`** (stdlib and `nextron.domain` only).
  - **The machine (AD-4).**
    - `CampaignStatus`: `draft`, `active`, `paused`.
    - `CampaignEvent`: `activate`, `pause`, `resume`.
    - `CampaignCause`: `operator_activate`, `operator_pause`, `operator_resume`.
    - The read-only `TRANSITIONS` table holds exactly `draft --activate--> active`, `active --pause--> paused` and `paused --resume--> active`.
    - `transition(current, event)` returns a `Transition` or raises `IllegalTransition`, as in `domain/job.py`.
    - Constants: `MACHINE = "campaign"`, `LIA_RECORDED_CAUSE = "operator_record_lia"`, `VARIANT_ADDED_CAUSE = "operator_add_variant"`.
  - **Variants.**
    - `VariantStatus`: `active`, `inactive`. `MAX_VARIANTS = 2`, with `TOO_MANY_VARIANTS = "A Campaign can have at most 2 Variants"`.
    - `StepAngleNote(step, note)` and `VariantDef(name, status, step_angles)`.
  - **The LIA.**
    - `Lia(ref, date)`. `LIA_FIRST = "Record the legitimate-interests assessment reference and date first"`.
    - `needs_lia(country_code)` is true exactly for `markets.GB`.
    - `may_run(status, country_code, lia) -> bool` is true only for `active`, with a non-`None` `lia` when `needs_lia`.
    - `lia_from_fields(ref_text, date_text, today) -> Lia` strips both, then collects every problem and raises `CampaignRefused(problems)`:
      - a blank field gets "Enter a value" on that field, plus `form`: `LIA_FIRST`;
      - the ref follows the text rules, at most 200 characters;
      - the date is a real `YYYY-MM-DD` date ("Use a date written YYYY-MM-DD"), no later than `today` ("can't be in the future").
  - **`CampaignRefused(problems: Mapping[str, str])`** has code `campaign_refused`. The keys are field names or `form`.
  - **The file.** The top level is exactly `campaigns: [<campaign>, ...]`, with 1 to 50 items.
    - A campaign is exactly `key`, `name`, `segment`, `market`, `offer`, `sequence` and `variants`.
    - A Variant is `name` and `status`, plus optional `step_angles`: 0 to 6 items, each exactly `step` and `note`.
    - Missing and unknown keys are refused.
    - `CampaignDef(key, name, segment, market, offer, sequence, variants)`.
    - **`validate_campaigns(data)`** raises `InvalidCampaigns(field, reason)` (code `invalid_campaigns`), with paths like `campaigns[law_private_ai_uk].variants[A].step_angles[0].note`. A Variant's brackets hold its name when valid and not repeated, else its index.
  - **Value rules:**
    - `key` and `segment` use the key pattern; `market` uses 3.1's code pattern; `offer` and `sequence` use the key pattern.
    - `name` (Campaign) is at most 100 characters, a Variant `name` at most 40, and a `note` at most 200, all under the text rules.
    - `step` is an int from 1 to 6, unique within a Variant.
  - **Rules:**
    - `variants` holds 1 or 2 items, and a third gives `TOO_MANY_VARIANTS` on `.variants`.
    - Variant names are unique per Campaign, case-insensitively.
    - At least one Variant is `active` ("A Campaign needs an active Variant").
    - Keys are unique, and so is each (`segment`, `market`) pair: "A Campaign already exists for this Segment and Market".
  - **`check_campaign_references(campaigns, segment_keys, market_countries, offer_keys, sequence_steps)`** (`sequence_steps` maps a Sequence key to its number of Steps) raises `InvalidCampaigns` in file order:
    - an unknown `segment`, `market`, `offer` or `sequence`;
    - a `step` above the Sequence's Step count;
    - **the angle-note floor (decision 5):**
      - a note that `segments.mentions_aba_512` is true for needs a `US` Market, and one that `mentions_sra_notice` is true for needs a `GB` Market;
      - a note with a currency sign (`$`, `£`, `€`) or the word `usd`, `gbp` or `eur` is refused: "An angle note can't name a price (FR-4)".
  - `campaign_form`/`campaign_from_form` round-trip. `variant_from_fields(name, step_angles, *, existing_names, steps, country_code) -> VariantDef` is the command path. It returns status `active` and raises `CampaignRefused` with the same rules.
- **Persistence.** Migration **`0012_4_2_campaigns`** has down revision `0011_4_3_company_facts`, and `downgrade()` raises.
  - **`offers`:** `id` ULID; `key` `String(32)` (`uq_offers_key`); `name` `String(100)`; `ladder` `Text` (canonical JSON of the rung forms); `version` `Integer` (`ck_offers_version`: at least 1); `created_at` and `updated_at`.
  - **`campaigns`:**
    - `id` ULID; `key` `String(32)` (`uq_campaigns_key`); `name` `String(100)`;
    - `segment_id`, `market_id`, `offer_id` and `sequence_id`, foreign keys to their tables, with no cascade; `uq_campaigns_segment_id_market_id`;
    - `status` `String(8)` (`ck_campaigns_status`: one of the three);
    - `lia_ref` `String(200)` and `lia_date` `Date`, both **nullable**; `ck_campaigns_lia`: both NULL or both set;
    - `version` (at least 1), `created_at` and `updated_at`.
  - **`variants`:**
    - `id` ULID; `campaign_id`, a foreign key to `campaigns`;
    - `name` `String(40)` (`uq_variants_campaign_id_name`);
    - `status` `String(8)` (`ck_variants_status`);
    - `step_angles` `Text` (canonical JSON, `[]` for none);
    - `created_at` and `updated_at`.
  - Every column not marked nullable is NOT NULL.
  - **Triggers (fail-closed backstops):**
    - `trg_campaigns_lia_insert` and `trg_campaigns_lia_update` abort a row whose `status` isn't `draft`, whose Market's `country_code` is `GB`, and whose `lia_ref` or `lia_date` is NULL.
    - `trg_variants_at_most_two` aborts an `INSERT` when the Campaign already has 2 Variants.
  - **Models and registries.** The models go in `db/models/setup.py`. `owners.py` maps every column to `setup`, and `pii_registry.py` classifies every column `none`.
  - **The repositories:**
    - `db/repos/setup/offers.py`: `all_offers`, `insert` and `update`, like `sequences.py`.
    - `db/repos/setup/campaigns.py`: `all_campaigns()` with their Variants, `by_id`, `insert` (with its Variants), `update_config`, `insert_variant`, `update_variant`, `set_status(id, from_status, to_status, at)` (a conditional `UPDATE`; it returns whether exactly one row changed) and `set_lia`.
    - Neither repository has a delete. Rows decode through the domain forms, and a row that doesn't decode raises `InvalidOffers`/`InvalidCampaigns`.
  - Bump the `HEAD` constants, the chain probes (`0012_*` → `0013_*`, "revision 12" → "revision 13") and `test_source_tree` `FILES`. Add a trigger-existence test to `test_persistence_contracts.py`.
- **Setup seed** (`app/setup/api.py`).
  - `check_seed(segments, markets, sequences, offers, campaigns)` adds `_offer_inputs` and `_campaign_inputs`. They give a `TypeError` for an item of the wrong type and a `ValueError` for a repeated key. They re-validate the file form, then run `check_campaign_references` against this sync's Segments, Markets, Offers and Sequences.
  - `CheckedSeed` and `SetupSeedSync` gain `offers` and `campaigns`. `OfferSync` and `CampaignSync` are shaped like `SequenceSync`.
  - **`Setup.seed_sync`** runs the Offers, then the Campaigns, after the Sequences. Keys and codes map to ids through setup's own repositories.
  - **Offers:** created, changed (version n+1) or unchanged, with events like the Sequences. A stored Offer left out of the file stays and is reported.
  - **Campaigns** (decision 4). For each file Campaign, in file order:
    - **No row:** insert at `draft` with no LIA and its Variants. Event `None` → `v1`.
    - **A stored row:**
      - Its Segment or Market differs: `InvalidCampaigns` on `.segment`/`.market`: "can't change: a Campaign keeps its Segment and Market".
      - Stored ∪ file Variant names (case-insensitive) exceed 2: `TOO_MANY_VARIANTS` on `.variants`.
      - A stored Variant is left out: `.variants`: "leaves out the stored Variant <name>: keep it, and set its status to inactive to retire it".
      - Otherwise, when the name, Offer, Sequence, or any Variant's status or angles changed, or a Variant was added: update in place (version n+1, event `v<n>` → `v<n+1>`).
      - Otherwise unchanged.
    - The seed **never writes `status`, `lia_ref` or `lia_date`.**
    - A stored Campaign left out of the file stays and is reported. A file Campaign whose (Segment, Market) pair a left-out stored Campaign holds: `InvalidCampaigns` on `campaigns[<key>]`: "uses the Segment and Market of the stored Campaign <other>, which is no longer in the file: keep that Campaign's key".
  - The seed events use entities `offers` and `campaigns`, cause `seed_sync` and actor `seed`. `log_seed_sync` adds `offers_synced` and `campaigns_synced`.
- **Ops** (`app/ops/api.py`). `Seeds.sync_seed(segments, markets, sequences, company_facts, offers, campaigns)`, with **the new arguments last**. `SeedSync` gains `offers` and `campaigns`, still in one transaction.
- **`Campaigns(clock)`** (`app/setup/api.py`). Each method is a `@use_case` that reads the clock once and runs one transaction.
  - **`activate(campaign_id, lia_ref=None, lia_date=None) -> CampaignChanged`:**
    - An unknown id gives `CampaignRefused({"form": "No Campaign has this id"})`.
    - A status the machine refuses gives `{"form": "This Campaign is <status>, so it can't be activated"}`.
    - For a GB Market: the submitted fields (blank when `None`) go through `lia_from_fields`, with `today` the date in `sender_timezone` (`UTC` while unset or unreadable). New values are stored, with an LIA event first.
    - Elsewhere, any non-`None` LIA argument gives `{"form": "Only a UK Campaign records a legitimate-interests assessment"}`.
    - Then `set_status`, plus an event `draft` → `active` (cause `operator_activate`, actor `operator`).
  - **`pause` and `resume`(campaign_id):** the same refusals. `resume` of a GB Campaign also requires a stored LIA (`LIA_FIRST`). Events `active` → `paused` and `paused` → `active`.
  - **`record_lia(campaign_id, lia_ref, lia_date) -> LiaSaved(campaign_id, changed)`:**
    - It applies only to a GB Campaign, in any status, with the same validation.
    - Equal values are a no-op (`changed=False`, no event).
    - Otherwise it calls `set_lia` and appends an event `<status>` → `<status>` (cause `operator_record_lia`, actor `operator`). Later reads see the new values.
  - **`add_variant(campaign_id, name, step_angles=()) -> VariantAdded`:**
    - It uses `variant_from_fields` against the stored Sequence's Step count and the Market's country.
    - A third Variant gives `CampaignRefused({"form": TOO_MANY_VARIANTS})`.
    - It inserts the Variant `active`, bumps the Campaign's version, and appends the event `v<n>` → `v<n+1>` (cause `operator_add_variant`, actor `operator`).
  - Logs after the commit: `campaign_activated`, `campaign_paused`, `campaign_resumed`, `campaign_lia_saved` and `variant_added`, each with the Campaign `key` only.
- **Queries** (`app/queries/setup.py`).
  - `offers()` and `offer(key)` → `SeededOffer(id, key, version, definition)`.
  - `campaigns()` (by key) and `campaign(campaign_id)` → `SeededCampaign(id, key, name, version, segment_id, market_id, offer_id, sequence_id, status, lia: Lia | None, variants: tuple[SeededVariant(id, name, status, step_angles), ...])`. Variants are ordered by name.
  - **`campaign_active(campaign_id) -> bool`:** `may_run` on the stored status, the LIA and the Market's **current** `country_code`, read live. An unknown id is `False`.
- **The command** (`main.py`).
  - `OFFERS_FILE_*` and `CAMPAIGNS_FILE_*` (`seed/offers.yaml`, `seed/campaigns.yaml`). All six files are required.
  - `_load_seed` also catches `InvalidOffers` and `InvalidCampaigns`. Errors raised during the sync print under their file's label.
  - `format_seed_sync` appends `_sync_block("offers", …)` and then `_sync_block("campaigns", …)` **after** the facts block.
  - `build_app` passes `campaigns=Campaigns(clock)` to `create_app`. Update the docstrings and the `seed sync` help in `main.py` and `__main__.py`.
- **The page.**
  - The Settings page includes `templates/campaigns_panel.html` (`id="campaigns-panel"`), built by `web/campaigns_page.py` from `queries.setup` in the GET's read-only unit. With no Campaigns it reads "No Campaigns yet. Run nextron seed sync to load them."
  - Each Campaign shows its name, status, Segment, Market, Offer and Sequence names, and its Variants ("A (active)"). A GB Campaign also shows "Legitimate-interests assessment: <ref> dated <date>" or "not recorded".
  - **The forms:**
    - `draft`: Activate. A GB Campaign's form adds `lia_ref` and `lia_date` (`type="date"`), prefilled from what is stored.
    - `active`: Pause.
    - `paused`: Resume.
    - GB and not `draft`: "Save the LIA".
  - **The POSTs:** `/settings/campaigns/activate`, `/pause`, `/resume` and `/lia` (`campaign_activate`, `campaign_pause`, `campaign_resume`, `campaign_lia`), all `@personal_data(False)`.
    - They take their fields through `form_fields`. Only `campaign` (a ULID), plus `lia_ref` and `lia_date` where the form has them, each at most once; anything else is `MalformedForm` (400).
    - Each runs its use case in `units.open()`, then renders the panel partial read-only (`hx-target="#campaigns-panel"`, `hx-swap="outerHTML"`).
  - **The notices** (`role="status"`): "Activated <name>.", "Paused <name>: its Steps won't be enrolled or enter the Approval Queue until you resume it.", "Resumed <name>.", "Saved the legitimate-interests assessment for <name>." and "Nothing changed: the assessment already has these values.".
  - **On `CampaignRefused`** the answer is 200: `role="alert"` "Nothing was changed.", plus the form problem, and the LIA fields re-rendered with their submitted values and `aria-invalid="true"`.
- **The four earlier seed test files** (`test_seed_sync.py`, `test_market_sync.py`, `test_sequence_sync.py` and `test_company_fact_sync.py`) change only where they must:
  - `seed_dir()` copies the two new files;
  - the exact outputs, the event counts and the log-name lists gain the new blocks;
  - the direct `Seeds(clock).sync_seed(...)` calls pass the shipped Offers and Campaigns.
- **Tests run as `test`** with temporary data dirs. The domain tests import only the stdlib, pytest and `nextron.domain`.

**Never:**
- No YAML library, second loader or command; no new dependency, adapter, port, setting, job type, trigger, credential or navigation section.
- No Enrollment, `variant_counters`, Provenance write, Draft or Approval Queue (4.8, 3.7, Epic 5). No Campaign creation or editing screens (Epic 11).
- Never let the seed set a Campaign's status or LIA. Never let any setting or route turn off the LIA rule (NFR-7).
- No price or Offer wording passed to drafting from `offers`, and no new Approved Company Fact. The `audit_offer` fact (4.3) stays the only claim a Draft may make about the Offer.
- No editing outside `nextron-engine/`, except this spec, `sprint-status.yaml` and `deferred-work.md`.

### Decisions made overnight (pending user review)

1. **One Offer, a three-rung ladder.**
   - `seed/offers.yaml` holds one Offer, `default`: a reply, then the fixed-fee Private AI & Workflow Audit ($2,500–$3,500, credited in full toward a build), then a build ($10k–$30k), straight from the Glossary.
   - `offers` is commercial configuration for the Operator and later call prep. It isn't Draft wording. Steps never name a price, and Drafts cite only the `audit_offer` fact, which names no price (4.3 decision 7). That way the two don't duplicate each other.
2. **The seed creates Campaigns at `draft` and never touches status or the LIA.** The LIA is entered only in the dashboard, as the Operator's record.
3. **One Campaign per Segment × Market** (a unique index). This matches Epic 11's "A Campaign already exists for this Segment and Market". The seed can't change a stored Campaign's Segment or Market, so a US Campaign can't become a UK one and skip the LIA.
4. **Variants.**
   - The file lists every Variant of a Campaign: 1 or 2, with at least one active. Variants are keyed by name, case-insensitively.
   - They are never deleted. To retire one, set it `inactive`; a stored Variant left out of the file fails the sync.
   - A Campaign's version and event cover its Variants, like 4.1's briefs.
   - The AC's "command" is `Campaigns.add_variant`, which stores the new Variant `active` (Epic 11) and has no screen yet.
   - A third Variant is refused by the seed, by the command and by a database trigger.
5. **Angle notes are per Step of the Campaign's Sequence,** with a floor: no price, ABA 512 only on a US Market and the SRA notice only on a GB one (the 4.1 and 4.3 floors).
6. **LIA entry.**
   - A UK Campaign's Activate form carries the LIA fields. "Save the LIA" edits them once the Campaign is active or paused.
   - Both values are always required, so an LIA can't be cleared.
   - "Not in the future" is judged against today in `sender_timezone` (UTC while it is unset), as the daily backup does. There is no one-day tolerance, because this is a compliance date.
   - "UK" means a Market whose country is `GB`.
7. **`campaign_active` fails closed.**
   - It reads the Market's country live, so a Market whose country changes to GB stops an LIA-less Campaign.
   - An unknown id is `False`.
   - `resume` re-checks the LIA. Triggers refuse a non-draft GB Campaign without an LIA (AD-5's predicate wording).
8. **The events.**
   - Seed and `add_variant` changes are version events, `None` → `v1` and `v<n>` → `v<n+1>`.
   - The Operator's transitions are status events.
   - An LIA save is `<status>` → `<status>` with cause `operator_record_lia`. `events` hold no values.
9. **Deferred to 4.8 and 3.7:** "Provenance bases already recorded keep the reference they recorded". `enroll` (4.8) passes the LIA values it reads from `queries.setup.campaign` at enrollment to `research.record_eligibility_basis` (3.7), which copies them. This story proves that a later read sees the new values. Variant round-robin order is 4.8's.
10. **A stale-page control is refused, not repeated.** For example, Pause on a Campaign that is already paused shows "This Campaign is paused, so it can't be paused" and changes nothing.
11. **The output order.** The offers and campaigns blocks follow the company_facts block, and `sync_seed` takes `offers, campaigns` last. Each story appends its own block.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| First sync | DB at head; the six shipped files | Earlier blocks as in 4.3, then "offers: 1 changed, 0 unchanged", "  default: created (version 1)", "campaigns: 4 changed, 0 unchanged" and four created lines in file order. 4 Campaigns `draft`, no LIA, Sequence `default`, Offer `default`, one Variant `A` `active` with no angles. 5 events. Exit 0. | N/A |
| Second sync | same files | "offers: 0 changed, 1 unchanged", "campaigns: 0 changed, 4 unchanged"; nothing written | N/A |
| Seed edit | Campaign name, Offer terms, or Variant B added | version 2, one event; status and LIA untouched (also on an active Campaign) | N/A |
| Third Variant | the file lists 3, or stored A,C + file A,B | exit 1, `seed/campaigns.yaml: campaigns[<key>].variants: A Campaign can have at most 2 Variants`; DB unchanged | nothing written |
| Invalid | Segment/Market change; a Variant left out; no active Variant; duplicate pair; unknown ref; step 5 on 4 Steps; "$2,500" note; ABA note on a UK Campaign; offer without `reply` first; audit without prices | exit 1, `<label>: <field>: <reason>`; the DB file's SHA-256 is unchanged | nothing written |
| Activate US | `draft` US Campaign | `active`; event `draft`→`active`, `operator_activate`, `operator`; `campaign_active` true | N/A |
| Activate UK, no LIA | blank or missing ref or date | refused: "Record the legitimate-interests assessment reference and date first"; still `draft` | `CampaignRefused` |
| Activate UK, LIA | ref "LIA-2026-01", date today | `active`; `lia_ref`/`lia_date` stored; LIA event then activation event | N/A |
| Bad LIA | date tomorrow (sender tz); "2026-02-30"; ref of 201 characters | refused with the field reason; nothing stored | `CampaignRefused` |
| Pause, resume | `active` → pause → resume | `campaign_active` false right after the pause, true after the resume; two events | N/A |
| Illegal | pause a `draft`; activate an `active`; resume an `active` | refused with "This Campaign is <status>, so it can't be <verb>" | `CampaignRefused` |
| Edit LIA | active UK, new ref | stored; still `active`; `campaign(id).lia` has the new ref; the same values again → "Nothing changed" | N/A |
| Command | `add_variant` B, then C | B `active`, version +1; C refused "A Campaign can have at most 2 Variants" | `CampaignRefused` |
| Backstops | raw `UPDATE` setting a GB Campaign `active` without an LIA; a third raw Variant `INSERT`; one LIA column NULL | aborted | `IntegrityError` |
| Live country | an active US Campaign whose Market's country becomes GB | `campaign_active` false | N/A |
| Bad POST | missing CSRF; an extra field; a non-ULID `campaign` | 403 / 400 / 400; nothing written | N/A |

</frozen-after-approval>

## Code Map

- **Seed sync, to extend:**
  - `nextron/app/setup/api.py`:
    - the docstring (the Sequences paragraph and the `check_seed` bullets, about 77-112);
    - the constants (332-339);
    - `SequenceSync`/`SetupSeedSync`/`CheckedSeed`/`check_seed`/`log_seed_sync` (372-437);
    - `Setup.seed_sync` (530-539);
    - `_sequence_inputs`/`_sync_sequences`/`_record_sequence` (741-805): the pattern to mirror.
    - `_refuse_shared_markets` (693) is the pattern for the left-out pair check.
  - `nextron/app/ops/api.py`: the docstring (89-103), `SeedSync` and `Seeds.sync_seed` (358-399).
  - `nextron/main.py`:
    - the constants (447-454);
    - `_sync_block`, `_facts_block`, `format_seed_sync`, `_load_seed` and `seed_sync` (1352-1488);
    - the "Seed sync" docstring paragraph;
    - `build_app`'s `create_app(...)` (about 558).
  - `nextron/__main__.py`: the docstring and the `seed sync` help and description.
- **Domain patterns:**
  - `nextron/domain/sequences.py` (the `_Validator`, paths, text rules, `KEY_PATTERN`, `MAX_TEXT`, form round trip): mirror its private helpers, don't import them.
  - `nextron/domain/markets.py` (`CODE_PATTERN`, `GB`, `US`).
  - `nextron/domain/segments.py:228-245` (`mentions_aba_512`, `mentions_sra_notice`): reuse.
  - `nextron/domain/job.py:179-255` and `nextron/domain/machines.py` (`Transition`, `IllegalTransition`): the machine pattern.
  - `nextron/domain/company_facts.py` (`InvalidFact`-style problem mappings): the pattern for `CampaignRefused`.
- **Persistence:**
  - `db/alembic/versions/0010_4_1_sequences.py` (foreign keys) and `0011_4_3_company_facts.py` (triggers, `downgrade` raising);
  - `db/models/setup.py` (constants and docstring);
  - `db/owners.py` and `db/pii_registry.py`;
  - `db/repos/setup/sequences.py` (canonical JSON, decoding, `FIRST_VERSION`) and `db/repos/setup/__init__.py` (docstring);
  - `db/eventlog.py` `append`.
- **Queries:** `nextron/app/queries/setup.py` (`SeededSequence`, `sequences()`, `get`; its docstring). `SettingNotSet` handling for `sender_timezone`: `app/ops/api.py:779-789`.
- **Web:**
  - `nextron/web/app.py`: the docstring route list, `create_app` parameters, `settings_page`, and `facts_answer` and the facts POSTs (402-481, the write-then-render-read-only pattern);
  - `web/facts_page.py` (panel context, `submitted_*`, `Refusal`, notices);
  - `templates/facts_panel.html` (field macros, `aria-invalid`);
  - `templates/settings.html` (include the panel after the settings form);
  - `web/forms.py` (`form_fields`, `MalformedForm`);
  - `web/templating.py` (`plain`, `csrf_field`);
  - `static/app.css`;
  - `domain/ids.py` `is_ulid`.
- **Tests to update:**
  - `tests/dashboard.py` (`create_app`, about 226; build `Campaigns(self.clock)`, and its docstring);
  - `tests/contracts/test_web_contracts.py` (the route and template lists);
  - `tests/contracts/test_persistence_contracts.py` (`HEAD`, the probes and the trigger test next to the company_facts one);
  - `tests/unit/test_migrations.py`;
  - `tests/app/test_serve_process.py`;
  - `tests/contracts/test_source_tree.py` `FILES`;
  - the four seed test files: `seed_dir`, the summary constants (`test_seed_sync.py:136-160`) and the direct `sync_seed` calls (grep `sync_seed(`);
  - `tests/unit/test_settings_page.py` (only if the page's new section breaks an assertion).
- **Contracts that apply:**
  - `test_domain_stdlib_only.py`;
  - `test_error_contracts.py` (unique codes `invalid_offers`, `invalid_campaigns` and `campaign_refused`);
  - `test_log_event_names.py`;
  - `test_owner_contracts.py`;
  - import-linter c6/c7;
  - the web form, autoescape and `|safe` checks.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/domain/offers.py`, `nextron/domain/campaign.py` -- the types, the machine, the LIA rules, the file validation, the references and floor, and the form round trips -- the Offer and Campaign definitions.
- [x] `nextron-engine/seed/offers.yaml`, `seed/campaigns.yaml` -- the Design Notes content, with header comments: what each is, that `nextron seed sync` loads it, the YAML subset, that the seed never sets status or the LIA, and that Variants are retired, never removed.
- [x] `nextron-engine/nextron/db/models/setup.py`, `db/models/__init__.py` (if needed), `db/alembic/versions/0012_4_2_campaigns.py`, `db/owners.py`, `db/pii_registry.py`, `db/repos/setup/offers.py`, `db/repos/setup/campaigns.py`, `db/repos/setup/__init__.py` -- the tables, triggers, mapping and repositories.
- [x] `nextron-engine/nextron/app/setup/api.py`, `app/queries/setup.py`, `app/ops/api.py` -- the seed sync extension, `Campaigns`, the result types, the queries and `campaign_active`; `Seeds` takes the two new files.
- [x] `nextron-engine/nextron/main.py`, `nextron/__main__.py` -- the two files, the two blocks, `create_app(..., campaigns=...)`, the docstrings and help.
- [x] `nextron-engine/nextron/web/app.py`, `web/campaigns_page.py`, `web/templates/campaigns_panel.html`, `web/templates/settings.html`, `web/static/app.css` -- the Campaigns section and its four POSTs.
- [x] `nextron-engine/tests/unit/test_offers_domain.py`, `tests/unit/test_campaign_domain.py` -- stdlib-only:
  - every value rule and rule with its exact path and reason;
  - all nine status × event pairs;
  - `may_run`, `needs_lia` and `lia_from_fields`, including a date of exactly today, tomorrow and invalid dates;
  - the floor;
  - the round trips.
- [x] `nextron-engine/tests/unit/test_campaign_seed.py` -- the shipped files validate into exactly the Design Notes values: the ladder equals the Glossary prices; four Campaigns in the AC's order, each on Offer `default`, Sequence `default` and Variant `A` `active`. The references pass against the shipped Segments, Markets and Sequences.
- [x] `nextron-engine/tests/app/test_campaign_sync.py` -- the seed rows of the I/O Matrix through `main.seed_sync(env="test", seed_dir=...)`. Also:
  - the `Campaigns` use cases and their events and logs;
  - `campaign_active`, including the live-country row;
  - the triggers and constraints;
  - a row that no longer decodes;
  - the `sender_timezone` today, set and unset;
  - the CLI help naming the two files.
- [x] `nextron-engine/tests/app/test_campaigns_panel.py` -- the Settings section and every POST row through `Dashboard(units=units)`: notices, the refused LIA keeping its values, `aria-invalid`, CSRF and malformed refusals, and `no-store` on the GET.
- [x] `nextron-engine/tests/contracts/test_campaign_contracts.py`:
  - the offers and campaigns repositories' writers are used only in `app/setup/api.py`;
  - `set_status` is called only inside the `Campaigns` transitions;
  - no web module imports a repository;
  - no setting key or route name can turn off the LIA rule (NFR-7).

  Each check has a probe.
- [x] The earlier tests from the Code Map -- only the forced changes.
- [x] `nextron-engine/README.md` -- "Seeds" covers six files and six blocks. Add a "Campaigns, Offers and Variants" subsection (the fields, the machine, the LIA, `campaign_active`, the panel and the seed rules) and a migrations-table row.

**Acceptance Criteria:**
- Given the full suite, when it runs through the suite lock and `uv run lint-imports` runs, then everything passes and every contract is kept.
- Given every model column, when the persistence contracts run, then `offers`, `campaigns` and `variants` are fully mapped to `setup` and classified `none`, and the migration matches the models.
- Given the 2.1, 3.1, 4.1 and 4.3 behaviour, when their tests run with only the forced changes, then they pass.

## Implementation Notes

- **Domain.** `nextron/domain/offers.py` and `nextron/domain/campaign.py` follow the spec. `campaign.py` also exposes the helpers it shares between the file, the command path and the repository: `parse_date`, `stored_lia(ref, date_text, path)` (decodes a stored LIA and never compares it with today), `names_price` and `note_floor_problem`. The floor's reasons are "names ABA Formal Opinion 512, which only a US Campaign's note may (this Market is GB)" and "names the SRA notice, which only a GB Campaign's note may (this Market is US)". `MARKET` codes use `markets.CODE_PATTERN`, and `needs_lia` compares with `markets.GB`.
- **Paths and reasons.** The duplicate-pair refusal is on `campaigns[<key>]`. A repeated Variant name is on `variants[<index>].name`, and a repeated note step is on `step_angles[<i>].step`. The command path keys a note problem as `step_angles`, with its first problem only.
- **Persistence.** Migration `0012_4_2_campaigns` (down `0011_4_3_company_facts`) creates the three tables, the named constraints and the three triggers. The two LIA triggers share one `WHEN` condition: `status <> 'draft'`, the Market's `country_code` is `GB`, and `lia_ref` or `lia_date` is NULL.
  - `db/repos/setup/offers.py` adds a `by_key` read, which `queries.setup.offer` uses.
  - `db/repos/setup/campaigns.py` reads each Campaign joined with its Segment, Market, Offer and Sequence keys and the Market's live `country_code`. It decodes the Campaign through `campaign_from_form`, with its Variants by name. `by_id` returns `None` for a text that isn't a ULID.
- **Setup.** `check_seed(segments, markets, sequences, offers, campaigns)`, `Setup.seed_sync` (`_sync_offers`, then `_sync_campaigns`), `OfferSync`, `CampaignSync`, `Campaigns`, `CampaignChanged`, `LiaSaved` and `VariantAdded` are all in `app/setup/api.py`.
  - "Today" is the date in `sender_timezone`. It falls back to UTC on `SettingNotSet`, `InvalidSettingValue` or an unloadable zone.
  - A Variant whose name changes only in case counts as changed: the seed updates the stored name and gives the Campaign a version.
  - `campaign_lia_saved` is logged only when an LIA was written, and Activate logs it before `campaign_activated`.
  - Each log carries only the key, in the field `campaign`.
- **Implementation choice (not in the spec): no write the trigger would abort.** The LIA triggers refuse any write to a non-draft GB row without an LIA. That row can only exist when its Market's country became `GB` after the Campaign left `draft`. For such a row, `pause` and `add_variant` refuse with `{"form": LIA_FIRST}`, like `resume`, instead of failing with `IntegrityError`. `record_lia` fixes the row. A seed edit of such a row still fails with "seed sync failed (IntegrityError); nothing was changed".
- **Ops and main.** `Seeds.sync_seed(..., company_facts, offers, campaigns)` and `SeedSync(..., company_facts, offers, campaigns)` keep the new fields last. `main.py` adds the `OFFERS_FILE_*` and `CAMPAIGNS_FILE_*` constants, the two `_sync_block`s after the facts block, the two labelled refusals and `create_app(..., campaigns=Campaigns(clock))`. The `seed sync` help names all six files.
- **Web.** `web/campaigns_page.py` builds the panel from `queries.setup`. The page and the POST answers read it under the one context name `campaigns_panel`.
  - A refusal's alert is "Nothing was changed for <name>." (plain "Nothing was changed." for an id that names no listed Campaign; P4), plus the form problem when there is one. Each field's reason sits beside its field, and only a refused field gets `aria-invalid="true"`.
  - The four POSTs share one `campaigns_answer` helper in `web/app.py`.
  - The Activate form's LIA fields are optional, and each may be given at most once.
- **Earlier tests, forced changes only.** These files got the new `seed_dir` files, the appended `offers`/`campaigns` blocks, the event counts and event order, the log-name lists, and the two new `sync_seed` arguments:
  - `test_seed_sync.py`, `test_market_sync.py`, `test_sequence_sync.py` and `test_company_fact_sync.py`;
  - `test_facts_page.py`, whose fixture calls `sync_seed`.

  A call whose inputs store no `default` Sequence or no `uk` Market passes `()` Campaigns. The other changes are the `HEAD` constants, the chain probes (`0013`/`revision 13`), `test_source_tree` `FILES`, the web-contract route and template lists, and `tests/dashboard.py`.
- **Review patches (pass 1):**
  - **P1.** On `GET /settings` only, `settings_page` (`web/app.py`) catches `InvalidCampaigns`, `InvalidOffers`, `InvalidSequences`, `InvalidSegments` and `InvalidMarkets` (the tuple `STORED_ROW_REFUSED`) around building the Campaigns context. It then renders `campaign_rows.unavailable_context(message)` (`web/campaigns_page.py`): a `role="alert"` "The Campaigns can't be shown: <field>: <reason>", no Campaign and no Campaign form. `Panel.unavailable` hides "No Campaigns yet", and the settings form renders as usual. Test: `test_a_stored_row_that_no_longer_decodes_leaves_the_settings_reachable` in `tests/app/test_campaigns_panel.py`, which corrupts `variants.step_angles`.
  - **P2.** The README's "Variants by command" paragraph says a Variant added by command must be added to `seed/campaigns.yaml` before the next seed sync, or the sync refuses ("leaves out the stored Variant <name>", decision 4).
  - **P3.** The same paragraph says `trg_variants_at_most_two` refuses *inserting* a third Variant.
  - **P4.** `refused_notice(refusal, name)` names the refused Campaign when its id is a listed one: "Nothing was changed for <name>. <problem>." With no form problem it reads "Nothing was changed for <name>."; for an unknown id it is unchanged. `panel_context` passes the name, the README's panel paragraph follows, and the panel tests assert the new alerts.
  - **P5.** `test_a_campaign_moved_to_another_offer_or_sequence_updates_in_place`: `law_private_ai_us` moves to a second Offer, `extra`, and `law_private_ai_uk` to a second Sequence, `other`, each "changed (version 1 -> 2)". The new `offer_id` and `sequence_id` are stored, one `v1`→`v2` event is written per Campaign, and status and LIA are untouched. `seed_dir` gained a `sequences` text.
  - **P6.** `test_notes_re_sync_unchanged_and_a_note_edit_or_a_case_rename_is_a_change`: a re-sync of a Campaign with notes is unchanged (SHA-256 equal). A note-text edit gives version 1→2 and the stored JSON updates. A case-only rename `A`→`a` gives 2→3. Each writes one event.
  - **P7.** `test_a_stored_offer_that_no_longer_decodes`: after `UPDATE offers SET ladder = 'x'`, `queries.setup.offers()` raises `InvalidOffers` on `offers[default].ladder`, and `sync()` exits 1 with `seed/offers.yaml: offers[default].ladder: is not valid JSON`.
  - **P8.** `test_today_is_the_date_in_sender_timezone` gains a raw stored `sender_timezone` of `Mars/Base`. The test checks that `get` raises `InvalidSettingValue`, then that UTC's dates are accepted and refused.
  - **P9.** `test_a_paused_uk_campaign_shows_resume_and_its_prefilled_lia_form`: a paused UK Campaign shows Resume (no Pause) and the "Save the LIA" form prefilled with the stored values.
  - **P10.** `test_sync_seed_refuses_bad_offers_before_any_transaction` and `test_sync_seed_refuses_bad_campaigns_before_any_transaction` cover each wrong type (`TypeError`), repeated key (`ValueError`) and broken rule or reference (`InvalidOffers`/`InvalidCampaigns`). Each is raised with `uow.transaction` patched to fail, and the database's SHA-256 is unchanged.

**Final verification (after the pass-1 patches)**
- **The spec's targeted set** plus `test_facts_page.py`, `test_settings_page.py`, `test_migrations.py` and `tests/contracts`: 1601 passed, 1 skipped.
- **`uv run lint-imports`:** 29 kept, 0 broken.
- **`NEXTRON_ENV=test uv run nextron seed sync --help`** names the six files.
- **The full suite through `suite_lock.py` (`track-C`):** 6078 passed and 2 skipped, with no failure (the job-runner flake didn't appear). `lint-imports` exited 0.

## Spec Change Log

## Review Triage Log

**Pass 1.** Three layers reviewed the diff since the baseline (`nextron-engine/` only):
- blind hunter (BH): 14 findings;
- edge-case hunter (EC): 12;
- verification gap (VG): 6.

That is 32 raw findings, which deduplicate into 24 groups: 10 patched (P1–P10), 0 deferred and 14 rejected (R1–R14). Each raw finding appears once:
- BH: P1, P2, P3, R1, R2, R3, R4, R5, R6, R7, R8, R9, R10, R11 (14);
- EC: P1, P3, P4, R1, R2, R3, R5, R8, R10, R12, R13, R14 (12);
- VG: P5, P6, P7, P8, P9, P10 (6).

| # | Finding(s) | Verdict | Evidence | Route |
|---|---|---|---|---|
| P1 | BH2, EC1: the whole Settings page answers 500 when any Segment, Market, Sequence, Offer or Campaign row no longer decodes | medium | **Real:** `settings_page` (`web/app.py:411-414`) now calls `campaigns_context()`, which reads all five through `queries.setup`; any `Invalid*` escapes and no handler catches it. Before this story the page read only `settings`, and a bad stored setting was flagged on the page. A rule tightened in a later version is enough to trigger it, and then the Operator can't reach any setting. | patch: catch the five decode errors around the panel context on `GET /settings`, and render the settings form with a Campaigns section that shows a `role="alert"` "The Campaigns can't be shown: <field>: <reason>" and no Campaign forms; add a test with a corrupted `variants.step_angles` |
| P2 | BH5: after `add_variant`, the next seed sync is refused until the Variant is added to `seed/campaigns.yaml`, and the README doesn't say so | low | **Real:** that is decision 4's intended behaviour, but the README's "Variants by command" paragraph (`README.md:767`) doesn't mention it, so a developer meets the refusal unwarned. The fix is one sentence. | patch: the README says a Variant added by command must be added to `seed/campaigns.yaml` before the next seed sync, or the sync refuses it |
| P3 | BH7, EC8: no delete trigger, and `trg_variants_at_most_two` fires only on `INSERT`, so a raw `UPDATE variants SET campaign_id` can make a third Variant | low | **Real, in part:** the README says the trigger refuses a third Variant "whatever writes it", which is untrue for an `UPDATE` of `campaign_id`. No code updates `campaign_id` or deletes (the repository has neither), and the spec names exactly three triggers, so the extra triggers are rejected. The README claim is a direct correction. | patch: the README says the trigger refuses inserting a third Variant |
| P4 | EC12: a form-level refusal's alert doesn't name the Campaign whose control was refused | low | **Real:** `refused_notice` (`web/campaigns_page.py:209`) gives "Nothing was changed. This Campaign is paused, so it can't be paused." at the top of a panel listing four Campaigns. The fix is a direct correction. | patch: the alert names the Campaign when the refused id is a listed Campaign: "Nothing was changed for <name>. <problem>." (unchanged for an unknown id); update the tests |
| P5 | VG1: a stored Campaign moved to another existing Offer (or Sequence) by the seed is never tested | low (gap, pre-verified) | **Filed evidence:** with `found.offer_id, found.sequence_id` dropped from the comparison, every test passed. | patch: re-sync with `law_private_ai_us` pointing at a second Offer: "changed (version 1 -> 2)", the new `offer_id`, one `v1`→`v2` event, status and LIA untouched; the same for a second Sequence if cheap |
| P6 | VG2: Variant note edits, and a re-sync of a Campaign with notes, are never tested | low (gap, pre-verified) | **Filed evidence:** `StoredVariant.definition` without its notes, or a comparison of name and status only, passes every test. | patch: sync a file with notes, re-sync (unchanged, DB SHA-256 equal), then change only the note text (version 1→2, stored JSON updated, one event); add the case-only rename |
| P7 | VG3: a stored Offer that no longer decodes has no test | low (gap, pre-verified) | **Filed evidence:** deleting `except InvalidOffers` in `seed_sync`, or the `_stored` guard, breaks no test. | patch: mirror `test_a_stored_campaign_that_no_longer_decodes` with `UPDATE offers SET ladder = 'x'`: `queries.setup.offers()` raises `InvalidOffers` and `sync()` prints the labelled field |
| P8 | VG4: the UTC fallback for a stored but unreadable `sender_timezone` is never tested | low (gap, pre-verified) | **Filed evidence:** narrowing the `except` to `SettingNotSet` passes every test. | patch: add a raw `sender_timezone = 'Mars/Base'` case to `test_today_is_the_date_in_sender_timezone`, expecting the UTC accept and refuse dates |
| P9 | VG5: the "Save the LIA" form on a paused UK Campaign is never rendered in a test | low (gap, pre-verified) | **Filed evidence:** `save_lia` built only for `active` passes every test. | patch: activate `law_private_ai_uk` with an LIA, pause it, assert the Resume form and the prefilled LIA form |
| P10 | VG6: the pre-transaction input checks for Offers and Campaigns have no test | low (gap, pre-verified) | **Filed evidence:** dropping the re-validation in `_campaign_inputs`, or the repeated-key `ValueError`, breaks no test. | patch: two parametrized tests mirroring `test_sync_seed_refuses_bad_sequences_before_any_transaction` (wrong type, repeated key, a broken rule), with no transaction opened |
| R1 | BH1, EC4: a seed sync after a Market's country becomes GB fails with an unnamed "seed sync failed (IntegrityError)" when it changes an active, LIA-less Campaign in that Market | low | **Real but fails safe:** nothing is changed, and `campaign_active` already stops that Campaign. It needs the seed to move a Market to GB, which `uq_markets_country_code` and the `uk` Market make implausible. The fix adds a guard branch to the sync. | rejected |
| R2 | BH3, EC2: the Campaign POSTs catch only `CampaignRefused`, so an `InvalidCampaigns` or a trigger abort answers 500 | low | Through the use cases no trigger can fire (`_refuse_lia_gap`, LIA stored before activation), and a row that doesn't decode now shows no forms (P1). It needs a corrupted row between the GET and the POST. The fix adds branches. | rejected |
| R3 | BH4, EC3: `campaign_active` raises `InvalidCampaigns` on a row that doesn't decode instead of returning `False` | false | **Refuted:** an exception stops the caller (enrollment, the gate), so no Campaign runs. The safety outcome claimed doesn't occur; loud failure on corrupt data is the frozen spec's decode rule. Decoding four rows per call costs nothing measurable. | rejected |
| R4 | BH6: `uq_variants_campaign_id_name` is case-sensitive while the rule is case-insensitive | low | Only a raw insert can add `a` beside `A`; every app path checks case-insensitively. The spec names that constraint. | rejected: it would change the approved spec |
| R5 | BH8, EC9: the price floor misses "2,500 dollars", "pounds", "euros" and other currency signs | low | **Spec definition:** the frozen floor lists exactly `$`, `£`, `€`, `usd`, `gbp` and `eur`. 4.3 R14 rejected the same keyword-broadening. 4.13's grounding rules are the guard on Drafts, and "pounds" alone has false positives. | rejected: it would change the approved spec (for Mac) |
| R6 | BH9: the Offer ladder's order past the first rung, and audit ≤ build pricing, aren't enforced | low | **Spec definition:** the frozen rules are exactly the four listed. The shipped ladder is in order. | rejected: it would change the approved spec (for Mac) |
| R7 | BH10: editing an LIA overwrites the previous reference, and the event holds no value | low | **Decisions 8 and 9:** `events` hold no values, and each enrollment's Provenance basis copies the LIA in force (4.8, 3.7). No enrollment exists yet, so no history is lost today. | rejected: it would change the approved spec (for Mac: a Campaign-level LIA history) |
| R8 | BH11, EC11: `activate(id)` with no LIA arguments ignores an LIA stored on a draft by `record_lia` | low | **Spec behaviour:** "the submitted fields (blank when `None`) go through `lia_from_fields`". The page's Activate form is prefilled with the stored LIA, so only a direct API call meets it, and it fails closed. | rejected: it would change the approved spec |
| R9 | BH12: the seed can switch an active Campaign's Sequence or Offer | false | **Refuted by the planning:** Epic 11 says a Campaign's "Offer and Sequence can change and apply to new Enrollments only". No Enrollment exists yet; 4.8/4.9 bind the Steps an Enrollment runs. | rejected (note for 4.8) |
| R10 | BH13, EC10: the LIA date has no earliest bound, so a typo year passes | low | The spec's rule is "a real date, not in the future". The panel shows the stored date. The fix is a new rule. | rejected: it would change the approved spec |
| R11 | BH14: `_today` falls back to UTC with no log line | low | An unreadable `sender_timezone` is already flagged on the Settings page (`stored_problems`). The fix adds logging for a rare state. | rejected |
| R12 | EC5: a new file Campaign listed before a stored one whose pair it takes fails with an unnamed `IntegrityError` | low | It needs a pair swap between two Campaigns in one edit; the stored one's "can't change" refusal names it when listed first. Nothing is changed either way. The fix widens the held-pair check and its message. | rejected |
| R13 | EC6: a stored Campaign left out of the file isn't re-checked when this sync shrinks its Sequence or flips its Market's country | low | Same class as 4.3 R3: it needs several unusual seed edits, and file Campaigns are re-checked on every sync. A note on a missing Step is never used. | rejected |
| R14 | EC7: the LIA triggers accept a blank or malformed `lia_ref`/`lia_date` written by raw SQL | low | Every app write goes through `lia_from_fields`; the backstop's job, per the spec, is the missing LIA. Decoding refuses such a row loudly. | rejected |

## Design Notes

**`seed/offers.yaml`** (after the header comment):

```yaml
offers:
  - key: default
    name: Reply, then the audit, then a build
    ladder:
      - kind: reply
        name: A reply
        terms: The first ask is only a reply; Steps never name a price or propose the audit.
      - kind: audit
        name: Private AI & Workflow Audit
        price_usd_min: 2500
        price_usd_max: 3500
        terms: Fixed fee, credited in full toward a build.
      - kind: build
        name: Build
        price_usd_min: 10000
        price_usd_max: 30000
```

**`seed/campaigns.yaml`** (after the header comment; the AC's order):

```yaml
campaigns:
  - key: law_private_ai_us
    name: Law-PrivateAI × US
    segment: law_private_ai
    market: us
    offer: default
    sequence: default
    variants:
      - name: A
        status: active
  # law_private_ai_uk (Law-PrivateAI × UK, market uk), ops_automation_us (Ops-Automation × US),
  # ops_automation_uk (Ops-Automation × UK): the same shape, written out in full
```

Quote any value with `: `, a leading special character, or `'` (doubled inside single quotes), per the reader's subset. If the reader refuses `×`, use it quoted. The prices are the Glossary's [ASSUMPTION] values, validated on the first 10 held calls (FR-69).

## Verification

**Commands** (from `nextron-engine/`):
- `uv run pytest -q tests/unit/test_offers_domain.py tests/unit/test_campaign_domain.py tests/unit/test_campaign_seed.py tests/app/test_campaign_sync.py tests/app/test_campaigns_panel.py tests/app/test_seed_sync.py tests/app/test_market_sync.py tests/app/test_sequence_sync.py tests/app/test_company_fact_sync.py tests/unit/test_settings_page.py tests/unit/test_migrations.py tests/contracts` -- expected: all pass.
- `uv run lint-imports` -- expected: every contract kept.
- `NEXTRON_ENV=test uv run nextron seed sync --help` -- expected: names the six files.
- The full suite through `suite_lock.py` (`track-C`), run by the orchestrator only -- expected: exit 0.
