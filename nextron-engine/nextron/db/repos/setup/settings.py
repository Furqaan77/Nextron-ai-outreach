"""The ``settings`` table's repository (AD-3, AD-12): the only code that reads or writes it.

Every function works through the current unit of work's session (``nextron.db.uow.current()``):
reads work in a read-only unit of work too, writes only inside the running use case's
transaction. A value is the canonical text ``nextron.domain.settings_schema.encode`` returned;
this module stores and returns it as it is and checks nothing.

- ``stored_text(key)``: the key's text, or ``None`` without a row;
- ``stored_texts()``: every row as ``{key: text}``, by key;
- ``upsert(key, text, at)``: one ``INSERT ... ON CONFLICT(key) DO UPDATE`` that sets the text
  and ``updated_at``, keeping the row's ``id``;
- ``delete(key)``: removes the key's row, if any.
"""

from datetime import datetime

from sqlalchemy import delete as delete_rows
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert

from nextron.db.models.base import new_id
from nextron.db.models.setup import SettingRecord
from nextron.db.uow import current


def stored_text(key: str) -> str | None:
    """The stored text of ``key``, or ``None`` when it has no row."""
    statement = select(SettingRecord.value).where(SettingRecord.key == str(key))
    return current().session.execute(statement).scalar_one_or_none()


def stored_texts() -> dict[str, str]:
    """Every stored ``{key: text}``, ordered by key."""
    statement = select(SettingRecord.key, SettingRecord.value).order_by(SettingRecord.key)
    return {key: text for key, text in current().session.execute(statement)}


def upsert(key: str, text: str, at: datetime) -> None:
    """Store ``text`` for ``key``, changed at ``at``: a new row, or the existing one updated."""
    statement = insert(SettingRecord).values(id=new_id(), key=str(key), value=text, updated_at=at)
    current().session.execute(
        statement.on_conflict_do_update(
            index_elements=[SettingRecord.key],
            set_={"value": statement.excluded.value, "updated_at": statement.excluded.updated_at},
        )
    )


def delete(key: str) -> None:
    """Remove the row of ``key``, if it has one."""
    current().session.execute(delete_rows(SettingRecord).where(SettingRecord.key == str(key)))
