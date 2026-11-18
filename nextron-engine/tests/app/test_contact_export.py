"""The subject access export (``Compliance.export_contact``; Story 3.13's ``Erasures.open_dsr``,
``complete_dsr`` and ``export_on_request``; the exporters ``queries.research``,
``queries.compliance`` and ``queries.notify`` ``export_pii``; ``eventlog.history``; FR-52, AD-3,
AD-4, AD-17, Story 3.15) with the real subscriber registry on a temporary database at head, with
``FrozenClock``: every row of the spec's I/O matrix on real units of work: a full export (every
section filled, the new request ``completed`` with its two ``events`` rows), a bare Contact
(every section present, ``[]`` when empty), another Contact's rows, entries, Signals' links,
page lines and request kept out, a Contact erased by Story 3.13's real erasure (tombstones plus
suppression facts found by hash or by its request), refused references and unknown Contacts
writing nothing, a failure rolling the request back, the read-only unit, the public completer,
and no log line."""

import json
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.compliance import api as compliance_api
from nextron.app.compliance import erasure as erasure_module
from nextron.app.compliance.api import Compliance
from nextron.app.compliance.erasure import Erasures
from nextron.app.discovery.api import Discovery
from nextron.app.pipeline.api import Pipeline
from nextron.app.playbook.api import Playbook
from nextron.app.queries import compliance as compliance_queries
from nextron.app.queries import notify as notify_queries
from nextron.app.queries import playbook as playbook_queries
from nextron.app.queries import research as research_queries
from nextron.app.research.api import Research
from nextron.app.setup.api import Setup
from nextron.db import eventlog
from nextron.db.models.base import new_id
from nextron.db.repos.notify import alerts as alerts_repo
from nextron.db.repos.notify import notifications as notifications_repo
from nextron.db.repos.research import crawl as crawl_repo
from nextron.db.repos.research import postings as postings_repo
from nextron.db.repos.research import signals as signals_repo
from nextron.db.uow import UnitOfWorkFactory
from nextron.domain import seed_yaml
from nextron.domain.contact_export import SECTIONS, ContactExport
from nextron.domain.contactability import ContactSubject, SuppressionScope, key_hash
from nextron.domain.erasure import REDACTED
from nextron.domain.machines import IllegalTransition
from nextron.domain.segments import validate_segments
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
BOB_REF = "Letter of 17 Nov 2026"  # Story 3.13 refuses a reference naming the person
ERASE_REF = "Erasure email"
# check_request_ref's messages (Story 3.13)
REF_FORM = "must be 1 to 200 characters on one line"
REF_ADDRESS = "must not hold an email address"
REF_NAME = "must not hold the person's name"
# alerts and notices carry no Contact's data (Story 3.13's notify ERASURE: not_held)
JANE_ALERT = "Contact needs review"
BOB_ALERT = "Second Contact needs review"
JANE_NOTICE = "A Contact needs a look"
# Story 4.6: an Operator's Reply class override made from Jane's Reply, pseudonymised
# Story 2.11: a posting naming Jane, and the firm's own
JANE_POSTING = "Legal assistant to Jane Doe"
FIRM_POSTING = "Paralegal, property team"
JANE_REPLY = "Hi, Jane Doe here: what would the audit cost for our team?"
JANE_OVERRIDE = "Hi, [name] here: what would the audit cost for our team?"
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


def erasures(world: World) -> Erasures:
    """Story 3.13's ``Erasures`` on the world's clock and ``Compliance``."""
    return Erasures(world.clock, world.compliance)


