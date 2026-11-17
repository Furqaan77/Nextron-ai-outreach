---
title: "Story 4.11: Fill in Furqaan's signature and the compliance footer"
type: 'feature'
created: '2026-11-18'
status: 'done'
route: 'dispatch'
baseline_commit: '88888d818b852f0c2e0e28900e72bf2116b1fbcb'
review_loop_iteration: 0
story_key: '4-11-fill-in-furqaan-s-signature-and-the-compliance-footer'
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-4-context.md'
  - '{project-root}/nextron-engine/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Every email must end with Furqaan's signature, Nextron's legal name, a postal address and an opt-out line, plus a UK privacy line (FR-23, CAN-SPAM, PECR, UK GDPR Art. 14). Story 3.1 seeded each Market's footer template and `render_footer_template`, but the footer settings, the renderer that fills them, the completeness test the send gate needs, the `footer_snapshots` table and the `FooterChanged` publication don't exist yet.

**Approach:** Declare the footer settings in `settings_schema`. Add a stdlib-only `nextron/domain/render.py` (the spine's `domain.render`) with `footer`, `footer_complete` and helpers built on `markets.render_footer_template`. Add a `queries.setup.footer_settings()` read. Make `Setup.save`/`Setup.set` publish `FooterChanged` per affected Market in the same transaction. Create `footer_snapshots` (owner `drafting`) with a reuse-on-identical-content repository. Add an NFR-7 contract proving that no setting or control removes or shortens the footer.

## Boundaries & Constraints

**Always:**
- **Settings** (`nextron/domain/settings_schema.py`). Add five `Key` members and `SCHEMA` entries, all type `STRING` in `Section.FOOTER_AND_BOOKING`. Insert them in this order **just before** `US_SOLICITATION_LABEL` in both `Key` and `SCHEMA`, so the page shows the footer in footer order:

  | key | label | default | rules |
  |---|---|---|---|
  | `sender_signature` | Sender signature | `REQUIRED` | `max_length(200)`, `NO_PRICE_RULE` |
  | `legal_name` | Legal name | `REQUIRED` | `max_length(200)`, `NO_PRICE_RULE` |
  | `postal_address` | Postal address | `REQUIRED` | `max_length(300)`, `NO_PRICE_RULE` |
  | `optout_line` | Opt-out line | `"Not relevant? Reply 'no' and I won't contact you again."` | `max_length(200)`, `NO_PRICE_RULE`, `OPTOUT_REPLY_RULE` |
  | `privacy_notice_url` | Privacy notice URL | `REQUIRED` | `max_length(500)`, `HTTPS_URL_RULE` |

  - Each unit (hint) says where the value appears and, for the required ones, that "no email is sent while it is blank" (for `privacy_notice_url`: "no UK email"). The postal address is one line.
  - `us_solicitation_label` (3.1) is reused, never declared again. It gains `NO_PRICE_RULE`, and its comment names 4.11's footer.
  - **`NO_PRICE_RULE`** ("must not name a price"): the value names a price per a single definition, `names_price`. Move `CURRENCY_SIGNS`, `CURRENCY_WORDS`, the word-break pattern and `names_price` unchanged from `domain/campaign.py` into a new leaf `nextron/domain/prices.py` (stdlib only). `campaign.py` imports them from there and keeps its names. Importing `campaign` from `settings_schema` would be a cycle through `markets`.
  - **`OPTOUT_REPLY_RULE`** ("must tell the reader to reply to opt out (hold the word reply)"): the lowercased value holds `reply` as a word.
  - **`HTTPS_URL_RULE`** ("must be an https link"): `nextron.domain.company_facts.is_link(value)` and `urlsplit(value).scheme.lower() == "https"`.
  - **Required.** Being `REQUIRED` makes Story 1.14's `required_settings` doctor check (and the health panel's readiness list) report them as missing required settings. `Setup.save` already refuses to clear a set required key, and a blank `optout_line` means its default. No new doctor check, so no pinned doctor-id list changes.
