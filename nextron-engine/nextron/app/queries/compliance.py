"""Read API of the ``compliance`` service (AD-3, AD-16, AD-17; Stories 3.4, 3.8 and 3.13).

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
"""

from collections.abc import Iterable, Mapping
from datetime import datetime

from nextron.db.repos.compliance import data_subject_requests as requests_repo
from nextron.db.repos.compliance import suppression_entries as entries_repo
from nextron.db.repos.compliance import unsub_tokens as unsub_tokens_repo
from nextron.db.repos.compliance.data_subject_requests import StoredRequest
from nextron.db.repos.compliance.suppression_entries import StoredSuppressionEntry
from nextron.domain.contactability import SuppressionKey

__all__ = [
    "StoredRequest",
    "StoredSuppressionEntry",
    "active_entry",
    "active_keys",
    "completed_erasures",
    "newest_unsub_token",
    "newest_unsub_tokens",
    "open_request",
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
