"""``InMemoryAlert``: the in-memory fake of ``nextron.ports.alert.AlertPort``.

``sent`` records every message it accepted, in order, and each delivery's ``message_id`` is
``alert-<n>``. Set ``failure`` to an ``AdapterError`` to make every send raise it (and record
nothing), as a channel that is down would.

``outcomes`` scripts the next sends, one each, before ``failure`` applies: an ``AlertDelivery``
is returned as it is (the message recorded in ``sent``), an exception is raised (nothing
recorded). ``attempts`` records every message it was asked to send, accepted or not, and
``observer``, when set, is called with each message before anything else (a test checks there
what surrounds the call).

``delete`` (Story 3.14) records each ``(recipient, message_id)`` in ``deleted`` and returns the
next of ``delete_outcomes`` (a ``bool`` returned, an exception raised), then ``True``.
"""

from collections import deque
from collections.abc import Callable

from nextron.ports.alert import AlertDelivery, AlertMessage
from nextron.ports.errors import AdapterError


class InMemoryAlert:
    def __init__(self) -> None:
        self.sent: list[AlertMessage] = []
        self.failure: AdapterError | None = None
        self.outcomes: deque[AlertDelivery | BaseException] = deque()
        self.attempts: list[AlertMessage] = []
        self.observer: Callable[[AlertMessage], None] | None = None
        self.deleted: list[tuple[str, str]] = []
        self.delete_outcomes: deque[bool | BaseException] = deque()

    def send(self, message: AlertMessage) -> AlertDelivery:
        self.attempts.append(message)
        if self.observer is not None:
            self.observer(message)
        if self.outcomes:
            outcome = self.outcomes.popleft()
            if isinstance(outcome, BaseException):
                raise outcome
            self.sent.append(message)
            return outcome
        if self.failure is not None:
            raise self.failure
        self.sent.append(message)
        return AlertDelivery(f"alert-{len(self.sent)}")

    def delete(self, recipient: str, message_id: str) -> bool:
        self.deleted.append((recipient, message_id))
        if self.delete_outcomes:
            outcome = self.delete_outcomes.popleft()
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        return True
