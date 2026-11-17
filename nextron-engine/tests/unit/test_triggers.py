"""The trigger enum, schedules (``Every``, ``DailyRun`` and ``DailyAt``) and registry
(``nextron/jobs/triggers.py``, AD-10).

The enum is checked against a hard-coded copy of AD-10's closed list and against the spine's
AD-10 table itself, parsed from ``ARCHITECTURE-SPINE.md`` (skipped only when the planning
folder isn't next to the engine).
"""

import dataclasses
import re
from datetime import UTC, time, timedelta

import pytest

from nextron.domain.job import Job, JobType, JobTypes, Lane, UnknownJobType, is_job_type_name
from nextron.jobs.triggers import DailyAt, DailyRun, Every, Trigger, Triggers, TriggerSpec
from tests.contracts.support import ENGINE_ROOT

AD10_TRIGGERS = (
    "sync_mailbox",
    "send_due_messages",
    "reconcile_sends",
    "poll_unsubscribes",
    "poll_ai_batches",
    "expire_approvals",
    "refresh_due_queue",
    "check_domains",
    "recompute_tiers",
    "recheck_leads",
    "recontact_due",
    "apply_retention",
    "purge_place_coords",
    "purge_page_text",
    "submit_bulk_ai",
    "daily_selection",
    "backup_db",
    "restore_test",
    "check_recovery",
)
SPINE = (
    ENGINE_ROOT.parent
    / "_bmad-output"
    / "planning-artifacts"
    / "architecture"
    / "architecture-nextron-outreach-engine-2026-11-14"
    / "ARCHITECTURE-SPINE.md"
)
MINUTE = timedelta(minutes=1)


def noop(job: Job) -> None:
    del job


def job_types(*names: str, frozen: bool = True) -> JobTypes:
    registry = JobTypes()
    for name in names:
        registry.register_job_type(
            JobType(name, Lane.REALTIME, frozenset({Lane.REALTIME}), 50, MINUTE, 0, noop)
        )
    if frozen:
        registry.freeze()
    return registry


# The enum ---------------------------------------------------------------------------------------


def test_the_enum_is_ad10s_closed_list_in_table_order() -> None:
    assert tuple(trigger.value for trigger in Trigger) == AD10_TRIGGERS
    assert all(trigger.name == trigger.value.upper() for trigger in Trigger)


def spine_triggers(text: str) -> tuple[str, ...]:
    """The trigger names in the first column of AD-10's trigger table, in order."""
    section = text.split("### AD-10:", 1)[1].split("\n### ", 1)[0]
    lines = [line.strip() for line in section.splitlines()]
    start = lines.index("| Trigger | When |")
    names: list[str] = []
    for line in lines[start + 2 :]:  # past the header and its |---| rule
        if not line.startswith("|"):
            break
        first_cell = line.split("|")[1]
        names += re.findall(r"`([a-z_]+)`", first_cell)
    return tuple(names)


def test_the_enum_matches_the_spines_ad10_table_exactly() -> None:
    if not SPINE.is_file():
        pytest.skip(f"the architecture spine is not at {SPINE}")

    assert spine_triggers(SPINE.read_text(encoding="utf-8")) == tuple(t.value for t in Trigger)


def test_the_spine_parser_reads_every_name_of_a_shared_row() -> None:
    text = (
        "### AD-10: Jobs\n"
        "    | Trigger | When |\n"
        "    |---|---|\n"
        "    | `sync_mailbox` (per inbox) | every 2 min |\n"
        "    | `expire_approvals`, `refresh_due_queue` | every 15 min |\n"
        "\n"
        "    | `not_a_trigger` | after the table |\n"
        "### AD-11: Time\n"
    )

    assert spine_triggers(text) == ("sync_mailbox", "expire_approvals", "refresh_due_queue")


def test_every_trigger_names_a_valid_job_type() -> None:
    assert all(is_job_type_name(trigger.value) for trigger in Trigger)


# Every ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "period", [MINUTE, timedelta(minutes=2), timedelta(hours=24), timedelta(days=31)]
)
def test_every_takes_whole_seconds_from_1_minute_to_31_days(period: timedelta) -> None:
    schedule = Every(period)

    assert (schedule.period, schedule.at_startup, schedule.grace) == (period, False, period)


def test_every_can_fire_at_startup_and_its_grace_is_its_period() -> None:
    schedule = Every(timedelta(minutes=2), at_startup=True)

    assert schedule.at_startup is True
    assert schedule.grace == timedelta(minutes=2)


@pytest.mark.parametrize(
    "period",
    [
        timedelta(seconds=30),
        timedelta(seconds=59),
        timedelta(days=31, seconds=1),
        timedelta(days=32),
        timedelta(minutes=1, microseconds=1),
        timedelta(0),
        timedelta(minutes=-1),
    ],
)
def test_every_refuses_a_period_out_of_range_or_not_whole_seconds(period: timedelta) -> None:
    with pytest.raises(ValueError, match="whole seconds from 1 minute to 31 days"):
        Every(period)


