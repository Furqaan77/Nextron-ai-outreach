"""Read API of the ``compliance`` service (AD-3, AD-16, AD-17; Stories 3.4, 3.8, 3.13 and 3.14).

Import rule: ``nextron.domain`` and ``nextron.db.repos.compliance`` only. All read through the
current unit of work, read-only ones included; nothing here writes, caches or logs.

- ``active_entry(kind, value_hash)``: the ``active`` Suppression List entry of that key (a
  ``StoredSuppressionEntry``), or ``None``.
- ``active_keys(keys)``: the (``kind``, ``value_hash``) pairs of the ``SuppressionKey`` items in
  ``keys`` that have an ``active`` entry, as a ``frozenset``; a voided entry isn't active. An
  item that isn't a ``SuppressionKey`` raises ``TypeError`` before anything is read.
  ``compliance.assert_contactable`` passes it ``nextron.domain.contactability.lookup_keys``.
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

Story 3.14:

- ``receipts(contact_ids) -> Mapping[str, tuple[StoredReceipt, ...]]``: each of those Contacts'
  erasure receipts, oldest first, each with its store rows in ``ErasureStore`` order, by
  Contact id (a Contact with none is left out). The Lead page and the erase step show them.
- ``health_issues()``: the erasures' health issues (``nextron.main.build_health_issues``
  registers it last, as the ``erasure`` source), oldest first: one ``erasure_open`` issue per
  ``open`` erase request (subject the request; "Erasure request still open: Contact <id>; erase
  it again from the Lead page"), then one ``erasure_failed`` issue per ``failed`` store row
  (subject the receipt; "Erasure failed: <store label>, Contact <id>, receipt <id>", detail the
  row's detail). There is no Dismiss: an open request clears when an erasure completes it, and a
  failed store stays (retrying or acknowledging one is deferred).
"""

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Final

from nextron.db.repos.compliance import data_subject_requests as requests_repo
from nextron.db.repos.compliance import erasure_receipts as receipts_repo
from nextron.db.repos.compliance import suppression_entries as entries_repo
from nextron.db.repos.compliance import unsub_tokens as unsub_tokens_repo
from nextron.db.repos.compliance.data_subject_requests import StoredRequest
from nextron.db.repos.compliance.erasure_receipts import StoredReceipt, StoredReceiptStore
from nextron.db.repos.compliance.suppression_entries import StoredSuppressionEntry
from nextron.domain.contactability import SuppressionKey
from nextron.domain.erasure_receipt import ErasureStore
from nextron.domain.health import HealthIssue

ERASURE_OPEN: Final = "erasure_open"
ERASURE_FAILED: Final = "erasure_failed"

__all__ = [
    "StoredReceipt",
    "StoredReceiptStore",
    "StoredRequest",
    "StoredSuppressionEntry",
    "active_entry",
    "active_keys",
    "completed_erasures",
    "health_issues",
    "newest_unsub_token",
    "newest_unsub_tokens",
    "open_request",
    "receipts",
]


def active_entry(kind: str, value_hash: str) -> StoredSuppressionEntry | None:
    """The ``active`` entry of the key (``kind``, ``value_hash``), or ``None``."""
    return entries_repo.active_for(kind, value_hash)


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


def newest_unsub_token(kid: int) -> datetime | None:
    """When the newest unsubscribe token under ``kid`` was issued, or ``None``."""
    return unsub_tokens_repo.newest_issued_at(kid)


def newest_unsub_tokens() -> Mapping[int, datetime]:
    """``{kid: when its newest unsubscribe token was issued}`` for every kid with a token."""
    return unsub_tokens_repo.newest_by_kid()


def open_request(contact_id: str, kind: str, request_ref: str) -> StoredRequest | None:
    """The ``open`` data subject request of the Contact with that kind and reference, or
    ``None``."""
    return requests_repo.open_for(contact_id, kind, request_ref)


def completed_erasures(contact_ids: Iterable[str]) -> Mapping[str, StoredRequest]:
    """``{contact_id: the first completed erase request}`` for each of ``contact_ids`` with
    one."""
    return requests_repo.completed_erasures(contact_ids)


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
