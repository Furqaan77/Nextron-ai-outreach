"""A Lead's complete history (Story 9.8) through ``nextron.app.analytics.history`` and
``queries.analytics.lead_events`` on a temporary database at head, with real services
(``Discovery.upsert_lead``, ``Research.add_contact``, ``Pipeline``, ``Compliance.suppress``,
``Erasures``): the full timeline newest first, the generic foreign-key link (an existing chain
and a throwaway one), an unrelated Lead left out, filters, paging, a merged Lead, an erased
Contact, Signal expiry, an empty Lead and no row changed by a read."""

import dataclasses
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Column, ForeignKey, MetaData, String, Table

from nextron.app.analytics.history import LeadHistoryView, _evidence_entries, lead_history
from nextron.app.compliance.erasure import Erasures
from nextron.app.pipeline.api import OPERATOR
from nextron.app.queries import analytics as analytics_queries
from nextron.app.queries import compliance as compliance_queries
from nextron.app.queries import research as research_queries
from nextron.app.queries import setup as setup_queries
from nextron.db.models.base import new_id, utc_text
from nextron.db.repos.analytics import lineage
from nextron.db.repos.research import signals as signals_repo
from nextron.domain.call_outcomes import CallResult, Outcome, PriceReaction
from nextron.domain.contactability import DomainSubject, SuppressionScope, person_keys
from nextron.domain.contacts import ContactCandidate, ProvenanceSource
from nextron.domain.lead_history import (
    CONTACT_ADDED,
    EVENT,
    EVIDENCE_ADDED,
    EVIDENCE_RETRACTED,
    EVIDENCE_SUPERSEDED,
    PAGE_SIZE,
    SIGNAL_ADDED,
    SIGNAL_EXPIRED,
    SIGNAL_RETRACTED,
    HistoryEntry,
    HistoryType,
)
from nextron.domain.pipeline_stage import PipelineStage
from nextron.domain.suppression_entry import SuppressionSource
from tests.app.test_call_outcomes import book
from tests.app.test_offer_report import every_table
from tests.app.test_pipeline_manual import run
from tests.app.test_suppression import LATER, World, world  # noqa: F401 (the fixture)

S = PipelineStage
NOW = LATER + timedelta(days=1)
HELD = Outcome(
    outcome=CallResult.HELD,
    qualified=True,
    audit_proposed=False,
    price_reaction=PriceReaction.NOT_DISCUSSED,
    objections=(),
)


def history(
    world: World,
    lead_id: str,
    type: HistoryType = HistoryType.ALL,
    page: int = 1,
    now: datetime = NOW,
) -> LeadHistoryView:
    found = world.read(lambda: lead_history(lead_id, type=type, page=page, now=now))
    assert found is not None
    return found


def entries(view: LeadHistoryView) -> list[HistoryEntry]:
    return list(view.page.entries)


def evidence(world: World, lead_id: str, quote: str, at: datetime, **fields: Any) -> str:
    def store() -> str:
        return signals_repo.insert_evidence(
            lead_id=lead_id,
            evidence_type="quote",
            quote=quote,
            sought=None,
            number=None,
            fact=None,
            value=None,
            pages_checked=["https://firm.co.uk/news"],
            source="website",
            source_url=fields.get("source_url", "https://firm.co.uk/news"),
            retrieved_at=at,
            extracted_by=fields.get("extracted_by", "rule"),
            person_contact_id=fields.get("person"),
            at=at,
        )

    return world.transact(store)


def signal(
    world: World,
    lead_id: str,
    evidence_id: str,
    at: datetime,
    *,
    signal_class: str = "event",
    tier: int = 1,
    event_date: date | None = None,
    signal_type: str = "new_partner",
) -> str:
    return world.transact(
        lambda: signals_repo.insert_signal(
            lead_id=lead_id,
            signal_type=signal_type,
            signal_class=signal_class,
            tier=tier,
            score_only=False,
            event_date=event_date,
            first_seen_at=at,
            evidence_ids=[evidence_id],
        )
    )


def later(world: World, minutes: int) -> datetime:
    """Move the clock ``minutes`` past ``LATER``; return the new now."""
    world.clock.set(LATER + timedelta(minutes=minutes))
    return world.clock.now()


def kinds(view: LeadHistoryView) -> list[tuple[str, str, str | None]]:
    return [(each.kind, each.entity, each.to_status) for each in view.page.entries]


# The full timeline -------------------------------------------------------------------------------


