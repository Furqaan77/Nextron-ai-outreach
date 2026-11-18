"""The ``markets`` table's repository (FR-2, AD-3, AD-12): the only code that reads or writes it.

Every function works through the current unit of work's session (``nextron.db.uow.current()``):
reads work in a read-only unit of work too, writes only inside the running use case's
transaction. A Market is stored as typed columns from its file form
(``nextron.domain.markets.market_form``): ``cctlds``, ``footer_template`` and
``eligible_legal_forms`` as canonical JSON lists (``ensure_ascii=False``, no spaces), with NULL
for ``not_checked`` legal forms and for an unset ``unknown_send_window``. A row is decoded back
into its form and validated again through ``market_from_form``, so a row that no longer
validates (or whose list column isn't a JSON list) raises ``InvalidMarkets``.

- ``all_markets()``: every row as a ``StoredMarket``, by code;
- ``by_code(code)``: the row of ``code``, or ``None``; ``by_id(market_id)`` (Story 4.12): the
  row of ``market_id``, or ``None``;
- ``footer_templates()``: each row's ``id`` and its ``footer_template`` lines, by code, without
  validating anything else (Story 4.11's ``FooterChanged`` reads it): the lines are ``None``
  when the column isn't a JSON list of ``str`` (``Setup`` treats such a row, or one whose lines
  lack a required footer placeholder, as using every placeholder);
- ``insert(market, at) -> id``: a new row at version 1, created and updated at ``at``;
- ``update(market_id, market, version, at)``: sets the row's Market fields, version and
  ``updated_at``.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, select
from sqlalchemy import insert as insert_rows
from sqlalchemy import update as update_rows

from nextron.db.models.base import new_id
from nextron.db.models.setup import MarketRecord
from nextron.db.uow import current
from nextron.domain.markets import (
    CODE_PATTERN,
    NOT_CHECKED,
    InvalidMarkets,
    MarketDef,
    market_form,
    market_from_form,
)

FIRST_VERSION = 1
_LIST_COLUMNS = ("cctlds", "footer_template")


@dataclass(frozen=True, slots=True)
class StoredMarket:
    """One ``markets`` row, its Market decoded."""

    id: str
    code: str
    version: int
    created_at: datetime
    updated_at: datetime
    market: MarketDef


_COLUMNS = (
    MarketRecord.id,
    MarketRecord.code,
    MarketRecord.name,
    MarketRecord.enabled,
    MarketRecord.country_code,
    MarketRecord.cctlds,
    MarketRecord.cold_email_allowed,
    MarketRecord.eligible_legal_forms,
    MarketRecord.free_webmail_blocked,
    MarketRecord.default_timezone,
    MarketRecord.unknown_timezone,
    MarketRecord.unknown_send_window,
    MarketRecord.english_variant,
    MarketRecord.footer_template,
    MarketRecord.privacy_line_required,
    MarketRecord.version,
    MarketRecord.created_at,
    MarketRecord.updated_at,
)


def _list_text(items: object) -> str:
    return json.dumps(items, ensure_ascii=False, separators=(",", ":"))


def _values(market: MarketDef) -> dict[str, Any]:
    """The Market's columns, from its file form."""
    form = market_form(market)
    legal_forms = form["eligible_legal_forms"]
    return {
        "code": form["code"],
        "name": form["name"],
        "enabled": form["enabled"],
        "country_code": form["country_code"],
        "cctlds": _list_text(form["cctlds"]),
        "cold_email_allowed": form["cold_email_allowed"],
        "eligible_legal_forms": None if legal_forms == NOT_CHECKED else _list_text(legal_forms),
        "free_webmail_blocked": form["free_webmail_blocked"],
        "default_timezone": form["default_timezone"],
        "unknown_timezone": form["unknown_timezone"],
        "unknown_send_window": form.get("unknown_send_window"),
        "english_variant": form["english_variant"],
        "footer_template": _list_text(form["footer_template"]),
        "privacy_line_required": form["privacy_line_required"],
    }


