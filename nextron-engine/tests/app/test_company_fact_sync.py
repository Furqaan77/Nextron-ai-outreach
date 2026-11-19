"""The Approved Company Facts (FR-72, AD-7, AD-12, NFR-24, Story 4.3) on real units of work in a
temporary data dir: every seed row of the spec's I/O matrix through ``main.seed_sync(env="test",
seed_dir=...)`` with captured output; the ``Seeds`` transaction; ``queries.playbook``
(``company_facts``, ``fact``, ``allowed_facts``); the ``CompanyFacts`` use cases (``add``,
``edit``, ``retract``) with their events and logs; the table's triggers and checks; a stored row
that no longer decodes; and the CLI help."""

import dataclasses
import io
import json
import logging
import sqlite3
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from nextron import main
from nextron.app.ops.api import Seeds
from nextron.app.playbook import api as playbook_api
from nextron.app.playbook.api import (
    CompanyFacts,
    CompanyFactSync,
    FactRetracted,
    FactSaved,
)
from nextron.app.queries import playbook as playbook_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.setup.segment_editing import SegmentEditor
from nextron.bootstrap import BootstrapConfig, load_bootstrap
from nextron.db.migrations import head_revision
from nextron.db.uow import UnitOfWorkFactory
from nextron.domain import seed_yaml
from nextron.domain.campaign import CampaignDef, validate_campaigns
from nextron.domain.company_facts import (
    ABA_REASON,
    FactContent,
    FactCreator,
    FactFields,
    FactType,
    InvalidCompanyFacts,
    InvalidFact,
    SeedFact,
    validate_company_facts,
)
from nextron.domain.markets import validate_markets
from nextron.domain.offers import OfferDef, validate_offers
from nextron.domain.segments import file_form, validate_segments
from nextron.domain.sequences import validate_sequences
from tests.fakes.clock import DEFAULT_NOW, FrozenClock
from tests.migration_chains import run_sql, sha256

SEGMENTS_TEXT = (main.SEED_DIR / main.SEGMENTS_FILE_NAME).read_text(encoding="utf-8")
MARKETS_TEXT = (main.SEED_DIR / main.MARKETS_FILE_NAME).read_text(encoding="utf-8")
SEQUENCES_TEXT = (main.SEED_DIR / main.SEQUENCES_FILE_NAME).read_text(encoding="utf-8")
FACTS_TEXT = (main.SEED_DIR / main.COMPANY_FACTS_FILE_NAME).read_text(encoding="utf-8")
OFFERS_TEXT = (main.SEED_DIR / main.OFFERS_FILE_NAME).read_text(encoding="utf-8")  # Story 4.2
CAMPAIGNS_TEXT = (main.SEED_DIR / main.CAMPAIGNS_FILE_NAME).read_text(encoding="utf-8")
CAMPAIGN_KEYS = ("law_private_ai_us", "law_private_ai_uk", "ops_automation_us", "ops_automation_uk")
LATER = DEFAULT_NOW + timedelta(days=3)
STAMP = "2026-11-16T09:00:00.000000Z"
LATER_STAMP = "2026-11-19T09:00:00.000000Z"
FACT_IDS = (
    "nextron_team",
    "legal_ai",
    "wish_wheels",
    "mp_online_digital",
    "property_3d_sites",
    "audit_offer",
    "aba_formal_opinion_512",
    "sra_ai_notice",
)
OTHERS_FIRST = (
    "segments: 2 changed, 0 unchanged\n"
    "  law_private_ai: created (version 1)\n"
    "  ops_automation: created (version 1)\n"
    "markets: 2 changed, 0 unchanged\n"
    "  us: created (version 1)\n"
    "  uk: created (version 1)\n"
    "sequences: 1 changed, 0 unchanged\n"
    "  default: created (version 1)\n"
)
OTHERS_UNCHANGED = (
    "segments: Segments are managed in the dashboard; seed/segments.yaml ignored\n"
    "markets: 0 changed, 2 unchanged\n"
    "sequences: Sequences are managed in the dashboard; seed/sequences.yaml ignored\n"
)
FACTS_FIRST = "company_facts: 8 changed, 0 unchanged\n" + "".join(
    f"  {fact_id}: created (version 1)\n" for fact_id in FACT_IDS
)
FACTS_UNCHANGED = "company_facts: 0 changed, 8 unchanged\n"
# Story 4.2: the offers and campaigns blocks follow the company_facts block
OFFERS_CAMPAIGNS_FIRST = (
    "offers: 1 changed, 0 unchanged\n  default: created (version 1)\n"
    "campaigns: 4 changed, 0 unchanged\n"
    + "".join(f"  {key}: created (version 1)\n" for key in CAMPAIGN_KEYS)
)
OFFERS_CAMPAIGNS_UNCHANGED = "offers: 0 changed, 1 unchanged\ncampaigns: 0 changed, 4 unchanged\n"
LEGAL_AI_WORDING = (
    "    wording: Built an AI legal assistant platform for research and document help for a "
    "legal-tech venture (Legal AI).\n"
)
NEW_LEGAL_AI_WORDING = (
    "    wording: Built an AI legal research and drafting assistant for a legal-tech venture "
    "(Legal AI).\n"
)
WISH_WHEELS_START = "    wording: Built a single operations platform covering CRM"
FACT_COLUMNS = (
    "id, fact_id, version, type, wording, source_url, allowed_segment_ids, allowed_market_ids, "
    "metric, metric_confirmed_at, scope_note, last_checked_on, created_by, created_at, "
    "retracted_at"
)
STAT_X = FactFields(
    type="statistic",
    wording="Firms that use private AI keep client data on their own servers.",
    source_url="https://example.com/survey",
    segments=(),
    markets=(),
    metric="",
    metric_confirmed_at="",
    scope_note="",
    last_checked_on="2026-11-15",
)


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


def seed_dir(tmp_path: Path, facts: str | bytes | None, name: str = "seed") -> Path:
    """A seed folder with the shipped Segments, Markets and Sequences and, unless ``None``,
    ``facts`` as the fourth file."""
    folder = tmp_path / name
    folder.mkdir()
    for file_name, text in (
        (main.SEGMENTS_FILE_NAME, SEGMENTS_TEXT),
        (main.MARKETS_FILE_NAME, MARKETS_TEXT),
        (main.SEQUENCES_FILE_NAME, SEQUENCES_TEXT),
        (main.OFFERS_FILE_NAME, OFFERS_TEXT),
        (main.CAMPAIGNS_FILE_NAME, CAMPAIGNS_TEXT),
    ):
        (folder / file_name).write_text(text, encoding="utf-8", newline="")
    path = folder / main.COMPANY_FACTS_FILE_NAME
    if isinstance(facts, bytes):
        path.write_bytes(facts)
    elif facts is not None:
        path.write_text(facts, encoding="utf-8", newline="")
    return folder


def edited(old: str, new: str, text: str = FACTS_TEXT) -> str:
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


def fact_rows(path: Path, fact_id: str | None = None) -> list[tuple[Any, ...]]:
    where = "" if fact_id is None else f" WHERE fact_id = '{fact_id}'"
    return query(path, f"SELECT {FACT_COLUMNS} FROM company_facts{where} ORDER BY fact_id, version")


