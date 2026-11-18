"""The subject access export (``Compliance.open_dsr``, ``complete_dsr``, ``export_contact`` and
``export_on_request``; the exporters ``queries.research``, ``queries.compliance`` and
``queries.notify`` ``export_pii``; ``eventlog.history``; FR-52, AD-3, AD-4, AD-17, Story 3.15)
with the real subscriber registry on a temporary database at head, with ``FrozenClock``: every
row of the spec's I/O matrix on real units of work: a full export (every section filled, the new
request ``completed`` with its two ``events`` rows), a bare Contact (every section present, ``[]``
when empty), another Contact's rows, entries, Signals' links, page lines and request kept out,
a simulated erased Contact (tombstones plus suppression facts found by hash or by its request),
refused references and unknown Contacts writing nothing, a failure rolling the request back, the
read-only unit, the request machine, the table's checks, and no log line."""

import json
import logging
import sqlite3
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.compliance import api as compliance_api
from nextron.app.compliance.api import Compliance
from nextron.app.discovery.api import Discovery
from nextron.app.pipeline.api import Pipeline
from nextron.app.queries import compliance as compliance_queries
from nextron.app.queries import notify as notify_queries
from nextron.app.queries import research as research_queries
from nextron.app.research.api import Research
from nextron.app.setup.api import Setup
from nextron.db import eventlog
from nextron.db.models.base import new_id
from nextron.db.repos.notify import alerts as alerts_repo
from nextron.db.repos.notify import notifications as notifications_repo
from nextron.db.repos.research import crawl as crawl_repo
from nextron.db.repos.research import signals as signals_repo
from nextron.db.uow import UnitOfWorkFactory
from nextron.domain import seed_yaml
from nextron.domain.contact_export import SECTIONS, TOMBSTONE, ContactExport
from nextron.domain.contactability import ContactSubject, SuppressionScope, key_hash
from nextron.domain.machines import IllegalTransition
from nextron.domain.segments import validate_segments
from nextron.domain.subject_request import REF_INVALID, REF_REQUIRED, RequestRefused
from nextron.domain.suppression_entry import SuppressionSource
from nextron.domain.widgets import ContactMethod, Detection, MatchSource
from tests.app.test_suppression import (
    EMAIL,
    LINKEDIN,
    SHIPPED,
    T0,
    World,
    queue_on,
)
from tests.fakes.clock import FrozenClock
from tests.migration_chains import run_sql

NOW = T0 + timedelta(days=2)
NOW_STAMP = "2026-11-20T09:00:00.000000Z"
OLD_ADDRESS = "jane@oldfirm.co.uk"
BOB_EMAIL = "bob.smith@firm.co.uk"
CAMPAIGN = "01JD3QZ8X5V9K2M7N4P6R8T0CA"
JANE_CAUSE = "01JD3QZ8X5V9K2M7N4P6R8T0CB"
BOB_CAUSE = "01JD3QZ8X5V9K2M7N4P6R8T0CC"
TEAM_TEXT = (
    "Our people\n"
    "Jane Doe is a partner in the property team.\n"
    "Bob Smith heads the litigation team.\n"
    "Write to jane.doe@firm.co.uk about leases.\n"
    "Opening hours: 9 to 5"
)
BOB_TEXT = "News\nBob Smith joined the firm.\nCall us any time."
TEAM_URL = "https://firm.co.uk/team"
NEWS_URL = "https://firm.co.uk/news"
JANE_QUOTE = "Jane Doe joined as a partner"
BOB_QUOTE = "Bob Smith was promoted"
JANE_REF = "Email from Jane of 18 Nov 2026"
BOB_REF = "Letter from Bob Smith"
REQUEST_COLUMNS = (
    "id",
    "contact_id",
    "kind",
    "status",
    "request_ref",
    "opened_at",
    "completed_at",
    "receipt_id",
)
SUPPRESSION_KEYS = {"kind", "status", "source", "created_at", "voided_at", "void_reason"}


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
    return found


def export(world: World, contact_id: str, reference: str = JANE_REF) -> ContactExport:
    world.clock.set(NOW)
    with world.units.open():
        return world.compliance.export_on_request(contact_id, reference)


def document(found: ContactExport) -> dict[str, Any]:
    return dict(json.loads(found.body.decode("utf-8")))


def request_rows(world: World) -> list[dict[str, Any]]:
    columns = ", ".join(REQUEST_COLUMNS)
    rows = world.sql(f"SELECT {columns} FROM data_subject_requests ORDER BY rowid")
    return [dict(zip(REQUEST_COLUMNS, row, strict=True)) for row in rows]


def request_events(world: World) -> list[tuple[Any, ...]]:
    return world.sql(
        "SELECT entity_id, from_status, to_status, cause, cause_ref, actor, at FROM events "
        "WHERE entity = 'data_subject_requests' ORDER BY rowid"
    )


