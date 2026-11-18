"""How a Lead's website lets a visitor get in touch (FR-10, AD-18, Story 2.6): contact methods
detected on a page, and the Lead's profile value that lists them. Standard library only
(``html.parser`` included).

**Contact methods.** ``ContactMethod`` is ``form``, ``chat``, ``booking``, ``phone`` or
``after_hours`` (an after-hours or emergency line), in that order. ``MatchSource`` is where a
match came from: ``script``, ``iframe`` (an embed's URL), ``link`` (an ``a`` href), ``html`` (a
marker in the raw HTML) or ``text`` (the visible text).

**Signatures.** ``Signature(key, method, hosts, markers, phrases)``:

- ``hosts`` match a URL's host when it is equal to one or a subdomain of one; a host written
  with a path (``docs.google.com/forms``) also needs the URL's path to be that path or to start
  with it followed by ``/`` (both lowercased);
- ``markers`` are case-insensitive substrings of the raw HTML;
- ``phrases`` are case-insensitive whole phrases of the visible text: no letter or digit on
  either side, and any run of whitespace in the text matches a space in the phrase.

``CHAT_SIGNATURES`` (Intercom, Drift, LiveChat, Tawk.to, Zendesk Chat, HubSpot chat, Tidio,
Smith.ai, Ngage, ApexChat, Intaker, Podium, Olark, Crisp, Freshchat, Zoho SalesIQ, LiveAgent and
JivoChat: their widget hosts, their loader markers (widget hosts or loader globals such as
``$crisp``), and their product phrases) and ``BOOKING_SIGNATURES`` (Calendly, Cal.com, Acuity,
Clio Grow, LawTap, Microsoft Bookings, HubSpot meetings, Setmore, SimplyBook.me, YouCanBook.me,
Zoho Bookings and Google Calendar appointment pages: their hosts and product names) each have at
least one host and one phrase.
``FORM_SIGNATURES`` are vendor forms by host (HubSpot forms, Typeform, Jotform, Wufoo, Formstack,
Cognito Forms, Microsoft Forms, Google Forms) and WordPress form plugins by marker
(``gform_wrapper``, ``wpcf7``, ``wpforms-form``). ``GENERIC_SIGNATURES`` are phrase rules:
``chat_text`` ("live chat", "chat with us", "chat now", "start a chat"), ``booking_text`` ("book
online", "online booking", "schedule online", "book an appointment online", "book a
consultation online") and ``after_hours_text`` ("24/7", "24x7", "24 hours a day", "24-hour",
"around the clock", "after hours", "after-hours", "out of hours", "out-of-hours", "evenings and
weekends", "emergency line"). Three more generic rules are code:

- ``form_tag`` (``form``, ``html``): a ``<form>`` holding a ``<textarea>`` or an
  ``<input type="tel">``, read with ``html.parser``;
- ``tel_link`` (``phone``, ``html``): an element whose ``href`` starts ``tel:``;
- ``phone_text`` (``phone``, ``text``): a US number (10 digits, an optional ``+1``, separated by
  spaces, ``.``, ``-`` or a parenthesised area code) or a UK number (``+44``, optionally
  followed by ``(0)``, or ``0``, then 9 or 10 digits with optional single spaces or hyphens
  between them), with no digit on either side.

**Which sources check what.** ``script`` and ``iframe`` URLs check the hosts of every
signature; ``link`` URLs only those of booking and form signatures (a link to a chat vendor's
own site is not a chat widget); ``html`` checks the markers, ``form_tag`` and ``tel_link``;
``text`` checks the phrases and ``phone_text``.

**``detect(html, embeds, text) -> tuple[Detection, ...]``.** ``embeds`` is an iterable of
``(tag, url)`` pairs, ``tag`` one of ``a``, ``iframe`` or ``script``. A ``Detection(method,
signature, source, matched)`` keeps only the first match of a signature from a source: the
first embed in order, the marker or phrase found earliest. ``matched`` is the host of the URL
that matched (never the URL itself, which may carry account ids), the marker as the signature
writes it, or the text as the page writes it; at most ``MAX_MATCHED`` (200) characters, and
never in the ``repr`` (it may be a phone number). There is one detection per method, signature
and source, ordered by method, then by signature (``RULE_KEYS`` order), then by source. Input of
the wrong type, a pair that isn't one, or HTML the parser chokes on is skipped; ``detect`` never
raises.

**The Lead's profile value.** ``ContactMethods(run_id, checked_at, pages_checked, found)``:
the crawl run it was summarised from, when, the URLs checked (sorted, without repeats) and, for
each of the five methods, the sorted URLs it was detected on (possibly none).
``summarize(run_id, checked_at, pages)`` takes ``pages`` as ``(url, methods)`` pairs: every page
listed is checked. ``encode(value)`` writes canonical JSON (sorted keys, no spaces, ``{"v":1,
...}``, ``checked_at`` as ``YYYY-MM-DDTHH:MM:SS.ffffffZ``); ``decode(text)`` reads it back and
raises ``ValueError`` for anything ``encode`` wouldn't write (``"v"`` must be the integer 1, not
``1.0`` or ``true``).

**Tag managers (Story 2.9).** A tag manager can inject a chat widget that neither the raw HTML
nor the visible text names, so a page that loads one can't show that it has no chat.
``TagManager(key, hosts, markers)`` matches as a ``Signature`` does (``hosts`` against a URL's
host and path, ``markers`` as case-insensitive substrings of the raw HTML). ``TAG_MANAGERS``, in
order:

- ``google_tag_manager``: hosts ``googletagmanager.com/gtm.js`` and
  ``googletagmanager.com/ns.html`` (a container and its ``noscript`` frame); markers the same two
  and ``gtm.start`` (the container snippet). The Google tag (``googletagmanager.com/gtag/js``,
  for Analytics and Ads) loads only Google's own products and isn't one;
- ``segment``: hosts and markers ``cdn.segment.com`` and ``cdn.segment.io``;
- ``tealium``: host ``tags.tiqcdn.com``; markers ``tags.tiqcdn.com`` and ``utag.js``;
- ``adobe_launch``: host and marker ``assets.adobedtm.com``.

``tag_managers(html, embeds) -> tuple[str, ...]`` is the keys found, from ``script`` and
``iframe`` URLs (an ``a`` link loads nothing) and from raw-HTML markers, each once, in
``TAG_MANAGERS`` order. It takes ``detect``'s ``html`` and ``embeds`` and, like it, never raises.
"""