def event_rows(path: Path, entity: str = "company_facts") -> list[tuple[Any, ...]]:
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


def ids(units: UnitOfWorkFactory) -> dict[str, str]:
    """The stored Segment and Market ids, by key and code."""
    with units.open(read_only=True):
        found = {seeded.key: seeded.id for seeded in setup_queries.segments()}
        found.update({seeded.code: seeded.id for seeded in setup_queries.markets()})
    return found


def history(units: UnitOfWorkFactory, fact_id: str) -> playbook_queries.FactHistory:
    with units.open(read_only=True):
        found = playbook_queries.fact(fact_id)
    assert found is not None
    return found


def fields_of(content: FactContent, **changes: Any) -> FactFields:
    """The Operator's form for ``content``, with ``changes``."""
    confirmed = content.metric_confirmed_at
    base = FactFields(
        type=str(content.type),
        wording=content.wording,
        source_url=content.source_url,
        segments=content.segments,
        markets=content.markets,
        metric=content.metric or "",
        metric_confirmed_at="" if confirmed is None else confirmed.isoformat(),
        scope_note=content.scope_note or "",
        last_checked_on=content.last_checked_on.isoformat(),
    )
    return dataclasses.replace(base, **changes)


def operator_edit(
    units: UnitOfWorkFactory, fact_id: str, clock: FrozenClock | None = None, **changes: Any
) -> FactSaved:
    found = history(units, fact_id)
    shown = found.current if found.current is not None else found.versions[0]
    with units.open():
        return CompanyFacts(clock or FrozenClock(LATER)).edit(
            fact_id, found.versions[0].version, fields_of(shown.content, **changes)
        )


def retract(units: UnitOfWorkFactory, fact_id: str) -> FactRetracted:
    with units.open():
        return CompanyFacts(FrozenClock(LATER)).retract(fact_id)


def add_stat_x(units: UnitOfWorkFactory) -> FactSaved:
    known = ids(units)
    with units.open():
        return CompanyFacts(FrozenClock(LATER)).add(
            "stat_x",
            dataclasses.replace(
                STAT_X, segments=(known["ops_automation"],), markets=(known["us"],)
            ),
        )


def shipped_inputs() -> tuple[Any, Any, Any, tuple[SeedFact, ...]]:
    return (
        validate_segments(seed_yaml.parse(SEGMENTS_TEXT)),
        validate_markets(seed_yaml.parse(MARKETS_TEXT)),
        validate_sequences(seed_yaml.parse(SEQUENCES_TEXT)),
        validate_company_facts(seed_yaml.parse(FACTS_TEXT)),
    )


def shipped_offers_and_campaigns() -> tuple[tuple[OfferDef, ...], tuple[CampaignDef, ...]]:
    """Story 4.2: sync_seed's fifth and sixth arguments."""
    return (
        validate_offers(seed_yaml.parse(OFFERS_TEXT)),
        validate_campaigns(seed_yaml.parse(CAMPAIGNS_TEXT)),
    )


# The I/O matrix: first and second sync ------------------------------------------------------------


def test_the_first_sync_stores_eight_facts_at_version_1_with_their_events(
    database: Path,
) -> None:
    run = sync()

    assert (run.code, run.out, run.err) == (
        0, OTHERS_FIRST + FACTS_FIRST + OFFERS_CAMPAIGNS_FIRST, ""
    )
    rows = fact_rows(database)
    assert sorted(row[1] for row in rows) == sorted(FACT_IDS)
    assert {(row[2], row[12], row[13], row[14]) for row in rows} == {(1, "seed", STAMP, None)}
    assert event_rows(database) == [
        ("company_facts", row_id, None, "v1", "seed_sync", None, "seed", STAMP)
        for row_id in [
            next(row[0] for row in rows if row[1] == fact_id) for fact_id in FACT_IDS
        ]
    ]


def test_the_rows_hold_typed_columns_and_sorted_id_lists(
    database: Path, units: UnitOfWorkFactory
) -> None:
    sync()
    known = ids(units)

    [aba] = fact_rows(database, "aba_formal_opinion_512")
    [team] = fact_rows(database, "nextron_team")
    [legal] = fact_rows(database, "legal_ai")

    assert aba[3:6] == (
        "regulation",
        "ABA Formal Opinion 512 says a lawyer needs the client's informed consent before "
        "putting client information into a self-learning generative AI tool.",
        "https://www.americanbar.org/content/dam/aba/administrative/professional_responsibility/"
        "ethics-opinions/aba-formal-opinion-512.pdf",
    )
    assert (aba[6], aba[7]) == (f'["{known["law_private_ai"]}"]', f'["{known["us"]}"]')
    both_segments = sorted([known["law_private_ai"], known["ops_automation"]])
    both_markets = sorted([known["us"], known["uk"]])
    assert json.loads(team[6]) == both_segments and json.loads(team[7]) == both_markets
    assert team[6] == json.dumps(both_segments, separators=(",", ":"))
    assert (team[8], team[9], team[10], team[11]) == (None, None, None, "2026-11-14")
    assert legal[10] == "The client was a legal-tech venture, not a law firm."


def test_a_second_sync_reports_8_unchanged_and_writes_nothing(database: Path) -> None:
    sync()
    before = table_dump(database)

    run = sync(clock=FrozenClock(LATER))

    assert (run.code, run.out, run.err) == (
        0, OTHERS_UNCHANGED + FACTS_UNCHANGED + OFFERS_CAMPAIGNS_UNCHANGED, ""
    )
    assert table_dump(database) == before


# The I/O matrix: a seed edit, a conflict, a retracted fact, a fact not in the file ---------------


def test_a_seed_edit_adds_version_2_and_keeps_version_1(database: Path, tmp_path: Path) -> None:
    sync()
    [v1] = fact_rows(database, "legal_ai")

    run = sync(seed_dir(tmp_path, edited(LEGAL_AI_WORDING, NEW_LEGAL_AI_WORDING)),
               clock=FrozenClock(LATER))

    assert (run.code, run.err) == (0, "")
    assert run.out == OTHERS_UNCHANGED + (
        "company_facts: 1 changed, 7 unchanged\n  legal_ai: changed (version 1 -> 2)\n"
    ) + OFFERS_CAMPAIGNS_UNCHANGED
    first, second = fact_rows(database, "legal_ai")
    assert first == v1
    assert second[2:5] == (
        2,
        "case_study",
        "Built an AI legal research and drafting assistant for a legal-tech venture (Legal AI).",
    )
    assert (second[12], second[13], second[14]) == ("seed", LATER_STAMP, None)
    assert event_rows(database)[-1] == (
        "company_facts", second[0], "v1", "v2", "seed_sync", None, "seed", LATER_STAMP
    )
    again = sync(seed_dir(tmp_path, edited(LEGAL_AI_WORDING, NEW_LEGAL_AI_WORDING), "again"))
    assert again.out == OTHERS_UNCHANGED + FACTS_UNCHANGED + OFFERS_CAMPAIGNS_UNCHANGED


