"""Offers (FR-3, AD-12, Story 4.2): the commercial ladder a Campaign leads to.

An Offer is a ladder of rungs: first only a reply (FR-4: the first ask is only a reply), then
the fixed-fee audit, then a build. It is commercial configuration for the Operator and later
call prep, never Draft wording: no Step names a price, and a Draft cites only the
``audit_offer`` Approved Company Fact, which names no price (Story 4.3). ``seed/offers.yaml``
holds the Offers (read by ``nextron.domain.seed_yaml``) and ``nextron seed sync`` loads them into
the ``offers`` table (owner ``setup``); a Campaign names its Offer by key.

**Types** (frozen, slotted; every list is a tuple): ``OfferRungKind`` (``reply``, ``audit``,
``build``), ``OfferRung(kind, name, price_usd_min, price_usd_max, terms)`` (both prices are
whole US dollars or ``None``; ``terms`` is ``None`` when the rung has none) and
``OfferDef(key, name, ladder)``.

**The file form.** The top level is exactly ``offers: [<offer>, ...]``, with 1 to 20 items. An
offer is exactly ``key``, ``name`` and ``ladder`` (1 to 5 rungs). A rung has ``kind`` and
``name``, plus optional ``price_usd_min``, ``price_usd_max`` and ``terms``. Missing and unknown
keys are refused.

**Validation.** ``validate_offers(data)`` returns the ``OfferDef``s in file order, or raises
``InvalidOffers(field, reason)`` (code ``invalid_offers``) for the first problem in file order
(each mapping's fields in the file's order, then its missing fields, then its rules). The
message is ``"<field>: <reason>"``; ``field`` is a path such as
``offers[default].ladder[1].price_usd_min``. An offer's brackets hold its ``key`` when that key
is valid (and not a repeat), else its 0-based index; a rung's brackets hold its 0-based index.
A reason never repeats a value longer than 40 characters.

- ``key`` fullmatches ``[a-z][a-z0-9_]{0,30}`` and is unique in the file.
- Texts are a ``str``, non-blank, with no leading or trailing whitespace and no ``Cc``, ``Cf``,
  ``Zl`` or ``Zp`` character (Story 2.1's rules): an offer's and a rung's ``name`` at most 100
  characters, ``terms`` at most 200.
- A price is an ``int``, never a ``bool``, from 1 to 1,000,000.
- ``kind`` is an ``OfferRungKind``.
- **The rules**, each failing on its own field, checked for each rung after its fields: (1)
  rung kinds are unique within a ladder (on ``kind``); (2) the first rung is ``reply``, the
  first ask being only a reply (on ``kind``); (3) a ``reply`` rung has no price (on the first
  price it holds); (4) an ``audit`` or ``build`` rung has both prices (on the missing one),
  with ``price_usd_min <= price_usd_max`` (on ``price_usd_max``).

**The file form back.** ``offer_form(offer)`` is the offer's file mapping (the kind as its
value, the ladder as a list, the optional keys only when set; a value of the wrong type is left
as it is, so validation refuses it); ``offer_from_form(form)`` validates one such mapping, so
``offer_from_form(offer_form(o)) == o`` for a valid ``o``. The ``offers`` repository stores and
decodes Offers through these two.
"""

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from nextron.domain.errors import DomainError

KEY_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{0,30}")  # matched with fullmatch
_FIELD_NAME: Final = re.compile(r"[a-z][a-z0-9_]{0,30}")  # an unknown field shown by name

TOP_LEVEL: Final = "(top level)"  # the field path of a problem with the whole file
MAX_OFFERS: Final = 20
MAX_RUNGS: Final = 5
MAX_NAME: Final = 100
MAX_TERMS: Final = 200
MIN_PRICE: Final = 1
MAX_PRICE: Final = 1_000_000
REFUSED_TEXT_CATEGORIES: Final = frozenset({"Cc", "Cf", "Zl", "Zp"})
PRICE_FIELDS: Final = ("price_usd_min", "price_usd_max")
FIRST_RUNG_REASON: Final = "must be reply: the first ask is only a reply (FR-4)"
REPLY_PRICE_REASON: Final = "a reply rung has no price: the first ask is only a reply (FR-4)"
_KEY_REASON: Final = (
    "must be lowercase letters, digits and underscores, starting with a letter, at most 31 "
    "characters"
)


class OfferRungKind(StrEnum):
    """What a rung asks for: only a reply, the fixed-fee audit, or a build."""

    REPLY = "reply"
    AUDIT = "audit"
    BUILD = "build"


