"""``nextron seed sync`` for the Sequences (``nextron.main.seed_sync``, ``Seeds.sync_seed``'s
third argument and the ``queries.setup`` Sequence reads; FR-4, FR-71, AD-12, Story 4.1), on real
units of work in a temporary data dir. Every row of the spec's I/O matrix is driven through
``main.seed_sync(env="test", seed_dir=...)`` with captured output."""

import dataclasses
import io
import logging
import sqlite3
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

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
    SequenceDifference,
    SequenceSync,
)
from nextron.bootstrap import BootstrapConfig, load_bootstrap
from nextron.db.migrations import head_revision
from nextron.db.repos.setup import sequences as sequences_repo
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.campaign import CampaignDef, validate_campaigns
from nextron.domain.company_facts import SeedFact, validate_company_facts
from nextron.domain.markets import MarketDef, validate_markets
from nextron.domain.offers import OfferDef, validate_offers
from nextron.domain.segments import SegmentDef, validate_segments
from nextron.domain.sequences import (
    REGULATION_REASON,
    InvalidSequences,
    SequenceDef,
    sequence_form,
    validate_sequences,
)
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

OTHERS_FIRST = (
    "segments: 2 changed, 0 unchanged\n"
    "  law_private_ai: created (version 1)\n"
    "  ops_automation: created (version 1)\n"
    "markets: 2 changed, 0 unchanged\n"
    "  us: created (version 1)\n"
    "  uk: created (version 1)\n"
)
OTHERS_UNCHANGED = "segments: Segments are managed in the dashboard; seed/segments.yaml ignored\nmarkets: 0 changed, 2 unchanged\n"
SEQUENCES_FIRST = "sequences: 1 changed, 0 unchanged\n  default: created (version 1)\n"
# Story 11.7: once the database holds a Sequence, the dashboard owns them
SEQUENCES_UNCHANGED = (
    "sequences: Sequences are managed in the dashboard; seed/sequences.yaml ignored\n"
)
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
CAMPAIGNS_MANAGED = (  # Story 11.8: once a Campaign is stored, the dashboard owns them
    "Campaigns are managed in the dashboard; seed/campaigns.yaml and seed/offers.yaml ignored"
)
OFFERS_CAMPAIGNS_UNCHANGED = f"offers: {CAMPAIGNS_MANAGED}\ncampaigns: {CAMPAIGNS_MANAGED}\n"

