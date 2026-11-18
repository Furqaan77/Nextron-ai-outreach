"""Story 2.10's Primary Contact on a temporary database at head (FR-13, AD-3, AD-7, AD-17):
``Research.select_primary`` and ``make_primary`` through ``PrimaryContacts``, with the shipped
Segments and Markets. The world is Story 2.9's (``tests.app.test_signal_extraction``).

Every row of the spec's matrix the selection owns is here: the AP/AR preference (through a real
extraction), no decision-maker, the operator's choice and its refusals, a departed Contact; also
the best rank and its ties, writing only what changed, the ``events`` rows, the partial unique
index, the reparent on a merge, the skipped Leads and the ``select_primary_contact`` job."""

import json
import logging
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from nextron import main
from nextron.app.compliance.api import Compliance
from nextron.app.research.people import PrimaryContacts
from nextron.db.repos.research import signals as signals_repo
from nextron.domain.contactability import ContactSubject, SuppressionScope
from nextron.domain.contacts import (
    ContactCandidate,
    PrimaryChoice,
    PrimaryRefusal,
    PrimaryRefused,
    PrimarySetBy,
    ProvenanceSource,
)
from nextron.domain.job import Job, Lane
from nextron.domain.suppression_entry import SuppressionSource
from nextron.ports.llm import LlmOk, LlmUsage
from tests.app.test_signal_extraction import (
    ABOUT,
    HAIKU,
    T0,
    TEAM,
    World,
    build_world,
    crawled,
    no_jobs,
    ok,
    signal,
)

AP_CLERK = "We are hiring an Accounts Payable Clerk."
DISPATCHER = "We are hiring a dispatch coordinator."
COO = "Ann Fox, Chief Operating Officer"
CONTROLLER = "Bo Day, Financial Controller"
SYSTEM_ACTOR = "system:research"


@pytest.fixture
def world(db_engine: Engine) -> World:
    return build_world(db_engine)


def ops(world: World) -> None:
    [(segment,)] = world.sql("SELECT id FROM segments WHERE key = 'ops_automation'")
    world.segment = segment


def primary(world: World) -> PrimaryContacts:
    return PrimaryContacts(world.clock, world.research)


def select(world: World, lead: str) -> PrimaryChoice | None:
    with world.units.open():
        return primary(world).select_primary_contact(lead)


def make(world: World, lead: str, contact: str) -> None:
    with world.units.open():
        primary(world).make_primary(lead, contact)


def add(
    world: World,
    lead: str,
    name: str,
    role: str | None,
    *,
    email: str | None = None,
    minutes: int = 0,
) -> str:
    world.clock.advance(timedelta(minutes=minutes))
    added = world.transact(
        lambda: world.research.add_contact(
            lead,
            ContactCandidate(name=name, role=role, email=email),
            source=ProvenanceSource.CSV,
            detail="list.csv",
            retrieved_at=T0,
        )
    )
    return added.contact_id


def flags(world: World, lead: str) -> dict[str, tuple[Any, Any]]:
    rows = world.sql(
        "SELECT name, is_primary, primary_set_by FROM contacts WHERE lead_id = ? "
        "ORDER BY created_at, id",
        (lead,),
    )
    return {name: (bool(is_primary), set_by) for name, is_primary, set_by in rows}


def no_decision_maker(world: World, lead: str) -> Any:
    [(value,)] = world.sql("SELECT no_decision_maker FROM leads WHERE id = ?", (lead,))
    return value


def primary_events(world: World, lead: str) -> list[tuple[Any, ...]]:
    return world.sql(
        "SELECT from_status, to_status, cause, cause_ref, actor FROM events "
        "WHERE entity = 'leads' AND entity_id = ? AND to_status LIKE 'primary_contact_%' "
        "ORDER BY rowid",
        (lead,),
    )


def invalidate(world: World, contact: str) -> None:
    world.sql(
        "UPDATE contacts SET email = 'gone@example.com', address_class = 'invalid' WHERE id = ?",
        (contact,),
    )


# The best rank, ties and what changed -----------------------------------------------------------


