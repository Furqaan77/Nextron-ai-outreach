"""The footer settings, ``FooterChanged`` and ``footer_snapshots`` (FR-23, NFR-7, AD-3, Story
4.11) on real units of work at head: the setting rows through ``Setup.save``, ``Setup.set`` and
``queries.setup``; ``footer_settings()``; doctor's ``required_unset``; the publish rows with a
recording subscriber registry and the seeded Markets; and the snapshot repository."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import startup_checks
from nextron.app.queries import setup as setup_queries
from nextron.app.setup.api import Setup
from nextron.db.models.base import new_id
from nextron.db.repos.drafting import footer_snapshots
from nextron.db.repos.drafting.footer_snapshots import StoredFooterSnapshot
from nextron.db.repos.setup import markets as markets_repo
from nextron.db.uow import (
    Subscribers,
    UnitOfWorkFactory,
    command,
    current,
    use_case,
)
from nextron.domain import seed_yaml, settings_schema
from nextron.domain.errors import DomainError
from nextron.domain.markets import validate_markets
from nextron.domain.notifications import FooterChanged
from nextron.domain.render import FooterSettings, footer, footer_complete, footer_sha256
from nextron.domain.settings_schema import (
    DEFAULT_OPTOUT_LINE,
    InvalidSettings,
    InvalidSettingValue,
    Key,
)
from tests.contracts.support import ENGINE_ROOT
from tests.fakes.clock import DEFAULT_NOW, FrozenClock

SEEDED = validate_markets(
    seed_yaml.parse((ENGINE_ROOT / "seed" / "markets.yaml").read_text(encoding="utf-8"))
)
FOOTER = {
    "sender_signature": "Furqaan, Nextron AI",
    "legal_name": "Nextron AI Ltd",
    "postal_address": "1 Example Street, London EC1A 1AA",
    "privacy_notice_url": "https://nextronai.com/privacy",
}
MARK = "QZXPLANTED"


class InvariantBroken(DomainError):
    """A subscriber's invariant broke (the recorder raises it on demand)."""

    code = "invariant_broken"
LATER = DEFAULT_NOW + timedelta(hours=1)


@dataclass
class Recorder:
    """The ``FooterChanged`` notifications a subscriber saw, each with the ``legal_name`` text
    stored when it ran; ``fail`` makes the subscriber raise after recording."""

    seen: list[tuple[str, str | None]] = field(default_factory=list)
    fail: bool = False


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def units(db_engine: Engine, recorder: Recorder) -> UnitOfWorkFactory:
    """Units of work whose registry runs one recording ``FooterChanged`` subscriber."""

    @command
    def record(notification: FooterChanged) -> None:
        texts = setup_queries.stored_texts()  # inside the publishing transaction
        recorder.seen.append((notification.market_id, texts.get("legal_name")))
        if recorder.fail:
            raise InvariantBroken("the recorder's invariant broke")

    subscribers = Subscribers()
    subscribers.register_subscriber(FooterChanged, record)
    subscribers.freeze()
    return UnitOfWorkFactory(db_engine, subscribers)


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock()


@pytest.fixture
def setup(clock: FrozenClock) -> Setup:
    return Setup(clock)


@pytest.fixture
def market_ids(units: UnitOfWorkFactory, setup: Setup) -> dict[str, str]:
    """The seeded US and UK Markets, ``{code: id}``."""
    with units.open():
        setup.sync_markets(SEEDED)
    with units.open(read_only=True):
        return {market.code: market.id for market in setup_queries.markets()}


def save(units: UnitOfWorkFactory, setup: Setup, texts: dict[str, Any]) -> dict[str, str]:
    with units.open():
        return setup.save(texts)


def set_value(units: UnitOfWorkFactory, setup: Setup, key: str, value: object) -> None:
    with units.open():
        setup.set(key, value)


def stored(units: UnitOfWorkFactory) -> dict[str, str]:
    with units.open(read_only=True):
        return setup_queries.stored_texts()


def read_footer(units: UnitOfWorkFactory) -> FooterSettings:
    with units.open(read_only=True):
        return setup_queries.footer_settings()


