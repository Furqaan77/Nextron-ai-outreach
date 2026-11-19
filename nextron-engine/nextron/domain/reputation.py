"""Sending Domain reputation guardrails (FR-38, FR-58, AD-9): DNSBL answers and Google
Postmaster spam rates, judged; what pauses an inbox, what only alerts or warns. Standard library
only; a function that needs the time takes it.

**DNSBL zones.** ``KNOWN_ZONES`` are the domain blocklists the engine knows how to read
(``dbl.spamhaus.org``, ``multi.uribl.com`` and ``multi.surbl.org``); the ``dnsbl_zones``
setting (default ``DEFAULT_ZONES``: ``dbl.spamhaus.org``) must hold at least one of them and
nothing else (``is_zone_list``), so no setting can switch the guard off. ``dnsbl_name(domain,
zone)`` is the name queried: ``<domain>.<zone>``.

**One zone's outcome** (``zone_outcome(zone, answers)``, from the A answers as dotted IPv4):

- no answer: ``clean``;
- any answer in ``127.255.255.0/24`` (the list refused the query, as Spamhaus does through a
  public resolver): ``query_refused``;
- otherwise any answer in the zone's own listing codes (``LISTING_CODES``): ``listed``;
- anything else (``127.0.0.1``, a resolver's hijacked NXDOMAIN, an unparsable answer):
  ``unrecognised``.

The listing codes: ``dbl.spamhaus.org`` ``127.0.1.2`` to ``127.0.1.106``; ``multi.uribl.com``
the even ``127.0.0.2`` to ``127.0.0.14``; ``multi.surbl.org`` ``127.0.0.2`` to
``127.0.0.254``. ``overall(outcomes)`` is the worst: ``listed`` > ``query_refused`` >
``unrecognised`` > ``clean`` (``clean`` for none). Only ``listed`` pauses an inbox; a refused
or unrecognised answer is a health warning (AD-9: no false-positive pauses).

**Storage.** ``encode_dnsbl(outcomes)`` is the compact JSON, keys sorted, of ``{zone:
outcome}`` (at least one known zone); ``decode_dnsbl(text)`` reads it back, sorted by zone.
Anything else raises ``ValueError``.

**Spam rate.** Postmaster's ``userReportedSpamRatio`` is stored in parts per million:
``ratio_ppm(ratio)`` is ``round(Decimal(str(ratio)) * 1_000_000)`` (a ratio from 0 to 1; anything
else raises ``ValueError``). ``spam_status(ppm)``: ``None`` is ``no_data`` (never 0%), under
``ALERT_PPM`` (1,000: 0.1%) ``ok``, under ``PAUSE_PPM`` (3,000: 0.3%) ``alert``, and from it
``pause``. ``postmaster_day(checked_at)`` is the UTC date before the check's: the day whose
traffic stats are read. ``spam_rate_text(ppm)`` is the panel's text: ``"0.12%"``, or ``"No
data"``.

**Alerts** (``notify``, deduplicated): kinds ``DOMAIN_BLOCKLISTED``, ``SPAM_RATE_PAUSE`` and
``SPAM_RATE_HIGH``; ``alert_subject(domain_id, day)`` is ``<domain id lower-case>:<YYYY-MM-DD>``,
so a persisting condition alerts once per UTC day. The texts carry IDs and counts only.

**Health issue kinds.** ``DOMAIN_BLOCKLISTED``, ``DNSBL_QUERY_REFUSED``, ``SPAM_RATE_HIGH``,
``SPAM_RATE_PAUSE`` and ``POSTMASTER_GRANT_MISSING`` (subject ``POSTMASTER_SUBJECT``).
"""

import ipaddress
import json
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from types import MappingProxyType
from typing import Final

SPAMHAUS_DBL: Final = "dbl.spamhaus.org"
URIBL_MULTI: Final = "multi.uribl.com"
SURBL_MULTI: Final = "multi.surbl.org"
KNOWN_ZONES: Final = (SPAMHAUS_DBL, URIBL_MULTI, SURBL_MULTI)
DEFAULT_ZONES: Final = (SPAMHAUS_DBL,)

REFUSED_NETWORK: Final = ipaddress.IPv4Network("127.255.255.0/24")


def _addresses(first: str, last: str, step: int = 1) -> frozenset[ipaddress.IPv4Address]:
    start = int(ipaddress.IPv4Address(first))
    stop = int(ipaddress.IPv4Address(last))
    return frozenset(ipaddress.IPv4Address(value) for value in range(start, stop + 1, step))


LISTING_CODES: Final[Mapping[str, frozenset[ipaddress.IPv4Address]]] = MappingProxyType(
    {
        SPAMHAUS_DBL: _addresses("127.0.1.2", "127.0.1.106"),
        URIBL_MULTI: _addresses("127.0.0.2", "127.0.0.14", 2),
        SURBL_MULTI: _addresses("127.0.0.2", "127.0.0.254"),
    }
)