def export(world: World, contact_id: str, reference: str = JANE_REF) -> ContactExport:
    world.clock.set(NOW)
    with world.units.open():
        return erasures(world).export_on_request(contact_id, reference)


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
                text=JANE_ALERT,
                status="sent",
                at=T0,
            )
        )
        world.transact(
            lambda: alerts_repo.insert_alert(
                kind="contact_review",
                subject=self.bob,
                channel="telegram",
                text=BOB_ALERT,
                status="sent",
                at=T0,
            )
        )
        world.transact(
            lambda: notifications_repo.insert_notification(
                kind="contact_review", subject=self.jane, text=JANE_NOTICE, at=T0
            )
        )
        # Story 2.11: a posting naming Jane and the firm's own posting
        for title in (JANE_POSTING, FIRM_POSTING):
            world.transact(
                lambda title=title: postings_repo.insert_posting(
                    lead_id=self.lead,
                    source="careers_page",
                    board_token=None,
                    url="https://firm.co.uk/careers",
                    title=title,
                    description=None,
                    posted_date=None,
                    checked_at=T0,
                )
            )
        # an override case made from Jane's Reply (Story 4.6)
        self.override = world.transact(
            lambda: Playbook(world.clock).add_override_case(
                JANE_REPLY, "question", contact_id=self.jane, names=("Jane Doe",)
            )
        )
        # Bob's request and entries come first, so the file must leave them out by more than
        # time; then Jane's entries: every key with the firm's domain, and her old address
        world.clock.set(T0 + timedelta(hours=1))
        with world.units.open():
            erasures(world).export_on_request(self.bob, BOB_REF)
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
        "playbook",
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
    [posting] = research["job_postings"]  # never the firm's own posting
    assert (posting["title"], posting["mentions"]) == (JANE_POSTING, ["name"])
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
    assert (alert["subject"], alert["text"]) == (seeded.jane, JANE_ALERT)
    [notice] = file["notify"]["notifications"]
    assert notice["text"] == JANE_NOTICE
    [override] = file["playbook"]["override_cases"]
    assert (override["case_key"], override["origin"], override["contact_id"]) == (
        seeded.override,
        "override",
        seeded.jane,
    )
    assert json.loads(override["input"])["blocks"][0]["text"] == JANE_OVERRIDE
    assert json.loads(override["expected"]) == {"reply_class": "question"}
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
        (request["id"], None, "open", "opened", seeded.jane, "operator", NOW_STAMP),
        (request["id"], "open", "completed", "completed", seeded.jane, "operator", NOW_STAMP),
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
    assert [row["text"] for row in file["notify"]["alerts"]] == [BOB_ALERT]
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
    sections = ("addresses", "evidence", "signals", "pages", "contact_methods", "job_postings")
    for section in sections:
        assert research[section] == [], section
    assert file["compliance"]["suppression"] == []
    assert [row["status"] for row in file["compliance"]["requests"]] == ["completed"]
    assert file["notify"] == {"alerts": [], "notifications": []}
    assert file["playbook"] == {"override_cases": []}
    assert file["event_log"] == {"contact_history": []}


# An erased Contact (Story 3.13's real erasure) -------------------------------------------------