def test_the_full_timeline_shows_every_change_newest_first(world: World) -> None:
    lead = world.lead()
    later(world, 1)
    contact = world.contact(lead, name="Pat Lee", email="pat@firm.co.uk")
    first = evidence(world, lead, "We hired a new partner.", later(world, 2))
    second = evidence(world, lead, "We hired two new partners.", later(world, 3))
    world.transact(lambda: signals_repo.supersede_evidence(first, second))
    signal(world, lead, second, later(world, 4))
    later(world, 5)
    booking = book(world, lead, contact)  # replied (raw), then call_booked by the Operator
    later(world, 6)
    run(world, lambda p: p.record_call_outcome(booking, HELD, actor=OPERATOR))
    later(world, 7)
    run(world, lambda p: p.move(lead, S.AUDIT_PROPOSED, actor=OPERATOR))
    later(world, 8)
    world.suppress(contact)

    view = history(world, lead)
    found = entries(view)

    ats = [(each.at, each.sort_id) for each in found]
    assert ats == sorted(ats, reverse=True)
    assert view.page.total == len(found)
    by_kind = {(each.kind, each.entity, each.to_status): each for each in found}
    created = by_kind[(EVENT, "leads", "new")]
    assert created.cause == "created_from_csv" and created.type is HistoryType.STAGES
    added = by_kind[(CONTACT_ADDED, "contacts", "added")]
    assert added.cause == "csv" and added.contact_id == contact
    assert view.contact_names[contact] == "Pat Lee"
    assert by_kind[(EVIDENCE_ADDED, "evidence", "live")].type is HistoryType.EVIDENCE
    superseded = by_kind[(EVIDENCE_SUPERSEDED, "evidence", "superseded")]
    assert superseded.entity_id == first
    assert superseded.at == LATER + timedelta(minutes=3)  # the superseding row's created_at
    assert by_kind[(SIGNAL_ADDED, "signals", "live")].text == "new_partner"
    booked = by_kind[(EVENT, "leads", "call_booked")]
    assert booked.actor == "operator" and booked.cause == "operator_move"
    calls = [each for each in found if each.entity == "call_outcomes"]
    assert calls and all(each.type is HistoryType.CALLS for each in calls)
    assert by_kind[(EVENT, "leads", "audit_proposed")].from_status == "call_held"
    suppressed = [each for each in found if each.entity == "suppression_entries"]
    assert suppressed and all(each.type is None for each in suppressed)
    assert all(
        each.text is not None and each.text.endswith("source: Reply rule") for each in suppressed
    )
    newest = {each.entity for each in found if each.at == found[0].at}
    assert found[0].at == LATER + timedelta(minutes=8)
    assert newest == {"suppression_entries", "leads"}  # the opt-out closes the Lead lost
    assert by_kind[(EVENT, "leads", "lost")].from_status == "audit_proposed"


def test_a_read_changes_no_row(world: World) -> None:
    lead = world.lead()
    contact = world.contact(lead, name="Pat Lee", email="pat@firm.co.uk")
    world.suppress(contact)
    before = every_table(world)

    for kind in HistoryType:
        history(world, lead, kind)

    assert every_table(world) == before


# The generic link --------------------------------------------------------------------------------


def test_an_existing_foreign_key_chain_links_a_data_subject_request(world: World) -> None:
    lead = world.lead()
    contact = world.contact(lead, name="Pat Lee", email="pat@firm.co.uk")
    erasures = Erasures(world.clock, world.compliance)
    with world.units.open():
        erasures.erase_on_request(contact, "DSR-1")

    found = entries(history(world, lead))

    requests = [each for each in found if each.entity == "data_subject_requests"]
    assert {each.to_status for each in requests} >= {"open", "completed"}
    assert all(each.contact_id == contact for each in requests)  # by cause_ref
    assert all(each.type is None for each in requests)


def test_an_unrelated_leads_events_are_left_out(world: World) -> None:
    lead = world.lead()
    other = world.lead("other.co.uk")
    mine = world.contact(lead, name="Pat Lee", email="pat@firm.co.uk")
    theirs = world.contact(other, name="Sam Roe", email="sam@other.co.uk")
    world.suppress(theirs)

    found = entries(history(world, lead))

    assert {each.entity_id for each in found if each.entity == "leads"} == {lead}
    contact_ids = {each.contact_id for each in found if each.contact_id is not None}
    assert contact_ids == {mine}
    assert not [each for each in found if each.entity == "suppression_entries"]


