"""The closed list of notifications (AD-3) and the ULID shape check in ``nextron.domain.ids``."""

import dataclasses
from typing import Any

import pytest

from nextron.domain.ids import is_ulid
from nextron.domain.notifications import (
    NOTIFICATIONS,
    ContactErased,
    DraftRejected,
    EventClosed,
    FooterChanged,
    InboxRemoved,
    LeadsMerged,
    MessageSent,
    Notification,
    SubjectSuppressed,
)

A = "01JD3QZ8X5V9K2M7N4P6R8T0WY"
B = "01JD3QZ8X5V9K2M7N4P6R8T0WZ"

AD3_ORDER = [
    "SubjectSuppressed",
    "ContactErased",
    "LeadsMerged",
    "MessageSent",
    "InboxRemoved",
    "Enrolled",
    "DraftRejected",
    "EvidenceChanged",
    "EventClosed",
    "FooterChanged",
]
FIELDS = {
    "SubjectSuppressed": ["suppression_entry_id", "source", "contact_id", "lead_id"],
    "ContactErased": ["contact_id", "cause"],
    "LeadsMerged": ["survivor_lead_id", "merged_lead_id"],
    "MessageSent": ["om_id", "draft_id", "kind", "step_no"],
    "InboxRemoved": ["inbox_id"],
    "Enrolled": ["enrollment_id", "lead_id"],
    "DraftRejected": ["draft_id", "step_no", "bad_fit"],
    "EvidenceChanged": ["lead_id", "evidence_id"],
    "EventClosed": ["signal_id", "lead_id"],
    "FooterChanged": ["market_id"],
}


def valid_arguments(kind: type[Notification]) -> dict[str, Any]:
    arguments: dict[str, Any] = {}
    for field in dataclasses.fields(kind):
        if field.name.endswith("_id"):
            arguments[field.name] = A
        elif field.default is dataclasses.MISSING:
            arguments[field.name] = "opt_out"
    return arguments


def test_the_list_is_closed_and_in_ad3_order() -> None:
    assert [kind.__name__ for kind in NOTIFICATIONS] == AD3_ORDER


@pytest.mark.parametrize("kind", NOTIFICATIONS, ids=lambda kind: kind.__name__)
def test_each_notification_is_a_frozen_payload_with_its_fields(kind: type[Notification]) -> None:
    assert issubclass(kind, Notification)
    assert dataclasses.is_dataclass(kind)
    assert [field.name for field in dataclasses.fields(kind)] == FIELDS[kind.__name__]

    notification = kind(**valid_arguments(kind))

    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(notification, dataclasses.fields(kind)[0].name, B)


def test_payloads_hold_ulids_codes_ints_bools_and_none() -> None:
    sent = MessageSent(om_id=A, draft_id=B, kind="cold_step", step_no=2)
    rejected = DraftRejected(draft_id=A, step_no=None, bad_fit=True)
    suppressed = SubjectSuppressed(suppression_entry_id=A, source="reply_rule", lead_id=B)

    assert (sent.kind, sent.step_no) == ("cold_step", 2)
    assert (rejected.step_no, rejected.bad_fit) == (None, True)
    assert (suppressed.contact_id, suppressed.lead_id) == (None, B)
    assert ContactErased(contact_id=A, cause="x" * 64).cause == "x" * 64


@pytest.mark.parametrize(
    ("build", "error"),
    [
        (lambda: FooterChanged(market_id=A.lower()), ValueError),
        (lambda: FooterChanged(market_id="market-7"), ValueError),
        (lambda: FooterChanged(market_id=A + "\n"), ValueError),
        (lambda: FooterChanged(market_id=7), TypeError),  # type: ignore[arg-type]
        (lambda: InboxRemoved(inbox_id=A.encode()), TypeError),  # type: ignore[arg-type]
        (lambda: LeadsMerged(survivor_lead_id=A, merged_lead_id="opt_out"), ValueError),
        (lambda: ContactErased(contact_id=A, cause="DSR"), ValueError),
        (lambda: ContactErased(contact_id=A, cause="x" * 65), ValueError),
        (lambda: ContactErased(contact_id=A, cause="opt out"), ValueError),
        (lambda: ContactErased(contact_id=A, cause=""), ValueError),
        (lambda: ContactErased(contact_id=A, cause=A), ValueError),
        (lambda: ContactErased(contact_id=A, cause="ann@example.com"), ValueError),
        (lambda: MessageSent(A, B, "cold", step_no=1.5), TypeError),  # type: ignore[arg-type]
        (lambda: MessageSent(om_id=A, draft_id=B, kind=["cold"]), TypeError),  # type: ignore
        (lambda: EventClosed(signal_id=A, lead_id={"id": B}), TypeError),  # type: ignore[arg-type]
    ],
)
def test_anything_else_raises_at_construction(build: Any, error: type[Exception]) -> None:
    with pytest.raises(error, match="must be"):
        build()


@pytest.mark.parametrize(
    ("build", "field"),
    [
        (lambda: FooterChanged(market_id=None), "FooterChanged.market_id"),  # type: ignore
        (lambda: ContactErased(contact_id=A, cause=None), "ContactErased.cause"),  # type: ignore
        (lambda: MessageSent(A, None, "cold"), "MessageSent.draft_id"),  # type: ignore
    ],
)
def test_a_required_field_cannot_be_none(build: Any, field: str) -> None:
    with pytest.raises(TypeError, match=f"{field} is required"):
        build()


def test_an_optional_field_can_be_none() -> None:
    assert SubjectSuppressed(suppression_entry_id=A, source="dsr", contact_id=None).lead_id is None
    assert MessageSent(om_id=A, draft_id=B, kind="cold", step_no=None).step_no is None


def test_the_error_names_the_field_never_the_value() -> None:
    with pytest.raises(ValueError) as raised:
        ContactErased(contact_id=A, cause="ann@example.com")

    assert "ContactErased.cause" in str(raised.value)
    assert "ann@example.com" not in str(raised.value)


# nextron.domain.ids ------------------------------------------------------------------------------


@pytest.mark.parametrize("value", [A, B, "0" * 26, "7" + "Z" * 25])
def test_is_ulid_accepts_canonical_text(value: str) -> None:
    assert is_ulid(value)


@pytest.mark.parametrize(
    "value",
    [
        A.lower(),
        A[:-1],
        A + "0",
        A + "\n",
        "8ZZZZZZZZZZZZZZZZZZZZZZZZZ",
        "01JD3QZ8X5V9K2M7N4P6R8T0WI",
        "01JD3QZ8X5V9K2M7N4P6R8T0WL",
        "01JD3QZ8X5V9K2M7N4P6R8T0WO",
        "01JD3QZ8X5V9K2M7N4P6R8T0WU",
        "",
        None,
        7,
        b"01JD3QZ8X5V9K2M7N4P6R8T0WY",
    ],
)
def test_is_ulid_rejects_anything_else(value: object) -> None:
    assert not is_ulid(value)
