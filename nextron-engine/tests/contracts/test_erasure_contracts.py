"""Erasure contracts (FR-52, AD-3, AD-17, Story 3.13), each with probes proving the check catches
a violation.

- **A hook for every personal-data table.** Every table with a column whose PII class isn't
  ``none`` is declared exactly once, in its owner's ``ERASURE`` (``nextron.app.<owner>.api``;
  compliance's in ``nextron.app.compliance.erasure``), as a ``TableErasure``; no owner declares
  a table it doesn't own or one with no personal-data column. Each such owner's ``erase_pii`` is
  registered for ``ContactErased`` in ``nextron.main.build_subscribers``, every subscriber there
  is an ``erase_pii``, compliance's runs first (it reads the Contact before it is tombstoned)
  and research's second. The declarations are the I/O matrix's coverage row.
- **Every hashed column knows its kind.** Every ``keep_hash`` column is in
  ``pii_registry.HASH_KINDS``, every entry there is a ``keep_hash`` column, and each kind is
  ``None`` (already a hash) or a single-value key kind.
- **No cascade.** No foreign key of ``Base.metadata``, and none of the migrated database at head
  (``PRAGMA foreign_key_list``), has ``ON DELETE CASCADE``: an erasure deletes no row.
- **A handler for every store** (Story 3.14). ``ErasureStores(...).handlers`` has exactly the
  ``EXTERNAL_STORES`` as keys, in order, and every ``AD17_STORES`` store is one of them.
- **The mailbox handler arrives with the threads.** While ``threads`` is a table of
  ``Base.metadata`` (Story 6.x), the ``mailboxes`` handler must not be ``MAILBOX_PLACEHOLDER``
  and a ``MailboxPort`` in ``nextron.ports`` must have ``trash_thread`` (the Contact's Threads
  are moved to Trash, AD-17).
- **One number per store.** Every external store has a ``STORE_NUMBERS`` entry, no two share
  one, and no other store has one: a number is a job key's discriminator.
"""

import importlib
import pkgutil
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType, ModuleType

import pytest
from sqlalchemy import Column, Engine, ForeignKey, MetaData, Table, Text

from nextron import main
from nextron.app.compliance.api import Compliance
from nextron.app.compliance.erasure import Erasures
from nextron.app.compliance.erasure_stores import MAILBOX_PLACEHOLDER, ErasureStores
from nextron.app.playbook.api import Playbook
from nextron.app.research.api import Research
from nextron.db.models import Base
from nextron.db.models.base import Ulid
from nextron.db.owners import OWNERS, SHARED_WRITERS, TABLES
from nextron.db.pii_registry import HASH_KINDS, PII, PiiClass
from nextron.db.uow import is_command
from nextron.domain.erasure import HASHABLE_KINDS, Coverage, TableErasure
from nextron.domain.erasure_receipt import (
    AD17_STORES,
    EXTERNAL_STORES,
    STORE_NUMBERS,
    ErasureStore,
)
from nextron.domain.notifications import ContactErased
from tests.fakes.clock import FrozenClock

HOOK = "erase_pii"
DECLARATION = "ERASURE"
FIRST = ("compliance", "research")  # the order the first hooks run in
COVERAGE = {  # the spec's I/O matrix, coverage row
    "contacts": ("research", Coverage.TOMBSTONED),
    "provenance_records": ("research", Coverage.TOMBSTONED),
    "evidence": ("research", Coverage.TOMBSTONED),
    "crawled_pages": ("research", Coverage.TOMBSTONED),
    "page_contact_methods": ("research", Coverage.TOMBSTONED),
    "job_postings": ("research", Coverage.TOMBSTONED),  # Story 2.11
    "suppression_entries": ("compliance", Coverage.TOMBSTONED),
    "data_subject_requests": ("compliance", Coverage.KEPT),
    "erasure_receipts": ("compliance", Coverage.KEPT),  # Story 3.14
    "settings": ("setup", Coverage.NOT_HELD),
    "footer_snapshots": ("drafting", Coverage.NOT_HELD),
    "jobs": ("jobqueue", Coverage.NOT_HELD),
    "backups": ("ops", Coverage.NOT_HELD),
    "restore_tests": ("ops", Coverage.NOT_HELD),
    "alerts": ("notify", Coverage.NOT_HELD),
    "notifications": ("notify", Coverage.NOT_HELD),
    "ai_test_cases": ("playbook", Coverage.TOMBSTONED),  # Story 4.6's override cases
}

type Declarations = Mapping[str, object]  # owner -> its ERASURE (or None)