def test_an_erased_contact_exports_only_tombstones_and_its_suppression_facts(
    world: World,
) -> None:
    seeded = Seeded(world, jane_entries=False)
    world.clock.set(T0 + timedelta(hours=3))
    with world.units.open():
        erased = erasures(world).erase_on_request(seeded.jane, ERASE_REF)
    assert erased.erased
    hashed = key_hash("address", EMAIL)
    old_hashed = key_hash("address", OLD_ADDRESS)

    file = document(export(world, seeded.jane, "Second request"))

    research = file["research"]
    [contact] = research["contact"]
    assert (contact["name"], contact["role"], contact["email"], contact["linkedin_url"]) == (
        REDACTED,
        REDACTED,
        hashed,
        None,
    )
    assert contact["erased_at"] == "2026-11-18T12:00:00.000000Z"
    assert {record["source_detail"] for record in research["provenance_records"]} == {REDACTED}
    assert {record["address"] for record in research["provenance_records"]} == {
        None,
        hashed,
        old_hashed,
    }
    assert [row["quote"] for row in research["evidence"]] == [REDACTED]
    assert research["pages"] == [] and research["contact_methods"] == []
    assert research["job_postings"] == []  # Story 2.11: tombstoned, so nothing mentions her
    # Story 4.6: playbook's hook erased and retired her override case
    [override] = file["playbook"]["override_cases"]
    assert (override["input"], override["retired_at"]) == (
        REDACTED,
        "2026-11-18T12:00:00.000000Z",
    )
    # alerts and notices carry no Contact's data, so the erasure leaves them (not_held)
    assert [row["text"] for row in file["notify"]["alerts"]] == [JANE_ALERT]
    text = json.dumps(file)
    for plain in ("Jane", "jane.doe", "oldfirm", "linkedin.com", "Partner"):
        assert plain not in text, plain
    # the old address's entry and the DSR's address entry found by their hashes, and the DSR's
    # person and profile entries, whose plain values are gone, found by the erase request
    suppression = [(row["kind"], row["source"]) for row in file["compliance"]["suppression"]]
    assert suppression[0] == ("address", "operator")
    assert sorted(suppression[1:]) == [("address", "dsr"), ("person", "dsr"), ("profile", "dsr")]
    assert all(set(row) == SUPPRESSION_KEYS for row in file["compliance"]["suppression"])
    assert [
        (row["kind"], row["status"], row["request_ref"]) for row in file["compliance"]["requests"]
    ] == [("erase", "completed", ERASE_REF), ("export", "completed", "Second request")]


def test_an_entry_found_only_by_a_request_is_exported(world: World) -> None:
    lead = world.lead()
    contact = world.contact(lead, name="Ann Lee")
    request = world.transact(lambda: erasures(world).open_dsr(contact, "erase", "Erasure"))
    world.suppress(contact, SuppressionSource.DSR, SuppressionScope.PERSON, request)
    world.sql("UPDATE contacts SET name = ? WHERE id = ?", (REDACTED, contact))

    file = document(export(world, contact))

    assert [(row["kind"], row["source"]) for row in file["compliance"]["suppression"]] == [
        ("person", "dsr")
    ]


# Refusals ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reference", "message"),
    [
        ("", REF_FORM),
        ("   \t ", REF_FORM),
        (" Email of 18 Nov 2026", REF_FORM),
        ("x" * 201, REF_FORM),
        ("ticket\x0742", REF_FORM),
        ("ticket\n42", REF_FORM),
        ("Reply from ann.lee@firm.co.uk", REF_ADDRESS),
        ("Letter from Ann Lee", REF_NAME),
    ],
)
def test_a_bad_reference_is_refused_and_writes_nothing(
    world: World, reference: str, message: str
) -> None:
    seeded_lead = world.lead()
    contact = world.contact(seeded_lead, name="Ann Lee")
    before = world.dump()

    with pytest.raises(ValueError, match=message) as caught:
        export(world, contact, reference)

    if reference.strip():
        assert reference.strip() not in str(caught.value)
    nothing_written(world, before)


@pytest.mark.parametrize(
    ("contact_id", "error"),
    [("nope", ValueError), ("01JD3QZ8X5V9K2M7N4P6R8T0ZZ", LookupError), ("", ValueError)],
)
def test_an_unknown_contact_is_refused_without_its_id_and_writes_nothing(
    world: World, contact_id: str, error: type[Exception]
) -> None:
    world.contact(world.lead(), name="Ann Lee")
    before = world.dump()

    with pytest.raises(error) as caught:
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
    def broken(request_id: str, completed_at: datetime) -> None:
        raise RuntimeError("complete failed")

    monkeypatch.setattr(erasure_module.requests_repo, "complete", broken)

    with pytest.raises(RuntimeError, match="complete failed"):
        export(world, contact)

    nothing_written(world, before)


