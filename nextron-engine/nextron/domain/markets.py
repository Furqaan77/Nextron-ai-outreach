"""Market rules (FR-2, AD-12, NFR-2, NFR-5, NFR-40; Story 3.1): where cold email may go and how.

A Market holds one country's cold-email rules: its country code and country-code TLDs, whether
cold email is allowed, the eligible legal forms, whether free webmail is blocked, the default
and unknown-location timezones (with an optional narrowed Send Window), the English variant and
the footer template. They come from ``seed/markets.yaml`` (read by ``nextron.domain.seed_yaml``)
through ``nextron seed sync``; the ``markets`` table (owner ``setup``) stores each one. A Lead's
Market is derived from its country code through ``market_for_country``, never stored on it.

**Types** (frozen, slotted; every list is a tuple): ``EnglishVariant`` (``en_us``, ``en_gb``),
``FooterPlaceholder`` (``sender_signature``, ``legal_name``, ``postal_address``,
``optout_line``, ``privacy_line``, ``us_solicitation_label``), ``SendWindow(start, end)`` (two
``datetime.time`` values) and ``MarketDef(code, name, enabled, country_code, cctlds,
cold_email_allowed, eligible_legal_forms, free_webmail_blocked, default_timezone,
unknown_timezone, unknown_send_window, english_variant, footer_template,
privacy_line_required)``. ``eligible_legal_forms`` is ``None`` when legal forms are not checked;
``unknown_send_window`` is ``None`` when the Send Window isn't narrowed for an unknown location;
``footer_template`` holds one item per footer line.

**The file form.** The top level is exactly ``markets: [<market>, ...]``, with 1 to 50 items.
Each item has exactly the ``MarketDef`` field names as keys: ``unknown_send_window`` is
optional, every other key is required, and unknown keys are refused. ``eligible_legal_forms``
is the plain text ``not_checked`` or a list; ``unknown_send_window`` is ``'HH:MM-HH:MM'``.

**Validation.** ``validate_markets(data)`` returns the ``MarketDef``s in file order, or raises
``InvalidMarkets(field, reason)`` (code ``invalid_markets``) for the first problem in file order
(each mapping's fields in the file's order, then its missing fields, then its rules). The
message is ``"<field>: <reason>"``; ``field`` is a path such as ``markets[uk].cctlds[0]``, with
an item's ``code`` in the brackets when that code is valid (and not a repeat), else its 0-based
index; a reason never repeats a value longer than 40 characters.

- ``code`` fullmatches ``[a-z][a-z0-9_]{0,15}`` and is unique in the file; ``country_code``
  fullmatches ``[A-Z]{2}`` and is unique in the file.
- ``cctlds`` holds 1 to 10 items, each ``[a-z]{2}``, with no duplicates and none shared with
  another Market; for each item ``t``, ``cctld_country("x." + t)`` is the Market's
  ``country_code`` (a rule: ``uk`` belongs to ``GB``).
- ``name`` is a text of at most 100 characters: a ``str``, non-blank, with no leading or
  trailing whitespace and no ``Cc``, ``Cf``, ``Zl`` or ``Zp`` character (Story 2.1's rules).
- ``enabled``, ``cold_email_allowed``, ``free_webmail_blocked`` and ``privacy_line_required``
  are ``bool``.
- ``eligible_legal_forms`` is ``not_checked`` (stored as ``None``) or 1 to 50 items, each
  ``[a-z][a-z0-9-]{0,63}``, with no duplicates. An empty list is refused, so it can never be
  read as "anything goes".
- ``default_timezone`` and ``unknown_timezone`` are members of
  ``settings_schema.timezone_names()``; ``unknown_send_window`` is ``HH:MM-HH:MM`` with times
  from 00:00 to 23:59 and the start before the end; ``english_variant`` is an
  ``EnglishVariant``.
- ``footer_template`` holds 1 to 20 lines, each a text of at most 200 characters. A placeholder
  is ``{<name>}`` with the name a ``FooterPlaceholder``; any other ``{`` or ``}`` is refused,
  each placeholder appears at most once, and ``{privacy_line}`` and
  ``{us_solicitation_label}`` each stand alone on their line. As rules: the template holds
  ``{sender_signature}``, ``{legal_name}``, ``{postal_address}`` and ``{optout_line}`` (FR-23);
  ``{privacy_line}`` if and only if ``privacy_line_required``; and ``{us_solicitation_label}`` if
  and only if the ``country_code`` is ``US`` (NFR-2).
- **The compliance floor** (decision 3), each naming its field: ``cold_email_allowed`` is true
  only for a ``US`` or ``GB`` Market (NFR-5); a ``GB`` Market has ``free_webmail_blocked`` and
  ``privacy_line_required`` true and lists its ``eligible_legal_forms`` (PECR), each one of
  decision 2's eight Companies House types (``ltd``, ``plc``, ``private-limited-guarant-nsc``,
  ``private-limited-guarant-nsc-limited-exemption``,
  ``private-limited-shares-section-30-exemption``, ``private-unlimited``,
  ``private-unlimited-nsc``, ``llp``; any other fails on ``eligible_legal_forms[<index>]``, and
  a narrower list is fine). A seed edit can't loosen them.

The rules run in this order: the ccTLDs' country, the compliance floor, then the footer
template.

**The file form back.** ``market_form(market)`` is the market's file mapping (``not_checked``
for ``None``, enums as their values, lists as lists, ``unknown_send_window`` only when set, as
``send_window_text`` writes it; a window that isn't two naive whole-minute times is left as it
is, so validation refuses it); ``market_from_form(form)`` validates one such mapping, so
``market_from_form(market_form(m)) == m``. The ``markets`` repository stores and decodes
Markets through these two.

**Functions:**

- ``market_for_country(code, markets)``: ``code`` stripped and uppercased (ASCII only); the one
  Market with that ``country_code``, only when it is ``enabled``. Anything else is ``None``: a
  code that isn't ``[A-Z]{2}``, ``None``, an unknown code, or a code more than one Market claims.
- ``cctld_country(domain)``: the input lowercased and stripped, cut to the part after its last
  ``@``, one trailing ``.`` dropped. With no ``.`` left it is ``None``; otherwise a last label
  that fullmatches ``[a-z]{2}`` is the country code uppercased (``uk`` is ``GB``), and any other
  last label is generic (``None``). So ``.eu`` and ``.io`` count as countries, the conservative
  side; an internationalised ccTLD (``xn--...``) counts as generic.
- ``is_free_webmail(domain)``: the input normalised the same way; true when it, or any parent at
  a label boundary, is in ``FREE_WEBMAIL_DOMAINS`` (``mail.gmail.com`` is, ``notgmail.com``
  isn't).
- ``render_footer_template(template, values)``: each ``{<name>}`` replaced once by
  ``values[name]``, which must be a ``str`` (a missing one raises ``ValueError`` naming the
  placeholder, never a value); values are never scanned again. A line left blank afterwards is
  dropped, so an optional line with a blank value disappears; the lines are joined with ``\\n``.
- ``placeholders(template)``: the names of every ``{<name>}`` the lines hold.
"""