def nothing_written(world: World, before: list[list[tuple[Any, ...]]]) -> None:
    assert world.dump() == before
    assert request_rows(world) == []
    assert request_events(world) == []


# Seeding ----------------------------------------------------------------------------------------


def insert_page(world: World, lead_id: str, run_id: str, url: str, text: str | None) -> str:
    page_id = world.transact(
        lambda: crawl_repo.insert_page(
            lead_id=lead_id,
            run_id=run_id,
            url=url,
            final_url=url,
            kind="team",
            fetched_at=T0,
            http_status=200,
            content_hash="c" * 64,
            text=text or "",
            text_purge_after=T0 + timedelta(days=90),
            changed_at=T0,
        )
    )
    if text is None:
        world.sql("UPDATE crawled_pages SET text = NULL WHERE id = ?", (page_id,))
    return page_id


def method(name: ContactMethod, signature: str, matched: str) -> Detection:
    return Detection(name, signature, MatchSource.LINK, matched)


def person_evidence(world: World, lead_id: str, contact_id: str, quote: str) -> str:
    return world.transact(
        lambda: signals_repo.insert_evidence(
            lead_id=lead_id,
            evidence_type="quote",
            quote=quote,
            sought=None,
            number=None,
            fact=None,
            value=None,
            pages_checked=("https://firm.co.uk/team", "https://firm.co.uk/news"),
            source="website",
            source_url="https://firm.co.uk/team",
            retrieved_at=T0,
            extracted_by="ai:extract_people@v1",
            person_contact_id=contact_id,
            at=T0,
        )
    )


def signal(world: World, lead_id: str, signal_type: str, *evidence_ids: str) -> str:
    return world.transact(
        lambda: signals_repo.insert_signal(
            lead_id=lead_id,
            signal_type=signal_type,
            signal_class="event",
            tier=1,
            score_only=False,
            event_date=date(2026, 11, 1),
            first_seen_at=T0,
            evidence_ids=evidence_ids,
        )
    )


def address_record(world: World, contact_id: str, address: str, at: datetime) -> None:
    stamp = at.strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    world.sql(
        "INSERT INTO provenance_records (id, contact_id, subject, source_kind, source_detail, "
        "address, retrieved_at, address_class) VALUES (?, ?, 'address', 'website', "
        "'https://oldfirm.co.uk/team', ?, ?, 'guessed')",
        (new_id(), contact_id, address, stamp),
    )


class Seeded:
    """Jane (the Contact exported) and Bob (another Contact on her Lead), and what each holds."""

    def __init__(self, world: World, *, jane_entries: bool = True) -> None:
        self.lead = world.lead()
        self.jane = world.contact(
            self.lead, name="Jane Doe", role="Partner", email=EMAIL, linkedin_url=LINKEDIN
        )
        self.bob = world.contact(self.lead, name="Bob Smith", email=BOB_EMAIL)
        # an LIA basis on Jane's contact record, and an earlier address
        world.sql(
            "UPDATE provenance_records SET basis_rule = 'uk_lia', basis_campaign_id = ?, "
            "lia_ref = 'LIA-2026-01', lia_date = '2026-11-01', "
            "basis_recorded_at = '2026-11-18T09:00:00.000000Z' "
            "WHERE contact_id = ? AND subject = 'contact'",
            (CAMPAIGN, self.jane),
        )
        address_record(world, self.jane, OLD_ADDRESS, T0 - timedelta(days=30))
        # Evidence about each, a Signal each, and one Signal both support
        self.jane_evidence = person_evidence(world, self.lead, self.jane, JANE_QUOTE)
        self.bob_evidence = person_evidence(world, self.lead, self.bob, BOB_QUOTE)
        self.jane_signal = signal(world, self.lead, "law_new_partner", self.jane_evidence)
        self.bob_signal = signal(world, self.lead, "law_promotion", self.bob_evidence)
        self.shared_signal = signal(
            world, self.lead, "law_expansion", self.jane_evidence, self.bob_evidence
        )
        # a page naming both, a page naming only Bob, and their contact methods
        run_id = world.transact(
            lambda: crawl_repo.insert_run(
                lead_id=self.lead,
                site_domain="firm.co.uk",
                final_domain=None,
                started_at=T0,
                ended_at=T0,
                outcome="ok",
                error_code=None,
                pages_fetched=2,
                pages_skipped_robots=0,
                renders=0,
                errors=0,
            )
        )
        self.team_page = insert_page(world, self.lead, run_id, TEAM_URL, TEAM_TEXT)
        self.news_page = insert_page(world, self.lead, run_id, NEWS_URL, BOB_TEXT)
        world.transact(
            lambda: crawl_repo.add_contact_methods(
                self.team_page,
                [
                    method(ContactMethod.FORM, "f" * 32, f"mailto:{EMAIL}"),
                    method(ContactMethod.PHONE, "p" * 32, f"mailto:{BOB_EMAIL}"),
                    method(ContactMethod.BOOKING, "b" * 32, "https://calendly.com/firm"),
                ],
                T0,
            )
        )
        # alerts and notifications about each
        world.transact(
            lambda: alerts_repo.insert_alert(
                kind="contact_review",
                subject=self.jane,
                channel="telegram",
                text="Review Jane Doe",
                status="sent",
                at=T0,
            )
        )
        world.transact(
            lambda: alerts_repo.insert_alert(
                kind="contact_review",
                subject=self.bob,
                channel="telegram",
                text="Review Bob Smith",
                status="sent",
                at=T0,
            )
        )
        world.transact(
            lambda: notifications_repo.insert_notification(
                kind="contact_review", subject=self.jane, text="Jane Doe needs a look", at=T0
            )
        )
        # Bob's request and entries come first, so the file must leave them out by more than
        # time; then Jane's entries: every key with the firm's domain, and her old address
        world.clock.set(T0 + timedelta(hours=1))
        with world.units.open():
            world.compliance.export_on_request(self.bob, BOB_REF)
        world.suppress(self.bob, SuppressionSource.IMPORT, SuppressionScope.PERSON, BOB_CAUSE)
        world.clock.set(T0 + timedelta(hours=2))
        if jane_entries:
            world.suppress(
                self.jane, SuppressionSource.OPERATOR, SuppressionScope.FIRM, JANE_CAUSE
            )
        with world.units.open():
            world.compliance.add_value(OLD_ADDRESS)


