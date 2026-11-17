"""The ``offers`` table's repository (FR-3, AD-3, AD-12): the only code that reads or writes it.

Every function works through the current unit of work's session (``nextron.db.uow.current()``):
reads work in a read-only unit of work too, writes only inside the running use case's
transaction. An Offer is one row: its ``key``, ``name`` and ``version``, and its ``ladder`` as
the canonical JSON list of its rungs' file forms (``nextron.domain.offers.offer_form``;
``json.dumps`` with ``sort_keys``, ``ensure_ascii=False`` and no spaces, as Story 4.1 stores
JSON). A row is decoded back into its file form and validated again through
``offer_from_form``, so a row that no longer validates (a ladder that isn't JSON, or a rung that
breaks a rule) raises ``InvalidOffers``.

- ``all_offers()``: every Offer as a ``StoredOffer``, by key;
- ``by_key(key)``: the Offer ``key``, or ``None``;
- ``insert(offer, at) -> id``: a new Offer at version 1, created and updated at ``at``;
- ``update(offer_id, offer, version, at)``: sets the Offer's ``name``, ``ladder``, ``version``
  and ``updated_at``.

There is no delete: Campaigns point to their Offer.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, select
from sqlalchemy import insert as insert_rows
from sqlalchemy import update as update_rows

from nextron.db.models.base import new_id
from nextron.db.models.setup import OfferRecord
from nextron.db.uow import current
from nextron.domain.offers import (
    KEY_PATTERN,
    InvalidOffers,
    OfferDef,
    offer_form,
    offer_from_form,
)

FIRST_VERSION = 1


@dataclass(frozen=True, slots=True)
class StoredOffer:
    """One ``offers`` row, decoded."""

    id: str
    key: str
    version: int
    created_at: datetime
    updated_at: datetime
    offer: OfferDef


_COLUMNS = (
    OfferRecord.id,
    OfferRecord.key,
    OfferRecord.name,
    OfferRecord.ladder,
    OfferRecord.version,
    OfferRecord.created_at,
    OfferRecord.updated_at,
)


def _json_text(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _shown(key: object) -> str:
    return key if isinstance(key, str) and KEY_PATTERN.fullmatch(key) else "0"


def _stored(row: Row[Any]) -> StoredOffer:
    """The ``StoredOffer`` of one row, validated again."""
    try:
        ladder = json.loads(row.ladder)
    except (TypeError, ValueError, RecursionError):  # JSONDecodeError is a ValueError
        raise InvalidOffers(f"offers[{_shown(row.key)}].ladder", "is not valid JSON") from None
    offer = offer_from_form({"key": row.key, "name": row.name, "ladder": ladder})
    return StoredOffer(
        id=row.id,
        key=offer.key,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        offer=offer,
    )


def all_offers() -> tuple[StoredOffer, ...]:
    """Every stored Offer, ordered by key."""
    statement = select(*_COLUMNS).order_by(OfferRecord.key)
    return tuple(_stored(row) for row in current().session.execute(statement))


def by_key(key: str) -> StoredOffer | None:
    """The stored Offer ``key``, or ``None`` when it has no row."""
    statement = select(*_COLUMNS).where(OfferRecord.key == str(key))
    row = current().session.execute(statement).one_or_none()
    return None if row is None else _stored(row)


def _ladder_text(offer: OfferDef) -> str:
    return _json_text(offer_form(offer)["ladder"])


def insert(offer: OfferDef, at: datetime) -> str:
    """Store a new Offer at version 1, created at ``at``; return its new ULID."""
    offer_id = new_id()
    current().session.execute(
        insert_rows(OfferRecord).values(
            id=offer_id,
            key=offer.key,
            name=offer.name,
            ladder=_ladder_text(offer),
            version=FIRST_VERSION,
            created_at=at,
            updated_at=at,
        )
    )
    return offer_id


def update(offer_id: str, offer: OfferDef, version: int, at: datetime) -> None:
    """Set the name, ladder and version of the Offer ``offer_id``, changed at ``at``."""
    current().session.execute(
        update_rows(OfferRecord)
        .where(OfferRecord.id == offer_id)
        .values(name=offer.name, ladder=_ladder_text(offer), version=version, updated_at=at)
    )