def test_a_seed_change_after_the_operators_edit_is_a_conflict(
    database: Path, units: UnitOfWorkFactory, tmp_path: Path
) -> None:
    sync()
    saved = operator_edit(units, "legal_ai", wording="Built a legal research assistant.")
    assert saved == FactSaved("legal_ai", 2, True)
    unchanged = sync(clock=FrozenClock(LATER))
    # an unchanged seed: no conflict
    assert unchanged.out == OTHERS_UNCHANGED + FACTS_UNCHANGED + OFFERS_CAMPAIGNS_UNCHANGED
    before = table_dump(database)

    run = sync(seed_dir(tmp_path, edited(LEGAL_AI_WORDING, NEW_LEGAL_AI_WORDING)),
               clock=FrozenClock(LATER))

    assert (run.code, run.err) == (0, "")
    assert run.out == OTHERS_UNCHANGED + (
        "company_facts: 0 changed, 7 unchanged, 1 conflict\n"
        "  legal_ai: conflict, left as the Operator set it (version 2); "
        "seed/company_facts.yaml differs\n"
    ) + OFFERS_CAMPAIGNS_UNCHANGED
    assert table_dump(database) == before  # no row and no event


def test_two_conflicts_are_counted_in_the_plural(
    database: Path, units: UnitOfWorkFactory, tmp_path: Path
) -> None:
    sync()
    operator_edit(units, "legal_ai", wording="Built a legal research assistant.")
    retract(units, "wish_wheels")
    text = edited(LEGAL_AI_WORDING, NEW_LEGAL_AI_WORDING)
    text = edited(WISH_WHEELS_START, WISH_WHEELS_START.replace("single", "unified"), text)

    run = sync(seed_dir(tmp_path, text))

    counts = "company_facts: 0 changed, 6 unchanged, 2 conflicts\n"
    assert run.out.startswith(OTHERS_UNCHANGED + counts)
    assert "  wish_wheels: conflict, left as the Operator set it (version 1);" in run.out


def test_a_retracted_fact_stays_retracted_whatever_the_seed_says(
    database: Path, units: UnitOfWorkFactory, tmp_path: Path
) -> None:
    sync()
    assert retract(units, "wish_wheels") == FactRetracted("wish_wheels", True)
    before = table_dump(database)

    unchanged = sync(clock=FrozenClock(LATER))
    changed = sync(
        seed_dir(tmp_path, edited(WISH_WHEELS_START, WISH_WHEELS_START.replace("single", "one"))),
        clock=FrozenClock(LATER),
    )

    assert unchanged.out == OTHERS_UNCHANGED + FACTS_UNCHANGED + OFFERS_CAMPAIGNS_UNCHANGED
    assert changed.out == OTHERS_UNCHANGED + (
        "company_facts: 0 changed, 7 unchanged, 1 conflict\n"
        "  wish_wheels: conflict, left as the Operator set it (version 1); "
        "seed/company_facts.yaml differs\n"
    ) + OFFERS_CAMPAIGNS_UNCHANGED
    assert table_dump(database) == before  # never restored
    assert history(units, "wish_wheels").current is None


def test_a_seed_fact_equal_to_an_older_seed_version_after_an_edit_is_unchanged(
    database: Path, units: UnitOfWorkFactory, tmp_path: Path
) -> None:
    sync(seed_dir(tmp_path, edited(LEGAL_AI_WORDING, NEW_LEGAL_AI_WORDING)))  # v1 is the seed's
    operator_edit(units, "legal_ai", wording="Built a legal research assistant.")  # v2

    run = sync(seed_dir(tmp_path, edited(LEGAL_AI_WORDING, NEW_LEGAL_AI_WORDING), "same"))

    assert run.out == OTHERS_UNCHANGED + FACTS_UNCHANGED + OFFERS_CAMPAIGNS_UNCHANGED
    assert [row[2] for row in fact_rows(database, "legal_ai")] == [1, 2]


def test_after_an_edit_the_seed_compares_with_its_highest_version(
    database: Path, units: UnitOfWorkFactory, tmp_path: Path
) -> None:
    sync()  # v1 seed
    new_wording = edited(LEGAL_AI_WORDING, NEW_LEGAL_AI_WORDING)
    sync(seed_dir(tmp_path, new_wording, "v2"))  # v2 seed
    operator_edit(units, "legal_ai", wording="Built a legal research assistant.")  # v3
    before = table_dump(database)

    same = sync(seed_dir(tmp_path, new_wording, "same"), clock=FrozenClock(LATER))
    older = sync(clock=FrozenClock(LATER))  # the shipped file: v1's content, not v2's

    # equals the highest seed version
    assert same.out == OTHERS_UNCHANGED + FACTS_UNCHANGED + OFFERS_CAMPAIGNS_UNCHANGED
    assert older.out == OTHERS_UNCHANGED + (
        "company_facts: 0 changed, 7 unchanged, 1 conflict\n"
        "  legal_ai: conflict, left as the Operator set it (version 3); "
        "seed/company_facts.yaml differs\n"
    ) + OFFERS_CAMPAIGNS_UNCHANGED
    assert table_dump(database) == before  # no new row or event in either
    assert [(row[2], row[12]) for row in fact_rows(database, "legal_ai")] == [
        (1, "seed"),
        (2, "seed"),
        (3, "operator"),
    ]


def test_a_date_of_utc_today_plus_one_day_is_accepted_by_the_seed(
    database: Path, tmp_path: Path
) -> None:
    text = edited(
        "what it says about client data.\n    last_checked_on: '2026-11-14'\n",
        "what it says about client data.\n    last_checked_on: '2026-11-17'\n",
    )

    run = sync(seed_dir(tmp_path, text), clock=FrozenClock(DEFAULT_NOW))  # 2026-11-16 09:00 UTC

    assert (run.code, run.err) == (0, "")
    [row] = fact_rows(database, "sra_ai_notice")
    assert row[11] == "2026-11-17"


def test_a_seed_entry_for_an_operator_added_fact_is_a_conflict(
    database: Path, units: UnitOfWorkFactory, tmp_path: Path
) -> None:
    sync()
    add_stat_x(units)
    entry = (
        "  - fact_id: stat_x\n    type: statistic\n"
        "    wording: Firms that use private AI keep client data on their own servers.\n"
        "    source_url: https://example.com/survey\n    segments: [ops_automation]\n"
        "    markets: [uk]\n    last_checked_on: '2026-11-15'\n"
    )

    run = sync(seed_dir(tmp_path, FACTS_TEXT + entry))

    assert "company_facts: 0 changed, 8 unchanged, 1 conflict\n" in run.out
    assert "  stat_x: conflict, left as the Operator set it (version 1);" in run.out
    assert [row[12] for row in fact_rows(database, "stat_x")] == ["operator"]


def test_a_stored_fact_not_in_the_file_is_left_as_it_is(
    database: Path, units: UnitOfWorkFactory
) -> None:
    sync()
    add_stat_x(units)
    before = table_dump(database)

    run = sync(clock=FrozenClock(LATER))

    assert run.out == OTHERS_UNCHANGED + FACTS_UNCHANGED + (
        "  stat_x: not in seed/company_facts.yaml, left as it is (version 1)\n"
    ) + OFFERS_CAMPAIGNS_UNCHANGED
    assert table_dump(database) == before


# The I/O matrix: an invalid file ------------------------------------------------------------------