import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import time
from enum import StrEnum
from typing import Any, Final

from nextron.domain.errors import DomainError
from nextron.domain.settings_schema import TIMEZONE_REASON, timezone_names

CODE_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]{0,15}")  # matched with fullmatch
COUNTRY_PATTERN: Final = re.compile(r"[A-Z]{2}")  # matched with fullmatch
CCTLD_PATTERN: Final = re.compile(r"[a-z]{2}")  # matched with fullmatch
LEGAL_FORM_PATTERN: Final = re.compile(r"[a-z][a-z0-9-]{0,63}")  # matched with fullmatch
SEND_WINDOW_PATTERN: Final = re.compile(r"([0-9]{2}):([0-9]{2})-([0-9]{2}):([0-9]{2})")
_FIELD_NAME: Final = re.compile(r"[a-z][a-z0-9_]{0,30}")  # an unknown field shown by name
_PLACEHOLDER: Final = re.compile(r"\{([^{}]*)\}")
_RENDERED_PLACEHOLDER: Final = re.compile(r"\{([a-z][a-z0-9_]{0,63})\}")

TOP_LEVEL: Final = "(top level)"  # the field path of a problem with the whole file
MAX_MARKETS: Final = 50
MAX_NAME: Final = 100
MAX_CCTLDS: Final = 10
MAX_LEGAL_FORMS: Final = 50
MAX_FOOTER_LINES: Final = 20
MAX_FOOTER_LINE: Final = 200
REFUSED_TEXT_CATEGORIES: Final = frozenset({"Cc", "Cf", "Zl", "Zp"})
NOT_CHECKED: Final = "not_checked"  # eligible_legal_forms when legal forms aren't checked
US: Final = "US"
GB: Final = "GB"
COLD_EMAIL_COUNTRIES: Final = frozenset({US, GB})  # NFR-5: cold email only to the US and UK
# Decision 2: the Companies House company types a GB Market may list (PECR: corporate
# subscribers). A seed may list fewer, never another.
_GB_CORPORATE_FORMS: Final = frozenset(
    {
        "ltd",
        "plc",
        "private-limited-guarant-nsc",
        "private-limited-guarant-nsc-limited-exemption",
        "private-limited-shares-section-30-exemption",
        "private-unlimited",
        "private-unlimited-nsc",
        "llp",
    }
)


