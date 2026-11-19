"""``nextron.domain.recheck`` (Story 8.7): which re-check a Lead is due, at the day boundaries,
and which Primary Contact Eligibility still allows one."""

from datetime import UTC, datetime, timedelta

import pytest

from nextron.domain.budget import BudgetClass
from nextron.domain.eligibility import Decision, Reason, ReasonKind
from nextron.domain.recheck import (
    MODE_FIELD,
    RECHECK_LEAD_BUDGET_CLASS,
    RECHECK_LEAD_JOB,
    RECHECK_LEADS_JOB,
    RECHECK_LEADS_PERIOD,
    RecheckMode,
    eligibility_allows,
    mode_due,
)

NOW = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)
DAY = timedelta(days=1)


def ago(days: float) -> datetime:
    return NOW - days * DAY


def test_the_constants() -> None:
    assert (RECHECK_LEADS_JOB, RECHECK_LEAD_JOB, MODE_FIELD) == (
        "recheck_leads",
        "recheck_lead",
        "mode",
    )
    assert RECHECK_LEADS_PERIOD == timedelta(hours=24)
    assert RECHECK_LEAD_BUDGET_CLASS is BudgetClass.BLOCKABLE
    assert [mode.value for mode in RecheckMode] == ["full", "jobs"]


@pytest.mark.parametrize(
    ("last_full", "last_hiring", "expected"),
    [
        (None, None, RecheckMode.FULL),  # never crawled
        (None, ago(1), RecheckMode.FULL),  # only a jobs check: no full crawl yet
        (ago(31), ago(1), RecheckMode.FULL),
        (ago(30), ago(1), RecheckMode.FULL),  # exactly 30 days is due
        (ago(30) + timedelta(seconds=1), ago(1), None),  # a second short
        (ago(10), None, RecheckMode.JOBS),  # never checked for hiring
        (ago(10), ago(8), RecheckMode.JOBS),
        (ago(10), ago(7), RecheckMode.JOBS),  # exactly 7 days is due
        (ago(10), ago(7) + timedelta(seconds=1), None),
        (ago(10), ago(2), None),
    ],
)
def test_mode_due_at_the_boundaries(
    last_full: datetime | None, last_hiring: datetime | None, expected: RecheckMode | None
) -> None:
    assert mode_due(last_full, last_hiring, NOW, 30, 7) is expected


def test_mode_due_reads_the_given_periods() -> None:
    assert mode_due(ago(2), ago(1), NOW, 1, 7) is RecheckMode.FULL
    assert mode_due(ago(2), ago(1), NOW, 3, 1) is RecheckMode.JOBS
    assert mode_due(ago(2), ago(1), NOW, 3, 2) is None


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ((datetime(2026, 11, 1), None, NOW, 30, 7), "last_full"),
        ((None, datetime(2026, 11, 1), NOW, 30, 7), "last_hiring"),
        ((None, None, datetime(2026, 11, 18), 30, 7), "now"),
        ((None, None, None, 30, 7), "now"),
        ((None, None, NOW, 0, 7), "full_days"),
        ((None, None, NOW, 30, 0), "jobs_days"),
        ((None, None, NOW, True, 7), "full_days"),
        ((None, None, NOW, 30, 7.5), "jobs_days"),
    ],
)
def test_mode_due_refuses_naive_times_and_bad_periods(
    arguments: tuple[object, ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        mode_due(*arguments)  # type: ignore[arg-type]


def decision(*kinds: ReasonKind) -> Decision:
    reasons = tuple(Reason(kind, f"{kind.value} text") for kind in kinds)
    if not reasons:
        return Decision(eligible=True, basis_rule="us_basis", reasons=())
    return Decision(eligible=False, basis_rule=None, reasons=reasons)


def test_eligibility_allows_an_eligible_or_recontact_only_primary() -> None:
    assert eligibility_allows(decision()) is True
    assert eligibility_allows(decision(ReasonKind.RECONTACT_NOT_ALLOWED)) is True


OTHER_KINDS = [
    kind
    for kind in ReasonKind
    if kind not in (ReasonKind.RECONTACT_NOT_ALLOWED, ReasonKind.NOT_NOW_UNTIL)
]


@pytest.mark.parametrize("kind", OTHER_KINDS)
def test_eligibility_refuses_any_other_reason(kind: ReasonKind) -> None:
    assert eligibility_allows(decision(kind)) is False


def test_eligibility_refuses_recontact_with_another_reason() -> None:
    kinds = sorted(
        (ReasonKind.RECONTACT_NOT_ALLOWED, ReasonKind.UK_LEGAL_FORM_INELIGIBLE),
        key=list(ReasonKind).index,
    )
    assert eligibility_allows(decision(*kinds)) is False


def test_eligibility_allows_takes_only_a_decision() -> None:
    with pytest.raises(TypeError, match="Decision"):
        eligibility_allows("eligible")  # type: ignore[arg-type]