- **`nextron/domain/render.py`** is stdlib only and imports only `nextron.domain.*`.
  - **`FooterSettings`** (frozen dataclass): the six values as `str`, `""` for an unset or unreadable one. Its fields are the six setting keys.
  - **`FOOTER_SETTING_KEYS`**: the six keys in declaration order. **`PLACEHOLDER_OF`**: setting key → `FooterPlaceholder` (`privacy_notice_url` → `privacy_line`; the others → their own name).
  - **`PRIVACY_SOURCE_PHRASES`** (read-only mapping, keyed by Provenance source-kind code):

    | code | phrase |
    |---|---|
    | `website` | your firm's website |
    | `registry` | Companies House |
    | `signal_inbox` | a public post |
    | `csv` | a business list |
    | `referral` | a colleague's referral |
    | `finder` | a business email directory |

  - **`privacy_line(source, url) -> str`**:
    - `"Your details came from <phrase>. Privacy notice: <url>"`;
    - `"Your details came from <phrase>."` when `url` is blank;
    - a source not in the mapping (or not a `str`) raises `ValueError("no privacy-line phrase for this Provenance source")`, which never shows the value.
  - **`footer(settings, market, provenance_source=None) -> str`**: `render_footer_template(market.footer_template, values)`. The values are the five setting values, plus `privacy_line` = `privacy_line(provenance_source, settings.privacy_notice_url)` when `market.privacy_line_required`, else `""`.
    - So: US (template has `{us_solicitation_label}`) gives signature, legal name, address, opt-out, then the label only when non-blank. UK gives the same four, then the privacy line.
    - Blank values drop their line, so drafting proceeds with an incomplete footer.
  - **`footer_complete(settings, market) -> FooterCheck`**:
    - `FooterCheck` is a frozen dataclass with `missing: tuple[str, ...]` (setting keys, in `FOOTER_SETTING_KEYS` order), `complete` (no missing) and `__bool__` = `complete`.
    - `missing` lists every key of `REQUIRED_PLACEHOLDERS` (`sender_signature`, `legal_name`, `postal_address`, `optout_line`) whose value is blank after `strip()`, **checked explicitly whatever the template**. It adds `privacy_notice_url` when `market.privacy_line_required` and it is blank.
  - **`incomplete_notice(check) -> str`**: `"Sending blocked: footer incomplete (<label>, <label>)"`, using each key's settings label. 4.12's Draft view, 5.x's card and 6.9's gate show this one text.
  - **`footer_sha256(text) -> str`**: the lowercase hex SHA-256 of UTF-8 `text`.
- **`queries.setup.footer_settings() -> FooterSettings`**: each key through `get`. `SettingNotSet` or `InvalidSettingValue` gives `""`, so a stored text that no longer parses counts as missing (fail closed).
- **`FooterChanged`** (`nextron/app/setup/api.py`).
  - Inside the same transaction, after the writes, `Setup.save` and `Setup.set` publish one `FooterChanged(market_id=<markets.id>)` per stored Market, in code order, whose template uses the placeholder of any **changed** footer key.
  - A key is changed when its effective text changes:
    - `save`: the existing `changed` list, deletes included;
    - `set`: the new canonical text differs from the stored text, or from `default_text` when there is no row. `set` still upserts as today.
  - Templates are read through a new `markets_repo.footer_templates() -> tuple[tuple[str, tuple[str, ...] | None], ...]`: (`id`, the decoded `footer_template` lines) by code. It gives `None` when the column isn't a JSON list of `str`. A `None` template counts as using every placeholder, so a bad row is over-notified, never skipped.
  - No Market, or no changed footer key, publishes nothing. No subscriber is registered: `drafting.recompose_ready` comes in 4.12, and `main.build_subscribers` stays empty.
- **`footer_snapshots`** (owner `drafting`). Migration `0013_4_11_footer_snapshots` (down `0012_4_2_campaigns`; `downgrade()` raises), model `nextron/db/models/drafting.py` (`FooterSnapshotRecord`):
  - **Columns:** `id` (ULID PK); `market_id` (FK `markets.id`, no cascade); `sha256` (`String(64)`; `ck_footer_snapshots_sha256`: length 64 and only `0-9a-f`); `text` (`Text`; `ck_footer_snapshots_text`: `length(text) > 0`); `created_at`. All NOT NULL, with `uq_footer_snapshots_market_id_sha256`.
  - **Repository** `nextron/db/repos/drafting/footer_snapshots.py`:
    - `ensure(market_id, text, at) -> str` returns the id of the row with the same (`market_id`, `footer_sha256(text)`), else inserts one. Identical content reuses the row. It raises `ValueError` for a blank `text`.
    - `by_id(snapshot_id) -> StoredFooterSnapshot | None` (`id`, `market_id`, `sha256`, `text`, `created_at`). `None` for a non-ULID or unknown id.
    - There is no update or delete function.
  - **Owner and PII:** `owners.py` maps every column to `drafting`. `pii_registry.py`: `text` is `redact` (it holds the sender's name and Nextron's address, like `settings.value`); every other column is `none`.
