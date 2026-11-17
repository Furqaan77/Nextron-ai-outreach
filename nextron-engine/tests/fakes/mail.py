"""``InMemoryMail``: the in-memory fake of ``nextron.ports.mail.MailPort``.

``sent`` records every message it accepted, in order; each delivery's ids are ``mail-<n>`` and
``thread-<n>``. Set ``failure`` to an ``AdapterError`` to make every send raise it (and record
nothing), as a provider that is down would.
"""

from nextron.ports.errors import AdapterError
from nextron.ports.mail import MailDelivery, MailMessage


class InMemoryMail:
    def __init__(self) -> None:
        self.sent: list[MailMessage] = []
        self.failure: AdapterError | None = None

    def send(self, message: MailMessage) -> MailDelivery:
        if self.failure is not None:
            raise self.failure
        self.sent.append(message)
        number = len(self.sent)
        return MailDelivery(f"mail-{number}", f"thread-{number}")
