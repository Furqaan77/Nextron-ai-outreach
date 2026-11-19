"""The Approval Queue page (``GET /queue``; FR-26, AD-13, AD-16; Story 5.1).

``queue_context(queue, card_param, now, started, zone_name)`` turns
``nextron.app.queries.approval.DueQueue`` into what ``templates/queue.html`` shows:

- ``groups``: each group's label and count (``GroupRow``), in queue order;
- ``card``: the focused card (``CardRow``), card ``k`` of the flattened group order, where ``k``
  is ``card_index(card_param, N)``: the ``card`` query parameter, 1-based, default 1, clamped to
  ``1..N``, and 1 for a text that isn't a whole number; ``None`` when N is 0;
- ``position``: ``"k of N"``; ``previous_href`` and ``next_href``: plain GET links to the
  neighbouring cards (``None`` at either end);
- ``held_back``: each held-back Draft's name (the Lead's, else the Contact's, else
  ``"Unknown Lead"``) and its reasons' texts (``HeldRow``);
- ``elapsed``: the session's time since it started, ``"<H>h <MM>m"`` (``elapsed_text``), or
  ``None`` when the start is unknown;
- the empty state, when N is 0: ``"Queue clear"`` plus ``next_due``, the earliest due time of a
  ``ready`` Draft in the Sender's timezone (``"YYYY-MM-DD HH:MM <zone>"``; UTC when the setting
  is unset or unreadable), or, when ``next_due`` is ``None``, ``"Nothing scheduled"``.

Every value is plain text; the template autoescapes it inside an ``hx-disable`` container. The
page has no form and no action: deciding a card comes in later stories.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nextron.app.queries.approval import DueQueue, QueueCard
from nextron.domain.draft import DraftKind
from nextron.domain.render import Composed

TEMPLATE: Final = "queue.html"
QUEUE_PATH: Final = "/queue"
CARD_PARAM: Final = "card"
NO_VALUE: Final = "—"
UNKNOWN_NAME: Final = "Unknown Lead"
NOTHING_SCHEDULED: Final = "Nothing scheduled"
TIME_FORMAT: Final = "%Y-%m-%d %H:%M"
KIND_LABELS: Final = {
    DraftKind.STEP: "Sequence Step",
    DraftKind.RECONTACT: "Re-contact",
    DraftKind.REPLY: "Reply",
    DraftKind.REBOOK: "Rebook",
    DraftKind.CHANNEL: "Channel message",
}


@dataclass(frozen=True, slots=True)
class GroupRow:
    """One group header: its label and how many cards it holds."""

    key: str
    label: str
    count: int


@dataclass(frozen=True, slots=True)
class CardRow:
    """The focused card as the page shows it."""

    draft_id: str
    kind: str
    step: str | None
    lead_name: str
    segment: str
    market: str
    tier: str
    score: str
    contact_name: str
    contact_role: str
    address_class: str
    message: Composed | None
    blocked: str | None


@dataclass(frozen=True, slots=True)
class HeldRow:
    """One held-back Draft: a name and the reasons."""

    name: str
    reasons: tuple[str, ...]


def card_index(raw: str | None, total: int) -> int:
    """The 1-based card to show of ``total``; see the module docstring."""
    try:
        wanted = 1 if raw is None else int(raw)
    except ValueError:
        wanted = 1
    return max(1, min(wanted, max(total, 1)))


def elapsed_text(started: datetime, now: datetime) -> str:
    """The time from ``started`` to ``now`` as ``<H>h <MM>m`` (never negative)."""
    minutes = max(0, int((now - started).total_seconds()) // 60)
    hours, rest = divmod(minutes, 60)
    return f"{hours}h {rest:02d}m"


def zone_of(name: str | None) -> tuple[tzinfo, str]:
    """The timezone ``name`` and its label; UTC for ``None`` or a name that isn't known."""
    if name:
        try:
            return ZoneInfo(name), name
        except (ZoneInfoNotFoundError, ValueError, OSError):  # e.g. a tzdata directory
            pass
    return UTC, "UTC"


def local_text(at: datetime, zone_name: str | None) -> str:
    """``at`` in the timezone ``zone_name`` as ``YYYY-MM-DD HH:MM <zone>``."""
    zone, label = zone_of(zone_name)
    return f"{at.astimezone(zone).strftime(TIME_FORMAT)} {label}"


def _text(value: object) -> str:
    return NO_VALUE if value is None or value == "" else str(value)


def card_row(card: QueueCard) -> CardRow:
    """The ``CardRow`` of ``card``."""
    return CardRow(
        draft_id=card.draft_id,
        kind=KIND_LABELS.get(card.kind, str(card.kind)),
        step=None if card.step_no is None else f"Step {card.step_no}",
        lead_name=_text(card.lead_name),
        segment=_text(card.segment_name),
        market=_text(None if card.market_code is None else card.market_code.upper()),
        tier=_text(None if card.tier is None else str(card.tier)),
        score=_text(card.lead_score),
        contact_name=_text(card.contact_name),
        contact_role=_text(card.contact_role),
        address_class=_text(card.address_class),
        message=card.message,
        blocked=card.blocked,
    )


def _href(index: int) -> str:
    return f"{QUEUE_PATH}?{CARD_PARAM}={index}"


def queue_context(
    queue: DueQueue,
    card_param: str | None,
    now: datetime,
    started: datetime | None,
    zone_name: str | None,
) -> dict[str, object]:
    """What ``queue.html`` renders; see the module docstring."""
    cards = queue.cards
    total = len(cards)
    index = card_index(card_param, total)
    focused = card_row(cards[index - 1]) if total else None
    return {
        "groups": tuple(GroupRow(group.key, group.label, len(group.cards)) for group in queue.groups),
        "card": focused,
        "total": total,
        "position": f"{index} of {total}" if total else None,
        "previous_href": _href(index - 1) if total and index > 1 else None,
        "next_href": _href(index + 1) if total and index < total else None,
        "held_back": tuple(
            HeldRow(
                name=held.card.lead_name or held.card.contact_name or UNKNOWN_NAME,
                reasons=tuple(reason.text for reason in held.reasons),
            )
            for held in queue.held_back
        ),
        "elapsed": None if started is None else elapsed_text(started, now),
        "next_due": (
            None if queue.next_due_at is None else local_text(queue.next_due_at, zone_name)
        ),
        "nothing_scheduled": NOTHING_SCHEDULED,
    }
