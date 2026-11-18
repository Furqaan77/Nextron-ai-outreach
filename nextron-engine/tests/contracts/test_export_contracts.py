"""The subject access export covers every personal-data column (FR-52, AD-17, Story 3.15).

The tables that must be covered are derived, never listed: every table that
``nextron.db.pii_registry.PII`` gives a column classified other than ``none``, and every table
of ``nextron.db.models.Base.metadata`` with a column whose foreign key targets ``contacts.id``
(a table about a Contact, even with ``none`` columns only). Checked here:

- (a) each such table has a ``nextron.domain.contact_export.COVERAGE`` entry, whose owner is the
  table's writer in ``nextron.db.owners.TABLES``, and no entry is stale (a table with neither a
  personal-data column nor a foreign key to ``contacts.id``, or no table at all);
- (b) every ``shown_as`` and ``withheld`` key of an ``Exported`` entry is a personal-data column
  of its table;
- (c) each exporting owner has a callable ``nextron.app.queries.<owner>.export_pii``;
- (d) a seeded full export (``tests.app.test_contact_export.Seeded``, on real units of work) has
  rows in every ``Exported`` section, and each row carries every personal-data column of its
  table (or the column's ``shown_as`` key) that isn't withheld;
- (e) no withheld value stored in the database appears in that file.

Each check has a probe test showing it catches the problem: a probe table with a ``redact``
column and no ``COVERAGE`` entry fails (a), and so does a probe table whose only link to a person
is a foreign key to ``contacts.id``.
"""

import importlib
import json
from collections.abc import Callable, Iterable, Mapping
from types import MappingProxyType, ModuleType
from typing import Any

import pytest
from sqlalchemy import Column, ForeignKey, MetaData, String, Table

from nextron.db.models import Base
from nextron.db.owners import TABLES
from nextron.db.pii_registry import PII, PiiClass
from nextron.domain.contact_export import (
    COVERAGE,
    EXTRA_SECTIONS,
    SECTIONS,
    Coverage,
    Exported,
    NotAboutAContact,
)
from tests.app.test_contact_export import Seeded, export, world  # noqa: F401 (the fixture)
from tests.app.test_suppression import World

type Pii = Mapping[str, Mapping[str, PiiClass]]
type Tables = Mapping[str, Mapping[str, tuple[str, ...]]]


def personal_columns(pii: Pii) -> dict[str, frozenset[str]]:
    """Each table with a column classified other than ``none``, with those columns."""
    found = {
        table: frozenset(column for column, kind in columns.items() if kind is not PiiClass.NONE)
        for table, columns in pii.items()
    }
    return {table: columns for table, columns in found.items() if columns}


CONTACT_KEY = "contacts.id"


def contact_tables(metadata: MetaData) -> frozenset[str]:
    """Each table with a column whose foreign key targets ``contacts.id``."""
    return frozenset(
        table.name
        for table in metadata.tables.values()
        for column in table.columns
        for key in column.foreign_keys
        if key.target_fullname == CONTACT_KEY
    )


CONTACT_TABLES = contact_tables(Base.metadata)


def coverage_problems(
    coverage: Mapping[str, Coverage],
    pii: Pii,
    tables: Tables,
    contact_refs: Iterable[str] = CONTACT_TABLES,
) -> list[str]:
    """Checks (a) and (b)."""
    needed = personal_columns(pii)
    referencing = frozenset(contact_refs)
    problems = []
    for table in sorted(set(needed) | referencing):
        entry = coverage.get(table)
        if entry is None:
            why = "holds personal data" if table in needed else "references a Contact"
            problems.append(f"{table} {why} but has no COVERAGE entry")
            continue
        writers = sorted(tables.get(table, {}))
        if entry.owner not in writers:
            problems.append(f"{table}: COVERAGE names {entry.owner}, but its writers are {writers}")
    for table in sorted(set(coverage) - set(needed) - referencing):
        problems.append(f"{table}: a stale COVERAGE entry (the table holds no personal data)")
    for table, entry in sorted(coverage.items()):
        if not isinstance(entry, Exported):
            continue
        for kind, columns in (("shown_as", entry.shown_as), ("withheld", entry.withheld)):
            for column in sorted(set(columns) - needed.get(table, frozenset())):
                problems.append(f"{table}.{column}: {kind} names no personal-data column")
    return problems


def exporter_problems(
    coverage: Mapping[str, Coverage], load: Callable[[str], ModuleType] = importlib.import_module
) -> list[str]:
    """Check (c)."""
    owners = sorted({entry.owner for entry in coverage.values() if isinstance(entry, Exported)})
    problems = []
    for owner in owners:
        try:
            module = load(f"nextron.app.queries.{owner}")
        except ModuleNotFoundError:
            problems.append(f"{owner}: no nextron.app.queries.{owner}")
            continue
        if not callable(getattr(module, "export_pii", None)):
            problems.append(f"{owner}: nextron.app.queries.{owner} has no export_pii")
    return problems


