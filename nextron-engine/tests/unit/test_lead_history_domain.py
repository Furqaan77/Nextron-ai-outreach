"""``nextron.domain.lead_history`` (Story 9.8): categories, labels, Signal expiry, ordering ties,
the type filter and the page clamp."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from nextron.domain.lead_history import (
    EVENT,
    PAGE_SIZE,
    HistoryEntry,
    HistoryType,
    actor_label,
    code_label,
    entity_label,
    history_type,
    http_link,
    page_of,
    signal_expired_at,
)

T = datetime(2026, 11, 19, 9, 0, tzinfo=UTC)


def entry(sort_id: str, at: datetime = T, type: HistoryType | None = None) -> HistoryEntry:
    return HistoryEntry(
        at=at,
        sort_id=sort_id,
        type=type,
        kind=EVENT,
        entity="leads",
        entity_id="x",
        from_status=None,
        to_status="new",
        cause=None,
        actor=None,
        title="Lead",
    )


@pytest.mark.parametrize(
    ("entity", "to_status", "expected"),
    [
        ("outbound_messages", "sent", HistoryType.MESSAGES),
        ("replies", "classified", HistoryType.MESSAGES),
        ("linkedin_tasks", "done", HistoryType.MESSAGES),
        ("drafts", "approved", HistoryType.DRAFTS),
        ("approvals", "granted", HistoryType.DRAFTS),
        ("evidence", "live", HistoryType.EVIDENCE),
        ("signals", "expired", HistoryType.EVIDENCE),
        ("job_postings", "live", HistoryType.EVIDENCE),
        ("call_outcomes", "held", HistoryType.CALLS),
        ("call_preps", "passed", HistoryType.CALLS),
        ("leads", "call_booked", HistoryType.STAGES),
        ("leads", "new", HistoryType.STAGES),
        ("leads", "contact_blocked", None),
        ("contacts", "erased", None),
        ("suppression_entries", "active", None),
        ("enrollments", "active", None),
        ("some_later_table", "done", None),
    ],
)
def test_each_table_has_its_category(
    entity: str, to_status: str, expected: HistoryType | None
) -> None:
    assert history_type(entity, to_status) is expected


def test_labels() -> None:
    assert entity_label("call_outcomes") == "Call"
    assert entity_label("some_later_table") == "Some later table"
    assert code_label("created_from_csv") == "Created from csv"
    assert code_label(None) == code_label("") == "—"
    assert actor_label("operator") == "Operator"
    assert actor_label("sender") == "Sender"
    assert actor_label("seed") == "Seed"
    assert actor_label("system:pipeline") == "Engine (pipeline)"
    assert actor_label("ai:extract_signals@v1.2") == "AI (extract_signals)"
    assert actor_label("rule") == "Engine (rule)"
    assert actor_label(None) == "—"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://firm.co.uk/news", "https://firm.co.uk/news"),
        ("HTTP://firm.co.uk/", "HTTP://firm.co.uk/"),
        ("javascript:alert(1)", None),
        ("ftp://firm.co.uk/", None),
        ("https:///nohost", None),
        (None, None),
    ],
)
def test_only_http_links_are_kept(url: str | None, expected: str | None) -> None:
    assert http_link(url) == expected


def test_signal_expiry_follows_the_liveness_rule() -> None:
    seen = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)

    tier_1 = signal_expired_at("event", 1, None, seen)
    tier_2 = signal_expired_at("event", 2, date(2026, 8, 1), seen)

    assert tier_1 == datetime(2026, 8, 1, tzinfo=UTC) + timedelta(days=90)
    assert tier_2 == datetime(2026, 8, 1, tzinfo=UTC) + timedelta(days=91)
    assert signal_expired_at("attribute", 1, None, seen) is None
    # an event date long before first seen: never before first seen
    assert signal_expired_at("event", 1, date(2026, 1, 1), seen) == seen
    # a class the domain refuses counts as not live at once
    assert signal_expired_at("bogus", 1, None, seen) == seen


def test_newest_first_ties_break_on_the_sort_id_descending() -> None:
    entries = [entry("01A"), entry("01C"), entry("01B"), entry("00Z", T + timedelta(seconds=1))]

    found = page_of(entries, HistoryType.ALL, 1)

    assert [each.sort_id for each in found.entries] == ["00Z", "01C", "01B", "01A"]


def test_the_filter_keeps_only_its_type_and_all_keeps_every_entry() -> None:
    entries = [entry("1", type=HistoryType.CALLS), entry("2"), entry("3", type=HistoryType.STAGES)]

    assert [e.sort_id for e in page_of(entries, HistoryType.CALLS, 1).entries] == ["1"]
    assert page_of(entries, HistoryType.ALL, 1).total == 3
    assert page_of(entries, HistoryType.DRAFTS, 1).entries == ()


def test_pages_hold_100_and_a_page_past_the_last_shows_the_last() -> None:
    entries = [entry(f"{index:04d}", T + timedelta(seconds=index)) for index in range(230)]

    third = page_of(entries, HistoryType.ALL, 3)
    past = page_of(entries, HistoryType.ALL, 9)
    empty = page_of([], HistoryType.ALL, 4)

    assert (third.total, third.page, third.pages, len(third.entries)) == (230, 3, 3, 30)
    assert third.entries[0].sort_id == "0029"
    assert past == third
    assert len(page_of(entries, HistoryType.ALL, 1).entries) == PAGE_SIZE
    assert (empty.page, empty.pages, empty.entries) == (1, 1, ())


@pytest.mark.parametrize(
    ("arguments", "error"),
    [
        ({"type": "all"}, TypeError),
        ({"page": True}, TypeError),
        ({"page": 1.0}, TypeError),
        ({"page": 0}, ValueError),
    ],
)
def test_bad_arguments_raise(arguments: dict[str, Any], error: type[Exception]) -> None:
    call: dict[str, Any] = {"type": HistoryType.ALL, "page": 1, **arguments}

    with pytest.raises(error):
        page_of([], call["type"], call["page"])
