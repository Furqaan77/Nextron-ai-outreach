"""Public command API of the ``setup`` service.

Other services and the delivery layers change ``setup`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back.

``Setup`` (AD-12) stores operator settings in the ``settings`` table, each as the canonical text
of a setting declared in ``nextron.domain.settings_schema``. Each method is a ``@use_case``
that reads the clock once and opens one ``uow.transaction()``.

- ``set(key, value)`` stores one typed value: an undeclared key raises ``UnknownSetting``, then
  ``encode`` raises ``InvalidSettingValue`` for a value it refuses; otherwise the canonical text
  is upserted with ``updated_at`` = now (the row keeps its ``id``).
- ``save(texts)`` stores what the Settings page submitted, all or nothing. An undeclared key
  raises ``UnknownSetting`` before anything is read. Each text is stripped, then:

  - blank, on a key with a default: the row is deleted, so the default applies;
  - blank, on a required key that isn't set: nothing changes (a setting can stay unset);
  - blank, on a required key that is set: a problem with rule ``required`` (it can't be
    cleared);
  - otherwise ``parse``; the canonical text is written only when it differs from the stored
    one, so an unchanged key keeps its ``updated_at``. A key without a row compares with its
    default text instead: the page shows the default, and sending it back unchanged leaves the
    key unset, so a default changed in a later version still reaches it.

  Any problem raises ``InvalidSettings`` (every refused key with its rule) before anything is
  written. It returns every stored text after the save, as ``{key: text}``.

**The Lead Score weights** (Story 2.15, Decision 1). ``set`` and ``save`` refuse a change that
leaves the effective ``lead_score_fit_weight`` and ``lead_score_signal_weight`` (the value
written, else the stored one, else the default; a deleted key's default) not summing to 100:
``InvalidSettings`` with rule ``weights_sum`` (``nextron.domain.scoring.WEIGHTS_SUM_RULE``) on
both keys, and nothing is stored. ``save`` checks it only when it submitted a weight key that no
other rule refused.

Both log ``settings_changed`` with ``keys``, the changed key names comma-joined, when at least
one key changed; never a value.

**``FooterChanged``** (FR-23, AD-3, Story 4.11). Inside the same transaction, after the writes,
``set`` and ``save`` publish one ``FooterChanged(market_id=<markets.id>)`` per stored Market, in
code order, whose footer template uses the placeholder (``nextron.domain.render.PLACEHOLDER_OF``)
of a footer setting (``FOOTER_SETTING_KEYS``) whose effective text changed: for ``save``, a key
in its changed list, a delete counting only when the deleted text isn't the default text; for
``set``, a canonical text that differs from the stored one, or from the default text when the
key has no row (``set`` still upserts as before). The templates come from
``markets_repo.footer_templates()``; a template that isn't a JSON list of lines, or that lacks
one of ``nextron.domain.markets.REQUIRED_PLACEHOLDERS``, counts as using every placeholder, so
a bad row is over-notified, never skipped. No Market, or no changed footer key, publishes
nothing. A subscriber that raises rolls the whole save back. Story 4.12 registers
``drafting.recompose_ready``; until then nothing subscribes. A seed sync that changes a Market's
template publishes nothing (Story 3.1).

``Setup.sync_segments(segments)`` (FR-1, AD-12, Story 2.1; ``nextron seed sync``) loads the
Segments of ``seed/segments.yaml``, as ``nextron.domain.segments.SegmentDef``s, into the
``segments`` table **only while it holds none** (Story 11.5: once a Segment exists the dashboard
owns them, AD-12). Before any transaction it refuses an item that isn't a ``SegmentDef``
(``TypeError``) or a repeated key (``ValueError``), and validates the definitions again as the
file form (``validate_segments``, when there is at least one), so a hand-built ``SegmentDef``
that breaks a rule raises ``InvalidSegments`` and nothing is stored. It reads the clock once,
then works in one transaction:

- **Bootstrap** (no stored Segment): each Segment, in input order, is inserted at version 1
  (with its ``segment_versions`` row, actor ``seed``) and one ``events`` row (entity
  ``segments``, ``None`` to ``v1``, cause ``seed_sync``, actor ``seed``).
- **Managed** (any stored Segment): nothing is written. The result lists the differences
  between the file and the database as ``SegmentDifference(key, only_in, paths)``: each file
  key with no row (``only_in`` = ``file``) and each file key whose stored definition (decoded
  with ``segment_from_text``, which raises ``InvalidSegments`` for a row that no longer
  validates) differs, with the changed field paths (``segment_form_diff`` from the stored
  definition to the file's), in input order; then each stored key the file leaves out
  (``only_in`` = ``dashboard``), by key.

A sync enqueues no job, publishes no notification and touches no other table, so it never
re-scores a Lead. It returns ``SegmentSync(created, changed, unchanged, not_in_file, managed,
differences)``: in bootstrap mode ``created`` holds ``(key, 1)`` in input order and the rest is
empty; in managed mode only ``managed`` (true) and ``differences`` are set. After the commit it
logs ``segments_synced`` (INFO) with the four counts: ``created_count``, ``changed_count``,
``unchanged_count`` and ``not_in_file_count`` (``created`` alone is a ``LogRecord`` attribute,
which ``extra`` may not overwrite), plus ``managed`` and ``differences_count``.

``Setup.sync_markets(markets)`` (FR-2, AD-12, Story 3.1) does the same for the Markets of
``seed/markets.yaml``, as ``nextron.domain.markets.MarketDef`` values, keyed by ``code`` into the
``markets`` table. Before any transaction it refuses an item that isn't a ``MarketDef``
(``TypeError``) or a repeated code (``ValueError``) and, when there is at least one, validates
the definitions again as the file form (``validate_markets`` on each ``market_form``), so a
hand-built ``MarketDef`` that breaks a rule raises ``InvalidMarkets``; the Markets it stores and
compares are the ones that validation returns. It reads the clock once, then works in one
transaction. Before any write it raises ``InvalidMarkets`` for the first input Market, in file
order, whose ``country_code`` or a ccTLD a stored Market the input leaves out still uses,
naming ``markets[<code>].country_code`` ("is also the country code of the stored Market
<code>, which is no longer in the file: keep that Market's code") or ``markets[<code>].cctlds``
("holds <tld>, a ccTLD of the stored Market <code>, which is no longer in the file: keep that
Market's code"). Then, in input order: a code with no row is inserted at version 1 with one
``events`` row (entity ``markets``, ``None`` to ``v1``, cause ``seed_sync``, actor ``seed``); a
row whose decoded ``MarketDef`` differs is updated to version n+1 with one ``events`` row
``v<n>`` to ``v<n+1>``; anything else writes nothing. A stored Market missing from the input is
never changed or deleted. Decoding a stored row that no longer validates raises
``InvalidMarkets`` (nothing is written). It returns ``MarketSync(created, changed, unchanged,
not_in_file)``, shaped like ``SegmentSync`` with codes, and after the commit logs
``markets_synced`` (INFO) with the same four ``*_count`` fields.

The Sequences of ``seed/sequences.yaml`` (FR-4, AD-12, Story 4.1), as
``nextron.domain.sequences.SequenceDef`` values, are synced only through ``seed_sync``, keyed by
``key`` into the ``sequences`` table with one ``step_briefs`` row per Step; there is no
standalone ``sync_sequences``. Before any transaction ``_sequence_inputs`` refuses an item that
isn't a ``SequenceDef`` (``TypeError``) or a repeated key (``ValueError``) and, when there is at
least one, validates the definitions again as the file form (``validate_sequences`` on each
``sequence_form``), then checks every angle against the Segments and Markets of the same call
(``check_sequence_references``: a Segment key, a Market code, and ABA Formal Opinion 512 only on
a US Market's angle and the SRA notice only on a GB Market's), raising ``InvalidSequences``; the
Sequences it stores and compares are the ones validation returns. Then, inside the transaction
(Story 11.7: once a Sequence exists the dashboard's Sequences page owns them, AD-12):

- **Bootstrap** (no stored Sequence): each Sequence, in input order, is inserted at version 1
  with its brief rows, its ``sequence_versions`` row (actor ``seed``) and one ``events`` row
  (entity ``sequences``, ``None`` to ``v1``, cause ``seed_sync``, actor ``seed``).
- **Managed** (any stored Sequence): nothing is written. The result lists the differences
  between the file and the database as ``SequenceDifference(key, only_in, paths)``: each file
  key with no row (``only_in`` = ``file``) and each file key whose stored Sequence differs, with
  the changed field paths (``sequence_form_diff`` from the stored Sequence to the file's), in
  input order; then each stored key the file leaves out (``only_in`` = ``dashboard``), by key.
  Decoding a stored Sequence that no longer validates raises ``InvalidSequences``.

The result is ``SequenceSync(created, changed, unchanged, not_in_file, managed,
differences)``: in bootstrap mode ``created`` holds ``(key, 1)`` in input order and the rest is
empty; in managed mode only ``managed`` (true) and ``differences`` are set.

The Offers of ``seed/offers.yaml`` (FR-3, Story 4.2), as ``nextron.domain.offers.OfferDef``
values, are synced only through ``seed_sync``, keyed by ``key`` into the ``offers`` table.
Before any transaction ``_offer_inputs`` refuses an item that isn't an ``OfferDef``
(``TypeError``) or a repeated key (``ValueError``) and, when there is at least one, validates the
definitions again as the file form (``validate_offers`` on each ``offer_form``), raising
``InvalidOffers``. Inside the transaction, while the database holds no Campaign, in input
order: a key with no row is inserted at version 1 with one ``events`` row (entity ``offers``,
``None`` to ``v1``, cause ``seed_sync``, actor ``seed``); an Offer whose decoded ``OfferDef``
differs is updated to version n+1 with one ``events`` row ``v<n>`` to ``v<n+1>``; anything else
writes nothing. A stored Offer the input leaves out is never changed or deleted. The result is
``OfferSync(created, changed, unchanged, not_in_file, managed, differences)``, shaped like
``SequenceSync``. **Managed** (Story 11.8: the database holds any Campaign, so the dashboard's
Campaigns page owns Campaigns, Variants and Offers): nothing is written, and ``differences``
lists ``OfferDifference(key, only_in, paths)`` like the Sequences' (``paths``: ``name`` and
``ladder`` where they differ).

The Campaigns of ``seed/campaigns.yaml`` (FR-3, AD-4, NFR-3, Story 4.2), as
``nextron.domain.campaign.CampaignDef`` values, are synced only through ``seed_sync``, keyed by
``key`` into the ``campaigns`` table with one ``variants`` row per Variant. Before any
transaction ``_campaign_inputs`` refuses an item that isn't a ``CampaignDef`` (``TypeError``) or
a repeated key (``ValueError``) and, when there is at least one, validates the definitions again
as the file form (``validate_campaigns`` on each ``campaign_form``), then runs
``check_campaign_references`` against this call's Segments, Markets (``{code: country_code}``),
Offers and Sequences (``{key: number of Steps}``), raising ``InvalidCampaigns``; in managed
mode (``campaigns_managed``, below) it checks the file's shape only, with no reference check.
Inside the transaction (Story 11.8: once a Campaign exists the dashboard's Campaigns page owns
the Campaigns, their Variants and the Offers, AD-12):

- **Bootstrap** (no stored Campaign): it maps keys and codes to ids through ``setup``'s own
  repositories (which see this transaction's rows), then inserts each Campaign, in input order,
  at ``draft`` with no LIA and its Variants, with one ``events`` row (entity ``campaigns``,
  ``None`` to ``v1``, cause ``seed_sync``, actor ``seed``).
- **Managed** (any stored Campaign): nothing is written. ``differences`` lists
  ``CampaignDifference(key, only_in, paths)`` like the Sequences', the paths from
  ``nextron.domain.campaign.campaign_form_diff`` (stored against file). Decoding a stored
  Campaign that no longer validates raises ``InvalidCampaigns``.

The seed never writes a Campaign's ``status``, ``lia_ref`` or ``lia_date``. The result is
``CampaignSync(created, changed, unchanged, not_in_file, managed, differences)``, shaped like
``SequenceSync``: in bootstrap mode only ``created``, in managed mode only ``managed`` (true)
and ``differences``.

**The seed sync** (Story 4.3 split it so the Approved Company Facts, owned by ``playbook``,
load in the same transaction; ``nextron.app.ops.api.Seeds.sync_seed`` is the use case
``nextron seed sync`` calls, and a unit of work runs one use case):

- ``check_seed(segments, markets, sequences, offers, campaigns, stored_segment_keys=None,
  stored_sequence_steps=None, campaigns_managed=False) -> CheckedSeed`` runs every check of the
  five syncs that needs
  no database (Segments, Markets, Sequences, whose angles are checked against the Segments and
  this call's Markets, Offers, then Campaigns, whose references are checked against the
  Segments, this call's Markets and Offers, and the Sequences), raising as described above.
  "The Segments" are this call's, or, when ``stored_segment_keys`` is given (Story 11.5: the
  database holds Segments, so the file's are ignored), those stored keys. "The Sequences" are
  this call's ``{key: number of Steps}``, or, when ``stored_sequence_steps`` is given (Story
  11.7: the database holds Sequences), the stored ones. ``campaigns_managed`` (Story 11.8: the
  database holds a Campaign) skips the Campaigns' reference checks, since the file is ignored.
  ``CheckedSeed`` holds what they return, with ``segment_keys``
  (the keys the references were checked against) and ``market_countries`` (``{code:
  country_code}`` of this sync's Markets) for the facts' checks.
- ``@command Setup.seed_sync(checked, now) -> SetupSeedSync(segments, markets, sequences,
  offers, campaigns)`` runs the five syncs in the caller's transaction, in that order, all at
  ``now``, so a failure anywhere changes nothing. The Offers and Campaigns are managed when
  ``checked.campaigns_managed`` is true or the database holds a Campaign, so Campaigns whose
  references weren't checked are never inserted.
- ``log_seed_sync(result)``, after the commit, logs ``segments_synced``, ``markets_synced``,
  ``sequences_synced``, ``offers_synced`` and then ``campaigns_synced``, each with the four
  ``*_count`` fields (``segments_synced``, ``sequences_synced``, ``offers_synced`` and
  ``campaigns_synced`` also with ``managed`` and ``differences_count``).

**The Operator's Campaign commands** (FR-3, AD-4, AD-5, NFR-3, NFR-7, Story 4.2).
``Campaigns(clock)``: each method is a ``@use_case`` that reads the clock once and runs one
transaction; every refusal is ``CampaignRefused`` and writes nothing. An unknown id is
``{"form": "No Campaign has this id"}``; an event the Campaign machine refuses from the stored
status is ``{"form": "This Campaign is <status>, so it can't be <activated|paused|resumed>"}``
(a stale page's control is refused, never repeated). "Today" is the date of now in the
``sender_timezone`` setting, or in UTC while it is unset or unreadable. A UK Campaign is one
whose Market's ``country_code`` is ``GB`` (read live with the Campaign).

- ``activate(campaign_id, lia_ref=None, lia_date=None) -> CampaignChanged``: for a UK Campaign
  the submitted fields (blank when ``None``) go through ``lia_from_fields`` with today; values
  that differ from the stored ones are stored (``set_lia``) with an LIA event first (``draft``
  to ``draft``, cause ``operator_record_lia``). Any other Campaign given a non-``None`` LIA
  argument is refused: "Only a UK Campaign records a legitimate-interests assessment". Then
  ``set_status`` and an event ``draft`` to ``active`` (cause ``operator_activate``).
- ``pause(campaign_id)`` and ``resume(campaign_id) -> CampaignChanged``: events ``active`` to
  ``paused`` (``operator_pause``) and ``paused`` to ``active`` (``operator_resume``). ``resume``
  of a UK Campaign also needs a stored LIA (``LIA_FIRST``). So does ``pause`` (and
  ``add_variant``) of a UK Campaign that left ``draft`` and has no LIA, which happens only when
  its Market's country became ``GB`` afterwards: the database triggers refuse any write to such
  a row, so the Operator records the LIA first (``record_lia``), and ``campaign_active`` is
  false meanwhile.
- ``record_lia(campaign_id, lia_ref, lia_date) -> LiaSaved(campaign_id, changed)``: only a UK
  Campaign, in any status, with the same validation; values equal to the stored ones are a
  no-op (``changed`` false, no event); otherwise ``set_lia`` and an event ``<status>`` to
  ``<status>`` (cause ``operator_record_lia``).
- ``add_variant(campaign_id, name, step_angles=(), based_on=None) -> VariantAdded``:
  ``variant_from_fields`` against the stored Sequence's Step count and the Market's country,
  every stored Variant's name (retired ones included) and the number of **active** ones (a third
  active Variant is ``{"form": TOO_MANY_VARIANTS}``); it inserts the Variant ``active``, gives
  the Campaign the next version and appends ``v<n>`` to ``v<n+1>`` (cause
  ``operator_add_variant``). ``based_on`` (Story 11.8's page) other than the stored version is
  ``{"form": STALE_CAMPAIGN}``.

Story 11.8 adds the Campaigns page's commands. "Enrolled" is read through
``nextron.app.queries.sequencing.enrollment_counts`` (every Enrollment status counts). A
``based_on`` other than the stored version is ``{"form": "This Campaign changed since you opened
it"}``, checked right after the Campaign is read. The references are row ids: a blank one is
``{"<field>": "Choose one"}`` and one that names no stored row ``{"<field>": "No stored
<Segment|Market|Offer|Sequence> has this id"}`` (fields ``segment``, ``market``, ``offer``,
``sequence``). Every field problem is collected into one refusal.

- ``activate`` and ``resume`` of a Campaign with no ``active`` Variant are ``{"form": "Add an
  active Variant first"}``, checked before the LIA.
- ``create_campaign(key, name, segment_id, market_id, offer_id, sequence_id) -> CampaignSaved``:
  the stripped key and name through ``campaign_fields_problems``, a stored key ``{"key": "A
  Campaign with this key already exists"}``, the references, then a (Segment, Market) pair a
  Campaign holds ``{"form": PAIR_TAKEN}``. It inserts the Campaign at ``draft``, version 1, with
  no Variant and no LIA, and appends ``None`` to ``v1`` (cause ``operator_create``).
- ``edit_campaign(campaign_id, based_on, name, segment_id, market_id, offer_id, sequence_id) ->
  CampaignSaved``: in order, the unknown id, the stale form, an LIA-less non-draft UK Campaign
  (``LIA_FIRST``, as below), the name and references, then an identical save (nothing written,
  ``changed`` false); a changed Segment or Market of a Campaign with any Enrollment
  (``{"form": PAIR_FROZEN}``), onto a pair another Campaign holds (``PAIR_TAKEN``), or of a
  non-draft Campaign onto a pair in a GB Market (``{"form": LIA_FIRST, "market":
  UK_PAIR_DRAFT_ONLY}``, even when only the Segment changed); a new Sequence with fewer Steps
  than an active Variant's note (``{"sequence": ...}``); a new Market whose country an active
  Variant's note breaks the floor for (``note_floor_problem``, ``{"market": ...}``). It updates
  the definition to version n+1 and appends ``v<n>`` to ``v<n+1>`` (cause ``operator_edit``). A
  Segment or Market change also clears the stored LIA, which was made for the old pair: a draft
  UK Campaign then asks for a new one at activation. The key never changes. Enrollments already
  bind their Offer, Sequence and Sequence version, so an Offer or Sequence change reaches new
  Enrollments only.
- ``edit_variant(campaign_id, variant_id, based_on, name, step_angles) -> VariantSaved``: a
  Variant this Campaign doesn't have is ``{"form": NO_SUCH_VARIANT}``, a retired one
  ``{"form": VARIANT_RETIRED}``, one with any Enrollment ``{"form": VARIANT_ENROLLED}``; then
  ``variant_from_fields`` against the other Variants' names and no cap. An identical save writes
  nothing; otherwise it updates the Variant and appends ``v<n>`` to ``v<n+1>`` (cause
  ``operator_edit_variant``).
- ``retire_variant(campaign_id, variant_id, based_on) -> VariantSaved``: an unknown Variant or a
  retired one is refused as above; the last active Variant of a Campaign that isn't ``draft`` is
  ``{"form": LAST_ACTIVE_VARIANT}``. It sets the Variant ``inactive`` (final) and appends
  ``v<n>`` to ``v<n+1>`` (cause ``operator_retire_variant``). Its Enrollments keep it.

Each of the three Variant commands and ``edit_campaign`` is refused by the LIA-gap rule first,
as ``add_variant`` is. Every event has entity ``campaigns`` and actor ``operator``, and holds no
value. After the commit they log ``campaign_activated``, ``campaign_paused``,
``campaign_resumed``, ``campaign_lia_saved`` (when an LIA was written), ``variant_added``,
``campaign_created``, ``campaign_edited``, ``variant_edited`` and ``variant_retired`` (the last
three only when something was written), each with the Campaign's ``key`` only (field
``campaign``).

``Credentials`` (AD-12, FR-56, NFR-18) stores the external credentials declared in
``nextron.domain.credentials`` in Windows Credential Manager through ``SecretsPort``. Its
methods are not ``@use_case``s: the credential methods touch no database and need no unit of
work, and the unsubscribe token key methods (below) only read, in the caller's unit of work. One
``threading.Lock`` runs them one at a time. Each credential has two keyring entries: its own
(``secret_name``), which holds the value, and ``meta/<secret_name>``, which holds only the
value's last-updated time (``encode_updated_at``). An undeclared key raises
``UnknownCredential`` before any keyring call.

- ``statuses()`` gives a ``CredentialStatus`` per declared credential, in registry order;
  ``status(key)`` gives one. The state is read from the credential's own entry: present is
  ``set``, absent is ``not set``, and the value is dropped at once (Credential Manager has no
  existence-only call). Only when it is ``set`` is ``updated_at`` decoded from the meta entry;
  a missing, garbled or unreadable meta entry leaves it ``None`` ("last update time unknown").
  An ``AdapterError`` reading the credential's own entry gives ``unknown``, with no time. Either
  read failure logs ``credential_read_failed`` (WARNING) with ``credential``, ``entry``
  (``value`` or ``meta``) and the error's ``code``.
- ``save(key, text)`` strips ``text``. A blank result raises ``InvalidCredentialValue`` with
  reason ``ENTER_A_VALUE``, and more than ``MAX_SECRET_LENGTH`` UTF-16 units raises it with
  reason ``SECRET_TOO_LONG``; either way nothing is written. Otherwise it writes the stripped
  value, then the meta entry (the secret first, so a failed meta write can only understate the
  time), logs ``credential_saved`` and returns the ``set`` status with the new time.
- ``remove(key)`` deletes the credential's entry, then its meta entry, logs
  ``credential_removed`` and returns the ``not set`` status.
- An ``AdapterError`` while saving or removing logs ``credential_store_failed`` (``credential``,
  ``operation`` = ``save`` or ``remove``, and the error's ``code``) and raises
  ``CredentialStoreFailed``; ``status(key)`` then shows what is stored.

**Unsubscribe token keys** (Story 3.8, AD-15): the per-kid ``k_tok`` and ``k_edge``
(``nextron.domain.credentials.UNSUB_KEYS``, keyring entries ``unsub/<key>/<kid>`` and
``meta/unsub/<key>/<kid>``). These methods read the database in the caller's unit of work (the
dashboard runs them inside ``units.open(read_only=True)``), always before any keyring call; a
read-only unit's ``BEGIN IMMEDIATE`` then serialises the keyring writes with token issuance, so
no token can be issued between the check and the write. An unknown key raises
``UnknownCredential`` and a kid outside 1 to 255 ``TypeError`` or ``ValueError``, before any
read.

- ``unsub_keys()`` reads the ``unsub_active_kid`` setting and ``newest_unsub_tokens()``, then
  the keyring. It returns an ``UnsubKidKeys(kid, role, newest_token_at, keys)`` for the active
  kid, the next one (active + 1, while it is 255 or less) and every kid with a token, by kid.
  ``role`` is ``active``, else ``in_use`` (a token under 2 years old, ``key_in_use``), else
  ``next`` (active + 1), else ``retired``. ``keys`` holds an ``UnsubKeyStatus(credential, kid,
  state, updated_at, valid)`` for ``k_tok`` and ``k_edge``, read as ``status`` reads one;
  ``valid`` says the stored value is a 32-byte base64url key (``parse_key``), and the value is
  dropped at once. ``unsub_key_status(kid, key)`` gives one, reading only the keyring.
- ``save_unsub_key(kid, key, text)`` strips ``text``: blank raises ``InvalidCredentialValue``
  with ``ENTER_A_VALUE``, and anything ``parse_key`` refuses with ``NOT_A_KEY``; nothing is
  read or written then. It reads ``newest_unsub_token(kid)``, then the stored value and the
  kid's other key: a value equal to the other key raises ``InvalidCredentialValue`` with
  ``SAME_KEY`` (Cloudflare holds ``k_edge``, so it must never be the decryption key). While
  ``key_in_use`` holds, replacing a valid stored key raises ``UnsubKeyInUse`` ("Key <kid>
  still has tokens under 2 years old"), because a new value breaks those tokens too. A key
  that isn't set, or isn't a valid key, can always be entered (one restored from escrow). It
  writes the canonical text (``encode_key``), then the meta entry, logs ``unsub_key_saved``
  (``credential``, ``kid``) and returns the ``set`` status.
- ``remove_unsub_key(kid, key)`` reads ``newest_unsub_token(kid)``; while ``key_in_use`` holds
  it raises ``UnsubKeyInUse`` and deletes nothing. Otherwise it deletes the entry, then its
  meta entry, logs ``unsub_key_removed`` and returns the ``not set`` status.
- A keyring failure while saving or removing logs ``unsub_key_store_failed`` (``credential``,
  ``kid``, ``operation`` and the error's ``code``) and raises ``CredentialStoreFailed`` (its
  ``key`` is the keyring entry's name); a failed read logs ``unsub_key_read_failed``.

Every log record names the credential's key, never its value.

``GoogleAccess`` (AD-12, AD-14, INT-2, INT-3, FR-56, NFR-18) holds the Google OAuth grants
(``nextron.domain.google_oauth``): the imported Desktop client and one refresh token per grant,
all in Windows Credential Manager through ``SecretsPort``. It touches no database. It reaches
Google through ``GoogleOAuthPort``, the browser through ``browser(url)`` (true when it opened),
the client file through ``ClientFilePort``, and builds one ``ConsentListener`` per flow with
``listen()``. ``allowlist()`` returns the ``sink_allowlist`` entries; ``start(job)`` runs a
flow's background job (a daemon thread by default).

**Two locks.** The state lock covers the in-memory flows, outcomes and cached access tokens and
is never held over I/O. The store lock serialises keyring changes to grants and may be held
over a revoke. When both are taken, the store lock comes first.

- ``client_status()``: whether ``google/client_config`` is set (``CredentialState``), with its
  import time from ``meta/google/client_config``.
- ``import_client(path_text)``: ``client_path``, then ``files.read(path,
  MAX_CLIENT_FILE_BYTES)`` (each file error code becomes an ``InvalidClientFile`` reason), then
  ``parse_client_file`` and ``encode_client_config`` (over 1,280 UTF-16 units:
  ``InvalidClientFile`` with ``SECRET_TOO_LONG``). It writes the client, then its time; an
  ``AdapterError`` there logs ``google_store_failed`` and raises
  ``CredentialStoreFailed("google_client")``, and the file stays. Then it drops every cached
  access token, deletes the file (a failure logs ``google_client_file_kept`` and gives
  ``file_deleted=False``), logs ``google_client_imported`` and returns ``ClientImport``.
- ``start_grant(grant)``: reads the client (absent or undecodable: ``ClientNotImported``; a
  keyring failure: ``CredentialStoreFailed``), cancels any waiting flow of the grant (marks it
  and closes its listener), opens a listener with ``listen()`` (any failure: outcome
  ``listener_failed``), makes ``state = token_urlsafe(32)`` and ``verifier =
  token_urlsafe(64)``, registers the flow as waiting until ``now + CONSENT_TIMEOUT`` (cancelling
  any flow a concurrent click registered meanwhile the same way), opens the
  consent URL with ``browser(url)`` (false or raising: outcome ``browser_failed``), logs
  ``google_consent_started`` and hands the flow's job to ``start``. It returns the grant's
  status, waiting. The job calls ``wait(300.0)``, closes the listener and completes the flow.
- **Completing a flow** never raises (anything unexpected is ``crashed``, logged as
  ``google_consent_crashed``). In order: no redirect is ``timed_out``; a ``state`` not given
  exactly once and equal to the flow's (``hmac.compare_digest``) is ``state_mismatch``; an
  ``error``, or not exactly one non-empty ``code``, is ``declined``; a failed ``exchange`` is
  ``exchange_failed``; no account from the ID token is ``no_account``; an unticked scope is
  ``scope_not_granted``; no refresh token is ``no_refresh_token``; then ``account_problem``
  (an allowlist provider that raises counts as an empty allowlist and logs
  ``google_allowlist_unreadable``). A flow that was superseded meanwhile stops there with no
  outcome and stores nothing. Otherwise the refresh token is stored, then the ``GrantMeta``
  (a failure restores what was there, best effort, and is ``store_failed``), the replaced
  token is revoked (below), and the access token is cached in memory with its expiry. Every
  refusal or failure from ``no_account`` on revokes the new refresh token (the access token
  when there is none) and stores nothing. Only the current flow of a grant records its
  outcome (``ConsentOutcome``); a failure logs ``google_consent_failed``.
- **Revoking.** A token is revoked only when no stored grant (its token present and its meta
  naming the same account, ``same_account``) holds the same account, because Google's revoke
  ends the client's whole grant for that account. Otherwise it is only discarded:
  ``Revocation.SHARED`` (``google_revoke_skipped``). A revoke error (or a keyring failure
  while checking) is ``FAILED`` (``google_revoke_failed``); a done revoke is ``REVOKED``
  (``google_token_revoked``). An unknown account counts as held by no grant.
- ``remove_grant(grant)``: cancels a waiting flow, revokes the stored token (unless another
  grant holds its account), deletes the token and then its meta, even when the revoke failed,
  drops the cached access token and the last outcome, logs ``google_grant_removed`` and returns
  ``GrantRemoval``. A keyring failure logs ``google_store_failed`` and raises
  ``CredentialStoreFailed``.
- ``access_token(grant)``: the cached access token while it is valid for more than 60 s more.
  Otherwise, every error an ``AdapterError`` that isn't retryable: no client is
  ``google_client_not_imported``, no token ``google_not_granted``, and a meta that already says
  "needs re-consent" ``google_needs_reconsent``, with no call to Google. ``refresh`` failing
  with ``google_invalid_grant`` writes ``needs_reconsent_at`` into the meta (only while the
  stored token is still the one that failed), logs ``google_grant_needs_reconsent`` and raises
  ``google_needs_reconsent``; other port errors propagate as they are. A new access token is
  cached (only while its refresh token is still stored) and never written to the keyring.
- ``check_stored_grant(grant) -> None``: raises exactly as ``access_token``'s checks do before
  any refresh (the same helper): client not imported, not granted, needs re-consent, or a
  Credential Manager ``AdapterError``. It never calls Google, so it refreshes, records and
  marks nothing; the health panel's readiness list uses it (``nextron doctor`` refreshes).
- ``grant_statuses()`` and ``grant_status(grant)`` give ``GrantStatus``: ``GrantState`` (``not
  granted``, ``granted``, ``needs re-consent``, or ``unknown`` when the keyring can't be read),
  the account and times from the meta, ``waiting_until`` while a flow waits, and the last
  outcome.

Its log records carry only ``grant``, ``reason`` and ``code``: never a token, the client
secret, a code, the verifier, the state, a URL, a path or an account address.

**Erasure (Story 3.13).** ``ERASURE`` declares ``settings`` ``not_held``: it holds Nextron's own
settings, never a Contact's data. The module-level ``@command erase_pii(n: ContactErased)`` is
setup's ``ContactErased`` subscriber (``nextron.main.build_subscribers``); it writes nothing.
"""