def test_a_throwaway_table_linked_by_a_foreign_key_chain_is_found(world: World) -> None:
    lead = world.lead()
    other = world.lead("other.co.uk")
    world.sql("CREATE TABLE x_children (id TEXT PRIMARY KEY, lead_id TEXT)")
    world.sql("CREATE TABLE x_grandchildren (id TEXT PRIMARY KEY, child_id TEXT)")
    metadata = MetaData()
    Table("leads", metadata, Column("id", String, primary_key=True))
    Table(
        "x_children",
        metadata,
        Column("id", String, primary_key=True),
        Column("lead_id", String, ForeignKey("leads.id")),
    )
    Table(
        "x_grandchildren",
        metadata,
        Column("id", String, primary_key=True),
        Column("child_id", String, ForeignKey("x_children.id")),
    )
    Table("x_loose", metadata, Column("id", String, primary_key=True))
    mine, theirs = new_id(), new_id()
    grand, other_grand = new_id(), new_id()
    world.sql("INSERT INTO x_children VALUES (?, ?), (?, ?)", (mine, lead, theirs, other))
    world.sql(
        "INSERT INTO x_grandchildren VALUES (?, ?), (?, ?)", (grand, mine, other_grand, theirs)
    )
    for entity_id in (grand, other_grand):
        world.sql(
            "INSERT INTO events (id, entity, entity_id, from_status, to_status, cause, "
            "cause_ref, actor, at) VALUES (?, 'x_grandchildren', ?, NULL, 'sent', "
            "'message_sent', NULL, 'system:sending', ?)",
            (new_id(), entity_id, utc_text(LATER)),
        )

    assert lineage.linked_tables(metadata) == ("leads", "x_children", "x_grandchildren")
    found = world.read(lambda: lineage.lead_events([lead], {}, metadata=metadata))

    assert {(each.entity, each.entity_id) for each in found} == {
        ("x_grandchildren", grand),
        ("leads", lead),
    }


def test_the_shipped_models_link_every_lead_child_and_no_events(world: World) -> None:
    linked = lineage.linked_tables()

    assert {"leads", "contacts", "call_outcomes", "evidence", "signals"} <= set(linked)
    assert {"data_subject_requests", "provenance_records", "signal_evidence"} <= set(linked)
    assert "events" not in linked and "suppression_entries" not in linked


def test_lead_events_ignores_ids_that_are_not_ulids(world: World) -> None:
    lead = world.lead()

    found = world.read(
        lambda: analytics_queries.lead_events(["nope", lead], {"suppression_entries": ["x"]})
    )

    assert [each.entity_id for each in found] == [lead]
    assert world.read(lambda: analytics_queries.lead_events([], {})) == ()


def test_matching_reads_entries_of_any_status_and_checks_its_keys(world: World) -> None:
    lead = world.lead()
    contact = world.contact(lead, name="Pat Lee", email="pat@firm.co.uk")
    world.suppress(contact, source=SuppressionSource.REPLY_RULE)
    keys = person_keys(
        email="pat@firm.co.uk", name="Pat Lee", linkedin_url=None, web_domain="firm.co.uk"
    )
    before = world.read(lambda: compliance_queries.matching(keys))
    with world.units.open():
        world.compliance.reverse_entry(before[0].id, "Pat said yes")

    found = world.read(lambda: compliance_queries.matching(keys))

    assert len(before) >= 2 and {each.status for each in before} == {"active"}
    assert [each.id for each in found] == [each.id for each in before]
    assert "voided" in {each.status for each in found}
    assert world.read(lambda: compliance_queries.matching([])) == ()
    with pytest.raises(TypeError):
        world.read(lambda: compliance_queries.matching(["address"]))  # type: ignore[list-item]


# Filters and paging ------------------------------------------------------------------------------


def test_each_filter_keeps_only_its_category(world: World) -> None:
    lead = world.lead()
    contact = world.contact(lead, name="Pat Lee", email="pat@firm.co.uk")
    first = evidence(world, lead, "We hired a new partner.", later(world, 1))
    signal(world, lead, first, later(world, 2))
    booking = book(world, lead, contact)
    run(world, lambda p: p.record_call_outcome(booking, HELD, actor=OPERATOR))

    calls = entries(history(world, lead, HistoryType.CALLS))
    stages = entries(history(world, lead, HistoryType.STAGES))
    found = entries(history(world, lead, HistoryType.EVIDENCE))

    assert calls and {each.entity for each in calls} == {"call_outcomes"}
    assert stages and {each.entity for each in stages} == {"leads"}
    assert {each.to_status for each in stages} >= {"new", "call_booked", "call_held"}
    assert {each.entity for each in found} == {"evidence", "signals"}
    assert history(world, lead, HistoryType.MESSAGES).page.total == 0
    assert history(world, lead, HistoryType.DRAFTS).page.entries == ()