def refusal(units: UnitOfWorkFactory, setup: Setup, key: str, text: str) -> InvalidSettingValue:
    with pytest.raises(InvalidSettings) as raised:
        save(units, setup, {key: text})
    assert list(raised.value.problems) == [key]
    return raised.value.problems[key]


def run_sql(engine: Engine, *statements: str, parameters: tuple[Any, ...] = ()) -> None:
    """Run statements outside the engine, foreign keys on, as another tool would."""
    assert engine.url.database
    writer = sqlite3.connect(engine.url.database, isolation_level=None)
    try:
        writer.execute("PRAGMA foreign_keys=ON")
        for statement in statements:
            writer.execute(statement, parameters)
    finally:
        writer.close()


# The settings rows -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url", ["http://nextronai.com/privacy", "https://user@x.com", "ftp://x", "nextronai.com"]
)
def test_the_privacy_url_must_be_an_https_link(
    units: UnitOfWorkFactory, setup: Setup, url: str
) -> None:
    refused = refusal(units, setup, "privacy_notice_url", url)

    assert (refused.rule, refused.reason) == ("https_url", "must be an https link")
    assert stored(units) == {}


def test_an_https_privacy_url_is_stored(units: UnitOfWorkFactory, setup: Setup) -> None:
    save(units, setup, {"privacy_notice_url": " HTTPS://nextronai.com/privacy "})

    assert stored(units) == {"privacy_notice_url": "HTTPS://nextronai.com/privacy"}


@pytest.mark.parametrize(
    ("key", "text"),
    [
        ("sender_signature", "Audit from $500"),
        ("legal_name", "Nextron AI Ltd (£2k audits)"),
        ("postal_address", "1 Example Street, 500 EUR"),
        ("optout_line", "Reply 'no' to skip the 300 usd audit"),
        ("us_solicitation_label", "Advertisement: audits from €400"),
    ],
)
def test_no_footer_text_names_a_price(
    units: UnitOfWorkFactory, setup: Setup, key: str, text: str
) -> None:
    refused = refusal(units, setup, key, text)

    assert (refused.rule, refused.reason) == ("no_price", "must not name a price")
    assert stored(units) == {}


@pytest.mark.parametrize("text", ["Thanks", "Not relevant? Replying helps", "Unsubscribe below"])
def test_the_optout_line_must_tell_the_reader_to_reply(
    units: UnitOfWorkFactory, setup: Setup, text: str
) -> None:
    refused = refusal(units, setup, "optout_line", text)

    assert refused.rule == "optout_reply"
    assert refused.reason == "must tell the reader to reply to opt out (hold the word reply)"


def test_an_optout_line_holding_reply_is_stored(units: UnitOfWorkFactory, setup: Setup) -> None:
    save(units, setup, {"optout_line": "Not for you? REPLY stop."})

    assert read_footer(units).optout_line == "Not for you? REPLY stop."


@pytest.mark.parametrize(
    ("key", "limit"),
    [
        ("sender_signature", 200),
        ("legal_name", 200),
        ("postal_address", 300),
        ("optout_line", 200),
        ("privacy_notice_url", 500),
    ],
)
def test_each_footer_setting_has_its_length_limit(
    units: UnitOfWorkFactory, setup: Setup, key: str, limit: int
) -> None:
    fits = {
        "privacy_notice_url": "https://nextronai.com/" + "p" * (limit - 22),
        "optout_line": "Reply " + "n" * (limit - 6),
    }.get(key, "a" * limit)
    too_long = fits + "x"

    assert refusal(units, setup, key, too_long).rule == "max_length"
    save(units, setup, {key: fits})
    assert stored(units) == {key: fits}


def test_a_set_required_footer_value_cant_be_cleared(
    units: UnitOfWorkFactory, setup: Setup
) -> None:
    save(units, setup, FOOTER)

    for key in FOOTER:
        refused = refusal(units, setup, key, "  ")
        assert refused.rule == "required"
    assert stored(units) == FOOTER


def test_a_blank_optout_line_means_its_default(units: UnitOfWorkFactory, setup: Setup) -> None:
    save(units, setup, {"optout_line": "Reply 'no' to stop these."})
    save(units, setup, {"optout_line": ""})

    assert stored(units) == {}
    assert read_footer(units).optout_line == DEFAULT_OPTOUT_LINE
    assert DEFAULT_OPTOUT_LINE == "Not relevant? Reply 'no' and I won't contact you again."


