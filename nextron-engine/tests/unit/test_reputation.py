"""``nextron.domain.reputation`` (Story 6.6): DNSBL outcomes, the codec, spam statuses and their
boundaries, the panel texts and the alert subjects."""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from nextron.domain import reputation as r
from nextron.domain.reputation import SpamStatus, ZoneOutcome

DBL, URIBL, SURBL = r.KNOWN_ZONES


# Zone outcomes ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("zone", "answers", "outcome"),
    [
        (DBL, (), ZoneOutcome.CLEAN),
        (DBL, ("127.0.1.2",), ZoneOutcome.LISTED),
        (DBL, ("127.0.1.106",), ZoneOutcome.LISTED),
        (DBL, ("127.0.1.107",), ZoneOutcome.UNRECOGNISED),
        (DBL, ("127.0.1.1",), ZoneOutcome.UNRECOGNISED),
        (DBL, ("127.0.0.2",), ZoneOutcome.UNRECOGNISED),
        (DBL, ("127.255.255.254",), ZoneOutcome.QUERY_REFUSED),
        (DBL, ("127.255.255.0",), ZoneOutcome.QUERY_REFUSED),
        (DBL, ("127.255.255.252", "127.0.1.2"), ZoneOutcome.QUERY_REFUSED),
        (DBL, ("127.0.0.1",), ZoneOutcome.UNRECOGNISED),
        (DBL, ("93.184.216.34",), ZoneOutcome.UNRECOGNISED),  # a hijacked NXDOMAIN
        (DBL, ("not an address",), ZoneOutcome.UNRECOGNISED),
        (DBL, ("93.184.216.34", "127.0.1.5"), ZoneOutcome.LISTED),
        (URIBL, ("127.0.0.2",), ZoneOutcome.LISTED),
        (URIBL, ("127.0.0.14",), ZoneOutcome.LISTED),
        (URIBL, ("127.0.0.8",), ZoneOutcome.LISTED),
        (URIBL, ("127.0.0.3",), ZoneOutcome.UNRECOGNISED),
        (URIBL, ("127.0.0.16",), ZoneOutcome.UNRECOGNISED),
        (URIBL, ("127.0.0.1",), ZoneOutcome.UNRECOGNISED),
        (SURBL, ("127.0.0.2",), ZoneOutcome.LISTED),
        (SURBL, ("127.0.0.3",), ZoneOutcome.LISTED),
        (SURBL, ("127.0.0.254",), ZoneOutcome.LISTED),
        (SURBL, ("127.0.0.1",), ZoneOutcome.UNRECOGNISED),
        (SURBL, ("127.0.0.255",), ZoneOutcome.UNRECOGNISED),
        (SURBL, ("127.255.255.254",), ZoneOutcome.QUERY_REFUSED),
    ],
)
def test_each_answer_has_its_outcome(
    zone: str, answers: tuple[str, ...], outcome: ZoneOutcome
) -> None:
    assert r.zone_outcome(zone, answers) is outcome


def test_an_unknown_zone_is_refused() -> None:
    with pytest.raises(ValueError, match="known DNSBL zone"):
        r.zone_outcome("zen.spamhaus.org", ())


def test_the_overall_outcome_is_the_worst() -> None:
    assert r.overall([]) is ZoneOutcome.CLEAN
    assert r.overall([ZoneOutcome.CLEAN, ZoneOutcome.UNRECOGNISED]) is ZoneOutcome.UNRECOGNISED
    assert r.overall([ZoneOutcome.UNRECOGNISED, ZoneOutcome.QUERY_REFUSED]) is (
        ZoneOutcome.QUERY_REFUSED
    )
    assert r.overall([ZoneOutcome.QUERY_REFUSED, ZoneOutcome.LISTED, ZoneOutcome.CLEAN]) is (
        ZoneOutcome.LISTED
    )


def test_the_zone_list_rule_needs_one_known_zone() -> None:
    assert r.is_zone_list(["dbl.spamhaus.org"])
    assert r.is_zone_list(list(r.KNOWN_ZONES))
    assert not r.is_zone_list([])
    assert not r.is_zone_list(["dbl.spamhaus.org", "zen.spamhaus.org"])
    assert r.DEFAULT_ZONES == ("dbl.spamhaus.org",)
    assert r.dnsbl_name("example.com", DBL) == "example.com.dbl.spamhaus.org"


# The codec --------------------------------------------------------------------------------------


def test_the_dnsbl_result_round_trips_in_canonical_form() -> None:
    outcomes = {SURBL: ZoneOutcome.CLEAN, DBL: ZoneOutcome.LISTED}

    text = r.encode_dnsbl(outcomes)

    assert text == '{"dbl.spamhaus.org":"listed","multi.surbl.org":"clean"}'
    assert r.decode_dnsbl(text) == {DBL: ZoneOutcome.LISTED, SURBL: ZoneOutcome.CLEAN}
    assert list(r.decode_dnsbl(text)) == [DBL, SURBL]


