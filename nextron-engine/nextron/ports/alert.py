"""The ``AlertPort`` (AD-14, AD-15): plain-text alerts to Nextron people.

``AlertPort`` is the only channel to Nextron people (ops alerts, Hot Lead alerts, LinkedIn
Tasks). Its first adapter is Telegram (``nextron.adapters.telegram``, Story 1.12); tests use
``tests.fakes.alert.InMemoryAlert``.

- ``AlertMessage(recipient, text)``: ``recipient`` is where the alert goes (a numeric Telegram
  chat id for Telegram) and ``text`` is plain text. Ops alerts carry IDs and counts only.
- ``AlertDelivery(message_id, sink=False)``: the channel's id for the sent message, or ``None``
  when it has none. ``sink=True`` means the dev and test sink (``nextron.adapters.sink``)
  recorded the message instead of sending it.
- A failed send raises ``AdapterError`` (``nextron.ports.errors``). One whose code is
  ``DELIVERY_UNKNOWN`` (``alert_delivery_unknown``) is never retryable and means the provider
  may have received the message: the request went out, but no answer that proves delivery came
  back (a timeout or a dropped connection after sending, or a success answer without a message
  id). The ``notify`` service re-sends such an alert once at most; any other non-retryable code
  means the message was not delivered.
- ``delete(recipient, message_id) -> bool`` (Story 3.14, an erasure): deletes a message the
  channel sent earlier, by its ``message_id``. ``True`` means it was deleted, ``False`` that the
  channel refused (Telegram does for a message over 48 hours old, or one already gone). A
  failure that may pass raises a retryable ``AdapterError``; any other raises one that isn't.

Outside ``prod``, ``nextron.main`` wraps every adapter registered for the ``alert`` role in
``AlertSink``, which reads ``recipient`` and ``text``; a new field here must be reviewed against
the sink (``tests/contracts/test_sink_contracts.py`` pins the fields). The ``repr`` of a message
leaves out its recipient and text, so a stray log call can't print them.
"""

from dataclasses import dataclass, field
from typing import Final, Protocol, runtime_checkable

DELIVERY_UNKNOWN: Final = "alert_delivery_unknown"  # never retryable; may have been delivered


@dataclass(frozen=True, slots=True)
class AlertMessage:
    """One plain-text alert to one recipient."""

    recipient: str = field(repr=False)
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class AlertDelivery:
    """The outcome of a sent alert: the channel's message id, and whether the sink took it."""

    message_id: str | None
    sink: bool = False


@runtime_checkable
class AlertPort(Protocol):
    """Sends plain-text alerts."""

    def send(self, message: AlertMessage) -> AlertDelivery:
        """Send ``message``; raise ``AdapterError`` when it can't be sent."""
        ...

    def delete(self, recipient: str, message_id: str) -> bool:
        """Delete the message ``message_id`` sent to ``recipient``; ``False`` when the channel
        refuses; raise ``AdapterError`` when it can't be asked."""
        ...
