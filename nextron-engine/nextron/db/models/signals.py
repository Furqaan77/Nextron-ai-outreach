"""The ``research`` service's Signal tables (FR-10, FR-11, FR-57, AD-7, AD-18, NFR-24; migration
``0022_2_9_signals_evidence``), written only by ``nextron.db.repos.research.signals``. No
foreign key cascades.

**``evidence``**: one immutable observation behind a Signal or a Lead fact. ``lead_id``
(indexed); ``type`` (``quote``, ``absence`` or ``count``: ``nextron.domain.segments.EvidenceType``);
``quote`` (normalised, at most 300 characters) for a quote, or ``sought`` (what was sought or
counted) for an absence or count, with ``number`` for a count or a size fact; ``fact`` (a
``nextron.domain.extraction.FactKind`` or NULL) with ``value`` for a profile fact;
``pages_checked`` (a JSON array of URLs); ``source`` (an ``EvidenceSource``) and the nullable
``source_url``; ``retrieved_at``; ``extracted_by`` (``rule``, ``operator`` or
``ai:<route>@<version>``); the nullable ``person_contact_id`` (a Contact the Evidence is about),
``superseded_by`` (the row that replaced it) and ``retracted_at`` (its source vanished);
``last_confirmed_at`` and ``created_at``. The named CHECKs keep each type's shape (see the
migration). Two triggers, which no model declares, make it immutable: no ``DELETE``, and only
``lead_id``, ``last_confirmed_at`` and, once each, ``superseded_by`` and ``retracted_at``
change.

**``signals``**: a reason to write to a Lead. ``lead_id`` (indexed); ``signal_type`` (the
Segment's key); ``signal_class`` (``event`` or ``attribute``), ``tier`` (1 to 3) and
``score_only``, copied from the Segment when the Signal is created; ``event_date`` (a date, an
Event's only); ``first_seen_at`` and ``retracted_at``.

**``signal_evidence``**: which Evidence supports which Signal (unique per pair, indexed on
``evidence_id``); its own ULID ``id`` keeps the one-``id`` key rule.

**``extraction_runs``**: one row per ``extract_lead`` run: ``lead_id`` (indexed), the nullable
``crawl_run_id`` that queued it, ``route``, the nullable ``actor`` (the route tuple's),
``outcome`` (a ``nextron.domain.extraction.ExtractionOutcome``) with a nullable ``error_code``,
the counts ``pages_sent``, ``pages_truncated``, ``stored`` and ``discarded``, ``started_at`` and
``ended_at``.

A quote or a fact's value may quote a person, so ``evidence.quote`` and ``evidence.value`` are
PII class ``redact``; every other column (IDs, codes, counts, URLs of public pages, numbers and
times) is ``none``.
"""

from collections.abc import Iterable
from datetime import date, datetime
from enum import StrEnum
from typing import Final

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, Ulid, UtcDateTime, ulid_pk
from nextron.domain.extraction import (
    PROFILE_FACTS,
    SIZE_FACTS,
    EvidenceSource,
    ExtractionOutcome,
    FactKind,
)
from nextron.domain.segments import EvidenceType, SignalClass

TYPE_LENGTH: Final = 8
QUOTE_LENGTH: Final = 300  # nextron.domain.extraction.MAX_QUOTE
VALUE_LENGTH: Final = 200  # nextron.domain.extraction.MAX_VALUE
FACT_LENGTH: Final = 32
SOURCE_LENGTH: Final = 16
URL_LENGTH: Final = 2048  # nextron.domain.fetch_policy.MAX_URL_LENGTH
ACTOR_LENGTH: Final = 128  # nextron.db.eventlog.ACTOR_PATTERN fits in it
SIGNAL_TYPE_LENGTH: Final = 64
CLASS_LENGTH: Final = 16
ROUTE_LENGTH: Final = 64
OUTCOME_LENGTH: Final = 32
ERROR_CODE_LENGTH: Final = 64


def _one_of(values: Iterable[StrEnum]) -> str:
    """``('a','b',...)``, in declaration order."""
    return "({})".format(",".join(f"'{value}'" for value in values))


