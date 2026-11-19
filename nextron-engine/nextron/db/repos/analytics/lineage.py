"""Which ``events`` rows are about a Lead (Story 9.8; FR-46, FR-51, AD-4): read-only.

A row is **linked** to a Lead when it is the Lead, or when one of its foreign keys points at a
linked row, directly or through a chain of foreign keys. The links are found generically from
the models' metadata (``nextron.db.models.Base.metadata`` unless a caller passes another), so a
table a later story adds with a foreign key into that chain (drafts, enrollments, outbound
messages, replies, call preps) is covered without code here. ``leads`` is the root;
``leads.merged_into`` is not followed (a caller passes the merged Leads' ids itself), and
``events`` is never a linked table. A table's self-reference (``evidence.superseded_by``) and
any cycle are skipped: each chain visits a table at most once.

- ``linked_tables(metadata)``: the name of every table linked to ``leads`` (``leads``
  included), sorted.
- ``lead_events(lead_ids, extra, *, metadata)``: every ``events`` row whose ``entity`` is
  ``leads`` and ``entity_id`` one of ``lead_ids``; or whose ``entity`` is a linked table with
  an ``id`` column and ``entity_id`` the id of one of its rows linked to those Leads (a nested
  ``IN`` subquery per foreign key chain); or whose ``entity`` is a key of ``extra`` and
  ``entity_id`` one of its ids (rows no foreign key links, such as suppression entries). Each
  is a ``StoredEvent``, newest first (``at``, then ``id``, descending).

Every read runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``), so it works in a read-only unit of work. It writes nothing.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import ColumnElement, MetaData, Table, false, or_, select

from nextron.db.models import Base
from nextron.db.models.events import EventRecord
from nextron.db.uow import current

ROOT: Final = "leads"
EVENTS: Final = "events"
ID: Final = "id"


@dataclass(frozen=True, slots=True)
class StoredEvent:
    """One ``events`` row."""

    id: str
    entity: str
    entity_id: str
    from_status: str | None
    to_status: str
    cause: str
    cause_ref: str | None
    actor: str
    at: datetime


def _linked(metadata: MetaData) -> dict[str, Table]:
    tables = dict(metadata.tables)
    linked = {ROOT: tables[ROOT]}
    grew = True
    while grew:
        grew = False
        for name, table in tables.items():
            if name in linked or name == EVENTS:
                continue
            if any(fk.column.table.name in linked for fk in table.foreign_keys):
                linked[name] = table
                grew = True
    return linked


def linked_tables(metadata: MetaData | None = None) -> tuple[str, ...]:
    """Every table linked to ``leads`` by foreign keys, ``leads`` included, by name."""
    return tuple(sorted(_linked(Base.metadata if metadata is None else metadata)))


def _condition(
    table: Table,
    linked: Mapping[str, Table],
    lead_ids: list[str],
    visiting: frozenset[str],
) -> ColumnElement[bool]:
    """The rows of ``table`` linked to ``lead_ids``, as a condition on ``table``."""
    if table.name == ROOT:
        return table.c[ID].in_(lead_ids)
    seen = visiting | {table.name}
    matches: list[ColumnElement[bool]] = []
    for fk in sorted(table.foreign_keys, key=lambda each: (each.parent.name, each.target_fullname)):
        parent = fk.column.table
        if parent.name not in linked or parent.name in seen:
            continue
        if parent.name == ROOT and fk.column.name == ID:
            matches.append(fk.parent.in_(lead_ids))
            continue
        inner = _condition(parent, linked, lead_ids, seen)
        matches.append(fk.parent.in_(select(fk.column).where(inner).correlate(None)))
    return or_(*matches) if matches else false()


def lead_events(
    lead_ids: Collection[str],
    extra: Mapping[str, Collection[str]],
    *,
    metadata: MetaData | None = None,
) -> tuple[StoredEvent, ...]:
    """Every ``events`` row about the Leads ``lead_ids``, a row linked to them or a row of
    ``extra``, newest first; see the module docstring."""
    ids = sorted(set(lead_ids))
    linked = _linked(Base.metadata if metadata is None else metadata)
    matches: list[ColumnElement[bool]] = []
    if ids:
        matches.append((EventRecord.entity == ROOT) & EventRecord.entity_id.in_(ids))
        for name in sorted(linked):
            table = linked[name]
            if name == ROOT or ID not in table.c:
                continue
            rows = (
                select(table.c[ID])
                .where(_condition(table, linked, ids, frozenset()))
                .correlate(None)
            )
            matches.append((EventRecord.entity == name) & EventRecord.entity_id.in_(rows))
    for entity in sorted(extra):
        wanted = sorted(set(extra[entity]))
        if wanted:
            matches.append((EventRecord.entity == entity) & EventRecord.entity_id.in_(wanted))
    if not matches:
        return ()
    statement = (
        select(
            EventRecord.id,
            EventRecord.entity,
            EventRecord.entity_id,
            EventRecord.from_status,
            EventRecord.to_status,
            EventRecord.cause,
            EventRecord.cause_ref,
            EventRecord.actor,
            EventRecord.at,
        )
        .where(or_(*matches))
        .order_by(EventRecord.at.desc(), EventRecord.id.desc())
    )
    return tuple(StoredEvent(*row) for row in current().session.execute(statement))
