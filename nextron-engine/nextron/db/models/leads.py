"""The ``leads`` table (FR-9, AD-3, AD-4; migration ``0011_2_2_leads``): one row per
organization.

Three owners write it, column by column (``nextron.db.owners``):

- the identity group, owner ``discovery`` (``nextron.db.repos.discovery.leads``): ``id``,
  ``name``, the three keys ``web_domain``, ``ch_number`` and ``place_id`` (each nullable and
  unique: ``uq_leads_<column>``), ``segment_id`` (``fk_leads_segment_id_segments``),
  ``country_code`` (nullable; ``ck_leads_country_code``: two uppercase letters),
  ``merged_into`` (nullable, the survivor this Lead was merged into:
  ``fk_leads_merged_into_leads``; ``ck_leads_not_self_merged``) and ``created_at``;
- the pipeline group, owner ``pipeline`` (``nextron.db.repos.pipeline.stages``):
  ``pipeline_stage``, server default ``new``, one of the stages of
  ``nextron.domain.pipeline_stage`` (``ck_leads_pipeline_stage``), and the deal details (Story
  9.1, migration ``0049_9_1_manual_stages``): ``deal_value`` (BigInteger micro-dollars, a whole
  number of US dollars from 0 to 1,000,000,000) and ``notes`` (plain text, at most 5,000
  characters), both nullable with no CHECK (added in place; ``nextron.domain.manual_stages``
  keeps the limits);
- the profile group, owner ``research`` (``nextron.db.repos.research.profile``): ``hq_tz``,
  the IANA zone of the Lead's headquarters, and ``hq_evidence_id``, the ``evidence`` row it
  comes from (Story 2.9, migration ``0025_2_9_signals_evidence``), both nullable and kept NULL
  together by the code (no CHECK, because the columns were added in place; the Evidence id has
  no foreign key: one would make a ``leads``/``evidence`` cycle); ``contact_methods``
  (Story 2.6, migration ``0027_2_6_contact_methods``), nullable, the canonical JSON of
  ``nextron.domain.widgets.ContactMethods``: how the Lead's website lets a visitor get in
  touch, and every page checked; and ``no_decision_maker`` (Story 2.10, migration
  ``0028_2_10_primary_contact``), a nullable Boolean: NULL until the Primary Contact's
  selection first runs, then whether it found no decision-maker; and the legal form (Story
  2.12, migration ``0036_2_12_legal_form``): ``legal_form`` (the linked Companies House
  record's company type), ``company_status``, ``legal_form_status`` (``confirmed``,
  ``not_corporate`` or ``unconfirmed``; NULL until first checked) and the two ``evidence`` rows
  the finding rests on, ``legal_form_site_evidence_id`` and ``legal_form_registry_evidence_id``
  (no foreign key, like ``hq_evidence_id``), all nullable with no CHECK (added in place);
- the score group, owner ``scoring`` (``nextron.db.repos.scoring.scores``; Story 2.15, migration
  ``0041_2_15_scoring``), every column nullable and NULL until ``score_lead`` first runs:
  ``fit_score``, ``signal_score`` and ``lead_score`` (0 to 100), ``tier`` (``A``, ``B`` or
  ``C``), ``tier_computed_at``, ``score_segment_version`` (the Segment version applied),
  ``score_factors`` (``nextron.domain.scoring.factors_text``), and for a Disqualified Lead
  ``disqualified_rule`` (the disqualifier's key) and ``disqualified_evidence`` (a JSON array of
  Evidence ids). No CHECK, because the columns were added in place.

The identity group also holds a Google Places location (Story 8.2, migration
``0044_8_2_place_coords``): ``place_lat`` and ``place_lng`` (floats) and ``place_coords_at``
(when they were stored), all nullable with no CHECK (added in place). The Maps terms let them be
kept for at most 30 days: the daily ``purge_place_coords`` job nulls all three after that, and
keeps ``place_id``. No other column holds anything a Places search returned.

``website_lookup_at`` (Story 8.4, migration ``0060_8_4_registry_discovery``, nullable, no
CHECK, added in place): when Companies House discovery decided a registry Lead's one website
lookup through Places (attached, or none usable); NULL until then, and after a budget refusal.

``ck_leads_identity_key``: a Lead that isn't merged holds at least one key. No foreign key
cascades. There is no ``updated_at``, because several owners write the row. Every column is
organization data, PII class ``none``, except ``notes`` (Story 9.1: free text that may name a
person, class ``redact``): erasing a Contact sets its Lead's notes to ``[erased]``
(``Pipeline.erase_pii``) and touches nothing else of the firm's Lead.

**``signal_inbox_items``** (Story 2.16, migration ``0054_2_16_signal_inbox``), owner
``discovery`` (``nextron.db.repos.discovery.signal_inbox``): one item pasted into the Signal
Inbox. ``url`` (2,048, stored as a source only and never fetched), ``text`` (10,000), the
nullable ``author`` and ``company`` (200); ``status`` (``ck_signal_inbox_items_status``:
``pending``, ``processed`` or ``flagged``) and ``flag_reason``, set exactly when the item is
flagged (``ck_signal_inbox_items_flag_reason``: a ``FlagReason``); ``submitted_at``;
``processed_at`` and ``lead_id`` (``fk_signal_inbox_items_lead_id_leads``), set exactly when
the item is processed (``ck_signal_inbox_items_processed``); the accepted parse, each nullable:
``parsed_segment``, ``parsed_signal_type``, ``parsed_website`` (a registrable domain),
``parsed_author_name``, ``parsed_author_role``, ``parsed_event_date`` (a date), ``parsed_quote``
(300) and ``extracted_by`` (the parse's actor). Indexed by ``(status, submitted_at)`` and by
``lead_id``. The pasted text, author, company, the parsed author and the quote are personal
data (``redact``).
"""

