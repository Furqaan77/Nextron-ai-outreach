"""``nextron seed sync`` (``nextron.main.seed_sync``, ``Setup.sync_segments`` and the
``queries.setup`` Segment reads; FR-1, AD-12, Story 2.1), on real units of work in a temporary
data dir. Every row of the spec's I/O matrix is driven through ``main.seed_sync(env="test",
seed_dir=...)`` with captured output."""

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
from nextron.app.jobqueue.api import JobQueue
from nextron.app.queries import setup as setup_queries
from nextron.app.setup import api as setup_api
from nextron.app.setup.api import SegmentDifference, SegmentSync, Setup
from nextron.app.setup.segment_editing import SegmentEditor
from nextron.bootstrap import ENV_ERROR, BootstrapConfig, load_bootstrap
from nextron.db.migrations import head_revision
from nextron.db.uow import UnitOfWorkFactory
from nextron.domain import seed_yaml
from nextron.domain.job import Job, JobType, JobTypes, Lane
from nextron.domain.segments import (
    InvalidSegments,
    SegmentDef,
    definition_text,
    file_form,
    validate_segments,
)
from tests.fakes.clock import DEFAULT_NOW, FrozenClock
from tests.migration_chains import run_sql, sha256, stored_revisions

SHIPPED = main.SEED_DIR / main.SEGMENTS_FILE_NAME
SHIPPED_TEXT = SHIPPED.read_text(encoding="utf-8")
SHIPPED_MARKETS = main.SEED_DIR / main.MARKETS_FILE_NAME  # Story 3.1: the second seed file
SHIPPED_SEQUENCES = main.SEED_DIR / main.SEQUENCES_FILE_NAME  # Story 4.1: the third seed file
SHIPPED_FACTS = main.SEED_DIR / main.COMPANY_FACTS_FILE_NAME  # Story 4.3: the fourth
SHIPPED_OFFERS = main.SEED_DIR / main.OFFERS_FILE_NAME  # Story 4.2: the fifth
SHIPPED_CAMPAIGNS = main.SEED_DIR / main.CAMPAIGNS_FILE_NAME  # and the sixth
LATER = DEFAULT_NOW + timedelta(days=3)
KEYS = ("law_private_ai", "ops_automation")


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


def seed_dir(tmp_path: Path, text: str | bytes, name: str = "seed") -> Path:
    folder = tmp_path / name
    folder.mkdir()
    (folder / main.MARKETS_FILE_NAME).write_bytes(SHIPPED_MARKETS.read_bytes())
    (folder / main.SEQUENCES_FILE_NAME).write_bytes(SHIPPED_SEQUENCES.read_bytes())
    (folder / main.COMPANY_FACTS_FILE_NAME).write_bytes(SHIPPED_FACTS.read_bytes())
    (folder / main.OFFERS_FILE_NAME).write_bytes(SHIPPED_OFFERS.read_bytes())
    (folder / main.CAMPAIGNS_FILE_NAME).write_bytes(SHIPPED_CAMPAIGNS.read_bytes())
    path = folder / main.SEGMENTS_FILE_NAME
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8", newline="")
    return folder


def edited(old: str, new: str) -> str:
    assert SHIPPED_TEXT.count(old) >= 1, old
    return SHIPPED_TEXT.replace(old, new, 1)