class ZoneOutcome(StrEnum):
    CLEAN = "clean"
    QUERY_REFUSED = "query_refused"
    LISTED = "listed"
    UNRECOGNISED = "unrecognised"


SEVERITY: Final[Mapping[ZoneOutcome, int]] = MappingProxyType(
    {
        ZoneOutcome.CLEAN: 0,
        ZoneOutcome.UNRECOGNISED: 1,
        ZoneOutcome.QUERY_REFUSED: 2,
        ZoneOutcome.LISTED: 3,
    }
)

OUTCOME_TEXTS: Final[Mapping[ZoneOutcome, str]] = MappingProxyType(
    {
        ZoneOutcome.CLEAN: "Clean",
        ZoneOutcome.QUERY_REFUSED: "Query refused",
        ZoneOutcome.LISTED: "Listed",
        ZoneOutcome.UNRECOGNISED: "Unrecognised answer",
    }
)


class SpamStatus(StrEnum):
    OK = "ok"
    ALERT = "alert"
    PAUSE = "pause"
    NO_DATA = "no_data"


ALERT_PPM: Final = 1_000  # 0.1%
PAUSE_PPM: Final = 3_000  # 0.3%
PPM: Final = 1_000_000

DOMAIN_BLOCKLISTED: Final = "domain_blocklisted"
SPAM_RATE_HIGH: Final = "spam_rate_high"
SPAM_RATE_PAUSE: Final = "spam_rate_pause"
DNSBL_QUERY_REFUSED: Final = "dnsbl_query_refused"
POSTMASTER_GRANT_MISSING: Final = "postmaster_grant_missing"
POSTMASTER_SUBJECT: Final = "postmaster"
POSTMASTER_GRANT_TEXT: Final = "Postmaster grant missing"
POSTMASTER_GRANT_HINT: Final = (
    "Grant the engine Postmaster Tools access on the Credentials page; until then spam rates "
    "read as no data."
)
DELIST_HINT: Final = (
    "Ask the blocklist to delist the domain, then resume its inboxes and press Recheck on the "
    "Sending Inboxes page."
)
REFUSED_HINT: Final = (
    "The blocklist did not answer usably (a public DNS resolver, say); nothing was paused."
)
SPAM_HINT: Final = (
    "Check the domain's Postmaster Tools dashboard and the messages being sent. The rate is "
    "the previous UTC day's, so a resume or Recheck the same day pauses again; a resume holds "
    "once a newer day's rate is below 0.30%."
)


# DNSBL ----------------------------------------------------------------------------------------


def is_zone_list(value: Iterable[object]) -> bool:
    """Whether ``value`` holds at least one zone, each in ``KNOWN_ZONES``."""
    items = list(value)
    return bool(items) and all(isinstance(item, str) and item in KNOWN_ZONES for item in items)


def dnsbl_name(domain: str, zone: str) -> str:
    """The name queried for ``domain`` on ``zone``: ``<domain>.<zone>``."""
    return f"{domain}.{zone}"


def _known(zone: object) -> str:
    if not isinstance(zone, str) or zone not in KNOWN_ZONES:
        raise ValueError("not a known DNSBL zone")
    return zone


def zone_outcome(zone: str, answers: Iterable[str]) -> ZoneOutcome:
    """``zone``'s outcome from its A ``answers``; see the module docstring."""
    codes = LISTING_CODES[_known(zone)]
    parsed: list[ipaddress.IPv4Address | None] = []
    for answer in answers:
        try:
            parsed.append(ipaddress.IPv4Address(answer))
        except ValueError:
            parsed.append(None)
    if not parsed:
        return ZoneOutcome.CLEAN
    if any(address is not None and address in REFUSED_NETWORK for address in parsed):
        return ZoneOutcome.QUERY_REFUSED
    if any(address is not None and address in codes for address in parsed):
        return ZoneOutcome.LISTED
    return ZoneOutcome.UNRECOGNISED


def overall(outcomes: Iterable[ZoneOutcome]) -> ZoneOutcome:
    """The worst of ``outcomes``; ``clean`` for none."""
    return max(outcomes, key=SEVERITY.__getitem__, default=ZoneOutcome.CLEAN)


def zones_with(outcomes: Mapping[str, ZoneOutcome], outcome: ZoneOutcome) -> tuple[str, ...]:
    """The zones whose outcome is ``outcome``, sorted."""
    return tuple(sorted(zone for zone, found in outcomes.items() if found == outcome))


def encode_dnsbl(outcomes: Mapping[str, ZoneOutcome]) -> str:
    """The stored ``dnsbl_result``: compact JSON, keys sorted; ``ValueError`` unless it holds
    at least one known zone, each with an outcome."""
    if not isinstance(outcomes, Mapping) or not outcomes:
        raise ValueError("a DNSBL result holds at least one zone")
    for zone, outcome in outcomes.items():
        _known(zone)
        if not isinstance(outcome, ZoneOutcome):
            raise ValueError("every zone needs an outcome")
    return json.dumps(
        {zone: str(outcome) for zone, outcome in outcomes.items()},
        sort_keys=True,
        separators=(",", ":"),
    )