import functools
import hmac
import json
import logging
import threading
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta, tzinfo
from enum import StrEnum
from secrets import token_urlsafe
from types import MappingProxyType
from typing import Final, NoReturn
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nextron.app.queries import compliance as compliance_queries
from nextron.app.queries import sequencing as sequencing_queries
from nextron.app.queries import setup as setup_queries
from nextron.db import eventlog
from nextron.db.repos.setup import campaigns as campaigns_repo
from nextron.db.repos.setup import markets as markets_repo
from nextron.db.repos.setup import offers as offers_repo
from nextron.db.repos.setup import segments as segments_repo
from nextron.db.repos.setup import sequences as sequences_repo
from nextron.db.repos.setup import settings as settings_repo
from nextron.db.uow import command, current, use_case
from nextron.domain import credentials as credential_registry
from nextron.domain import settings_schema
from nextron.domain.campaign import (
    CAMPAIGN_CREATED_CAUSE,
    CAMPAIGN_EDITED_CAUSE,
    CHOOSE_ONE,
    FORM_FIELD,
    KEY_FIELD,
    KEY_TAKEN,
    LAST_ACTIVE_VARIANT,
    LIA_FIRST,
    LIA_RECORDED_CAUSE,
    MARKET_FIELD,
    NEEDS_VARIANT_FIRST,
    NO_SUCH_VARIANT,
    OFFER_FIELD,
    PAIR_FROZEN,
    PAIR_TAKEN,
    SEGMENT_FIELD,
    SEQUENCE_FIELD,
    STALE_CAMPAIGN,
    VARIANT_ADDED_CAUSE,
    VARIANT_EDITED_CAUSE,
    VARIANT_ENROLLED,
    VARIANT_RETIRED,
    VARIANT_RETIRED_CAUSE,
    CampaignCause,
    CampaignDef,
    CampaignEvent,
    CampaignRefused,
    CampaignStatus,
    InvalidCampaigns,
    StepAngleNote,
    VariantStatus,
    campaign_fields_problems,
    campaign_form,
    campaign_form_diff,
    check_campaign_references,
    lia_from_fields,
    needs_lia,
    note_floor_problem,
    transition,
    validate_campaigns,
    variant_from_fields,
)
from nextron.domain.credentials import (
    Credential,
    InvalidCredentialValue,
    UnsubKeyCredential,
    decode_updated_at,
    declared_unsub_keys,
    encode_updated_at,
    unsub_key,
)
from nextron.domain.erasure import Coverage, TableErasure
from nextron.domain.ids import is_ulid
from nextron.domain.google_oauth import (
    CLIENT_META_NAME,
    CLIENT_SECRET_NAME,
    CONSENT_TIMEOUT,
    FILE_MISSING,
    FILE_NOT_REGULAR,
    FILE_TOO_LARGE,
    FILE_UNREADABLE,
    GRANTS,
    MAX_CLIENT_FILE_BYTES,
    ClientConfig,
    ClientNotImported,
    ConsentFailure,
    GoogleGrant,
    GrantMeta,
    GrantSpec,
    InvalidClientFile,
    account_problem,
    authorization_url,
    client_path,
    code_challenge,
    decode_client_config,
    decode_grant_meta,
    encode_client_config,
    encode_grant_meta,
    grant_spec,
    id_token_account,
    parse_client_file,
    same_account,
    scope_granted,
)
from nextron.domain.machines import IllegalTransition, Transition
from nextron.domain.markets import (
    REQUIRED_PLACEHOLDERS,
    InvalidMarkets,
    MarketDef,
    market_form,
    placeholders,
    validate_markets,
)
from nextron.domain.notifications import ContactErased, FooterChanged
from nextron.domain.offers import OfferDef, offer_form, validate_offers
from nextron.domain.render import FOOTER_SETTING_KEYS, PLACEHOLDER_OF
from nextron.domain.scoring import WEIGHTS_SUM_REASON, WEIGHTS_SUM_RULE, weights_sum_ok
from nextron.domain.segments import (
    SegmentDef,
    definition_text,
    segment_form_diff,
    segment_from_text,
    validate_segments,
)
from nextron.domain.sequences import (
    SequenceDef,
    check_sequence_references,
    sequence_form,
    sequence_form_diff,
    validate_sequences,
)
from nextron.domain.settings_schema import (
    REQUIRED,
    REQUIRED_RULE,
    InvalidSettings,
    InvalidSettingValue,
    Key,
    SettingNotSet,
)
from nextron.domain.sink import sink_applies
from nextron.domain.unsub_token import (
    MAX_KID,
    UnsubKeyInUse,
    check_kid,
    encode_key,
    key_in_use,
    parse_key,
)
from nextron.ports.clock import Clock
from nextron.ports.errors import AdapterError
from nextron.ports.google_oauth import (
    CLIENT_FILE_MISSING,
    CLIENT_FILE_NOT_REGULAR,
    CLIENT_FILE_TOO_LARGE,
    GOOGLE_CLIENT_NOT_IMPORTED,
    GOOGLE_INVALID_GRANT,
    GOOGLE_NEEDS_RECONSENT,
    GOOGLE_NOT_GRANTED,
    ClientFilePort,
    ConsentListener,
    GoogleOAuthPort,
    Query,
    TokenSet,
)
from nextron.ports.secrets import (
    MAX_SECRET_LENGTH,
    SECRET_TOO_LONG,
    SecretsPort,
    read_unsub_key,
    secret_length,
)

