"""The unsubscribe poll (FR-70, FR-41, FR-50, AD-10, AD-15, AD-16, AD-20; Story 3.11): the
``poll_unsubscribes`` job's service, owned by ``compliance``.

``UnsubscribePolls(clock, compliance, port, notify)`` brings the one-click opt-outs that the
unsubscribe Worker (Story 3.10) recorded back to the PC, through ``port`` (the
``UnsubscribePort``), and applies each through ``compliance.unsubscribe_by_token``. The Worker
keeps every request (KV lets each go after 30 days), so a request made while the PC was off
lands at the next poll, and the ``unsub_requests`` record makes each land once.

- **``@use_case poll() -> PollCounts | None``**:

  1. a transaction reads the ``unsub_worker_host`` setting. Blank (the default) means polling
     is off: it logs ``unsub_poll_skipped`` and returns ``None``, with no request;
  2. outside any transaction, ``port.fetch_pending(host)``. An ``AdapterError`` propagates, so
     nothing is applied and the job retries a retryable one;
  3. one transaction calls ``compliance.unsubscribe_by_token(token, one_click, received_at)``
     for each entry, in order. When ``invalid + malformed`` isn't 0, it also raises the
     dashboard notification ``unsub_tokens_invalid`` (subject ``unsub_invalid:<UTC date>``, so
     at most one a day; its text holds only the count). A failure rolls every entry back, and
     the next poll applies them again, once;
  4. after the commit it logs ``unsub_poll_applied`` (``fetched``, ``applied``, ``duplicate``,
     ``invalid``, ``malformed``), and a WARNING ``unsub_tokens_invalid`` (``count``) when that
     count isn't 0, and returns the counts.

Invalid tokens and malformed entries are not recorded, so each poll tries them again while the
Worker keeps them: a token whose key is restored later still lands. The poll is never
budget-gated. Nothing logged, stored or notified holds a token, an address, an address hash, the
bearer or the host.
"""

import logging
from dataclasses import dataclass
from datetime import UTC
from typing import Final

from nextron.app.compliance.api import Compliance
from nextron.app.notify.api import Notify
from nextron.app.queries import setup as setup_queries
from nextron.db.uow import current, use_case
from nextron.domain.alert import Channel
from nextron.domain.settings_schema import Key
from nextron.domain.suppression_entry import SuppressionSource
from nextron.domain.unsub_requests import UnsubOutcome
from nextron.ports.clock import Clock
from nextron.ports.unsubscribe import UnsubscribePort

log = logging.getLogger(__name__)

POLL_SKIPPED: Final = "unsub_poll_skipped"
POLL_APPLIED: Final = "unsub_poll_applied"
TOKENS_INVALID: Final = "unsub_tokens_invalid"  # the WARNING and the notification's kind
INVALID_SUBJECT_PREFIX: Final = "unsub_invalid:"


@dataclass(frozen=True, slots=True)
class PollCounts:
    """What one poll fetched and what became of each entry."""

    fetched: int
    applied: int
    duplicate: int
    invalid: int
    malformed: int

    @property
    def failed(self) -> int:
        """The requests that couldn't be applied: invalid tokens and malformed entries."""
        return self.invalid + self.malformed


def invalid_text(count: int) -> str:
    """The notification's text: the count, nothing else."""
    return (
        f"{count} unsubscribe requests from the Worker could not be decoded and were not "
        "applied; check the unsubscribe token keys"
    )


class UnsubscribePolls:
    """The ``poll_unsubscribes`` job's service; see the module docstring."""

    def __init__(
        self, clock: Clock, compliance: Compliance, port: UnsubscribePort, notify: Notify
    ) -> None:
        self._clock = clock
        self._compliance = compliance
        self._port = port
        self._notify = notify

    @use_case
    def poll(self) -> PollCounts | None:
        """Fetch the Worker's pending list and apply it; see the module docstring."""
        uow = current()
        with uow.transaction():  # step 1
            host = setup_queries.get(Key.UNSUB_WORKER_HOST)
        if not host:
            log.info(POLL_SKIPPED)
            return None
        fetched = self._port.fetch_pending(host)  # step 2: no transaction is open
        outcomes = dict.fromkeys(UnsubOutcome, 0)
        with uow.transaction():  # step 3
            for entry in fetched.entries:
                outcome = self._compliance.unsubscribe_by_token(
                    entry.token, SuppressionSource.ONE_CLICK, entry.received_at
                )
                outcomes[outcome] += 1
            counts = PollCounts(
                fetched=len(fetched.entries) + fetched.malformed,
                applied=outcomes[UnsubOutcome.APPLIED],
                duplicate=outcomes[UnsubOutcome.DUPLICATE],
                invalid=outcomes[UnsubOutcome.INVALID],
                malformed=fetched.malformed,
            )
            if counts.failed:
                day = self._clock.now().astimezone(UTC).date().isoformat()
                self._notify.alert(
                    TOKENS_INVALID,
                    f"{INVALID_SUBJECT_PREFIX}{day}",
                    invalid_text(counts.failed),
                    (Channel.DASHBOARD,),
                )
        log.info(  # step 4: after the commit
            POLL_APPLIED,
            extra={
                "fetched": counts.fetched,
                "applied": counts.applied,
                "duplicate": counts.duplicate,
                "invalid": counts.invalid,
                "malformed": counts.malformed,
            },
        )
        if counts.failed:
            log.warning(TOKENS_INVALID, extra={"count": counts.failed})
        return counts