# The full export --------------------------------------------------------------------------------


def test_a_full_export_fills_every_section_and_completes_its_request(world: World) -> None:
    seeded = Seeded(world)

    found = export(world, seeded.jane)

    assert found.filename == f"contact-{seeded.jane}-export.json"
    file = document(found)
    assert list(file) == [
        "schema_version",
        "generated_at",
        "contact_id",
        "research",
        "compliance",
        "notify",
        "event_log",
    ]
    assert (file["schema_version"], file["generated_at"], file["contact_id"]) == (
        1,
        NOW_STAMP,
        seeded.jane,
    )
    for owner, sections in SECTIONS.items():
        assert list(file[owner]) == list(sections)
        for section in sections:
            assert file[owner][section], f"{owner}.{section} is empty"
    research = file["research"]
    [contact] = research["contact"]
    assert contact["id"] == seeded.jane
    assert (contact["name"], contact["role"], contact["email"]) == ("Jane Doe", "Partner", EMAIL)
    assert contact["linkedin_url"] == LINKEDIN
    assert contact["address_class"] == "guessed"
    records = {(row["subject"], row["address"]): row for row in research["provenance_records"]}
    assert set(records) == {("contact", None), ("address", EMAIL), ("address", OLD_ADDRESS)}
    contact_record = records[("contact", None)]
    assert (
        contact_record["basis_rule"],
        contact_record["basis_campaign_id"],
        contact_record["lia_ref"],
        contact_record["lia_date"],
        contact_record["basis_recorded_at"],
    ) == ("uk_lia", CAMPAIGN, "LIA-2026-01", "2026-11-01", "2026-11-18T09:00:00.000000Z")
    assert records[("address", EMAIL)]["address_class"] == "guessed"
    assert [(entry["address"], entry["current"]) for entry in research["addresses"]] == [
        (OLD_ADDRESS, False),
        (EMAIL, True),
    ]
    assert research["addresses"][0]["history"] == [
        {
            "address_class": "guessed",
            "source_kind": "website",
            "retrieved_at": "2026-10-19T09:00:00.000000Z",
        }
    ]
    [evidence] = research["evidence"]
    assert (evidence["id"], evidence["quote"]) == (seeded.jane_evidence, JANE_QUOTE)
    assert evidence["pages_checked"] == ["https://firm.co.uk/team", "https://firm.co.uk/news"]
    assert [(row["id"], row["evidence_ids"]) for row in research["signals"]] == sorted(
        [
            (seeded.jane_signal, [seeded.jane_evidence]),
            (seeded.shared_signal, [seeded.jane_evidence]),  # never Bob's Evidence id
        ]
    )
    [page] = research["pages"]
    assert page["id"] == seeded.team_page
    assert "text" not in page
    assert page["mentions"] == ["name", "email"]
    assert page["text_excerpts"] == [
        "Jane Doe is a partner in the property team.",
        "Write to jane.doe@firm.co.uk about leases.",
    ]
    [contact_method] = research["contact_methods"]
    assert contact_method["page_url"] == "https://firm.co.uk/team"
    assert (contact_method["method"], contact_method["matched"]) == ("form", f"mailto:{EMAIL}")
    assert contact_method["mentions"] == ["email"]
    suppression = file["compliance"]["suppression"]
    assert [set(row) for row in suppression] == [SUPPRESSION_KEYS] * 5
    # written in one instant, so ordered by their random ids: compare them sorted
    assert sorted((row["kind"], row["source"], row["status"]) for row in suppression) == [
        ("address", "operator", "active"),
        ("address", "operator", "active"),
        ("domain", "operator", "active"),
        ("person", "operator", "active"),
        ("profile", "operator", "active"),
    ]
    assert {row["created_at"] for row in suppression} == {"2026-11-18T11:00:00.000000Z"}
    [request] = file["compliance"]["requests"]
    assert list(request) == list(REQUEST_COLUMNS)
    assert (request["contact_id"], request["kind"], request["status"]) == (
        seeded.jane,
        "export",
        "completed",
    )
    assert (request["request_ref"], request["opened_at"], request["completed_at"]) == (
        JANE_REF,
        NOW_STAMP,
        NOW_STAMP,
    )
    assert request["receipt_id"] is None
    [alert] = file["notify"]["alerts"]
    assert (alert["subject"], alert["text"]) == (seeded.jane, "Review Jane Doe")
    [notice] = file["notify"]["notifications"]
    assert notice["text"] == "Jane Doe needs a look"
    history = file["event_log"]["contact_history"]
    assert [(row["entity"], row["entity_id"], row["to_status"]) for row in history] == [
        ("contacts", seeded.jane, "guessed")
    ]
    # the stored request and its two events rows
    [_, stored] = request_rows(world)
    assert stored == {
        "id": request["id"],
        "contact_id": seeded.jane,
        "kind": "export",
        "status": "completed",
        "request_ref": JANE_REF,
        "opened_at": NOW_STAMP,
        "completed_at": NOW_STAMP,
        "receipt_id": None,
    }
    assert request_events(world)[-2:] == [
        (request["id"], None, "open", "opened", None, "operator", NOW_STAMP),
        (request["id"], "open", "completed", "completed", None, "operator", NOW_STAMP),
    ]