def test_the_best_rank_becomes_primary_and_only_changes_are_written(world: World) -> None:
    ops(world)
    lead = world.lead()
    add(world, lead, "Cy Ceo", "CEO")
    coo = add(world, lead, "Ann Fox", "Chief Operating Officer", minutes=1)
    assert no_decision_maker(world, lead) is None  # before the first selection

    assert select(world, lead) == PrimaryChoice(coo, PrimarySetBy.SYSTEM)

    assert flags(world, lead) == {"Cy Ceo": (False, None), "Ann Fox": (True, "system")}
    assert no_decision_maker(world, lead) == 0
    set_row = (None, "primary_contact_set", "selection", coo, SYSTEM_ACTOR)
    assert primary_events(world, lead) == [set_row]
    [(updated,)] = world.sql("SELECT updated_at FROM contacts WHERE id = ?", (coo,))

    world.clock.advance(timedelta(hours=1))
    assert select(world, lead) == PrimaryChoice(coo, PrimarySetBy.SYSTEM)

    assert primary_events(world, lead) == [set_row]  # nothing changed: nothing written
    assert world.sql("SELECT updated_at FROM contacts WHERE id = ?", (coo,)) == [(updated,)]


def test_a_tie_goes_to_the_earliest_found(world: World) -> None:
    ops(world)
    lead = world.lead()
    first = add(world, lead, "Ann Fox", "VP of Operations")
    add(world, lead, "Bo Day", "COO", minutes=1)

    assert select(world, lead) == PrimaryChoice(first, PrimarySetBy.SYSTEM)


def test_a_new_better_contact_replaces_the_engines_primary_with_one_event(world: World) -> None:
    ops(world)
    lead = world.lead()
    ceo = add(world, lead, "Cy Ceo", "CEO")
    select(world, lead)
    coo = add(world, lead, "Ann Fox", "COO", minutes=1)

    select(world, lead)

    assert flags(world, lead) == {"Cy Ceo": (False, None), "Ann Fox": (True, "system")}
    assert primary_events(world, lead) == [
        (None, "primary_contact_set", "selection", ceo, SYSTEM_ACTOR),
        (None, "primary_contact_set", "selection", coo, SYSTEM_ACTOR),
    ]


# Row: None --------------------------------------------------------------------------------------


def test_no_decision_maker_means_no_primary_and_the_flag(world: World) -> None:
    ops(world)
    lead = world.lead()
    add(world, lead, "Ola Ops", "Operations Manager")
    add(world, lead, "Pa Doe", "PA to the CEO")

    assert select(world, lead) == PrimaryChoice(None, None)

    assert flags(world, lead) == {"Ola Ops": (False, None), "Pa Doe": (False, None)}
    assert no_decision_maker(world, lead) == 1
    assert primary_events(world, lead) == []

    coo = add(world, lead, "Ann Fox", "COO", minutes=1)
    select(world, lead)
    assert no_decision_maker(world, lead) == 0
    assert primary_events(world, lead) == [
        (None, "primary_contact_set", "selection", coo, SYSTEM_ACTOR)
    ]


def test_a_lead_without_contacts_has_no_decision_maker(world: World) -> None:
    lead = world.lead()

    assert select(world, lead) == PrimaryChoice(None, None)
    assert no_decision_maker(world, lead) == 1


def test_an_invalid_address_or_a_departure_clears_the_engines_primary(world: World) -> None:
    ops(world)
    lead = world.lead()
    coo = add(world, lead, "Ann Fox", "COO")
    select(world, lead)
    invalidate(world, coo)

    select(world, lead)

    assert flags(world, lead) == {"Ann Fox": (False, None)}
    assert no_decision_maker(world, lead) == 1
    assert primary_events(world, lead)[-1] == (
        None,
        "primary_contact_cleared",
        "selection",
        coo,
        SYSTEM_ACTOR,
    )


# Row: AP/AR (through a real extraction) ---------------------------------------------------------


def people_answer(pages: dict[str, str]) -> LlmOk:
    return LlmOk(
        {
            "people": [
                {
                    "name": "Ann Fox",
                    "role": "Chief Operating Officer",
                    "block_id": pages[TEAM],
                    "quote": COO,
                },
                {
                    "name": "Bo Day",
                    "role": "Financial Controller",
                    "block_id": pages[TEAM],
                    "quote": CONTROLLER,
                },
            ]
        },
        model=HAIKU,
        usage=LlmUsage(requests=1),
    )


