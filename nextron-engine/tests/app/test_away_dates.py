"""Away dates (Story 5.11, FR-31) on a temporary database at head through real units of work:
``Sequencing.enroll`` refuses origins ``selection`` and ``restart`` with ``operator_away`` on
an away day in ``sender_timezone`` (right after the AD-16 gate, writing nothing), while
``operator_pick``, ``referral`` and ``recontact`` enroll as usual; ``queries.setup.away_periods``
ignores a stored line that no longer parses and ``queries.setup.away_until`` reads the day in
``sender_timezone`` (UTC while it is unset). The world is Story 4.8's (``tests.app.test_enroll``).
"""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import Engine

from nextron.app.compliance.api import Compliance
from nextron.app.queries import setup as setup_queries
from nextron.app.setup.api import Setup
from nextron.domain.enrollment import (
    EnrollmentOrigin,
    EnrollRefusal,
    NewEnrollment,
    RefusalCode,
)
from nextron.domain.sending_policy import AwayPeriod
from nextron.domain.settings_schema import Key
from tests.app.test_enroll import LATER, OPS_US, US_CAMPAIGN, World, build_world

TODAY = LATER.date()  # 2026-11-18
AWAY_NOW = (f"{TODAY.isoformat()} to 2026-11-20",)
PAUSED = (EnrollmentOrigin.SELECTION, EnrollmentOrigin.RESTART)
GOING_AHEAD = (
    EnrollmentOrigin.OPERATOR_PICK,
    EnrollmentOrigin.REFERRAL,
    EnrollmentOrigin.RECONTACT,
)


@pytest.fixture
def world(db_engine: Engine) -> World:
    return build_world(db_engine)


def set_setting(world: World, key: Key, value: object) -> None:
    with world.units.open():
        Setup(world.clock).set(key, value)


def refused_for(world: World, origin: EnrollmentOrigin, campaign: str = US_CAMPAIGN) -> object:
    _, contact_id = world.contact()
    before = world.counts()
    found = world.enroll(contact_id, world.campaign(campaign), origin)
    if isinstance(found, EnrollRefusal):
        assert world.counts() == before  # no Enrollment, event, Draft, counter or job
    return found


@pytest.mark.parametrize("origin", PAUSED)
def test_an_away_day_refuses_selection_and_restart_writing_nothing(
    world: World, origin: EnrollmentOrigin
) -> None:
    set_setting(world, Key.AWAY_PERIODS, AWAY_NOW)

    assert refused_for(world, origin) == EnrollRefusal(RefusalCode.OPERATOR_AWAY)


@pytest.mark.parametrize("origin", GOING_AHEAD)
def test_an_away_day_never_refuses_the_operators_own_picks(
    world: World, origin: EnrollmentOrigin
) -> None:
    set_setting(world, Key.AWAY_PERIODS, AWAY_NOW)

    found = refused_for(world, origin)

    assert isinstance(found, NewEnrollment)
    [(drafts,)] = world.sql(
        "SELECT COUNT(*) FROM drafts d JOIN enrollments e ON e.id = d.enrollment_id "
        "WHERE e.id = ?",
        (found.enrollment_id,),
    )
    assert drafts == 1  # its Step 1 slot was requested as usual


@pytest.mark.parametrize("origin", PAUSED)
def test_outside_the_period_selection_and_restart_enroll(
    world: World, origin: EnrollmentOrigin
) -> None:
    set_setting(world, Key.AWAY_PERIODS, ("2026-11-19 to 2026-11-20", "2026-11-10 to 2026-11-17"))

    assert isinstance(refused_for(world, origin), NewEnrollment)


def test_the_last_day_of_a_period_is_away(world: World) -> None:
    set_setting(world, Key.AWAY_PERIODS, (f"2026-11-01 to {TODAY.isoformat()}",))

    assert refused_for(world, EnrollmentOrigin.SELECTION) == EnrollRefusal(
        RefusalCode.OPERATOR_AWAY
    )


def test_the_day_is_read_in_the_sender_timezone(world: World) -> None:
    set_setting(world, Key.AWAY_PERIODS, ("2026-12-21 to 2027-01-04",))
    world.clock.set(datetime(2026, 12, 21, 3, 0, tzinfo=UTC))

    # in UTC (sender_timezone unset) it is already 2026-12-21: away
    assert refused_for(world, EnrollmentOrigin.SELECTION) == EnrollRefusal(
        RefusalCode.OPERATOR_AWAY
    )

    set_setting(world, Key.SENDER_TIMEZONE, "America/Los_Angeles")  # still 2026-12-20 there

    assert isinstance(refused_for(world, EnrollmentOrigin.SELECTION), NewEnrollment)


def test_the_gate_still_answers_first(world: World) -> None:
    set_setting(world, Key.AWAY_PERIODS, AWAY_NOW)
    _, contact_id = world.contact(web="gated.com")
    with world.units.open():
        Compliance(world.clock).add_value("gated.com")

    found = world.enroll(contact_id, world.campaign(US_CAMPAIGN), EnrollmentOrigin.SELECTION)

    assert isinstance(found, EnrollRefusal) and found.code is RefusalCode.BLOCKED


def test_away_answers_before_the_campaign_is_checked(world: World) -> None:
    set_setting(world, Key.AWAY_PERIODS, AWAY_NOW)

    found = refused_for(world, EnrollmentOrigin.SELECTION, OPS_US)  # a draft Campaign

    assert found == EnrollRefusal(RefusalCode.OPERATOR_AWAY)


def test_the_queries_read_the_periods_and_the_end_of_the_run(world: World) -> None:
    assert world.read(setup_queries.away_periods) == ()
    assert world.read(lambda: setup_queries.away_until(LATER)) is None

    periods = (f"{TODAY.isoformat()} to 2026-11-20", "2026-11-21 to 2026-11-25")
    set_setting(world, Key.AWAY_PERIODS, periods)

    assert world.read(setup_queries.away_periods) == (
        AwayPeriod(TODAY, date(2026, 11, 20)),
        AwayPeriod(date(2026, 11, 21), date(2026, 11, 25)),
    )
    assert world.read(lambda: setup_queries.away_until(LATER)) == date(2026, 11, 25)
    assert world.read(lambda: setup_queries.sender_local_date(LATER)) == TODAY


def test_a_stored_line_that_no_longer_parses_is_ignored(world: World) -> None:
    set_setting(world, Key.AWAY_PERIODS, AWAY_NOW)
    world.sql(
        "UPDATE settings SET value = ? WHERE key = 'away_periods'",
        (f"next week\n2026-11-20 to {TODAY.isoformat()}\n{AWAY_NOW[0]}",),
    )

    assert world.read(setup_queries.away_periods) == (AwayPeriod(TODAY, date(2026, 11, 20)),)
    assert refused_for(world, EnrollmentOrigin.SELECTION) == EnrollRefusal(
        RefusalCode.OPERATOR_AWAY
    )


def test_an_unreadable_sender_timezone_reads_utc(world: World) -> None:
    set_setting(world, Key.SENDER_TIMEZONE, "America/Los_Angeles")
    world.sql("UPDATE settings SET value = 'Mars/Olympus' WHERE key = 'sender_timezone'")
    late = datetime(2026, 12, 21, 3, 0, tzinfo=UTC)

    assert world.read(lambda: setup_queries.sender_local_date(late)) == date(2026, 12, 21)
    with pytest.raises(TypeError):
        world.read(lambda: setup_queries.sender_local_date(datetime(2026, 12, 21)))  # noqa: DTZ001