SETTINGS_CHANGED: Final = "settings_changed"
CANNOT_CLEAR: Final = "is required and can't be cleared once set"
ENTER_A_VALUE: Final = "enter a value"
NOT_A_KEY: Final = (
    "enter a 32-byte key in base64url: 43 characters of A-Z, a-z, 0-9, - and _ (or 44 ending "
    "in =)"
)
SAME_KEY: Final = "k_tok and k_edge of a key id must differ"
CREDENTIAL_SAVED: Final = "credential_saved"
CREDENTIAL_REMOVED: Final = "credential_removed"
CREDENTIAL_STORE_FAILED: Final = "credential_store_failed"
CREDENTIAL_READ_FAILED: Final = "credential_read_failed"
UNSUB_KEY_SAVED: Final = "unsub_key_saved"
UNSUB_KEY_REMOVED: Final = "unsub_key_removed"
UNSUB_KEY_STORE_FAILED: Final = "unsub_key_store_failed"
UNSUB_KEY_READ_FAILED: Final = "unsub_key_read_failed"
SEGMENTS_SYNCED: Final = "segments_synced"
SEGMENTS_ENTITY: Final = "segments"
SEGMENT_ONLY_IN_FILE: Final = "file"  # SegmentDifference.only_in
SEGMENT_ONLY_IN_DASHBOARD: Final = "dashboard"
MARKETS_SYNCED: Final = "markets_synced"
MARKETS_ENTITY: Final = "markets"
SEQUENCES_SYNCED: Final = "sequences_synced"
SEQUENCES_ENTITY: Final = "sequences"
OFFERS_SYNCED: Final = "offers_synced"
OFFERS_ENTITY: Final = "offers"
CAMPAIGNS_SYNCED: Final = "campaigns_synced"
CAMPAIGNS_ENTITY: Final = "campaigns"
SEED_SYNC_CAUSE: Final = "seed_sync"
SEED_ACTOR: Final = "seed"
OPERATOR_ACTOR: Final = "operator"
CAMPAIGN_ACTIVATED: Final = "campaign_activated"
CAMPAIGN_PAUSED: Final = "campaign_paused"
CAMPAIGN_RESUMED: Final = "campaign_resumed"
CAMPAIGN_LIA_SAVED: Final = "campaign_lia_saved"
VARIANT_ADDED: Final = "variant_added"
CAMPAIGN_CREATED: Final = "campaign_created"
CAMPAIGN_EDITED: Final = "campaign_edited"
VARIANT_EDITED: Final = "variant_edited"
VARIANT_RETIRED_LOG: Final = "variant_retired"
# Story 11.8: a reference row id that names no stored row, by form field.
NO_SUCH_REFERENCE: Final = {
    SEGMENT_FIELD: "No stored Segment has this id",
    MARKET_FIELD: "No stored Market has this id",
    OFFER_FIELD: "No stored Offer has this id",
    SEQUENCE_FIELD: "No stored Sequence has this id",
}
CAMPAIGN_LOG_FIELD: Final = "campaign"  # the Campaign's key, the only field its logs carry
NO_SUCH_CAMPAIGN: Final = "No Campaign has this id"
# Story 11.8: a non-draft Campaign moving to a new pair in a GB Market (the market field's reason).
UK_PAIR_DRAFT_ONLY: Final = (
    "Only a draft Campaign can move to a new UK Segment or Market: its assessment must be made "
    "for the new pair"
)
ONLY_UK_LIA: Final = "Only a UK Campaign records a legitimate-interests assessment"
CAMPAIGN_CHANGED: Final = "This Campaign changed meanwhile; reload the page and try again"
# How a refusal names each event: "This Campaign is <status>, so it can't be <verb>".
EVENT_VERBS: Final = {
    CampaignEvent.ACTIVATE: "activated",
    CampaignEvent.PAUSE: "paused",
    CampaignEvent.RESUME: "resumed",
}
SAVE: Final = "save"
REMOVE: Final = "remove"
VALUE_ENTRY: Final = "value"  # the credential's own keyring entry
META_ENTRY: Final = "meta"  # its meta/<name> entry

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SegmentDifference:
    """How the seed file and the database differ on one Segment key (Story 11.5): ``only_in``
    is ``file`` or ``dashboard`` for a key only one side has, else ``None``, and then ``paths``
    are the changed fields of a key both have."""

    key: str
    only_in: str | None
    paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SegmentSync:
    """What ``sync_segments`` did: ``created`` as ``(key, version)``, ``changed`` as ``(key,
    from_version, to_version)`` and ``unchanged`` keys, each in input order, and
    ``not_in_file`` as ``(key, version)`` of each stored Segment the input left out, by key.
    Since Story 11.5 only a bootstrap writes (``created``); ``managed`` says the database
    already held Segments, so nothing was written and ``differences`` lists how the file
    differs."""

    created: tuple[tuple[str, int], ...]
    changed: tuple[tuple[str, int, int], ...]
    unchanged: tuple[str, ...]
    not_in_file: tuple[tuple[str, int], ...]
    managed: bool = False
    differences: tuple[SegmentDifference, ...] = ()


@dataclass(frozen=True, slots=True)
class MarketSync:
    """What ``sync_markets`` did: ``created`` as ``(code, version)``, ``changed`` as ``(code,
    from_version, to_version)`` and ``unchanged`` codes, each in input order, and
    ``not_in_file`` as ``(code, version)`` of each stored Market the input left out, by code."""

    created: tuple[tuple[str, int], ...]
    changed: tuple[tuple[str, int, int], ...]
    unchanged: tuple[str, ...]
    not_in_file: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class SequenceDifference:
    """How the seed file and the database differ on one Sequence key (Story 11.7), shaped like
    ``SegmentDifference``."""

    key: str
    only_in: str | None
    paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SequenceSync:
    """What ``seed_sync`` did to the Sequences: ``created`` as ``(key, version)``, ``changed``
    as ``(key, from_version, to_version)`` and ``unchanged`` keys, each in input order, and
    ``not_in_file`` as ``(key, version)`` of each stored Sequence the input left out, by key.
    Since Story 11.7 only a bootstrap writes (``created``); ``managed`` says the database
    already held Sequences, so nothing was written and ``differences`` lists how the file
    differs."""

    created: tuple[tuple[str, int], ...]
    changed: tuple[tuple[str, int, int], ...]
    unchanged: tuple[str, ...]
    not_in_file: tuple[tuple[str, int], ...]
    managed: bool = False
    differences: tuple[SequenceDifference, ...] = ()


@dataclass(frozen=True, slots=True)
class OfferDifference:
    """How the seed file and the database differ on one Offer key (Story 11.8), shaped like
    ``SegmentDifference``."""

    key: str
    only_in: str | None
    paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OfferSync:
    """What ``seed_sync`` did to the Offers: ``created`` as ``(key, version)``, ``changed`` as
    ``(key, from_version, to_version)`` and ``unchanged`` keys, each in input order, and
    ``not_in_file`` as ``(key, version)`` of each stored Offer the input left out, by key.
    ``managed`` (Story 11.8) says the database held a Campaign, so nothing was written and
    ``differences`` lists how the file differs."""

    created: tuple[tuple[str, int], ...]
    changed: tuple[tuple[str, int, int], ...]
    unchanged: tuple[str, ...]
    not_in_file: tuple[tuple[str, int], ...]
    managed: bool = False
    differences: tuple[OfferDifference, ...] = ()


@dataclass(frozen=True, slots=True)
class CampaignDifference:
    """How the seed file and the database differ on one Campaign key (Story 11.8), shaped like
    ``SegmentDifference``."""

    key: str
    only_in: str | None
    paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CampaignSync:
    """What ``seed_sync`` did to the Campaigns: ``created`` as ``(key, version)`` in input
    order. The other counts stay for the summary's shape: since Story 11.8 only a bootstrap
    writes, and ``managed`` says the database already held a Campaign, so nothing was written
    and ``differences`` lists how the file differs."""

    created: tuple[tuple[str, int], ...]
    changed: tuple[tuple[str, int, int], ...]
    unchanged: tuple[str, ...]
    not_in_file: tuple[tuple[str, int], ...]
    managed: bool = False
    differences: tuple[CampaignDifference, ...] = ()


@dataclass(frozen=True, slots=True)
class SetupSeedSync:
    """What ``seed_sync`` did: the Segments' sync, then the Markets', the Sequences', the
    Offers' and the Campaigns'."""

    segments: SegmentSync
    markets: MarketSync
    sequences: SequenceSync
    offers: OfferSync
    campaigns: CampaignSync


@dataclass(frozen=True, slots=True)
class CheckedSeed:
    """The seed's Segments (with their definition texts), Markets, Sequences, Offers and
    Campaigns, checked before any transaction (``check_seed``)."""

    segments: tuple[SegmentDef, ...]
    segment_texts: tuple[str, ...]
    markets: tuple[MarketDef, ...]
    sequences: tuple[SequenceDef, ...]
    offers: tuple[OfferDef, ...]
    campaigns: tuple[CampaignDef, ...]
    stored_segment_keys: frozenset[str] | None = None
    stored_sequence_steps: Mapping[str, int] | None = None
    campaigns_managed: bool = False

    @property
    def segment_keys(self) -> frozenset[str]:
        """The Segment keys the references were checked against: the stored ones when the
        database holds Segments (Story 11.5), else this sync's."""
        if self.stored_segment_keys is not None:
            return self.stored_segment_keys
        return frozenset(segment.key for segment in self.segments)

    @property
    def market_countries(self) -> dict[str, str]:
        """This sync's Markets, ``{code: country_code}``."""
        return {market.code: market.country_code for market in self.markets}


def check_seed(
    segments: Sequence[SegmentDef],
    markets: Sequence[MarketDef],
    sequences: Sequence[SequenceDef],
    offers: Sequence[OfferDef],
    campaigns: Sequence[CampaignDef],
    stored_segment_keys: Collection[str] | None = None,
    stored_sequence_steps: Mapping[str, int] | None = None,
    campaigns_managed: bool = False,
) -> CheckedSeed:
    """Every check of the seed's five syncs that needs no database; see the module
    docstring."""
    chosen_segments, texts = _segment_inputs(segments)
    chosen_markets = _market_inputs(markets)
    stored_keys = None if stored_segment_keys is None else frozenset(stored_segment_keys)
    segment_keys = (
        frozenset(segment.key for segment in chosen_segments)
        if stored_keys is None
        else stored_keys
    )
    market_countries = {market.code: market.country_code for market in chosen_markets}
    chosen_sequences = _sequence_inputs(sequences, segment_keys, market_countries)
    chosen_offers = _offer_inputs(offers)
    stored_steps = None if stored_sequence_steps is None else dict(stored_sequence_steps)
    chosen_campaigns = _campaign_inputs(
        campaigns,
        segment_keys,
        market_countries,
        frozenset(offer.key for offer in chosen_offers),
        (
            {sequence.key: len(sequence.steps) for sequence in chosen_sequences}
            if stored_steps is None
            else stored_steps
        ),
        check_references=not campaigns_managed,
    )
    return CheckedSeed(
        chosen_segments,
        texts,
        chosen_markets,
        chosen_sequences,
        chosen_offers,
        chosen_campaigns,
        stored_keys,
        stored_steps,
        bool(campaigns_managed),
    )


def log_seed_sync(result: SetupSeedSync) -> None:
    """Log ``segments_synced``, ``markets_synced``, ``sequences_synced``, ``offers_synced`` and
    ``campaigns_synced`` with their counts; ``segments_synced`` also carries ``managed`` and
    ``differences_count`` (Story 11.5), so a managed sync is told apart from a no-op, and so
    do ``sequences_synced`` (Story 11.7), ``offers_synced`` and ``campaigns_synced`` (Story
    11.8)."""
    _log_segment_sync(result.segments)
    _log_sync(MARKETS_SYNCED, result.markets)
    _log_sync(
        SEQUENCES_SYNCED,
        result.sequences,
        managed=result.sequences.managed,
        differences_count=len(result.sequences.differences),
    )
    _log_sync(
        OFFERS_SYNCED,
        result.offers,
        managed=result.offers.managed,
        differences_count=len(result.offers.differences),
    )
    _log_sync(
        CAMPAIGNS_SYNCED,
        result.campaigns,
        managed=result.campaigns.managed,
        differences_count=len(result.campaigns.differences),
    )


def _log_changed(keys: list[str]) -> None:
    if keys:
        log.info(SETTINGS_CHANGED, extra={"keys": ",".join(keys)})