def test_no_other_contacts_data_or_any_hash_id_or_cause_is_in_the_file(world: World) -> None:
    seeded = Seeded(world)

    text = export(world, seeded.jane).body.decode("utf-8")

    for value in (
        "Bob",
        "bob.smith",
        BOB_QUOTE,
        BOB_REF,
        seeded.bob,
        seeded.bob_evidence,
        seeded.bob_signal,
        seeded.news_page,
        "litigation",  # Bob's line on the shared page
        "Opening hours",  # the firm's own line
        "calendly",
        JANE_CAUSE,
        BOB_CAUSE,
    ):
        assert value not in text, value
    assert TEAM_TEXT not in json.loads(text)["research"]["pages"][0].values()
    hashes = [row[0] for row in world.sql("SELECT value_hash FROM suppression_entries")]
    entry_ids = [row[0] for row in world.sql("SELECT id FROM suppression_entries")]
    assert len(hashes) == 7  # Bob's address and person, Jane's four, her old address
    for value in (*hashes, *entry_ids):
        assert value not in text


def test_bobs_own_export_holds_bobs_data_and_not_janes(world: World) -> None:
    seeded = Seeded(world)

    file = document(export(world, seeded.bob, "Second letter"))

    text = json.dumps(file)
    for value in ("Jane", "jane.doe", JANE_QUOTE, JANE_REF, seeded.jane_signal, LINKEDIN):
        assert value not in text, value
    pages = file["research"]["pages"]
    assert [page["url"] for page in pages] == ["https://firm.co.uk/news", "https://firm.co.uk/team"]
    assert [page["id"] for page in pages] == [seeded.news_page, seeded.team_page]
    assert pages[0]["text_excerpts"] == ["Bob Smith joined the firm."]
    assert pages[1]["text_excerpts"] == ["Bob Smith heads the litigation team."]
    assert [row["matched"] for row in file["research"]["contact_methods"]] == [
        f"mailto:{BOB_EMAIL}"
    ]
    # Bob's two entries, then the firm's domain entry (it blocks Bob too)
    suppression = [(row["kind"], row["source"]) for row in file["compliance"]["suppression"]]
    assert sorted(suppression[:2]) == [("address", "import"), ("person", "import")]
    assert suppression[2:] == [("domain", "operator")]
    assert [row["request_ref"] for row in file["compliance"]["requests"]] == [
        BOB_REF,
        "Second letter",
    ]
    assert [row["text"] for row in file["notify"]["alerts"]] == ["Review Bob Smith"]
    assert file["notify"]["notifications"] == []


