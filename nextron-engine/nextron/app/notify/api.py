"""Public command API of the ``notify`` service (AD-3, AD-4, AD-15, INT-8, UX-DR5).

Other services and the delivery layers change ``notify`` data only through this module (AD-3).
Commands join the caller's unit of work and never begin, commit or roll back.

``Notify(clock, queue, *, alert_ports)`` owns the ``alerts`` and ``notifications`` tables and
the Alert machine (``nextron.domain.alert``). ``alert_ports`` maps each delivery channel to a
callable that builds its ``AlertPort`` (``nextron.main`` passes ``build_role_adapter`` for
Telegram, which wraps the adapter in the sink outside ``prod``); each port is built on its
channel's first send and kept, under a lock, so building ``Notify`` builds no adapter. Every
status change appends an ``events`` row in the same transaction: entity ``alerts``, actor
``system:notify``, no ``cause_ref``.

- ``@command alert(kind, subject, text, channels) -> Alerted(created)`` runs in the caller's
  transaction. ``check_alert`` first (``InvalidAlert`` dooms the caller's transaction, with
  nothing written). For each delivery channel (Telegram) without a ``(kind, subject,
  channel)`` row it inserts a ``pending`` alert with its ``requested`` creation event and
  enqueues ``send_alert`` (scope the alert id, discriminator ``"0"``, payload ``{"v": 1,
  "alert_id": <id>}``, the realtime lane). For ``dashboard`` without a ``(kind, subject)``
  notification it inserts one. An existing row is left untouched, so a repeat is a no-op.
  ``created`` lists the channels it added, in ``Channel`` order.
- ``@use_case send(alert_id)`` is the ``send_alert`` job's use case. The port is never called
  inside a transaction (AD-3): the sink reads its allowlist in a unit of work of its own.

  1. **Prepare**, in one transaction. Nothing to do (it returns) for a missing alert, a status
     other than ``pending``, or an ``unknown`` alert that was already re-sent or whose channel
     isn't in ``RESENDS_UNKNOWN``. The chat id is ``queries.setup.get(Key.ALERT_CHAT_ID)``; a
     ``SettingNotSet`` or ``InvalidSettingValue`` fails the alert in this same transaction,
     with the error's code (see *Not delivered*), and returns.
  2. **The call**: ``port.send(AlertMessage(chat_id, text))``. A retryable ``AdapterError``
     re-raises, so the job retries and the alert stays as it was; ``DELIVERY_UNKNOWN`` is an
     unknown outcome; any other ``AdapterError`` is a failure with its code; anything else
     propagates.
  3. **Apply**, in one transaction, fenced on the status prepare saw (a miss changes nothing).
     Delivered: ``deliver``, with ``provider_message_id`` (``NULL`` for a sink delivery). Unknown
     on the first send: ``lose``, ``error_code``, and the resend job (discriminator ``"1"``,
     ``run_after`` now + ``RESEND_DELAY``). Unknown on the resend: the status stays
     ``unknown``, ``resent_at`` = now, and *Not delivered* applies. Failure: ``fail``,
     ``error_code`` and *Not delivered*. A resend that reaches apply always sets ``resent_at``.

  **Not delivered**: a ``dashboard``-only alert of kind ``alert_not_delivered`` about the alert
  id, ``"Telegram alert not delivered: <kind> <alert ULID>, <code>"`` (or ``"Telegram alert may
  not have been delivered: ..."`` after a second unknown outcome).
- ``@use_case dismiss(notification_id) -> bool`` sets ``dismissed_at`` once, in one
  transaction. An unknown or already dismissed id is ``False`` and changes nothing.
- ``delete_message(chat_id, message_id) -> bool`` (Story 3.14) is a plain call of the Telegram
  port's ``delete``: ``True`` when the message was deleted, ``False`` when Telegram refused. It
  writes nothing, joins no unit of work and must be called outside any transaction (AD-3);
  the port's ``AdapterError`` propagates. An erasure's Telegram store calls it for each recent
  alert about the Contact.

**Logs**, written after the transaction commits, carry ``alert_id``, ``kind``, ``channel`` and
``code`` or ``sink``, never text, a chat id or a token: ``alert_sent`` (INFO, with ``sink``),
``alert_unknown`` and ``alert_failed`` (WARNING, with ``code``).

**Erasure (Story 3.13).** ``ERASURE`` declares ``alerts`` and ``notifications`` ``not_held``:
they carry IDs and counts only, never a Contact's data. The module-level ``@command erase_pii(n:
ContactErased)`` is notify's ``ContactErased`` subscriber (``nextron.main.build_subscribers``);
it writes nothing.
"""