from datetime import date, datetime
from typing import Final

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, Ulid, UtcDateTime, ulid_pk
from nextron.domain.pipeline_stage import PipelineStage
from nextron.domain.signal_inbox_item import FlagReason, ItemStatus

NAME_LENGTH: Final = 200  # nextron.domain.leads.MAX_NAME_LENGTH
WEB_DOMAIN_LENGTH: Final = 253  # nextron.domain.identity.MAX_DOMAIN_LENGTH
CH_NUMBER_LENGTH: Final = 8
PLACE_ID_LENGTH: Final = 512
COUNTRY_CODE_LENGTH: Final = 2
STAGE_LENGTH: Final = 32
HQ_TZ_LENGTH: Final = 64
LEGAL_FORM_LENGTH: Final = 64  # nextron.domain.legal_form.LEGAL_FORM_PATTERN
COMPANY_STATUS_LENGTH: Final = 32  # nextron.domain.legal_form.COMPANY_STATUS_PATTERN
LEGAL_FORM_STATUS_LENGTH: Final = 16
TIER_LENGTH: Final = 1
RULE_KEY_LENGTH: Final = 31  # nextron.domain.segments.KEY_PATTERN

STAGE_CHECK: Final = "pipeline_stage IN ({})".format(
    ",".join(f"'{stage}'" for stage in PipelineStage)
)
COUNTRY_CODE_CHECK: Final = "country_code IS NULL OR country_code GLOB '[A-Z][A-Z]'"
IDENTITY_KEY_CHECK: Final = (
    "merged_into IS NOT NULL OR web_domain IS NOT NULL OR ch_number IS NOT NULL "
    "OR place_id IS NOT NULL"
)
NOT_SELF_MERGED_CHECK: Final = "merged_into IS NULL OR merged_into <> id"

# signal_inbox_items (Story 2.16)
ITEM_URL_LENGTH: Final = 2048  # nextron.domain.signal_inbox_item.MAX_URL
ITEM_TEXT_LENGTH: Final = 10_000  # nextron.domain.signal_inbox_item.MAX_TEXT
ITEM_FIELD_LENGTH: Final = 200  # author, company and the parsed author's name and role
ITEM_STATUS_LENGTH: Final = 16
FLAG_REASON_LENGTH: Final = 32
SEGMENT_KEY_LENGTH: Final = 64
SIGNAL_TYPE_LENGTH: Final = 64
QUOTE_LENGTH: Final = 300  # nextron.domain.extraction.MAX_QUOTE
ACTOR_LENGTH: Final = 128
ITEM_STATUS_CHECK: Final = "status IN ({})".format(",".join(f"'{each}'" for each in ItemStatus))
FLAG_REASON_CHECK: Final = (
    f"(status = '{ItemStatus.FLAGGED}' AND flag_reason IN "
    + "({})".format(",".join(f"'{each}'" for each in FlagReason))
    + f") OR (status <> '{ItemStatus.FLAGGED}' AND flag_reason IS NULL)"
)
PROCESSED_CHECK: Final = (
    f"(status = '{ItemStatus.PROCESSED}' AND processed_at IS NOT NULL AND lead_id IS NOT NULL)"
    f" OR (status <> '{ItemStatus.PROCESSED}' AND processed_at IS NULL AND lead_id IS NULL)"
)


