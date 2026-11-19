"""The ``discovery`` service's run log (Story 8.3, FR-5, AD-7; migrations
``0038_8_3_discovery_runs``, ``0039_8_4_registry_discovery`` and ``0040_8_10_discovery_topup``),
written only by ``nextron.db.repos.discovery.runs``.

``discovery_runs``: one row per discovery run. ``id``; ``source`` (``ck_discovery_runs_source``:
``places`` or, Story 8.4, ``companies_house``); the run's Campaign (``campaign_id``,
``fk_discovery_runs_campaign_id_campaigns``) and its Segment (``segment_id``,
``fk_discovery_runs_segment_id_segments``); ``country_code``
(``ck_discovery_runs_country_code``: two uppercase letters) and ``location`` (the Operator's
setting text, at most 100 characters; set exactly on a ``places`` run that isn't
``pool_sufficient``: ``ck_discovery_runs_location``); ``query_hash`` (16 hex characters: the
hash of the Segment's queries, never the queries); ``status`` (``ck_discovery_runs_status``:
one of ``RunStatus``); ``started_at`` and the nullable ``finished_at`` (NULL exactly while
``running``: ``ck_discovery_runs_finished_at``); and the counts ``seen``, ``created``,
``merged``, ``suppressed``, ``no_website``, ``market_blocked`` and, Story 8.4, ``inactive``
(``ck_discovery_runs_counts``: none negative). Story 8.10 adds
``scheduled_for`` (the local date a scheduled run or a ``pool_sufficient`` row is for; NULL on a
manual run; ``ck_discovery_runs_scheduled_for``: set on every ``pool_sufficient`` row) and
``ready_pool`` (the Campaign's ready Leads, set exactly on a ``pool_sufficient`` row and never
negative: ``ck_discovery_runs_ready_pool``). Index
``ix_discovery_runs_started_at``. No column holds anything a Places search returned, and every
column is PII class ``none`` (Operator settings, ids, counts and times). No foreign key
cascades.
"""

from datetime import date, datetime
from typing import Final

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, Ulid, UtcDateTime, ulid_pk
from nextron.domain.place_discovery import (
    COUNT_FIELDS,
    HASH_LENGTH,
    MAX_LOCATION_LENGTH,
    SOURCES,
    RunStatus,
)

SOURCE_LENGTH: Final = 16
COUNTRY_CODE_LENGTH: Final = 2
STATUS_LENGTH: Final = 16

SOURCE_CHECK: Final = "source IN ({})".format(",".join(f"'{source}'" for source in SOURCES))
LOCATION_CHECK: Final = (
    f"(source = 'places' AND status <> '{RunStatus.POOL_SUFFICIENT}') = (location IS NOT NULL)"
)
SCHEDULED_FOR_CHECK: Final = (
    f"status <> '{RunStatus.POOL_SUFFICIENT}' OR scheduled_for IS NOT NULL"
)
READY_POOL_CHECK: Final = (
    f"(status = '{RunStatus.POOL_SUFFICIENT}') = (ready_pool IS NOT NULL) "
    "AND (ready_pool IS NULL OR ready_pool >= 0)"
)
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
        CheckConstraint(LOCATION_CHECK, name="location"),
        CheckConstraint(COUNTS_CHECK, name="counts"),
        CheckConstraint(SCHEDULED_FOR_CHECK, name="scheduled_for"),
        CheckConstraint(READY_POOL_CHECK, name="ready_pool"),
    )

    id: Mapped[str] = ulid_pk()
    source: Mapped[str] = mapped_column(String(SOURCE_LENGTH))
    campaign_id: Mapped[str] = mapped_column(Ulid, ForeignKey("campaigns.id"))
    segment_id: Mapped[str] = mapped_column(Ulid, ForeignKey("segments.id"))
    country_code: Mapped[str] = mapped_column(String(COUNTRY_CODE_LENGTH))
    location: Mapped[str | None] = mapped_column(String(MAX_LOCATION_LENGTH))
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
    inactive: Mapped[int] = mapped_column(Integer)
    scheduled_for: Mapped[date | None] = mapped_column(Date)  # Story 8.10
    ready_pool: Mapped[int | None] = mapped_column(Integer)  # Story 8.10
