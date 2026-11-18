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
    research,
    sending,
    setup,
)
from nextron.db.models.base import Base, Ulid, UtcDateTime, new_id, ulid_pk
from nextron.db.models.crawl import AtsLinkRecord, CrawledPageRecord, CrawlRunRecord
from nextron.db.models.events import EventRecord
from nextron.db.models.jobqueue import JobRecord
from nextron.db.models.leads import LeadRecord
from nextron.db.models.metering import AiUsageRecord, ApiUsageRecord
from nextron.db.models.notify import AlertRecord, NotificationRecord
from nextron.db.models.ops import (
    BackupRecord,
    HeartbeatRecord,
    RecoveryStateRecord,
    RestoreTestRecord,
)
from nextron.db.models.playbook import (
    AiRouteActivationRecord,
    AiRouteTupleRecord,
    AiTestCaseRecord,
    AiTestRunRecord,
    CompanyFactRecord,
    PlaybookCheckRecord,
    PlaybookSnapshotRecord,
    PromptSnapshotRecord,
)
from nextron.db.models.research import ContactRecord, ProvenanceRecord
from nextron.db.models.sending import SendingDomainRecord, SendingInboxRecord
from nextron.db.models.setup import (
    CampaignRecord,
    MarketRecord,
    OfferRecord,
    SegmentRecord,
    SequenceRecord,
    SettingRecord,
    StepBriefRecord,
    VariantRecord,
)

__all__ = [
    "AiRouteActivationRecord",
    "AiRouteTupleRecord",
    "AiTestCaseRecord",
    "AiTestRunRecord",
    "AiUsageRecord",
    "AlertRecord",
    "ApiUsageRecord",
    "AtsLinkRecord",
    "BackupRecord",
    "Base",
    "CampaignRecord",
    "CompanyFactRecord",
    "ContactRecord",
    "CrawlRunRecord",
    "CrawledPageRecord",
    "EventRecord",
    "HeartbeatRecord",
    "JobRecord",
    "LeadRecord",
    "MarketRecord",
    "NotificationRecord",
    "OfferRecord",
    "PlaybookCheckRecord",
    "PlaybookSnapshotRecord",
    "PromptSnapshotRecord",
    "ProvenanceRecord",
    "RecoveryStateRecord",
    "RestoreTestRecord",
    "SegmentRecord",
    "SendingDomainRecord",
    "SendingInboxRecord",
    "SequenceRecord",
    "SettingRecord",
    "StepBriefRecord",
    "Ulid",
    "UtcDateTime",
    "VariantRecord",
    "crawl",
    "events",
    "jobqueue",
    "leads",
    "metering",
    "new_id",
    "notify",
    "ops",
    "playbook",
    "research",
    "sending",
    "setup",
    "ulid_pk",
]
