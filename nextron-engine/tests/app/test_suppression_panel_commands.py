"""The Suppression List panel's commands and use cases (``Compliance.suppress`` with an address
or a domain, ``import_values``, ``reverse_optout`` and the use cases ``add_value``,
``import_list`` and ``reverse_entry``; FR-50, FR-41, AD-4, AD-16, NFR-36, Story 3.5) with the
real subscriber registry on a temporary database at head, with ``FrozenClock``: every row of the
spec's I/O matrix (an unknown and a known address, a repeat, a domain, free webmail, values that
aren't valid, a shared platform, an import with each kind of line, a refused file, a reversal
returning exactly the rest of its own opt-out, and each refused reversal), the published
notifications (a domain held by a merged Lead goes out with the survivor), the Pipeline Stage
left alone, ``events`` rows and notifications free of personal data and of the reason, the
import's batches, and the reads
(``queries.compliance``, ``queries.research.contacts_with_email``,
``queries.discovery.lead_by_web_domain``)."""

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.compliance import api as compliance_api
from nextron.app.compliance.api import (
    AddedValue,
    Compliance,
    ImportCounts,
    ImportOutcome,
    SuppressResult,
)
from nextron.app.discovery.api import Discovery
from nextron.app.pipeline.api import Pipeline
from nextron.app.queries import compliance as compliance_queries
from nextron.app.queries import discovery as discovery_queries
from nextron.app.queries import research as research_queries
from nextron.app.research.api import Research
from nextron.app.setup.api import Setup
from nextron.db.repos.compliance import suppression_entries as entries_repo
from nextron.db.uow import UnitOfWork, UnitOfWorkFactory
from nextron.domain import seed_yaml
from nextron.domain.contactability import (
    AddressSubject,
    DomainSubject,
    KeyKind,
    SuppressionScope,
    key_hash,
)
from nextron.domain.notifications import Notification, SubjectSuppressed
from nextron.domain.segments import validate_segments
from nextron.domain.suppression_entry import EntryStatus, SuppressionSource
from nextron.domain.suppression_list import (
    ALREADY_REVERSED,
    FILE_EMPTY,
    FILE_NOT_UTF8,
    FILE_TOO_LARGE,
    FILE_TOO_MANY,
    FREE_WEBMAIL,
    MAX_FILE_BYTES,
    MAX_VALUES,
    NOT_VALID,
    ONLY_RULE_MATCHED,
    REASON_INVALID,
    REASON_REQUIRED,
    SHARED_PLATFORM,
    UNKNOWN_ENTRY,
    SuppressionRefused,
    entry_value,
)
from tests.app.test_suppression import (
    ADDRESS,
    CAUSE_REF,
    COMPLIANCE_LOGGER,
    EMAIL,
    LATER,
    LATER_STAMP,
    LINKEDIN,
    PERSON,
    PROFILE,
    SHIPPED,
    T0,
    World,
    assert_no_personal_data,
    entry,
    jane,
    queue_on,
)
from tests.fakes.clock import FrozenClock

REVERSED_AT = LATER + timedelta(hours=1)
REVERSED_STAMP = "2026-11-18T11:00:00.000000Z"
REASON = "the rule misread a yes"
FIRM = key_hash("domain", "firm.co.uk")
OTHER_ULID = "01JD3QZ8X5V9K2M7N4P6R8T0AF"
OTHER_CAUSE = "01JD3QZ8X5V9K2M7N4P6R8T0AG"


@pytest.fixture
def world(db_engine: Engine) -> World:
    clock = FrozenClock(T0)
    units = UnitOfWorkFactory(db_engine, main.build_subscribers(clock=clock))
    segments = validate_segments(seed_yaml.parse(SHIPPED.read_text(encoding="utf-8")))
    with units.open():
        Setup(clock).sync_segments(segments)
    found = World(
        units,
        db_engine,
        clock,
        Discovery(clock, Pipeline(clock), queue_on(clock)),
        Research(clock),
        Compliance(clock),
    )
    [(segment,), *_] = found.sql("SELECT id FROM segments ORDER BY key")
    found.segment = segment
    clock.set(LATER)
    return found


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[Notification]]:
    """Every notification published, in order, while the real subscribers still run."""
    seen: list[Notification] = []
    original = UnitOfWork.publish

    def recording(self: UnitOfWork, notification: Notification) -> None:
        seen.append(notification)
        original(self, notification)

    monkeypatch.setattr(UnitOfWork, "publish", recording)
    yield seen


