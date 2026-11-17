"""``nextron seed sync`` for the Markets (``nextron.main.seed_sync``, ``Setup.sync_markets``,
``Seeds.sync_seed`` and the ``queries.setup`` Market reads; FR-2, AD-12, NFR-2, NFR-40, Story
3.1), on real units of work in a temporary data dir. Every row of the spec's I/O matrix is
driven through ``main.seed_sync(env="test", seed_dir=...)`` with captured output."""

import dataclasses
import io
import logging
import sqlite3
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from nextron import main
from nextron.app.queries import setup as setup_queries
from nextron.app.setup import api as setup_api
from nextron.app.ops.api import SeedSync, Seeds
from nextron.app.playbook.api import CompanyFactSync
from nextron.app.setup.api import (
    CampaignSync,
    MarketSync,
    OfferSync,
    SegmentSync,
    SequenceSync,
    Setup,
)
from nextron.bootstrap import BootstrapConfig, load_bootstrap
from nextron.db.migrations import head_revision
from nextron.db.repos.setup.markets import StoredMarket
from nextron.db.uow import UnitOfWorkFactory
from nextron.domain import seed_yaml
from nextron.domain.campaign import CampaignDef, validate_campaigns
from nextron.domain.company_facts import SeedFact, validate_company_facts
from nextron.domain.markets import (
    InvalidMarkets,
    MarketDef,
    cctld_country,
    is_free_webmail,
    market_for_country,
    render_footer_template,
    validate_markets,
)
from nextron.domain.offers import OfferDef, validate_offers
from nextron.domain.segments import validate_segments
from nextron.domain.sequences import SequenceDef, validate_sequences
from nextron.domain.settings_schema import TIMEZONE_REASON, Key
from tests.fakes.clock import DEFAULT_NOW, FrozenClock
from tests.migration_chains import run_sql, sha256

SEGMENTS_TEXT = (main.SEED_DIR / main.SEGMENTS_FILE_NAME).read_text(encoding="utf-8")
MARKETS_TEXT = (main.SEED_DIR / main.MARKETS_FILE_NAME).read_text(encoding="utf-8")
SEQUENCES_TEXT = (main.SEED_DIR / main.SEQUENCES_FILE_NAME).read_text(encoding="utf-8")
FACTS_TEXT = (main.SEED_DIR / main.COMPANY_FACTS_FILE_NAME).read_text(encoding="utf-8")
OFFERS_TEXT = (main.SEED_DIR / main.OFFERS_FILE_NAME).read_text(encoding="utf-8")  # Story 4.2
CAMPAIGNS_TEXT = (main.SEED_DIR / main.CAMPAIGNS_FILE_NAME).read_text(encoding="utf-8")
CAMPAIGN_KEYS = ("law_private_ai_us", "law_private_ai_uk", "ops_automation_us", "ops_automation_uk")
SHIPPED_FACT_IDS = (
    "nextron_team",
    "legal_ai",
    "wish_wheels",
    "mp_online_digital",
    "property_3d_sites",
    "audit_offer",
    "aba_formal_opinion_512",
    "sra_ai_notice",
)
LATER = DEFAULT_NOW + timedelta(days=3)
STAMP = "2026-11-16T09:00:00.000000Z"
LATER_STAMP = "2026-11-19T09:00:00.000000Z"

SEGMENTS_FIRST = (
    "segments: 2 changed, 0 unchanged\n"
    "  law_private_ai: created (version 1)\n"
    "  ops_automation: created (version 1)\n"
)
SEGMENTS_UNCHANGED = "segments: 0 changed, 2 unchanged\n"
MARKETS_FIRST = (
    "markets: 2 changed, 0 unchanged\n  us: created (version 1)\n  uk: created (version 1)\n"
)
MARKETS_UNCHANGED = "markets: 0 changed, 2 unchanged\n"
# Story 4.1: the sequences block follows the markets block
SEQUENCES_FIRST = "sequences: 1 changed, 0 unchanged\n  default: created (version 1)\n"
SEQUENCES_UNCHANGED = "sequences: 0 changed, 1 unchanged\n"
# Story 4.3: the company_facts block follows the sequences block
FACTS_FIRST = (
    "company_facts: 8 changed, 0 unchanged\n"
    "  nextron_team: created (version 1)\n"
    "  legal_ai: created (version 1)\n"
    "  wish_wheels: created (version 1)\n"
    "  mp_online_digital: created (version 1)\n"
    "  property_3d_sites: created (version 1)\n"
    "  audit_offer: created (version 1)\n"
    "  aba_formal_opinion_512: created (version 1)\n"
    "  sra_ai_notice: created (version 1)\n"
)
FACTS_UNCHANGED = "company_facts: 0 changed, 8 unchanged\n"
# Story 4.2: the offers and campaigns blocks follow the company_facts block
OFFERS_CAMPAIGNS_FIRST = (
    "offers: 1 changed, 0 unchanged\n  default: created (version 1)\n"
    "campaigns: 4 changed, 0 unchanged\n"
    + "".join(f"  {key}: created (version 1)\n" for key in CAMPAIGN_KEYS)
)
OFFERS_CAMPAIGNS_UNCHANGED = "offers: 0 changed, 1 unchanged\ncampaigns: 0 changed, 4 unchanged\n"

