"""Public command API of the ``sending`` service (AD-3, AD-4, AD-14, AD-21, FR-32).

Other services and the delivery layers change ``sending`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back.

``Sending(clock, *, env, gap_source=None, domain_checks=None, notify=None)`` owns the ``sending_domains`` and ``sending_inboxes``
tables and the SendingInbox machine (``nextron.domain.sending_inbox``). ``env`` is the engine's
environment: outside ``prod`` only allowlisted test inboxes can be registered (AD-14).

- ``@use_case register_inbox(address, label) -> RegisteredInbox(inbox_id, sending_domain_id,
  domain, domain_created)``:

  1. ``check_address`` and ``check_label`` run before any transaction (a non-``str`` argument
     raises ``TypeError``).
  2. Then one transaction, in order:

     - outside ``prod``, the ``sink_allowlist`` check (``allowlist_refusal``); an allowlist
       whose stored text no longer parses counts as empty, so the inbox is refused;
     - the duplicate-address check, over every status (a ``removed`` inbox's address stays
       registered);
     - ``from_name`` is ``queries.setup.get(Key.SENDER_NAME)``; ``SettingNotSet`` or
       ``InvalidSettingValue`` is ``sender_name_unset``;
     - the ``sending_domains`` row of the address's registrable domain is found or inserted
       (``domain_created``);
     - the inbox is inserted at ``CREATION.to`` (``connecting``), ``created_at`` = now, and
       paced: its ``next_send_at`` is ``sending_policy.repaced_next_send_at(None, now,
       gap_source)``, one gap after its registration (Story 6.11);
     - one ``events`` row: entity ``sending_inboxes``, ``None`` to ``connecting``, cause
       ``registered``, no ``cause_ref``, actor ``operator``.

  3. Every refusal raises ``InboxRefused`` (``.refusal``; its message is
     ``REFUSAL_MESSAGES[refusal]``) and rolls the whole transaction back: nothing is stored.
  4. After it, the use case logs ``sending_inbox_registered`` (``inbox_id``,
     ``sending_domain_id`` and ``domain_created``) or ``sending_inbox_refused`` (``refusal``),
     never the address or the label.

- ``@use_case record_warmup(inbox_id, started_on, method, warm_level, tool_daily, ramp_step,
  ramp_target) -> WarmupRecord`` (Story 6.4, FR-33, FR-34): the warm-up form's six texts. One
  transaction, in order:

  1. ``sender_timezone`` through ``queries.setup.get``; ``SettingNotSet`` or
     ``InvalidSettingValue`` is ``timezone_unset``. Today is ``sending_policy.local_date`` of
     the clock's now in it;
  2. ``check_warmup(..., today=today)`` (``nextron.domain.sending_inbox``);
  3. the inbox's status: no such inbox (or an id that isn't a ULID) is ``unknown_inbox``, a
     ``removed`` one ``inbox_removed``;
  4. the six warm-up columns are updated (``repos.sending.inboxes.update_warmup``).

  It changes no status, so it appends no ``events`` row. Every refusal raises
  ``WarmupRefused`` (``.refusal``; the message names the field, never the value) and stores
  nothing. After it, the use case logs ``sending_warmup_recorded`` (``inbox_id``) or
  ``sending_warmup_refused`` (``refusal``, plus ``inbox_id`` when it is a ULID); never a form
  value.
- ``inbox_caps() -> InboxCaps(local_date, caps)``, a plain read for the current unit of work (a
  read-only one included): today's date in ``sender_timezone`` (``UTC`` while it is unset or
  unreadable) and ``queries.sending.inbox_caps`` on it, what the Sending Inboxes panel shows.

**Cold-send pacing (Story 6.11, FR-31, AD-11, NFR-22).** ``Sending`` records
``process_started_at = clock.now()`` when it is built (the engine builds it once, at start).
``gap_source`` is the random source of every gap (``random.SystemRandom()`` when ``None``); the
draw itself is ``sending_policy.draw_gap``'s. No planned send slot is stored: only
``next_send_at`` and ``last_sent_at``.

- ``@command record_cold_send(inbox_id, sent_at) -> datetime | None``: joins the caller's
  transaction (Story 6.10's apply unit) and sets the inbox's ``last_sent_at`` to ``sent_at`` (an
  aware ``datetime``, else ``ValueError``) and its ``next_send_at`` to
  ``sending_policy.next_send_after(sent_at, gap_source)``; returns that ``next_send_at``, or
  ``None`` when no inbox has that id (nothing is written).
- ``@use_case repace_after_start() -> int``: one transaction; every listed (non-``removed``)
  inbox's ``next_send_at`` becomes ``sending_policy.repaced_next_send_at(last_sent_at,
  process_started_at, gap_source)`` (``last_sent_at`` unchanged); returns how many inboxes it
  re-paced and logs ``cold_sends_repaced`` (``count``). The engine's startup step
  ``repace_cold_sends`` runs it; an inbox registered later is paced by ``register_inbox``.
- ``dispatch_state(inbox_id, now) -> InboxDispatchState``: a plain read in the current unit of
  work. ``recovery_mode`` from ``queries.ops.recovery_mode``, ``next_send_at`` from the inbox
  view, ``last_sync_completed_at`` from ``queries.replies.last_sync_completed`` (``None`` until
  Story 6.8), and ``cold_sent_today`` (``queries.sending.cold_sent_on``, ``None`` until Story
  6.10) and ``cold_cap`` (``queries.sending.cold_daily_caps``) on ``now``'s local date in
  ``sender_timezone``. A missing inbox or one that isn't ``active`` (``connecting``,
  ``paused``, ``auth_failed`` or ``removed``), or an unset or unreadable
  ``sender_timezone``, gives a state with recovery mode on, no sync, no pace, no count and cap
  0, which ``pick_dispatch`` turns into no pick (fail closed). A naive ``now`` raises
  ``ValueError``.

**Connecting through Gmail OAuth (Story 6.2, FR-32, AD-4, AD-12).** ``Sending`` takes
``domain_checks`` (``DomainChecks``: its ``queue_check`` command) and ``notify`` (``Notify``:
its ``alert`` command); ``nextron.app.sending.connect.InboxConnections`` runs the consent flow
and the identity check, then calls these in a unit of work of its own:

- ``@use_case connect_inbox(address, label, *, now) -> ConnectedInbox(inbox_id,
  sending_domain_id, created, from_status, paused, check_queued)``: ``check_address`` and
  ``check_label`` (``label`` may be ``None`` for a registered inbox) first, as
  ``ConnectRefused`` with their connect failure; then one transaction, in order: the allowlist
  outside ``prod`` (``not_allowlisted``); ``from_name`` from the Sender's name
  (``sender_name_unset``); the inbox by address, registered as ``register_inbox`` does (its
  ``registered`` event, actor ``operator``) when there is none (``invalid_label`` without a
  label); its status: ``connecting`` moves by ``connect`` (cause ``connected``),
  ``auth_failed`` by ``reconsent`` (``reconsented``), ``active`` and ``paused`` are
  ``already_connected``, ``removed`` ``inbox_removed``; the move is fenced on the status read
  (``repos.sending.inboxes.mark_connected``: status, ``status_changed_at`` and ``connected_at``
  both ``now``, ``from_name``; a miss is ``store_failed``) with one ``events`` row (actor
  ``operator``); ``domain_checks.queue_check(sending_domain_id)``; and when the domain's newest
  check (``repos.sending.checks.newest``) has a DNSBL ``listed`` outcome, or else a spam rate
  status ``pause``, the inbox moves on to ``paused`` with that cause (``blocklist`` or
  ``spam_rate``, ``cause_ref`` the check's id, actor ``system:sending``); else, for an
  ``auth_failed`` inbox that was ``paused`` before its grant failed (the newest ``auth_fail``
  event's ``from_status``), it moves on to ``paused`` again with its newest pause event's cause
  (any cause, e.g. ``operator``; actor ``system:sending``, no ``cause_ref``); that cause is
  ``ConnectedInbox.paused``. Any refusal rolls the
  whole transaction back. It logs ``sending_inbox_connected`` with ids only.
- ``@use_case mark_auth_failed(inbox_id) -> bool``: one transaction; an ``active`` or
  ``paused`` inbox moves by ``auth_fail`` to ``auth_failed`` (cause ``invalid_grant``, fenced,
  ``status_changed_at`` set) with one ``events`` row (actor ``system:sending``) and one
  ``inbox_auth_failed`` alert (subject the event's id; Telegram and dashboard; the inbox's id
  only); any other status, an unknown id or one that isn't a ULID returns ``False`` and changes
  nothing. It logs ``sending_inbox_auth_failed`` (``inbox_id``).

``repos.sending.inboxes.set_status`` also sets ``status_changed_at`` (Story 6.6's pauses pass
their time).

This service registers and connects inboxes, records their warm-up and paces their cold sends:
the pause, resume and remove commands arrive with Story 6.3, and enrollment
(Story 4.8) binds a Sequence to one of ``queries.sending.inboxes_for_binding()``. Nothing here
sends email or runs warm-up exchanges.
"""