def sync(
    folder: Path | None = None, *, clock: FrozenClock | None = None, env: str | None = "test"
) -> Run:
    out, err = io.StringIO(), io.StringIO()
    code = main.seed_sync(
        env=env,
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


def segment_rows(path: Path) -> list[tuple[Any, ...]]:
    return query(
        path, "SELECT id, key, definition, version, created_at, updated_at FROM segments ORDER BY key"
    )


def event_rows(path: Path) -> list[tuple[Any, ...]]:
    return query(
        path,
        "SELECT entity, entity_id, from_status, to_status, cause, cause_ref, actor, at "
        "FROM events ORDER BY rowid",
    )


def table_dump(path: Path, *, skip: frozenset[str] = frozenset()) -> dict[str, list[Any]]:
    names = [
        name
        for (name,) in query(
            path, "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
        if name not in skip
    ]
    return {name: query(path, f"SELECT * FROM {name} ORDER BY rowid") for name in names}


SEGMENTS_FIRST = (
    "segments: 2 changed, 0 unchanged\n"
    "  law_private_ai: created (version 1)\n"
    "  ops_automation: created (version 1)\n"
)
# Story 3.1: the markets block follows the segments block
MARKETS_FIRST = (
    "markets: 2 changed, 0 unchanged\n"
    "  us: created (version 1)\n"
    "  uk: created (version 1)\n"
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
    "offers: 1 changed, 0 unchanged\n"
    "  default: created (version 1)\n"
    "campaigns: 4 changed, 0 unchanged\n"
    "  law_private_ai_us: created (version 1)\n"
    "  law_private_ai_uk: created (version 1)\n"
    "  ops_automation_us: created (version 1)\n"
    "  ops_automation_uk: created (version 1)\n"
)
OFFERS_CAMPAIGNS_UNCHANGED = "offers: 0 changed, 1 unchanged\ncampaigns: 0 changed, 4 unchanged\n"
SUMMARY_FIRST = (
    SEGMENTS_FIRST + MARKETS_FIRST + SEQUENCES_FIRST + FACTS_FIRST + OFFERS_CAMPAIGNS_FIRST
)
# Story 11.5: once the database holds a Segment, the dashboard owns them
SEGMENTS_MANAGED = "segments: Segments are managed in the dashboard; seed/segments.yaml ignored\n"
OTHERS_UNCHANGED = (
    MARKETS_UNCHANGED + SEQUENCES_UNCHANGED + FACTS_UNCHANGED + OFFERS_CAMPAIGNS_UNCHANGED
)


# The I/O matrix -----------------------------------------------------------------------------------


def test_the_first_sync_creates_both_segments_at_version_1(database: Path) -> None:
    run = sync()

    assert (run.code, run.out, run.err) == (0, SUMMARY_FIRST, "")
    rows = segment_rows(database)
    assert [(key, version) for _, key, _, version, _, _ in rows] == [(k, 1) for k in KEYS]
    stamp = "2026-11-16T09:00:00.000000Z"
    assert all((created, updated) == (stamp, stamp) for *_, created, updated in rows)
    assert event_rows(database)[:2] == [  # the markets' events follow
        ("segments", row_id, None, "v1", "seed_sync", None, "seed", stamp)
        for row_id, *_ in rows
    ]


def test_the_stored_definitions_are_the_shipped_files_canonical_text(database: Path) -> None:
    sync()

    shipped = validate_segments(seed_yaml.parse(SHIPPED_TEXT))
    stored = {key: definition for _, key, definition, *_ in segment_rows(database)}
    assert stored == {s.key: definition_text(s) for s in shipped}


def test_the_first_sync_stores_each_segments_first_version_row(database: Path) -> None:
    sync()

    rows = query(
        database,
        "SELECT s.key, v.version, v.actor, v.definition = s.definition, v.created_at "
        "FROM segment_versions v JOIN segments s ON s.id = v.segment_id ORDER BY s.key",
    )
    stamp = "2026-11-16T09:00:00.000000Z"
    assert rows == [(key, 1, "seed", 1, stamp) for key in KEYS]


def test_a_second_sync_says_the_dashboard_owns_segments_and_writes_nothing(
    database: Path,
) -> None:
    sync()
    before = table_dump(database)

    run = sync(clock=FrozenClock(LATER))

    assert (run.code, run.out, run.err) == (0, SEGMENTS_MANAGED + OTHERS_UNCHANGED, "")
    assert table_dump(database) == before


def test_a_changed_file_after_the_first_sync_writes_no_segment_and_lists_the_difference(
    database: Path, tmp_path: Path
) -> None:
    """Story 11.5: ``nextron seed sync`` never updates a stored Segment."""
    sync()
    before = table_dump(database)
    changed = edited(
        "        sources: [job_ads, website]\n        score_only: false\n        weight: 30",
        "        sources: [job_ads, website]\n        score_only: false\n        weight: 25",
    )

    run = sync(seed_dir(tmp_path, changed), clock=FrozenClock(LATER))

    assert (run.code, run.err) == (0, "")
    assert run.out == (
        SEGMENTS_MANAGED
        + "  ops_automation: differs in signal_types[ops_dynamics_gp].weight\n"
        + OTHERS_UNCHANGED
    )
    assert table_dump(database) == before


def renamed(segment: SegmentDef, key: str, prefix: str) -> SegmentDef:
    """``segment`` stored as ``key``, with its signal-type and disqualifier keys prefixed, so
    they stay unique across the stored Segments."""
    return dataclasses.replace(
        segment,
        key=key,
        name=f"Segment {key}",
        signal_types=tuple(
            dataclasses.replace(t, key=prefix + t.key) for t in segment.signal_types
        ),
        disqualifiers=tuple(
            dataclasses.replace(d, key=prefix + d.key) for d in segment.disqualifiers
        ),
    )


def store(units: UnitOfWorkFactory, *segments: SegmentDef) -> None:
    with units.open():
        Setup(FrozenClock()).sync_segments(segments)


def test_the_other_files_are_checked_against_the_stored_segments(
    database: Path, units: UnitOfWorkFactory
) -> None:
    """Managed mode: the file's Segments are ignored, so a Sequence angle naming a Segment the
    database doesn't hold is refused, and nothing is written."""
    law = shipped_defs()[0]
    store(units, renamed(law, "old_segment", "old_"))
    before = table_dump(database)

    run = sync(clock=FrozenClock(LATER))

    assert (run.code, run.out) == (1, "")
    assert run.err.startswith("seed/sequences.yaml: ")
    assert table_dump(database) == before


def test_the_managed_mode_lists_every_kind_of_difference(units: UnitOfWorkFactory) -> None:
    law, ops = shipped_defs()
    store(units, law, renamed(ops, "aa_ops", "aa_"))
    changed_law = dataclasses.replace(law, name="Law firms")

    with units.open():
        result = Setup(FrozenClock()).sync_segments([changed_law, ops])

    assert result == SegmentSync(
        (),
        (),
        (),
        (),
        True,
        (
            SegmentDifference("law_private_ai", None, ("name",)),
            SegmentDifference("ops_automation", "file"),
            SegmentDifference("aa_ops", "dashboard"),
        ),
    )


def test_the_cli_prints_each_difference(
    database: Path, units: UnitOfWorkFactory, tmp_path: Path
) -> None:
    sync()
    law = shipped_defs()[0]
    with units.open():  # a Segment only the dashboard has
        SegmentEditor(FrozenClock()).create(
            file_form(renamed(law, "accountants", "acc_"))
        )

    run = sync(clock=FrozenClock(LATER))

    assert run.code == 0
    assert run.out == (
        SEGMENTS_MANAGED + "  accountants: only in the dashboard\n" + OTHERS_UNCHANGED
    )


INVALID_RULES = [
    (
        "        sources: [team_pages, practice_pages]\n        score_only: false\n        weight: 15",
        "        sources: [team_pages, practice_pages]\n        score_only: false\n        weight: 70",
        "segments[law_private_ai].signal_types[law_size_document_heavy].weight",
        "a Tier 3 weight must be below every Tier 1 weight",
    ),
    (
        "evidence: [quote]\n        sources: [signal_inbox]",
        "evidence: [quote, absence]\n        sources: [signal_inbox]",
        "segments[law_private_ai].signal_types[law_ai_confidentiality_post].evidence",
        "an Event's evidence must be exactly [quote]",
    ),
    (
        "sources: [signal_inbox]\n        score_only: false",
        "sources: [signal_inbox]\n        score_only: true",
        "segments[law_private_ai].signal_types[law_ai_confidentiality_post].score_only",
        "an Event can't be score-only",
    ),
    (
        "key: ops_expansion",
        "key: law_expansion",
        "segments[ops_automation].signal_types[2].key",
        "repeats an earlier signal type key (law_expansion)",
    ),
    (
        "name: Post about AI or client confidentiality",
        "name: ABA Formal Opinion 512",
        "segments[law_private_ai].signal_types[law_ai_confidentiality_post].name",
        "market-wide fact",
    ),
    (
        "key: law_ai_confidentiality_post",
        "key: law_sra_notice",
        "segments[law_private_ai].signal_types[law_sra_notice].key",
        "market-wide fact",
    ),
    (
        "key: ops_automation",
        "key: law_private_ai",
        "segments[1].key",
        "repeats an earlier segment key (law_private_ai)",
    ),
]


@pytest.mark.parametrize(("old", "new", "field", "reason"), INVALID_RULES)
def test_an_invalid_rule_names_its_field_and_changes_nothing(
    database: Path, tmp_path: Path, old: str, new: str, field: str, reason: str
) -> None:
    before = sha256(database)

    run = sync(seed_dir(tmp_path, edited(old, new)))

    assert (run.code, run.out) == (1, "")
    assert run.err.startswith(f"seed/segments.yaml: {field}: ")
    assert reason in run.err
    assert run.err.count("\n") == 1
    assert sha256(database) == before


def test_an_invalid_file_changes_a_seeded_database_in_no_way(
    database: Path, tmp_path: Path
) -> None:
    sync()
    before = sha256(database)

    run = sync(seed_dir(tmp_path, edited("weight: 35", "weight: 75")))

    assert run.code == 1
    assert sha256(database) == before


SYNTAX = [
    ("segments:\n\t- key: x\n", 2, "tabs aren't allowed"),
    ("segments: &anchor\n", 1, "anchors, aliases and tags"),
    ("segments: {a: 1}\n", 1, "flow mappings"),
    ("segments:\n  - key: 1.5\n", 2, "number, date or time"),
    ("segments:\n  - key: yes\n", 2, "boolean or null"),
    ("segments:\n  - key: a\n    key: b\n", 3, "repeated key 'key'"),
    ("segments:\n  - name: 'unclosed\n", 2, "unclosed quote"),
]


@pytest.mark.parametrize(("text", "line", "reason"), SYNTAX)
def test_a_syntax_problem_names_its_line_and_changes_nothing(
    database: Path, tmp_path: Path, text: str, line: int, reason: str
) -> None:
    before = sha256(database)

    run = sync(seed_dir(tmp_path, text))

    assert (run.code, run.out) == (1, "")
    assert run.err.startswith(f"seed/segments.yaml: line {line}: ")
    assert reason in run.err
    assert sha256(database) == before


def test_a_syntax_problem_in_the_shipped_layout_names_its_line(
    database: Path, tmp_path: Path
) -> None:
    lines = SHIPPED_TEXT.splitlines(keepends=True)
    number = next(i for i, line in enumerate(lines, 1) if "weight: 35" in line)
    text = SHIPPED_TEXT.replace("weight: 35", "weight: 35.0", 1)

    run = sync(seed_dir(tmp_path, text))

    assert run.err.startswith(f"seed/segments.yaml: line {number}: ")


def test_a_problem_found_before_the_database_is_checked_leaves_no_database(
    config: BootstrapConfig, tmp_path: Path
) -> None:
    run = sync(seed_dir(tmp_path, "segments: yes\n"))

    assert run.code == 1
    assert run.err.startswith("seed/segments.yaml: line 1: ")
    assert not config.data_dir.exists()


MISSING = (
    "the database doesn't exist yet: start the engine once (nextron serve), "
    "then run seed sync again\n"
)


def test_no_database_is_refused_and_nothing_is_created(config: BootstrapConfig) -> None:
    run = sync()

    assert (run.code, run.out, run.err) == (1, "", MISSING)
    assert not config.data_dir.exists()


def test_a_data_dir_without_a_database_stays_empty(config: BootstrapConfig) -> None:
    config.data_dir.mkdir(parents=True)

    run = sync()

    assert (run.code, run.err) == (1, MISSING)
    assert list(config.data_dir.iterdir()) == []


def test_an_empty_database_file_is_refused_untouched(config: BootstrapConfig) -> None:
    config.data_dir.mkdir(parents=True)
    path = config.data_dir / main.DB_FILE_NAME
    path.write_bytes(b"")

    run = sync()

    assert (run.code, run.err) == (1, MISSING)
    assert path.read_bytes() == b""


def test_a_database_behind_head_is_refused_and_not_migrated(database: Path) -> None:
    run_sql(database, "UPDATE alembic_version SET version_num = '0005_1_12_notify'")
    before = sha256(database)

    run = sync()

    assert (run.code, run.out) == (1, "")
    assert run.err == (
        f"the database is at 0005_1_12_notify, not {head_revision()}: start the engine once "
        "(nextron serve) to migrate it, then run seed sync again\n"
    )
    assert stored_revisions(database) == ["0005_1_12_notify"]
    assert sha256(database) == before


def test_a_database_ahead_of_the_code_is_refused(database: Path) -> None:
    run_sql(database, "UPDATE alembic_version SET version_num = '9999_from_the_future'")

    run = sync()

    assert (run.code, run.err) == (
        1,
        "database revision 9999_from_the_future is newer than this code\n",
    )
    assert stored_revisions(database) == ["9999_from_the_future"]


def test_tables_without_a_revision_are_refused(config: BootstrapConfig) -> None:
    config.data_dir.mkdir(parents=True)
    path = config.data_dir / main.DB_FILE_NAME
    run_sql(path, "PRAGMA journal_mode=WAL", "CREATE TABLE foreign_data (id INTEGER)")
    before = sha256(path)

    run = sync()

    assert (run.code, run.out) == (1, "")
    assert run.err == (
        "the database has tables but no Alembic revision; it was not made by this engine\n"
    )
    assert sha256(path) == before
    assert query(path, "SELECT name FROM sqlite_master") == [("foreign_data",)]


def test_a_database_in_another_journal_mode_is_refused(config: BootstrapConfig) -> None:
    config.data_dir.mkdir(parents=True)
    path = config.data_dir / main.DB_FILE_NAME
    run_sql(path, "CREATE TABLE foreign_data (id INTEGER)")

    run = sync()

    assert run.code == 1
    assert "not wal; it is not this engine's database" in run.err


def test_a_file_that_is_not_a_database_is_refused(config: BootstrapConfig) -> None:
    config.data_dir.mkdir(parents=True)
    path = config.data_dir / main.DB_FILE_NAME
    path.write_bytes(b"this is not SQLite " * 100)

    run = sync()

    assert run.code == 1
    assert run.err.startswith("seed sync failed (")
    assert run.err.endswith("); nothing was changed\n")
    assert path.read_bytes() == b"this is not SQLite " * 100


def test_a_missing_seed_file_is_named(database: Path, tmp_path: Path) -> None:
    folder = tmp_path / "empty"
    folder.mkdir()

    run = sync(folder)

    assert (run.code, run.err) == (1, "seed/segments.yaml: the file doesn't exist\n")


def test_a_missing_seed_folder_is_named(database: Path, tmp_path: Path) -> None:
    run = sync(tmp_path / "nowhere")

    assert (run.code, run.err) == (1, "seed/segments.yaml: the file doesn't exist\n")


def test_a_seed_file_over_1_mib_is_refused(database: Path, tmp_path: Path) -> None:
    padding = main.MAX_SEED_FILE_BYTES + 1 - len(SHIPPED_TEXT.encode("utf-8"))
    text = SHIPPED_TEXT + "#" * (padding - 1) + "\n"
    assert len(text.encode("utf-8")) == main.MAX_SEED_FILE_BYTES + 1

    run = sync(seed_dir(tmp_path, text))

    assert (run.code, run.err) == (1, "seed/segments.yaml: the file is larger than 1 MiB\n")
    assert segment_rows(database) == []


def test_a_seed_file_of_exactly_1_mib_is_read(database: Path, tmp_path: Path) -> None:
    padding = main.MAX_SEED_FILE_BYTES - len(SHIPPED_TEXT.encode("utf-8"))
    text = SHIPPED_TEXT + "#" * (padding - 1) + "\n"
    assert len(text.encode("utf-8")) == main.MAX_SEED_FILE_BYTES

    run = sync(seed_dir(tmp_path, text))

    assert (run.code, run.out) == (0, SUMMARY_FIRST)


def test_a_seed_file_that_is_not_utf8_is_refused(database: Path, tmp_path: Path) -> None:
    data = SHIPPED_TEXT.encode("utf-8").replace("–".encode(), b"\x96")  # cp1252's en dash

    run = sync(seed_dir(tmp_path, data))

    assert (run.code, run.err) == (1, "seed/segments.yaml: the file isn't valid UTF-8\n")


def test_an_unreadable_seed_file_is_refused(database: Path, tmp_path: Path) -> None:
    folder = tmp_path / "seed"
    (folder / main.SEGMENTS_FILE_NAME).mkdir(parents=True)  # a folder, not a file

    run = sync(folder)

    assert run.code == 1
    assert run.err.startswith("seed/segments.yaml: the file can't be read (")


def test_a_seed_file_with_a_bom_and_crlf_line_ends_is_read(database: Path, tmp_path: Path) -> None:
    text = "\ufeff" + SHIPPED_TEXT.replace("\n", "\r\n")

    run = sync(seed_dir(tmp_path, text))

    assert (run.code, run.out) == (0, SUMMARY_FIRST)
    assert sync().out == SEGMENTS_MANAGED + OTHERS_UNCHANGED  # the same, with no difference


def test_an_unset_environment_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEXTRON_ENV")

    run = sync(env=None)

    assert (run.code, run.out, run.err) == (2, "", f"{ENV_ERROR}\n")


def test_an_invalid_environment_exits_2() -> None:
    run = sync(env="staging")

    assert (run.code, run.err) == (2, f"{ENV_ERROR}\n")


def test_env_picks_that_environments_data_dir(
    monkeypatch: pytest.MonkeyPatch, database: Path, local_app_data: Path
) -> None:
    monkeypatch.setenv("NEXTRON_ENV", "dev")

    run = sync(env="test")

    assert (run.code, run.out) == (0, SUMMARY_FIRST)
    assert len(segment_rows(database)) == 2
    assert not (local_app_data / "Nextron" / "dev").exists()


def test_without_env_it_uses_nextron_env(
    monkeypatch: pytest.MonkeyPatch, database: Path, local_app_data: Path
) -> None:
    monkeypatch.setenv("NEXTRON_ENV", "dev")

    run = sync(env=None)

    assert (run.code, run.err) == (1, MISSING)  # dev has no database
    assert not (local_app_data / "Nextron" / "dev").exists()
    assert segment_rows(database) == []


def noop(job: Job) -> None:
    del job


def test_a_sync_never_re_scores_or_touches_another_table(
    database: Path, units: UnitOfWorkFactory, tmp_path: Path
) -> None:
    job_types = JobTypes()
    job_types.register_job_type(
        JobType(
            "probe_job", Lane.BULK, frozenset({Lane.BULK}), 50, timedelta(minutes=1), 0, noop
        )
    )
    job_types.freeze()
    with units.open():
        JobQueue(FrozenClock(), job_types).enqueue_tick("probe_job")
    with units.open():
        Setup(FrozenClock()).save({"sender_timezone": "Europe/London"})
    sync()
    others = frozenset({"segments", "events"})
    before = table_dump(database, skip=others)
    events_before = event_rows(database)
    assert before["jobs"] and before["settings"]
    changed = edited("weight: 35", "weight: 40")

    run = sync(seed_dir(tmp_path, changed), clock=FrozenClock(LATER))

    assert "law_private_ai: differs in signal_types[law_expansion].weight" in run.out
    assert table_dump(database, skip=others) == before
    assert event_rows(database) == events_before  # Story 11.5: no Segment is written
    assert query(database, "SELECT status FROM jobs") == [("queued",)]


def test_it_takes_no_lock_writes_no_log_and_closes_the_database(
    config: BootstrapConfig, database: Path, tmp_path: Path
) -> None:
    sync()
    sync(seed_dir(tmp_path, edited("weight: 35", "weight: 75")))  # a refused run too

    assert sorted(p.name for p in config.data_dir.iterdir()) == ["nextron.db"]
    moved = database.rename(database.with_name("moved.db"))  # no handle is left open
    assert moved.is_file()


def test_a_failure_inside_the_sync_rolls_back_and_says_so(
    database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    real = setup_api._record_segment

    def record_then_fail(segment_id: str, *args: Any) -> None:
        calls.append(segment_id)
        real(segment_id, *args)
        if len(calls) == 2:
            raise RuntimeError("boom, with details that are never printed")

    monkeypatch.setattr(setup_api, "_record_segment", record_then_fail)

    run = sync()

    assert (run.code, run.out) == (1, "")
    assert run.err == "seed sync failed (RuntimeError); nothing was changed\n"
    assert segment_rows(database) == []
    assert event_rows(database) == []
    assert len(calls) == 2


def test_the_sync_logs_its_counts(
    database: Path, units: UnitOfWorkFactory, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.setup.api")
    law, ops = shipped_defs()

    sync()
    sync(clock=FrozenClock(LATER))
    store(units, renamed(law, "aa_old", "aa_"), renamed(ops, "zz_old", "zz_"))
    run = sync(seed_dir(tmp_path, edited("weight: 35", "weight: 40")), clock=FrozenClock(LATER))

    records = [r for r in caplog.records if r.getMessage() == "segments_synced"]
    assert run.code == 0
    assert {r.levelno for r in records} == {logging.INFO}
    counts = ("created_count", "changed_count", "unchanged_count", "not_in_file_count")
    assert [[getattr(r, name) for name in counts] for r in records] == [
        [2, 0, 0, 0],  # the first sync
        [0, 0, 0, 0],  # the same file again: managed, nothing written
        [0, 0, 0, 0],  # managed: aa_old and zz_old are not stored either
        [0, 0, 0, 0],  # managed
    ]
    managed = [(getattr(r, "managed"), getattr(r, "differences_count")) for r in records]
    assert managed[:2] == [(False, 0), (True, 0)]  # a managed no-op told apart from a bootstrap
    assert [flag for flag, _ in managed[2:]] == [True, True]
    assert managed[3][1] >= 1  # the edited weight is a difference


# The use case ------------------------------------------------------------------------------------


def shipped_defs() -> tuple[SegmentDef, ...]:
    return validate_segments(seed_yaml.parse(SHIPPED_TEXT))


def test_sync_segments_returns_what_it_did(units: UnitOfWorkFactory) -> None:
    law, ops = shipped_defs()
    with units.open():
        first = Setup(FrozenClock()).sync_segments([ops, law])
    changed_ops = dataclasses.replace(ops, name="Ops-Automation 2")
    with units.open():
        second = Setup(FrozenClock()).sync_segments([changed_ops])

    assert first == SegmentSync(
        created=(("ops_automation", 1), ("law_private_ai", 1)),
        changed=(),
        unchanged=(),
        not_in_file=(),
    )
    assert second == SegmentSync(  # Story 11.5: the stored Segment isn't touched
        (),
        (),
        (),
        (),
        managed=True,
        differences=(
            SegmentDifference("ops_automation", None, ("name",)),
            SegmentDifference("law_private_ai", "dashboard"),
        ),
    )
    with units.open(read_only=True):
        assert setup_queries.segment("ops_automation").version == 1  # type: ignore[union-attr]


def test_stored_segments_come_back_in_key_order(units: UnitOfWorkFactory) -> None:
    law, ops = shipped_defs()
    store(  # stored out of key order
        units,
        renamed(ops, "zz_ops", "zz_"),
        renamed(law, "aa_law", "aa_"),
        renamed(law, "mm_law", "mm_"),
    )

    with units.open():
        result = Setup(FrozenClock()).sync_segments(())
    with units.open(read_only=True):
        seeded = setup_queries.segments()

    assert [each.key for each in result.differences] == ["aa_law", "mm_law", "zz_ops"]
    assert [s.key for s in seeded] == ["aa_law", "mm_law", "zz_ops"]


def test_the_version_check_refuses_a_version_below_1(database: Path) -> None:
    sync()

    with pytest.raises(sqlite3.IntegrityError, match="ck_segments_version"):
        run_sql(database, "UPDATE segments SET version = 0")

    assert [row[3] for row in segment_rows(database)] == [1, 1]


def test_sync_segments_reads_the_clock_once(units: UnitOfWorkFactory) -> None:
    reads: list[int] = []

    class CountingClock(FrozenClock):
        def now(self) -> Any:
            reads.append(1)
            return super().now()

    with units.open():
        Setup(CountingClock()).sync_segments(shipped_defs())

    assert len(reads) == 1


def test_sync_segments_with_nothing_lists_what_is_stored(units: UnitOfWorkFactory) -> None:
    with units.open():
        Setup(FrozenClock()).sync_segments(shipped_defs())
    with units.open():
        result = Setup(FrozenClock()).sync_segments(())

    assert result == SegmentSync(
        (),
        (),
        (),
        (),
        True,
        (
            SegmentDifference("law_private_ai", "dashboard"),
            SegmentDifference("ops_automation", "dashboard"),
        ),
    )


@pytest.mark.parametrize(
    ("make", "error"),
    [
        (lambda law, ops: [law, {"key": "x"}], TypeError),
        (lambda law, ops: [law, "ops_automation"], TypeError),
        (lambda law, ops: [law, ops, law], ValueError),
        (lambda law, ops: [law, dataclasses.replace(ops, key="law_private_ai")], ValueError),
    ],
)
def test_sync_segments_refuses_bad_input_before_any_transaction(
    database: Path,
    units: UnitOfWorkFactory,
    make: Callable[[SegmentDef, SegmentDef], list[Any]],
    error: type[Exception],
) -> None:
    law, ops = shipped_defs()
    before = sha256(database)

    with units.open(), pytest.raises(error):
        Setup(FrozenClock()).sync_segments(make(law, ops))

    assert segment_rows(database) == []
    assert sha256(database) == before


def test_sync_segments_refuses_a_hand_built_definition_that_breaks_a_rule(
    database: Path, units: UnitOfWorkFactory
) -> None:
    law, ops = shipped_defs()
    broken = dataclasses.replace(
        law,
        signal_types=(dataclasses.replace(law.signal_types[0], score_only=True),),
    )

    with units.open(), pytest.raises(InvalidSegments) as caught:
        Setup(FrozenClock()).sync_segments([broken, ops])

    assert caught.value.field == (
        "segments[law_private_ai].signal_types[law_hiring_legal_ops].score_only"
    )
    assert segment_rows(database) == []


# The queries ----------------------------------------------------------------------------------------


def test_the_queries_read_the_seeded_segments_by_key(units: UnitOfWorkFactory) -> None:
    law, ops = shipped_defs()
    sync()

    with units.open(read_only=True):
        everything = setup_queries.segments()
        one = setup_queries.segment("ops_automation")
        none = setup_queries.segment("missing")

    assert [(s.key, s.version, s.definition) for s in everything] == [
        ("law_private_ai", 1, law),
        ("ops_automation", 1, ops),
    ]
    assert one == everything[1]
    assert none is None
    assert len(one.id) == 26
    assert one.definition.titles()[-3:] == ("Controller", "CFO", "Finance Director")
    with pytest.raises(dataclasses.FrozenInstanceError):
        one.version = 2  # type: ignore[misc]


def test_the_queries_return_nothing_before_a_sync(units: UnitOfWorkFactory) -> None:
    with units.open(read_only=True):
        assert setup_queries.segments() == ()
        assert setup_queries.segment("law_private_ai") is None


def test_a_stored_definition_that_no_longer_validates_raises(
    database: Path, units: UnitOfWorkFactory
) -> None:
    sync()
    run_sql(
        database,
        "UPDATE segments SET definition = '{\"key\": \"law_private_ai\"}' "
        "WHERE key = 'law_private_ai'",
    )

    with units.open(read_only=True), pytest.raises(InvalidSegments):
        setup_queries.segments()
    with units.open(read_only=True):
        assert setup_queries.segment("ops_automation") is not None


# The command line -------------------------------------------------------------------------------


def test_cli_dispatches_seed_sync_with_its_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from nextron import __main__ as cli

    calls: list[dict[str, object]] = []

    def seed_sync(**kwargs: object) -> int:
        calls.append(kwargs)
        return 1

    monkeypatch.setattr(cli.engine, "seed_sync", seed_sync)

    assert cli.main(["seed", "sync"]) == 1
    assert cli.main(["seed", "sync", "--env", "prod"]) == 1
    assert calls == [{"env": None}, {"env": "prod"}]


@pytest.mark.parametrize(
    "parsed",
    [
        {"command": "seed", "seed_command": "pull", "env": None},  # a later seed subcommand
        # a later command (restore is dispatched since Story 1.19, so this names one no story has)
        {"command": "export", "env": None, "as_json": False},
    ],
)
def test_cli_never_lets_an_undispatched_command_fall_through(
    parsed: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    import argparse

    from nextron import __main__ as cli

    class Parser:
        def parse_args(self, argv: object) -> argparse.Namespace:
            return argparse.Namespace(**parsed)

    monkeypatch.setattr(cli, "build_parser", Parser)
    monkeypatch.setattr(cli.engine, "seed_sync", lambda **kwargs: pytest.fail("seed_sync ran"))
    monkeypatch.setattr(cli.engine, "doctor", lambda **kwargs: pytest.fail("doctor ran"))

    with pytest.raises(AssertionError, match="doesn't dispatch"):
        cli.main([])


@pytest.mark.parametrize(
    "argv", [["seed"], ["seed", "pull"], ["seed", "sync", "--env", "staging"], ["seed", "sync", "x"]]
)
def test_cli_refuses_seed_without_a_valid_subcommand(
    argv: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from nextron import __main__ as cli

    monkeypatch.setattr(cli.engine, "seed_sync", lambda **kwargs: pytest.fail("ran"))

    with pytest.raises(SystemExit) as caught:
        cli.main(argv)

    assert caught.value.code == 2
    assert "usage: nextron" in capsys.readouterr().err


def test_cli_seed_sync_runs_end_to_end(database: Path) -> None:
    from nextron import __main__ as cli

    assert cli.main(["seed", "sync"]) == 0
    assert [row[1] for row in segment_rows(database)] == list(KEYS)
