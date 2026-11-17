"""The email footer (FR-23, NFR-2, NFR-7, CAN-SPAM, PECR, UK GDPR Art. 14; Story 4.11).

The spine's ``domain.render``: the single definition of ``footer`` and ``footer_complete``
(Story 4.12 adds ``compose`` and ``text_hash`` here). Standard library only, importing only
``nextron.domain``. Every email ends with a footer built from a Market's seeded footer template
(``nextron.domain.markets``, Story 3.1) and the footer settings
(``nextron.domain.settings_schema``); nothing here sends or composes an email.

- ``FooterSettings``: the six footer setting values as ``str`` (``""`` for one that is unset or
  whose stored text no longer parses: ``nextron.app.queries.setup.footer_settings()`` reads it).
  Its fields are the six setting keys; a non-``str`` value raises ``TypeError``.
- ``FOOTER_SETTING_KEYS``: those keys in declaration order (``sender_signature``,
  ``legal_name``, ``postal_address``, ``optout_line``, ``privacy_notice_url``,
  ``us_solicitation_label``). ``PLACEHOLDER_OF`` (read-only) maps each to the
  ``FooterPlaceholder`` its value fills: ``privacy_notice_url`` fills ``privacy_line`` (through
  ``privacy_line``), every other key the placeholder of its own name.
- ``PRIVACY_SOURCE_PHRASES`` (read-only) maps a Provenance source-kind code to the words a UK
  privacy line names it with: ``website`` "your firm's website", ``registry`` "Companies
  House", ``signal_inbox`` "a public post", ``csv`` "a business list", ``referral`` "a
  colleague's referral" and ``finder`` "a business email directory".
- ``privacy_line(source, url)``: ``"Your details came from <phrase>. Privacy notice: <url>"``,
  or ``"Your details came from <phrase>."`` when ``url`` is blank. A source without a phrase
  (or not a ``str``) raises ``ValueError`` (``NO_PHRASE``), which never shows the value: a UK
  footer never states a false source.
- ``footer(settings, market, provenance_source=None)``: ``render_footer_template`` of the
  Market's template with the five setting values and ``privacy_line`` =
  ``privacy_line(provenance_source, settings.privacy_notice_url)`` when the Market's
  ``privacy_line_required``, else ``""``. A line left blank after substitution is dropped (the
  seeded templates hold one placeholder per line, so there a blank value drops its line), and
  ``footer_complete`` still names the missing key: drafting proceeds with an incomplete footer,
  sending doesn't. A value is never scanned for placeholders again.
- ``footer_complete(settings, market) -> FooterCheck``: ``missing`` holds, in
  ``FOOTER_SETTING_KEYS`` order, every key of ``REQUIRED_PLACEHOLDERS`` (``sender_signature``,
  ``legal_name``, ``postal_address``, ``optout_line``) whose value is blank after ``strip()``,
  checked whatever the template holds, and ``privacy_notice_url`` when the Market's
  ``privacy_line_required`` and it is blank. ``FooterCheck.complete`` (and its truth value) is
  "nothing missing". An incomplete footer blocks sending (6.9's gate), never drafting.
- ``incomplete_notice(check)``: ``"Sending blocked: footer incomplete (<label>, <label>)"``,
  naming each missing key by its settings label; the one text 4.12's Draft view, 5.x's
  approval card and 6.9's gate show. A complete check raises ``ValueError``.
- ``footer_sha256(text)``: the lowercase hex SHA-256 of UTF-8 ``text`` (the
  ``footer_snapshots`` key).
"""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, fields
from types import MappingProxyType
from typing import Final

from nextron.domain.markets import (
    REQUIRED_PLACEHOLDERS,
    FooterPlaceholder,
    MarketDef,
    render_footer_template,
)
from nextron.domain.settings_schema import Key, setting

