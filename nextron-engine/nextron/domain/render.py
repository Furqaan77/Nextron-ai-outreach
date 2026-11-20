"""The email footer (FR-23, NFR-2, NFR-7, CAN-SPAM, PECR, UK GDPR Art. 14; Story 4.11).

The spine's ``domain.render``: the single definition of ``footer``, ``footer_complete``,
``compose``, ``canon`` and ``text_hash`` (Story 4.12: FR-20, AD-6). Standard library only,
importing only ``nextron.domain``. Every email ends with a footer built from a Market's seeded
footer template (``nextron.domain.markets``, Story 3.1) and the footer settings
(``nextron.domain.settings_schema``); nothing here sends an email.

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

**Composing (Story 4.12).** What the prospect receives, plain text only:

- ``compose(revision, footer_snapshot, recipient) -> Composed(to_address, subject, body)``:
  ``revision`` is anything with ``str`` ``subject`` and ``body`` (a Draft revision),
  ``footer_snapshot`` anything with a ``str`` ``text`` (a footer snapshot) and ``recipient``
  the to-address ``str``. ``subject`` is the revision's subject unchanged; ``body`` is the
  revision's body, a blank line (``BODY_FOOTER_SEPARATOR``, two line breaks) and the footer
  text, so an empty body still ends with the full footer (the footer is never part of the
  editable body). Nothing is added: no HTML, no tracking pixel, no link other than what the
  footer text holds, and no setting changes it. A value that isn't a ``str`` raises
  ``TypeError``.
- ``canon(text)``: NFC, CRLF and CR line breaks turned into LF, trailing whitespace stripped
  from each line, trailing blank lines dropped.
- ``text_hash(to_address, inbox_id, subject, body)``: the lowercase hex SHA-256 of the UTF-8 of
  the to-address, LF, the inbox id, LF, ``canon(subject)``, two LFs and ``canon(body)``, where
  ``body`` is the composed body. What an approval binds to (AD-6); transport headers are
  outside it. A value that isn't a ``str`` raises ``TypeError``.

**Edit ratio (Story 5.4).** ``edit_ratio(first, approved)``: ``levenshtein`` (character edits;
common prefix and suffix trimmed, then Myers' bit-parallel algorithm, no dependency) between
revision 1's and the approved revision's ``subject + "\\n" + body``, divided by revision 1's
length, capped at 1.0; equal texts give 0.0. Stored as ``approvals.edit_ratio``. Story 5.6:
``MAJOR_EDIT_RATIO`` (0.20) and ``is_major_edit(ratio)``, true only above it (0.21 is major, 0.20
isn't).
"""

import hashlib
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, fields
from types import MappingProxyType
from typing import Final, Protocol

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


# Composing (Story 4.12) ------------------------------------------------------------------------

LF: Final = "\n"
BODY_FOOTER_SEPARATOR: Final = LF + LF


class RevisionText(Protocol):
    """What ``compose`` reads of a Draft revision."""

    @property
    def subject(self) -> str: ...

    @property
    def body(self) -> str: ...


class FooterText(Protocol):
    """What ``compose`` reads of a footer snapshot."""

    @property
    def text(self) -> str: ...


@dataclass(frozen=True, slots=True)
class Composed:
    """One email exactly as the prospect receives it: plain text."""

    to_address: str
    subject: str
    body: str


def _str(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str, not {type(value).__name__}")
    return value


def compose(revision: RevisionText, footer_snapshot: FooterText, recipient: str) -> Composed:
    """``revision`` rendered for ``recipient`` with ``footer_snapshot``; see the module
    docstring."""
    subject = _str(revision.subject, "the revision's subject")
    body = _str(revision.body, "the revision's body")
    footer_text = _str(footer_snapshot.text, "the footer snapshot's text")
    to_address = _str(recipient, "the recipient")
    return Composed(to_address, subject, body + BODY_FOOTER_SEPARATOR + footer_text)


def canon(text: str) -> str:
    """``text`` canonicalised for ``text_hash``; see the module docstring."""
    normal = unicodedata.normalize("NFC", _str(text, "the text"))
    unified = normal.replace("\r\n", LF).replace("\r", LF)
    lines = [line.rstrip() for line in unified.split(LF)]
    while lines and not lines[-1]:
        lines.pop()
    return LF.join(lines)


def text_hash(to_address: str, inbox_id: str, subject: str, body: str) -> str:
    """The hash an approval binds to; see the module docstring."""
    content = LF.join(
        (
            _str(to_address, "the to-address"),
            _str(inbox_id, "the inbox id"),
            canon(subject),
            "",
            canon(body),
        )
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# Edit ratio (Story 5.4) -------------------------------------------------------------------------

MAX_EDIT_RATIO: Final = 1.0
MAJOR_EDIT_RATIO: Final = 0.20  # Story 5.6: an edit ratio above it is a major edit


def levenshtein(a: str, b: str) -> int:
    """The character Levenshtein distance between ``a`` and ``b``: the common prefix and suffix
    are trimmed, then Myers' bit-parallel algorithm (Hyyrö's global-distance form) runs over the
    shorter text as the pattern, one machine-word step per character of the longer one."""
    if a == b:
        return 0
    start = 0
    limit = min(len(a), len(b))
    while start < limit and a[start] == b[start]:
        start += 1
    end_a, end_b = len(a), len(b)
    while end_a > start and end_b > start and a[end_a - 1] == b[end_b - 1]:
        end_a -= 1
        end_b -= 1
    a, b = a[start:end_a], b[start:end_b]
    if len(a) > len(b):
        a, b = b, a
    if not a:
        return len(b)
    size = len(a)
    mask = (1 << size) - 1
    last = 1 << (size - 1)
    peq: dict[str, int] = {}
    for index, char in enumerate(a):
        peq[char] = peq.get(char, 0) | (1 << index)
    pv, mv, score = mask, 0, size
    for char in b:
        eq = peq.get(char, 0)
        xv = eq | mv
        xh = ((((eq & pv) + pv) & mask) ^ pv) | eq
        ph = (mv | ~(xh | pv)) & mask
        mh = pv & xh
        if ph & last:
            score += 1
        elif mh & last:
            score -= 1
        ph = ((ph << 1) | 1) & mask
        mh = (mh << 1) & mask
        pv = (mh | ~(xv | ph)) & mask
        mv = ph & xv
    return score


def edit_ratio(first: RevisionText, approved: RevisionText) -> float:
    """How much the approved text differs from revision 1 (FR-55, AD-4 analytics): the
    character Levenshtein distance between their ``subject + "\\n" + body``, divided by
    revision 1's length and capped at 1.0. Equal texts (both empty included) give 0.0."""
    original = _str(first.subject, "revision 1's subject") + LF + _str(first.body, "its body")
    final = _str(approved.subject, "the approved subject") + LF + _str(approved.body, "its body")
    if original == final:
        return 0.0
    if not original:
        return MAX_EDIT_RATIO
    return min(MAX_EDIT_RATIO, levenshtein(original, final) / len(original))


def is_major_edit(ratio: float) -> bool:
    """Whether an edit ratio counts as a major edit: above ``MAJOR_EDIT_RATIO`` (Story 5.6)."""
    if not isinstance(ratio, int | float) or isinstance(ratio, bool):
        raise TypeError("is_major_edit takes a number")
    return ratio > MAJOR_EDIT_RATIO
