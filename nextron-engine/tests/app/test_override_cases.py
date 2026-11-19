"""The Operator's override cases (``Playbook.add_override_case`` and ``erase_override_cases``,
Story 4.6; FR-40, FR-73, AD-12, AD-17) on a temporary database at head: a pseudonymised Reply
case is stored with its Contact and no source text, it survives every fixture sync, a bad call
writes nothing, and an erasure redacts and retires the Contact's cases.
"""

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.playbook.api import MAX_OVERRIDE_TEXT, Playbook
from nextron.app.queries import playbook as playbook_queries
from nextron.db.repos.playbook import test_cases as cases_repo
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain.ai_test_cases import TestCase, stored_case
from nextron.domain.ai_test_gate import TestSet
from nextron.domain.ids import is_ulid
from nextron.domain.reply_classes import ReplyClass
from tests.fakes.clock import FrozenClock

T0 = datetime(2026, 11, 18, 23, 30, tzinfo=UTC)
CONTACT = "01JD3QZ8X5V9K2M7N4P6R8T0F1"
OTHER = "01JD3QZ8X5V9K2M7N4P6R8T0F2"
REPLY = (
    "Hi Sam,\n\n"
    "Not me, but Dana Whitfield, our Managing Partner, handles this. She's on "
    "dana@brightwell.com or 020 7123 4567, at 4 Market Street, Lincoln LN1 1AB. "
    "Brightwell & Hale LLP's site is https://www.brightwell-law.com.\n\n"
    "Kind regards,\nPriya Shah\n\n"
    "On Mon, 16 Nov 2026 at 10:02, Sam <sam@nextron.example.com> wrote:\n"
    "> Saw you're hiring an intake coordinator.\n"
)
DETAILS = (
    "Sam",
    "Dana",
    "Whitfield",
    "dana@",
    "7123",
    "Market Street",
    "LN1",
    "Lincoln",
    "Brightwell",
    "Hale",
    "brightwell-law",
    "Priya",
    "Shah",
    "intake coordinator",
)
_LOG_MACHINERY_NUMBERS = frozenset(
    {"created", "msecs", "relativeCreated", "thread", "process", "lineno"}
)
COLUMNS = (
    "case_key, test_set, route, input, expected, covers, sha256, loaded_at, retired_at, "
    "origin, contact_id, id"
)


class World:
    def __init__(self, units: UnitOfWorkFactory, engine: Engine) -> None:
        self.units = units
        self.engine = engine
        self.clock = FrozenClock(T0)
        self.playbook = Playbook(self.clock)

    def sql(self, statement: str, params: tuple[object, ...] = ()) -> list[tuple[Any, ...]]:
        with self.engine.connect() as connection:
            result = connection.exec_driver_sql(statement, params)
            rows = result.all() if result.returns_rows else []
            connection.commit()
        return [tuple(row) for row in rows]

    def in_transaction[R](self, work: Callable[[], R]) -> R:
        @use_case
        def run() -> R:
            with current().transaction():
                return work()

        with self.units.open():
            return run()

    def add(self, text: str = REPLY, label: Any = "referral", **kwargs: Any) -> str:
        kwargs.setdefault("contact_id", CONTACT)
        return self.in_transaction(lambda: self.playbook.add_override_case(text, label, **kwargs))

    def erase(self, contact_id: str = CONTACT) -> int:
        return self.in_transaction(lambda: self.playbook.erase_override_cases(contact_id))

    def rows(self) -> list[tuple[Any, ...]]:
        return self.sql(f"SELECT {COLUMNS} FROM ai_test_cases ORDER BY loaded_at, id")

    def overrides(self) -> list[tuple[Any, ...]]:
        return self.sql(
            f"SELECT {COLUMNS} FROM ai_test_cases WHERE origin = 'override' ORDER BY loaded_at, id"
        )

    def later(self, minutes: int = 1) -> None:
        self.clock.advance(timedelta(minutes=minutes))


@pytest.fixture
def world(units: UnitOfWorkFactory, db_engine: Engine) -> World:
    return World(units, db_engine)