def test_a_refused_value_never_shows_in_the_refusal(units: UnitOfWorkFactory, setup: Setup) -> None:
    with pytest.raises(InvalidSettings) as raised:
        save(
            units,
            setup,
            {"sender_signature": f"{MARK} $5", "privacy_notice_url": f"http://{MARK}.com"},
        )

    assert MARK not in str(raised.value)
    assert all(MARK not in str(problem) for problem in raised.value.problems.values())


# footer_settings() -------------------------------------------------------------------------------


def test_unset_footer_settings_read_blank_except_the_optout_default(
    units: UnitOfWorkFactory,
) -> None:
    assert read_footer(units) == FooterSettings(
        sender_signature="",
        legal_name="",
        postal_address="",
        optout_line=DEFAULT_OPTOUT_LINE,
        privacy_notice_url="",
        us_solicitation_label="",
    )


def test_set_footer_settings_read_back(units: UnitOfWorkFactory, setup: Setup) -> None:
    save(units, setup, {**FOOTER, "us_solicitation_label": "Advertisement"})

    assert read_footer(units) == FooterSettings(
        **FOOTER, optout_line=DEFAULT_OPTOUT_LINE, us_solicitation_label="Advertisement"
    )
    us, uk = SEEDED
    assert footer_complete(read_footer(units), uk).complete


def test_a_stored_footer_text_that_no_longer_parses_reads_blank(
    units: UnitOfWorkFactory, setup: Setup, db_engine: Engine
) -> None:
    save(units, setup, FOOTER)
    run_sql(
        db_engine,
        "UPDATE settings SET value = 'http://nextronai.com/privacy' "
        "WHERE key = 'privacy_notice_url'",
        "UPDATE settings SET value = 'Audit from $500' WHERE key = 'legal_name'",
    )

    read = read_footer(units)

    assert (read.privacy_notice_url, read.legal_name) == ("", "")
    assert read.sender_signature == FOOTER["sender_signature"]
    us, uk = SEEDED
    assert footer_complete(read, uk).missing == ("legal_name", "privacy_notice_url")


# Doctor ------------------------------------------------------------------------------------------


def test_a_fresh_database_lists_the_footer_settings_after_the_older_required_ones(
    units: UnitOfWorkFactory,
) -> None:
    with units.open(read_only=True):
        unset = setup_queries.required_unset()

    assert unset == (
        "sender_name",  # Story 4.7
        "sender_timezone",
        "alert_chat_id",
        "sender_signature",
        "legal_name",
        "postal_address",
        "privacy_notice_url",
    )
    result = startup_checks.check_required_settings(lambda: unset)
    assert result.message == (
        "required settings not set or no longer valid: Sender's name (sender_name), Sender's "
        "timezone (sender_timezone), Telegram alert chat id (alert_chat_id), Sender signature "
        "(sender_signature), Legal name (legal_name), Postal address (postal_address), "
        "Privacy notice URL (privacy_notice_url); set them on the Settings page"
    )


def test_set_footer_settings_leave_the_required_list(
    units: UnitOfWorkFactory, setup: Setup
) -> None:
    save(units, setup, FOOTER)

    with units.open(read_only=True):
        assert setup_queries.required_unset() == (
            "sender_name",
            "sender_timezone",
            "alert_chat_id",
        )


# Row: Publish ------------------------------------------------------------------------------------


def test_a_changed_legal_name_publishes_for_both_markets_in_code_order_in_the_transaction(
    units: UnitOfWorkFactory, setup: Setup, market_ids: dict[str, str], recorder: Recorder
) -> None:
    save(units, setup, {"legal_name": "Nextron AI Ltd"})

    # code order (uk before us); the subscriber already sees the new text
    assert recorder.seen == [
        (market_ids["uk"], "Nextron AI Ltd"),
        (market_ids["us"], "Nextron AI Ltd"),
    ]


def test_a_failing_subscriber_rolls_the_save_back(
    units: UnitOfWorkFactory, setup: Setup, market_ids: dict[str, str], recorder: Recorder
) -> None:
    recorder.fail = True

    with pytest.raises(InvariantBroken):
        save(units, setup, {"legal_name": "Nextron AI Ltd", "sender_timezone": "Europe/London"})

    assert stored(units) == {}
    assert recorder.seen == [(market_ids["uk"], "Nextron AI Ltd")]  # it ran, then raised