CA_BLOCK = """\
  - code: ca
    name: Canada
    enabled: false
    country_code: CA
    cctlds: [ca]
    cold_email_allowed: false
    eligible_legal_forms: not_checked
    free_webmail_blocked: false
    default_timezone: America/Toronto
    unknown_timezone: America/Toronto
    english_variant: en_us
    footer_template: ['{sender_signature}', '{legal_name}', '{postal_address}', '{optout_line}']
    privacy_line_required: false
"""
WITH_CA = MARKETS_TEXT + CA_BLOCK
UK_FOOTER = (
    "footer_template: ['{sender_signature}', '{legal_name}', '{postal_address}', "
    "'{optout_line}', '{privacy_line}']"
)
FOOTER_VALUES = {
    "sender_signature": "Mac, Nextron AI",
    "legal_name": "Nextron AI Ltd",
    "postal_address": "1 Example Street, London",
    "optout_line": "Reply STOP to opt out.",
}


@dataclasses.dataclass
class Run:
    code: int
    out: str
    err: str


@pytest.fixture
def config() -> BootstrapConfig:
    return load_bootstrap()


@pytest.fixture
def database(config: BootstrapConfig) -> Path:
    """``nextron.db`` at head in the test data dir, as ``serve`` leaves it."""
    config.data_dir.mkdir(parents=True)
    units, revision = main.open_database(config)
    units.engine.dispose()
    assert revision == head_revision()
    return config.data_dir / main.DB_FILE_NAME


@pytest.fixture
def units(database: Path, config: BootstrapConfig) -> Iterator[UnitOfWorkFactory]:
    factory = main.connect_database(config)
    yield factory
    factory.engine.dispose()


def seed_dir(
    tmp_path: Path,
    markets: str | bytes | None,
    name: str = "seed",
    *,
    segments: str = SEGMENTS_TEXT,
) -> Path:
    """A seed folder with ``segments`` and, unless ``None``, ``markets`` as the two files."""
    folder = tmp_path / name
    folder.mkdir()
    (folder / main.SEGMENTS_FILE_NAME).write_text(segments, encoding="utf-8", newline="")
    (folder / main.SEQUENCES_FILE_NAME).write_text(SEQUENCES_TEXT, encoding="utf-8", newline="")
    (folder / main.COMPANY_FACTS_FILE_NAME).write_text(FACTS_TEXT, encoding="utf-8", newline="")
    (folder / main.OFFERS_FILE_NAME).write_text(OFFERS_TEXT, encoding="utf-8", newline="")
    (folder / main.CAMPAIGNS_FILE_NAME).write_text(CAMPAIGNS_TEXT, encoding="utf-8", newline="")
    path = folder / main.MARKETS_FILE_NAME
    if isinstance(markets, bytes):
        path.write_bytes(markets)
    elif markets is not None:
        path.write_text(markets, encoding="utf-8", newline="")
    return folder


def edited(old: str, new: str, text: str = MARKETS_TEXT) -> str:
    assert text.count(old) == 1, old
    return text.replace(old, new)


def sync(folder: Path | None = None, *, clock: FrozenClock | None = None) -> Run:
    out, err = io.StringIO(), io.StringIO()
    code = main.seed_sync(
        env="test",
        seed_dir=folder,
        clock=clock if clock is not None else FrozenClock(),
        out=out,
        err=err,
    )
    return Run(code, out.getvalue(), err.getvalue())


def query(path: Path, sql: str) -> list[tuple[Any, ...]]:
    reader = sqlite3.connect(path)
    try:
        return reader.execute(sql).fetchall()
    finally:
        reader.close()


MARKET_COLUMNS = (
    "id, code, name, enabled, country_code, cctlds, cold_email_allowed, eligible_legal_forms, "
    "free_webmail_blocked, default_timezone, unknown_timezone, unknown_send_window, "
    "english_variant, footer_template, privacy_line_required, version, created_at, updated_at"
)


def market_rows(path: Path) -> list[tuple[Any, ...]]:
    return query(path, f"SELECT {MARKET_COLUMNS} FROM markets ORDER BY code")


def market_row(path: Path, code: str) -> tuple[Any, ...]:
    [row] = [row for row in market_rows(path) if row[1] == code]
    return row


def event_rows(path: Path, entity: str = "markets") -> list[tuple[Any, ...]]:
    return query(
        path,
        "SELECT entity, entity_id, from_status, to_status, cause, cause_ref, actor, at "
        f"FROM events WHERE entity = '{entity}' ORDER BY rowid",
    )


