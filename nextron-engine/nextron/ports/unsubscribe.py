"""The ``UnsubscribePort`` (FR-70, AD-9, AD-10, AD-15; Story 3.11): the unsubscribe Worker's
pending list.

``nextron.adapters.unsubscribe.WorkerUnsubscribe`` implements it over ``GET https://<host>/pending``
(Story 3.10's Worker), metered; tests use ``tests.fakes.unsubscribe.ScriptedUnsubscribe``.

- ``PendingUnsub(token, received_at)``: one request the Worker recorded. ``token`` is a ``str``
  (left out of the ``repr``) and ``received_at`` an aware datetime in UTC; a wrong type raises
  ``TypeError`` and a naive time ``ValueError``.
- ``PendingFetch(entries, malformed)``: the well-formed entries, in the Worker's order, and how
  many entries were skipped as malformed.
- ``fetch_pending(host) -> PendingFetch``: every entry the Worker at ``host`` still holds (the
  Worker never deletes one; KV lets each go after 30 days), so the same token comes back on each
  poll until then.
- ``probe(host, *, deadline_s) -> None`` (Story 3.12, ``nextron doctor``): one request like
  ``fetch_pending``'s, with the same refusals, headers and metering, that returns when the
  Worker answers well within ``deadline_s`` seconds. A good answer slower than that, or no
  answer by then, raises ``unsub_unreachable`` (retryable).

A failed call raises ``AdapterError`` (``nextron.ports.errors``): ``unsub_unavailable`` and
``unsub_unreachable`` are retryable; ``unsub_refused``, ``unsub_bearer_invalid``,
``unsub_unauthorized`` and ``unsub_bad_response`` aren't, nor are the credential codes
(``credential_not_set``, ``keyring_failed``). No message holds the bearer, a token, the host or
a body.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final, Protocol, runtime_checkable

UNSUB_REFUSED: Final = "unsub_refused"
UNSUB_BEARER_INVALID: Final = "unsub_bearer_invalid"
UNSUB_UNAUTHORIZED: Final = "unsub_unauthorized"
UNSUB_UNAVAILABLE: Final = "unsub_unavailable"
UNSUB_BAD_RESPONSE: Final = "unsub_bad_response"
UNSUB_UNREACHABLE: Final = "unsub_unreachable"  # Story 3.12: the probe's answer was too slow


@dataclass(frozen=True, slots=True)
class PendingUnsub:
    """One pending unsubscribe request: its token and when the Worker received it (UTC)."""

    token: str = field(repr=False)
    received_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.token, str):
            raise TypeError(f"PendingUnsub.token must be a str, not {type(self.token).__name__}")
        if not isinstance(self.received_at, datetime):
            raise TypeError(
                "PendingUnsub.received_at must be a datetime, not "
                f"{type(self.received_at).__name__}"
            )
        if self.received_at.tzinfo is None or self.received_at.utcoffset() is None:
            raise ValueError("PendingUnsub.received_at must be timezone-aware")
        if self.received_at.tzinfo is not UTC:
            object.__setattr__(self, "received_at", self.received_at.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class PendingFetch:
    """The well-formed pending entries, in order, and how many were malformed."""

    entries: tuple[PendingUnsub, ...]
    malformed: int


@runtime_checkable
class UnsubscribePort(Protocol):
    """Reads the unsubscribe Worker's pending list."""

    def fetch_pending(self, host: str) -> PendingFetch:
        """Every pending request the Worker at ``host`` holds."""
        ...

    def probe(self, host: str, *, deadline_s: float) -> None:
        """Return when the Worker at ``host`` answers well within ``deadline_s`` seconds."""
        ...
