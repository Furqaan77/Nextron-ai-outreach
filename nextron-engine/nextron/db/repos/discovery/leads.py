"""The ``leads`` identity group's repository (FR-9, AD-3): the only code that writes ``id``,
``name``, ``web_domain``, ``ch_number``, ``place_id``, ``segment_id``, ``country_code``,
``merged_into``, ``created_at``, the Places location ``place_lat``, ``place_lng`` and
``place_coords_at`` (Story 8.2) and ``website_lookup_at`` (Story 8.4).

Every function runs one explicit Core statement through the current unit of work's session
(``nextron.db.uow.current()``), so writes run in the order they are called: reads work in a
read-only unit of work too, writes only inside the running use case's transaction. It checks
nothing; ``nextron.app.discovery`` does.

- ``StoredLead``: the identity group of one row;
- ``get(lead_id)``: the row, or ``None``;
- ``by_web_domain(web_domain)``, ``by_ch_number(ch_number)``, ``by_place_id(place_id)``: the
  row holding that key, merged or not, or ``None``;
- ``insert(...) -> id``: a new row; ``pipeline_stage`` is left to its server default (``new``);
- ``set_keys(lead_id, **keys)``: sets the named key columns;
- ``set_web_domain(lead_id, web_domain)``: replaces the Lead's ``web_domain`` (Story 2.5: the
  domain its website now lives on; Story 8.4: a registry Lead's website, found or entered);
- ``set_website_lookup_at(lead_id, at)`` (Story 8.4): when the Lead's website lookup was
  decided;
- ``mark_merged(loser_id, survivor_id, cleared_keys)``: sets ``merged_into`` and clears each
  key column named in ``cleared_keys``, in one statement;
- ``set_place_coords(lead_id, lat, lng, at)`` (Story 8.3): sets ``place_lat``, ``place_lng``
  and ``place_coords_at`` together;
- ``purge_place_coords(cutoff) -> int`` (Story 8.2): nulls ``place_lat``, ``place_lng`` and
  ``place_coords_at`` on every row whose ``place_coords_at`` is strictly before ``cutoff``, and
  on every row holding a coordinate without a ``place_coords_at`` (its age is unknown), and
  keeps ``place_id``; the number of rows purged;
- ``merged_from(lead_id)`` (Story 3.13): the ids of every Lead whose merge chain leads to
  ``lead_id`` (merged into it, or into one of those, and so on), by id; a loop is followed once;
- ``ListedLead``, ``listed(*, stage, segment_id, search, limit, offset, website_needed=False)``
  and ``count_listed(*, stage, segment_id, search, website_needed=False)`` (Story 2.4): the
  Leads list's page of rows and their count. Both keep only Leads that aren't merged
  (``merged_into IS NULL``) and, for each argument that isn't ``None``, those at
  ``pipeline_stage == stage``, those in ``segment_id``, and those whose name or ``web_domain``
  contains ``search`` (SQLite ``LIKE``, so ASCII letters ignore case; ``%``, ``_`` and the
  escape character are matched literally); with ``website_needed`` (Story 8.4) only those whose
  ``web_domain`` is NULL.
  ``listed`` orders newest first (``created_at DESC, id DESC``). They read the pipeline
  group's ``pipeline_stage`` column of the same rows, read-only, so the stage filter and the
  paging stay in one statement; only ``nextron.db.repos.pipeline.stages`` writes it (AD-3).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy import insert as insert_rows
from sqlalchemy import update as update_rows

from nextron.db.models.base import new_id
from nextron.db.models.leads import LeadRecord
from nextron.db.uow import current

KEY_COLUMNS: Final = ("web_domain", "ch_number", "place_id")


@dataclass(frozen=True, slots=True)
class StoredLead:
    """The identity group of one ``leads`` row."""

    id: str
    name: str
    web_domain: str | None
    ch_number: str | None
    place_id: str | None
    segment_id: str
    country_code: str | None
    merged_into: str | None
    created_at: datetime


_COLUMNS = (
    LeadRecord.id,
    LeadRecord.name,
    LeadRecord.web_domain,
    LeadRecord.ch_number,
    LeadRecord.place_id,
    LeadRecord.segment_id,
    LeadRecord.country_code,
    LeadRecord.merged_into,
    LeadRecord.created_at,
)


def _one(condition: ColumnElement[bool]) -> StoredLead | None:
    row = current().session.execute(select(*_COLUMNS).where(condition)).one_or_none()
    return None if row is None else StoredLead(*row)


def get(lead_id: str) -> StoredLead | None:
    """The Lead ``lead_id``, or ``None``."""
    return _one(LeadRecord.id == lead_id)


def by_web_domain(web_domain: str) -> StoredLead | None:
    """The Lead holding ``web_domain``, or ``None``."""
    return _one(LeadRecord.web_domain == web_domain)


def by_ch_number(ch_number: str) -> StoredLead | None:
    """The Lead holding ``ch_number``, or ``None``."""
    return _one(LeadRecord.ch_number == ch_number)


def by_place_id(place_id: str) -> StoredLead | None:
    """The Lead holding ``place_id``, or ``None``."""
    return _one(LeadRecord.place_id == place_id)


def insert(
    *,
    name: str,
    segment_id: str,
    web_domain: str | None,
    ch_number: str | None,
    place_id: str | None,
    country_code: str | None,
    created_at: datetime,
) -> str:
    """Store a new Lead; return its new ULID. Its stage is the server default."""
    lead_id = new_id()
    current().session.execute(
        insert_rows(LeadRecord).values(
            id=lead_id,
            name=name,
            web_domain=web_domain,
            ch_number=ch_number,
            place_id=place_id,
            segment_id=segment_id,
            country_code=country_code,
            created_at=created_at,
        )
    )
    return lead_id


def set_keys(lead_id: str, **keys: str | None) -> None:
    """Set the key columns named in ``keys`` on the Lead ``lead_id``; nothing when empty."""
    if not keys:
        return
    current().session.execute(
        update_rows(LeadRecord).where(LeadRecord.id == lead_id).values(**keys)
    )


def set_web_domain(lead_id: str, web_domain: str) -> None:
    """Replace the Lead ``lead_id``'s ``web_domain`` with ``web_domain``."""
    current().session.execute(
        update_rows(LeadRecord).where(LeadRecord.id == lead_id).values(web_domain=web_domain)
    )