def test_a_changed_us_label_publishes_only_for_the_us(
    units: UnitOfWorkFactory, setup: Setup, market_ids: dict[str, str], recorder: Recorder
) -> None:
    save(units, setup, {"us_solicitation_label": "Advertisement"})

    assert [market for market, _ in recorder.seen] == [market_ids["us"]]


def test_a_changed_privacy_url_publishes_only_for_the_uk(
    units: UnitOfWorkFactory, setup: Setup, market_ids: dict[str, str], recorder: Recorder
) -> None:
    save(units, setup, {"privacy_notice_url": "https://nextronai.com/privacy"})

    assert [market for market, _ in recorder.seen] == [market_ids["uk"]]


def test_several_changed_footer_keys_publish_once_per_market(
    units: UnitOfWorkFactory, setup: Setup, market_ids: dict[str, str], recorder: Recorder
) -> None:
    save(units, setup, {**FOOTER, "us_solicitation_label": "Advertisement"})

    assert [market for market, _ in recorder.seen] == [market_ids["uk"], market_ids["us"]]


def test_resetting_a_custom_optout_line_to_its_default_publishes(
    units: UnitOfWorkFactory, setup: Setup, market_ids: dict[str, str], recorder: Recorder
) -> None:
    save(units, setup, {"optout_line": "Reply 'no' to stop these."})
    recorder.seen.clear()

    save(units, setup, {"optout_line": ""})  # a delete of a custom text

    assert [market for market, _ in recorder.seen] == [market_ids["uk"], market_ids["us"]]


def test_deleting_a_stored_default_publishes_nothing(
    units: UnitOfWorkFactory, setup: Setup, market_ids: dict[str, str], recorder: Recorder
) -> None:
    set_value(units, setup, Key.OPTOUT_LINE, DEFAULT_OPTOUT_LINE)  # stored, equal to the default
    assert stored(units) == {"optout_line": DEFAULT_OPTOUT_LINE}

    save(units, setup, {"optout_line": ""})

    assert stored(units) == {}  # the row is deleted
    assert recorder.seen == []  # the effective text didn't change


@pytest.mark.parametrize(
    "change",
    [
        {**FOOTER},  # saved again unchanged
        {"sender_timezone": "Europe/London"},  # not a footer key
        {"optout_line": DEFAULT_OPTOUT_LINE},  # the default, unset: stays unset
        {"us_solicitation_label": ""},  # blank, never set
    ],
)
def test_nothing_is_published_without_a_changed_footer_text(
    units: UnitOfWorkFactory,
    setup: Setup,
    market_ids: dict[str, str],
    recorder: Recorder,
    change: dict[str, str],
) -> None:
    save(units, setup, FOOTER)
    recorder.seen.clear()

    save(units, setup, change)

    assert recorder.seen == []


def test_nothing_is_published_without_markets(
    units: UnitOfWorkFactory, setup: Setup, recorder: Recorder
) -> None:
    save(units, setup, {**FOOTER, "us_solicitation_label": "Advertisement"})
    set_value(units, setup, Key.LEGAL_NAME, "Nextron AI Limited")

    assert recorder.seen == []
    assert stored(units)["legal_name"] == "Nextron AI Limited"


def test_set_publishes_when_the_effective_text_changes(
    units: UnitOfWorkFactory, setup: Setup, market_ids: dict[str, str], recorder: Recorder
) -> None:
    both = [market_ids["uk"], market_ids["us"]]

    set_value(units, setup, Key.LEGAL_NAME, "Nextron AI Ltd")  # no row before
    assert [market for market, _ in recorder.seen] == both
    recorder.seen.clear()

    set_value(units, setup, Key.LEGAL_NAME, " Nextron AI Ltd ")  # the same canonical text
    set_value(units, setup, Key.OPTOUT_LINE, DEFAULT_OPTOUT_LINE)  # its default, no row
    set_value(units, setup, Key.SENDER_TIMEZONE, "Europe/London")  # not a footer key
    assert recorder.seen == []
    assert stored(units)["optout_line"] == DEFAULT_OPTOUT_LINE  # set still upserts

    set_value(units, setup, Key.PRIVACY_NOTICE_URL, "https://nextronai.com/privacy")
    assert [market for market, _ in recorder.seen] == [market_ids["uk"]]
    recorder.seen.clear()

    set_value(units, setup, Key.US_SOLICITATION_LABEL, "Advertisement")  # default "" before
    assert [market for market, _ in recorder.seen] == [market_ids["us"]]