ABA_MARKETS = "    segments: [law_private_ai]\n    markets: [us]\n"
SRA_MARKETS = "    segments: [law_private_ai]\n    markets: [uk]\n"
INVALID = [
    (
        "a regulation with 2 markets",
        ABA_MARKETS,
        "    segments: [law_private_ai]\n    markets: [us, uk]\n",
        "company_facts[aba_formal_opinion_512].markets: A regulatory fact applies to one Market",
    ),
    (
        "a metric without its date",
        "    scope_note: The client was a legal-tech venture, not a law firm.\n",
        "    metric: '40% faster intake'\n"
        "    scope_note: The client was a legal-tech venture, not a law firm.\n",
        "company_facts[legal_ai].metric: Add a metric only once the client confirms it",
    ),
    (
        "an ftp source",
        "    source_url: https://www.sra.org.uk/solicitors/guidance/misuse-ai/\n",
        "    source_url: ftp://www.sra.org.uk/misuse-ai/\n",
        "company_facts[sra_ai_notice].source_url: Use an http or https link",
    ),
    (
        "ABA 512 on uk",
        ABA_MARKETS,
        "    segments: [law_private_ai]\n    markets: [uk]\n    # moved\n",
        f"company_facts[aba_formal_opinion_512].markets: {ABA_REASON}",
    ),
    (
        "an SRA scope note on us",
        "    wording: A 17+ person team of engineers, AI specialists and analysts, based in San "
        "Jose.\n",
        "    wording: A 17+ person team of engineers, AI specialists and analysts, based in San "
        "Jose.\n    scope_note: Cite it beside the SRA guidance.\n",
        "company_facts[nextron_team].markets: The SRA notice applies only to a UK Market",
    ),
    (
        "an unknown segment",
        ABA_MARKETS,
        "    segments: [law_privte_ai]\n    markets: [us]\n",
        "company_facts[aba_formal_opinion_512].segments[0]: names law_privte_ai, which isn't a "
        "Segment of this sync",
    ),
    (
        "an unknown market",
        SRA_MARKETS,
        "    segments: [law_private_ai]\n    markets: [gb]\n",
        "company_facts[sra_ai_notice].markets[0]: names gb, which isn't a Market of this sync",
    ),
    (
        "a repeated fact_id",
        "  - fact_id: mp_online_digital\n",
        "  - fact_id: legal_ai\n",
        "company_facts[3].fact_id: repeats an earlier fact id (legal_ai)",
    ),
    (
        "a missing wording",
        "    wording: A 17+ person team of engineers, AI specialists and analysts, based in San "
        "Jose.\n",
        "",
        "company_facts[nextron_team].wording: missing",
    ),
    (
        "a future date",
        "what it says about client data.\n    last_checked_on: '2026-11-14'\n",
        "what it says about client data.\n    last_checked_on: '2026-11-18'\n",
        "company_facts[sra_ai_notice].last_checked_on: can't be in the future",
    ),
    (
        "a plain date",
        "what it says about client data.\n    last_checked_on: '2026-11-14'\n",
        "what it says about client data.\n    last_checked_on: 2026-11-14\n",
        None,
    ),
]


@pytest.mark.parametrize(("name", "old", "new", "message"), INVALID, ids=[c[0] for c in INVALID])
def test_an_invalid_facts_file_names_the_field_and_writes_nothing(
    database: Path, tmp_path: Path, name: str, old: str, new: str, message: str | None
) -> None:
    before = sha256(database)

    run = sync(seed_dir(tmp_path, edited(old, new)))

    assert (run.code, run.out) == (1, ""), name
    if message is None:  # a syntax error: the reader names the line
        assert run.err.startswith("seed/company_facts.yaml: line "), run.err
    else:
        assert run.err == f"seed/company_facts.yaml: {message}\n", name
    assert sha256(database) == before


def test_a_missing_facts_file_is_refused(database: Path, tmp_path: Path) -> None:
    run = sync(seed_dir(tmp_path, None))

    assert (run.code, run.out, run.err) == (
        1,
        "",
        "seed/company_facts.yaml: the file doesn't exist\n",
    )
    assert query(database, "SELECT count(*) FROM segments") == [(0,)]


def test_a_facts_file_over_1_mib_is_refused(database: Path, tmp_path: Path) -> None:
    padding = main.MAX_SEED_FILE_BYTES + 1 - len(FACTS_TEXT.encode("utf-8"))
    text = FACTS_TEXT + "#" * (padding - 1) + "\n"
    assert len(text.encode("utf-8")) == main.MAX_SEED_FILE_BYTES + 1

    run = sync(seed_dir(tmp_path, text))

    assert (run.code, run.err) == (1, "seed/company_facts.yaml: the file is larger than 1 MiB\n")
    assert fact_rows(database) == []


def test_a_facts_file_that_is_not_utf8_is_refused(database: Path, tmp_path: Path) -> None:
    data = FACTS_TEXT.encode("utf-8").replace(b"San Jose", b"San Jos\xe9")  # cp1252's e-acute

    run = sync(seed_dir(tmp_path, data))

    assert (run.code, run.err) == (1, "seed/company_facts.yaml: the file isn't valid UTF-8\n")
    assert fact_rows(database) == []


# The I/O matrix: atomic ---------------------------------------------------------------------------


def test_a_failure_while_recording_a_fact_rolls_back_everything(
    database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real = playbook_api._record

    def record_then_fail(row_id: str, *args: Any) -> None:
        calls.append(row_id)
        real(row_id, *args)
        if len(calls) == 3:
            raise RuntimeError("boom, with details that are never printed")

    monkeypatch.setattr(playbook_api, "_record", record_then_fail)

    run = sync()

    assert (run.code, run.out) == (1, "")
    assert run.err == "seed sync failed (RuntimeError); nothing was changed\n"
    assert len(calls) == 3
    for table in ("segments", "markets", "sequences", "step_briefs", "company_facts", "events"):
        assert query(database, f"SELECT count(*) FROM {table}") == [(0,)], table


# The Seeds use case -------------------------------------------------------------------------------


def test_seeds_reads_the_clock_once_and_runs_one_transaction(units: UnitOfWorkFactory) -> None:
    opened: list[str] = []

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
            *shipped_inputs(), *shipped_offers_and_campaigns()
        )

    assert opened == ["clock", "transaction"]
    assert result.company_facts == CompanyFactSync(
        tuple((fact_id, 1) for fact_id in FACT_IDS), (), (), (), ()
    )
    assert result.sequences.created == (("default", 1),)


def no_transaction() -> None:
    raise AssertionError("a transaction was opened")


def hand_built(fact: SeedFact, **changes: Any) -> SeedFact:
    return dataclasses.replace(fact, content=dataclasses.replace(fact.content, **changes))