- **NFR-7 contract** `tests/contracts/test_footer_contracts.py`. Each part has a probe proving it catches a violation.
  - **(a)** Every footer setting key is in `Section.FOOTER_AND_BOOKING`, is a `STRING`, and is either `REQUIRED` or has a non-blank default, except `us_solicitation_label` (it only adds a line). The set of `Section.FOOTER_AND_BOOKING` settings is exactly `FOOTER_SETTING_KEYS`.
  - **(b)** No declared setting outside `FOOTER_SETTING_KEYS` has `footer`, `signature`, `optout`/`opt_out` or `privacy` in its key or label.
  - **(c)** No route of `Dashboard().app` has `footer`, `signature`, `optout` or `privacy` in its path or name. The only POST that writes settings is `/settings`, and it can't clear a set required footer key (behavioural: `Setup.save` refuses with rule `required`).
  - **(d)** No module under `nextron/web` imports `nextron.db.repos.drafting` or calls `render_footer_template` (AST).
- **Docs.** Update `nextron-engine/README.md`:
  - the "Settings" section: the footer settings, rules, `FooterChanged` and the doctor;
  - the Database migrations table row;
  - a short "Footer" paragraph: render, completeness, phrases and snapshots.

  Also update the docstrings of `settings_schema`, `app/setup/api.py`, `queries/setup.py`, `repos/setup/markets.py`, `db/models/drafting.py` and `repos/drafting/__init__.py`.

**Never:**
- No email is sent or composed here. `compose`, `text_hash`, `draft_revisions` and `recompose_ready` are 4.12, and the gate is 6.9.
- No HTML, tracking or links other than the privacy URL in the footer. No price.
- No setting or control that hides, shortens, toggles or reorders footer lines. Templates stay seed-owned (3.1).
- Don't re-declare `us_solicitation_label`, don't change `render_footer_template`, and don't add a doctor check or a notification type.
- No change to `main.build_subscribers`.

### Decisions made overnight (pending user review)