def declaring_module(owner: str) -> str:
    """The module that holds ``owner``'s ``ERASURE`` and ``erase_pii``."""
    if owner == "compliance":
        return "nextron.app.compliance.erasure"
    return f"nextron.app.{owner}.api"


def declarations() -> dict[str, object]:
    """Each owner's ``ERASURE``, or ``None`` when it declares none."""
    found: dict[str, object] = {}
    for owner in OWNERS:
        module: ModuleType = importlib.import_module(declaring_module(owner))
        found[owner] = getattr(module, DECLARATION, None)
    return found


def personal_tables(pii: Mapping[str, Mapping[str, PiiClass]]) -> set[str]:
    return {
        table
        for table, columns in pii.items()
        if any(pii_class is not PiiClass.NONE for pii_class in columns.values())
    }


def owner_of(table: str, tables: Mapping[str, Mapping[str, Sequence[str]]]) -> str | None:
    owners = [writer for writer in tables.get(table, {}) if writer not in SHARED_WRITERS]
    return owners[0] if len(owners) == 1 else None


def declaration_problems(
    pii: Mapping[str, Mapping[str, PiiClass]],
    tables: Mapping[str, Mapping[str, Sequence[str]]],
    declared: Declarations,
) -> list[str]:
    """Each personal-data table its owner doesn't declare, and each stale, foreign or malformed
    declaration."""
    problems = []
    held = personal_tables(pii)
    for table in sorted(held):
        owner = owner_of(table, tables)
        found = declared.get(owner) if owner is not None else None
        if not isinstance(found, Mapping) or table not in found:
            problems.append(f"{table} ({owner}) has no ERASURE declaration")
    for owner, found in sorted(declared.items()):
        if found is None:
            continue
        if not isinstance(found, Mapping):
            problems.append(f"{owner}'s ERASURE is not a mapping")
            continue
        for table, erasure in sorted(found.items()):
            if table not in held:
                problems.append(f"{owner} declares {table}, which holds no personal data")
            elif owner_of(table, tables) != owner:
                problems.append(f"{owner} declares {table}, which it doesn't own")
            if not isinstance(erasure, TableErasure):
                problems.append(f"{owner} declares {table} without a TableErasure")
    return problems


def _owner_of_command(subscriber: Callable[..., object]) -> str:
    module = str(getattr(subscriber, "__module__", ""))
    parts = module.split(".")
    return parts[2] if len(parts) > 2 and parts[:2] == ["nextron", "app"] else module


def hook_problems(
    pii: Mapping[str, Mapping[str, PiiClass]],
    tables: Mapping[str, Mapping[str, Sequence[str]]],
    subscribers: Sequence[Callable[..., object]],
) -> list[str]:
    """Each owner of a personal-data table without a registered ``erase_pii``, each subscriber
    that isn't one, and a wrong order of the first two."""
    problems = []
    registered = [_owner_of_command(subscriber) for subscriber in subscribers]
    for subscriber, owner in zip(subscribers, registered, strict=True):
        name = getattr(subscriber, "__name__", repr(subscriber))
        if name != HOOK:
            problems.append(f"{owner}'s {name} is registered for ContactErased")
    owners = {owner_of(table, tables) for table in personal_tables(pii)}
    for owner in sorted(str(owner) for owner in owners):
        if owner not in registered:
            problems.append(f"{owner} registers no erase_pii for ContactErased")
    if tuple(registered[: len(FIRST)]) != FIRST:
        problems.append("compliance's erase_pii must run first, then research's")
    return problems


def test_every_personal_data_table_is_declared_by_its_owner() -> None:
    assert declaration_problems(PII, TABLES, declarations()) == []


def test_every_owner_of_a_personal_data_table_registers_its_hook_in_order() -> None:
    subscribers = main.build_subscribers().subscribers_of(ContactErased)

    assert hook_problems(PII, TABLES, subscribers) == []
    assert len(subscribers) == len({owner for owner, _ in COVERAGE.values()})


def test_the_declarations_are_the_coverage_row() -> None:
    found = {
        table: (owner, erasure.coverage)
        for owner, declared in declarations().items()
        if isinstance(declared, Mapping)
        for table, erasure in declared.items()
        if isinstance(erasure, TableErasure)
    }

    assert found == COVERAGE
    assert set(found) == personal_tables(PII)
    assert all(
        isinstance(declared, MappingProxyType) or declared is None
        for declared in declarations().values()
    )


# Probes -----------------------------------------------------------------------------------------


def _without(declared: Declarations, owner: str, table: str) -> dict[str, object]:
    copy = dict(declared)
    found = copy[owner]
    assert isinstance(found, Mapping)
    copy[owner] = {name: value for name, value in found.items() if name != table}
    return copy