def table_dump(path: Path) -> dict[str, list[Any]]:
    names = [
        name
        for (name,) in query(
            path, "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
    ]
    return {name: query(path, f"SELECT * FROM {name} ORDER BY rowid") for name in names}


def shipped_markets() -> tuple[MarketDef, ...]:
    return validate_markets(seed_yaml.parse(MARKETS_TEXT))


def shipped_sequences() -> tuple[SequenceDef, ...]:  # Story 4.1: sync_seed's third argument
    return validate_sequences(seed_yaml.parse(SEQUENCES_TEXT))


def shipped_facts() -> tuple[SeedFact, ...]:  # Story 4.3: sync_seed's fourth argument
    return validate_company_facts(seed_yaml.parse(FACTS_TEXT))


def shipped_offers() -> tuple[OfferDef, ...]:  # Story 4.2: sync_seed's fifth argument
    return validate_offers(seed_yaml.parse(OFFERS_TEXT))


def shipped_campaigns() -> tuple[CampaignDef, ...]:  # and its sixth
    return validate_campaigns(seed_yaml.parse(CAMPAIGNS_TEXT))


def stored_definitions(units: UnitOfWorkFactory) -> tuple[MarketDef, ...]:
    with units.open(read_only=True):
        return tuple(seeded.definition for seeded in setup_queries.markets())


# The I/O matrix: first sync -------------------------------------------------------------------


def test_the_first_sync_creates_the_us_and_uk_at_version_1(database: Path) -> None:
    run = sync()

    assert (run.code, run.out, run.err) == (
        0,
        SEGMENTS_FIRST + MARKETS_FIRST + SEQUENCES_FIRST + FACTS_FIRST + OFFERS_CAMPAIGNS_FIRST,
        "",
    )
    uk, us = market_rows(database)
    legal_forms = (
        '["ltd","plc","private-limited-guarant-nsc",'
        '"private-limited-guarant-nsc-limited-exemption",'
        '"private-limited-shares-section-30-exemption","private-unlimited",'
        '"private-unlimited-nsc","llp"]'
    )
    assert uk[1:] == (
        "uk",
        "United Kingdom",
        1,
        "GB",
        '["uk"]',
        1,
        legal_forms,
        1,
        "Europe/London",
        "Europe/London",
        None,
        "en_gb",
        '["{sender_signature}","{legal_name}","{postal_address}","{optout_line}",'
        '"{privacy_line}"]',
        1,
        1,
        STAMP,
        STAMP,
    )
    assert us[1:] == (
        "us",
        "United States",
        1,
        "US",
        '["us"]',
        1,
        None,
        0,
        "America/New_York",
        "America/New_York",
        "11:00-17:00",
        "en_us",
        '["{sender_signature}","{legal_name}","{postal_address}","{optout_line}",'
        '"{us_solicitation_label}"]',
        0,
        1,
        STAMP,
        STAMP,
    )
    assert len(us[0]) == len(uk[0]) == 26
    assert event_rows(database) == [  # in file order: us, then uk
        ("markets", us[0], None, "v1", "seed_sync", None, "seed", STAMP),
        ("markets", uk[0], None, "v1", "seed_sync", None, "seed", STAMP),
    ]
    assert len(event_rows(database, "segments")) == 2


def test_the_stored_markets_decode_to_the_shipped_definitions(units: UnitOfWorkFactory) -> None:
    sync()

    us, uk = shipped_markets()
    assert stored_definitions(units) == (uk, us)  # by code


# The I/O matrix: second sync ---------------------------------------------------------------------


def test_a_second_sync_reports_0_changed_and_writes_nothing(database: Path) -> None:
    sync()
    before = table_dump(database)

    run = sync(clock=FrozenClock(LATER))

    assert (run.code, run.out, run.err) == (
        0,
        SEGMENTS_UNCHANGED + MARKETS_UNCHANGED + SEQUENCES_UNCHANGED
        + FACTS_UNCHANGED + OFFERS_CAMPAIGNS_UNCHANGED,
        "",
    )
    assert table_dump(database) == before


# The I/O matrix: changed -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("old", "new", "check"),
    [
        ("name: United Kingdom", "name: Great Britain", lambda row: row[2] == "Great Britain"),
        (
            UK_FOOTER,
            UK_FOOTER.replace("'{postal_address}'", "'Registered office: {postal_address}'"),
            lambda row: "Registered office: {postal_address}" in row[13],
        ),
    ],
)
def test_a_changed_uk_gets_version_2_and_one_event(
    database: Path, tmp_path: Path, old: str, new: str, check: Callable[[tuple[Any, ...]], bool]
) -> None:
    sync()
    uk_before, us_before = market_rows(database)
    events_before = event_rows(database)

    run = sync(seed_dir(tmp_path, edited(old, new)), clock=FrozenClock(LATER))

    assert (run.code, run.err) == (0, "")
    assert run.out == (
        SEGMENTS_UNCHANGED
        + "markets: 1 changed, 1 unchanged\n  uk: changed (version 1 -> 2)\n"
        + SEQUENCES_UNCHANGED + FACTS_UNCHANGED + OFFERS_CAMPAIGNS_UNCHANGED
    )
    uk_after, us_after = market_rows(database)
    assert us_after == us_before
    assert uk_after[0] == uk_before[0]  # the same row
    assert (uk_after[15], uk_after[16], uk_after[17]) == (2, STAMP, LATER_STAMP)
    assert check(uk_after)
    assert event_rows(database) == [
        *events_before,
        ("markets", uk_before[0], "v1", "v2", "seed_sync", None, "seed", LATER_STAMP),
    ]
    # and the next sync of the same file changes nothing
    again = sync(seed_dir(tmp_path, edited(old, new), "again"))
    assert again.out == (
        SEGMENTS_UNCHANGED + MARKETS_UNCHANGED + SEQUENCES_UNCHANGED
        + FACTS_UNCHANGED + OFFERS_CAMPAIGNS_UNCHANGED
    )