def add(world: World, text: str) -> AddedValue:
    with world.units.open():
        return world.compliance.add_value(text)


def import_list(world: World, data: bytes) -> ImportOutcome:
    with world.units.open():
        return world.compliance.import_list(data)


def reverse(world: World, entry_id: str, reason: str = REASON) -> int:
    world.clock.set(REVERSED_AT)
    with world.units.open():
        return world.compliance.reverse_entry(entry_id, reason)


def refused(fn: Any, *args: Any) -> str:
    with pytest.raises(SuppressionRefused) as caught:
        fn(*args)
    return caught.value.message


def created(entry_id: str, source: str = "operator") -> tuple[Any, ...]:
    return ("suppression_entries", entry_id, None, "active", source, None, "operator", LATER_STAMP)


def operator(
    entry_id: str, kind: str, value_hash: str, domain: str | None = None
) -> dict[str, Any]:
    return entry(entry_id, kind, value_hash, "operator", domain, None)


# Add an address ---------------------------------------------------------------------------------


def test_an_unknown_address_writes_one_operator_entry_and_publishes_without_a_contact(
    world: World, published: list[Notification], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    added = add(world, "  Jane@Firm.co.uk ")

    [entry_id] = added.written
    assert added == AddedValue((entry_id,), LATER, "operator")
    key = key_hash("address", "jane@firm.co.uk")
    assert world.entries() == [operator(entry_id, "address", key)]
    assert world.entry_events() == [created(entry_id)]
    assert published == [SubjectSuppressed(entry_id, "operator", None, None)]
    assert_no_personal_data(world.entry_events())
    assert_no_personal_data(published)
    assert [r for r in caplog.records if r.name.startswith(COMPLIANCE_LOGGER)] == []
    assert_no_personal_data([record.getMessage() for record in caplog.records])


def test_a_known_contacts_address_writes_every_key_and_leaves_the_stage(
    world: World, published: list[Notification]
) -> None:
    lead_id, contact_id = jane(world)

    added = add(world, "JANE.DOE@firm.co.uk")

    address, person, profile = added.written
    assert world.entries() == [
        operator(address, "address", ADDRESS),
        operator(person, "person", PERSON),
        operator(profile, "profile", PROFILE),
    ]
    assert world.entry_events() == [created(address), created(person), created(profile)]
    assert published == [SubjectSuppressed(address, "operator", contact_id, lead_id)]
    assert world.stage(lead_id) == "new"
    assert_no_personal_data(world.entry_events())
    assert_no_personal_data(published)


def test_an_address_held_by_two_contacts_writes_both_and_publishes_once_for_each(
    world: World, published: list[Notification]
) -> None:
    first_lead, first = jane(world)
    second_lead = world.lead("other.co.uk")
    second = world.contact(second_lead, name="J Doe", email=EMAIL)

    added = add(world, EMAIL)

    address, person, profile, second_person = added.written
    assert [(row["kind"], row["value_hash"]) for row in world.entries()] == [
        ("address", ADDRESS),
        ("person", PERSON),
        ("profile", PROFILE),
        ("person", key_hash("person", ("J Doe", "other.co.uk"))),
    ]
    assert second_person not in (address, person, profile)
    assert published == [
        SubjectSuppressed(address, "operator", first, first_lead),
        SubjectSuppressed(address, "operator", second, second_lead),
    ]
    assert world.stage(first_lead) == world.stage(second_lead) == "new"


def test_adding_again_writes_nothing_and_answers_with_the_first_entry(
    world: World, published: list[Notification]
) -> None:
    [first] = add(world, EMAIL).written
    world.clock.set(REVERSED_AT)
    before = world.dump()

    again = add(world, EMAIL.upper())

    assert again == AddedValue((), LATER, "operator")
    assert world.dump() == before  # no new entry and no events row
    assert published[-1] == SubjectSuppressed(first, "operator", None, None)


def test_an_address_already_opted_out_answers_with_its_source(world: World) -> None:
    _, contact_id = jane(world)
    world.suppress(contact_id)  # reply_rule
    world.clock.set(REVERSED_AT)

    again = add(world, EMAIL)

    assert again == AddedValue((), LATER, "reply_rule")


def test_a_contact_that_appears_later_gets_its_other_keys_on_the_next_add(
    world: World, published: list[Notification]
) -> None:
    [address] = add(world, EMAIL).written  # no Contact holds it yet
    lead_id, contact_id = jane(world)
    world.clock.set(REVERSED_AT)

    again = add(world, EMAIL)

    person, profile = again.written
    assert again == AddedValue((person, profile), LATER, "operator")
    assert [(row["id"], row["kind"]) for row in world.entries()] == [
        (address, "address"),
        (person, "person"),
        (profile, "profile"),
    ]
    assert published[-1] == SubjectSuppressed(address, "operator", contact_id, lead_id)


# Add a domain -----------------------------------------------------------------------------------


def test_a_domain_is_normalised_and_published_with_the_lead_holding_it(
    world: World, published: list[Notification]
) -> None:
    lead_id, _ = jane(world)

    added = add(world, "https://www.Firm.co.uk/x")

    [entry_id] = added.written
    assert world.entries() == [operator(entry_id, "domain", FIRM, "firm.co.uk")]
    assert world.entry_events() == [created(entry_id)]
    assert published == [SubjectSuppressed(entry_id, "operator", None, lead_id)]
    assert world.stage(lead_id) == "new"


def test_a_domain_no_lead_holds_is_published_with_no_lead(
    world: World, published: list[Notification]
) -> None:
    [entry_id] = add(world, "firm.co.uk").written

    assert published == [SubjectSuppressed(entry_id, "operator", None, None)]


def merged_holder(world: World) -> tuple[str, str]:
    """A Lead on firm.co.uk merged into an older Lead; the survivor's and the loser's ids."""
    survivor = world.lead("survivor.co.uk")
    world.clock.set(LATER + timedelta(minutes=1))
    loser = world.lead("firm.co.uk")
    world.clock.set(LATER + timedelta(minutes=2))
    world.transact(lambda: world.discovery.merge(survivor, loser))
    held = world.read(lambda: discovery_queries.lead_by_web_domain("firm.co.uk"))
    assert held is not None and (held.id, held.merged_into) == (loser, survivor)
    return survivor, loser


def test_a_domain_held_by_a_merged_lead_is_published_with_its_survivor(
    world: World, published: list[Notification]
) -> None:
    survivor, _ = merged_holder(world)
    published.clear()

    [entry_id] = add(world, "firm.co.uk").written

    assert published == [SubjectSuppressed(entry_id, "operator", None, survivor)]


def test_a_broken_merge_chain_publishes_the_lead_holding_the_domain(
    world: World, published: list[Notification], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, loser = merged_holder(world)
    monkeypatch.setattr(discovery_queries, "survivor_of", lambda lead_id: None)
    published.clear()

    [entry_id] = add(world, "firm.co.uk").written

    assert published == [SubjectSuppressed(entry_id, "operator", None, loser)]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("gmail.com", FREE_WEBMAIL),
        ("mail.yahoo.co.uk", FREE_WEBMAIL),
        ("https://www.hotmail.com/", FREE_WEBMAIL),
        ("jane@", NOT_VALID),
        ("not a domain", NOT_VALID),
        ("", NOT_VALID),
        ("co.uk", NOT_VALID),
        ("mailto:jane@firm.co.uk", NOT_VALID),
        ("https://facebook.com/firm", SHARED_PLATFORM),
        ("sites.google.com/view/firm", SHARED_PLATFORM),
    ],
)
def test_a_refused_value_writes_and_publishes_nothing(
    world: World, published: list[Notification], text: str, message: str
) -> None:
    jane(world)
    before = world.dump()

    assert refused(add, world, text) == message
    assert world.dump() == before
    assert published == []


