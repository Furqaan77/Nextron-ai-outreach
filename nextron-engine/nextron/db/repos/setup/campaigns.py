"""The ``campaigns`` and ``variants`` tables' repository (FR-3, AD-3, AD-4, NFR-3): the only code
that reads or writes them.

Every function works through the current unit of work's session (``nextron.db.uow.current()``):
reads work in a read-only unit of work too, writes only inside the running use case's
transaction. A Campaign is one ``campaigns`` row and one ``variants`` row per Variant. A
Variant's ``step_angles`` is the canonical JSON list of its notes' file forms (``[]`` for none;
``json.dumps`` with ``sort_keys``, ``ensure_ascii=False`` and no spaces).

**Decoding.** A Campaign is read with the keys of its Segment, Offer and Sequence and the code
and current ``country_code`` of its Market (joined, so the country is read live), and its
Variants by name. It is decoded into its stored form and validated again through
``nextron.domain.campaign.stored_campaign_from_form`` (Story 11.8: any number of Variants, none
active included, at most 2 active), so a row set that no longer validates (a ``step_angles``
that isn't JSON, more than 2 active Variants, two names equal case-insensitively) raises
``InvalidCampaigns``; so do a ``status`` that isn't a ``CampaignStatus`` and an LIA whose
reference breaks the text rules, whose date isn't a real ``YYYY-MM-DD`` date, or that has only
one of the two columns set.

- ``all_campaigns()``: every Campaign as a ``StoredCampaign``, by key;
- ``keys()`` (Story 11.8): every stored Campaign's key, a ``frozenset``, without decoding (the
  seed sync's managed mode); ``ids_for_pair(segment_id, market_id)``: the ids of the Campaigns
  of that pair (at most one), without decoding;
- ``by_id(campaign_id)``: the Campaign ``campaign_id``, or ``None`` (also for a text that isn't
  a ULID);
- ``insert(campaign, *, segment_id, market_id, offer_id, sequence_id, at) -> id``: a new
  Campaign at ``draft``, version 1, with no LIA, and a row per Variant of ``campaign`` (none
  included: a dashboard Campaign starts with no Variant);
- ``update_config(campaign_id, *, name, offer_id, sequence_id, version, at)``: sets those
  columns and ``updated_at`` (never the Segment, the Market, the status or the LIA);
- ``update_definition(campaign_id, *, name, segment_id, market_id, offer_id, sequence_id,
  version, at, clear_lia=False)`` (Story 11.8): sets those columns and ``updated_at`` (never the
  key or the status); with ``clear_lia`` it also sets ``lia_ref`` and ``lia_date`` NULL (a
  Segment or Market change: the stored LIA was made for the old pair);
- ``insert_variant(campaign_id, variant, at) -> id`` and ``update_variant(variant_id, variant,
  at)`` (its ``name``, ``status`` and ``step_angles``);
- ``set_variant_status(variant_id, status, at)`` (Story 11.8): sets its ``status`` and
  ``updated_at``;
- ``set_status(campaign_id, from_status, to_status, at) -> bool``: one conditional ``UPDATE``
  (``WHERE id = ? AND status = from_status``); true when exactly one row changed;
- ``set_lia(campaign_id, lia, at)``: sets ``lia_ref``, ``lia_date`` and ``updated_at``.

There is no delete: a Variant is retired by setting it ``inactive``.
"""

import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, String, select, type_coerce
from sqlalchemy import insert as insert_rows
from sqlalchemy import update as update_rows

from nextron.db.models.base import new_id
from nextron.db.models.setup import (
    CampaignRecord,
    MarketRecord,
    OfferRecord,
    SegmentRecord,
    SequenceRecord,
    VariantRecord,
)
from nextron.db.uow import current
from nextron.domain.campaign import (
    KEY_PATTERN,
    CampaignDef,
    CampaignStatus,
    InvalidCampaigns,
    Lia,
    StepAngleNote,
    VariantDef,
    VariantStatus,
    stored_campaign_from_form,
    stored_lia,
)
from nextron.domain.ids import is_ulid