@pytest.mark.parametrize(
    ("posting", "expected"),
    [(AP_CLERK, "Bo Day"), (DISPATCHER, "Ann Fox")],
)
def test_the_strongest_events_ap_ar_quote_makes_the_controller_primary(
    world: World, posting: str, expected: str
) -> None:
    ops(world)
    lead, run_id, pages = crawled(
        world, team=f"<p>{COO}</p><p>{CONTROLLER}</p>", about=f"<p>{posting}</p>"
    )
    answer = ok(signals=[signal(pages[ABOUT], posting, "ops_hiring_manual_entry")])
    world.extract(lead, run_id, answer, people=[people_answer(pages)])

    choice = select(world, lead)

    [(contact_id,)] = world.sql("SELECT id FROM contacts WHERE name = ?", (expected,))
    assert choice == PrimaryChoice(contact_id, PrimarySetBy.SYSTEM)


def insert_event(world: World, lead: str, quote: str, event_date: date | None) -> str:
    """A live Evidence row and its ``ops_hiring_manual_entry`` Signal."""

    def write() -> str:
        evidence_id = signals_repo.insert_evidence(
            lead_id=lead,
            evidence_type="quote",
            quote=quote,
            sought=None,
            number=None,
            fact=None,
            value=None,
            pages_checked=("https://smith-law.com/careers",),
            source="website",
            source_url="https://smith-law.com/careers",
            retrieved_at=T0,
            extracted_by="ai:extract_signals@v1",
            person_contact_id=None,
            at=T0,
        )
        return signals_repo.insert_signal(
            lead_id=lead,
            signal_type="ops_hiring_manual_entry",
            signal_class="event",
            tier=1,
            score_only=False,
            event_date=event_date,
            first_seen_at=T0,
            evidence_ids=[evidence_id],
        )

    return world.transact(write)


def test_an_expired_or_retracted_events_quote_prefers_nobody(world: World) -> None:
    ops(world)
    lead = world.lead()
    coo = add(world, lead, "Ann Fox", "COO")
    add(world, lead, "Bo Day", "Controller", minutes=1)
    insert_event(world, lead, AP_CLERK, T0.date() - timedelta(days=95))  # expired

    assert select(world, lead) == PrimaryChoice(coo, PrimarySetBy.SYSTEM)

    [(evidence_id,)] = world.sql("SELECT id FROM evidence")
    world.transact(lambda: signals_repo.retract_evidence(evidence_id, T0))
    insert_event(world, lead, AP_CLERK, None)  # live, first seen now
    [(controller,)] = world.sql("SELECT id FROM contacts WHERE name = 'Bo Day'")
    assert select(world, lead) == PrimaryChoice(controller, PrimarySetBy.SYSTEM)


def test_a_superseded_quote_on_the_strongest_event_prefers_nobody(world: World) -> None:
    ops(world)
    lead = world.lead()
    coo = add(world, lead, "Ann Fox", "COO")
    controller = add(world, lead, "Bo Day", "Controller", minutes=1)
    signal_id = insert_event(world, lead, AP_CLERK, None)
    assert select(world, lead) == PrimaryChoice(controller, PrimarySetBy.SYSTEM)
    [(old_id,)] = world.sql("SELECT id FROM evidence")

    def replace_quote() -> None:
        new_id = signals_repo.insert_evidence(
            lead_id=lead,
            evidence_type="quote",
            quote="We are hiring a Data Entry Clerk.",
            sought=None,
            number=None,
            fact=None,
            value=None,
            pages_checked=("https://smith-law.com/careers",),
            source="website",
            source_url="https://smith-law.com/careers",
            retrieved_at=T0,
            extracted_by="ai:extract_signals@v1",
            person_contact_id=None,
            at=T0,
        )
        signals_repo.supersede_evidence(old_id, new_id)
        signals_repo.link(signal_id, new_id)

    world.transact(replace_quote)

    # the Event is still the strongest, but only its live quote counts: no AP/AR term
    assert select(world, lead) == PrimaryChoice(coo, PrimarySetBy.SYSTEM)


# Row: Operator ----------------------------------------------------------------------------------