import json
import re
import urllib.parse
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from html.parser import HTMLParser
from types import MappingProxyType
from typing import Final

MAX_MATCHED: Final = 200
PROFILE_VERSION: Final = 1
KEY_PATTERN: Final = re.compile(r"[a-z][a-z0-9_]*")  # matched with fullmatch
UTC_TEXT: Final = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z")


class ContactMethod(StrEnum):
    """A way a visitor can get in touch, in the order detections are listed."""

    FORM = "form"
    CHAT = "chat"
    BOOKING = "booking"
    PHONE = "phone"
    AFTER_HOURS = "after_hours"


class MatchSource(StrEnum):
    """Where a match came from, in the order detections are listed."""

    SCRIPT = "script"
    IFRAME = "iframe"
    LINK = "link"
    HTML = "html"
    TEXT = "text"


SOURCE_OF_TAG: Final[Mapping[str, MatchSource]] = MappingProxyType(
    {"script": MatchSource.SCRIPT, "iframe": MatchSource.IFRAME, "a": MatchSource.LINK}
)


@dataclass(frozen=True, slots=True)
class Signature:
    """One product or rule: its key, the method it shows and what matches it."""

    key: str
    method: ContactMethod
    hosts: tuple[str, ...] = ()
    markers: tuple[str, ...] = ()
    phrases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or KEY_PATTERN.fullmatch(self.key) is None:
            raise ValueError("a signature key is lowercase letters, digits and underscores")
        if not isinstance(self.method, ContactMethod):
            raise TypeError("a signature's method must be a ContactMethod")
        for name in ("hosts", "markers", "phrases"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or not all(
                isinstance(value, str) and value and value == value.strip() for value in values
            ):
                raise ValueError(f"a signature's {name} must be a tuple of non-empty str")
        if any(host != host.lower() or "://" in host for host in self.hosts):
            raise ValueError("a signature's hosts are lowercase, without a scheme")
        if any(phrase != phrase.lower() for phrase in self.phrases):
            raise ValueError("a signature's phrases are lowercase")


CHAT: Final = ContactMethod.CHAT
BOOKING: Final = ContactMethod.BOOKING
FORM: Final = ContactMethod.FORM

CHAT_SIGNATURES: Final[tuple[Signature, ...]] = (
    Signature(
        "intercom",
        CHAT,
        hosts=("widget.intercom.io", "js.intercomcdn.com"),
        markers=("intercomSettings", "widget.intercom.io", "js.intercomcdn.com"),
        phrases=("powered by intercom",),
    ),
    Signature(
        "drift",
        CHAT,
        hosts=("js.driftt.com",),
        markers=("drift.load(", "js.driftt.com"),
        phrases=("powered by drift", "chat by drift"),
    ),
    Signature(
        "livechat",
        CHAT,
        hosts=("cdn.livechatinc.com",),
        markers=("__lc.license", "cdn.livechatinc.com"),
        phrases=("livechat",),
    ),
    Signature(
        "tawk_to",
        CHAT,
        hosts=("embed.tawk.to",),
        markers=("Tawk_API", "embed.tawk.to"),
        phrases=("tawk.to",),
    ),
    Signature(
        "zendesk_chat",
        CHAT,
        hosts=("static.zdassets.com", "zopim.com"),
        markers=("zESettings", "static.zdassets.com", "zopim.com"),
        phrases=("zendesk chat",),
    ),
    Signature(
        "hubspot_chat",
        CHAT,
        hosts=("js.usemessages.com",),
        markers=("HubSpotConversations", "js.usemessages.com"),
        phrases=("hubspot chat",),
    ),
    Signature(
        "tidio",
        CHAT,
        hosts=("code.tidio.co",),
        markers=("tidioChatApi", "code.tidio.co"),
        phrases=("tidio",),
    ),
    Signature("smith_ai", CHAT, hosts=("smith.ai",), markers=("smith.ai",), phrases=("smith.ai",)),
    Signature(
        "ngage",
        CHAT,
        hosts=("ngageics.com",),
        markers=("ngageics.com",),
        phrases=("ngage live",),
    ),
    Signature(
        "apexchat",
        CHAT,
        hosts=("apexchat.net", "apexchat.com"),
        markers=("apexchat.net", "apexchat.com"),
        phrases=("apexchat",),
    ),
    Signature(
        "intaker",
        CHAT,
        hosts=("intaker.com",),
        markers=("intaker.com",),
        phrases=("intaker",),
    ),
    Signature(
        "podium",
        CHAT,
        hosts=("podium.com",),
        markers=("connect.podium.com",),
        phrases=("powered by podium",),
    ),
    Signature(
        "olark",
        CHAT,
        hosts=("olark.com",),
        markers=("static.olark.com",),
        phrases=("olark",),
    ),
    Signature(
        "crisp",
        CHAT,
        hosts=("crisp.chat",),
        markers=("client.crisp.chat", "$crisp"),
        phrases=("crisp chat",),
    ),
    Signature(
        "freshchat",
        CHAT,
        hosts=("freshchat.com",),
        markers=("wchat.freshchat.com",),
        phrases=("freshchat",),
    ),
    Signature(
        "zoho_salesiq",
        CHAT,
        hosts=("salesiq.zoho.com", "salesiq.zohopublic.com", "salesiq.zoho.eu"),
        markers=("$zoho.salesiq",),
        phrases=("zoho salesiq",),
    ),
    Signature(
        "liveagent",
        CHAT,
        hosts=("ladesk.com",),
        markers=("ladesk.com",),
        phrases=("liveagent",),
    ),
    Signature(
        "jivochat",
        CHAT,
        hosts=("jivosite.com",),
        markers=("code.jivosite.com",),
        phrases=("jivochat",),
    ),
)

BOOKING_SIGNATURES: Final[tuple[Signature, ...]] = (
    Signature("calendly", BOOKING, hosts=("calendly.com",), phrases=("calendly",)),
    Signature("cal_com", BOOKING, hosts=("cal.com",), phrases=("cal.com",)),
    Signature(
        "acuity",
        BOOKING,
        hosts=("acuityscheduling.com", "as.me"),
        phrases=("acuity scheduling",),
    ),
    Signature("clio_grow", BOOKING, hosts=("grow.clio.com",), phrases=("clio grow",)),
    Signature("lawtap", BOOKING, hosts=("lawtap.com",), phrases=("lawtap",)),
    Signature(
        "ms_bookings",
        BOOKING,
        hosts=(
            "outlook.office365.com/owa/calendar",
            "outlook.office365.com/book",
            "outlook.office.com/book",
        ),
        phrases=("microsoft bookings",),
    ),
    Signature(
        "hubspot_meetings",
        BOOKING,
        hosts=("meetings.hubspot.com",),
        phrases=("hubspot meetings",),
    ),
    Signature("setmore", BOOKING, hosts=("setmore.com",), phrases=("setmore",)),
    Signature("simplybook", BOOKING, hosts=("simplybook.me",), phrases=("simplybook.me",)),
    Signature("youcanbookme", BOOKING, hosts=("youcanbook.me",), phrases=("youcanbook.me",)),
    Signature(
        "zoho_bookings",
        BOOKING,
        hosts=("bookings.zoho.com", "zohobookings.com"),
        phrases=("zoho bookings",),
    ),
    Signature(
        "google_appointments",
        BOOKING,
        hosts=("calendar.app.google", "calendar.google.com/calendar/appointments"),
        phrases=("google calendar appointment",),
    ),
)

FORM_SIGNATURES: Final[tuple[Signature, ...]] = (
    Signature("hubspot_forms", FORM, hosts=("hsforms.net", "hsforms.com")),
    Signature("typeform", FORM, hosts=("typeform.com",)),
    Signature("jotform", FORM, hosts=("jotform.com", "jotfor.ms")),
    Signature("wufoo", FORM, hosts=("wufoo.com",)),
    Signature("formstack", FORM, hosts=("formstack.com",)),
    Signature("cognito_forms", FORM, hosts=("cognitoforms.com",)),
    Signature("microsoft_forms", FORM, hosts=("forms.office.com", "forms.microsoft.com")),
    Signature("google_forms", FORM, hosts=("docs.google.com/forms", "forms.gle")),
    Signature("gravity_forms", FORM, markers=("gform_wrapper",)),
    Signature("contact_form_7", FORM, markers=("wpcf7",)),
    Signature("wpforms", FORM, markers=("wpforms-form",)),
)

CHAT_TEXT: Final = "chat_text"
BOOKING_TEXT: Final = "booking_text"
AFTER_HOURS_TEXT: Final = "after_hours_text"
FORM_TAG: Final = "form_tag"
TEL_LINK: Final = "tel_link"
PHONE_TEXT: Final = "phone_text"

GENERIC_SIGNATURES: Final[tuple[Signature, ...]] = (
    Signature(
        CHAT_TEXT, CHAT, phrases=("live chat", "chat with us", "chat now", "start a chat")
    ),
    Signature(
        BOOKING_TEXT,
        BOOKING,
        phrases=(
            "book online",
            "online booking",
            "schedule online",
            "book an appointment online",
            "book a consultation online",
        ),
    ),
    Signature(
        AFTER_HOURS_TEXT,
        ContactMethod.AFTER_HOURS,
        phrases=(
            "24/7",
            "24x7",
            "24 hours a day",
            "24-hour",
            "around the clock",
            "after hours",
            "after-hours",
            "out of hours",
            "out-of-hours",
            "evenings and weekends",
            "emergency line",
        ),
    ),
)

SIGNATURES: Final[tuple[Signature, ...]] = (
    *CHAT_SIGNATURES,
    *BOOKING_SIGNATURES,
    *FORM_SIGNATURES,
    *GENERIC_SIGNATURES,
)
# Every rule key, in the order detections of one method are listed
RULE_KEYS: Final[tuple[str, ...]] = (
    *(signature.key for signature in SIGNATURES),
    FORM_TAG,
    TEL_LINK,
    PHONE_TEXT,
)
EMBED_SIGNATURES: Final = tuple(signature for signature in SIGNATURES if signature.hosts)
LINK_SIGNATURES: Final = tuple(
    signature for signature in EMBED_SIGNATURES if signature.method in (BOOKING, FORM)
)
MARKER_SIGNATURES: Final = tuple(signature for signature in SIGNATURES if signature.markers)
PHRASE_SIGNATURES: Final = tuple(signature for signature in SIGNATURES if signature.phrases)

_METHOD_ORDER: Final = {method: index for index, method in enumerate(ContactMethod)}
_SOURCE_ORDER: Final = {source: index for index, source in enumerate(MatchSource)}
_RULE_ORDER: Final = {key: index for index, key in enumerate(RULE_KEYS)}

# Whole phrases: no letter or digit on either side
_NOT_WORD_BEFORE: Final = r"(?<![^\W_])"
_NOT_WORD_AFTER: Final = r"(?![^\W_])"
US_PHONE: Final = re.compile(
    r"(?<![\d+])(?:\+1[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]?\d{3}[ .-]?\d{4}(?!\d)"
)
UK_PHONE: Final = re.compile(r"(?<![\d+])(?:\+44 ?(?:\(0\) ?)?|0)(?:\d[ -]?){8,9}\d(?!\d)")
FORM_FIELD_TEXTAREA: Final = "form textarea"
FORM_FIELD_TEL: Final = "form input[type=tel]"
TEL_SCHEME: Final = "tel:"


def _phrase_pattern(phrases: Iterable[str]) -> re.Pattern[str]:
    """One pattern for ``phrases``, longest first, each matched whole and case-insensitively."""
    parts = sorted(
        (r"\s+".join(re.escape(word) for word in phrase.split()) for phrase in phrases),
        key=len,
        reverse=True,
    )
    return re.compile(
        _NOT_WORD_BEFORE + "(?:" + "|".join(parts) + ")" + _NOT_WORD_AFTER, re.IGNORECASE
    )


_PHRASES: Final[Mapping[str, re.Pattern[str]]] = MappingProxyType(
    {signature.key: _phrase_pattern(signature.phrases) for signature in PHRASE_SIGNATURES}
)


@dataclass(frozen=True, slots=True)
class Detection:
    """One contact method found on a page; ``matched`` never shows in the ``repr``."""

    method: ContactMethod
    signature: str
    source: MatchSource
    matched: str = field(repr=False)


# Detecting ------------------------------------------------------------------------------------


def _host_and_path(url: object) -> tuple[str, str] | None:
    """A URL's lowercased host (no trailing dot) and path, or ``None``."""
    if not isinstance(url, str):
        return None
    try:
        parts = urllib.parse.urlsplit(url.strip())
        host = parts.hostname
    except (ValueError, TypeError):
        return None
    if not host:
        return None
    return host.rstrip(".").lower(), parts.path.lower()


def _host_matches(spec: str, host: str, path: str) -> bool:
    name, _, prefix = spec.partition("/")
    if host != name and not host.endswith("." + name):
        return False
    if not prefix:
        return True
    wanted = "/" + prefix
    return path == wanted or path.startswith(wanted + "/")


def _pairs(embeds: object) -> list[tuple[MatchSource, str]]:
    """The ``(source, url)`` of each well-formed ``(tag, url)`` pair; the rest are skipped."""
    found: list[tuple[MatchSource, str]] = []
    if isinstance(embeds, str | bytes) or embeds is None:
        return found
    try:
        for pair in embeds:  # type: ignore[attr-defined]
            if not isinstance(pair, tuple | list) or len(pair) != 2:
                continue
            tag, url = pair
            if isinstance(tag, str) and tag in SOURCE_OF_TAG and isinstance(url, str):
                found.append((SOURCE_OF_TAG[tag], url))
    except Exception:  # noqa: BLE001 - garbage input never raises: keep what was read
        return found
    return found


class _Scan(HTMLParser):
    """One pass over the raw HTML for ``form_tag`` and ``tel_link``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms = 0
        self.form_field: str | None = None
        self.tel: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "form":
            self.forms += 1
        elif self.forms and self.form_field is None:
            if tag == "textarea":
                self.form_field = FORM_FIELD_TEXTAREA
            elif tag == "input" and any(
                name == "type" and (value or "").strip().lower() == "tel" for name, value in attrs
            ):
                self.form_field = FORM_FIELD_TEL
        if self.tel is None:
            for name, value in attrs:
                if name == "href" and value and value.strip().lower().startswith(TEL_SCHEME):
                    self.tel = value.strip()
                    break

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self.forms:
            self.forms -= 1


def _scan(html: str) -> _Scan:
    scan = _Scan()
    try:
        scan.feed(html)
        scan.close()
    except Exception:  # noqa: BLE001 - HTML the parser chokes on keeps what was read
        pass
    return scan


def _first_marker(lowered: str, markers: Iterable[str]) -> str | None:
    """The marker found earliest in ``lowered`` (the first listed on a tie), or ``None``."""
    best: tuple[int, str] | None = None
    for marker in markers:
        index = lowered.find(marker.lower())
        if index >= 0 and (best is None or index < best[0]):
            best = (index, marker)
    return None if best is None else best[1]


def _first_phone(text: str) -> str | None:
    found = [match for match in (US_PHONE.search(text), UK_PHONE.search(text)) if match]
    if not found:
        return None
    return min(found, key=lambda match: match.start()).group(0)


def _order(detection: Detection) -> tuple[int, int, int]:
    return (
        _METHOD_ORDER[detection.method],
        _RULE_ORDER[detection.signature],
        _SOURCE_ORDER[detection.source],
    )


def detect(html: str, embeds: Iterable[tuple[str, str]], text: str) -> tuple[Detection, ...]:
    """The contact methods a page shows; see the module docstring."""
    html = html if isinstance(html, str) else ""
    text = text if isinstance(text, str) else ""
    found: dict[tuple[ContactMethod, str, MatchSource], Detection] = {}

    def add(method: ContactMethod, key: str, source: MatchSource, matched: str) -> None:
        if (method, key, source) not in found:
            found[(method, key, source)] = Detection(method, key, source, matched[:MAX_MATCHED])

    for source, url in _pairs(embeds):
        parsed = _host_and_path(url)
        if parsed is None:
            continue
        host, path = parsed
        candidates = LINK_SIGNATURES if source is MatchSource.LINK else EMBED_SIGNATURES
        for signature in candidates:
            if any(_host_matches(spec, host, path) for spec in signature.hosts):
                add(signature.method, signature.key, source, host)
    lowered = html.lower()
    for signature in MARKER_SIGNATURES:
        marker = _first_marker(lowered, signature.markers)
        if marker is not None:
            add(signature.method, signature.key, MatchSource.HTML, marker)
    scan = _scan(html)
    if scan.form_field is not None:
        add(ContactMethod.FORM, FORM_TAG, MatchSource.HTML, scan.form_field)
    if scan.tel is not None:
        add(ContactMethod.PHONE, TEL_LINK, MatchSource.HTML, scan.tel)
    for signature in PHRASE_SIGNATURES:
        match = _PHRASES[signature.key].search(text)
        if match is not None:
            add(signature.method, signature.key, MatchSource.TEXT, match.group(0))
    phone = _first_phone(text)
    if phone is not None:
        add(ContactMethod.PHONE, PHONE_TEXT, MatchSource.TEXT, phone)
    return tuple(sorted(found.values(), key=_order))


# Tag managers (Story 2.9) ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TagManager:
    """A tag manager: its key and what matches it, as a ``Signature`` matches."""

    key: str
    hosts: tuple[str, ...]
    markers: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.key, str) or KEY_PATTERN.fullmatch(self.key) is None:
            raise ValueError("a tag manager key is lowercase letters, digits and underscores")
        for name in ("hosts", "markers"):
            values = getattr(self, name)
            if (
                not isinstance(values, tuple)
                or not values
                or not all(
                    isinstance(value, str) and value and value == value.strip()
                    for value in values
                )
            ):
                raise ValueError(f"a tag manager's {name} must be a non-empty tuple of str")
        if any(host != host.lower() or "://" in host for host in self.hosts):
            raise ValueError("a tag manager's hosts are lowercase, without a scheme")


TAG_MANAGERS: Final[tuple[TagManager, ...]] = (
    TagManager(
        "google_tag_manager",
        hosts=("googletagmanager.com/gtm.js", "googletagmanager.com/ns.html"),
        markers=("googletagmanager.com/gtm.js", "googletagmanager.com/ns.html", "gtm.start"),
    ),
    TagManager(
        "segment",
        hosts=("cdn.segment.com", "cdn.segment.io"),
        markers=("cdn.segment.com", "cdn.segment.io"),
    ),
    TagManager("tealium", hosts=("tags.tiqcdn.com",), markers=("tags.tiqcdn.com", "utag.js")),
    TagManager("adobe_launch", hosts=("assets.adobedtm.com",), markers=("assets.adobedtm.com",)),
)
# The embeds that load something into the page: an ``a`` link loads nothing
_LOADING_SOURCES: Final = frozenset({MatchSource.SCRIPT, MatchSource.IFRAME})


def tag_managers(html: str, embeds: Iterable[tuple[str, str]]) -> tuple[str, ...]:
    """The keys of the tag managers a page loads; see the module docstring."""
    html = html if isinstance(html, str) else ""
    found: set[str] = set()
    for source, url in _pairs(embeds):
        if source not in _LOADING_SOURCES:
            continue
        parsed = _host_and_path(url)
        if parsed is None:
            continue
        host, path = parsed
        for manager in TAG_MANAGERS:
            if any(_host_matches(spec, host, path) for spec in manager.hosts):
                found.add(manager.key)
    lowered = html.lower()
    for manager in TAG_MANAGERS:
        if _first_marker(lowered, manager.markers) is not None:
            found.add(manager.key)
    return tuple(manager.key for manager in TAG_MANAGERS if manager.key in found)


# The Lead's profile value ---------------------------------------------------------------------


def _is_sorted_unique(values: object) -> bool:
    return (
        isinstance(values, tuple)
        and all(isinstance(value, str) for value in values)
        and list(values) == sorted(set(values))
    )


@dataclass(frozen=True, slots=True)
class ContactMethods:
    """The contact methods a Lead's run found, and every page it checked."""

    run_id: str
    checked_at: datetime
    pages_checked: tuple[str, ...]
    found: Mapping[ContactMethod, tuple[str, ...]]

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id:
            raise TypeError("run_id must be a non-empty str")
        if not isinstance(self.checked_at, datetime) or self.checked_at.utcoffset() is None:
            raise TypeError("checked_at must be an aware datetime")
        if not _is_sorted_unique(self.pages_checked):
            raise ValueError("pages_checked must be a sorted tuple of distinct str")
        if not isinstance(self.found, Mapping) or set(self.found) != set(ContactMethod):
            raise ValueError("found must map each of the five contact methods")
        checked = set(self.pages_checked)
        for urls in self.found.values():
            if not _is_sorted_unique(urls) or not set(urls) <= checked:
                raise ValueError("found must list sorted, distinct pages that were checked")
        ordered = {method: self.found[method] for method in ContactMethod}
        object.__setattr__(self, "found", MappingProxyType(ordered))


def summarize(
    run_id: str,
    checked_at: datetime,
    pages: Iterable[tuple[str, Iterable[ContactMethod | str]]],
) -> ContactMethods:
    """The profile value of a run's ``pages``, each a ``(url, methods)`` pair."""
    checked: set[str] = set()
    by_method: dict[ContactMethod, set[str]] = {method: set() for method in ContactMethod}
    for url, methods in pages:
        if not isinstance(url, str):
            raise TypeError("a page's url must be a str")
        checked.add(url)
        for method in methods:
            by_method[ContactMethod(method)].add(url)
    return ContactMethods(
        run_id=run_id,
        checked_at=checked_at,
        pages_checked=tuple(sorted(checked)),
        found={method: tuple(sorted(urls)) for method, urls in by_method.items()},
    )


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="microseconds") + "Z"


