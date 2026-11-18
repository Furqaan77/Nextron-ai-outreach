"""``ScriptedUnsubscribe``: the scripted fake of ``nextron.ports.unsubscribe.UnsubscribePort``.

``ScriptedUnsubscribe(*answers)`` answers each ``fetch_pending`` with the next answer: a
``PendingFetch`` (returned), an ``AdapterError`` (raised) or a no-argument callable returning or
raising one of those. Once the script is down to its last answer, that answer repeats, so the
Worker's list stays the same until a test changes it; with no answer at all the list is empty.
``answers`` is a plain list a test may append to.

``hosts`` records the host of every call, in order. Unlike the real adapter it checks no host,
reads no bearer and meters nothing.

``probe(host, *, deadline_s)`` (Story 3.12) records ``(host, deadline_s)`` in ``probes`` and
answers with ``probe_answer``: ``None`` (the Worker is reachable) or an ``AdapterError``
(raised).
"""

from collections.abc import Callable

from nextron.ports.errors import AdapterError
from nextron.ports.unsubscribe import PendingFetch, PendingUnsub

type FetchAnswer = PendingFetch | AdapterError | Callable[[], PendingFetch]

EMPTY: PendingFetch = PendingFetch((), 0)


def pending(*entries: PendingUnsub, malformed: int = 0) -> PendingFetch:
    """A ``PendingFetch`` of ``entries`` and ``malformed`` skipped ones."""
    return PendingFetch(tuple(entries), malformed)


class ScriptedUnsubscribe:
    def __init__(self, *answers: FetchAnswer) -> None:
        self.answers: list[FetchAnswer] = list(answers)
        self.hosts: list[str] = []
        self.probes: list[tuple[str, float]] = []
        self.probe_answer: AdapterError | None = None

    def fetch_pending(self, host: str) -> PendingFetch:
        self.hosts.append(host)
        if not self.answers:
            return EMPTY
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if callable(answer):
            answer = answer()
        if isinstance(answer, AdapterError):
            raise answer
        if not isinstance(answer, PendingFetch):
            raise AssertionError("ScriptedUnsubscribe: an answer must be a PendingFetch")
        return answer

    def probe(self, host: str, *, deadline_s: float) -> None:
        self.probes.append((host, deadline_s))
        if self.probe_answer is not None:
            raise self.probe_answer