FOOTER_SETTING_KEYS: Final[tuple[str, ...]] = (
    Key.SENDER_SIGNATURE.value,
    Key.LEGAL_NAME.value,
    Key.POSTAL_ADDRESS.value,
    Key.OPTOUT_LINE.value,
    Key.PRIVACY_NOTICE_URL.value,
    Key.US_SOLICITATION_LABEL.value,
)
PLACEHOLDER_OF: Final[Mapping[str, FooterPlaceholder]] = MappingProxyType(
    {
        Key.SENDER_SIGNATURE.value: FooterPlaceholder.SENDER_SIGNATURE,
        Key.LEGAL_NAME.value: FooterPlaceholder.LEGAL_NAME,
        Key.POSTAL_ADDRESS.value: FooterPlaceholder.POSTAL_ADDRESS,
        Key.OPTOUT_LINE.value: FooterPlaceholder.OPTOUT_LINE,
        Key.PRIVACY_NOTICE_URL.value: FooterPlaceholder.PRIVACY_LINE,
        Key.US_SOLICITATION_LABEL.value: FooterPlaceholder.US_SOLICITATION_LABEL,
    }
)
_KEY_OF: Final[Mapping[FooterPlaceholder, str]] = MappingProxyType(
    {placeholder: key for key, placeholder in PLACEHOLDER_OF.items()}
)
PRIVACY_SOURCE_PHRASES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "website": "your firm's website",
        "registry": "Companies House",
        "signal_inbox": "a public post",
        "csv": "a business list",
        "referral": "a colleague's referral",
        "finder": "a business email directory",
    }
)
NO_PHRASE: Final = "no privacy-line phrase for this Provenance source"
INCOMPLETE_PREFIX: Final = "Sending blocked: footer incomplete"


@dataclass(frozen=True, slots=True)
class FooterSettings:
    """The six footer setting values, ``""`` for an unset or unreadable one."""

    sender_signature: str
    legal_name: str
    postal_address: str
    optout_line: str
    privacy_notice_url: str
    us_solicitation_label: str

    def __post_init__(self) -> None:
        for field in fields(self):
            if not isinstance(getattr(self, field.name), str):
                raise TypeError(f"the footer setting {field.name} must be a str")

    def value_of(self, key: str) -> str:
        """The value of the footer setting ``key`` (one of ``FOOTER_SETTING_KEYS``)."""
        if key not in FOOTER_SETTING_KEYS:
            raise KeyError(key)
        value: str = getattr(self, str(key))
        return value


@dataclass(frozen=True, slots=True)
class FooterCheck:
    """Which footer settings an email to one Market still needs: ``missing`` holds their keys
    in ``FOOTER_SETTING_KEYS`` order."""

    missing: tuple[str, ...]

    @property
    def complete(self) -> bool:
        """Whether nothing is missing."""
        return not self.missing

    def __bool__(self) -> bool:
        return self.complete


def privacy_line(source: object, url: str) -> str:
    """The UK privacy line naming ``source`` (a Provenance source-kind code) and linking to
    ``url``; see the module docstring."""
    phrase = PRIVACY_SOURCE_PHRASES.get(source) if isinstance(source, str) else None
    if phrase is None:
        raise ValueError(NO_PHRASE)
    if not url.strip():
        return f"Your details came from {phrase}."
    return f"Your details came from {phrase}. Privacy notice: {url}"


def footer(settings: FooterSettings, market: MarketDef, provenance_source: object = None) -> str:
    """The footer of an email to a Contact in ``market``, whose details came from
    ``provenance_source``; see the module docstring."""
    values: dict[str, str] = {
        PLACEHOLDER_OF[key].value: settings.value_of(key)
        for key in FOOTER_SETTING_KEYS
        if key != Key.PRIVACY_NOTICE_URL
    }
    values[FooterPlaceholder.PRIVACY_LINE.value] = (
        privacy_line(provenance_source, settings.privacy_notice_url)
        if market.privacy_line_required
        else ""
    )
    return render_footer_template(market.footer_template, values)


def footer_complete(settings: FooterSettings, market: MarketDef) -> FooterCheck:
    """Which footer settings an email to ``market`` still needs; see the module docstring."""
    needed = {_KEY_OF[placeholder] for placeholder in REQUIRED_PLACEHOLDERS}
    if market.privacy_line_required:
        needed.add(Key.PRIVACY_NOTICE_URL.value)
    return FooterCheck(
        tuple(
            key
            for key in FOOTER_SETTING_KEYS
            if key in needed and not settings.value_of(key).strip()
        )
    )


def incomplete_notice(check: FooterCheck) -> str:
    """``"Sending blocked: footer incomplete (<label>, ...)"`` for an incomplete ``check``."""
    if check.complete:
        raise ValueError("the footer is complete, so nothing blocks sending")
    labels = ", ".join(setting(key).label for key in check.missing)
    return f"{INCOMPLETE_PREFIX} ({labels})"


def footer_sha256(text: str) -> str:
    """The lowercase hex SHA-256 of ``text`` encoded as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
