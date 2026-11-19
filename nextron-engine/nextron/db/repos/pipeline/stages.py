"""The ``leads`` pipeline group's repository (AD-3, AD-4): the only code that writes
``leads.pipeline_stage``, ``leads.deal_value`` and ``leads.notes``.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It checks nothing; ``nextron.app.pipeline`` applies the
Pipeline Stage machine.

- ``stage(lead_id)``: the stored stage text, or ``None`` when the Lead has no row;
- ``set_stage(lead_id, stage)``: sets it;
- ``deal(lead_id)`` (Story 9.1): the Lead's ``Deal`` (``deal_value``, ``notes``), or ``None``
  when it has no row;
- ``set_deal(lead_id, deal_value, notes)``: sets both;
- ``redact_notes(lead_id, tombstone)``: sets the Lead's ``notes`` to ``tombstone`` when it holds
  any, returning how many rows changed (0 or 1);
- ``last_close(lead_id)``: the Lead's latest ``events`` row (entity ``leads``, read-only) that
  moved it into ``lost`` or ``disqualified`` from another stage (or from none): a ``StageEntry``
  with its ``from_status``, ``to_status`` and ``cause_ref``, latest by ``at`` then ``id``; a
  same-stage row (a deal save on a closed Lead) is no close; ``None`` without one;
- ``leads_reached(lead_ids, stage)`` (Story 9.6): those of ``lead_ids`` with any ``events`` row
  (entity ``leads``, read-only) whose ``to_status`` is ``stage``, as a ``frozenset``;
- ``merged_refs(lead_ids)`` (Story 9.6): for each of ``lead_ids``, the Leads merged into it,
  followed transitively through ``events`` rows (entity ``leads``, read-only) of the Lead with
  cause ``leads_merged`` and a ``cause_ref`` (the merged Lead's id), as a mapping to a
  ``frozenset`` (Leads with none are left out). A visited set makes it cycle-safe and bounds it
  by the number of Leads. A merge that left the survivor's stage unchanged writes no such row,
  so that merged Lead isn't found.
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass

from sqlalchemy import CursorResult, or_, select
from sqlalchemy import update as update_rows

from nextron.db.models.events import EventRecord
from nextron.db.models.leads import LeadRecord
from nextron.db.uow import current
from nextron.domain.pipeline_stage import CLOSED_STAGES, StageCause

LEADS_ENTITY = "leads"


@dataclass(frozen=True, slots=True)
class Deal:
    """A Lead's deal value (micro-dollars) and notes, each ``None`` when unset."""

    deal_value: int | None
    notes: str | None


@dataclass(frozen=True, slots=True)
class StageEntry:
    """An ``events`` row that moved a Lead into a stage."""

    from_status: str | None
    to_status: str
    cause_ref: str | None


def stage(lead_id: str) -> str | None:
    """The stored Pipeline Stage of the Lead ``lead_id``, or ``None``."""
    statement = select(LeadRecord.pipeline_stage).where(LeadRecord.id == lead_id)
    return current().session.execute(statement).scalar_one_or_none()


def set_stage(lead_id: str, stage: str) -> None:
    """Set the Pipeline Stage of the Lead ``lead_id``."""
    current().session.execute(
        update_rows(LeadRecord).where(LeadRecord.id == lead_id).values(pipeline_stage=stage)
    )


def deal(lead_id: str) -> Deal | None:
    """The deal value and notes of the Lead ``lead_id``, or ``None`` without a row."""
    statement = select(LeadRecord.deal_value, LeadRecord.notes).where(LeadRecord.id == lead_id)
    row = current().session.execute(statement).one_or_none()
    return None if row is None else Deal(row[0], row[1])


def set_deal(lead_id: str, deal_value: int | None, notes: str | None) -> None:
    """Set the deal value and notes of the Lead ``lead_id``."""
    current().session.execute(
        update_rows(LeadRecord)
        .where(LeadRecord.id == lead_id)
        .values(deal_value=deal_value, notes=notes)
    )


def redact_notes(lead_id: str, tombstone: str) -> int:
    """Replace the notes of the Lead ``lead_id`` with ``tombstone`` when it has any."""
    result = current().session.execute(
        update_rows(LeadRecord)
        .where(LeadRecord.id == lead_id, LeadRecord.notes.is_not(None))
        .values(notes=tombstone)
    )
    return result.rowcount if isinstance(result, CursorResult) else 0


def last_close(lead_id: str) -> StageEntry | None:
    """The Lead's latest ``events`` row closing it; see the module docstring."""
    statement = (
        select(EventRecord.from_status, EventRecord.to_status, EventRecord.cause_ref)
        .where(
            EventRecord.entity == LEADS_ENTITY,
            EventRecord.entity_id == lead_id,
            EventRecord.to_status.in_([str(each) for each in CLOSED_STAGES]),
            or_(
                EventRecord.from_status.is_(None),
                EventRecord.from_status != EventRecord.to_status,
            ),
        )
        .order_by(EventRecord.at.desc(), EventRecord.id.desc())
        .limit(1)
    )
    row = current().session.execute(statement).one_or_none()
    return None if row is None else StageEntry(row[0], row[1], row[2])


def leads_reached(lead_ids: Collection[str], stage: str) -> frozenset[str]:
    """Those of ``lead_ids`` with an ``events`` row into ``stage``."""
    if not lead_ids:
        return frozenset()
    statement = (
        select(EventRecord.entity_id)
        .where(
            EventRecord.entity == LEADS_ENTITY,
            EventRecord.entity_id.in_(sorted(lead_ids)),
            EventRecord.to_status == stage,
        )
        .distinct()
    )
    return frozenset(current().session.execute(statement).scalars())


def merged_refs(lead_ids: Collection[str]) -> Mapping[str, frozenset[str]]:
    """The Leads merged into each of ``lead_ids``; see the module docstring."""
    children: dict[str, set[str]] = {}
    seen: set[str] = set(lead_ids)
    frontier = sorted(seen)
    while frontier:
        statement = select(EventRecord.entity_id, EventRecord.cause_ref).where(
            EventRecord.entity == LEADS_ENTITY,
            EventRecord.entity_id.in_(frontier),
            EventRecord.cause == str(StageCause.LEADS_MERGED),
            EventRecord.cause_ref.is_not(None),
        )
        found: set[str] = set()
        for parent, child in current().session.execute(statement):
            children.setdefault(parent, set()).add(child)
            if child not in seen:
                found.add(child)
        seen |= found
        frontier = sorted(found)
    result: dict[str, frozenset[str]] = {}
    for lead_id in lead_ids:
        reached: set[str] = set()
        stack = [lead_id]
        while stack:
            for child in children.get(stack.pop(), ()):
                if child != lead_id and child not in reached:
                    reached.add(child)
                    stack.append(child)
        if reached:
            result[lead_id] = frozenset(reached)
    return result