def _publish_footer_changes(changed: Iterable[str]) -> None:
    """In the running transaction, publish ``FooterChanged`` for each stored Market, by code,
    whose footer template uses the placeholder of a changed footer key (Story 4.11). A template
    that isn't a JSON list of lines, or that lacks a ``REQUIRED_PLACEHOLDERS`` placeholder,
    counts as using every placeholder."""
    used = {PLACEHOLDER_OF[key].value for key in changed if key in FOOTER_SETTING_KEYS}
    if not used:
        return
    uow = current()
    for market_id, template in markets_repo.footer_templates():
        found = None if template is None else placeholders(template)
        bad = found is None or any(need.value not in found for need in REQUIRED_PLACEHOLDERS)
        if bad or not used.isdisjoint(found or ()):
            uow.publish(FooterChanged(market_id=market_id))


WEIGHT_KEYS: Final = (str(Key.LEAD_SCORE_FIT_WEIGHT), str(Key.LEAD_SCORE_SIGNAL_WEIGHT))


def _weight(name: str, text: str | None) -> int:
    """The weight ``text`` holds, or the default when it is ``None`` or no longer parses."""
    found = settings_schema.setting(name)
    if text is not None:
        try:
            value = settings_schema.parse(found, text)
        except InvalidSettingValue:
            pass
        else:
            if isinstance(value, int):
                return value
    default = found.default
    return default if isinstance(default, int) else 0


def _weights_problems(texts: Mapping[str, str | None]) -> dict[str, InvalidSettingValue]:
    """The ``weights_sum`` refusals of both weight keys when their effective texts (``None``:
    the default) don't sum to 100; ``{}`` when they do."""
    fit, signal = (_weight(name, texts.get(name)) for name in WEIGHT_KEYS)
    if weights_sum_ok(fit, signal):
        return {}
    refusal = {name: (name, WEIGHTS_SUM_RULE, WEIGHTS_SUM_REASON) for name in WEIGHT_KEYS}
    return {name: InvalidSettingValue(*args) for name, args in refusal.items()}


class Setup:
    """Operator settings, one typed value at a time or a whole Settings page at once, and the
    Segments, Markets, Sequences, Offers and Campaigns a seed sync loads."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    @use_case
    def set(self, key: str, value: object) -> None:
        """Store ``value`` for the declared setting ``key``."""
        now = self._clock.now()
        found = settings_schema.setting(key)
        text = settings_schema.encode(found, value)
        name = str(found.key)
        with current().transaction():
            if name in WEIGHT_KEYS:
                weights = {key: settings_repo.stored_text(key) for key in WEIGHT_KEYS}
                weights[name] = text
                problems = _weights_problems(weights)
                if problems:
                    raise InvalidSettings(problems)
            before = settings_repo.stored_text(name)
            # an unset key compares with its default text (None for a required one)
            effective = before if before is not None else settings_schema.default_text(found)
            settings_repo.upsert(name, text, now)
            if text != effective:
                _publish_footer_changes([name])
        _log_changed([name])

    @use_case
    def save(self, texts: Mapping[str, str]) -> dict[str, str]:
        """Store the submitted ``texts`` (``{key: text}``, any subset of the declared keys),
        all or nothing; return every stored text afterwards."""
        now = self._clock.now()
        chosen = {key: settings_schema.setting(key) for key in texts}
        with current().transaction():
            stored = settings_repo.stored_texts()
            problems: dict[str, InvalidSettingValue] = {}
            writes: dict[str, str] = {}
            deletes: list[str] = []
            changed: list[str] = []  # in submission order
            same_as_default: set[str] = set()  # deleted, but the stored text was the default
            for key, raw in texts.items():
                found = chosen[key]
                name = str(found.key)
                if not isinstance(raw, str):
                    problems[name] = InvalidSettingValue(name, found.type, "must be text")
                    continue
                text = raw.strip()
                before = stored.get(name)
                if not text:
                    if found.default is not REQUIRED:
                        if before is not None:
                            deletes.append(name)
                            changed.append(name)
                            if before == settings_schema.default_text(found):
                                same_as_default.add(name)
                    elif before is not None:
                        problems[name] = InvalidSettingValue(name, REQUIRED_RULE, CANNOT_CLEAR)
                    continue
                try:
                    canonical = settings_schema.encode(found, settings_schema.parse(found, text))
                except InvalidSettingValue as refused:
                    problems[name] = refused
                    continue
                # an unset key compares with its default text, so an unchanged default stays unset
                unchanged = before if before is not None else settings_schema.default_text(found)
                if canonical != unchanged:
                    writes[name] = canonical
                    changed.append(name)
            submitted = {str(found.key) for found in chosen.values()}
            if submitted & set(WEIGHT_KEYS) and not set(problems) & set(WEIGHT_KEYS):
                weights: dict[str, str | None] = {}
                for name in WEIGHT_KEYS:
                    weights[name] = None if name in deletes else writes.get(name, stored.get(name))
                problems.update(_weights_problems(weights))
            if problems:
                raise InvalidSettings(problems)
            for name in deletes:
                settings_repo.delete(name)
            for name, canonical in writes.items():
                settings_repo.upsert(name, canonical, now)
            _publish_footer_changes([name for name in changed if name not in same_as_default])
            after = settings_repo.stored_texts()
        _log_changed(changed)
        return after

    @use_case
    def sync_segments(self, segments: Sequence[SegmentDef]) -> SegmentSync:
        """Load ``segments`` into a database that holds none, else report the differences;
        see the module docstring."""
        chosen, texts = _segment_inputs(segments)
        now = self._clock.now()
        with current().transaction():
            result = _sync_segments(chosen, texts, now)
        _log_segment_sync(result)
        return result

    @use_case
    def sync_markets(self, markets: Sequence[MarketDef]) -> MarketSync:
        """Upsert ``markets`` by code; see the module docstring."""
        chosen = _market_inputs(markets)
        now = self._clock.now()
        with current().transaction():
            result = _sync_markets(chosen, now)
        _log_sync(MARKETS_SYNCED, result)
        return result

    @command
    def seed_sync(self, checked: CheckedSeed, now: datetime) -> SetupSeedSync:
        """Upsert the checked Segments, Markets, Sequences, Offers and Campaigns in the caller's
        transaction; see the module docstring. Managed (Story 11.8: the dashboard owns the
        Offers and Campaigns) when the check skipped the references or a Campaign is stored."""
        if not isinstance(checked, CheckedSeed):
            raise TypeError(f"seed_sync takes a CheckedSeed, not {type(checked).__name__}")
        segment_result = _sync_segments(checked.segments, checked.segment_texts, now)
        market_result = _sync_markets(checked.markets, now)
        sequence_result = _sync_sequences(checked.sequences, now)
        managed = checked.campaigns_managed or bool(campaigns_repo.keys())
        offer_result = _sync_offers(checked.offers, now, managed=managed)
        campaign_result = _sync_campaigns(checked.campaigns, now, managed=managed)
        return SetupSeedSync(
            segment_result, market_result, sequence_result, offer_result, campaign_result
        )


def _log_sync(
    event: str,
    result: SegmentSync | MarketSync | SequenceSync | OfferSync | CampaignSync,
    **more: object,
) -> None:
    log.info(  # "created" itself is a LogRecord attribute, so each count is suffixed
        event,
        extra={
            "created_count": len(result.created),
            "changed_count": len(result.changed),
            "unchanged_count": len(result.unchanged),
            "not_in_file_count": len(result.not_in_file),
            **more,
        },
    )


def _log_segment_sync(result: SegmentSync) -> None:
    _log_sync(
        SEGMENTS_SYNCED,
        result,
        managed=result.managed,
        differences_count=len(result.differences),
    )


# Segments -----------------------------------------------------------------------------------------


def _segment_inputs(
    segments: Sequence[SegmentDef],
) -> tuple[tuple[SegmentDef, ...], tuple[str, ...]]:
    """``sync_segments``'s checks before any transaction: the Segments and their definition
    texts, or ``TypeError``, ``ValueError`` or ``InvalidSegments``."""
    chosen = tuple(segments)
    for segment in chosen:
        if not isinstance(segment, SegmentDef):
            raise TypeError(f"sync_segments takes SegmentDefs, not {type(segment).__name__}")
    counts = Counter(segment.key for segment in chosen)
    repeated = sorted(key for key, count in counts.items() if count > 1)
    if repeated:
        raise ValueError(f"sync_segments got a key more than once: {', '.join(repeated)}")
    texts = tuple(definition_text(segment) for segment in chosen)
    if texts:  # the file form again: InvalidSegments for a SegmentDef that breaks a rule
        validate_segments({"segments": [json.loads(text) for text in texts]})
    return chosen, texts


def _sync_segments(
    chosen: tuple[SegmentDef, ...], texts: tuple[str, ...], now: datetime
) -> SegmentSync:
    """The Segments' bootstrap, or the managed mode's differences, inside the running use
    case's transaction."""
    stored = segments_repo.all_segments()
    if stored:
        return SegmentSync((), (), (), (), True, _segment_differences(chosen, stored))
    created: list[tuple[str, int]] = []
    for segment, text in zip(chosen, texts, strict=True):
        segment_id = segments_repo.insert(segment.key, text, now, SEED_ACTOR)
        _record_segment(segment_id, None, segments_repo.FIRST_VERSION, now)
        created.append((segment.key, segments_repo.FIRST_VERSION))
    return SegmentSync(tuple(created), (), (), ())


def _segment_differences(
    chosen: Sequence[SegmentDef], stored: Sequence[segments_repo.StoredSegment]
) -> tuple[SegmentDifference, ...]:
    """How the file's Segments differ from the stored ones: the file's keys in input order,
    then the stored keys the file leaves out, by key."""
    by_key = {row.key: row for row in stored}
    found: list[SegmentDifference] = []
    for segment in chosen:
        row = by_key.get(segment.key)
        if row is None:
            found.append(SegmentDifference(segment.key, SEGMENT_ONLY_IN_FILE))
            continue
        changes = segment_form_diff(segment_from_text(row.definition), segment)
        if changes:
            paths = tuple(change.path for change in changes)
            found.append(SegmentDifference(segment.key, None, paths))
    keys = {segment.key for segment in chosen}
    found += [
        SegmentDifference(row.key, SEGMENT_ONLY_IN_DASHBOARD)
        for row in stored
        if row.key not in keys
    ]
    return tuple(found)


def _version_status(version: int) -> str:
    return f"v{version}"


def _record_segment(
    segment_id: str, from_version: int | None, to_version: int, at: datetime
) -> None:
    eventlog.append(
        entity=SEGMENTS_ENTITY,
        entity_id=segment_id,
        from_status=None if from_version is None else _version_status(from_version),
        to_status=_version_status(to_version),
        cause=SEED_SYNC_CAUSE,
        cause_ref=None,
        actor=SEED_ACTOR,
        at=at,
    )


# Markets ------------------------------------------------------------------------------------------


def _market_inputs(markets: Sequence[MarketDef]) -> tuple[MarketDef, ...]:
    """``sync_markets``'s checks before any transaction: the Markets as validation returns them
    from their file form, or ``TypeError``, ``ValueError`` or ``InvalidMarkets``."""
    chosen = tuple(markets)
    for market in chosen:
        if not isinstance(market, MarketDef):
            raise TypeError(f"sync_markets takes MarketDefs, not {type(market).__name__}")
    counts = Counter(market.code for market in chosen)
    repeated = sorted(code for code, count in counts.items() if count > 1)
    if repeated:
        raise ValueError(f"sync_markets got a code more than once: {', '.join(repeated)}")
    if not chosen:
        return ()
    return validate_markets({"markets": [market_form(market) for market in chosen]})


def _sync_markets(chosen: tuple[MarketDef, ...], now: datetime) -> MarketSync:
    """The Markets' upsert, inside the running use case's transaction."""
    created: list[tuple[str, int]] = []
    changed: list[tuple[str, int, int]] = []
    unchanged: list[str] = []
    stored_markets = {row.code: row for row in markets_repo.all_markets()}
    codes = {market.code for market in chosen}
    left_out = [row for code, row in stored_markets.items() if code not in codes]
    _refuse_shared_markets(chosen, left_out)  # before any write
    for market in chosen:
        stored = stored_markets.get(market.code)
        if stored is None:
            market_id = markets_repo.insert(market, now)
            _record_market(market_id, None, markets_repo.FIRST_VERSION, now)
            created.append((market.code, markets_repo.FIRST_VERSION))
        elif stored.market != market:
            version = stored.version + 1
            markets_repo.update(stored.id, market, version, now)
            _record_market(stored.id, stored.version, version, now)
            changed.append((market.code, stored.version, version))
        else:
            unchanged.append(market.code)
    not_in_file = tuple((row.code, row.version) for row in left_out)
    return MarketSync(tuple(created), tuple(changed), tuple(unchanged), not_in_file)


def _refuse_shared_markets(
    markets: Sequence[MarketDef], left_out: Sequence[markets_repo.StoredMarket]
) -> None:
    """Country codes and ccTLDs stay unique across every stored Market: raise
    ``InvalidMarkets`` for the first input Market, in file order, whose country code or a ccTLD
    a stored Market the input leaves out still uses (a Market renamed in the file, say)."""
    countries: dict[str, str] = {}
    cctlds: dict[str, str] = {}
    for row in left_out:
        countries.setdefault(row.market.country_code, row.code)
        for cctld in row.market.cctlds:
            cctlds.setdefault(cctld, row.code)
    for market in markets:
        owner = countries.get(market.country_code)
        if owner is not None:
            raise InvalidMarkets(
                f"markets[{market.code}].country_code",
                f"is also the country code of the stored Market {owner}, which is no longer in "
                "the file: keep that Market's code",
            )
        for cctld in market.cctlds:
            owner = cctlds.get(cctld)
            if owner is not None:
                raise InvalidMarkets(
                    f"markets[{market.code}].cctlds",
                    f"holds {cctld}, a ccTLD of the stored Market {owner}, which is no longer in "
                    "the file: keep that Market's code",
                )


def _record_market(
    market_id: str, from_version: int | None, to_version: int, at: datetime
) -> None:
    eventlog.append(
        entity=MARKETS_ENTITY,
        entity_id=market_id,
        from_status=None if from_version is None else _version_status(from_version),
        to_status=_version_status(to_version),
        cause=SEED_SYNC_CAUSE,
        cause_ref=None,
        actor=SEED_ACTOR,
        at=at,
    )


# Sequences ----------------------------------------------------------------------------------------


def _sequence_inputs(
    sequences: Sequence[SequenceDef],
    segment_keys: Collection[str],
    market_countries: Mapping[str, str],
) -> tuple[SequenceDef, ...]:
    """``check_seed``'s Sequence checks before any transaction: the Sequences as validation
    returns them from their file form, their angles checked against ``segment_keys`` and
    ``market_countries`` (a Market code to its country code), or ``TypeError``, ``ValueError``
    or ``InvalidSequences``."""
    chosen = tuple(sequences)
    for sequence in chosen:
        if not isinstance(sequence, SequenceDef):
            raise TypeError(f"sync_seed takes SequenceDefs, not {type(sequence).__name__}")
    counts = Counter(sequence.key for sequence in chosen)
    repeated = sorted(key for key, count in counts.items() if count > 1)
    if repeated:
        raise ValueError(f"sync_seed got a Sequence key more than once: {', '.join(repeated)}")
    if not chosen:
        return ()
    validated = validate_sequences({"sequences": [sequence_form(s) for s in chosen]})
    check_sequence_references(validated, segment_keys, market_countries)
    return validated


def _sync_sequences(chosen: tuple[SequenceDef, ...], now: datetime) -> SequenceSync:
    """The Sequences' bootstrap, or the managed mode's differences (Story 11.7), inside the
    running use case's transaction."""
    stored = sequences_repo.all_sequences()
    if stored:
        return SequenceSync((), (), (), (), True, _sequence_differences(chosen, stored))
    created: list[tuple[str, int]] = []
    for sequence in chosen:
        sequence_id = sequences_repo.insert(sequence, now, SEED_ACTOR)
        _record_sequence(sequence_id, None, sequences_repo.FIRST_VERSION, now)
        created.append((sequence.key, sequences_repo.FIRST_VERSION))
    return SequenceSync(tuple(created), (), (), ())


def _sequence_differences(
    chosen: Sequence[SequenceDef], stored: Sequence[sequences_repo.StoredSequence]
) -> tuple[SequenceDifference, ...]:
    """How the file's Sequences differ from the stored ones: the file's keys in input order,
    then the stored keys the file leaves out, by key."""
    by_key = {row.key: row for row in stored}
    found: list[SequenceDifference] = []
    for sequence in chosen:
        row = by_key.get(sequence.key)
        if row is None:
            found.append(SequenceDifference(sequence.key, SEGMENT_ONLY_IN_FILE))
            continue
        changes = sequence_form_diff(row.sequence, sequence)
        if changes:
            paths = tuple(change.path for change in changes)
            found.append(SequenceDifference(sequence.key, None, paths))
    keys = {sequence.key for sequence in chosen}
    found += [
        SequenceDifference(row.key, SEGMENT_ONLY_IN_DASHBOARD)
        for row in stored
        if row.key not in keys
    ]
    return tuple(found)