def test_a_page_naming_only_an_earlier_provenance_address_is_exported(world: World) -> None:
    lead = world.lead()
    jane = world.contact(lead, name="Jane Doe", email=EMAIL)
    address_record(world, jane, OLD_ADDRESS, T0 - timedelta(days=30))
    run_id = world.transact(
        lambda: crawl_repo.insert_run(
            lead_id=lead,
            site_domain="firm.co.uk",
            final_domain=None,
            started_at=T0,
            ended_at=T0,
            outcome="ok",
            error_code=None,
            pages_fetched=1,
            pages_skipped_robots=0,
            renders=0,
            errors=0,
        )
    )
    page = insert_page(
        world, lead, run_id, TEAM_URL, "Leases\nWrite to jane@oldfirm.co.uk today\nOpening hours"
    )
    world.transact(
        lambda: crawl_repo.add_contact_methods(
            page, [method(ContactMethod.FORM, "f" * 32, f"mailto:{OLD_ADDRESS}")], T0
        )
    )

    research = document(export(world, jane))["research"]

    [found] = research["pages"]
    assert (found["id"], found["mentions"]) == (page, ["email"])
    assert found["text_excerpts"] == ["Write to jane@oldfirm.co.uk today"]
    [contact_method] = research["contact_methods"]
    assert (contact_method["matched"], contact_method["mentions"]) == (
        f"mailto:{OLD_ADDRESS}",
        ["email"],
    )


# Voided entries ---------------------------------------------------------------------------------


def reverse(world: World, entry_id: str, reason: str, at: datetime) -> None:
    world.clock.set(at)
    with world.units.open():
        world.compliance.reverse_entry(entry_id, reason)


def test_a_voided_entry_is_exported_with_its_time_and_reason(world: World) -> None:
    lead = world.lead()
    jane = world.contact(lead, name="Jane Doe", email=EMAIL)
    world.clock.set(T0 + timedelta(hours=1))
    address, person = world.suppress(jane, SuppressionSource.REPLY_RULE).written
    reverse(world, address, "The rule misread a yes", T0 + timedelta(hours=5))

    suppression = document(export(world, jane))["compliance"]["suppression"]

    by_kind = {row["kind"]: row for row in suppression}
    assert set(by_kind) == {"address", "person"}
    assert by_kind["address"] == {
        "kind": "address",
        "status": "voided",
        "source": "reply_rule",
        "created_at": "2026-11-18T10:00:00.000000Z",
        "voided_at": "2026-11-18T14:00:00.000000Z",
        "void_reason": "The rule misread a yes",
    }
    assert (by_kind["person"]["status"], by_kind["person"]["void_reason"]) == ("active", None)
    assert person


def test_a_firm_domain_entrys_reason_is_left_out_but_a_persons_own_is_kept(
    world: World,
) -> None:
    lead = world.lead()
    jane = world.contact(lead, name="Jane Doe", email=EMAIL)
    world.clock.set(T0 + timedelta(hours=1))
    written = world.suppress(jane, SuppressionSource.REPLY_RULE, SuppressionScope.FIRM).written
    address, _, domain = written  # the address, person and domain keys
    reverse(world, domain, "Bob Smith asked to keep the firm", T0 + timedelta(hours=2))
    reverse(world, address, "Jane's reply was misread", T0 + timedelta(hours=3))

    found = export(world, jane)

    by_kind = {row["kind"]: row for row in document(found)["compliance"]["suppression"]}
    assert by_kind["domain"]["status"] == "voided"
    assert by_kind["domain"]["voided_at"] == "2026-11-18T11:00:00.000000Z"
    assert "void_reason" in by_kind["domain"] and by_kind["domain"]["void_reason"] is None
    assert by_kind["address"]["void_reason"] == "Jane's reply was misread"
    assert "Bob Smith" not in found.body.decode("utf-8")


# Superseded and retracted Evidence and Signals --------------------------------------------------


