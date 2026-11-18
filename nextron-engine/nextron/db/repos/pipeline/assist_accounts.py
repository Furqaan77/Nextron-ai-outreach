"""The ``linkedin_accounts`` repository (FR-49, PRD OQ-5, AD-3; Story 10.2): the only code that
writes the accounts a person sends LinkedIn Tasks from by hand.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): reads work in a read-only unit of work too, writes only inside
the running use case's transaction. It checks nothing; ``nextron.app.pipeline`` does, and the
table's CHECKs and unique indexes back it.

- ``StoredAccount``: one row;
- ``listed()``: every account, oldest first (by ``created_at``, then ``id``);
- ``get(account_id)``: one account, or ``None``;
- ``id_by_label(label)``: the id of the account with that label, or ``None``;
- ``active_ids()``: the ids of the ``active`` accounts (at most one, by the partial index);
- ``insert(...) -> id``: a new row;
- ``update(account_id, ...)``: rewrites every column but ``id`` and ``created_at``.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import insert as insert_rows
from sqlalchemy import select
from sqlalchemy import update as update_rows

from nextron.db.models.base import new_id
from nextron.db.models.pipeline import AssistAccountRecord
from nextron.db.uow import current


@dataclass(frozen=True, slots=True)
class StoredAccount:
    """One ``linkedin_accounts`` row."""

    id: str
    label: str
    owner_name: str
    owner_role: str
    status: str
    tasks_per_weekday: int
    daily_invite_cap: int
    weekly_invite_cap: int
    created_at: datetime
    updated_at: datetime


_COLUMNS = (
    AssistAccountRecord.id,
    AssistAccountRecord.label,
    AssistAccountRecord.owner_name,
    AssistAccountRecord.owner_role,
    AssistAccountRecord.status,
    AssistAccountRecord.tasks_per_weekday,
    AssistAccountRecord.daily_invite_cap,
    AssistAccountRecord.weekly_invite_cap,
    AssistAccountRecord.created_at,
    AssistAccountRecord.updated_at,
)


def listed() -> list[StoredAccount]:
    """Every account, oldest first."""
    rows = current().session.execute(
        select(*_COLUMNS).order_by(AssistAccountRecord.created_at, AssistAccountRecord.id)
    )
    return [StoredAccount(*row) for row in rows]


def get(account_id: str) -> StoredAccount | None:
    """The account ``account_id``, or ``None``."""
    row = (
        current()
        .session.execute(select(*_COLUMNS).where(AssistAccountRecord.id == account_id))
        .one_or_none()
    )
    return None if row is None else StoredAccount(*row)


def id_by_label(label: str) -> str | None:
    """The id of the account labelled ``label``, or ``None``."""
    found = current().session.execute(
        select(AssistAccountRecord.id).where(AssistAccountRecord.label == label)
    )
    return found.scalar_one_or_none()


def active_ids() -> list[str]:
    """The ids of the ``active`` accounts."""
    found = current().session.execute(
        select(AssistAccountRecord.id).where(AssistAccountRecord.status == "active")
    )
    return list(found.scalars())


def insert(
    *,
    label: str,
    owner_name: str,
    owner_role: str,
    status: str,
    tasks_per_weekday: int,
    daily_invite_cap: int,
    weekly_invite_cap: int,
    at: datetime,
) -> str:
    """Store a new account created ``at``; return its new ULID."""
    account_id = new_id()
    current().session.execute(
        insert_rows(AssistAccountRecord).values(
            id=account_id,
            label=label,
            owner_name=owner_name,
            owner_role=owner_role,
            status=status,
            tasks_per_weekday=tasks_per_weekday,
            daily_invite_cap=daily_invite_cap,
            weekly_invite_cap=weekly_invite_cap,
            created_at=at,
            updated_at=at,
        )
    )
    return account_id


def update(
    account_id: str,
    *,
    label: str,
    owner_name: str,
    owner_role: str,
    status: str,
    tasks_per_weekday: int,
    daily_invite_cap: int,
    weekly_invite_cap: int,
    at: datetime,
) -> None:
    """Rewrite the account ``account_id``, updated ``at``."""
    current().session.execute(
        update_rows(AssistAccountRecord)
        .where(AssistAccountRecord.id == account_id)
        .values(
            label=label,
            owner_name=owner_name,
            owner_role=owner_role,
            status=status,
            tasks_per_weekday=tasks_per_weekday,
            daily_invite_cap=daily_invite_cap,
            weekly_invite_cap=weekly_invite_cap,
            updated_at=at,
        )
    )