def _record_sequence(
    sequence_id: str, from_version: int | None, to_version: int, at: datetime
) -> None:
    eventlog.append(
        entity=SEQUENCES_ENTITY,
        entity_id=sequence_id,
        from_status=None if from_version is None else _version_status(from_version),
        to_status=_version_status(to_version),
        cause=SEED_SYNC_CAUSE,
        cause_ref=None,
        actor=SEED_ACTOR,
        at=at,
    )


# Offers -------------------------------------------------------------------------------------------


def _offer_inputs(offers: Sequence[OfferDef]) -> tuple[OfferDef, ...]:
    """``check_seed``'s Offer checks before any transaction: the Offers as validation returns
    them from their file form, or ``TypeError``, ``ValueError`` or ``InvalidOffers``."""
    chosen = tuple(offers)
    for offer in chosen:
        if not isinstance(offer, OfferDef):
            raise TypeError(f"sync_seed takes OfferDefs, not {type(offer).__name__}")
    counts = Counter(offer.key for offer in chosen)
    repeated = sorted(key for key, count in counts.items() if count > 1)
    if repeated:
        raise ValueError(f"sync_seed got an Offer key more than once: {', '.join(repeated)}")
    if not chosen:
        return ()
    return validate_offers({"offers": [offer_form(offer) for offer in chosen]})


def _sync_offers(
    chosen: tuple[OfferDef, ...], now: datetime, *, managed: bool = False
) -> OfferSync:
    """The Offers' upsert, or (Story 11.8) the managed mode's differences, inside the running
    use case's transaction."""
    if managed:
        return OfferSync((), (), (), (), True, _offer_differences(chosen, offers_repo.all_offers()))
    created: list[tuple[str, int]] = []
    changed: list[tuple[str, int, int]] = []
    unchanged: list[str] = []
    stored_offers = {row.key: row for row in offers_repo.all_offers()}
    keys = {offer.key for offer in chosen}
    left_out = [row for key, row in stored_offers.items() if key not in keys]
    for offer in chosen:
        stored = stored_offers.get(offer.key)
        if stored is None:
            offer_id = offers_repo.insert(offer, now)
            _record_version(OFFERS_ENTITY, offer_id, None, offers_repo.FIRST_VERSION, now)
            created.append((offer.key, offers_repo.FIRST_VERSION))
        elif stored.offer != offer:
            version = stored.version + 1
            offers_repo.update(stored.id, offer, version, now)
            _record_version(OFFERS_ENTITY, stored.id, stored.version, version, now)
            changed.append((offer.key, stored.version, version))
        else:
            unchanged.append(offer.key)
    not_in_file = tuple((row.key, row.version) for row in left_out)
    return OfferSync(tuple(created), tuple(changed), tuple(unchanged), not_in_file)


def _offer_differences(
    chosen: Sequence[OfferDef], stored: Sequence[offers_repo.StoredOffer]
) -> tuple[OfferDifference, ...]:
    """How the file's Offers differ from the stored ones: the file's keys in input order, then
    the stored keys the file leaves out, by key."""
    by_key = {row.key: row for row in stored}
    found: list[OfferDifference] = []
    for offer in chosen:
        row = by_key.get(offer.key)
        if row is None:
            found.append(OfferDifference(offer.key, SEGMENT_ONLY_IN_FILE))
            continue
        paths = tuple(
            name for name in ("name", "ladder") if getattr(row.offer, name) != getattr(offer, name)
        )
        if paths:
            found.append(OfferDifference(offer.key, None, paths))
    keys = {offer.key for offer in chosen}
    found += [
        OfferDifference(row.key, SEGMENT_ONLY_IN_DASHBOARD) for row in stored if row.key not in keys
    ]
    return tuple(found)


def _record_version(
    entity: str, row_id: str, from_version: int | None, to_version: int, at: datetime
) -> None:
    """A seed version event, from ``None`` (a creation) or ``v<from>`` to ``v<to>``."""
    eventlog.append(
        entity=entity,
        entity_id=row_id,
        from_status=None if from_version is None else _version_status(from_version),
        to_status=_version_status(to_version),
        cause=SEED_SYNC_CAUSE,
        cause_ref=None,
        actor=SEED_ACTOR,
        at=at,
    )


# Campaigns ----------------------------------------------------------------------------------------


def _campaign_inputs(
    campaigns: Sequence[CampaignDef],
    segment_keys: Collection[str],
    market_countries: Mapping[str, str],
    offer_keys: Collection[str],
    sequence_steps: Mapping[str, int],
    *,
    check_references: bool = True,
) -> tuple[CampaignDef, ...]:
    """``check_seed``'s Campaign checks before any transaction: the Campaigns as validation
    returns them from their file form, their references checked against this call's Segments,
    Markets, Offers and Sequences (unless ``check_references`` is false: Story 11.8's managed
    mode checks the file's shape only), or ``TypeError``, ``ValueError`` or
    ``InvalidCampaigns``."""
    chosen = tuple(campaigns)
    for campaign in chosen:
        if not isinstance(campaign, CampaignDef):
            raise TypeError(f"sync_seed takes CampaignDefs, not {type(campaign).__name__}")
    counts = Counter(campaign.key for campaign in chosen)
    repeated = sorted(key for key, count in counts.items() if count > 1)
    if repeated:
        raise ValueError(f"sync_seed got a Campaign key more than once: {', '.join(repeated)}")
    if not chosen:
        return ()
    validated = validate_campaigns({"campaigns": [campaign_form(c) for c in chosen]})
    if check_references:
        check_campaign_references(
            validated, segment_keys, market_countries, offer_keys, sequence_steps
        )
    return validated


@dataclass(frozen=True, slots=True)
class _CampaignIds:
    """The ids a file Campaign's keys and codes name in this transaction."""

    segment_id: str
    market_id: str
    offer_id: str
    sequence_id: str


_REFERENCES: Final = (
    ("segment", "Segment"),
    ("market", "Market"),
    ("offer", "Offer"),
    ("sequence", "Sequence"),
)


def _campaign_ids(campaign: CampaignDef, ids: Sequence[Mapping[str, str]]) -> _CampaignIds:
    """``campaign``'s Segment, Market, Offer and Sequence ids, from ``ids`` (one map per
    reference, in ``_REFERENCES`` order, a key or code to its id); ``InvalidCampaigns`` for a
    reference this sync didn't store."""
    found = []
    for (name, kind), known in zip(_REFERENCES, ids, strict=True):
        found_id = known.get(getattr(campaign, name))
        if found_id is None:
            raise InvalidCampaigns(
                f"campaigns[{campaign.key}].{name}", f"isn't a {kind} of this sync"
            )
        found.append(found_id)
    return _CampaignIds(*found)


def _sync_campaigns(
    chosen: tuple[CampaignDef, ...], now: datetime, *, managed: bool = False
) -> CampaignSync:
    """The Campaigns' bootstrap, or (Story 11.8) the managed mode's differences, inside the
    running use case's transaction. It never writes a Campaign's status or LIA."""
    if managed:
        stored = campaigns_repo.all_campaigns()
        return CampaignSync((), (), (), (), True, _campaign_differences(chosen, stored))
    ids = (
        {row.key: row.id for row in segments_repo.all_segments()},
        {row.code: row.id for row in markets_repo.all_markets()},
        {row.key: row.id for row in offers_repo.all_offers()},
        {row.key: row.id for row in sequences_repo.all_sequences()},
    )
    refs = {campaign.key: _campaign_ids(campaign, ids) for campaign in chosen}
    created: list[tuple[str, int]] = []
    for campaign in chosen:
        found = refs[campaign.key]
        campaign_id = campaigns_repo.insert(
            campaign,
            segment_id=found.segment_id,
            market_id=found.market_id,
            offer_id=found.offer_id,
            sequence_id=found.sequence_id,
            at=now,
        )
        _record_version(CAMPAIGNS_ENTITY, campaign_id, None, campaigns_repo.FIRST_VERSION, now)
        created.append((campaign.key, campaigns_repo.FIRST_VERSION))
    return CampaignSync(tuple(created), (), (), ())


def _campaign_differences(
    chosen: Sequence[CampaignDef], stored: Sequence[campaigns_repo.StoredCampaign]
) -> tuple[CampaignDifference, ...]:
    """How the file's Campaigns differ from the stored ones: the file's keys in input order,
    then the stored keys the file leaves out, by key."""
    by_key = {row.key: row for row in stored}
    found: list[CampaignDifference] = []
    for campaign in chosen:
        row = by_key.get(campaign.key)
        if row is None:
            found.append(CampaignDifference(campaign.key, SEGMENT_ONLY_IN_FILE))
            continue
        paths = campaign_form_diff(row.campaign, campaign)
        if paths:
            found.append(CampaignDifference(campaign.key, None, paths))
    keys = {campaign.key for campaign in chosen}
    found += [
        CampaignDifference(row.key, SEGMENT_ONLY_IN_DASHBOARD)
        for row in stored
        if row.key not in keys
    ]
    return tuple(found)


@dataclass(frozen=True, slots=True)
class CampaignChanged:
    """What ``activate``, ``pause`` or ``resume`` did: the Campaign's id, key and name, and the
    status it moved from and to."""

    campaign_id: str
    key: str
    name: str
    from_status: CampaignStatus
    to_status: CampaignStatus


@dataclass(frozen=True, slots=True)
class LiaSaved:
    """What ``record_lia`` did: whether new values were stored."""

    campaign_id: str
    changed: bool


@dataclass(frozen=True, slots=True)
class VariantAdded:
    """What ``add_variant`` did: the new Variant's id and name, and the Campaign's version
    afterwards."""

    campaign_id: str
    variant_id: str
    name: str
    version: int


@dataclass(frozen=True, slots=True)
class CampaignSaved:
    """What ``create_campaign`` or ``edit_campaign`` did: the Campaign's id, key and (new or
    unchanged) version, and whether anything was written (Story 11.8)."""

    campaign_id: str
    key: str
    version: int
    changed: bool


@dataclass(frozen=True, slots=True)
class VariantSaved:
    """What ``edit_variant`` or ``retire_variant`` did: the Variant's id and name, the
    Campaign's (new or unchanged) version, and whether anything was written (Story 11.8)."""

    campaign_id: str
    variant_id: str
    name: str
    version: int
    changed: bool


@dataclass(frozen=True, slots=True)
class _References:
    """The stored rows a Campaign form's ids name: the Market's country and the Sequence's
    Step count, which the checks need."""

    segment_id: str
    market_id: str
    offer_id: str
    sequence_id: str
    country_code: str
    sequence_steps: int


def _text(value: object, label: str) -> str:
    """A submitted text, stripped; ``TypeError`` for anything that isn't text."""
    if not isinstance(value, str):
        raise TypeError(f"the Campaign {label} is text, not {type(value).__name__}")
    return value.strip()


def _references(
    segment_id: object,
    market_id: object,
    offer_id: object,
    sequence_id: object,
    problems: dict[str, str],
) -> _References | None:
    """The stored rows the four ids name, or ``None`` after adding each blank id
    (``CHOOSE_ONE``) or id that names no stored row to ``problems``."""
    given = {
        SEGMENT_FIELD: _text(segment_id, SEGMENT_FIELD),
        MARKET_FIELD: _text(market_id, MARKET_FIELD),
        OFFER_FIELD: _text(offer_id, OFFER_FIELD),
        SEQUENCE_FIELD: _text(sequence_id, SEQUENCE_FIELD),
    }
    for name, text in given.items():
        if not text:
            problems[name] = CHOOSE_ONE
        elif not is_ulid(text):
            problems[name] = NO_SUCH_REFERENCE[name]
            given[name] = ""  # names no stored row
    segment_text = given[SEGMENT_FIELD]
    if segment_text and segments_repo.get(segment_text) is None:
        problems[SEGMENT_FIELD] = NO_SUCH_REFERENCE[SEGMENT_FIELD]
    countries = {row.id: row.market.country_code for row in markets_repo.all_markets()}
    if given[MARKET_FIELD] and given[MARKET_FIELD] not in countries:
        problems[MARKET_FIELD] = NO_SUCH_REFERENCE[MARKET_FIELD]
    offer_ids = {row.id for row in offers_repo.all_offers()}
    if given[OFFER_FIELD] and given[OFFER_FIELD] not in offer_ids:
        problems[OFFER_FIELD] = NO_SUCH_REFERENCE[OFFER_FIELD]
    sequence = sequences_repo.by_id(given[SEQUENCE_FIELD]) if given[SEQUENCE_FIELD] else None
    if given[SEQUENCE_FIELD] and sequence is None:
        problems[SEQUENCE_FIELD] = NO_SUCH_REFERENCE[SEQUENCE_FIELD]
    if any(name in problems for name in given) or sequence is None:
        return None
    return _References(
        segment_id=given[SEGMENT_FIELD],
        market_id=given[MARKET_FIELD],
        offer_id=given[OFFER_FIELD],
        sequence_id=given[SEQUENCE_FIELD],
        country_code=countries[given[MARKET_FIELD]],
        sequence_steps=len(sequence.sequence.steps),
    )


def _pair_holder(segment_id: str, market_id: str, other_than: str | None) -> bool:
    """Whether a stored Campaign other than ``other_than`` holds this (Segment, Market) pair;
    read without decoding the Campaigns."""
    return any(
        held != other_than
        for held in campaigns_repo.ids_for_pair(segment_id, market_id)
    )


def _active(stored: campaigns_repo.StoredCampaign) -> list[campaigns_repo.StoredVariant]:
    return [found for found in stored.variants if found.status == VariantStatus.ACTIVE]


def _refuse_no_active_variant(stored: campaigns_repo.StoredCampaign) -> None:
    """``CampaignRefused`` (``NEEDS_VARIANT_FIRST``) for a Campaign with no active Variant."""
    if not _active(stored):
        raise CampaignRefused({FORM_FIELD: NEEDS_VARIANT_FIRST})


def _refuse_stale(stored: campaigns_repo.StoredCampaign, based_on: object) -> None:
    if isinstance(based_on, bool) or based_on != stored.version:
        raise CampaignRefused({FORM_FIELD: STALE_CAMPAIGN})


def _stored_variant(
    stored: campaigns_repo.StoredCampaign, variant_id: object
) -> campaigns_repo.StoredVariant:
    """The Campaign's Variant ``variant_id``, still ``active``; ``CampaignRefused`` when it has
    no such Variant or the Variant is retired."""
    for found in stored.variants:
        if found.id == variant_id:
            if found.status != VariantStatus.ACTIVE:
                raise CampaignRefused({FORM_FIELD: VARIANT_RETIRED})
            return found
    raise CampaignRefused({FORM_FIELD: NO_SUCH_VARIANT})


def _bump(stored: campaigns_repo.StoredCampaign, cause: str, now: datetime) -> int:
    """Give the Campaign its next version, unchanged otherwise, with its event; the new
    version."""
    version = stored.version + 1
    campaigns_repo.update_config(
        stored.id,
        name=stored.name,
        offer_id=stored.offer_id,
        sequence_id=stored.sequence_id,
        version=version,
        at=now,
    )
    _record_campaign(
        stored.id, _version_status(stored.version), _version_status(version), cause, now
    )
    return version


def _steps_text(steps: int) -> str:
    return f"{steps} Step" if steps == 1 else f"{steps} Steps"


def _edit_problems(
    stored: campaigns_repo.StoredCampaign, found: _References, enrolled: bool
) -> dict[str, str]:
    """``edit_campaign``'s checks of a changed definition, in order; the first problem only."""
    if (found.segment_id, found.market_id) != (stored.segment_id, stored.market_id):
        if enrolled:
            return {FORM_FIELD: PAIR_FROZEN}
        if _pair_holder(found.segment_id, found.market_id, stored.id):
            return {FORM_FIELD: PAIR_TAKEN}
        if stored.status != CampaignStatus.DRAFT and needs_lia(found.country_code):
            # The stored LIA was made for the old pair and is cleared, so only a draft
            # Campaign (which asks for a new one at activation) may move into a UK pair.
            return {FORM_FIELD: LIA_FIRST, MARKET_FIELD: UK_PAIR_DRAFT_ONLY}
    active = _active(stored)
    if found.sequence_id != stored.sequence_id:
        for variant in active:
            beyond = [note.step for note in variant.step_angles if note.step > found.sequence_steps]
            if beyond:
                return {
                    SEQUENCE_FIELD: f"Variant {variant.name} has an angle note for Step "
                    f"{max(beyond)}, but this Sequence has {_steps_text(found.sequence_steps)}"
                }
    if found.market_id != stored.market_id:
        for variant in active:
            for index, note in enumerate(variant.step_angles):
                problem = note_floor_problem(note.note, found.country_code)
                if problem is not None:
                    return {
                        MARKET_FIELD: f"Variant {variant.name}'s angle note {index + 1} {problem}"
                    }
    return {}


