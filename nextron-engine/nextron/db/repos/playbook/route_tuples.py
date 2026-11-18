"""The ``ai_route_tuples`` table's repository (FR-71, AD-3, AD-8, Story 4.4): the only code that
reads or writes it.

Every function works through the current unit of work's session (``nextron.db.uow.current()``):
reads work in a read-only unit of work too, writes only inside the running use case's
transaction. It stores and returns what it is given and checks nothing; the table's checks,
foreign keys and partial unique indexes refuse a bad row (``IntegrityError``). A row is never
updated or deleted.

- ``find(route, model, effort, prompt_version, schema_version, playbook_sha256)``: the row of
  that tuple as a ``StoredTuple`` (``playbook_sha256`` ``None`` matches only a row without a
  Playbook), or ``None``;
- ``get(tuple_id)``: the row ``tuple_id`` as a ``StoredTuple``, or ``None`` (Story 4.5);
- ``every()``: every row, by ``route``, then newest first (``created_at``, then ``id``);
- ``insert(...) -> id``: a new tuple created at ``at``.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import insert as insert_rows
from sqlalchemy import select

from nextron.db.models.base import new_id
from nextron.db.models.playbook import AiRouteTupleRecord
from nextron.db.uow import current


@dataclass(frozen=True, slots=True)
class StoredTuple:
    """One ``ai_route_tuples`` row."""

    id: str
    route: str
    model: str
    effort: str
    prompt_version: str
    prompt_sha256: str
    playbook_sha256: str | None
    schema_version: int
    created_at: datetime


_COLUMNS = (
    AiRouteTupleRecord.id,
    AiRouteTupleRecord.route,
    AiRouteTupleRecord.model,
    AiRouteTupleRecord.effort,
    AiRouteTupleRecord.prompt_version,
    AiRouteTupleRecord.prompt_sha256,
    AiRouteTupleRecord.playbook_sha256,
    AiRouteTupleRecord.schema_version,
    AiRouteTupleRecord.created_at,
)


def find(
    route: str,
    model: str,
    effort: str,
    prompt_version: str,
    schema_version: int,
    playbook_sha256: str | None,
) -> StoredTuple | None:
    """The row of that tuple, or ``None`` when it has none."""
    playbook = (
        AiRouteTupleRecord.playbook_sha256.is_(None)
        if playbook_sha256 is None
        else AiRouteTupleRecord.playbook_sha256 == str(playbook_sha256)
    )
    statement = select(*_COLUMNS).where(
        AiRouteTupleRecord.route == str(route),
        AiRouteTupleRecord.model == str(model),
        AiRouteTupleRecord.effort == str(effort),
        AiRouteTupleRecord.prompt_version == str(prompt_version),
        AiRouteTupleRecord.schema_version == int(schema_version),
        playbook,
    )
    row = current().session.execute(statement).one_or_none()
    return None if row is None else StoredTuple(*row)


def get(tuple_id: str) -> StoredTuple | None:
    """The row ``tuple_id``, or ``None`` when there is none."""
    statement = select(*_COLUMNS).where(AiRouteTupleRecord.id == str(tuple_id))
    row = current().session.execute(statement).one_or_none()
    return None if row is None else StoredTuple(*row)


def every() -> tuple[StoredTuple, ...]:
    """Every row, by route, then newest first."""
    statement = select(*_COLUMNS).order_by(
        AiRouteTupleRecord.route,
        AiRouteTupleRecord.created_at.desc(),
        AiRouteTupleRecord.id.desc(),
    )
    return tuple(StoredTuple(*row) for row in current().session.execute(statement))


def insert(
    *,
    route: str,
    model: str,
    effort: str,
    prompt_version: str,
    prompt_sha256: str,
    playbook_sha256: str | None,
    schema_version: int,
    at: datetime,
) -> str:
    """Store a new tuple, created at ``at``; return its ULID."""
    tuple_id = new_id()
    current().session.execute(
        insert_rows(AiRouteTupleRecord).values(
            id=tuple_id,
            route=route,
            model=model,
            effort=effort,
            prompt_version=prompt_version,
            prompt_sha256=prompt_sha256,
            playbook_sha256=playbook_sha256,
            schema_version=schema_version,
            created_at=at,
        )
    )
    return tuple_id