# The I/O matrix: a CA Market added, and left out -------------------------------------------------


def test_a_disabled_ca_market_loads_without_code_changes(
    database: Path, units: UnitOfWorkFactory, tmp_path: Path
) -> None:
    sync()

    run = sync(seed_dir(tmp_path, WITH_CA))

    assert (run.code, run.err) == (0, "")
    assert run.out == (
        SEGMENTS_UNCHANGED
        + "markets: 1 changed, 2 unchanged\n  ca: created (version 1)\n"
        + SEQUENCES_UNCHANGED + FACTS_UNCHANGED + OFFERS_CAMPAIGNS_UNCHANGED
    )
    assert market_row(database, "ca")[2:5] == ("Canada", 0, "CA")
    markets = stored_definitions(units)
    assert market_for_country("CA", markets) is None
    assert market_for_country("US", markets) is not None

    enabled = edited("enabled: false", "enabled: true", WITH_CA)
    run = sync(seed_dir(tmp_path, enabled, "enabled"), clock=FrozenClock(LATER))

    assert "  ca: changed (version 1 -> 2)\n" in run.out
    markets = stored_definitions(units)
    found = market_for_country("CA", markets)
    assert found is not None and (found.code, found.cold_email_allowed) == ("ca", False)


def test_a_stored_market_not_in_the_file_is_left_as_it_is(
    database: Path, tmp_path: Path
) -> None:
    sync(seed_dir(tmp_path, WITH_CA))
    ca_row = market_row(database, "ca")
    events_before = event_rows(database)

    run = sync(clock=FrozenClock(LATER))

    assert (run.code, run.err) == (0, "")
    assert run.out == (
        SEGMENTS_UNCHANGED
        + MARKETS_UNCHANGED
        + "  ca: not in seed/markets.yaml, left as it is (version 1)\n"
        + SEQUENCES_UNCHANGED + FACTS_UNCHANGED + OFFERS_CAMPAIGNS_UNCHANGED
    )
    assert market_row(database, "ca") == ca_row
    assert event_rows(database) == events_before


def test_the_nullable_columns_change_both_ways(
    database: Path, units: UnitOfWorkFactory, tmp_path: Path
) -> None:
    """The US's window goes from set to NULL and back, and its legal forms from not_checked
    (NULL) to a JSON list and back, each a change the query decodes as the file says."""
    window_line = (
        "    unknown_send_window: '11:00-17:00'  # Eastern, for a Contact with no known state\n"
    )
    changed = edited(window_line, "")
    changed = edited("eligible_legal_forms: not_checked", "eligible_legal_forms: [llc]", changed)

    def check(text: str, window: str | None, legal_forms: str | None) -> None:
        us = market_row(database, "us")
        assert (us[11], us[7]) == (window, legal_forms)
        with units.open(read_only=True):
            seeded = setup_queries.market("us")
        assert seeded is not None
        assert seeded.definition == validate_markets(seed_yaml.parse(text))[0]

    sync()
    check(MARKETS_TEXT, "11:00-17:00", None)
    steps = [
        (seed_dir(tmp_path, changed), changed, "1 -> 2", None, '["llc"]'),
        (None, MARKETS_TEXT, "2 -> 3", "11:00-17:00", None),
    ]
    for folder, text, versions, window, legal_forms in steps:
        run = sync(folder, clock=FrozenClock(LATER))

        assert (run.code, run.err) == (0, "")
        assert run.out == (
            SEGMENTS_UNCHANGED
            + f"markets: 1 changed, 1 unchanged\n  us: changed (version {versions})\n"
            + SEQUENCES_UNCHANGED + FACTS_UNCHANGED + OFFERS_CAMPAIGNS_UNCHANGED
        )
        check(text, window, legal_forms)
    assert [row[3] for row in event_rows(database)][-2:] == ["v2", "v3"]


# The I/O matrix: invalid files -------------------------------------------------------------------

UNKNOWN_PLACEHOLDER = (
    "holds an unknown placeholder: the placeholders are {sender_signature}, {legal_name}, "
    "{postal_address}, {optout_line}, {privacy_line}, {us_solicitation_label}"
)