@pytest.mark.parametrize(
    ("make", "error", "match"),
    [
        (lambda facts: [*facts, {"fact_id": "x"}], TypeError, "takes SeedFacts, not dict"),
        (lambda facts: [facts[0], facts[0]], ValueError, "a fact_id more than once: nextron_team"),
        (
            lambda facts: [hand_built(facts[6], markets=("uk", "us"))],
            InvalidCompanyFacts,
            r"company_facts\[aba_formal_opinion_512\]\.markets: A regulatory fact applies",
        ),
        (
            lambda facts: [hand_built(facts[0], wording=" padded")],
            InvalidCompanyFacts,
            r"company_facts\[nextron_team\]\.wording: must not start or end with whitespace",
        ),
        (
            lambda facts: [hand_built(facts[6], markets=("uk",))],
            InvalidCompanyFacts,
            r"company_facts\[aba_formal_opinion_512\]\.markets: ABA Formal Opinion 512",
        ),
        (
            lambda facts: [hand_built(facts[0], last_checked_on=date(2026, 11, 18))],
            InvalidCompanyFacts,
            r"company_facts\[nextron_team\]\.last_checked_on: can't be in the future",
        ),
    ],
    ids=["dict", "repeat", "rule-1", "value", "floor", "future"],
)
def test_seeds_refuses_bad_facts_before_any_write(
    database: Path,
    units: UnitOfWorkFactory,
    monkeypatch: pytest.MonkeyPatch,
    make: Any,
    error: type[Exception],
    match: str,
) -> None:
    segments, markets, sequences, facts = shipped_inputs()
    before = sha256(database)

    with units.open() as uow:
        with pytest.raises(error, match=match):
            Seeds(FrozenClock()).sync_seed(
                segments, markets, sequences, make(facts), *shipped_offers_and_campaigns()
            )

    assert sha256(database) == before


