"""The closed send gate (FR-37, FR-18, FR-23, FR-33, FR-34, FR-50, FR-64, AD-11, AD-16, NFR-7;
Story 6.9). Standard library and ``nextron.domain`` only, no clock and no I/O: ``evaluate``
judges the facts ``nextron.app.compliance.send_gate.assert_sendable`` gathered, at the ``now`` it
is given.

**The closed list.** ``PREDICATES`` is exactly these 13 ``GatePredicate(name, cold_only,
label)``, in this order; no setting or control disables or weakens one, and nothing adds a
fourteenth (brand-domain and recovery-mode defence sit inside ``inbox_ready``):

- for every kind: ``contactable``, ``approval_live``, ``hash_matches``, ``inbox_ready``,
  ``footer_complete``, ``market_eligible``;
- for the cold kinds only: ``enrollment_active``, ``thread_quiet``, ``campaign_active``,
  ``in_send_window``, ``under_daily_cap``, ``warmup_ok``, ``under_total_ceiling``.

``LABELS`` maps each ``Predicate`` to its plain-English label (the "Approved, not yet sent" list,
Epic 5, shows the labels of the predicates an OM currently fails).

**Kinds.** ``OmKind``: the cold kinds ``step`` and ``recontact`` (``COLD_KINDS``) and the
responsive kinds ``reply`` and ``rebook`` (``RESPONSIVE_KINDS``). A responsive kind skips every
cold predicate. ``om_kind(value)`` reads one; any other text (``channel`` included) raises
``ValueError``, a non-``str`` ``TypeError``.

**``SendCandidate(om_id, kind, draft_id, approval_id, sending_inbox_id, contact_id,
enrollment_id, thread_id)``** is what the gate needs of one OutboundMessage (Story 6.10 builds
it from the OM row). Building one with a bad kind, or an ``om_id`` that isn't a ULID (it is the
alert's subject), raises ``ValueError``; a non-``str`` id ``TypeError`` (``enrollment_id`` and
``thread_id`` may be ``None``).

**``GateFacts``** holds the raw facts, every one ``None`` by default, and ``None`` fails the
predicate it feeds (fail closed), with one exception: ``contact_tz`` ``None`` means the Contact's
timezone is unknown, so the Market's unknown-location window applies
(``sending_policy.in_send_window``).

**``evaluate(kind, facts, now) -> GateResult(failing)``** lists every failing predicate, in
``PREDICATES`` order; ``GateResult.passed`` is true when none fails. A predicate passes only
when:

- ``contactable``: ``contactable`` is ``True`` (``assert_contactable`` answered ``Allowed``);
- ``approval_live``: ``approval_current`` is ``True`` (the Draft is ``approved`` and the OM's
  approval is its current one) and ``approved_at`` is less than 72 hours before ``now``
  (``sending_policy.approval_expired``) and not after it (an approval dated in the future fails
  closed);
- ``hash_matches``: ``approved_hash`` equals both ``revision_hash`` (the current revision's
  stored hash) and ``recomputed_hash`` (a fresh hash of what would be sent now);
- ``inbox_ready``: ``inbox_status`` is ``active``, ``domain_auth_ok`` is ``True``,
  ``recovery_mode`` is ``False`` and ``inbox_address`` isn't on ``nextronai.com`` or a
  subdomain of it (``brand_address``);
- ``footer_complete``: ``footer_missing`` is empty and ``footer_matches`` is ``True``;
- ``market_eligible``: ``market`` is an enabled ``MarketDef``, and, for a cold kind,
  ``eligible`` is ``True`` (live Eligibility);
- ``enrollment_active``: ``enrollment_status`` is ``active``;
- ``thread_quiet``: ``thread_quiet`` is ``True``;
- ``campaign_active``: ``campaign_active`` is ``True``;
- ``in_send_window``: ``market`` is a ``MarketDef`` and ``in_send_window(contact_tz, market,
  now)``;
- ``under_daily_cap``: ``cold_sent_today < cold_cap`` (both known);
- ``warmup_ok``: ``warmup_days >= MIN_WARMUP_DAYS`` (14);
- ``under_total_ceiling``: ``sent_total_today < ceiling`` (both known; the total includes the
  warm-up tool figure).

A naive ``now`` raises ``ValueError``.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from nextron.domain.ids import is_ulid
from nextron.domain.markets import MarketDef
from nextron.domain.sending_inbox import BRAND_DOMAIN
from nextron.domain.sending_policy import MIN_WARMUP_DAYS, approval_expired, in_send_window

ACTIVE: Final = "active"  # the inbox and the Enrollment status a send needs


class Predicate(StrEnum):
    CONTACTABLE = "contactable"
    APPROVAL_LIVE = "approval_live"
    HASH_MATCHES = "hash_matches"
    INBOX_READY = "inbox_ready"
    FOOTER_COMPLETE = "footer_complete"
    MARKET_ELIGIBLE = "market_eligible"
    ENROLLMENT_ACTIVE = "enrollment_active"
    THREAD_QUIET = "thread_quiet"
    CAMPAIGN_ACTIVE = "campaign_active"
    IN_SEND_WINDOW = "in_send_window"
    UNDER_DAILY_CAP = "under_daily_cap"
    WARMUP_OK = "warmup_ok"
    UNDER_TOTAL_CEILING = "under_total_ceiling"


@dataclass(frozen=True, slots=True)
class GatePredicate:
    """One predicate of the closed list: its name, whether only cold kinds check it, its
    label."""

    name: Predicate
    cold_only: bool
    label: str


PREDICATES: Final = (
    GatePredicate(Predicate.CONTACTABLE, False, "Contact not contactable"),
    GatePredicate(Predicate.APPROVAL_LIVE, False, "Approval expired or not current"),
    GatePredicate(Predicate.HASH_MATCHES, False, "Text changed since approval"),
    GatePredicate(Predicate.INBOX_READY, False, "Sending Inbox not ready"),
    GatePredicate(Predicate.FOOTER_COMPLETE, False, "Footer incomplete or out of date"),
    GatePredicate(Predicate.MARKET_ELIGIBLE, False, "Not eligible in the Contact's Market"),
    GatePredicate(Predicate.ENROLLMENT_ACTIVE, True, "Sequence not active"),
    GatePredicate(Predicate.THREAD_QUIET, True, "Thread has new activity"),
    GatePredicate(Predicate.CAMPAIGN_ACTIVE, True, "Campaign not active"),
    GatePredicate(Predicate.IN_SEND_WINDOW, True, "Outside Send Window"),
    GatePredicate(Predicate.UNDER_DAILY_CAP, True, "Daily Cap reached"),
    GatePredicate(Predicate.WARMUP_OK, True, "Warm-up under 14 days"),
    GatePredicate(Predicate.UNDER_TOTAL_CEILING, True, "Total daily ceiling reached"),
)
LABELS: Final[Mapping[Predicate, str]] = MappingProxyType(
    {predicate.name: predicate.label for predicate in PREDICATES}
)


class OmKind(StrEnum):
    STEP = "step"
    RECONTACT = "recontact"
    REPLY = "reply"
    REBOOK = "rebook"


COLD_KINDS: Final = frozenset({OmKind.STEP, OmKind.RECONTACT})
RESPONSIVE_KINDS: Final = frozenset({OmKind.REPLY, OmKind.REBOOK})


def om_kind(value: object) -> OmKind:
    """``value`` as an ``OmKind``; ``TypeError`` for a non-``str``, ``ValueError`` for any other
    text."""
    if not isinstance(value, str):
        raise TypeError(f"an OM kind is a str, not {type(value).__name__}")
    try:
        return OmKind(value)
    except ValueError:
        raise ValueError("the send gate takes the kinds step, recontact, reply and rebook") from None


def _id(value: object, name: str, *, optional: bool = False) -> None:
    if value is None and optional:
        return
    if not isinstance(value, str):
        raise TypeError(f"SendCandidate.{name} must be a str")


@dataclass(frozen=True, slots=True)
class SendCandidate:
    """What the gate needs of one OutboundMessage; see the module docstring."""

    om_id: str
    kind: OmKind
    draft_id: str
    approval_id: str
    sending_inbox_id: str
    contact_id: str
    enrollment_id: str | None = None
    thread_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", om_kind(self.kind))
        for name in ("om_id", "draft_id", "approval_id", "sending_inbox_id", "contact_id"):
            _id(getattr(self, name), name)
        _id(self.enrollment_id, "enrollment_id", optional=True)
        _id(self.thread_id, "thread_id", optional=True)
        if not is_ulid(self.om_id):
            raise ValueError("SendCandidate.om_id must be a ULID")

    @property
    def cold(self) -> bool:
        return self.kind in COLD_KINDS


@dataclass(frozen=True, slots=True)
class GateFacts:
    """The raw facts the gate judges; ``None`` fails the predicate it feeds (see the module
    docstring for ``contact_tz``)."""

    contactable: bool | None = None
    approval_current: bool | None = None
    approved_at: datetime | None = None
    approved_hash: str | None = None
    revision_hash: str | None = None
    recomputed_hash: str | None = None
    inbox_status: str | None = None
    inbox_address: str | None = None
    domain_auth_ok: bool | None = None
    recovery_mode: bool | None = None
    footer_missing: tuple[str, ...] | None = None
    footer_matches: bool | None = None
    market: MarketDef | None = None
    eligible: bool | None = None
    enrollment_status: str | None = None
    thread_quiet: bool | None = None
    campaign_active: bool | None = None
    contact_tz: str | None = None
    cold_sent_today: int | None = None
    cold_cap: int | None = None
    warmup_days: int | None = None
    sent_total_today: int | None = None
    ceiling: int | None = None


@dataclass(frozen=True, slots=True)
class GateResult:
    """Every failing predicate, in ``PREDICATES`` order."""

    failing: tuple[Predicate, ...]

    @property
    def passed(self) -> bool:
        return not self.failing

    @property
    def labels(self) -> tuple[str, ...]:
        """The failing predicates' labels, in order."""
        return tuple(LABELS[predicate] for predicate in self.failing)


