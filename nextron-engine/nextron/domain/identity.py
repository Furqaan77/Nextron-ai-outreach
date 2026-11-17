"""Identity normalisation (FR-9, SC-8): the only module that normalises a Lead's or a Contact's
identity. Standard library only.

Every refusal raises ``IdentityError(reason)`` (code ``invalid_identity``), whose message is
the reason and never holds the input, which may be an email address. A non-``str`` argument
raises ``TypeError``, and any input longer than ``MAX_INPUT_LENGTH`` (2,048) characters is
refused. Each normaliser is idempotent: it returns its own output unchanged.

- ``url_host(text)``: a website's or URL's host name, steps 1 to 4 below: the ASCII,
  lowercased, IDNA-encoded host, with one trailing separator dropped and ``www`` kept
  (``https://WWW.Smith-Law.co.uk./team`` gives ``www.smith-law.co.uk``). It applies every
  refusal of those steps; nothing checks the host against the Public Suffix List.
  ``nextron.domain.fetch_policy`` matches hosts with it.
- ``registrable_domain(text)``: a website's registrable domain (eTLD+1).

  1. The text is stripped; an empty text, or one holding whitespace or a ``Cc`` character, is
     refused.
  2. A text with a ``<scheme>://`` prefix must use ``http`` or ``https`` (in any case); any
     other scheme is refused. A text without one is read as ``http://`` + text.
  3. The hostname comes from ``urllib.parse.urlsplit``; a missing one or an IP address is
     refused. One trailing separator is dropped (and the rest checked again).
  4. Each label (split on the IDNA 2003 separators: ``.``, ``U+3002``, ``U+FF0E``,
     ``U+FF61``) is IDNA-encoded with the stdlib ``encodings.idna.ToASCII`` (IDNA 2003) and
     lowercased, and must fullmatch ``LABEL_PATTERN`` (letters, digits and hyphens, at most
     63). The whole name is at most 253 characters. A label holding one of the IDNA 2008
     deviation characters (``DEVIATION_CHARACTERS``: U+00DF, U+1E9E, U+03C2, ZWNJ, ZWJ) is
     refused: IDNA 2003 maps them away, so ``stra\u00dfe.de`` would become ``strasse.de``,
     another registered domain.
  5. One leading ``www`` label is stripped.
  6. The result is eTLD+1 by the Public Suffix List algorithm over both of its sections: the
     prevailing rule is the matching exception rule if there is one, otherwise the longest
     matching rule (wildcards count). A name that is itself a public suffix, a single label,
     or under a top-level label no rule matches (``example.local``) is refused, and so is a
     registrable domain whose own first label is ``www`` (``a.www.co.uk`` would give
     ``www.co.uk``, which step 5 refuses), so the result is idempotent.

- ``normalize_email(text)``: stripped, NFC'd, lowercased and NFC'd again (lowercasing can
  leave a pair NFC composes, such as ``J`` and U+030C). Whitespace, a ``Cc`` or ``Cf`` character
  (a zero-width space or soft hyphen, say) or anything other than exactly one ``@`` is refused.
  The local part (1 to 64 characters) is kept as it is: no dot or plus-tag stripping. The
  domain is IDNA-encoded label by label like a website's (letters, digits and hyphens, at least
  two labels, no deviation character). The whole address is at most 254 characters.
- ``normalize_name(text)``: NFKC, then ``casefold``; each run of characters that are neither
  ``isalnum()`` nor a combining mark (category ``M*``) becomes one space, and the result is
  stripped and NFKC'd again. An empty result, or an input over 500 characters, is refused.
- ``normalize_linkedin_url(text)``: only ``http(s)`` (or no scheme), on ``linkedin.com`` or a
  ``*.linkedin.com`` host, with a first path segment ``in`` (in any case) followed by a slug.
  The slug is percent-decoded (strict UTF-8), NFC'd, lowercased and NFC'd again; it must be 1
  to 100 characters with no whitespace, ``Cc`` or ``Cf`` character. The result is
  ``https://www.linkedin.com/in/<slug>``, the slug re-encoded with
  ``urllib.parse.quote(slug, safe="-_.~")``; further segments, the query and the fragment are
  dropped. Every other URL is refused.
- ``normalize_company_number(text)``: stripped and uppercased (ASCII only). 1 to 8 digits are
  zero-padded to 8; otherwise the text must fullmatch ``[A-Z0-9]{8}`` with at least one digit.
- ``normalize_place_id(text)``: stripped; it must fullmatch ``[A-Za-z0-9_-]{1,512}``. Case is
  kept.
- ``normalize_country_code(text)``: stripped and uppercased (ASCII only); an alias in
  ``COUNTRY_ALIASES`` becomes its code (``UK`` is ``GB``, the one alias), and the result must be
  in ``COUNTRY_CODES``, the 249 assigned ISO 3166-1 alpha-2 codes (the set in tzdata's
  ``iso3166.tab``).

**The Public Suffix List** is vendored byte for byte at ``nextron/domain/psl/
public_suffix_list.dat`` (MPL-2.0, header kept; its SHA-256 is pinned in
``tests/contracts/test_vendored_assets.py``). It is parsed once; each rule's labels are
IDNA-encoded the same way as a domain's. ``psl_version()`` returns the header's ``VERSION``.
To update it, replace the file byte for byte from publicsuffix.org, then record its new SHA-256
and version in that test.
"""