1. **`sender_signature` is required and counts toward completeness,** like legal name and address. 3.1's review (R1) asked that `footer_complete` cover every required placeholder, so a blank signature also blocks sending. That is stricter than FR-23's list.
2. **Privacy-line wording:** "Your details came from <phrase>. Privacy notice: <url>". The phrases are the AC's.
   - Companies House uses source code `registry`, as Evidence and the Companies House story (`add_contact(source=registry)`) do.
   - An email finder uses `finder` (main's `ProvenanceSource.FINDER`).
   - Any other source (`pattern`, `replier`, `operator`, `eligibility_basis`) has no phrase, and a UK footer raises rather than state a false source. Drafting (4.15) must pass the Contact's own source record.
3. **`FooterChanged` per affected Market.** It fires only for Markets whose template uses a changed placeholder (a US label change doesn't touch UK). An unreadable template counts as affected.
4. **Two content guards beyond the AC (NFR-7, compliance):**
   - the opt-out line must hold the word "reply", because opt-out handling is reply-based;
   - no footer text may name a price.
5. **Snapshots reuse by (Market, SHA-256),** not by SHA-256 alone. The text is classed `redact`. Immutability comes from a repository with no update or delete, as 4.12's revisions will have; no trigger, since erasure would need to redact.
6. **Deferred to 4.12:** the Lead page Draft view's "Sending blocked: footer incomplete (<fields>)". Drafts and the Lead page aren't in this tree. `incomplete_notice` gives 4.12 the exact text.
7. **Seed sync doesn't publish `FooterChanged`** when a Market's `footer_template` changes. That is 3.1's frozen behaviour, so it is logged in `deferred-work.md` for 4.12.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| US complete | the seeded US template; all set, label blank | 4 lines: signature, legal name, address, opt-out; `footer_complete` true | N/A |
| US label | as above, label "Advertisement" | 5th line "Advertisement" | N/A |
| UK each source | the UK template; the 6 codes | 5th line "Your details came from <phrase>. Privacy notice: https://…" | N/A |
| UK URL blank | the UK template, URL "" | 5th line "Your details came from <phrase>."; missing `("privacy_notice_url",)` | N/A |
| Incomplete | legal name, address blank (US) | lines dropped; missing `("legal_name","postal_address")`; notice "Sending blocked: footer incomplete (Legal name, Postal address)" | N/A |
| US ignores URL | US, URL blank, rest set | complete | N/A |
| Unknown source | UK, `operator` or `None` | — | `ValueError`, no value shown |
| Bad URL | `http://…`, `https://user@x.com`, `ftp://x` | — | `InvalidSettingValue` rule `https_url` |
| Price | signature "Audit from $500" | — | rule `no_price` |
| Opt-out text | "Thanks" | — | rule `optout_reply` |
| Clear required | `legal_name` set, saved blank | nothing written | `InvalidSettings` rule `required` |
| Doctor | fresh DB | `required_settings` fail lists Sender signature, Legal name, Postal address, Privacy notice URL (after the two older keys) | N/A |
| Publish | save a changed `legal_name` with US and UK seeded | `FooterChanged` for both, code order (uk, us), same transaction | a failing subscriber rolls the save back |
| Publish label | change `us_solicitation_label` | only US | N/A |
| Publish URL | change `privacy_notice_url` | only UK | N/A |
| No publish | resave unchanged, or change `sender_timezone`, or no Markets | nothing published | N/A |
| Snapshot reuse | `ensure` the same text and Market twice | same id, one row; another Market or text → new row | blank text: `ValueError` |

</frozen-after-approval>

## Code Map

- **Settings:**
  - `nextron/domain/settings_schema.py`: the docstring, `Key` (133), `REQUIRED`, `Rule`, `max_length`, `SCHEMA` (the last entry is `US_SOLICITATION_LABEL` at about 1000);
  - imports: no cycle, because `company_facts` imports only `errors`, `ids` and `segments`.
- **Prices:** `nextron/domain/campaign.py:133-134` (`CURRENCY_SIGNS`, `CURRENCY_WORDS`), `_NOT_WORD` and `names_price` (370) move to `domain/prices.py`.
- **Links:** `nextron/domain/company_facts.py:279` `is_link`.
- **Markets:** `nextron/domain/markets.py`:
  - `FooterPlaceholder` (147);
  - `REQUIRED_PLACEHOLDERS` (160);
  - `render_footer_template` (335);
  - `placeholders(template)` (352);
  - `MarketDef.footer_template` / `.privacy_line_required`.
- **Setup:**
  - `nextron/app/setup/api.py`: the docstring (1-30), `Setup.set` (627) and `Setup.save` (638-684), which have `changed`, `deletes` and `current().transaction()`;
  - `current().publish(...)`: see `nextron/db/uow.py` and `tests/unit/test_uow.py:700-730` (`frozen(...)` registries).
- **Setup repository:** `nextron/db/repos/setup/markets.py` (`_list_value` and `all_markets` are the pattern for `footer_templates`).
- **Setup queries:** `nextron/app/queries/setup.py` (`get` at 85, the docstring list).
- **Notifications:** `nextron/domain/notifications.py:144` `FooterChanged(market_id)`.
- **Persistence:**
  - `db/alembic/versions/0012_4_2_campaigns.py` (pattern, FK naming);
  - `db/models/setup.py` (`CampaignRecord`: `Ulid`, `UtcDateTime`, `ulid_pk`, `new_id`) and `db/models/__init__.py` (import `drafting`, export the record);
  - `db/owners.py` (`TABLES`) and `db/pii_registry.py`;
  - `db/repos/drafting/__init__.py` (docstring) and `app/queries/drafting.py` (unchanged).
- **Doctor:** `nextron/startup_checks.py:555` `check_required_settings` (unchanged; it reads `queries.setup.required_unset`).
- **Tests to update** (forced by the new required keys and the migration):
  - `tests/unit/test_settings_schema.py` (key lists at about 505-534 and 968);
  - `tests/contracts/test_settings_contracts.py:132`;
  - `tests/unit/test_settings_page.py:525-560` (field order);
  - `tests/app/test_telegram_secrecy.py:185` (post every key);
  - `tests/app/test_setup_settings.py:336-367`;
  - `tests/unit/test_doctor.py` (`SETTINGS` at 78; 899);
  - `tests/contracts/test_persistence_contracts.py:72` `HEAD` and the probes at 681-693 (`0013`→`0014`, "revision 13"→"14");
  - `tests/unit/test_migrations.py:38`;
  - `tests/contracts/test_source_tree.py` `FILES`;
  - grep `us_solicitation_label=""` and `real_body(` for other full-form posts.
- **Contracts that apply:**
  - `test_domain_stdlib_only.py`;
  - `test_settings_contracts.py`, where the new keys and labels must pass the protected-stem and secret-word checks;
  - `test_persistence_contracts.py`;
  - `test_owner_contracts.py`;
  - import-linter c7: drafting's repository is imported only by `app.drafting`, `queries.drafting` and tests.

## Tasks & Acceptance

**Execution:**
- [x] `nextron-engine/nextron/domain/prices.py`, `domain/campaign.py` -- move the price check -- single definition.
- [x] `nextron-engine/nextron/domain/settings_schema.py` -- the five keys, three rules and docstring -- FR-23 settings.
- [x] `nextron-engine/nextron/domain/render.py` -- `FooterSettings`, constants, `privacy_line`, `footer`, `footer_complete`/`FooterCheck`, `incomplete_notice`, `footer_sha256` -- `domain.render` footer.
- [x] `nextron-engine/nextron/app/queries/setup.py` -- `footer_settings()` -- typed read.
- [x] `nextron-engine/nextron/db/repos/setup/markets.py`, `nextron/app/setup/api.py` -- `footer_templates()`; publish in `save`/`set` -- `FooterChanged`.
- [x] `nextron-engine/nextron/db/models/drafting.py`, `models/__init__.py`, `db/alembic/versions/0013_4_11_footer_snapshots.py`, `db/owners.py`, `db/pii_registry.py`, `db/repos/drafting/footer_snapshots.py`, `db/repos/drafting/__init__.py` -- the table and its repository.
- [x] `nextron-engine/tests/unit/test_render_footer.py` -- the matrix's render, completeness, phrase, notice and digest rows; the 3.1 seeded templates; a value holding `{legal_name}` that isn't re-scanned.
- [x] `nextron-engine/tests/app/test_footer_settings.py` -- the setting rows through `Setup.save`/`queries`; `footer_settings()` (unset, set, an unparsable stored text gives `""`); doctor's `required_unset`; the publish rows with a recording subscriber registry and seeded Markets (`seed sync` or the repository), including rollback on a failing subscriber and a corrupt template counting as affected; `footer_snapshots` reuse, `by_id`, constraints and the absence of update/delete.
- [x] `nextron-engine/tests/contracts/test_footer_contracts.py` -- NFR-7 (a)-(d) with probes.
- [ ] The forced test updates in the Code Map, `nextron-engine/README.md`, and `_bmad-output/implementation-artifacts/deferred-work.md` (decisions 6 and 7).

**Acceptance Criteria:**
- Given the full suite through the suite lock, and `uv run lint-imports`, when they run, then everything passes and every contract is kept.
- Given the models, when the persistence contracts run, then `footer_snapshots` is fully mapped (owner `drafting`) and classified, and the migration matches the model.
- Given the Settings page, when it renders, then the "Footer and booking" section shows `sender_signature`, `legal_name`, `postal_address`, `optout_line`, `privacy_notice_url` and `us_solicitation_label` in that order, each editable.

## Implementation Notes

**What was built**
- **Price check.** `nextron/domain/prices.py` is a new stdlib leaf holding `CURRENCY_SIGNS`, `CURRENCY_WORDS`, the word-break pattern and `names_price`, moved unchanged from `domain/campaign.py`. `campaign.py` imports them under the same names.
- **Settings** (`nextron/domain/settings_schema.py`):
  - `Key` and `SCHEMA` gain `sender_signature`, `legal_name`, `postal_address`, `optout_line` (default `DEFAULT_OPTOUT_LINE`) and `privacy_notice_url`, just before `us_solicitation_label`, with the spec's labels, limits and hints.
  - The rules are `NO_PRICE_RULE` (`no_price`), `OPTOUT_REPLY_RULE` (`optout_reply`: `reply` as a word) and `HTTPS_URL_RULE` (`https_url`: `company_facts.is_link` plus an `https` scheme).
  - `us_solicitation_label` gains `NO_PRICE_RULE` only.
- **`nextron/domain/render.py`.** Exactly the spec's surface: `FooterSettings`, `FOOTER_SETTING_KEYS`, `PLACEHOLDER_OF`, `PRIVACY_SOURCE_PHRASES`, `NO_PHRASE`, `privacy_line`, `footer`, `footer_complete`/`FooterCheck`, `incomplete_notice` and `footer_sha256`. It imports only `nextron.domain.markets` and `nextron.domain.settings_schema`.
- **Reads and publishing:**
  - `queries.setup.footer_settings()`;
  - `markets_repo.footer_templates()`;
  - `app/setup/api.py`'s `_publish_footer_changes`, called inside the transaction by `Setup.set` (when the effective text changes) and `Setup.save`.
- **`footer_snapshots`:**
  - migration `0013_4_11_footer_snapshots` (down `0012_4_2_campaigns`; `downgrade()` raises);
  - `db/models/drafting.py` (`FooterSnapshotRecord`), registered in `db/models/__init__.py`;
  - `owners.py` (`drafting`) and `pii_registry.py` (`text` `redact`, the rest `none`);
  - `db/repos/drafting/footer_snapshots.py` (`ensure`, `by_id`, `StoredFooterSnapshot`).
- **Tests:**
  - `tests/unit/test_render_footer.py`, `tests/app/test_footer_settings.py` and `tests/contracts/test_footer_contracts.py` cover every matrix row and NFR-7 (a)–(d), with probes.
  - The forced updates from the Code Map are made. `test_serve_process.py`'s `HEAD`, a Settings-page footer-section test and settings-declaration edge cases were added as well.
- **Docs:** README ("Settings": the footer settings, rules, `FooterChanged` and the doctor; a "The footer" subsection; the migrations row), the docstrings named in the spec, and `deferred-work.md` (decisions 6 and 7).

**Choices made inside the spec's latitude**
- `incomplete_notice` raises `ValueError` for a complete check, so an empty "()" notice can't appear.
- `FooterSettings` raises `TypeError` for a non-`str` value and offers `value_of(key)`.
- `footer_snapshots.ensure` also raises `ValueError` for a `market_id` that isn't a ULID. A ULID naming no Market fails on the foreign key (`IntegrityError`).
- `privacy_line` treats a whitespace-only URL as blank.
- **Contract (c)'s writer half** is an AST scan: under `nextron/web`, `.save`/`.set` on a receiver named `setup` may appear only in `settings_save`, which must be `POST /settings`. Its behavioural half is parametrised over the four required footer keys.
- **Contract (d)** flags any reference to `render_footer_template` (import, name or attribute) and any import of `nextron.db.repos.drafting`, with relative imports resolved.
- **Review patches (pass 1):**
  - **P1.** `Setup.save` leaves out of `FooterChanged` a deleted key whose stored text equalled `default_text` (`same_as_default`). The delete and the `settings_changed` log are unchanged. Tests: `test_deleting_a_stored_default_publishes_nothing`, and `test_resetting_a_custom_optout_line_to_its_default_publishes`, which still publishes.
  - **P2.** `_publish_footer_changes` treats a template that is `None`, or lacks any `REQUIRED_PLACEHOLDERS`, as affected. `[]` and `["hi"]` are added to `test_an_unreadable_or_incomplete_template_counts_as_using_every_placeholder`.
  - **P3.** `render.py` and the README say a line left blank after substitution is dropped (the seeded templates hold one placeholder per line), and `footer_complete` still names the key.
  - **P4.** One `FOOTER_WORDS` list (footer, signature, optout, opt_out, privacy, postal, legal, solicitation, unsubscribe) serves contracts (b) and (c). Route paths and names are normalised with `words()`. New probes: `show_postal_address`, `hide_legal_name`, the "US solicitation text" and "Unsubscribe words" labels, `/settings/opt-out`, `Postal_Address_save`, `/legal-name/clear` and `/unsubscribe/off`.
  - **P5.** The contract docstrings state exactly what is checked; (d) says rendering through `nextron.domain.render` isn't checked.
  - **P6.** The `prices.py` docstring now reads "every footer text but the privacy notice URL".

**Known limits (for review)**
- **Seed sync** publishes no `FooterChanged` on a template change (decision 7, deferred to 4.12).
- **A UK footer** raises for a Provenance source without a phrase (decision 2). 4.15 must pass the Contact's own source record.
- **Contract (a)** pins "Footer and booking" to exactly the six footer keys. The booking-link story must relax it (triage R8).
- **CRLF files.** Some files (`app/setup/api.py`, `README.md`, `test_setup_settings.py`, `test_doctor.py`) have CRLF endings in the working tree; they were kept as they were.

**Verification so far**
- After the patches, the spec's targeted set gave 1578 passed and 1 skipped (`ai_eval`).
- `uv run lint-imports`: 29 kept, 0 broken.

**Final verification (after review pass 1, P1–P6).** The full suite through `suite_lock.py` (`track-C`): the first run had 6267 passed, 2 skipped and 1 failed. The failure was only the known flaky `tests/unit/test_job_runner.py::test_a_second_start_raises_and_stop_is_idempotent`, because this tree predates main's fix. The one allowed re-run passed: 6268 passed, 2 skipped. `uv run lint-imports`: 29 kept, 0 broken, on both runs.

## Spec Change Log

## Review Triage Log

**Pass 1.** Three layers reviewed the diff since the baseline: the blind hunter (BH, 15 findings), the edge-case hunter (EC, 17) and the verification-gap reviewer (VG, 0 gaps plus 2 other findings). That is 34 raw findings, deduplicated into 21 groups:
- 6 patched (P1–P6);
- 1 deferred (D1, already logged by this story);
- 14 rejected (R1–R14).

| # | Finding(s) | Verdict | Evidence | Route |
|---|---|---|---|---|
| P1 | VG-O2, EC6, EC17, BH2: `Setup.save` publishes `FooterChanged` when it deletes a footer row whose text equals the default, so the effective footer doesn't change | low | **Real.** In the blank branch of `save`, any existing row goes into `changed`, and `_publish_footer_changes(changed)` publishes. `set(OPTOUT_LINE, DEFAULT_OPTOUT_LINE)`, or saving the default text over a custom one, stores the default; a later blank save deletes that row and publishes for every Market. The frozen rule is "a key is changed when its effective text changes", and the README says the same. Once 4.12's `recompose_ready` subscribes, this would make needless `footer_change` revisions. | patch: a delete counts toward footer changes only when the deleted text differs from `default_text(found)`; the `settings_changed` log is unchanged; add a test |
| P2 | BH1: `footer_templates()` decodes a stored `[]`, or a list without the footer placeholders such as `["hi"]`, to lines with no placeholders, so the Market is skipped | low | **Real.** `_template_lines` returns `None` only for a column that isn't a JSON list of `str`. `placeholders(())` is empty, so nothing is published, against the documented "a bad row is over-notified, never skipped". Such a row needs a hand edit, but the fix is one condition. | patch: in `_publish_footer_changes`, a template that lacks any `REQUIRED_PLACEHOLDERS` counts as affected, like `None`; add `"[]"` and `["hi"]` to the unreadable-template test |
| P3 | EC2, BH11: the docs say "a blank value drops its line", but a combined template line (`{legal_name}, {postal_address}`) with one blank value keeps `, 1 Example Street` | low | **Real for combined lines only.** `render_footer_template` (3.1, frozen) drops only a line that is blank after substitution. The seeded templates hold one placeholder per line, and `footer_complete` still reports the missing key, so sending stays blocked. The wording overstates it. | patch: in the `render.py` docstring and the README, say a line left blank after substitution is dropped (the seeded templates hold one placeholder per line) |
| P4 | EC11, EC12: NFR-7 contracts (b) and (c) match too few words. A setting `show_postal_address` or `hide_legal_name`, or a route `/settings/opt-out`, passes | low | **Real.** (b)'s words are `footer`, `signature`, `optout`/`opt_out` and `privacy`; (c) matches raw substrings, so the hyphenated `opt-out` isn't `optout`. The fix extends the test's word lists, a direct correction. | patch: add `postal`, `legal`, `solicitation` and `unsubscribe`; normalise route paths and names with `words()` so `opt-out` reads `opt_out`; add probes |
| P5 | EC13, EC15, EC16, BH10: the contract docstrings claim more than the checks do. (c)'s writer check matches only a receiver named `setup`; (d) bans only `render_footer_template` and `repos.drafting` (`from nextron.domain.render import footer` passes); the module says it "proves" no control can shorten the footer | low | **Real wording gap.** The checks are name- and AST-based by design. A web page may render a footer preview through `domain.render`, which doesn't remove anything. Tightening the AST checks would add complexity for contrived aliasing. | patch: narrow the module docstring and the (c) and (d) docstrings to exactly what is checked |
| P6 | BH5: the `prices.py` docstring says every footer setting is price-checked, but `privacy_notice_url` isn't | low | **Real doc inaccuracy.** `privacy_notice_url` has only `max_length` and `https_url`. `settings_schema` already says "every footer text but the URL". | patch: correct the `prices.py` docstring |
| D1 | EC7: a seed sync that changes a Market's `footer_template` or `privacy_line_required` publishes no `FooterChanged` | medium | **Real, and 3.1's frozen behaviour** ("a sync publishes no notification"). It matters once 4.12's `recompose_ready` exists. This is overnight decision 7, logged in `deferred-work.md` by this story. | defer (already logged; no second entry) |
| R1 | VG-O1, EC8: a stored `us_solicitation_label` that no longer parses (one saved with a currency sign before `no_price`, or hand-edited) reads `""`, so the US label line silently disappears while `footer_complete` says complete | low | **Real but unlikely.** No US email has been sent, the label is decided with counsel before the first one (OQ-8), and the Settings page marks a stored text that no longer parses (`stored_problems`). The fix changes the frozen `footer_complete` definition (the label is optional) and the `FooterSettings` shape. | rejected: it would change the approved spec |
| R2 | EC1, BH7: a UK Contact whose source is `operator`, `replier`, `pattern` or `eligibility_basis` (or `ats`) makes `footer()` raise, so it can't be drafted, and `footer_complete` can't see the source | low | **Spec behaviour:** overnight decision 2 chose to fail closed rather than state a false source (UK GDPR Art. 14). `footer_complete` takes no source by the AC's signature. Note for 4.15: pass the Contact's own source record (`website`, `csv`, `signal_inbox`, `referral`, `finder` or `registry`). | rejected: it would change the approved spec |
| R3 | EC3: the price check misses `300USD`, `EUR500`, `500 dollars`, `¥` and `₹` | low | **Real but unlikely.** A footer is Nextron's own signature, name, address and opt-out text, so a price there is improbable. `names_price` is 4.2's single definition, moved unchanged (spec); widening it changes Campaign-note behaviour too. | rejected |
| R4 | EC4, BH3: the opt-out rule accepts "Please do not reply" and "Sent from a no-reply address" | low | **Real but unlikely.** It needs the Operator to replace the default with a line that contradicts itself. The fix adds negation parsing (new branches). The default and the hint both say to reply. | rejected |
| R5 | EC5: a signature, legal name or address of `.` or `x` counts as complete | low | **Real but unlikely.** It needs deliberate garbage input, and the fix adds a new "meaningful text" rule with arbitrary thresholds. | rejected |
| R6 | EC9: `ensure()` with a well-formed ULID that names no Market raises `IntegrityError`, not `ValueError` | false | The docstring promises `ValueError` only for a blank text or a non-ULID id. The foreign key refuses an unknown Market loudly and the transaction rolls back. 4.12's callers pass a stored Market's id. | rejected |
| R7 | EC10, BH8: erasure would redact `footer_snapshots.text`, keep `sha256`, and `ensure()` would then reuse a `[erased]` row; the hash of a short footer is guessable | false | No erasure path writes `footer_snapshots`: erasure hooks are per Contact (AD-17), the table names no Contact, and the repository has no update. The hashed text is Nextron's own published footer, not a secret. | rejected |
| R8 | EC14, BH9: contract (a) requires "Footer and booking" to hold exactly the six footer keys, so the planned `booking_link` setting will fail it | low | **Spec behaviour:** the frozen Boundaries say "The set of `Section.FOOTER_AND_BOOKING` settings is exactly `FOOTER_SETTING_KEYS`". Note for the booking-link story: relax (a) to "`FOOTER_SETTING_KEYS` lead the section, in order" and update `test_render_footer.py`'s section test. | rejected: it would change the approved spec |
| R9 | BH4: no rule stops links or HTML in the non-URL footer settings | false | FR-4 and the PRD allow links in the signature ("no links except the signature"; the sales review recommends one website link there), and the footer counts as signature. Messages are plain text only (FR-23, 4.12's compose), so markup shows literally; there is no HTML part. | rejected |
| R10 | BH6: the `registry` phrase key isn't a `ProvenanceSource` member on `main` | false | The epics define `research.add_contact(source=registry)` for Companies House officers (epics.md, the Companies House officers story), and the spine's Evidence sources include `registry`. No path produces such a Contact yet, so the phrase is ready for it. Note for the merge: a later contract can assert the phrase keys are a subset of `ProvenanceSource` once `registry` is added. | rejected |
| R11 | BH12: price-check leftovers: `_NOT_WORD` declared again in `settings_schema`, unused `CURRENCY_*` re-exports in `campaign.py`, no direct `prices.py` test | low | **Cosmetic.** The spec requires `campaign.py` to keep the names. `settings_schema`'s pattern serves the reply rule. `names_price` is exercised through the Campaign-note tests and the `no_price` rule tests. | rejected |
| R12 | BH13: the footer test values are copied into five test files, `us, uk = SEEDED` depends on seed order, and a missing blank line | low | **Cosmetic.** The seed file's order is fixed and versioned, and nothing enforces formatting in this tree. | rejected |
| R13 | BH14: `ensure()` gives a "blank" message for a non-`str` text, and `ck_footer_snapshots_text` accepts whitespace-only text | low | **Unlikely.** The repository is the table's only writer and refuses both. The message never shows a value. | rejected |
| R14 | BH15: `footer_settings()` runs six queries, and a stored `optout_line` that no longer parses blocks sending but isn't listed by doctor | low | **Negligible.** Six local SQLite reads per call. An unparseable opt-out line fails closed and is named by `incomplete_notice` ("footer incomplete (Opt-out line)") and by the Settings page's stored-problem marker. | rejected |

## Design Notes

**Golden footers** (the seeded templates; settings "Furqaan, Nextron AI" / "Nextron AI Ltd" / "1 Example Street, London EC1A 1AA" / the default opt-out / `https://nextronai.com/privacy`):

```
UK, source website:                  US, label "Advertisement":
Furqaan, Nextron AI                  Furqaan, Nextron AI
Nextron AI Ltd                       Nextron AI Ltd
1 Example Street, London EC1A 1AA    1 Example Street, London EC1A 1AA
Not relevant? Reply 'no' and I won't contact you again.   (same)
Your details came from your firm's website. Privacy notice: https://nextronai.com/privacy
                                     Advertisement
```

**Why required settings rather than a new doctor check.** Spine §first-run lists "the required Settings (FR-23 footer, …)" under doctor's checks. Being `REQUIRED` gives the doctor and health-panel report for free, along with "can't be cleared once set" (NFR-7).

## Verification

**Commands** (from `nextron-engine/`):
- `uv run pytest -q tests/unit/test_render_footer.py tests/app/test_footer_settings.py tests/contracts tests/unit/test_settings_schema.py tests/unit/test_settings_page.py tests/app/test_setup_settings.py tests/unit/test_doctor.py tests/unit/test_migrations.py tests/app/test_telegram_secrecy.py` -- expected: all pass.
- `uv run lint-imports` -- expected: every contract kept.
- The full suite through `suite_lock.py` (`track-C`) -- the orchestrator only.
