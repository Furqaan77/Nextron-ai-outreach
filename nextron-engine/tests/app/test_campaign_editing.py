"""Creating and editing Campaigns and Variants (FR-3, AD-4, AD-21, NFR-3; Story 11.8) on real
units of work: every row of the spec's I/O matrix through ``Campaigns``, the stored decode of a
Campaign with no Variant or only retired ones, ``queries.sequencing.enrollment_counts``, the
migration 0045 triggers, and the 11.7 deferred fix (a middle Step removal under an active
Variant's notes). ``bare`` is a database seeded with the shipped files but no Campaign;
``world`` is ``tests.app.test_enroll``'s (Law-PrivateAI x US active, Contacts to enroll)."""

import logging
import sqlite3
from typing import Any

import pytest
from sqlalchemy import Engine

from nextron import main
from nextron.app.ops.api import Seeds
from nextron.app.queries import sequencing as sequencing_queries
from nextron.app.queries import setup as setup_queries
from nextron.app.setup.api import UK_PAIR_DRAFT_ONLY, CampaignSaved, Campaigns, VariantSaved
from nextron.app.setup.sequence_editing import SequenceEditor
from nextron.db.repos.setup import offers as offers_repo
from nextron.db.repos.setup import sequences as sequences_repo
from nextron.db.uow import UnitOfWorkFactory, current, use_case
from nextron.domain import seed_yaml
from nextron.domain.campaign import (
    KEY_TAKEN,
    LAST_ACTIVE_VARIANT,
    LIA_FIRST,
    NEEDS_VARIANT_FIRST,
    NO_SUCH_VARIANT,
    PAIR_FROZEN,
    PAIR_TAKEN,
    STALE_CAMPAIGN,
    TOO_MANY_VARIANTS,
    VARIANT_ENROLLED,
    VARIANT_NAME_TAKEN,
    VARIANT_RETIRED,
    CampaignRefused,
    CampaignStatus,
    StepAngleNote,
    VariantStatus,
)
from nextron.domain.company_facts import validate_company_facts
from nextron.domain.enrollment import NewEnrollment
from nextron.domain.markets import validate_markets
from nextron.domain.offers import OfferDef, validate_offers
from nextron.domain.segments import validate_segments
from nextron.domain.sequences import SequenceDef, SequenceRefused, validate_sequences
from tests.app.test_enroll import US_CAMPAIGN, World, build_world
from tests.app.test_sequence_editing import fields_of
from tests.fakes.clock import FrozenClock

