"""``domain.send_gate`` (FR-31, AD-11, NFR-7; Story 6.9): the closed list of exactly 13
predicates in order with their labels, the kinds (cold, responsive, anything else refused when a
``SendCandidate`` is built), and each row of the I/O matrix on the pure ``evaluate`` at a fixed
``now``: every fact good passes; each fact made bad fails its own predicate and nothing else;
``None`` (unknown) fails closed; a responsive OM skips every cold predicate."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from nextron import main
from nextron.domain import seed_yaml
from nextron.domain.markets import MarketDef, validate_markets
from nextron.domain.send_gate import (
    COLD_KINDS,
    LABELS,
    PREDICATES,
    RESPONSIVE_KINDS,
    GateFacts,
    GatePredicate,
    OmKind,
    Predicate,
    SendCandidate,
    brand_address,
    evaluate,
    om_kind,
)

P = Predicate
# Wednesday 2026-11-18 16:00 UTC: 11:00 in New York, 16:00 in London
NOW = datetime(2026, 11, 18, 16, 0, tzinfo=UTC)
HASH = "a" * 64
OM_ID = "01JD3QZ8X5V9K2M7N4P6R8T0AA"
ID = "01JD3QZ8X5V9K2M7N4P6R8T0AB"


def shipped_market(code: str) -> MarketDef:
    text = (main.SEED_DIR / main.MARKETS_FILE_NAME).read_text(encoding="utf-8")
    [found] = [market for market in validate_markets(seed_yaml.parse(text)) if market.code == code]
    return found


US, UK = shipped_market("us"), shipped_market("uk")
GOOD = GateFacts(
    contactable=True,
    approval_current=True,
    approved_at=NOW - timedelta(hours=1),
    approved_hash=HASH,
    revision_hash=HASH,
    recomputed_hash=HASH,
    inbox_status="active",
    inbox_address="furqaan@nextron-ai.com",
    domain_auth_ok=True,
    recovery_mode=False,
    footer_missing=(),
    footer_matches=True,
    market=US,
    eligible=True,
    enrollment_status="active",
    thread_quiet=True,
    campaign_active=True,
    contact_tz="America/New_York",
    cold_sent_today=3,
    cold_cap=10,
    warmup_days=20,
    sent_total_today=20,
    ceiling=50,
)
ALL_KIND = (
    P.CONTACTABLE,
    P.APPROVAL_LIVE,
    P.HASH_MATCHES,
    P.INBOX_READY,
    P.FOOTER_COMPLETE,
    P.MARKET_ELIGIBLE,
)
COLD_ONLY = (
    P.ENROLLMENT_ACTIVE,
    P.THREAD_QUIET,
    P.CAMPAIGN_ACTIVE,
    P.IN_SEND_WINDOW,
    P.UNDER_DAILY_CAP,
    P.WARMUP_OK,
    P.UNDER_TOTAL_CEILING,
)


def failing(facts: GateFacts, kind: str = "step", now: datetime = NOW) -> tuple[Predicate, ...]:
    return evaluate(kind, facts, now).failing


# The closed list --------------------------------------------------------------------------------


def test_the_predicates_are_exactly_the_13_in_order() -> None:
    assert [(p.name.value, p.cold_only) for p in PREDICATES] == [
        ("contactable", False),
        ("approval_live", False),
        ("hash_matches", False),
        ("inbox_ready", False),
        ("footer_complete", False),
        ("market_eligible", False),
        ("enrollment_active", True),
        ("thread_quiet", True),
        ("campaign_active", True),
        ("in_send_window", True),
        ("under_daily_cap", True),
        ("warmup_ok", True),
        ("under_total_ceiling", True),
    ]
    assert [p.name for p in PREDICATES] == list(Predicate)
    assert all(isinstance(p, GatePredicate) for p in PREDICATES)
    assert isinstance(PREDICATES, tuple)


def test_every_predicate_has_a_distinct_label() -> None:
    assert list(LABELS) == list(Predicate)
    assert LABELS[P.IN_SEND_WINDOW] == "Outside Send Window"
    assert all(label.strip() for label in LABELS.values())
    assert len(set(LABELS.values())) == len(LABELS)
    with pytest.raises(TypeError):
        LABELS[P.WARMUP_OK] = "x"  # type: ignore[index]


def test_the_kinds() -> None:
    assert COLD_KINDS == {OmKind.STEP, OmKind.RECONTACT}
    assert RESPONSIVE_KINDS == {OmKind.REPLY, OmKind.REBOOK}
    assert om_kind("rebook") is OmKind.REBOOK


@pytest.mark.parametrize("kind", ["channel", "Step", "", "task", "call"])
def test_another_kind_raises_when_the_candidate_is_built(kind: str) -> None:
    with pytest.raises(ValueError):
        SendCandidate(OM_ID, kind, ID, ID, ID, ID)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        evaluate(kind, GOOD, NOW)


def test_a_candidate_checks_its_ids() -> None:
    built = SendCandidate(OM_ID, "reply", ID, ID, ID, ID)  # type: ignore[arg-type]
    assert built.kind is OmKind.REPLY and not built.cold
    assert SendCandidate(OM_ID, OmKind.STEP, ID, ID, ID, ID, ID, ID).cold
    with pytest.raises(TypeError):
        SendCandidate(OM_ID, OmKind.STEP, ID, None, ID, ID)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        SendCandidate(OM_ID, 1, ID, ID, ID, ID)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        SendCandidate("not-a-ulid", OmKind.STEP, ID, ID, ID, ID)


def test_a_naive_now_raises() -> None:
    with pytest.raises(ValueError):
        evaluate("step", GOOD, NOW.replace(tzinfo=None))


# The matrix --------------------------------------------------------------------------------------


def test_every_fact_good_passes() -> None:
    result = evaluate("step", GOOD, NOW)
    assert result.failing == () and result.passed
    assert evaluate("recontact", GOOD, NOW).passed
    assert evaluate("reply", GOOD, NOW).passed


def test_no_fact_fails_every_predicate_in_order() -> None:
    assert failing(GateFacts()) == tuple(Predicate)
    assert failing(GateFacts(), "rebook") == ALL_KIND
    result = evaluate("step", GateFacts(), NOW)
    assert result.labels == tuple(p.label for p in PREDICATES)


def test_approval_expiry_at_72_hours() -> None:
    approved = NOW - timedelta(hours=72)
    assert failing(replace(GOOD, approved_at=approved)) == (P.APPROVAL_LIVE,)
    edge = NOW - timedelta(hours=71, minutes=59)
    assert failing(replace(GOOD, approved_at=edge)) == ()
    assert failing(replace(GOOD, approved_at=None)) == (P.APPROVAL_LIVE,)
    naive = (NOW - timedelta(hours=1)).replace(tzinfo=None)
    assert failing(replace(GOOD, approved_at=naive)) == (P.APPROVAL_LIVE,)
    assert failing(replace(GOOD, approved_at=NOW)) == ()


@pytest.mark.parametrize("ahead", [timedelta(seconds=1), timedelta(hours=1), timedelta(hours=100)])
def test_an_approval_dated_after_now_fails_closed(ahead: timedelta) -> None:
    assert failing(replace(GOOD, approved_at=NOW + ahead)) == (P.APPROVAL_LIVE,)
    assert failing(replace(GOOD, approved_at=NOW + ahead), "reply") == (P.APPROVAL_LIVE,)


@pytest.mark.parametrize("current", [False, None])
def test_an_approval_that_isnt_current(current: bool | None) -> None:
    assert failing(replace(GOOD, approval_current=current)) == (P.APPROVAL_LIVE,)


@pytest.mark.parametrize(
    "change",
    [
        {"revision_hash": "b" * 64},
        {"recomputed_hash": "b" * 64},
        {"approved_hash": None},
        {"approved_hash": ""},
        {"revision_hash": None},
        {"recomputed_hash": None},
    ],
)
def test_a_hash_mismatch(change: dict[str, object]) -> None:
    assert failing(replace(GOOD, **change)) == (P.HASH_MATCHES,)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "change",
    [
        {"inbox_status": "paused"},
        {"inbox_status": "auth_failed"},
        {"inbox_status": "connecting"},
        {"inbox_status": None},
        {"domain_auth_ok": False},
        {"domain_auth_ok": None},
        {"recovery_mode": True},
        {"recovery_mode": None},
        {"inbox_address": "furqaan@nextronai.com"},
        {"inbox_address": "furqaan@mail.NextronAI.com."},
        {"inbox_address": None},
    ],
)
def test_an_inbox_that_isnt_ready(change: dict[str, object]) -> None:
    assert failing(replace(GOOD, **change)) == (P.INBOX_READY,)  # type: ignore[arg-type]


def test_the_brand_domain() -> None:
    assert brand_address("a@nextronai.com") and brand_address("a@x.nextronai.com")
    assert not brand_address("a@nextron-ai.com") and not brand_address("a@notnextronai.com")
    assert brand_address("no-at-sign") and brand_address(None)


@pytest.mark.parametrize(
    "change",
    [
        {"footer_missing": ("legal_name",)},
        {"footer_missing": ("privacy_notice_url",)},
        {"footer_missing": None},
        {"footer_matches": False},
        {"footer_matches": None},
    ],
)
def test_an_incomplete_or_stale_footer(change: dict[str, object]) -> None:
    assert failing(replace(GOOD, **change)) == (P.FOOTER_COMPLETE,)  # type: ignore[arg-type]


def test_market_eligibility() -> None:
    disabled = replace(US, enabled=False)
    assert failing(replace(GOOD, market=disabled)) == (P.MARKET_ELIGIBLE,)
    assert failing(replace(GOOD, eligible=False)) == (P.MARKET_ELIGIBLE,)
    assert failing(replace(GOOD, eligible=None)) == (P.MARKET_ELIGIBLE,)
    # no Market: eligibility, and the window it would be read in
    assert failing(replace(GOOD, market=None)) == (P.MARKET_ELIGIBLE, P.IN_SEND_WINDOW)
    # a responsive kind needs an enabled Market but no live Eligibility
    assert failing(replace(GOOD, eligible=False), "reply") == ()
    assert failing(replace(GOOD, market=disabled), "reply") == (P.MARKET_ELIGIBLE,)


def test_suppressed_after_approval() -> None:
    assert failing(replace(GOOD, contactable=False)) == (P.CONTACTABLE,)
    assert failing(replace(GOOD, contactable=None)) == (P.CONTACTABLE,)


@pytest.mark.parametrize("status", ["paused", "stopped", "completed", None])
def test_an_enrollment_that_isnt_active(status: str | None) -> None:
    assert failing(replace(GOOD, enrollment_status=status)) == (P.ENROLLMENT_ACTIVE,)


@pytest.mark.parametrize("quiet", [False, None])
def test_thread_activity_or_unknown(quiet: bool | None) -> None:
    assert failing(replace(GOOD, thread_quiet=quiet)) == (P.THREAD_QUIET,)


@pytest.mark.parametrize("active", [False, None])
def test_an_inactive_campaign(active: bool | None) -> None:
    assert failing(replace(GOOD, campaign_active=active)) == (P.CAMPAIGN_ACTIVE,)


def test_the_send_window() -> None:
    monday_0759 = datetime(2026, 11, 16, 7, 59, tzinfo=UTC)  # London is on GMT
    uk = replace(
        GOOD, market=UK, contact_tz="Europe/London", approved_at=monday_0759 - timedelta(hours=2)
    )
    assert failing(uk, now=monday_0759 - timedelta(hours=1)) == (P.IN_SEND_WINDOW,)
    assert failing(uk, now=monday_0759.replace(hour=8, minute=0) - timedelta(minutes=1)) == (
        P.IN_SEND_WINDOW,
    )
    saturday = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
    assert P.IN_SEND_WINDOW in failing(replace(uk, approved_at=saturday), now=saturday)
    unknown_us = replace(GOOD, contact_tz=None)
    ny_1059 = datetime(2026, 11, 18, 15, 59, tzinfo=UTC)
    assert failing(replace(unknown_us, approved_at=ny_1059), now=ny_1059) == (P.IN_SEND_WINDOW,)
    assert failing(unknown_us) == ()  # 11:00 New York


@pytest.mark.parametrize(
    "change",
    [
        {"cold_sent_today": 10},
        {"cold_sent_today": 11},
        {"cold_sent_today": None},
        {"cold_cap": None},
        {"cold_cap": 0, "cold_sent_today": 0},
    ],
)
def test_the_daily_cap(change: dict[str, object]) -> None:
    assert failing(replace(GOOD, **change)) == (P.UNDER_DAILY_CAP,)  # type: ignore[arg-type]


@pytest.mark.parametrize("days", [13, 0, -1, None])
def test_warm_up_under_14_days(days: int | None) -> None:
    assert failing(replace(GOOD, warmup_days=days)) == (P.WARMUP_OK,)
    assert failing(replace(GOOD, warmup_days=14)) == ()


@pytest.mark.parametrize(
    "change", [{"sent_total_today": 50}, {"sent_total_today": None}, {"ceiling": None}]
)
def test_the_total_ceiling(change: dict[str, object]) -> None:
    assert failing(replace(GOOD, **change)) == (P.UNDER_TOTAL_CEILING,)  # type: ignore[arg-type]
    assert failing(replace(GOOD, sent_total_today=49)) == ()


def test_a_count_that_isnt_a_whole_number_fails_closed() -> None:
    assert failing(replace(GOOD, cold_sent_today=True)) == (P.UNDER_DAILY_CAP,)  # type: ignore[arg-type]
    assert failing(replace(GOOD, warmup_days=20.0)) == (P.WARMUP_OK,)  # type: ignore[arg-type]


@pytest.mark.parametrize("kind", ["reply", "rebook"])
def test_a_responsive_om_skips_every_cold_predicate(kind: str) -> None:
    cold_all_bad = replace(
        GOOD,
        eligible=False,
        enrollment_status="stopped",
        thread_quiet=None,
        campaign_active=False,
        contact_tz="Europe/London",
        cold_sent_today=None,
        cold_cap=None,
        warmup_days=None,
        sent_total_today=None,
        ceiling=None,
    )
    saturday = datetime(2026, 11, 21, 3, 0, tzinfo=UTC)
    facts = replace(cold_all_bad, approved_at=saturday - timedelta(hours=1))
    assert failing(facts, kind, saturday) == ()
    # eligible=False is the cold half of market_eligible
    assert failing(facts, "step", saturday) == (P.MARKET_ELIGIBLE, *COLD_ONLY)
    worst = replace(facts, contactable=False, footer_matches=False)
    assert failing(worst, kind, saturday) == (P.CONTACTABLE, P.FOOTER_COMPLETE)


def test_every_failure_is_listed_in_order() -> None:
    facts = replace(GOOD, warmup_days=1, contactable=False, thread_quiet=False, recovery_mode=True)
    assert failing(facts) == (P.CONTACTABLE, P.INBOX_READY, P.THREAD_QUIET, P.WARMUP_OK)