INVALID = [
    (
        edited("country_code: GB", "country_code: US"),
        "markets[uk].country_code: repeats the country code of an earlier Market (US)",
    ),
    (
        edited("eligible_legal_forms: not_checked", "eligible_legal_forms: []"),
        "markets[us].eligible_legal_forms: must hold at least 1 item (write not_checked when "
        "legal forms aren't checked)",
    ),
    (
        edited("cold_email_allowed: false", "cold_email_allowed: true", WITH_CA),
        "markets[ca].cold_email_allowed: may be true only for a US or GB Market (NFR-5)",
    ),
    (
        edited("free_webmail_blocked: true", "free_webmail_blocked: false"),
        "markets[uk].free_webmail_blocked: must be true for a GB Market (PECR: corporate "
        "subscribers only)",
    ),
    (
        edited("'{optout_line}', '{us_solicitation_label}'", "'{us_solicitation_label}'"),
        "markets[us].footer_template: must hold {optout_line} (FR-23)",
    ),
    (
        edited(", '{us_solicitation_label}']", "]"),
        "markets[us].footer_template: a US Market's template must hold "
        "{us_solicitation_label} (NFR-2)",
    ),
    (
        edited("'{privacy_line}'", "'Privacy: {privacy_line}'"),
        "markets[uk].footer_template[4]: {privacy_line} must stand alone on its line",
    ),
    (
        edited("'{privacy_line}'", "'{privacy_line}', '{company_number}'"),
        f"markets[uk].footer_template[5]: {UNKNOWN_PLACEHOLDER}",
    ),
    (
        edited("default_timezone: Europe/London", "default_timezone: GMT"),
        f"markets[uk].default_timezone: {TIMEZONE_REASON}",
    ),
    (
        edited("cctlds: [us]", "cctlds: [ca]"),
        "markets[us].cctlds[0]: is the ccTLD of CA, not of the Market's country code US",
    ),
    (
        edited("      - llp\n", "      - llp\n      - limited-partnership\n"),
        "markets[uk].eligible_legal_forms[8]: isn't a corporate-subscriber company type a GB "
        "Market may list (PECR)",
    ),
]


@pytest.mark.parametrize(("text", "message"), INVALID)
def test_an_invalid_market_names_its_field_and_changes_nothing(
    database: Path, tmp_path: Path, text: str, message: str
) -> None:
    sync()
    before = sha256(database)
    segments = SEGMENTS_TEXT.replace("weight: 35", "weight: 40", 1)  # a valid Segment change

    run = sync(seed_dir(tmp_path, text, segments=segments), clock=FrozenClock(LATER))

    assert (run.code, run.out) == (1, "")
    assert run.err == f"seed/markets.yaml: {message}\n"
    assert sha256(database) == before  # the Segments are untouched too


def test_an_invalid_market_on_a_new_database_writes_nothing(
    database: Path, tmp_path: Path
) -> None:
    before = sha256(database)

    run = sync(seed_dir(tmp_path, edited("cctlds: [uk]", "cctlds: [us]")))

    assert run.err == (
        "seed/markets.yaml: markets[uk].cctlds[0]: is also a ccTLD of an earlier Market (us)\n"
    )
    assert sha256(database) == before


def test_a_syntax_problem_names_the_markets_file_and_its_line(
    database: Path, tmp_path: Path
) -> None:
    text = edited("'11:00-17:00'", "11:00-17:00")
    line = next(
        number
        for number, content in enumerate(text.splitlines(), 1)
        if "unknown_send_window: 11:00" in content
    )
    before = sha256(database)

    run = sync(seed_dir(tmp_path, text))

    assert run.code == 1
    assert run.err.startswith(f"seed/markets.yaml: line {line}: ")
    assert "number, date or time" in run.err
    assert sha256(database) == before


def test_a_segments_problem_is_found_before_the_markets_file_is_read(
    database: Path, tmp_path: Path
) -> None:
    run = sync(seed_dir(tmp_path, None, segments="segments: yes\n"))

    assert run.err.startswith("seed/segments.yaml: line 1: ")


def test_a_markets_problem_is_found_before_the_database_is_checked(
    config: BootstrapConfig, tmp_path: Path
) -> None:
    run = sync(seed_dir(tmp_path, "markets: yes\n"))

    assert run.code == 1
    assert run.err.startswith("seed/markets.yaml: line 1: ")
    assert not config.data_dir.exists()


# The I/O matrix: the file ------------------------------------------------------------------------


def test_a_missing_markets_file_is_named_and_nothing_is_written(
    database: Path, tmp_path: Path
) -> None:
    before = sha256(database)

    run = sync(seed_dir(tmp_path, None))

    assert (run.code, run.out, run.err) == (1, "", "seed/markets.yaml: the file doesn't exist\n")
    assert sha256(database) == before


def test_a_markets_file_over_1_mib_is_refused(database: Path, tmp_path: Path) -> None:
    padding = main.MAX_SEED_FILE_BYTES + 1 - len(MARKETS_TEXT.encode("utf-8"))
    text = MARKETS_TEXT + "#" * (padding - 1) + "\n"
    assert len(text.encode("utf-8")) == main.MAX_SEED_FILE_BYTES + 1
    before = sha256(database)

    run = sync(seed_dir(tmp_path, text))

    assert (run.code, run.err) == (1, "seed/markets.yaml: the file is larger than 1 MiB\n")
    assert sha256(database) == before