def test_the_declaration_check_names_a_missing_table() -> None:
    declared = _without(declarations(), "research", "evidence")

    assert declaration_problems(PII, TABLES, declared) == [
        "evidence (research) has no ERASURE declaration"
    ]


def test_the_declaration_check_names_an_owner_without_declarations() -> None:
    declared = {**declarations(), "ops": None}

    assert declaration_problems(PII, TABLES, declared) == [
        "backups (ops) has no ERASURE declaration",
        "restore_tests (ops) has no ERASURE declaration",
    ]


def test_the_declaration_check_names_a_new_personal_data_table() -> None:
    pii = {**PII, "probe_rows": {"id": PiiClass.NONE, "note": PiiClass.REDACT}}
    tables = {**TABLES, "probe_rows": {"scoring": ("id", "note")}}

    assert declaration_problems(pii, tables, declarations()) == [
        "probe_rows (scoring) has no ERASURE declaration"
    ]
    subscribers = main.build_subscribers().subscribers_of(ContactErased)
    assert hook_problems(pii, tables, subscribers) == [
        "scoring registers no erase_pii for ContactErased"
    ]


def test_the_declaration_check_names_stale_and_foreign_declarations() -> None:
    erasure = TableErasure(Coverage.NOT_HELD, "probe")
    declared = {
        **declarations(),
        "setup": {"settings": erasure, "segments": erasure, "ghosts": erasure},
        "drafting": {"footer_snapshots": erasure, "contacts": erasure},
        "notify": {"alerts": erasure, "notifications": "not_held"},
        "scoring": ("a tuple",),
    }

    assert declaration_problems(PII, TABLES, declared) == [
        "drafting declares contacts, which it doesn't own",
        "notify declares notifications without a TableErasure",
        "scoring's ERASURE is not a mapping",
        "setup declares ghosts, which holds no personal data",
        "setup declares segments, which holds no personal data",
    ]


def test_the_hook_check_names_a_missing_hook_a_stranger_and_a_wrong_order() -> None:
    clock = FrozenClock()
    erasures = Erasures(clock, Compliance(clock))
    research = Research(clock)
    real = list(main.build_subscribers().subscribers_of(ContactErased))

    assert hook_problems(PII, TABLES, [real[0], *real[2:]]) == [
        "research registers no erase_pii for ContactErased",
        "compliance's erase_pii must run first, then research's",
    ]
    assert hook_problems(PII, TABLES, [research.erase_pii, erasures.erase_pii, *real[2:]]) == [
        "compliance's erase_pii must run first, then research's"
    ]
    assert hook_problems(PII, TABLES, [*real, research.reparent_lead]) == [
        "research's reparent_lead is registered for ContactErased"
    ]
    assert hook_problems(PII, TABLES, real[:-1]) == [
        "notify registers no erase_pii for ContactErased"
    ]


# Every hashed column knows its kind --------------------------------------------------------------


def hash_kind_problems(
    pii: Mapping[str, Mapping[str, PiiClass]], hash_kinds: Mapping[tuple[str, str], object]
) -> list[str]:
    """Each ``keep_hash`` column missing from ``hash_kinds``, and each entry that isn't one or
    names a kind a tombstone can't hash as."""
    problems = []
    for table, columns in sorted(pii.items()):
        for column, pii_class in columns.items():
            if pii_class is PiiClass.KEEP_HASH and (table, column) not in hash_kinds:
                problems.append(f"{table}.{column} is keep_hash but not in HASH_KINDS")
    for (table, column), kind in sorted(hash_kinds.items()):
        if pii.get(table, {}).get(column) is not PiiClass.KEEP_HASH:
            problems.append(f"{table}.{column} is in HASH_KINDS but isn't keep_hash")
        if kind is not None and kind not in HASHABLE_KINDS:
            problems.append(f"{table}.{column} hashes as {kind!r}, which no tombstone takes")
    return problems


def test_every_keep_hash_column_is_in_hash_kinds() -> None:
    assert hash_kind_problems(PII, HASH_KINDS) == []
    assert dict(HASH_KINDS) == {
        ("contacts", "email"): "address",
        ("provenance_records", "address"): "address",
        ("suppression_entries", "value_hash"): None,
    }


