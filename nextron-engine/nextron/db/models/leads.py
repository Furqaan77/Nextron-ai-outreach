"""The ``leads`` table (FR-9, AD-3, AD-4; migration ``0011_2_2_leads``): one row per
organization.

Two owners write it, column by column (``nextron.db.owners``):

- the identity group, owner ``discovery`` (``nextron.db.repos.discovery.leads``): ``id``,
  ``name``, the three keys ``web_domain``, ``ch_number`` and ``place_id`` (each nullable and
  unique: ``uq_leads_<column>``), ``segment_id`` (``fk_leads_segment_id_segments``),
  ``country_code`` (nullable; ``ck_leads_country_code``: two uppercase letters),
  ``merged_into`` (nullable, the survivor this Lead was merged into:
  ``fk_leads_merged_into_leads``; ``ck_leads_not_self_merged``) and ``created_at``;
- the pipeline group, owner ``pipeline`` (``nextron.db.repos.pipeline.stages``):
  ``pipeline_stage``, server default ``new``, one of the stages of
  ``nextron.domain.pipeline_stage`` (``ck_leads_pipeline_stage``);
- the profile group, owner ``research`` (``nextron.db.repos.research.profile``; Story 2.9,
  migration ``0022_2_9_signals_evidence``): ``hq_tz``, the IANA zone of the Lead's
  headquarters, and ``hq_evidence_id``, the ``evidence`` row it comes from, both nullable and
  kept NULL together by the code (no CHECK, because the columns were added in place). The
  Evidence id has no foreign key: one would make a ``leads``/``evidence`` cycle. Story 2.10
  (migration ``0023_2_10_primary_contact``) adds ``no_decision_maker``, a nullable Boolean:
  NULL until the Primary Contact's selection first runs, then whether it found no
  decision-maker.

``ck_leads_identity_key``: a Lead that isn't merged holds at least one key. No foreign key
cascades. There is no ``updated_at``, because two owners write the row. Every column is
organization data, PII class ``none``: erasing a Contact never touches the firm's Lead.
"""

from datetime import datetime
from typing import Final

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from nextron.db.models.base import Base, Ulid, UtcDateTime, ulid_pk
from nextron.domain.pipeline_stage import PipelineStage

NAME_LENGTH: Final = 200  # nextron.domain.leads.MAX_NAME_LENGTH
WEB_DOMAIN_LENGTH: Final = 253  # nextron.domain.identity.MAX_DOMAIN_LENGTH
CH_NUMBER_LENGTH: Final = 8
PLACE_ID_LENGTH: Final = 512
COUNTRY_CODE_LENGTH: Final = 2
STAGE_LENGTH: Final = 32
HQ_TZ_LENGTH: Final = 64

STAGE_CHECK: Final = "pipeline_stage IN ({})".format(
    ",".join(f"'{stage}'" for stage in PipelineStage)
)
COUNTRY_CODE_CHECK: Final = "country_code IS NULL OR country_code GLOB '[A-Z][A-Z]'"
IDENTITY_KEY_CHECK: Final = (
    "merged_into IS NOT NULL OR web_domain IS NOT NULL OR ch_number IS NOT NULL "
    "OR place_id IS NOT NULL"
)
NOT_SELF_MERGED_CHECK: Final = "merged_into IS NULL OR merged_into <> id"


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
    no_decision_maker: Mapped[bool | None] = mapped_column(Boolean)