import logging
import random
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, Protocol

from nextron.app.queries import ops as ops_queries
from nextron.app.queries import replies as replies_queries
from nextron.app.queries import sending as sending_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.queries.sending import InboxCapView
from nextron.db import eventlog
from nextron.db.repos.sending import checks as checks_repo
from nextron.db.repos.sending import domains as domains_repo
from nextron.db.repos.sending import inboxes as inboxes_repo
from nextron.db.uow import command, current, use_case
from nextron.domain.alert import Channel
from nextron.domain.ids import is_ulid
from nextron.domain.reputation import SpamStatus, ZoneOutcome, decode_dnsbl, overall
from nextron.domain.sending_inbox import (
    CREATION,
    INBOX_AUTH_FAILED,
    PAUSE_EVENTS,
    ConnectFailure,
    ConnectRefused,
    InboxAddress,
    InboxEvent,
    InboxRefused,
    InboxStatus,
    PauseCause,
    Refusal,
    WarmupRecord,
    WarmupRefusal,
    WarmupRefused,
    allowlist_refusal,
    auth_failed_text,
    check_address,
    check_label,
    check_warmup,
    connect_failure,
    transition,
)
from nextron.domain.sending_policy import (
    GapSource,
    InboxDispatchState,
    local_date,
    next_send_after,
    repaced_next_send_at,
)
from nextron.domain.settings_schema import InvalidSettingValue, Key, SettingNotSet
from nextron.domain.sink import sink_applies
from nextron.ports.clock import Clock