def set_website_lookup_at(lead_id: str, at: datetime) -> None:
    """Record when the Lead ``lead_id``'s website lookup was decided."""
    current().session.execute(
        update_rows(LeadRecord).where(LeadRecord.id == lead_id).values(website_lookup_at=at)
    )


def mark_merged(loser_id: str, survivor_id: str, cleared_keys: tuple[str, ...]) -> None:
    """Set the Lead ``loser_id``'s ``merged_into`` to ``survivor_id`` and clear each key column
    in ``cleared_keys``."""
    values: dict[str, str | None] = {key: None for key in cleared_keys}
    current().session.execute(
        update_rows(LeadRecord)
        .where(LeadRecord.id == loser_id)
        .values(merged_into=survivor_id, **values)
    )


def set_place_coords(lead_id: str, lat: float, lng: float, at: datetime) -> None:
    """Store the Places location of the Lead ``lead_id``, stored at ``at``."""
    current().session.execute(
        update_rows(LeadRecord)
        .where(LeadRecord.id == lead_id)
        .values(place_lat=lat, place_lng=lng, place_coords_at=at)
    )


def purge_place_coords(cutoff: datetime) -> int:
    """Null the Places coordinates stored strictly before ``cutoff``, or held without a time;
    the number of rows."""
    result = current().session.execute(
        update_rows(LeadRecord)
        .where(
            or_(
                LeadRecord.place_coords_at < cutoff,
                and_(
                    LeadRecord.place_coords_at.is_(None),
                    or_(LeadRecord.place_lat.is_not(None), LeadRecord.place_lng.is_not(None)),
                ),
            )
        )
        .values(place_lat=None, place_lng=None, place_coords_at=None)
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


@dataclass(frozen=True, slots=True)
class ListedLead:
    """One row of the Leads list: a Lead's identity facts and its Pipeline Stage as stored."""

    id: str
    name: str
    web_domain: str | None
    segment_id: str
    country_code: str | None
    pipeline_stage: str
    created_at: datetime


_LISTED_COLUMNS = (
    LeadRecord.id,
    LeadRecord.name,
    LeadRecord.web_domain,
    LeadRecord.segment_id,
    LeadRecord.country_code,
    LeadRecord.pipeline_stage,  # the pipeline group's column, read only
    LeadRecord.created_at,
)


def merged_from(lead_id: str) -> tuple[str, ...]:
    """The ids of the Leads whose merge chain leads to ``lead_id``, by id."""
    session = current().session
    found: set[str] = set()
    frontier = {lead_id}
    while frontier:
        statement = select(LeadRecord.id).where(LeadRecord.merged_into.in_(sorted(frontier)))
        merged = set(session.execute(statement).scalars()) - found - {lead_id}
        found |= merged
        frontier = merged
    return tuple(sorted(found))


def _listed_where(
    *, stage: str | None, segment_id: str | None, search: str | None, website_needed: bool
) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [LeadRecord.merged_into.is_(None)]
    if website_needed:
        conditions.append(LeadRecord.web_domain.is_(None))
    if stage is not None:
        conditions.append(LeadRecord.pipeline_stage == stage)
    if segment_id is not None:
        conditions.append(LeadRecord.segment_id == segment_id)
    if search is not None:
        conditions.append(
            or_(
                LeadRecord.name.contains(search, autoescape=True),
                LeadRecord.web_domain.contains(search, autoescape=True),
            )
        )
    return conditions


def listed(
    *,
    stage: str | None,
    segment_id: str | None,
    search: str | None,
    limit: int,
    offset: int,
    website_needed: bool = False,
) -> tuple[ListedLead, ...]:
    """At most ``limit`` Leads that aren't merged and match the filters, newest first, after
    skipping ``offset``."""
    statement = (
        select(*_LISTED_COLUMNS)
        .where(
            *_listed_where(
                stage=stage,
                segment_id=segment_id,
                search=search,
                website_needed=website_needed,
            )
        )
        .order_by(LeadRecord.created_at.desc(), LeadRecord.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return tuple(ListedLead(*row) for row in current().session.execute(statement))


def count_listed(
    *,
    stage: str | None,
    segment_id: str | None,
    search: str | None,
    website_needed: bool = False,
) -> int:
    """How many Leads that aren't merged match the filters."""
    statement = (
        select(func.count())
        .select_from(LeadRecord)
        .where(
            *_listed_where(
                stage=stage,
                segment_id=segment_id,
                search=search,
                website_needed=website_needed,
            )
        )
    )
    return int(current().session.execute(statement).scalar_one())