# The subjects' scopes ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("subject", "scope"),
    [
        (AddressSubject(EMAIL), SuppressionScope.FIRM),
        (DomainSubject("firm.co.uk"), SuppressionScope.PERSON),
    ],
)
def test_a_typed_subject_takes_only_its_own_scope(
    world: World,
    published: list[Notification],
    subject: AddressSubject | DomainSubject,
    scope: SuppressionScope,
) -> None:
    before = world.dump()

    with pytest.raises(ValueError) as caught:
        world.transact(
            lambda: world.compliance.suppress(subject, scope, SuppressionSource.OPERATOR, None)
        )

    assert str(caught.value) in (
        "suppress: an AddressSubject takes scope person only",
        "suppress: a DomainSubject takes scope firm only",
    )
    assert world.dump() == before
    assert published == []


def test_a_typed_subject_may_carry_a_cause_ref_and_an_opt_out_source(
    world: World, published: list[Notification]
) -> None:
    lead_id, contact_id = jane(world)

    result = world.transact(
        lambda: world.compliance.suppress(
            AddressSubject(EMAIL),
            SuppressionScope.PERSON,
            SuppressionSource.ONE_CLICK,
            CAUSE_REF,
        )
    )

    assert len(result.written) == 3
    assert {row["cause_ref"] for row in world.entries()} == {CAUSE_REF}
    assert published == [SubjectSuppressed(result.written[0], "one_click", contact_id, lead_id)]
    assert world.stage(lead_id) == "lost"  # an opt-out source still closes the Lead


