"""The ``playbook`` service's tables (AD-3, AD-7).

``company_facts`` holds the Approved Company Facts (FR-72, Story 4.3) as append-only versions,
written only by ``nextron.db.repos.playbook.company_facts`` (migration
``0011_4_3_company_facts``): one row per version, keyed (``fact_id``, ``version``)
(``uq_company_facts_fact_id_version``; ``ck_company_facts_version``: at least 1). Each row holds
the ``nextron.domain.company_facts.FactContent`` fields as typed columns: ``type``
(``ck_company_facts_type``: one of the five ``FactType`` values), ``wording``, ``source_url``,
``allowed_segment_ids`` and ``allowed_market_ids`` (canonical JSON sorted lists of
``segments.id`` and ``markets.id`` ULIDs), ``metric`` (NULL when none), ``metric_confirmed_at``
(the client's confirmation date, NULL when none), ``scope_note`` (NULL when none) and
``last_checked_on``; then ``created_by`` (``ck_company_facts_created_by``: ``seed`` or
``operator``), ``created_at`` and ``retracted_at`` (NULL until the fact is retracted). Every
other column is NOT NULL.

Three triggers keep it append-only (AD-7, the ``events`` precedent):
``trg_company_facts_no_delete`` aborts any ``DELETE``; ``trg_company_facts_no_id_reuse`` aborts
an ``INSERT`` whose ``id``, or whose (``fact_id``, ``version``), already exists (so ``INSERT OR
REPLACE`` can't replace a version); and ``trg_company_facts_immutable`` aborts an ``UPDATE``
that changes any column but ``retracted_at``, or changes ``retracted_at`` once it isn't NULL. A
later migration that rebuilds the table in batch mode must create them again.

Facts are claims about Nextron, its work, its Offer and public regulation, never about a person,
so no column is personal data.
"""

from datetime import date, datetime
from typing import Final

from sqlalchemy import CheckConstraint, Date, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, UtcDateTime, ulid_pk

FACT_ID_LENGTH: Final = 31  # nextron.domain.company_facts.FACT_ID_PATTERN
FACT_TYPE_LENGTH: Final = 16  # company, case_study, offer, regulation, statistic
METRIC_LENGTH: Final = 200  # nextron.domain.company_facts.MAX_METRIC
CREATED_BY_LENGTH: Final = 8  # seed, operator
FACT_TYPE_CHECK: Final = "type IN ('company', 'case_study', 'offer', 'regulation', 'statistic')"
CREATED_BY_CHECK: Final = "created_by IN ('seed', 'operator')"


class CompanyFactRecord(Base):
    """One version of an Approved Company Fact."""

    __tablename__ = "company_facts"
    __table_args__ = (
        UniqueConstraint("fact_id", "version"),
        CheckConstraint("version >= 1", name="version"),
        CheckConstraint(FACT_TYPE_CHECK, name="type"),
        CheckConstraint(CREATED_BY_CHECK, name="created_by"),
    )

    id: Mapped[str] = ulid_pk()
    fact_id: Mapped[str] = mapped_column(String(FACT_ID_LENGTH))
    version: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(FACT_TYPE_LENGTH))
    wording: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    allowed_segment_ids: Mapped[str] = mapped_column(Text)
    allowed_market_ids: Mapped[str] = mapped_column(Text)
    metric: Mapped[str | None] = mapped_column(String(METRIC_LENGTH))
    metric_confirmed_at: Mapped[date | None] = mapped_column(Date)
    scope_note: Mapped[str | None] = mapped_column(Text)
    last_checked_on: Mapped[date] = mapped_column(Date)
    created_by: Mapped[str] = mapped_column(String(CREATED_BY_LENGTH))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    retracted_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