def read(name: str) -> object:
    return seed_yaml.parse((main.SEED_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture
def bare(units: UnitOfWorkFactory) -> UnitOfWorkFactory:
    """The shipped Segments, Markets, Sequences, facts and Offers, and no Campaign."""
    with units.open():
        Seeds(FrozenClock()).sync_seed(
            validate_segments(read(main.SEGMENTS_FILE_NAME)),
            validate_markets(read(main.MARKETS_FILE_NAME)),
            validate_sequences(read(main.SEQUENCES_FILE_NAME)),
            validate_company_facts(read(main.COMPANY_FACTS_FILE_NAME)),
            validate_offers(read(main.OFFERS_FILE_NAME)),
            (),
        )
    return units


@pytest.fixture
def world(db_engine: Engine) -> World:
    return build_world(db_engine)


def sql(units: UnitOfWorkFactory, statement: str, *params: object) -> list[tuple[Any, ...]]:
    with units.engine.connect() as connection:
        return [tuple(row) for row in connection.exec_driver_sql(statement, params).all()]


def ref(units: UnitOfWorkFactory) -> dict[str, str]:
    """The ids of the shipped Segments, Markets, Offer and Sequence, by key or code."""
    found: dict[str, str] = {}
    for table, column in (
        ("segments", "key"),
        ("markets", "code"),
        ("offers", "key"),
        ("sequences", "key"),
    ):
        for row_id, name in sql(units, f"SELECT id, {column} FROM {table}"):
            found[f"{table}:{name}"] = row_id
    return found


def run(units: UnitOfWorkFactory, call: str, *args: Any, **kwargs: Any) -> Any:
    with units.open():
        return getattr(Campaigns(FrozenClock()), call)(*args, **kwargs)


def counts(units: UnitOfWorkFactory) -> list[Any]:
    return [
        sql(units, f"SELECT count(*) FROM {table}")[0][0]
        for table in ("campaigns", "variants", "events")
    ] + sql(units, "SELECT id, version, name, segment_id, market_id, offer_id FROM campaigns")


def refusal(units: UnitOfWorkFactory, call: str, *args: Any, **kwargs: Any) -> dict[str, str]:
    before = counts(units) + sql(units, "SELECT * FROM variants ORDER BY id")
    with pytest.raises(CampaignRefused) as caught:
        run(units, call, *args, **kwargs)
    assert counts(units) + sql(units, "SELECT * FROM variants ORDER BY id") == before
    return caught.value.problems


def create(
    units: UnitOfWorkFactory, key: str = "law_us", segment: str = "law_private_ai",
    market: str = "us",
) -> CampaignSaved:
    ids = ref(units)
    saved = run(
        units,
        "create_campaign",
        key,
        "Law US",
        ids[f"segments:{segment}"],
        ids[f"markets:{market}"],
        ids["offers:default"],
        ids["sequences:default"],
    )
    assert isinstance(saved, CampaignSaved)
    return saved


def campaign(units: UnitOfWorkFactory, campaign_id: str) -> Any:
    with units.open(read_only=True):
        found = setup_queries.campaign(campaign_id)
    assert found is not None
    return found


def edit(
    units: UnitOfWorkFactory, campaign_id: str, based_on: int | None = None, **changes: str
) -> tuple[Any, ...]:
    """``edit_campaign``'s name and arguments: the stored Campaign with ``changes``."""
    found = campaign(units, campaign_id)
    fields = {
        "name": found.name,
        "segment_id": found.segment_id,
        "market_id": found.market_id,
        "offer_id": found.offer_id,
        "sequence_id": found.sequence_id,
        **changes,
    }
    version = found.version if based_on is None else based_on
    return ("edit_campaign", found.id, version, *fields.values())


def events(units: UnitOfWorkFactory, campaign_id: str) -> list[tuple[Any, ...]]:
    return sql(
        units,
        "SELECT from_status, to_status, cause, cause_ref, actor FROM events "
        "WHERE entity = 'campaigns' AND entity_id = ? ORDER BY rowid",
        campaign_id,
    )


# Create ---------------------------------------------------------------------------------------------


def test_create_stores_a_draft_at_version_1_with_no_variant_and_its_event(
    bare: UnitOfWorkFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.setup.api")

    saved = create(bare)

    assert (saved.key, saved.version, saved.changed) == ("law_us", 1, True)
    found = campaign(bare, saved.campaign_id)
    assert (found.status, found.version, found.variants, found.lia) == (
        CampaignStatus.DRAFT, 1, (), None
    )
    assert events(bare, saved.campaign_id) == [
        (None, "v1", "operator_create", None, "operator")
    ]
    [record] = [r for r in caplog.records if r.getMessage() == "campaign_created"]
    assert record.campaign == "law_us"  # type: ignore[attr-defined]
    with bare.open(read_only=True):
        assert setup_queries.campaign_keys() == frozenset({"law_us"})
        assert setup_queries.campaign_active(saved.campaign_id) is False


def test_create_collects_every_field_problem_and_writes_nothing(bare: UnitOfWorkFactory) -> None:
    ids = ref(bare)

    problems = refusal(
        bare,
        "create_campaign",
        " ",
        "x" * 101,
        "",
        "01ARZ3NDEKTSV4RRFFQ69G5FAV",
        ids["offers:default"],
        "not-an-id",
    )

    assert problems == {
        "key": "Enter a value",
        "name": "must be at most 100 characters",
        "segment": "Choose one",
        "market": "No stored Market has this id",
        "sequence": "No stored Sequence has this id",
    }
    assert refusal(bare, "create_campaign", "Bad-Key", "Ok", *list(ids.values())[:4])["key"]


def test_create_refuses_a_taken_key_and_a_taken_pair(bare: UnitOfWorkFactory) -> None:
    create(bare)
    ids = ref(bare)
    rest = (
        ids["segments:law_private_ai"],
        ids["markets:us"],
        ids["offers:default"],
        ids["sequences:default"],
    )

    assert refusal(bare, "create_campaign", "law_us", "Another", *rest) == {"key": KEY_TAKEN}
    assert refusal(bare, "create_campaign", "law_us_2", "Another", *rest) == {
        "form": PAIR_TAKEN
    }


# Activation -----------------------------------------------------------------------------------------


def test_activate_with_no_active_variant_is_refused_before_the_lia(
    bare: UnitOfWorkFactory,
) -> None:
    uk = create(bare, "law_uk", market="uk").campaign_id

    assert refusal(bare, "activate", uk) == {"form": NEEDS_VARIANT_FIRST}
    run(bare, "add_variant", uk, "A")
    assert refusal(bare, "activate", uk)["form"] == LIA_FIRST
    run(bare, "activate", uk, "LIA-1", "2026-11-16")
    assert campaign(bare, uk).status == CampaignStatus.ACTIVE


def test_resume_with_no_active_variant_is_refused(bare: UnitOfWorkFactory) -> None:
    us = create(bare).campaign_id
    run(bare, "add_variant", us, "A")
    run(bare, "activate", us)
    run(bare, "pause", us)
    variant = campaign(bare, us).variants[0].id
    sql_write(bare, "UPDATE variants SET status = 'inactive' WHERE id = ?", variant)

    assert refusal(bare, "resume", us) == {"form": NEEDS_VARIANT_FIRST}


def sql_write(units: UnitOfWorkFactory, statement: str, *params: object) -> None:
    with units.engine.begin() as connection:
        connection.exec_driver_sql(statement, params)


# Variants -------------------------------------------------------------------------------------------


def test_a_third_active_variant_is_refused_and_retired_ones_do_not_count(
    bare: UnitOfWorkFactory,
) -> None:
    us = create(bare).campaign_id
    run(bare, "add_variant", us, "A")
    run(bare, "add_variant", us, "B")

    assert refusal(bare, "add_variant", us, "C") == {"form": TOO_MANY_VARIANTS}
    a = campaign(bare, us).variants[0]
    retired = run(bare, "retire_variant", us, a.id, campaign(bare, us).version)
    added = run(bare, "add_variant", us, "C")

    assert isinstance(retired, VariantSaved) and retired.changed
    assert added.name == "C"
    found = campaign(bare, us)
    assert [(v.name, v.status) for v in found.variants] == [
        ("A", VariantStatus.INACTIVE),
        ("B", VariantStatus.ACTIVE),
        ("C", VariantStatus.ACTIVE),
    ]
    # a retired Variant's name stays taken, case-insensitively
    run(bare, "retire_variant", us, found.variants[1].id, found.version)
    assert refusal(bare, "add_variant", us, "a") == {"name": VARIANT_NAME_TAKEN}
    assert [event[2] for event in events(bare, us)][-5:] == [
        "operator_add_variant",
        "operator_add_variant",
        "operator_retire_variant",
        "operator_add_variant",
        "operator_retire_variant",
    ]


def test_a_stored_campaign_with_only_retired_variants_decodes(bare: UnitOfWorkFactory) -> None:
    us = create(bare).campaign_id
    run(bare, "add_variant", us, "A")
    run(bare, "retire_variant", us, campaign(bare, us).variants[0].id, 2)

    found = campaign(bare, us)

    assert [(v.name, v.status) for v in found.variants] == [("A", VariantStatus.INACTIVE)]


def test_retire_then_add_round_robins_over_the_active_variants_only(world: World) -> None:
    campaign_id = world.campaign(US_CAMPAIGN)
    world.add_variant(US_CAMPAIGN, "B")
    _, first_contact = world.contact()
    first = world.enroll(first_contact, campaign_id)
    assert isinstance(first, NewEnrollment)
    variants = world.variants(US_CAMPAIGN)
    assert first.variant_id == variants["A"]
    version = campaign(world.units, campaign_id).version

    run(world.units, "retire_variant", campaign_id, variants["A"], version)
    run(world.units, "add_variant", campaign_id, "C")
    picked = []
    for _ in range(4):
        _, contact_id = world.contact()
        enrolled = world.enroll(contact_id, campaign_id)
        assert isinstance(enrolled, NewEnrollment)
        picked.append(enrolled.variant_id)

    variants = world.variants(US_CAMPAIGN)
    assert picked == [variants["B"], variants["C"], variants["B"], variants["C"]]
    assert world.sql(
        "SELECT variant_id FROM enrollments WHERE id = ?", (first.enrollment_id,)
    ) == [(variants["A"],)]
    assert world.sql("SELECT status FROM variants WHERE id = ?", (variants["A"],)) == [
        ("inactive",)
    ]
    with world.units.open(read_only=True):
        assert dict(sequencing_queries.enrollment_counts(campaign_id)) == {
            variants["A"]: 1,
            variants["B"]: 2,
            variants["C"]: 2,
        }
        assert dict(sequencing_queries.enrollment_counts("x")) == {}


def test_edit_variant_saves_an_unenrolled_one_and_an_identical_save_writes_nothing(
    bare: UnitOfWorkFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="nextron.app.setup.api")
    us = create(bare).campaign_id
    run(bare, "add_variant", us, "A")
    a = campaign(bare, us).variants[0].id

    saved = run(bare, "edit_variant", us, a, 2, " Anchor ", (StepAngleNote(2, " Client data "),))
    same = run(bare, "edit_variant", us, a, 3, "Anchor", (StepAngleNote(2, "Client data"),))

    assert (saved.name, saved.version, saved.changed) == ("Anchor", 3, True)
    assert (same.version, same.changed) == (3, False)
    found = campaign(bare, us).variants[0]
    assert (found.name, found.step_angles) == ("Anchor", (StepAngleNote(2, "Client data"),))
    assert events(bare, us)[-1] == ("v2", "v3", "operator_edit_variant", None, "operator")
    assert [r.getMessage() for r in caplog.records].count("variant_edited") == 1
    assert refusal(bare, "edit_variant", us, a, 3, "A", (StepAngleNote(5, "x"),)) == {
        "step_angles": "Step 5 is beyond the Sequence's 4 Steps"
    }


def test_edit_variant_refuses_an_enrolled_a_retired_and_an_unknown_variant(world: World) -> None:
    campaign_id = world.campaign(US_CAMPAIGN)
    _, contact_id = world.contact()
    assert isinstance(world.enroll(contact_id, campaign_id), NewEnrollment)
    world.add_variant(US_CAMPAIGN, "B")
    variants = world.variants(US_CAMPAIGN)
    version = campaign(world.units, campaign_id).version

    assert refusal(world.units, "edit_variant", campaign_id, variants["A"], version, "A2") == {
        "form": VARIANT_ENROLLED
    }
    run(world.units, "retire_variant", campaign_id, variants["A"], version)
    version += 1
    assert refusal(world.units, "edit_variant", campaign_id, variants["A"], version, "A2") == {
        "form": VARIANT_RETIRED
    }
    assert refusal(world.units, "retire_variant", campaign_id, variants["A"], version) == {
        "form": VARIANT_RETIRED
    }
    assert refusal(
        world.units, "edit_variant", campaign_id, "01ARZ3NDEKTSV4RRFFQ69G5FAV", version, "Z"
    ) == {"form": NO_SUCH_VARIANT}


def test_retiring_the_last_active_variant_of_a_running_campaign_is_refused(
    world: World,
) -> None:
    campaign_id = world.campaign(US_CAMPAIGN)
    [a] = world.variants(US_CAMPAIGN).values()

    assert refusal(world.units, "retire_variant", campaign_id, a, 1) == {
        "form": LAST_ACTIVE_VARIANT
    }


def test_a_draft_may_retire_its_last_variant(bare: UnitOfWorkFactory) -> None:
    draft = create(bare).campaign_id
    run(bare, "add_variant", draft, "A")
    run(bare, "retire_variant", draft, campaign(bare, draft).variants[0].id, 2)


def test_a_variant_write_is_stale_checked_and_refused_by_the_lia_gap(world: World) -> None:
    campaign_id = world.campaign(US_CAMPAIGN)
    [a] = world.variants(US_CAMPAIGN).values()
    world.add_variant(US_CAMPAIGN, "B")

    assert refusal(world.units, "retire_variant", campaign_id, a, 1) == {"form": STALE_CAMPAIGN}
    assert refusal(world.units, "add_variant", campaign_id, "C", based_on=1) == {
        "form": STALE_CAMPAIGN
    }
    world.sql("UPDATE markets SET country_code = 'XX' WHERE code = 'uk'")
    world.sql("UPDATE markets SET country_code = 'GB' WHERE code = 'us'")
    assert refusal(world.units, "retire_variant", campaign_id, a, 2) == {"form": LIA_FIRST}
    assert refusal(world.units, "edit_variant", campaign_id, a, 2, "Z") == {"form": LIA_FIRST}


# Edit -----------------------------------------------------------------------------------------------


def test_edit_saves_a_new_version_and_an_identical_save_writes_nothing(
    bare: UnitOfWorkFactory,
) -> None:
    us = create(bare).campaign_id
    ids = ref(bare)
    other = create(bare, "ops_us", segment="ops_automation").campaign_id

    saved = run(bare, *edit(bare, us, name=" Law in the US ", segment_id=ids["segments:law_private_ai"]))
    same = run(bare, *edit(bare, us))

    assert (saved.key, saved.version, saved.changed) == ("law_us", 2, True)
    assert (same.version, same.changed) == (2, False)
    assert campaign(bare, us).name == "Law in the US"
    assert events(bare, us)[-1] == ("v1", "v2", "operator_edit", None, "operator")
    # a pair another Campaign holds; a stale form; an unknown id
    assert refusal(bare, *edit(bare, us, segment_id=ids["segments:ops_automation"])) == {
        "form": PAIR_TAKEN
    }
    assert refusal(bare, *edit(bare, us, based_on=7)) == {"form": STALE_CAMPAIGN}
    assert refusal(
        bare, "edit_campaign", "01ARZ3NDEKTSV4RRFFQ69G5FAV", 1, "X", *list(ids.values())[:4]
    ) == {"form": "No Campaign has this id"}
    assert refusal(bare, *edit(bare, us, name="", offer_id="")) == {
        "name": "Enter a value",
        "offer": "Choose one",
    }
    # moving to the free pair (ops x uk) is allowed while nothing is enrolled
    moved = run(bare, *edit(bare, other, market_id=ids["markets:uk"]))
    assert moved.changed and campaign(bare, other).market_id == ids["markets:uk"]


def test_a_segment_or_market_change_is_refused_once_a_contact_is_enrolled(world: World) -> None:
    campaign_id = world.campaign(US_CAMPAIGN)
    _, contact_id = world.contact()
    enrolled = world.enroll(contact_id, campaign_id)
    assert isinstance(enrolled, NewEnrollment)
    world.stop(enrolled.enrollment_id)  # any status counts
    [(segment,)] = world.sql("SELECT id FROM segments WHERE key = 'ops_automation'")
    [(market,)] = world.sql("SELECT id FROM markets WHERE code = 'uk'")

    assert refusal(world.units, *edit(world.units, campaign_id, segment_id=segment)) == {
        "form": PAIR_FROZEN
    }
    assert refusal(world.units, *edit(world.units, campaign_id, market_id=market)) == {
        "form": PAIR_FROZEN
    }
    # the name, Offer and Sequence may still change
    assert run(world.units, *edit(world.units, campaign_id, name="Renamed")).changed


def second_offer_and_sequence(world: World) -> tuple[str, str]:
    """A stored Offer ``other`` and a 3-Step Sequence ``short``; their ids."""
    default_offer = world.read(lambda: setup_queries.offer("default"))
    default_sequence = world.read(lambda: setup_queries.sequence("default"))
    assert default_offer is not None and default_sequence is not None
    ladder = default_offer.definition.ladder
    short = SequenceDef("short", "Short", default_sequence.definition.steps[:3])

    @use_case
    def insert() -> tuple[str, str]:
        with current().transaction():
            offer_id = offers_repo.insert(OfferDef("other", "Other", ladder), world.clock.now())
            sequence_id = sequences_repo.insert(short, world.clock.now(), "operator")
            return offer_id, sequence_id

    with world.units.open():
        return insert()


def test_an_offer_or_sequence_change_reaches_new_enrollments_only(world: World) -> None:
    campaign_id = world.campaign(US_CAMPAIGN)
    _, first_contact = world.contact()
    first = world.enroll(first_contact, campaign_id)
    assert isinstance(first, NewEnrollment)
    before = world.sql(
        "SELECT offer_id, sequence_id, sequence_version, sequence_version_id, variant_id "
        "FROM enrollments WHERE id = ?",
        (first.enrollment_id,),
    )
    offer_id, sequence_id = second_offer_and_sequence(world)

    saved = run(
        world.units, *edit(world.units, campaign_id, offer_id=offer_id, sequence_id=sequence_id)
    )
    _, next_contact = world.contact()
    second = world.enroll(next_contact, campaign_id)

    assert saved.changed
    assert isinstance(second, NewEnrollment)
    assert world.sql(
        "SELECT offer_id, sequence_id, sequence_version, sequence_version_id, variant_id "
        "FROM enrollments WHERE id = ?",
        (first.enrollment_id,),
    ) == before
    assert world.sql(
        "SELECT offer_id, sequence_id FROM enrollments WHERE id = ?", (second.enrollment_id,)
    ) == [(offer_id, sequence_id)]


def test_a_new_sequence_must_cover_the_active_notes(world: World) -> None:
    campaign_id = world.campaign(US_CAMPAIGN)
    world.sql(
        "UPDATE variants SET step_angles = '[{\"note\":\"A last nudge\",\"step\":4}]' "
        "WHERE campaign_id = ?",
        (campaign_id,),
    )
    _, sequence_id = second_offer_and_sequence(world)

    assert refusal(world.units, *edit(world.units, campaign_id, sequence_id=sequence_id)) == {
        "sequence": "Variant A has an angle note for Step 4, but this Sequence has 3 Steps"
    }


def test_a_new_market_reruns_the_angle_note_floor(bare: UnitOfWorkFactory) -> None:
    ops = create(bare, "ops_us", segment="ops_automation").campaign_id
    run(bare, "add_variant", ops, "A", (StepAngleNote(2, "ABA Formal Opinion 512 applies"),))
    uk = ref(bare)["markets:uk"]
    problem = refusal(bare, *edit(bare, ops, market_id=uk))["market"]
    assert problem.startswith("Variant A's angle note 1 names ABA Formal Opinion 512")


def lia_columns(units: UnitOfWorkFactory, campaign_id: str) -> list[tuple[Any, ...]]:
    return sql(units, "SELECT lia_ref, lia_date FROM campaigns WHERE id = ?", campaign_id)


def test_a_running_campaign_can_t_move_to_a_gb_market(bare: UnitOfWorkFactory) -> None:
    ops = create(bare, "ops_us", segment="ops_automation").campaign_id
    run(bare, "add_variant", ops, "A")
    run(bare, "activate", ops)

    assert refusal(bare, *edit(bare, ops, market_id=ref(bare)["markets:uk"])) == {
        "form": LIA_FIRST,
        "market": UK_PAIR_DRAFT_ONLY,
    }


def test_a_draft_uk_campaign_s_segment_change_clears_its_lia(bare: UnitOfWorkFactory) -> None:
    uk = create(bare, "law_uk", market="uk").campaign_id
    run(bare, "add_variant", uk, "A")
    run(bare, "record_lia", uk, "LIA-1", "2026-11-16")
    assert lia_columns(bare, uk) == [("LIA-1", "2026-11-16")]

    saved = run(bare, *edit(bare, uk, segment_id=ref(bare)["segments:ops_automation"]))

    assert saved.changed
    assert lia_columns(bare, uk) == [(None, None)]
    assert campaign(bare, uk).lia is None
    assert refusal(bare, "activate", uk)["form"] == LIA_FIRST  # a new assessment is needed


def test_a_running_uk_campaign_can_t_change_its_segment(bare: UnitOfWorkFactory) -> None:
    uk = create(bare, "law_uk", market="uk").campaign_id
    run(bare, "add_variant", uk, "A")
    run(bare, "activate", uk, "LIA-1", "2026-11-16")
    ids = ref(bare)

    assert refusal(bare, *edit(bare, uk, segment_id=ids["segments:ops_automation"])) == {
        "form": LIA_FIRST,
        "market": UK_PAIR_DRAFT_ONLY,
    }
    assert lia_columns(bare, uk) == [("LIA-1", "2026-11-16")]
    # leaving the UK is allowed, and the old pair's assessment goes with it
    assert run(bare, *edit(bare, uk, market_id=ids["markets:us"])).changed
    assert lia_columns(bare, uk) == [(None, None)]


# Migration 0045 triggers ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("statement", "match"),
    [
        (
            "INSERT INTO variants SELECT '01ARZ3NDEKTSV4RRFFQ69G5FA' || n, campaign_id, 'V' || n, "
            "'active', '[]', created_at, updated_at FROM variants, "
            "(SELECT 'V' AS n UNION SELECT 'W') WHERE name = 'A' "
            "AND campaign_id = (SELECT id FROM campaigns WHERE key = 'law_private_ai_us')",
            "a Campaign can have at most 2 active Variants",
        ),
        (
            "UPDATE variants SET name = 'Z' WHERE campaign_id = "
            "(SELECT id FROM campaigns WHERE key = 'law_private_ai_us')",
            "the name and notes of a Variant with Enrollments never change",
        ),
        (
            "UPDATE variants SET step_angles = '[{\"note\":\"x\",\"step\":1}]' WHERE campaign_id = "
            "(SELECT id FROM campaigns WHERE key = 'law_private_ai_us')",
            "the name and notes of a Variant with Enrollments never change",
        ),
        (
            "UPDATE campaigns SET segment_id = (SELECT id FROM segments WHERE key = "
            "'ops_automation'), market_id = (SELECT id FROM markets WHERE code = 'uk') "
            "WHERE key = 'law_private_ai_us'",
            "the Segment and Market of a Campaign with Enrollments never change",
        ),
    ],
    ids=["third-active", "enrolled-name", "enrolled-notes", "enrolled-pair"],
)
def test_the_0045_triggers_refuse(world: World, statement: str, match: str) -> None:
    _, contact_id = world.contact()
    assert isinstance(world.enroll(contact_id, world.campaign(US_CAMPAIGN)), NewEnrollment)

    with pytest.raises(sqlite3.IntegrityError, match=match):
        world.sql(statement)


def test_the_0045_triggers_let_a_retire_through_and_keep_it_final(world: World) -> None:
    world.add_variant(US_CAMPAIGN, "B")
    _, contact_id = world.contact()
    assert isinstance(world.enroll(contact_id, world.campaign(US_CAMPAIGN)), NewEnrollment)
    variants = world.variants(US_CAMPAIGN)

    world.sql("UPDATE variants SET status = 'inactive' WHERE id = ?", (variants["A"],))
    world.sql(
        "INSERT INTO variants SELECT '01ARZ3NDEKTSV4RRFFQ69G5FAV', campaign_id, 'C', 'active', "
        "'[]', created_at, updated_at FROM variants WHERE id = ?",
        (variants["B"],),
    )  # retired ones don't count towards the cap
    with pytest.raises(sqlite3.IntegrityError, match="never becomes active again"):
        world.sql("UPDATE variants SET status = 'active' WHERE id = ?", (variants["A"],))
    world.sql(  # an unenrolled Variant's name may change
        "UPDATE variants SET name = 'C2' WHERE id = '01ARZ3NDEKTSV4RRFFQ69G5FAV'"
    )


# The 11.7 deferred fix --------------------------------------------------------------------------


def edit_sequence(world: World, fields: dict[str, Any]) -> Any:
    found = world.read(lambda: setup_queries.sequence("default"))
    assert found is not None
    with world.units.open():
        return SequenceEditor(world.clock).edit(found.id, found.version, fields)


def removing_step_2(world: World) -> dict[str, Any]:
    found = world.read(lambda: setup_queries.sequence("default"))
    assert found is not None
    fields = fields_of(found.definition)
    fields["steps"] = [fields["steps"][0], *fields["steps"][2:]]
    return fields


def test_a_middle_step_removal_under_an_active_variant_s_note_is_refused(world: World) -> None:
    campaign_id = world.campaign(US_CAMPAIGN)
    world.sql(
        "UPDATE variants SET step_angles = '[{\"note\":\"Their intake\",\"step\":3}]' "
        "WHERE campaign_id = ?",
        (campaign_id,),
    )

    with pytest.raises(SequenceRefused) as caught:
        edit_sequence(world, removing_step_2(world))

    assert caught.value.problems == {
        "steps": f"Campaign {US_CAMPAIGN}'s Variant A has angle notes on Steps after a removed "
        "Step; retire it and add a new one before removing Step 2"
    }


def test_a_retired_variant_s_notes_never_block_a_sequence_edit(world: World) -> None:
    campaign_id = world.campaign(US_CAMPAIGN)
    world.add_variant(US_CAMPAIGN, "B")
    variants = world.variants(US_CAMPAIGN)
    world.sql(
        "UPDATE variants SET step_angles = '[{\"note\":\"Their intake\",\"step\":4}]' "
        "WHERE id = ?",
        (variants["A"],),
    )
    run(world.units, "retire_variant", campaign_id, variants["A"], 2)

    saved = edit_sequence(world, removing_step_2(world))

    assert saved.changed and saved.version == 2


def test_a_note_on_exactly_the_removed_step_is_refused(world: World) -> None:
    campaign_id = world.campaign(US_CAMPAIGN)
    world.sql(
        "UPDATE variants SET step_angles = '[{\"note\":\"Their intake\",\"step\":2}]' "
        "WHERE campaign_id = ?",
        (campaign_id,),
    )
    fields = removing_step_2(world)
    assert len(fields["steps"]) == 3  # 4 Steps stored, Step 2 removed

    with pytest.raises(SequenceRefused) as caught:
        edit_sequence(world, fields)

    assert caught.value.problems == {
        "steps": f"Campaign {US_CAMPAIGN}'s Variant A has angle notes on Steps after a removed "
        "Step; retire it and add a new one before removing Step 2"
    }


def test_a_removal_after_every_note_is_allowed(world: World) -> None:
    world.sql(
        "UPDATE variants SET step_angles = '[{\"note\":\"Opening\",\"step\":1}]' "
        "WHERE campaign_id = ?",
        (world.campaign(US_CAMPAIGN),),
    )

    assert edit_sequence(world, removing_step_2(world)).changed
