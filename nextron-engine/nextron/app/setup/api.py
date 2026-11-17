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

Both log ``settings_changed`` with ``keys``, the changed key names comma-joined, when at least
one key changed; never a value.

``Setup.sync_segments(segments)`` (FR-1, AD-12, Story 2.1; ``nextron seed sync``) upserts the
Segments of ``seed/segments.yaml``, as ``nextron.domain.segments.SegmentDef``s, by key into
the ``segments`` table. Before any transaction it refuses an item that isn't a ``SegmentDef``
(``TypeError``) or a repeated key (``ValueError``), and validates the definitions again as the
file form (``validate_segments``, when there is at least one), so a hand-built ``SegmentDef``
that breaks a rule raises ``InvalidSegments`` and nothing is stored. It reads the clock once,
then works in one transaction. Before any write it decodes each stored Segment the input leaves
out (``segment_from_text``) and raises ``InvalidSegments`` for the first input signal-type or
disqualifier key, in file order, that such a Segment still uses (say, after a Segment's key
was renamed in the file), naming the input field (``segments[<key>].signal_types[<key>].key``:
"is also a key of the stored Segment <key>, which is no longer in the file: rename one of
them"), so keys stay unique across every stored Segment; nothing is written. Then, in input
order: a key with no row is inserted at version 1 with one ``events`` row
(entity ``segments``, ``None`` to ``v1``, cause ``seed_sync``, actor ``seed``); a row whose
``definition`` differs from ``definition_text(segment)`` is updated to version n+1 with one
``events`` row ``v<n>`` to ``v<n+1>``; anything else writes nothing. A stored key missing from
the input is never changed or deleted (Leads may point to it). A sync enqueues no job,
publishes no notification and touches no other table, so it never re-scores a Lead. It returns
``SegmentSync(created, changed, unchanged, not_in_file)``: ``(key, version)``,
``(key, from_version, to_version)`` and keys in input order, and ``(key, version)`` of each
stored Segment not in the input, by key. After the commit it logs ``segments_synced`` (INFO)
with the four counts: ``created_count``, ``changed_count``, ``unchanged_count`` and
``not_in_file_count`` (``created`` alone is a ``LogRecord`` attribute, which ``extra`` may not
overwrite).

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

``Setup.sync_seed(segments, markets)`` is what ``nextron seed sync`` calls (a unit of work runs
one use case): every pre-transaction check of both syncs (Segments first), the clock read once,
then both syncs in **one** transaction, Segments first, so a failure anywhere changes nothing.
After the commit it logs ``segments_synced``, then ``markets_synced``, and returns
``SeedSync(segments, markets)``.

``Credentials`` (AD-12, FR-56, NFR-18) stores the external credentials declared in
``nextron.domain.credentials`` in Windows Credential Manager through ``SecretsPort``. It touches
no database, so its methods are not ``@use_case``s and need no unit of work; one
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
"""

import functools
import hmac
import json
import logging
import threading
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from secrets import token_urlsafe
from typing import Final, NoReturn

from nextron.db import eventlog
from nextron.db.repos.setup import markets as markets_repo
from nextron.db.repos.setup import segments as segments_repo
from nextron.db.repos.setup import settings as settings_repo
from nextron.db.uow import current, use_case
from nextron.domain import credentials as credential_registry
from nextron.domain import settings_schema
from nextron.domain.credentials import (
    Credential,
    InvalidCredentialValue,
    decode_updated_at,
    encode_updated_at,
)
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
from nextron.domain.markets import (
    InvalidMarkets,
    MarketDef,
    market_form,
    validate_markets,
)
from nextron.domain.segments import (
    InvalidSegments,
    SegmentDef,
    definition_text,
    segment_from_text,
    validate_segments,
)
from nextron.domain.settings_schema import (
    REQUIRED,
    REQUIRED_RULE,
    InvalidSettings,
    InvalidSettingValue,
)
from nextron.domain.sink import sink_applies
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
    secret_length,
)

SETTINGS_CHANGED: Final = "settings_changed"
CANNOT_CLEAR: Final = "is required and can't be cleared once set"
ENTER_A_VALUE: Final = "enter a value"
CREDENTIAL_SAVED: Final = "credential_saved"
CREDENTIAL_REMOVED: Final = "credential_removed"
CREDENTIAL_STORE_FAILED: Final = "credential_store_failed"
CREDENTIAL_READ_FAILED: Final = "credential_read_failed"
SEGMENTS_SYNCED: Final = "segments_synced"
SEGMENTS_ENTITY: Final = "segments"
MARKETS_SYNCED: Final = "markets_synced"
MARKETS_ENTITY: Final = "markets"
SEED_SYNC_CAUSE: Final = "seed_sync"
SEED_ACTOR: Final = "seed"
SAVE: Final = "save"
REMOVE: Final = "remove"
VALUE_ENTRY: Final = "value"  # the credential's own keyring entry
META_ENTRY: Final = "meta"  # its meta/<name> entry

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SegmentSync:
    """What ``sync_segments`` did: ``created`` as ``(key, version)``, ``changed`` as ``(key,
    from_version, to_version)`` and ``unchanged`` keys, each in input order, and
    ``not_in_file`` as ``(key, version)`` of each stored Segment the input left out, by key."""

    created: tuple[tuple[str, int], ...]
    changed: tuple[tuple[str, int, int], ...]
    unchanged: tuple[str, ...]
    not_in_file: tuple[tuple[str, int], ...]


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
class SeedSync:
    """What ``sync_seed`` did: the Segments' sync, then the Markets'."""

    segments: SegmentSync
    markets: MarketSync


def _log_changed(keys: list[str]) -> None:
    if keys:
        log.info(SETTINGS_CHANGED, extra={"keys": ",".join(keys)})


class Setup:
    """Operator settings, one typed value at a time or a whole Settings page at once, and the
    Segments and Markets a seed sync loads."""

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
            settings_repo.upsert(name, text, now)
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
            if problems:
                raise InvalidSettings(problems)
            for name in deletes:
                settings_repo.delete(name)
            for name, canonical in writes.items():
                settings_repo.upsert(name, canonical, now)
            after = settings_repo.stored_texts()
        _log_changed(changed)
        return after

    @use_case
    def sync_segments(self, segments: Sequence[SegmentDef]) -> SegmentSync:
        """Upsert ``segments`` by key; see the module docstring."""
        chosen, texts = _segment_inputs(segments)
        now = self._clock.now()
        with current().transaction():
            result = _sync_segments(chosen, texts, now)
        _log_sync(SEGMENTS_SYNCED, result)
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

    @use_case
    def sync_seed(
        self, segments: Sequence[SegmentDef], markets: Sequence[MarketDef]
    ) -> SeedSync:
        """Upsert ``segments`` and ``markets`` in one transaction; see the module docstring."""
        chosen_segments, texts = _segment_inputs(segments)
        chosen_markets = _market_inputs(markets)
        now = self._clock.now()
        with current().transaction():
            segment_result = _sync_segments(chosen_segments, texts, now)
            market_result = _sync_markets(chosen_markets, now)
        _log_sync(SEGMENTS_SYNCED, segment_result)
        _log_sync(MARKETS_SYNCED, market_result)
        return SeedSync(segment_result, market_result)


def _log_sync(event: str, result: SegmentSync | MarketSync) -> None:
    log.info(  # "created" itself is a LogRecord attribute, so each count is suffixed
        event,
        extra={
            "created_count": len(result.created),
            "changed_count": len(result.changed),
            "unchanged_count": len(result.unchanged),
            "not_in_file_count": len(result.not_in_file),
        },
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
    """The Segments' upsert, inside the running use case's transaction."""
    created: list[tuple[str, int]] = []
    changed: list[tuple[str, int, int]] = []
    unchanged: list[str] = []
    keys = {segment.key for segment in chosen}
    left_out = [row for row in segments_repo.all_segments() if row.key not in keys]
    _refuse_shared_keys(chosen, left_out)  # before any write
    for segment, text in zip(chosen, texts, strict=True):
        stored = segments_repo.by_key(segment.key)
        if stored is None:
            segment_id = segments_repo.insert(segment.key, text, now)
            _record_segment(segment_id, None, segments_repo.FIRST_VERSION, now)
            created.append((segment.key, segments_repo.FIRST_VERSION))
        elif stored.definition != text:
            version = stored.version + 1
            segments_repo.update(stored.id, text, version, now)
            _record_segment(stored.id, stored.version, version, now)
            changed.append((segment.key, stored.version, version))
        else:
            unchanged.append(segment.key)
    not_in_file = tuple((row.key, row.version) for row in left_out)
    return SegmentSync(tuple(created), tuple(changed), tuple(unchanged), not_in_file)


def _refuse_shared_keys(
    segments: Sequence[SegmentDef], left_out: Sequence[segments_repo.StoredSegment]
) -> None:
    """Signal-type and disqualifier keys stay unique across every stored Segment: raise
    ``InvalidSegments`` for the first input key, in file order, that a stored Segment the input
    leaves out still uses (a Segment renamed in the file, say)."""
    owners: dict[tuple[str, str], str] = {}
    for row in left_out:
        definition = segment_from_text(row.definition)
        for disqualifier in definition.disqualifiers:
            owners.setdefault(("disqualifiers", disqualifier.key), row.key)
        for signal_type in definition.signal_types:
            owners.setdefault(("signal_types", signal_type.key), row.key)
    for segment in segments:
        used = [("disqualifiers", d.key) for d in segment.disqualifiers]
        used += [("signal_types", t.key) for t in segment.signal_types]
        for field_name, key in used:
            owner = owners.get((field_name, key))
            if owner is not None:
                raise InvalidSegments(
                    f"segments[{segment.key}].{field_name}[{key}].key",
                    f"is also a key of the stored Segment {owner}, which is no longer in the "
                    "file: rename one of them",
                )


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
