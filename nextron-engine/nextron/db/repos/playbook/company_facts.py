"""The ``company_facts`` table's repository (FR-72, AD-3, AD-7): the only code that reads or
writes it.

Every function works through the current unit of work's session (``nextron.db.uow.current()``):
reads work in a read-only unit of work too, writes only inside the running use case's
transaction. A version is stored as typed columns from its ``FactContent``:
``allowed_segment_ids`` and ``allowed_market_ids`` as canonical JSON sorted lists
(``json.dumps`` with ``sort_keys``, ``ensure_ascii=False`` and no spaces, as Story 4.1 stores
JSON), dates as ``Date`` columns. A row is decoded back into the fact's file form and validated
again through ``nextron.domain.company_facts.fact_from_form`` (the value rules and rules 1 and
2), so a row that no longer validates (a list column that isn't JSON, a date that isn't
``YYYY-MM-DD``, a ``created_by`` that isn't a ``FactCreator``) raises ``InvalidCompanyFacts``.

- ``all_versions()``: every row as a ``StoredFact``, by ``fact_id`` then ``version``;
- ``versions_of(fact_id)``: the rows of one fact, by ``version`` (empty when it has none);
- ``insert(fact_id, version, content, created_by, at) -> id``: a new version, created at
  ``at``;
- ``retract(fact_id, at) -> ids``: sets ``retracted_at`` on every row of the fact where it is
  NULL, and returns those rows' ids, oldest version first.

There is no other write: the table's triggers refuse a delete and any other change.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, String, select, type_coerce
from sqlalchemy import insert as insert_rows
from sqlalchemy import update as update_rows

from nextron.db.models.base import new_id
from nextron.db.models.playbook import CompanyFactRecord
from nextron.db.uow import current
from nextron.domain.company_facts import (
    FACT_ID_PATTERN,
    FactContent,
    FactCreator,
    FactType,
    InvalidCompanyFacts,
    fact_from_form,
)


@dataclass(frozen=True, slots=True)
class StoredFact:
    """One ``company_facts`` row: one version of a fact, its content decoded."""

    id: str
    fact_id: str
    version: int
    content: FactContent
    created_by: FactCreator
    created_at: datetime
    retracted_at: datetime | None


_COLUMNS = (
    CompanyFactRecord.id,
    CompanyFactRecord.fact_id,
    CompanyFactRecord.version,
    CompanyFactRecord.type,
    CompanyFactRecord.wording,
    CompanyFactRecord.source_url,
    CompanyFactRecord.allowed_segment_ids,
    CompanyFactRecord.allowed_market_ids,
    CompanyFactRecord.metric,
    # the dates as their stored text, so a malformed one fails validation, not the driver
    type_coerce(CompanyFactRecord.metric_confirmed_at, String).label("metric_confirmed_at"),
    CompanyFactRecord.scope_note,
    type_coerce(CompanyFactRecord.last_checked_on, String).label("last_checked_on"),
    CompanyFactRecord.created_by,
    CompanyFactRecord.created_at,
    CompanyFactRecord.retracted_at,
)


def _json_text(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _shown(fact_id: object) -> str:
    return fact_id if isinstance(fact_id, str) and FACT_ID_PATTERN.fullmatch(fact_id) else "0"


def _list_value(text: object, fact_id: object, name: str, column: str) -> object:
    """A list column's JSON, or ``InvalidCompanyFacts`` (on the content field ``name``) when it
    isn't JSON."""
    try:
        return json.loads(text)  # type: ignore[arg-type]
    except (TypeError, ValueError, RecursionError):  # JSONDecodeError is a ValueError
        raise InvalidCompanyFacts(
            f"company_facts[{_shown(fact_id)}].{name}", f"{column} is not valid JSON"
        ) from None


def _stored(row: Row[Any]) -> StoredFact:
    """The ``StoredFact`` of one row of ``_COLUMNS``, validated again."""
    fact_id = row.fact_id
    form: dict[str, object] = {
        "fact_id": fact_id,
        "type": row.type,
        "wording": row.wording,
        "source_url": row.source_url,
        "segments": _list_value(
            row.allowed_segment_ids, fact_id, "segments", "allowed_segment_ids"
        ),
        "markets": _list_value(row.allowed_market_ids, fact_id, "markets", "allowed_market_ids"),
        "last_checked_on": row.last_checked_on,
    }
    for name in ("metric", "metric_confirmed_at", "scope_note"):
        value = getattr(row, name)
        if value is not None:
            form[name] = value
    fact = fact_from_form(form)
    try:
        created_by = FactCreator(row.created_by)
    except ValueError:
        raise InvalidCompanyFacts(
            f"company_facts[{_shown(fact_id)}].created_by", "must be seed or operator"
        ) from None
    return StoredFact(
        id=row.id,
        fact_id=fact.fact_id,
        version=row.version,
        content=fact.content,
        created_by=created_by,
        created_at=row.created_at,
        retracted_at=row.retracted_at,
    )


def all_versions() -> tuple[StoredFact, ...]:
    """Every stored version, ordered by ``fact_id`` then ``version``."""
    statement = select(*_COLUMNS).order_by(CompanyFactRecord.fact_id, CompanyFactRecord.version)
    return tuple(_stored(row) for row in current().session.execute(statement))


def versions_of(fact_id: str) -> tuple[StoredFact, ...]:
    """The stored versions of ``fact_id``, ordered by ``version``."""
    statement = (
        select(*_COLUMNS)
        .where(CompanyFactRecord.fact_id == str(fact_id))
        .order_by(CompanyFactRecord.version)
    )
    return tuple(_stored(row) for row in current().session.execute(statement))


def _values(fact_id: str, content: FactContent) -> dict[str, Any]:
    """The version's content columns (the dates as ``date`` values)."""
    return {
        "fact_id": fact_id,
        "type": str(FactType(content.type)),
        "wording": content.wording,
        "source_url": content.source_url,
        "allowed_segment_ids": _json_text(sorted(content.segments)),
        "allowed_market_ids": _json_text(sorted(content.markets)),
        "metric": content.metric,
        "metric_confirmed_at": content.metric_confirmed_at,
        "scope_note": content.scope_note,
        "last_checked_on": content.last_checked_on,
    }


def insert(
    fact_id: str, version: int, content: FactContent, created_by: FactCreator, at: datetime
) -> str:
    """Store version ``version`` of ``fact_id`` with ``content``, made by ``created_by`` at
    ``at``; return its new ULID."""
    row_id = new_id()
    current().session.execute(
        insert_rows(CompanyFactRecord).values(
            id=row_id,
            **_values(fact_id, content),
            version=version,
            created_by=str(FactCreator(created_by)),
            created_at=at,
            retracted_at=None,
        )
    )
    return row_id


def retract(fact_id: str, at: datetime) -> tuple[str, ...]:
    """Set ``retracted_at`` = ``at`` on every version of ``fact_id`` that isn't retracted; return
    their ids, oldest version first."""
    session = current().session
    found = tuple(
        session.execute(
            select(CompanyFactRecord.id)
            .where(
                CompanyFactRecord.fact_id == str(fact_id),
                CompanyFactRecord.retracted_at.is_(None),
            )
            .order_by(CompanyFactRecord.version)
        ).scalars()
    )
    if found:
        session.execute(
            update_rows(CompanyFactRecord)
            .where(CompanyFactRecord.id.in_(found), CompanyFactRecord.retracted_at.is_(None))
            .values(retracted_at=at)
        )
    return found
