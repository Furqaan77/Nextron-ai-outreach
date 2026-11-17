"""The ``AlertPort`` (AD-14, AD-15): plain-text alerts to Nextron people.

``AlertPort`` is the only channel to Nextron people (ops alerts, Hot Lead alerts, LinkedIn
Tasks). Its first adapter, Telegram, arrives in Story 1.12; tests use
``tests.fakes.alert.InMemoryAlert``.

- ``AlertMessage(recipient, text)``: ``recipient`` is where the alert goes (a numeric Telegram
  chat id for Telegram) and ``text`` is plain text. Ops alerts carry IDs and counts only.
- ``AlertDelivery(message_id, sink=False)``: the channel's id for the sent message, or ``None``
  when it has none. ``sink=True`` means the dev and test sink (``nextron.adapters.sink``)
  recorded the message instead of sending it.
- A failed send raises ``AdapterError`` (``nextron.ports.errors``).

Outside ``prod``, ``nextron.main`` wraps every adapter registered for the ``alert`` role in
``AlertSink``, which reads ``recipient`` and ``text``; a new field here must be reviewed against
the sink (``tests/contracts/test_sink_contracts.py`` pins the fields). The ``repr`` of a message
leaves out its recipient and text, so a stray log call can't print them.
"""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


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