class EnglishVariant(StrEnum):
    EN_US = "en_us"
    EN_GB = "en_gb"


class FooterPlaceholder(StrEnum):
    """What a footer template line may hold as ``{<name>}``: the Story 4.11 footer settings,
    ``privacy_line`` (computed by Story 4.11 from the Provenance source) and the
    ``us_solicitation_label`` setting."""

    SENDER_SIGNATURE = "sender_signature"
    LEGAL_NAME = "legal_name"
    POSTAL_ADDRESS = "postal_address"
    OPTOUT_LINE = "optout_line"
    PRIVACY_LINE = "privacy_line"
    US_SOLICITATION_LABEL = "us_solicitation_label"


REQUIRED_PLACEHOLDERS: Final = (  # FR-23: every footer names the sender and offers an opt-out
    FooterPlaceholder.SENDER_SIGNATURE,
    FooterPlaceholder.LEGAL_NAME,
    FooterPlaceholder.POSTAL_ADDRESS,
    FooterPlaceholder.OPTOUT_LINE,
)
STANDALONE_PLACEHOLDERS: Final = frozenset(
    {FooterPlaceholder.PRIVACY_LINE, FooterPlaceholder.US_SOLICITATION_LABEL}
)
_PLACEHOLDER_NAMES: Final = frozenset(member.value for member in FooterPlaceholder)
_PLACEHOLDERS_LISTED: Final = ", ".join(f"{{{member.value}}}" for member in FooterPlaceholder)

# Free webmail providers (Story 3.1 Design Notes, extended in review with more UK ISP and free
# providers): a domain, or any subdomain of one, is free webmail. A UK Market never emails one
# (PECR: corporate subscribers only).
FREE_WEBMAIL_DOMAINS: Final = frozenset(
    {
        # Google
        "gmail.com",
        "googlemail.com",
        # Microsoft
        "outlook.com",
        "hotmail.com",
        "hotmail.co.uk",
        "live.com",
        "live.co.uk",
        "msn.com",
        # Yahoo
        "yahoo.com",
        "yahoo.co.uk",
        "ymail.com",
        "rocketmail.com",
        # Apple
        "icloud.com",
        "me.com",
        "mac.com",
        # AOL
        "aol.com",
        "aol.co.uk",
        "aim.com",
        # Proton
        "protonmail.com",
        "protonmail.ch",
        "proton.me",
        "pm.me",
        # GMX, Web.de and Mail.com
        "gmx.com",
        "gmx.co.uk",
        "gmx.net",
        "gmx.de",
        "web.de",
        "mail.com",
        # Yandex
        "yandex.com",
        # UK providers
        "btinternet.com",
        "btopenworld.com",
        "talk21.com",
        "sky.com",
        "virginmedia.com",
        "virgin.net",
        "ntlworld.com",
        "talktalk.net",
        "blueyonder.co.uk",
        "tiscali.co.uk",
        "orange.net",
        "wanadoo.co.uk",
        "plus.com",
        "freeserve.co.uk",
        # Tuta
        "tutanota.com",
        "tutanota.de",
        "tuta.io",
        "tuta.com",
        # Zoho
        "zoho.com",
        "zohomail.com",
        # Others
        "fastmail.com",
        "hushmail.com",
    }
)


