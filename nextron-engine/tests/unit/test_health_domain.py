"""``nextron.domain.health``, ``nextron.domain.readiness`` and the health parts of
``nextron.domain.job`` and ``nextron.domain.budget`` (Story 1.15): the worker rule at 119, 120,
-5 and -300 seconds, every ``HealthIssue`` and ``IssueSource`` refusal (naming the field, never
the value), the registry's rules, ``job_group_runs``'s order, the ``JobGroup`` labels and the
``JobType.group`` check, ``budget_issues`` (the Budget row) and ``tally``."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from nextron import startup_checks
from nextron.domain.budget import ApiUse, Limits, Usage, assess, budget_issues
from nextron.domain.health import (
    HEARTBEAT_FRESH,
    GroupRuns,
    HealthIssue,
    HealthIssues,
    IssueSource,
    LastRun,
    job_group_runs,
    worker_running,
)
from nextron.domain.job import JobGroup, JobType, Lane
from nextron.domain.readiness import CheckResult, Status, tally

NOW = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
ULID = "01JD3QZ8X5V9K2M7N4P6R8T0WY"
SECRET = "mac@example.com"  # a value no refusal may repeat


# The worker rule --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds_ago", "running"),
    [(0, True), (119, True), (120, False), (-5, True), (-119, True), (-120, False), (-300, False)],
)
def test_the_worker_runs_only_while_its_beat_is_under_2_minutes_away(
    seconds_ago: int, running: bool
) -> None:
    assert worker_running(NOW - timedelta(seconds=seconds_ago), NOW) is running


def test_the_threshold_is_2_minutes() -> None:
    assert timedelta(minutes=2) == HEARTBEAT_FRESH


# Health issues ----------------------------------------------------------------------------------


def test_a_health_issue_holds_its_fields() -> None:
    issue = HealthIssue("job_dead", ULID, "Job dead: send_alert x", detail="Last error: x")

    assert (issue.kind, issue.subject, issue.text, issue.detail) == (
        "job_dead",
        ULID,
        "Job dead: send_alert x",
        "Last error: x",
    )
    assert HealthIssue("quota_reached", "hunter:2026-11", "Hunter quota reached").detail is None
    with pytest.raises(AttributeError):
        issue.text = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("fields", "error", "named"),
    [
        ({"kind": 1}, TypeError, "kind"),
        ({"kind": "Job_dead"}, ValueError, "kind"),
        ({"kind": "job dead"}, ValueError, "kind"),
        ({"kind": "a" * 33}, ValueError, "kind"),
        ({"kind": SECRET}, ValueError, "kind"),
        ({"subject": None}, TypeError, "subject"),
        ({"subject": ""}, ValueError, "subject"),
        ({"subject": ULID.lower() + "@"}, ValueError, "subject"),
        ({"subject": SECRET}, ValueError, "subject"),
        ({"subject": "x" * 65}, ValueError, "subject"),
        ({"subject": "Hunter"}, ValueError, "subject"),
        ({"text": None}, TypeError, "text"),
        ({"text": b"bytes"}, TypeError, "text"),
        ({"text": ""}, ValueError, "text"),
        ({"text": " \n\t"}, ValueError, "text"),
        ({"detail": 5}, TypeError, "detail"),
        ({"detail": b"bytes"}, TypeError, "detail"),
    ],
)
def test_a_health_issue_refuses_bad_fields_naming_the_field_never_the_value(
    fields: dict[str, Any], error: type[Exception], named: str
) -> None:
    values: dict[str, Any] = {"kind": "job_dead", "subject": ULID, "text": "Job dead", **fields}

    with pytest.raises(error) as caught:
        HealthIssue(**values)

    assert named in str(caught.value)
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize("subject", [ULID, "ai_budget:2026-11", "jobqueue", "a", "x" * 64])
def test_a_subject_is_a_ulid_or_a_token(subject: str) -> None:
    assert HealthIssue("kind", subject, "text").subject == subject


def test_an_issue_source_holds_its_name_and_reader() -> None:
    def read() -> tuple[HealthIssue, ...]:
        return ()

    source = IssueSource("jobqueue", read)

    assert (source.name, source.read) == ("jobqueue", read)


@pytest.mark.parametrize(
    ("name", "read", "error", "named"),
    [
        (None, tuple, TypeError, "name"),
        ("Jobqueue", tuple, ValueError, "name"),
        ("job queue", tuple, ValueError, "name"),
        ("", tuple, ValueError, "name"),
        ("a" * 33, tuple, ValueError, "name"),
        (SECRET, tuple, ValueError, "name"),
        ("jobqueue", None, TypeError, "read"),
        ("jobqueue", (), TypeError, "read"),
    ],
)
def test_an_issue_source_refuses_a_bad_name_or_reader(
    name: Any, read: Any, error: type[Exception], named: str
) -> None:
    with pytest.raises(error) as caught:
        IssueSource(name, read)

    assert named in str(caught.value)
    assert SECRET not in str(caught.value)


# The registry -----------------------------------------------------------------------------------


def test_the_registry_keeps_sources_in_order_until_frozen() -> None:
    registry = HealthIssues()
    first, second = IssueSource("jobqueue", tuple), IssueSource("budget", tuple)

    registry.register_source(first)
    registry.register_source(second)

    assert not registry.frozen
    assert registry.sources == (first, second)
    registry.freeze()
    assert registry.frozen
    with pytest.raises(RuntimeError):
        registry.register_source(IssueSource("late", tuple))
    assert registry.sources == (first, second)


def test_the_registry_refuses_a_repeated_name_and_anything_but_a_source() -> None:
    registry = HealthIssues()
    registry.register_source(IssueSource("jobqueue", tuple))

    with pytest.raises(ValueError, match="jobqueue"):
        registry.register_source(IssueSource("jobqueue", list))
    with pytest.raises(TypeError):
        registry.register_source(("budget", tuple))  # type: ignore[arg-type]
    assert [source.name for source in registry.sources] == ["jobqueue"]


def test_the_registry_hands_out_a_copy() -> None:
    registry = HealthIssues()
    registry.register_source(IssueSource("jobqueue", tuple))

    sources = registry.sources

    assert isinstance(sources, tuple)
    assert registry.sources == sources and registry.sources is not sources


# Last successful runs ---------------------------------------------------------------------------


def test_groups_come_in_job_group_order_with_types_in_registration_order() -> None:
    t1, t2 = NOW - timedelta(hours=2), NOW - timedelta(minutes=5)
    pairs = [
        ("send_alert", JobGroup.OTHER),
        ("backup_db", JobGroup.BACKUP),
        ("send_due_messages", JobGroup.SENDING),
        ("discover_places", JobGroup.DISCOVERY),
        ("reconcile_sends", JobGroup.SENDING),
    ]

    groups = job_group_runs(pairs, {"send_alert": t1, "reconcile_sends": t2, "unknown_type": t1})

    assert groups == (
        GroupRuns(JobGroup.DISCOVERY, (LastRun("discover_places", None),)),
        GroupRuns(
            JobGroup.SENDING,
            (LastRun("send_due_messages", None), LastRun("reconcile_sends", t2)),
        ),
        GroupRuns(JobGroup.BACKUP, (LastRun("backup_db", None),)),
        GroupRuns(JobGroup.OTHER, (LastRun("send_alert", t1),)),
    )


def test_no_job_types_is_no_groups() -> None:
    assert job_group_runs([], {"send_alert": NOW}) == ()


def test_the_job_groups_and_their_labels() -> None:
    assert [(group.value, group.label) for group in JobGroup] == [
        ("discovery", "Discovery"),
        ("enrichment", "Enrichment"),
        ("recheck", "Re-check"),
        ("sending", "Sending"),
        ("reply_check", "Reply check"),
        ("unsubscribe_poll", "Unsubscribe Endpoint poll"),
        ("backup", "Backup"),
        ("other", "Other"),
    ]


def job_type(**changes: Any) -> JobType:
    values: dict[str, Any] = {
        "name": "send_alert",
        "default_lane": Lane.REALTIME,
        "lanes": frozenset({Lane.REALTIME}),
        "priority": 90,
        "max_runtime": timedelta(minutes=3),
        "retry_limit": 5,
        "handler": lambda job: None,
        **changes,
    }
    return JobType(**values)


def test_a_job_type_is_in_the_other_group_unless_it_names_one() -> None:
    assert job_type().group is JobGroup.OTHER
    assert job_type(group=JobGroup.SENDING).group is JobGroup.SENDING


@pytest.mark.parametrize("group", ["sending", None, 1])
def test_a_job_type_refuses_a_group_that_is_not_a_job_group(group: object) -> None:
    with pytest.raises(TypeError, match="group"):
        job_type(group=group)


# The Budget row ---------------------------------------------------------------------------------

QUOTA_LIMITS = {
    "hunter": 100,  # 50 credits, stored doubled
    "prospeo": 100,
    "places_enterprise": 1_000,
    "places_pro": 0,
    "job_aggregator": 0,
}


def issues_for(*lines: ApiUse, spent: int = 0, budget: int = 20_000_000) -> list[tuple[str, ...]]:
    usage = Usage("2026-11", lines, 1 if spent else 0, spent)
    found = budget_issues(assess(usage, Limits(QUOTA_LIMITS, budget, 80)))
    assert all(isinstance(issue, HealthIssue) and issue.detail is None for issue in found)
    return [(issue.kind, issue.subject, issue.text) for issue in found]


def test_ai_spend_at_the_warning_percentage_is_a_warning_issue() -> None:
    assert issues_for(spent=16_000_000) == [
        ("ai_budget_warning", "ai_budget:2026-11", "AI spend at 80% of $20: $16 spent in 2026-11")
    ]


def test_ai_spend_below_the_warning_percentage_is_no_issue() -> None:
    assert issues_for(spent=15_990_000) == []


def test_the_ai_budget_reached_shows_only_the_reached_issue() -> None:
    assert issues_for(spent=21_000_000) == [
        (
            "ai_budget_reached",
            "ai_budget:2026-11",
            "AI budget reached: discovery, enrichment and re-checks stopped ($21 of $20 spent "
            "in 2026-11)",
        )
    ]


def test_a_quota_reached_is_an_issue() -> None:
    assert issues_for(ApiUse("hunter", "find", None, 50, 0, 100)) == [
        (
            "quota_reached",
            "hunter:2026-11",
            "Hunter quota reached: enrichment stopped (50 of 50 credits used in 2026-11)",
        )
    ]


def test_a_limit_of_0_is_never_an_issue() -> None:
    places_pro = ApiUse("places", "text_search", "pro", 3, 0, 3)  # quota 0: off

    assert issues_for(places_pro) == []
    assert issues_for(spent=5_000_000, budget=0) == []  # an AI budget of $0: off


def test_every_budget_issue_comes_in_threshold_order() -> None:
    hunter = ApiUse("hunter", "find", None, 50, 0, 100)
    prospeo = ApiUse("prospeo", "find", None, 100, 0, 100)

    assert [kind_subject[:2] for kind_subject in issues_for(hunter, prospeo, spent=20_000_000)] == [
        ("ai_budget_reached", "ai_budget:2026-11"),
        ("quota_reached", "hunter:2026-11"),
        ("quota_reached", "prospeo:2026-11"),
    ]


# tally ------------------------------------------------------------------------------------------


def test_tally_counts_ok_warn_and_fail() -> None:
    results = [
        CheckResult("a", Status.OK, "fine"),
        CheckResult("b", Status.FAIL, "broken"),
        CheckResult("c", Status.OK, "fine"),
        CheckResult("d", Status.WARN, "later"),
        CheckResult("e", Status.FAIL, "broken"),
        CheckResult("f", Status.FAIL, "broken"),
    ]

    assert tally(results) == (2, 1, 3)
    assert tally([]) == (0, 0, 0)
    assert tally(iter(results)) == (2, 1, 3)  # any iterable, read once


def test_the_startup_checks_types_are_the_readiness_types() -> None:
    assert startup_checks.CheckResult is CheckResult
    assert startup_checks.Status is Status