def test_an_override_is_stored_pseudonymised_with_its_contact_and_class(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    key = world.add(names=["Dana Whitfield", "Priya Shah"], organisations=["Brightwell"])

    [row] = world.rows()
    case_key, test_set, route, text, expected, covers, sha, loaded, retired, origin, contact, row_id = row
    assert key == case_key == f"override-{row_id.lower()}" and is_ulid(row_id)
    assert (test_set, route, origin, contact, retired) == (
        "reply",
        "classify_reply",
        "override",
        CONTACT,
        None,
    )
    assert loaded == "2026-11-18T23:30:00.000000Z"
    assert json.loads(expected) == {"reply_class": "referral"}  # the class only (decision 3)
    assert json.loads(covers) == ["class:referral", "override"]
    found = json.loads(text)
    assert found["received_on"] == "2026-11-18"  # today in UTC
    [block] = found["blocks"]
    assert (block["id"], block["source"]) == ("r1", "reply")
    for detail in DETAILS:
        assert detail not in block["text"], detail
    for placeholder in ("[name]", "[email]", "[phone]", "[address]", "[organisation]", "[url]"):
        assert placeholder in block["text"], placeholder
    assert "handles this" in block["text"]
    case = stored_case(case_key, test_set, route, text, expected, covers)
    assert case.sha256 == sha
    logged = [record for record in caplog.records if record.getMessage() == "override_case_added"]
    assert [record.case_key for record in logged] == [key]  # type: ignore[attr-defined]
    for record in caplog.records:
        # Every attribute except the logging module's own timing and process numbers, whose
        # digits (a timestamp such as ``...1001.7123506``) could collide with the phone number.
        fields = {k: v for k, v in record.__dict__.items() if k not in _LOG_MACHINERY_NUMBERS}
        rendered = record.getMessage() + json.dumps(fields, default=str)
        for detail in ("Dana", "Whitfield", "brightwell", "7123", CONTACT):
            assert detail not in rendered


def test_the_label_may_be_a_reply_class_and_the_text_is_cut(world: World) -> None:
    world.add("Please stop. " + "x" * (MAX_OVERRIDE_TEXT * 2), ReplyClass.UNSUBSCRIBE)

    [row] = world.rows()
    [block] = json.loads(row[3])["blocks"]
    assert len(block["text"]) == MAX_OVERRIDE_TEXT
    assert block["text"].startswith("Please stop. x")
    assert json.loads(row[4]) == {"reply_class": "unsubscribe"}


def test_an_override_survives_every_fixture_sync_and_joins_the_reply_set(world: World) -> None:
    cases = main.load_test_cases()
    with world.units.open():
        world.playbook.sync_test_cases(cases)
    with world.units.open(read_only=True):
        before = playbook_queries.case_set_fingerprint(TestSet.REPLY, "classify_reply")
    world.later()

    key = world.add()

    with world.units.open(read_only=True):
        live = cases_repo.live("reply", "classify_reply")
        after = playbook_queries.case_set_fingerprint(TestSet.REPLY, "classify_reply")
    assert key in {row.case_key for row in live}  # read with the fixtures
    assert len(live) == 1 + sum(1 for case in cases if case.test_set is TestSet.REPLY)
    assert after != before  # each override re-queues the Reply set's tuples (decision 6)
    world.later()
    extraction = [case for case in cases if case.test_set is TestSet.EXTRACTION]
    with world.units.open():
        synced = world.playbook.sync_test_cases(extraction)  # every reply fixture removed

    assert key not in {case_key for _, case_key in synced.retired}
    assert all(test_set != "reply" or key != case_key for test_set, case_key in synced.unchanged)
    [override] = world.overrides()
    assert override[8] is None  # still live
    assert world.sql(
        "SELECT COUNT(*) FROM ai_test_cases WHERE test_set = 'reply' AND origin = 'fixture' "
        "AND retired_at IS NULL"
    ) == [(0,)]


@pytest.mark.parametrize(
    ("text", "label", "contact_id", "error", "match"),
    [
        (REPLY, "maybe", CONTACT, ValueError, "label must be a Reply Class"),
        (REPLY, 5, CONTACT, ValueError, "label must be a Reply Class"),
        (REPLY, "referral", "not-a-ulid", ValueError, "contact_id must be a ULID"),
        (REPLY, "referral", CONTACT.lower(), ValueError, "contact_id must be a ULID"),
        ("   \n ", "referral", CONTACT, ValueError, "blank once pseudonymised"),
        ("> Saw you're hiring.\n> Worth a look?", "referral", CONTACT, ValueError, "blank once"),
        (b"bytes", "referral", CONTACT, TypeError, "as a str"),
    ],
)
def test_a_bad_override_raises_and_writes_nothing(
    world: World, text: Any, label: Any, contact_id: str, error: type[Exception], match: str
) -> None:
    with pytest.raises(error, match=match) as raised:
        world.add(text, label, contact_id=contact_id)

    assert world.rows() == []
    assert "Dana" not in str(raised.value) and "Whitfield" not in str(raised.value)


def test_a_bad_name_list_raises_and_writes_nothing(world: World) -> None:
    with pytest.raises(TypeError):
        world.add(names=[5])

    assert world.rows() == []


def test_erasure_redacts_and_retires_each_override_of_the_contact(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.playbook.api")
    first = world.add()
    world.later()
    second = world.add("Not now, try us in March.", "not_now")
    world.later()
    other = world.add("Stop.", "unsubscribe", contact_id=OTHER)
    world.sql(  # the first one is already retired: it is still erased
        "UPDATE ai_test_cases SET retired_at = '2026-11-18T23:30:30.000000Z' WHERE case_key = ?",
        (first,),
    )
    world.later()

    assert world.erase() == 2

    rows = {row[0]: row for row in world.overrides()}
    assert (rows[first][3], rows[first][8]) == ("[erased]", "2026-11-18T23:30:30.000000Z")
    assert (rows[second][3], rows[second][8]) == ("[erased]", "2026-11-18T23:33:00.000000Z")
    assert rows[other][3] != "[erased]" and rows[other][8] is None
    assert json.loads(rows[second][4]) == {"reply_class": "not_now"}  # the label is kept
    [record] = [r for r in caplog.records if r.getMessage() == "override_cases_erased"]
    assert record.count == 2  # type: ignore[attr-defined]
    assert world.erase() == 0  # nothing left to erase
    with world.units.open(read_only=True):
        live = {row.case_key for row in cases_repo.live("reply", "classify_reply")}
    assert live == {other}


def test_erasure_takes_a_ulid_and_leaves_fixtures_alone(world: World) -> None:
    with world.units.open():
        world.playbook.sync_test_cases(main.load_test_cases())
    before = world.rows()

    with pytest.raises(ValueError, match="contact_id must be a ULID"):
        world.erase("nobody")
    assert world.erase() == 0

    assert world.rows() == before


def test_an_override_case_reads_back_as_a_test_case(world: World) -> None:
    key = world.add("Talk to Priya, our COO.", "referral")

    with world.units.open(read_only=True):
        [row] = cases_repo.live("reply", "classify_reply")
    case = stored_case(row.case_key, row.test_set, row.route, row.input, row.expected, row.covers)

    assert isinstance(case, TestCase) and case.case_key == key
    assert case.input["blocks"][0]["text"] == "Talk to [name], our COO."


def test_a_corrected_override_replaces_the_earlier_one_of_its_text(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.playbook.api")
    wrong = world.add("Talk to Priya, our COO.", "not_interested")
    other_text = world.add("Not now, try us in March.", "not_now")
    elsewhere = world.add("Talk to Priya, our COO.", "not_interested", contact_id=OTHER)
    world.later()

    right = world.add("Talk to Priya, our COO.", "referral")  # the Operator corrects the label

    rows = {row[0]: row for row in world.overrides()}
    assert rows[wrong][8] == "2026-11-18T23:31:00.000000Z"  # retired: the newest label wins
    assert rows[right][8] is None and json.loads(rows[right][4]) == {"reply_class": "referral"}
    assert rows[other_text][8] is None  # another text of the Contact stays
    assert rows[elsewhere][8] is None  # the same text of another Contact stays
    with world.units.open(read_only=True):
        live = {row.case_key for row in cases_repo.live("reply", "classify_reply")}
    assert live == {right, other_text, elsewhere}
    logged = [r for r in caplog.records if r.getMessage() == "override_case_added"]
    assert [r.replaced_count for r in logged] == [0, 0, 0, 1]  # type: ignore[attr-defined]