PROFILE_FACT_VALUES: Final = _one_of(kind for kind in FactKind if kind in PROFILE_FACTS)
SIZE_FACT_VALUES: Final = _one_of(kind for kind in FactKind if kind in SIZE_FACTS)
TYPE_CHECK: Final = f"type IN {_one_of(EvidenceType)}"
SOURCE_CHECK: Final = f"source IN {_one_of(EvidenceSource)}"
FACT_CHECK: Final = (
    f"fact IS NULL OR fact IN {PROFILE_FACT_VALUES} OR fact IN {SIZE_FACT_VALUES}"
)
EXTRACTED_BY_CHECK: Final = "extracted_by IN ('rule','operator') OR extracted_by GLOB 'ai:?*@?*'"
QUOTE_SHAPE_CHECK: Final = "type <> 'quote' OR (quote IS NOT NULL AND sought IS NULL)"
SOUGHT_SHAPE_CHECK: Final = "type = 'quote' OR (sought IS NOT NULL AND quote IS NULL)"
NUMBER_SHAPE_CHECK: Final = (
    "(type <> 'count' OR number IS NOT NULL) AND (type <> 'absence' OR number IS NULL)"
)
VALUE_SHAPE_CHECK: Final = (
    f"(value IS NOT NULL) = (fact IS NOT NULL AND fact IN {PROFILE_FACT_VALUES})"
)
SIZE_NUMBER_CHECK: Final = f"fact IS NULL OR fact NOT IN {SIZE_FACT_VALUES} OR number IS NOT NULL"
LENGTHS_CHECK: Final = (
    "(quote IS NULL OR length(quote) BETWEEN 1 AND 300) "
    "AND (sought IS NULL OR length(sought) BETWEEN 1 AND 300) "
    "AND (value IS NULL OR length(value) BETWEEN 1 AND 200)"
)
NUMBER_CHECK: Final = "number IS NULL OR number >= 0"
NOT_SUPERSEDED_AND_RETRACTED_CHECK: Final = "superseded_by IS NULL OR retracted_at IS NULL"
SIGNAL_CLASS_CHECK: Final = f"signal_class IN {_one_of(SignalClass)}"
TIER_CHECK: Final = "tier BETWEEN 1 AND 3"
EVENT_DATE_CHECK: Final = f"event_date IS NULL OR signal_class = '{SignalClass.EVENT}'"
OUTCOME_CHECK: Final = f"outcome IN {_one_of(ExtractionOutcome)}"
COUNTS_CHECK: Final = (
    "pages_sent >= 0 AND pages_truncated >= 0 AND stored >= 0 AND discarded >= 0"
)


class EvidenceRecord(Base):
    """One immutable observation behind a Signal or a Lead fact."""

    __tablename__ = "evidence"
    __table_args__ = (
        Index("ix_evidence_lead_id", "lead_id"),
        CheckConstraint(TYPE_CHECK, name="type"),
        CheckConstraint(SOURCE_CHECK, name="source"),
        CheckConstraint(FACT_CHECK, name="fact"),
        CheckConstraint(EXTRACTED_BY_CHECK, name="extracted_by"),
        CheckConstraint(QUOTE_SHAPE_CHECK, name="quote_shape"),
        CheckConstraint(SOUGHT_SHAPE_CHECK, name="sought_shape"),
        CheckConstraint(NUMBER_SHAPE_CHECK, name="number_shape"),
        CheckConstraint(VALUE_SHAPE_CHECK, name="value_shape"),
        CheckConstraint(SIZE_NUMBER_CHECK, name="size_number"),
        CheckConstraint(LENGTHS_CHECK, name="lengths"),
        CheckConstraint(NUMBER_CHECK, name="number"),
        CheckConstraint(NOT_SUPERSEDED_AND_RETRACTED_CHECK, name="not_superseded_and_retracted"),
    )

    id: Mapped[str] = ulid_pk()
    lead_id: Mapped[str] = mapped_column(Ulid, ForeignKey("leads.id"))
    type: Mapped[str] = mapped_column(String(TYPE_LENGTH))
    quote: Mapped[str | None] = mapped_column(String(QUOTE_LENGTH))
    sought: Mapped[str | None] = mapped_column(String(QUOTE_LENGTH))
    number: Mapped[int | None] = mapped_column(Integer)
    fact: Mapped[str | None] = mapped_column(String(FACT_LENGTH))
    value: Mapped[str | None] = mapped_column(String(VALUE_LENGTH))
    pages_checked: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(SOURCE_LENGTH))
    source_url: Mapped[str | None] = mapped_column(String(URL_LENGTH))
    retrieved_at: Mapped[datetime] = mapped_column(UtcDateTime)
    extracted_by: Mapped[str] = mapped_column(String(ACTOR_LENGTH))
    person_contact_id: Mapped[str | None] = mapped_column(Ulid, ForeignKey("contacts.id"))
    superseded_by: Mapped[str | None] = mapped_column(Ulid, ForeignKey("evidence.id"))
    retracted_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_confirmed_at: Mapped[datetime] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)


