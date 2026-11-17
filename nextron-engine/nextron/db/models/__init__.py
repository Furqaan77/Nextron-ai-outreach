"""ORM models: one module per owner's tables, all on ``Base.metadata``.

Import every model module here, so ``Base.metadata`` is complete wherever ``nextron.db.models``
is imported: the owner and PII coverage tests, the Alembic drift test and ``env.py`` all read
it. ``tests/contracts/test_persistence_contracts.py`` fails on a module left out.
"""

from nextron.db.models import (
    compliance,
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
from nextron.db.models.compliance import SuppressionEntryRecord, UnsubTokenRecord
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
    CompanyFactRecord,
    PromptSnapshotRecord,
)
from nextron.db.models.research import ContactRecord, ProvenanceRecord
from nextron.db.models.sending import SendingDomainRecord, SendingInboxRecord
from nextron.db.models.setup import (
    MarketRecord,
    SegmentRecord,
    SequenceRecord,
    SettingRecord,
    StepBriefRecord,
)

__all__ = [
    "AiRouteActivationRecord",
    "AiUsageRecord",
    "AlertRecord",
    "ApiUsageRecord",
    "BackupRecord",
    "Base",
    "CompanyFactRecord",
    "ContactRecord",
    "EventRecord",
    "HeartbeatRecord",
    "JobRecord",
    "LeadRecord",
    "MarketRecord",
    "NotificationRecord",
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
    "SuppressionEntryRecord",
    "Ulid",
    "UnsubTokenRecord",
    "UtcDateTime",
    "compliance",
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