def _record_campaign(
    campaign_id: str, from_status: str | None, to_status: str, cause: str, at: datetime
) -> None:
    """An Operator's ``campaigns`` event."""
    eventlog.append(
        entity=CAMPAIGNS_ENTITY,
        entity_id=campaign_id,
        from_status=from_status,
        to_status=to_status,
        cause=cause,
        cause_ref=None,
        actor=OPERATOR_ACTOR,
        at=at,
    )


def _log_campaign(event: str, key: str) -> None:
    log.info(event, extra={CAMPAIGN_LOG_FIELD: key})


def _stored_campaign(campaign_id: object) -> campaigns_repo.StoredCampaign:
    """The stored Campaign ``campaign_id``; ``CampaignRefused`` when there is none."""
    stored = campaigns_repo.by_id(campaign_id) if isinstance(campaign_id, str) else None
    if stored is None:
        raise CampaignRefused({FORM_FIELD: NO_SUCH_CAMPAIGN})
    return stored


def _allowed(
    stored: campaigns_repo.StoredCampaign, event: CampaignEvent
) -> Transition[CampaignStatus, CampaignCause]:
    """Where ``event`` takes the stored Campaign; ``CampaignRefused`` when the machine refuses
    it from the stored status."""
    try:
        return transition(stored.status, event)
    except IllegalTransition:
        verb = EVENT_VERBS[event]
        raise CampaignRefused(
            {FORM_FIELD: f"This Campaign is {stored.status}, so it can't be {verb}"}
        ) from None


def _move(
    stored: campaigns_repo.StoredCampaign,
    moved: Transition[CampaignStatus, CampaignCause],
    now: datetime,
) -> CampaignChanged:
    """Write the transition and its event."""
    if not campaigns_repo.set_status(stored.id, stored.status, moved.to, now):
        raise CampaignRefused({FORM_FIELD: CAMPAIGN_CHANGED})  # BEGIN IMMEDIATE: can't happen
    _record_campaign(stored.id, str(stored.status), str(moved.to), str(moved.cause), now)
    return CampaignChanged(stored.id, stored.key, stored.name, stored.status, moved.to)


def _refuse_lia_gap(stored: campaigns_repo.StoredCampaign) -> None:
    """``CampaignRefused`` (``LIA_FIRST``) for a UK Campaign that isn't ``draft`` and has no
    stored LIA: only possible when its Market's country became ``GB`` after it left ``draft``.
    The database triggers refuse any write to such a row, so the Operator records the LIA
    first (``record_lia``); meanwhile ``campaign_active`` is false."""
    if (
        stored.status != CampaignStatus.DRAFT
        and needs_lia(stored.country_code)
        and stored.lia is None
    ):
        raise CampaignRefused({FORM_FIELD: LIA_FIRST})


def _given(text: str | None) -> str:
    """A submitted LIA field, blank when it wasn't submitted."""
    if text is None:
        return ""
    if not isinstance(text, str):
        raise TypeError(f"an LIA field is text, not {type(text).__name__}")
    return text


class Campaigns:
    """The Operator's Campaign commands: activate, pause, resume, record the LIA, add a Variant,
    and (Story 11.8) create and edit a Campaign and edit and retire a Variant; see the module
    docstring."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock

    def _today(self, now: datetime) -> date:
        """The date of ``now`` in ``sender_timezone``, or in UTC while it is unset or
        unreadable."""
        zone: tzinfo = UTC
        try:
            zone = ZoneInfo(str(setup_queries.get(Key.SENDER_TIMEZONE)))
        except (SettingNotSet, InvalidSettingValue, ZoneInfoNotFoundError, ValueError, OSError):
            pass
        return now.astimezone(zone).date()

    def _save_lia(
        self,
        stored: campaigns_repo.StoredCampaign,
        lia_ref: str | None,
        lia_date: str | None,
        now: datetime,
    ) -> bool:
        """Validate a UK Campaign's submitted LIA and store it, with its event, when it differs
        from the stored one; whether it was stored."""
        lia = lia_from_fields(_given(lia_ref), _given(lia_date), self._today(now))
        if lia == stored.lia:
            return False
        campaigns_repo.set_lia(stored.id, lia, now)
        status = str(stored.status)
        _record_campaign(stored.id, status, status, LIA_RECORDED_CAUSE, now)
        return True

    @use_case
    def activate(
        self, campaign_id: str, lia_ref: str | None = None, lia_date: str | None = None
    ) -> CampaignChanged:
        """Activate a ``draft`` Campaign, recording a UK Campaign's LIA first."""
        now = self._clock.now()
        with current().transaction():
            stored = _stored_campaign(campaign_id)
            moved = _allowed(stored, CampaignEvent.ACTIVATE)
            _refuse_no_active_variant(stored)  # Story 11.8: before the LIA
            lia_saved = False
            if needs_lia(stored.country_code):
                lia_saved = self._save_lia(stored, lia_ref, lia_date, now)
            elif lia_ref is not None or lia_date is not None:
                raise CampaignRefused({FORM_FIELD: ONLY_UK_LIA})
            result = _move(stored, moved, now)
        if lia_saved:
            _log_campaign(CAMPAIGN_LIA_SAVED, stored.key)
        _log_campaign(CAMPAIGN_ACTIVATED, stored.key)
        return result

    @use_case
    def pause(self, campaign_id: str) -> CampaignChanged:
        """Pause an ``active`` Campaign: nothing of it is enrolled or queued until it resumes."""
        now = self._clock.now()
        with current().transaction():
            stored = _stored_campaign(campaign_id)
            moved = _allowed(stored, CampaignEvent.PAUSE)
            _refuse_lia_gap(stored)
            result = _move(stored, moved, now)
        _log_campaign(CAMPAIGN_PAUSED, stored.key)
        return result

    @use_case
    def resume(self, campaign_id: str) -> CampaignChanged:
        """Resume a ``paused`` Campaign; a UK one needs its stored LIA."""
        now = self._clock.now()
        with current().transaction():
            stored = _stored_campaign(campaign_id)
            moved = _allowed(stored, CampaignEvent.RESUME)
            _refuse_no_active_variant(stored)  # Story 11.8: before the LIA
            _refuse_lia_gap(stored)  # a paused UK Campaign resumes only with its stored LIA
            result = _move(stored, moved, now)
        _log_campaign(CAMPAIGN_RESUMED, stored.key)
        return result

    @use_case
    def record_lia(self, campaign_id: str, lia_ref: str, lia_date: str) -> LiaSaved:
        """Record a UK Campaign's LIA, in any status; equal values change nothing."""
        now = self._clock.now()
        with current().transaction():
            stored = _stored_campaign(campaign_id)
            if not needs_lia(stored.country_code):
                raise CampaignRefused({FORM_FIELD: ONLY_UK_LIA})
            saved = self._save_lia(stored, lia_ref, lia_date, now)
        if saved:
            _log_campaign(CAMPAIGN_LIA_SAVED, stored.key)
        return LiaSaved(stored.id, saved)

    @use_case
    def add_variant(
        self,
        campaign_id: str,
        name: str,
        step_angles: Sequence[StepAngleNote] = (),
        based_on: int | None = None,
    ) -> VariantAdded:
        """Add an ``active`` Variant to a Campaign, giving the Campaign the next version."""
        now = self._clock.now()
        with current().transaction():
            stored = _stored_campaign(campaign_id)
            if based_on is not None:
                _refuse_stale(stored, based_on)
            _refuse_lia_gap(stored)
            sequence = sequences_repo.by_key(stored.campaign.sequence)
            variant = variant_from_fields(
                name,
                step_angles,
                existing_names=[found.name for found in stored.variants],
                steps=0 if sequence is None else len(sequence.sequence.steps),
                country_code=stored.country_code,
                active_count=len(_active(stored)),
            )
            variant_id = campaigns_repo.insert_variant(stored.id, variant, now)
            version = _bump(stored, VARIANT_ADDED_CAUSE, now)
        _log_campaign(VARIANT_ADDED, stored.key)
        return VariantAdded(stored.id, variant_id, variant.name, version)

    @use_case
    def create_campaign(
        self,
        key: str,
        name: str,
        segment_id: str,
        market_id: str,
        offer_id: str,
        sequence_id: str,
    ) -> CampaignSaved:
        """Create a ``draft`` Campaign with no Variant (Story 11.8)."""
        chosen_key = _text(key, KEY_FIELD)
        chosen_name = _text(name, "name")
        now = self._clock.now()
        with current().transaction():
            problems = campaign_fields_problems(chosen_name, chosen_key)
            if KEY_FIELD not in problems and chosen_key in campaigns_repo.keys():
                problems[KEY_FIELD] = KEY_TAKEN
            found = _references(segment_id, market_id, offer_id, sequence_id, problems)
            if problems or found is None:
                raise CampaignRefused(problems)
            if _pair_holder(found.segment_id, found.market_id, None):
                raise CampaignRefused({FORM_FIELD: PAIR_TAKEN})
            # Only the ids are stored; the keys are what the file form would name.
            definition = CampaignDef(chosen_key, chosen_name, "", "", "", "", ())
            campaign_id = campaigns_repo.insert(
                definition,
                segment_id=found.segment_id,
                market_id=found.market_id,
                offer_id=found.offer_id,
                sequence_id=found.sequence_id,
                at=now,
            )
            _record_campaign(
                campaign_id,
                None,
                _version_status(campaigns_repo.FIRST_VERSION),
                CAMPAIGN_CREATED_CAUSE,
                now,
            )
        _log_campaign(CAMPAIGN_CREATED, chosen_key)
        return CampaignSaved(campaign_id, chosen_key, campaigns_repo.FIRST_VERSION, True)

    @use_case
    def edit_campaign(
        self,
        campaign_id: str,
        based_on: int,
        name: str,
        segment_id: str,
        market_id: str,
        offer_id: str,
        sequence_id: str,
    ) -> CampaignSaved:
        """Change a Campaign's name, Segment, Market, Offer or Sequence (Story 11.8); its key
        never changes."""
        chosen_name = _text(name, "name")
        now = self._clock.now()
        with current().transaction():
            stored = _stored_campaign(campaign_id)
            _refuse_stale(stored, based_on)
            _refuse_lia_gap(stored)
            problems = campaign_fields_problems(chosen_name)
            found = _references(segment_id, market_id, offer_id, sequence_id, problems)
            if problems or found is None:
                raise CampaignRefused(problems)
            proposed = (
                chosen_name,
                found.segment_id,
                found.market_id,
                found.offer_id,
                found.sequence_id,
            )
            before = (
                stored.name,
                stored.segment_id,
                stored.market_id,
                stored.offer_id,
                stored.sequence_id,
            )
            if proposed == before:
                return CampaignSaved(stored.id, stored.key, stored.version, False)
            enrolled = any(sequencing_queries.enrollment_counts(stored.id).values())
            problems = _edit_problems(stored, found, enrolled)
            if problems:
                raise CampaignRefused(problems)
            version = stored.version + 1
            campaigns_repo.update_definition(
                stored.id,
                name=chosen_name,
                segment_id=found.segment_id,
                market_id=found.market_id,
                offer_id=found.offer_id,
                sequence_id=found.sequence_id,
                version=version,
                at=now,
                clear_lia=(found.segment_id, found.market_id)
                != (stored.segment_id, stored.market_id),
            )
            _record_campaign(
                stored.id,
                _version_status(stored.version),
                _version_status(version),
                CAMPAIGN_EDITED_CAUSE,
                now,
            )
        _log_campaign(CAMPAIGN_EDITED, stored.key)
        return CampaignSaved(stored.id, stored.key, version, True)

    @use_case
    def edit_variant(
        self,
        campaign_id: str,
        variant_id: str,
        based_on: int,
        name: str,
        step_angles: Sequence[StepAngleNote] = (),
    ) -> VariantSaved:
        """Change an active, never-enrolled Variant's name and notes (Story 11.8)."""
        now = self._clock.now()
        with current().transaction():
            stored = _stored_campaign(campaign_id)
            _refuse_stale(stored, based_on)
            _refuse_lia_gap(stored)
            before = _stored_variant(stored, variant_id)
            if sequencing_queries.enrollment_counts(stored.id).get(before.id, 0) > 0:
                raise CampaignRefused({FORM_FIELD: VARIANT_ENROLLED})
            sequence = sequences_repo.by_id(stored.sequence_id)
            variant = variant_from_fields(
                name,
                step_angles,
                existing_names=[found.name for found in stored.variants if found.id != before.id],
                steps=0 if sequence is None else len(sequence.sequence.steps),
                country_code=stored.country_code,
                active_count=0,  # it is already one of the active Variants
            )
            if variant == before.definition:
                return VariantSaved(stored.id, before.id, before.name, stored.version, False)
            campaigns_repo.update_variant(before.id, variant, now)
            version = _bump(stored, VARIANT_EDITED_CAUSE, now)
        _log_campaign(VARIANT_EDITED, stored.key)
        return VariantSaved(stored.id, before.id, variant.name, version, True)

    @use_case
    def retire_variant(self, campaign_id: str, variant_id: str, based_on: int) -> VariantSaved:
        """Retire an active Variant for good (Story 11.8); its Enrollments keep it."""
        now = self._clock.now()
        with current().transaction():
            stored = _stored_campaign(campaign_id)
            _refuse_stale(stored, based_on)
            _refuse_lia_gap(stored)
            before = _stored_variant(stored, variant_id)
            if stored.status != CampaignStatus.DRAFT and len(_active(stored)) <= 1:
                raise CampaignRefused({FORM_FIELD: LAST_ACTIVE_VARIANT})
            campaigns_repo.set_variant_status(before.id, VariantStatus.INACTIVE, now)
            version = _bump(stored, VARIANT_RETIRED_CAUSE, now)
        _log_campaign(VARIANT_RETIRED_LOG, stored.key)
        return VariantSaved(stored.id, before.id, before.name, version, True)


# Credentials ----------------------------------------------------------------------------------


class CredentialState(StrEnum):
    """Whether a credential's keyring entry exists, as Credential Manager answered."""

    SET = "set"
    NOT_SET = "not set"
    UNKNOWN = "unknown"  # Credential Manager could not be read


@dataclass(frozen=True, slots=True)
class CredentialStatus:
    """A credential's state, and its last-updated time when it is set and the time is known."""

    credential: Credential
    state: CredentialState
    updated_at: datetime | None


class UnsubKeyRole(StrEnum):
    """What an unsubscribe-token kid is to the engine, as the Credentials page shows it."""

    ACTIVE = "active"  # unsub_active_kid: new tokens are encoded under it
    NEXT = "next"  # active + 1: enter its keys (and the Worker's) before switching to it
    IN_USE = "in_use"  # it has a token under 2 years old: its keys can't change
    RETIRED = "retired"  # every token is 2 years old or more


@dataclass(frozen=True, slots=True)
class UnsubKeyStatus:
    """One unsubscribe-token key of one kid: its state, its last-updated time when it is set
    and the time is known, and whether the stored value is a 32-byte base64url key."""

    credential: UnsubKeyCredential
    kid: int
    state: CredentialState
    updated_at: datetime | None
    valid: bool


@dataclass(frozen=True, slots=True)
class UnsubKidKeys:
    """One kid on the Credentials page: its role, its newest token's time and its two keys."""

    kid: int
    role: UnsubKeyRole
    newest_token_at: datetime | None
    keys: tuple[UnsubKeyStatus, ...]


class CredentialStoreFailed(Exception):
    """Credential Manager failed while saving or removing the credential ``key``."""

    def __init__(self, key: str) -> None:
        super().__init__(f"Windows Credential Manager failed for credential {key}")
        self.key = str(key)


