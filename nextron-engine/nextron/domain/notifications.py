"""The closed list of notifications (AD-3): effects that point up the app DAG.

A publisher calls ``uow.publish(<Notification>)`` inside its transaction; the subscribers
registered in ``nextron.main.build_subscribers`` run in the same transaction, before commit, in
their registered order. ``NOTIFICATIONS`` is the whole list, in AD-3's order, and
``nextron.db.uow`` refuses any type outside it (subclasses included). A new notification is an
architecture change, not a story decision.

Each notification is a frozen dataclass holding IDs and scalars only, never personal data. Its
fields are checked when it is constructed:

- a field whose name ends in ``_id`` holds a ULID in canonical text or ``None``;
- any other field holds an ``int``, a ``bool``, ``None`` or a code: lowercase snake_case of at
  most 64 characters (``CODE_PATTERN``);
- ``None`` only in a field with a default: a required field always holds a value.

Anything else raises ``TypeError`` (a disallowed type, or ``None`` in a required field) or
``ValueError`` (a string of the wrong shape). The error names the field, never the value. A
publishing story may add fields under the same rules.
"""

import re
from dataclasses import MISSING, dataclass, fields
from typing import Final

from nextron.domain.ids import is_ulid

CODE_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")  # matched with fullmatch
ID_SUFFIX: Final = "_id"


def _problem(name: str, value: object) -> type[Exception] | None:
    """The error class for a bad field value, or ``None`` when the value is allowed."""
    if value is None:
        return None
    if name.endswith(ID_SUFFIX):
        if not isinstance(value, str):
            return TypeError
        return None if is_ulid(value) else ValueError
    if isinstance(value, int):  # bool is an int
        return None
    if isinstance(value, str):
        return None if CODE_PATTERN.fullmatch(value) else ValueError
    return TypeError


@dataclass(frozen=True, slots=True)
class Notification:
    """Base of the closed list. Publish one of the subclasses in ``NOTIFICATIONS``."""

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            required = field.default is MISSING and field.default_factory is MISSING
            if value is None and required:
                raise TypeError(f"{type(self).__name__}.{field.name} is required, not None")
            error = _problem(field.name, value)
            if error is None:
                continue
            if field.name.endswith(ID_SUFFIX):
                rule = "a ULID string or None"
            else:
                rule = "an int, a bool, None or a lowercase snake_case code (at most 64 characters)"
            raise error(f"{type(self).__name__}.{field.name} must be {rule}")


@dataclass(frozen=True, slots=True)
class SubjectSuppressed(Notification):
    """Published by ``compliance`` when a subject joins the Suppression List."""

    suppression_entry_id: str
    source: str
    contact_id: str | None = None
    lead_id: str | None = None


@dataclass(frozen=True, slots=True)
class ContactErased(Notification):
    """Published by ``compliance`` when a Contact is erased (``cause``: dsr or retention)."""

    contact_id: str
    cause: str


@dataclass(frozen=True, slots=True)
class LeadsMerged(Notification):
    """Published by ``discovery`` when one Lead is merged into a survivor."""

    survivor_lead_id: str
    merged_lead_id: str


@dataclass(frozen=True, slots=True)
class MessageSent(Notification):
    """Published by ``sending`` when an OutboundMessage is sent."""

    om_id: str
    draft_id: str
    kind: str
    step_no: int | None = None


@dataclass(frozen=True, slots=True)
class InboxRemoved(Notification):
    """Published by ``sending`` when a Sending Inbox is removed."""

    inbox_id: str


@dataclass(frozen=True, slots=True)
class Enrolled(Notification):
    """Published by ``sequencing`` when a Lead is enrolled."""

    enrollment_id: str
    lead_id: str


@dataclass(frozen=True, slots=True)
class DraftRejected(Notification):
    """Published by ``approval`` when the Operator rejects a Draft."""

    draft_id: str
    step_no: int | None = None
    bad_fit: bool = False


@dataclass(frozen=True, slots=True)
class EvidenceChanged(Notification):
    """Published by ``research`` when a Lead's Evidence changes."""

    lead_id: str
    evidence_id: str


@dataclass(frozen=True, slots=True)
class EventClosed(Notification):
    """Published by ``research`` when a Signal's Event closes."""

    signal_id: str
    lead_id: str


@dataclass(frozen=True, slots=True)
class FooterChanged(Notification):
    """Published by ``setup`` when a Market's footer changes."""

    market_id: str


NOTIFICATIONS: Final[tuple[type[Notification], ...]] = (
    SubjectSuppressed,
    ContactErased,
    LeadsMerged,
    MessageSent,
    InboxRemoved,
    Enrolled,
    DraftRejected,
    EvidenceChanged,
    EventClosed,
    FooterChanged,
)