def test_the_operators_primary_stays_until_its_address_is_invalid(world: World) -> None:
    ops(world)
    lead = world.lead()
    ceo = add(world, lead, "Cy Ceo", "CEO")
    select(world, lead)

    make(world, lead, ceo)  # the engine's Primary: pinned
    coo = add(world, lead, "Ann Fox", "COO", minutes=1)
    assert select(world, lead) == PrimaryChoice(ceo, PrimarySetBy.OPERATOR)
    assert flags(world, lead) == {"Cy Ceo": (True, "operator"), "Ann Fox": (False, None)}

    invalidate(world, ceo)
    assert select(world, lead) == PrimaryChoice(coo, PrimarySetBy.SYSTEM)

    assert flags(world, lead) == {"Cy Ceo": (False, None), "Ann Fox": (True, "system")}
    assert primary_events(world, lead) == [
        (None, "primary_contact_set", "selection", ceo, SYSTEM_ACTOR),
        (None, "primary_contact_set", "operator", ceo, "operator"),
        (None, "primary_contact_set", "selection", coo, SYSTEM_ACTOR),
    ]


def test_the_operator_may_pick_any_contact_and_it_clears_the_flag(world: World) -> None:
    ops(world)
    lead = world.lead()
    office = add(world, lead, "Ola Ops", "Operations Manager")
    select(world, lead)
    assert no_decision_maker(world, lead) == 1

    make(world, lead, office)

    assert flags(world, lead) == {"Ola Ops": (True, "operator")}
    assert no_decision_maker(world, lead) == 0
    events = primary_events(world, lead)
    make(world, lead, office)  # the operator's own Primary again: nothing written
    assert primary_events(world, lead) == events


def test_making_another_contact_primary_moves_the_flag(world: World) -> None:
    ops(world)
    lead = world.lead()
    coo = add(world, lead, "Ann Fox", "COO")
    ceo = add(world, lead, "Cy Ceo", "CEO", minutes=1)
    select(world, lead)

    make(world, lead, ceo)

    assert flags(world, lead) == {"Ann Fox": (False, None), "Cy Ceo": (True, "operator")}
    assert primary_events(world, lead)[-1] == (
        None,
        "primary_contact_set",
        "operator",
        ceo,
        "operator",
    )
    assert coo != ceo


# Row: Refusals ----------------------------------------------------------------------------------


def snapshot(world: World) -> list[Any]:
    return [
        world.sql("SELECT id, is_primary, primary_set_by, updated_at FROM contacts ORDER BY id"),
        world.sql("SELECT id, no_decision_maker FROM leads ORDER BY id"),
        world.sql("SELECT COUNT(*) FROM events"),
    ]


def test_each_refusal_writes_nothing(world: World) -> None:
    ops(world)
    lead = world.lead()
    other = world.lead(web_domain="other-firm.com")
    coo = add(world, lead, "Ann Fox", "COO")
    elsewhere = add(world, other, "Bo Day", "COO")
    invalid = add(world, lead, "Cy Ceo", "CEO", email="cy@smith-law.com")
    invalidate(world, invalid)
    select(world, lead)
    merged = world.lead(web_domain="merged-firm.com")
    world.transact(lambda: world.discovery.merge(lead, merged))
    before = snapshot(world)

    cases = [
        ("01KA0000000000000000000000", coo, PrimaryRefusal.UNKNOWN_LEAD),
        ("not-a-ulid", coo, PrimaryRefusal.UNKNOWN_LEAD),
        (merged, coo, PrimaryRefusal.MERGED_LEAD),
        (lead, elsewhere, PrimaryRefusal.NOT_ON_LEAD),
        (lead, "01KA0000000000000000000000", PrimaryRefusal.NOT_ON_LEAD),
        (lead, "not-a-ulid", PrimaryRefusal.NOT_ON_LEAD),
        (lead, invalid, PrimaryRefusal.ADDRESS_INVALID),
    ]
    for lead_id, contact_id, reason in cases:
        with pytest.raises(PrimaryRefused) as refused:
            make(world, lead_id, contact_id)
        assert refused.value.reason is reason

    assert snapshot(world) == before


# The partial unique index -----------------------------------------------------------------------