class InvalidMarkets(DomainError):
    """Market definitions that break a rule: ``field`` is the path of the first problem and
    ``reason`` says what a valid value is, repeating no value longer than 40 characters."""

    code = "invalid_markets"

    def __init__(self, field: str, reason: str) -> None:
        super().__init__(f"{field}: {reason}")
        self.field = field
        self.reason = reason


@dataclass(frozen=True, slots=True)
class SendWindow:
    """The hours an email may be sent, in the Market's local time: from ``start`` to ``end``."""

    start: time
    end: time


@dataclass(frozen=True, slots=True)
class MarketDef:
    code: str
    name: str
    enabled: bool
    country_code: str
    cctlds: tuple[str, ...]
    cold_email_allowed: bool
    eligible_legal_forms: tuple[str, ...] | None  # None: legal forms are not checked
    free_webmail_blocked: bool
    default_timezone: str
    unknown_timezone: str
    unknown_send_window: SendWindow | None
    english_variant: EnglishVariant
    footer_template: tuple[str, ...]  # one item per line
    privacy_line_required: bool


# Domains and countries --------------------------------------------------------------------------


def _domain(domain: str) -> str:
    """``domain`` lowercased and stripped, cut to the part after its last ``@``, with one
    trailing ``.`` dropped."""
    if not isinstance(domain, str):
        raise TypeError(f"a domain is a str, not {type(domain).__name__}")
    text = domain.lower().strip()
    if "@" in text:
        text = text.rpartition("@")[2]
    if text.endswith("."):
        text = text[:-1]
    return text


def cctld_country(domain: str) -> str | None:
    """The country of ``domain``'s two-letter last label (``uk`` is ``GB``), or ``None`` for a
    generic one; see the module docstring."""
    name = _domain(domain)
    if "." not in name:
        return None
    label = name.rpartition(".")[2]
    if CCTLD_PATTERN.fullmatch(label) is None:
        return None
    return GB if label == "uk" else label.upper()


def is_free_webmail(domain: str) -> bool:
    """Whether ``domain`` (or an address), or any parent of it, is a free webmail domain."""
    labels = _domain(domain).split(".")
    return any(".".join(labels[index:]) in FREE_WEBMAIL_DOMAINS for index in range(len(labels)))


def market_for_country(code: str | None, markets: Iterable[MarketDef]) -> MarketDef | None:
    """The enabled Market of the country ``code``, or ``None``; see the module docstring."""
    if not isinstance(code, str):
        return None
    stripped = code.strip()
    if not stripped.isascii():
        return None
    wanted = stripped.upper()
    if COUNTRY_PATTERN.fullmatch(wanted) is None:
        return None
    found = [market for market in markets if market.country_code == wanted]
    if len(found) != 1 or not found[0].enabled:
        return None
    return found[0]


# The footer -------------------------------------------------------------------------------------


def render_footer_template(template: Sequence[str], values: Mapping[str, str]) -> str:
    """``template``'s lines with each placeholder replaced by its value, blank lines dropped,
    joined with ``\\n``; ``ValueError`` names a placeholder without a ``str`` value."""
    if isinstance(template, str):
        raise TypeError("a footer template is a sequence of lines, not a str")

    def value_of(match: re.Match[str]) -> str:
        name = match.group(1)
        value = values.get(name)
        if not isinstance(value, str):
            raise ValueError(f"the footer placeholder {{{name}}} has no text value")
        return value

    lines = [_RENDERED_PLACEHOLDER.sub(value_of, line) for line in template]
    return "\n".join(line for line in lines if line.strip())


