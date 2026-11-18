"""The event log (AD-4): one append-only ``events`` row per state transition.

The owner that changes a status calls ``append`` in the same transaction, through the current
unit of work's session. ``events`` never holds personal data: every argument is an ID, a code, an
actor or a time, and ``append`` checks each one before writing anything, raising ``ValueError``
(which names the argument, never its value) on any mismatch:

- ``entity``: a table in ``nextron.db.owners.TABLES``;
- ``entity_id``: a ULID; ``cause_ref``: a ULID or ``None``;
- ``to_status``, ``cause`` and ``from_status`` (``None`` for a creation): lowercase snake_case
  of at most 64 characters;
- ``actor``: ``operator``, ``sender``, ``seed``, ``system:<an owner in OWNERS>`` or
  ``ai:<route>@<version>``, where the route matches ``[a-z][a-z0-9_]{0,63}`` and the version
  ``[A-Za-z0-9][A-Za-z0-9._-]{0,31}``;
- ``at``: an aware ``datetime`` (stored as UTC).

No code reads ``events`` to trigger behaviour; analytics read it, and so does
``history(entity, entity_id)`` (Story 3.15): every column of each row about one entity, as a
``dict``, by ``at``, then ``id``, for a subject access export. It only reads, so it works in a
read-only unit of work too.
"""

import re
from datetime import datetime
from typing import Final

from sqlalchemy import insert, select

from nextron.db.models.base import new_id
from nextron.db.models.events import EventRecord
from nextron.db.owners import OWNERS, TABLES
from nextron.db.uow import current
from nextron.domain.ids import is_ulid

CODE_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")  # matched with fullmatch
ACTOR_PATTERN: Final = re.compile(
    "operator|sender|seed"
    f"|system:(?:{'|'.join(re.escape(owner) for owner in OWNERS)})"
    r"|ai:[a-z][a-z0-9_]{0,63}@[A-Za-z0-9][A-Za-z0-9._-]{0,31}"
)  # matched with fullmatch


def _is_code(value: object) -> bool:
    return isinstance(value, str) and CODE_PATTERN.fullmatch(value) is not None


def _problems(
    entity: object,
    entity_id: object,
    from_status: object,
    to_status: object,
    cause: object,
    cause_ref: object,
    actor: object,
    at: object,
) -> list[str]:
    problems = []
    if not (isinstance(entity, str) and entity in TABLES):
        problems.append("entity must be a table in owners.TABLES")
    if not is_ulid(entity_id):
        problems.append("entity_id must be a ULID")
    if not (from_status is None or _is_code(from_status)):
        problems.append("from_status must be None or a lowercase snake_case code")
    if not _is_code(to_status):
        problems.append("to_status must be a lowercase snake_case code")
    if not _is_code(cause):
        problems.append("cause must be a lowercase snake_case code")
    if not (cause_ref is None or is_ulid(cause_ref)):
        problems.append("cause_ref must be None or a ULID")
    if not (isinstance(actor, str) and ACTOR_PATTERN.fullmatch(actor)):
        problems.append(
            "actor must be operator, sender, seed, system:<owner> or ai:<route>@<version>"
        )
    if not (isinstance(at, datetime) and at.utcoffset() is not None):
        problems.append("at must be an aware datetime")
    return problems


def append(
    *,
    entity: str,
    entity_id: str,
    from_status: str | None,
    to_status: str,
    cause: str,
    cause_ref: str | None,
    actor: str,
    at: datetime,
) -> str:
    """Write one ``events`` row in the current transaction; return its new ULID."""
    problems = _problems(entity, entity_id, from_status, to_status, cause, cause_ref, actor, at)
    if problems:
        raise ValueError("; ".join(problems))
    session = current().session
    event_id = new_id()
    session.execute(
        insert(EventRecord).values(
            id=event_id,
            entity=entity,
            entity_id=entity_id,
            from_status=from_status,
            to_status=to_status,
            cause=cause,
            cause_ref=cause_ref,
            actor=actor,
            at=at,
        )
    )
    return event_id


def history(entity: str, entity_id: str) -> tuple[dict[str, object], ...]:
    """Every column of each ``events`` row about ``entity_id`` of ``entity``, by ``at``, then
    ``id``."""
    table = EventRecord.__table__
    statement = (
        select(table)
        .where(table.c.entity == entity, table.c.entity_id == entity_id)
        .order_by(table.c.at, table.c.id)
    )
    return tuple(dict(row._mapping) for row in current().session.execute(statement))