import functools
import ipaddress
import re
import unicodedata
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from encodings import idna
from importlib import resources
from types import MappingProxyType
from typing import Final

from nextron.domain.errors import DomainError

MAX_INPUT_LENGTH: Final = 2048
MAX_NAME_INPUT_LENGTH: Final = 500
MAX_DOMAIN_LENGTH: Final = 253
MAX_LOCAL_PART_LENGTH: Final = 64
MAX_EMAIL_LENGTH: Final = 254
MAX_SLUG_LENGTH: Final = 100
COMPANY_NUMBER_LENGTH: Final = 8

WWW: Final = "www"
WEB_SCHEMES: Final = frozenset({"http", "https"})
LINKEDIN_HOST: Final = "linkedin.com"
LINKEDIN_PROFILE_PREFIX: Final = "https://www.linkedin.com/in/"
LINKEDIN_PROFILE_SEGMENT: Final = "in"
SLUG_SAFE: Final = "-_.~"

PSL_PATH: Final = ("psl", "public_suffix_list.dat")
PSL_VERSION_PREFIX: Final = "// VERSION:"
PSL_SECTIONS: Final = ("ICANN DOMAINS", "PRIVATE DOMAINS")
PSL_WILDCARD: Final = "*"
PSL_EXCEPTION: Final = "!"

# Each matched with fullmatch, except SCHEME_PATTERN (match, at the start).
LABEL_PATTERN: Final = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
SCHEME_PATTERN: Final = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*)://")
DIGITS_PATTERN: Final = re.compile(r"[0-9]{1,8}")
COMPANY_NUMBER_PATTERN: Final = re.compile(r"[A-Z0-9]{8}")
PLACE_ID_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]{1,512}")
# The IDNA 2008 deviation characters, which IDNA 2003 maps to other text (sharp s to ss, final
# sigma to sigma) or drops (ZWNJ, ZWJ): a label holding one is refused, never matched to
# another domain.
DEVIATION_CHARACTERS: Final = frozenset("\u00df\u1e9e\u03c2\u200c\u200d")
# The IDNA 2003 label separators (RFC 3490 section 3.1), as in encodings.idna.
LABEL_SEPARATORS: Final = re.compile("[.。．｡]")

# The 249 assigned ISO 3166-1 alpha-2 codes: the codes of tzdata's zoneinfo/iso3166.tab.
COUNTRY_CODES: Final = frozenset(
    """
    AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ
    BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM
    DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS
    GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN
    KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ
    MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM
    PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV
    SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI
    VN VU WF WS YE YT ZA ZM ZW
    """.split()
)
# Common names that aren't ISO codes, each mapped to its code (Story 2.3: CSV files say UK).
COUNTRY_ALIASES: Final[Mapping[str, str]] = MappingProxyType({"UK": "GB"})