ENTITY: Final = "sending_inboxes"
ACTOR: Final = "operator"
INBOX_REGISTERED: Final = "sending_inbox_registered"
INBOX_REFUSED: Final = "sending_inbox_refused"
WARMUP_RECORDED: Final = "sending_warmup_recorded"
WARMUP_REFUSED: Final = "sending_warmup_refused"
COLD_SENDS_REPACED: Final = "cold_sends_repaced"
FALLBACK_TIMEZONE: Final = "UTC"
SYSTEM_ACTOR: Final = "system:sending"
INBOX_CONNECTED: Final = "sending_inbox_connected"
INBOX_AUTH_FAILED_LOG: Final = "sending_inbox_auth_failed"
ALERT_CHANNELS: Final = (Channel.TELEGRAM, Channel.DASHBOARD)
# What connecting does from each status: the machine's event, or the refusal.
CONNECT_EVENTS: Final = {
    InboxStatus.CONNECTING: InboxEvent.CONNECT,
    InboxStatus.AUTH_FAILED: InboxEvent.RECONSENT,
}
CONNECT_REFUSALS: Final = {
    InboxStatus.ACTIVE: ConnectFailure.ALREADY_CONNECTED,
    InboxStatus.PAUSED: ConnectFailure.ALREADY_CONNECTED,
    InboxStatus.REMOVED: ConnectFailure.INBOX_REMOVED,
}

log = logging.getLogger(__name__)


class QueueCheck(Protocol):
    """What connecting needs of ``DomainChecks``: its ``queue_check`` command."""

    def queue_check(self, sending_domain_id: str) -> bool: ...