class SignalRecord(Base):
    """One Signal on a Lead."""

    __tablename__ = "signals"
    __table_args__ = (
        Index("ix_signals_lead_id", "lead_id"),
        CheckConstraint(SIGNAL_CLASS_CHECK, name="signal_class"),
        CheckConstraint(TIER_CHECK, name="tier"),
        CheckConstraint(EVENT_DATE_CHECK, name="event_date"),
    )

    id: Mapped[str] = ulid_pk()
    lead_id: Mapped[str] = mapped_column(Ulid, ForeignKey("leads.id"))
    signal_type: Mapped[str] = mapped_column(String(SIGNAL_TYPE_LENGTH))
    signal_class: Mapped[str] = mapped_column(String(CLASS_LENGTH))
    tier: Mapped[int] = mapped_column(Integer)
    score_only: Mapped[bool] = mapped_column(Boolean)
    event_date: Mapped[date | None] = mapped_column(Date)
    first_seen_at: Mapped[datetime] = mapped_column(UtcDateTime)
    retracted_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class SignalEvidenceRecord(Base):
    """One Evidence row supporting one Signal."""

    __tablename__ = "signal_evidence"
    __table_args__ = (
        UniqueConstraint("signal_id", "evidence_id"),
        Index("ix_signal_evidence_evidence_id", "evidence_id"),
    )

    id: Mapped[str] = ulid_pk()
    signal_id: Mapped[str] = mapped_column(Ulid, ForeignKey("signals.id"))
    evidence_id: Mapped[str] = mapped_column(Ulid, ForeignKey("evidence.id"))


class ExtractionRunRecord(Base):
    """One ``extract_lead`` run."""

    __tablename__ = "extraction_runs"
    __table_args__ = (
        Index("ix_extraction_runs_lead_id", "lead_id"),
        CheckConstraint(OUTCOME_CHECK, name="outcome"),
        CheckConstraint(COUNTS_CHECK, name="counts"),
    )

    id: Mapped[str] = ulid_pk()
    lead_id: Mapped[str] = mapped_column(Ulid, ForeignKey("leads.id"))
    crawl_run_id: Mapped[str | None] = mapped_column(Ulid, ForeignKey("crawl_runs.id"))
    route: Mapped[str] = mapped_column(String(ROUTE_LENGTH))
    actor: Mapped[str | None] = mapped_column(String(ACTOR_LENGTH))
    outcome: Mapped[str] = mapped_column(String(OUTCOME_LENGTH))
    error_code: Mapped[str | None] = mapped_column(String(ERROR_CODE_LENGTH))
    pages_sent: Mapped[int] = mapped_column(Integer)
    pages_truncated: Mapped[int] = mapped_column(Integer)
    stored: Mapped[int] = mapped_column(Integer)
    discarded: Mapped[int] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(UtcDateTime)
    ended_at: Mapped[datetime] = mapped_column(UtcDateTime)