FIRST_VERSION = 1
STATUSES_REASON = "must be draft, active or paused"


@dataclass(frozen=True, slots=True)
class StoredVariant:
    """One ``variants`` row, decoded."""

    id: str
    name: str
    status: VariantStatus
    step_angles: tuple[StepAngleNote, ...]
    created_at: datetime
    updated_at: datetime

    @property
    def definition(self) -> VariantDef:
        return VariantDef(self.name, self.status, self.step_angles)


@dataclass(frozen=True, slots=True)
class StoredCampaign:
    """One ``campaigns`` row with its Variants (by name), decoded; ``country_code`` is its
    Market's, read with it; ``campaign`` is its file form's ``CampaignDef``."""

    id: str
    key: str
    name: str
    version: int
    segment_id: str
    market_id: str
    offer_id: str
    sequence_id: str
    status: CampaignStatus
    lia: Lia | None
    country_code: str
    created_at: datetime
    updated_at: datetime
    campaign: CampaignDef
    variants: tuple[StoredVariant, ...]


_CAMPAIGN_COLUMNS = (
    CampaignRecord.id,
    CampaignRecord.key,
    CampaignRecord.name,
    CampaignRecord.segment_id,
    CampaignRecord.market_id,
    CampaignRecord.offer_id,
    CampaignRecord.sequence_id,
    CampaignRecord.status,
    CampaignRecord.lia_ref,
    # the date as its stored text, so a malformed one fails validation, not the driver
    type_coerce(CampaignRecord.lia_date, String).label("lia_date"),
    CampaignRecord.version,
    CampaignRecord.created_at,
    CampaignRecord.updated_at,
    SegmentRecord.key.label("segment_key"),
    MarketRecord.code.label("market_code"),
    MarketRecord.country_code.label("country_code"),
    OfferRecord.key.label("offer_key"),
    SequenceRecord.key.label("sequence_key"),
)
_VARIANT_COLUMNS = (
    VariantRecord.id,
    VariantRecord.campaign_id,
    VariantRecord.name,
    VariantRecord.status,
    VariantRecord.step_angles,
    VariantRecord.created_at,
    VariantRecord.updated_at,
)


def _campaigns_query() -> Any:
    return (
        select(*_CAMPAIGN_COLUMNS)
        .join(SegmentRecord, SegmentRecord.id == CampaignRecord.segment_id)
        .join(MarketRecord, MarketRecord.id == CampaignRecord.market_id)
        .join(OfferRecord, OfferRecord.id == CampaignRecord.offer_id)
        .join(SequenceRecord, SequenceRecord.id == CampaignRecord.sequence_id)
    )