class IdentityError(DomainError):
    """An identity that can't be normalised. The message is the reason, never the input."""

    code = "invalid_identity"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# Shared checks --------------------------------------------------------------------------------


def _checked(text: object, function: str) -> str:
    """``text`` itself, once it is a ``str`` of at most ``MAX_INPUT_LENGTH`` characters."""
    if not isinstance(text, str):
        raise TypeError(f"{function} takes a str, not {type(text).__name__}")
    if len(text) > MAX_INPUT_LENGTH:
        raise IdentityError(f"is longer than {MAX_INPUT_LENGTH:,} characters")
    return text


def _has_space_or_control(text: str) -> bool:
    return any(ch.isspace() or unicodedata.category(ch) == "Cc" for ch in text)


def _has_space_control_or_format(text: str) -> bool:
    return any(ch.isspace() or unicodedata.category(ch) in ("Cc", "Cf") for ch in text)


def _nfc_lower(text: str) -> str:
    """NFC, lowercase, then NFC again: lowercasing can leave a pair NFC composes."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", text).lower())


def _ascii_label(label: str) -> str:
    """One label, IDNA-encoded (IDNA 2003) and lowercased; it must be letters, digits and
    hyphens of at most 63 characters. A deviation character is refused before encoding."""
    if not DEVIATION_CHARACTERS.isdisjoint(label):
        raise IdentityError("a label holds an IDNA 2008 deviation character")
    try:
        encoded = idna.ToASCII(label).decode("ascii").lower()
    except UnicodeError:
        raise IdentityError("a label can't be IDNA-encoded") from None
    if not LABEL_PATTERN.fullmatch(encoded):
        raise IdentityError("a label is not letters, digits and hyphens of at most 63")
    return encoded


def _ascii_labels(name: str) -> tuple[str, ...]:
    """``name``'s labels, each IDNA-encoded; an empty label is refused."""
    labels = LABEL_SEPARATORS.split(name)
    if any(not label for label in labels):
        raise IdentityError("has an empty label")
    return tuple(_ascii_label(label) for label in labels)


def _is_ip_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _web_url(text: str) -> tuple[urllib.parse.SplitResult, str]:
    """Steps 1 to 3 of ``registrable_domain``: the split URL and its hostname."""
    stripped = text.strip()
    if not stripped:
        raise IdentityError("is empty")
    if _has_space_or_control(stripped):
        raise IdentityError("holds whitespace or a control character")
    scheme = SCHEME_PATTERN.match(stripped)
    if scheme is None:
        stripped = "http://" + stripped
    elif scheme.group(1).lower() not in WEB_SCHEMES:
        raise IdentityError("is not an http or https URL")
    try:
        parts = urllib.parse.urlsplit(stripped)
        hostname = parts.hostname
    except ValueError:
        raise IdentityError("is not a valid URL") from None
    if not hostname:
        raise IdentityError("has no host name")
    if _is_ip_address(hostname):
        raise IdentityError("is an IP address, not a domain")
    return parts, hostname


# The Public Suffix List -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _SuffixList:
    """The parsed list: its version, its rules (wildcards as ``*`` labels) and its exception
    rules (without the ``!``), each as a tuple of IDNA-encoded labels, left to right."""

    version: str
    rules: frozenset[tuple[str, ...]]
    exceptions: frozenset[tuple[str, ...]]
    rule_count: int


def _rule_labels(rule: str) -> tuple[str, ...]:
    labels = []
    for label in rule.split("."):
        if label == PSL_WILDCARD:
            labels.append(label)
        else:
            labels.append(idna.ToASCII(label).decode("ascii").lower())
    return tuple(labels)


@functools.cache
def _suffix_list() -> _SuffixList:
    """The vendored list, parsed once."""
    text = resources.files(__package__).joinpath(*PSL_PATH).read_text(encoding="utf-8")
    return _parse_suffix_list(text)