@pytest.mark.parametrize("period", [60, 60.0, "PT1M", None])
def test_every_refuses_a_period_that_is_not_a_timedelta(period: object) -> None:
    with pytest.raises(TypeError, match="timedelta"):
        Every(period)  # type: ignore[arg-type]


@pytest.mark.parametrize("at_startup", [1, 0, "yes", None])
def test_every_refuses_an_at_startup_that_is_not_a_bool(at_startup: object) -> None:
    with pytest.raises(TypeError, match="at_startup"):
        Every(MINUTE, at_startup=at_startup)  # type: ignore[arg-type]


def test_every_takes_at_startup_only_by_keyword_and_is_frozen() -> None:
    with pytest.raises(TypeError):
        Every(MINUTE, True)  # type: ignore[misc]
    schedule = Every(MINUTE)
    with pytest.raises(dataclasses.FrozenInstanceError):
        schedule.period = timedelta(minutes=5)  # type: ignore[misc]


def test_every_has_no_active_by_default_and_takes_a_callable_by_keyword() -> None:
    def recovery_on() -> bool:
        return True

    assert Every(MINUTE).active is None
    schedule = Every(MINUTE, at_startup=True, active=recovery_on)
    assert (schedule.period, schedule.at_startup, schedule.active) == (MINUTE, True, recovery_on)
    assert schedule.grace == MINUTE
    with pytest.raises(TypeError):
        Every(MINUTE, False, recovery_on)  # type: ignore[misc]


@pytest.mark.parametrize("active", [True, "recovery_mode", 1])
def test_every_refuses_an_active_that_is_not_none_or_callable(active: object) -> None:
    with pytest.raises(TypeError, match="active"):
        Every(MINUTE, active=active)  # type: ignore[arg-type]


# TriggerSpec ------------------------------------------------------------------------------------


def test_a_spec_enqueues_the_job_type_with_the_triggers_own_name() -> None:
    spec = TriggerSpec(Trigger.SEND_DUE_MESSAGES, Every(MINUTE))

    assert spec.job_type == "send_due_messages"
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.trigger = Trigger.BACKUP_DB  # type: ignore[misc]


@pytest.mark.parametrize(
    ("trigger", "schedule"),
    [("send_due_messages", Every(MINUTE)), (Trigger.SEND_DUE_MESSAGES, MINUTE)],
)
def test_a_spec_refuses_a_plain_name_or_a_bare_period(trigger: object, schedule: object) -> None:
    with pytest.raises(TypeError):
        TriggerSpec(trigger, schedule)  # type: ignore[arg-type]


def test_a_spec_refuses_a_bare_daily_run() -> None:
    with pytest.raises(TypeError, match="the schedule must be an Every or a DailyAt"):
        TriggerSpec(Trigger.BACKUP_DB, DailyRun(time(10, 0), "UTC"))  # type: ignore[arg-type]


# Triggers ---------------------------------------------------------------------------------------


def test_register_keeps_specs_in_registration_order() -> None:
    triggers = Triggers(job_types("send_due_messages", "reconcile_sends", "backup_db"))

    triggers.register_trigger(Trigger.SEND_DUE_MESSAGES, Every(MINUTE))
    triggers.register_trigger(Trigger.BACKUP_DB, Every(timedelta(days=1)))
    triggers.register_trigger(Trigger.RECONCILE_SENDS, Every(2 * MINUTE, at_startup=True))
    triggers.freeze()

    assert triggers.frozen
    assert triggers.specs() == (
        TriggerSpec(Trigger.SEND_DUE_MESSAGES, Every(MINUTE)),
        TriggerSpec(Trigger.BACKUP_DB, Every(timedelta(days=1))),
        TriggerSpec(Trigger.RECONCILE_SENDS, Every(2 * MINUTE, at_startup=True)),
    )


def test_a_new_registry_is_empty_and_open() -> None:
    triggers = Triggers(job_types())

    assert (triggers.frozen, triggers.specs()) == (False, ())


@pytest.mark.parametrize("job_types_arg", [None, object(), ("send_due_messages",)])
def test_the_registry_needs_a_job_type_registry(job_types_arg: object) -> None:
    with pytest.raises(TypeError, match="JobTypes"):
        Triggers(job_types_arg)  # type: ignore[arg-type]


def test_the_registry_needs_a_frozen_job_type_registry() -> None:
    with pytest.raises(RuntimeError, match="freeze the job type registry"):
        Triggers(job_types("send_due_messages", frozen=False))


@pytest.mark.parametrize("trigger", ["send_due_messages", "SEND_DUE_MESSAGES", None, 1])
def test_register_refuses_anything_but_a_trigger_member(trigger: object) -> None:
    triggers = Triggers(job_types("send_due_messages"))

    with pytest.raises(TypeError, match="not a Trigger member"):
        triggers.register_trigger(trigger, Every(MINUTE))  # type: ignore[arg-type]

    assert triggers.specs() == ()