def test_seeds_checks_the_facts_against_its_own_segments_and_markets(
    database: Path, units: UnitOfWorkFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    segments, markets, sequences, facts = shipped_inputs()
    us = [market for market in markets if market.code == "us"]
    no_uk_angle = [
        dataclasses.replace(
            sequence,
            steps=tuple(
                dataclasses.replace(
                    brief,
                    segment_angles=tuple(a for a in brief.segment_angles if a.market == "us"),
                )
                for brief in sequence.steps
            ),
        )
        for sequence in sequences
    ]

    with units.open() as uow:
        with pytest.raises(InvalidCompanyFacts) as caught:
            Seeds(FrozenClock()).sync_seed(  # US only, so no Campaign (they name uk too)
                segments, us, no_uk_angle, facts, shipped_offers_and_campaigns()[0], ()
            )

    assert caught.value.field == "company_facts[nextron_team].markets[0]"
    assert caught.value.reason == "names uk, which isn't a Market of this sync"


def test_in_managed_mode_the_facts_are_checked_against_the_stored_segments(
    units: UnitOfWorkFactory,
) -> None:
    segments, markets, sequences, facts = shipped_inputs()
    with units.open():  # the bootstrap stores the file's Segments
        Seeds(FrozenClock()).sync_seed(
            segments, markets, sequences, facts, *shipped_offers_and_campaigns()
        )
    with units.open(read_only=True):
        law = setup_queries.segment("law_private_ai")
    assert law is not None
    form: dict[str, Any] = json.loads(json.dumps(file_form(law.definition)))
    form["key"], form["name"] = "accountants", "Accountants"
    for index, each in enumerate(form["signal_types"]):
        each["key"] = f"accountants_signal_{index}"
    for index, each in enumerate(form["disqualifiers"]):
        each["key"] = f"accountants_rule_{index}"
    with units.open():  # a dashboard-only Segment
        SegmentEditor(FrozenClock()).create(form)

    dashboard_only = [hand_built(facts[0], segments=("accountants",)), *facts[1:]]
    with units.open():  # managed: the stored keys count, so the fact is accepted
        result = Seeds(FrozenClock(LATER)).sync_seed(
            segments, markets, sequences, dashboard_only, *shipped_offers_and_campaigns()
        )
    assert result.segments.managed
    assert result.company_facts.changed == (("nextron_team", 1, 2),)

    form["key"] = "file_only"  # fresh Signal type and rule keys too, so the file validates
    for each in (*form["signal_types"], *form["disqualifiers"]):
        each["key"] = each["key"].replace("accountants", "file_only")
    file_only = [*segments, *validate_segments({"segments": [form]})]
    named = [hand_built(facts[0], segments=("file_only",)), *facts[1:]]
    with units.open(), pytest.raises(InvalidCompanyFacts) as caught:
        Seeds(FrozenClock(LATER)).sync_seed(  # a key only in the file is not stored
            file_only, markets, sequences, named, *shipped_offers_and_campaigns()
        )
    assert caught.value.field == "company_facts[nextron_team].segments[0]"


def test_an_empty_facts_input_syncs_nothing_but_reports_the_stored_ones(
    units: UnitOfWorkFactory,
) -> None:
    segments, markets, sequences, facts = shipped_inputs()
    with units.open():
        Seeds(FrozenClock()).sync_seed(
            segments, markets, sequences, facts, *shipped_offers_and_campaigns()
        )

    with units.open():
        result = Seeds(FrozenClock(LATER)).sync_seed(
            segments, markets, sequences, (), *shipped_offers_and_campaigns()
        )

    assert result.company_facts == CompanyFactSync(
        (), (), (), (), tuple(sorted((fact_id, 1) for fact_id in FACT_IDS))
    )


def test_the_sync_logs_the_facts_counts(
    database: Path,
    units: UnitOfWorkFactory,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.playbook.api")
    sync()
    operator_edit(units, "legal_ai", wording="Built a legal research assistant.")
    add_stat_x(units)

    sync(seed_dir(tmp_path, edited(LEGAL_AI_WORDING, NEW_LEGAL_AI_WORDING)))

    records = [r for r in caplog.records if r.getMessage() == "company_facts_synced"]
    counts = (
        "created_count",
        "changed_count",
        "unchanged_count",
        "not_in_file_count",
        "conflict_count",
    )
    assert [[getattr(r, name) for name in counts] for r in records] == [
        [8, 0, 0, 0, 0],
        [0, 0, 7, 1, 1],
    ]
    assert {r.levelno for r in records} == {logging.INFO}


# The queries --------------------------------------------------------------------------------------


def test_company_facts_and_fact_list_every_version_newest_first(
    database: Path, units: UnitOfWorkFactory
) -> None:
    sync()
    operator_edit(units, "legal_ai", wording="Built a legal research assistant.")

    with units.open(read_only=True):
        everything = playbook_queries.company_facts()
        legal = playbook_queries.fact("legal_ai")
        missing = playbook_queries.fact("no_such_fact")

    assert [found.fact_id for found in everything] == sorted(FACT_IDS)
    assert missing is None
    assert legal is not None
    assert [version.version for version in legal.versions] == [2, 1]
    assert legal.current == legal.versions[0]
    assert legal.current.content.wording == "Built a legal research assistant."
    assert legal.current.created_by is FactCreator.OPERATOR
    assert legal.current.created_at == LATER
    assert legal.versions[1].created_by is FactCreator.SEED
    assert legal.versions[1].created_at == DEFAULT_NOW


def test_allowed_facts_holds_each_regulation_only_in_its_market(
    database: Path, units: UnitOfWorkFactory
) -> None:
    sync()
    known = ids(units)

    with units.open(read_only=True):
        law_uk = playbook_queries.allowed_facts(known["law_private_ai"], known["uk"])
        law_us = playbook_queries.allowed_facts(known["law_private_ai"], known["us"])
        ops_us = playbook_queries.allowed_facts(known["ops_automation"], known["us"])
        nothing = playbook_queries.allowed_facts(known["us"], known["law_private_ai"])

    common = [
        "audit_offer",
        "legal_ai",
        "mp_online_digital",
        "nextron_team",
        "property_3d_sites",
        "wish_wheels",
    ]
    assert [found.fact_id for found in law_uk] == sorted([*common, "sra_ai_notice"])
    assert [found.fact_id for found in law_us] == sorted([*common, "aba_formal_opinion_512"])
    assert [found.fact_id for found in ops_us] == common
    assert nothing == ()
    assert all(found.version == 1 and found.retracted_at is None for found in law_uk)


def test_allowed_facts_skips_a_retracted_fact_and_follows_an_edit(
    database: Path, units: UnitOfWorkFactory
) -> None:
    sync()
    known = ids(units)
    retract(units, "sra_ai_notice")
    operator_edit(units, "legal_ai", markets=(known["us"],))

    with units.open(read_only=True):
        law_uk = [f.fact_id for f in playbook_queries.allowed_facts(
            known["law_private_ai"], known["uk"])]
        law_us = playbook_queries.allowed_facts(known["law_private_ai"], known["us"])

    assert "sra_ai_notice" not in law_uk and "legal_ai" not in law_uk
    assert [(f.fact_id, f.version) for f in law_us if f.fact_id == "legal_ai"] == [
        ("legal_ai", 2)
    ]


# The use cases ------------------------------------------------------------------------------------


def test_add_saves_version_1_with_its_event(database: Path, units: UnitOfWorkFactory) -> None:
    sync()
    known = ids(units)

    saved = add_stat_x(units)

    assert saved == FactSaved("stat_x", 1, True)
    [row] = fact_rows(database, "stat_x")
    assert row[1:6] == (
        "stat_x",
        1,
        "statistic",
        "Firms that use private AI keep client data on their own servers.",
        "https://example.com/survey",
    )
    assert (row[6], row[7]) == (f'["{known["ops_automation"]}"]', f'["{known["us"]}"]')
    assert row[11:] == ("2026-11-15", "operator", LATER_STAMP, None)
    assert event_rows(database)[-1] == (
        "company_facts", row[0], None, "v1", "operator_add", None, "operator", LATER_STAMP
    )


def test_add_normalises_the_form(database: Path, units: UnitOfWorkFactory) -> None:
    sync()
    known = ids(units)
    given = dataclasses.replace(
        STAT_X,
        type=" statistic ",
        wording="  Firms that use\r\n private AI   keep data in-house. ",
        segments=(known["law_private_ai"],),
        markets=(known["uk"],),
        metric=" 40%  faster ",
        metric_confirmed_at=" 2026-11-01 ",
        scope_note="   ",
    )

    with units.open():
        CompanyFacts(FrozenClock(LATER)).add("  stat_y ", given)

    [row] = fact_rows(database, "stat_y")
    assert (row[4], row[8], row[9], row[10]) == (
        "Firms that use private AI keep data in-house.",
        "40% faster",
        "2026-11-01",
        None,
    )


def test_add_refuses_a_bad_or_taken_fact_id_with_the_fields_problems(
    database: Path, units: UnitOfWorkFactory
) -> None:
    sync()
    before = table_dump(database)

    def problems(fact_id: str, given: FactFields) -> dict[str, str]:
        with pytest.raises(InvalidFact) as caught, units.open():
            CompanyFacts(FrozenClock(LATER)).add(fact_id, given)
        return caught.value.problems

    assert problems("legal_ai", STAT_X) == {
        "fact_id": "This fact id is already used",
        "segments": "Choose at least one Segment",
        "markets": "Choose at least one Market",
    }
    assert problems("Stat X", STAT_X)["fact_id"] == (
        "Use lowercase letters, digits and underscores, starting with a letter"
    )
    assert problems("  ", STAT_X)["fact_id"] == "Enter a value"
    assert problems("a" * 32, STAT_X)["fact_id"].startswith("Use lowercase letters")
    assert table_dump(database) == before


def test_add_refuses_a_metric_naming_aba_512_on_a_uk_market(
    database: Path, units: UnitOfWorkFactory
) -> None:
    sync()
    known = ids(units)
    before = table_dump(database)
    given = dataclasses.replace(
        STAT_X,
        segments=(known["law_private_ai"],),
        markets=(known["uk"],),
        metric="Cut ABA Formal Opinion 512 consent reviews by 40%",
        metric_confirmed_at="2026-11-01",
    )

    with pytest.raises(InvalidFact) as caught, units.open():
        CompanyFacts(FrozenClock(LATER)).add("stat_y", given)

    assert caught.value.problems == {"markets": ABA_REASON}
    assert table_dump(database) == before


def test_add_accepts_dates_of_utc_today_plus_one_day(
    database: Path, units: UnitOfWorkFactory
) -> None:
    sync()
    known = ids(units)
    given = dataclasses.replace(
        STAT_X,
        segments=(known["ops_automation"],),
        markets=(known["us"],),
        metric="40% faster intake",
        metric_confirmed_at="2026-11-17",
        last_checked_on="2026-11-17",
    )

    with units.open():
        saved = CompanyFacts(FrozenClock()).add("stat_y", given)  # 2026-11-16 09:00 UTC

    assert saved == FactSaved("stat_y", 1, True)
    [row] = fact_rows(database, "stat_y")
    assert (row[8], row[9], row[11]) == ("40% faster intake", "2026-11-17", "2026-11-17")


def test_edit_saves_a_new_version_with_its_event(
    database: Path, units: UnitOfWorkFactory
) -> None:
    sync()
    [v1] = fact_rows(database, "legal_ai")

    saved = operator_edit(units, "legal_ai", wording="Built a legal research assistant.")

    assert saved == FactSaved("legal_ai", 2, True)
    first, second = fact_rows(database, "legal_ai")
    assert first == v1
    assert (second[2], second[4], second[12]) == (2, "Built a legal research assistant.",
                                                  "operator")
    assert event_rows(database)[-1] == (
        "company_facts", second[0], "v1", "v2", "operator_edit", None, "operator", LATER_STAMP
    )


def test_an_edit_that_changes_nothing_saves_nothing(
    database: Path, units: UnitOfWorkFactory
) -> None:
    sync()
    before = table_dump(database)

    saved = operator_edit(units, "legal_ai")
    spaced = operator_edit(units, "legal_ai", scope_note="  The client was a legal-tech venture, "
                           "not a law   firm.  ")

    assert saved == spaced == FactSaved("legal_ai", 1, False)
    assert table_dump(database) == before


def test_edit_refuses_a_stale_page_and_an_unknown_fact(
    database: Path, units: UnitOfWorkFactory
) -> None:
    sync()
    operator_edit(units, "legal_ai", wording="Built a legal research assistant.")
    [*_, latest] = fact_rows(database, "legal_ai")
    legal = history(units, "legal_ai").versions[1]
    before = table_dump(database)

    with pytest.raises(InvalidFact) as stale, units.open():
        CompanyFacts(FrozenClock(LATER)).edit("legal_ai", 1, fields_of(legal.content))
    with pytest.raises(InvalidFact) as unknown, units.open():
        CompanyFacts(FrozenClock(LATER)).edit(
            "no_such_fact", 1, fields_of(legal.content, wording="")
        )

    assert stale.value.problems == {
        "form": "This fact changed since the page loaded; reload the page and edit again"
    }
    assert unknown.value.problems == {"form": "No fact has this id", "wording": "Enter a value"}
    assert table_dump(database) == before
    assert latest[2] == 2


def test_edit_refuses_the_acs_rule_breaks(database: Path, units: UnitOfWorkFactory) -> None:
    sync()
    known = ids(units)
    aba = history(units, "aba_formal_opinion_512").versions[0]
    before = table_dump(database)

    def problems(**changes: Any) -> dict[str, str]:
        with pytest.raises(InvalidFact) as caught, units.open():
            CompanyFacts(FrozenClock(LATER)).edit(
                "aba_formal_opinion_512", 1, fields_of(aba.content, **changes)
            )
        return caught.value.problems

    assert problems(markets=(known["us"], known["uk"])) == {
        "markets": "A regulatory fact applies to one Market"
    }
    assert problems(metric="A result") == {
        "metric": "Add a metric only once the client confirms it"
    }
    assert problems(source_url="javascript:alert(1)") == {"source_url": "Use an http or https link"}
    assert problems(markets=(known["uk"],)) == {"markets": ABA_REASON}
    assert table_dump(database) == before


def test_retract_retracts_every_version_with_one_event(
    database: Path, units: UnitOfWorkFactory
) -> None:
    sync()
    operator_edit(units, "legal_ai", wording="Built a legal research assistant.")
    v1, v2 = fact_rows(database, "legal_ai")

    result = retract(units, "legal_ai")

    assert result == FactRetracted("legal_ai", True)
    after = fact_rows(database, "legal_ai")
    assert [row[14] for row in after] == [LATER_STAMP, LATER_STAMP]
    assert [row[:14] for row in after] == [v1[:14], v2[:14]]
    assert event_rows(database)[-1] == (
        "company_facts", v2[0], "v2", "retracted", "operator_retract", None, "operator",
        LATER_STAMP,
    )
    found = history(units, "legal_ai")
    assert found.current is None and len(found.versions) == 2
    before = table_dump(database)
    assert retract(units, "legal_ai") == FactRetracted("legal_ai", False)
    assert table_dump(database) == before


def test_editing_a_retracted_fact_restores_it(database: Path, units: UnitOfWorkFactory) -> None:
    sync()
    retract(units, "wish_wheels")

    saved = operator_edit(units, "wish_wheels")  # the same content, but it was retracted

    assert saved == FactSaved("wish_wheels", 2, True)
    found = history(units, "wish_wheels")
    assert found.current is not None and found.current.version == 2
    assert found.versions[1].retracted_at == LATER
    assert event_rows(database)[-1][2:7] == ("v1", "v2", "operator_edit", None, "operator")
    assert retract(units, "wish_wheels") == FactRetracted("wish_wheels", True)
    assert event_rows(database)[-1][2:4] == ("v2", "retracted")


def test_retract_refuses_an_unknown_fact(database: Path, units: UnitOfWorkFactory) -> None:
    sync()
    before = table_dump(database)

    with pytest.raises(InvalidFact) as caught, units.open():
        CompanyFacts(FrozenClock()).retract("no_such_fact")

    assert caught.value.problems == {"form": "No fact has this id"}
    assert table_dump(database) == before


def test_the_use_cases_log_after_the_commit(
    database: Path, units: UnitOfWorkFactory, caplog: pytest.LogCaptureFixture
) -> None:
    sync()
    caplog.set_level(logging.INFO, logger="nextron.app.playbook.api")

    add_stat_x(units)
    operator_edit(units, "stat_x", wording="Firms keep client data in-house.")
    operator_edit(units, "stat_x")
    retract(units, "stat_x")
    retract(units, "stat_x")

    found = [
        (r.getMessage(), getattr(r, "fact_id", None), getattr(r, "version", None),
         getattr(r, "changed", None))
        for r in caplog.records
        if r.getMessage().startswith("company_fact")
    ]
    assert found == [
        ("company_fact_saved", "stat_x", 1, True),
        ("company_fact_saved", "stat_x", 2, True),
        ("company_fact_saved", "stat_x", 2, False),
        ("company_fact_retracted", "stat_x", None, None),
    ]
    assert all("Firms" not in r.getMessage() for r in caplog.records)


def test_each_use_case_reads_the_clock_once(database: Path, units: UnitOfWorkFactory) -> None:
    sync()
    reads: list[str] = []

    class CountingClock(FrozenClock):
        def now(self) -> datetime:
            reads.append("clock")
            return super().now()

    known = ids(units)
    given = dataclasses.replace(STAT_X, segments=(known["ops_automation"],), markets=(known["us"],))
    for call in (
        lambda facts: facts.add("stat_x", given),
        lambda facts: facts.edit("stat_x", 1, dataclasses.replace(given, wording="Changed.")),
        lambda facts: facts.retract("stat_x"),
    ):
        reads.clear()
        with units.open():
            call(CompanyFacts(CountingClock(LATER)))
        assert reads == ["clock"]


def test_today_is_the_utc_date_of_now() -> None:
    late = datetime(2026, 11, 16, 23, 30, tzinfo=UTC)

    assert CompanyFacts(FrozenClock(late)).today() == date(2026, 11, 16)
    assert playbook_api.latest_allowed(late) == date(2026, 11, 17)


# The table: triggers and checks -------------------------------------------------------------------

# The triggers' existence is a persistence contract
# (tests/contracts/test_persistence_contracts.py::test_the_company_facts_triggers_exist_at_head).

REFUSED = [
    ("DELETE FROM company_facts", "append-only"),
    ("DELETE FROM company_facts WHERE fact_id = 'legal_ai'", "append-only"),
    (
        f"INSERT INTO company_facts SELECT id, 'other', 1, type, wording, source_url, "
        "allowed_segment_ids, allowed_market_ids, metric, metric_confirmed_at, scope_note, "
        "last_checked_on, created_by, created_at, retracted_at FROM company_facts "
        "WHERE fact_id = 'legal_ai'",
        "append-only",
    ),
    (
        "INSERT INTO company_facts SELECT '01ARZ3NDEKTSV4RRFFQ69G5FAV', fact_id, version, type, "
        "'Replaced.', source_url, allowed_segment_ids, allowed_market_ids, metric, "
        "metric_confirmed_at, scope_note, last_checked_on, created_by, created_at, retracted_at "
        "FROM company_facts WHERE fact_id = 'legal_ai'",
        "append-only",
    ),
    (
        "INSERT OR REPLACE INTO company_facts SELECT '01ARZ3NDEKTSV4RRFFQ69G5FAV', fact_id, "
        "version, type, 'Replaced.', source_url, allowed_segment_ids, allowed_market_ids, "
        "metric, metric_confirmed_at, scope_note, last_checked_on, created_by, created_at, "
        "retracted_at FROM company_facts WHERE fact_id = 'legal_ai'",
        "append-only",
    ),
]


@pytest.mark.parametrize(("statement", "message"), REFUSED)
def test_the_triggers_refuse_any_change_but_one_retraction(
    database: Path, statement: str, message: str
) -> None:
    sync()
    before = table_dump(database)

    with pytest.raises(sqlite3.IntegrityError, match=message):
        run_sql(database, statement)

    assert table_dump(database) == before


# Values an UPDATE probe sets: of the column's type, different from legal_ai's version 1, and
# valid for the column's checks, so only the immutability trigger can refuse them.
CHANGED_LITERALS = {
    "id": "'01ARZ3NDEKTSV4RRFFQ69G5FAV'",
    "fact_id": "'other_fact'",
    "type": "'offer'",
    "allowed_segment_ids": "'[]'",
    "allowed_market_ids": "'[]'",
    "created_by": "'operator'",
}


def changed_literal(name: str, declared: str, current: object) -> str:
    """An SQL literal for column ``name`` (declared ``declared``) that differs from
    ``current``."""
    if name in CHANGED_LITERALS:
        return CHANGED_LITERALS[name]
    if declared == "INTEGER":
        return str(int(current) + 1)  # type: ignore[call-overload]
    if declared == "DATE":
        return "'2026-11-15'"
    if declared == "VARCHAR(27)":  # a UtcDateTime
        return "'2026-01-01T00:00:00.000000Z'"
    return f"'Changed {name}.'"


def test_the_immutability_trigger_refuses_a_change_to_every_column_but_retracted_at(
    database: Path,
) -> None:
    sync()
    columns = [
        (name, declared)
        for _, name, declared, *_ in query(database, "PRAGMA table_info(company_facts)")
    ]
    names = [name for name, _ in columns]
    selected = ", ".join(names)
    [row] = query(database, f"SELECT {selected} FROM company_facts WHERE fact_id = 'legal_ai'")
    current = dict(zip(names, row, strict=True))
    probed = [(name, declared) for name, declared in columns if name != "retracted_at"]
    assert len(probed) == len(columns) - 1 == 14
    before = table_dump(database)

    not_refused = []
    for name, declared in probed:
        value = changed_literal(name, declared, current[name])
        assert query(database, f"SELECT {value}") != [(current[name],)], name  # a change
        try:
            run_sql(
                database, f"UPDATE company_facts SET {name} = {value} WHERE fact_id = 'legal_ai'"
            )
        except sqlite3.IntegrityError as error:
            if "append-only" not in str(error):
                not_refused.append(f"{name}: {error}")
        else:
            not_refused.append(f"{name}: changed")

    assert not_refused == []
    assert table_dump(database) == before


def test_retracted_at_is_set_once_and_never_changed(database: Path) -> None:
    sync()
    stamp = "2026-11-17T10:00:00.000000Z"

    run_sql(
        database, f"UPDATE company_facts SET retracted_at = '{stamp}' WHERE fact_id = 'legal_ai'"
    )

    for again in (f"'{LATER_STAMP}'", "NULL"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            run_sql(
                database,
                f"UPDATE company_facts SET retracted_at = {again} WHERE fact_id = 'legal_ai'",
            )
    assert [row[14] for row in fact_rows(database, "legal_ai")] == [stamp]


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("version", "0", "ck_company_facts_version"),
        ("type", "'other'", "ck_company_facts_type"),
        ("created_by", "'admin'", "ck_company_facts_created_by"),
        ("wording", "NULL", "NOT NULL constraint failed: company_facts.wording"),
        ("last_checked_on", "NULL", "NOT NULL constraint failed: company_facts.last_checked_on"),
        ("allowed_segment_ids", "NULL", "NOT NULL constraint failed"),
    ],
)
def test_the_checks_refuse_a_bad_row(
    database: Path, column: str, value: str, constraint: str
) -> None:
    sync()
    columns = FACT_COLUMNS.split(", ")
    selected = ", ".join(
        "'01ARZ3NDEKTSV4RRFFQ69G5FAV'" if name == "id"
        else "'probe'" if name == "fact_id"
        else value if name == column
        else name
        for name in columns
    )

    with pytest.raises(sqlite3.IntegrityError, match=constraint):
        run_sql(
            database,
            f"INSERT INTO company_facts ({FACT_COLUMNS}) SELECT {selected} FROM company_facts "
            "WHERE fact_id = 'legal_ai'",
        )

    assert fact_rows(database, "probe") == []


# A stored row that no longer decodes --------------------------------------------------------------


@pytest.mark.parametrize(
    ("column", "value", "field", "reason"),
    [
        ("allowed_segment_ids", "'not json'", "segments", "allowed_segment_ids is not valid JSON"),
        ("allowed_market_ids", "'[]'", "markets", "must hold at least 1 item"),
        ("wording", "' padded'", "wording", "must not start or end with whitespace"),
        ("source_url", "'javascript:alert(1)'", "source_url", "Use an http or https link"),
        ("last_checked_on", "'14/11/2026'", "last_checked_on", "must be a real date"),
        ("metric", "'A result'", "metric", "Add a metric only once the client confirms it"),
    ],
)
def test_a_stored_row_that_no_longer_decodes_raises(
    database: Path, units: UnitOfWorkFactory, column: str, value: str, field: str, reason: str
) -> None:
    sync()
    columns = FACT_COLUMNS.split(", ")
    selected = ", ".join(
        "'01ARZ3NDEKTSV4RRFFQ69G5FAV'" if name == "id"
        else "'broken'" if name == "fact_id"
        else value if name == column
        else name
        for name in columns
    )
    run_sql(
        database,
        f"INSERT INTO company_facts ({FACT_COLUMNS}) SELECT {selected} FROM company_facts "
        "WHERE fact_id = 'nextron_team'",
    )

    with pytest.raises(InvalidCompanyFacts) as caught, units.open(read_only=True):
        playbook_queries.company_facts()

    assert caught.value.field == f"company_facts[broken].{field}"
    assert caught.value.reason.startswith(reason)
    with units.open(read_only=True):
        assert playbook_queries.fact("legal_ai") is not None  # other facts still read
    before = sha256(database)
    run = sync(clock=FrozenClock(LATER))  # the sync decodes every stored fact: it refuses
    assert (run.code, run.out) == (1, "")
    assert run.err.startswith(f"seed/company_facts.yaml: company_facts[broken].{field}: ")
    assert sha256(database) == before


# The command line ---------------------------------------------------------------------------------


def test_cli_seed_sync_help_names_the_facts_file(capsys: pytest.CaptureFixture[str]) -> None:
    from nextron import __main__ as cli

    with pytest.raises(SystemExit) as caught:
        cli.main(["seed", "sync", "--help"])

    assert caught.value.code == 0
    shown = " ".join(capsys.readouterr().out.split())
    assert "seed/company_facts.yaml" in shown and "Approved Company Facts" in shown


def test_cli_seed_sync_loads_the_facts_end_to_end(database: Path) -> None:
    from nextron import __main__ as cli

    assert cli.main(["seed", "sync"]) == 0
    assert sorted(row[1] for row in fact_rows(database)) == sorted(FACT_IDS)


def test_the_shipped_fact_types(units: UnitOfWorkFactory, database: Path) -> None:
    sync()

    with units.open(read_only=True):
        types = {found.fact_id: found.versions[0].content.type
                 for found in playbook_queries.company_facts()}

    assert types["nextron_team"] is FactType.COMPANY
    assert types["audit_offer"] is FactType.OFFER
    assert [fact_id for fact_id, kind in types.items() if kind is FactType.REGULATION] == [
        "aba_formal_opinion_512",
        "sra_ai_notice",
    ]