class Credentials:
    """External credentials in Windows Credential Manager: saved and removed, never shown."""

    def __init__(self, secrets: SecretsPort, clock: Clock) -> None:
        self._secrets = secrets
        self._clock = clock
        self._lock = threading.Lock()

    def statuses(self) -> tuple[CredentialStatus, ...]:
        """The status of every declared credential, in registry order."""
        declared = credential_registry.declared()
        with self._lock:
            return tuple(self._status(found) for found in declared)

    def status(self, key: str) -> CredentialStatus:
        """The status of the declared credential ``key``."""
        found = credential_registry.credential(key)
        with self._lock:
            return self._status(found)

    def save(self, key: str, text: str) -> CredentialStatus:
        """Store ``text``, stripped, as the declared credential ``key``, with its time."""
        found = credential_registry.credential(key)
        value = text.strip() if isinstance(text, str) else ""
        if not value:
            raise InvalidCredentialValue(found.key, ENTER_A_VALUE)
        if secret_length(value) > MAX_SECRET_LENGTH:
            raise InvalidCredentialValue(found.key, SECRET_TOO_LONG)
        with self._lock:
            stamp = encode_updated_at(self._clock.now())
            try:
                self._secrets.write(found.secret_name, value)
                self._secrets.write(found.meta_name, stamp)
            except AdapterError as failed:
                _store_failed(found, SAVE, failed)
            log.info(CREDENTIAL_SAVED, extra={"credential": str(found.key)})
            return CredentialStatus(found, CredentialState.SET, decode_updated_at(stamp))

    def remove(self, key: str) -> CredentialStatus:
        """Delete the declared credential ``key`` and its time."""
        found = credential_registry.credential(key)
        with self._lock:
            try:
                self._secrets.delete(found.secret_name)
                self._secrets.delete(found.meta_name)
            except AdapterError as failed:
                _store_failed(found, REMOVE, failed)
            log.info(CREDENTIAL_REMOVED, extra={"credential": str(found.key)})
            return CredentialStatus(found, CredentialState.NOT_SET, None)

    def _status(self, found: Credential) -> CredentialStatus:
        try:
            present = self._secrets.read(found.secret_name) is not None  # the value isn't kept
        except AdapterError as failed:
            _read_failed(found, VALUE_ENTRY, failed)
            return CredentialStatus(found, CredentialState.UNKNOWN, None)
        if not present:
            return CredentialStatus(found, CredentialState.NOT_SET, None)
        try:
            updated_at = decode_updated_at(self._secrets.read(found.meta_name))
        except AdapterError as failed:
            _read_failed(found, META_ENTRY, failed)
            updated_at = None
        return CredentialStatus(found, CredentialState.SET, updated_at)

    # Unsubscribe token keys (Story 3.8) -----------------------------------------------------

    def unsub_keys(self) -> tuple[UnsubKidKeys, ...]:
        """The active kid, the next one and every kid with a token, by kid; see the module
        docstring. Reads the database in the caller's unit of work, then the keyring."""
        active = check_kid(setup_queries.get(Key.UNSUB_ACTIVE_KID))
        newest = compliance_queries.newest_unsub_tokens()
        kids = {active, *newest}
        if active < MAX_KID:
            kids.add(active + 1)
        now = self._clock.now()
        with self._lock:
            return tuple(
                UnsubKidKeys(
                    kid,
                    _unsub_role(kid, active, newest.get(kid), now),
                    newest.get(kid),
                    tuple(self._unsub_status(found, kid) for found in declared_unsub_keys()),
                )
                for kid in sorted(kids)
            )

    def unsub_key_status(self, kid: int, key: str) -> UnsubKeyStatus:
        """The status of the unsubscribe-token key ``key`` of ``kid``; reads only the
        keyring."""
        found = unsub_key(key)
        kid = check_kid(kid)
        with self._lock:
            return self._unsub_status(found, kid)

    def save_unsub_key(self, kid: int, key: str, text: str) -> UnsubKeyStatus:
        """Store ``text``, a 32-byte base64url key, as ``key`` of ``kid``, with its time; see
        the module docstring."""
        found = unsub_key(key)
        kid = check_kid(kid)
        stripped = text.strip() if isinstance(text, str) else ""
        if not stripped:
            raise InvalidCredentialValue(found.key, ENTER_A_VALUE)
        raw = parse_key(stripped)
        if raw is None:
            raise InvalidCredentialValue(found.key, NOT_A_KEY)
        newest = compliance_queries.newest_unsub_token(kid)
        name = found.secret_name(kid)
        with self._lock:
            now = self._clock.now()
            try:
                stored = parse_key(self._secrets.read(name)) is not None  # the value isn't kept
                other = next(o for o in declared_unsub_keys() if o.key != found.key)
                other_raw = read_unsub_key(self._secrets, other.key, kid)
            except AdapterError as failed:
                _unsub_store_failed(found, kid, SAVE, failed)
            if other_raw is not None and hmac.compare_digest(other_raw, raw):
                raise InvalidCredentialValue(found.key, SAME_KEY)  # Cloudflare holds k_edge
            if stored and key_in_use(newest, now):
                raise UnsubKeyInUse(kid)
            stamp = encode_updated_at(now)
            try:
                self._secrets.write(name, encode_key(raw))
                self._secrets.write(found.meta_name(kid), stamp)
            except AdapterError as failed:
                _unsub_store_failed(found, kid, SAVE, failed)
            log.info(UNSUB_KEY_SAVED, extra={"credential": str(found.key), "kid": kid})
            return UnsubKeyStatus(found, kid, CredentialState.SET, decode_updated_at(stamp), True)

    def remove_unsub_key(self, kid: int, key: str) -> UnsubKeyStatus:
        """Delete ``key`` of ``kid`` and its time, unless ``kid`` has a token under 2 years old
        (``UnsubKeyInUse``); see the module docstring."""
        found = unsub_key(key)
        kid = check_kid(kid)
        newest = compliance_queries.newest_unsub_token(kid)
        with self._lock:
            if key_in_use(newest, self._clock.now()):
                raise UnsubKeyInUse(kid)
            try:
                self._secrets.delete(found.secret_name(kid))
                self._secrets.delete(found.meta_name(kid))
            except AdapterError as failed:
                _unsub_store_failed(found, kid, REMOVE, failed)
            log.info(UNSUB_KEY_REMOVED, extra={"credential": str(found.key), "kid": kid})
            return UnsubKeyStatus(found, kid, CredentialState.NOT_SET, None, False)

    def _unsub_status(self, found: UnsubKeyCredential, kid: int) -> UnsubKeyStatus:
        try:
            value = self._secrets.read(found.secret_name(kid))
        except AdapterError as failed:
            _unsub_read_failed(found, kid, VALUE_ENTRY, failed)
            return UnsubKeyStatus(found, kid, CredentialState.UNKNOWN, None, False)
        if value is None:
            return UnsubKeyStatus(found, kid, CredentialState.NOT_SET, None, False)
        valid = parse_key(value) is not None
        del value  # the value isn't kept
        try:
            updated_at = decode_updated_at(self._secrets.read(found.meta_name(kid)))
        except AdapterError as failed:
            _unsub_read_failed(found, kid, META_ENTRY, failed)
            updated_at = None
        return UnsubKeyStatus(found, kid, CredentialState.SET, updated_at, valid)


def _read_failed(found: Credential, entry: str, failed: AdapterError) -> None:
    log.warning(
        CREDENTIAL_READ_FAILED,
        extra={"credential": str(found.key), "entry": entry, "code": failed.code},
    )


def _store_failed(found: Credential, operation: str, failed: AdapterError) -> NoReturn:
    log.warning(
        CREDENTIAL_STORE_FAILED,
        extra={"credential": str(found.key), "operation": operation, "code": failed.code},
    )
    raise CredentialStoreFailed(found.key) from None


def _unsub_role(kid: int, active: int, newest: datetime | None, now: datetime) -> UnsubKeyRole:
    if kid == active:
        return UnsubKeyRole.ACTIVE
    if key_in_use(newest, now):  # before next: a lowered active kid's old one keeps its tokens
        return UnsubKeyRole.IN_USE
    return UnsubKeyRole.NEXT if kid == active + 1 else UnsubKeyRole.RETIRED


def _unsub_read_failed(
    found: UnsubKeyCredential, kid: int, entry: str, failed: AdapterError
) -> None:
    log.warning(
        UNSUB_KEY_READ_FAILED,
        extra={"credential": str(found.key), "kid": kid, "entry": entry, "code": failed.code},
    )


def _unsub_store_failed(
    found: UnsubKeyCredential, kid: int, operation: str, failed: AdapterError
) -> NoReturn:
    log.warning(
        UNSUB_KEY_STORE_FAILED,
        extra={
            "credential": str(found.key),
            "kid": kid,
            "operation": operation,
            "code": failed.code,
        },
    )
    raise CredentialStoreFailed(found.secret_name(kid)) from None


# Google OAuth ---------------------------------------------------------------------------------

GOOGLE_CLIENT_IMPORTED: Final = "google_client_imported"
GOOGLE_CLIENT_FILE_KEPT: Final = "google_client_file_kept"
GOOGLE_STORE_FAILED: Final = "google_store_failed"
GOOGLE_CONSENT_STARTED: Final = "google_consent_started"
GOOGLE_CONSENT_FAILED: Final = "google_consent_failed"
GOOGLE_GRANT_STORED: Final = "google_grant_stored"
GOOGLE_GRANT_REMOVED: Final = "google_grant_removed"
GOOGLE_TOKEN_REVOKED: Final = "google_token_revoked"
GOOGLE_REVOKE_FAILED: Final = "google_revoke_failed"
GOOGLE_REVOKE_SKIPPED: Final = "google_revoke_skipped"
GOOGLE_GRANT_NEEDS_RECONSENT: Final = "google_grant_needs_reconsent"
GOOGLE_CONSENT_CRASHED: Final = "google_consent_crashed"
GOOGLE_ALLOWLIST_UNREADABLE: Final = "google_allowlist_unreadable"

GOOGLE_CLIENT_KEY: Final = "google_client"  # CredentialStoreFailed's key for the client
SHARED: Final = "shared"  # the reason of google_revoke_skipped
CONSENT_WAIT_S: Final = CONSENT_TIMEOUT.total_seconds()  # 300.0
TOKEN_MARGIN: Final = timedelta(seconds=60)  # a cached token this close to expiry is renewed
FILE_REASONS: Final[Mapping[str, str]] = {
    CLIENT_FILE_MISSING: FILE_MISSING,
    CLIENT_FILE_NOT_REGULAR: FILE_NOT_REGULAR,
    CLIENT_FILE_TOO_LARGE: FILE_TOO_LARGE,
}  # any other file error code: FILE_UNREADABLE

Browser = Callable[[str], bool]
Listen = Callable[[], ConsentListener]
Allowlist = Callable[[], Iterable[str]]
Start = Callable[[Callable[[], None]], None]


def start_daemon(job: Callable[[], None]) -> None:
    """Run ``job`` on a new daemon thread."""
    threading.Thread(target=job, name="google-consent", daemon=True).start()


class Revocation(StrEnum):
    """What happened to a token that was let go."""

    REVOKED = "revoked"
    SHARED = "shared"  # not revoked: another stored grant holds the same Google account
    FAILED = "failed"  # Google (or Credential Manager) failed; revoke it by hand


class GrantState(StrEnum):
    """A Google grant's state, as Credential Manager answered."""

    NOT_GRANTED = "not granted"
    GRANTED = "granted"
    NEEDS_RECONSENT = "needs re-consent"
    UNKNOWN = "unknown"  # Credential Manager could not be read


@dataclass(frozen=True, slots=True)
class ConsentOutcome:
    """How a grant's last flow ended: ``failure`` is ``None`` when the grant was stored.
    ``revocation`` is what happened to the new token after a failure, or to the replaced token
    after a success (``None``: there was none). ``code`` is the error code that caused it."""

    failure: ConsentFailure | None
    revocation: Revocation | None = None
    code: str | None = None


@dataclass(frozen=True, slots=True)
class ClientStatus:
    """Whether the Desktop client is imported, and when."""

    state: CredentialState
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class ClientImport:
    """An import's result: the client's status and whether the file was deleted."""

    status: ClientStatus
    file_deleted: bool


@dataclass(frozen=True, slots=True)
class GrantStatus:
    """A grant as the Credentials page shows it."""

    spec: GrantSpec
    state: GrantState
    account: str | None
    granted_at: datetime | None
    needs_reconsent_at: datetime | None
    waiting_until: datetime | None
    outcome: ConsentOutcome | None


@dataclass(frozen=True, slots=True)
class GrantRemoval:
    """A removal's result: the grant's status after it, and what happened to its token."""

    status: GrantStatus
    revocation: Revocation | None


@dataclass(eq=False)
class _Flow:
    """One waiting consent flow; it never leaves this module."""

    spec: GrantSpec
    client: ClientConfig = field(repr=False)
    listener: ConsentListener
    state: str = field(repr=False)
    verifier: str = field(repr=False)
    redirect_uri: str
    waiting_until: datetime
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class _CachedToken:
    token: str = field(repr=False)
    expires_at: datetime