# Import -----------------------------------------------------------------------------------------


def test_an_import_counts_added_already_and_invalid_lines(
    world: World, published: list[Notification]
) -> None:
    lead_id, contact_id = jane(world)
    add(world, "known.co.uk")
    data = "\n".join(
        [
            "new@other.co.uk",  # 1 added
            "NEW@other.co.uk",  # 2 already (a repeat)
            "https://www.firm.co.uk/",  # 3 added, the Lead's domain
            "gmail.com",  # 4 invalid
            "junk",  # 5 invalid
            "",  # 6 blank
            '"a@b.com",x',  # 7 added
            "known.co.uk",  # 8 already
            EMAIL,  # 9 added: Jane's address, so every key of hers
        ]
    ).encode("utf-8")

    outcome = import_list(world, data)

    assert outcome == ImportOutcome(added=4, already=2, invalid_lines=(4, 5))
    imported = [row for row in world.entries() if row["source"] == "import"]
    assert [(row["kind"], row["value_hash"]) for row in imported] == [
        ("address", key_hash("address", "new@other.co.uk")),
        ("domain", FIRM),
        ("address", key_hash("address", "a@b.com")),
        ("address", ADDRESS),
        ("person", PERSON),
        ("profile", PROFILE),
    ]
    assert all(row["cause_ref"] is None for row in imported)
    assert [event[6] for event in world.entry_events()] == ["operator"] * 7
    assert SubjectSuppressed(imported[3]["id"], "import", contact_id, lead_id) in published
    assert world.stage(lead_id) == "new"
    assert_no_personal_data(world.entry_events())
    assert_no_personal_data(published)


def test_an_import_counts_by_the_lines_own_key(world: World) -> None:
    lead_id = world.lead()
    contact_id = world.contact(lead_id, name="Jane Doe", linkedin_url=LINKEDIN)
    world.suppress(contact_id, SuppressionSource.OPERATOR)  # her person and profile keys
    assert world.contact(lead_id, name="Jane Doe", email=EMAIL) == contact_id  # now her address
    add(world, "other@firm.co.uk")

    outcome = import_list(world, f"{EMAIL}\nother@firm.co.uk\n".encode())

    # her address is new although her other keys were on the list; the other was already there
    assert outcome == ImportOutcome(added=1, already=1, invalid_lines=())
    assert [row["kind"] for row in world.entries()] == ["person", "profile", "address", "address"]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (("x@firm.co.uk\n" * (MAX_VALUES + 1)).encode(), FILE_TOO_MANY),
        (b"firm.co.uk\n" + b" " * MAX_FILE_BYTES, FILE_TOO_LARGE),
        (b"firm.co.uk\n\xff", FILE_NOT_UTF8),
        (b"firm.co.uk\n\x00", FILE_NOT_UTF8),
        (b"", FILE_EMPTY),
    ],
)
def test_a_refused_file_writes_nothing(
    world: World, published: list[Notification], data: bytes, message: str
) -> None:
    before = world.dump()

    assert refused(import_list, world, data) == message
    assert world.dump() == before
    assert published == []