class Alerts(Protocol):
    """What ``mark_auth_failed`` needs of the ``notify`` service."""

    def alert(self, kind: str, subject: str, text: str, channels: Iterable[Channel]) -> object: ...


@dataclass(frozen=True, slots=True)
class ConnectedInbox:
    """A connected inbox: its id and Sending Domain, whether it was created by this connect, the
    status it came from, and the pause cause if it was paused at once (its domain's newest
    check, or the pause it had before its grant failed)."""

    inbox_id: str
    sending_domain_id: str
    created: bool
    from_status: InboxStatus
    paused: PauseCause | None
    check_queued: bool


@dataclass(frozen=True, slots=True)
class RegisteredInbox:
    """A registered inbox: its id, its Sending Domain's id and domain, and whether that Sending
    Domain was created for it."""

    inbox_id: str
    sending_domain_id: str
    domain: str
    domain_created: bool


@dataclass(frozen=True, slots=True)
class InboxCaps:
    """Today's date in ``sender_timezone`` and each listed inbox's caps on it."""

    local_date: date
    caps: tuple[InboxCapView, ...]


class Sending:
    """Sending Inboxes and their Sending Domains; see the module docstring."""

    def __init__(
        self,
        clock: Clock,
        *,
        env: str,
        gap_source: GapSource | None = None,
        domain_checks: QueueCheck | None = None,
        notify: Alerts | None = None,
    ) -> None:
        self._clock = clock
        self._env = env
        self._gaps: GapSource = random.SystemRandom() if gap_source is None else gap_source
        self._domain_checks = domain_checks
        self._notify = notify
        self.process_started_at: Final[datetime] = clock.now()

    @use_case
    def register_inbox(self, address: str, label: str) -> RegisteredInbox:
        """Register a Sending Inbox at ``connecting``; see the module docstring."""
        try:
            checked = check_address(address)
            shown_label = check_label(label)
            with current().transaction():
                registered = self._register(checked, shown_label)
        except InboxRefused as refused:
            log.info(INBOX_REFUSED, extra={"refusal": str(refused.refusal)})
            raise
        log.info(
            INBOX_REGISTERED,
            extra={
                "inbox_id": registered.inbox_id,
                "sending_domain_id": registered.sending_domain_id,
                "domain_created": registered.domain_created,
            },
        )
        return registered

    def _register(self, checked: InboxAddress, label: str) -> RegisteredInbox:
        allowlist = self._allowlist() if sink_applies(self._env) else ()
        refusal = allowlist_refusal(checked.address, self._env, allowlist)
        if refusal is not None:
            raise InboxRefused(refusal)
        if inboxes_repo.id_by_address(checked.address) is not None:
            raise InboxRefused(Refusal.ALREADY_REGISTERED)
        try:
            from_name = str(setup_queries.get(Key.SENDER_NAME))
        except (SettingNotSet, InvalidSettingValue):
            raise InboxRefused(Refusal.SENDER_NAME_UNSET) from None
        now = self._clock.now()
        found = domains_repo.by_domain(checked.domain)
        if found is None:
            domain_id = domains_repo.insert(domain=checked.domain, created_at=now)
        else:
            domain_id = found.id
        inbox_id = inboxes_repo.insert(
            address=checked.address,
            label=label,
            sending_domain_id=domain_id,
            from_name=from_name,
            status=CREATION.to,
            created_at=now,
        )
        inboxes_repo.update_pacing(
            inbox_id, next_send_at=repaced_next_send_at(None, now, self._gaps)
        )
        eventlog.append(
            entity=ENTITY,
            entity_id=inbox_id,
            from_status=None,
            to_status=CREATION.to,
            cause=CREATION.cause,
            cause_ref=None,
            actor=ACTOR,
            at=now,
        )
        return RegisteredInbox(inbox_id, domain_id, checked.domain, found is None)

    @staticmethod
    def _allowlist() -> tuple[object, ...]:
        """The ``sink_allowlist`` entries; a stored list that no longer parses counts as
        empty."""
        try:
            return tuple(setup_queries.get(Key.SINK_ALLOWLIST))
        except InvalidSettingValue:
            return ()

    # Connecting through Gmail OAuth (Story 6.2) -------------------------------------------------

    @use_case
    def connect_inbox(self, address: str, label: str | None, *, now: datetime) -> ConnectedInbox:
        """Record a verified Gmail connection of ``address``; see the module docstring."""
        checks = self._domain_checks
        if checks is None:
            raise RuntimeError("Sending was built without domain_checks; it can't connect")
        try:
            checked = check_address(address)
            shown_label = None if label is None else check_label(label)
        except InboxRefused as refused:
            raise ConnectRefused(connect_failure(refused.refusal)) from None
        with current().transaction():
            connected = self._connect(checked, shown_label, now, checks)
        log.info(
            INBOX_CONNECTED,
            extra={
                "inbox_id": connected.inbox_id,
                "sending_domain_id": connected.sending_domain_id,
                "created": connected.created,
                "from_status": str(connected.from_status),
                "paused": None if connected.paused is None else str(connected.paused),
            },
        )
        return connected

    def _connect(
        self, checked: InboxAddress, label: str | None, now: datetime, checks: QueueCheck
    ) -> ConnectedInbox:
        allowlist = self._allowlist() if sink_applies(self._env) else ()
        refusal = allowlist_refusal(checked.address, self._env, allowlist)
        if refusal is not None:
            raise ConnectRefused(connect_failure(refusal))
        try:
            from_name = str(setup_queries.get(Key.SENDER_NAME))
        except (SettingNotSet, InvalidSettingValue):
            raise ConnectRefused(ConnectFailure.SENDER_NAME_UNSET) from None
        inbox_id = inboxes_repo.id_by_address(checked.address)
        created = inbox_id is None
        if inbox_id is None:
            if label is None:
                raise ConnectRefused(ConnectFailure.INVALID_LABEL)
            try:
                inbox_id = self._register(checked, label).inbox_id
            except InboxRefused as refused:
                raise ConnectRefused(connect_failure(refused.refusal)) from None
        stored = inboxes_repo.by_id(inbox_id)
        if stored is None:
            raise ConnectRefused(ConnectFailure.UNKNOWN_INBOX)
        status = InboxStatus(stored.status)
        if status in CONNECT_REFUSALS:
            raise ConnectRefused(CONNECT_REFUSALS[status])
        moved = transition(status, CONNECT_EVENTS[status])
        if not inboxes_repo.mark_connected(
            inbox_id, from_status=status, to_status=moved.to, at=now, from_name=from_name
        ):
            raise ConnectRefused(ConnectFailure.STORE_FAILED)  # changed meanwhile
        eventlog.append(
            entity=ENTITY,
            entity_id=inbox_id,
            from_status=str(status),
            to_status=str(moved.to),
            cause=str(moved.cause),
            cause_ref=None,
            actor=ACTOR,
            at=now,
        )
        check_queued = checks.queue_check(stored.sending_domain_id)
        paused = self._repause(inbox_id, stored.sending_domain_id, now)
        if paused is None and status is InboxStatus.AUTH_FAILED:
            paused = self._restore_pause(inbox_id, now)
        return ConnectedInbox(
            inbox_id, stored.sending_domain_id, created, status, paused, check_queued
        )

    @staticmethod
    def _repause(inbox_id: str, sending_domain_id: str, now: datetime) -> PauseCause | None:
        """Pause the just-connected inbox when its domain's newest check pauses (a DNSBL
        listing first, then a spam rate ``pause``); the cause, or ``None``."""
        newest = checks_repo.newest(sending_domain_id)
        if newest is None:
            return None
        cause: PauseCause | None = None
        if (
            newest.dnsbl_result is not None
            and overall(decode_dnsbl(newest.dnsbl_result).values()) is ZoneOutcome.LISTED
        ):
            cause = PauseCause.BLOCKLIST
        elif newest.spam_rate_status == SpamStatus.PAUSE:
            cause = PauseCause.SPAM_RATE
        if cause is None:
            return None
        moved = transition(InboxStatus.ACTIVE, PAUSE_EVENTS[cause])
        if not inboxes_repo.set_status(
            inbox_id, from_status=InboxStatus.ACTIVE, to_status=moved.to, at=now
        ):
            return None
        eventlog.append(
            entity=ENTITY,
            entity_id=inbox_id,
            from_status=str(InboxStatus.ACTIVE),
            to_status=str(moved.to),
            cause=str(moved.cause),
            cause_ref=newest.id,
            actor=SYSTEM_ACTOR,
            at=now,
        )
        return cause

    @staticmethod
    def _restore_pause(inbox_id: str, now: datetime) -> PauseCause | None:
        """Pause the just-reconsented inbox again when it was ``paused`` before its grant failed
        (the newest ``auth_fail`` event's ``from_status``), with its newest pause cause; the
        cause, or ``None``."""
        failed = inboxes_repo.newest_event_into(inbox_id, InboxStatus.AUTH_FAILED)
        if failed is None or failed[0] != InboxStatus.PAUSED:
            return None
        newest = inboxes_repo.newest_event_into(inbox_id, InboxStatus.PAUSED)
        if newest is None or newest[1] not in PauseCause:
            return None
        cause = PauseCause(newest[1])
        moved = transition(InboxStatus.ACTIVE, PAUSE_EVENTS[cause])
        if not inboxes_repo.set_status(
            inbox_id, from_status=InboxStatus.ACTIVE, to_status=moved.to, at=now
        ):
            return None
        eventlog.append(
            entity=ENTITY,
            entity_id=inbox_id,
            from_status=str(InboxStatus.ACTIVE),
            to_status=str(moved.to),
            cause=str(moved.cause),
            cause_ref=None,
            actor=SYSTEM_ACTOR,
            at=now,
        )
        return cause

    @use_case
    def mark_auth_failed(self, inbox_id: str) -> bool:
        """Move an ``active`` or ``paused`` inbox to ``auth_failed`` (Google answered
        ``invalid_grant``); see the module docstring."""
        notify = self._notify
        if notify is None:
            raise RuntimeError("Sending was built without notify; it can't mark auth_failed")
        with current().transaction():
            status = inboxes_repo.status_of(inbox_id) if is_ulid(inbox_id) else None
            if status not in (InboxStatus.ACTIVE, InboxStatus.PAUSED):
                return False
            current_status = InboxStatus(status)
            moved = transition(current_status, InboxEvent.AUTH_FAIL)
            now = self._clock.now()
            if not inboxes_repo.set_status(
                inbox_id, from_status=current_status, to_status=moved.to, at=now
            ):
                return False
            event_id = eventlog.append(
                entity=ENTITY,
                entity_id=inbox_id,
                from_status=str(current_status),
                to_status=str(moved.to),
                cause=str(moved.cause),
                cause_ref=None,
                actor=SYSTEM_ACTOR,
                at=now,
            )
            notify.alert(INBOX_AUTH_FAILED, event_id, auth_failed_text(inbox_id), ALERT_CHANNELS)
        log.warning(INBOX_AUTH_FAILED_LOG, extra={"inbox_id": inbox_id})
        return True

    # Warm-up (Story 6.4) ----------------------------------------------------------------------

    @use_case
    def record_warmup(
        self,
        inbox_id: str,
        started_on: str,
        method: str,
        warm_level: str,
        tool_daily: str,
        ramp_step: str,
        ramp_target: str,
    ) -> WarmupRecord:
        """Record an inbox's warm-up and ramp; see the module docstring."""
        try:
            with current().transaction():
                record = self._record_warmup(
                    inbox_id, started_on, method, warm_level, tool_daily, ramp_step, ramp_target
                )
        except WarmupRefused as refused:
            extra: dict[str, object] = {"refusal": str(refused.refusal)}
            if isinstance(inbox_id, str) and is_ulid(inbox_id):
                extra["inbox_id"] = inbox_id
            log.info(WARMUP_REFUSED, extra=extra)
            raise
        log.info(WARMUP_RECORDED, extra={"inbox_id": inbox_id})
        return record

    def _record_warmup(
        self,
        inbox_id: str,
        started_on: str,
        method: str,
        warm_level: str,
        tool_daily: str,
        ramp_step: str,
        ramp_target: str,
    ) -> WarmupRecord:
        try:
            timezone = str(setup_queries.get(Key.SENDER_TIMEZONE))
        except (SettingNotSet, InvalidSettingValue):
            raise WarmupRefused(WarmupRefusal.TIMEZONE_UNSET) from None
        today = local_date(self._clock.now(), timezone)
        record = check_warmup(
            started_on, method, warm_level, tool_daily, ramp_step, ramp_target, today=today
        )
        if not isinstance(inbox_id, str) or not is_ulid(inbox_id):
            raise WarmupRefused(WarmupRefusal.UNKNOWN_INBOX)
        status = inboxes_repo.status_of(inbox_id)
        if status is None:
            raise WarmupRefused(WarmupRefusal.UNKNOWN_INBOX)
        if status == InboxStatus.REMOVED:
            raise WarmupRefused(WarmupRefusal.INBOX_REMOVED)
        inboxes_repo.update_warmup(
            inbox_id,
            warmup_started_on=record.warmup_started_on,
            warmup_method=record.warmup_method,
            warm_level=record.warm_level,
            warmup_tool_daily=record.warmup_tool_daily,
            ramp_step=record.ramp_step,
            ramp_target=record.ramp_target,
        )
        return record

    def inbox_caps(self) -> InboxCaps:
        """Today's date in ``sender_timezone`` and each listed inbox's caps on it, read in the
        current unit of work (a read-only one included)."""
        try:
            timezone = str(setup_queries.get(Key.SENDER_TIMEZONE))
        except (SettingNotSet, InvalidSettingValue):
            timezone = FALLBACK_TIMEZONE
        today = local_date(self._clock.now(), timezone)
        return InboxCaps(today, sending_queries.inbox_caps(today))

    # Cold-send pacing (Story 6.11) ------------------------------------------------------------

    @command
    def record_cold_send(self, inbox_id: str, sent_at: datetime) -> datetime | None:
        """Record a cold send at ``sent_at`` and pace the inbox's next one; see the module
        docstring."""
        next_send_at = next_send_after(sent_at, self._gaps)
        changed = inboxes_repo.update_pacing(
            inbox_id, next_send_at=next_send_at, last_sent_at=sent_at
        )
        return next_send_at if changed else None

    @use_case
    def repace_after_start(self) -> int:
        """Re-pace every listed inbox from process start; see the module docstring."""
        with current().transaction():
            listed = sending_queries.inboxes()
            for inbox in listed:
                inboxes_repo.update_pacing(
                    inbox.id,
                    next_send_at=repaced_next_send_at(
                        inbox.last_sent_at, self.process_started_at, self._gaps
                    ),
                )
        log.info(COLD_SENDS_REPACED, extra={"count": len(listed)})
        return len(listed)

    def dispatch_state(self, inbox_id: str, now: datetime) -> InboxDispatchState:
        """The inbox's dispatch facts at ``now``, read in the current unit of work; see the
        module docstring."""
        local_date(now, FALLBACK_TIMEZONE)  # refuses a naive now before any read
        closed = InboxDispatchState(
            inbox_id=inbox_id,
            recovery_mode=True,
            process_started_at=self.process_started_at,
            last_sync_completed_at=None,
            next_send_at=None,
            cold_sent_today=None,
            cold_cap=0,
        )
        try:
            timezone = str(setup_queries.get(Key.SENDER_TIMEZONE))
        except (SettingNotSet, InvalidSettingValue):
            return closed
        today = local_date(now, timezone)
        inbox = next((view for view in sending_queries.inboxes() if view.id == inbox_id), None)
        if inbox is None or inbox.status is not InboxStatus.ACTIVE:
            return closed
        return InboxDispatchState(
            inbox_id=inbox_id,
            recovery_mode=ops_queries.recovery_mode(),
            process_started_at=self.process_started_at,
            last_sync_completed_at=replies_queries.last_sync_completed(inbox_id),
            next_send_at=inbox.next_send_at,
            cold_sent_today=sending_queries.cold_sent_on(inbox_id, today),
            cold_cap=sending_queries.cold_daily_caps(today).get(inbox_id, 0),
        )
