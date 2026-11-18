"""``nextron.domain.domain_auth`` (Story 6.5, FR-64): every SPF, DKIM and DMARC reason, the
alignment cases, the verdict's edges (25:59, 26:00, a future stamp, no check), its texts, the
details codec's round trip and refusals, and ``check_missed``."""

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

import pytest

from nextron.domain.domain_auth import (
    CHECK_WINDOW,
    DOMAIN_CHECK_TIME,
    ITEM_LABELS,
    ITEM_REASONS,
    REASON_TEXTS,
    AuthItem,
    AuthJudgement,
    AuthReason,
    AuthVerdict,
    auth_verdict,
    check_missed,
    decode_details,
    dkim_name,
    dmarc_name,
    encode_details,
    judge,
)

DOMAIN = "nextron-ai.com"
SPF_OK = "v=spf1 include:_spf.google.com ~all"
DKIM_OK = "v=DKIM1; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA"
DMARC_OK = "v=DMARC1; p=none"
NOW = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)


def spf(*records: str) -> AuthReason:
    return judge(DOMAIN, records, [DKIM_OK], [DMARC_OK], []).spf


def dkim(*records: str) -> AuthReason:
    return judge(DOMAIN, [SPF_OK], records, [DMARC_OK], []).dkim


def dmarc(*records: str) -> AuthReason:
    return judge(DOMAIN, [SPF_OK], [DKIM_OK], records, []).dmarc


# Names ------------------------------------------------------------------------------------------


def test_the_dkim_and_dmarc_names() -> None:
    assert dkim_name(DOMAIN) == "google._domainkey.nextron-ai.com"
    assert dmarc_name(DOMAIN) == "_dmarc.nextron-ai.com"
    assert (DOMAIN_CHECK_TIME, CHECK_WINDOW) == (time(7, 0), timedelta(hours=26))


# SPF --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "records",
    [
        (SPF_OK,),
        ("v=spf1 +include:_spf.google.com -all",),
        ("V=SPF1 INCLUDE:_SPF.GOOGLE.COM ~ALL",),  # any case
        ("v=spf1  ip4:192.0.2.1   include:_spf.google.com  -all",),  # any whitespace
        ("google-site-verification=abc", SPF_OK),  # other TXT records don't count
    ],
)
def test_spf_passes_on_a_direct_google_include_in_the_one_spf_record(
    records: tuple[str, ...],
) -> None:
    assert spf(*records) is AuthReason.OK


@pytest.mark.parametrize(
    ("records", "reason"),
    [
        ((), AuthReason.SPF_MISSING),
        (("google-site-verification=abc",), AuthReason.SPF_MISSING),
        (("v=spf10 include:_spf.google.com",), AuthReason.SPF_MISSING),  # not v=spf1
        (("include:_spf.google.com v=spf1",), AuthReason.SPF_MISSING),  # v=spf1 must be first
        ((SPF_OK, "v=spf1 -all"), AuthReason.SPF_MULTIPLE),
        (("v=spf1 include:_spf.example.net -all",), AuthReason.SPF_NO_GOOGLE),
        (("v=spf1 redirect=_spf.google.com",), AuthReason.SPF_NO_GOOGLE),  # never followed
        (("v=spf1 -include:_spf.google.com",), AuthReason.SPF_NO_GOOGLE),
        (("v=spf1 include:_spf.google.com.",), AuthReason.SPF_NO_GOOGLE),
    ],
)
def test_each_spf_failure(records: tuple[str, ...], reason: AuthReason) -> None:
    assert spf(*records) is reason


# DKIM -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "records",
    [
        (DKIM_OK,),
        ("p=MIIB",),
        ("v=DKIM1; p = MIIB AQAB ",),  # whitespace around and inside the key
        ("v=DKIM1; p=", "v=DKIM1; p=MIIB"),  # some record has a key
    ],
)
def test_dkim_passes_on_a_non_empty_key(records: tuple[str, ...]) -> None:
    assert dkim(*records) is AuthReason.OK


@pytest.mark.parametrize(
    ("records", "reason"),
    [
        ((), AuthReason.DKIM_MISSING),
        (("v=DKIM1; p=",), AuthReason.DKIM_EMPTY_KEY),
        (("v=DKIM1; k=rsa; p=  ",), AuthReason.DKIM_EMPTY_KEY),
        (("v=DKIM1; k=rsa",), AuthReason.DKIM_EMPTY_KEY),
        (("v=DKIM1; P=MIIB",), AuthReason.DKIM_EMPTY_KEY),  # DKIM tags are case-sensitive
    ],
)
def test_each_dkim_failure(records: tuple[str, ...], reason: AuthReason) -> None:
    assert dkim(*records) is reason


