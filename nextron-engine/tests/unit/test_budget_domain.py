"""``nextron.domain.budget``: the call records and their refusals, Hunter's weights, UTC months at
their edges, ``allow`` at 79%, 80%, 99% and 100% of each quota and the AI budget (and at a
limit of 0), ``essential`` work always allowed, ``assess``'s states, the exact threshold texts,
and the money and quantity formats."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from nextron.domain.budget import (
    QUOTAS,
    UNIT_WEIGHTS,
    AiCall,
    AiLine,
    AiState,
    ApiCall,
    ApiUse,
    BudgetClass,
    Limits,
    QuotaLine,
    QuotaState,
    Threshold,
    UnknownQuota,
    Usage,
    allow,
    assess,
    display_scale,
    dollars,
    month_bounds,
    month_of,
    quantity,
    quota_spec,
    thresholds,
)
from nextron.domain.errors import DomainError

MONTH = "2026-11"
NOW = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
BUDGET = 20_000_000  # $20
DEFAULT_QUOTAS = {
    "hunter": 100,  # 50 credits, stored doubled
    "prospeo": 100,
    "places_enterprise": 1_000,
    "places_pro": 0,
    "job_aggregator": 0,
}


def limits(ai_budget: int = BUDGET, ai_pct: int = 80, **quotas: int) -> Limits:
    return Limits({**DEFAULT_QUOTAS, **quotas}, ai_budget, ai_pct)


def usage(*lines: ApiUse, ai_cost: int = 0, ai_calls: int = 0, month: str = MONTH) -> Usage:
    return Usage(month, lines, ai_calls, ai_cost)


def line(provider: str, operation: str, units: int, sku: str | None = None) -> ApiUse:
    return ApiUse(provider, operation, sku, calls=units, failed=0, units=units)


def ai(**overrides: Any) -> AiCall:
    fields: dict[str, Any] = {
        "route": "draft_email",
        "route_tuple": "draft_email:v3:claude-opus-5-5",
        "served_model": "claude-opus-5-5",
        "input_tokens": 1_200,
        "output_tokens": 300,
        "cost_micros": 1_234,
    }
    fields.update(overrides)
    route, route_tuple, served_model = (
        fields.pop("route"),
        fields.pop("route_tuple"),
        fields.pop("served_model"),
    )
    return AiCall(route, route_tuple, served_model, **fields)


# Call records -----------------------------------------------------------------------------------


def test_an_api_call_keeps_its_fields_and_is_frozen() -> None:
    call = ApiCall("places", "text_search", ok=False, error_code="places_unavailable", sku="pro")

    assert (call.provider, call.operation, call.sku, call.ok, call.error_code, call.count) == (
        "places",
        "text_search",
        "pro",
        False,
        "places_unavailable",
        1,
    )
    with pytest.raises(AttributeError):
        call.ok = True  # type: ignore[misc]


def test_ok_is_keyword_only_and_required() -> None:
    with pytest.raises(TypeError):
        ApiCall("hunter", "find", True)  # type: ignore[misc]
    with pytest.raises(TypeError):
        ApiCall("hunter", "find")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("arguments", "error", "field"),
    [
        ({"provider": "Hunter"}, ValueError, "provider"),
        ({"provider": "hunter.io"}, ValueError, "provider"),
        ({"provider": "a" * 33}, ValueError, "provider"),
        ({"provider": 7}, TypeError, "provider"),
        ({"operation": "a b"}, ValueError, "operation"),
        ({"operation": "find@x"}, ValueError, "operation"),
        ({"operation": ""}, ValueError, "operation"),
        ({"sku": "Pro"}, ValueError, "sku"),
        ({"sku": "pro plan"}, ValueError, "sku"),
        ({"ok": 1}, TypeError, "ok"),
        ({"ok": "true"}, TypeError, "ok"),
        ({"ok": True, "error_code": "hunter_failed"}, ValueError, "error_code"),
        ({"ok": False, "error_code": None}, ValueError, "error_code"),
        ({"ok": False, "error_code": "Hunter failed"}, ValueError, "error_code"),
        ({"ok": False, "error_code": "mac@example.com"}, ValueError, "error_code"),
        ({"count": -1}, ValueError, "count"),
        ({"count": 1_000_001}, ValueError, "count"),
        ({"count": True}, TypeError, "count"),
        ({"count": 1.0}, TypeError, "count"),
    ],
)
def test_a_bad_api_call_is_refused_naming_the_field_not_the_value(
    arguments: dict[str, Any], error: type[Exception], field: str
) -> None:
    fields: dict[str, Any] = {"provider": "hunter", "operation": "find", "ok": True}
    fields.update(arguments)
    provider, operation = fields.pop("provider"), fields.pop("operation")

    with pytest.raises(error) as raised:
        ApiCall(provider, operation, **fields)

    message = str(raised.value)
    assert message.startswith(field)
    for value in arguments.values():
        if isinstance(value, str) and value:
            assert value not in message


@pytest.mark.parametrize(
    ("arguments", "error", "field"),
    [
        ({"route": "Draft"}, ValueError, "route"),
        ({"route": "draft email"}, ValueError, "route"),
        ({"route_tuple": "draft@v3"}, ValueError, "route_tuple"),
        ({"route_tuple": "draft v3"}, ValueError, "route_tuple"),
        ({"route_tuple": "-draft"}, ValueError, "route_tuple"),
        ({"served_model": "claude@opus"}, ValueError, "served_model"),
        ({"served_model": "mac@example.com"}, ValueError, "served_model"),
        ({"served_model": "claude opus"}, ValueError, "served_model"),
        ({"served_model": "x" * 129}, ValueError, "served_model"),
        ({"input_tokens": -1}, ValueError, "input_tokens"),
        ({"output_tokens": True}, TypeError, "output_tokens"),
        ({"cache_read_tokens": 10**12 + 1}, ValueError, "cache_read_tokens"),
        ({"cache_write_tokens": 1.5}, TypeError, "cache_write_tokens"),
        ({"iterations": False}, TypeError, "iterations"),
        ({"cost_micros": -5}, ValueError, "cost_micros"),
        ({"batch": 0}, TypeError, "batch"),
        ({"refused": "no"}, TypeError, "refused"),
        ({"error_code": "Rate limited"}, ValueError, "error_code"),
    ],
)
def test_a_bad_ai_call_is_refused_naming_the_field_not_the_value(
    arguments: dict[str, Any], error: type[Exception], field: str
) -> None:
    with pytest.raises(error) as raised:
        ai(**arguments)

    message = str(raised.value)
    assert message.startswith(field)
    for value in arguments.values():
        if isinstance(value, str):
            assert value not in message


def test_an_ai_call_keeps_every_field() -> None:
    call = ai(
        cache_read_tokens=5,
        cache_write_tokens=6,
        iterations=2,
        batch=True,
        refused=True,
        error_code="ai_refused",
    )

    assert (call.route, call.route_tuple, call.served_model) == (
        "draft_email",
        "draft_email:v3:claude-opus-5-5",
        "claude-opus-5-5",
    )
    assert (
        call.input_tokens,
        call.output_tokens,
        call.cache_read_tokens,
        call.cache_write_tokens,
        call.iterations,
        call.batch,
        call.cost_micros,
        call.refused,
        call.error_code,
    ) == (1_200, 300, 5, 6, 2, True, 1_234, True, "ai_refused")
    assert ai(cost_micros=10**12).cost_micros == 10**12


def test_hunters_credits_are_stored_doubled() -> None:
    assert dict(UNIT_WEIGHTS) == {
        ("hunter", "find"): 2,
        ("hunter", "domain_search"): 2,
        ("hunter", "verify"): 1,
    }
    assert ApiCall("hunter", "find", ok=True).units == 2
    assert ApiCall("hunter", "domain_search", ok=True).units == 2
    assert ApiCall("hunter", "verify", ok=True).units == 1
    assert ApiCall("hunter", "find", ok=True, count=3).units == 6
    assert ApiCall("hunter", "find", ok=True, count=0).units == 0
    assert ApiCall("hunter", "account", ok=True).units == 1  # unweighted: 1 per count
    assert ApiCall("places", "text_search", ok=True, sku="pro", count=4).units == 4
    assert ApiCall("telegram", "send_message", ok=False, error_code="x").units == 1


# Months -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "month", "start", "end"),
    [
        (
            datetime(2026, 11, 30, 23, 59, 59, 999_999, tzinfo=UTC),
            "2026-11",
            datetime(2026, 11, 1, tzinfo=UTC),
            datetime(2026, 12, 1, tzinfo=UTC),
        ),
        (
            datetime(2026, 12, 1, tzinfo=UTC),
            "2026-12",
            datetime(2026, 12, 1, tzinfo=UTC),
            datetime(2027, 1, 1, tzinfo=UTC),
        ),
        (
            datetime(2026, 12, 31, 23, 59, 59, 999_999, tzinfo=UTC),
            "2026-12",
            datetime(2026, 12, 1, tzinfo=UTC),
            datetime(2027, 1, 1, tzinfo=UTC),
        ),
        (
            datetime(2027, 1, 1, tzinfo=UTC),
            "2027-01",
            datetime(2027, 1, 1, tzinfo=UTC),
            datetime(2027, 2, 1, tzinfo=UTC),
        ),
        (  # 00:30 on 1 December in UTC+1 is still November in UTC
            datetime(2026, 12, 1, 0, 30, tzinfo=timezone(timedelta(hours=1))),
            "2026-11",
            datetime(2026, 11, 1, tzinfo=UTC),
            datetime(2026, 12, 1, tzinfo=UTC),
        ),
    ],
)
def test_the_month_is_the_utc_calendar_month(
    now: datetime, month: str, start: datetime, end: datetime
) -> None:
    assert month_of(now) == month
    assert month_bounds(now) == (start, end)


def test_a_naive_now_is_refused() -> None:
    with pytest.raises(ValueError, match="aware"):
        month_of(datetime(2026, 11, 17, 9, 0))
    with pytest.raises(ValueError, match="aware"):
        month_bounds(datetime(2026, 11, 17, 9, 0))


# Usage --------------------------------------------------------------------------------------------


def test_usage_lines_are_sorted_and_summed_by_provider_or_sku() -> None:
    found = usage(
        line("places", "text_search", 7, "pro"),
        line("hunter", "verify", 1),
        line("places", "text_search", 5, "enterprise"),
        line("hunter", "find", 4),
        line("places", "details", 2, "enterprise"),
        line("places", "text_search", 3),
    )

    assert [(item.provider, item.operation, item.sku) for item in found.api] == [
        ("hunter", "find", None),
        ("hunter", "verify", None),
        ("places", "details", "enterprise"),
        ("places", "text_search", None),
        ("places", "text_search", "enterprise"),
        ("places", "text_search", "pro"),
    ]
    assert found.units("hunter") == 5
    assert found.units("places") == 17
    assert found.units("places", "enterprise") == 7
    assert found.units("places", "pro") == 7
    assert found.units("prospeo") == 0


def test_limits_must_name_every_quota() -> None:
    with pytest.raises(ValueError):
        Limits({"hunter": 100}, BUDGET, 80)
    with pytest.raises(ValueError):
        Limits({**DEFAULT_QUOTAS, "hunter": -1}, BUDGET, 80)
    with pytest.raises(ValueError):
        Limits(DEFAULT_QUOTAS, BUDGET, 101)


# allow --------------------------------------------------------------------------------------------

QUOTA_NAMES = [spec.name for spec in QUOTAS]


def quota_usage(name: str, units: int) -> Usage:
    spec = quota_spec(name)
    return usage(line(spec.provider, "call", units, spec.sku))


@pytest.mark.parametrize("name", QUOTA_NAMES)
@pytest.mark.parametrize(("percent", "allowed"), [(79, True), (80, True), (99, True), (100, False)])
def test_blockable_work_is_refused_only_at_100_percent_of_its_quota(
    name: str, percent: int, allowed: bool
) -> None:
    limit = 2_000
    found = quota_usage(name, limit * percent // 100)

    assert allow(BudgetClass.BLOCKABLE, found, limits(**{name: limit}), quota=name) is allowed
    assert allow(BudgetClass.ESSENTIAL, found, limits(**{name: limit}), quota=name) is True


@pytest.mark.parametrize(("percent", "allowed"), [(79, True), (80, True), (99, True), (100, False)])
def test_blockable_work_is_refused_only_at_100_percent_of_the_ai_budget(
    percent: int, allowed: bool
) -> None:
    found = usage(ai_cost=BUDGET * percent // 100)

    assert allow(BudgetClass.BLOCKABLE, found, limits()) is allowed
    for name in QUOTA_NAMES:  # the AI budget stops every blockable quota's work too
        expected = allowed and limits().quotas[name] > 0
        assert allow(BudgetClass.BLOCKABLE, found, limits(), quota=name) is expected


def test_essential_work_is_allowed_even_at_150_percent_of_everything() -> None:
    found = usage(
        line("hunter", "find", 300),
        line("prospeo", "find", 300),
        line("places", "text_search", 3_000, "enterprise"),
        ai_cost=BUDGET * 3 // 2,
    )

    assert allow(BudgetClass.ESSENTIAL, found, limits()) is True
    for name in QUOTA_NAMES:
        assert allow(BudgetClass.ESSENTIAL, found, limits(), quota=name) is True
        assert allow(BudgetClass.BLOCKABLE, found, limits(), quota=name) is False
    assert allow("essential", found, limits()) is True  # type: ignore[arg-type]


@pytest.mark.parametrize("name", QUOTA_NAMES)
def test_a_quota_of_zero_always_refuses_blockable_work(name: str) -> None:
    assert allow(BudgetClass.BLOCKABLE, usage(), limits(**{name: 0}), quota=name) is False
    assert allow(BudgetClass.ESSENTIAL, usage(), limits(**{name: 0}), quota=name) is True


def test_an_ai_budget_of_zero_always_refuses_blockable_work() -> None:
    assert allow(BudgetClass.BLOCKABLE, usage(), limits(ai_budget=0)) is False
    assert allow(BudgetClass.ESSENTIAL, usage(), limits(ai_budget=0)) is True


def test_a_quota_counts_only_its_own_provider_and_sku() -> None:
    found = usage(line("places", "text_search", 5_000, "pro"), line("hunter", "verify", 99))

    assert allow(BudgetClass.BLOCKABLE, found, limits(), quota="places_enterprise") is True
    assert allow(BudgetClass.BLOCKABLE, found, limits(places_pro=5_000), quota="places_pro") is (
        False
    )
    assert allow(BudgetClass.BLOCKABLE, found, limits(), quota="hunter") is True  # 99 < 100


def test_an_unknown_quota_name_raises_unknown_quota() -> None:
    with pytest.raises(UnknownQuota) as raised:
        allow(BudgetClass.BLOCKABLE, usage(), limits(), quota="hunter_io")

    assert isinstance(raised.value, DomainError)
    assert raised.value.code == "unknown_quota"
    with pytest.raises(UnknownQuota):
        quota_spec("places")


def test_an_unknown_budget_class_is_refused() -> None:
    with pytest.raises(ValueError):
        allow("urgent", usage(), limits())  # type: ignore[arg-type]


# assess ---------------------------------------------------------------------------------------


def test_the_quotas_are_the_specified_five_in_order() -> None:
    assert [
        (spec.name, spec.label, spec.provider, spec.sku, spec.setting, spec.scale, spec.unit)
        for spec in QUOTAS
    ] == [
        ("hunter", "Hunter", "hunter", None, "quota_hunter_credits", 2, "credits"),
        ("prospeo", "Prospeo", "prospeo", None, "quota_prospeo_credits", 1, "credits"),
        (
            "places_enterprise",
            "Places Enterprise",
            "places",
            "enterprise",
            "quota_places_enterprise",
            1,
            "requests",
        ),
        ("places_pro", "Places Pro", "places", "pro", "quota_places_pro", 1, "requests"),
        (
            "job_aggregator",
            "Job aggregator",
            "job_aggregator",
            None,
            "quota_job_aggregator",
            1,
            "requests",
        ),
    ]
    assert [spec.stops for spec in QUOTAS] == [
        "enrichment",
        "enrichment",
        "discovery",
        "discovery",
        "discovery",
    ]
    assert display_scale("hunter") == 2
    assert display_scale("places") == 1
    assert display_scale("telegram") == 1


def test_assess_gives_each_quota_its_state() -> None:
    found = assess(
        usage(line("hunter", "find", 100), line("places", "text_search", 999, "enterprise")),
        limits(),
    )

    assert [(item.spec.name, item.used, item.limit, item.state) for item in found.quotas] == [
        ("hunter", 100, 100, QuotaState.REACHED),
        ("prospeo", 0, 100, QuotaState.OK),
        ("places_enterprise", 999, 1_000, QuotaState.OK),
        ("places_pro", 0, 0, QuotaState.OFF),
        ("job_aggregator", 0, 0, QuotaState.OFF),
    ]
    assert found.stopped == ("Hunter quota reached",)
    assert found.month == MONTH


@pytest.mark.parametrize(
    ("spent", "budget", "pct", "state"),
    [
        (0, 0, 80, AiState.OFF),
        (5, 0, 80, AiState.OFF),
        (15_999_999, BUDGET, 80, AiState.OK),
        (16_000_000, BUDGET, 80, AiState.WARNING),
        (19_999_999, BUDGET, 80, AiState.WARNING),
        (20_000_000, BUDGET, 80, AiState.REACHED),
        (30_000_000, BUDGET, 80, AiState.REACHED),
        (19_999_999, BUDGET, 100, AiState.OK),
        (200_000, BUDGET, 1, AiState.WARNING),
    ],
)
def test_assess_gives_the_ai_spend_its_state(
    spent: int, budget: int, pct: int, state: AiState
) -> None:
    found = assess(usage(ai_cost=spent, ai_calls=3), limits(ai_budget=budget, ai_pct=pct))

    assert found.ai == AiLine(spent, budget, pct, 3, state)
    assert ("AI budget reached" in found.stopped) is (state is AiState.REACHED)


def test_the_ai_budget_is_listed_first_among_the_stopped() -> None:
    found = assess(
        usage(
            line("job_aggregator", "search", 10),
            line("prospeo", "find", 100),
            ai_cost=BUDGET,
        ),
        limits(job_aggregator=10),
    )

    assert found.stopped == (
        "AI budget reached",
        "Prospeo quota reached",
        "Job aggregator quota reached",
    )


# thresholds -------------------------------------------------------------------------------------


def test_a_warning_gives_the_80_percent_notice() -> None:
    found = thresholds(assess(usage(ai_cost=16_000_000), limits()))

    assert found == (
        Threshold(
            "ai_budget_warning",
            "ai_budget:2026-11",
            "AI spend at 80% of $20: $16 spent in 2026-11",
        ),
    )


def test_a_reached_budget_gives_both_ai_notices_and_each_reached_quota_its_own() -> None:
    found = thresholds(
        assess(
            usage(
                line("hunter", "find", 100),
                line("places", "text_search", 1_000, "enterprise"),
                ai_cost=20_050_000,
            ),
            limits(),
        )
    )

    assert found == (
        Threshold(
            "ai_budget_warning",
            "ai_budget:2026-11",
            "AI spend at 80% of $20: $20.05 spent in 2026-11",
        ),
        Threshold(
            "ai_budget_reached",
            "ai_budget:2026-11",
            "AI budget reached: discovery, enrichment and re-checks stopped "
            "($20.05 of $20 spent in 2026-11)",
        ),
        Threshold(
            "quota_reached",
            "hunter:2026-11",
            "Hunter quota reached: enrichment stopped (50 of 50 credits used in 2026-11)",
        ),
        Threshold(
            "quota_reached",
            "places_enterprise:2026-11",
            "Places Enterprise quota reached: discovery stopped "
            "(1,000 of 1,000 requests used in 2026-11)",
        ),
    )


def test_nothing_reached_and_limits_of_zero_give_no_notice() -> None:
    assert thresholds(assess(usage(ai_cost=15_999_999), limits())) == ()
    assert thresholds(assess(usage(), limits(ai_budget=0))) == ()  # off is quiet
    assert thresholds(assess(usage(line("places", "x", 3, "pro")), limits())) == ()


def test_a_threshold_subject_names_its_month() -> None:
    december = usage(ai_cost=BUDGET, month="2026-12")

    subjects = {threshold.subject for threshold in thresholds(assess(december, limits()))}

    assert subjects == {"ai_budget:2026-12"}


def test_the_quota_line_type_is_frozen() -> None:
    found = assess(usage(), limits()).quotas[0]

    assert isinstance(found, QuotaLine)
    with pytest.raises(AttributeError):
        found.used = 5  # type: ignore[misc]
    assert replace(found, used=5).used == 5


# Formatting -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("micros", "text"),
    [
        (0, "$0"),
        (20_000_000, "$20"),
        (16_050_000, "$16.05"),
        (16_059_999, "$16.05"),
        (16_000_001, "$16.00"),
        (999_999, "$0.99"),
        (1_234_000_000, "$1,234"),
        (100_000_000, "$100"),
    ],
)
def test_dollars_shows_whole_dollars_or_truncated_cents(micros: int, text: str) -> None:
    assert dollars(micros) == text


@pytest.mark.parametrize(
    ("units", "scale", "text"),
    [
        (5, 2, "2.5"),
        (100, 2, "50"),
        (1, 2, "0.5"),
        (0, 2, "0"),
        (2_001, 2, "1,000.5"),
        (1_000, 1, "1,000"),
        (12_345_678, 1, "12,345,678"),
    ],
)
def test_quantity_shows_units_in_the_quota_unit_with_halves(
    units: int, scale: int, text: str
) -> None:
    assert quantity(units, scale) == text