def test_230_entries_page_by_100_and_a_page_past_the_end_shows_the_last(world: World) -> None:
    lead = world.lead()
    rows = [
        (new_id(), lead, utc_text(LATER + timedelta(seconds=second)))
        for second in range(229)  # plus the creation row: 230
    ]
    for row in rows:
        world.sql(
            "INSERT INTO events (id, entity, entity_id, from_status, to_status, cause, "
            "cause_ref, actor, at) VALUES (?, 'leads', ?, NULL, 'contact_blocked', "
            "'address_suppressed', NULL, 'system:research', ?)",
            row,
        )

    third = history(world, lead, page=3)
    past = history(world, lead, page=9)

    assert (third.page.total, third.page.page, third.page.pages) == (230, 3, 3)
    assert len(third.page.entries) == 230 - 2 * PAGE_SIZE
    assert past.page.page == 3 and past.page.entries == third.page.entries
    assert len(history(world, lead, page=1).page.entries) == PAGE_SIZE


# Merged, erased, expired, empty ------------------------------------------------------------------


def test_a_merged_leads_history_shows_on_its_survivor(world: World) -> None:
    survivor = world.lead()
    later(world, 1)
    loser = world.lead("other.co.uk")
    contact = world.contact(loser, name="Sam Roe", email="sam@other.co.uk")
    world.transact(lambda: world.discovery.merge(survivor, loser))

    view = history(world, survivor)
    found = entries(view)

    assert view.lead_id == survivor
    created = {each.entity_id for each in found if each.entity == "leads" and each.to_status == "new"}
    assert created == {survivor, loser}
    assert contact in {each.contact_id for each in found}


def test_an_erased_contact_shows_erased_with_its_ids_statuses_and_times(world: World) -> None:
    lead = world.lead()
    contact = world.contact(lead, name="Pat Lee", email="pat@firm.co.uk")
    quote = evidence(world, lead, "Pat Lee joined us.", later(world, 1), person=contact)
    erasures = Erasures(world.clock, world.compliance)
    with world.units.open():
        erasures.erase_on_request(contact, "DSR-1")

    view = history(world, lead)
    found = entries(view)

    assert view.contact_names[contact] == "[erased]"
    [added] = [each for each in found if each.kind == CONTACT_ADDED]
    assert added.contact_id == contact and added.at == LATER
    [quoted] = [each for each in found if each.kind == EVIDENCE_ADDED]
    assert quoted.entity_id == quote and quoted.text == "[erased]"
    assert ("contacts", "erased") in {(each.entity, each.to_status) for each in found}
    text = repr(found) + repr(view.contact_names)
    assert "Pat" not in text and "pat@" not in text


def test_a_signal_expires_by_its_age_and_a_retraction_before_hides_the_expiry(
    world: World,
) -> None:
    lead = world.lead()
    proof = evidence(world, lead, "We hired a new partner.", LATER)
    aged = signal(world, lead, proof, LATER, event_date=date(2026, 8, 1), tier=1)
    retracted = signal(world, lead, proof, LATER, event_date=date(2026, 8, 1), tier=2)
    world.sql(
        "UPDATE signals SET retracted_at = ? WHERE id = ?",
        (utc_text(LATER + timedelta(days=20)), retracted),
    )
    fresh = signal(world, lead, proof, LATER, event_date=date(2026, 11, 1), tier=1)

    found = entries(history(world, lead, now=datetime(2026, 11, 20, tzinfo=UTC)))
    expired = {each.entity_id: each for each in found if each.kind == SIGNAL_EXPIRED}

    # a Tier 1 Event dated 2026-08-01 is not live from age 90, 2026-10-30, so from first seen
    assert expired[aged].at == LATER and expired[aged].from_status == "live"
    # a Tier 2 Event is live through age 90; 2026-10-31 is before the retraction
    assert retracted in expired
    [gone] = [each for each in found if each.kind == SIGNAL_RETRACTED]
    assert gone.from_status == "expired"
    assert fresh not in expired