# DMARC ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "records",
    [
        (DMARC_OK,),
        ("v=DMARC1",),
        ("  v = DMARC1 ; p=reject",),  # whitespace around = and ;
        ("v=DMARC2; p=none", DMARC_OK),  # only one is a DMARC record
    ],
)
def test_dmarc_passes_on_exactly_one_dmarc1_record(records: tuple[str, ...]) -> None:
    assert dmarc(*records) is AuthReason.OK


@pytest.mark.parametrize(
    ("records", "reason"),
    [
        ((), AuthReason.DMARC_MISSING),  # NXDOMAIN answers nothing
        (("v=DMARC2; p=none",), AuthReason.DMARC_MISSING),
        (("v=dmarc1; p=none",), AuthReason.DMARC_MISSING),  # the exact value
        (("p=none; v=DMARC1",), AuthReason.DMARC_MISSING),  # v must be the first tag
        (("v=DMARC1x",), AuthReason.DMARC_MISSING),
        ((DMARC_OK, "v=DMARC1; p=reject"), AuthReason.DMARC_MULTIPLE),
    ],
)
def test_each_dmarc_failure(records: tuple[str, ...], reason: AuthReason) -> None:
    assert dmarc(*records) is reason


# Alignment --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spf_txt", "dkim_txt", "from_domains", "reason"),
    [
        ([SPF_OK], [DKIM_OK], [DOMAIN], AuthReason.OK),
        ([SPF_OK], [], [DOMAIN], AuthReason.OK),  # SPF alone aligns
        ([], [DKIM_OK], [DOMAIN], AuthReason.OK),  # DKIM alone aligns
        ([SPF_OK], [DKIM_OK], ["mail.nextron-ai.com"], AuthReason.OK),  # relaxed
        ([SPF_OK], [DKIM_OK], [], AuthReason.OK),  # no inbox: the Sending Domain itself
        ([], [], [DOMAIN], AuthReason.UNAUTHENTICATED),
        (["v=spf1 -all"], ["v=DKIM1; p="], [DOMAIN], AuthReason.UNAUTHENTICATED),
        ([SPF_OK], [DKIM_OK], [DOMAIN, "other.example.com"], AuthReason.FROM_DOMAIN_MISMATCH),
        ([SPF_OK], [DKIM_OK], ["co.uk"], AuthReason.FROM_DOMAIN_MISMATCH),  # no org domain
    ],
)
def test_relaxed_alignment(
    spf_txt: list[str], dkim_txt: list[str], from_domains: list[str], reason: AuthReason
) -> None:
    assert judge(DOMAIN, spf_txt, dkim_txt, [DMARC_OK], from_domains).alignment is reason


def test_a_passing_judgement_and_a_failing_one_list_their_items_in_order() -> None:
    passing = judge(DOMAIN, [SPF_OK], [DKIM_OK], [DMARC_OK], [DOMAIN])
    failing = judge(DOMAIN, [], [], [], [DOMAIN])

    assert passing == AuthJudgement(AuthReason.OK, AuthReason.OK, AuthReason.OK, AuthReason.OK)
    assert (passing.passed, passing.failing) == (True, ())
    assert failing.reasons == {
        AuthItem.SPF: AuthReason.SPF_MISSING,
        AuthItem.DKIM: AuthReason.DKIM_MISSING,
        AuthItem.DMARC: AuthReason.DMARC_MISSING,
        AuthItem.ALIGNMENT: AuthReason.UNAUTHENTICATED,
    }
    assert (failing.passed, failing.failing) == (False, tuple(AuthItem))


def test_a_judgement_refuses_a_reason_of_another_item() -> None:
    with pytest.raises(ValueError, match="spf"):
        AuthJudgement(AuthReason.DKIM_MISSING, AuthReason.OK, AuthReason.OK, AuthReason.OK)


def test_judge_refuses_values_that_arent_str() -> None:
    with pytest.raises(TypeError):
        judge(DOMAIN, [b"v=spf1"], [], [], [])  # type: ignore[list-item]
    with pytest.raises(TypeError):
        judge(None, [], [], [], [])  # type: ignore[arg-type]


# The verdict ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    checked_at: datetime
    passed: bool
    failing: tuple[AuthItem, ...] = ()


@pytest.mark.parametrize(
    ("age", "blocked", "text"),
    [
        (timedelta(0), False, "Cold sends allowed"),
        (timedelta(hours=25, minutes=59), False, "Cold sends allowed"),
        (
            timedelta(hours=26),
            True,
            "Cold sends blocked: no check passed in the last 26 hours",
        ),
        (
            timedelta(seconds=-1),  # stamped in the future: the clock was set back
            True,
            "Cold sends blocked: no check passed in the last 26 hours",
        ),
    ],
)
def test_a_pass_counts_for_26_hours_and_never_from_the_future(
    age: timedelta, blocked: bool, text: str
) -> None:
    verdict = auth_verdict(Check(NOW - age, True), NOW)

    assert verdict == AuthVerdict(blocked, (), text)