def row_problems(file: Mapping[str, Any], coverage: Mapping[str, Coverage], pii: Pii) -> list[str]:
    """Check (d)."""
    needed = personal_columns(pii)
    problems = []
    for table, entry in sorted(coverage.items()):
        if not isinstance(entry, Exported):
            continue
        rows = file.get(entry.owner, {}).get(entry.section, [])
        if not rows:
            problems.append(f"{entry.owner}.{entry.section} ({table}) has no row to check")
        for index, row in enumerate(rows):
            for column in sorted(needed.get(table, frozenset()) - set(entry.withheld)):
                key = entry.shown_as.get(column, column)
                if key not in row:
                    problems.append(
                        f"{entry.owner}.{entry.section}[{index}] lacks {key} ({table}.{column})"
                    )
    return problems


def withheld_problems(text: str, withheld: Mapping[str, Iterable[str]]) -> list[str]:
    """Check (e): ``withheld`` maps ``table.column`` to its stored values."""
    return [
        f"{name}: a withheld value appears in the file"
        for name, values in sorted(withheld.items())
        for value in values
        if value and value in text
    ]


def withheld_values(world: World, coverage: Mapping[str, Coverage]) -> dict[str, list[str]]:
    """Every stored value of every withheld column, by ``table.column``."""
    found: dict[str, list[str]] = {}
    for table, entry in coverage.items():
        if isinstance(entry, Exported):
            for column in entry.withheld:
                rows = world.sql(f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL")
                found[f"{table}.{column}"] = [str(value) for (value,) in rows]
    return found


# The checks on the real registries --------------------------------------------------------------


def test_every_personal_data_table_is_covered_by_its_owner() -> None:
    assert coverage_problems(COVERAGE, PII, TABLES) == []
    # Story 4.6's ai_test_cases and Story 2.11's job_postings
    assert len(personal_columns(PII)) == len(COVERAGE) == 17
    assert CONTACT_TABLES == {
        "provenance_records",
        "evidence",
        "data_subject_requests",
        "erasure_receipts",  # Story 3.14
    }


def test_every_exporting_owner_has_an_exporter() -> None:
    assert exporter_problems(COVERAGE) == []


def test_every_section_is_declared_once() -> None:
    exported = [
        (entry.owner, entry.section) for entry in COVERAGE.values() if isinstance(entry, Exported)
    ]
    declared = [*exported, *EXTRA_SECTIONS]
    assert len(declared) == len(set(declared))
    assert sorted(declared) == sorted(
        (owner, section) for owner, sections in SECTIONS.items() for section in sections
    )
    assert all(
        entry.reason for entry in COVERAGE.values() if isinstance(entry, NotAboutAContact)
    )


def test_a_seeded_full_export_carries_every_personal_data_column(world: World) -> None:
    seeded = Seeded(world)

    file = export(world, seeded.jane)

    assert row_problems(json.loads(file.body), COVERAGE, PII) == []


def test_no_withheld_value_appears_in_the_file(world: World) -> None:
    seeded = Seeded(world)

    text = export(world, seeded.jane).body.decode("utf-8")

    values = withheld_values(world, COVERAGE)
    assert len(values["suppression_entries.value_hash"]) == 7  # not vacuous
    assert withheld_problems(text, values) == []


# Probes: each check catches its problem ---------------------------------------------------------


def probe_pii(**tables: dict[str, PiiClass]) -> dict[str, dict[str, PiiClass]]:
    return {**{table: dict(columns) for table, columns in PII.items()}, **tables}


def probe_tables(**tables: dict[str, tuple[str, ...]]) -> dict[str, dict[str, tuple[str, ...]]]:
    return {**{table: dict(writers) for table, writers in TABLES.items()}, **tables}


def test_a_probe_table_with_a_redact_column_and_no_entry_fails() -> None:
    pii = probe_pii(probe_notes={"id": PiiClass.NONE, "notes": PiiClass.REDACT})
    tables = probe_tables(probe_notes={"research": ("id", "notes")})

    assert coverage_problems(COVERAGE, pii, tables) == [
        "probe_notes holds personal data but has no COVERAGE entry"
    ]


def test_a_probe_table_referencing_a_contact_and_no_entry_fails() -> None:
    metadata = MetaData()
    Table("contacts", metadata, Column("id", String(26), primary_key=True))
    Table(
        "probe_links",
        metadata,
        Column("id", String(26), primary_key=True),
        Column("contact_id", String(26), ForeignKey("contacts.id")),
    )
    Table("probe_other", metadata, Column("id", String(26), primary_key=True))
    pii = probe_pii(probe_links={"id": PiiClass.NONE, "contact_id": PiiClass.NONE})
    tables = probe_tables(probe_links={"research": ("id", "contact_id")})

    found = contact_tables(metadata)

    assert found == {"probe_links"}
    assert coverage_problems(COVERAGE, pii, tables, CONTACT_TABLES | found) == [
        "probe_links references a Contact but has no COVERAGE entry"
    ]
    covered = {**COVERAGE, "probe_links": NotAboutAContact("research", "a probe")}
    assert coverage_problems(covered, pii, tables, CONTACT_TABLES | found) == []  # not stale


@pytest.mark.parametrize("kind", [PiiClass.NULL, PiiClass.KEEP_HASH])
def test_a_new_personal_column_on_a_table_with_none_needs_an_entry(kind: PiiClass) -> None:
    pii = probe_pii(segments={**PII["segments"], "owner_name": kind})

    assert coverage_problems(COVERAGE, pii, TABLES) == [
        "segments holds personal data but has no COVERAGE entry"
    ]


def test_an_entry_naming_another_owner_fails() -> None:
    coverage = {**COVERAGE, "contacts": Exported("compliance", "contact")}

    assert coverage_problems(coverage, PII, TABLES) == [
        "contacts: COVERAGE names compliance, but its writers are ['research']"
    ]


@pytest.mark.parametrize(
    ("table", "entry"),
    [
        ("segments", NotAboutAContact("setup", "no person")),
        ("gone_table", Exported("research", "gone")),
    ],
)
def test_a_stale_entry_fails(table: str, entry: Coverage) -> None:
    coverage = {**COVERAGE, table: entry}

    assert coverage_problems(coverage, PII, TABLES) == [
        f"{table}: a stale COVERAGE entry (the table holds no personal data)"
    ]


@pytest.mark.parametrize(
    ("entry", "found"),
    [
        (
            Exported("research", "pages", shown_as=MappingProxyType({"url": "address"})),
            "crawled_pages.url: shown_as names no personal-data column",
        ),
        (
            Exported("research", "pages", withheld=MappingProxyType({"body": "gone"})),
            "crawled_pages.body: withheld names no personal-data column",
        ),
    ],
)
def test_shown_as_and_withheld_must_name_personal_data_columns(
    entry: Exported, found: str
) -> None:
    coverage = {**COVERAGE, "crawled_pages": entry}

    assert coverage_problems(coverage, PII, TABLES) == [found]


def test_an_owner_without_an_exporter_fails() -> None:
    coverage = {**COVERAGE, "settings": Exported("setup", "settings")}

    assert exporter_problems(coverage) == ["setup: nextron.app.queries.setup has no export_pii"]


def test_an_owner_without_a_queries_module_fails() -> None:
    def load(name: str) -> ModuleType:
        if name.endswith(".notify"):
            raise ModuleNotFoundError(name)
        return importlib.import_module(name)

    assert exporter_problems(COVERAGE, load) == ["notify: no nextron.app.queries.notify"]


def full_file() -> dict[str, Any]:
    """A file with one row per ``Exported`` section, holding every key (d) asks for."""
    needed = personal_columns(PII)
    file: dict[str, Any] = {}
    for table, entry in COVERAGE.items():
        if isinstance(entry, Exported):
            row = {
                entry.shown_as.get(column, column): "x"
                for column in needed[table] - set(entry.withheld)
            }
            file.setdefault(entry.owner, {})[entry.section] = [row]
    return file


def test_the_row_check_passes_a_full_file_and_catches_a_missing_column() -> None:
    file = full_file()
    assert row_problems(file, COVERAGE, PII) == []

    del file["research"]["contact"][0]["email"]
    del file["research"]["pages"][0]["text_excerpts"]

    assert row_problems(file, COVERAGE, PII) == [
        "research.contact[0] lacks email (contacts.email)",
        "research.pages[0] lacks text_excerpts (crawled_pages.text)",
    ]


def test_the_row_check_catches_an_empty_section() -> None:
    file = full_file()
    file["notify"]["alerts"] = []

    assert row_problems(file, COVERAGE, PII) == ["notify.alerts (alerts) has no row to check"]


def test_the_row_check_catches_a_new_column_the_export_lacks() -> None:
    pii = probe_pii(contacts={**PII["contacts"], "nickname": PiiClass.REDACT})

    assert row_problems(full_file(), COVERAGE, pii) == [
        "research.contact[0] lacks nickname (contacts.nickname)"
    ]


def test_the_withheld_check_catches_a_value_in_the_file() -> None:
    value = "a" * 64

    assert withheld_problems(f'{{"value_hash": "{value}"}}', {"t.c": [value, ""]}) == [
        "t.c: a withheld value appears in the file"
    ]
    assert withheld_problems("{}", {"t.c": [value]}) == []