def test_a_markets_file_of_exactly_1_mib_is_read(database: Path, tmp_path: Path) -> None:
    padding = main.MAX_SEED_FILE_BYTES - len(MARKETS_TEXT.encode("utf-8"))
    text = MARKETS_TEXT + "#" * (padding - 1) + "\n"

    run = sync(seed_dir(tmp_path, text))

    assert (run.code, run.out) == (
        0,
        SEGMENTS_FIRST + MARKETS_FIRST + SEQUENCES_FIRST + FACTS_FIRST + OFFERS_CAMPAIGNS_FIRST,
    )


def test_a_markets_file_that_is_not_utf8_is_refused(database: Path, tmp_path: Path) -> None:
    data = MARKETS_TEXT.encode("utf-8").replace(b"# The Markets", b"# The \x96 Markets", 1)
    before = sha256(database)

    run = sync(seed_dir(tmp_path, data))

    assert (run.code, run.err) == (1, "seed/markets.yaml: the file isn't valid UTF-8\n")
    assert sha256(database) == before


def test_an_unreadable_markets_file_is_refused(database: Path, tmp_path: Path) -> None:
    folder = seed_dir(tmp_path, None)
    (folder / main.MARKETS_FILE_NAME).mkdir()  # a folder, not a file

    run = sync(folder)

    assert run.code == 1
    assert run.err.startswith("seed/markets.yaml: the file can't be read (")


def test_a_markets_file_with_a_bom_and_crlf_line_ends_is_read(
    database: Path, tmp_path: Path
) -> None:
    run = sync(seed_dir(tmp_path, "﻿" + MARKETS_TEXT.replace("\n", "\r\n")))

    assert (run.code, run.out) == (
        0,
        SEGMENTS_FIRST + MARKETS_FIRST + SEQUENCES_FIRST + FACTS_FIRST + OFFERS_CAMPAIGNS_FIRST,
    )
    assert sync().out == (  # the same definitions
        SEGMENTS_UNCHANGED + MARKETS_UNCHANGED + SEQUENCES_UNCHANGED
        + FACTS_UNCHANGED + OFFERS_CAMPAIGNS_UNCHANGED
    )


# The I/O matrix: atomic --------------------------------------------------------------------------