def test_no_check_is_blocked_and_a_failed_check_names_its_items() -> None:
    failed = Check(NOW, False, (AuthItem.SPF, AuthItem.DMARC))

    assert auth_verdict(None, NOW) == AuthVerdict(True, (), "Cold sends blocked: not checked yet")
    assert auth_verdict(failed, NOW) == AuthVerdict(
        True, (AuthItem.SPF, AuthItem.DMARC), "Cold sends blocked: SPF, DMARC failing"
    )
    assert auth_verdict(Check(NOW, False, (AuthItem.SPF,)), NOW).text == (
        "Cold sends blocked: SPF failing"
    )
    assert auth_verdict(Check(NOW - timedelta(days=3), False, (AuthItem.ALIGNMENT,)), NOW).text == (
        "Cold sends blocked: Alignment failing"  # a stale failure still names its items
    )


def test_every_item_has_a_label_and_every_reason_a_text() -> None:
    assert list(ITEM_LABELS.items()) == [
        (AuthItem.SPF, "SPF"),
        (AuthItem.DKIM, "DKIM"),
        (AuthItem.DMARC, "DMARC"),
        (AuthItem.ALIGNMENT, "Alignment"),
    ]
    assert set(REASON_TEXTS) == set(AuthReason)
    assert all(text.strip() for text in REASON_TEXTS.values())
    assert set().union(*ITEM_REASONS.values()) == set(AuthReason)


# Details ----------------------------------------------------------------------------------------


def test_details_round_trip_as_compact_sorted_json() -> None:
    reasons = judge(DOMAIN, ["v=spf1 -all"], ["v=DKIM1; p="], [], ["other.example.com"]).reasons

    text = encode_details(reasons)

    assert text == (
        '{"alignment":"unauthenticated","dkim":"dkim_empty_key","dmarc":"dmarc_missing",'
        '"spf":"spf_no_google"}'
    )
    assert decode_details(text) == reasons
    assert list(decode_details(text)) == list(AuthItem)
    assert len(encode_details(judge(DOMAIN, [], [], [], ["x.example"]).reasons)) <= 256


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "[]",
        '{"spf":"ok","dkim":"ok","dmarc":"ok"}',  # an item missing
        '{"alignment":"ok","dkim":"ok","dmarc":"ok","spf":"ok","x":"ok"}',  # an extra key
        '{"alignment":"ok","dkim":"ok","dmarc":"ok","spf":"dkim_missing"}',  # another item's
        '{"alignment":"ok","dkim":"ok","dmarc":"ok","spf":"nope"}',
        '{"alignment":"ok","dkim":"ok","dmarc":"ok","spf":1}',
        '{"spf":"ok","dmarc":"ok","dkim":"ok","alignment":"ok"}',  # keys not sorted
        '{"alignment": "ok", "dkim": "ok", "dmarc": "ok", "spf": "ok"}',  # not compact
    ],
)
def test_decode_refuses_anything_encode_doesnt_write(text: str) -> None:
    with pytest.raises(ValueError):
        decode_details(text)


def test_decode_refuses_a_non_str_and_encode_refuses_a_wrong_mapping() -> None:
    every_ok = dict.fromkeys(AuthItem, AuthReason.OK)
    with pytest.raises(ValueError):
        decode_details(None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        encode_details({AuthItem.SPF: AuthReason.OK})
    with pytest.raises(ValueError):
        encode_details({**every_ok, AuthItem.DMARC: AuthReason.SPF_MISSING})
    with pytest.raises(ValueError):
        encode_details({**every_ok, AuthItem.DMARC: "ok"})  # type: ignore[dict-item]
    assert decode_details(encode_details(every_ok)) == every_ok


# The daily check --------------------------------------------------------------------------------


def test_check_missed_when_a_domain_has_no_check_since_the_latest_slot() -> None:
    at = time(7, 0)
    slot = datetime(2026, 11, 18, 7, 0, tzinfo=UTC)  # NOW is 09:00 UTC

    assert check_missed([], NOW, at, "UTC") is False  # no listed domain
    assert check_missed([None], NOW, at, "UTC") is True  # never checked
    assert check_missed([slot], NOW, at, "UTC") is False
    assert check_missed([slot, slot - timedelta(seconds=1)], NOW, at, "UTC") is True
    # stamped after now (the clock was set back): it proves nothing, so the check runs again
    assert check_missed([NOW + timedelta(seconds=1)], NOW, at, "UTC") is True
    assert check_missed([NOW], NOW, at, "UTC") is False
    # 09:00 UTC is 14:30 in Kolkata: today's 07:00 there was 01:30 UTC
    assert check_missed([datetime(2026, 11, 18, 1, 30, tzinfo=UTC)], NOW, at, "Asia/Kolkata") is (
        False
    )
    assert check_missed([datetime(2026, 11, 18, 1, 29, tzinfo=UTC)], NOW, at, "Asia/Kolkata") is (
        True
    )