def test_set_rolls_back_when_a_subscriber_fails(
    units: UnitOfWorkFactory, setup: Setup, market_ids: dict[str, str], recorder: Recorder
) -> None:
    recorder.fail = True

    with pytest.raises(InvariantBroken):
        set_value(units, setup, Key.LEGAL_NAME, "Nextron AI Ltd")

    assert stored(units) == {}


@pytest.mark.parametrize(
    "template",
    ["not json", '{"a": 1}', '["{sender_signature}", 3]', "null", "[]", '["hi"]'],
    ids=["not-json", "not-a-list", "not-text", "null", "empty", "no-placeholders"],
)
def test_an_unreadable_or_incomplete_template_counts_as_using_every_placeholder(
    units: UnitOfWorkFactory,
    setup: Setup,
    db_engine: Engine,
    market_ids: dict[str, str],
    recorder: Recorder,
    template: str,
) -> None:
    run_sql(
        db_engine,
        "UPDATE markets SET footer_template = ? WHERE code = 'uk'",
        parameters=(template,),
    )

    save(units, setup, {"us_solicitation_label": "Advertisement"})

    assert [market for market, _ in recorder.seen] == [market_ids["uk"], market_ids["us"]]


def test_footer_templates_reads_each_markets_lines_by_code(
    units: UnitOfWorkFactory, market_ids: dict[str, str]
) -> None:
    with units.open(read_only=True):
        templates = markets_repo.footer_templates()

    us, uk = SEEDED
    assert templates == (
        (market_ids["uk"], uk.footer_template),
        (market_ids["us"], us.footer_template),
    )


# footer_snapshots --------------------------------------------------------------------------------


def in_transaction[R](units: UnitOfWorkFactory, work: Callable[[], R]) -> R:
    @use_case
    def run() -> R:
        with current().transaction():
            return work()

    with units.open():
        return run()


def snapshot_rows(engine: Engine) -> list[tuple[Any, ...]]:
    assert engine.url.database
    reader = sqlite3.connect(engine.url.database)
    try:
        return reader.execute(
            "SELECT id, market_id, sha256, text, created_at FROM footer_snapshots ORDER BY rowid"
        ).fetchall()
    finally:
        reader.close()


@pytest.fixture
def us_footer() -> str:
    us, _ = SEEDED
    values = FooterSettings(
        **FOOTER, optout_line=DEFAULT_OPTOUT_LINE, us_solicitation_label="Advertisement"
    )
    return footer(values, us)


def test_identical_content_reuses_its_snapshot(
    units: UnitOfWorkFactory, db_engine: Engine, market_ids: dict[str, str], us_footer: str
) -> None:
    us_id, uk_id = market_ids["us"], market_ids["uk"]

    first = in_transaction(units, lambda: footer_snapshots.ensure(us_id, us_footer, DEFAULT_NOW))
    again = in_transaction(units, lambda: footer_snapshots.ensure(us_id, us_footer, LATER))
    other_market = in_transaction(
        units, lambda: footer_snapshots.ensure(uk_id, us_footer, LATER)
    )
    other_text = in_transaction(
        units, lambda: footer_snapshots.ensure(us_id, us_footer + "\nMore", LATER)
    )

    assert first == again
    assert len({first, other_market, other_text}) == 3
    rows = snapshot_rows(db_engine)
    assert [row[0] for row in rows] == [first, other_market, other_text]
    assert rows[0] == (
        first,
        us_id,
        footer_sha256(us_footer),
        us_footer,
        "2026-11-16T09:00:00.000000Z",
    )