def test_an_expiry_after_now_or_after_a_retraction_is_not_shown(world: World) -> None:
    lead = world.lead()
    proof = evidence(world, lead, "We hired a new partner.", LATER)
    kept = signal(world, lead, proof, LATER, event_date=date(2026, 11, 1), tier=1)
    early = signal(world, lead, proof, LATER, event_date=date(2026, 11, 1), tier=1)
    world.sql(
        "UPDATE signals SET retracted_at = ? WHERE id = ?",
        (utc_text(LATER + timedelta(days=1)), early),
    )

    found = entries(history(world, lead, now=datetime(2027, 6, 1, tzinfo=UTC)))
    expired = {each.entity_id for each in found if each.kind == SIGNAL_EXPIRED}
    soon = entries(history(world, lead, now=datetime(2026, 12, 1, tzinfo=UTC)))

    assert kept in expired and early not in expired
    [gone] = [each for each in found if each.kind == SIGNAL_RETRACTED]
    assert gone.from_status == "live"
    assert not [each for each in soon if each.kind == SIGNAL_EXPIRED]


def test_a_new_lead_has_only_its_creation_and_an_unknown_lead_none(world: World) -> None:
    lead = world.lead()

    [created] = entries(history(world, lead))

    assert (created.entity, created.to_status, created.actor) == ("leads", "new", "system:pipeline")
    assert history(world, lead, HistoryType.CALLS).page.entries == ()
    assert world.read(lambda: lead_history(new_id(), type=HistoryType.ALL, page=1, now=NOW)) is None
    assert world.read(lambda: lead_history("nope", type=HistoryType.ALL, page=1, now=NOW)) is None


@pytest.mark.parametrize(
    ("change", "error"),
    [
        (lambda kw: kw.update(type="all"), TypeError),
        (lambda kw: kw.update(page=True), TypeError),
        (lambda kw: kw.update(page=0), ValueError),
    ],
)
def test_bad_arguments_raise(
    world: World, change: Callable[[dict[str, Any]], None], error: type[Exception]
) -> None:
    lead = world.lead()
    arguments: dict[str, Any] = {"type": HistoryType.ALL, "page": 1, "now": NOW}
    change(arguments)

    with pytest.raises(error):
        world.read(lambda: lead_history(lead, **arguments))


# Review patches -----------------------------------------------------------------------------------


def suppression_ids(view: LeadHistoryView) -> set[str]:
    return {each.entity_id for each in view.page.entries if each.entity == "suppression_entries"}


def test_an_erasure_shows_its_own_suppression_entries(world: World) -> None:
    lead = world.lead()
    contact = world.contact(
        lead, name="Pat Lee", email="pat@firm.co.uk", linkedin_url="https://www.linkedin.com/in/pat"
    )
    with world.units.open():
        Erasures(world.clock, world.compliance).erase_on_request(contact, "DSR-1")
    dsr = {
        row[0]
        for row in world.sql(
            "SELECT id FROM suppression_entries WHERE cause_ref IN "
            "(SELECT id FROM data_subject_requests WHERE contact_id = ?)",
            (contact,),
        )
    }

    found = suppression_ids(history(world, lead))

    assert len(dsr) >= 3  # address, person and profile keys
    assert dsr <= found


def test_a_merged_contacts_keys_use_the_survivors_web_domain(world: World) -> None:
    survivor = world.lead(web_domain=None, ch_number="OC123456")
    later(world, 1)
    loser = world.lead("other.co.uk")
    contact = world.contact(loser, name="Sam Roe", email="sam@other.co.uk")
    world.suppress(contact)  # the person key holds (name, other.co.uk)
    world.transact(lambda: world.discovery.merge(survivor, loser))
    every = {row[0] for row in world.sql("SELECT id FROM suppression_entries")}

    found = suppression_ids(history(world, survivor))

    assert len(every) >= 2 and found == every


def test_a_suppression_matched_only_by_the_leads_own_domain_shows(world: World) -> None:
    lead = world.lead()
    other = world.lead("other.co.uk")
    world.contact(lead, name="Pat Lee", email="pat@elsewhere.com")
    world.transact(
        lambda: world.compliance.suppress(
            DomainSubject("firm.co.uk"), SuppressionScope.FIRM, SuppressionSource.IMPORT, None
        )
    )

    found = suppression_ids(history(world, lead))

    assert found and found == {row[0] for row in world.sql("SELECT id FROM suppression_entries")}
    assert suppression_ids(history(world, other)) == set()