def test_superseded_and_retracted_evidence_and_retracted_signals_are_exported(
    world: World,
) -> None:
    lead = world.lead()
    jane = world.contact(lead, name="Jane Doe", email=EMAIL)
    old = person_evidence(world, lead, jane, "Jane Doe is an associate")
    new = person_evidence(world, lead, jane, "Jane Doe is a partner")
    gone = person_evidence(world, lead, jane, "Jane Doe spoke at the conference")
    live = signal(world, lead, "law_new_partner", new)
    retracted = signal(world, lead, "law_speaking", gone)
    retracted_at = T0 + timedelta(days=1)
    world.transact(lambda: signals_repo.supersede_evidence(old, new))
    world.transact(lambda: signals_repo.retract_evidence(gone, retracted_at))
    assert world.transact(lambda: signals_repo.retract_unsupported(lead, retracted_at)) == (
        retracted,
    )

    research = document(export(world, jane))["research"]

    evidence = {row["id"]: row for row in research["evidence"]}
    assert set(evidence) == {old, new, gone}
    assert (evidence[old]["superseded_by"], evidence[old]["retracted_at"]) == (new, None)
    assert (evidence[new]["superseded_by"], evidence[new]["retracted_at"]) == (None, None)
    assert (evidence[gone]["superseded_by"], evidence[gone]["retracted_at"]) == (
        None,
        "2026-11-19T09:00:00.000000Z",
    )
    signals = {row["id"]: row for row in research["signals"]}
    assert set(signals) == {live, retracted}
    assert signals[live]["retracted_at"] is None
    assert signals[retracted]["retracted_at"] == "2026-11-19T09:00:00.000000Z"
    assert signals[retracted]["evidence_ids"] == [gone]


# A bare Contact ---------------------------------------------------------------------------------


def test_a_bare_contact_has_every_section_with_empty_arrays(world: World) -> None:
    lead = world.lead()
    contact = world.contact(lead, name="Ann Lee")

    file = document(export(world, contact))

    research = file["research"]
    assert [row["id"] for row in research["contact"]] == [contact]
    assert len(research["provenance_records"]) == 1
    for section in ("addresses", "evidence", "signals", "pages", "contact_methods"):
        assert research[section] == [], section
    assert file["compliance"]["suppression"] == []
    assert [row["status"] for row in file["compliance"]["requests"]] == ["completed"]
    assert file["notify"] == {"alerts": [], "notifications": []}
    assert file["event_log"] == {"contact_history": []}


# An erased Contact (simulated until Story 3.13) -------------------------------------------------


def test_an_erased_contact_exports_only_tombstones_and_its_suppression_facts(
    world: World,
) -> None:
    seeded = Seeded(world, jane_entries=False)
    world.clock.set(T0 + timedelta(hours=3))
    erase_request = world.transact(
        lambda: world.compliance.open_dsr(seeded.jane, "erase", "Erasure email")
    )
    world.suppress(seeded.jane, SuppressionSource.DSR, SuppressionScope.PERSON, erase_request)
    hashed = key_hash("address", EMAIL)
    old_hashed = key_hash("address", OLD_ADDRESS)
    world.sql(
        "UPDATE contacts SET name = ?, role = ?, email = ?, linkedin_url = NULL WHERE id = ?",
        (TOMBSTONE, TOMBSTONE, hashed, seeded.jane),
    )
    world.sql(
        "UPDATE provenance_records SET source_detail = ?, notes = NULL, address = CASE address "
        "WHEN ? THEN ? WHEN ? THEN ? ELSE address END WHERE contact_id = ?",
        (TOMBSTONE, EMAIL, hashed, OLD_ADDRESS, old_hashed, seeded.jane),
    )
    # Story 3.13's erasure will redact a quote; until then, lift the immutability trigger on
    # this temporary database to simulate it
    world.sql("DROP TRIGGER trg_evidence_immutable")
    world.sql(
        "UPDATE evidence SET quote = ? WHERE person_contact_id = ?", (TOMBSTONE, seeded.jane)
    )
    world.sql("UPDATE crawled_pages SET text = NULL")
    for table in ("alerts", "notifications"):
        world.sql(f"UPDATE {table} SET text = ? WHERE subject = ?", (TOMBSTONE, seeded.jane))

    file = document(export(world, seeded.jane, "Second request"))

    research = file["research"]
    [contact] = research["contact"]
    assert (contact["name"], contact["role"], contact["email"], contact["linkedin_url"]) == (
        TOMBSTONE,
        TOMBSTONE,
        hashed,
        None,
    )
    assert {record["source_detail"] for record in research["provenance_records"]} == {TOMBSTONE}
    assert [row["quote"] for row in research["evidence"]] == [TOMBSTONE]
    assert research["pages"] == [] and research["contact_methods"] == []
    assert [row["text"] for row in file["notify"]["alerts"]] == [TOMBSTONE]
    text = json.dumps(file)
    for plain in ("Jane", "jane.doe", "oldfirm", "linkedin.com", "Partner"):
        assert plain not in text, plain
    # the old address's entry and the DSR's address entry found by their hashes, and the DSR's
    # person and profile entries, whose plain values are gone, found by the erase request
    suppression = [(row["kind"], row["source"]) for row in file["compliance"]["suppression"]]
    assert suppression[0] == ("address", "operator")
    assert sorted(suppression[1:]) == [("address", "dsr"), ("person", "dsr"), ("profile", "dsr")]
    assert all(set(row) == SUPPRESSION_KEYS for row in file["compliance"]["suppression"])
    assert [(row["kind"], row["status"]) for row in file["compliance"]["requests"]] == [
        ("erase", "open"),
        ("export", "completed"),
    ]


