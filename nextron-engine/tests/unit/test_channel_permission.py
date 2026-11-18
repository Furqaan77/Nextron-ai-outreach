"""Story 10.7: the Channel Permission rules (``nextron.domain.channel_permission``): the actions
each channel accepts, the record form's checks, local times (DST gap included), the 24-hour
Instagram window at its exact boundary, the machine and the block texts."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from nextron.domain import channel_permission as cp
from nextron.domain.channel_permission import (
    PermissionCause,
    PermissionChannel,
    PermissionEvent,
    PermissionRefused,
    PermissionStatus,
    ProspectAction,
)
from nextron.domain.contactability import BlockReason
from nextron.domain.extraction import MAX_QUOTE
from nextron.domain.machines import IllegalTransition

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
LONDON = ZoneInfo("Europe/London")
NEW_YORK = ZoneInfo("America/New_York")
EVIDENCE = "x" * 120


def record(**overrides: object) -> cp.RecordFields:
    fields: dict[str, object] = {
        "channel": "instagram",
        "action": "started_instagram_conversation",
        "occurred_at": "2026-11-18T11:00",
        "evidence": EVIDENCE,
        "number": "",
        "handle": "@jane.doe_1",
    }
    fields.update(overrides)
    return cp.check_record(
        fields["channel"],
        fields["action"],
        fields["occurred_at"],
        fields["evidence"],
        fields["number"],
        fields["handle"],
        now=NOW,
        tz=LONDON,
    )


def refused(**overrides: object) -> dict[str, str]:
    with pytest.raises(PermissionRefused) as caught:
        record(**overrides)
    assert caught.value.code == "channel_permission_refused"
    return caught.value.problems


def test_each_channel_accepts_only_its_own_actions() -> None:
    assert dict(cp.VALID_ACTIONS) == {
        PermissionChannel.WHATSAPP: (
            ProspectAction.STARTED_WHATSAPP_CONVERSATION,
            ProspectAction.GAVE_NUMBER_FOR_WHATSAPP,
        ),
        PermissionChannel.INSTAGRAM: (ProspectAction.STARTED_INSTAGRAM_CONVERSATION,),
    }
    assert set(cp.ACTION_LABELS) == set(ProspectAction)
    assert cp.NOT_QUALIFYING == (
        "An email reply, or a number in a signature or on a website, doesn't qualify"
    )


def test_a_valid_instagram_record_is_stripped_and_in_utc() -> None:
    fields = record(evidence=f"  {EVIDENCE}  ")

    assert fields == cp.RecordFields(
        PermissionChannel.INSTAGRAM,
        ProspectAction.STARTED_INSTAGRAM_CONVERSATION,
        datetime(2026, 11, 18, 11, 0, tzinfo=UTC),
        EVIDENCE,
        None,
        "jane.doe_1",
    )


def test_a_valid_whatsapp_record_keeps_the_number_without_separators() -> None:
    fields = record(
        channel="whatsapp",
        action="gave_number_for_whatsapp",
        number=" +44 7700-900 123 ",
        handle="",
    )

    assert (fields.channel, fields.action) == (
        PermissionChannel.WHATSAPP,
        ProspectAction.GAVE_NUMBER_FOR_WHATSAPP,
    )
    assert (fields.whatsapp_number, fields.instagram_handle) == ("+447700900123", None)


@pytest.mark.parametrize(
    ("overrides", "problems"),
    [
        ({"action": "gave_number_for_whatsapp"}, {"prospect_action": cp.BAD_ACTION}),
        ({"action": "replied_by_email"}, {"prospect_action": cp.BAD_ACTION}),
        ({"channel": "linkedin"}, {"channel": cp.BAD_CHANNEL}),
        ({"occurred_at": "2026-11-18T12:01"}, {"occurred_at": cp.FUTURE_TIME}),
        ({"occurred_at": "18/11/2026 11:00"}, {"occurred_at": cp.BAD_TIME}),
        ({"occurred_at": "2026-02-30T11:00"}, {"occurred_at": cp.BAD_TIME}),
        ({"evidence": "x" * 301}, {"evidence": cp.LONG_EVIDENCE}),
        ({"evidence": "   "}, {"evidence": cp.BLANK_EVIDENCE}),
        ({"evidence": "ok\x00"}, {"evidence": cp.BAD_EVIDENCE}),
        ({"handle": ""}, {"instagram_handle": cp.BAD_HANDLE}),
        ({"handle": "a" * 31}, {"instagram_handle": cp.BAD_HANDLE}),
        ({"handle": "jane doe"}, {"instagram_handle": cp.BAD_HANDLE}),
        ({"number": "+447700900123"}, {"whatsapp_number": cp.OTHER_CHANNEL}),
        (
            {"channel": "whatsapp", "action": "started_whatsapp_conversation", "number": "0770"},
            {"whatsapp_number": cp.BAD_NUMBER, "instagram_handle": cp.OTHER_CHANNEL},
        ),
        (
            {"channel": "whatsapp", "action": "started_whatsapp_conversation", "handle": ""},
            {"whatsapp_number": cp.BAD_NUMBER},
        ),
    ],
)
def test_each_bad_field_is_refused_with_its_message(
    overrides: dict[str, object], problems: dict[str, str]
) -> None:
    assert refused(**overrides) == problems


def test_every_problem_is_reported_at_once_and_the_message_holds_no_value() -> None:
    with pytest.raises(PermissionRefused) as caught:
        record(channel="", action="", occurred_at="", evidence="", handle="secret.handle")

    assert set(caught.value.problems) == {"channel", "prospect_action", "occurred_at", "evidence"}
    assert "secret" not in str(caught.value)


def test_evidence_of_exactly_300_characters_and_now_itself_are_accepted() -> None:
    fields = record(evidence="y" * 300, occurred_at="2026-11-18T12:00")

    assert (len(fields.evidence), fields.occurred_at) == (300, NOW)


def test_a_line_break_in_the_evidence_is_kept_as_a_newline() -> None:
    assert record(evidence="first line\r\nsecond line").evidence == "first line\nsecond line"
    assert record(evidence="first line\nsecond line").evidence == "first line\nsecond line"


def test_a_browser_crlf_does_not_count_twice_against_the_limit() -> None:
    # A textarea's maxlength counts a line break once; the browser submits it as CRLF.
    typed = "\n".join(["y" * 99] * 3) + "z"  # 300 characters as typed
    fields = record(evidence=typed.replace("\n", "\r\n"))  # 302 as submitted

    assert fields.evidence == typed
    assert cp.MAX_EVIDENCE == MAX_QUOTE


@pytest.mark.parametrize(
    ("text", "zone"),
    [
        ("9999-12-31T23:59", NEW_YORK),  # past datetime.max once in UTC
        ("0001-01-01T00:00", ZoneInfo("Asia/Tokyo")),  # before datetime.min once in UTC
    ],
)
def test_a_time_past_the_datetime_bounds_is_a_bad_time(text: str, zone: ZoneInfo) -> None:
    with pytest.raises(ValueError, match="Enter a date and time"):
        cp.parse_local(text, zone)
    with pytest.raises(PermissionRefused) as caught:
        cp.check_record(
            "instagram",
            "started_instagram_conversation",
            text,
            EVIDENCE,
            "",
            "jane",
            now=NOW,
            tz=zone,
        )
    assert caught.value.problems == {"occurred_at": cp.BAD_TIME}


def test_parse_local_reads_the_zone_and_refuses_a_dst_gap() -> None:
    summer = cp.parse_local("2026-07-01 09:30:15", LONDON)
    assert summer == datetime(2026, 7, 1, 8, 30, 15, tzinfo=UTC)
    with pytest.raises(ValueError, match="doesn't exist"):
        cp.parse_local("2026-03-08T02:30", NEW_YORK)  # the clocks skip 02:00-03:00
    # an ambiguous local time (the clocks go back) is its first occurrence
    assert cp.parse_local("2026-11-01T01:30", NEW_YORK) == datetime(
        2026, 11, 1, 5, 30, tzinfo=UTC
    )
    with pytest.raises(TypeError):
        cp.parse_local("2026-07-01T09:30", "Europe/London")  # type: ignore[arg-type]


def test_check_inbound_needs_a_later_past_time() -> None:
    stored = datetime(2026, 11, 18, 10, 0, tzinfo=UTC)

    assert cp.check_inbound("2026-11-18T11:30", now=NOW, tz=LONDON, stored=stored) == datetime(
        2026, 11, 18, 11, 30, tzinfo=UTC
    )
    for text, message in [
        ("2026-11-18T09:00", cp.NOT_LATER),
        ("2026-11-18T10:00", cp.NOT_LATER),
        ("2026-11-18T12:30", cp.FUTURE_TIME),
        ("soon", cp.BAD_TIME),
    ]:
        with pytest.raises(PermissionRefused) as caught:
            cp.check_inbound(text, now=NOW, tz=LONDON, stored=stored)
        assert caught.value.problems == {"last_inbound_at": message}


def test_the_window_closes_at_exactly_24_hours() -> None:
    last = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)

    assert cp.window_closes_at(last) == NOW
    assert cp.window_open(last, NOW - timedelta(microseconds=1))
    assert not cp.window_open(last, NOW)
    assert cp.window_text(last, NOW - timedelta(hours=1), LONDON) == (
        "24-hour window closes at 2026-11-18 12:00 (Europe/London)"
    )
    assert cp.window_text(last - timedelta(hours=1), NOW, NEW_YORK) == (
        "24-hour window closed at 2026-11-18 06:00 (America/New_York): don't send"
    )
    with pytest.raises(TypeError):
        cp.window_open(last, datetime(2026, 11, 18, 12, 0))  # noqa: DTZ001 - naive on purpose


def test_the_machine_revokes_an_active_permission_once() -> None:
    assert cp.CREATION.to is PermissionStatus.ACTIVE
    assert cp.CREATION.cause is PermissionCause.RECORDED
    causes = {
        PermissionEvent.REVOKE: PermissionCause.REVOKED,
        PermissionEvent.OPT_OUT: PermissionCause.OPTED_OUT,
        PermissionEvent.ERASE: PermissionCause.CONTACT_ERASED,
    }
    for event, cause in causes.items():
        change = cp.transition(PermissionStatus.ACTIVE, event)
        assert (change.to, change.cause) == (PermissionStatus.REVOKED, cause)
        with pytest.raises(IllegalTransition, match="not allowed from revoked"):
            cp.transition(PermissionStatus.REVOKED, event)
    assert cp.TERMINAL == (PermissionStatus.REVOKED,)


def test_the_block_text_names_every_reason() -> None:
    assert set(cp.BLOCK_TEXTS) == set(BlockReason)
    assert cp.blocked_text((BlockReason.ADDRESS_SUPPRESSED, BlockReason.LEAD_LOST)) == (
        "Nothing was recorded: this Contact can't be contacted (the email address is on the "
        "Suppression List; the Lead is closed as lost)"
    )
    assert cp.already_active(PermissionChannel.INSTAGRAM) == (
        "This Contact already has an active Instagram permission"
    )
