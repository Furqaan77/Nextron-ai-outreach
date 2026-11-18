"""The ``ai_test_cases`` table's repository (FR-73, AD-3, AD-12, Story 4.5): the only code that
reads or writes it.

Every function works through the current unit of work's session (``nextron.db.uow.current()``):
reads work in a read-only unit of work too, writes only inside the running use case's
transaction. It stores and returns what it is given and checks nothing; the table's checks and
partial unique index refuse a bad row (``IntegrityError``). A row is never deleted; only
``retired_at`` is ever set, and an override row's ``input`` is erased (Story 4.6).

- ``live(test_set, route)``: the live cases (``retired_at`` NULL) of that set and route, by
  ``case_key``, each a ``StoredCase``, whatever their origin;
- ``live_sha256s(test_set, route)``: the ``sha256`` of each of those cases, by ``case_key`` (what
  a case-set fingerprint is taken over, without reading the cases' text);
- ``all_live_fixtures()``: every live ``fixture`` case, by ``test_set``, then ``case_key``
  (``Playbook.sync_test_cases`` never sees an override row);
- ``override_rows(contact_id)``: every ``override`` row of that Contact, live or retired, by
  ``loaded_at`` then ``id``;
- ``new_case_id() -> id``: a new row ULID, for a case whose key is made from its row id (an
  override case);
- ``insert(case, at, *, origin, contact_id, case_id=None) -> id``: a new live case (a
  ``nextron.domain.ai_test_cases.TestCase``), stored as canonical JSON with its SHA-256, loaded
  at ``at``, with its origin and Contact (``case_id`` the row id when given);
- ``retire(case_id, at)``: sets ``retired_at`` = ``at`` on that row when it is live;
- ``erase(case_id, at)``: sets ``input`` to ``ERASED`` (``[erased]``, AD-17's ``redact``) and,
  when the row is live, ``retired_at`` = ``at``.

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
from nextron.domain.ai_test_cases import CaseOrigin, TestCase, canonical_json


ERASED = "[erased]"  # nextron.db.pii_registry: a redacted column's value


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
    origin: str
    contact_id: str | None


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
    AiTestCaseRecord.origin,
    AiTestCaseRecord.contact_id,
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


def live_sha256s(test_set: str, route: str) -> tuple[str, ...]:
    """The ``sha256`` of each live case of ``test_set`` and ``route``, by ``case_key``."""
    statement = (
        select(AiTestCaseRecord.sha256)
        .where(
            AiTestCaseRecord.test_set == str(test_set),
            AiTestCaseRecord.route == str(route),
            AiTestCaseRecord.retired_at.is_(None),
        )
        .order_by(AiTestCaseRecord.case_key)
    )
    return tuple(current().session.scalars(statement))


def all_live_fixtures() -> tuple[StoredCase, ...]:
    """Every live ``fixture`` case, by ``test_set``, then ``case_key``."""
    statement = (
        select(*_COLUMNS)
        .where(
            AiTestCaseRecord.retired_at.is_(None),
            AiTestCaseRecord.origin == CaseOrigin.FIXTURE.value,
        )
        .order_by(AiTestCaseRecord.test_set, AiTestCaseRecord.case_key)
    )
    return tuple(StoredCase(*row) for row in current().session.execute(statement))


def override_rows(contact_id: str) -> tuple[StoredCase, ...]:
    """Every ``override`` row of ``contact_id``, live or retired, by ``loaded_at`` then
    ``id``."""
    statement = (
        select(*_COLUMNS)
        .where(
            AiTestCaseRecord.origin == CaseOrigin.OVERRIDE.value,
            AiTestCaseRecord.contact_id == str(contact_id),
        )
        .order_by(AiTestCaseRecord.loaded_at, AiTestCaseRecord.id)
    )
    return tuple(StoredCase(*row) for row in current().session.execute(statement))


def new_case_id() -> str:
    """A new row ULID."""
    return new_id()


def insert(
    case: TestCase,
    at: datetime,
    *,
    origin: str,
    contact_id: str | None,
    case_id: str | None = None,
) -> str:
    """Store ``case`` as a new live case of ``origin`` (and ``contact_id``), loaded at ``at``;
    return its ULID (``case_id`` when given)."""
    case_id = new_id() if case_id is None else case_id
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
            origin=str(origin),
            contact_id=contact_id,
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


def erase(case_id: str, at: datetime) -> None:
    """Set ``input`` to ``ERASED`` on the case ``case_id``, and ``retired_at`` = ``at`` when it
    is live."""
    current().session.execute(
        update_rows(AiTestCaseRecord)
        .where(AiTestCaseRecord.id == str(case_id))
        .values(input=ERASED)
    )
    retire(case_id, at)