@pytest.mark.parametrize("schedule", [MINUTE, 60, None, DailyRun(time(10, 0), "UTC")])
def test_register_refuses_a_schedule_that_is_not_an_every_or_a_daily_at(schedule: object) -> None:
    triggers = Triggers(job_types("send_due_messages"))

    with pytest.raises(TypeError, match="the schedule must be an Every or a DailyAt"):
        triggers.register_trigger(Trigger.SEND_DUE_MESSAGES, schedule)  # type: ignore[arg-type]


def test_register_refuses_a_trigger_whose_job_type_is_not_registered() -> None:
    triggers = Triggers(job_types("reconcile_sends"))

    with pytest.raises(UnknownJobType) as raised:
        triggers.register_trigger(Trigger.SEND_DUE_MESSAGES, Every(MINUTE))

    assert raised.value.job_type == "send_due_messages"
    assert triggers.specs() == ()


def test_register_refuses_a_duplicate() -> None:
    triggers = Triggers(job_types("send_due_messages"))
    triggers.register_trigger(Trigger.SEND_DUE_MESSAGES, Every(MINUTE))

    with pytest.raises(ValueError, match="already registered"):
        triggers.register_trigger(Trigger.SEND_DUE_MESSAGES, Every(5 * MINUTE))

    assert triggers.specs() == (TriggerSpec(Trigger.SEND_DUE_MESSAGES, Every(MINUTE)),)


def test_register_refuses_after_freeze() -> None:
    triggers = Triggers(job_types("send_due_messages"))
    triggers.freeze()

    with pytest.raises(RuntimeError, match="frozen"):
        triggers.register_trigger(Trigger.SEND_DUE_MESSAGES, Every(MINUTE))

    assert triggers.specs() == ()


# DailyRun and DailyAt (Story 1.16) -------------------------------------------------------------


def test_a_daily_run_holds_a_time_a_timezone_and_catch_up() -> None:
    run = DailyRun(time(2, 30), "America/Los_Angeles", catch_up=True)

    assert (run.at, run.timezone, run.catch_up) == (time(2, 30), "America/Los_Angeles", True)
    assert DailyRun(time(10, 0), "UTC").catch_up is False
    with pytest.raises(dataclasses.FrozenInstanceError):
        run.catch_up = False  # type: ignore[misc]


@pytest.mark.parametrize(
    ("at", "zone", "catch_up", "error", "message"),
    [
        ("10:00", "UTC", False, TypeError, "datetime.time"),
        (time(10, 0, 30), "UTC", False, ValueError, "whole minutes"),
        (time(10, 0, 0, 1), "UTC", False, ValueError, "whole minutes"),
        (time(10, 0, tzinfo=UTC), "UTC", False, ValueError, "naive"),
        (time(10, 0), None, False, TypeError, "timezone must be a str"),
        (time(10, 0), "Mars/Olympus_Mons", False, ValueError, "IANA name"),
        (time(10, 0), "", False, ValueError, "IANA name"),
        (time(10, 0), "../etc/passwd", False, ValueError, "IANA name"),
        (time(10, 0), "UTC", 1, TypeError, "catch_up must be a bool"),
    ],
)
def test_a_daily_run_refuses_a_bad_field(
    at: object, zone: object, catch_up: object, error: type[Exception], message: str
) -> None:
    with pytest.raises(error, match=message):
        DailyRun(at, zone, catch_up)  # type: ignore[arg-type]


def test_a_daily_at_has_a_day_of_grace() -> None:
    fallback = DailyRun(time(10, 0), "UTC")

    schedule = DailyAt(lambda: fallback, fallback=fallback)

    assert schedule.grace == timedelta(hours=24)


@pytest.mark.parametrize(
    ("when", "fallback", "message"),
    [
        (None, DailyRun(time(10, 0), "UTC"), "when must be callable"),
        (lambda: None, time(10, 0), "fallback must be a DailyRun"),
        (lambda: None, None, "fallback must be a DailyRun"),
    ],
)
def test_a_daily_at_refuses_a_bad_field(when: object, fallback: object, message: str) -> None:
    with pytest.raises(TypeError, match=message):
        DailyAt(when, fallback=fallback)  # type: ignore[arg-type]


def test_a_daily_at_needs_its_fallback_by_keyword() -> None:
    fallback = DailyRun(time(10, 0), "UTC")

    with pytest.raises(TypeError):
        DailyAt(lambda: fallback, fallback)  # type: ignore[misc]


def test_register_accepts_a_daily_at_and_does_not_call_it() -> None:
    calls: list[str] = []

    def when() -> DailyRun:
        calls.append("read")
        return DailyRun(time(10, 0), "UTC")

    triggers = Triggers(job_types("backup_db"))
    schedule = DailyAt(when, fallback=DailyRun(time(10, 0), "UTC"))

    triggers.register_trigger(Trigger.BACKUP_DB, schedule)

    assert triggers.specs() == (TriggerSpec(Trigger.BACKUP_DB, schedule),)
    assert calls == []  # read when the scheduler starts, never when registered