def placeholders(template: Iterable[str]) -> frozenset[str]:
    """The placeholder names ``template``'s lines hold (``{<name>}``, any name)."""
    return frozenset(match.group(1) for line in template for match in _PLACEHOLDER.finditer(line))


# Validation ------------------------------------------------------------------------------------

_FIELDS: Final = (
    "code",
    "name",
    "enabled",
    "country_code",
    "cctlds",
    "cold_email_allowed",
    "eligible_legal_forms",
    "free_webmail_blocked",
    "default_timezone",
    "unknown_timezone",
    "unknown_send_window",
    "english_variant",
    "footer_template",
    "privacy_line_required",
)
_OPTIONAL_FIELDS: Final = frozenset({"unknown_send_window"})
_REQUIRED_FIELDS: Final = tuple(name for name in _FIELDS if name not in _OPTIONAL_FIELDS)
SEND_WINDOW_REASON: Final = (
    "must be HH:MM-HH:MM, such as 11:00-17:00, with times from 00:00 to 23:59"
)

type _Check = Callable[[object, str], Any]


def _unknown_field(name: object, path: str) -> InvalidMarkets:
    if isinstance(name, str) and _FIELD_NAME.fullmatch(name):  # shown only when short and plain
        return InvalidMarkets(f"{path}.{name}" if path else name, "unknown field")
    return InvalidMarkets(path or TOP_LEVEL, "has an unknown field")


def _fields(
    value: object, path: str, checks: dict[str, _Check], required: tuple[str, ...]
) -> dict[str, Any]:
    """``value`` as a mapping whose fields are ``checks``: each present field checked in the
    file's order (an unknown one refused where it appears), then the missing ones of
    ``required`` refused."""
    if not isinstance(value, dict):
        raise InvalidMarkets(path, "must be a mapping")
    checked: dict[str, Any] = {}
    for name, item in value.items():
        check = checks.get(name) if isinstance(name, str) else None
        if check is None:
            raise _unknown_field(name, path)
        checked[name] = check(item, f"{path}.{name}")
    for name in required:
        if name not in value:
            raise InvalidMarkets(f"{path}.{name}", "missing")
    return checked


def _list(value: object, path: str, maximum: int, empty_reason: str | None = None) -> list[Any]:
    if not isinstance(value, list):
        raise InvalidMarkets(path, "must be a list")
    if not value:
        raise InvalidMarkets(path, empty_reason or "must hold at least 1 item")
    if len(value) > maximum:
        raise InvalidMarkets(path, f"must hold at most {maximum} items")
    return value


def _text(value: object, path: str, limit: int) -> str:
    if not isinstance(value, str):
        raise InvalidMarkets(path, "must be text")
    if not value.strip():
        raise InvalidMarkets(path, "must not be blank")
    if value != value.strip():
        raise InvalidMarkets(path, "must not start or end with whitespace")
    if any(unicodedata.category(char) in REFUSED_TEXT_CATEGORIES for char in value):
        raise InvalidMarkets(path, "must not hold control, format or line-separator characters")
    if len(value) > limit:
        raise InvalidMarkets(path, f"must be at most {limit} characters")
    return value