@pytest.mark.parametrize(
    "outcomes",
    [{}, {"zen.spamhaus.org": ZoneOutcome.CLEAN}, {DBL: "clean"}, [(DBL, ZoneOutcome.CLEAN)]],
)
def test_encoding_refuses_anything_else(outcomes: object) -> None:
    with pytest.raises(ValueError):
        r.encode_dnsbl(outcomes)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "text",
    [
        "",
        "null",
        "{}",
        "[]",
        '{"dbl.spamhaus.org":"maybe"}',
        '{"zen.spamhaus.org":"clean"}',
        '{"dbl.spamhaus.org": "clean"}',  # not compact
        '{"multi.surbl.org":"clean","dbl.spamhaus.org":"clean"}',  # not sorted
        '{"dbl.spamhaus.org":1}',
    ],
)
def test_decoding_refuses_anything_encode_does_not_write(text: str) -> None:
    with pytest.raises(ValueError):
        r.decode_dnsbl(text)


def test_decoding_refuses_a_non_str() -> None:
    with pytest.raises(ValueError):
        r.decode_dnsbl(None)  # type: ignore[arg-type]


# Spam rate --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ratio", "ppm", "status"),
    [
        (0.0009, 900, SpamStatus.OK),
        (0.001, 1000, SpamStatus.ALERT),
        (0.0029, 2900, SpamStatus.ALERT),
        (0.003, 3000, SpamStatus.PAUSE),
        (0, 0, SpamStatus.OK),
        (1, 1_000_000, SpamStatus.PAUSE),
        (0.0012345, 1234, SpamStatus.ALERT),  # rounded half to even at the micro
        ("0.0005", 500, SpamStatus.OK),
        (Decimal("0.0031"), 3100, SpamStatus.PAUSE),
    ],
)
def test_ratios_become_ppm_and_statuses(ratio: object, ppm: int, status: SpamStatus) -> None:
    assert r.ratio_ppm(ratio) == ppm
    assert r.spam_status(r.ratio_ppm(ratio)) is status


@pytest.mark.parametrize(
    ("ppm", "status"),
    [
        (None, SpamStatus.NO_DATA),
        (0, SpamStatus.OK),
        (999, SpamStatus.OK),
        (1000, SpamStatus.ALERT),
        (2999, SpamStatus.ALERT),
        (3000, SpamStatus.PAUSE),
    ],
)
def test_each_ppm_boundary(ppm: int | None, status: SpamStatus) -> None:
    assert r.spam_status(ppm) is status


def test_no_data_is_never_zero_percent() -> None:
    assert r.spam_status(None) is SpamStatus.NO_DATA
    assert r.spam_rate_text(None) == "No data"
    assert r.spam_rate_text(0) == "0.00%"


@pytest.mark.parametrize("ratio", [-0.001, 1.5, True, None, "x", float("nan"), float("inf")])
def test_a_ratio_outside_0_to_1_or_not_a_number_is_refused(ratio: object) -> None:
    with pytest.raises(ValueError):
        r.ratio_ppm(ratio)


@pytest.mark.parametrize("ppm", [-1, True, 1.5, "1000"])
def test_a_bad_ppm_is_refused(ppm: object) -> None:
    with pytest.raises(ValueError):
        r.spam_status(ppm)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("ppm", "text"), [(1234, "0.12%"), (900, "0.09%"), (3000, "0.30%"), (1_000_000, "100.00%")]
)
def test_the_spam_rate_text(ppm: int, text: str) -> None:
    assert r.spam_rate_text(ppm) == text


def test_the_postmaster_day_is_the_utc_date_before_the_check() -> None:
    assert r.postmaster_day(datetime(2026, 11, 19, 7, 0, tzinfo=UTC)) == date(2026, 11, 18)
    late_east = datetime(2026, 11, 19, 1, 0, tzinfo=timezone(timedelta(hours=5)))  # 18th UTC
    assert r.postmaster_day(late_east) == date(2026, 11, 17)
    with pytest.raises(ValueError):
        r.postmaster_day(datetime(2026, 11, 19, 7, 0))  # noqa: DTZ001 - naive on purpose


# Texts and subjects -----------------------------------------------------------------------------


def test_the_dnsbl_texts() -> None:
    assert r.dnsbl_text({DBL: ZoneOutcome.CLEAN}) == "Clean"
    assert r.dnsbl_text({DBL: ZoneOutcome.LISTED}) == "Listed (dbl.spamhaus.org)"
    assert r.dnsbl_text({SURBL: ZoneOutcome.LISTED, DBL: ZoneOutcome.LISTED}) == (
        "Listed (dbl.spamhaus.org, multi.surbl.org)"
    )
    assert r.dnsbl_text({DBL: ZoneOutcome.QUERY_REFUSED}) == "Query refused"
    assert r.dnsbl_text({DBL: ZoneOutcome.UNRECOGNISED}) == "Unrecognised answer"


def test_the_alert_subject_is_one_per_domain_and_utc_day() -> None:
    assert r.alert_subject("01JD3QZ8X5V9K2M7N4P6R8T0WY", date(2026, 11, 19)) == (
        "01jd3qz8x5v9k2m7n4p6r8t0wy:2026-11-19"
    )


def test_the_alert_texts_carry_ids_and_counts_only() -> None:
    domain_id = "01JD3QZ8X5V9K2M7N4P6R8T0WY"
    assert r.blocklisted_text(domain_id, 2) == (
        f"Sending Domain {domain_id} is blocklisted; 2 inboxes paused"
    )
    assert r.blocklisted_text(domain_id, 1).endswith("1 inbox paused")
    assert r.spam_pause_text(domain_id, 3100, 0) == (
        f"Sending Domain {domain_id} spam rate 0.31%; 0 inboxes paused"
    )
    assert "0.12%" in r.spam_high_text(domain_id, 1234)