class GoogleAccess:
    """The Google Desktop client and the Google grants; see the module docstring."""

    def __init__(
        self,
        secrets: SecretsPort,
        clock: Clock,
        *,
        oauth: GoogleOAuthPort,
        listen: Listen,
        files: ClientFilePort,
        browser: Browser,
        env: str,
        allowlist: Allowlist,
        start: Start = start_daemon,
    ) -> None:
        self._secrets = secrets
        self._clock = clock
        self._oauth = oauth
        self._listen = listen
        self._files = files
        self._browser = browser
        self._env = env
        self._allowlist = allowlist
        self._start = start
        self._state_lock = threading.Lock()  # flows, outcomes, cached tokens; never over I/O
        self._store_lock = threading.Lock()  # keyring changes to grants; taken first
        self._flows: dict[GoogleGrant, _Flow] = {}
        self._outcomes: dict[GoogleGrant, ConsentOutcome] = {}
        self._tokens: dict[GoogleGrant, _CachedToken] = {}

    # The client ---------------------------------------------------------------------------

    def client_status(self) -> ClientStatus:
        """Whether ``google/client_config`` is set, and its import time."""
        try:
            present = self._secrets.read(CLIENT_SECRET_NAME) is not None  # not kept
        except AdapterError:
            return ClientStatus(CredentialState.UNKNOWN, None)
        if not present:
            return ClientStatus(CredentialState.NOT_SET, None)
        try:
            updated_at = decode_updated_at(self._secrets.read(CLIENT_META_NAME))
        except AdapterError:
            updated_at = None
        return ClientStatus(CredentialState.SET, updated_at)

    def import_client(self, path_text: str) -> ClientImport:
        """Store the Desktop client in the file at ``path_text``, then delete the file."""
        path = client_path(path_text)
        try:
            raw = self._files.read(path, MAX_CLIENT_FILE_BYTES)
        except AdapterError as failed:
            raise InvalidClientFile(FILE_REASONS.get(failed.code, FILE_UNREADABLE)) from None
        text = encode_client_config(parse_client_file(raw))
        if secret_length(text) > MAX_SECRET_LENGTH:
            raise InvalidClientFile(SECRET_TOO_LONG)
        with self._store_lock:
            stamp = encode_updated_at(self._clock.now())
            try:
                self._secrets.write(CLIENT_SECRET_NAME, text)
                self._secrets.write(CLIENT_META_NAME, stamp)
            except AdapterError as failed:
                log.warning(GOOGLE_STORE_FAILED, extra={"reason": "import", "code": failed.code})
                raise CredentialStoreFailed(GOOGLE_CLIENT_KEY) from None
        with self._state_lock:
            self._tokens.clear()
        log.info(GOOGLE_CLIENT_IMPORTED)
        try:
            self._files.delete(path)
            deleted = True
        except AdapterError as failed:
            deleted = False
            log.warning(GOOGLE_CLIENT_FILE_KEPT, extra={"code": failed.code})
        return ClientImport(
            ClientStatus(CredentialState.SET, decode_updated_at(stamp)), file_deleted=deleted
        )

    def _client(self) -> ClientConfig | None:
        return decode_client_config(self._secrets.read(CLIENT_SECRET_NAME))

    # Statuses -----------------------------------------------------------------------------

    def grant_statuses(self) -> tuple[GrantStatus, ...]:
        """Every grant's status, in ``GRANTS`` order."""
        return tuple(self._status(spec) for spec in GRANTS)

    def grant_status(self, grant: str) -> GrantStatus:
        """The status of ``grant``; raises ``UnknownGrant``."""
        return self._status(grant_spec(grant))

    def _status(self, spec: GrantSpec) -> GrantStatus:
        with self._state_lock:
            flow = self._flows.get(spec.grant)
            waiting_until = flow.waiting_until if flow is not None else None
            outcome = self._outcomes.get(spec.grant)

        def status(state: GrantState, meta: GrantMeta | None = None) -> GrantStatus:
            return GrantStatus(
                spec=spec,
                state=state,
                account=meta.account if meta else None,
                granted_at=meta.granted_at if meta else None,
                needs_reconsent_at=meta.needs_reconsent_at if meta else None,
                waiting_until=waiting_until,
                outcome=outcome,
            )

        try:
            present = self._secrets.read(spec.secret_name) is not None  # the token isn't kept
        except AdapterError:
            return status(GrantState.UNKNOWN)
        if not present:
            return status(GrantState.NOT_GRANTED)
        try:
            meta = decode_grant_meta(self._secrets.read(spec.meta_name))
        except AdapterError:
            meta = None
        if meta is not None and meta.needs_reconsent_at is not None:
            return status(GrantState.NEEDS_RECONSENT, meta)
        return status(GrantState.GRANTED, meta)

    # Starting a flow ----------------------------------------------------------------------

    def start_grant(self, grant: str) -> GrantStatus:
        """Start the consent flow of ``grant``; see the module docstring."""
        spec = grant_spec(grant)
        try:
            client = self._client()
        except AdapterError as failed:
            _google_store_failed(spec, "read_client", failed)
        if client is None:
            raise ClientNotImported()
        self._cancel(spec)
        try:
            listener = self._listen()
        except Exception as failed:  # noqa: BLE001 - any failure to listen is listener_failed
            code = failed.code if isinstance(failed, AdapterError) else None
            self._record(spec, ConsentOutcome(ConsentFailure.LISTENER_FAILED, code=code))
            return self._status(spec)
        try:
            flow = _Flow(
                spec=spec,
                client=client,
                listener=listener,
                state=token_urlsafe(32),
                verifier=token_urlsafe(64),
                redirect_uri=listener.redirect_uri(),
                waiting_until=self._clock.now() + CONSENT_TIMEOUT,
            )
        except Exception:  # noqa: BLE001 - the listener must not stay open
            listener.close()
            self._record(spec, ConsentOutcome(ConsentFailure.LISTENER_FAILED))
            return self._status(spec)
        with self._state_lock:
            # a concurrent Grant click may have registered a flow since _cancel: replace it too
            overwritten = self._flows.get(spec.grant)
            if overwritten is not None:
                overwritten.cancelled = True
            self._flows[spec.grant] = flow
            self._outcomes.pop(spec.grant, None)
        if overwritten is not None:
            overwritten.listener.close()
        url = authorization_url(
            client.client_id, spec, flow.redirect_uri, flow.state, code_challenge(flow.verifier)
        )
        try:
            opened = bool(self._browser(url))
        except Exception:  # noqa: BLE001 - any browser failure is browser_failed
            opened = False
        if not opened:
            self._abandon(flow, ConsentFailure.BROWSER_FAILED)
            return self._status(spec)
        log.info(GOOGLE_CONSENT_STARTED, extra={"grant": str(spec.grant)})
        try:
            self._start(functools.partial(self._run, flow))
        except Exception:  # noqa: BLE001 - no job, no flow
            log.error(GOOGLE_CONSENT_CRASHED, extra={"grant": str(spec.grant)})
            self._abandon(flow, ConsentFailure.CRASHED)
        return self._status(spec)

    def _record(self, spec: GrantSpec, outcome: ConsentOutcome) -> None:
        """Record ``outcome`` for a grant with no waiting flow."""
        with self._state_lock:
            self._outcomes[spec.grant] = outcome
        _log_failure(spec, outcome)

    def _cancel(self, spec: GrantSpec) -> None:
        """Mark the grant's waiting flow cancelled and close its listener."""
        with self._state_lock:
            flow = self._flows.pop(spec.grant, None)
            if flow is not None:
                flow.cancelled = True
        if flow is not None:
            flow.listener.close()

    def _abandon(self, flow: _Flow, failure: ConsentFailure) -> None:
        try:
            flow.listener.close()
        finally:
            self._finish(flow, ConsentOutcome(failure))

    def _finish(self, flow: _Flow, outcome: ConsentOutcome) -> None:
        """Record ``outcome`` as the grant's, unless ``flow`` was superseded meanwhile."""
        with self._state_lock:
            if self._flows.get(flow.spec.grant) is not flow:
                return  # superseded or cancelled: no outcome
            del self._flows[flow.spec.grant]
            self._outcomes[flow.spec.grant] = outcome
        _log_failure(flow.spec, outcome)

    # Completing a flow --------------------------------------------------------------------

    def _run(self, flow: _Flow) -> None:
        """The flow's background job: wait for Google's answer, close, complete."""
        try:
            try:
                query = flow.listener.wait(CONSENT_WAIT_S)
            finally:
                flow.listener.close()
            outcome = self._complete(flow, query)
        except Exception:  # noqa: BLE001 - completing never raises
            log.error(GOOGLE_CONSENT_CRASHED, extra={"grant": str(flow.spec.grant)})
            outcome = ConsentOutcome(ConsentFailure.CRASHED)
        if outcome is not None:
            self._finish(flow, outcome)

    def _complete(self, flow: _Flow, query: Query | None) -> ConsentOutcome | None:
        spec = flow.spec
        if query is None:
            return ConsentOutcome(ConsentFailure.TIMED_OUT)
        states = query.get("state", ())
        if len(states) != 1 or not hmac.compare_digest(
            states[0].encode("utf-8", "replace"), flow.state.encode("utf-8")
        ):
            return ConsentOutcome(ConsentFailure.STATE_MISMATCH)
        codes = query.get("code", ())
        if "error" in query or len(codes) != 1 or not codes[0]:
            return ConsentOutcome(ConsentFailure.DECLINED)
        try:
            tokens = self._oauth.exchange(flow.client, codes[0], flow.verifier, flow.redirect_uri)
        except AdapterError as failed:
            return ConsentOutcome(ConsentFailure.EXCHANGE_FAILED, code=failed.code)
        now = self._clock.now()
        account = id_token_account(tokens.id_token, flow.client.client_id, now)
        failure: ConsentFailure | None
        if account is None:
            failure = ConsentFailure.NO_ACCOUNT
        elif not scope_granted(tokens.scope, spec):
            failure = ConsentFailure.SCOPE_NOT_GRANTED
        elif tokens.refresh_token is None:
            failure = ConsentFailure.NO_REFRESH_TOKEN
        else:
            failure = account_problem(spec, account, self._env, self._allowlist_for(spec))
        if failure is not None or account is None:
            with self._store_lock:
                revocation = self._revoke(spec, _new_token(tokens), account, exclude=None)
            return ConsentOutcome(failure or ConsentFailure.NO_ACCOUNT, revocation)
        return self._store(flow, tokens, account, now)

    def _allowlist_for(self, spec: GrantSpec) -> tuple[str, ...]:
        if not sink_applies(self._env):
            return ()  # prod: no allowlist, and no database read
        try:
            entries = self._allowlist()
            if isinstance(entries, (str, bytes)):
                raise TypeError("the allowlist is a bare string")
            return tuple(entries)
        except Exception:  # noqa: BLE001 - unreadable counts as empty, so the grant is refused
            log.warning(GOOGLE_ALLOWLIST_UNREADABLE, extra={"grant": str(spec.grant)})
            return ()

    def _store(
        self, flow: _Flow, tokens: TokenSet, account: str, now: datetime
    ) -> ConsentOutcome | None:
        spec = flow.spec
        refresh_token = _new_token(tokens)  # _complete checked that it is the refresh token
        with self._store_lock:
            with self._state_lock:
                superseded = self._flows.get(spec.grant) is not flow
            if superseded:
                return None  # a newer flow (or a removal) owns the grant now: no outcome
            try:
                old_token = self._secrets.read(spec.secret_name)
                old_meta = self._secrets.read(spec.meta_name) if old_token is not None else None
            except AdapterError as failed:
                _log_store_failed(spec, "store", failed)
                revocation = self._revoke(spec, refresh_token, account, exclude=None)
                return ConsentOutcome(ConsentFailure.STORE_FAILED, revocation, failed.code)
            try:
                self._secrets.write(spec.secret_name, refresh_token)
                self._secrets.write(spec.meta_name, encode_grant_meta(GrantMeta(account, now)))
            except AdapterError as failed:
                _log_store_failed(spec, "store", failed)
                self._restore(spec, old_token, old_meta)
                revocation = self._revoke(spec, refresh_token, account, exclude=None)
                return ConsentOutcome(ConsentFailure.STORE_FAILED, revocation, failed.code)
            log.info(GOOGLE_GRANT_STORED, extra={"grant": str(spec.grant)})
            replaced = None
            if old_token is not None and old_token != refresh_token:
                old = decode_grant_meta(old_meta)
                old_account = old.account if old is not None else None
                replaced = self._revoke(spec, old_token, old_account, exclude=None)
            with self._state_lock:
                self._tokens[spec.grant] = _CachedToken(
                    tokens.access_token, now + timedelta(seconds=tokens.expires_in)
                )
        return ConsentOutcome(None, replaced)

    def _restore(self, spec: GrantSpec, old_token: str | None, old_meta: str | None) -> None:
        """Put back what a failed store replaced, best effort (the store lock is held)."""
        try:
            if old_token is None:
                self._secrets.delete(spec.secret_name)
            else:
                self._secrets.write(spec.secret_name, old_token)
                if old_meta is not None:
                    self._secrets.write(spec.meta_name, old_meta)
        except AdapterError:
            pass  # the row shows what is stored now

    def _held_by_a_grant(self, account: str, exclude: GoogleGrant | None) -> bool:
        """Whether a stored grant other than ``exclude`` holds ``account``."""
        for other in GRANTS:
            if other.grant == exclude or self._secrets.read(other.secret_name) is None:
                continue
            meta = decode_grant_meta(self._secrets.read(other.meta_name))
            if meta is not None and same_account(meta.account, account):
                return True
        return False

    def _revoke(
        self, spec: GrantSpec, token: str, account: str | None, *, exclude: GoogleGrant | None
    ) -> Revocation:
        """Revoke ``token`` unless a stored grant holds ``account`` (the store lock is held)."""
        grant = str(spec.grant)
        try:
            shared = account is not None and self._held_by_a_grant(account, exclude)
        except AdapterError as failed:
            log.warning(GOOGLE_REVOKE_FAILED, extra={"grant": grant, "code": failed.code})
            return Revocation.FAILED
        if shared:
            log.info(GOOGLE_REVOKE_SKIPPED, extra={"grant": grant, "reason": SHARED})
            return Revocation.SHARED
        try:
            self._oauth.revoke(token)
        except AdapterError as failed:
            log.warning(GOOGLE_REVOKE_FAILED, extra={"grant": grant, "code": failed.code})
            return Revocation.FAILED
        log.info(GOOGLE_TOKEN_REVOKED, extra={"grant": grant})
        return Revocation.REVOKED

    # Removing -----------------------------------------------------------------------------

    def remove_grant(self, grant: str) -> GrantRemoval:
        """Revoke and delete ``grant``; see the module docstring."""
        spec = grant_spec(grant)
        self._cancel(spec)
        with self._store_lock:
            try:
                token = self._secrets.read(spec.secret_name)
                meta = decode_grant_meta(self._secrets.read(spec.meta_name))
            except AdapterError as failed:
                _google_store_failed(spec, "remove", failed)
            revocation = None
            if token is not None:
                account = meta.account if meta is not None else None
                revocation = self._revoke(spec, token, account, exclude=spec.grant)
            with self._state_lock:
                self._tokens.pop(spec.grant, None)
                self._outcomes.pop(spec.grant, None)
            try:
                self._secrets.delete(spec.secret_name)
                self._secrets.delete(spec.meta_name)
            except AdapterError as failed:
                _google_store_failed(spec, "remove", failed)
        log.info(GOOGLE_GRANT_REMOVED, extra={"grant": str(spec.grant)})
        return GrantRemoval(self._status(spec), revocation)

    # Access tokens ------------------------------------------------------------------------

    def _stored_grant(self, spec: GrantSpec) -> tuple[ClientConfig, str]:
        """The stored client and refresh token of ``spec``'s grant, read without calling
        Google; raises (each an ``AdapterError`` that isn't retryable) when the client isn't
        imported, the grant isn't stored, or its meta says it needs re-consent, and passes on a
        Credential Manager failure."""
        client = self._client()
        if client is None:
            raise AdapterError(
                False, GOOGLE_CLIENT_NOT_IMPORTED, "the Google Desktop client is not imported"
            )
        token = self._secrets.read(spec.secret_name)
        if token is None:
            raise AdapterError(False, GOOGLE_NOT_GRANTED, f"Google {spec.grant} is not granted")
        meta = decode_grant_meta(self._secrets.read(spec.meta_name))
        if meta is not None and meta.needs_reconsent_at is not None:
            raise _needs_reconsent(spec)
        return client, token

    def check_stored_grant(self, grant: str) -> None:
        """Raise as ``access_token`` would before any refresh (client not imported, not
        granted, needs re-consent, or a Credential Manager failure); never calls Google, so
        nothing is refreshed, recorded or marked. Returns ``None`` when the grant is stored and
        usable as far as Credential Manager can tell."""
        self._stored_grant(grant_spec(grant))

    def access_token(self, grant: str) -> str:
        """A valid access token for ``grant``; see the module docstring."""
        spec = grant_spec(grant)
        now = self._clock.now()
        with self._state_lock:
            cached = self._tokens.get(spec.grant)
        if cached is not None and cached.expires_at - TOKEN_MARGIN > now:
            return cached.token
        client, token = self._stored_grant(spec)
        try:
            tokens = self._oauth.refresh(client, token)
        except AdapterError as failed:
            if failed.code != GOOGLE_INVALID_GRANT:
                raise
            self._mark_needs_reconsent(spec, token)
            raise _needs_reconsent(spec) from None
        with self._store_lock:
            try:
                still_stored = self._secrets.read(spec.secret_name) == token
            except AdapterError:
                still_stored = False  # the token is still good; it just isn't cached
            if still_stored:  # not replaced or removed meanwhile
                with self._state_lock:
                    self._tokens[spec.grant] = _CachedToken(
                        tokens.access_token, now + timedelta(seconds=tokens.expires_in)
                    )
        return tokens.access_token

    def _mark_needs_reconsent(self, spec: GrantSpec, failed_token: str) -> None:
        """Write ``needs_reconsent_at`` into the meta, while ``failed_token`` is still the
        stored one."""
        now = self._clock.now()
        with self._store_lock:
            try:
                if self._secrets.read(spec.secret_name) != failed_token:
                    return  # replaced or removed meanwhile: nothing to mark
                meta = decode_grant_meta(self._secrets.read(spec.meta_name))
                marked = replace(meta or GrantMeta(None, now), needs_reconsent_at=now)
                self._secrets.write(spec.meta_name, encode_grant_meta(marked))
            except AdapterError as failed:
                _log_store_failed(spec, "mark_reconsent", failed)
                return
            with self._state_lock:
                self._tokens.pop(spec.grant, None)
        log.warning(GOOGLE_GRANT_NEEDS_RECONSENT, extra={"grant": str(spec.grant)})


def _new_token(tokens: TokenSet) -> str:
    """The token to revoke when a new grant is let go: the refresh token, else the access
    token."""
    return tokens.refresh_token if tokens.refresh_token is not None else tokens.access_token


def _needs_reconsent(spec: GrantSpec) -> AdapterError:
    return AdapterError(False, GOOGLE_NEEDS_RECONSENT, f"Google {spec.grant} needs re-consent")


def _log_failure(spec: GrantSpec, outcome: ConsentOutcome) -> None:
    if outcome.failure is not None:
        extra: dict[str, str] = {"grant": str(spec.grant), "reason": str(outcome.failure)}
        if outcome.code is not None:
            extra["code"] = outcome.code
        log.warning(GOOGLE_CONSENT_FAILED, extra=extra)


def _log_store_failed(spec: GrantSpec, reason: str, failed: AdapterError) -> None:
    log.warning(
        GOOGLE_STORE_FAILED,
        extra={"grant": str(spec.grant), "reason": reason, "code": failed.code},
    )


def _google_store_failed(spec: GrantSpec, reason: str, failed: AdapterError) -> NoReturn:
    _log_store_failed(spec, reason, failed)
    raise CredentialStoreFailed(f"google_{spec.grant}") from None


# Erasure (Story 3.13) -------------------------------------------------------------------------

# What setup's erase_pii does to each of its tables with a personal-data column: none of them
# ever holds a Contact's data.
ERASURE: Final[Mapping[str, TableErasure]] = MappingProxyType(
    {
        "settings": TableErasure(
            Coverage.NOT_HELD,
            "Nextron's own settings: the Sender's details, staff addresses and the postal "
            "address, never a Contact's data",
        ),
    }
)


@command
def erase_pii(n: ContactErased) -> None:
    """``setup``'s ``ContactErased`` subscriber (Story 3.13, AD-17): it writes nothing,
    because no ``setup`` table holds a Contact's data (``ERASURE``)."""