def test_by_id_reads_a_snapshot_back(
    units: UnitOfWorkFactory, market_ids: dict[str, str], us_footer: str
) -> None:
    us_id = market_ids["us"]
    snapshot_id = in_transaction(
        units, lambda: footer_snapshots.ensure(us_id, us_footer, DEFAULT_NOW)
    )

    with units.open(read_only=True):
        found = footer_snapshots.by_id(snapshot_id)
        unknown = footer_snapshots.by_id(new_id())
        malformed = [footer_snapshots.by_id(text) for text in ("", "x", snapshot_id.lower())]
        not_text = footer_snapshots.by_id(None)  # type: ignore[arg-type]

    assert found == StoredFooterSnapshot(
        id=snapshot_id,
        market_id=us_id,
        sha256=footer_sha256(us_footer),
        text=us_footer,
        created_at=DEFAULT_NOW,
    )
    assert unknown is None and malformed == [None, None, None] and not_text is None


@pytest.mark.parametrize("text", ["", "   ", "\n", None, 3])
def test_a_blank_snapshot_text_is_refused(
    units: UnitOfWorkFactory, db_engine: Engine, market_ids: dict[str, str], text: Any
) -> None:
    us_id = market_ids["us"]

    with pytest.raises(ValueError, match="isn't blank"):
        in_transaction(units, lambda: footer_snapshots.ensure(us_id, text, DEFAULT_NOW))

    assert snapshot_rows(db_engine) == []


def test_a_snapshot_needs_a_market_id(units: UnitOfWorkFactory, us_footer: str) -> None:
    with pytest.raises(ValueError, match="ULID"):
        in_transaction(units, lambda: footer_snapshots.ensure("us", us_footer, DEFAULT_NOW))


def test_the_repository_has_no_update_or_delete() -> None:
    public = {
        name
        for name, value in vars(footer_snapshots).items()
        if callable(value)
        and not name.startswith("_")
        and getattr(value, "__module__", "") == footer_snapshots.__name__
    }

    assert public == {"ensure", "by_id", "StoredFooterSnapshot"}


SHA = "a" * 64
INSERT = (
    "INSERT INTO footer_snapshots (id, market_id, sha256, text, created_at) "
    "VALUES (?, ?, ?, ?, '2026-11-16T09:00:00.000000Z')"
)


@pytest.mark.parametrize(
    ("sha", "text", "constraint"),
    [
        ("a" * 63, "Footer", "ck_footer_snapshots_sha256"),
        ("a" * 65, "Footer", "ck_footer_snapshots_sha256"),
        ("A" * 64, "Footer", "ck_footer_snapshots_sha256"),
        ("g" * 64, "Footer", "ck_footer_snapshots_sha256"),
        ("a" * 63 + " ", "Footer", "ck_footer_snapshots_sha256"),
        (SHA, "", "ck_footer_snapshots_text"),
        (None, "Footer", "NOT NULL"),
        (SHA, None, "NOT NULL"),
    ],
)
def test_the_table_refuses_a_malformed_row(
    db_engine: Engine, market_ids: dict[str, str], sha: Any, text: Any, constraint: str
) -> None:
    with pytest.raises(sqlite3.IntegrityError, match=constraint):
        run_sql(db_engine, INSERT, parameters=(new_id(), market_ids["us"], sha, text))


def test_the_table_refuses_a_repeat_and_an_unknown_market(
    db_engine: Engine, market_ids: dict[str, str]
) -> None:
    run_sql(db_engine, INSERT, parameters=(new_id(), market_ids["us"], SHA, "Footer"))

    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        run_sql(db_engine, INSERT, parameters=(new_id(), market_ids["us"], SHA, "Other"))
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        run_sql(db_engine, INSERT, parameters=(new_id(), new_id(), SHA, "Footer"))
    run_sql(db_engine, INSERT, parameters=(new_id(), market_ids["uk"], SHA, "Footer"))
    assert len(snapshot_rows(db_engine)) == 2


def test_a_market_with_snapshots_cant_be_deleted(
    db_engine: Engine, market_ids: dict[str, str]
) -> None:
    run_sql(db_engine, INSERT, parameters=(new_id(), market_ids["us"], SHA, "Footer"))

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        run_sql(db_engine, "DELETE FROM markets WHERE code = 'us'")


def test_reading_the_footer_writes_nothing(units: UnitOfWorkFactory) -> None:
    read_footer(units)
    assert stored(units) == {}
    assert settings_schema.setting(Key.OPTOUT_LINE).default == DEFAULT_OPTOUT_LINE
