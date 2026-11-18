"""``InMemorySinkFiles``: the in-memory fake of ``nextron.ports.sink.SinkFilesPort``.

``erased`` records every address hash it was asked to erase, in order. Each call returns the
next of ``outcomes`` (a ``SinkErasure`` returned, an exception raised), and then ``result``
(``SinkErasure(0, 0)`` unless a test sets it).
"""

from collections import deque

from nextron.ports.sink import SinkErasure


class InMemorySinkFiles:
    def __init__(self) -> None:
        self.erased: list[str] = []
        self.outcomes: deque[SinkErasure | BaseException] = deque()
        self.result = SinkErasure(0, 0)

    def erase_recipient(self, address_hash: str) -> SinkErasure:
        self.erased.append(address_hash)
        if self.outcomes:
            outcome = self.outcomes.popleft()
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        return self.result