def _json_text(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _shown(key: object) -> str:
    return key if isinstance(key, str) and KEY_PATTERN.fullmatch(key) else "0"


def _angles_value(text: object, key: object) -> object:
    """A Variant's ``step_angles`` JSON, or ``InvalidCampaigns`` when it isn't JSON."""
    try:
        return json.loads(text)  # type: ignore[arg-type]
    except (TypeError, ValueError, RecursionError):  # JSONDecodeError is a ValueError
        raise InvalidCampaigns(
            f"campaigns[{_shown(key)}].variants", "a stored Variant's step_angles is not valid JSON"
        ) from None


def _stored(row: Row[Any], variants: Sequence[Row[Any]]) -> StoredCampaign:
    """The ``StoredCampaign`` of one joined ``campaigns`` row and its Variant rows by name,
    validated again."""
    form = {
        "key": row.key,
        "name": row.name,
        "segment": row.segment_key,
        "market": row.market_code,
        "offer": row.offer_key,
        "sequence": row.sequence_key,
        "variants": [
            {
                "name": variant.name,
                "status": variant.status,
                "step_angles": _angles_value(variant.step_angles, row.key),
            }
            for variant in variants
        ],
    }
    campaign = stored_campaign_from_form(form)
    try:
        status = CampaignStatus(row.status)
    except ValueError:
        raise InvalidCampaigns(f"campaigns[{_shown(row.key)}].status", STATUSES_REASON) from None
    decoded = [
        StoredVariant(
            id=variant.id,
            name=found.name,
            status=found.status,
            step_angles=found.step_angles,
            created_at=variant.created_at,
            updated_at=variant.updated_at,
        )
        for variant, found in zip(variants, campaign.variants, strict=True)
    ]
    return StoredCampaign(
        id=row.id,
        key=campaign.key,
        name=campaign.name,
        version=row.version,
        segment_id=row.segment_id,
        market_id=row.market_id,
        offer_id=row.offer_id,
        sequence_id=row.sequence_id,
        status=status,
        lia=stored_lia(row.lia_ref, row.lia_date, f"campaigns[{_shown(row.key)}]"),
        country_code=row.country_code,
        created_at=row.created_at,
        updated_at=row.updated_at,
        campaign=campaign,
        variants=tuple(decoded),
    )


def _variants_of(campaign_ids: Sequence[str]) -> dict[str, list[Row[Any]]]:
    """The Variant rows of each of ``campaign_ids``, by name."""
    found: dict[str, list[Row[Any]]] = defaultdict(list)
    if not campaign_ids:
        return found
    statement = (
        select(*_VARIANT_COLUMNS)
        .where(VariantRecord.campaign_id.in_(campaign_ids))
        .order_by(VariantRecord.campaign_id, VariantRecord.name)
    )
    for variant in current().session.execute(statement):
        found[variant.campaign_id].append(variant)
    return found


def all_campaigns() -> tuple[StoredCampaign, ...]:
    """Every stored Campaign with its Variants, ordered by key."""
    rows = list(current().session.execute(_campaigns_query().order_by(CampaignRecord.key)))
    variants = _variants_of([row.id for row in rows])
    return tuple(_stored(row, variants.get(row.id, [])) for row in rows)


def keys() -> frozenset[str]:
    """Every stored Campaign's key, without decoding."""
    return frozenset(current().session.scalars(select(CampaignRecord.key)))


def ids_for_pair(segment_id: str, market_id: str) -> frozenset[str]:
    """The ids of the stored Campaigns of this (Segment, Market) pair (at most one), without
    decoding."""
    statement = select(CampaignRecord.id).where(
        CampaignRecord.segment_id == segment_id, CampaignRecord.market_id == market_id
    )
    return frozenset(current().session.scalars(statement))


def by_id(campaign_id: str) -> StoredCampaign | None:
    """The stored Campaign ``campaign_id`` with its Variants, or ``None`` when it has no row
    (or ``campaign_id`` isn't a ULID)."""
    if not is_ulid(campaign_id):
        return None
    statement = _campaigns_query().where(CampaignRecord.id == campaign_id)
    row = current().session.execute(statement).one_or_none()
    if row is None:
        return None
    return _stored(row, _variants_of([row.id]).get(row.id, []))


def _variant_values(variant: VariantDef) -> dict[str, Any]:
    angles = [{"step": angle.step, "note": angle.note} for angle in variant.step_angles]
    return {
        "name": variant.name,
        "status": str(VariantStatus(variant.status)),
        "step_angles": _json_text(angles),
    }


def insert_variant(campaign_id: str, variant: VariantDef, at: datetime) -> str:
    """Store a Variant of the Campaign ``campaign_id``, created at ``at``; return its ULID."""
    variant_id = new_id()
    current().session.execute(
        insert_rows(VariantRecord).values(
            id=variant_id,
            campaign_id=campaign_id,
            **_variant_values(variant),
            created_at=at,
            updated_at=at,
        )
    )
    return variant_id


def update_variant(variant_id: str, variant: VariantDef, at: datetime) -> None:
    """Set the name, status and notes of the Variant ``variant_id``, changed at ``at``."""
    current().session.execute(
        update_rows(VariantRecord)
        .where(VariantRecord.id == variant_id)
        .values(**_variant_values(variant), updated_at=at)
    )


def insert(
    campaign: CampaignDef,
    *,
    segment_id: str,
    market_id: str,
    offer_id: str,
    sequence_id: str,
    at: datetime,
) -> str:
    """Store a new Campaign at ``draft``, version 1, with no LIA, and its Variants, created at
    ``at``; return its new ULID."""
    campaign_id = new_id()
    current().session.execute(
        insert_rows(CampaignRecord).values(
            id=campaign_id,
            key=campaign.key,
            name=campaign.name,
            segment_id=segment_id,
            market_id=market_id,
            offer_id=offer_id,
            sequence_id=sequence_id,
            status=str(CampaignStatus.DRAFT),
            lia_ref=None,
            lia_date=None,
            version=FIRST_VERSION,
            created_at=at,
            updated_at=at,
        )
    )
    for variant in campaign.variants:
        insert_variant(campaign_id, variant, at)
    return campaign_id


def update_config(
    campaign_id: str,
    *,
    name: str,
    offer_id: str,
    sequence_id: str,
    version: int,
    at: datetime,
) -> None:
    """Set the name, Offer, Sequence and version of the Campaign ``campaign_id``, changed at
    ``at``."""
    current().session.execute(
        update_rows(CampaignRecord)
        .where(CampaignRecord.id == campaign_id)
        .values(
            name=name, offer_id=offer_id, sequence_id=sequence_id, version=version, updated_at=at
        )
    )


def update_definition(
    campaign_id: str,
    *,
    name: str,
    segment_id: str,
    market_id: str,
    offer_id: str,
    sequence_id: str,
    version: int,
    at: datetime,
    clear_lia: bool = False,
) -> None:
    """Set the name, Segment, Market, Offer, Sequence and version of the Campaign
    ``campaign_id``, changed at ``at``; with ``clear_lia``, also clear its stored LIA."""
    values: dict[str, Any] = {
        "name": name,
        "segment_id": segment_id,
        "market_id": market_id,
        "offer_id": offer_id,
        "sequence_id": sequence_id,
        "version": version,
        "updated_at": at,
    }
    if clear_lia:
        values.update(lia_ref=None, lia_date=None)
    current().session.execute(
        update_rows(CampaignRecord).where(CampaignRecord.id == campaign_id).values(**values)
    )


def set_variant_status(variant_id: str, status: VariantStatus, at: datetime) -> None:
    """Set the status of the Variant ``variant_id``, changed at ``at``."""
    current().session.execute(
        update_rows(VariantRecord)
        .where(VariantRecord.id == variant_id)
        .values(status=str(VariantStatus(status)), updated_at=at)
    )


def set_status(campaign_id: str, from_status: str, to_status: str, at: datetime) -> bool:
    """Move the Campaign ``campaign_id`` from ``from_status`` to ``to_status`` at ``at``, only
    while it is at ``from_status``; whether exactly one row changed."""
    result = current().session.execute(
        update_rows(CampaignRecord)
        .where(CampaignRecord.id == campaign_id, CampaignRecord.status == str(from_status))
        .values(status=str(to_status), updated_at=at)
    )
    return result.rowcount == 1  # type: ignore[attr-defined]


def set_lia(campaign_id: str, lia: Lia, at: datetime) -> None:
    """Record the LIA of the Campaign ``campaign_id`` at ``at``."""
    current().session.execute(
        update_rows(CampaignRecord)
        .where(CampaignRecord.id == campaign_id)
        .values(lia_ref=lia.ref, lia_date=lia.date, updated_at=at)
    )