STEP_3_ASK = "        ask: Offer to send more detail by reply (no link or attachment)\n"
STEP_5 = """\
      # Step 5, day 21
      - delay_days: 21
        purpose: A last note
        word_limit: 40
        word_comparison: at_most
        allowed_content:
          - One new fact from Approved Company Facts
        ask: Whether to check back next quarter
        link_rule: footer_only
        no_price: true
        no_audit: true
"""
OLD_SEQUENCE = """\
  - key: old
    name: An older Sequence
    steps:
      - delay_days: 0
        purpose: Event-led cold email
        word_limit: 60
        word_comparison: under
        allowed_content: [One observed Signal]
        ask: Worth a look?
        link_rule: footer_only
        no_price: true
        no_audit: true
        subject_rule:
          word_limit: 6
          word_comparison: at_most
          guidance: [Short]
"""
STEP_1_SUBJECT = """\
        subject_rule:
          word_limit: 7
          word_comparison: under
          guidance:
            - About the prospect's world
            - Never misleading
"""
BRIEF_COLUMNS = (
    "id, sequence_id, step, delay_days, purpose, word_limit, word_comparison, allowed_content, "
    "ask, link_rule, no_price, no_audit, subject_rule, segment_angles, created_at, updated_at"
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


def seed_dir(tmp_path: Path, sequences: str | bytes | None, name: str = "seed") -> Path:
    """A seed folder with the shipped Segments and Markets and, unless ``None``, ``sequences``
    as the third file."""
    folder = tmp_path / name
    folder.mkdir()
    (folder / main.SEGMENTS_FILE_NAME).write_text(SEGMENTS_TEXT, encoding="utf-8", newline="")
    (folder / main.MARKETS_FILE_NAME).write_text(MARKETS_TEXT, encoding="utf-8", newline="")
    (folder / main.COMPANY_FACTS_FILE_NAME).write_text(FACTS_TEXT, encoding="utf-8", newline="")
    (folder / main.OFFERS_FILE_NAME).write_text(OFFERS_TEXT, encoding="utf-8", newline="")
    (folder / main.CAMPAIGNS_FILE_NAME).write_text(CAMPAIGNS_TEXT, encoding="utf-8", newline="")
    path = folder / main.SEQUENCES_FILE_NAME
    if isinstance(sequences, bytes):
        path.write_bytes(sequences)
    elif sequences is not None:
        path.write_text(sequences, encoding="utf-8", newline="")
    return folder


def edited(old: str, new: str, text: str = SEQUENCES_TEXT) -> str:
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


def store(units: UnitOfWorkFactory, text: str, at: Any = LATER) -> None:
    """Story 11.7: seed sync no longer changes a stored Sequence, so these tests change one
    through the repository's ``update`` (what ``SequenceEditor.edit`` calls), at ``at``."""
    [sequence] = shipped_sequences(text)

    @use_case
    def write() -> None:
        with current().transaction():
            stored = sequences_repo.by_key(sequence.key)
            assert stored is not None
            sequences_repo.update(stored.id, sequence, stored.version + 1, at, "operator")

    with units.open():
        write()


def query(path: Path, sql: str) -> list[tuple[Any, ...]]:
    reader = sqlite3.connect(path)
    try:
        return reader.execute(sql).fetchall()
    finally:
        reader.close()


def sequence_rows(path: Path) -> list[tuple[Any, ...]]:
    return query(
        path, "SELECT id, key, name, version, created_at, updated_at FROM sequences ORDER BY key"
    )


def brief_rows(path: Path, key: str = "default") -> list[tuple[Any, ...]]:
    return query(
        path,
        f"SELECT {BRIEF_COLUMNS} FROM step_briefs WHERE sequence_id = "
        f"(SELECT id FROM sequences WHERE key = '{key}') ORDER BY step",
    )


def event_rows(path: Path, entity: str = "sequences") -> list[tuple[Any, ...]]:
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


def shipped_segments() -> tuple[SegmentDef, ...]:
    return validate_segments(seed_yaml.parse(SEGMENTS_TEXT))


def shipped_markets() -> tuple[MarketDef, ...]:
    return validate_markets(seed_yaml.parse(MARKETS_TEXT))


def shipped_sequences(text: str = SEQUENCES_TEXT) -> tuple[SequenceDef, ...]:
    return validate_sequences(seed_yaml.parse(text))


def shipped_facts() -> tuple[SeedFact, ...]:  # Story 4.3: sync_seed's fourth argument
    return validate_company_facts(seed_yaml.parse(FACTS_TEXT))


def shipped_offers() -> tuple[OfferDef, ...]:  # Story 4.2: sync_seed's fifth argument
    return validate_offers(seed_yaml.parse(OFFERS_TEXT))


def shipped_campaigns() -> tuple[CampaignDef, ...]:  # and its sixth
    return validate_campaigns(seed_yaml.parse(CAMPAIGNS_TEXT))


# The I/O matrix: first and second sync ------------------------------------------------------------


def test_the_first_sync_creates_the_default_sequence_at_version_1(database: Path) -> None:
    run = sync()

    assert (run.code, run.out, run.err) == (
        0,
        OTHERS_FIRST + SEQUENCES_FIRST + FACTS_FIRST + OFFERS_CAMPAIGNS_FIRST,
        "",
    )
    [(sequence_id, key, name, version, created, updated)] = sequence_rows(database)
    assert (key, name, version, created, updated) == (
        "default",
        "Default four-Step Sequence",
        1,
        STAMP,
        STAMP,
    )
    assert len(sequence_id) == 26
    briefs = brief_rows(database)
    assert [(row[1], row[2], row[3]) for row in briefs] == [
        (sequence_id, 1, 0),
        (sequence_id, 2, 3),
        (sequence_id, 3, 7),
        (sequence_id, 4, 14),
    ]
    assert all((row[14], row[15]) == (STAMP, STAMP) for row in briefs)
    assert len({row[0] for row in briefs}) == 4
    assert event_rows(database) == [
        ("sequences", sequence_id, None, "v1", "seed_sync", None, "seed", STAMP)
    ]
    # the Segments' and Markets' events come first, in the same transaction
    entities = query(database, "SELECT entity FROM events ORDER BY rowid")
    assert [entity for (entity,) in entities] == [
        "segments",
        "segments",
        "markets",
        "markets",
        "sequences",
        "offers",  # Story 4.2: the Offers' and Campaigns' events follow the Sequences'
        *["campaigns"] * 4,
        *["company_facts"] * 8,  # Story 4.3: the facts' events come last
    ]


def test_the_brief_rows_hold_typed_columns_and_canonical_json(database: Path) -> None:
    sync()

    step_1, step_2, step_3, step_4 = (row[2:14] for row in brief_rows(database))

    assert step_1 == (
        1,
        0,
        "Event-led cold email",
        80,
        "under",
        '["One observed Signal","One sentence on the likely problem",'
        '"One credibility line from Approved Company Facts"]',
        'Relevance only, such as "Worth a look?" No meeting request or price',
        "footer_only",
        1,
        1,
        '{"guidance":["About the prospect\'s world","Never misleading"],'
        '"word_comparison":"under","word_limit":7}',
        "[]",
    )
    assert step_2[3:5] == (70, "at_most")
    assert step_2[10:] == (
        None,
        '[{"angle":"What ABA Formal Opinion 512 means for their intake or document work",'
        '"market":"us","segment":"law_private_ai"},'
        '{"angle":"What the SRA notice means for their intake or document work",'
        '"market":"uk","segment":"law_private_ai"}]',
    )
    assert step_3[5] == (
        '["One relevant case study or regulatory note from Approved Company Facts",'
        '"Never \\"just following up\\""]'
    )
    assert (step_4[3], step_4[4], step_4[10], step_4[11]) == (50, "at_most", None, "[]")


def test_a_second_sync_reports_0_changed_and_writes_nothing(database: Path) -> None:
    sync()
    before = table_dump(database)

    run = sync(clock=FrozenClock(LATER))

    assert (run.code, run.out, run.err) == (
        0,
        OTHERS_UNCHANGED + SEQUENCES_UNCHANGED + FACTS_UNCHANGED + OFFERS_CAMPAIGNS_UNCHANGED,
        "",
    )
    assert table_dump(database) == before


# The I/O matrix: changed, grown, shrunk -----------------------------------------------------------


def test_a_changed_file_writes_no_sequence_and_lists_the_difference(
    database: Path, tmp_path: Path
) -> None:
    sync()
    before = table_dump(database)
    changed = edited(STEP_3_ASK, "        ask: Offer to send a short case study by reply\n")

    run = sync(seed_dir(tmp_path, changed), clock=FrozenClock(LATER))

    assert (run.code, run.err) == (0, "")
    assert run.out == (
        OTHERS_UNCHANGED
        + SEQUENCES_UNCHANGED
        + "  default: differs in steps[3].ask\n"
        + FACTS_UNCHANGED
        + OFFERS_CAMPAIGNS_UNCHANGED
    )
    assert table_dump(database) == before


def test_a_changed_ask_through_the_repository_rewrites_only_its_brief_row(
    database: Path, units: UnitOfWorkFactory
) -> None:
    sync()
    [before] = sequence_rows(database)
    briefs_before = brief_rows(database)
    events_before = event_rows(database)
    changed = edited(STEP_3_ASK, "        ask: Offer to send a short case study by reply\n")

    store(units, changed)

    [after] = sequence_rows(database)
    assert (after[0], after[3], after[4], after[5]) == (before[0], 2, STAMP, LATER_STAMP)
    briefs_after = brief_rows(database)
    assert [briefs_after[i] for i in (0, 1, 3)] == [briefs_before[i] for i in (0, 1, 3)]
    step_3 = briefs_after[2]
    assert step_3[0] == briefs_before[2][0]  # the same row
    assert (step_3[8], step_3[14], step_3[15]) == (
        "Offer to send a short case study by reply",
        STAMP,
        LATER_STAMP,
    )
    assert event_rows(database) == events_before  # the repository writes no event
    versions = query(database, "SELECT version, actor FROM sequence_versions ORDER BY version")
    assert versions == [(1, "seed"), (2, "operator")]


def test_a_renamed_sequence_changes_only_its_sequence_row(
    database: Path, units: UnitOfWorkFactory
) -> None:
    sync()
    briefs_before = brief_rows(database)

    store(units, edited("name: Default four-Step Sequence", "name: Default"))

    assert sequence_rows(database)[0][2:4] == ("Default", 2)
    assert brief_rows(database) == briefs_before


def test_a_step_5_is_added_then_removed(database: Path, units: UnitOfWorkFactory) -> None:
    sync()
    briefs_v1 = brief_rows(database)
    grown = SEQUENCES_TEXT + STEP_5

    store(units, grown)

    briefs_v2 = brief_rows(database)
    assert sequence_rows(database)[0][3] == 2
    assert [row[2:4] for row in briefs_v2] == [(1, 0), (2, 3), (3, 7), (4, 14), (5, 21)]
    assert briefs_v2[:4] == briefs_v1  # the first four rows untouched
    assert (briefs_v2[4][14], briefs_v2[4][15]) == (LATER_STAMP, LATER_STAMP)

    store(units, SEQUENCES_TEXT)

    assert sequence_rows(database)[0][3] == 3
    assert brief_rows(database) == briefs_v1
    assert query(database, "SELECT count(*) FROM step_briefs") == [(4,)]
    versions = query(database, "SELECT version FROM sequence_versions ORDER BY version")
    assert versions == [(1,), (2,), (3,)]


def test_an_added_angle_changes_its_column(database: Path, units: UnitOfWorkFactory) -> None:
    sync()
    angle = (
        "          - segment: ops_automation\n"
        "            market: uk\n"
        "            angle: Their month-end close\n"
    )
    text = edited(
        "            angle: What the SRA notice means for their intake or document work\n",
        "            angle: What the SRA notice means for their intake or document work\n" + angle,
    )

    store(units, text)

    assert brief_rows(database)[1][13].endswith(
        ',{"angle":"Their month-end close","market":"uk","segment":"ops_automation"}]'
    )
    with units.open(read_only=True):
        seeded = setup_queries.sequence("default")
    assert seeded is not None
    assert seeded.definition == shipped_sequences(text)[0]


def test_edited_subject_guidance_and_allowed_content_change_only_their_rows(
    database: Path, units: UnitOfWorkFactory
) -> None:
    sync()
    before = brief_rows(database)
    text = edited("            - Never misleading\n", "            - Never misleading or vague\n")
    text = edited(
        '          - Never "just following up"\n',
        '          - Never "just checking in"\n',
        text,
    )

    store(units, text)

    after = brief_rows(database)
    assert after[0][12] == (
        '{"guidance":["About the prospect\'s world","Never misleading or vague"],'
        '"word_comparison":"under","word_limit":7}'
    )
    assert after[2][7] == (
        '["One relevant case study or regulatory note from Approved Company Facts",'
        '"Never \\"just checking in\\""]'
    )
    assert [row[15] for row in after] == [LATER_STAMP, STAMP, LATER_STAMP, STAMP]
    assert [after[i] for i in (1, 3)] == [before[i] for i in (1, 3)]
    with units.open(read_only=True):
        seeded = setup_queries.sequence("default")
    assert seeded is not None
    assert seeded.definition == shipped_sequences(text)[0]


def test_a_removed_middle_step_moves_the_later_steps_up(
    database: Path, units: UnitOfWorkFactory
) -> None:
    sync()
    [before] = sequence_rows(database)
    briefs_before = brief_rows(database)
    step_2_start = SEQUENCES_TEXT.index("      # Step 2, day 3\n")
    step_3_start = SEQUENCES_TEXT.index("      # Step 3, day 7\n")
    text = SEQUENCES_TEXT[:step_2_start] + SEQUENCES_TEXT[step_3_start:]

    store(units, text)

    assert sequence_rows(database)[0][3] == 2
    assert sequence_rows(database)[0][0] == before[0]
    after = brief_rows(database)
    assert [row[2:5] for row in after] == [
        (1, 0, "Event-led cold email"),
        (2, 7, "Proof"),
        (3, 14, "Respectful close"),
    ]
    assert [row[2:14] for row in after[1:]] == [
        (2, *briefs_before[2][3:14]),
        (3, *briefs_before[3][3:14]),
    ]
    assert after[0] == briefs_before[0]
    assert [row[15] for row in after] == [STAMP, LATER_STAMP, LATER_STAMP]
    with units.open(read_only=True):
        seeded = setup_queries.sequence("default")
    assert seeded is not None
    assert seeded.definition == shipped_sequences(text)[0]


# The I/O matrix: not in file ----------------------------------------------------------------------


def test_a_stored_sequence_not_in_the_file_is_listed_and_left_as_it_is(
    database: Path, tmp_path: Path
) -> None:
    with_old = SEQUENCES_TEXT.replace("sequences:\n", "sequences:\n" + OLD_SEQUENCE, 1)
    sync(seed_dir(tmp_path, with_old, "with_old"))
    [_, old_before] = sequence_rows(database)
    old_briefs = brief_rows(database, "old")
    events_before = event_rows(database)

    run = sync(clock=FrozenClock(LATER))

    assert (run.code, run.err) == (0, "")
    assert run.out == (
        OTHERS_UNCHANGED
        + SEQUENCES_UNCHANGED
        + "  old: only in the dashboard\n"
        + FACTS_UNCHANGED + OFFERS_CAMPAIGNS_UNCHANGED
    )
    assert sequence_rows(database)[1] == old_before
    assert brief_rows(database, "old") == old_briefs
    assert event_rows(database) == events_before


def test_managed_mode_checks_variant_steps_against_the_stored_step_counts(
    database: Path, units: UnitOfWorkFactory, tmp_path: Path
) -> None:
    """Story 11.7: once Sequences are managed, a Variant's angle note is checked against the
    stored Step count (3 here), not the file's (still the shipped 4 Steps)."""
    sync()
    [shipped] = shipped_sequences()

    @use_case
    def shorten() -> None:
        with current().transaction():
            stored = sequences_repo.by_key(shipped.key)
            assert stored is not None
            three = dataclasses.replace(shipped, steps=shipped.steps[:3])
            sequences_repo.update(stored.id, three, stored.version + 1, LATER, "operator")

    with units.open():
        shorten()
    # Story 11.8: with a Campaign stored the Campaigns file is ignored, so check a database
    # that holds Sequences but no Campaign
    writer = sqlite3.connect(database, isolation_level=None)
    try:
        writer.execute("DELETE FROM variants")
        writer.execute("DELETE FROM campaigns")
    finally:
        writer.close()
    before = table_dump(database)
    folder = seed_dir(tmp_path, SEQUENCES_TEXT, "step_4_note")
    old = "    variants:\n      - name: A\n        status: active\n"
    start = CAMPAIGNS_TEXT.index("  - key: law_private_ai_us\n")
    at = CAMPAIGNS_TEXT.index(old, start)
    note = old + "        step_angles:\n          - step: 4\n            note: A fourth-Step angle\n"
    campaigns = CAMPAIGNS_TEXT[:at] + note + CAMPAIGNS_TEXT[at + len(old) :]
    (folder / main.CAMPAIGNS_FILE_NAME).write_text(campaigns, encoding="utf-8", newline="")

    run = sync(folder, clock=FrozenClock(LATER))

    assert run.code == 1
    assert "campaigns[law_private_ai_us].variants[A].step_angles[0].step" in run.err
    assert "the Sequence default has 3 Steps" in run.err
    assert table_dump(database) == before


# The I/O matrix: invalid files --------------------------------------------------------------------

DEFAULT = "seed/sequences.yaml: sequences[default]"
STEP_3_FLAGS = (
    "        ask: Offer to send more detail by reply (no link or attachment)\n"
    "        link_rule: footer_only\n"
    "        no_price: true\n"
)
SEVENTH = "".join(
    f"      - delay_days: {day}\n"
    f"        purpose: Day {day}\n"
    "        word_limit: 50\n"
    "        word_comparison: at_most\n"
    "        allowed_content: [One fact]\n"
    "        ask: Worth a look?\n"
    "        link_rule: footer_only\n"
    "        no_price: true\n"
    "        no_audit: true\n"
    for day in (8, 9, 10)
)
INVALID = [
    ("a step missing its ask", STEP_3_ASK, "", f"{DEFAULT}.steps[3].ask: missing"),
    (
        "delays 0, 7, 7",
        "      - delay_days: 3\n",
        "      - delay_days: 7\n",
        f"{DEFAULT}.steps[3].delay_days: must be more than Step 2's delay_days (7)",
    ),
    (
        "a step 1 delay of 3",
        "      - delay_days: 0\n",
        "      - delay_days: 3\n",
        f"{DEFAULT}.steps[1].delay_days: must be 0: Step 1 is sent when the Sequence starts",
    ),
    (
        "a word limit of 0",
        "        word_limit: 80\n",
        "        word_limit: 0\n",
        f"{DEFAULT}.steps[1].word_limit: must be from 1 to 150",
    ),
    (
        "no_price false",
        STEP_3_FLAGS,
        STEP_3_FLAGS.replace("no_price: true", "no_price: false"),
        f"{DEFAULT}.steps[3].no_price: must be true: no Step names a price (FR-4)",
    ),
    (
        "step 1's link rule",
        "        link_rule: footer_only\n        no_price: true\n        no_audit: true\n"
        "        subject_rule:\n",
        "        link_rule: in_body\n        no_price: true\n        no_audit: true\n"
        "        subject_rule:\n",
        f"{DEFAULT}.steps[1].link_rule: must be one of footer_only",
    ),
    (
        "step 1 without a subject rule",
        STEP_1_SUBJECT,
        "",
        f"{DEFAULT}.steps[1].subject_rule: missing: Step 1 needs a subject rule (FR-4: fewer "
        "than 7 words)",
    ),
    (
        "a step 2 subject rule",
        "        ask: The relevance question, reworded\n",
        "        ask: The relevance question, reworded\n"
        "        subject_rule:\n"
        "          word_limit: 5\n"
        "          word_comparison: under\n"
        "          guidance: [Short]\n",
        f"{DEFAULT}.steps[2].subject_rule: only Step 1 has a subject rule: follow-ups reply in "
        "Step 1's thread (AD-22)",
    ),
    (
        "SRA in a purpose",
        "        purpose: Proof\n",
        "        purpose: Proof of SRA readiness\n",
        f"{DEFAULT}.steps[3].purpose: {REGULATION_REASON}",
    ),
    (
        "a 7th step",
        "      # Step 4, day 14\n",
        SEVENTH + "      # Step 4, day 14\n",
        f"{DEFAULT}.steps: must hold at most 6 items",
    ),
]


@pytest.mark.parametrize(("name", "old", "new", "message"), INVALID, ids=[c[0] for c in INVALID])
def test_an_invalid_sequences_file_names_the_field_and_writes_nothing(
    database: Path, tmp_path: Path, name: str, old: str, new: str, message: str
) -> None:
    before = sha256(database)

    run = sync(seed_dir(tmp_path, edited(old, new)))

    assert (run.code, run.out, run.err) == (1, "", f"{message}\n"), name
    assert sha256(database) == before


@pytest.mark.parametrize(
    ("name", "old", "new", "message"),
    [
        (
            "an ABA 512 angle on uk",
            "            angle: What the SRA notice means for their intake or document work\n",
            "            angle: What ABA Formal Opinion 512 means for UK firms\n",
            f"{DEFAULT}.steps[2].segment_angles[1].angle: names ABA Formal Opinion 512, which "
            "only a US Market's angle may (market uk is GB)",
        ),
        (
            "an SRA angle on us",
            "            angle: What ABA Formal Opinion 512 means for their intake or document "
            "work\n",
            "            angle: What the SRA notice means for US firms\n",
            f"{DEFAULT}.steps[2].segment_angles[0].angle: names the SRA notice, which only a GB "
            "Market's angle may (market us is US)",
        ),
        (
            "segment law_privateai",
            "          - segment: law_private_ai\n            market: us\n",
            "          - segment: law_privateai\n            market: us\n",
            f"{DEFAULT}.steps[2].segment_angles[0].segment: isn't the key of a Segment in this "
            "seed sync",
        ),
        (
            "market ca",
            "            market: uk\n",
            "            market: ca\n",
            f"{DEFAULT}.steps[2].segment_angles[1].market: isn't the code of a Market in this "
            "seed sync",
        ),
    ],
    ids=["aba-on-uk", "sra-on-us", "unknown-segment", "unknown-market"],
)
def test_an_angle_s_references_are_checked_against_this_sync_and_write_nothing(
    database: Path, tmp_path: Path, name: str, old: str, new: str, message: str
) -> None:
    sync()
    before = sha256(database)

    run = sync(seed_dir(tmp_path, edited(old, new)), clock=FrozenClock(LATER))

    assert (run.code, run.out, run.err) == (1, "", f"{message}\n"), name
    assert sha256(database) == before


def test_a_segment_left_out_of_segments_yaml_fails_the_angle_that_names_it(
    database: Path, tmp_path: Path
) -> None:
    """The references are this sync's Segments: a Law angle can't silently outlive its
    Segment's removal from the file (decision 8)."""
    folder = seed_dir(tmp_path, SEQUENCES_TEXT)
    law_start = SEGMENTS_TEXT.index("  - key: law_private_ai")
    ops_start = SEGMENTS_TEXT.index("  - key: ops_automation")
    (folder / main.SEGMENTS_FILE_NAME).write_text(
        SEGMENTS_TEXT[:law_start] + SEGMENTS_TEXT[ops_start:], encoding="utf-8", newline=""
    )

    run = sync(folder)

    assert run.code == 1
    assert run.err == (
        f"{DEFAULT}.steps[2].segment_angles[0].segment: isn't the key of a Segment in this "
        "seed sync\n"
    )
    assert query(database, "SELECT count(*) FROM segments") == [(0,)]


def test_a_syntax_error_names_the_sequences_file_and_its_line(
    database: Path, tmp_path: Path
) -> None:
    before = sha256(database)

    text = edited("    name: Default four-Step Sequence\n", "    name: yes\n")
    line = text.splitlines().index("    name: yes") + 1

    run = sync(seed_dir(tmp_path, text))

    assert run.code == 1
    assert run.err.startswith(f"seed/sequences.yaml: line {line}: ")
    assert sha256(database) == before


# The I/O matrix: the file itself ------------------------------------------------------------------


def test_a_missing_sequences_file_is_refused(database: Path, tmp_path: Path) -> None:
    run = sync(seed_dir(tmp_path, None))

    assert (run.code, run.out, run.err) == (1, "", "seed/sequences.yaml: the file doesn't exist\n")
    assert query(database, "SELECT count(*) FROM segments") == [(0,)]


def test_a_sequences_file_over_1_mib_is_refused(database: Path, tmp_path: Path) -> None:
    padding = main.MAX_SEED_FILE_BYTES + 1 - len(SEQUENCES_TEXT.encode("utf-8"))
    text = SEQUENCES_TEXT + "#" * (padding - 1) + "\n"
    assert len(text.encode("utf-8")) == main.MAX_SEED_FILE_BYTES + 1

    run = sync(seed_dir(tmp_path, text))

    assert (run.code, run.err) == (1, "seed/sequences.yaml: the file is larger than 1 MiB\n")
    assert sequence_rows(database) == []


def test_a_sequences_file_that_is_not_utf8_is_refused(database: Path, tmp_path: Path) -> None:
    data = SEQUENCES_TEXT.encode("utf-8").replace("§".encode(), b"\xa7")  # cp1252's section sign
    assert data != SEQUENCES_TEXT.encode("utf-8")

    run = sync(seed_dir(tmp_path, data))

    assert (run.code, run.err) == (1, "seed/sequences.yaml: the file isn't valid UTF-8\n")
    assert sequence_rows(database) == []


# The I/O matrix: atomic ---------------------------------------------------------------------------


def test_a_failure_while_recording_a_sequence_rolls_back_everything(
    database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real = setup_api._record_sequence

    def record_then_fail(sequence_id: str, *args: Any) -> None:
        calls.append(sequence_id)
        real(sequence_id, *args)
        raise RuntimeError("boom, with details that are never printed")

    monkeypatch.setattr(setup_api, "_record_sequence", record_then_fail)

    run = sync()

    assert (run.code, run.out) == (1, "")
    assert run.err == "seed sync failed (RuntimeError); nothing was changed\n"
    assert len(calls) == 1
    for table in ("segments", "markets", "sequences", "step_briefs", "events"):
        assert query(database, f"SELECT count(*) FROM {table}") == [(0,)], table


# The command line -------------------------------------------------------------------------------


def test_cli_seed_sync_help_names_the_sequences_file(capsys: pytest.CaptureFixture[str]) -> None:
    from nextron import __main__ as cli

    with pytest.raises(SystemExit) as caught:
        cli.main(["seed", "sync", "--help"])

    assert caught.value.code == 0
    shown = capsys.readouterr().out
    assert "seed/sequences.yaml" in shown and "Sequences" in shown


def test_cli_seed_sync_loads_the_sequences_end_to_end(database: Path) -> None:
    from nextron import __main__ as cli

    assert cli.main(["seed", "sync"]) == 0
    assert [(row[1], row[3]) for row in sequence_rows(database)] == [("default", 1)]
    assert len(brief_rows(database)) == 4


# The use case -------------------------------------------------------------------------------------


def test_sync_seed_returns_what_it_did_to_the_sequences(units: UnitOfWorkFactory) -> None:
    [default] = shipped_sequences()
    other = dataclasses.replace(default, key="aa_other", name="Another")
    renamed = dataclasses.replace(default, name="Renamed")
    segments, markets = shipped_segments(), shipped_markets()

    with units.open():
        first = Seeds(FrozenClock()).sync_seed(
            segments,
            markets,
            [default, other],
            shipped_facts(),
            shipped_offers(),
            shipped_campaigns(),
        )
    with units.open():
        second = Seeds(FrozenClock()).sync_seed(
            segments, markets, [renamed], shipped_facts(), shipped_offers(), shipped_campaigns()
        )
    with units.open():  # no Sequence, so no Campaign
        third = Seeds(FrozenClock()).sync_seed(
            segments, markets, (), shipped_facts(), shipped_offers(), ()
        )

    assert first == SeedSync(
        SegmentSync((("law_private_ai", 1), ("ops_automation", 1)), (), (), ()),
        MarketSync((("us", 1), ("uk", 1)), (), (), ()),
        SequenceSync((("default", 1), ("aa_other", 1)), (), (), ()),
        CompanyFactSync(
            tuple((fact_id, 1) for fact_id in SHIPPED_FACT_IDS), (), (), (), ()
        ),
        OfferSync((("default", 1),), (), (), ()),
        CampaignSync(tuple((key, 1) for key in CAMPAIGN_KEYS), (), (), ()),
    )
    # Story 11.7: once Sequences are stored, a sync writes none and lists the differences
    assert second.sequences == SequenceSync(
        (),
        (),
        (),
        (),
        True,
        (
            SequenceDifference("default", None, ("name",)),
            SequenceDifference("aa_other", "dashboard"),
        ),
    )
    assert third.sequences == SequenceSync(
        (),
        (),
        (),
        (),
        True,
        (SequenceDifference("aa_other", "dashboard"), SequenceDifference("default", "dashboard")),
    )
    with units.open(read_only=True):
        assert [(s.key, s.version) for s in setup_queries.sequences()] == [
            ("aa_other", 1),
            ("default", 1),
        ]


def test_sync_seed_logs_the_sequences_counts(
    database: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.setup.api")
    caplog.set_level(logging.INFO, logger="nextron.app.playbook.api")  # Story 4.3
    with_old = SEQUENCES_TEXT.replace("sequences:\n", "sequences:\n" + OLD_SEQUENCE, 1)

    sync()
    sync(clock=FrozenClock(LATER))
    third = sync(seed_dir(tmp_path, with_old, "with_old"))
    run = sync(seed_dir(tmp_path, edited(STEP_3_ASK, "        ask: Shorter\n"), "changed"))
    assert "  old: only in the file\n" in third.out
    assert "  default: differs in steps[3].ask\n" in run.out

    assert run.code == 0
    records = [r for r in caplog.records if r.getMessage() == "sequences_synced"]
    assert {r.levelno for r in records} == {logging.INFO}
    counts = ("created_count", "changed_count", "unchanged_count", "not_in_file_count")
    assert [[getattr(r, name) for name in counts] for r in records] == [
        [1, 0, 0, 0],  # the first sync
        [0, 0, 0, 0],  # Story 11.7: managed from here on, nothing written
        [0, 0, 0, 0],
        [0, 0, 0, 0],
    ]
    assert [(r.managed, r.differences_count) for r in records] == [
        (False, 0),
        (True, 0),  # the same file again
        (True, 1),  # "old: only in the file" (the run wrote nothing, so old isn't stored)
        (True, 1),  # "default: differs in steps[3].ask" (old left the file; it was never stored)
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


def no_transaction() -> None:
    raise AssertionError("a transaction was opened")


@pytest.mark.parametrize(
    ("make", "error", "match"),
    [
        (lambda s: [s, sequence_form(s)], TypeError, "sync_seed takes SequenceDefs, not dict"),
        (lambda s: ["default"], TypeError, "sync_seed takes SequenceDefs, not str"),
        (lambda s: [s, s], ValueError, "sync_seed got a Sequence key more than once: default"),
        (
            lambda s: [s, dataclasses.replace(s, name="Again")],
            ValueError,
            "a Sequence key more than once: default",
        ),
        (
            lambda s: [
                dataclasses.replace(
                    s, steps=(dataclasses.replace(s.steps[0], no_price=False), *s.steps[1:])
                )
            ],
            InvalidSequences,
            r"sequences\[default\]\.steps\[1\]\.no_price: must be true",
        ),
        (
            lambda s: [
                dataclasses.replace(
                    s,
                    steps=(
                        s.steps[0],
                        dataclasses.replace(
                            s.steps[1],
                            segment_angles=(
                                dataclasses.replace(s.steps[1].segment_angles[0], market="ca"),
                            ),
                        ),
                        *s.steps[2:],
                    ),
                )
            ],
            InvalidSequences,
            r"segment_angles\[0\]\.market: isn't the code of a Market",
        ),
    ],
    ids=["dict", "str", "repeat", "repeat-key", "rule", "reference"],
)
def test_sync_seed_refuses_bad_sequences_before_any_write(
    database: Path,
    units: UnitOfWorkFactory,
    monkeypatch: pytest.MonkeyPatch,
    make: Any,
    error: type[Exception],
    match: str,
) -> None:
    [default] = shipped_sequences()
    before = sha256(database)

    with units.open() as uow:
        with pytest.raises(error, match=match):
            Seeds(FrozenClock()).sync_seed(
                shipped_segments(),
                shipped_markets(),
                make(default),
                shipped_facts(),
                shipped_offers(),
                shipped_campaigns(),
            )

    assert sha256(database) == before


@pytest.mark.parametrize(
    ("segments", "markets", "field"),
    [
        ("ops", "both", "sequences[default].steps[2].segment_angles[0].segment"),
        ("both", "us", "sequences[default].steps[2].segment_angles[1].market"),
        ("both", "none", "sequences[default].steps[2].segment_angles[0].market"),
    ],
)
def test_sync_seed_checks_the_angles_against_its_own_segments_and_markets(
    database: Path,
    units: UnitOfWorkFactory,
    monkeypatch: pytest.MonkeyPatch,
    segments: str,
    markets: str,
    field: str,
) -> None:
    law, ops = shipped_segments()
    us, uk = shipped_markets()
    chosen_segments = {"ops": [ops], "both": [law, ops]}[segments]
    chosen_markets = {"us": [us], "both": [us, uk], "none": []}[markets]

    with units.open() as uow:
        with pytest.raises(InvalidSequences) as caught:
            Seeds(FrozenClock()).sync_seed(
                chosen_segments,
                chosen_markets,
                shipped_sequences(),
                shipped_facts(),
                shipped_offers(),
                shipped_campaigns(),
            )

    assert caught.value.field == field


def test_a_hand_built_sequence_is_stored_as_validation_returns_it(
    units: UnitOfWorkFactory,
) -> None:
    [default] = shipped_sequences()
    segments, markets = shipped_segments(), shipped_markets()
    with units.open():
        Seeds(FrozenClock()).sync_seed(
            segments, markets, [default], shipped_facts(), shipped_offers(), shipped_campaigns()
        )
    loose = dataclasses.replace(
        default,
        steps=tuple(
            dataclasses.replace(
                brief,
                step=9,
                word_comparison=str(brief.word_comparison),  # type: ignore[arg-type]
                allowed_content=list(brief.allowed_content),  # type: ignore[arg-type]
            )
            for brief in default.steps
        ),
    )

    with units.open():
        result = Seeds(FrozenClock()).sync_seed(
            segments, markets, [loose], shipped_facts(), shipped_offers(), shipped_campaigns()
        )

    assert result.sequences == SequenceSync((), (), (), (), True, ())  # no difference


# The queries --------------------------------------------------------------------------------------


def test_the_queries_read_the_seeded_sequences_by_key(units: UnitOfWorkFactory) -> None:
    [default] = shipped_sequences()
    sync()

    with units.open(read_only=True):
        everything = setup_queries.sequences()
        one = setup_queries.sequence("default")
        none = setup_queries.sequence("missing")

    assert [(s.key, s.version, s.definition) for s in everything] == [("default", 1, default)]
    assert one == everything[0]
    assert none is None
    assert one is not None and len(one.id) == 26
    assert [brief.delay_days for brief in one.definition.steps] == [0, 3, 7, 14]
    with pytest.raises(dataclasses.FrozenInstanceError):
        one.version = 2  # type: ignore[misc]


def test_the_queries_return_sequences_by_key_and_nothing_before_a_sync(
    units: UnitOfWorkFactory,
) -> None:
    [default] = shipped_sequences()
    with units.open(read_only=True):
        assert setup_queries.sequences() == ()
        assert setup_queries.sequence("default") is None
    stored = [dataclasses.replace(default, key=key) for key in ("zz", "aa", "mm")]
    with units.open():
        Seeds(FrozenClock()).sync_seed(  # no default Sequence, so no Campaign
            shipped_segments(), shipped_markets(), stored, shipped_facts(), shipped_offers(), ()
        )

    with units.open(read_only=True):
        assert [s.key for s in setup_queries.sequences()] == ["aa", "mm", "zz"]


@pytest.mark.parametrize(
    ("statement", "field", "reason"),
    [
        (
            "UPDATE step_briefs SET allowed_content = 'not json' WHERE step = 2",
            "sequences[default].steps[2].allowed_content",
            "is not valid JSON",
        ),
        (
            "UPDATE step_briefs SET subject_rule = '{' WHERE step = 1",
            "sequences[default].steps[1].subject_rule",
            "is not valid JSON",
        ),
        (
            "UPDATE step_briefs SET segment_angles = 'x' WHERE step = 4",
            "sequences[default].steps[4].segment_angles",
            "is not valid JSON",
        ),
        (
            "UPDATE step_briefs SET no_price = 0 WHERE step = 1",
            "sequences[default].steps[1].no_price",
            "must be true: no Step names a price (FR-4)",
        ),
        (
            "UPDATE step_briefs SET subject_rule = NULL WHERE step = 1",
            "sequences[default].steps[1].subject_rule",
            "missing: Step 1 needs a subject rule (FR-4: fewer than 7 words)",
        ),
        (
            "UPDATE step_briefs SET allowed_content = '{}' WHERE step = 3",
            "sequences[default].steps[3].allowed_content",
            "must be a list",
        ),
        (
            "DELETE FROM step_briefs WHERE step = 2",
            "sequences[default].steps",
            "the stored Steps aren't numbered 1 to 3",
        ),
        (
            "DELETE FROM step_briefs",
            "sequences[default].steps",
            "must hold at least 1 item",
        ),
    ],
)
def test_a_stored_sequence_that_no_longer_decodes_raises(
    database: Path, units: UnitOfWorkFactory, statement: str, field: str, reason: str
) -> None:
    sync()
    run_sql(database, statement)
    before = sha256(database)

    with units.open(read_only=True), pytest.raises(InvalidSequences) as caught:
        setup_queries.sequences()
    run = sync(clock=FrozenClock(LATER))

    assert (caught.value.field, caught.value.reason) == (field, reason)
    assert (run.code, run.out, run.err) == (1, "", f"seed/sequences.yaml: {field}: {reason}\n")
    assert sha256(database) == before


# The tables ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        ("UPDATE sequences SET version = 0", "ck_sequences_version"),
        ("UPDATE step_briefs SET step = 7 WHERE step = 4", "ck_step_briefs_step"),
        ("UPDATE step_briefs SET step = 0 WHERE step = 1", "ck_step_briefs_step"),
        ("UPDATE step_briefs SET delay_days = -1 WHERE step = 1", "ck_step_briefs_delay_days"),
        ("UPDATE step_briefs SET word_limit = 0 WHERE step = 1", "ck_step_briefs_word_limit"),
        ("UPDATE step_briefs SET word_limit = 151 WHERE step = 1", "ck_step_briefs_word_limit"),
        (
            "UPDATE step_briefs SET step = 1 WHERE step = 2",
            "UNIQUE constraint failed: step_briefs.sequence_id, step_briefs.step",
        ),
        (
            "INSERT INTO sequences SELECT '01ARZ3NDEKTSV4RRFFQ69G5FAV', key, name, version, "
            "created_at, updated_at FROM sequences",
            "UNIQUE constraint failed: sequences.key",
        ),
        ("UPDATE step_briefs SET purpose = NULL WHERE step = 1", "NOT NULL constraint failed"),
        ("UPDATE step_briefs SET segment_angles = NULL", "NOT NULL constraint failed"),
    ],
)
def test_the_checks_refuse_a_bad_row(database: Path, statement: str, constraint: str) -> None:
    sync()

    with pytest.raises(sqlite3.IntegrityError, match=constraint):
        run_sql(database, statement)

    assert sequence_rows(database)[0][3] == 1


def test_step_briefs_reference_their_sequence_without_cascade(database: Path) -> None:
    sync()

    [(table, origin, target, on_delete)] = query(
        database,
        'SELECT "table", "from", "to", on_delete FROM pragma_foreign_key_list(\'step_briefs\')',
    )
    assert (table, origin, target, on_delete) == ("sequences", "sequence_id", "id", "NO ACTION")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        run_sql(database, "PRAGMA foreign_keys=ON", "DELETE FROM sequences")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        run_sql(
            database,
            "PRAGMA foreign_keys=ON",
            "UPDATE step_briefs SET sequence_id = '01ARZ3NDEKTSV4RRFFQ69G5FAV' WHERE step = 1",
        )
    assert query(database, "SELECT count(*) FROM step_briefs") == [(4,)]


def test_the_engine_s_own_connections_enforce_the_foreign_key(
    database: Path, units: UnitOfWorkFactory
) -> None:
    sync()

    with (
        pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"),
        units.engine.begin() as connection,
    ):
        connection.execute(text("DELETE FROM sequences"))

    assert len(sequence_rows(database)) == 1
