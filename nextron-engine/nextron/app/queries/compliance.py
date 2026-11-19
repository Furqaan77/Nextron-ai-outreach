"""Read API of the ``compliance`` service (AD-3, AD-16, AD-17; Stories 3.4, 3.5, 3.8, 3.12, 3.13,
3.14, 3.15 and 3.16).

Import rule: ``nextron.domain`` and ``nextron.db.repos.compliance`` only, plus
``nextron.app.queries.ops`` for the recovery row (Story 3.16). All read through the
current unit of work, read-only ones included; nothing here writes, caches or logs. Each listed
entry is a ``ListedEntry`` (``id``, ``kind``, ``value_hash``, ``domain_plain``, ``status``,
``source``, ``created_at``): never its ``cause_ref``, ``voided_at`` or ``void_reason``.

- ``entry_list(*, kind=None, source=None, status=None, page=1)`` (Story 3.5): one page of the
  Suppression List as an ``EntryListPage``: at most ``ENTRIES_PER_PAGE`` (50) entries, newest
  first (by ``created_at``, then ``id``, descending), the ``total`` that match, the ``page``
  shown and the number of ``pages`` (``max(1, ceil(total / 50))``). ``kind`` keeps the entries
  of that ``KeyKind``, ``source`` of that ``SuppressionSource`` and ``status`` of that
  ``EntryStatus``; ``None`` is any. A ``page`` past the last shows the last. A ``page`` that
  isn't an ``int`` (a ``bool`` included), or a filter that isn't its enum, raises
  ``TypeError``; a ``page`` below 1 raises ``ValueError``.
- ``entries(ids)`` (Story 3.5): the entries of ``ids``, in the order given, each once; an id
  that isn't a ULID, or that no entry has, is left out.
- ``active_entry(kind, value_hash)`` (Stories 3.4 and 3.5): the ``active`` entry of that key
  (``kind`` a ``KeyKind`` or its ``str`` value), or ``None``.
- ``active_keys(keys)``: the (``kind``, ``value_hash``) pairs of the ``SuppressionKey`` items in
  ``keys`` that have an ``active`` entry, as a ``frozenset``; a voided entry isn't active. An
  item that isn't a ``SuppressionKey`` raises ``TypeError`` before anything is read.
  ``compliance.assert_contactable`` passes it ``nextron.domain.contactability.lookup_keys``.
- ``matching(keys)`` (Story 9.8): every entry, ``active`` or ``voided``, whose (``kind``,
  ``value_hash``) is that of one of the ``SuppressionKey`` items in ``keys``, oldest first (by
  ``created_at``, then ``id``); an item that isn't a ``SuppressionKey`` raises ``TypeError``
  before anything is read. A Lead's history reads it for the keys of its Contacts and domain.
- ``caused_by_requests(contact_ids)`` (Story 9.8): every entry, ``active`` or ``voided``, whose
  ``cause_ref`` is the id of a data subject request of one of ``contact_ids`` (a DSR's own
  entries, whose person keys can't be derived once the Contact is erased), oldest first; an id
  that isn't a ULID is ignored.
- ``newest_unsub_token(kid) -> datetime | None``: when the newest unsubscribe token under
  ``kid`` was issued, or ``None`` when ``kid`` has none (Story 3.8).
- ``newest_unsub_tokens() -> Mapping[int, datetime]``: the same for every kid with a token, by
  kid.
- ``open_request(contact_id, kind, request_ref)`` (Story 3.13): the ``open`` data subject
  request of that Contact, kind and reference, as a ``StoredRequest``, or ``None``.
- ``completed_erasures(contact_ids) -> Mapping[str, StoredRequest]``: for each of those Contacts
  with a ``completed`` ``erase`` request, its first completed erase request (the one that erased
  it, or, after a retention erasure, the first request completed afterwards), by Contact id;
  the Lead page shows its reference on the erased Contact's row.

Story 3.12 adds the unsubscribe poll's state (FR-70, FR-58, AD-20). This module can't read the
``unsub_worker_host`` setting (``setup``'s), so its callers pass ``polling_on``: whether the host
is set.

- ``unsub_poll_status(polling_on) -> UnsubPollStatus(polling_on, state)``: ``state`` is the
  ``unsub_poll_state`` row as a ``PollState`` (``last_success_at``, ``failing_since``,
  ``last_failure_at``, ``last_failure_code``), or ``None`` before the first recorded poll.
- ``unsub_poll_issues(now, *, polling_on)``: one ``HealthIssue("unsub_poll_failing", "unsub_poll",
  failing_text(failing_since))`` while polling is on and
  ``nextron.domain.unsub_requests.polling_failing(...)`` holds at ``now``; else none. A blank
  host is "off", never an issue.

Story 3.15 adds the subject access exporter (``nextron.domain.contact_export``):

- ``export_pii(contact_id, value_hashes) -> {section: tuple[dict, ...]}``, read with generic
  selects, so a column a later story adds is exported without code:

  - ``suppression``: every entry, ``active`` or ``voided``, whose ``value_hash`` is one of
    ``value_hashes`` (``Compliance.export_contact`` passes ``contact_export.export_hashes``:
    AD-3 keeps this module from reading ``research``'s Contact) or whose ``cause_ref`` is the id
    of one of the Contact's requests, each showing only its ``kind``, ``status``, ``source``,
    ``created_at``, ``voided_at`` and ``void_reason``, by ``created_at``, then ``id``: never its
    id, hash, domain or cause. A ``domain`` entry's ``void_reason`` is always ``None`` (the key
    stays): a firm's domain entry is in every Contact's file at that firm, and its reason is the
    Operator's free text about whoever opted the firm out, who may be a colleague. A person's own
    entry keeps its reason;
  - ``requests``: every column of each of the Contact's data subject requests, by
    ``opened_at``, then ``id``;
  - ``erasure_receipts`` (Story 3.14): every column of each of the Contact's erasure receipts,
    oldest first, each with ``stores``, every column of its store rows (the store, action,
    status, detail and time: codes, counts, dates and ids only).

  An item of ``value_hashes`` that isn't a key hash (``contactability.is_hash``) is ignored; a
  ``contact_id`` that isn't a ULID has no requests.

Story 3.14:

- ``receipts(contact_ids) -> Mapping[str, tuple[StoredReceipt, ...]]``: each of those Contacts'
  erasure receipts, oldest first, each with its store rows in ``ErasureStore`` order, by
  Contact id (a Contact with none is left out). The Lead page and the erase step show them.
- ``health_issues()``: the erasures' health issues (``nextron.main.build_health_issues``
  registers it as the ``erasure`` source, before ``compliance``), oldest first: one ``erasure_open`` issue per
  ``open`` erase request (subject the request; "Erasure request still open: Contact <id>; erase
  it again from the Lead page"), then one ``erasure_failed`` issue per ``failed`` store row
  (subject the receipt; "Erasure failed: <store label>, Contact <id>, receipt <id>", detail the
  row's detail). There is no Dismiss: an open request clears when an erasure completes it, and a
  failed store stays (retrying or acknowledging one is deferred).
- ``pending_reapply() -> str | None`` (Story 3.16): the id of the ``on`` recovery row
  (``queries.ops.recovery_state``) while its ``reapply_erasure_ledger`` step is ``pending``,
  else ``None``.
- ``ledger_health_issues(ledger: Path)`` (Story 3.16; the health source ``compliance``): while
  ``pending_reapply()`` gives a row, one issue: ``erasure_ledger_missing`` ("Erasure ledger not
  found; sending stays blocked", detail "Put the ledger at <path> and restart the engine") when
  no file is at ``ledger``, else ``erasure_ledger_not_reapplied`` ("Erasure ledger not yet
  reapplied; sending stays blocked", detail "Restart the engine to retry; the log has
  recovery_step_failed"), each with the recovery row's id as its subject. Otherwise ``()``. It
  only checks whether the file exists; it never opens it.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from math import ceil
from pathlib import Path
from typing import Final

from nextron.app.queries import ops as ops_queries

from nextron.db.repos.compliance import data_subject_requests as requests_repo
from nextron.db.repos.compliance import erasure_receipts as receipts_repo
from nextron.db.repos.compliance import suppression_entries as entries_repo
from nextron.db.repos.compliance import unsub_poll_state as poll_state_repo
from nextron.db.repos.compliance import unsub_tokens as unsub_tokens_repo
from nextron.db.repos.compliance.data_subject_requests import StoredRequest
from nextron.db.repos.compliance.erasure_receipts import StoredReceipt, StoredReceiptStore
from nextron.db.repos.compliance.suppression_entries import ListedEntry
from nextron.db.repos.compliance.unsub_poll_state import PollState
from nextron.domain.contactability import KeyKind, SuppressionKey, is_hash
from nextron.domain.erasure_receipt import ErasureStore
from nextron.domain.health import HealthIssue
from nextron.domain.ids import is_ulid
from nextron.domain.recovery import RecoveryMode, StepStatus
from nextron.domain.suppression_entry import EntryStatus, SuppressionSource
from nextron.domain.unsub_requests import POLL_FAILING, failing_text, polling_failing

ERASURE_OPEN: Final = "erasure_open"
ERASURE_FAILED: Final = "erasure_failed"

__all__ = [
    "ENTRIES_PER_PAGE",
    "EntryListPage",
    "ListedEntry",
    "PollState",
    "StoredReceipt",
    "StoredReceiptStore",
    "StoredRequest",
    "UnsubPollStatus",
    "active_entry",
    "active_keys",
    "caused_by_requests",
    "completed_erasures",
    "entries",
    "entry_list",
    "export_pii",
    "health_issues",
    "ledger_health_issues",
    "matching",
    "newest_unsub_token",
    "newest_unsub_tokens",
    "open_request",
    "pending_reapply",
    "receipts",
    "unsub_poll_issues",
    "unsub_poll_status",
]

ENTRIES_PER_PAGE: Final = 50
ERASURE_LEDGER_MISSING: Final = "erasure_ledger_missing"
ERASURE_LEDGER_NOT_REAPPLIED: Final = "erasure_ledger_not_reapplied"
LEDGER_MISSING_TEXT: Final = "Erasure ledger not found; sending stays blocked"
LEDGER_NOT_REAPPLIED_TEXT: Final = "Erasure ledger not yet reapplied; sending stays blocked"
LEDGER_NOT_REAPPLIED_DETAIL: Final = (
    "Restart the engine to retry; the log has recovery_step_failed"
)
POLL_ISSUE_SUBJECT: Final = "unsub_poll"


@dataclass(frozen=True, slots=True)
class UnsubPollStatus:
    """The unsubscribe poll as the health panel shows it: whether the Worker host is set, and
    the recorded state (``None`` before the first recorded poll)."""

    polling_on: bool
    state: PollState | None


@dataclass(frozen=True, slots=True)
class EntryListPage:
    """One page of the Suppression List: its entries, how many match, the page shown (from 1)
    and the number of pages (at least 1)."""

    entries: tuple[ListedEntry, ...]
    total: int
    page: int
    pages: int


def entry_list(
    *,
    kind: KeyKind | None = None,
    source: SuppressionSource | None = None,
    status: EntryStatus | None = None,
    page: int = 1,
) -> EntryListPage:
    """One page of the entries that match the filters, newest first."""
    if not isinstance(page, int) or isinstance(page, bool):
        raise TypeError(f"page must be an int, not {type(page).__name__}")
    if page < 1:
        raise ValueError("page must be at least 1")
    for name, value, enum in (
        ("kind", kind, KeyKind),
        ("source", source, SuppressionSource),
        ("status", status, EntryStatus),
    ):
        if value is not None and not isinstance(value, enum):
            raise TypeError(f"{name} must be a {enum.__name__}, not {type(value).__name__}")
    stored_kind = None if kind is None else str(kind)
    stored_source = None if source is None else str(source)
    stored_status = None if status is None else str(status)
    total = entries_repo.count_listed(kind=stored_kind, source=stored_source, status=stored_status)
    pages = max(1, ceil(total / ENTRIES_PER_PAGE))
    shown = min(page, pages)
    rows = entries_repo.listed(
        kind=stored_kind,
        source=stored_source,
        status=stored_status,
        limit=ENTRIES_PER_PAGE,
        offset=(shown - 1) * ENTRIES_PER_PAGE,
    )
    return EntryListPage(entries=rows, total=total, page=shown, pages=pages)


def entries(ids: Iterable[str]) -> tuple[ListedEntry, ...]:
    """The entries of ``ids``, in the order given, each once."""
    wanted = list(dict.fromkeys(entry_id for entry_id in ids if is_ulid(entry_id)))
    found = {entry.id: entry for entry in entries_repo.by_ids(wanted)}
    return tuple(found[entry_id] for entry_id in wanted if entry_id in found)


def active_entry(kind: KeyKind | str, value_hash: str) -> ListedEntry | None:
    """The ``active`` entry of the key (``kind``, ``value_hash``), or ``None``."""
    stored = entries_repo.active_for(str(kind), value_hash)
    if stored is None:
        return None
    return ListedEntry(
        stored.id,
        stored.kind,
        stored.value_hash,
        stored.domain_plain,
        stored.status,
        stored.source,
        stored.created_at,
    )


def active_keys(keys: Iterable[SuppressionKey]) -> frozenset[tuple[str, str]]:
    """The (``kind``, ``value_hash``) pairs among ``keys`` that have an ``active`` entry."""
    pairs: list[tuple[str, str]] = []
    for key in keys:
        if not isinstance(key, SuppressionKey):
            raise TypeError(f"active_keys takes SuppressionKey items, not {type(key).__name__}")
        pair = (str(key.kind), key.value_hash)
        if pair not in pairs:
            pairs.append(pair)
    return frozenset(pair for pair in pairs if entries_repo.active_for(*pair) is not None)


def matching(keys: Iterable[SuppressionKey]) -> tuple[ListedEntry, ...]:
    """Every entry, of any status, of one of ``keys``, oldest first."""
    pairs: set[tuple[str, str]] = set()
    for key in keys:
        if not isinstance(key, SuppressionKey):
            raise TypeError(f"matching takes SuppressionKey items, not {type(key).__name__}")
        pairs.add((str(key.kind), key.value_hash))
    return entries_repo.matching(pairs)


def caused_by_requests(contact_ids: Iterable[str]) -> tuple[ListedEntry, ...]:
    """Every entry, of any status, caused by a data subject request of one of ``contact_ids``,
    oldest first."""
    wanted = [contact_id for contact_id in contact_ids if is_ulid(contact_id)]
    return entries_repo.for_cause_refs(requests_repo.ids_for_contacts(wanted))


def newest_unsub_token(kid: int) -> datetime | None:
    """When the newest unsubscribe token under ``kid`` was issued, or ``None``."""
    return unsub_tokens_repo.newest_issued_at(kid)


def newest_unsub_tokens() -> Mapping[int, datetime]:
    """``{kid: when its newest unsubscribe token was issued}`` for every kid with a token."""
    return unsub_tokens_repo.newest_by_kid()


def unsub_poll_status(polling_on: bool) -> UnsubPollStatus:
    """Whether polling is on (the caller read the host) and the recorded poll state."""
    return UnsubPollStatus(bool(polling_on), poll_state_repo.get())


def unsub_poll_issues(now: datetime, *, polling_on: bool) -> tuple[HealthIssue, ...]:
    """One ``unsub_poll_failing`` issue while polling is on and has failed for over 24 hours;
    else none."""
    if not polling_on:
        return ()
    state = poll_state_repo.get()
    if state is None or state.failing_since is None:
        return ()
    if not polling_failing(state.last_success_at, state.failing_since, now):
        return ()
    return (HealthIssue(POLL_FAILING, POLL_ISSUE_SUBJECT, failing_text(state.failing_since)),)


def open_request(contact_id: str, kind: str, request_ref: str) -> StoredRequest | None:
    """The ``open`` data subject request of the Contact with that kind and reference, or
    ``None``."""
    return requests_repo.open_for(contact_id, kind, request_ref)


def completed_erasures(contact_ids: Iterable[str]) -> Mapping[str, StoredRequest]:
    """``{contact_id: the first completed erase request}`` for each of ``contact_ids`` with
    one."""
    return requests_repo.completed_erasures(contact_ids)


def export_pii(
    contact_id: str, value_hashes: Iterable[str]
) -> dict[str, tuple[dict[str, object], ...]]:
    """The Contact's suppression facts and requests; see the module docstring."""
    requests = requests_repo.for_contact(contact_id) if is_ulid(contact_id) else ()
    hashes = {value for value in value_hashes if is_hash(value)}
    cause_refs = {str(request["id"]) for request in requests}
    entries = entries_repo.for_export(hashes, cause_refs)
    return {
        "suppression": tuple(
            {**entry, "void_reason": None} if entry["kind"] == str(KeyKind.DOMAIN) else entry
            for entry in entries
        ),
        "requests": requests,
        "erasure_receipts": receipts_repo.for_export(contact_id) if is_ulid(contact_id) else (),
    }


