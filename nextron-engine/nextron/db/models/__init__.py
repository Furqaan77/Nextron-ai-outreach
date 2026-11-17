"""ORM models: one module per owner's tables, all on ``Base.metadata``.

Import every model module here, so ``Base.metadata`` is complete wherever ``nextron.db.models``
is imported: the owner and PII coverage tests, the Alembic drift test and ``env.py`` all read
it. ``tests/contracts/test_persistence_contracts.py`` fails on a module left out.
"""

from nextron.db.models import (
    crawl,
    events,
    jobqueue,
    leads,
    metering,
    notify,
    ops,
    playbook,
    setup,
)
from nextron.db.models.base import Base, Ulid, UtcDateTime, new_id, ulid_pk
from nextron.db.models.crawl import AtsLinkRecord, CrawledPageRecord, CrawlRunRecord
from nextron.db.models.events import EventRecord
from nextron.db.models.jobqueue import JobRecord
from nextron.db.models.leads import LeadRecord
from nextron.db.models.metering import AiUsageRecord, ApiUsageRecord
from nextron.db.models.notify import AlertRecord, NotificationRecord
from nextron.db.models.ops import BackupRecord, HeartbeatRecord
from nextron.db.models.playbook import AiRouteActivationRecord, PromptSnapshotRecord
from nextron.db.models.setup import MarketRecord, SegmentRecord, SettingRecord

__all__ = [
    "AiRouteActivationRecord",
    "AiUsageRecord",
    "AlertRecord",
    "ApiUsageRecord",
    "AtsLinkRecord",
    "BackupRecord",
    "Base",
    "CrawlRunRecord",
    "CrawledPageRecord",
    "EventRecord",
    "HeartbeatRecord",
    "JobRecord",
    "LeadRecord",
    "MarketRecord",
    "NotificationRecord",
    "PromptSnapshotRecord",
    "SegmentRecord",
    "SettingRecord",
    "Ulid",
    "UtcDateTime",
    "crawl",
    "events",
    "jobqueue",
    "leads",
    "metering",
    "new_id",
    "notify",
    "ops",
    "playbook",
    "setup",
    "ulid_pk",
]