class InvalidOffers(DomainError):
    """Offer definitions that break a rule: ``field`` is the path of the first problem and
    ``reason`` says what a valid value is, repeating no value longer than 40 characters."""

    code = "invalid_offers"

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


@dataclass(frozen=True, slots=True)
class OfferRung:
    kind: OfferRungKind
    name: str
    price_usd_min: int | None  # whole US dollars
    price_usd_max: int | None
    terms: str | None


@dataclass(frozen=True, slots=True)
class OfferDef:
    key: str
    name: str
    ladder: tuple[OfferRung, ...]


# Validation ------------------------------------------------------------------------------------

_OFFER_REQUIRED: Final = ("key", "name", "ladder")
_RUNG_REQUIRED: Final = ("kind", "name")

type _Check = Callable[[object, str], Any]


def _unknown_field(name: object, path: str) -> InvalidOffers:
    if isinstance(name, str) and _FIELD_NAME.fullmatch(name):  # shown only when short and plain
        return InvalidOffers(f"{path}.{name}" if path else name, "unknown field")
    return InvalidOffers(path or TOP_LEVEL, "has an unknown field")


def _fields(
    value: object, path: str, checks: dict[str, _Check], required: tuple[str, ...]
) -> dict[str, Any]:
    """``value`` as a mapping whose fields are ``checks``: each present field checked in the
    file's order (an unknown one refused where it appears), then the missing ones of
    ``required`` refused."""
    if not isinstance(value, dict):
        raise InvalidOffers(path, "must be a mapping")
    checked: dict[str, Any] = {}
    for name, item in value.items():
        check = checks.get(name) if isinstance(name, str) else None
        if check is None:
            raise _unknown_field(name, path)
        checked[name] = check(item, f"{path}.{name}")
    for name in required:
        if name not in value:
            raise InvalidOffers(f"{path}.{name}", "missing")
    return checked


def _list(value: object, path: str, minimum: int, maximum: int) -> list[Any]:
    if not isinstance(value, list):
        raise InvalidOffers(path, "must be a list")
    if len(value) < minimum:
        raise InvalidOffers(path, f"must hold at least {minimum} item")
    if len(value) > maximum:
        raise InvalidOffers(path, f"must hold at most {maximum} items")
    return value


def _text(value: object, path: str, limit: int) -> str:
    if not isinstance(value, str):
        raise InvalidOffers(path, "must be text")
    if not value.strip():
        raise InvalidOffers(path, "must not be blank")
    if value != value.strip():
        raise InvalidOffers(path, "must not start or end with whitespace")
    if any(unicodedata.category(char) in REFUSED_TEXT_CATEGORIES for char in value):
        raise InvalidOffers(path, "must not hold control, format or line-separator characters")
    if len(value) > limit:
        raise InvalidOffers(path, f"must be at most {limit} characters")
    return value


def _name(value: object, path: str) -> str:
    return _text(value, path, MAX_NAME)


def _terms(value: object, path: str) -> str:
    return _text(value, path, MAX_TERMS)


