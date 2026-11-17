"""ORM models: one module per owner's tables, all on ``Base.metadata``.

Import every model module here, so ``Base.metadata`` is complete wherever ``nextron.db.models``
is imported: the owner and PII coverage tests, the Alembic drift test and ``env.py`` all read
it. ``tests/contracts/test_persistence_contracts.py`` fails on a module left out.
"""

from nextron.db.models import events, jobqueue, notify, ops, setup
from nextron.db.models.base import Base, Ulid, UtcDateTime, new_id, ulid_pk
from nextron.db.models.events import EventRecord
from nextron.db.models.jobqueue import JobRecord
from nextron.db.models.notify import AlertRecord, NotificationRecord
from nextron.db.models.ops import HeartbeatRecord
from nextron.db.models.setup import SettingRecord

__all__ = [
    "AlertRecord",
    "Base",
    "EventRecord",
    "HeartbeatRecord",
    "JobRecord",
    "NotificationRecord",
    "SettingRecord",
    "Ulid",
    "UtcDateTime",
    "events",
    "jobqueue",
    "new_id",
    "notify",
    "ops",
    "setup",
    "ulid_pk",
]