def test_an_entry_found_only_by_a_request_is_exported(world: World) -> None:
    lead = world.lead()
    contact = world.contact(lead, name="Ann Lee")
    request = world.transact(lambda: world.compliance.open_dsr(contact, "erase", "Erasure"))
    world.suppress(contact, SuppressionSource.DSR, SuppressionScope.PERSON, request)
    world.sql("UPDATE contacts SET name = ? WHERE id = ?", (TOMBSTONE, contact))

    file = document(export(world, contact))

    assert [(row["kind"], row["source"]) for row in file["compliance"]["suppression"]] == [
        ("person", "dsr")
    ]


# Refusals ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reference", "message"),
    [
        ("", REF_REQUIRED),
        ("   \t ", REF_REQUIRED),
        ("x" * 201, REF_INVALID),
        ("ticket\x0742", REF_INVALID),
        ("ticket\n42", REF_INVALID),
    ],
)
def test_a_bad_reference_is_refused_and_writes_nothing(
    world: World, reference: str, message: str
) -> None:
    seeded_lead = world.lead()
    contact = world.contact(seeded_lead, name="Ann Lee")
    before = world.dump()

    with pytest.raises(RequestRefused) as caught:
        export(world, contact, reference)

    assert caught.value.message == message
    nothing_written(world, before)


@pytest.mark.parametrize("contact_id", ["nope", "01JD3QZ8X5V9K2M7N4P6R8T0ZZ", ""])
def test_an_unknown_contact_is_refused_without_its_id_and_writes_nothing(
    world: World, contact_id: str
) -> None:
    world.contact(world.lead(), name="Ann Lee")
    before = world.dump()

    with pytest.raises(LookupError) as caught:
        export(world, contact_id)

    if contact_id:
        assert contact_id not in str(caught.value)
    nothing_written(world, before)