def test_an_operator_and_a_csv_contact_are_added_by_the_operator(world: World) -> None:
    lead = world.lead()
    csv = world.contact(lead, name="Pat Lee", email="pat@firm.co.uk")
    typed = world.transact(
        lambda: world.research.add_contact(
            lead,
            ContactCandidate(name="Sam Roe"),  # an operator source yields no address
            source=ProvenanceSource.OPERATOR,
            detail="Met at a conference",
            retrieved_at=LATER,
        )
    ).contact_id

    found = entries(history(world, lead))

    added = {each.contact_id: each for each in found if each.kind == CONTACT_ADDED}
    assert (added[csv].cause, added[csv].actor) == ("csv", "operator")
    assert (added[typed].cause, added[typed].actor) == ("operator", "operator")


def test_a_retracted_evidence_row_goes_from_live(world: World) -> None:
    lead = world.lead()
    gone = evidence(world, lead, "We hired a new partner.", later(world, 1))
    world.sql(
        "UPDATE evidence SET retracted_at = ? WHERE id = ?",
        (utc_text(LATER + timedelta(minutes=9)), gone),
    )

    found = entries(history(world, lead))

    [retracted] = [each for each in found if each.kind == EVIDENCE_RETRACTED]
    assert (retracted.entity_id, retracted.at) == (gone, LATER + timedelta(minutes=9))
    assert (retracted.from_status, retracted.to_status) == ("live", "retracted")
    assert retracted.type is HistoryType.EVIDENCE


def test_a_superseded_then_retracted_row_is_retracted_from_superseded(world: World) -> None:
    # the table refuses a row both superseded and retracted (a CHECK); the timeline copes anyway
    lead = world.lead()
    first = evidence(world, lead, "We hired two partners.", later(world, 1))
    second = evidence(world, lead, "We hired three partners.", later(world, 2))
    rows = {row.id: row for row in world.read(lambda: research_queries.all_evidence(lead))}
    both = dataclasses.replace(
        rows[first], superseded_by=second, retracted_at=LATER + timedelta(minutes=9)
    )
    early = dataclasses.replace(both, retracted_at=LATER + timedelta(minutes=1, seconds=30))

    retracted = [e for e in _evidence_entries([both, rows[second]]) if e.kind == EVIDENCE_RETRACTED]
    before = [e for e in _evidence_entries([early, rows[second]]) if e.kind == EVIDENCE_RETRACTED]

    assert [each.from_status for each in retracted] == ["superseded"]
    assert [each.from_status for each in before] == ["live"]  # retracted before the supersede


def test_a_missing_superseding_row_dates_the_supersede_by_last_confirmed(world: World) -> None:
    lead = world.lead()
    evidence(world, lead, "We hired a new partner.", later(world, 1))
    [row] = world.read(lambda: research_queries.all_evidence(lead))
    orphan = dataclasses.replace(
        row, superseded_by=new_id(), last_confirmed_at=LATER + timedelta(minutes=5)
    )

    [_, superseded] = _evidence_entries([orphan])

    assert superseded.kind == EVIDENCE_SUPERSEDED
    assert superseded.at == LATER + timedelta(minutes=5)


def test_a_merged_leads_evidence_and_signals_show_on_its_survivor(world: World) -> None:
    survivor = world.lead()
    later(world, 1)
    loser = world.lead("other.co.uk")
    proof = evidence(world, loser, "We hired a new partner.", later(world, 2))
    found_signal = signal(world, loser, proof, later(world, 3))
    world.transact(lambda: world.discovery.merge(survivor, loser))

    found = entries(history(world, survivor))

    ids = {(each.kind, each.entity_id) for each in found}
    assert (EVIDENCE_ADDED, proof) in ids and (SIGNAL_ADDED, found_signal) in ids


def test_a_signal_shows_its_types_name(world: World) -> None:
    lead = world.lead()
    seeded = world.read(lambda: setup_queries.segment_by_id(world.segment))
    assert seeded is not None
    kind = seeded.definition.signal_types[0]
    proof = evidence(world, lead, "We hired a new partner.", later(world, 1))
    signal(world, lead, proof, later(world, 2), signal_type=kind.key)

    [added] = [each for each in entries(history(world, lead)) if each.kind == SIGNAL_ADDED]

    assert added.text == kind.name != kind.key