def _price(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidOffers(path, "must be a whole number of US dollars")
    if not MIN_PRICE <= value <= MAX_PRICE:
        raise InvalidOffers(path, f"must be from {MIN_PRICE} to {MAX_PRICE:,}")
    return value


def _kind(value: object, path: str) -> OfferRungKind:
    allowed = [member.value for member in OfferRungKind]
    if not isinstance(value, str) or value not in allowed:
        raise InvalidOffers(path, f"must be one of {', '.join(allowed)}")
    return OfferRungKind(value)


def _is_key(value: object) -> bool:
    return isinstance(value, str) and KEY_PATTERN.fullmatch(value) is not None


def _check_rung_rules(rung: OfferRung, path: str, index: int, kinds: set[str]) -> None:
    """Rules 1 to 4 of one rung, ``kinds`` holding the kinds of the rungs before it."""
    if rung.kind in kinds:
        raise InvalidOffers(f"{path}.kind", f"repeats an earlier rung's kind ({rung.kind})")
    if index == 0 and rung.kind != OfferRungKind.REPLY:
        raise InvalidOffers(f"{path}.kind", FIRST_RUNG_REASON)
    if rung.kind == OfferRungKind.REPLY:
        for name in PRICE_FIELDS:
            if getattr(rung, name) is not None:
                raise InvalidOffers(f"{path}.{name}", REPLY_PRICE_REASON)
        return
    article = "an" if rung.kind == OfferRungKind.AUDIT else "a"
    for name in PRICE_FIELDS:
        if getattr(rung, name) is None:
            raise InvalidOffers(
                f"{path}.{name}", f"missing: {article} {rung.kind} rung needs both prices"
            )
    assert rung.price_usd_min is not None and rung.price_usd_max is not None
    if rung.price_usd_min > rung.price_usd_max:
        raise InvalidOffers(
            f"{path}.price_usd_max", f"must be at least price_usd_min ({rung.price_usd_min:,})"
        )


def _ladder(value: object, path: str) -> tuple[OfferRung, ...]:
    """1 to 5 rungs, each named by its 0-based index."""
    items = _list(value, path, 1, MAX_RUNGS)
    rungs: list[OfferRung] = []
    kinds: set[str] = set()
    for index, item in enumerate(items):
        rung_path = f"{path}[{index}]"
        checked = _fields(
            item,
            rung_path,
            {
                "kind": _kind,
                "name": _name,
                "price_usd_min": _price,
                "price_usd_max": _price,
                "terms": _terms,
            },
            _RUNG_REQUIRED,
        )
        rung = OfferRung(
            kind=checked["kind"],
            name=checked["name"],
            price_usd_min=checked.get("price_usd_min"),
            price_usd_max=checked.get("price_usd_max"),
            terms=checked.get("terms"),
        )
        _check_rung_rules(rung, rung_path, index, kinds)
        kinds.add(rung.kind)
        rungs.append(rung)
    return tuple(rungs)


class _Validator:
    """One validation run: the Offer keys seen so far, for the uniqueness rule."""

    def __init__(self) -> None:
        self._keys: set[str] = set()

    def offers(self, data: object) -> tuple[OfferDef, ...]:
        if not isinstance(data, dict):
            raise InvalidOffers(TOP_LEVEL, "must be a mapping with exactly one field, offers")
        result: tuple[OfferDef, ...] = ()
        for name, value in data.items():
            if name != "offers":
                raise _unknown_field(name, "")
            items = _list(value, "offers", 1, MAX_OFFERS)
            result = tuple(self.offer(item, index) for index, item in enumerate(items))
        if "offers" not in data:
            raise InvalidOffers("offers", "missing")
        return result

    def offer(self, value: object, index: int) -> OfferDef:
        key = value.get("key") if isinstance(value, dict) else None
        shown = key if _is_key(key) and key not in self._keys else str(index)
        path = f"offers[{shown}]"
        checked = _fields(
            value,
            path,
            {"key": self._key, "name": _name, "ladder": _ladder},
            _OFFER_REQUIRED,
        )
        return OfferDef(key=checked["key"], name=checked["name"], ladder=checked["ladder"])

    def _key(self, value: object, path: str) -> str:
        if not _is_key(value):
            raise InvalidOffers(path, _KEY_REASON)
        assert isinstance(value, str)
        if value in self._keys:
            raise InvalidOffers(path, f"repeats an earlier Offer key ({value})")
        self._keys.add(value)
        return value


def validate_offers(data: object) -> tuple[OfferDef, ...]:
    """The ``OfferDef``s of a parsed seed file, in file order; ``InvalidOffers`` for the first
    problem. See the module docstring."""
    return _Validator().offers(data)


# The file form ----------------------------------------------------------------------------------


def _enum_value(value: object) -> object:
    """A ``StrEnum`` member as its plain value; anything else as it is."""
    return str(value) if isinstance(value, StrEnum) else value


def _rung_form(rung: object) -> object:
    if not isinstance(rung, OfferRung):
        return rung
    form: dict[str, object] = {"kind": _enum_value(rung.kind), "name": rung.name}
    for name in (*PRICE_FIELDS, "terms"):
        value = getattr(rung, name)
        if value is not None:
            form[name] = value
    return form


def offer_form(offer: OfferDef) -> dict[str, object]:
    """``offer`` as the file's mapping: the kind as its value, the ladder as a list, optional
    rung keys only when set."""
    ladder = offer.ladder
    return {
        "key": offer.key,
        "name": offer.name,
        "ladder": (
            [_rung_form(rung) for rung in ladder] if isinstance(ladder, (tuple, list)) else ladder
        ),
    }


def offer_from_form(form: object) -> OfferDef:
    """The ``OfferDef`` of one file-form mapping, validated as one item of a file (path
    ``offers[<key>]``); ``InvalidOffers`` for the first problem."""
    return _Validator().offer(form, 0)