def brand_address(address: object) -> bool:
    """Whether ``address`` sends from ``nextronai.com`` or a subdomain of it; anything that
    isn't a ``str`` with an ``@`` counts as brand (fail closed)."""
    if not isinstance(address, str) or "@" not in address:
        return True
    host = address.rpartition("@")[2].strip().lower().removesuffix(".")
    return host == BRAND_DOMAIN or host.endswith("." + BRAND_DOMAIN)


def _true(value: object) -> bool:
    return value is True


def _count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _below(count: object, limit: object) -> bool:
    found, bound = _count(count), _count(limit)
    return found is not None and bound is not None and found < bound


def _approval_live(facts: GateFacts, now: datetime) -> bool:
    approved_at = facts.approved_at
    if not _true(facts.approval_current) or not isinstance(approved_at, datetime):
        return False
    if approved_at.utcoffset() is None or approved_at > now:
        return False
    return not approval_expired(approved_at, now)


def _hash_matches(facts: GateFacts) -> bool:
    approved = facts.approved_hash
    return (
        isinstance(approved, str)
        and bool(approved)
        and approved == facts.revision_hash
        and approved == facts.recomputed_hash
    )


def _inbox_ready(facts: GateFacts) -> bool:
    return (
        facts.inbox_status == ACTIVE
        and _true(facts.domain_auth_ok)
        and facts.recovery_mode is False
        and not brand_address(facts.inbox_address)
    )


