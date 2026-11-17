"""The ``MailPort`` (AD-14): outbound email, minimal until Epic 6.

Story 1.8 declares the port so that the ``mail`` role has a sink before any mail adapter exists;
Epic 6 (Gmail sending) may extend ``MailMessage``. Tests use ``tests.fakes.mail.InMemoryMail``.

- ``MailMessage(recipient, subject, text)``: one plain-text email to one address.
- ``MailDelivery(message_id, thread_id, sink=False)``: the provider's ids for the sent message
  and its thread, or ``None``. ``sink=True`` means the dev and test sink
  (``nextron.adapters.sink``) recorded the message instead of sending it.
- A failed send raises ``AdapterError`` (``nextron.ports.errors``).

Outside ``prod``, ``nextron.main`` wraps every adapter registered for the ``mail`` role in
``MailSink``, which reads ``recipient``, ``subject`` and ``text``. A new field here (a CC
recipient, say) could carry a recipient the sink never checks, so
``tests/contracts/test_sink_contracts.py`` pins the fields: adding one fails it until the sink is
reviewed. The ``repr`` of a message leaves out its recipient, subject and text.
"""

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class MailMessage:
    """One plain-text email to one recipient."""

    recipient: str = field(repr=False)
    subject: str = field(repr=False)
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class MailDelivery:
    """The outcome of a sent email: the provider's message and thread ids, and whether the sink
    took it."""

    message_id: str | None
    thread_id: str | None
    sink: bool = False


@runtime_checkable
class MailPort(Protocol):
    """Sends email."""

    def send(self, message: MailMessage) -> MailDelivery:
        """Send ``message``; raise ``AdapterError`` when it can't be sent."""
        ...