def test_a_failure_while_recording_a_market_rolls_back_the_segments_too(
    database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real = setup_api._record_market

    def record_then_fail(market_id: str, *args: Any) -> None:
        calls.append(market_id)
        real(market_id, *args)
        raise RuntimeError("boom, with details that are never printed")

    monkeypatch.setattr(setup_api, "_record_market", record_then_fail)

    run = sync()

    assert (run.code, run.out) == (1, "")
    assert run.err == "seed sync failed (RuntimeError); nothing was changed\n"
    assert len(calls) == 1
    assert query(database, "SELECT count(*) FROM segments") == [(0,)]
    assert market_rows(database) == []
    assert query(database, "SELECT count(*) FROM events") == [(0,)]


def test_sync_seed_runs_both_syncs_in_one_transaction(units: UnitOfWorkFactory) -> None:
    opened: list[str] = []
    segments = validate_segments(seed_yaml.parse(SEGMENTS_TEXT))
    markets = shipped_markets()

    class CountingClock(FrozenClock):
        def now(self) -> Any:
            opened.append("clock")
            return super().now()

    with units.open() as uow:
        real = uow.transaction

        def counted() -> Any:
            opened.append("transaction")
            return real()

        uow.transaction = counted  # type: ignore[method-assign]
        result = Seeds(CountingClock()).sync_seed(
            segments,
            markets,
            shipped_sequences(),
            shipped_facts(),
            shipped_offers(),
            shipped_campaigns(),
        )

    assert opened == ["clock", "transaction"]
    assert result == SeedSync(
        SegmentSync((("law_private_ai", 1), ("ops_automation", 1)), (), (), ()),
        MarketSync((("us", 1), ("uk", 1)), (), (), ()),
        SequenceSync((("default", 1),), (), (), ()),
        CompanyFactSync(
            tuple((fact_id, 1) for fact_id in SHIPPED_FACT_IDS), (), (), (), ()
        ),
        OfferSync((("default", 1),), (), (), ()),
        CampaignSync(tuple((key, 1) for key in CAMPAIGN_KEYS), (), (), ()),
    )


# The I/O matrix: the footer ----------------------------------------------------------------------


def test_the_us_footer_drops_a_blank_label_and_shows_a_set_one(units: UnitOfWorkFactory) -> None:
    sync()

    def us_footer() -> list[str]:
        with units.open(read_only=True):
            us = setup_queries.market("us")
            label = setup_queries.get(Key.US_SOLICITATION_LABEL)
        assert us is not None
        values = {**FOOTER_VALUES, "us_solicitation_label": label}
        return render_footer_template(us.definition.footer_template, values).split("\n")

    blank = us_footer()
    with units.open():
        Setup(FrozenClock()).set(Key.US_SOLICITATION_LABEL, "Advertisement")
    labelled = us_footer()

    assert blank == list(FOOTER_VALUES.values())
    assert labelled == [*FOOTER_VALUES.values(), "Advertisement"]


# The I/O matrix: domains -------------------------------------------------------------------------


def test_domains_map_to_the_stored_markets(units: UnitOfWorkFactory) -> None:
    sync()
    markets = stored_definitions(units)

    uk = market_for_country(cctld_country("firm.co.uk"), markets)
    assert uk is not None and uk.code == "uk"
    assert cctld_country("firm.com") is None
    assert cctld_country("firm.de") == "DE"
    assert market_for_country(cctld_country("firm.de"), markets) is None
    assert is_free_webmail("Bob@Mail.GMAIL.com.") and is_free_webmail("x.btinternet.com")
    assert uk.free_webmail_blocked


# The queries -------------------------------------------------------------------------------------


def test_the_queries_read_the_seeded_markets_by_code(units: UnitOfWorkFactory) -> None:
    us, uk = shipped_markets()
    sync()

    with units.open(read_only=True):
        everything = setup_queries.markets()
        one = setup_queries.market("us")
        none = setup_queries.market("missing")

    assert [(m.code, m.version, m.definition) for m in everything] == [("uk", 1, uk), ("us", 1, us)]
    assert one == everything[1]
    assert none is None
    assert one is not None and len(one.id) == 26
    with pytest.raises(dataclasses.FrozenInstanceError):
        one.version = 2  # type: ignore[misc]


def test_the_queries_return_nothing_before_a_sync(units: UnitOfWorkFactory) -> None:
    with units.open(read_only=True):
        assert setup_queries.markets() == ()
        assert setup_queries.market("us") is None


@pytest.mark.parametrize(
    ("sql", "field"),
    [
        (
            "UPDATE markets SET footer_template = '[]' WHERE code = 'uk'",
            "markets[uk].footer_template",
        ),
        ("UPDATE markets SET cctlds = 'not json' WHERE code = 'uk'", "markets[uk].cctlds"),
        (
            "UPDATE markets SET eligible_legal_forms = NULL WHERE code = 'uk'",
            "markets[uk].eligible_legal_forms",
        ),
        (
            "UPDATE markets SET unknown_send_window = '17:00-11:00' WHERE code = 'us'",
            "markets[us].unknown_send_window",
        ),
    ],
)
def test_a_stored_market_that_no_longer_validates_raises(
    database: Path, units: UnitOfWorkFactory, sql: str, field: str
) -> None:
    sync()
    run_sql(database, sql)

    with units.open(read_only=True), pytest.raises(InvalidMarkets) as caught:
        setup_queries.markets()
    assert caught.value.field == field
    before = table_dump(database)

    run = sync(clock=FrozenClock(LATER))  # a sync can't decode it either: nothing is written

    assert (run.code, run.out) == (1, "")
    assert run.err.startswith(f"seed/markets.yaml: {field}: ")
    assert table_dump(database) == before


def test_the_version_check_refuses_a_version_below_1(database: Path) -> None:
    sync()

    with pytest.raises(sqlite3.IntegrityError, match="ck_markets_version"):
        run_sql(database, "UPDATE markets SET version = 0")

    assert [row[15] for row in market_rows(database)] == [1, 1]


# The use case ------------------------------------------------------------------------------------


def test_the_sync_logs_its_counts(
    database: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.setup.api")
    caplog.set_level(logging.INFO, logger="nextron.app.playbook.api")  # Story 4.3

    sync()
    sync(clock=FrozenClock(LATER))
    sync(seed_dir(tmp_path, WITH_CA))
    run = sync(seed_dir(tmp_path, edited("name: United States", "name: USA"), "next"))

    assert run.code == 0
    records = [r for r in caplog.records if r.getMessage() == "markets_synced"]
    assert {r.levelno for r in records} == {logging.INFO}
    counts = ("created_count", "changed_count", "unchanged_count", "not_in_file_count")
    assert [[getattr(r, name) for name in counts] for r in records] == [
        [2, 0, 0, 0],  # the first sync
        [0, 0, 2, 0],  # the same file again
        [1, 0, 2, 0],  # ca added
        [0, 1, 1, 1],  # us changed, uk unchanged, ca not in the file
    ]
    names = [r.getMessage() for r in caplog.records if r.getMessage().endswith("_synced")]
    assert names == [
        "segments_synced",
        "markets_synced",
        "sequences_synced",
        "offers_synced",
        "campaigns_synced",
        "company_facts_synced",
    ] * 4


def test_sync_markets_returns_what_it_did(units: UnitOfWorkFactory) -> None:
    us, uk = shipped_markets()
    with units.open():
        first = Setup(FrozenClock()).sync_markets([uk, us])
    with units.open():
        second = Setup(FrozenClock()).sync_markets([dataclasses.replace(uk, name="Britain")])
    with units.open():
        third = Setup(FrozenClock()).sync_markets(())

    assert first == MarketSync((("uk", 1), ("us", 1)), (), (), ())
    assert second == MarketSync((), (("uk", 1, 2),), (), (("us", 1),))
    assert third == MarketSync((), (), (), (("uk", 2), ("us", 1)))


@pytest.mark.parametrize(
    ("make", "error"),
    [
        (lambda us, uk: [us, {"code": "x"}], TypeError),
        (lambda us, uk: [us, "uk"], TypeError),
        (lambda us, uk: [us, uk, us], ValueError),
        (lambda us, uk: [us, dataclasses.replace(uk, code="us")], ValueError),
        (lambda us, uk: [us, dataclasses.replace(uk, free_webmail_blocked=False)], InvalidMarkets),
        (lambda us, uk: [us, dataclasses.replace(uk, country_code="US")], InvalidMarkets),
    ],
)
def test_sync_markets_refuses_bad_input_before_any_transaction(
    database: Path,
    units: UnitOfWorkFactory,
    monkeypatch: pytest.MonkeyPatch,
    make: Callable[[MarketDef, MarketDef], list[Any]],
    error: type[Exception],
) -> None:
    us, uk = shipped_markets()
    before = sha256(database)

    def no_transaction() -> None:
        raise AssertionError("a transaction was opened")

    with units.open() as uow:
        monkeypatch.setattr(uow, "transaction", no_transaction)
        with pytest.raises(error):
            Setup(FrozenClock()).sync_markets(make(us, uk))

    assert market_rows(database) == []
    assert sha256(database) == before


def test_sync_seed_refuses_bad_markets_before_any_transaction(
    database: Path, units: UnitOfWorkFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    segments = validate_segments(seed_yaml.parse(SEGMENTS_TEXT))
    us, uk = shipped_markets()

    def no_transaction() -> None:
        raise AssertionError("a transaction was opened")

    with units.open() as uow:
        monkeypatch.setattr(uow, "transaction", no_transaction)
        with pytest.raises(ValueError, match="sync_markets got a code more than once: us"):
            Seeds(FrozenClock()).sync_seed(
                segments,
                [us, us],
                shipped_sequences(),
                shipped_facts(),
                shipped_offers(),
                shipped_campaigns(),
            )

    assert query(database, "SELECT count(*) FROM segments") == [(0,)]


def test_a_hand_built_market_is_stored_as_validation_returns_it(units: UnitOfWorkFactory) -> None:
    us, uk = shipped_markets()
    with units.open():
        Setup(FrozenClock()).sync_markets([us, uk])
    loose: dict[str, Any] = {"english_variant": "en_us", "cctlds": ["us"]}  # not enum, tuple
    hand_built = dataclasses.replace(us, **loose)

    with units.open():
        result = Setup(FrozenClock()).sync_markets([hand_built, uk])

    assert result.unchanged == ("us", "uk")


def test_a_country_a_stored_market_left_out_still_uses_is_refused(
    database: Path, units: UnitOfWorkFactory
) -> None:
    us, uk = shipped_markets()
    with units.open():  # the UK stored under an old code
        Setup(FrozenClock()).sync_markets([dataclasses.replace(uk, code="old_uk")])
    before = table_dump(database)

    run = sync(clock=FrozenClock(LATER))

    assert (run.code, run.out) == (1, "")
    assert run.err == (
        "seed/markets.yaml: markets[uk].country_code: is also the country code of the stored "
        "Market old_uk, which is no longer in the file: keep that Market's code\n"
    )
    assert table_dump(database) == before


def test_a_cctld_a_stored_market_left_out_still_uses_is_refused() -> None:
    """Unreachable through valid stored rows (a ccTLD names its Market's country, so the country
    check fires first), so the check itself is called with a hand-built row."""
    us, uk = shipped_markets()
    stored = StoredMarket(
        id="0" * 26,
        code="old",
        version=1,
        created_at=DEFAULT_NOW,
        updated_at=DEFAULT_NOW,
        market=dataclasses.replace(uk, code="old", country_code="IE"),
    )

    with pytest.raises(InvalidMarkets) as caught:
        setup_api._refuse_shared_markets([us, uk], [stored])

    assert (caught.value.field, caught.value.reason) == (
        "markets[uk].cctlds",
        "holds uk, a ccTLD of the stored Market old, which is no longer in the file: keep that "
        "Market's code",
    )


def test_sync_markets_never_touches_another_table(
    database: Path, units: UnitOfWorkFactory
) -> None:
    us, uk = shipped_markets()
    before = table_dump(database)

    with units.open():
        Setup(FrozenClock()).sync_markets([us, uk])

    after = table_dump(database)
    changed = sorted(name for name in after if after[name] != before[name])
    assert changed == ["events", "markets"]


# The command line --------------------------------------------------------------------------------


def test_cli_help_names_both_seed_files(capsys: pytest.CaptureFixture[str]) -> None:
    from nextron import __main__ as cli

    with pytest.raises(SystemExit) as caught:
        cli.main(["seed", "--help"])

    assert caught.value.code == 0
    shown = capsys.readouterr().out
    assert "seed/markets.yaml" in shown and "Markets" in shown


def test_cli_seed_sync_loads_the_markets_end_to_end(database: Path) -> None:
    from nextron import __main__ as cli

    assert cli.main(["seed", "sync"]) == 0
    assert [row[1] for row in market_rows(database)] == ["uk", "us"]