def test_a_failure_while_building_the_file_rolls_the_request_back(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    contact = world.contact(world.lead(), name="Ann Lee")
    before = world.dump()

    def broken(document: object) -> bytes:
        raise RuntimeError("render failed")

    monkeypatch.setattr(compliance_api, "render", broken)

    with pytest.raises(RuntimeError, match="render failed"):
        export(world, contact)

    nothing_written(world, before)


def test_a_failure_completing_the_request_rolls_its_opening_back(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    contact = world.contact(world.lead(), name="Ann Lee")
    before = world.dump()
    monkeypatch.setattr(
        compliance_api.requests_repo, "complete", lambda request_id, completed_at: False
    )

    with pytest.raises(IllegalTransition):
        export(world, contact)

    nothing_written(world, before)


def test_nothing_is_logged(world: World, caplog: pytest.LogCaptureFixture) -> None:
    seeded = Seeded(world)
    caplog.clear()
    caplog.set_level(logging.DEBUG)

    export(world, seeded.jane)
    with pytest.raises(RequestRefused):
        export(world, seeded.jane, "")
    with pytest.raises(LookupError):
        export(world, "01JD3QZ8X5V9K2M7N4P6R8T0ZZ")

    assert [r for r in caplog.records if r.name.startswith("nextron.app")] == []
    logged = " ".join(record.getMessage() for record in caplog.records)
    for value in ("Jane", "jane.doe", JANE_REF, f"contact-{seeded.jane}"):
        assert value not in logged


# The commands and the readers -------------------------------------------------------------------


def test_open_dsr_checks_its_arguments_in_order(world: World) -> None:
    contact = world.contact(world.lead(), name="Ann Lee")
    before = world.dump()
    compliance = world.compliance

    cases: list[tuple[tuple[Any, ...], type[Exception]]] = [
        ((contact, 1, "ref"), TypeError),
        ((contact, "delete", "ref"), ValueError),
        ((1, "export", "ref"), TypeError),
        ((contact, "export", None), TypeError),
        ((contact, "export", " "), RequestRefused),
        (("nope", "export", "ref"), LookupError),
        (("01JD3QZ8X5V9K2M7N4P6R8T0ZZ", "export", "ref"), LookupError),
    ]
    for arguments, error in cases:
        with pytest.raises(error):
            world.transact(lambda arguments=arguments: compliance.open_dsr(*arguments))
    nothing_written(world, before)


def test_open_dsr_takes_either_kind_and_complete_dsr_completes_once(world: World) -> None:
    contact = world.contact(world.lead(), name="Ann Lee")
    world.clock.set(NOW)

    erase = world.transact(lambda: world.compliance.open_dsr(contact, "erase", "  By phone  "))
    exported = world.transact(lambda: world.compliance.open_dsr(contact, "export", "By email"))
    world.transact(lambda: world.compliance.complete_dsr(exported))

    assert [(row["kind"], row["status"], row["request_ref"]) for row in request_rows(world)] == [
        ("erase", "open", "By phone"),
        ("export", "completed", "By email"),
    ]
    with pytest.raises(IllegalTransition):
        world.transact(lambda: world.compliance.complete_dsr(exported))
    for bad, error in ((1, TypeError), ("nope", LookupError), (contact, LookupError)):
        with pytest.raises(error):
            world.transact(lambda bad=bad: world.compliance.complete_dsr(bad))
    assert request_events(world) == [
        (erase, None, "open", "opened", None, "operator", NOW_STAMP),
        (exported, None, "open", "opened", None, "operator", NOW_STAMP),
        (exported, "open", "completed", "completed", None, "operator", NOW_STAMP),
    ]


def test_export_contact_runs_in_a_read_only_unit_and_writes_nothing(world: World) -> None:
    seeded = Seeded(world)
    before = world.dump()
    requests_before = request_rows(world)

    found = world.read(lambda: world.compliance.export_contact(seeded.jane))

    assert document(found)["compliance"]["requests"] == []  # Jane has none yet
    assert world.dump() == before and request_rows(world) == requests_before
    with pytest.raises(LookupError):
        world.read(lambda: world.compliance.export_contact("nope"))


def test_the_readers_return_every_column(world: World) -> None:
    seeded = Seeded(world)

    research = world.read(lambda: research_queries.export_pii(seeded.jane))
    compliance = world.read(lambda: compliance_queries.export_pii(seeded.bob, ["not a hash", 3]))
    notify = world.read(lambda: notify_queries.export_pii(seeded.jane))
    history = world.read(lambda: eventlog.history("contacts", seeded.jane))

    assert set(research) == set(SECTIONS["research"])
    [contact] = research["contact"]
    assert set(contact) == {
        "id", "lead_id", "name", "role", "email", "address_class", "linkedin_url", "is_primary",
        "tz", "market_id", "market_basis", "tz_basis", "office_country", "office_region",
        "created_at", "updated_at",
    }  # fmt: skip
    assert contact["created_at"] == T0 and contact["is_primary"] is False
    assert compliance["suppression"] == ()  # no valid hash given, and Bob's request caused none
    assert [row["request_ref"] for row in compliance["requests"]] == [BOB_REF]
    assert set(notify) == {"alerts", "notifications"}
    assert [row["entity_id"] for row in history] == [seeded.jane]
    with pytest.raises(LookupError):
        world.read(lambda: research_queries.export_pii("nope"))
    assert world.read(lambda: compliance_queries.export_pii("nope", [])) == {
        "suppression": (),
        "requests": (),
    }


# The table --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("values", "constraint"),
    [
        ("'delete', 'open', 'ref', NULL", "ck_data_subject_requests_kind"),
        ("'export', 'closed', 'ref', NULL", "ck_data_subject_requests_status"),
        ("'export', 'open', '', NULL", "ck_data_subject_requests_request_ref"),
        (f"'export', 'open', '{'x' * 201}', NULL", "ck_data_subject_requests_request_ref"),
        ("'export', 'completed', 'ref', NULL", "ck_data_subject_requests_completed"),
        (f"'export', 'open', 'ref', '{NOW_STAMP}'", "ck_data_subject_requests_completed"),
    ],
)
def test_the_checks_refuse_a_bad_row(world: World, values: str, constraint: str) -> None:
    contact = world.contact(world.lead(), name="Ann Lee")

    with pytest.raises(sqlite3.IntegrityError, match=constraint):
        run_sql(
            world.db_file,
            "INSERT INTO data_subject_requests (id, contact_id, kind, status, request_ref, "
            f"completed_at, opened_at) VALUES ('{new_id()}', '{contact}', {values}, "
            f"'{NOW_STAMP}')",
        )

    assert request_rows(world) == []


def test_the_contact_key_has_no_cascade(world: World) -> None:
    [(sql,)] = world.sql(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'data_subject_requests'"
    )
    assert "fk_data_subject_requests_contact_id_contacts" in sql
    assert "CASCADE" not in sql.upper()
    [(index,)] = world.sql(
        "SELECT name FROM sqlite_master WHERE type = 'index' "
        "AND tbl_name = 'data_subject_requests' AND name NOT LIKE 'sqlite_%'"
    )
    assert index == "ix_data_subject_requests_contact_id"


def test_the_stored_times_are_utc_text(world: World) -> None:
    contact = world.contact(world.lead(), name="Ann Lee")

    export(world, contact)

    [row] = request_rows(world)
    assert row["opened_at"] == row["completed_at"] == NOW_STAMP
    assert datetime.fromisoformat(NOW_STAMP[:-1]).replace(tzinfo=UTC) == NOW
