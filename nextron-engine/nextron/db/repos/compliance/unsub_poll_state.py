"""The ``unsub_poll_state`` repository (FR-70, FR-58, AD-3, AD-20, Story 3.12): the one row
recording how the unsubscribe poll went.

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``): ``get`` works in a read-only unit of work too, ``save`` only
inside the running transaction. The row's id is always ``UNSUB_POLL_STATE_ID``. It stores what
``nextron.app.compliance`` gives it and checks nothing.

- ``PollState``: the row's four state columns;
- ``get()``: the state, or ``None`` before the first recorded poll;
- ``save(state)``: one ``INSERT ... ON CONFLICT(id) DO UPDATE`` that writes all four columns,
  inserting the row when it is missing.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as upsert

from nextron.db.models.compliance import UNSUB_POLL_STATE_ID, UnsubPollStateRecord
from nextron.db.uow import current


@dataclass(frozen=True, slots=True)
class PollState:
    """How the unsubscribe poll went: the last success and the current failure episode."""

    last_success_at: datetime | None
    failing_since: datetime | None
    last_failure_at: datetime | None
    last_failure_code: str | None


_COLUMNS = (
    UnsubPollStateRecord.last_success_at,
    UnsubPollStateRecord.failing_since,
    UnsubPollStateRecord.last_failure_at,
    UnsubPollStateRecord.last_failure_code,
)


def get() -> PollState | None:
    """The poll state, or ``None`` when no poll was recorded yet."""
    statement = select(*_COLUMNS).where(UnsubPollStateRecord.id == UNSUB_POLL_STATE_ID)
    row = current().session.execute(statement).one_or_none()
    return None if row is None else PollState(*row)


def save(state: PollState) -> None:
    """Store ``state``: the row updated, or inserted when it is missing."""
    statement = upsert(UnsubPollStateRecord).values(
        id=UNSUB_POLL_STATE_ID,
        last_success_at=state.last_success_at,
        failing_since=state.failing_since,
        last_failure_at=state.last_failure_at,
        last_failure_code=state.last_failure_code,
    )
    current().session.execute(
        statement.on_conflict_do_update(
            index_elements=[UnsubPollStateRecord.id],
            set_={
                "last_success_at": statement.excluded.last_success_at,
                "failing_since": statement.excluded.failing_since,
                "last_failure_at": statement.excluded.last_failure_at,
                "last_failure_code": statement.excluded.last_failure_code,
            },
        )
    )