import logging
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Final

from nextron.app.jobqueue.api import JobQueue
from nextron.app.queries import setup as setup_queries
from nextron.db import eventlog
from nextron.db.repos.notify import alerts as alerts_repo
from nextron.db.repos.notify import notifications as notifications_repo
from nextron.db.uow import command, current, use_case
from nextron.domain.alert import (
    CREATION,
    DELIVERY_CHANNELS,
    NOT_DELIVERED,
    RESEND_DELAY,
    RESENDS_UNKNOWN,
    SEND_ALERT_JOB,
    AlertCause,
    AlertEvent,
    AlertStatus,
    Channel,
    check_alert,
    not_delivered_text,
    transition,
)
from nextron.domain.erasure import Coverage, TableErasure
from nextron.domain.ids import is_ulid
from nextron.domain.job import Lane
from nextron.domain.machines import Transition
from nextron.domain.notifications import ContactErased
from nextron.domain.settings_schema import InvalidSettingValue, Key, SettingNotSet
from nextron.ports.alert import DELIVERY_UNKNOWN, AlertDelivery, AlertMessage, AlertPort
from nextron.ports.clock import Clock
from nextron.ports.errors import AdapterError

ENTITY: Final = "alerts"
ACTOR: Final = "system:notify"
FIRST_SEND: Final = "0"  # the send_alert job's discriminator for the first send
RESEND: Final = "1"  # and for the one resend of an unknown alert
PAYLOAD_VERSION: Final = 1

ALERT_SENT: Final = "alert_sent"
ALERT_UNKNOWN: Final = "alert_unknown"
ALERT_FAILED: Final = "alert_failed"

log = logging.getLogger(__name__)

type AlertPorts = Mapping[Channel, Callable[[], AlertPort]]
# A log record to write once the transaction has committed: (level, event, extra).
type _Record = tuple[int, str, dict[str, object]]


@dataclass(frozen=True, slots=True)
class Alerted:
    """The channels ``alert`` added a row for, in ``Channel`` order; empty for a repeat."""

    created: tuple[Channel, ...]


@dataclass(frozen=True, slots=True)
class _Plan:
    """What prepare found: the alert to send, to whom, and whether this is its resend."""

    alert_id: str
    kind: str
    channel: Channel
    text: str
    status: AlertStatus
    chat_id: str
    resend: bool


@dataclass(frozen=True, slots=True)
class _Delivered:
    delivery: AlertDelivery


@dataclass(frozen=True, slots=True)
class _Unknown:
    code: str


@dataclass(frozen=True, slots=True)
class _Failed:
    code: str


type _Outcome = _Delivered | _Unknown | _Failed


def _fields(alert_id: str, kind: str, channel: str, **more: object) -> dict[str, object]:
    return {"alert_id": alert_id, "kind": kind, "channel": channel, **more}


def _write(records: Iterable[_Record]) -> None:
    for level, event, extra in records:
        log.log(level, event, extra=extra)


def _record(
    alert_id: str,
    from_status: AlertStatus | None,
    change: Transition[AlertStatus, AlertCause],
    at: datetime,
) -> None:
    eventlog.append(
        entity=ENTITY,
        entity_id=alert_id,
        from_status=None if from_status is None else str(from_status),
        to_status=str(change.to),
        cause=str(change.cause),
        cause_ref=None,
        actor=ACTOR,
        at=at,
    )