def test_the_hash_kind_check_catches_a_missing_a_stale_and_a_bad_entry() -> None:
    pii = {**PII, "probe_rows": {"id": PiiClass.NONE, "phone": PiiClass.KEEP_HASH}}
    hash_kinds = {
        **{key: kind for key, kind in HASH_KINDS.items() if key != ("contacts", "email")},
        ("contacts", "name"): "address",
        ("provenance_records", "address"): "person",
    }

    assert hash_kind_problems(pii, hash_kinds) == [
        "contacts.email is keep_hash but not in HASH_KINDS",
        "probe_rows.phone is keep_hash but not in HASH_KINDS",
        "contacts.name is in HASH_KINDS but isn't keep_hash",
        "provenance_records.address hashes as 'person', which no tombstone takes",
    ]


# No cascade -------------------------------------------------------------------------------------


def metadata_cascades(metadata: MetaData) -> list[str]:
    """Each foreign key in ``metadata`` with ``ON DELETE CASCADE``."""
    return sorted(
        f"{table.name}.{key.parent.name} cascades on delete"
        for table in metadata.tables.values()
        for key in table.foreign_keys
        if (key.ondelete or "").upper() == "CASCADE"
    )


def database_cascades(connection: sqlite3.Connection) -> list[str]:
    """Each foreign key of the database's tables with ``ON DELETE CASCADE``."""
    tables = [
        str(name)
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
    ]
    problems = []
    for table in tables:
        for row in connection.execute(f"PRAGMA foreign_key_list({table})"):
            if str(row[6]).upper() == "CASCADE":
                problems.append(f"{table}.{row[3]} cascades on delete")
    return sorted(problems)


def _connect(engine: Engine) -> sqlite3.Connection:
    assert engine.url.database
    return sqlite3.connect(engine.url.database)