def _list_value(text: object, code: object, column: str) -> object:
    """A list column's JSON, or ``InvalidMarkets`` when it isn't JSON."""
    try:
        return json.loads(text)  # type: ignore[arg-type]
    except (TypeError, ValueError, RecursionError):  # JSONDecodeError is a ValueError
        shown = code if isinstance(code, str) and CODE_PATTERN.fullmatch(code) else "0"
        raise InvalidMarkets(f"markets[{shown}].{column}", "is not valid JSON") from None


def _stored(row: Row[Any]) -> StoredMarket:
    """The ``StoredMarket`` of one row of ``_COLUMNS``, validated again."""
    code = row.code
    form: dict[str, object] = {
        name: getattr(row, name)
        for name in (
            "code",
            "name",
            "enabled",
            "country_code",
            "cold_email_allowed",
            "free_webmail_blocked",
            "default_timezone",
            "unknown_timezone",
            "english_variant",
            "privacy_line_required",
        )
    }
    for name in _LIST_COLUMNS:
        form[name] = _list_value(getattr(row, name), code, name)
    legal_forms = row.eligible_legal_forms
    form["eligible_legal_forms"] = (
        NOT_CHECKED
        if legal_forms is None
        else _list_value(legal_forms, code, "eligible_legal_forms")
    )
    if row.unknown_send_window is not None:
        form["unknown_send_window"] = row.unknown_send_window
    return StoredMarket(
        id=row.id,
        code=code,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        market=market_from_form(form),
    )


def all_markets() -> tuple[StoredMarket, ...]:
    """Every stored Market, ordered by code."""
    statement = select(*_COLUMNS).order_by(MarketRecord.code)
    return tuple(_stored(row) for row in current().session.execute(statement))


def _template_lines(text: object) -> tuple[str, ...] | None:
    """A ``footer_template`` column's lines, or ``None`` when it isn't a JSON list of ``str``."""
    try:
        lines = json.loads(text)  # type: ignore[arg-type]
    except (TypeError, ValueError, RecursionError):  # JSONDecodeError is a ValueError
        return None
    if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
        return None
    return tuple(lines)


def footer_templates() -> tuple[tuple[str, tuple[str, ...] | None], ...]:
    """Each stored Market's ``(id, footer template lines or None)``, ordered by code."""
    statement = select(MarketRecord.id, MarketRecord.footer_template).order_by(MarketRecord.code)
    return tuple(
        (market_id, _template_lines(text))
        for market_id, text in current().session.execute(statement)
    )


def by_code(code: str) -> StoredMarket | None:
    """The stored Market ``code``, or ``None`` when it has no row."""
    statement = select(*_COLUMNS).where(MarketRecord.code == str(code))
    row = current().session.execute(statement).one_or_none()
    return None if row is None else _stored(row)


def by_id(market_id: str) -> StoredMarket | None:
    """The stored Market ``market_id``, or ``None`` when it has no row."""
    statement = select(*_COLUMNS).where(MarketRecord.id == str(market_id))
    row = current().session.execute(statement).one_or_none()
    return None if row is None else _stored(row)


def insert(market: MarketDef, at: datetime) -> str:
    """Store a new Market at version 1, created at ``at``; return its new ULID."""
    market_id = new_id()
    current().session.execute(
        insert_rows(MarketRecord).values(
            id=market_id,
            **_values(market),
            version=FIRST_VERSION,
            created_at=at,
            updated_at=at,
        )
    )
    return market_id


def update(market_id: str, market: MarketDef, version: int, at: datetime) -> None:
    """Set the fields and version of the Market ``market_id``, changed at ``at``."""
    current().session.execute(
        update_rows(MarketRecord)
        .where(MarketRecord.id == market_id)
        .values(**_values(market), version=version, updated_at=at)
    )