def receipts(contact_ids: Iterable[str]) -> Mapping[str, tuple[StoredReceipt, ...]]:
    """``{contact_id: its erasure receipts, oldest first}`` for each of ``contact_ids`` with
    one."""
    return receipts_repo.receipts_for(contact_ids)


def _label(store: str) -> str:
    try:
        return ErasureStore(store).label
    except ValueError:  # a code the domain no longer knows shows as itself
        return store


def health_issues() -> tuple[HealthIssue, ...]:
    """One ``erasure_open`` issue per open erase request, then one ``erasure_failed`` issue per
    failed store, each oldest first."""
    still_open = tuple(
        HealthIssue(
            ERASURE_OPEN,
            request.id,
            f"Erasure request still open: Contact {request.contact_id}; erase it again from "
            "the Lead page",
        )
        for request in requests_repo.open_erase_requests()
    )
    failed = tuple(
        HealthIssue(
            ERASURE_FAILED,
            row.receipt_id,
            f"Erasure failed: {_label(row.store)}, Contact {row.contact_id}, receipt "
            f"{row.receipt_id}",
            detail=row.detail,
        )
        for row in receipts_repo.failed_rows()
    )
    return still_open + failed
def pending_reapply() -> str | None:
    """The ``on`` recovery row's id while its erasure ledger step is ``pending``, else
    ``None``."""
    row = ops_queries.recovery_state()
    if row is None or row.mode != RecoveryMode.ON:
        return None
    if row.reapply_erasure_ledger != StepStatus.PENDING:
        return None
    return row.id


def ledger_health_issues(ledger: Path) -> tuple[HealthIssue, ...]:
    """The erasure ledger's issue while a restore's reapply is pending; see the module
    docstring."""
    recovery_id = pending_reapply()
    if recovery_id is None:
        return ()
    if not ledger.exists():
        return (
            HealthIssue(
                ERASURE_LEDGER_MISSING,
                recovery_id,
                LEDGER_MISSING_TEXT,
                detail=f"Put the ledger at {ledger} and restart the engine",
            ),
        )
    return (
        HealthIssue(
            ERASURE_LEDGER_NOT_REAPPLIED,
            recovery_id,
            LEDGER_NOT_REAPPLIED_TEXT,
            detail=LEDGER_NOT_REAPPLIED_DETAIL,
        ),
    )