def _bool(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise InvalidMarkets(path, "must be true or false")
    return value


def _timezone(value: object, path: str) -> str:
    if not isinstance(value, str) or value not in timezone_names():
        raise InvalidMarkets(path, TIMEZONE_REASON)
    return value


def _english_variant(value: object, path: str) -> EnglishVariant:
    allowed = [member.value for member in EnglishVariant]
    if not isinstance(value, str) or value not in allowed:
        raise InvalidMarkets(path, f"must be one of {', '.join(allowed)}")
    return EnglishVariant(value)


def _clock_time(hours: str, minutes: str) -> time | None:
    hour, minute = int(hours), int(minutes)
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def _send_window(value: object, path: str) -> SendWindow:
    match = SEND_WINDOW_PATTERN.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise InvalidMarkets(path, SEND_WINDOW_REASON)
    start = _clock_time(match.group(1), match.group(2))
    end = _clock_time(match.group(3), match.group(4))
    if start is None or end is None:
        raise InvalidMarkets(path, SEND_WINDOW_REASON)
    if not start < end:
        raise InvalidMarkets(path, "must start before it ends")
    return SendWindow(start, end)


def send_window_text(window: SendWindow) -> str:
    """``window`` as its file form, ``HH:MM-HH:MM``."""
    return (
        f"{window.start.hour:02d}:{window.start.minute:02d}-"
        f"{window.end.hour:02d}:{window.end.minute:02d}"
    )


def _legal_forms(value: object, path: str) -> tuple[str, ...] | None:
    if value == NOT_CHECKED:
        return None
    if not isinstance(value, list):
        raise InvalidMarkets(path, f"must be {NOT_CHECKED} or a list of legal forms")
    items = _list(
        value,
        path,
        MAX_LEGAL_FORMS,
        f"must hold at least 1 item (write {NOT_CHECKED} when legal forms aren't checked)",
    )
    forms: list[str] = []
    for index, item in enumerate(items):
        item_path = f"{path}[{index}]"
        if not isinstance(item, str) or LEGAL_FORM_PATTERN.fullmatch(item) is None:
            raise InvalidMarkets(
                item_path,
                "must be lowercase letters, digits and hyphens, starting with a letter, at most "
                "64 characters",
            )
        if item in forms:
            raise InvalidMarkets(item_path, "repeats an earlier item")
        forms.append(item)
    return tuple(forms)


def _footer_line(text: str, path: str, seen: set[str]) -> None:
    """The placeholder rules of one footer line; ``seen`` holds the template's placeholders so
    far."""
    names: list[str] = []
    for match in _PLACEHOLDER.finditer(text):
        name = match.group(1)
        if name not in _PLACEHOLDER_NAMES:
            raise InvalidMarkets(
                path, f"holds an unknown placeholder: the placeholders are {_PLACEHOLDERS_LISTED}"
            )
        if name in seen:
            raise InvalidMarkets(
                path, f"repeats the placeholder {{{name}}}: each may appear only once"
            )
        seen.add(name)
        names.append(name)
    rest = _PLACEHOLDER.sub("", text)
    if "{" in rest or "}" in rest:
        raise InvalidMarkets(
            path, "holds a { or } that isn't part of a placeholder such as {legal_name}"
        )
    for name in names:
        if name in STANDALONE_PLACEHOLDERS and text != f"{{{name}}}":
            raise InvalidMarkets(path, f"{{{name}}} must stand alone on its line")


def _footer_template(value: object, path: str) -> tuple[str, ...]:
    lines = _list(value, path, MAX_FOOTER_LINES)
    seen: set[str] = set()
    result = []
    for index, line in enumerate(lines):
        line_path = f"{path}[{index}]"
        text = _text(line, line_path, MAX_FOOTER_LINE)
        _footer_line(text, line_path, seen)
        result.append(text)
    return tuple(result)


def _check_cctld_countries(market: MarketDef, path: str) -> None:
    for index, cctld in enumerate(market.cctlds):
        country = cctld_country(f"x.{cctld}")
        if country != market.country_code:
            raise InvalidMarkets(
                f"{path}.cctlds[{index}]",
                f"is the ccTLD of {country}, not of the Market's country code "
                f"{market.country_code}",
            )


def _check_compliance_floor(market: MarketDef, path: str) -> None:
    """Decision 3: rules a seed edit can't loosen (NFR-5, PECR)."""
    if market.cold_email_allowed and market.country_code not in COLD_EMAIL_COUNTRIES:
        raise InvalidMarkets(
            f"{path}.cold_email_allowed", "may be true only for a US or GB Market (NFR-5)"
        )
    if market.country_code != GB:
        return
    if not market.free_webmail_blocked:
        raise InvalidMarkets(
            f"{path}.free_webmail_blocked",
            "must be true for a GB Market (PECR: corporate subscribers only)",
        )
    if not market.privacy_line_required:
        raise InvalidMarkets(
            f"{path}.privacy_line_required", "must be true for a GB Market (UK GDPR)"
        )
    if market.eligible_legal_forms is None:
        raise InvalidMarkets(
            f"{path}.eligible_legal_forms",
            "must list the eligible legal forms for a GB Market (PECR: corporate subscribers "
            "only)",
        )
    for index, form in enumerate(market.eligible_legal_forms):
        if form not in _GB_CORPORATE_FORMS:
            raise InvalidMarkets(
                f"{path}.eligible_legal_forms[{index}]",
                "isn't a corporate-subscriber company type a GB Market may list (PECR)",
            )


def _check_footer_rules(market: MarketDef, path: str) -> None:
    field = f"{path}.footer_template"
    used = placeholders(market.footer_template)
    for name in REQUIRED_PLACEHOLDERS:
        if name not in used:
            raise InvalidMarkets(field, f"must hold {{{name}}} (FR-23)")
    privacy = FooterPlaceholder.PRIVACY_LINE
    if market.privacy_line_required and privacy not in used:
        raise InvalidMarkets(
            field, f"must hold {{{privacy}}}, because privacy_line_required is true"
        )
    if not market.privacy_line_required and privacy in used:
        raise InvalidMarkets(
            field, f"must not hold {{{privacy}}}, because privacy_line_required is false"
        )
    label = FooterPlaceholder.US_SOLICITATION_LABEL
    if market.country_code == US and label not in used:
        raise InvalidMarkets(field, f"a US Market's template must hold {{{label}}} (NFR-2)")
    if market.country_code != US and label in used:
        raise InvalidMarkets(field, f"only a US Market's template may hold {{{label}}}")


class _Validator:
    """One validation run: the codes, country codes and ccTLDs seen so far, for the uniqueness
    rules."""

    def __init__(self) -> None:
        self._codes: set[str] = set()
        self._countries: set[str] = set()
        self._cctlds: set[str] = set()

    def markets(self, data: object) -> tuple[MarketDef, ...]:
        if not isinstance(data, dict):
            raise InvalidMarkets(TOP_LEVEL, "must be a mapping with exactly one field, markets")
        result: tuple[MarketDef, ...] = ()
        for name, value in data.items():
            if name != "markets":
                raise _unknown_field(name, "")
            items = _list(value, "markets", MAX_MARKETS)
            result = tuple(self.market(item, index) for index, item in enumerate(items))
        if "markets" not in data:
            raise InvalidMarkets("markets", "missing")
        return result

    def market(self, value: object, index: int) -> MarketDef:
        code = value.get("code") if isinstance(value, dict) else None
        shown = code if _is_code(code) and code not in self._codes else str(index)
        path = f"markets[{shown}]"
        checked = _fields(
            value,
            path,
            {
                "code": self._code,
                "name": lambda v, p: _text(v, p, MAX_NAME),
                "enabled": _bool,
                "country_code": self._country,
                "cctlds": self._cctld_list,
                "cold_email_allowed": _bool,
                "eligible_legal_forms": _legal_forms,
                "free_webmail_blocked": _bool,
                "default_timezone": _timezone,
                "unknown_timezone": _timezone,
                "unknown_send_window": _send_window,
                "english_variant": _english_variant,
                "footer_template": _footer_template,
                "privacy_line_required": _bool,
            },
            _REQUIRED_FIELDS,
        )
        market = MarketDef(
            code=checked["code"],
            name=checked["name"],
            enabled=checked["enabled"],
            country_code=checked["country_code"],
            cctlds=checked["cctlds"],
            cold_email_allowed=checked["cold_email_allowed"],
            eligible_legal_forms=checked["eligible_legal_forms"],
            free_webmail_blocked=checked["free_webmail_blocked"],
            default_timezone=checked["default_timezone"],
            unknown_timezone=checked["unknown_timezone"],
            unknown_send_window=checked.get("unknown_send_window"),
            english_variant=checked["english_variant"],
            footer_template=checked["footer_template"],
            privacy_line_required=checked["privacy_line_required"],
        )
        _check_cctld_countries(market, path)
        _check_compliance_floor(market, path)
        _check_footer_rules(market, path)
        return market

    def _code(self, value: object, path: str) -> str:
        if not _is_code(value):
            raise InvalidMarkets(
                path,
                "must be lowercase letters, digits and underscores, starting with a letter, "
                "at most 16 characters",
            )
        assert isinstance(value, str)
        if value in self._codes:
            raise InvalidMarkets(path, f"repeats an earlier Market code ({value})")
        self._codes.add(value)
        return value

    def _country(self, value: object, path: str) -> str:
        if not isinstance(value, str) or COUNTRY_PATTERN.fullmatch(value) is None:
            raise InvalidMarkets(
                path, "must be a two-letter country code in capitals, such as US or GB"
            )
        if value in self._countries:
            raise InvalidMarkets(path, f"repeats the country code of an earlier Market ({value})")
        self._countries.add(value)
        return value

    def _cctld_list(self, value: object, path: str) -> tuple[str, ...]:
        items = _list(value, path, MAX_CCTLDS)
        found: list[str] = []
        for index, item in enumerate(items):
            item_path = f"{path}[{index}]"
            if not isinstance(item, str) or CCTLD_PATTERN.fullmatch(item) is None:
                raise InvalidMarkets(
                    item_path,
                    "must be two lowercase letters, a country-code top-level domain such as us",
                )
            if item in found:
                raise InvalidMarkets(item_path, "repeats an earlier item")
            if item in self._cctlds:
                raise InvalidMarkets(item_path, f"is also a ccTLD of an earlier Market ({item})")
            found.append(item)
        self._cctlds.update(found)
        return tuple(found)


def _is_code(value: object) -> bool:
    return isinstance(value, str) and CODE_PATTERN.fullmatch(value) is not None


def validate_markets(data: object) -> tuple[MarketDef, ...]:
    """The ``MarketDef``s of a parsed seed file, in file order; ``InvalidMarkets`` for the
    first problem. See the module docstring."""
    return _Validator().markets(data)


# The file form ----------------------------------------------------------------------------------


def market_form(market: MarketDef) -> dict[str, object]:
    """``market`` as the file's mapping: ``not_checked`` for unchecked legal forms, enums as
    their values, lists as lists, ``unknown_send_window`` only when set."""
    legal_forms = market.eligible_legal_forms
    form: dict[str, object] = {
        "code": market.code,
        "name": market.name,
        "enabled": market.enabled,
        "country_code": market.country_code,
        "cctlds": list(market.cctlds),
        "cold_email_allowed": market.cold_email_allowed,
        "eligible_legal_forms": NOT_CHECKED if legal_forms is None else list(legal_forms),
        "free_webmail_blocked": market.free_webmail_blocked,
        "default_timezone": market.default_timezone,
        "unknown_timezone": market.unknown_timezone,
    }
    window = market.unknown_send_window
    if window is not None:  # anything but whole-minute naive times stays as it is and fails
        whole = isinstance(window, SendWindow) and all(
            isinstance(at, time) and at.tzinfo is None and not (at.second or at.microsecond)
            for at in (window.start, window.end)
        )
        form["unknown_send_window"] = send_window_text(window) if whole else window
    form["english_variant"] = str(market.english_variant)
    form["footer_template"] = list(market.footer_template)
    form["privacy_line_required"] = market.privacy_line_required
    return form


def market_from_form(form: object) -> MarketDef:
    """The ``MarketDef`` of one file-form mapping, validated as one item of a file (path
    ``markets[<code>]``); ``InvalidMarkets`` for the first problem."""
    return _Validator().market(form, 0)
