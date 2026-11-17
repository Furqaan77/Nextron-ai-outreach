"""Types every state machine shares (AD-4).

Each machine lives in its own ``nextron/domain/<machine>.py`` with closed ``StrEnum`` statuses,
events and causes, a read-only transition table and a ``transition(current, event)`` function
that returns a ``Transition`` or raises ``IllegalTransition``. The owner that applies a
transition writes the new status and appends one ``events`` row (``from_status``,
``to_status``, ``cause``) in the same transaction.

- ``Transition[S, C](to, cause)``: where an event leads (``to``, a status) and why (``cause``,
  the code the ``events`` row records). Frozen.
- ``IllegalTransition(machine, event, status)``: an event the table has no row for, from that
  status. Its code is ``illegal_transition`` and its message
  ``"<machine>: <event> is not allowed from <status>"``.
"""

from dataclasses import dataclass

from nextron.domain.errors import DomainError


@dataclass(frozen=True, slots=True)
class Transition[S, C]:
    """Where an event leads: the status ``to``, recorded with ``cause``."""

    to: S
    cause: C


class IllegalTransition(DomainError):
    """``event`` has no row in ``machine``'s transition table from ``status``."""

    code = "illegal_transition"

    def __init__(self, machine: str, event: object, status: object) -> None:
        super().__init__(f"{machine}: {event} is not allowed from {status}")
        self.machine = machine
        self.event = str(event)
        self.status = str(status)