class Notify:
    """Ops alerts by Telegram and the dashboard's notifications; see the module docstring."""

    def __init__(self, clock: Clock, queue: JobQueue, *, alert_ports: AlertPorts) -> None:
        self._clock = clock
        self._queue = queue
        self._factories = dict(alert_ports)
        self._ports: dict[Channel, AlertPort] = {}
        self._lock = threading.Lock()

    def _port(self, channel: Channel) -> AlertPort:
        """The channel's port, built on its first use and kept."""
        with self._lock:
            port = self._ports.get(channel)
            if port is None:
                port = self._factories[channel]()
                self._ports[channel] = port
            return port

    # Raising alerts --------------------------------------------------------------------------

    @command
    def alert(self, kind: str, subject: str, text: str, channels: Iterable[Channel]) -> Alerted:
        """Raise an alert in the caller's transaction; see the module docstring."""
        chosen = check_alert(kind, subject, text, channels)
        now = self._clock.now()
        created: list[Channel] = []
        for channel in Channel:
            if channel not in chosen:
                continue
            if channel in DELIVERY_CHANNELS:
                if alerts_repo.find(kind, subject, channel) is not None:
                    continue
                alert_id = alerts_repo.insert_alert(
                    kind=kind,
                    subject=subject,
                    channel=str(channel),
                    text=text,
                    status=str(CREATION.to),
                    at=now,
                )
                _record(alert_id, None, CREATION, now)
                self._enqueue_send(alert_id, FIRST_SEND, now)
                created.append(channel)
            elif channel is Channel.DASHBOARD:
                if notifications_repo.find(kind, subject) is not None:
                    continue
                notifications_repo.insert_notification(
                    kind=kind, subject=subject, text=text, at=now
                )
                created.append(channel)
        return Alerted(tuple(created))

    def _enqueue_send(self, alert_id: str, discriminator: str, run_after: datetime) -> None:
        self._queue.enqueue(
            SEND_ALERT_JOB,
            scope=alert_id,
            discriminator=discriminator,
            payload={"v": PAYLOAD_VERSION, "alert_id": alert_id},
            run_after=run_after,
            lane=Lane.REALTIME,
        )

    # Deleting (Story 3.14) -----------------------------------------------------------------

    def delete_message(self, chat_id: str, message_id: str) -> bool:
        """Delete a Telegram message the bot sent; ``False`` when Telegram refuses."""
        return self._port(Channel.TELEGRAM).delete(chat_id, message_id)

    # Sending ---------------------------------------------------------------------------------

    @use_case
    def send(self, alert_id: str) -> None:
        """Send one alert: prepare, the port call between transactions, then apply."""
        records: list[_Record] = []
        with current().transaction():
            plan = self._prepare(alert_id, records)
        _write(records)
        if plan is None:
            return
        outcome = self._deliver(plan)
        records = []
        with current().transaction():
            self._apply(plan, outcome, records)
        _write(records)

    def _prepare(self, alert_id: str, records: list[_Record]) -> _Plan | None:
        if not is_ulid(alert_id):
            return None
        row = alerts_repo.get(alert_id)
        if row is None:
            return None
        status = AlertStatus(row.status)
        channel = Channel(row.channel)
        if status is AlertStatus.PENDING:
            resend = False
        elif (
            status is AlertStatus.UNKNOWN
            and row.resent_at is None
            and channel in RESENDS_UNKNOWN
        ):
            resend = True
        else:
            return None
        try:
            chat_id = setup_queries.get(Key.ALERT_CHAT_ID)
        except (SettingNotSet, InvalidSettingValue) as unusable:
            now = self._clock.now()
            self._fail(row.id, row.kind, channel, status, unusable.code, now, records)
            return None
        return _Plan(row.id, row.kind, channel, row.text, status, str(chat_id), resend)

    def _deliver(self, plan: _Plan) -> _Outcome:
        port = self._port(plan.channel)
        try:
            delivery = port.send(AlertMessage(plan.chat_id, plan.text))
        except AdapterError as error:
            if error.retryable:
                raise  # the job retries; the alert stays as it was
            if error.code == DELIVERY_UNKNOWN:
                return _Unknown(error.code)
            return _Failed(error.code)
        return _Delivered(delivery)

    def _apply(self, plan: _Plan, outcome: _Outcome, records: list[_Record]) -> None:
        now = self._clock.now()
        resent = {"resent_at": now} if plan.resend else {}
        match outcome:
            case _Delivered(delivery):
                change = transition(plan.status, AlertEvent.DELIVER)
                if self._move(
                    plan,
                    change,
                    now,
                    provider_message_id=delivery.message_id,
                    **resent,
                ):
                    fields = _fields(plan.alert_id, plan.kind, plan.channel, sink=delivery.sink)
                    records.append((logging.INFO, ALERT_SENT, fields))
            case _Unknown(code) if not plan.resend:
                change = transition(plan.status, AlertEvent.LOSE)
                if self._move(plan, change, now, error_code=code):
                    self._enqueue_send(plan.alert_id, RESEND, now + RESEND_DELAY)
                    fields = _fields(plan.alert_id, plan.kind, plan.channel, code=code)
                    records.append((logging.WARNING, ALERT_UNKNOWN, fields))
            case _Unknown(code):
                count = alerts_repo.update_seen(
                    plan.alert_id, str(plan.status), at=now, error_code=code, **resent
                )
                if count == 1:
                    self._not_delivered(plan.alert_id, plan.kind, code, maybe=True)
                    fields = _fields(plan.alert_id, plan.kind, plan.channel, code=code)
                    records.append((logging.WARNING, ALERT_UNKNOWN, fields))
            case _Failed(code):
                self._fail(
                    plan.alert_id,
                    plan.kind,
                    plan.channel,
                    plan.status,
                    code,
                    now,
                    records,
                    **resent,
                )

    def _move(
        self,
        plan: _Plan,
        change: Transition[AlertStatus, AlertCause],
        at: datetime,
        **changes: object,
    ) -> bool:
        """Apply ``change`` to the alert while it still has the status prepare saw, and record
        it; whether the fence held."""
        count = alerts_repo.update_seen(
            plan.alert_id, str(plan.status), at=at, status=str(change.to), **changes
        )
        if count != 1:
            return False
        _record(plan.alert_id, plan.status, change, at)
        return True

    def _fail(
        self,
        alert_id: str,
        kind: str,
        channel: Channel,
        seen: AlertStatus,
        code: str,
        at: datetime,
        records: list[_Record],
        **changes: object,
    ) -> None:
        change = transition(seen, AlertEvent.FAIL)
        count = alerts_repo.update_seen(
            alert_id, str(seen), at=at, status=str(change.to), error_code=code, **changes
        )
        if count != 1:
            return
        _record(alert_id, seen, change, at)
        self._not_delivered(alert_id, kind, code)
        fields = _fields(alert_id, kind, channel, code=code)
        records.append((logging.WARNING, ALERT_FAILED, fields))

    def _not_delivered(self, alert_id: str, kind: str, code: str, *, maybe: bool = False) -> None:
        text = not_delivered_text(kind, alert_id, code, maybe=maybe)
        self.alert(NOT_DELIVERED, alert_id, text, (Channel.DASHBOARD,))

    # The notification area -------------------------------------------------------------------

    @use_case
    def dismiss(self, notification_id: str) -> bool:
        """Dismiss one notification; ``False`` when it is unknown or already dismissed."""
        if not is_ulid(notification_id):
            return False
        now = self._clock.now()
        with current().transaction():
            return notifications_repo.dismiss(notification_id, now) == 1


# Erasure (Story 3.13) -------------------------------------------------------------------------

# What notify's erase_pii does to each of its tables with a personal-data column: none of them
# ever holds a Contact's data.
ERASURE: Final[Mapping[str, TableErasure]] = MappingProxyType(
    {
        "alerts": TableErasure(
            Coverage.NOT_HELD,
            "ops alerts carry IDs and counts only (AD-15); no alert kind that names a person "
            "exists yet",
        ),
        "notifications": TableErasure(
            Coverage.NOT_HELD,
            "dashboard notices carry IDs and counts only; no kind that names a person exists "
            "yet",
        ),
    }
)


@command
def erase_pii(n: ContactErased) -> None:
    """``notify``'s ``ContactErased`` subscriber (Story 3.13, AD-17): it writes nothing,
    because no ``notify`` table holds a Contact's data (``ERASURE``)."""