def test_the_index_allows_one_primary_per_lead(world: World) -> None:
    lead = world.lead()
    other = world.lead(web_domain="other-firm.com")
    first = add(world, lead, "Ann Fox", "Managing Partner")
    second = add(world, lead, "Bo Day", "Owner")
    elsewhere = add(world, other, "Cy Ceo", "Owner")
    world.sql(
        "UPDATE contacts SET is_primary = 1, primary_set_by = 'system' WHERE id = ?", (first,)
    )
    world.sql(
        "UPDATE contacts SET is_primary = 1, primary_set_by = 'system' WHERE id = ?", (elsewhere,)
    )

    with pytest.raises(IntegrityError, match="UNIQUE"):
        world.sql("UPDATE contacts SET is_primary = 1 WHERE id = ?", (second,))
    world.sql("UPDATE contacts SET is_primary = 0 WHERE id = ?", (second,))  # not Primary: fine


# The reparent on a merge ------------------------------------------------------------------------


def test_a_merged_leads_operator_pick_beats_the_survivors_engine_pick(world: World) -> None:
    ops(world)
    survivor = world.lead(web_domain="survivor.com")
    loser = world.lead(web_domain="loser.com")
    ceo = add(world, survivor, "Cy Ceo", "CEO")
    select(world, survivor)
    office = add(world, loser, "Ola Ops", "Operations Manager")
    make(world, loser, office)

    world.transact(lambda: world.discovery.merge(survivor, loser))

    # Mac's pick moved in and stays; the survivor's engine pick is cleared, with its row
    assert flags(world, survivor) == {"Cy Ceo": (False, None), "Ola Ops": (True, "operator")}
    assert primary_events(world, survivor) == [
        (None, "primary_contact_set", "selection", ceo, SYSTEM_ACTOR),
        (None, "primary_contact_cleared", "selection", ceo, SYSTEM_ACTOR),
    ]
    assert no_decision_maker(world, loser) == 0  # the merged Lead's profile stays


def test_when_both_picks_are_the_operators_the_survivors_wins(world: World) -> None:
    ops(world)
    survivor = world.lead(web_domain="survivor.com")
    loser = world.lead(web_domain="loser.com")
    ceo = add(world, survivor, "Cy Ceo", "CEO")
    make(world, survivor, ceo)
    coo = add(world, loser, "Ann Fox", "COO")
    make(world, loser, coo)

    world.transact(lambda: world.discovery.merge(survivor, loser))

    assert flags(world, survivor) == {"Cy Ceo": (True, "operator"), "Ann Fox": (False, None)}
    assert primary_events(world, survivor) == [
        (None, "primary_contact_set", "operator", ceo, "operator"),
        (None, "primary_contact_cleared", "selection", coo, SYSTEM_ACTOR),
    ]


def test_a_merge_clears_the_moved_engine_pick_and_selects_the_survivors_again(
    world: World,
) -> None:
    ops(world)
    survivor = world.lead(web_domain="survivor.com")
    loser = world.lead(web_domain="loser.com")
    ceo = add(world, survivor, "Cy Ceo", "CEO")
    select(world, survivor)
    coo = add(world, loser, "Ann Fox", "COO")
    select(world, loser)

    world.transact(lambda: world.discovery.merge(survivor, loser))

    # the moved engine pick was cleared (the survivor had one), then the selection ran
    assert flags(world, survivor) == {"Cy Ceo": (False, None), "Ann Fox": (True, "system")}
    assert primary_events(world, survivor) == [
        (None, "primary_contact_set", "selection", ceo, SYSTEM_ACTOR),
        (None, "primary_contact_cleared", "selection", coo, SYSTEM_ACTOR),
        (None, "primary_contact_set", "selection", coo, SYSTEM_ACTOR),
    ]


def test_a_merge_keeps_the_moved_operators_primary_when_the_survivor_has_none(
    world: World,
) -> None:
    ops(world)
    survivor = world.lead(web_domain="survivor.com")
    loser = world.lead(web_domain="loser.com")
    add(world, survivor, "Ann Fox", "COO")
    office = add(world, loser, "Ola Ops", "Operations Manager")
    make(world, loser, office)

    world.transact(lambda: world.discovery.merge(survivor, loser))

    assert flags(world, survivor) == {"Ann Fox": (False, None), "Ola Ops": (True, "operator")}
    assert no_decision_maker(world, survivor) == 0


