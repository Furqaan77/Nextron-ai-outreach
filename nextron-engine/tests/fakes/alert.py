"""``InMemoryAlert``: the in-memory fake of ``nextron.ports.alert.AlertPort``.

``sent`` records every message it accepted, in order, and each delivery's ``message_id`` is
``alert-<n>``. Set ``failure`` to an ``AdapterError`` to make every send raise it (and record
nothing), as a channel that is down would.
"""

from nextron.ports.alert import AlertDelivery, AlertMessage
from nextron.ports.errors import AdapterError


class InMemoryAlert:
    def __init__(self) -> None:
        self.sent: list[AlertMessage] = []
        self.failure: AdapterError | None = None

    def send(self, message: AlertMessage) -> AlertDelivery:
        if self.failure is not None:
            raise self.failure
        self.sent.append(message)
        return AlertDelivery(f"alert-{len(self.sent)}")