def decode_dnsbl(text: str) -> dict[str, ZoneOutcome]:
    """The outcomes stored as ``dnsbl_result``, sorted by zone; ``ValueError`` for anything
    ``encode_dnsbl`` doesn't write."""
    if not isinstance(text, str):
        raise ValueError("a DNSBL result must be a str")
    try:
        found = json.loads(text)
    except ValueError:
        raise ValueError("the DNSBL result is not JSON") from None
    if not isinstance(found, dict) or not found:
        raise ValueError("a DNSBL result holds at least one zone")
    outcomes: dict[str, ZoneOutcome] = {}
    for zone in sorted(found):
        value = found[zone]
        if not isinstance(value, str) or value not in set(ZoneOutcome):
            raise ValueError("every zone needs an outcome")
        outcomes[_known(zone)] = ZoneOutcome(value)
    if encode_dnsbl(outcomes) != text:
        raise ValueError("the DNSBL result is not in its stored form")
    return outcomes


def dnsbl_text(outcomes: Mapping[str, ZoneOutcome]) -> str:
    """The panel's text: the worst outcome, naming the listing zones (``Listed
    (dbl.spamhaus.org)``)."""
    worst = overall(outcomes.values())
    if worst is ZoneOutcome.LISTED:
        return f"Listed ({', '.join(zones_with(outcomes, ZoneOutcome.LISTED))})"
    return OUTCOME_TEXTS[worst]


# Spam rate ------------------------------------------------------------------------------------


def ratio_ppm(ratio: object) -> int:
    """A Postmaster ``userReportedSpamRatio`` (0 to 1) in parts per million; ``ValueError``
    for anything else."""
    if isinstance(ratio, bool) or not isinstance(ratio, int | float | str | Decimal):
        raise ValueError("a spam ratio must be a number")
    try:
        exact = Decimal(str(ratio))
    except InvalidOperation:
        raise ValueError("a spam ratio must be a number") from None
    if not exact.is_finite() or exact < 0 or exact > 1:
        raise ValueError("a spam ratio must be from 0 to 1")
    return round(exact * PPM)


def spam_status(ppm: int | None) -> SpamStatus:
    """The spam rate's status; ``None`` (no data) is never 0%."""
    if ppm is None:
        return SpamStatus.NO_DATA
    if isinstance(ppm, bool) or not isinstance(ppm, int) or ppm < 0:
        raise ValueError("a spam rate is a whole number of parts per million, from 0")
    if ppm < ALERT_PPM:
        return SpamStatus.OK
    if ppm < PAUSE_PPM:
        return SpamStatus.ALERT
    return SpamStatus.PAUSE


def spam_rate_text(ppm: int | None) -> str:
    """The panel's text: the rate as a percentage with two decimals, or ``No data``."""
    if ppm is None:
        return "No data"
    percent = (Decimal(ppm) / Decimal(10_000)).quantize(Decimal("0.01"))
    return f"{percent}%"


def postmaster_day(checked_at: datetime) -> date:
    """The UTC date before ``checked_at``'s: the day whose traffic stats a check reads."""
    if not isinstance(checked_at, datetime) or checked_at.utcoffset() is None:
        raise ValueError("checked_at must be an aware datetime")
    return checked_at.astimezone(UTC).date() - timedelta(days=1)


# Alerts ---------------------------------------------------------------------------------------


def alert_subject(sending_domain_id: str, day: date) -> str:
    """The dedup subject: ``<domain id lower-case>:<YYYY-MM-DD>``, one alert per kind, domain
    and UTC day."""
    return f"{sending_domain_id.lower()}:{day.isoformat()}"


def _inboxes(count: int) -> str:
    return f"{count} inbox{'' if count == 1 else 'es'}"


def blocklisted_text(sending_domain_id: str, paused: int) -> str:
    """The ``domain_blocklisted`` alert: the domain's id and how many inboxes were paused."""
    return f"Sending Domain {sending_domain_id} is blocklisted; {_inboxes(paused)} paused"


def spam_pause_text(sending_domain_id: str, ppm: int, paused: int) -> str:
    """The ``spam_rate_pause`` alert: the domain's id, its rate and how many inboxes were
    paused."""
    return (
        f"Sending Domain {sending_domain_id} spam rate {spam_rate_text(ppm)}; "
        f"{_inboxes(paused)} paused"
    )


def spam_high_text(sending_domain_id: str, ppm: int) -> str:
    """The ``spam_rate_high`` alert: the domain's id and its rate."""
    return f"Sending Domain {sending_domain_id} spam rate {spam_rate_text(ppm)}, at or above 0.10%"
