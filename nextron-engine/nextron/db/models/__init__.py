"""ORM models: one module per owner's tables, all on ``Base.metadata``.

Import every model module here, so ``Base.metadata`` is complete wherever ``nextron.db.models``
is imported: the owner and PII coverage tests, the Alembic drift test and ``env.py`` all read
it. ``tests/contracts/test_persistence_contracts.py`` fails on a module left out.
"""

from nextron.db.models import (
    compliance,
    crawl,
    drafting,
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
    signals,
)
from nextron.db.models.base import Base, Ulid, UtcDateTime, new_id, ulid_pk
from nextron.db.models.compliance import (
    DataSubjectRequestRecord,
    ErasureReceiptRecord,
    ErasureReceiptStoreRecord,
    SuppressionEntryRecord,
    UnsubTokenRecord,
)
from nextron.db.models.crawl import (
    AtsLinkRecord,
    CrawledPageRecord,
    CrawlRunRecord,
    JobPostingRecord,
    PageContactMethodRecord,
)
from nextron.db.models.drafting import FooterSnapshotRecord
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
from nextron.db.models.sending import DomainCheckRecord, SendingDomainRecord, SendingInboxRecord
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
from nextron.db.models.signals import (
    EvidenceRecord,
    ExtractionRunRecord,
    SignalEvidenceRecord,
    SignalRecord,
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
    "DataSubjectRequestRecord",
    "DomainCheckRecord",
    "ErasureReceiptRecord",
    "ErasureReceiptStoreRecord",
    "EventRecord",
    "EvidenceRecord",
    "ExtractionRunRecord",
    "FooterSnapshotRecord",
    "HeartbeatRecord",
    "JobPostingRecord",
    "JobRecord",
    "LeadRecord",
    "MarketRecord",
    "NotificationRecord",
    "OfferRecord",
    "PageContactMethodRecord",
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
    "SignalEvidenceRecord",
    "SignalRecord",
    "StepBriefRecord",
    "SuppressionEntryRecord",
    "Ulid",
    "UnsubTokenRecord",
    "UtcDateTime",
    "VariantRecord",
    "compliance",
    "crawl",
    "drafting",
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
    "signals",
    "ulid_pk",
]