def _parse_suffix_list(text: str) -> _SuffixList:
    """Parse a Public Suffix List. A malformed list (a rule outside the sections, no
    ``VERSION`` line, or sections other than ICANN then private) raises ``ValueError``."""
    version: str | None = None
    section: str | None = None
    seen: list[str] = []
    rules: set[tuple[str, ...]] = set()
    exceptions: set[tuple[str, ...]] = set()
    count = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(PSL_VERSION_PREFIX) and version is None:
            version = stripped.removeprefix(PSL_VERSION_PREFIX).strip()
        if stripped.startswith("// ===BEGIN ") and stripped.endswith("==="):
            section = stripped.removeprefix("// ===BEGIN ").removesuffix("===")
            seen.append(section)
            continue
        if stripped.startswith("// ===END ") and stripped.endswith("==="):
            section = None
            continue
        if not stripped or stripped.startswith("//"):
            continue
        if section is None:
            raise ValueError("the Public Suffix List has a rule outside its sections")
        rule = stripped.split()[0]
        count += 1
        if rule.startswith(PSL_EXCEPTION):
            exceptions.add(_rule_labels(rule.removeprefix(PSL_EXCEPTION)))
        else:
            rules.add(_rule_labels(rule))
    if not version:
        raise ValueError("the Public Suffix List has no VERSION line")
    if tuple(seen) != PSL_SECTIONS:
        raise ValueError("the Public Suffix List lacks its ICANN or private section")
    return _SuffixList(version, frozenset(rules), frozenset(exceptions), count)


def psl_version() -> str:
    """The vendored Public Suffix List's ``VERSION``, such as ``2026-11-16_22-30-55_UTC``."""
    return _suffix_list().version


def _public_suffix_length(labels: tuple[str, ...]) -> int | None:
    """How many of ``labels`` (from the right) the prevailing rule makes the public suffix;
    ``None`` when no rule matches."""
    suffixes = _suffix_list()
    exception = 0
    longest = 0
    for size in range(1, len(labels) + 1):
        tail = labels[-size:]
        if tail in suffixes.exceptions:
            exception = size
        if tail in suffixes.rules or (PSL_WILDCARD, *tail[1:]) in suffixes.rules:
            longest = size
    if exception:
        return exception - 1
    return longest or None


# The normalisers ------------------------------------------------------------------------------


def url_host(text: str) -> str:
    """The ASCII, lowercased host name of a website or URL, ``www`` kept: steps 1 to 4 of
    ``registrable_domain``; see the module docstring."""
    _, hostname = _web_url(_checked(text, "url_host"))
    if LABEL_SEPARATORS.fullmatch(hostname[-1]):
        hostname = hostname[:-1]
    if not hostname:
        raise IdentityError("has no host name")
    if _is_ip_address(hostname):
        raise IdentityError("is an IP address, not a domain")
    host = ".".join(_ascii_labels(hostname))
    if len(host) > MAX_DOMAIN_LENGTH:
        raise IdentityError(f"is longer than {MAX_DOMAIN_LENGTH} characters")
    return host


def registrable_domain(text: str) -> str:
    """The registrable domain (eTLD+1) of a website or URL; see the module docstring."""
    labels = tuple(url_host(_checked(text, "registrable_domain")).split("."))
    if labels[0] == WWW:
        labels = labels[1:]
    if len(labels) < 2:
        raise IdentityError("is a single label, not a registrable domain")
    suffix = _public_suffix_length(labels)
    if suffix is None or suffix < 1:
        raise IdentityError("has a top-level label the Public Suffix List doesn't know")
    if suffix >= len(labels):
        raise IdentityError("is a public suffix, not a registrable domain")
    registrable = labels[-(suffix + 1) :]
    if registrable[0] == WWW:
        raise IdentityError("is www under a public suffix, not a registrable domain")
    return ".".join(registrable)


