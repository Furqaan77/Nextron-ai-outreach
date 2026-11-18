"""The unsubscribe poll (FR-70, FR-41, FR-50, AD-10, AD-15, AD-16, AD-20; Stories 3.11 and
3.12): the ``poll_unsubscribes`` job's service, owned by ``compliance``.

``UnsubscribePolls(clock, compliance, port, notify, meter)`` brings the one-click opt-outs that
the unsubscribe Worker (Story 3.10) recorded back to the PC, through ``port`` (the
``UnsubscribePort``), and applies each through ``compliance.unsubscribe_by_token``. The Worker
keeps every request (KV lets each go after 30 days), so a request made while the PC was off
lands at the next poll, and the ``unsub_requests`` record makes each land once.

- **``@use_case poll() -> PollCounts | None``**:

  1. a transaction reads the ``unsub_worker_host`` setting. Blank (the default) means polling
     is off: it logs ``unsub_poll_skipped`` and returns ``None``, with no request and nothing
     recorded;
  2. outside any transaction, ``port.fetch_pending(host)``;
  3. one transaction calls ``compliance.unsubscribe_by_token(token, one_click, received_at)``
     for each entry, in order. When ``invalid + malformed`` isn't 0, it also raises the
     dashboard notification ``unsub_tokens_invalid`` (subject ``unsub_invalid:<UTC date>``, so
     at most one a day; its text holds only the count). The same transaction records the
     success and runs the KV check (below). A failure rolls every entry back, and the next
     poll applies them again, once;
  4. after the commit it logs ``unsub_poll_applied`` (``fetched``, ``applied``, ``duplicate``,
     ``invalid``, ``malformed``), and a WARNING ``unsub_tokens_invalid`` (``count``) when that
     count isn't 0, and returns the counts.

Invalid tokens and malformed entries are not recorded, so each poll tries them again while the
Worker keeps them: a token whose key is restored later still lands. The poll is never
budget-gated. Nothing logged, stored or notified holds a token, an address, an address hash, the
bearer or the host.

**Monitoring (Story 3.12; FR-70, FR-58, AD-15, AD-20, NFR-32).** Each poll's outcome is kept in
the one ``unsub_poll_state`` row (``nextron.db.repos.compliance.unsub_poll_state``):

- **Success**: step 3's transaction stores ``last_success_at`` = now and clears the failure
  columns; when a failure episode was open it also clears (``notify.clear``) that episode's
  ``unsub_poll_failing`` dashboard notification.
- **An ``AdapterError``** from step 2 or 3: the apply (if any) rolls back; then a transaction of
  its own keeps ``failing_since`` (or sets it to now), sets ``last_failure_at`` and
  ``last_failure_code`` (``nextron.domain.unsub_requests.failure_code``: a closed code is
  itself, ``credential_not_set`` is ``unsub_bearer_invalid``, anything else
  ``internal_error``) and, while ``polling_failing(...)`` holds, raises ``unsub_poll_failing``
  on Telegram and the dashboard with subject ``failing_subject(failing_since)``, so notify's
  permanent dedup makes it one alert per episode; its text holds the time and the code only.
  It logs ``unsub_poll_failed`` (WARNING, ``code``) and returns ``None``, so the job is
  ``done``: the next trigger firing is the retry, and no ``job_dead`` row is made. An
  ``AdapterError`` from the apply (the token codec's ``keyring_failed``, say) is recorded as
  ``internal_error``. ``unsub_unreachable`` is doctor's probe code only: ``fetch_pending``
  never raises it, so the poll never stores it.
- **Any other exception** is recorded the same way as ``internal_error`` and re-raised (a bug:
  the job retries and may die).
- When recording a failure itself fails, it logs ``unsub_poll_record_failed`` (ERROR,
  ``code``: the class name); an ``AdapterError`` still returns ``None`` and any other
  exception is still the one raised.
- **The KV check** ends every recording transaction: when today's (UTC) count of
  ``unsub_worker``/``kv_list`` calls (``meter.day_calls``, the ``api_usage`` rows) is 80% of
  the 1,000-a-day limit or more, it raises the dashboard notification ``kv_list_quota`` with
  subject ``kv_list:<YYYY-MM-DD>`` (one a UTC day) and the count. Polling never stops.

**The recovery step.** ``@use_case repolled(context) -> bool`` is the ``repoll_unsubscribes``
handler (``nextron.main.build_recovery_steps``): whether ``last_success_at`` is after
``context.restored_at``. The poll's trigger already fires at startup, and the Worker returns
every entry it still holds, so the dedup by ``token_hash`` re-applies whatever the restore
rolled back; the step enqueues nothing.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from nextron.app.compliance.api import Compliance
from nextron.app.notify.api import Notify
from nextron.app.queries import setup as setup_queries
from nextron.db.repos.compliance import unsub_poll_state as poll_state_repo
from nextron.db.repos.compliance.unsub_poll_state import PollState
from nextron.db.uow import current, use_case
from nextron.domain.alert import Channel
from nextron.domain.recovery import RecoveryContext
from nextron.domain.settings_schema import Key
from nextron.domain.suppression_entry import SuppressionSource
from nextron.domain.unsub_requests import (
    KV_LIST_OPERATION,
    KV_LIST_QUOTA,
    KV_PROVIDER,
    POLL_FAILING,
    PollFailure,
    UnsubOutcome,
    failing_alert_text,
    failing_subject,
    failure_code,
    kv_list_subject,
    kv_list_text,
    kv_list_warning,
    polling_failing,
)
from nextron.ports.clock import Clock
from nextron.ports.errors import AdapterError
from nextron.ports.unsubscribe import UnsubscribePort
from nextron.ports.usage_meter import UsageMeterPort

log = logging.getLogger(__name__)

POLL_SKIPPED: Final = "unsub_poll_skipped"
POLL_APPLIED: Final = "unsub_poll_applied"
POLL_FAILED: Final = "unsub_poll_failed"
RECORD_FAILED: Final = "unsub_poll_record_failed"
TOKENS_INVALID: Final = "unsub_tokens_invalid"  # the WARNING and the notification's kind
INVALID_SUBJECT_PREFIX: Final = "unsub_invalid:"
FAILING_CHANNELS: Final = (Channel.TELEGRAM, Channel.DASHBOARD)
DASHBOARD: Final = (Channel.DASHBOARD,)
NO_STATE: Final = PollState(None, None, None, None)


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
        self,
        clock: Clock,
        compliance: Compliance,
        port: UnsubscribePort,
        notify: Notify,
        meter: UsageMeterPort,
    ) -> None:
        self._clock = clock
        self._compliance = compliance
        self._port = port
        self._notify = notify
        self._meter = meter

    @use_case
    def poll(self) -> PollCounts | None:
        """Fetch the Worker's pending list, apply it and record the outcome; see the module
        docstring."""
        with current().transaction():  # step 1
            host = setup_queries.get(Key.UNSUB_WORKER_HOST)
        if not host:
            log.info(POLL_SKIPPED)
            return None
        try:
            counts = self._fetch_and_apply(host)
        except AdapterError as error:
            code = failure_code(error.code)
            self._record_failure_logged(code)
            log.warning(POLL_FAILED, extra={"code": str(code)})
            return None
        except Exception:
            self._record_failure_logged(PollFailure.INTERNAL_ERROR)
            raise
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

    def _fetch_and_apply(self, host: str) -> PollCounts:
        """Steps 2 and 3: the fetch outside any transaction, then the apply, the success record
        and the KV check in one transaction."""
        fetched = self._port.fetch_pending(host)
        outcomes = dict.fromkeys(UnsubOutcome, 0)
        with current().transaction():
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
            now = self._clock.now()
            if counts.failed:
                day = now.astimezone(UTC).date().isoformat()
                self._notify.alert(
                    TOKENS_INVALID,
                    f"{INVALID_SUBJECT_PREFIX}{day}",
                    invalid_text(counts.failed),
                    DASHBOARD,
                )
            self._record_success(now)
            self._check_kv(now)
        return counts

    def _record_success(self, now: datetime) -> None:
        """In the apply transaction: the poll worked, so any failure episode ends."""
        state = poll_state_repo.get() or NO_STATE
        if state.failing_since is not None:
            self._notify.clear(POLL_FAILING, failing_subject(state.failing_since))
        poll_state_repo.save(PollState(now, None, None, None))

    def _record_failure_logged(self, code: PollFailure) -> None:
        """``_record_failure``; a failure to record is logged, never raised."""
        try:
            self._record_failure(code)
        except Exception as recording:  # noqa: BLE001 - the poll's own outcome is what matters
            log.error(RECORD_FAILED, extra={"code": type(recording).__name__})

    def _record_failure(self, code: PollFailure) -> None:
        """In a transaction of its own: the failure, its alert while the episode is failing,
        and the KV check."""
        with current().transaction():
            now = self._clock.now()
            state = poll_state_repo.get() or NO_STATE
            since = state.failing_since if state.failing_since is not None else now
            poll_state_repo.save(PollState(state.last_success_at, since, now, str(code)))
            if polling_failing(state.last_success_at, since, now):
                self._notify.alert(
                    POLL_FAILING,
                    failing_subject(since),
                    failing_alert_text(since, str(code)),
                    FAILING_CHANNELS,
                )
            self._check_kv(now)

    def _check_kv(self, now: datetime) -> None:
        """Today's KV list calls: one dashboard notice a UTC day at 80% of the limit."""
        count = self._meter.day_calls(now, KV_PROVIDER, KV_LIST_OPERATION)
        if kv_list_warning(count):
            self._notify.alert(KV_LIST_QUOTA, kv_list_subject(now), kv_list_text(count), DASHBOARD)

    @use_case
    def repolled(self, context: RecoveryContext) -> bool:
        """The ``repoll_unsubscribes`` recovery step: whether a poll succeeded after the
        restore."""
        with current().transaction():
            state = poll_state_repo.get()
        return (
            state is not None
            and state.last_success_at is not None
            and state.last_success_at > context.restored_at
        )