class LeadRecord(Base):
    """One Lead: an organization, its keys and its Pipeline Stage."""

    __tablename__ = "leads"
    __table_args__ = (
        Index("uq_leads_web_domain", "web_domain", unique=True),
        Index("uq_leads_ch_number", "ch_number", unique=True),
        Index("uq_leads_place_id", "place_id", unique=True),
        CheckConstraint(STAGE_CHECK, name="pipeline_stage"),
        CheckConstraint(COUNTRY_CODE_CHECK, name="country_code"),
        CheckConstraint(IDENTITY_KEY_CHECK, name="identity_key"),
        CheckConstraint(NOT_SELF_MERGED_CHECK, name="not_self_merged"),
    )

    id: Mapped[str] = ulid_pk()
    name: Mapped[str] = mapped_column(String(NAME_LENGTH))
    web_domain: Mapped[str | None] = mapped_column(String(WEB_DOMAIN_LENGTH))
    ch_number: Mapped[str | None] = mapped_column(String(CH_NUMBER_LENGTH))
    place_id: Mapped[str | None] = mapped_column(String(PLACE_ID_LENGTH))
    segment_id: Mapped[str] = mapped_column(Ulid, ForeignKey("segments.id"))
    country_code: Mapped[str | None] = mapped_column(String(COUNTRY_CODE_LENGTH))
    merged_into: Mapped[str | None] = mapped_column(Ulid, ForeignKey("leads.id"))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    pipeline_stage: Mapped[str] = mapped_column(
        String(STAGE_LENGTH), server_default=text("'new'")
    )
    hq_tz: Mapped[str | None] = mapped_column(String(HQ_TZ_LENGTH))
    hq_evidence_id: Mapped[str | None] = mapped_column(Ulid)
    contact_methods: Mapped[str | None] = mapped_column(Text)
    no_decision_maker: Mapped[bool | None] = mapped_column(Boolean)
    legal_form: Mapped[str | None] = mapped_column(String(LEGAL_FORM_LENGTH))
    company_status: Mapped[str | None] = mapped_column(String(COMPANY_STATUS_LENGTH))
    legal_form_status: Mapped[str | None] = mapped_column(String(LEGAL_FORM_STATUS_LENGTH))
    legal_form_site_evidence_id: Mapped[str | None] = mapped_column(Ulid)
    legal_form_registry_evidence_id: Mapped[str | None] = mapped_column(Ulid)
    fit_score: Mapped[int | None] = mapped_column(Integer)
    signal_score: Mapped[int | None] = mapped_column(Integer)
    lead_score: Mapped[int | None] = mapped_column(Integer)
    tier: Mapped[str | None] = mapped_column(String(TIER_LENGTH))
    tier_computed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    score_segment_version: Mapped[int | None] = mapped_column(Integer)
    score_factors: Mapped[str | None] = mapped_column(Text)
    disqualified_rule: Mapped[str | None] = mapped_column(String(RULE_KEY_LENGTH))
    disqualified_evidence: Mapped[str | None] = mapped_column(Text)
    place_lat: Mapped[float | None] = mapped_column(Float)
    place_lng: Mapped[float | None] = mapped_column(Float)
    place_coords_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    deal_value: Mapped[int | None] = mapped_column(BigInteger)
    notes: Mapped[str | None] = mapped_column(Text)
    # Story 8.4 (migration 0060): when a registry Lead's website lookup was decided
    website_lookup_at: Mapped[datetime | None] = mapped_column(UtcDateTime)


class SignalInboxItemRecord(Base):
    """One item pasted into the Signal Inbox (Story 2.16), owned by ``discovery``."""

    __tablename__ = "signal_inbox_items"
    __table_args__ = (
        Index("ix_signal_inbox_items_status_submitted_at", "status", "submitted_at"),
        Index("ix_signal_inbox_items_lead_id", "lead_id"),
        CheckConstraint(ITEM_STATUS_CHECK, name="status"),
        CheckConstraint(FLAG_REASON_CHECK, name="flag_reason"),
        CheckConstraint(PROCESSED_CHECK, name="processed"),
    )

    id: Mapped[str] = ulid_pk()
    url: Mapped[str] = mapped_column(String(ITEM_URL_LENGTH))
    text: Mapped[str] = mapped_column(String(ITEM_TEXT_LENGTH))
    author: Mapped[str | None] = mapped_column(String(ITEM_FIELD_LENGTH))
    company: Mapped[str | None] = mapped_column(String(ITEM_FIELD_LENGTH))
    status: Mapped[str] = mapped_column(String(ITEM_STATUS_LENGTH))
    flag_reason: Mapped[str | None] = mapped_column(String(FLAG_REASON_LENGTH))
    submitted_at: Mapped[datetime] = mapped_column(UtcDateTime)
    processed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    lead_id: Mapped[str | None] = mapped_column(Ulid, ForeignKey("leads.id"))
    parsed_segment: Mapped[str | None] = mapped_column(String(SEGMENT_KEY_LENGTH))
    parsed_signal_type: Mapped[str | None] = mapped_column(String(SIGNAL_TYPE_LENGTH))
    parsed_website: Mapped[str | None] = mapped_column(String(WEB_DOMAIN_LENGTH))
    parsed_author_name: Mapped[str | None] = mapped_column(String(ITEM_FIELD_LENGTH))
    parsed_author_role: Mapped[str | None] = mapped_column(String(ITEM_FIELD_LENGTH))
    parsed_event_date: Mapped[date | None] = mapped_column(Date)
    parsed_quote: Mapped[str | None] = mapped_column(String(QUOTE_LENGTH))
    extracted_by: Mapped[str | None] = mapped_column(String(ACTOR_LENGTH))