def test_nothing_is_logged(world: World, caplog: pytest.LogCaptureFixture) -> None:
    seeded = Seeded(world)
    caplog.clear()
    caplog.set_level(logging.DEBUG)

    export(world, seeded.jane)
    with pytest.raises(ValueError):
        export(world, seeded.jane, "")
    with pytest.raises(LookupError):
        export(world, "01JD3QZ8X5V9K2M7N4P6R8T0ZZ")

    assert [r for r in caplog.records if r.name.startswith("nextron.app")] == []
    logged = " ".join(record.getMessage() for record in caplog.records)
    for value in ("Jane", "jane.doe", JANE_REF, f"contact-{seeded.jane}"):
        assert value not in logged


# The commands and the readers -------------------------------------------------------------------


def test_complete_dsr_completes_an_open_request_once(world: World) -> None:
    contact = world.contact(world.lead(), name="Ann Lee")
    world.clock.set(NOW)

    erase = world.transact(lambda: erasures(world).open_dsr(contact, "erase", "By phone"))
    exported = world.transact(lambda: erasures(world).open_dsr(contact, "export", "By email"))
    world.transact(lambda: erasures(world).complete_dsr(exported))

    assert [(row["kind"], row["status"], row["request_ref"]) for row in request_rows(world)] == [
        ("erase", "open", "By phone"),
        ("export", "completed", "By email"),
    ]
    with pytest.raises(IllegalTransition):
        world.transact(lambda: erasures(world).complete_dsr(exported))
    for bad, error in ((1, TypeError), ("nope", LookupError), (contact, LookupError)):
        with pytest.raises(error):
            world.transact(lambda bad=bad: erasures(world).complete_dsr(bad))
    assert request_events(world) == [
        (erase, None, "open", "opened", contact, "operator", NOW_STAMP),
        (exported, None, "open", "opened", contact, "operator", NOW_STAMP),
        (exported, "open", "completed", "completed", contact, "operator", NOW_STAMP),
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
    playbook = world.read(lambda: playbook_queries.export_pii(seeded.jane))
    history = world.read(lambda: eventlog.history("contacts", seeded.jane))

    assert set(research) == set(SECTIONS["research"])
    [contact] = research["contact"]
    assert set(contact) == {
        "id", "lead_id", "name", "role", "email", "address_class", "linkedin_url", "is_primary",
        "tz", "market_id", "market_basis", "tz_basis", "office_country", "office_region",
        "created_at", "updated_at", "primary_set_by", "erased_at",
    }  # fmt: skip
    assert contact["created_at"] == T0 and contact["is_primary"] is False
    assert compliance["suppression"] == ()  # no valid hash given, and Bob's request caused none
    assert [row["request_ref"] for row in compliance["requests"]] == [BOB_REF]
    assert set(notify) == {"alerts", "notifications"}
    [override] = playbook["override_cases"]
    assert set(override) == {
        "id", "case_key", "test_set", "route", "input", "expected", "covers", "sha256",
        "loaded_at", "retired_at", "origin", "contact_id",
    }  # fmt: skip
    assert world.read(lambda: playbook_queries.export_pii(seeded.bob)) == {
        "override_cases": ()
    }
    assert world.read(lambda: playbook_queries.export_pii("nope")) == {"override_cases": ()}
    assert [row["entity_id"] for row in history] == [seeded.jane]
    with pytest.raises(LookupError):
        world.read(lambda: research_queries.export_pii("nope"))
    assert world.read(lambda: compliance_queries.export_pii("nope", [])) == {
        "suppression": (),
        "requests": (),
    }


# The stored request ------------------------------------------------------------------------------


def test_the_stored_times_are_utc_text(world: World) -> None:
    contact = world.contact(world.lead(), name="Ann Lee")

    export(world, contact)

    [row] = request_rows(world)
    assert row["opened_at"] == row["completed_at"] == NOW_STAMP
    assert datetime.fromisoformat(NOW_STAMP[:-1]).replace(tzinfo=UTC) == NOW