def _footer_complete(facts: GateFacts) -> bool:
    return facts.footer_missing == () and _true(facts.footer_matches)


def _market_eligible(facts: GateFacts, cold: bool) -> bool:
    market = facts.market
    if not isinstance(market, MarketDef) or market.enabled is not True:
        return False
    return _true(facts.eligible) if cold else True


def _in_window(facts: GateFacts, now: datetime) -> bool:
    market = facts.market
    if not isinstance(market, MarketDef):
        return False
    tz = facts.contact_tz
    if tz is not None and not isinstance(tz, str):
        return False
    return in_send_window(tz, market, now)


def _warmup_ok(facts: GateFacts) -> bool:
    days = _count(facts.warmup_days)
    return days is not None and days >= MIN_WARMUP_DAYS


def evaluate(kind: OmKind | str, facts: GateFacts, now: datetime) -> GateResult:
    """Every predicate ``facts`` fail for an OM of ``kind`` at ``now``; see the module
    docstring."""
    chosen = om_kind(kind)
    if not isinstance(facts, GateFacts):
        raise TypeError(f"evaluate takes GateFacts, not {type(facts).__name__}")
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError("now must be an aware datetime")
    cold = chosen in COLD_KINDS
    passes = {
        Predicate.CONTACTABLE: _true(facts.contactable),
        Predicate.APPROVAL_LIVE: _approval_live(facts, now),
        Predicate.HASH_MATCHES: _hash_matches(facts),
        Predicate.INBOX_READY: _inbox_ready(facts),
        Predicate.FOOTER_COMPLETE: _footer_complete(facts),
        Predicate.MARKET_ELIGIBLE: _market_eligible(facts, cold),
        Predicate.ENROLLMENT_ACTIVE: facts.enrollment_status == ACTIVE,
        Predicate.THREAD_QUIET: _true(facts.thread_quiet),
        Predicate.CAMPAIGN_ACTIVE: _true(facts.campaign_active),
        Predicate.IN_SEND_WINDOW: _in_window(facts, now),
        Predicate.UNDER_DAILY_CAP: _below(facts.cold_sent_today, facts.cold_cap),
        Predicate.WARMUP_OK: _warmup_ok(facts),
        Predicate.UNDER_TOTAL_CEILING: _below(facts.sent_total_today, facts.ceiling),
    }
    return GateResult(
        tuple(
            predicate.name
            for predicate in PREDICATES
            if (cold or not predicate.cold_only) and not passes[predicate.name]
        )
    )