# The contactability gate (Story 3.4, at the 2.10 merge) -----------------------------------------


def suppress(world: World, contact: str) -> None:
    """The Contact asked not to be contacted: the operator suppresses their person keys."""
    world.transact(
        lambda: Compliance(world.clock).suppress(
            ContactSubject(contact), SuppressionScope.PERSON, SuppressionSource.OPERATOR, None
        )
    )


def test_a_contact_the_gate_blocks_is_never_the_primary(world: World) -> None:
    ops(world)
    lead = world.lead()
    ceo = add(world, lead, "Cy Ceo", "CEO")
    coo = add(world, lead, "Ann Fox", "Chief Operating Officer", minutes=1)
    assert select(world, lead) == PrimaryChoice(coo, PrimarySetBy.SYSTEM)

    suppress(world, coo)  # the engine's Primary is cleared for the next best
    assert select(world, lead) == PrimaryChoice(ceo, PrimarySetBy.SYSTEM)
    assert flags(world, lead) == {"Cy Ceo": (True, "system"), "Ann Fox": (False, None)}

    make(world, lead, coo)  # an operator's pick that the gate blocks doesn't survive a selection
    assert select(world, lead) == PrimaryChoice(ceo, PrimarySetBy.SYSTEM)
    assert flags(world, lead) == {"Cy Ceo": (True, "system"), "Ann Fox": (False, None)}

    world.sql("UPDATE leads SET pipeline_stage = 'lost' WHERE id = ?", (lead,))  # closed
    assert select(world, lead) == PrimaryChoice(None, None)
    assert flags(world, lead) == {"Cy Ceo": (False, None), "Ann Fox": (False, None)}
    assert no_decision_maker(world, lead) == 1
    assert primary_events(world, lead)[-1] == (
        None,
        "primary_contact_cleared",
        "selection",
        ceo,
        SYSTEM_ACTOR,
    )


# The use case and the job -----------------------------------------------------------------------


@pytest.mark.parametrize("merged", [False, True])
def test_an_unknown_or_merged_lead_is_skipped(
    world: World, merged: bool, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.research.people")
    survivor = world.lead(web_domain="survivor.com")
    lead = world.lead()
    target = "01KA0000000000000000000000"
    if merged:
        world.transact(lambda: world.discovery.merge(survivor, lead))
        target = lead
    before = world.sql("SELECT COUNT(*) FROM events")

    assert select(world, target) is None

    assert world.sql("SELECT COUNT(*) FROM events") == before
    [record] = [r for r in caplog.records if r.getMessage() == "primary_selection_skipped"]
    assert getattr(record, "reason") == ("merged_lead" if merged else "unknown_lead")
    assert getattr(record, "lead_id") == target


def test_the_select_primary_contact_job_runs_the_selection(world: World) -> None:
    lead, run_id, pages = crawled(world, team="<p>Jane Roe, Managing Partner</p>")
    world.extract(
        lead,
        run_id,
        ok(),
        people=[
            LlmOk(
                {
                    "people": [
                        {
                            "name": "Jane Roe",
                            "role": "Managing Partner",
                            "block_id": pages[TEAM],
                            "quote": "Jane Roe, Managing Partner",
                        }
                    ]
                },
                model=HAIKU,
                usage=LlmUsage(requests=1),
            )
        ],
    )
    contacts = primary(world)
    handler = main.build_job_types(
        alerts=no_jobs,
        backups=no_jobs,
        restore_tests=no_jobs,
        recovery=no_jobs,
        contact_markets=no_jobs,
        crawls=no_jobs,
        discovery=no_jobs,
        research=no_jobs,
        extractions=no_jobs,
        primary=lambda: contacts,
        test_runs=no_jobs,
    ).get("select_primary_contact").handler
    [(_, _, payload, _)] = world.jobs("select_primary_contact")
    job = Job(
        "01JD0000000000000000000099", "select_primary_contact", Lane.BULK, json.loads(payload), 1
    )

    with world.units.open():
        assert handler(job) is None

    assert flags(world, lead) == {"Jane Roe": (True, "system")}
