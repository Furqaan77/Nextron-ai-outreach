"""The ``ai_test_cases`` table's repository (FR-73, AD-3, AD-12, Story 4.5): the only code that
reads or writes it.

Every function works through the current unit of work's session (``nextron.db.uow.current()``):
reads work in a read-only unit of work too, writes only inside the running use case's
transaction. It stores and returns what it is given and checks nothing; the table's checks and
partial unique index refuse a bad row (``IntegrityError``). A row is never deleted, and only
``retired_at`` is ever set.

- ``live(test_set, route)``: the live cases (``retired_at`` NULL) of that set and route, by
  ``case_key``, each a ``StoredCase``;
- ``all_live()``: every live case, by ``test_set``, then ``case_key``;
- ``insert(case, at) -> id``: a new live case (a ``nextron.domain.ai_test_cases.TestCase``),
  stored as canonical JSON with its SHA-256, loaded at ``at``;
- ``retire(case_id, at)``: sets ``retired_at`` = ``at`` on that row when it is live.

``StoredCase`` keeps ``input``, ``expected`` and ``covers`` as the stored JSON text;
``nextron.domain.ai_test_cases.stored_case`` turns a row back into a ``TestCase``.
"""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import insert as insert_rows
from sqlalchemy import select
from sqlalchemy import update as update_rows

from nextron.db.models.base import new_id
from nextron.db.models.playbook import AiTestCaseRecord
from nextron.db.uow import current
from nextron.domain.ai_test_cases import TestCase, canonical_json


@dataclass(frozen=True, slots=True)
class StoredCase:
    """One ``ai_test_cases`` row."""

    id: str
    case_key: str
    test_set: str
    route: str
    input: str = field(repr=False)
    expected: str = field(repr=False)
    covers: str
    sha256: str
    loaded_at: datetime
    retired_at: datetime | None


_COLUMNS = (
    AiTestCaseRecord.id,
    AiTestCaseRecord.case_key,
    AiTestCaseRecord.test_set,
    AiTestCaseRecord.route,
    AiTestCaseRecord.input,
    AiTestCaseRecord.expected,
    AiTestCaseRecord.covers,
    AiTestCaseRecord.sha256,
    AiTestCaseRecord.loaded_at,
    AiTestCaseRecord.retired_at,
)


def live(test_set: str, route: str) -> tuple[StoredCase, ...]:
    """The live cases of ``test_set`` and ``route``, by ``case_key``."""
    statement = (
        select(*_COLUMNS)
        .where(
            AiTestCaseRecord.test_set == str(test_set),
            AiTestCaseRecord.route == str(route),
            AiTestCaseRecord.retired_at.is_(None),
        )
        .order_by(AiTestCaseRecord.case_key)
    )
    return tuple(StoredCase(*row) for row in current().session.execute(statement))


def all_live() -> tuple[StoredCase, ...]:
    """Every live case, by ``test_set``, then ``case_key``."""
    statement = (
        select(*_COLUMNS)
        .where(AiTestCaseRecord.retired_at.is_(None))
        .order_by(AiTestCaseRecord.test_set, AiTestCaseRecord.case_key)
    )
    return tuple(StoredCase(*row) for row in current().session.execute(statement))


def insert(case: TestCase, at: datetime) -> str:
    """Store ``case`` as a new live case, loaded at ``at``; return its ULID."""
    case_id = new_id()
    current().session.execute(
        insert_rows(AiTestCaseRecord).values(
            id=case_id,
            case_key=case.case_key,
            test_set=case.test_set.value,
            route=case.route,
            input=canonical_json(case.input),
            expected=canonical_json(case.expected),
            covers=canonical_json(list(case.covers)),
            sha256=case.sha256,
            loaded_at=at,
            retired_at=None,
        )
    )
    return case_id


def retire(case_id: str, at: datetime) -> None:
    """Set ``retired_at`` = ``at`` on the case ``case_id`` when it is live."""
    current().session.execute(
        update_rows(AiTestCaseRecord)
        .where(AiTestCaseRecord.id == str(case_id), AiTestCaseRecord.retired_at.is_(None))
        .values(retired_at=at)
    )
