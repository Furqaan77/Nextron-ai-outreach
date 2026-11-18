"""The ``discovery`` service's run log (Story 8.3, FR-5, AD-7; migration
``0047_8_3_discovery_runs``), written only by ``nextron.db.repos.discovery.runs``.

``discovery_runs``: one row per discovery run. ``id``; ``source`` (``ck_discovery_runs_source``:
``places``); the run's Campaign (``campaign_id``, ``fk_discovery_runs_campaign_id_campaigns``)
and its Segment (``segment_id``, ``fk_discovery_runs_segment_id_segments``); ``country_code``
(``ck_discovery_runs_country_code``: two uppercase letters) and ``location`` (the Operator's
setting text, at most 100 characters); ``query_hash`` (16 hex characters:
``nextron.domain.place_discovery.query_hash`` of the Segment's queries, never the queries);
``status`` (``ck_discovery_runs_status``: one of ``RunStatus``); ``started_at`` and the nullable
``finished_at`` (NULL exactly while ``running``: ``ck_discovery_runs_finished_at``); and the
counts ``seen``, ``created``, ``merged``, ``suppressed``, ``no_website`` and
``market_blocked`` (``ck_discovery_runs_counts``: none negative). Index
``ix_discovery_runs_started_at``. No column holds anything a Places search returned, and every
column is PII class ``none`` (Operator settings, ids, counts and times). No foreign key
cascades.
"""

from datetime import datetime
from typing import Final

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, Ulid, UtcDateTime, ulid_pk
from nextron.domain.place_discovery import (
    COUNT_FIELDS,
    HASH_LENGTH,
    MAX_LOCATION_LENGTH,
    SOURCE_PLACES,
    RunStatus,
)

SOURCE_LENGTH: Final = 16
COUNTRY_CODE_LENGTH: Final = 2
STATUS_LENGTH: Final = 16

SOURCE_CHECK: Final = f"source IN ('{SOURCE_PLACES}')"
STATUS_CHECK: Final = "status IN ({})".format(",".join(f"'{status}'" for status in RunStatus))
COUNTRY_CODE_CHECK: Final = "country_code GLOB '[A-Z][A-Z]'"
FINISHED_AT_CHECK: Final = (
    f"(status = '{RunStatus.RUNNING}') = (finished_at IS NULL)"
)
COUNTS_CHECK: Final = " AND ".join(f"{name} >= 0" for name in COUNT_FIELDS)


class DiscoveryRunRecord(Base):
    """One discovery run: where it searched, how it ended and what its candidates came to."""

    __tablename__ = "discovery_runs"
    __table_args__ = (
        Index("ix_discovery_runs_started_at", "started_at"),
        CheckConstraint(SOURCE_CHECK, name="source"),
        CheckConstraint(STATUS_CHECK, name="status"),
        CheckConstraint(COUNTRY_CODE_CHECK, name="country_code"),
        CheckConstraint(FINISHED_AT_CHECK, name="finished_at"),
        CheckConstraint(COUNTS_CHECK, name="counts"),
    )

    id: Mapped[str] = ulid_pk()
    source: Mapped[str] = mapped_column(String(SOURCE_LENGTH))
    campaign_id: Mapped[str] = mapped_column(Ulid, ForeignKey("campaigns.id"))
    segment_id: Mapped[str] = mapped_column(Ulid, ForeignKey("segments.id"))
    country_code: Mapped[str] = mapped_column(String(COUNTRY_CODE_LENGTH))
    location: Mapped[str] = mapped_column(String(MAX_LOCATION_LENGTH))
    query_hash: Mapped[str] = mapped_column(String(HASH_LENGTH))
    status: Mapped[str] = mapped_column(String(STATUS_LENGTH))
    started_at: Mapped[datetime] = mapped_column(UtcDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    seen: Mapped[int] = mapped_column(Integer)
    created: Mapped[int] = mapped_column(Integer)
    merged: Mapped[int] = mapped_column(Integer)
    suppressed: Mapped[int] = mapped_column(Integer)
    no_website: Mapped[int] = mapped_column(Integer)
    market_blocked: Mapped[int] = mapped_column(Integer)