def test_no_foreign_key_cascades_in_the_models_or_at_head(db_engine: Engine) -> None:
    connection = _connect(db_engine)
    try:
        assert database_cascades(connection) == []
        keys = sum(
            1
            for (table,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            for _ in connection.execute(f"PRAGMA foreign_key_list({table})")
        )
    finally:
        connection.close()
    assert metadata_cascades(Base.metadata) == []
    assert keys > 10  # not vacuous: the head has many foreign keys


def test_the_cascade_check_catches_a_model_that_cascades() -> None:
    metadata = MetaData()
    for table in Base.metadata.tables.values():
        table.to_metadata(metadata)
    Table(
        "probe_rows",
        metadata,
        Column("id", Ulid, primary_key=True),
        Column("contact_id", Ulid, ForeignKey("contacts.id", ondelete="CASCADE")),
        Column("note", Text),
    )

    assert metadata_cascades(metadata) == ["probe_rows.contact_id cascades on delete"]


def test_the_cascade_check_catches_a_table_that_cascades(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "probe.db")
    try:
        connection.execute("CREATE TABLE contacts (id TEXT PRIMARY KEY)")
        connection.execute(
            "CREATE TABLE probe_rows (id TEXT PRIMARY KEY, contact_id TEXT "
            "REFERENCES contacts (id) ON DELETE CASCADE)"
        )
        connection.execute(
            "CREATE TABLE fine_rows (id TEXT PRIMARY KEY, contact_id TEXT "
            "REFERENCES contacts (id) ON DELETE RESTRICT)"
        )

        assert database_cascades(connection) == ["probe_rows.contact_id cascades on delete"]
    finally:
        connection.close()


@pytest.mark.parametrize("owner", sorted({owner for owner, _ in COVERAGE.values()}))
def test_each_declaring_owner_has_an_erase_pii_command(owner: str) -> None:
    module = importlib.import_module(declaring_module(owner))

    hook = getattr(module, HOOK, None)
    if hook is None:  # compliance's, research's and playbook's are methods of their services
        service = {"compliance": Erasures, "research": Research, "playbook": Playbook}[owner]
        hook = getattr(service, HOOK)

    assert is_command(hook)


# A handler for every store (Story 3.14) ---------------------------------------------------------


def handler_problems(
    handlers: Mapping[object, object],
    external: Sequence[object] = EXTERNAL_STORES,
    ad17: Sequence[object] = AD17_STORES,
) -> list[str]:
    """Each store without a handler, each handler of no external store, a different order, and
    each AD-17 store that isn't external."""
    problems = [f"{store} has no handler" for store in external if store not in handlers]
    problems += [f"{store} isn't an external store" for store in handlers if store not in external]
    if not problems and tuple(handlers) != tuple(external):
        problems.append("the handlers aren't in EXTERNAL_STORES order")
    problems += [
        f"AD-17's {store} isn't an external store" for store in ad17 if store not in external
    ]
    return problems


def test_every_external_store_has_a_handler() -> None:
    stores = ErasureStores(FrozenClock(), None, None)  # type: ignore[arg-type]

    assert handler_problems(stores.handlers) == []
    assert set(AD17_STORES) <= set(EXTERNAL_STORES)


def test_the_handler_check_catches_a_missing_an_extra_and_a_stray_store() -> None:
    handlers = dict(ErasureStores(FrozenClock(), None, None).handlers)  # type: ignore[arg-type]
    del handlers[ErasureStore.TELEGRAM]
    handlers[ErasureStore.DATABASE] = MAILBOX_PLACEHOLDER

    assert handler_problems(handlers, ad17=(*AD17_STORES, "fax_machine")) == [
        "telegram has no handler",
        "database isn't an external store",
        "AD-17's fax_machine isn't an external store",
    ]
    built = ErasureStores(FrozenClock(), None, None)  # type: ignore[arg-type]
    reordered = dict(reversed(built.handlers.items()))
    assert handler_problems(reordered) == ["the handlers aren't in EXTERNAL_STORES order"]


# The mailbox handler arrives with the threads --------------------------------------------------


THREADS = "threads"
MAILBOX_PORT = "MailboxPort"
TRASH = "trash_thread"


def mailbox_ports() -> list[type]:
    """Every class named ``MailboxPort`` in ``nextron.ports``."""
    import nextron.ports  # noqa: PLC0415 - only this check walks the ports package

    found = []
    for info in pkgutil.walk_packages(nextron.ports.__path__, "nextron.ports."):
        module = importlib.import_module(info.name)
        port = getattr(module, MAILBOX_PORT, None)
        if isinstance(port, type) and port.__module__ == module.__name__:
            found.append(port)
    return found


def mailbox_problems(
    tables: Sequence[str], mailboxes: object, ports: Sequence[type]
) -> list[str]:
    """While a ``threads`` table exists: the placeholder mailbox handler, and no
    ``MailboxPort.trash_thread``."""
    if THREADS not in tables:
        return []
    problems = []
    if mailboxes is MAILBOX_PLACEHOLDER:
        problems.append("threads exists, but the mailboxes handler is still MAILBOX_PLACEHOLDER")
    if not any(callable(getattr(port, TRASH, None)) for port in ports):
        problems.append("threads exists, but no MailboxPort has trash_thread")
    return problems


def test_a_threads_table_needs_a_mailbox_handler_that_trashes_threads() -> None:
    stores = ErasureStores(FrozenClock(), None, None)  # type: ignore[arg-type]

    assert mailbox_problems(
        sorted(Base.metadata.tables), stores.handlers[ErasureStore.MAILBOXES], mailbox_ports()
    ) == []


def test_the_mailbox_check_catches_threads_without_the_handler_or_the_port() -> None:
    class MailboxPort:
        def list_threads(self) -> None: ...

    class TrashingPort:
        def trash_thread(self, thread_id: str) -> None: ...

    tables = [*sorted(Base.metadata.tables), THREADS]

    assert mailbox_problems(tables, MAILBOX_PLACEHOLDER, [MailboxPort]) == [
        "threads exists, but the mailboxes handler is still MAILBOX_PLACEHOLDER",
        "threads exists, but no MailboxPort has trash_thread",
    ]
    assert mailbox_problems(tables, object(), [TrashingPort]) == []
    assert mailbox_problems(sorted(Base.metadata.tables), MAILBOX_PLACEHOLDER, []) == []


# One number per store ---------------------------------------------------------------------------


def number_problems(
    numbers: Mapping[object, object], external: Sequence[object] = EXTERNAL_STORES
) -> list[str]:
    """Each external store without a number, each number shared, and each number of a store
    that isn't external or isn't a positive int."""
    problems = [f"{store} has no number" for store in external if store not in numbers]
    seen: dict[object, object] = {}
    for store, number in numbers.items():
        if store not in external:
            problems.append(f"{store} isn't an external store")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            problems.append(f"{store}'s number isn't a positive int")
        elif number in seen:
            problems.append(f"{store} reuses {seen[number]}'s number {number}")
        else:
            seen[number] = store
    return problems


def test_every_external_store_has_its_own_number() -> None:
    assert number_problems(STORE_NUMBERS) == []
    assert sorted(STORE_NUMBERS.values()) == list(range(1, len(EXTERNAL_STORES) + 1))


def test_the_number_check_catches_a_missing_a_shared_and_a_stray_number() -> None:
    numbers = dict(STORE_NUMBERS)
    del numbers[ErasureStore.LOGS]
    numbers[ErasureStore.SINK_FILES] = 1
    numbers[ErasureStore.DATABASE] = 0

    assert number_problems(numbers) == [
        "logs has no number",
        "sink_files reuses mailboxes's number 1",
        "database isn't an external store",
        "database's number isn't a positive int",
    ]