def encode(value: ContactMethods) -> str:
    """``value`` as canonical JSON; see the module docstring."""
    if not isinstance(value, ContactMethods):
        raise TypeError("encode takes a ContactMethods")
    document = {
        "v": PROFILE_VERSION,
        "run_id": value.run_id,
        "checked_at": _utc_text(value.checked_at),
        "pages_checked": list(value.pages_checked),
        "found": {str(method): list(urls) for method, urls in value.found.items()},
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _str_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("expected a list of str")
    return tuple(value)


def decode(text: str) -> ContactMethods:
    """The ``ContactMethods`` that ``encode`` wrote as ``text``; ``ValueError`` otherwise."""
    if not isinstance(text, str):
        raise ValueError("a stored contact_methods value is a str")
    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        raise ValueError("the stored contact_methods value isn't JSON") from None
    if not isinstance(document, dict) or set(document) != {
        "v",
        "run_id",
        "checked_at",
        "pages_checked",
        "found",
    }:
        raise ValueError("the stored contact_methods value has the wrong fields")
    if type(document["v"]) is not int or document["v"] != PROFILE_VERSION:  # not 1.0 or True
        raise ValueError("the stored contact_methods value has an unknown version")
    stamp = document["checked_at"]
    if not isinstance(stamp, str) or UTC_TEXT.fullmatch(stamp) is None:
        raise ValueError("the stored checked_at isn't UTC text")
    found = document["found"]
    if not isinstance(found, dict) or set(found) != {str(method) for method in ContactMethod}:
        raise ValueError("the stored contact_methods value must list the five methods")
    try:
        return ContactMethods(
            run_id=document["run_id"],
            checked_at=datetime.fromisoformat(stamp[:-1]).replace(tzinfo=UTC),
            pages_checked=_str_list(document["pages_checked"]),
            found={ContactMethod(name): _str_list(urls) for name, urls in found.items()},
        )
    except TypeError:
        raise ValueError("the stored contact_methods value is malformed") from None