def test_an_import_runs_in_batches_and_keeps_the_batches_before_a_failure(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(compliance_api, "IMPORT_BATCH", 2)
    opened: list[int] = []
    original_transaction = UnitOfWork.transaction

    @contextmanager
    def counting(self: UnitOfWork) -> Iterator[None]:
        opened.append(1)
        with original_transaction(self):
            yield

    monkeypatch.setattr(UnitOfWork, "transaction", counting)
    data = "\n".join(f"user{number}@firm.co.uk" for number in range(5)).encode()
    original_insert = entries_repo.insert
    inserted: list[str] = []

    def failing_fifth(**fields: Any) -> str:
        if len(inserted) == 4:
            raise RuntimeError("disk full")
        inserted.append(original_insert(**fields))
        return inserted[-1]

    monkeypatch.setattr(entries_repo, "insert", failing_fifth)

    with pytest.raises(RuntimeError):
        import_list(world, data)

    assert len(opened) == 3  # two batches committed, the third rolled back
    assert [row["id"] for row in world.entries()] == inserted
    monkeypatch.setattr(entries_repo, "insert", original_insert)
    assert import_list(world, data) == ImportOutcome(added=1, already=4, invalid_lines=())


def test_import_values_refuses_anything_but_entry_values(world: World) -> None:
    before = world.dump()

    with pytest.raises(TypeError):
        world.transact(
            lambda: world.compliance.import_values(
                [entry_value("firm.co.uk"), "other.co.uk"]  # type: ignore[list-item]
            )
        )

    assert world.dump() == before


def test_import_values_is_a_command_of_the_callers_transaction(world: World) -> None:
    counts = world.transact(
        lambda: world.compliance.import_values(
            [entry_value("firm.co.uk"), entry_value("www.firm.co.uk")]
        )
    )

    assert counts == ImportCounts(added=1, already=1)


# Reverse ----------------------------------------------------------------------------------------


def opted_out(world: World, cause_ref: str | None = CAUSE_REF) -> tuple[str, list[str]]:
    """Jane opted out by a reply rule: her Lead is lost; the Lead id and the three entry ids."""
    lead_id, contact_id = jane(world)
    written = list(world.suppress(contact_id, cause_ref=cause_ref).written)
    assert world.stage(lead_id) == "lost"
    return lead_id, written


def test_a_reversal_voids_one_entry_and_returns_the_rest_of_its_own_opt_out(
    world: World, published: list[Notification]
) -> None:
    lead_id, (address, person, profile) = opted_out(world)
    # another person's opt-out (another cause_ref), and the same cause_ref from another source
    other_lead = world.lead("other.co.uk")
    other = world.contact(other_lead, name="Bo Lee", email="bo@other.co.uk")
    other_opt_out = world.suppress(other, cause_ref=OTHER_CAUSE).written
    [one_click] = world.transact(
        lambda: world.compliance.suppress(
            AddressSubject("x@third.co.uk"),
            SuppressionScope.PERSON,
            SuppressionSource.ONE_CLICK,
            CAUSE_REF,
        )
    ).written
    published.clear()
    events = len(world.entry_events())

    assert reverse(world, address, f"  {REASON}  ") == tuple(sorted((person, profile)))

    rows = {row["id"]: row for row in world.entries()}
    assert rows[address] == {
        **entry(address, "address", ADDRESS),
        "status": "voided",
        "voided_at": REVERSED_STAMP,
        "void_reason": REASON,
    }
    assert rows[person]["status"] == rows[profile]["status"] == "active"
    assert world.entry_events()[events:] == [
        (
            "suppression_entries",
            address,
            "active",
            "voided",
            "operator_reversal",
            None,
            "operator",
            REVERSED_STAMP,
        )
    ]
    assert published == []
    assert world.stage(lead_id) == "lost"
    assert REASON not in repr(world.sql("SELECT * FROM events")).lower()
    assert reverse(world, person) == (profile,)
    assert reverse(world, profile) == ()
    rows = {row["id"]: row for row in world.entries()}
    assert {rows[entry_id]["status"] for entry_id in (*other_opt_out, one_click)} == {"active"}


def test_a_reversal_without_a_cause_ref_returns_no_others(world: World) -> None:
    _, (address, *_) = opted_out(world, cause_ref=None)

    assert reverse(world, address) == ()


def test_a_reversed_key_can_be_suppressed_again(world: World) -> None:
    _, (address, *_) = opted_out(world)
    reverse(world, address)

    again = add(world, EMAIL)

    [entry_id] = again.written
    assert entry_id != address
    assert again == AddedValue((entry_id,), REVERSED_AT, "operator")


@pytest.mark.parametrize(
    ("which", "reason", "message"),
    [
        ("operator", REASON, ONLY_RULE_MATCHED),
        ("voided", REASON, ALREADY_REVERSED),
        ("unknown", REASON, UNKNOWN_ENTRY),
        ("not a ulid", REASON, UNKNOWN_ENTRY),
        ("lower", REASON, UNKNOWN_ENTRY),
        ("reply_rule", "", REASON_REQUIRED),
        ("reply_rule", "   ", REASON_REQUIRED),
        ("reply_rule", "x" * 501, REASON_INVALID),
        ("reply_rule", "two\nlines", REASON_INVALID),
        # the checks run in order: the source before the reason
        ("operator", "", ONLY_RULE_MATCHED),
        ("voided", "", ALREADY_REVERSED),
        ("unknown", "", UNKNOWN_ENTRY),
    ],
)
def test_a_refused_reversal_changes_nothing(
    world: World, published: list[Notification], which: str, reason: str, message: str
) -> None:
    _, (address, person, _) = opted_out(world)
    reverse(world, person)
    [operator_entry] = add(world, "firm.co.uk").written
    targets = {
        "operator": operator_entry,
        "voided": person,
        "unknown": OTHER_ULID,
        "not a ulid": "x",
        "lower": address.lower(),
        "reply_rule": address,
    }
    published.clear()
    before = world.dump()

    assert refused(reverse, world, targets[which], reason) == message
    assert world.dump() == before
    assert published == []


@pytest.mark.parametrize(("entry_id", "reason"), [(None, REASON), (CAUSE_REF, None), (3, 4)])
def test_a_reversal_takes_strings(world: World, entry_id: object, reason: object) -> None:
    before = world.dump()

    with pytest.raises(TypeError):
        world.transact(
            lambda: world.compliance.reverse_optout(entry_id, reason)  # type: ignore[arg-type]
        )

    assert world.dump() == before


# Reads ------------------------------------------------------------------------------------------


def test_the_entry_list_pages_newest_first_and_filters(world: World) -> None:
    ids = []
    for number in range(53):
        world.clock.set(LATER + timedelta(minutes=number))
        ids += add(world, f"user{number}@firm.co.uk").written
    world.clock.set(LATER + timedelta(hours=2))
    [domain] = add(world, "firm.co.uk").written

    first = world.read(lambda: compliance_queries.entry_list())
    second = world.read(lambda: compliance_queries.entry_list(page=2))
    past = world.read(lambda: compliance_queries.entry_list(page=9))
    domains = world.read(lambda: compliance_queries.entry_list(kind=KeyKind.DOMAIN))
    voided = world.read(lambda: compliance_queries.entry_list(status=EntryStatus.VOIDED))
    imported = world.read(
        lambda: compliance_queries.entry_list(source=SuppressionSource.IMPORT)
    )

    assert (first.total, first.page, first.pages) == (54, 1, 2)
    assert [row.id for row in first.entries] == [domain, *reversed(ids[4:])]
    assert [row.id for row in second.entries] == list(reversed(ids[:4]))
    assert (past.page, past.entries) == (2, second.entries)
    assert [row.id for row in domains.entries] == [domain]
    assert (voided.total, voided.page, voided.pages, voided.entries) == (0, 1, 1, ())
    assert imported.total == 0
    assert not hasattr(first.entries[0], "void_reason")


def test_the_entry_list_checks_its_arguments(world: World) -> None:
    for bad in (
        lambda: compliance_queries.entry_list(page=0),
        lambda: compliance_queries.entry_list(page=True),  # type: ignore[arg-type]
        lambda: compliance_queries.entry_list(kind="domain"),  # type: ignore[arg-type]
        lambda: compliance_queries.entry_list(source="operator"),  # type: ignore[arg-type]
        lambda: compliance_queries.entry_list(status="active"),  # type: ignore[arg-type]
    ):
        with pytest.raises((TypeError, ValueError)):
            world.read(bad)


def test_entries_keeps_the_given_order_and_skips_unknown_ids(world: World) -> None:
    [first] = add(world, "a@firm.co.uk").written
    [second] = add(world, "b@firm.co.uk").written

    found = world.read(
        lambda: compliance_queries.entries([second, "x", OTHER_ULID, first, second])
    )

    assert [row.id for row in found] == [second, first]
    assert world.read(lambda: compliance_queries.entries([])) == ()


def test_active_entry_finds_only_an_active_key(world: World) -> None:
    _, (address, *_) = opted_out(world)

    found = world.read(lambda: compliance_queries.active_entry(KeyKind.ADDRESS, ADDRESS))
    assert found is not None
    assert (found.id, found.source, found.created_at) == (address, "reply_rule", LATER)
    reverse(world, address)
    assert world.read(lambda: compliance_queries.active_entry("address", ADDRESS)) is None


def test_contacts_with_email_and_lead_by_web_domain(world: World) -> None:
    lead_id, contact_id = jane(world)

    found = world.read(lambda: research_queries.contacts_with_email(EMAIL))
    assert [contact.id for contact in found] == [contact_id]
    assert world.read(lambda: research_queries.contacts_with_email(EMAIL.upper())) == ()
    no_email = world.read(
        lambda: research_queries.contacts_with_email(None)  # type: ignore[arg-type]
    )
    assert no_email == ()
    lead = world.read(lambda: discovery_queries.lead_by_web_domain("firm.co.uk"))
    assert lead is not None and lead.id == lead_id
    assert world.read(lambda: discovery_queries.lead_by_web_domain("other.co.uk")) is None
    no_domain = world.read(
        lambda: discovery_queries.lead_by_web_domain(3)  # type: ignore[arg-type]
    )
    assert no_domain is None


def test_the_void_writer_touches_only_an_active_entry(world: World) -> None:
    _, (address, *_) = opted_out(world)
    reverse(world, address)

    changed = world.transact(lambda: entries_repo.void(address, REVERSED_AT, "again"))

    assert changed is False
    [row] = [row for row in world.entries() if row["id"] == address]
    assert row["void_reason"] == REASON


def test_a_raw_void_of_an_operator_entry_is_still_refused_by_the_trigger(world: World) -> None:
    [entry_id] = add(world, "firm.co.uk").written

    with pytest.raises(sqlite3.IntegrityError):
        world.sql(
            "UPDATE suppression_entries SET status = 'voided', voided_at = ?, void_reason = 'x' "
            "WHERE id = ?",
            (REVERSED_STAMP, entry_id),
        )


def test_suppress_result_for_a_domain_reports_an_existing_key(world: World) -> None:
    [first] = add(world, "firm.co.uk").written

    result = world.transact(
        lambda: world.compliance.suppress(
            DomainSubject("firm.co.uk"), SuppressionScope.FIRM, SuppressionSource.IMPORT, None
        )
    )

    assert result == SuppressResult((), (first,))