def normalize_email(text: str) -> str:
    """An email address, stripped, NFC'd and lowercased with an IDNA domain; see the module
    docstring."""
    value = _nfc_lower(_checked(text, "normalize_email").strip())
    if _has_space_control_or_format(value):
        raise IdentityError("holds whitespace, a control or a format character")
    if value.count("@") != 1:
        raise IdentityError("must hold exactly one @")
    local, domain = value.split("@")
    if not 1 <= len(local) <= MAX_LOCAL_PART_LENGTH:
        raise IdentityError(f"has a local part that isn't 1 to {MAX_LOCAL_PART_LENGTH} characters")
    labels = _ascii_labels(domain)
    if len(labels) < 2:
        raise IdentityError("has a domain of fewer than two labels")
    address = f"{local}@{'.'.join(labels)}"
    if len(address) > MAX_EMAIL_LENGTH:
        raise IdentityError(f"is longer than {MAX_EMAIL_LENGTH} characters")
    return address


def _is_word_character(ch: str) -> bool:
    """A letter or digit, or a combining mark (category ``M*``), which belongs to its letter."""
    return ch.isalnum() or unicodedata.category(ch).startswith("M")


def normalize_name(text: str) -> str:
    """A name folded for matching: NFKC, casefold, punctuation runs to one space, NFKC."""
    value = _checked(text, "normalize_name")
    if len(value) > MAX_NAME_INPUT_LENGTH:
        raise IdentityError(f"is longer than {MAX_NAME_INPUT_LENGTH} characters")
    folded = unicodedata.normalize("NFKC", value).casefold()
    words = "".join(ch if _is_word_character(ch) else " " for ch in folded).split()
    result = unicodedata.normalize("NFKC", " ".join(words))
    if not result:
        raise IdentityError("has no letters or digits")
    return result


def normalize_linkedin_url(text: str) -> str:
    """A LinkedIn profile URL as ``https://www.linkedin.com/in/<slug>``; see the module
    docstring."""
    parts, hostname = _web_url(_checked(text, "normalize_linkedin_url"))
    labels = hostname.split(".")
    if not (hostname == LINKEDIN_HOST or hostname.endswith("." + LINKEDIN_HOST)) or not all(
        LABEL_PATTERN.fullmatch(label) for label in labels
    ):
        raise IdentityError("is not on linkedin.com")
    segments = parts.path.split("/")
    if len(segments) < 3 or segments[1].lower() != LINKEDIN_PROFILE_SEGMENT or not segments[2]:
        raise IdentityError("is not a LinkedIn /in/ profile URL")
    try:
        decoded = urllib.parse.unquote(segments[2], encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        raise IdentityError("has a profile slug that isn't UTF-8") from None
    slug = _nfc_lower(decoded)
    if not 1 <= len(slug) <= MAX_SLUG_LENGTH:
        raise IdentityError(f"has a profile slug that isn't 1 to {MAX_SLUG_LENGTH} characters")
    if _has_space_control_or_format(slug):
        raise IdentityError("has a profile slug with whitespace, a control or a format character")
    return LINKEDIN_PROFILE_PREFIX + urllib.parse.quote(slug, safe=SLUG_SAFE)


def normalize_company_number(text: str) -> str:
    """A Companies House number: 8 characters, digits zero-padded."""
    value = _checked(text, "normalize_company_number").strip()
    if not value.isascii():
        raise IdentityError("is not a Companies House number")
    value = value.upper()
    if DIGITS_PATTERN.fullmatch(value):
        return value.zfill(COMPANY_NUMBER_LENGTH)
    if COMPANY_NUMBER_PATTERN.fullmatch(value) and any(ch.isdigit() for ch in value):
        return value
    raise IdentityError("is not a Companies House number")


def normalize_place_id(text: str) -> str:
    """A Google place ID, stripped; case is kept."""
    value = _checked(text, "normalize_place_id").strip()
    if not PLACE_ID_PATTERN.fullmatch(value):
        raise IdentityError("is not a place ID")
    return value


def normalize_country_code(text: str) -> str:
    """An ISO 3166-1 alpha-2 country code, uppercased."""
    value = _checked(text, "normalize_country_code").strip()
    if not value.isascii():
        raise IdentityError("is not an ISO 3166-1 alpha-2 country code")
    code = value.upper()
    code = COUNTRY_ALIASES.get(code, code)
    if code not in COUNTRY_CODES:
        raise IdentityError("is not an ISO 3166-1 alpha-2 country code")
    return code
